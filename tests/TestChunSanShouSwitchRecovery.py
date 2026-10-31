import ast
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

if __package__:
    from .TestChunSanShouPreparation import ROOT, Rotation, RotationTimeout, Phase
else:
    from TestChunSanShouPreparation import ROOT, Rotation, RotationTimeout, Phase


class CombatEnded(Exception):
    pass


class TaskCancelled(Exception):
    pass


def combat_methods(path, class_name, names):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    original = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    methods = [node for node in original.body if isinstance(node, ast.FunctionDef) and node.name in names]
    isolated = ast.ClassDef(name=class_name, bases=[], keywords=[], body=methods, decorator_list=[])
    namespace = {'time': time, 'logger': Mock(), 'TaskDisabledException': TaskCancelled}
    module = ast.fix_missing_locations(ast.Module(body=[isolated], type_ignores=[]))
    exec(compile(module, str(path), 'exec'), namespace)
    return namespace[class_name]


CombatDetection = combat_methods(ROOT / 'src/combat/CombatCheck.py', 'CombatCheck',
                                 {'in_combat', 'do_check_in_combat', 'target_enemy'})
CombatChecks = combat_methods(ROOT / 'src/task/BaseCombatTask.py', 'BaseCombatTask',
                              {'check_combat', 'sleep_check'})


class CombatTask(CombatChecks, CombatDetection):
    pass


class TestChunSanShouSwitchRecovery(unittest.TestCase):
    def setUp(self):
        self.clock = [100.0]
        self.addCleanup(patch.stopall)
        patch('time.monotonic', side_effect=lambda: self.clock[0]).start()
        patch('time.time', side_effect=lambda: self.clock[0]).start()
        self.cam = SimpleNamespace(name='Camellya', index=0, logger=Mock(), f_break=Mock(),
                                   skip_combat_check=Mock(return_value=False), current_con=1,
                                   is_sub_dps=False, waiting_for_forte_drop=False, switch_out=Mock())
        self.shore = SimpleNamespace(name='ShoreKeeper', index=2, logger=Mock(), wait_switch=Mock(return_value=False),
                                     has_intro=True, has_sub_dps_intro=False, intro_motion_freeze_duration=0.8)
        self.task = CombatTask()
        self.task.chars = [self.cam, self.shore]
        self.task._in_combat = True
        self.task.skip_combat_check = False
        self.task.in_sleep_check = False
        self.task.in_liberation = False
        self.task.debug = False
        self.task.target_enemy_time_out = 3
        self.task.combat_end_condition = None
        self.task.scene = SimpleNamespace(in_combat=Mock(return_value=None), set_in_combat=Mock(return_value=True))
        self.task.check_f_break = Mock()
        self.task.get_current_char = Mock(return_value=self.cam)
        self.task.on_combat_check = Mock(return_value=True)
        self.task.has_target = Mock(return_value=False)
        self.task.check_health_bar = Mock(return_value=True)
        self.task.should_check_monthly_card = Mock(return_value=False)
        self.task.reset_to_false = Mock(return_value=False)
        self.task.raise_not_in_combat = Mock(side_effect=CombatEnded)
        self.task.mouse_up = Mock()
        self.task.next_frame = Mock()
        self.task.in_team = Mock(return_value=(True, self.cam.index, 3))
        self.task.send_key = Mock(side_effect=self.send_key)
        self.task.click = Mock()
        self.task.middle_click = Mock(side_effect=self.middle_click)
        self.task.sleep = self.sleep
        self.task.add_freeze_duration = Mock()
        self.cam.sleep = self.sleep
        self.cam.check_combat = lambda: self.task.check_combat()
        self.cam.switch_next_char = Mock(side_effect=self.switch)
        self.rotation = Rotation(self.task)
        self.rotation.phase = Phase.CAMELLYA_RETURN
        self.rotation.capture_switch_diagnostics = Mock()
        self.allow_switch = False

    def sleep(self, seconds, **kwargs):
        self.clock[0] += seconds
        if not self.task.in_sleep_check and kwargs.get('check_combat', True):
            self.task.sleep_check()

    def middle_click(self, **kwargs):
        self.task.click()
        self.sleep(0.05)

    def send_key(self, key, **kwargs):
        if self.allow_switch and str(key) == str(self.shore.index + 1):
            self.task.in_team.return_value = (True, self.shore.index, 3)

    def switch(self, post_action):
        self.cam.f_break(check_f_on_switch=True)
        while True:
            self.sleep(0.01)
            self.task.check_combat()
            self.shore.wait_switch()
            self.task.send_key(self.shore.index + 1)
            if self.task.in_team()[1] == self.shore.index:
                post_action(self.shore, True)
                return

    def first_timeout(self):
        with self.assertRaisesRegex(RotationTimeout, 'switch timeout') as caught:
            self.rotation.handoff(self.cam, Phase.SHORE_START)
        self.assertLess(self.clock[0], 104.57)
        self.task.raise_not_in_combat.assert_not_called()
        self.task.reset_to_false.assert_not_called()
        self.assertFalse(self.task.in_sleep_check)
        return caught.exception

    def test_sleep_retarget_timeout_reaches_local_retry(self):
        # 实测是 F-break 后 HUD 短暂不可读，sleep_check 进入 30 秒锁敌，再吞掉输入上的超时。
        self.cam.f_break.side_effect = lambda **kwargs: self.clock.__setitem__(0, self.clock[0] + 2.25)
        error = self.first_timeout()
        self.task.has_target.return_value = True
        self.cam.f_break.side_effect = None
        self.allow_switch = True
        self.assertTrue(self.rotation.try_local_switch_recovery(self.cam, error))
        self.assertEqual(self.rotation.phase, Phase.SHORE_START)
        self.assertEqual(self.rotation.intro_source, 'Camellya')
        self.assertEqual(self.cam.switch_next_char.call_count, 2)
        self.assertLess(self.clock[0], 106)

    def test_explicit_combat_check_does_not_swallow_timeout(self):
        self.cam.switch_next_char.side_effect = lambda post_action: self.task.check_combat()
        self.first_timeout()

    def test_failed_retry_keeps_original_six_second_deadline(self):
        error = self.first_timeout()
        deadline = self.rotation.switch_deadline
        self.assertFalse(self.rotation.try_local_switch_recovery(self.cam, error))
        self.assertEqual(self.rotation.switch_deadline, deadline)
        self.assertGreaterEqual(self.clock[0], 106)
        self.assertLess(self.clock[0], 106.08)
        self.task.raise_not_in_combat.assert_not_called()
        self.task.reset_to_false.assert_not_called()

    def test_full_recovery_retries_without_replaying_burst(self):
        self.task.has_target.side_effect = lambda: self.clock[0] >= 104.5
        self.allow_switch = True
        self.rotation.camellya_burst = Mock()
        self.rotation.perform(self.cam)
        self.assertEqual(self.rotation.phase, Phase.SHORE_START)
        self.assertEqual(self.cam.switch_next_char.call_count, 2)
        self.rotation.camellya_burst.assert_not_called()
        self.task.raise_not_in_combat.assert_not_called()
        self.assertLess(self.clock[0], 106)

    def test_hud_recovery_uses_remaining_retry_budget(self):
        error = self.first_timeout()
        self.task.in_team.side_effect = lambda: (self.clock[0] >= 104.8, self.cam.index, 3)
        self.task.has_target.return_value = True
        self.allow_switch = True

        def switch_key(key, **kwargs):
            if str(key) == str(self.shore.index + 1):
                self.task.in_team.side_effect = None
                self.task.in_team.return_value = (True, self.shore.index, 3)

        self.task.send_key.side_effect = switch_key
        self.assertTrue(self.rotation.try_local_switch_recovery(self.cam, error))
        self.assertEqual(self.rotation.phase, Phase.SHORE_START)
        self.assertGreaterEqual(self.clock[0], 104.8)
        self.assertLess(self.clock[0], 106)

    def test_retarget_wait_is_limited_to_attempt_remaining_time(self):
        requested = []
        target_enemy = self.task.target_enemy

        def observed_target_enemy(**kwargs):
            requested.append(kwargs['time_out'])
            return target_enemy(**kwargs)

        self.task.target_enemy = observed_target_enemy
        self.cam.f_break.side_effect = lambda **kwargs: self.clock.__setitem__(0, self.clock[0] + 2.407)
        self.first_timeout()
        self.assertEqual(len(requested), 1)
        self.assertAlmostEqual(requested[0], 4.5 - 2.407 - 0.01)

    def test_target_observed_after_timeout_does_not_replay_switch(self):
        error = self.first_timeout()
        self.task.in_team.return_value = (True, self.shore.index, 3)
        self.assertTrue(self.rotation.try_local_switch_recovery(self.cam, error))
        self.assertEqual(self.rotation.phase, Phase.SHORE_START)
        self.cam.switch_next_char.assert_called_once()
        self.cam.switch_out.assert_called_once_with(con_full=False)

    def test_ordinary_target_loss_still_ends_combat(self):
        self.task.check_health_bar.return_value = False
        with self.assertRaises(CombatEnded):
            self.rotation.handoff(self.cam, Phase.SHORE_START)
        self.task.reset_to_false.assert_called_once_with(reason='target enemy failed')
        self.assertLess(self.clock[0], 104)

    def test_combat_ending_before_retry_prevents_new_switch_actions(self):
        error = self.first_timeout()
        self.task.combat_end_condition = lambda: True
        with self.assertRaises(CombatEnded):
            self.rotation.try_local_switch_recovery(self.cam, error)
        self.cam.switch_next_char.assert_called_once()
        self.task.send_key.assert_not_called()
        self.assertLess(self.clock[0], 104.57)

    def test_visible_combat_end_and_cancellation_propagate(self):
        for kind in ('combat_end', 'cancelled'):
            with self.subTest(kind=kind):
                self.setUp()
                if kind == 'combat_end':
                    self.task.combat_end_condition = lambda: True
                    expected = CombatEnded
                else:
                    self.task.middle_click.side_effect = TaskCancelled('cancelled')
                    expected = TaskCancelled
                with self.assertRaises(expected):
                    self.rotation.perform(self.cam)
                self.assertEqual(self.rotation.phase, Phase.SHORE_START)
                self.assertIsNone(self.rotation.switch_deadline)
                self.assertFalse(self.task.in_sleep_check)
                self.assertLess(self.clock[0], 100.1)
                self.task.mouse_up.assert_called()

    def test_handoff_restores_class_methods_and_instance_overrides(self):
        for instance_override in (False, True):
            with self.subTest(instance_override=instance_override):
                self.setUp()
                if instance_override:
                    self.task.in_combat = Mock(wraps=self.task.in_combat)
                    self.task.target_enemy = Mock(wraps=self.task.target_enemy)
                original = dict(self.task.__dict__)
                self.first_timeout()
                self.assertEqual(set(self.task.__dict__), set(original))
                for name in ('in_combat', 'target_enemy', 'click', 'check_combat', 'send_key'):
                    if name in original:
                        self.assertIs(self.task.__dict__[name], original[name])

    def test_visible_live_timer_protects_return_when_native_target_wait_would_fail(self):
        # 4547 分诊断场在第二次收尾后仍剩约 53 秒，却因三秒丢锁脱战并立即重入同局。
        self.task.get_abyss_remaining_time = Mock(return_value=53.0)
        self.task.check_health_bar.return_value = False
        self.allow_switch = True
        self.rotation.handoff(self.cam, Phase.SHORE_START)
        self.assertEqual(self.rotation.phase, Phase.SHORE_START)
        self.assertEqual(self.rotation.intro_source, 'Camellya')
        self.task.reset_to_false.assert_not_called()
        self.task.raise_not_in_combat.assert_not_called()
        self.task.middle_click.assert_not_called()
        self.task.get_abyss_remaining_time.assert_called()
        self.assertTrue(all(not call.args and not call.kwargs
                            for call in self.task.get_abyss_remaining_time.call_args_list))
        self.assertLess(self.clock[0], 100.1)

    def test_empty_or_ended_timer_preserves_native_combat_exit(self):
        for remaining in (None, 0.0, -1.0):
            with self.subTest(remaining=remaining):
                self.setUp()
                self.task.get_abyss_remaining_time = Mock(return_value=remaining)
                self.task.check_health_bar.return_value = False
                with self.assertRaises(CombatEnded):
                    self.rotation.handoff(self.cam, Phase.SHORE_START)
                self.task.reset_to_false.assert_called_once_with(reason='target enemy failed')
                self.task.send_key.assert_not_called()
                self.assertLess(self.clock[0], 104)

    def test_hidden_hud_does_not_use_cached_positive_timer_as_evidence(self):
        self.task.get_abyss_remaining_time = Mock(return_value=53.0)
        self.task.in_team.return_value = (False, -1, 3)
        self.task.check_health_bar.return_value = False
        with self.assertRaises(CombatEnded):
            self.rotation.handoff(self.cam, Phase.SHORE_START)
        self.task.get_abyss_remaining_time.assert_not_called()
        self.task.send_key.assert_not_called()

    def test_explicit_end_condition_is_not_overridden_by_positive_timer(self):
        self.task.get_abyss_remaining_time = Mock(return_value=53.0)
        self.task.combat_end_condition = lambda: True
        with self.assertRaises(CombatEnded):
            self.rotation.handoff(self.cam, Phase.SHORE_START)
        self.task.get_abyss_remaining_time.assert_not_called()
        self.task.reset_to_false.assert_called_once_with(reason='end condition reached')
        self.task.send_key.assert_not_called()

    def test_timer_cancellation_propagates_and_restores_return_wrappers(self):
        self.task.get_abyss_remaining_time = Mock(side_effect=TaskCancelled('cancelled'))
        original = dict(self.task.__dict__)
        with self.assertRaises(TaskCancelled):
            self.rotation.perform(self.cam)
        self.assertFalse(self.task.in_sleep_check)
        self.task.send_key.assert_not_called()
        self.task.mouse_up.assert_called()
        for name in ('send_key', 'click', 'check_combat'):
            if name in original:
                self.assertIs(getattr(self.task, name), original[name])
            else:
                self.assertNotIn(name, self.task.__dict__)

    def test_slow_timer_read_cannot_extend_return_attempt_or_send_keys_after_budget(self):
        def read_timer():
            self.clock[0] += 4.6
            return 53.0

        self.task.get_abyss_remaining_time = read_timer
        with self.assertRaisesRegex(RotationTimeout, 'switch timeout'):
            self.rotation.handoff(self.cam, Phase.SHORE_START)
        self.assertEqual(self.rotation.switch_deadline, 106)
        self.task.send_key.assert_not_called()
        self.task.reset_to_false.assert_not_called()
        self.assertFalse(self.task.in_sleep_check)

    def test_other_handoffs_keep_native_combat_checks(self):
        self.rotation.phase = Phase.SANHUA_OUTRO
        self.task.get_abyss_remaining_time = Mock(return_value=53.0)
        self.task.check_health_bar.return_value = False
        with self.assertRaises(CombatEnded):
            self.rotation.handoff(self.cam, Phase.SHORE_START)
        self.task.get_abyss_remaining_time.assert_not_called()
        self.task.send_key.assert_not_called()


if __name__ == '__main__':
    unittest.main()
