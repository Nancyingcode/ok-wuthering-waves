import gc
import time
from functools import wraps

from ok.task.TaskExecutor import TaskExecutor
from ok.util.logger import Logger

logger = Logger.get_logger(__name__)

OCR_IDLE_RELEASE_SECONDS = 60.0


def _ocr_lib(self, name="default"):
    # Reads must share the eviction lock so a cache hit cannot race with removal.
    with self._ocr_lib_lock:
        self._ocr_idle_since = None
        if name not in self._ocr_lib:
            self._ocr_lib[name] = self._create_ocr_lib(name)
        return self._ocr_lib[name]


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
            ocr_config = self.config.get('ocr') or {}
            names = []
            for name in self._ocr_lib:
                options = ocr_config.get(name) or {}
                uses_openvino = (options.get('lib') == 'onnxocr'
                                 and (options.get('params') or {}).get('use_openvino', False))
                if uses_openvino:
                    names.append(name)
            retired = [self._ocr_lib.pop(name) for name in names]
        finally:
            self._ocr_lib_lock.release()
    if retired:
        # Drop only cache ownership. Borrowed models must finish inference before
        # destruction, and native cleanup must run outside both executor locks.
        retired.clear()
        gc.collect()
        logger.info(f'Released idle OpenVINO OCR models: {names}; reload on next use')


def install_ocr_idle_release():
    """Bridge ok-script 2.0.22 until it provides native idle OCR eviction."""
    if hasattr(TaskExecutor, '_release_idle_ocr'):
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

    # Keep this application workaround out of installed package files; remove it
    # once the minimum ok-script version includes the same lifecycle protection.
    TaskExecutor.ocr_lib = _ocr_lib
    TaskExecutor._release_idle_ocr = _release_idle_ocr
    TaskExecutor._wait_for_activity = wait_with_ocr_release
    TaskExecutor._run_job = run_job_with_ocr_activity
    TaskExecutor.next_task = next_task_with_ocr_activity
