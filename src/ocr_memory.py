import gc
import threading
import time
import weakref
from functools import wraps

import psutil

from ok.task.TaskExecutor import TaskExecutor
from ok.task.exceptions import FinishedException
from ok.util.logger import Logger

logger = Logger.get_logger(__name__)

OCR_IDLE_RELEASE_SECONDS = 60.0
OCR_MAX_CALLS = 128
OCR_MAX_AGE_SECONDS = 300.0
OCR_MEMORY_GROWTH_BYTES = 512 * 1024 * 1024


def _private_memory():
    try:
        return getattr(psutil.Process().memory_info(), 'private', None)
    except (psutil.Error, OSError):
        return None


def _cpu_model(model):
    predictors = (getattr(model, 'text_detector', None), getattr(model, 'text_recognizer', None))
    return all(getattr(predictor, 'is_openvino', False)
               and getattr(predictor, 'is_npu', None) is False
               for predictor in predictors)


def _drain_model(model):
    for name in ('text_detector', 'text_recognizer', 'text_classifier'):
        predictor = getattr(model, name, None)
        queue = getattr(predictor, '_async_queue', None)
        if queue is not None:
            queue.wait_all()


class ManagedOCR:
    """Keep cached callers attached while replacing the model between inferences."""

    def __init__(self, executor, name, model=None):
        # Notification adapters retain this object after dropping their factory.
        # A weak reference avoids keeping the entire executor alive through them.
        self._executor = weakref.ref(executor)
        self._exit_event = executor.exit_event
        self._name = name
        self._condition = threading.Condition(threading.Lock())
        self._model = model
        self._active = 0
        self._recycling = False
        self._pending_reason = None
        self._last_used = time.monotonic()
        self._reset_usage()

    def _reset_usage(self):
        self._calls = 0
        self._created_at = time.monotonic()
        self._memory_baseline = _private_memory()
        self._runtime_recycling = self._model is not None and _cpu_model(self._model)

    def _check_cancelled(self):
        if self._exit_event.is_set():
            raise FinishedException()
        executor = self._executor()
        if executor is not None and executor.is_executor_thread():
            executor.check_enabled(check_pause=False)

    def _acquire(self):
        with self._condition:
            # A pending recycle closes admission before the last caller exits;
            # otherwise overlapping notification/task calls could starve cleanup.
            while self._recycling or self._pending_reason is not None:
                self._check_cancelled()
                self._condition.wait(0.1)
            self._check_cancelled()
            if self._model is not None:
                self._active += 1
                self._last_used = time.monotonic()
                return self._model
            self._recycling = True
        try:
            executor = self._executor()
            if executor is None:
                raise FinishedException()
            model = executor._create_ocr_lib(self._name)
            with self._condition:
                self._model = model
                self._reset_usage()
                self._active += 1
                self._last_used = time.monotonic()
                return model
        finally:
            with self._condition:
                self._recycling = False
                self._condition.notify_all()

    def prepare(self):
        model = self._acquire()
        model = None
        self._finish_call(counted=False)

    def ocr(self, *args, **kwargs):
        return self._call('ocr', *args, **kwargs)

    def __call__(self, *args, **kwargs):
        return self._call(None, *args, **kwargs)

    def _call(self, method, *args, **kwargs):
        model = self._acquire()
        try:
            if method is None:
                return model(*args, **kwargs)
            return getattr(model, method)(*args, **kwargs)
        finally:
            # A local model or bound method would keep native memory alive until
            # after new callers are admitted. Clear it before closing the lease.
            model = None
            self._finish_call()

    def _recycle_reason(self, now):
        if not self._runtime_recycling:
            return None
        if self._calls >= OCR_MAX_CALLS:
            return 'call limit'
        if now - self._created_at >= OCR_MAX_AGE_SECONDS:
            return 'model age'
        memory = _private_memory()
        has_memory_growth = (memory is not None and self._memory_baseline is not None
                             and memory - self._memory_baseline >= OCR_MEMORY_GROWTH_BYTES)
        if has_memory_growth:
            # Process-wide growth is a safety signal, not attribution to OCR.
            return 'private memory growth'
        return None

    def _finish_call(self, counted=True):
        with self._condition:
            self._active -= 1
            self._last_used = time.monotonic()
            if counted:
                self._calls += 1
                if self._pending_reason is None:
                    self._pending_reason = self._recycle_reason(self._last_used)
            should_recycle = self._active == 0 and self._pending_reason is not None
            if should_recycle:
                self._recycling = True
            self._condition.notify_all()
        if should_recycle:
            self._recycle()

    def release_idle(self, now):
        with self._condition:
            can_release = (self._model is not None and not self._recycling and self._active == 0
                           and now - self._last_used >= OCR_IDLE_RELEASE_SECONDS)
            if not can_release:
                return
            self._pending_reason = 'idle'
            self._recycling = True
        self._recycle()

    def _recycle(self):
        reason = self._pending_reason
        model = self._model
        try:
            # Python inference may have returned just before its native callback
            # leaves the queue. Drain it before dropping any model ownership.
            _drain_model(model)
            self._model = None
            model = None
            gc.collect()
            logger.info(f'Released OpenVINO OCR model {self._name}: {reason}; reload on next use')
        except Exception as error:
            # Failed draining must leave the old model intact. Reset the budget
            # so repeated calls do not retry a failed cleanup on every inference.
            logger.error(f'Could not release OpenVINO OCR model {self._name}', error)
        finally:
            with self._condition:
                self._reset_usage()
                self._pending_reason = None
                self._recycling = False
                self._condition.notify_all()


def _ocr_lib(self, name="default"):
    # Reads must share the eviction lock so a cache hit cannot race with removal.
    with self._ocr_lib_lock:
        self._ocr_idle_since = None
        options = (self.config.get('ocr') or {}).get(name) or {}
        uses_openvino = (options.get('lib') == 'onnxocr'
                         and (options.get('params') or {}).get('use_openvino', False))
        if name not in self._ocr_lib:
            if uses_openvino:
                self._ocr_lib[name] = ManagedOCR(self, name)
            else:
                self._ocr_lib[name] = self._create_ocr_lib(name)
        elif uses_openvino and not isinstance(self._ocr_lib[name], ManagedOCR):
            self._ocr_lib[name] = ManagedOCR(self, name, self._ocr_lib[name])
        instance = self._ocr_lib[name]
    if isinstance(instance, ManagedOCR):
        instance.prepare()
    return instance


def _release_idle_ocr(self):
    with self.lock:
        has_pending_work = (self.current_job is not None or self.current_task is not None
                            or self._job_queue or self.onetime_task_queue
                            or self._exclusive_owner is not None)
        has_enabled_tasks = not self.paused and any(
            task.enabled for task in (*self.onetime_tasks, *self.trigger_tasks))
        if has_pending_work or has_enabled_tasks:
            self._ocr_idle_since = None
            return
        # Model warmup can call back into the executor; never wait on its lock
        # while holding the scheduler lock.
        if not self._ocr_lib_lock.acquire(blocking=False):
            return
        try:
            now = time.monotonic()
            idle_since = getattr(self, '_ocr_idle_since', None)
            if idle_since is None:
                self._ocr_idle_since = now
                return
            if now - idle_since < OCR_IDLE_RELEASE_SECONDS:
                return
            instances = [instance for instance in self._ocr_lib.values()
                         if isinstance(instance, ManagedOCR)]
        finally:
            self._ocr_lib_lock.release()
    for instance in instances:
        instance.release_idle(now)


def install_ocr_idle_release():
    """Manage OpenVINO model ownership for both active and idle executors."""
    if getattr(TaskExecutor, '_okww_managed_ocr', False):
        return

    wait_for_activity = TaskExecutor._wait_for_activity
    run_job = TaskExecutor._run_job
    next_task = TaskExecutor.next_task

    @wraps(wait_for_activity)
    def wait_with_ocr_release(self, timeout, wake_version=None):
        self._release_idle_ocr()
        return wait_for_activity(self, timeout, wake_version)

    @wraps(run_job)
    def run_job_with_ocr_activity(self, job):
        self._ocr_idle_since = None
        return run_job(self, job)

    @wraps(next_task)
    def next_task_with_ocr_activity(self):
        result = next_task(self)
        if result[0] is not None:
            self._ocr_idle_since = None
        return result

    # Native idle eviction alone cannot rotate models retained by notifications.
    # Keep one managed hook even when an installed library already has that hook.
    TaskExecutor.ocr_lib = _ocr_lib
    TaskExecutor._release_idle_ocr = _release_idle_ocr
    TaskExecutor._wait_for_activity = wait_with_ocr_release
    TaskExecutor._run_job = run_job_with_ocr_activity
    TaskExecutor.next_task = next_task_with_ocr_activity
    TaskExecutor._okww_managed_ocr = True
