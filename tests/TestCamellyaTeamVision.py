import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from src.char.Camellya import Camellya
from src.char.Sanhua import Sanhua
from src.char.ShoreKeeper import ShoreKeeper
from src.char.Verina import Verina
from src.char.CamellyaSanhuaShoreKeeper import CamellyaTeamVision as Vision, Rotation
from src.task.BaseCombatTask import BaseCombatTask


FIXTURES = Path(__file__).parent / 'images/chunsanshou'


class TestCamellyaTeamVision(unittest.TestCase):
    def test_real_skill_icons_and_resized_copies(self):
        images = sorted(FIXTURES.glob('*_e_original.png'))
        self.assertEqual(len(images), 5)
        expected = ['no_bloom_ring', 'ready', 'ready', 'ready', 'icon_dark']
        for path, state in zip(images, expected):
            for scale in (1, 2 / 3, 0.5):
                with self.subTest(image=path.name, scale=scale):
                    image = cv2.resize(cv2.imread(str(path)), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                    result = Vision.skill_pixels(image)
                    self.assertEqual(result['state'], state)
                    self.assertEqual(result['ready'], state == 'ready')

    def test_real_forte_visible_extent_and_resized_copies(self):
        images = sorted(FIXTURES.glob('*_forte_original.png'))
        self.assertEqual(len(images), 5)
        bounds = [(0.45, 0.60), (0.22, 0.35), (0.005, 0.03), (0.38, 0.50), (0.10, 0.18)]
        for path, (low, high) in zip(images, bounds):
            for scale in (1, 2 / 3, 0.5):
                with self.subTest(image=path.name, scale=scale):
                    crop = cv2.imread(str(path))[54:72, 50:596]
                    crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                    result = Vision.forte_pixels(crop)
                    self.assertEqual(result['state'], 'filled')
                    self.assertGreaterEqual(result['value'], low)
                    self.assertLessEqual(result['value'], high)

    def test_missing_or_obscured_forte_is_unknown(self):
        for crop in (None, np.zeros((18, 546, 3), np.uint8), np.full((18, 546, 3), (170, 60, 240), np.uint8),
                     np.full((18, 546, 3), 255, np.uint8)):
            self.assertEqual(Vision.forte_pixels(crop)['value'], -1)

    def test_empty_forte_requires_visible_scale_and_is_not_unknown(self):
        crop = np.zeros((18, 546, 3), np.uint8)
        for x in range(0, 546, 13):
            crop[2:16, x:x + 6] = 160
        self.assertEqual(Vision.forte_pixels(crop)['value'], 0)
        crop[2:16, 260:266] = (170, 60, 240)
        self.assertEqual(Vision.forte_pixels(crop)['value'], -1)

    def test_solid_color_and_local_effect_do_not_count_as_ready_skill(self):
        for color in (0, 255, (170, 60, 240)):
            self.assertFalse(Vision.skill_pixels(np.full((160, 160, 3), color, np.uint8))['ready'])
        crop = cv2.imread(str(sorted(FIXTURES.glob('*_e_original.png'))[0]))
        crop[:60, :60] = (170, 60, 240)
        self.assertFalse(Vision.skill_pixels(crop)['ready'])

    def test_builtin_recognition_is_only_used_for_exact_team(self):
        task = BaseCombatTask.__new__(BaseCombatTask)
        cam = Camellya(task, 0)
        task.chars = [cam, Sanhua(task, 1), ShoreKeeper(task, 2)]
        task.box_of_screen_scaled = Mock()
        cam.calculate_color_percentage_in_masked = Mock(return_value=0)
        cam.calculate_forte_percent = Mock(return_value=0.3)
        with patch.object(Vision, 'skill', return_value={'ready': True}) as skill, \
                patch.object(Vision, 'forte', return_value={'value': 0.5}) as forte, \
                patch.object(Vision, 'budding_forte', return_value={'value': 0.38}) as budding:
            self.assertTrue(cam.ephemeral_ready())
            self.assertEqual(cam.get_forte(), 0.5)
            self.assertEqual(float(cam.get_forte(budding=True)), 0.38)
            task.chars[2] = Verina(task, 2)
            self.assertFalse(cam.ephemeral_ready())
            self.assertEqual(float(cam.get_forte()), 0.3)
            self.assertEqual(float(cam.get_forte(budding=True)), 0.3)
            skill.assert_called_once_with(cam)
            forte.assert_called_once_with(cam)
            budding.assert_called_once_with(cam)

    def test_budding_forte_real_glow_samples_and_resized_copies(self):
        images = sorted((FIXTURES / 'budding').glob('*.png'))
        self.assertEqual(len(images), 3)
        for path, bounds in zip(images, [(0.07, 0.10), (0.30, 0.45), (0.14, 0.18)]):
            for scale in (1, 2 / 3, 0.5):
                with self.subTest(image=path.name, scale=scale):
                    crop = cv2.imread(str(path))[54:72, 50:596]
                    crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                    result = Vision.budding_forte_pixels(crop)
                    self.assertEqual(result['state'], 'filled')
                    self.assertGreater(result['value'], bounds[0])
                    self.assertLess(result['value'], bounds[1])

    def test_purple_tail_does_not_extend_into_neutral_or_disconnected_ticks(self):
        path = FIXTURES / 'budding/2026-10-30_18-40-27_v1_0_5_purple_tail_forte_original.png'
        crop = cv2.imread(str(path))[54:72, 50:596]
        self.assertEqual(Vision.budding_forte_pixels(crop)['segments'], 6)
        # 复制真实亮格到空回路后方，不能把分离的特效接进左侧资源条。
        crop[:, 147:152] = crop[:, 66:71]
        self.assertEqual(Vision.budding_forte_pixels(crop)['segments'], 6)
        neutral = cv2.cvtColor(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        self.assertEqual(Vision.budding_forte_pixels(neutral)['state'], 'empty')
        crop[:, 53:58] = 0
        self.assertEqual(Vision.budding_forte_pixels(crop)['state'], 'unknown')

    def test_active_bloom_marker_and_resource_on_real_state_crops(self):
        from config import config
        from ok.feature.FeatureSet import FeatureSet

        features = FeatureSet(False, None, **dict(config['template_matching']))
        paths = sorted((FIXTURES / 'state').glob('*.png'))
        self.assertEqual(len(paths), 3)
        for index, path in enumerate(paths):
            crop = cv2.imread(str(path))
            original = np.zeros((2160, 3840, 3), np.uint8)
            original[1940:2070, 1580:2230] = crop
            for scale in (1, 2 / 3, 0.5):
                with self.subTest(image=path.name, scale=scale):
                    frame = cv2.resize(original, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                    marker = features.find_one_feature(frame, 'camellya_budding', threshold=0.7)
                    bar = cv2.resize(crop[54:72, 50:596], None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                    forte = Vision.budding_forte_pixels(bar)['value']
                    self.assertEqual(bool(marker), index == 0)
                    if index == 0:
                        self.assertGreater(forte, 0.25)
                        self.assertLess(forte, 0.36)
                    else:
                        self.assertEqual(forte, 0)

    def test_budding_missing_scale_or_solid_glow_is_unknown(self):
        for crop in (None, np.zeros((18, 546, 3), np.uint8),
                     np.full((18, 546, 3), 255, np.uint8), np.full((18, 546, 3), (210, 200, 255), np.uint8)):
            self.assertEqual(Vision.budding_forte_pixels(crop)['value'], -1)
        crop = cv2.imread(str(sorted((FIXTURES / 'budding').glob('*.png'))[-1]))[54:72, 50:596]
        crop[:, 160:] = 0
        self.assertEqual(Vision.budding_forte_pixels(crop)['value'], -1)

    def test_budding_empty_requires_scale_and_rejects_isolated_light(self):
        crop = np.full((18, 546, 3), 80, np.uint8)
        for index in range(40):
            x = round(14.5 + index * 13.5)
            crop[:, x - 3:x + 4] = 180
        self.assertEqual(Vision.budding_forte_pixels(crop)['state'], 'empty')
        crop[:, 148:153] = (210, 200, 255)
        self.assertEqual(Vision.budding_forte_pixels(crop)['value'], -1)

    def test_liberation_diagnostics_do_not_guess_energy(self):
        task = SimpleNamespace(chars=[], use_liberation=True, in_team=Mock(return_value=(True, 0, 3)),
                               get_cd=Mock(return_value=0))
        rotation = Rotation(task)
        char = SimpleNamespace(index=0, current_liberation=Mock(return_value=0), is_current_char=True,
                               has_intro=True, logger=Mock())
        rotation.log_liberation_unavailable(char, 'unavailable', 1)
        self.assertIn('cause=icon_dark_energy_or_animation_unknown', char.logger.info.call_args.args[0])
        task.get_cd.return_value = 3.5
        rotation.log_liberation_unavailable(char, 'unavailable', 1)
        self.assertIn('cause=cooldown', char.logger.info.call_args.args[0])
        self.assertIn('cd=3.500', char.logger.info.call_args.args[0])
        task.in_team.return_value = (False, -1, 3)
        rotation.log_liberation_unavailable(char, 'action_not_ready', 1)
        self.assertIn('cause=hud_unavailable', char.logger.info.call_args.args[0])

    def test_liberation_diagnostic_failure_is_nonfatal(self):
        task = SimpleNamespace(chars=[], in_team=Mock(side_effect=ValueError('no frame')))
        char = SimpleNamespace(logger=Mock())
        Rotation(task).log_liberation_unavailable(char, 'unavailable', 1)
        char.logger.warning.assert_called_once()

    def test_107_timeout_frame_still_contains_budding_resource(self):
        path = FIXTURES / 'tail/2026-10-30_20-26-45_v1_0_7_tail_forte_original.png'
        crop = cv2.imread(str(path))[54:72, 50:596]
        for scale in (1, 2 / 3, 0.5):
            with self.subTest(scale=scale):
                resized = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                result = Vision.budding_forte_pixels(resized)
                self.assertEqual(result['state'], 'filled')
                self.assertEqual(result['segments'], 4)
                self.assertAlmostEqual(result['value'], 0.106, delta=0.01)


if __name__ == '__main__':
    unittest.main()
