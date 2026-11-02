import gc
import json
import threading
import unittest
import weakref
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

import psutil

from ok.notification.ppocr import NotificationPPOCR
from ok.task.TaskExecutor import ExecutorJob, TaskExecutor
from ok.task.exceptions import FinishedException, TaskDisabledException
from src import ocr_memory
from src.ocr_memory import ManagedOCR, install_ocr_idle_release


class Model:
    def __init__(self):
        self.text_detector = SimpleNamespace(is_openvino=True, is_npu=False, _async_queue=Mock())
        self.text_recognizer = SimpleNamespace(is_openvino=True, is_npu=False, _async_queue=Mock())
        self.ocr = Mock(return_value=[[[[[0, 0], [10, 0], [10, 10], [0, 10]], ('text', .9)]]])

    def __call__(self, *args, **kwargs):
        return self.ocr(*args, **kwargs)


class TestOcrMemory(unittest.TestCase):
    def setUp(self):
        install_ocr_idle_release()
        self.clock = self.enterContext(patch('src.ocr_memory.time.monotonic', return_value=100.0))
        self.read_private_memory = ocr_memory._private_memory
        self.memory = self.enterContext(patch('src.ocr_memory._private_memory', return_value=0))
        self.enterContext(patch.object(TaskExecutor, 'load_tr'))
        self.enterContext(patch.object(TaskExecutor, 'init_default_ocr'))
        self.enterContext(patch('src.ocr_memory._start_startup_ocr_monitor', lambda executor: None))
        self.models = []
        self.executor = self.make_executor()

    def make_executor(self):
        executor = TaskExecutor(
            SimpleNamespace(interaction=None), exit_event=threading.Event(),
            global_config=Mock(), config={'ocr': {
                'lib': 'onnxocr', 'params': {'use_openvino': True},
            }})

        def create_model(name):
            model = Model()
            self.models.append(weakref.ref(model))
            return model

        executor._create_ocr_lib = Mock(side_effect=create_model)
        return executor

    def prepared_ocr(self, executor=None, name='default'):
        executor = executor or self.executor
        instance = executor.ocr_lib(name)
        if isinstance(instance, ManagedOCR):
            instance.prepare()
        return instance

    def test_opt_in_diagnostics_record_the_used_baseline_before_recycle_reset(self):
        with patch.dict('os.environ', {'OKWW_OCR_DIAGNOSTICS': '1'}), \
                patch.object(ocr_memory.logger, 'info') as log:
            proxy = self.prepared_ocr()
            self.memory.return_value = 513 * 1024**2
            proxy.ocr('frame')
        events = [json.loads(call.args[0].split('OCR_DIAGNOSTIC ', 1)[1])
                  for call in log.call_args_list if call.args[0].startswith('OCR_DIAGNOSTIC ')]
        baseline = next(row for row in events if row['event'] == 'budget_baseline'
                        and row['reason'] == 'model created')
        judgment = next(row for row in events if row['event'] == 'budget_judgment')
        reset = next(row for row in events if row['event'] == 'budget_baseline'
                     and row['reason'] == 'recycle finished')
        self.assertEqual(baseline['baseline_bytes'], 0)
        self.assertEqual(judgment['baseline_bytes'], baseline['baseline_bytes'])
        self.assertEqual(judgment['growth_bytes'], 513 * 1024**2)
        self.assertEqual(judgment['selected_reason'], 'private memory growth')
        self.assertEqual(reset['baseline_bytes'], 513 * 1024**2)
        self.assertFalse(reset['model_present'])
        self.assertFalse(proxy.snapshot()['loaded'])

    def test_diagnostic_memory_reads_are_disabled_by_default(self):
        with patch.dict('os.environ', {}, clear=True), patch.object(ocr_memory.logger, 'info') as log:
            proxy = self.prepared_ocr()
            self.memory.reset_mock()
            proxy.ocr('frame')
        self.memory.assert_called_once_with()
        self.assertFalse(any(call.args[0].startswith('OCR_DIAGNOSTIC ') for call in log.call_args_list))

    def wait_idle(self, now):
        self.clock.return_value = now
        self.executor._release_idle_ocr()
        return self.executor._wait_for_activity(0)

    def pool(self):
        pool = self.enterContext(ThreadPoolExecutor(max_workers=4))
        self.addCleanup(self.executor.exit_event.set)
        return pool

    def test_idle_threshold_unloads_model_and_preserves_proxy(self):
        proxy = self.prepared_ocr()
        reference = self.models[-1]
        self.wait_idle(100)
        self.wait_idle(159.999)
        self.assertIsNotNone(reference())
        self.wait_idle(160)
        self.assertIsNone(reference())
        self.assertIs(self.executor._ocr_lib['default'], proxy)
        self.assertIs(self.prepared_ocr(), proxy)
        self.assertEqual(self.executor._create_ocr_lib.call_count, 2)

    def test_every_busy_state_requires_a_new_full_idle_period(self):
        cases = {
            'current_job': object(), 'current_task': object(),
            '_job_queue': [object()], 'onetime_task_queue': [object()],
            '_exclusive_owner': 'mcp:test',
        }
        for attribute, value in cases.items():
            with self.subTest(attribute=attribute):
                self.clock.return_value = 100
                self.prepared_ocr()
                reference = self.models[-1]
                self.wait_idle(100)
                original = getattr(self.executor, attribute)
                setattr(self.executor, attribute, value)
                self.wait_idle(160)
                self.assertIsNotNone(reference())
                setattr(self.executor, attribute, original)
                self.wait_idle(200)
                self.wait_idle(259.999)
                self.assertIsNotNone(reference())
                self.wait_idle(260)
                self.assertIsNone(reference())

    def test_enabled_tasks_block_idle_release_until_paused(self):
        for attribute in ('onetime_tasks', 'trigger_tasks'):
            with self.subTest(attribute=attribute):
                self.clock.return_value = 100
                self.prepared_ocr()
                reference = self.models[-1]
                self.wait_idle(100)
                setattr(self.executor, attribute, [SimpleNamespace(enabled=True)])
                self.executor.paused = False
                self.wait_idle(160)
                self.assertIsNotNone(reference())
                self.executor.paused = True
                self.wait_idle(200)
                self.wait_idle(260)
                self.assertIsNone(reference())
                setattr(self.executor, attribute, [])

    def test_explicit_prepare_restarts_model_idle_period(self):
        self.prepared_ocr()
        reference = self.models[-1]
        self.wait_idle(100)
        self.clock.return_value = 159
        self.prepared_ocr()
        self.wait_idle(160)
        self.wait_idle(218.999)
        self.assertIsNotNone(reference())
        self.wait_idle(219)
        self.assertIsNone(reference())

    def test_notification_cached_proxy_rebuilds_after_idle_release(self):
        notification = NotificationPPOCR(self.executor.ocr_lib)
        expected = [('text', 0, 0, 10, 10)]
        self.assertEqual(notification.recognize('frame'), expected)
        proxy = notification._instance
        reference = self.models[-1]
        self.wait_idle(100)
        self.wait_idle(160)
        self.assertIsNone(reference())
        self.assertIsNone(notification._instance_factory)
        self.assertEqual(notification.recognize('next frame'), expected)
        self.assertIs(notification._instance, proxy)
        self.assertEqual(self.executor._create_ocr_lib.call_count, 2)

    def test_cached_notification_use_delays_idle_release(self):
        proxy = self.prepared_ocr()
        reference = self.models[-1]
        self.wait_idle(100)
        self.clock.return_value = 150
        proxy.ocr('notification')
        self.wait_idle(160)
        self.wait_idle(209.999)
        self.assertIsNotNone(reference())
        self.wait_idle(210)
        self.assertIsNone(reference())

    def test_idle_release_never_waits_for_initialization_lock(self):
        self.prepared_ocr()
        reference = self.models[-1]
        self.wait_idle(100)
        with self.executor._ocr_lib_lock:
            self.wait_idle(160)
        self.assertIsNotNone(reference())
        self.wait_idle(161)
        self.assertIsNone(reference())

    def test_other_ocr_backends_keep_raw_models(self):
        self.executor.config['ocr'].update({
            'cpu': {'lib': 'onnxocr', 'params': {'use_openvino': False}},
            'rapid': {'lib': 'rapidocr'},
        })
        references = {}
        for name in ('default', 'cpu', 'rapid'):
            instance = self.prepared_ocr(name=name)
            self.assertEqual(isinstance(instance, ManagedOCR), name == 'default')
            references[name] = self.models[-1]
        instance = None
        self.wait_idle(100)
        self.wait_idle(160)
        self.assertIsNone(references['default']())
        self.assertIsNotNone(references['cpu']())
        self.assertIsNotNone(references['rapid']())

    def test_call_budget_recycles_during_a_running_task(self):
        self.executor.current_task = object()
        self.executor.paused = False
        self.executor.trigger_tasks = [SimpleNamespace(enabled=True)]
        proxy = self.prepared_ocr()
        reference = self.models[-1]
        with patch.dict(proxy._policy, max_calls=2):
            result = proxy.ocr('first')
            self.assertIsNotNone(reference())
            self.assertEqual(proxy.ocr('second'), result)
            self.assertIsNone(reference())
            self.assertEqual(proxy.ocr('third'), result)
        self.assertEqual(self.executor._create_ocr_lib.call_count, 2)
        self.assertIs(self.executor._ocr_lib['default'], proxy)

    def test_release_diagnostics_preserve_completed_generation_before_reset(self):
        proxy = self.prepared_ocr()
        self.clock.return_value = 105
        with patch.dict(proxy._policy, max_calls=1), patch.object(ocr_memory.logger, 'info') as log:
            proxy.ocr('frame')
        events = [json.loads(call.args[0].split('OCR_LIFECYCLE ', 1)[1])
                  for call in log.call_args_list if call.args[0].startswith('OCR_LIFECYCLE ')]
        self.assertEqual([event['event'] for event in events],
                         ['release_requested', 'drained', 'release_complete'])
        for event in events:
            self.assertEqual((event['generation'], event['calls'], event['active_calls']), (1, 1, 0))
            self.assertEqual(event['reason'], 'call limit')
            self.assertEqual(event['age_seconds'], 5)
        self.assertIsNone(proxy.snapshot()['created_at'])
        self.assertEqual(proxy.snapshot()['calls'], 0)

    def test_model_age_recycles_at_inference_boundary(self):
        proxy = self.prepared_ocr()
        reference = self.models[-1]
        self.clock.return_value += ocr_memory.OCR_MAX_AGE_SECONDS
        proxy.ocr('frame')
        self.assertIsNone(reference())

    def test_private_memory_growth_recycles_model(self):
        proxy = self.prepared_ocr()
        reference = self.models[-1]
        self.memory.return_value = ocr_memory.OCR_MEMORY_GROWTH_BYTES - 1
        proxy.ocr('first')
        self.assertIsNotNone(reference())
        self.memory.return_value += 1
        proxy.ocr('second')
        self.assertIsNone(reference())
        proxy.ocr('third')
        reference = self.models[-1]
        proxy.ocr('fourth')
        self.assertIsNotNone(reference())

    def test_call_budget_still_works_without_private_memory_metric(self):
        self.memory.return_value = None
        proxy = self.prepared_ocr()
        reference = self.models[-1]
        with patch.dict(proxy._policy, max_calls=1):
            proxy.ocr('frame')
        self.assertIsNone(reference())

    def test_unavailable_process_metric_is_optional(self):
        with patch('src.ocr_memory.psutil.Process', side_effect=psutil.AccessDenied()):
            self.assertIsNone(self.read_private_memory())

    def test_npu_uses_idle_eviction_without_runtime_rotation(self):
        model = Model()
        model.text_detector.is_npu = True
        model.text_recognizer.is_npu = True
        self.executor._create_ocr_lib.side_effect = None
        self.executor._create_ocr_lib.return_value = model
        proxy = self.prepared_ocr()
        with patch.dict(proxy._policy, max_calls=1):
            proxy.ocr('frame')
            proxy.ocr('frame')
        self.assertIs(proxy._model, model)
        self.assertEqual(self.executor._create_ocr_lib.call_count, 1)
        self.wait_idle(100)
        self.wait_idle(160)
        self.assertIsNone(proxy._model)

    def test_callable_interface_preserves_arguments_and_results(self):
        proxy = self.prepared_ocr()
        result = proxy('frame', cls=False)
        self.assertEqual(result, self.models[-1]().ocr.return_value)
        self.models[-1]().ocr.assert_called_once_with('frame', cls=False)

    def test_npu_classifier_does_not_enter_cpu_rotation(self):
        model = Model()
        model.text_classifier = SimpleNamespace(is_openvino=True, is_npu=True, _async_queue=Mock())
        self.executor._create_ocr_lib.side_effect = None
        self.executor._create_ocr_lib.return_value = model
        proxy = self.prepared_ocr()
        with patch.dict(proxy._policy, max_calls=1):
            proxy.ocr('frame')
        self.assertIs(proxy._model, model)
        self.wait_idle(100)
        self.wait_idle(160)
        self.assertIsNone(proxy._model)
        model.text_classifier._async_queue.wait_all.assert_called_once()

    def test_initialization_failure_allows_retry(self):
        create = self.executor._create_ocr_lib.side_effect
        self.executor._create_ocr_lib.side_effect = ValueError('initialization failed')
        with self.assertRaisesRegex(ValueError, 'initialization failed'):
            self.prepared_ocr()
        self.executor._create_ocr_lib.side_effect = create
        self.assertIsInstance(self.prepared_ocr(), ManagedOCR)

    def test_concurrent_initialization_creates_only_one_model(self):
        entered = threading.Event()
        finish = threading.Event()
        self.addCleanup(finish.set)
        create = self.executor._create_ocr_lib.side_effect

        def slow_create(name):
            entered.set()
            if not finish.wait(5):
                raise TimeoutError('test initialization did not finish')
            return create(name)

        self.executor._create_ocr_lib.side_effect = slow_create
        pool = self.pool()
        first = pool.submit(self.prepared_ocr)
        self.assertTrue(entered.wait(5))
        second = pool.submit(self.prepared_ocr)
        self.wait_idle(100)
        self.wait_idle(160)
        finish.set()
        self.assertIs(first.result(5), second.result(5))
        self.assertEqual(self.executor._create_ocr_lib.call_count, 1)

    def test_exception_and_cancellation_do_not_leak_active_calls(self):
        proxy = self.prepared_ocr()
        for error in (ValueError('inference failed'), TaskDisabledException()):
            with self.subTest(error=type(error).__name__):
                self.models[-1]().ocr.side_effect = error
                with patch.dict(proxy._policy, max_calls=1):
                    with self.assertRaises(type(error)):
                        proxy.ocr('frame')
                gc.collect()
                self.assertIsNone(proxy._model)
                self.assertIsNotNone(proxy.ocr('next frame'))

    def test_drain_failure_preserves_old_model_and_allows_further_calls(self):
        proxy = self.prepared_ocr()
        reference = self.models[-1]
        reference().text_detector._async_queue.wait_all.side_effect = RuntimeError('drain failed')
        with patch.dict(proxy._policy, max_calls=1), patch.object(ocr_memory.logger, 'error') as log:
            result = proxy.ocr('frame')
            self.assertIs(proxy._model, reference())
            log.assert_called_once()
        self.assertEqual(proxy.ocr('next frame'), result)

    def test_finalizer_runs_outside_all_locks_and_before_new_model(self):
        proxy = self.prepared_ocr()
        reference = self.models[-1]
        available_locks = []
        drained = []
        reference().text_detector._async_queue.wait_all.side_effect = lambda: drained.append('detector')
        reference().text_recognizer._async_queue.wait_all.side_effect = lambda: drained.append('recognizer')

        def on_destroy():
            for lock in (self.executor.lock, self.executor._ocr_lib_lock, proxy._condition):
                acquired = lock.acquire(blocking=False)
                available_locks.append(acquired)
                if acquired:
                    lock.release()

        weakref.finalize(reference(), on_destroy)
        with patch.dict(proxy._policy, max_calls=1):
            proxy.ocr('first')
        self.assertEqual(available_locks, [True, True, True])
        self.assertEqual(drained, ['detector', 'recognizer'])
        self.assertIsNone(reference())
        self.assertEqual(self.executor._create_ocr_lib.call_count, 1)
        proxy.ocr('second')
        self.assertEqual(self.executor._create_ocr_lib.call_count, 2)

    def test_overlapping_inferences_close_admission_before_rotation(self):
        proxy = self.prepared_ocr()
        reference = self.models[-1]
        started = {name: threading.Event() for name in ('first', 'second')}
        finish = {name: threading.Event() for name in ('first', 'second')}
        waiting = threading.Event()
        for event in finish.values():
            self.addCleanup(event.set)

        def infer(frame):
            started[frame].set()
            if not finish[frame].wait(5):
                raise TimeoutError('test inference did not finish')
            return frame

        reference().ocr.side_effect = infer
        wait = proxy._condition.wait

        def wait_for_rotation(timeout):
            waiting.set()
            return wait(timeout)

        pool = self.pool()
        with patch.dict(proxy._policy, max_calls=1), \
                patch.object(proxy._condition, 'wait', side_effect=wait_for_rotation):
            first = pool.submit(proxy.ocr, 'first')
            second = pool.submit(proxy.ocr, 'second')
            self.assertTrue(started['first'].wait(5))
            self.assertTrue(started['second'].wait(5))
            finish['first'].set()
            self.assertEqual(first.result(5), 'first')
            third = pool.submit(proxy.ocr, 'third')
            self.assertTrue(waiting.wait(5))
            self.assertIsNotNone(reference())
            self.assertEqual(self.executor._create_ocr_lib.call_count, 1)
            finish['second'].set()
            self.assertEqual(second.result(5), 'second')
            self.assertIsNotNone(third.result(5))
        self.assertIsNone(reference())
        self.assertEqual(self.executor._create_ocr_lib.call_count, 2)

    def test_native_drain_blocks_requests_and_shutdown_cancels_waiters(self):
        proxy = self.prepared_ocr()
        reference = self.models[-1]
        draining = threading.Event()
        finish_drain = threading.Event()
        self.addCleanup(finish_drain.set)

        def drain():
            draining.set()
            if not finish_drain.wait(5):
                raise TimeoutError('test drain did not finish')

        reference().text_detector._async_queue.wait_all.side_effect = drain
        pool = self.pool()
        with patch.dict(proxy._policy, max_calls=1):
            first = pool.submit(proxy.ocr, 'first')
            self.assertTrue(draining.wait(5))
            second = pool.submit(proxy.ocr, 'second')
            self.executor.exit_event.set()
            with self.assertRaises(FinishedException):
                second.result(5)
            self.assertIsNotNone(reference())
            self.assertEqual(self.executor._create_ocr_lib.call_count, 1)
            finish_drain.set()
            first.result(5)
        self.assertIsNone(reference())

    def test_idle_release_does_not_interrupt_an_active_cached_call(self):
        proxy = self.prepared_ocr()
        reference = self.models[-1]
        entered = threading.Event()
        finish = threading.Event()
        self.addCleanup(finish.set)

        def infer(frame):
            entered.set()
            if not finish.wait(5):
                raise TimeoutError('test inference did not finish')
            return frame

        reference().ocr.side_effect = infer
        self.wait_idle(100)
        pool = self.pool()
        future = pool.submit(proxy.ocr, 'frame')
        self.assertTrue(entered.wait(5))
        self.wait_idle(160)
        self.assertIsNotNone(reference())
        finish.set()
        self.assertEqual(future.result(5), 'frame')
        self.wait_idle(219.999)
        self.assertIsNotNone(reference())
        self.wait_idle(220)
        self.assertIsNone(reference())

    def test_cancelled_executor_job_does_not_wait_for_model_recycling(self):
        proxy = self.prepared_ocr()
        draining = threading.Event()
        finish_drain = threading.Event()
        waiting = threading.Event()
        self.addCleanup(finish_drain.set)

        def drain():
            draining.set()
            if not finish_drain.wait(5):
                raise TimeoutError('test drain did not finish')

        self.models[-1]().text_detector._async_queue.wait_all.side_effect = drain
        job = ExecutorJob(self.executor, lambda: None)

        def run_waiting_job():
            self.executor.thread = threading.current_thread()
            self.executor.current_job = job
            return proxy.ocr('cancelled')

        wait = proxy._condition.wait

        def wait_for_rotation(timeout):
            waiting.set()
            return wait(timeout)

        pool = self.pool()
        with patch.dict(proxy._policy, max_calls=1), \
                patch.object(proxy._condition, 'wait', side_effect=wait_for_rotation):
            first = pool.submit(proxy.ocr, 'first')
            self.assertTrue(draining.wait(5))
            second = pool.submit(run_waiting_job)
            self.assertTrue(waiting.wait(5))
            job._cancel_requested.set()
            with self.assertRaises(TaskDisabledException):
                second.result(5)
            self.assertFalse(first.done())
            finish_drain.set()
            first.result(5)
        self.executor.current_job = None

    def test_notification_cached_proxy_recycles_during_active_task(self):
        self.executor.current_task = object()
        notification = NotificationPPOCR(self.executor.ocr_lib)
        proxy = self.executor.ocr_lib()
        with patch.dict(proxy._policy, max_calls=1):
            expected = notification.recognize('first')
            self.assertIsNone(self.models[-1]())
            self.assertEqual(notification.recognize('second'), expected)
        self.assertEqual(self.executor._create_ocr_lib.call_count, 2)

    def test_jobs_without_ocr_restart_idle_period_on_all_outcomes(self):
        def fail():
            raise ValueError('job failed')

        for outcome in ('success', 'failure', 'cancelled'):
            with self.subTest(outcome=outcome):
                self.clock.return_value = 100
                self.prepared_ocr()
                reference = self.models[-1]
                self.wait_idle(100)
                job = ExecutorJob(self.executor, fail if outcome == 'failure' else lambda: 42)
                if outcome == 'cancelled':
                    job.future.cancel()
                self.executor._run_job(job)
                self.assertTrue(job.future.done())
                if outcome == 'failure':
                    self.assertIsInstance(job.future.exception(), ValueError)
                elif outcome == 'success':
                    self.assertEqual(job.future.result(), 42)
                self.wait_idle(160)
                self.wait_idle(219.999)
                self.assertIsNotNone(reference())
                self.wait_idle(220)
                self.assertIsNone(reference())

    def test_gui_dispatch_without_ocr_restarts_idle_period(self):
        self.prepared_ocr()
        reference = self.models[-1]
        self.wait_idle(100)
        task = SimpleNamespace(enabled=True, name='GUI task')
        self.executor.onetime_tasks = [task]
        self.assertIs(self.executor.next_task()[0], task)
        self.executor.onetime_tasks.clear()
        self.wait_idle(160)
        self.wait_idle(219.999)
        self.assertIsNotNone(reference())
        self.wait_idle(220)
        self.assertIsNone(reference())

    def test_original_wait_keeps_wakeup_semantics(self):
        self.executor._wake_executor()
        self.assertTrue(self.executor._wait_for_activity(1, wake_version=0))
        self.assertFalse(self.executor._wait_for_activity(0))

    def run_scheduler(self, kind, outcome):
        executor = self.make_executor()
        self.prepared_ocr(executor)
        reference = self.models[-1]

        def run():
            if kind == 'gui':
                task.enabled = False
            if outcome == 'failure':
                raise ValueError('expected task failure')
            if outcome == 'cancelled':
                raise TaskDisabledException()

        if kind == 'gui':
            task = SimpleNamespace(
                name='GUI test', enabled=True, running=False, run=run,
                exit_after_task=False, config={}, disable=Mock(), info_set=Mock(),
                _app=SimpleNamespace(tr=str), on_destroy=Mock())
            executor.onetime_tasks.append(task)
            executor.paused = False
            executor.next_frame = Mock(return_value=object())
        else:
            executor._job_queue.append(ExecutorJob(executor, run))

        def advance_idle(timeout):
            self.clock.return_value += 60
            executor._release_idle_ocr()
            if self.clock.return_value >= 220:
                executor.exit_event.set()

        self.clock.return_value = 100
        with patch.object(executor._wake_condition, 'wait', side_effect=advance_idle), \
                patch('ok.task.TaskExecutor.communicate', Mock()), \
                patch('ok.task.TaskExecutor.prevent_sleeping'):
            executor.execute()
        self.assertIsNone(reference())
        self.assertIsNone(executor.current_task)
        self.assertIsNone(executor.current_job)

    def test_scheduler_releases_after_gui_success_failure_and_cancellation(self):
        for outcome in ('success', 'failure', 'cancelled'):
            with self.subTest(outcome=outcome):
                self.clock.return_value = 100
                self.run_scheduler('gui', outcome)

    def test_scheduler_releases_after_job_success_failure_and_cancellation(self):
        for outcome in ('success', 'failure', 'cancelled'):
            with self.subTest(outcome=outcome):
                self.clock.return_value = 100
                self.run_scheduler('job', outcome)

    def test_proxy_wraps_a_preexisting_raw_model(self):
        model = self.executor._create_ocr_lib('default')
        self.executor._ocr_lib['default'] = model
        proxy = self.prepared_ocr()
        self.assertIsInstance(proxy, ManagedOCR)
        self.assertIs(proxy._model, model)
        self.assertEqual(self.executor._create_ocr_lib.call_count, 1)

    def test_proxy_does_not_keep_executor_alive(self):
        proxy = self.prepared_ocr()
        reference = weakref.ref(self.executor)
        self.executor = None
        gc.collect()
        self.assertIsNone(reference())
        self.assertIsNotNone(proxy)

    def test_handle_lookup_neither_loads_nor_refreshes_last_use(self):
        proxy = self.executor.ocr_lib()
        self.executor._create_ocr_lib.assert_not_called()
        proxy.ocr('first')
        reference = self.models[-1]
        self.wait_idle(100)
        self.clock.return_value = 159
        self.assertIs(self.executor.ocr_lib(), proxy)
        self.wait_idle(160)
        self.assertIsNone(reference())

    def test_ocr_idle_releases_during_non_ocr_work_and_notification_reloads(self):
        self.executor.config['ocr']['default']['lifecycle'] = {'idle_policy': 'ocr_idle'}
        notification = NotificationPPOCR(self.executor.ocr_lib)
        expected = notification.recognize('first notification')
        proxy = notification._instance
        for now in (160, 220, 280):
            self.executor.current_job = object()
            self.executor._exclusive_owner = 'mcp:test'
            self.executor.trigger_tasks = [SimpleNamespace(enabled=True)]
            self.executor.paused = False
            self.wait_idle(now)
            self.assertIsNone(proxy._model)
            self.assertEqual(notification.recognize('next notification'), expected)
            self.assertIs(notification._instance, proxy)
        self.assertEqual(proxy.snapshot()['generation'], 4)

    def test_shutdown_during_creation_drains_without_publishing_model(self):
        proxy = self.executor.ocr_lib()
        create = self.executor._create_ocr_lib.side_effect

        def create_on_exit(name):
            model = create(name)
            self.executor.exit_event.set()
            return model

        self.executor._create_ocr_lib.side_effect = create_on_exit
        with self.assertRaises(FinishedException):
            proxy.ocr('frame')
        self.assertIsNone(proxy._model)
        self.assertEqual(proxy._active, 0)
        self.assertFalse(proxy._recycling)
        self.assertIsNone(self.models[-1]())

    def test_failed_idle_drain_waits_a_full_interval_before_retry(self):
        proxy = self.prepared_ocr()
        queue = proxy._model.text_detector._async_queue
        queue.wait_all.side_effect = RuntimeError('drain failed')
        self.wait_idle(100)
        self.wait_idle(160)
        self.wait_idle(161)
        queue.wait_all.assert_called_once()
        self.wait_idle(220)
        self.assertEqual(queue.wait_all.call_count, 2)

    def test_shutdown_creation_with_failed_drain_keeps_native_owner(self):
        proxy = self.executor.ocr_lib()
        model = Model()
        model.text_detector._async_queue.wait_all.side_effect = RuntimeError('busy native queue')

        def create_on_exit(name):
            self.executor.exit_event.set()
            return model

        self.executor._create_ocr_lib.side_effect = create_on_exit
        with self.assertRaises(FinishedException):
            proxy.ocr('frame')
        self.assertIs(proxy._model, model)
        with self.assertRaises(FinishedException):
            proxy.ocr('next frame')
        self.executor._create_ocr_lib.assert_called_once()

    def test_repeated_install_does_not_wrap_twice(self):
        methods = (TaskExecutor.ocr_lib, TaskExecutor._wait_for_activity,
                   TaskExecutor._run_job, TaskExecutor.next_task)
        install_ocr_idle_release()
        self.assertEqual(methods, (TaskExecutor.ocr_lib, TaskExecutor._wait_for_activity,
                                  TaskExecutor._run_job, TaskExecutor.next_task))

    def test_native_idle_support_does_not_skip_runtime_recycling(self):
        native = type('NativeExecutor', (), {
            '_release_idle_ocr': Mock(), '_wait_for_activity': Mock(),
            '_run_job': Mock(), 'next_task': Mock(), 'init_default_ocr': Mock(),
        })
        with patch('src.ocr_memory.TaskExecutor', native):
            install_ocr_idle_release()
        self.assertTrue(native._okww_managed_ocr)
        self.assertIs(native._release_idle_ocr, ocr_memory._release_idle_ocr)


class TestStartupOcrMemory(unittest.TestCase):
    def setUp(self):
        install_ocr_idle_release()
        self.clock = self.enterContext(patch('src.ocr_memory.time.monotonic', return_value=100.0))
        self.enterContext(patch('src.ocr_memory._private_memory', return_value=0))
        self.enterContext(patch.object(TaskExecutor, 'load_tr'))
        self.enterContext(patch.object(TaskExecutor, 'init_default_ocr'))
        self.exit_event = threading.Event()
        self.executor = TaskExecutor(
            SimpleNamespace(interaction=None), exit_event=self.exit_event,
            global_config=Mock(), config={'ocr': {
                'lib': 'onnxocr', 'params': {'use_openvino': True},
            }})
        self.models = []

        def create_model(name):
            model = Model()
            self.models.append(weakref.ref(model))
            return model

        self.executor._create_ocr_lib = Mock(side_effect=create_model)
        self.addCleanup(self.stop_monitor)

    def stop_monitor(self):
        self.exit_event.set()
        if self.executor is not None:
            self.executor._wake_executor()
            thread = getattr(self.executor, '_ocr_idle_thread', None)
            if thread is not None:
                thread.join(5)
                self.assertFalse(thread.is_alive())
            if self.executor.thread is not None:
                self.executor.thread.join(5)
                self.assertFalse(self.executor.thread.is_alive())

    def run_startup(self, steps):
        observations = []
        steps = iter(steps)

        def advance_idle(timeout):
            observations.append((self.clock.return_value, self.models[-1]() is not None))
            step = next(steps, None)
            if step is None:
                return True
            now, action = step
            self.clock.return_value = now
            if action is not None:
                action()
            return False

        with patch.object(self.exit_event, 'wait', side_effect=advance_idle):
            self.executor._init_default_ocr()
            thread = getattr(self.executor, '_ocr_idle_thread', None)
            if thread is not None:
                thread.join(5)
                self.assertFalse(thread.is_alive())
        return observations

    def test_startup_idle_releases_at_sixty_seconds_without_running_tasks(self):
        observations = self.run_startup([(159.999, None), (160, None)])
        self.assertEqual(observations, [(100, True), (159.999, True), (160, False)])
        self.assertIsNone(self.models[-1]())
        self.assertIsNone(self.executor.thread)
        self.assertTrue(self.executor.paused)
        self.assertIsNone(self.executor.current_job)
        self.assertIsNone(self.executor.current_task)

    def test_startup_ocr_use_delays_release_and_cached_proxy_reloads(self):
        notification = NotificationPPOCR(self.executor.ocr_lib)
        expected = []

        def recognize():
            expected.extend(notification.recognize('notification'))

        observations = self.run_startup([(150, recognize), (160, None), (209.999, None), (210, None)])
        self.assertEqual(observations, [(100, True), (150, True), (160, True),
                                        (209.999, True), (210, False)])
        proxy = self.executor._ocr_lib['default']
        self.assertIsNone(proxy._model)
        self.assertEqual(notification.recognize('after release'), expected)
        self.assertIs(notification._instance, proxy)
        self.assertEqual(self.executor._create_ocr_lib.call_count, 2)

    def test_startup_monitor_stops_on_shutdown_without_releasing_early(self):
        observations = self.run_startup([(130, self.exit_event.set)])
        self.assertEqual(observations, [(100, True)])
        self.assertIsNotNone(self.models[-1]())
        self.assertIsNone(self.executor.thread)

    def test_monitor_remains_owner_after_first_job(self):
        jobs = []

        def submit_job():
            jobs.append(self.executor.submit(lambda: 42))

        observations = self.run_startup([(130, submit_job)])
        self.assertEqual(observations, [(100, True), (130, True)])
        self.assertEqual(jobs[0].future.result(5), 42)
        self.assertTrue(self.executor.thread.is_alive())
        self.assertTrue(self.executor.paused)
        self.assertIsNotNone(self.models[-1]())

    def test_cached_ocr_access_keeps_one_startup_monitor(self):
        threads = []

        def use_cached_ocr():
            threads.append(self.executor._ocr_idle_thread)
            self.executor.ocr_lib()
            threads.append(self.executor._ocr_idle_thread)

        observations = self.run_startup([(150, use_cached_ocr), (210, None)])
        self.assertEqual(observations, [(100, True), (150, True), (210, False)])
        self.assertIs(threads[0], threads[1])

    def test_startup_monitor_does_not_keep_executor_alive_while_waiting(self):
        waiting = threading.Event()
        finish = threading.Event()
        self.addCleanup(finish.set)

        def wait(timeout):
            waiting.set()
            return finish.wait(5)

        with patch.object(self.exit_event, 'wait', side_effect=wait):
            self.executor._init_default_ocr()
            thread = self.executor._ocr_idle_thread
            self.assertTrue(waiting.wait(5))
            reference = weakref.ref(self.executor)
            self.executor = None
            gc.collect()
            self.assertIsNone(reference())
            finish.set()
            thread.join(5)
            self.assertFalse(thread.is_alive())


class TestOcrPreload(unittest.TestCase):
    def test_disabled_preload_starts_no_loader_and_first_notification_loads(self):
        install_ocr_idle_release()
        event = threading.Event()
        with patch.object(TaskExecutor, 'load_tr'), patch('src.ocr_memory._start_startup_ocr_monitor'):
            executor = TaskExecutor(SimpleNamespace(interaction=None), exit_event=event,
                                    global_config=Mock(), config={'ocr': {
                                        'lib': 'onnxocr', 'preload': False,
                                        'params': {'use_openvino': True}}})
            self.assertIsNone(executor._ocr_init_thread)
            self.assertEqual(executor._ocr_lib, {})
            executor._create_ocr_lib = Mock(side_effect=lambda name: Model())
            notification = NotificationPPOCR(executor.ocr_lib)
            self.assertEqual(notification.recognize('frame'), [('text', 0, 0, 10, 10)])
            executor._create_ocr_lib.assert_called_once_with('default')
        event.set()


class TestOcrCapabilities(unittest.TestCase):
    def test_passthrough_cannot_silently_forward_a_misspelling_to_an_old_backend(self):
        with patch.object(TaskExecutor, 'ocr_params_passthrough', True, create=True):
            with self.assertRaisesRegex(ValueError, 'rec_batch_nmu'):
                ocr_memory._validate_parameter_support({'params': {'rec_batch_nmu': 1}})

    def test_original_factory_accepts_only_compatible_parameters(self):
        with patch.object(TaskExecutor, 'ocr_params_passthrough', False, create=True):
            ocr_memory._validate_parameter_support({'params': {
                'use_openvino': True, 'use_npu': False, 'rec_batch_num': 6,
                'openvino_num_requests': 1, 'openvino_compile_config': {'CPU': {}, 'NPU': {}}}})
            with self.assertRaisesRegex(RuntimeError, 'patched ok-script'):
                ocr_memory._validate_parameter_support({'params': {'rec_batch_num': 1}})

    def test_passthrough_factory_rejects_compile_config_without_backend_capability(self):
        class OriginalBackend:
            def __init__(self, model_dir):
                pass

        with patch.object(TaskExecutor, 'ocr_params_passthrough', True, create=True), \
                patch('onnxocr.predict_base.PredictBase', OriginalBackend):
            with self.assertRaisesRegex(RuntimeError, 'patched onnxocr-ppocrv5'):
                ocr_memory._validate_parameter_support({'params': {
                    'openvino_compile_config': {'CPU': {'CPU_RUNTIME_CACHE_CAPACITY': 200}}}})

    def test_compile_capability_does_not_depend_on_version_string(self):
        class Backend:
            def __init__(self, openvino_compile_config=None):
                pass

        with patch.object(TaskExecutor, 'ocr_params_passthrough', True, create=True), \
                patch('onnxocr.predict_base.PredictBase', Backend):
            ocr_memory._validate_parameter_support({'params': {
                'openvino_compile_config': {'CPU': {'CPU_RUNTIME_CACHE_CAPACITY': 0}}}})


if __name__ == '__main__':
    unittest.main()
