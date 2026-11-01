import threading
import unittest
import weakref
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ok.task.TaskExecutor import ExecutorJob, TaskExecutor
from ok.task.exceptions import TaskDisabledException
from src.ocr_memory import install_ocr_idle_release


class Model:
    pass


class TestOcrMemory(unittest.TestCase):
    def setUp(self):
        install_ocr_idle_release()
        self.clock = self.enterContext(patch('src.ocr_memory.time.monotonic', return_value=100.0))
        self.enterContext(patch.object(TaskExecutor, 'load_tr'))
        self.enterContext(patch.object(TaskExecutor, 'init_default_ocr'))
        self.executor = self.make_executor()

    def make_executor(self):
        executor = TaskExecutor(
            SimpleNamespace(interaction=None), exit_event=threading.Event(),
            global_config=Mock(), config={'ocr': {
                'lib': 'onnxocr', 'params': {'use_openvino': True},
            }})
        executor._create_ocr_lib = Mock(side_effect=lambda name: Model())
        return executor

    def wait_idle(self, now):
        self.clock.return_value = now
        # A zero timeout exercises the scheduler hook without real-time sleeps.
        return self.executor._wait_for_activity(0)

    def test_idle_threshold_releases_and_next_access_recreates_model(self):
        reference = weakref.ref(self.executor.ocr_lib())
        self.wait_idle(100)
        self.wait_idle(159.999)
        self.assertIsNotNone(reference())
        self.wait_idle(160)
        self.assertIsNone(reference())
        self.assertEqual(self.executor._ocr_lib, {})
        self.assertIsInstance(self.executor.ocr_lib(), Model)
        self.assertEqual(self.executor._create_ocr_lib.call_count, 2)

    def test_every_busy_state_requires_a_new_full_idle_period(self):
        cases = {
            'current_job': object(), 'current_task': object(),
            '_job_queue': [object()], 'onetime_task_queue': [object()],
            '_exclusive_owner': 'mcp:test',
        }
        for attribute, value in cases.items():
            with self.subTest(attribute=attribute):
                reference = weakref.ref(self.executor.ocr_lib())
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

    def test_enabled_tasks_block_release_until_scheduler_is_paused(self):
        for attribute in ('onetime_tasks', 'trigger_tasks'):
            with self.subTest(attribute=attribute):
                reference = weakref.ref(self.executor.ocr_lib())
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

    def test_cached_ocr_access_restarts_idle_period(self):
        reference = weakref.ref(self.executor.ocr_lib())
        self.wait_idle(100)
        self.clock.return_value = 159
        self.executor.ocr_lib()
        self.wait_idle(160)
        self.wait_idle(219.999)
        self.assertIsNotNone(reference())
        self.wait_idle(220)
        self.assertIsNone(reference())
        self.assertEqual(self.executor._create_ocr_lib.call_count, 1)

    def test_release_never_waits_for_model_initialization_lock(self):
        reference = weakref.ref(self.executor.ocr_lib())
        self.wait_idle(100)
        with self.executor._ocr_lib_lock:
            self.wait_idle(160)
        self.assertIsNotNone(reference())
        self.wait_idle(161)
        self.assertIsNone(reference())

    def test_other_ocr_backends_keep_their_models(self):
        self.executor.config['ocr'].update({
            'cpu': {'lib': 'onnxocr', 'params': {'use_openvino': False}},
            'rapid': {'lib': 'rapidocr'},
        })
        references = {name: weakref.ref(self.executor.ocr_lib(name))
                      for name in ('default', 'cpu', 'rapid')}
        self.wait_idle(100)
        self.wait_idle(160)
        self.assertIsNone(references['default']())
        self.assertIsNotNone(references['cpu']())
        self.assertIsNotNone(references['rapid']())

    def test_concurrent_borrower_keeps_model_alive_until_return(self):
        borrowed = threading.Event()
        finished = threading.Event()
        references = []

        def borrow_model():
            model = self.executor.ocr_lib()
            references.append(weakref.ref(model))
            borrowed.set()
            finished.wait(5)

        worker = threading.Thread(target=borrow_model)
        worker.start()
        self.addCleanup(worker.join, 5)
        self.addCleanup(finished.set)
        self.assertTrue(borrowed.wait(5))
        self.wait_idle(100)
        self.wait_idle(160)
        self.assertEqual(self.executor._ocr_lib, {})
        self.assertIsNotNone(references[0]())
        self.assertIsNot(self.executor.ocr_lib(), references[0]())
        finished.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertIsNone(references[0]())

    def test_model_finalizer_runs_outside_executor_locks(self):
        available_locks = []
        executor = self.executor

        def on_destroy():
            for lock in (executor.lock, executor._ocr_lib_lock):
                acquired = lock.acquire(blocking=False)
                available_locks.append(acquired)
                if acquired:
                    lock.release()

        weakref.finalize(self.executor.ocr_lib(), on_destroy)
        self.wait_idle(100)
        self.wait_idle(160)
        self.assertEqual(available_locks, [True, True])

    def test_jobs_without_ocr_restart_idle_period_on_all_outcomes(self):
        def fail():
            raise ValueError('job failed')

        for outcome in ('success', 'failure', 'cancelled'):
            with self.subTest(outcome=outcome):
                reference = weakref.ref(self.executor.ocr_lib())
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
                self.assertIsNone(self.executor.current_job)
                self.wait_idle(160)
                self.wait_idle(219.999)
                self.assertIsNotNone(reference())
                self.wait_idle(220)
                self.assertIsNone(reference())

    def test_gui_task_dispatch_without_ocr_restarts_idle_period(self):
        reference = weakref.ref(self.executor.ocr_lib())
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
        reference = weakref.ref(executor.ocr_lib())

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
            job = ExecutorJob(executor, run)
            executor._job_queue.append(job)

        def advance_idle(timeout):
            self.clock.return_value += 60
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
        if kind == 'job':
            self.assertTrue(job.future.done())

    def test_scheduler_releases_after_gui_success_failure_and_cancellation(self):
        for outcome in ('success', 'failure', 'cancelled'):
            with self.subTest(outcome=outcome):
                self.run_scheduler('gui', outcome)

    def test_scheduler_releases_after_job_success_failure_and_cancellation(self):
        for outcome in ('success', 'failure', 'cancelled'):
            with self.subTest(outcome=outcome):
                self.run_scheduler('job', outcome)

    def test_repeated_install_and_native_support_do_not_wrap_twice(self):
        methods = (TaskExecutor.ocr_lib, TaskExecutor._wait_for_activity,
                   TaskExecutor._run_job, TaskExecutor.next_task)
        install_ocr_idle_release()
        self.assertEqual(methods, (TaskExecutor.ocr_lib, TaskExecutor._wait_for_activity,
                                  TaskExecutor._run_job, TaskExecutor.next_task))
        native = type('NativeExecutor', (), {'_release_idle_ocr': Mock()})
        with patch('src.ocr_memory.TaskExecutor', native):
            install_ocr_idle_release()
        self.assertNotIn('ocr_lib', vars(native))


if __name__ == '__main__':
    unittest.main()
