import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np

from src.task.FarmEchoTask import FarmEchoTask


def make_task(frame):
    task = FarmEchoTask.__new__(FarmEchoTask)
    height, width = frame.shape[:2]
    task._executor = SimpleNamespace(
        frame=frame,
        method=SimpleNamespace(width=width, height=height),
        device_manager=SimpleNamespace(supported_ratio=16 / 9),
    )
    task._in_combat = False
    task.draw_boxes = Mock()
    return task


class TestBossHealthBar(unittest.TestCase):
    def test_fleurdelys_health_bar_with_gradient_edges(self):
        # 2026-10-30 故障截图的顶部原始像素，保留坐标并补齐画面，排除其他 HUD 对识别的影响。
        hud = cv2.imread('tests/images/fleurdelys_health_bar.png')
        self.assertIsNotNone(hud)
        original = np.zeros((1440, 2560, 3), dtype=np.uint8)
        original[:hud.shape[0], :hud.shape[1]] = hud
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)):
            for in_combat in (False, True):
                with self.subTest(size=(width, height), in_combat=in_combat):
                    task = make_task(cv2.resize(original, (width, height)))
                    task._in_combat = in_combat

                    self.assertTrue(task.has_health_bar())

                    self.assertIsNotNone(task.boss_health)
                    self.assertEqual('boss_health', task.draw_boxes.call_args.args[0])

    def test_absent_or_unrelated_orange_regions_are_not_health_bars(self):
        cases = {
            'blank': None,
            'outside_boss_hud': (900, 600, 700, 13),
            'large_orange_panel': (900, 75, 700, 45),
            'small_icon': (900, 75, 30, 13),
        }
        for name, rect in cases.items():
            with self.subTest(name=name):
                frame = np.zeros((1440, 2560, 3), dtype=np.uint8)
                if rect:
                    x, y, width, height = rect
                    frame[y:y + height, x:x + width] = (30, 100, 250)

                self.assertFalse(make_task(frame).has_health_bar())

    def test_normal_enemy_health_bar_is_still_detected(self):
        frame = np.zeros((1440, 2560, 3), dtype=np.uint8)
        frame[600:609, 900:1100] = (65, 65, 200)

        self.assertTrue(make_task(frame).has_health_bar())


if __name__ == '__main__':
    unittest.main()
