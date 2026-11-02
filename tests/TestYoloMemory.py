import threading
import unittest
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import cv2

from src import globals as globals_module
from src.globals import Globals


class TestYoloMemory(unittest.TestCase):
    def setUp(self):
        self.models = []

        def load(instance):
            if instance._yolo_model is None:
                instance._yolo_model = Mock(detect=Mock(return_value='detected'))
                self.models.append(weakref.ref(instance._yolo_model))
            return instance._yolo_model

        self.enterContext(patch.object(Globals, 'yolo_model', property(load)))
        self.globals = Globals(threading.Event())

    def test_release_reloads_and_exception_does_not_leak_active_call(self):
        self.assertEqual(self.globals.yolo_detect('frame'), 'detected')
        first = self.models[-1]
        self.assertTrue(self.globals.release_yolo())
        self.assertIsNone(first())
        self.assertFalse(self.globals.release_yolo())
        self.assertEqual(self.globals.yolo_detect('frame'), 'detected')
        self.globals._yolo_model.detect.side_effect = ValueError('inference failed')
        with self.assertRaises(ValueError):
            self.globals.yolo_detect('frame')
        self.assertEqual(self.globals._yolo_active, 0)
        self.assertTrue(self.globals.release_yolo())

    def test_release_waits_for_detection_and_blocks_the_next_generation(self):
        self.globals.yolo_detect('warmup')
        first = self.models[-1]
        entered, finish, releasing = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(finish.set)

        def detect(*args, **kwargs):
            entered.set()
            self.assertTrue(finish.wait(5))
            return 'first'

        first().detect.side_effect = detect
        wait = self.globals._yolo_lock.wait

        def observe_wait(timeout):
            releasing.set()
            return wait(timeout)

        with ThreadPoolExecutor(max_workers=3) as pool, \
                patch.object(self.globals._yolo_lock, 'wait', side_effect=observe_wait):
            active = pool.submit(self.globals.yolo_detect, 'active')
            self.assertTrue(entered.wait(5))
            release = pool.submit(self.globals.release_yolo)
            self.assertTrue(releasing.wait(5))
            next_call = pool.submit(self.globals.yolo_detect, 'next')
            self.assertFalse(release.done())
            self.assertEqual(len(self.models), 1)
            finish.set()
            self.assertEqual(active.result(5), 'first')
            self.assertTrue(release.result(5))
            self.assertEqual(next_call.result(5), 'detected')
        self.assertIsNone(first())
        self.assertEqual(len(self.models), 2)

    def test_interrupted_release_reopens_admission(self):
        self.globals.yolo_detect('warmup')
        self.globals._yolo_active = 1
        with patch.object(self.globals._yolo_lock, 'wait', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                self.globals.release_yolo()
        self.globals._yolo_active = 0
        self.assertFalse(self.globals._yolo_releasing)
        self.assertEqual(self.globals.yolo_detect('next'), 'detected')


class TestYoloNative(unittest.TestCase):
    def test_real_detection_survives_explicit_release_and_reload(self):
        root = Path(__file__).resolve().parents[1]
        image = cv2.imread(str(root / 'tests/images/absorb.png'))
        instance = Globals(threading.Event())
        with patch.object(globals_module, 'get_path_relative_to_exe', side_effect=lambda name: str(root / name)), \
                patch.object(globals_module.og, 'config', {'ocr': {'params': {'use_openvino': True}}}):
            first = instance.yolo_detect(image, threshold=.3)
            self.assertTrue(first, 'The positive Echo fixture must contain a detection')
            device = instance.yolo_snapshot()['device']
            reference = weakref.ref(instance._yolo_model)
            self.assertTrue(instance.release_yolo())
            self.assertIsNone(reference())
            second = instance.yolo_detect(image, threshold=.3)
            self.assertEqual([(box.name, box.x, box.y, box.width, box.height) for box in first],
                             [(box.name, box.x, box.y, box.width, box.height) for box in second])
            self.assertEqual(instance.yolo_snapshot()['device'], device)
            self.assertTrue(instance.release_yolo())


if __name__ == '__main__':
    unittest.main()
