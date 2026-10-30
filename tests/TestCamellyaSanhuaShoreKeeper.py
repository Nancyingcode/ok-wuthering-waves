import itertools
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np

from src.char.BaseChar import CharType
from src.char.Camellya import Camellya
from src.char.CamellyaSanhuaShoreKeeper import (
    Phase, Rotation, RotationTimeout, active_rotation, perform_team_rotation, supports_team,
)
from src.char.Sanhua import Sanhua
from src.char.ShoreKeeper import ShoreKeeper
from src.char.Verina import Verina
from src.task.BaseCombatTask import BaseCombatTask, NotInCombatException


class TestCamellyaSanhuaShoreKeeper(unittest.TestCase):
    def setUp(self):
        self.task = BaseCombatTask.__new__(BaseCombatTask)
        self.task.skip_combat_check = False
        self.task.in_liberation = False
        self.task.use_liberation = True
        self.task.in_team = Mock(return_value=(True, 0, 3))
        self.task.mouse_up = Mock()
        self.task.mouse_down = Mock()
        self.task.next_frame = Mock()
        self.task.time_elapsed_accounting_for_freeze = lambda started, intro_motion_freeze=0: time.time() - started
        self.camellya = Camellya(self.task, 0, char_type=CharType.MAIN_DPS)
        self.sanhua = Sanhua(self.task, 1, char_type=CharType.SUB_DPS)
        self.shore = ShoreKeeper(self.task, 2, char_type=CharType.HEALER)
        self.task.chars = [self.camellya, self.sanhua, self.shore]
        self.rotation = Rotation(self.task)
        self.rotation.capture_bloom_diagnostics = Mock()
        self.rotation.capture_liberation_diagnostics = Mock()
        self.rotation.log_liberation_unavailable = Mock()
        self.task._camellya_sanhua_shorekeeper = self.rotation

    def confirm_switch(self, char, has_intro=True):
        def switch(post_action):
            target = self.task._choose_switch_target(char, has_intro)
            char.is_current_char = False
            target.is_current_char = True
            target.has_intro = has_intro
            target.has_sub_dps_intro = has_intro and char.is_sub_dps
            post_action(target, has_intro)

        char.switch_next_char = Mock(side_effect=switch)

    def stub_actions(self, char):
        for name in ('click_liberation', 'heavy_click_forte', 'continues_normal_attack', 'heavy_attack',
                     'click_echo', 'record_echo_use', 'wait_down', 'sleep', 'check_combat'):
            setattr(char, name, Mock(return_value=True))
        char.click_resonance = Mock(return_value=(True, 0, False))
        char.is_con_full = Mock(return_value=True)
        char.get_current_con = Mock(side_effect=lambda: 1 if char.is_con_full.return_value else 0.5)
        char.resonance_available = Mock(return_value=False)
        char.liberation_available = Mock(return_value=True)
        char.down = Mock(return_value=True)
        char.has_cd = Mock(return_value=False)
        char.get_forte = Mock(return_value=-1)
        char.click = Mock()

    def fake_clock(self, char):
        clock = [100.0]
        self.rotation.phase_started = clock[0]
        char.sleep.side_effect = lambda duration: clock.__setitem__(0, clock[0] + duration)
        timer = patch('src.char.CamellyaSanhuaShoreKeeper.time.monotonic', side_effect=lambda: clock[0])
        timer.start()
        self.addCleanup(timer.stop)
        return clock

    def test_only_exact_builtin_roster_in_any_order(self):
        for chars in itertools.permutations(self.task.chars):
            self.task.chars = list(chars)
            self.assertTrue(supports_team(self.task))
        self.task.chars = [self.camellya, self.sanhua, Verina(self.task, 2)]
        self.assertFalse(perform_team_rotation(self.camellya))
        self.assertIsNone(active_rotation(self.task))
        self.task.chars = [self.camellya, self.sanhua]
        self.assertFalse(supports_team(self.task))
        self.task.chars = [self.camellya, self.sanhua, None]
        self.assertFalse(supports_team(self.task))

    def test_custom_character_opts_whole_team_out(self):
        class CustomSanhua(Sanhua):
            pass

        self.task.chars[1] = CustomSanhua(self.task, 1)
        with patch.object(self.rotation, 'perform') as perform:
            self.assertFalse(perform_team_rotation(self.camellya))
            perform.assert_not_called()

    def test_all_three_character_entry_points_dispatch(self):
        with patch.object(self.rotation, 'perform') as perform:
            for char in self.task.chars:
                char.do_perform()
                perform.assert_called_with(char)

    def test_false_resonance_tuple_uses_forte_fallback(self):
        self.shore.click_resonance = Mock(return_value=(False, 0, False))
        self.shore.heavy_click_forte = Mock()
        self.rotation.shore_resonance(self.shore)
        self.shore.heavy_click_forte.assert_called_once_with(self.shore.is_mouse_forte_full)
        self.shore.heavy_click_forte.reset_mock()
        self.shore.click_resonance.return_value = (True, 0.1, False)
        self.rotation.shore_resonance(self.shore)
        self.shore.heavy_click_forte.assert_not_called()

    def test_phase_advances_only_after_confirmed_switch(self):
        self.rotation.phase = Phase.SANHUA_OUTRO

        def confirm(post_action):
            self.assertEqual(self.rotation.phase, Phase.SANHUA_OUTRO)
            self.assertIs(self.task._choose_switch_target(self.sanhua, True), self.camellya)
            post_action(self.camellya, True)

        self.sanhua.switch_next_char = Mock(side_effect=confirm)
        self.rotation.handoff(self.sanhua, Phase.CAMELLYA_BURST)
        self.assertEqual(self.rotation.phase, Phase.CAMELLYA_BURST)
        self.assertEqual(self.rotation.intro_source, 'Sanhua')
        self.assertIsNone(self.rotation.switch_to)

    def test_unconfirmed_switch_keeps_phase_and_clears_request(self):
        self.sanhua.switch_next_char = Mock()
        self.rotation.phase = Phase.SANHUA_OUTRO
        with self.assertRaises(RotationTimeout):
            self.rotation.handoff(self.sanhua, Phase.CAMELLYA_BURST)
        self.assertEqual(self.rotation.phase, Phase.SANHUA_OUTRO)
        self.assertIsNone(self.rotation.intro_source)
        self.assertIsNone(self.rotation.switch_to)

    def test_plain_switch_cannot_start_burst(self):
        self.confirm_switch(self.sanhua, has_intro=False)
        self.rotation.handoff(self.sanhua, Phase.CAMELLYA_BURST)
        self.assertEqual(self.rotation.phase, Phase.CAMELLYA_WARMUP)
        self.assertIsNone(self.rotation.intro_source)

    def test_precharged_shore_intro_skips_seed_and_enters_warmup(self):
        self.confirm_switch(self.shore, has_intro=True)
        self.rotation.handoff(self.shore, Phase.CAMELLYA_SEED)
        self.assertEqual(self.rotation.phase, Phase.CAMELLYA_WARMUP)
        self.assertEqual(self.rotation.intro_source, 'ShoreKeeper')

    def test_planned_return_bypasses_only_healer_lock(self):
        self.shore.last_full_con_switch_time = time.time()
        self.assertTrue(self.shore.healer_full_con_switch_locked())
        self.task._log_switch_candidate = Mock()
        self.confirm_switch(self.camellya, has_intro=False)
        self.rotation.handoff(self.camellya, Phase.SHORE_RECHARGE)
        self.assertTrue(self.shore.healer_full_con_switch_locked())
        self.assertIs(self.task._choose_switch_target(self.camellya, False), self.sanhua)

    def test_switch_deadline_also_covers_wait_switch_branch(self):
        clock = [100.0]
        self.rotation.phase_started = clock[0]
        self.rotation.switch_started = clock[0]
        self.rotation.switch_from = self.camellya
        self.rotation.switch_to = self.shore
        self.task.update_lib_portrait_icon = Mock()
        self.task.check_combat = Mock()
        self.task.in_team = Mock(return_value=(True, 0, 3))
        self.task.click = Mock()
        self.task.sleep = lambda seconds: clock.__setitem__(0, clock[0] + 1)
        self.camellya.get_current_con = Mock(return_value=0)
        self.camellya.is_con_full = Mock(return_value=False)
        self.shore.wait_switch = Mock(return_value=True)
        with patch('src.char.CamellyaSanhuaShoreKeeper.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(RotationTimeout):
                self.task.switch_next_char(self.camellya)
        self.assertLessEqual(self.shore.wait_switch.call_count, 6)

    def test_short_warmup_does_not_use_liberation(self):
        self.stub_actions(self.camellya)
        self.camellya.has_intro = True
        self.rotation.phase = Phase.CAMELLYA_WARMUP
        self.rotation.intro_source = 'ShoreKeeper'
        self.confirm_switch(self.camellya)
        self.rotation.perform(self.camellya)
        self.camellya.click_liberation.assert_not_called()
        self.camellya.heavy_attack.assert_called_once_with(1.2, until_con_full=True)
        self.assertEqual(self.rotation.phase, Phase.SHORE_RECHARGE)

    def test_burst_prioritizes_liberation_without_intro_heavy(self):
        self.stub_actions(self.camellya)
        events = []
        self.camellya.has_intro = True
        self.camellya.wait_down.side_effect = lambda **kwargs: events.append('intro')
        self.camellya.click_liberation.side_effect = lambda **kwargs: events.append('liberation') or True
        self.camellya.ephemeral_ready = Mock(return_value=True)
        self.rotation.cast_bloom = Mock(side_effect=lambda char: events.append('bloom'))
        self.camellya.time_elapsed_accounting_for_freeze = Mock(return_value=5.2)
        self.task.find_one = Mock(return_value=None)
        self.rotation.camellya_burst(self.camellya)
        self.assertEqual(events, ['intro', 'liberation', 'bloom'])
        self.camellya.heavy_attack.assert_not_called()
        self.task.mouse_up.assert_called()

    def test_liberation_retries_once_after_unconfirmed_cast(self):
        self.stub_actions(self.camellya)
        self.camellya.click_liberation.side_effect = [False, True]
        self.assertTrue(self.rotation.camellya_liberation(self.camellya))
        self.assertEqual(self.camellya.click_liberation.call_args_list,
                         [call(wait_if_cd_ready=0), call(wait_if_cd_ready=0)])
        self.camellya.sleep.assert_any_call(0.2)

    def test_liberation_stops_after_two_unconfirmed_casts(self):
        self.stub_actions(self.camellya)
        self.camellya.click_liberation.return_value = False
        self.assertFalse(self.rotation.camellya_liberation(self.camellya))
        self.assertEqual(self.camellya.click_liberation.call_count, 2)

    def test_unconfirmed_liberation_on_cooldown_is_not_retried(self):
        self.stub_actions(self.camellya)
        self.camellya.click_liberation.return_value = False
        self.camellya.has_cd.return_value = True
        self.assertFalse(self.rotation.camellya_liberation(self.camellya))
        self.camellya.click_liberation.assert_called_once()

    def test_liberation_wait_is_bounded_when_intro_never_becomes_actionable(self):
        self.stub_actions(self.camellya)
        clock = self.fake_clock(self.camellya)
        self.camellya.down.return_value = False
        self.assertFalse(self.rotation.camellya_liberation(self.camellya))
        self.camellya.click_liberation.assert_not_called()
        self.assertLess(clock[0] - 100, 1.4)

    def test_action_readiness_requires_current_character_and_consecutive_frames(self):
        self.stub_actions(self.camellya)
        self.fake_clock(self.camellya)
        self.task.in_team.side_effect = [(False, -1, 3), (True, 1, 3), (True, 0, 3),
                                         (False, -1, 3), (True, 0, 3), (True, 0, 3)]
        self.assertTrue(self.rotation.wait_camellya_action_ready(self.camellya))
        self.assertEqual(self.task.in_team.call_count, 6)
        self.assertEqual(self.camellya.down.call_count, 3)

    def test_disabled_or_unavailable_liberation_is_not_pressed(self):
        self.stub_actions(self.camellya)
        self.task.use_liberation = False
        self.assertFalse(self.rotation.camellya_liberation(self.camellya))
        self.camellya.click_liberation.assert_not_called()
        self.task.use_liberation = True
        self.camellya.liberation_available.return_value = False
        self.assertFalse(self.rotation.camellya_liberation(self.camellya))
        self.camellya.click_liberation.assert_not_called()

    def test_failed_liberation_still_requires_bloom_confirmation(self):
        self.stub_actions(self.camellya)
        self.fake_clock(self.camellya)
        self.camellya.click_liberation.return_value = False
        self.camellya.has_cd.return_value = True
        readiness = iter([True, False])
        self.camellya.ephemeral_ready = Mock(side_effect=lambda: next(readiness, False))
        self.task.is_con_full = Mock(return_value=False)
        self.task.find_one = Mock(return_value=None)
        with self.assertRaisesRegex(RotationTimeout, 'bloom not confirmed'):
            self.rotation.camellya_burst(self.camellya)

    def test_near_full_shore_finishes_concerto_before_handoff(self):
        self.stub_actions(self.shore)
        self.shore.is_con_full.side_effect = [False, True]
        self.shore.get_current_con.side_effect = None
        self.shore.get_current_con.return_value = 0.96
        self.rotation.phase = Phase.SHORE_CONCERTO
        self.confirm_switch(self.shore, has_intro=True)
        self.rotation.perform(self.shore)
        self.assertEqual(self.shore.continues_normal_attack.call_args_list,
                         [call(3, until_con_full=True), call(2, until_con_full=True)])
        self.assertEqual(self.rotation.phase, Phase.CAMELLYA_WARMUP)
        self.assertEqual(self.rotation.intro_source, 'ShoreKeeper')

    def test_stuck_near_full_shore_recovers_after_one_extension(self):
        self.stub_actions(self.shore)
        self.shore.is_con_full.return_value = False
        self.shore.get_current_con.side_effect = None
        self.shore.get_current_con.return_value = 0.96
        self.shore.switch_next_char = Mock()
        self.rotation.phase = Phase.SHORE_CONCERTO
        self.rotation.perform(self.shore)
        self.assertEqual(self.rotation.phase, Phase.SHORE_START)
        self.assertEqual(self.shore.continues_normal_attack.call_count, 2)
        self.shore.switch_next_char.assert_not_called()

    def test_concerto_extension_requires_supported_actor_and_phase(self):
        for char, concerto in ((self.shore, 0.79), (self.sanhua, 0.96)):
            with self.subTest(char=char.name, concerto=concerto):
                self.stub_actions(char)
                char.is_con_full.return_value = False
                char.get_current_con.side_effect = None
                char.get_current_con.return_value = concerto
                with self.assertRaisesRegex(RotationTimeout, 'concerto not ready'):
                    self.rotation.fill_concerto(char)
                char.continues_normal_attack.assert_called_once_with(3, until_con_full=True)

    def test_bloom_diagnostics_distinguish_each_missing_resource(self):
        self.stub_actions(self.camellya)
        self.camellya.ephemeral_ready = Mock()
        for skill, con, reason in ((True, True, ''), (True, False, 'concerto'),
                                  (False, True, 'bloom_skill'), (False, False, 'bloom_skill_and_concerto')):
            with self.subTest(reason=reason):
                self.camellya.ephemeral_ready.return_value = skill
                self.camellya.is_con_full.return_value = con
                result, value = self.rotation.wait_bloom_ready(self.camellya, 0)
                self.assertEqual(result, reason)
                self.assertEqual(value, 1 if con else 0.5)
        self.camellya.click.assert_not_called()

    def test_bloom_concerto_recovery_does_not_use_heavy(self):
        self.stub_actions(self.camellya)
        clock = self.fake_clock(self.camellya)
        self.camellya.ephemeral_ready = Mock(return_value=True)
        self.camellya.is_con_full.return_value = False

        def fill(*args, **kwargs):
            self.camellya.is_con_full.return_value = True

        self.camellya.continues_normal_attack.side_effect = fill
        self.rotation.prepare_bloom(self.camellya)
        self.camellya.continues_normal_attack.assert_called_once_with(2, until_con_full=True)
        self.camellya.heavy_attack.assert_not_called()
        self.assertGreaterEqual(clock[0] - 100, 5)
        self.assertLess(clock[0] - 100, 5.1)

    def test_bloom_skill_recovery_consumes_recognized_forte_once(self):
        self.stub_actions(self.camellya)
        self.fake_clock(self.camellya)
        self.camellya.ephemeral_ready = Mock(return_value=False)
        self.camellya.get_forte = Mock(return_value=0.4)

        def consume(*args):
            self.camellya.ephemeral_ready.return_value = True

        self.camellya.heavy_attack.side_effect = consume
        self.rotation.prepare_bloom(self.camellya)
        self.camellya.heavy_attack.assert_called_once_with(1.2)
        self.task.mouse_up.assert_called()

    def test_unknown_or_empty_forte_does_not_trigger_heavy_or_unbounded_retries(self):
        self.stub_actions(self.camellya)
        clock = self.fake_clock(self.camellya)
        self.camellya.ephemeral_ready = Mock(return_value=False)
        self.camellya.is_con_full.return_value = False
        self.camellya.get_forte = Mock()
        for forte in (-1, 0):
            with self.subTest(forte=forte):
                clock[0] = 100
                self.camellya.get_forte.return_value = forte
                with self.assertRaisesRegex(RotationTimeout, 'missing=bloom_skill_and_concerto'):
                    self.rotation.prepare_bloom(self.camellya)
                self.assertLess(clock[0] - 100, 7.2)
        self.camellya.heavy_attack.assert_not_called()

    def test_bloom_retry_rechecks_both_resources(self):
        self.stub_actions(self.camellya)
        self.fake_clock(self.camellya)
        self.camellya.ephemeral_ready = Mock(return_value=True)
        self.camellya.is_con_full.return_value = False

        def fill(*args, **kwargs):
            self.camellya.is_con_full.return_value = True
            self.camellya.ephemeral_ready.return_value = False

        self.camellya.continues_normal_attack.side_effect = fill
        with self.assertRaisesRegex(RotationTimeout, 'missing=bloom_skill concerto=1.000'):
            self.rotation.prepare_bloom(self.camellya)
        self.camellya.heavy_attack.assert_not_called()

    def test_bloom_heavy_recovery_releases_mouse_and_propagates_combat_end(self):
        self.stub_actions(self.camellya)
        self.fake_clock(self.camellya)
        self.camellya.ephemeral_ready = Mock(return_value=False)
        self.camellya.get_forte = Mock(return_value=0.4)
        self.camellya.heavy_attack.side_effect = NotInCombatException('ended')
        with self.assertRaises(NotInCombatException):
            self.rotation.prepare_bloom(self.camellya)
        self.task.mouse_up.assert_called()

    def test_bloom_diagnostics_only_run_at_failed_boundaries(self):
        self.stub_actions(self.camellya)
        self.fake_clock(self.camellya)
        self.camellya.ephemeral_ready = Mock(return_value=True)
        self.rotation.prepare_bloom(self.camellya)
        self.rotation.capture_bloom_diagnostics.assert_not_called()
        self.camellya.ephemeral_ready.return_value = False
        self.camellya.get_forte = Mock(return_value=-1)
        with self.assertRaisesRegex(RotationTimeout, 'missing=bloom_skill'):
            self.rotation.prepare_bloom(self.camellya)
        self.assertEqual(self.rotation.capture_bloom_diagnostics.call_args_list, [
            call(self.camellya, 'before_recovery', 'bloom_skill', 1),
            call(self.camellya, 'recovery_failed', 'bloom_skill', 1),
        ])

    def test_bloom_diagnostics_preserve_frame_and_character_state_at_multiple_sizes(self):
        for width, height in ((1920, 1080), (3840, 2160), (3440, 1440)):
            with self.subTest(size=(width, height)):
                frame = np.full((height, width, 3), 37, dtype=np.uint8)
                self.task._executor = SimpleNamespace(frame=frame, method=SimpleNamespace(width=width, height=height))
                self.task.screenshot = Mock()
                self.stub_actions(self.camellya)
                self.camellya.calculate_color_percentage_in_masked = Mock(return_value=0.08)
                self.camellya.get_forte = Mock(side_effect=[-1, 0.6])
                self.camellya.current_con = 0.94
                self.camellya.has_intro = True
                with patch.object(self.camellya, 'logger') as logger, \
                        patch.object(self.rotation, 'log_target_diagnostics') as target_diagnostics:
                    Rotation.capture_bloom_diagnostics(self.rotation, self.camellya, 'recovery_failed', 'bloom_skill', 1)
                target_diagnostics.assert_called_once()
                self.assertEqual(target_diagnostics.call_args.args[2], 'bloom')
                self.assertEqual(target_diagnostics.call_args.args[1], self.task.screenshot.call_args_list[0].args[0][:-5])
                captures = self.task.screenshot.call_args_list
                self.assertEqual(len(captures), 3)
                saved = {item.args[0].rsplit('_', 1)[1]: item.kwargs['frame'] for item in captures}
                self.assertEqual(saved['full'].shape, frame.shape)
                e_box = self.task.box_of_screen_scaled(2560, 1440, 2110, 1236, 2217, 1343, hcenter=True)
                forte_context = self.task.box_of_screen_scaled(3840, 2160, 1580, 1940, 2230, 2070, hcenter=True)
                self.assertEqual(saved['e'].shape[:2], (e_box.height, e_box.width))
                self.assertEqual(saved['forte'].shape[:2], (forte_context.height, forte_context.width))
                for pixels in saved.values():
                    self.assertFalse(np.shares_memory(pixels, frame))
                    self.assertTrue(np.all(pixels == 37))
                frame[:] = 0
                self.assertTrue(np.all(saved['full'] == 37))
                self.assertEqual(self.camellya.current_con, 0.94)
                self.assertTrue(self.camellya.has_intro)
                self.task.next_frame.assert_not_called()
                self.camellya.click.assert_not_called()
                self.camellya.sleep.assert_not_called()
                self.camellya.get_current_con.assert_not_called()
                self.camellya.click_resonance.assert_not_called()
                self.camellya.click_liberation.assert_not_called()
                self.camellya.heavy_attack.assert_not_called()
                record = logger.info.call_args.args[0]
                self.assertIn('red_percent=0.080000', record)
                self.assertIn('forte=-1.000 budding_forte=0.600', record)
                self.assertIn('legacy_ready=False', record)

    def test_diagnostic_save_error_does_not_replace_bloom_timeout(self):
        self.stub_actions(self.camellya)
        self.fake_clock(self.camellya)
        self.task._executor = SimpleNamespace(frame=np.zeros((10, 10, 3), dtype=np.uint8))
        self.task.screenshot = Mock(side_effect=OSError('disk unavailable'))
        del self.rotation.capture_bloom_diagnostics
        self.camellya.ephemeral_ready = Mock(return_value=False)
        self.camellya.get_forte = Mock(return_value=-1)
        with self.assertRaisesRegex(RotationTimeout, 'bloom not ready: missing=bloom_skill concerto=1.000'):
            self.rotation.prepare_bloom(self.camellya)
        self.assertEqual(self.task.screenshot.call_count, 2)

    def test_diagnostic_recognition_error_keeps_all_three_queued_images(self):
        self.task._executor = SimpleNamespace(frame=np.zeros((1080, 1920, 3), dtype=np.uint8),
                                             method=SimpleNamespace(width=1920, height=1080))
        self.task.screenshot = Mock()
        self.camellya.calculate_color_percentage_in_masked = Mock(side_effect=ValueError('recognition failed'))
        with patch.object(self.camellya, 'logger') as logger:
            Rotation.capture_bloom_diagnostics(self.rotation, self.camellya, 'not_confirmed', 'bloom not confirmed')
        self.assertEqual(self.task.screenshot.call_count, 3)
        self.assertIn('bloom diagnostic failed', logger.warning.call_args.args[0])

    def test_full_cycle_splits_sanhua_and_records_intro_sources(self):
        for char in self.task.chars:
            self.stub_actions(char)
            self.confirm_switch(char)
        self.task.in_team_and_world = Mock(return_value=True)
        self.rotation.camellya_burst = Mock()
        order = [self.shore, self.camellya, self.sanhua, self.shore,
                 self.camellya, self.shore, self.sanhua, self.camellya]
        for index, char in enumerate(order):
            self.confirm_switch(char, has_intro=index >= 3)
            self.rotation.perform(char)
            if index == 2:
                self.sanhua.click_liberation.assert_not_called()
            if index == 3:
                self.assertEqual(self.rotation.intro_source, 'ShoreKeeper')
            if index == 6:
                self.assertEqual(self.rotation.intro_source, 'Sanhua')
        self.sanhua.click_liberation.assert_called_once()
        self.rotation.camellya_burst.assert_called_once_with(self.camellya)
        self.assertEqual(self.rotation.phase, Phase.SHORE_START)

    def test_missing_concerto_recovers_instead_of_forcing_intro(self):
        self.stub_actions(self.sanhua)
        self.sanhua.is_con_full.return_value = False
        self.rotation.phase = Phase.SANHUA_OUTRO
        self.confirm_switch(self.sanhua, has_intro=False)
        self.rotation.perform(self.sanhua)
        self.assertEqual(self.rotation.phase, Phase.SHORE_START)
        self.assertTrue(self.shore.is_current_char)
        self.assertIsNone(self.rotation.intro_source)

    def test_recovery_releases_mouse_on_exception_during_heavy(self):
        self.stub_actions(self.sanhua)
        self.rotation.phase = Phase.SANHUA_OUTRO
        self.sanhua.click_liberation.side_effect = NotInCombatException('ended')
        self.sanhua.switch_next_char = Mock()
        with self.assertRaises(NotInCombatException):
            self.rotation.perform(self.sanhua)
        self.task.mouse_up.assert_called()
        self.sanhua.switch_next_char.assert_not_called()
        self.assertEqual(self.rotation.phase, Phase.SHORE_START)

    def test_expired_phase_recovers_without_running_old_actions(self):
        self.rotation.phase = Phase.CAMELLYA_BURST
        self.rotation.phase_started = time.monotonic() - 30
        self.rotation.camellya_burst = Mock()
        self.confirm_switch(self.camellya, has_intro=False)
        self.rotation.perform(self.camellya)
        self.rotation.camellya_burst.assert_not_called()
        self.assertEqual(self.rotation.phase, Phase.SHORE_START)
        self.assertTrue(self.shore.is_current_char)

    def test_stuck_bloom_input_has_wall_clock_deadline(self):
        clock = [100.0]
        self.rotation.phase_started = clock[0]
        self.camellya.ephemeral_ready = Mock(return_value=True)
        self.camellya.check_combat = Mock()
        self.camellya.send_resonance_key = Mock()
        self.camellya.sleep = lambda duration: clock.__setitem__(0, clock[0] + duration)
        with patch('src.char.CamellyaSanhuaShoreKeeper.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(RotationTimeout, 'input not accepted'):
                self.rotation.cast_bloom(self.camellya)
        self.assertLessEqual(self.camellya.send_resonance_key.call_count, 21)
        self.rotation.capture_bloom_diagnostics.assert_called_once_with(
            self.camellya, 'input_not_accepted', 'bloom input not accepted')

    def test_bloom_requires_existing_visual_confirmation(self):
        self.stub_actions(self.camellya)
        self.fake_clock(self.camellya)
        self.camellya.ephemeral_ready = Mock(return_value=False)
        self.task.is_con_full = Mock(return_value=False)
        self.task.find_one = Mock(return_value=None)
        with self.assertRaisesRegex(RotationTimeout, 'not confirmed'):
            self.rotation.cast_bloom(self.camellya)
        self.rotation.capture_bloom_diagnostics.assert_called_once_with(
            self.camellya, 'not_confirmed', 'bloom not confirmed')

    def bloom_cast_clock(self):
        self.stub_actions(self.camellya)
        clock = self.fake_clock(self.camellya)
        self.task.in_team = Mock(return_value=(True, 0, 3))
        self.task.in_liberation = False
        self.task.is_con_full = Mock(return_value=True)
        self.camellya.send_resonance_key = Mock()
        # 初次按 E 后短暂丢失就绪图标，但未进入开花；后续重新显示就绪。
        readiness = iter([True, False])
        self.camellya.ephemeral_ready = Mock(side_effect=lambda: next(readiness, True))
        self.task.find_one = Mock(side_effect=lambda *args, **kwargs: self.camellya.send_resonance_key.call_count == 2)
        return clock

    def test_failed_bloom_retries_once_after_fresh_ready_frames(self):
        clock = self.bloom_cast_clock()
        self.rotation.cast_bloom(self.camellya)
        self.assertEqual(self.camellya.send_resonance_key.call_count, 2)
        self.assertEqual(self.task.is_con_full.call_count, 2)
        self.assertGreaterEqual(self.task.next_frame.call_count, 5)
        self.assertLess(clock[0], 102.5)
        self.assertEqual([args.args[1] for args in self.rotation.capture_bloom_diagnostics.call_args_list],
                         ['not_confirmed', 'input_retry'])

    def test_bloom_first_confirmation_uses_new_frame_without_retry(self):
        self.bloom_cast_clock()
        self.task.find_one.side_effect = lambda *args, **kwargs: self.task.next_frame.call_count >= 2
        self.rotation.cast_bloom(self.camellya)
        self.camellya.send_resonance_key.assert_called_once()
        self.task.is_con_full.assert_not_called()
        self.rotation.capture_bloom_diagnostics.assert_not_called()

    def test_late_bloom_confirmation_does_not_press_e_again(self):
        self.bloom_cast_clock()
        self.task.find_one.side_effect = [None, True]
        self.rotation.cast_bloom(self.camellya)
        self.camellya.send_resonance_key.assert_called_once()
        self.task.is_con_full.assert_not_called()

    def test_bloom_retry_requires_live_resources_and_current_hud(self):
        for unavailable in ('concerto', 'skill', 'hud', 'character', 'animation', 'flicker'):
            with self.subTest(unavailable=unavailable):
                clock = self.bloom_cast_clock()
                self.camellya.current_con = 1
                if unavailable == 'concerto':
                    self.task.is_con_full.return_value = False
                elif unavailable == 'skill':
                    readiness = iter([True, False])
                    self.camellya.ephemeral_ready.side_effect = lambda: next(readiness, False)
                elif unavailable == 'hud':
                    self.task.in_team.return_value = (False, -1, 3)
                elif unavailable == 'character':
                    self.task.in_team.return_value = (True, 1, 3)
                elif unavailable == 'animation':
                    self.task.in_liberation = True
                else:
                    self.camellya.ephemeral_ready.side_effect = itertools.cycle([True, False])
                with self.assertRaisesRegex(RotationTimeout, 'bloom not confirmed'):
                    self.rotation.cast_bloom(self.camellya)
                self.camellya.send_resonance_key.assert_called_once()
                self.assertLess(clock[0], 101.7)

    def test_unaccepted_bloom_retry_never_starts_a_second_retry_loop(self):
        clock = self.bloom_cast_clock()
        self.task.find_one.side_effect = None
        self.task.find_one.return_value = None
        with self.assertRaisesRegex(RotationTimeout, 'bloom retry not confirmed'):
            self.rotation.cast_bloom(self.camellya)
        self.assertEqual(self.camellya.send_resonance_key.call_count, 2)
        self.assertLess(clock[0], 102.5)
        self.assertEqual(self.rotation.capture_bloom_diagnostics.call_args.args[1], 'retry_not_confirmed')

    def test_bloom_retry_does_not_outlive_recheck_or_phase_deadline(self):
        for limit in ('recheck', 'phase'):
            with self.subTest(limit=limit):
                clock = self.bloom_cast_clock()
                if limit == 'phase':
                    self.rotation.phase_started = 77
                else:
                    self.task.next_frame.side_effect = lambda: clock.__setitem__(0, clock[0] + 0.5)
                with self.assertRaises(RotationTimeout):
                    self.rotation.cast_bloom(self.camellya)
                self.camellya.send_resonance_key.assert_called_once()

    def test_combat_end_during_bloom_retry_propagates_without_more_inputs(self):
        self.bloom_cast_clock()

        def check_combat():
            if self.camellya.send_resonance_key.call_count == 2:
                raise NotInCombatException('ended')

        self.camellya.check_combat.side_effect = check_combat
        with self.assertRaises(NotInCombatException):
            self.rotation.cast_bloom(self.camellya)
        self.assertEqual(self.camellya.send_resonance_key.call_count, 2)

    def test_stuck_bloom_indicator_releases_mouse_even_if_freeze_timer_stops(self):
        self.stub_actions(self.camellya)
        clock = [100.0]
        self.rotation.phase_started = clock[0]
        self.rotation.cast_bloom = Mock()
        self.camellya.ephemeral_ready = Mock(return_value=True)
        self.camellya.time_elapsed_accounting_for_freeze = Mock(return_value=0)
        self.camellya.check_target = Mock()
        self.camellya.should_retry_heavy_attack = Mock()
        self.task.next_frame.side_effect = lambda: clock.__setitem__(0, clock[0] + 1)
        with patch('src.char.CamellyaSanhuaShoreKeeper.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(RotationTimeout, 'did not end'):
                self.rotation.camellya_burst(self.camellya)
        self.task.mouse_up.assert_called()
        self.rotation.capture_bloom_diagnostics.assert_called_once_with(
            self.camellya, 'state_timeout', 'bloom state did not end: output timeout')

    def test_liberation_pairs_before_and_after_for_each_attempt(self):
        self.stub_actions(self.camellya)
        events = []
        self.rotation.capture_liberation_diagnostics.side_effect = lambda *args: events.append(args[2])
        results = iter([False, True])
        self.camellya.click_liberation.side_effect = lambda **kwargs: events.append('cast') or next(results)
        self.assertTrue(self.rotation.camellya_liberation(self.camellya))
        self.assertEqual(events, ['before', 'cast', 'after', 'before', 'cast', 'after'])
        captures = self.rotation.capture_liberation_diagnostics.call_args_list
        for before, after in zip(captures[::2], captures[1::2]):
            self.assertEqual(before.args[1], after.args[1])
            self.assertEqual(before.args[3], after.args[3])
        self.assertFalse(captures[1].args[4])
        self.assertTrue(captures[3].args[4])

    def test_liberation_interruption_still_captures_after_without_retry(self):
        self.stub_actions(self.camellya)
        self.camellya.click_liberation.side_effect = NotInCombatException('ended')
        with self.assertRaises(NotInCombatException):
            self.rotation.camellya_liberation(self.camellya)
        self.camellya.click_liberation.assert_called_once()
        after = self.rotation.capture_liberation_diagnostics.call_args_list[-1]
        self.assertEqual(after.args[2], 'after')
        self.assertIsNone(after.args[4])

    def test_unavailable_liberation_does_not_create_cast_screenshots(self):
        self.stub_actions(self.camellya)
        self.camellya.liberation_available.return_value = False
        self.assertFalse(self.rotation.camellya_liberation(self.camellya))
        self.rotation.capture_liberation_diagnostics.assert_not_called()

    def test_liberation_observation_is_passive_and_keeps_unknown_separate(self):
        from ok import Box

        frame = np.full((1080, 1920, 3), 37, dtype=np.uint8)
        self.task._executor = SimpleNamespace(frame=frame)
        self.task.screenshot = Mock()
        self.task.get_target_names = Mock(return_value=('has_target', 'no_target'))
        self.task.is_browser = Mock(return_value=False)
        self.task.get_box_by_name = Mock(return_value=Box(1, 2, 30, 40))
        self.task.find_one = Mock(return_value=None)
        self.task.get_cd = Mock(return_value=0)
        self.task.has_target = Mock(side_effect=AssertionError('must not run target correction'))
        self.task.send_key = Mock()
        self.task.sleep = Mock()
        for name, expected in [('has_target', 'locked'), ('no_target', 'unlocked'), (None, 'unknown')]:
            with self.subTest(state=expected), patch.object(self.camellya, 'logger') as logger:
                match = Box(10, 20, 30, 40, name=name, confidence=0.9) if name else None
                self.task.find_best_match_in_box = Mock(return_value=match)
                Rotation.capture_liberation_diagnostics(self.rotation, self.camellya, 123, 'before', 1)
                self.assertIn(f'target_state={expected}', logger.info.call_args.args[0])
                Rotation.log_target_diagnostics(self.rotation, self.camellya, 'bloom_test', 'bloom')
                self.assertIn(f'bloom target capture=bloom_test target_state={expected}', logger.info.call_args.args[0])
                self.assertIn('distance=unknown hit=unknown', logger.info.call_args.args[0])
                saved = self.task.screenshot.call_args.kwargs['frame']
                self.assertFalse(np.shares_memory(saved, frame))
                self.assertTrue(np.array_equal(saved, frame))
        self.task.has_target.assert_not_called()
        self.task.send_key.assert_not_called()
        self.task.sleep.assert_not_called()
        self.task.next_frame.assert_not_called()

    def test_liberation_capture_failure_does_not_prevent_cast(self):
        self.stub_actions(self.camellya)
        self.task._executor = SimpleNamespace(frame=np.zeros((10, 10, 3), dtype=np.uint8))
        self.task.screenshot = Mock(side_effect=OSError('disk unavailable'))
        del self.rotation.capture_liberation_diagnostics
        self.assertTrue(self.rotation.camellya_liberation(self.camellya))
        self.camellya.click_liberation.assert_called_once()
        self.assertEqual(self.task.screenshot.call_count, 2)

    def test_delayed_liberation_capture_uses_normal_frames_without_waiting(self):
        self.stub_actions(self.camellya)
        clock = [100.0]
        original = Mock(side_effect=lambda: clock.__setitem__(0, clock[0] + 0.25) or 'fresh')
        self.task.next_frame = original
        self.rotation.cast_bloom = Mock()
        self.rotation.bloom_output = Mock()
        self.rotation.echo = Mock()

        def prepare(char):
            for _ in range(4):
                self.assertEqual(self.task.next_frame(), 'fresh')

        self.rotation.prepare_bloom = prepare
        with patch('src.char.CamellyaSanhuaShoreKeeper.time.monotonic', side_effect=lambda: clock[0]):
            self.rotation.phase_started = clock[0]
            self.rotation.wait_camellya_action_ready = Mock(return_value=True)
            self.rotation.camellya_burst(self.camellya)
        captures = self.rotation.capture_liberation_diagnostics.call_args_list
        self.assertEqual([item.args[2] for item in captures], ['before', 'after', 'after_350ms', 'after_900ms'])
        self.assertEqual(len({item.args[1] for item in captures}), 1)
        self.assertEqual(clock[0], 101)
        self.assertEqual(original.call_count, 4)
        self.camellya.sleep.assert_not_called()
        self.assertIs(self.task.next_frame, original)
        self.assertIsNone(self.rotation.liberation_observation)

    def test_delayed_capture_does_not_duplicate_late_frame_or_retry_failed_capture(self):
        clock = [101.2]
        self.rotation.liberation_observation = {'event_id': 42, 'attempt': 1, 'started': 100,
                                               'pending': [0.35, 0.9]}
        with patch('src.char.CamellyaSanhuaShoreKeeper.time.monotonic', side_effect=lambda: clock[0]), \
                patch.object(self.camellya, 'logger') as logger:
            self.rotation.observe_liberation_after_frame(self.camellya)
            self.rotation.observe_liberation_after_frame(self.camellya)
            self.assertIn('skipped=[0.35]', logger.info.call_args.args[0])
        self.rotation.capture_liberation_diagnostics.assert_called_once_with(
            self.camellya, 42, 'after_900ms', 1, True)
        self.rotation.liberation_observation = {'event_id': 43, 'attempt': 1, 'started': 100,
                                               'pending': [0.9]}
        self.rotation.capture_liberation_diagnostics.side_effect = OSError('disk full')
        with patch('src.char.CamellyaSanhuaShoreKeeper.time.monotonic', return_value=101.2):
            self.rotation.observe_liberation_after_frame(self.camellya)
            self.rotation.observe_liberation_after_frame(self.camellya)
        self.assertEqual(self.rotation.capture_liberation_diagnostics.call_count, 2)

    def test_delayed_capture_expires_or_drops_when_character_changes(self):
        for now, in_team in [(102.01, (True, 0, 3)), (100.5, (True, 1, 3)), (100.5, (False, -1, 0))]:
            with self.subTest(now=now, in_team=in_team):
                self.rotation.liberation_observation = {'event_id': 42, 'attempt': 1, 'started': 100,
                                                       'pending': [0.35, 0.9]}
                self.task.in_team.return_value = in_team
                with patch('src.char.CamellyaSanhuaShoreKeeper.time.monotonic', return_value=now):
                    self.rotation.observe_liberation_after_frame(self.camellya)
                self.assertIsNone(self.rotation.liberation_observation)
        self.rotation.capture_liberation_diagnostics.assert_not_called()

    def test_burst_cancellation_restores_frame_hook_and_discards_pending_capture(self):
        from ok import TaskDisabledException

        self.stub_actions(self.camellya)
        del self.task.next_frame
        original = self.task.next_frame
        self.rotation.wait_camellya_action_ready = Mock(return_value=True)
        self.rotation.prepare_bloom = Mock(side_effect=TaskDisabledException())
        with self.assertRaises(TaskDisabledException):
            self.rotation.camellya_burst(self.camellya)
        self.assertNotIn('next_frame', self.task.__dict__)
        self.assertEqual(self.task.next_frame, original)
        self.assertIsNone(self.rotation.liberation_observation)

    def output_clock(self):
        clock = [100.0, 0.0]
        self.rotation.phase_started = clock[0]
        for name in ('time', 'monotonic'):
            timer = patch(f'src.char.CamellyaSanhuaShoreKeeper.time.{name}', side_effect=lambda: clock[0])
            timer.start()
            self.addCleanup(timer.stop)
        self.camellya.time_elapsed_accounting_for_freeze = lambda start: clock[0] - start - clock[1]
        self.camellya.check_combat = Mock()
        self.camellya.check_target = Mock()
        self.camellya.should_retry_heavy_attack = Mock()
        self.camellya.get_forte = Mock(return_value=-1)
        self.task.next_frame.side_effect = lambda: clock.__setitem__(0, clock[0] + 0.25)
        self.task.find_one = Mock(return_value=None)
        return clock

    def test_retarget_wait_does_not_consume_output_and_restores_task_hook(self):
        clock = self.output_clock()
        original = Mock(side_effect=lambda **kwargs: clock.__setitem__(0, clock[0] + 7) or True)
        self.task.target_enemy = original
        waits = iter([True, False])

        def check_combat():
            if next(waits, False):
                self.task.target_enemy(time_out=30, check_health=True)

        self.camellya.check_combat.side_effect = check_combat
        with patch.object(self.camellya, 'logger') as logger:
            self.rotation.bloom_output(self.camellya)
        self.assertAlmostEqual(clock[0] - 100, 12.25)
        self.assertIn('target_wait=7.000', logger.info.call_args.args[0])
        self.assertIn('output=5.250', logger.info.call_args.args[0])
        self.assertIs(self.task.target_enemy, original)
        self.rotation.capture_bloom_diagnostics.assert_not_called()
        self.task.mouse_up.assert_called()

    def test_retarget_and_freeze_overlap_are_not_subtracted_twice(self):
        clock = self.output_clock()

        def retarget(**kwargs):
            clock[0] += 7
            clock[1] += 2
            return True

        self.task.target_enemy = Mock(side_effect=retarget)
        called = []

        def check_combat():
            if not called:
                called.append(True)
                self.task.target_enemy(time_out=30)

        self.camellya.check_combat.side_effect = check_combat
        with patch.object(self.camellya, 'logger') as logger:
            self.rotation.bloom_output(self.camellya)
        self.assertAlmostEqual(clock[0] - 100, 12.25)
        self.assertIn('output=5.250', logger.info.call_args.args[0])

    def test_multiple_retargets_share_wall_limit_and_do_not_add_output(self):
        clock = self.output_clock()
        self.task.target_enemy = Mock(side_effect=lambda **kwargs: clock.__setitem__(0, clock[0] + 3) or True)
        waits = iter([True, True, False])
        self.camellya.check_combat.side_effect = lambda: (
            self.task.target_enemy(time_out=30) if next(waits, False) else None)
        with patch.object(self.camellya, 'logger') as logger:
            self.rotation.bloom_output(self.camellya)
        self.assertAlmostEqual(clock[0] - 100, 11.25)
        self.assertIn('target_wait=6.000 wait_count=2', logger.info.call_args.args[0])

    def test_long_retarget_is_capped_before_global_combat_end_handling(self):
        clock = self.output_clock()
        budgets = []

        def retarget(**kwargs):
            budgets.append(kwargs['time_out'])
            clock[0] += kwargs['time_out']
            return False

        original = Mock(side_effect=retarget)
        self.task.target_enemy = original
        self.camellya.check_combat.side_effect = lambda: self.task.target_enemy(time_out=30, check_health=True)
        with self.assertRaisesRegex(RotationTimeout, 'wall timeout'):
            self.rotation.bloom_output(self.camellya)
        self.assertEqual(budgets, [16])
        self.assertIs(self.task.target_enemy, original)
        self.task.mouse_up.assert_called()
        self.camellya.should_retry_heavy_attack.assert_not_called()

    def test_existing_phase_deadline_also_caps_retarget(self):
        clock = self.output_clock()
        self.rotation.phase_started = 80
        original = Mock(side_effect=lambda **kwargs: clock.__setitem__(0, clock[0] + kwargs['time_out']) or True)
        self.task.target_enemy = original
        self.camellya.check_combat.side_effect = lambda: self.task.target_enemy(time_out=30)
        with self.assertRaisesRegex(RotationTimeout, 'phase timeout'):
            self.rotation.bloom_output(self.camellya)
        self.assertEqual(original.call_args.kwargs['time_out'], 4)
        self.assertIs(self.task.target_enemy, original)
        self.task.mouse_up.assert_called()

    def test_no_wait_freeze_stall_still_has_eight_second_guard(self):
        clock = self.output_clock()
        self.camellya.time_elapsed_accounting_for_freeze = Mock(return_value=0)
        with self.assertRaisesRegex(RotationTimeout, 'output timeout'):
            self.rotation.bloom_output(self.camellya)
        self.assertEqual(clock[0] - 100, 8)
        self.assertNotIn('target_enemy', self.task.__dict__)
        self.task.mouse_up.assert_called()

    def test_combat_end_during_retarget_is_propagated_and_hook_restored(self):
        self.output_clock()
        original = Mock(side_effect=NotInCombatException('ended'))
        self.task.target_enemy = original
        self.camellya.check_combat.side_effect = lambda: self.task.target_enemy(time_out=30)
        with self.assertRaises(NotInCombatException):
            self.rotation.bloom_output(self.camellya)
        self.assertIs(self.task.target_enemy, original)
        self.task.mouse_up.assert_called()
        self.rotation.capture_bloom_diagnostics.assert_not_called()

    def test_cancellation_restores_class_method_without_new_instance_override(self):
        from ok import TaskDisabledException

        self.output_clock()
        self.task.next_frame.side_effect = TaskDisabledException()
        with self.assertRaises(TaskDisabledException):
            self.rotation.bloom_output(self.camellya)
        self.assertNotIn('target_enemy', self.task.__dict__)
        self.task.mouse_up.assert_called()

    def test_short_failed_retarget_keeps_existing_combat_end_decision(self):
        self.output_clock()
        self.task.target_enemy_time_out = 3
        original = Mock(return_value=False)
        self.task.target_enemy = original

        def check_combat():
            if not self.task.target_enemy():
                raise NotInCombatException('ended')

        self.camellya.check_combat.side_effect = check_combat
        with self.assertRaises(NotInCombatException):
            self.rotation.bloom_output(self.camellya)
        self.assertEqual(original.call_args.kwargs['time_out'], 3)
        self.assertIs(self.task.target_enemy, original)

    def tail_clock(self, completes_at=109):
        clock = self.output_clock()
        self.camellya.sleep = Mock()
        self.camellya.get_forte.side_effect = lambda **kwargs: max(0.0, round(0.95 - (clock[0] - 100) * 0.1, 3))
        self.task.find_one.side_effect = lambda *args, **kwargs: clock[0] < completes_at
        return clock

    def test_consuming_bloom_tail_finishes_during_single_grace(self):
        clock = self.tail_clock()
        with patch.object(self.camellya, 'logger') as logger:
            self.rotation.bloom_output(self.camellya)
        self.assertEqual(clock[0], 109)
        self.assertEqual(sum('tail grace start' in item.args[0] for item in logger.info.call_args_list), 1)
        self.assertIn('outcome=completed', logger.info.call_args.args[0])
        self.assertIn('tail_grace=True tail_extra=1.000', logger.info.call_args.args[0])
        self.rotation.capture_bloom_diagnostics.assert_not_called()
        self.task.mouse_up.assert_called()
        self.assertNotIn('target_enemy', self.task.__dict__)

    def test_bloom_tail_grace_cannot_exceed_two_seconds(self):
        clock = self.tail_clock(completes_at=200)
        with self.assertRaisesRegex(RotationTimeout, 'tail grace timeout'):
            self.rotation.bloom_output(self.camellya)
        self.assertEqual(clock[0], 110)
        self.task.mouse_up.assert_called()
        self.assertNotIn('target_enemy', self.task.__dict__)

    def test_unknown_stalled_or_large_remaining_forte_gets_no_grace(self):
        clock = self.tail_clock(completes_at=200)
        for value in (-1, 0, 0.2, 'large'):
            with self.subTest(value=value):
                clock[0] = 100
                self.camellya.get_forte.side_effect = (
                    (lambda **kwargs: 1 - (clock[0] - 100) * 0.075) if value == 'large'
                    else (lambda **kwargs: value))
                with self.assertRaisesRegex(RotationTimeout, 'output timeout'):
                    self.rotation.bloom_output(self.camellya)
                self.assertEqual(clock[0], 108)

    def test_bloom_tail_does_not_continue_on_lost_consumption_evidence(self):
        clock = self.tail_clock(completes_at=200)
        for value in (-1, 0.2, 'stalled'):
            with self.subTest(value=value):
                clock[0] = 100

                def forte(**kwargs):
                    if clock[0] < 108:
                        return round(0.95 - (clock[0] - 100) * 0.1, 3)
                    return 0.175 if value == 'stalled' else value

                self.camellya.get_forte.side_effect = forte
                with self.assertRaisesRegex(RotationTimeout, 'tail progress lost'):
                    self.rotation.bloom_output(self.camellya)
                self.assertLess(clock[0], 110)

    def test_tail_transient_unknown_recovers_without_cutting_remaining_output(self):
        clock = self.tail_clock(completes_at=108.5)
        self.task.next_frame.side_effect = lambda: clock.__setitem__(0, clock[0] + 0.125)

        def forte(**kwargs):
            if clock[0] < 108:
                return round(0.95 - int((clock[0] - 100) * 4) * 0.025, 3)
            if clock[0] == 108:
                return -1
            # 10.6% 来自实测失败帧；相邻时刻为构造序列，用于验证一次丢识别后的复核。
            return max(0, 0.106 - int((clock[0] - 108.125) * 8) * 0.025)

        self.camellya.get_forte.side_effect = forte
        with patch.object(self.camellya, 'logger') as logger:
            self.rotation.bloom_output(self.camellya)
        logs = [entry.args[0] for entry in logger.info.call_args_list]
        self.assertTrue(any('event=recheck_start' in line and 'forte_unknown' in line for line in logs))
        self.assertTrue(any('event=recheck_recovered' in line for line in logs))
        self.assertEqual(clock[0], 108.5)
        self.rotation.capture_bloom_diagnostics.assert_not_called()

    def entry_recheck_clock(self, completes_at=108.3125):
        clock = self.tail_clock(completes_at=completes_at)
        self.task.next_frame.side_effect = lambda: clock.__setitem__(0, clock[0] + 0.03125)
        self.rotation.maintain_bloom_heavy = Mock()

        def forte(**kwargs):
            # 8 秒前的 8.2% 平台与失败日志一致；重置前值及之后的下降为构造序列。
            if clock[0] < 107.875:
                return 0.057
            if clock[0] < 108.125:
                return 0.082
            return 0.057 if clock[0] < 108.25 else 0.032

        self.camellya.get_forte.side_effect = forte
        return clock

    def test_recent_tail_rise_before_eight_seconds_can_recover_consumption(self):
        clock = self.entry_recheck_clock()
        with patch.object(self.camellya, 'logger') as logger:
            self.rotation.bloom_output(self.camellya)
        logs = [entry.args[0] for entry in logger.info.call_args_list]
        self.assertEqual(sum('event=entry_recheck_start' in line for line in logs), 1)
        self.assertTrue(any('event=entry_recheck_recovered' in line for line in logs))
        self.assertTrue(any("'previous': (7.84375, 0.057)" in line for line in logs))
        self.assertIn('outcome=completed', logs[-1])
        self.assertIn('tail_grace=True', logs[-1])
        self.assertEqual(clock[0], 108.3125)
        self.rotation.capture_bloom_diagnostics.assert_not_called()
        self.task.mouse_up.assert_called()
        self.assertNotIn('target_enemy', self.task.__dict__)

    def test_tail_reset_on_last_frame_can_collect_evidence_inside_recheck(self):
        clock = self.tail_clock(completes_at=108.35)
        self.rotation.maintain_bloom_heavy = Mock()
        # 前两帧和重置值来自第二轮日志；8 秒后的消耗及结束时刻为构造恢复序列。
        frames = iter([107.937, 107.984, 108.0, 108.1, 108.2, 108.3, 108.35])
        self.task.next_frame.side_effect = lambda: clock.__setitem__(0, next(frames))

        def forte(**kwargs):
            if clock[0] < 107.984:
                return 0.15567765567765568
            if clock[0] < 108.1:
                return 0.20512820512820512
            return 0.18 if clock[0] < 108.2 else 0.155

        self.camellya.get_forte.side_effect = forte
        with patch.object(self.camellya, 'logger') as logger:
            self.rotation.bloom_output(self.camellya)
        logs = [entry.args[0] for entry in logger.info.call_args_list]
        self.assertTrue(any('event=entry_recheck_start' in line and "'samples': 1" in line for line in logs))
        self.assertTrue(any('event=entry_recheck_recovered' in line for line in logs))
        self.assertIn('outcome=completed', logs[-1])
        self.assertIn('tail_grace=True', logs[-1])
        self.assertEqual(clock[0], 108.35)
        self.task.mouse_up.assert_called()

    def test_single_reset_sample_can_open_recheck_but_cannot_grant_grace(self):
        samples = [(7.984, 0.205)]
        reset = {'reason': 'forte_rise', 'at': 7.984, 'previous': (7.937, 0.156)}
        self.assertTrue(Rotation.bloom_entry_recheck_allowed(samples, 8, reset))
        self.assertFalse(Rotation.bloom_tail_progressing(samples, 8))
        self.assertFalse(Rotation.bloom_entry_recheck_allowed([], 8, reset))

    def test_entry_recheck_can_observe_completion_without_granting_full_grace(self):
        clock = self.entry_recheck_clock(completes_at=108.125)
        with patch.object(self.camellya, 'logger') as logger:
            self.rotation.bloom_output(self.camellya)
        self.assertEqual(clock[0], 108.125)
        self.assertIn('outcome=completed', logger.info.call_args.args[0])
        self.assertIn('tail_grace=False', logger.info.call_args.args[0])

    def test_entry_recheck_stall_and_repeated_rises_cannot_renew_budget(self):
        for repeated in (False, True):
            with self.subTest(repeated=repeated):
                clock = self.entry_recheck_clock(completes_at=200)
                def forte(**kwargs):
                    if clock[0] < 107.875:
                        return 0.057
                    rises = max(0, int((clock[0] - 108) * 8)) if repeated else 0
                    return 0.082 + rises * 0.025
                self.camellya.get_forte.side_effect = forte
                with patch.object(self.camellya, 'logger') as logger:
                    with self.assertRaisesRegex(RotationTimeout, 'entry recheck timeout'):
                        self.rotation.bloom_output(self.camellya)
                logs = [entry.args[0] for entry in logger.info.call_args_list]
                self.assertEqual(sum('event=entry_recheck_start' in line for line in logs), 1)
                self.assertLessEqual(clock[0], 108.4375)
                self.assertIn('tail_grace=False', logs[-1])
                self.task.mouse_up.assert_called()

    def test_entry_recheck_requires_current_low_forte_and_budding(self):
        for current in (-1, 0, 0.4, 'not_budding'):
            with self.subTest(current=current):
                clock = self.entry_recheck_clock(completes_at=200)
                previous = self.camellya.get_forte.side_effect
                self.camellya.get_forte.side_effect = lambda **kwargs: (
                    previous(**kwargs) if clock[0] < 108 or current == 'not_budding' else current)
                if current == 'not_budding':
                    self.task.find_one.side_effect = lambda *args, **kwargs: clock[0] < 108
                with self.assertRaisesRegex(RotationTimeout, 'output timeout'):
                    self.rotation.bloom_output(self.camellya)
                self.assertEqual(clock[0], 108)

    def test_entry_recheck_rejects_old_or_invalid_reset_history(self):
        samples = [(7.89, 0.082), (7.93, 0.082), (7.97, 0.082)]
        reset = {'reason': 'forte_rise', 'at': 7.89, 'previous': (7.875, 0.057)}
        self.assertTrue(Rotation.bloom_entry_recheck_allowed(samples, 8, reset))
        for change in ({'reason': 'forte_unknown'}, {'reason': 'sample_gap'}, {'reason': 'retarget'},
                       {'at': 7.5}, {'previous': None}, {'previous': (7.875, -1)}):
            with self.subTest(change=change):
                self.assertFalse(Rotation.bloom_entry_recheck_allowed(samples, 8, {**reset, **change}))

    def test_entry_recheck_keeps_wall_phase_and_retarget_guards(self):
        for guard in ('wall', 'phase', 'retarget'):
            with self.subTest(guard=guard):
                clock = self.entry_recheck_clock(completes_at=200)
                self.rotation.BLOOM_WALL_TIMEOUT = 8.125 if guard == 'wall' else 16
                if guard == 'phase':
                    self.rotation.phase_started = 84.125
                if guard == 'retarget':
                    self.task.target_enemy = Mock(side_effect=lambda **kwargs: clock.__setitem__(0, clock[0] + 1))
                    self.camellya.check_target.side_effect = lambda *args: (
                        self.task.target_enemy(time_out=30) if clock[0] >= 108.0625 else None)
                error = 'output timeout' if guard == 'retarget' else f'{guard} timeout'
                with self.assertRaisesRegex(RotationTimeout, error):
                    self.rotation.bloom_output(self.camellya)
                self.assertEqual(clock[0], 109.0625 if guard == 'retarget' else 108.125)
                self.task.mouse_up.assert_called()

    def test_tail_persistent_unknown_exits_after_bounded_recheck_with_reason(self):
        clock = self.tail_clock(completes_at=200)
        self.camellya.get_forte.side_effect = lambda **kwargs: (
            round(0.95 - (clock[0] - 100) * 0.1, 3) if clock[0] < 108 else -1)
        with patch.object(self.camellya, 'logger') as logger:
            with self.assertRaisesRegex(RotationTimeout, 'tail progress lost'):
                self.rotation.bloom_output(self.camellya)
        self.assertEqual(clock[0], 108.75)
        logs = [entry.args[0] for entry in logger.info.call_args_list]
        self.assertTrue(any('event=exit' in line and 'insufficient_samples' in line
                            and 'history_reset=forte_unknown' in line for line in logs))
        self.assertEqual(sum('event=recheck_start' in line for line in logs), 1)
        self.task.mouse_up.assert_called()
        self.assertNotIn('target_enemy', self.task.__dict__)

    def test_tail_observed_completion_can_finish_during_recheck(self):
        clock = self.tail_clock(completes_at=108.25)
        self.camellya.get_forte.side_effect = lambda **kwargs: (
            round(0.95 - (clock[0] - 100) * 0.1, 3) if clock[0] < 108 else 0)
        self.rotation.bloom_output(self.camellya)
        self.assertEqual(clock[0], 108.25)
        self.rotation.capture_bloom_diagnostics.assert_not_called()

    def test_tail_recheck_never_extends_hard_deadlines(self):
        for guard in ('tail grace', 'wall', 'phase'):
            with self.subTest(guard=guard):
                clock = self.tail_clock(completes_at=200)
                expires = 110 if guard == 'tail grace' else 108.5
                if guard == 'wall':
                    self.rotation.BLOOM_WALL_TIMEOUT = 8.5
                if guard == 'phase':
                    self.rotation.BLOOM_WALL_TIMEOUT = 16
                    self.rotation.phase_started = 84.5
                loss_at = expires - 0.25
                self.camellya.get_forte.side_effect = lambda **kwargs: (
                    round(0.95 - (clock[0] - 100) * 0.1, 3) if clock[0] < loss_at else -1)
                with self.assertRaisesRegex(RotationTimeout, f'{guard} timeout'):
                    self.rotation.bloom_output(self.camellya)
                self.assertEqual(clock[0], expires)

    def test_tail_retarget_does_not_reuse_grace_evidence(self):
        clock = self.tail_clock(completes_at=200)
        self.task.target_enemy = Mock(side_effect=lambda **kwargs: clock.__setitem__(0, clock[0] + 1) or True)

        def check_target(*args):
            if clock[0] >= 108.25:
                self.task.target_enemy(time_out=30)

        self.camellya.check_target.side_effect = check_target
        with self.assertRaisesRegex(RotationTimeout, 'tail progress lost'):
            self.rotation.bloom_output(self.camellya)
        self.assertEqual(clock[0], 109.25)

    def test_tail_diagnostic_distinguishes_window_and_reset_failures(self):
        cases = [([], 'insufficient_samples'), ([(0, 0.4), (0.25, 0.35), (0.5, 0.3)], 'outside_tail'),
                 ([(0, 0.2), (0.25, 0.175), (0.5, 0.15)], 'stale_sample'),
                 ([(0.6, 0.2), (0.8, 0.2), (1.2, 0.175)], 'insufficient_decreases'),
                 ([(0, 0.2), (0.2, 0.175), (0.4, 0.15), (1.2, 0.15)], 'stale_decrease')]
        for samples, reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(Rotation.bloom_tail_status(samples, 1.2)['reason'], reason)
        for value, at, reason in [(-1, 0.1, 'forte_unknown'), (0.1, 1, 'sample_gap'),
                                  (0.2, 0.1, 'forte_rise')]:
            samples = [(0, 0.15)]
            self.assertEqual(Rotation.record_bloom_progress(samples, value, at), reason)

    def test_retarget_clears_consumption_history_before_grace(self):
        clock = self.tail_clock(completes_at=200)
        original = Mock(side_effect=lambda **kwargs: clock.__setitem__(0, clock[0] + 3) or True)
        self.task.target_enemy = original
        waited = []

        def check_target(*args):
            if clock[0] >= 107.75 and not waited:
                waited.append(True)
                self.task.target_enemy(time_out=30)

        self.camellya.check_target.side_effect = check_target
        with patch.object(self.camellya, 'logger') as logger:
            with self.assertRaisesRegex(RotationTimeout, 'output timeout'):
                self.rotation.bloom_output(self.camellya)
        self.assertEqual(clock[0], 111)
        self.assertIn('target_wait=3.000', logger.info.call_args.args[0])
        self.assertIn('tail_grace=False', logger.info.call_args.args[0])
        self.assertIs(self.task.target_enemy, original)

    def test_tail_grace_keeps_total_wall_and_phase_deadlines(self):
        clock = self.tail_clock(completes_at=200)
        for guard in ('wall', 'phase'):
            with self.subTest(guard=guard):
                clock[0] = 100
                self.rotation.phase_started = 100 if guard == 'wall' else 91.5
                waited = []
                self.task.target_enemy = Mock(side_effect=lambda **kwargs: clock.__setitem__(0, clock[0] + 7) or True)

                def check_combat():
                    if not waited:
                        waited.append(True)
                        self.task.target_enemy(time_out=30)

                self.camellya.check_combat.side_effect = check_combat
                self.camellya.get_forte.side_effect = lambda **kwargs: max(0.0, round(0.95 - (clock[0] - 107) * 0.1, 3))
                with patch.object(self.camellya, 'logger') as logger:
                    with self.assertRaisesRegex(RotationTimeout, f'{guard} timeout'):
                        self.rotation.bloom_output(self.camellya)
                self.assertIn('tail_grace=True', logger.info.call_args.args[0])
                self.assertEqual(clock[0], 116 if guard == 'wall' else 115.5)

    def test_single_drop_or_stale_frame_cannot_grant_tail_time(self):
        clock = self.tail_clock(completes_at=200)
        for stale in (False, True):
            with self.subTest(stale=stale):
                clock[0] = 100
                self.camellya.get_forte.side_effect = lambda **kwargs: 0.2 if clock[0] < 107.75 else 0.175

                def next_frame():
                    clock[0] += 0.75 if stale and clock[0] == 107.25 else 0.25

                self.task.next_frame.side_effect = next_frame
                with self.assertRaisesRegex(RotationTimeout, 'output timeout'):
                    self.rotation.bloom_output(self.camellya)
                self.assertEqual(clock[0], 108)

    def test_cancellation_during_grace_releases_input_and_restores_target(self):
        from ok import TaskDisabledException

        clock = self.tail_clock(completes_at=200)
        original = Mock()
        self.task.target_enemy = original

        def check_target(*args):
            if clock[0] >= 108.25:
                raise TaskDisabledException()

        self.camellya.check_target.side_effect = check_target
        with self.assertRaises(TaskDisabledException):
            self.rotation.bloom_output(self.camellya)
        self.task.mouse_up.assert_called()
        self.assertIs(self.task.target_enemy, original)

    def test_shore_intro_restores_prior_combat_check_flag(self):
        self.shore.has_intro = True
        self.shore.sleep = Mock()
        self.task.skip_combat_check = True
        self.task.in_team_and_world = Mock(return_value=False)
        self.task.wait_in_team_and_world = Mock(return_value=False)
        with self.assertRaises(RotationTimeout):
            self.rotation.wait_shore_intro(self.shore)
        self.assertTrue(self.task.skip_combat_check)

    def test_near_full_sanhua_finishes_outro_before_handoff(self):
        self.stub_actions(self.sanhua)
        self.sanhua.is_con_full.side_effect = [False, True]
        self.sanhua.get_current_con.side_effect = None
        self.sanhua.get_current_con.return_value = 0.933
        self.rotation.phase = Phase.SANHUA_OUTRO
        self.confirm_switch(self.sanhua)
        self.rotation.perform(self.sanhua)
        self.assertEqual(self.sanhua.continues_normal_attack.call_args_list,
                         [call(3, until_con_full=True), call(2, until_con_full=True)])
        self.assertEqual(self.rotation.phase, Phase.CAMELLYA_BURST)
        self.assertEqual(self.rotation.intro_source, 'Sanhua')

    def test_sanhua_extension_is_single_bounded_attempt_and_does_not_fake_full(self):
        self.stub_actions(self.sanhua)
        self.sanhua.is_con_full.return_value = False
        self.sanhua.get_current_con.side_effect = None
        self.rotation.phase = Phase.SANHUA_OUTRO
        for value, calls in ((0.89, 1), (0.90, 2), (0.99, 2)):
            with self.subTest(concerto=value):
                self.sanhua.get_current_con.return_value = value
                self.sanhua.continues_normal_attack.reset_mock()
                with self.assertRaisesRegex(RotationTimeout, 'concerto not ready'):
                    self.rotation.fill_concerto(self.sanhua)
                self.assertEqual(self.sanhua.continues_normal_attack.call_count, calls)

    def test_initially_unavailable_liberation_is_rechecked_once_before_bloom(self):
        self.stub_actions(self.camellya)
        self.camellya.liberation_available.side_effect = [False, True]
        self.assertFalse(self.rotation.camellya_liberation(self.camellya))
        self.assertTrue(self.rotation.liberation_recheck_pending)
        self.rotation.wait_bloom_ready = Mock(side_effect=[('bloom_skill', 1), (None, 1), (None, 1)])
        self.rotation.prepare_bloom(self.camellya)
        self.camellya.click_liberation.assert_called_once()
        self.assertFalse(self.rotation.liberation_recheck_pending)
        self.rotation.prepare_bloom(self.camellya)
        self.camellya.click_liberation.assert_called_once()
        self.assertEqual(self.rotation.wait_bloom_ready.call_args_list,
                         [call(self.camellya, 5), call(self.camellya, 0), call(self.camellya, 5)])

    def test_deferred_liberation_still_unavailable_does_not_loop(self):
        self.stub_actions(self.camellya)
        self.camellya.liberation_available.return_value = False
        self.rotation.camellya_liberation(self.camellya)
        self.rotation.wait_bloom_ready = Mock(return_value=(None, 1))
        self.rotation.prepare_bloom(self.camellya)
        self.rotation.prepare_bloom(self.camellya)
        self.assertEqual(self.camellya.liberation_available.call_count, 2)
        self.camellya.click_liberation.assert_not_called()
        self.assertFalse(self.rotation.liberation_recheck_pending)

    def test_cooldown_or_already_attempted_liberation_cannot_defer(self):
        self.stub_actions(self.camellya)
        self.camellya.liberation_available.return_value = False
        self.camellya.has_cd.return_value = True
        self.task.get_cd = Mock(return_value=0.6)
        self.rotation.camellya_liberation(self.camellya)
        self.assertFalse(self.rotation.liberation_recheck_pending)
        self.camellya.liberation_available.return_value = True
        self.camellya.has_cd.return_value = False
        self.camellya.click_liberation.return_value = False
        self.rotation.camellya_liberation(self.camellya)
        self.assertEqual(self.camellya.click_liberation.call_count, 2)
        self.assertFalse(self.rotation.liberation_recheck_pending)

    def short_cooldown_clock(self):
        self.stub_actions(self.camellya)
        clock = self.fake_clock(self.camellya)
        self.rotation.wait_camellya_action_ready = Mock(return_value=True)
        self.task.get_cd = Mock(side_effect=lambda *args: 100.37 - clock[0])
        self.camellya.has_cd.side_effect = lambda *args: self.task.get_cd('liberation', 0) > 0.2
        self.camellya.liberation_available.side_effect = lambda: self.task.get_cd('liberation', 0) <= 0
        self.camellya.ephemeral_ready = Mock(return_value=True)
        return clock

    def test_short_cooldown_is_rechecked_once_after_expiry_without_five_second_wait(self):
        clock = self.short_cooldown_clock()
        self.assertFalse(self.rotation.camellya_liberation(self.camellya))
        self.assertTrue(self.rotation.liberation_recheck_pending)
        self.rotation.prepare_bloom(self.camellya)
        self.camellya.click_liberation.assert_called_once_with(wait_if_cd_ready=0)
        self.assertGreaterEqual(clock[0], 100.37)
        self.assertLess(clock[0], 100.51)
        self.assertFalse(self.rotation.liberation_recheck_pending)
        self.assertIsNone(self.rotation.liberation_recheck_deadline)
        self.rotation.prepare_bloom(self.camellya)
        self.camellya.click_liberation.assert_called_once()

    def test_short_cooldown_rechecks_bloom_resources_after_cast(self):
        self.short_cooldown_clock()
        self.rotation.camellya_liberation(self.camellya)
        self.camellya.ephemeral_ready.return_value = False
        self.camellya.is_con_full.return_value = False

        def cast(**kwargs):
            self.camellya.ephemeral_ready.return_value = True
            self.camellya.is_con_full.return_value = True
            return True

        self.camellya.click_liberation.side_effect = cast
        self.rotation.prepare_bloom(self.camellya)
        self.camellya.click_liberation.assert_called_once()
        self.camellya.heavy_attack.assert_not_called()
        self.rotation.capture_bloom_diagnostics.assert_not_called()

    def test_short_cooldown_still_dark_or_unconfirmed_does_not_repeat(self):
        for dark in (False, True):
            with self.subTest(dark=dark):
                self.short_cooldown_clock()
                self.rotation.camellya_liberation(self.camellya)
                self.camellya.click_liberation.return_value = False
                if dark:
                    self.camellya.liberation_available.side_effect = None
                    self.camellya.liberation_available.return_value = False
                self.rotation.prepare_bloom(self.camellya)
                self.rotation.prepare_bloom(self.camellya)
                self.assertEqual(self.camellya.click_liberation.call_count, 0 if dark else 1)
                self.assertFalse(self.rotation.liberation_recheck_pending)

    def test_short_cooldown_stuck_or_replaced_does_not_hold_ready_bloom(self):
        for cd in (0.37, 25):
            with self.subTest(cd=cd):
                clock = self.short_cooldown_clock()
                self.rotation.camellya_liberation(self.camellya)
                self.task.get_cd.side_effect = None
                self.task.get_cd.return_value = cd
                self.rotation.prepare_bloom(self.camellya)
                self.camellya.click_liberation.assert_not_called()
                self.assertLess(clock[0], 100.56)
                self.assertFalse(self.rotation.liberation_recheck_pending)
                self.assertIsNone(self.rotation.liberation_recheck_deadline)

    def test_short_cooldown_animation_preserves_preparation_window_and_phase_limit(self):
        clock = self.short_cooldown_clock()
        self.rotation.camellya_liberation(self.camellya)
        self.camellya.click_liberation.side_effect = lambda **kwargs: clock.__setitem__(0, clock[0] + 4) or True
        self.camellya.ephemeral_ready.side_effect = lambda: clock[0] >= 106
        self.rotation.prepare_bloom(self.camellya)
        self.assertGreaterEqual(clock[0], 106)
        self.assertLess(clock[0], 106.1)
        self.camellya.heavy_attack.assert_not_called()

        clock = self.short_cooldown_clock()
        self.rotation.camellya_liberation(self.camellya)
        self.rotation.phase_started = 80
        self.camellya.click_liberation.side_effect = lambda **kwargs: clock.__setitem__(0, clock[0] + 4) or True
        with self.assertRaisesRegex(RotationTimeout, 'phase timeout'):
            self.rotation.prepare_bloom(self.camellya)

    def test_short_cooldown_recheck_rejects_changed_character(self):
        clock = self.short_cooldown_clock()
        self.rotation.camellya_liberation(self.camellya)
        clock[0] += 0.4
        del self.rotation.wait_camellya_action_ready
        self.task.in_team.return_value = (True, 1, 3)
        self.rotation.prepare_bloom(self.camellya)
        self.camellya.click_liberation.assert_not_called()
        self.assertFalse(self.rotation.liberation_recheck_pending)

    def test_short_cooldown_pending_is_cleared_on_recovery_reset(self):
        self.short_cooldown_clock()
        self.rotation.camellya_liberation(self.camellya)
        self.rotation.reset()
        self.assertIsNone(self.rotation.liberation_recheck_deadline)
        self.assertFalse(self.rotation.liberation_recheck_pending)

    def test_failed_deferred_cast_has_no_additional_attempt(self):
        self.stub_actions(self.camellya)
        self.camellya.liberation_available.side_effect = [False, True]
        self.camellya.click_liberation.return_value = False
        self.rotation.camellya_liberation(self.camellya)
        self.rotation.wait_bloom_ready = Mock(return_value=(None, 1))
        self.rotation.prepare_bloom(self.camellya)
        self.camellya.click_liberation.assert_called_once()
        self.assertFalse(self.rotation.liberation_recheck_pending)

    def test_bloom_heavy_ignores_unknown_empty_and_continuing_consumption(self):
        self.stub_actions(self.camellya)
        observation = {'forte': None, 'since': 0, 'samples': 0, 'retries': 0}
        self.camellya.get_forte.side_effect = [0.4, 0.4, -1, 0.4, 0.4, 0.38, 0.36, 0.02, 0]
        for elapsed in [0, 0.7, 0.8, 1, 1.7, 1.8, 2.6, 3, 4]:
            self.rotation.maintain_bloom_heavy(self.camellya, observation, elapsed)
        self.task.mouse_up.assert_not_called()
        self.task.mouse_down.assert_not_called()

    def test_bloom_heavy_stall_needs_multiple_samples_and_retries_at_most_twice(self):
        self.stub_actions(self.camellya)
        observation = {'forte': None, 'since': 0, 'samples': 0, 'retries': 0}
        self.camellya.get_forte.return_value = 0.38
        self.rotation.maintain_bloom_heavy(self.camellya, observation, 0)
        self.rotation.maintain_bloom_heavy(self.camellya, observation, 1)
        self.task.mouse_up.assert_not_called()
        for elapsed in [1.1, 2, 2.5, 3, 4, 4.5, 5, 6, 7]:
            self.rotation.maintain_bloom_heavy(self.camellya, observation, elapsed)
        self.assertEqual(observation['retries'], 2)
        self.assertEqual(self.task.mouse_up.call_count, 2)
        self.assertEqual(self.task.mouse_down.call_count, 2)

    def test_character_refresh_preserves_phase_but_replacement_invalidates_it(self):
        self.rotation.phase = Phase.SANHUA_OUTRO
        for char in self.task.chars:
            char.reset_state()
        self.assertIs(active_rotation(self.task), self.rotation)
        self.assertEqual(self.rotation.phase, Phase.SANHUA_OUTRO)
        self.task.chars[1] = Sanhua(self.task, 1)
        self.assertIsNone(active_rotation(self.task))

    def test_combat_end_resets_shared_phase(self):
        self.task.get_current_char = Mock(return_value=None)
        self.task.combat_end()
        self.assertIsNone(active_rotation(self.task))


if __name__ == '__main__':
    unittest.main()
