from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2

from onnxocr.onnx_paddleocr import ONNXPaddleOcr
from src.task.ChangeEchoTask import ChangeEchoTask


class ConfirmationObserved(Exception):
    pass


class TestChangeEchoOcrROI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = ONNXPaddleOcr(use_openvino=True, use_npu=False, use_angle_cls=False)
        cls.image = cv2.imread(str(Path(__file__).resolve().parents[1] / 'ok_templates/echo_enhance.png'))

    def test_production_confirmation_crop_preserves_text_and_position(self):
        for width, height in ((1280, 720), (1920, 1080), (3840, 2160)):
            with self.subTest(size=(width, height)):
                image = cv2.resize(self.image, (width, height))
                task = SimpleNamespace(
                    config={'目标属性': '攻击'}, info_set=Mock(), click=Mock(), sleep=Mock(),
                    find_echo_enhance=Mock(side_effect=[True, False]), is_0_level=lambda: True,
                    wait_ocr=Mock(side_effect=[[], [SimpleNamespace(name='生命')], [], []]))

                def confirm(x, y, to_x, to_y, match, **kwargs):
                    crop = image[int(y * height):int(to_y * height), int(x * width):int(to_x * width)]
                    result = self.model.ocr(crop)[0]
                    matches = [line for line in result if line[1][0] == match]
                    self.assertEqual(len(matches), 1)
                    box = matches[0][0]
                    center_x = sum(point[0] for point in box) / 4 / width + x
                    center_y = sum(point[1] for point in box) / 4 / height + y
                    self.assertAlmostEqual(center_x, .69, delta=.03)
                    self.assertAlmostEqual(center_y, .725, delta=.03)
                    self.assertLess(crop.shape[0] * crop.shape[1], width * height / 4)
                    raise ConfirmationObserved()

                task.wait_click_ocr = confirm
                with self.assertRaises(ConfirmationObserved):
                    ChangeEchoTask.run(task)


if __name__ == '__main__':
    unittest.main()
