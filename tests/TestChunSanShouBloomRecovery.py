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


    def test_unreadable_frame_keeps_existing_consumption_evidence(self):
        # 单帧不可读不得清窗：实测 09:07:48 曾因下一帧读到 -1.0 把整段证据抹成 1 个样本。
        samples = [(0.0, 0.20), (0.2, 0.175), (0.4, 0.15)]
        self.assertEqual(Rotation.record_bloom_progress(samples, -1, 0.6), 'forte_unknown')
        self.assertEqual(len(samples), 3)
        self.assertEqual(Rotation.bloom_tail_status(samples, 0.6)['reason'], 'progressing')

    def test_cumulative_drop_supplies_consumption_without_one_large_step(self):
        samples = [(0.0, 0.25), (0.2, 0.24), (0.4, 0.23), (0.6, 0.21)]
        self.assertEqual(Rotation.bloom_tail_status(samples, 0.6)['reason'], 'progressing')
        short = [(0.0, 0.25), (0.2, 0.24), (0.4, 0.235)]
        self.assertEqual(Rotation.bloom_tail_status(short, 0.4)['reason'], 'insufficient_decreases')

    def test_relative_baseline_opens_tail_only_after_consumed_ratio(self):
        samples = [(0.0, 0.90), (0.3, 0.60), (0.6, 0.28)]
        self.assertEqual(Rotation.bloom_tail_status(samples, 0.6)['reason'], 'outside_tail')
        status = Rotation.bloom_tail_status(samples, 0.6, 0.95)
        self.assertEqual(status['reason'], 'progressing')
        self.assertGreaterEqual(status['consumed_ratio'], Rotation.BLOOM_TAIL_CONSUMED_RATIO)

    def test_unknown_baseline_never_opens_relative_tail(self):
        samples = [(0.0, 0.90), (0.3, 0.60), (0.6, 0.28)]
        for baseline in (None, -1, 0, 1.2):
            with self.subTest(baseline=baseline):
                self.assertEqual(Rotation.bloom_tail_status(samples, 0.6, baseline)['reason'], 'outside_tail')

    def test_near_full_stall_represses_before_nine_tenths(self):
        for elapsed in (0.0, 0.2, 0.4):
            self.sample(elapsed, forte=0.99, visible=True)
        self.assertEqual(self.observation['retries'], 1)
        self.task.mouse_down.assert_called_once()

    def test_boundary_sample_is_recorded_before_tail_decision(self):
        # 八秒边界那一帧的下降必须先入账再判软预算，否则「已打到 18% 仍判不足」会复现。
        def forte(**kwargs):
            at = self.clock[0] - 100
            if at < 8.0:
                return 0.28
            if at < 8.1:
                return 0.20
            return 0.05

        self.char.get_forte.side_effect = forte
        self.task.find_one.side_effect = lambda *args, **kwargs: (self.clock[0] - 100) < 8.1
        with patch.object(self.char, 'logger') as logger:
            self.rotation.bloom_output(self.char)
        logs = [entry.args[0] for entry in logger.info.call_args_list]
        self.assertIn('outcome=completed', logs[-1])
        self.assertIn('tail_grace=True', logs[-1])


    def test_full_forte_jitter_does_not_postpone_stall_retry(self):
        # 满格平台的识别抖动不得把停滞起点反复重置：1.2.1 实测因此拖到 1.271 秒才首次补按。
        jitter = [0.922, 0.936, 0.918, 0.930, 0.915, 0.928, 0.920, 0.933]
        for step, value in enumerate(jitter):
            self.sample(step * 0.1, forte=value)
            if self.observation['retries']:
                break
        self.assertEqual(self.observation['retries'], 1)
        self.assertLessEqual(self.clock[0] - 100, 0.7)
        self.task.mouse_down.assert_called_once()

    def test_plateau_consumption_beyond_three_percent_skips_repress(self):
        # 满格后已真实消耗 3% 以上属于正常输出，不得触发满格平台的补按。
        for step in range(6):
            self.sample(step * 0.1, forte=0.98 - step * 0.04)
        self.assertEqual(self.observation['retries'], 0)
        self.task.mouse_down.assert_not_called()


    def test_plateau_upward_jitter_raises_peak_without_resetting_stall(self):
        # 1.2.2 实测：0.922 档上抖到 0.94x 会把停滞起点重置，首次补按被拖到 1.112 / 1.231 / 2.172 秒。
        # 1.2.3 起向上抖动只抬高峰值，起点保持，0.4 秒判据不被推迟。
        for step, value in enumerate([0.922, 0.945, 0.918, 0.940, 0.920]):
            self.sample(step * 0.1, forte=value)
            if self.observation['retries']:
                break
        self.assertEqual(self.observation['retries'], 1)
        self.assertLessEqual(self.clock[0] - 100, 0.7)
        self.task.mouse_down.assert_called_once()

    def test_plateau_real_consumption_rearms_plateau_timer(self):
        # 「已真实消耗峰值 3% 以上」仍必须结束平台期并重新起算，不能被抖动修复吞掉。
        for step, value in enumerate([0.980, 0.975, 0.955, 0.945]):
            self.sample(step * 0.1, forte=value)
        self.assertEqual(self.observation['retries'], 0)
        self.task.mouse_down.assert_not_called()


    def test_below_nine_tenths_jitter_falls_back_to_nine_tenth_second_rule(self):
        # 1.2.5 回滚 1.2.4 的滑动窗口后，0.897 档重新落到 0.9 硬阈值之下，退回 0.9 秒判据：
        # 逐帧 1.5% 以上的读数抖动会不断重置停滞起点，所以 0.5 秒内不会补按——与 1.2.3 实测 2.050 秒一致。
        # 这是汇总文档明确接受的取舍：补按时机这条线就此停手，不再为它加判据。
        for step, value in enumerate((0.897, 0.930, 0.910, 0.885, 0.905, 0.890)):
            self.sample(step * 0.1, forte=value)
        self.assertEqual(self.observation['retries'], 0)
        self.task.mouse_down.assert_not_called()

    def test_real_consumption_across_nine_tenths_never_represses_early(self):
        # 真实消耗跨越 0.9 阈值（0.4 秒跌 9%）必须继续判为「在消耗」，不得触发提前补按。
        for step, value in enumerate((0.950, 0.920, 0.890, 0.860, 0.830)):
            self.sample(step * 0.1, forte=value)
        self.assertEqual(self.observation['retries'], 0)
        self.task.mouse_down.assert_not_called()

    def test_heavy_stall_window_rollback_removes_early_channel(self):
        # 1.2.5 回滚 1.2.4 的滑动窗口：常量、recent 缓冲与 early_retry_used 快速通道都不应残留。
        self.assertFalse(hasattr(self.rotation, 'BLOOM_HEAVY_STALL_WINDOW'))
        self.assertFalse(hasattr(self.rotation, 'BLOOM_HEAVY_STALL_RANGE'))
        for step in range(20):
            self.sample(step * 0.1, forte=0.799)
        self.assertNotIn('early_retry_used', self.observation)
        self.assertNotIn('recent', self.observation)
        self.assertEqual(self.observation['retries'], 2)

    def test_tail_baseline_tracks_observed_peak_instead_of_first_frame(self):
        # 不可读帧不参与基线更新；可读帧只抬高峰值，不会被后段回落拉低。
        baseline = None
        for forte in (-1, 0.403, 0.52, 0.95, 0.88, -1, 0.30):
            baseline = Rotation.bloom_tail_baseline(baseline, forte)
        self.assertAlmostEqual(baseline, 0.95)

    def test_peak_baseline_turns_pinned_low_tail_into_progressing(self):
        # 1.2.4 实测 17:57:59：samples=31、forte=0.304、consumed_ratio=0.245 → 反推基线只有 0.403。
        # 基线改用本段观测峰值后，同一组读数必须判 progressing 并允许宽限收尾。
        samples = [(7.0, 0.403), (7.2, 0.401), (7.4, 0.398), (7.6, 0.352), (7.8, 0.304)]
        self.assertEqual(Rotation.bloom_tail_status(samples, 7.8, 0.403)['reason'], 'outside_tail')
        status = Rotation.bloom_tail_status(samples, 7.8, 0.95)
        self.assertEqual(status['reason'], 'progressing')
        self.assertGreaterEqual(status['consumed_ratio'], Rotation.BLOOM_TAIL_CONSUMED_RATIO)

    def test_low_first_readable_frame_no_longer_pins_tail_baseline(self):
        # cast 结束头几帧偏低（0.403）、随后回满 0.95、尾段掉到 0.30：基线取峰值才能在 8 秒边界判 progressing。
        # 旧基线（首帧 0.403）在同一形态下会以 outside_tail 超时。
        def forte(**kwargs):
            at = self.clock[0] - 100
            if at < 0.3:
                return 0.403
            if at < 6.2:
                return 0.95
            if at < 8.0:
                return 0.95 - (at - 6.2) * 0.361
            return 0.05

        self.char.get_forte.side_effect = forte
        self.task.find_one.side_effect = lambda *args, **kwargs: (self.clock[0] - 100) < 8.0
        with patch.object(self.char, 'logger') as logger:
            self.rotation.bloom_output(self.char)
        logs = [entry.args[0] for entry in logger.info.call_args_list]
        self.assertIn('outcome=completed', logs[-1])
        self.assertIn('tail_grace=True', logs[-1])

    def test_zero_steady_tail_opens_exit_past_stale_decrease(self):
        # 1.2.5 实测 18:25:38（forte=0.000、drop_age=1.046）与 18:28:46（forte=0.032、drop_age=0.953）：
        # 读数已贴零并稳定超过 0.75 秒，却因最近一次下降过旧判 stale_decrease，而 bloom_ended 又要求标记消失。
        # 贴零段内不会再有 >=1.5% 的下降，所以 drop_age 只会继续变旧——这正是旧判据拒绝的形态。
        samples = [(0.0, 0.90), (0.3, 0.55), (0.6, 0.18), (1.2, 0.04), (1.5, 0.04), (1.8, 0.04), (2.1, 0.04)]
        status = Rotation.bloom_tail_status(samples, 2.4)
        self.assertEqual(status['reason'], 'progressing')
        self.assertGreaterEqual(status['zero_hold'], Rotation.BLOOM_TAIL_ZERO_SPAN)
        self.assertGreater(status['drop_age'], 0.75)

    def test_zero_steady_requires_full_zero_span(self):
        # 贴零样本跨度只有 0.3 秒时不得放行：仍按最近下降过旧判 stale_decrease。
        # 进入贴零那一步只有 1% 降幅，不计入下降证据，所以 drop_age 停在 0.9 秒。
        samples = [(0.0, 0.90), (0.5, 0.50), (1.0, 0.20), (1.5, 0.055), (2.0, 0.045), (2.3, 0.045)]
        status = Rotation.bloom_tail_status(samples, 2.4)
        self.assertEqual(status['reason'], 'stale_decrease')
        self.assertLess(status['zero_hold'], Rotation.BLOOM_TAIL_ZERO_SPAN)
        self.assertGreater(status['drop_age'], 0.75)

    def test_zero_steady_never_releases_mid_band_plateau(self):
        # P3（0.20–0.70 中高位纹丝不动）不在 1.2.6 范围内：没有贴零读数仍判 stale_decrease。
        samples = [(0.0, 0.90), (0.4, 0.60), (0.8, 0.35), (1.2, 0.30), (1.6, 0.30), (2.0, 0.30)]
        status = Rotation.bloom_tail_status(samples, 2.1, 0.95)
        self.assertEqual(status['reason'], 'stale_decrease')
        self.assertEqual(status['zero_hold'], -1.0)

    def test_zero_steady_keeps_eight_hard_limits_untouched(self):
        # 1.2.6 只新增贴零稳态判据：八个硬约束常量与既有门限必须逐字不动。
        self.assertEqual(self.rotation.BLOOM_OUTPUT_TIMEOUT, 8)
        self.assertEqual(self.rotation.BLOOM_WALL_TIMEOUT, 16)
        self.assertEqual(self.rotation.PHASE_TIMEOUT, 24)
        self.assertEqual(self.rotation.BLOOM_TAIL_GRACE, 2)
        self.assertEqual(self.rotation.BLOOM_TAIL_RECHECK, 0.4)
        self.assertEqual(self.rotation.BLOOM_FEEDBACK_TIMEOUT, 1.2)
        self.assertEqual(self.rotation.BLOOM_TARGET_WAIT_MAX, 2.0)
        self.assertEqual(self.rotation.BLOOM_TARGET_WAIT_TOTAL, 3.0)
        self.assertEqual(self.rotation.BLOOM_TAIL_ZERO_SPAN, 0.75)
        self.assertEqual(self.rotation.BLOOM_ENDED_FORTE, 0.05)

    def test_zero_steady_grace_covers_marker_lag_until_bloom_ended(self):
        # 贴零但开花标记仍在屏：旧判据在 8 秒边界给 output timeout 或 entry recheck timeout，
        # 1.2.6 先按贴零稳态开宽限，标记在宽限内消失即 completed。
        def forte(**kwargs):
            at = self.clock[0] - 100
            return max(0.0, 0.95 - at * 0.16)

        self.char.get_forte.side_effect = forte
        self.task.find_one.side_effect = lambda *args, **kwargs: (self.clock[0] - 100) < 8.35
        with patch.object(self.char, 'logger') as logger:
            self.rotation.bloom_output(self.char)
        logs = [entry.args[0] for entry in logger.info.call_args_list]
        self.assertIn('outcome=completed', logs[-1])
        self.assertIn('tail_grace=True', logs[-1])
        self.assertTrue(any('bloom tail grace start' in line for line in logs))

    def test_steady_completion_finishes_frozen_mid_band_output(self):
        # 1.2.7：实测 19:11:31 读数卡 0.724 整整 8 秒（decreases=0、drop_total=0.0、samples=38），
        # 同段两次补按 bloom heavy recovery reason=forte_stalled marker=True，即 heavy_retries=2 已用尽；
        # 旧判据在预算边界判 outside_tail → output timeout，1.2.7 按中高位稳态收尾。
        self.char.get_forte.side_effect = lambda **kwargs: 0.724
        self.rotation.bloom_output(self.char)
        logs = [entry.args[0] for entry in self.char.logger.info.call_args_list]
        self.assertIn('outcome=completed', logs[-1])
        self.assertIn('steady=True', logs[-1])
        self.assertIn('tail_grace=False', logs[-1])
        self.assertTrue(any('bloom steady completion' in line for line in logs))
        self.assertEqual(self.rotation.capture_bloom_diagnostics.call_args_list[-1].args,
                         (self.char, 'steady_completion', 'mid_band_frozen'))

    def test_steady_flat_needs_tolerance_and_full_span(self):
        # 容差 0.5% 与跨度 1.0 秒同时成立才算冻结；抖动 1.6% 时尾部窗口只剩最后一帧。
        plateau = [(5.0 + step * 0.1, 0.724) for step in range(12)]
        steady = Rotation.bloom_steady_status(plateau)
        self.assertTrue(steady['steady_flat'])
        self.assertGreaterEqual(steady['steady_hold'], Rotation.BLOOM_TAIL_STEADY_SPAN)
        self.assertLessEqual(steady['steady_range'], Rotation.BLOOM_TAIL_STEADY_TOLERANCE)
        jitter = [(5.0 + step * 0.1, 0.724 + (0.016 if step % 2 else 0.0)) for step in range(12)]
        self.assertFalse(Rotation.bloom_steady_status(jitter)['steady_flat'])

    def test_steady_completion_requires_exhausted_retries_marker_and_budget(self):
        samples = [(6.5 + step * 0.1, 0.452) for step in range(16)]
        self.assertTrue(Rotation.bloom_steady_completion_allowed(
            samples, 8.0, 8.0, 0.452, 2, True, True, False))
        # 19:05:07 的 resume 形态（重击只补 1 次）：仍判失败，归 bloom_resume_budget 单独立项。
        self.assertFalse(Rotation.bloom_steady_completion_allowed(
            samples, 4.844, 4.828, 0.452, 1, True, True, False))
        # 预算未耗尽不得提前截断真实输出。
        self.assertFalse(Rotation.bloom_steady_completion_allowed(
            samples, 7.900, 8.0, 0.452, 2, True, True, False))
        # 标记不可见 / 不在场 / 处于解放态都不算「仍在开花中」。
        self.assertFalse(Rotation.bloom_steady_completion_allowed(
            samples, 8.0, 8.0, 0.452, 2, True, False, False))
        self.assertFalse(Rotation.bloom_steady_completion_allowed(
            samples, 8.0, 8.0, 0.452, 2, False, True, False))
        self.assertFalse(Rotation.bloom_steady_completion_allowed(
            samples, 8.0, 8.0, 0.452, 2, True, True, True))
        # 0.799 满格平台按验收基线 D1/D3 必须继续超时：中高位区间上限刻意压在 0.75。
        self.assertFalse(Rotation.bloom_steady_completion_allowed(
            [(6.5 + step * 0.1, 0.799) for step in range(16)], 8.0, 8.0, 0.799, 2, True, True, False))

    def test_steady_completion_never_takes_zero_steady_tail(self):
        # 贴零形态已由 1.2.6 的 zero_steady 判 progressing，1.2.7 只接中高位 outside_tail。
        zero = [(0.0, 0.90), (0.3, 0.55), (0.6, 0.18), (1.2, 0.04), (1.5, 0.04), (1.8, 0.04), (2.1, 0.04)]
        self.assertEqual(Rotation.bloom_tail_status(zero, 2.4)['reason'], 'progressing')
        self.assertFalse(Rotation.bloom_steady_completion_allowed(
            zero, 8.0, 8.0, 0.04, 2, True, True, False))

    def test_steady_completion_keeps_eight_hard_limits_untouched(self):
        # 1.2.7 只新增中高位稳态判据：八个硬约束常量与 1.2.6 贴零门限必须逐字不动。
        self.assertEqual(self.rotation.BLOOM_OUTPUT_TIMEOUT, 8)
        self.assertEqual(self.rotation.BLOOM_WALL_TIMEOUT, 16)
        self.assertEqual(self.rotation.PHASE_TIMEOUT, 24)
        self.assertEqual(self.rotation.BLOOM_TAIL_GRACE, 2)
        self.assertEqual(self.rotation.BLOOM_TAIL_RECHECK, 0.4)
        self.assertEqual(self.rotation.BLOOM_FEEDBACK_TIMEOUT, 1.2)
        self.assertEqual(self.rotation.BLOOM_TARGET_WAIT_MAX, 2.0)
        self.assertEqual(self.rotation.BLOOM_TARGET_WAIT_TOTAL, 3.0)
        self.assertEqual(self.rotation.BLOOM_TAIL_ZERO_SPAN, 0.75)
        self.assertEqual(self.rotation.BLOOM_TAIL_STEADY_SPAN, 1.0)
        self.assertEqual(self.rotation.BLOOM_TAIL_STEADY_TOLERANCE, 0.005)
        self.assertEqual(self.rotation.BLOOM_TAIL_STEADY_MIN, 0.20)
        self.assertEqual(self.rotation.BLOOM_TAIL_STEADY_MAX, 0.75)
        self.assertEqual(self.rotation.BLOOM_TAIL_STEADY_RETRIES, 2)

    def test_floor_completion_finishes_three_grid_floor_after_stale_decrease(self):
        # 1.2.8：实测 21:43:54 —— forte=0.082（三格 8.15%）、decreases=3、drop_age=1.188、
        # steady_hold=1.11、steady_range=0.0、steady_flat=True、drop_total=0.074、consumed_ratio=0.918。
        # 该读数高于 BLOOM_COMPLETE_FORTE 与 BLOOM_ENDED_FORTE，又 <=0.25 只判 stale_decrease，
        # 1.2.6 贴零与 1.2.7 outside_tail 两条出口都接不住——1.2.8 的地板出口必须接走。
        samples = [(5.0, 0.30), (5.5, 0.20), (5.9, 0.12), (6.2, 0.082),
                   (6.5, 0.082), (6.8, 0.082), (7.1, 0.082), (7.4, 0.082), (8.0, 0.082)]
        status = Rotation.bloom_tail_status(samples, 8.0, 1.0)
        self.assertEqual(status['reason'], 'stale_decrease')
        self.assertAlmostEqual(status['consumed_ratio'], 0.918)
        self.assertGreater(status['drop_age'], 0.75)
        self.assertTrue(Rotation.bloom_floor_completion_allowed(
            samples, 8.0, 8.0, 0.082, 2, True, True, False, 1.0))

    def test_floor_completion_finishes_three_grid_floor_after_window_reset(self):
        # 实测 21:44:31 —— samples=26、decreases=0、drop_age=-1、drop_total=0.0、span=1.187：
        # 窗口在复位后只剩贴地样本，尾段只能判 insufficient_decreases；基线仍取复位前的本段峰值。
        samples = [(6.7 + step * 0.05, 0.082) for step in range(26)]
        status = Rotation.bloom_tail_status(samples, 8.0, 1.0)
        self.assertEqual(status['reason'], 'insufficient_decreases')
        self.assertGreaterEqual(status['steady_hold'], Rotation.BLOOM_TAIL_FLOOR_SPAN)
        self.assertTrue(Rotation.bloom_floor_completion_allowed(
            samples, 8.0, 8.0, 0.082, 2, True, True, False, 1.0))

    def test_floor_completion_rejects_four_grid_and_partial_consumption(self):
        # 四格 ≈0.109 与只消耗一半的中低位都不放行；预算未耗尽、补按未用尽、标记/在场/解放态一律拒绝。
        samples = [(6.7 + step * 0.05, 0.082) for step in range(26)]
        self.assertTrue(Rotation.bloom_floor_completion_allowed(
            samples, 8.0, 8.0, 0.082, 2, True, True, False, 1.0))
        self.assertFalse(Rotation.bloom_floor_completion_allowed(
            samples, 8.0, 8.0, 0.109, 2, True, True, False, 1.0))
        # 同一读数在基线 0.30 时已消耗 72.7%（放行），在基线 0.20 时只有 59%（拒绝）。
        self.assertTrue(Rotation.bloom_floor_completion_allowed(
            samples, 8.0, 8.0, 0.082, 2, True, True, False, 0.30))
        self.assertFalse(Rotation.bloom_floor_completion_allowed(
            samples, 8.0, 8.0, 0.082, 2, True, True, False, 0.20))
        self.assertFalse(Rotation.bloom_floor_completion_allowed(
            samples, 7.900, 8.0, 0.082, 2, True, True, False, 1.0))
        self.assertFalse(Rotation.bloom_floor_completion_allowed(
            samples, 8.0, 8.0, 0.082, 1, True, True, False, 1.0))
        self.assertFalse(Rotation.bloom_floor_completion_allowed(
            samples, 8.0, 8.0, 0.082, 2, True, False, False, 1.0))
        self.assertFalse(Rotation.bloom_floor_completion_allowed(
            samples, 8.0, 8.0, 0.082, 2, False, True, False, 1.0))
        self.assertFalse(Rotation.bloom_floor_completion_allowed(
            samples, 8.0, 8.0, 0.082, 2, True, True, True, 1.0))
        # 峰值不可读时不开放相对消耗判据。
        self.assertFalse(Rotation.bloom_floor_completion_allowed(
            samples, 8.0, 8.0, 0.082, 2, True, True, False))

    def test_floor_completion_never_takes_mid_band_or_zero_tail(self):
        # 1.2.7 的 0.724 中高位平台、1.2.6 的贴零尾巴、中低位 0.30 都不在地板窗内。
        mid = [(6.5 + step * 0.1, 0.724) for step in range(16)]
        self.assertFalse(Rotation.bloom_floor_completion_allowed(
            mid, 8.0, 8.0, 0.724, 2, True, True, False, 1.0))
        zero = [(0.0, 0.90), (0.3, 0.55), (0.6, 0.18), (1.2, 0.04),
                (1.5, 0.04), (1.8, 0.04), (2.1, 0.04)]
        self.assertFalse(Rotation.bloom_floor_completion_allowed(
            zero, 8.0, 8.0, 0.04, 2, True, True, False, 1.0))
        low = [(6.5 + step * 0.1, 0.30) for step in range(16)]
        self.assertFalse(Rotation.bloom_floor_completion_allowed(
            low, 8.0, 8.0, 0.30, 2, True, True, False, 1.0))

    def test_floor_completion_finishes_three_grid_plateau(self):
        # 端到端：回路从 0.95 消耗到 0.082 后纹丝不动、开花标记仍在屏，8 秒预算边界必须按地板收尾，
        # 不再以 output timeout / tail grace 收场；bloom timing 行须带 floor=True。
        def forte(**kwargs):
            at = self.clock[0] - 100
            if at < 5.0:
                return 0.95 - at * 0.16
            return 0.082

        self.char.get_forte.side_effect = forte
        with patch.object(self.char, 'logger') as logger:
            self.rotation.bloom_output(self.char)
        logs = [entry.args[0] for entry in logger.info.call_args_list]
        self.assertIn('outcome=completed', logs[-1])
        self.assertIn('floor=True', logs[-1])
        self.assertIn('steady=False', logs[-1])
        self.assertTrue(any('bloom floor completion' in line for line in logs))
        self.assertEqual(self.rotation.capture_bloom_diagnostics.call_args_list[-1].args,
                         (self.char, 'floor_completion', 'three_grid_floor'))

    def test_floor_completion_keeps_end_thresholds_and_hard_limits_untouched(self):
        # 1.2.8 只新增地板出口：两个结束确认阈值、八个硬约束常量与 1.2.6 / 1.2.7 门限逐字不动。
        self.assertEqual(self.rotation.BLOOM_COMPLETE_FORTE, 0.06)
        self.assertEqual(self.rotation.BLOOM_ENDED_FORTE, 0.05)
        self.assertEqual(self.rotation.BLOOM_OUTPUT_TIMEOUT, 8)
        self.assertEqual(self.rotation.BLOOM_WALL_TIMEOUT, 16)
        self.assertEqual(self.rotation.PHASE_TIMEOUT, 24)
        self.assertEqual(self.rotation.BLOOM_TAIL_GRACE, 2)
        self.assertEqual(self.rotation.BLOOM_TAIL_RECHECK, 0.4)
        self.assertEqual(self.rotation.BLOOM_FEEDBACK_TIMEOUT, 1.2)
        self.assertEqual(self.rotation.BLOOM_TARGET_WAIT_MAX, 2.0)
        self.assertEqual(self.rotation.BLOOM_TARGET_WAIT_TOTAL, 3.0)
        self.assertEqual(self.rotation.BLOOM_TAIL_ZERO_SPAN, 0.75)
        self.assertEqual(self.rotation.BLOOM_TAIL_STEADY_MIN, 0.20)
        self.assertEqual(self.rotation.BLOOM_TAIL_STEADY_MAX, 0.75)
        self.assertEqual(self.rotation.BLOOM_TAIL_FLOOR_MAX, 0.10)
        self.assertEqual(self.rotation.BLOOM_TAIL_FLOOR_SPAN, 1.0)
        self.assertEqual(self.rotation.BLOOM_TAIL_FLOOR_RETRIES, 2)
        self.assertEqual(self.rotation.BLOOM_TAIL_CONSUMED_RATIO, 0.65)
        self.assertEqual(self.rotation.BLOOM_TAIL_UNREADABLE_MAX_AGE, 1.0)
        self.assertEqual(self.rotation.BLOOM_TAIL_UNREADABLE_MIN_SAMPLES, 3)
        self.assertEqual(self.rotation.BLOOM_TAIL_UNREADABLE_FORTE_MAX, 0.25)

    @staticmethod
    def boundary_blackout_samples():
        # 1.2.9 实测 9.000 段原文 history：最后可读样本 8.046 forte=0.156，之后回路环读不到。
        return [(7.093, 0.304), (7.187, 0.304), (7.64, 0.18), (7.75, 0.23),
                (7.859, 0.156), (7.937, 0.156), (8.046, 0.156)]

    def test_unreadable_grace_takes_boundary_blackout_with_full_consumption(self):
        # 9.000 段：sample_age=0.954、samples=7、consumed_ratio=0.836、基线 0.95，判据层唯一可救的形态。
        samples = self.boundary_blackout_samples()
        status = Rotation.bloom_tail_status(samples, 9.0, 0.95)
        self.assertEqual(status['reason'], 'stale_sample')
        self.assertAlmostEqual(status['consumed_ratio'], 0.836)
        self.assertTrue(Rotation.bloom_unreadable_grace_allowed(
            samples, 9.0, 8.0, -1.0, True, True, False, 0.95))

    def test_unreadable_grace_rejects_blackout_without_consumption_evidence(self):
        # 8.016 / 8.047 两段整段零可读样本（samples=0、consumed_ratio=-1、基线未建立），属识别层，不接手。
        self.assertFalse(Rotation.bloom_unreadable_grace_allowed(
            [], 8.016, 8.0, -1.0, True, True, False, None))
        # 窗口只剩一两个样本时无法证明这段输出真打掉了回路，交回原超时。
        self.assertFalse(Rotation.bloom_unreadable_grace_allowed(
            [(7.96, 0.156), (8.03, 0.156)], 8.05, 8.0, -1.0, True, True, False, 0.95))

    def test_unreadable_grace_rejects_each_gate(self):
        samples = self.boundary_blackout_samples()

        def allowed(**overrides):
            params = dict(samples=samples, output_wall=9.0, output_budget=8.0, forte=-1.0,
                          same_character=True, marker_visible=True, in_liberation=False,
                          baseline=0.95)
            params.update(overrides)
            return Rotation.bloom_unreadable_grace_allowed(
                params['samples'], params['output_wall'], params['output_budget'], params['forte'],
                params['same_character'], params['marker_visible'], params['in_liberation'],
                params['baseline'])

        self.assertTrue(allowed())
        # 预算未耗尽 / 宽限已超出 BLOOM_TAIL_GRACE / 本帧可读 / 消耗不足 / 断流过久 / 标记消失 / 不在场 / 解放态。
        self.assertFalse(allowed(output_wall=7.9))
        self.assertFalse(allowed(output_wall=10.5))
        self.assertFalse(allowed(forte=0.156))
        self.assertFalse(allowed(baseline=0.20))
        self.assertFalse(allowed(output_wall=9.4))
        self.assertFalse(allowed(marker_visible=False))
        self.assertFalse(allowed(same_character=False))
        self.assertFalse(allowed(in_liberation=True))

    def test_unreadable_grace_end_to_end_marks_timing_flag(self):
        # 端到端：回路从 0.95 稳定消耗到 0.15，预算边界后回路环读不到；不得再以 output timeout /
        # tail progress lost 收场，bloom timing 行须带 unreadable_grace=True。
        def forte(**kwargs):
            at = self.clock[0] - 100
            if at < 2.0:
                return 0.95
            if at < 7.1:
                return 0.95 - (at - 2.0) * (0.80 / 5.1)
            return -1.0

        self.char.get_forte.side_effect = forte
        with patch.object(self.char, 'logger') as logger:
            self.rotation.bloom_output(self.char)
        logs = [entry.args[0] for entry in logger.info.call_args_list]
        self.assertIn('outcome=completed', logs[-1])
        self.assertIn('unreadable_grace=True', logs[-1])
        self.assertIn('floor=False', logs[-1])
        self.assertTrue(any('bloom unreadable grace completion' in line for line in logs))


if __name__ == '__main__':
    unittest.main()
