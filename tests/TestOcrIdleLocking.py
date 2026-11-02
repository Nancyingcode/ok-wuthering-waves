import threading
import unittest
from unittest.mock import Mock, patch

from src import ocr_memory
from src.ocr_memory import ManagedOCR


class Executor:
    def __init__(self):
        self.lock = threading.Lock()
        self._ocr_lib_lock = threading.Lock()
        self._ocr_idle_since = 100
        self.current_job = self.current_task = self._exclusive_owner = None
        self._job_queue = self.onetime_task_queue = self.onetime_tasks = self.trigger_tasks = []
        self.paused = True
        proxy = ManagedOCR.__new__(ManagedOCR)
        proxy._policy = {'idle_policy': 'executor_idle', 'idle_seconds': 60}
        proxy.release_idle = Mock()
        self._ocr_lib = {'default': proxy}
        self.watch_writes = False
        self.writes_locked = []

    def __setattr__(self, name, value):
        if name == '_ocr_idle_since' and getattr(self, 'watch_writes', False):
            self.writes_locked.append(self.lock.locked())
        object.__setattr__(self, name, value)

    def _run_job(self, job):
        if not self.lock.acquire(timeout=2):
            raise AssertionError('Wrapper retained scheduler lock across original job')
        self.lock.release()

    def next_task(self):
        if not self.lock.acquire(timeout=2):
            raise AssertionError('Wrapper retained scheduler lock across original next_task')
        self.lock.release()
        return object(), None

    def init_default_ocr(self):
        pass


class TestOcrIdleLocking(unittest.TestCase):
    def install(self, cls):
        with patch.object(ocr_memory, 'TaskExecutor', cls):
            ocr_memory.install_ocr_idle_release()

    def test_wrappers_reset_under_lock_but_call_original_methods_outside_it(self):
        class Subject(Executor):
            pass

        self.install(Subject)
        executor = Subject()
        executor.watch_writes = True
        executor._run_job(None)
        executor.next_task()
        self.assertEqual(executor.writes_locked, [True, True])

    def test_reset_cannot_change_idle_timestamp_between_locked_reads(self):
        read_started, writer_attempt, release_read = (threading.Event() for _ in range(3))

        class GateLock:
            def __init__(self):
                self.lock = threading.Lock()

            def acquire(self, *args, **kwargs):
                if threading.current_thread().name == 'JobReset':
                    writer_attempt.set()
                return self.lock.acquire(*args, **kwargs)

            def release(self):
                self.lock.release()

            def locked(self):
                return self.lock.locked()

            def __enter__(self):
                self.acquire()

            def __exit__(self, *args):
                self.release()

        class Subject(Executor):
            gated = False

            def __getattribute__(self, name):
                value = object.__getattribute__(self, name)
                if name == '_ocr_idle_since' and threading.current_thread().name == 'IdlePoll' and not self.gated:
                    self.gated = True
                    read_started.set()
                    if not release_read.wait(3):
                        raise TimeoutError('Controller did not release the reader')
                return value

        self.install(Subject)
        executor = Subject()
        executor.lock = GateLock()
        failures = []

        def run(fn):
            try:
                fn()
            except Exception as error:
                failures.append(type(error).__name__)

        reader = threading.Thread(target=run, args=(executor._release_idle_ocr,), name='IdlePoll')
        writer = threading.Thread(target=run, args=(lambda: executor._run_job(None),), name='JobReset')
        with patch.object(ocr_memory.time, 'monotonic', return_value=200):
            reader.start()
            try:
                self.assertTrue(read_started.wait(3))
                writer.start()
                self.assertTrue(writer_attempt.wait(3))
            finally:
                # The controller releases the reader without requiring the contested lock.
                release_read.set()
                reader.join(3)
                if writer.ident is not None:
                    writer.join(3)
        self.assertFalse(reader.is_alive() or writer.is_alive())
        self.assertEqual(failures, [])


if __name__ == '__main__':
    unittest.main()
