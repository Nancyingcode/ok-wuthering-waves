import gc
import inspect
import json
import math
import os
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
    predictors = [getattr(model, 'text_detector', None), getattr(model, 'text_recognizer', None)]
    classifier = getattr(model, 'text_classifier', None)
    if classifier is not None:
        predictors.append(classifier)
    return all(getattr(predictor, 'is_openvino', False)
               and getattr(predictor, 'is_npu', None) is False
               for predictor in predictors)


def _drain_model(model):
    for name in ('text_detector', 'text_recognizer', 'text_classifier'):
        predictor = getattr(model, name, None)
        queue = getattr(predictor, '_async_queue', None)
        if queue is not None:
            queue.wait_all()


def _ocr_options(executor, name):
    config = executor.config.get('ocr') or {}
    return config.get(name) or (config if name == 'default' else {})


def _validate_parameter_support(options):
    params = options.get('params') or {}
    if not isinstance(params, dict):
        raise ValueError('OCR params must be a dictionary')
    compatibility = {'use_openvino': True, 'use_npu': True, 'rec_batch_num': 6,
                     'openvino_num_requests': 1, 'openvino_compile_config': {'CPU': {}, 'NPU': {}}}
    needs_passthrough = any(key not in compatibility or (key not in ('use_openvino', 'use_npu')
                            and value != compatibility[key]) for key, value in params.items())
    if needs_passthrough and not getattr(TaskExecutor, 'ocr_params_passthrough', False):
        raise RuntimeError('OCR parameters need the patched ok-script wheel; run scripts/build_ocr_wheels.py')
    if needs_passthrough:
        # Older OnnxOCR accepts arbitrary kwargs and silently ignores misspellings.
        # Its parser defines the supported surface for the two pinned dependencies.
        from onnxocr.utils import infer_args
        supported = {action.dest for action in infer_args()._actions} | {'logger', 'openvino_compile_config'}
        unknown = set(params) - supported
        if unknown:
            raise ValueError(f'Unknown OCR parameters: {sorted(unknown)}')
    compile_config = params.get('openvino_compile_config')
    if compile_config is not None and not isinstance(compile_config, dict):
        raise ValueError('OCR openvino_compile_config must be a dictionary')
    if compile_config and any(compile_config.values()):
        from onnxocr.predict_base import PredictBase
        if 'openvino_compile_config' not in inspect.signature(PredictBase.__init__).parameters:
            raise RuntimeError('OCR compile configuration needs the patched onnxocr-ppocrv5 wheel; '
                               'run scripts/build_ocr_wheels.py')


def _lifecycle_options(options):
    policy = dict(options.get('lifecycle') or {})
    defaults = {'idle_policy': 'executor_idle', 'idle_seconds': OCR_IDLE_RELEASE_SECONDS,
                'max_calls': OCR_MAX_CALLS, 'max_age_seconds': OCR_MAX_AGE_SECONDS,
                'process_growth_mib': OCR_MEMORY_GROWTH_BYTES / 1024 ** 2}
    if set(policy) - set(defaults):
        raise ValueError(f'Unknown OCR lifecycle options: {set(policy) - set(defaults)}')
    defaults.update(policy)
    if defaults['idle_policy'] not in ('executor_idle', 'ocr_idle'):
        raise ValueError('OCR idle_policy must be executor_idle or ocr_idle')
    for key in ('idle_seconds', 'max_calls', 'max_age_seconds', 'process_growth_mib'):
        value = defaults[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f'OCR {key} must be positive')
    if type(defaults['max_calls']) is not int:
        raise ValueError('OCR max_calls must be an integer')
    if type(options.get('preload', True)) is not bool:
        raise ValueError('OCR preload must be a boolean')
    return defaults


class ManagedOCR:
    """Keep cached callers attached while replacing the model between inferences."""

    def __init__(self, executor, name, model=None):
        # Notification adapters retain this object after dropping their factory.
        # A weak reference avoids keeping the entire executor alive through them.
        self._executor = weakref.ref(executor)
        self._exit_event = executor.exit_event
        self._name = name
        self._policy = _lifecycle_options(_ocr_options(executor, name))
        self._condition = threading.Condition(threading.Lock())
        self._model = model
        self._active = 0
        self._recycling = False
        self._pending_reason = None
        self._last_used = time.monotonic()
        self._last_use_kind = None
        self._idle_retry_after = 0
        self._generation = int(model is not None)
        self._diagnostics = os.environ.get('OKWW_OCR_DIAGNOSTICS') == '1'
        self._reset_usage('proxy initialization')

    def _reset_usage(self, reason):
        self._calls = 0
        self._created_at = time.monotonic()
        self._memory_baseline = _private_memory()
        self._runtime_recycling = self._model is not None and _cpu_model(self._model)
        if self._diagnostics:
            self._diagnostic('budget_baseline', reason=reason, baseline_bytes=self._memory_baseline,
                             baseline_sampled_at=time.monotonic(), model_present=self._model is not None)

    def _diagnostic(self, event, **fields):
        if self._diagnostics:
            logger.info('OCR_DIAGNOSTIC ' + json.dumps({
                'event': event, 'name': self._name, 'generation': self._generation,
                'calls': self._calls, 'active_calls': self._active,
                'monotonic': time.monotonic(), **fields}))

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
                return self._model
            self._recycling = True
        try:
            executor = self._executor()
            if executor is None:
                raise FinishedException()
            started = time.monotonic()
            if self._diagnostics:
                self._diagnostic('factory_before', target_generation=self._generation + 1,
                                 private_bytes=_private_memory())
            model = executor._create_ocr_lib(self._name)
            if self._diagnostics:
                self._diagnostic('factory_returned', target_generation=self._generation + 1,
                                 private_bytes=_private_memory(), seconds=time.monotonic() - started,
                                 requested_npu=bool((_ocr_options(executor, self._name).get('params') or {})
                                                    .get('use_npu', True)))
            with self._condition:
                if not self._exit_event.is_set():
                    self._model = model
                    self._generation += 1
                    self._reset_usage('model created')
                    self._active += 1
                    logger.info(f'Created OCR model {self._name}: generation {self._generation}')
                    self._log_lifecycle('created', seconds=time.monotonic() - started)
                    return model
            # Native construction can overlap shutdown. Drain outside all locks,
            # and retain ownership if draining fails rather than discard a live queue.
            try:
                _drain_model(model)
            except Exception as error:
                with self._condition:
                    self._model = model
                logger.error('Could not drain OCR created during shutdown', error)
            finally:
                model = None
            raise FinishedException()
        finally:
            with self._condition:
                self._recycling = False
                self._condition.notify_all()

    def prepare(self):
        model = self._acquire()
        model = None
        self._finish_call(counted=False)
        executor = self._executor()
        if executor is not None:
            _start_startup_ocr_monitor(executor)

    def ocr(self, *args, **kwargs):
        return self._call('ocr', *args, **kwargs)

    def __call__(self, *args, **kwargs):
        return self._call(None, *args, **kwargs)

    def _call(self, method, *args, **kwargs):
        model = self._acquire()
        observe_call = self._diagnostics and self._calls < 4
        try:
            if observe_call:
                self._diagnostic('call_before', call_id=self._calls + 1, private_bytes=_private_memory())
            executor = self._executor()
            if executor is not None:
                _start_startup_ocr_monitor(executor)
            executor = None
            if method is None:
                return model(*args, **kwargs)
            return getattr(model, method)(*args, **kwargs)
        finally:
            if observe_call:
                self._diagnostic('call_returned', call_id=self._calls + 1, private_bytes=_private_memory())
            # A local model or bound method would keep native memory alive until
            # after new callers are admitted. Clear it before closing the lease.
            model = None
            self._finish_call()

    def _recycle_reason(self, now):
        if not self._runtime_recycling:
            return None
        call_limit = self._calls >= self._policy['max_calls']
        age_limit = now - self._created_at >= self._policy['max_age_seconds']
        if not self._diagnostics:
            if call_limit:
                return 'call limit'
            if age_limit:
                return 'model age'
        memory = _private_memory()
        has_memory_growth = (memory is not None and self._memory_baseline is not None
                             and memory - self._memory_baseline >= self._policy['process_growth_mib'] * 1024 ** 2)
        reason = None
        if call_limit:
            reason = 'call limit'
        elif age_limit:
            reason = 'model age'
        elif has_memory_growth:
            # Process-wide growth is a safety signal, not attribution to OCR.
            reason = 'private memory growth'
        self._diagnostic('budget_judgment', private_bytes=memory, baseline_bytes=self._memory_baseline,
                         growth_bytes=memory - self._memory_baseline
                         if memory is not None and self._memory_baseline is not None else None,
                         threshold_bytes=self._policy['process_growth_mib'] * 1024 ** 2,
                         call_limit=call_limit, age_limit=age_limit,
                         growth_limit=has_memory_growth, selected_reason=reason)
        return reason

    def _finish_call(self, counted=True):
        with self._condition:
            self._active -= 1
            self._last_used = time.monotonic()
            self._last_use_kind = 'inference' if counted else 'warmup'
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
                           and now >= self._idle_retry_after
                           and now - self._last_used >= self._policy['idle_seconds'])
            if not can_release:
                return
            self._pending_reason = 'idle'
            self._recycling = True
        self._recycle()

    def _recycle(self):
        reason = self._pending_reason
        model = self._model
        drain_failed = False
        started = time.monotonic()
        logger.info(f'Recycling OCR model {self._name}: {reason}; generation {self._generation}')
        self._log_lifecycle('release_requested', reason=reason)
        if self._diagnostics:
            self._diagnostic('drain_started', reason=reason, private_bytes=_private_memory())
        try:
            # Python inference may have returned just before its native callback
            # leaves the queue. Drain it before dropping any model ownership.
            _drain_model(model)
            logger.info(f'Drained OCR model {self._name}: {time.monotonic() - started:.3f}s')
            self._log_lifecycle('drained', reason=reason, seconds=time.monotonic() - started)
            self._model = None
            model = None
            if self._diagnostics:
                self._diagnostic('references_cleared', manager_model_cleared=True, local_model_cleared=True,
                                 private_bytes=_private_memory())
            collected = gc.collect()
            if self._diagnostics:
                self._diagnostic('cleanup_complete', gc_collected=collected, private_bytes=_private_memory())
            self._log_lifecycle('release_complete', reason=reason, seconds=time.monotonic() - started)
            logger.info(f'Released OpenVINO OCR model {self._name}: {reason}; '
                        f'{time.monotonic() - started:.3f}s; reload on next use')
        except Exception as error:
            drain_failed = True
            self._diagnostic('drain_failed', error_type=type(error).__name__)
            # Failed draining must leave the old model intact. Reset the budget
            # so repeated calls do not retry a failed cleanup on every inference.
            logger.error(f'Could not release OpenVINO OCR model {self._name}', error)
        finally:
            with self._condition:
                self._reset_usage('recycle finished' if not drain_failed else 'drain failed')
                # Failed drains also need an idle cooldown, not a retry each tick.
                if drain_failed:
                    self._idle_retry_after = time.monotonic() + self._policy['idle_seconds']
                self._pending_reason = None
                self._recycling = False
                self._condition.notify_all()

    def _log_lifecycle(self, event, **fields):
        # Scalar events preserve the completed generation before counters reset,
        # without giving diagnostics a reference to native model resources.
        logger.info('OCR_LIFECYCLE ' + json.dumps({
            'event': event, 'name': self._name, 'generation': self._generation,
            'calls': self._calls, 'active_calls': self._active,
            'created_at': self._created_at, 'monotonic': time.monotonic(),
            'age_seconds': time.monotonic() - self._created_at, **fields}))

    def snapshot(self):
        """Return scalar diagnostics without giving the sampler model ownership."""
        with self._condition:
            devices = []
            if self._model is not None:
                for name in ('text_detector', 'text_recognizer', 'text_classifier'):
                    predictor = getattr(self._model, name, None)
                    if predictor is not None:
                        devices.append({'model': name,
                                        'backend': 'openvino' if getattr(predictor, 'is_openvino', False) else 'onnxruntime',
                                        'device': 'NPU' if getattr(predictor, 'is_npu', False) else 'CPU'})
                predictor = None
            return {'name': self._name, 'generation': self._generation,
                    'loaded': self._model is not None, 'active_calls': self._active,
                    'calls': self._calls, 'idle_seconds': time.monotonic() - self._last_used,
                    'created_at': self._created_at if self._model is not None else None,
                    'model_age_seconds': time.monotonic() - self._created_at if self._model is not None else None,
                    'last_use_kind': self._last_use_kind,
                    'recycling': self._recycling, 'pending_reason': self._pending_reason,
                    'policy': dict(self._policy), 'devices': devices}


def ocr_snapshot(executor):
    with executor.lock:
        state = {'paused': executor.paused,
                 'current_task': getattr(executor.current_task, 'name', None),
                 'current_job': getattr(executor.current_job, 'name', None),
                 'queue_length': len(executor._job_queue) + len(executor.onetime_task_queue),
                 'enabled_tasks': sum(task.enabled for task in (*executor.onetime_tasks, *executor.trigger_tasks)),
                 'exclusive': executor._exclusive_owner is not None}
    with executor._ocr_lib_lock:
        instances = [instance for instance in executor._ocr_lib.values() if isinstance(instance, ManagedOCR)]
    state['models'] = [instance.snapshot() for instance in instances]
    state['executor_idle_blocked'] = (state['current_task'] is not None or state['current_job'] is not None
                                      or state['queue_length'] > 0 or state['exclusive']
                                      or (not state['paused'] and state['enabled_tasks'] > 0))
    return state


def _ocr_lib(self, name="default"):
    # Factory and notification threads must receive the same stable proxy.
    with self._ocr_lib_lock:
        options = _ocr_options(self, name)
        uses_openvino = (options.get('lib') == 'onnxocr'
                         and (options.get('params') or {}).get('use_openvino', False))
        if options.get('lib') == 'onnxocr':
            _validate_parameter_support(options)
        if name not in self._ocr_lib:
            if uses_openvino:
                self._ocr_lib[name] = ManagedOCR(self, name)
            else:
                self._ocr_lib[name] = self._create_ocr_lib(name)
        elif uses_openvino and not isinstance(self._ocr_lib[name], ManagedOCR):
            self._ocr_lib[name] = ManagedOCR(self, name, self._ocr_lib[name])
        instance = self._ocr_lib[name]
    return instance


def _monitor_startup_ocr(executor_ref, exit_event):
    while not exit_event.is_set():
        executor = executor_ref()
        if executor is None:
            return
        executor._release_idle_ocr()
        # Waiting must not keep an otherwise unused executor alive.
        executor = None
        if exit_event.wait(1):
            return


def _start_startup_ocr_monitor(executor):
    # One owner checks both policies for the entire executor lifetime, including
    # long non-OCR jobs where the scheduler never enters its idle wait.
    with executor._ocr_lib_lock:
        if getattr(executor, '_ocr_idle_thread', None) is not None:
            return
        thread = threading.Thread(
            target=_monitor_startup_ocr, args=(weakref.ref(executor), executor.exit_event),
            name='OCRIdleRelease', daemon=True)
        executor._ocr_idle_thread = thread
    thread.start()


def _release_idle_ocr(self):
    now = time.monotonic()
    with self.lock:
        has_pending_work = (self.current_job is not None or self.current_task is not None
                            or self._job_queue or self.onetime_task_queue
                            or self._exclusive_owner is not None)
        has_enabled_tasks = not self.paused and any(
            task.enabled for task in (*self.onetime_tasks, *self.trigger_tasks))
        executor_busy = bool(has_pending_work or has_enabled_tasks)
        if executor_busy:
            self._ocr_idle_since = None
        elif getattr(self, '_ocr_idle_since', None) is None:
            self._ocr_idle_since = now
        idle_since = self._ocr_idle_since
        # Model warmup can call back into the executor; never wait on its lock
        # while holding the scheduler lock.
        if not self._ocr_lib_lock.acquire(blocking=False):
            return
        try:
            instances = [instance for instance in self._ocr_lib.values()
                         if isinstance(instance, ManagedOCR)]
        finally:
            self._ocr_lib_lock.release()
    for instance in instances:
        policy = instance._policy
        executor_idle = (not executor_busy and now - idle_since >= policy['idle_seconds'])
        if policy['idle_policy'] == 'ocr_idle' or executor_idle:
            instance.release_idle(now)


def install_ocr_idle_release():
    """Manage OpenVINO model ownership for both active and idle executors."""
    if getattr(TaskExecutor, '_okww_managed_ocr', False):
        return

    run_job = TaskExecutor._run_job
    next_task = TaskExecutor.next_task
    init_default_ocr = TaskExecutor.init_default_ocr

    @wraps(init_default_ocr)
    def init_with_preload(self):
        options = _ocr_options(self, 'default')
        _lifecycle_options(options)
        if options.get('lib') == 'onnxocr':
            _validate_parameter_support(options)
        if options.get('preload', True):
            return init_default_ocr(self)

    def warm_default_ocr(self):
        try:
            instance = self.ocr_lib()
            if isinstance(instance, ManagedOCR):
                instance.prepare()
        except FinishedException:
            pass
        except Exception as error:
            logger.error('Could not preload OCR', error)

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

    # Cached notification callers must share the task executor's model ownership.
    TaskExecutor.ocr_lib = _ocr_lib
    TaskExecutor._release_idle_ocr = _release_idle_ocr
    TaskExecutor.init_default_ocr = init_with_preload
    TaskExecutor._init_default_ocr = warm_default_ocr
    TaskExecutor._run_job = run_job_with_ocr_activity
    TaskExecutor.next_task = next_task_with_ocr_activity
    TaskExecutor._okww_managed_ocr = True
