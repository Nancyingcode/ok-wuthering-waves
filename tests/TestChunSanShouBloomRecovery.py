import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

if __package__:
    from .TestChunSanShouPreparation import Phase, Rotation, RotationTimeout
else:
    from TestChunSanShouPreparation import Phase, Rotation, RotationTimeout


class TestChunSanShouBloomRecovery(unittest.TestCase):
    def setUp(self):
        self.clock = [100.0]
        self.addCleanup(patch.stopall)
        patch('time.monotonic', side_effect=lambda: self.clock[0]).start()
        patch('time.time', side_effect=lambda: self.clock[0]).start()
        self.char = SimpleNamespace(name='Camellya', index=0, logger=Mock(),
                                    get_forte=Mock(return_value=-1), check_combat=Mock(),
                                    check_target=Mock(), sleep=Mock(side_effect=self.advance),
                                    time_elapsed_accounting_for_freeze=lambda start: self.clock[0] - start)
        self.task = SimpleNamespace(chars=[self.char], mouse_down=Mock(), mouse_up=Mock(),
                                    next_frame=Mock(side_effect=lambda: self.advance(0.05)),
                                    in_team=Mock(return_value=(True, 0, 3)), in_liberation=False,
                                    has_target=Mock(return_value=True),
                                    find_one=Mock(return_value=True), combat_end_condition=None,
                                    target_enemy=Mock(), target_enemy_time_out=3,
                                    in_combat=Mock(return_value=True),
                                    do_check_in_combat=Mock(return_value=True), in_sleep_check=False)
        self.rotation = Rotation(self.task)
        self.rotation.phase = Phase.CAMELLYA_BURST
        self.rotation.capture_bloom_diagnostics = Mock()
        self.observation = {'forte': None, 'since': 0, 'samples': 0, 'retries': 0}

    def advance(self, duration, **kwargs):
        self.clock[0] += duration

    def sample(self, elapsed, forte=-1, visible=True, check_budget=None):
        self.clock[0] = 100 + elapsed
        self.char.get_forte.return_value = forte
        self.rotation.maintain_bloom_heavy(self.char, self.observation, elapsed, forte=forte,
                                          bloom_visible=visible, check_budget=check_budget)

    def unreadable_until_retry(self):
        for elapsed in (0, 0.3, 0.6, 0.9, 1.21):
            self.sample(elapsed)

    def test_unreadable_bloom_recovers_early_once_without_renewing_budget(self):
        for step in range(80):
            self.sample(step * 0.1)
        self.assertEqual(self.observation['retries'], 1)
        self.task.mouse_down.assert_called_once()
        self.assertEqual(self.rotation.phase_started, 100)
        self.assertEqual(self.rotation.BLOOM_OUTPUT_TIMEOUT, 8)
        self.assertEqual(self.rotation.BLOOM_WALL_TIMEOUT, 16)
        logs = [call.args[0] for call in self.char.logger.info.call_args_list]
        self.assertTrue(any('reason=forte_unreadable' in line and 'output=1.' in line for line in logs))

    def test_unreadable_without_visible_bloom_never_retries(self):
        for step in range(80):
            self.sample(step * 0.1, visible=False)
        self.task.mouse_up.assert_not_called()
        self.task.mouse_down.assert_not_called()

    def test_fresh_marker_loss_resets_unreadable_window(self):
        for elapsed in (0, 0.3, 0.6, 0.9):
            self.sample(elapsed)
        self.sample(1, visible=False)
        for elapsed in (1.1, 1.4, 1.7, 2):
            self.sample(elapsed)
        self.task.mouse_down.assert_not_called()

    def test_capture_gap_does_not_prove_continuous_bloom(self):
        for elapsed in (0, 0.3, 2, 2.3):
            self.sample(elapsed)
        self.task.mouse_down.assert_not_called()

    def test_occlusion_cannot_erase_observed_stall(self):
        for step in range(12):
            self.sample(step * 0.1, forte=0.799 if step % 2 == 0 else -1)
        self.task.mouse_down.assert_called_once()
        self.assertFalse(self.observation.get('feedback_retry_used', False))

    def test_continuous_consumption_does_not_retry(self):
        for step in range(30):
            self.sample(step * 0.1, forte=0.95 - step * 0.025)
        self.task.mouse_up.assert_not_called()
        self.task.mouse_down.assert_not_called()

    def test_unknown_recovery_and_known_stall_share_two_attempts(self):
        self.unreadable_until_retry()
        for step in range(15, 80):
            self.sample(step * 0.1, forte=0.799)
        self.assertEqual(self.observation['retries'], 2)
        self.assertEqual(self.task.mouse_down.call_count, 2)

    def test_near_empty_resource_never_triggers_heavy(self):
        for step in range(30):
            self.sample(step * 0.1, forte=0.032, visible=False)
        self.task.mouse_down.assert_not_called()

    def test_actor_or_hud_change_after_release_blocks_repress(self):
        for hud in ((False, -1, 3), (True, 1, 3)):
            with self.subTest(hud=hud):
                self.setUp()
                self.char.sleep.side_effect = lambda duration, **kw: setattr(self.task.in_team, 'return_value', hud)
                with self.assertRaisesRegex(RotationTimeout, 'actor or HUD changed'):
                    self.unreadable_until_retry()
                self.task.mouse_up.assert_called_once()
                self.task.mouse_down.assert_not_called()

    def test_visible_end_after_release_blocks_repress(self):
        self.task.combat_end_condition = lambda: True
        with self.assertRaisesRegex(RotationTimeout, 'combat ended'):
            self.unreadable_until_retry()
        self.task.mouse_down.assert_not_called()

    def test_target_loss_after_release_blocks_repress(self):
        self.char.sleep.side_effect = lambda duration, **kw: setattr(self.task.has_target, 'return_value', False)
        self.unreadable_until_retry()
        self.task.mouse_up.assert_called_once()
        self.task.mouse_down.assert_not_called()

    def test_new_frame_depletion_or_lost_marker_blocks_repress(self):
        for forte, marker, liberation in ((0.032, False, False), (-1, False, False), (-1, True, True)):
            with self.subTest(forte=forte, marker=marker, liberation=liberation):
                self.setUp()

                def changed_frame():
                    self.char.get_forte.return_value = forte
                    self.task.find_one.return_value = marker
                    self.task.in_liberation = liberation

                self.task.next_frame.side_effect = changed_frame
                self.unreadable_until_retry()
                self.task.mouse_up.assert_called_once()
                self.task.mouse_down.assert_not_called()

    def test_output_deadline_during_release_prevents_late_input(self):
        self.observation.update(forte=0.799, since=0, samples=10)

        def budget():
            if self.clock[0] >= 108:
                raise RotationTimeout('output timeout')

        with self.assertRaisesRegex(RotationTimeout, 'output timeout'):
            self.sample(7.95, forte=0.799, check_budget=budget)
        self.task.mouse_up.assert_called_once()
        self.task.mouse_down.assert_not_called()

    def test_cancellation_during_release_propagates(self):
        self.char.sleep.side_effect = RuntimeError('cancelled')
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.unreadable_until_retry()
        self.task.mouse_down.assert_not_called()

    def test_phase_deadline_during_capture_prevents_repress(self):
        self.task.next_frame.side_effect = lambda: self.clock.__setitem__(0, 124)
        with self.assertRaisesRegex(RotationTimeout, 'phase timeout'):
            self.unreadable_until_retry()
        self.task.mouse_down.assert_not_called()

    def test_persistent_80_percent_still_times_out_at_original_limit(self):
        self.char.get_forte.return_value = 0.799
        with self.assertRaisesRegex(RotationTimeout, 'output timeout'):
            self.rotation.bloom_output(self.char)
        self.assertLess(self.clock[0], 108.1)
        self.assertEqual(self.task.mouse_down.call_count, 3)
        self.assertGreaterEqual(self.task.mouse_up.call_count, 3)
        self.assertEqual(self.rotation.BLOOM_OUTPUT_TIMEOUT, 8)

    def test_missing_feedback_recovery_can_finish_interrupted_hold(self):
        # 末帧 79.9% 和 6.453 秒不可读来自日志；此前遮挡、重按后的消耗为构造模型。
        # 此用例验证恢复决定，不把模拟的输入响应当作第三轮实战成因。
        pressed_at = []
        self.task.mouse_down.side_effect = lambda: pressed_at.append(self.clock[0])

        def forte(**kwargs):
            if self.clock[0] < 106.453:
                return -1
            if len(pressed_at) < 2:
                return 0.799
            return max(0.032, 0.799 - (self.clock[0] - pressed_at[1]) * 0.145)

        self.char.get_forte.side_effect = forte
        self.task.find_one.side_effect = lambda *args, **kwargs: forte() < 0 or forte() > 0.032
        self.rotation.bloom_output(self.char)
        self.assertLess(pressed_at[1], 101.5)
        self.assertLess(self.clock[0], 108)
        self.assertLessEqual(forte(), 0.032)
        self.assertIn('outcome=completed', self.char.logger.info.call_args_list[-1].args[0])
        self.task.mouse_up.assert_called()

    def test_cancellation_in_output_restores_target_wrapper_and_releases(self):
        original_target = self.task.target_enemy
        self.task.next_frame.side_effect = RuntimeError('cancelled')
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.rotation.bloom_output(self.char)
        self.assertIs(self.task.target_enemy, original_target)
        self.task.mouse_up.assert_called()

    def test_cancellation_after_repress_releases_and_restores_output(self):
        self.char.sleep.side_effect = [None, RuntimeError('cancelled')]
        original_target = self.task.target_enemy
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.rotation.bloom_output(self.char)
        self.assertEqual(self.task.mouse_down.call_count, 2)
        self.assertIs(self.task.target_enemy, original_target)
        self.assertEqual(self.task.mouse_up.call_count, 2)

    def test_bloom_combat_check_timeout_reaches_local_recovery(self):
        # 全局 CombatCheck.in_combat 会吞掉段内 RotationTimeout；开花段必须让超时冒泡回局部恢复。
        original_in_combat = self.task.in_combat
        observed = []

        def check_combat():
            try:
                self.task.in_combat()
            except RotationTimeout as error:
                observed.append(str(error))
                raise

        self.char.check_combat.side_effect = check_combat
        self.task.do_check_in_combat.side_effect = RotationTimeout('target wait budget exhausted')
        with self.assertRaisesRegex(RotationTimeout, 'target wait budget exhausted'):
            self.rotation.bloom_output(self.char)
        self.assertEqual(observed, ['target wait budget exhausted'])
        self.assertIs(self.task.in_combat, original_in_combat)
        self.assertFalse(self.task.in_sleep_check)
        self.task.mouse_up.assert_called()

    def test_target_wait_budget_is_capped_and_isolated_from_phase_budget(self):
        # 丢锁空等不再按相位剩余时间申请预算：单次 2 秒、累计 3 秒封顶，用尽即交回局部恢复。
        budgets = []

        def native_target_enemy(wait=True, time_out=None, check_health=False):
            budgets.append(time_out)
            if time_out:
                self.clock[0] += time_out
            return False

        self.task.target_enemy = native_target_enemy
        self.task.target_enemy_time_out = 30
        self.char.check_combat.side_effect = lambda: self.task.target_enemy(wait=True)
        self.char.get_forte.return_value = 0.799
        with self.assertRaisesRegex(RotationTimeout, 'target wait budget exhausted'):
            self.rotation.bloom_output(self.char)
        self.assertTrue(budgets)
        for budget in budgets:
            self.assertLessEqual(budget, self.rotation.BLOOM_TARGET_WAIT_MAX)
        self.assertGreaterEqual(sum(budgets), self.rotation.BLOOM_TARGET_WAIT_TOTAL - 1e-6)
        self.assertEqual(self.rotation.PHASE_TIMEOUT, 24)
        self.assertLess(self.clock[0], 104)
        self.assertIs(self.task.target_enemy, native_target_enemy)

    def test_failed_target_wait_returns_to_local_recovery(self):
        # 重锁失败在全局检测里等于脱战；开花段必须把它交回队内局部恢复。
        native_target_enemy = Mock(return_value=False, side_effect=lambda **kwargs: self.advance(0.1))
        self.task.target_enemy = native_target_enemy
        self.task.target_enemy_time_out = 30
        self.task.do_check_in_combat = Mock(return_value=False)

        def check_combat():
            self.task.target_enemy(wait=True)
            self.task.in_combat()

        self.char.check_combat.side_effect = check_combat
        self.char.get_forte.return_value = 0.799
        with self.assertRaisesRegex(RotationTimeout, 'target lost during output'):
            self.rotation.bloom_output(self.char)
        self.assertTrue(native_target_enemy.called)
        self.assertIs(self.task.target_enemy, native_target_enemy)
        self.task.mouse_up.assert_called()


if __name__ == '__main__':
    unittest.main()
