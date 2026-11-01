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

    @staticmethod
    def consumed_tail_samples():
        # 1.2.10 实测 8.000 段原文：6.641 起读数稳定停在 0.279，13 个样本、span=1.437、decreases=0。
        return [(6.641 + step * 0.1, 0.279) for step in range(13)]

    def test_consumed_tail_finishes_stalled_after_full_consumption(self):
        # consumed_ratio=0.697、samples=13、sample_age=0.0、steady_flat=True、steady_hold=1.437，
        # 该段 heavy_retries=0 —— 补按门不能作为前提，否则唯一实测形态会被自己拒掉。
        samples = self.consumed_tail_samples()
        status = Rotation.bloom_tail_status(samples, 8.0, 0.92)
        self.assertEqual(status['reason'], 'insufficient_decreases')
        self.assertAlmostEqual(status['consumed_ratio'], 0.697)
        self.assertEqual(status['decreases'], 0)
        self.assertTrue(Rotation.bloom_consumed_tail_allowed(
            samples, 8.0, 8.0, 0.279, True, True, False, 0.92))

    def test_consumed_tail_rejects_unfinished_and_partial_consumption(self):
        # 0.823 / 0.700 两条 outside_tail 是真实未打完（消耗 10.7% / 26.3%），必须仍判超时。
        self.assertFalse(Rotation.bloom_consumed_tail_allowed(
            [(6.5 + step * 0.075, 0.823) for step in range(20)], 8.0, 8.0, 0.823, True, True, False, 0.92))
        self.assertFalse(Rotation.bloom_consumed_tail_allowed(
            [(6.5 + step * 0.075, 0.700) for step in range(20)], 8.0, 8.0, 0.700, True, True, False, 0.95))
        samples = self.consumed_tail_samples()
        # 基线过低（0.34，只消耗 17.9%）与峰值不可读（-1）都不放行。
        self.assertFalse(Rotation.bloom_consumed_tail_allowed(
            samples, 8.0, 8.0, 0.279, True, True, False, 0.34))
        self.assertFalse(Rotation.bloom_consumed_tail_allowed(
            samples, 8.0, 8.0, 0.279, True, True, False))

    def test_consumed_tail_rejects_each_gate(self):
        samples = self.consumed_tail_samples()

        def allowed(**overrides):
            params = dict(samples=samples, output_wall=8.0, output_budget=8.0, forte=0.279,
                          same_character=True, marker_visible=True, in_liberation=False,
                          baseline=0.92)
            params.update(overrides)
            return Rotation.bloom_consumed_tail_allowed(
                params['samples'], params['output_wall'], params['output_budget'], params['forte'],
                params['same_character'], params['marker_visible'], params['in_liberation'],
                params['baseline'])

        self.assertTrue(allowed())
        # 预算未耗尽 / 本帧不可读 / 标记消失 / 不在场 / 解放态。
        self.assertFalse(allowed(output_wall=7.9))
        self.assertFalse(allowed(forte=-1.0))
        self.assertFalse(allowed(marker_visible=False))
        self.assertFalse(allowed(same_character=False))
        self.assertFalse(allowed(in_liberation=True))
        # 尾部样本过期（sample_age=0.759）只判 stale_sample，不接手。
        self.assertFalse(allowed(samples=samples[:-6]))
        # 只剩两个样本时只能判 insufficient_samples。
        self.assertFalse(allowed(samples=samples[-2:]))

    def test_consumed_tail_end_to_end_marks_timing_flag(self):
        # 端到端：回路从 0.92 消耗到 0.279 后纹丝不动、开花标记仍在屏，8 秒边界必须按已消耗稳态收尾，
        # 不再以 insufficient_decreases 超时；bloom timing 行须带 consumed_tail=True。
        # 平台值取 0.20（七格）：<=0.25 让尾段判 insufficient_decreases，1.2.7 中高位出口要求
        # outside_tail、1.2.8 地板窗要求 <=0.10、1.2.6 贴零要求 <=0.05，四条都接不住，只有本出口能收。
        # 0.279 那一档不能用来做端到端：1.2.7 调用 bloom_tail_status 时不带基线，forte>0.25 一律判
        # outside_tail，只要 retries>=2 就会先收走（实测该段 heavy_retries=0 才轮到本出口）。
        def forte(**kwargs):
            at = self.clock[0] - 100
            return max(0.20, 0.92 - at * 0.35)

        self.char.get_forte.side_effect = forte
        with patch.object(self.char, 'logger') as logger:
            self.rotation.bloom_output(self.char)
        logs = [entry.args[0] for entry in logger.info.call_args_list]
        self.assertIn('outcome=completed', logs[-1])
        self.assertIn('consumed_tail=True', logs[-1])
        # steady=True 说明被 1.2.7 抢先收尾：本条出口一次没上场，等于没测到。
        self.assertIn('steady=False', logs[-1])
        self.assertTrue(any('bloom consumed tail completion' in line for line in logs))

    def test_consumed_tail_keeps_hard_limits_untouched(self):
        # 1.2.10 只加一条出口：八个硬约束常量、两个结束阈值与 1.2.6–1.2.9 门限逐字不动。
        self.assertEqual(self.rotation.BLOOM_TAIL_CONSUMED_SPAN, 1.0)
        self.assertEqual(self.rotation.BLOOM_TAIL_CONSUMED_RATIO, 0.65)
        self.assertEqual(self.rotation.BLOOM_OUTPUT_TIMEOUT, 8)
        self.assertEqual(self.rotation.BLOOM_WALL_TIMEOUT, 16)
        self.assertEqual(self.rotation.PHASE_TIMEOUT, 24)
        self.assertEqual(self.rotation.BLOOM_TAIL_GRACE, 2)
        self.assertEqual(self.rotation.BLOOM_TAIL_RECHECK, 0.4)
        self.assertEqual(self.rotation.BLOOM_FEEDBACK_TIMEOUT, 1.2)
        self.assertEqual(self.rotation.BLOOM_TARGET_WAIT_MAX, 2.0)
        self.assertEqual(self.rotation.BLOOM_TARGET_WAIT_TOTAL, 3.0)
        self.assertEqual(self.rotation.BLOOM_COMPLETE_FORTE, 0.06)
        self.assertEqual(self.rotation.BLOOM_ENDED_FORTE, 0.05)
        self.assertEqual(self.rotation.BLOOM_TAIL_FLOOR_MAX, 0.10)
        self.assertEqual(self.rotation.BLOOM_TAIL_UNREADABLE_MAX_AGE, 1.0)

    def test_not_engaged_aborts_when_bloom_ring_never_appears(self):
        # 1.2.10 两轮两次未收尾：cast confirmed 后整段 8 秒零可读样本、history=[]、重击 0，
        # 同帧识别遥测是 no_bloom_ring / legacy_red_percent=0.000000 / 开花标记不可见。
        self.assertTrue(Rotation.bloom_output_never_engaged([], 2.5, -1.0, True, False, False))

    def test_not_engaged_rejects_readable_visible_or_early_frames(self):
        # 只早退「整段零读数且标记不可见」这一种：可读帧、标记可见、解放态、不在场、样本未空、
        # 以及 2 秒之前一律不早退（避免把 lag 或首帧抖动当成未开环）。
        self.assertFalse(Rotation.bloom_output_never_engaged([], 1.9, -1.0, True, False, False))
        self.assertFalse(Rotation.bloom_output_never_engaged([], 2.5, 0.922, True, False, False))
        self.assertFalse(Rotation.bloom_output_never_engaged([], 2.5, -1.0, True, True, False))
        self.assertFalse(Rotation.bloom_output_never_engaged([], 2.5, -1.0, True, False, True))
        self.assertFalse(Rotation.bloom_output_never_engaged([], 2.5, -1.0, False, False, False))
        self.assertFalse(Rotation.bloom_output_never_engaged([(0.5, 0.92)], 2.5, -1.0, True, False, False))

    def test_not_engaged_end_to_end_stops_before_output_budget(self):
        # 端到端：整段零可读且开花标记不可见；必须早退、不得烧满 8 秒，bloom timing 行带 not_engaged=True。
        self.task.find_one = Mock(return_value=False)
        with patch.object(self.char, 'logger') as logger:
            self.rotation.bloom_output(self.char)
        logs = [entry.args[0] for entry in logger.info.call_args_list]
        self.assertIn('outcome=bloom not engaged', logs[-1])
        self.assertIn('not_engaged=True', logs[-1])
        self.assertTrue(any('bloom not engaged' in line for line in logs))
        wall = float(logs[-1].split('wall=')[1].split()[0])
        self.assertLess(wall, self.rotation.BLOOM_OUTPUT_TIMEOUT)

    def test_not_engaged_keeps_hard_limits_untouched(self):
        # 1.2.11 只加早退：八个硬约束常量、两个结束阈值与 1.2.6–1.2.10 门限逐字不动。
        self.assertEqual(self.rotation.BLOOM_NOT_ENGAGED_AFTER, 2.0)
        self.assertEqual(self.rotation.BLOOM_OUTPUT_TIMEOUT, 8)
        self.assertEqual(self.rotation.BLOOM_WALL_TIMEOUT, 16)
        self.assertEqual(self.rotation.PHASE_TIMEOUT, 24)
        self.assertEqual(self.rotation.BLOOM_TAIL_GRACE, 2)
        self.assertEqual(self.rotation.BLOOM_TAIL_RECHECK, 0.4)
        self.assertEqual(self.rotation.BLOOM_FEEDBACK_TIMEOUT, 1.2)
        self.assertEqual(self.rotation.BLOOM_TARGET_WAIT_MAX, 2.0)
        self.assertEqual(self.rotation.BLOOM_TARGET_WAIT_TOTAL, 3.0)
        self.assertEqual(self.rotation.BLOOM_COMPLETE_FORTE, 0.06)
        self.assertEqual(self.rotation.BLOOM_ENDED_FORTE, 0.05)
        self.assertEqual(self.rotation.BLOOM_TAIL_FLOOR_MAX, 0.10)
        self.assertEqual(self.rotation.BLOOM_TAIL_UNREADABLE_MAX_AGE, 1.0)
        self.assertEqual(self.rotation.BLOOM_TAIL_CONSUMED_SPAN, 1.0)

    def test_marker_end_takes_marker_gone_only_when_readings_unreadable(self):
        # 1.2.12：读数不可读时，本段见过标记之后标记消失就是结束证据；可读形态一律走原阈值。
        self.assertTrue(Rotation.bloom_marker_end_allowed(-1.0, True, False, True, False, 6.5, False))
        # 标记仍可见 / 本段从未见过标记 / 同帧可读 / 解放态 / 不在场 / 5.1 秒之前一律不认。
        self.assertFalse(Rotation.bloom_marker_end_allowed(-1.0, True, True, True, False, 6.5, False))
        self.assertFalse(Rotation.bloom_marker_end_allowed(-1.0, True, False, False, False, 6.5, False))
        self.assertFalse(Rotation.bloom_marker_end_allowed(0.156, True, False, True, False, 6.5, False))
        self.assertFalse(Rotation.bloom_marker_end_allowed(-1.0, True, False, True, True, 6.5, False))
        self.assertFalse(Rotation.bloom_marker_end_allowed(-1.0, False, False, True, False, 6.5, False))
        self.assertFalse(Rotation.bloom_marker_end_allowed(-1.0, True, False, True, False, 4.0, False))
        # resumed 段不受 5.1 秒限制，与 bloom_ended 的 resumed 分支一致。
        self.assertTrue(Rotation.bloom_marker_end_allowed(-1.0, True, False, True, False, 4.0, True))

    def test_not_engaged_ignores_sessions_that_already_saw_the_marker(self):
        # 1.2.12：见过标记后再消失属于「开花结束」，交给标记结束确认，不再当作没开环早退。
        self.assertTrue(Rotation.bloom_output_never_engaged([], 2.5, -1.0, True, False, False))
        self.assertFalse(Rotation.bloom_output_never_engaged([], 2.5, -1.0, True, False, False, True))

    def test_marker_end_completes_unreadable_bloom_after_marker_gone(self):
        # 端到端：回路整段读不出来，标记先可见后消失；必须按完成收尾（marker_end=True），
        # 不得烧满 8 秒超时，也不得走 1.2.11 的未开环早退。
        def find_one(name, threshold=None, **kwargs):
            return (self.clock[0] - 100) < 6.0

        self.task.find_one = Mock(side_effect=find_one)
        with patch.object(self.char, 'logger') as logger:
            self.rotation.bloom_output(self.char)
        logs = [entry.args[0] for entry in logger.info.call_args_list]
        self.assertIn('outcome=completed', logs[-1])
        self.assertIn('marker_end=True', logs[-1])
        self.assertIn('not_engaged=False', logs[-1])
        self.assertTrue(any('bloom marker end completion' in line for line in logs))

    def test_marker_end_keeps_hard_limits_and_thresholds_untouched(self):
        # 1.2.12 只加结束确认：两个结束阈值、八个硬约束常量与 1.2.6–1.2.11 门限逐字不动。
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
        self.assertEqual(self.rotation.BLOOM_TAIL_FLOOR_MAX, 0.10)
        self.assertEqual(self.rotation.BLOOM_TAIL_UNREADABLE_MAX_AGE, 1.0)
        self.assertEqual(self.rotation.BLOOM_TAIL_CONSUMED_SPAN, 1.0)
        self.assertEqual(self.rotation.BLOOM_NOT_ENGAGED_AFTER, 2.0)

    @staticmethod
    def tick_observations(filled_ticks=20, total=40):
        # 每个元素是 (刻度可读, 该格是否充能)；左起连续充能，与真实回路环一致。
        return [(True, index < filled_ticks) for index in range(total)]

    def test_ring_estimate_recovers_readings_when_one_tick_blinks(self):
        # 1.2.13 实测形态：visible_ticks=39/40 已过高可见度门限，却因充能段中间一格读不到
        # 就整帧判 unknown，8 秒窗口攒不到样本，最终兜成 output timeout。
        exact = Rotation.budding_forte_from_observations(self.tick_observations())
        self.assertEqual(exact['state'], 'filled')
        self.assertFalse(exact['estimated'])
        self.assertEqual(exact['segments'], 20)
        self.assertEqual(exact['visible_ticks'], 40)
        blinked = self.tick_observations()
        blinked[5] = (False, False)
        recovered = Rotation.budding_forte_from_observations(blinked)
        self.assertEqual(recovered['state'], 'filled_estimated')
        self.assertTrue(recovered['estimated'])
        self.assertEqual(recovered['visible_ticks'], 39)
        # 缺失格按「仍充能」计入，得到的是上界读数：只会让「已打完」更难成立。
        self.assertGreaterEqual(recovered['value'], exact['value'])
        # 缺失发生在充能段之后（首个未充能格右侧）时与剩余量无关，不标记为估算。
        late = self.tick_observations()
        late[25] = (False, False)
        tail_missing = Rotation.budding_forte_from_observations(late)
        self.assertEqual(tail_missing['state'], 'filled')
        self.assertFalse(tail_missing['estimated'])
        # 全部可见但一格都没充能：仍是明确空回路，不是不可读。
        empty = Rotation.budding_forte_from_observations(self.tick_observations(filled_ticks=0))
        self.assertEqual(empty['value'], 0.0)
        self.assertEqual(empty['state'], 'empty')

    def test_ring_estimate_stays_unknown_without_enough_ticks(self):
        # 刻度根本不可见时不估算，与 1.2.13 之前的整帧 unknown 行为一致。
        blank = [(False, False)] * 40
        self.assertEqual(Rotation.budding_forte_from_observations(blank)['value'], -1.0)
        # 可见度 31 低于 BLOOM_FORTE_FALLBACK_MIN_VISIBLE=34：不足以估算。
        sparse = self.tick_observations()
        sparse[3] = (False, False)
        for index in range(32, 40):
            sparse[index] = (False, False)
        self.assertEqual(Rotation.budding_forte_from_observations(sparse)['value'], -1.0)
        # 可见度 35 够高，但充能段连续缺 5 格超过 BLOOM_FORTE_MISSING_TOLERANCE=2。
        crowded = self.tick_observations()
        for index in range(3, 8):
            crowded[index] = (False, False)
        self.assertEqual(Rotation.budding_forte_from_observations(crowded)['value'], -1.0)

    def test_ring_estimate_keeps_hard_limits_and_thresholds_untouched(self):
        # 1.2.13 只动识别容差：两个结束阈值、八个硬约束常量与 1.2.6–1.2.12 门限逐字不动。
        self.assertEqual(self.rotation.BLOOM_FORTE_MIN_VISIBLE, 28)
        self.assertEqual(self.rotation.BLOOM_FORTE_FALLBACK_MIN_VISIBLE, 34)
        self.assertEqual(self.rotation.BLOOM_FORTE_MISSING_TOLERANCE, 2)
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
        self.assertEqual(self.rotation.BLOOM_TAIL_FLOOR_MAX, 0.10)
        self.assertEqual(self.rotation.BLOOM_TAIL_UNREADABLE_MAX_AGE, 1.0)
        self.assertEqual(self.rotation.BLOOM_TAIL_CONSUMED_SPAN, 1.0)
        self.assertEqual(self.rotation.BLOOM_NOT_ENGAGED_AFTER, 2.0)

    def test_estimate_streak_drops_upper_bound_after_limit(self):
        # 1.2.14：整段每次采样都只拿到估算上界（实测 0.996 / 0.799 长期不动）时，超过上限就把
        # 本帧按读不到处理，交回不可读门与原超时，不让上界长期替代精确读数。
        self.assertEqual(self.rotation.BLOOM_FORTE_ESTIMATE_MAX_SECONDS, 0.5)
        self.char.get_forte_reading = Mock(return_value={
            'value': 0.996, 'state': 'filled_estimated', 'estimated': True,
            'segments': 39, 'visible_ticks': 39})
        with patch.object(self.char, 'logger') as logger:
            with self.assertRaises(RotationTimeout):
                self.rotation.bloom_output(self.char)
        logs = [entry.args[0] for entry in logger.info.call_args_list]
        self.assertIn('ring_fallback=True', logs[-1])
        self.assertIn('ring_estimate_streak=', logs[-1])
        self.assertIn('estimate_fallback=', logs[-1])
        self.assertIn('estimate_hold=', logs[-1])
        self.assertIn('outcome=bloom state did not end: output timeout', logs[-1])
        self.assertIn('consumed_tail=False', logs[-1])
        # 1.2.15：确证字段是「秒级窗口下实际降级的帧数」，必须 >0，否则说明收敛没生效。
        fallback = int(logs[-1].split('estimate_fallback=')[1].split()[0])
        self.assertGreater(fallback, 0)
        streak = int(logs[-1].split('ring_estimate_streak=')[1].split()[0])
        self.assertGreater(streak, 0)

    def test_estimate_streak_resets_on_exact_reading(self):
        # 精确读数即归零：可见刻度齐全时估算不得累积，ring_estimate_streak 保持 0。
        self.char.get_forte_reading = Mock(return_value={
            'value': 0.95, 'state': 'filled', 'estimated': False,
            'segments': 39, 'visible_ticks': 40})
        # 读数恒为 0.95 精确值：整段既不消耗也不收尾，8 秒预算耗尽必然抛超时；
        # 本用例只验估算计数不累积，异常路径的 timing 行同样由 finally 落盘。
        with patch.object(self.char, 'logger') as logger:
            with self.assertRaises(RotationTimeout):
                self.rotation.bloom_output(self.char)
        logs = [entry.args[0] for entry in logger.info.call_args_list]
        self.assertIn('ring_estimate_streak=0', logs[-1])
        self.assertIn('ring_fallback=False', logs[-1])
        self.assertEqual(self.rotation.BLOOM_FORTE_ESTIMATE_MAX_SECONDS, 0.5)
        self.assertEqual(self.rotation.BLOOM_FORTE_MIN_VISIBLE, 28)
        self.assertEqual(self.rotation.BLOOM_FORTE_FALLBACK_MIN_VISIBLE, 34)
        self.assertEqual(self.rotation.BLOOM_FORTE_MISSING_TOLERANCE, 2)
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
        self.assertEqual(self.rotation.BLOOM_TAIL_FLOOR_MAX, 0.10)
        self.assertEqual(self.rotation.BLOOM_TAIL_UNREADABLE_MAX_AGE, 1.0)
        self.assertEqual(self.rotation.BLOOM_TAIL_CONSUMED_SPAN, 1.0)
        self.assertEqual(self.rotation.BLOOM_NOT_ENGAGED_AFTER, 2.0)


    def test_estimate_convergence_window_is_reachable_at_observed_frame_rate(self):
        # 1.2.16：run 0588d04d 的 8 段开花里估算连续帧数最高 23 帧、estimate_fallback 全为 0，说明
        # 23 帧累计不足 1.2 秒（帧间隔 < 0.052 秒，约 19 帧/秒以上），1.2.15 的窗口在现场不可达。
        # 窗口降到 0.5 秒，等于恢复 1.2.14 已验证会触发的时机，同时保留按秒判定、不随帧率漂移。
        self.assertEqual(self.rotation.BLOOM_FORTE_ESTIMATE_MAX_SECONDS, 0.5)
        self.assertFalse(self.rotation.bloom_estimate_converged(None, 10.0))
        self.assertFalse(self.rotation.bloom_estimate_converged(10.0, 10.3))
        self.assertTrue(self.rotation.bloom_estimate_converged(10.0, 10.5))
        self.assertTrue(self.rotation.bloom_estimate_converged(10.0, 12.0))

    def test_estimate_convergence_keeps_dropping_upper_bound_alive(self):
        # 1.2.17：1.2.16 run1 的 estimate_fallback=62 段里，估算读数一路 0.996 -> 0.502 -> 0.106，
        # 上界仍跟着消耗在走；这种帧不许降级，否则真实进度会被抹成 samples=0。
        self.assertEqual(self.rotation.BLOOM_FORTE_ESTIMATE_DROP, 0.015)
        self.assertFalse(self.rotation.bloom_estimate_progressing(None, 0.996))
        self.assertFalse(self.rotation.bloom_estimate_progressing(0.996, 0.99))
        self.assertTrue(self.rotation.bloom_estimate_progressing(0.996, 0.502))
        self.assertTrue(self.rotation.bloom_estimate_progressing(0.502, 0.106))
        self.assertFalse(self.rotation.bloom_estimate_progressing(0.106, 0.106))

    def test_floor_stall_takes_flat_floor_reading_still_reported_as_progressing(self):
        # 1.2.18：1.2.17 run2 实测 forte=0.082、steady_range=0.0、steady_hold=0.485、consumed_ratio=0.918、
        # drop_age=0.657，尾段 reason 因此落在 progressing；1.2.8 地板出口要求 reason 为
        # stale_decrease/insufficient_decreases 且 steady_hold >= 1.0，这条被双重挡住后只能烧满宽限。
        samples = [(9.0, 0.996), (9.25, 0.109), (9.3, 0.082)]
        samples += [(9.3 + index * 0.05, 0.082) for index in range(1, 10)]
        status = Rotation.bloom_tail_status(samples, 9.75, 0.996)
        self.assertEqual(status['reason'], 'progressing')
        self.assertAlmostEqual(status['consumed_ratio'], 0.918, places=3)
        self.assertEqual(status['steady_range'], 0.0)
        self.assertGreaterEqual(status['steady_hold'], 0.4)
        self.assertTrue(Rotation.bloom_floor_stall_allowed(
            samples, 9.75, 8.0, 0.082, 2, True, True, False, 0.996))

    def test_floor_stall_rejects_moving_or_non_floor_readings(self):
        # 反例护栏：补按未用尽、读数不在地板窗、尾部仍在变动、消耗不足时一律交回原判据。
        samples = [(9.0, 0.996), (9.25, 0.109), (9.3, 0.082)]
        samples += [(9.3 + index * 0.05, 0.082) for index in range(1, 10)]
        self.assertFalse(Rotation.bloom_floor_stall_allowed(
            samples, 9.75, 8.0, 0.082, 1, True, True, False, 0.996))
        self.assertFalse(Rotation.bloom_floor_stall_allowed(
            samples, 9.75, 8.0, 0.12, 2, True, True, False, 0.996))
        # 消耗不足：峰值 0.12 只消耗 31.7%，不满足 >=65%。
        self.assertFalse(Rotation.bloom_floor_stall_allowed(
            samples, 9.75, 8.0, 0.082, 2, True, True, False, 0.12))
        # 尾部仍在变动：平窗只有 0.05 秒。
        moving = [(9.0, 0.996), (9.6, 0.109), (9.65, 0.082), (9.7, 0.082)]
        self.assertFalse(Rotation.bloom_floor_stall_allowed(
            moving, 9.7, 8.0, 0.082, 2, True, True, False, 0.996))
        # 预算未耗尽前不放行。
        self.assertFalse(Rotation.bloom_floor_stall_allowed(
            samples, 7.5, 8.0, 0.082, 2, True, True, False, 0.996))

    def test_floor_stall_keeps_hard_limits_and_thresholds_untouched(self):
        # 1.2.18 只加一条并列出口：八个硬约束常量、两个结束阈值与既有门限逐字不动。
        self.assertEqual(self.rotation.BLOOM_TAIL_FLOOR_STALL_SPAN, 0.4)
        self.assertEqual(self.rotation.BLOOM_TAIL_FLOOR_MAX, 0.10)
        self.assertEqual(self.rotation.BLOOM_TAIL_FLOOR_SPAN, 1.0)
        self.assertEqual(self.rotation.BLOOM_TAIL_FLOOR_RETRIES, 2)
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

    def test_untargeted_aborts_zero_read_sample_segment_without_locking(self):
        # 1.2.19：1.2.18 run2 的 streak=84 段整段零可读、target_state=unlocked、heavy_retries=0，
        # 8 秒里根本没有目标可打；这种段只早退交回轮转，不判完成。
        self.assertEqual(self.rotation.BLOOM_OUTPUT_UNTARGETED_SPAN, 2.0)
        self.assertTrue(Rotation.bloom_output_untargeted(4.0, 1.5, True, True, False, []))
        self.assertTrue(Rotation.bloom_output_untargeted(2.0, 0.0, True, True, False, []))
        # 出现过可读样本、标记不可见、丢锁跨度不足、角色不在场或解放态一律不放行。
        self.assertFalse(Rotation.bloom_output_untargeted(4.0, 1.5, True, True, False, [(3.9, 0.5)]))
        self.assertFalse(Rotation.bloom_output_untargeted(4.0, 1.5, False, True, False, []))
        self.assertFalse(Rotation.bloom_output_untargeted(4.0, 3.0, True, True, False, []))
        self.assertFalse(Rotation.bloom_output_untargeted(4.0, None, True, True, False, []))
        self.assertFalse(Rotation.bloom_output_untargeted(1.0, 0.0, True, True, False, []))
        self.assertFalse(Rotation.bloom_output_untargeted(4.0, 1.5, True, False, False, []))
        self.assertFalse(Rotation.bloom_output_untargeted(4.0, 1.5, True, True, True, []))

    def test_untargeted_keeps_hard_limits_and_thresholds_untouched(self):
        # 1.2.19 只加一条早退：八个硬约束常量、两个结束阈值与既有门限逐字不动。
        self.assertEqual(self.rotation.BLOOM_NOT_ENGAGED_AFTER, 2.0)
        self.assertEqual(self.rotation.BLOOM_TAIL_FLOOR_STALL_SPAN, 0.4)
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

    def test_consumed_tail_stall_takes_flat_reading_after_one_tick_rise(self):
        # 1.2.20：1.2.19 run1 实测 forte=0.279、steady_range=0.0、steady_hold=0.61、steady_flat=False、
        # drop_total=0.025、consumed_ratio=0.72、history_reset=forte_rise（7.375 时 0.279 -> 0.304）；
        # 1.2.10 要求 steady_hold>=1.0 与 steady_flat，边界前平窗攒不满，只能烧满预算。
        samples = [(7.4 + index * 0.05, 0.279) for index in range(13)]
        status = Rotation.bloom_tail_status(samples, 8.0, 0.996)
        self.assertEqual(status['reason'], 'insufficient_decreases')
        self.assertEqual(status['steady_range'], 0.0)
        self.assertFalse(status['steady_flat'])
        self.assertGreaterEqual(status['steady_hold'], 0.4)
        self.assertAlmostEqual(status['consumed_ratio'], 0.72, places=2)
        self.assertTrue(Rotation.bloom_consumed_tail_stall_allowed(
            samples, 8.0, 8.0, 0.279, 2, True, True, False, 0.996))

    def test_consumed_tail_stall_rejects_retry_short_or_unconsumed_forms(self):
        # 反例护栏：补按未用尽、消耗不足、预算未耗尽、标记不可见、尾部平窗不足时一律交回原判据。
        samples = [(7.4 + index * 0.05, 0.279) for index in range(13)]
        self.assertFalse(Rotation.bloom_consumed_tail_stall_allowed(
            samples, 8.0, 8.0, 0.279, 1, True, True, False, 0.996))
        self.assertFalse(Rotation.bloom_consumed_tail_stall_allowed(
            samples, 8.0, 8.0, 0.279, 2, True, True, False, 0.35))
        self.assertFalse(Rotation.bloom_consumed_tail_stall_allowed(
            samples, 7.5, 8.0, 0.279, 2, True, True, False, 0.996))
        self.assertFalse(Rotation.bloom_consumed_tail_stall_allowed(
            samples, 8.0, 8.0, 0.279, 2, True, False, False, 0.996))
        # 尾部平窗只有 0.1 秒，不足以证明「打不动」。
        short = [(7.9, 0.279), (7.95, 0.279), (8.0, 0.279)]
        self.assertFalse(Rotation.bloom_consumed_tail_stall_allowed(
            short, 8.0, 8.0, 0.279, 2, True, True, False, 0.996))

    def test_consumed_tail_stall_keeps_hard_limits_and_thresholds_untouched(self):
        # 1.2.20 只加一条并列出口：八个硬约束常量、两个结束阈值与既有门限逐字不动。
        self.assertEqual(self.rotation.BLOOM_TAIL_CONSUMED_STALL_SPAN, 0.4)
        self.assertEqual(self.rotation.BLOOM_TAIL_CONSUMED_SPAN, 1.0)
        self.assertEqual(self.rotation.BLOOM_TAIL_DROP_TOTAL, 0.03)
        self.assertEqual(self.rotation.BLOOM_TAIL_CONSUMED_RATIO, 0.65)
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

    def test_target_relock_takes_continuous_unlock_after_three_frames(self):
        # 1.2.21：1.2.20 批 run1 的 untargeted 段 3.5 秒里 check_target Release/Retry=80/85，
        # 而 target lost try retarget 一次都没打印（wait_count=0）：锁定在输出段就丢了，
        # 却始终没有补锁动作，读数只能整段不可读。
        self.assertEqual(self.rotation.BLOOM_TARGET_RELOCK_FRAMES, 3)
        self.assertEqual(self.rotation.BLOOM_TARGET_RELOCK_TIMEOUT, 0.5)
        self.assertEqual(self.rotation.BLOOM_TARGET_RELOCK_MAX, 2)
        self.assertTrue(Rotation.bloom_target_relock_allowed(3, 0, True, True, False))
        # 丢锁帧数不足、本段重锁已满两次、标记消失、角色不在场或解放态一律不补锁。
        self.assertFalse(Rotation.bloom_target_relock_allowed(2, 0, True, True, False))
        self.assertFalse(Rotation.bloom_target_relock_allowed(9, 2, True, True, False))
        self.assertFalse(Rotation.bloom_target_relock_allowed(9, 0, True, False, False))
        self.assertFalse(Rotation.bloom_target_relock_allowed(9, 0, False, True, False))
        self.assertFalse(Rotation.bloom_target_relock_allowed(9, 0, True, True, True))

    def test_target_relock_attempts_lock_before_untargeted_exit(self):
        # 端到端：整段读数不可读、has_target 恒为假、开花标记仍在屏；必须先补锁（上限两次），
        # 之后才轮到 1.2.19 的丢锁早退，bloom timing 行须带 relock=2。
        self.char.get_forte = Mock(return_value=-1.0)
        self.task.has_target = Mock(return_value=False)
        with patch.object(self.char, 'logger') as logger:
            self.rotation.bloom_output(self.char)
        logs = [entry.args[0] for entry in logger.info.call_args_list]
        self.assertTrue(any('bloom target relock count=1' in line for line in logs))
        self.assertTrue(any('bloom target relock result count=2' in line for line in logs))
        self.assertIn('relock=2', logs[-1])
        self.assertIn('outcome=bloom output untargeted', logs[-1])

    def test_target_relock_keeps_hard_limits_and_thresholds_untouched(self):
        # 1.2.21 只补重锁动作：八个硬约束常量、两个结束阈值与 1.2.6–1.2.20 门限逐字不动。
        self.assertEqual(self.rotation.BLOOM_TARGET_WAIT_MAX, 2.0)
        self.assertEqual(self.rotation.BLOOM_TARGET_WAIT_TOTAL, 3.0)
        self.assertEqual(self.rotation.BLOOM_OUTPUT_UNTARGETED_SPAN, 2.0)
        self.assertEqual(self.rotation.BLOOM_TAIL_CONSUMED_STALL_SPAN, 0.4)
        self.assertEqual(self.rotation.BLOOM_COMPLETE_FORTE, 0.06)
        self.assertEqual(self.rotation.BLOOM_ENDED_FORTE, 0.05)
        self.assertEqual(self.rotation.BLOOM_OUTPUT_TIMEOUT, 8)
        self.assertEqual(self.rotation.BLOOM_WALL_TIMEOUT, 16)
        self.assertEqual(self.rotation.PHASE_TIMEOUT, 24)
        self.assertEqual(self.rotation.BLOOM_TAIL_GRACE, 2)
        self.assertEqual(self.rotation.BLOOM_TAIL_RECHECK, 0.4)
        self.assertEqual(self.rotation.BLOOM_FEEDBACK_TIMEOUT, 1.2)

    def test_relock_counts_unlocked_frames_on_readable_readings(self):
        # 1.2.22：1.2.21 批 run2 的 target lost 段实测 relock=0、estimate_drops=6、wait_count=1，
        # 读数多数可读；旧逻辑先按「可读即在场」清零计数，重锁门永远攒不到 3 帧。
        # 读数可读说明开花真没打完：补锁之后本段仍应烧满预算判 output timeout，
        # 本条只验证「可读帧同样计入重锁」与 1.2.19 早退口径不变，不验证收尾。
        self.char.get_forte = Mock(return_value=0.90)
        self.task.has_target = Mock(return_value=False)
        with patch.object(self.char, 'logger') as logger:
            with self.assertRaises(RotationTimeout):
                self.rotation.bloom_output(self.char)
        logs = [entry.args[0] for entry in logger.info.call_args_list]
        self.assertTrue(any('bloom target relock count=1' in line for line in logs))
        self.assertTrue(any('readable=True' in line for line in logs))
        self.assertIn('relock=2', logs[-1])
        self.assertIn('relock_readable=2', logs[-1])
        # 读数可读的丢锁帧不得触发 1.2.19 的零样本早退：该门限口径完全不变。
        self.assertIn('untargeted=False', logs[-1])

    def test_relock_readable_keeps_untargeted_gate_and_limits_untouched(self):
        # 1.2.22 只放宽重锁计数：1.2.19 早退门限、八个硬约束常量与两个结束阈值逐字不动。
        self.assertEqual(self.rotation.BLOOM_TARGET_RELOCK_FRAMES, 3)
        self.assertEqual(self.rotation.BLOOM_TARGET_RELOCK_TIMEOUT, 0.5)
        self.assertEqual(self.rotation.BLOOM_TARGET_RELOCK_MAX, 2)
        self.assertEqual(self.rotation.BLOOM_OUTPUT_UNTARGETED_SPAN, 2.0)
        self.assertEqual(self.rotation.BLOOM_OUTPUT_TIMEOUT, 8)
        self.assertEqual(self.rotation.BLOOM_WALL_TIMEOUT, 16)
        self.assertEqual(self.rotation.PHASE_TIMEOUT, 24)
        self.assertEqual(self.rotation.BLOOM_TAIL_GRACE, 2)
        self.assertEqual(self.rotation.BLOOM_TAIL_RECHECK, 0.4)
        self.assertEqual(self.rotation.BLOOM_FEEDBACK_TIMEOUT, 1.2)
        self.assertEqual(self.rotation.BLOOM_TARGET_WAIT_MAX, 2.0)
        self.assertEqual(self.rotation.BLOOM_TARGET_WAIT_TOTAL, 3.0)
        self.assertEqual(self.rotation.BLOOM_COMPLETE_FORTE, 0.06)
        self.assertEqual(self.rotation.BLOOM_ENDED_FORTE, 0.05)

    def test_budget_guard_field_present_and_zero_without_grace(self):
        # 1.2.23：1.2.22 批 8.468s 那朵 tail_extra=+0.171、1.2.17 批更出现 +2.078；硬预算原先只在帧边界
        # 复核，阻塞调用可以把 8+2 秒边界推过一个调用周期。这里验证预算未耗尽时 budget_guard=0。
        def forte(**kwargs):
            at = self.clock[0] - 100
            return max(0.20, 0.92 - at * 0.35)

        self.char.get_forte.side_effect = forte
        with patch.object(self.char, 'logger') as logger:
            self.rotation.bloom_output(self.char)
        logs = [entry.args[0] for entry in logger.info.call_args_list]
        self.assertIn('outcome=completed', logs[-1])
        self.assertIn('budget_guard=0', logs[-1])
        self.assertIn('tail_grace=False', logs[-1])

    def test_budget_guard_rechecks_inside_grace_window(self):
        # 宽限窗内每帧都要复核一次硬预算：一旦 tail_grace=True，guard 至少命中一次，
        # 这样越界时不会等到下一次帧边界才发现。
        def forte(**kwargs):
            at = self.clock[0] - 100
            return max(0.10, 0.92 - at * 0.08)

        self.char.get_forte.side_effect = forte
        with patch.object(self.char, 'logger') as logger:
            with self.assertRaises(RotationTimeout):
                self.rotation.bloom_output(self.char)
        logs = [entry.args[0] for entry in logger.info.call_args_list]
        self.assertIn('budget_guard=', logs[-1])
        if 'tail_grace=True' in logs[-1]:
            self.assertNotIn('budget_guard=0', logs[-1])

    def test_budget_guard_keeps_hard_limits_and_thresholds_untouched(self):
        # 1.2.23 只补复核点：八个硬约束常量、两个结束阈值与 1.2.6–1.2.22 门限逐字不动。
        self.assertEqual(self.rotation.BLOOM_OUTPUT_TIMEOUT, 8)
        self.assertEqual(self.rotation.BLOOM_WALL_TIMEOUT, 16)
        self.assertEqual(self.rotation.PHASE_TIMEOUT, 24)
        self.assertEqual(self.rotation.BLOOM_TAIL_GRACE, 2)
        self.assertEqual(self.rotation.BLOOM_TAIL_RECHECK, 0.4)
        self.assertEqual(self.rotation.BLOOM_FEEDBACK_TIMEOUT, 1.2)
        self.assertEqual(self.rotation.BLOOM_TARGET_WAIT_MAX, 2.0)
        self.assertEqual(self.rotation.BLOOM_TARGET_WAIT_TOTAL, 3.0)
        self.assertEqual(self.rotation.BLOOM_COMPLETE_FORTE, 0.06)
        self.assertEqual(self.rotation.BLOOM_ENDED_FORTE, 0.05)
        self.assertEqual(self.rotation.BLOOM_TARGET_RELOCK_FRAMES, 3)
        self.assertEqual(self.rotation.BLOOM_OUTPUT_UNTARGETED_SPAN, 2.0)

    @staticmethod
    def boundary_samples():
        # 1.2.23 批实测形态：预算边界后读数由 0.082 掉到 0.032，本段峰值 0.996。
        return [(7.0 + step * 0.09, 0.996 - step * 0.08) for step in range(13)]

    def test_boundary_confirm_takes_single_frame_after_budget(self):
        # 1.2.24：越界后允许「单帧 + 已消耗 >=65% 旁证」收尾，把两帧确认的成本从 tail_extra 去掉。
        self.assertEqual(self.rotation.BLOOM_BOUNDARY_CONFIRM_RATIO, 0.65)
        samples = self.boundary_samples()
        self.assertTrue(Rotation.bloom_boundary_confirm_allowed(
            0.036, True, False, True, False, 8.0, False, True, 8.08, 8.0, samples, 0.996))
        # 宽限尚未开始时一律交回原 check_budget，避免抢走 1.2.5 低首帧形态的宽限。
        self.assertFalse(Rotation.bloom_boundary_confirm_allowed(
            0.036, True, False, True, False, 8.0, False, False, 8.08, 8.0, samples, 0.996))

    def test_boundary_confirm_rejects_inside_budget_or_weak_evidence(self):
        # 反例护栏：预算内、读数未到结束阈值、标记仍在屏、未见标记、解放态、不在场、
        # 5.1 秒前、以及消耗不足（峰值过低）一律交回原判据，不允许单帧放行。
        samples = self.boundary_samples()

        def allowed(**overrides):
            params = dict(forte=0.036, same_character=True, marker_visible=False, marker_seen=True,
                          in_liberation=False, elapsed=8.0, resumed=False, tail_grace=True,
                          output_wall=8.08, output_budget=8.0, samples=samples, baseline=0.996)
            params.update(overrides)
            return Rotation.bloom_boundary_confirm_allowed(
                params['forte'], params['same_character'], params['marker_visible'],
                params['marker_seen'], params['in_liberation'], params['elapsed'],
                params['resumed'], params['tail_grace'], params['output_wall'],
                params['output_budget'], params['samples'], params['baseline'])

        self.assertTrue(allowed())
        self.assertFalse(allowed(output_wall=7.9))
        self.assertFalse(allowed(tail_grace=False))
        self.assertFalse(allowed(forte=0.30))
        self.assertFalse(allowed(forte=-1.0))
        # 1.2.25 起「标记可见」不再一律拒绝，改由 BLOOM_BOUNDARY_MARKER_RATIO 把关：
        # 消耗不足的标记可见形态仍必须交回原超时（此处 consumed≈0.82 < 0.90）。
        self.assertFalse(allowed(marker_visible=True, baseline=0.20))
        self.assertFalse(allowed(marker_seen=False))
        self.assertFalse(allowed(in_liberation=True))
        self.assertFalse(allowed(same_character=False))
        self.assertFalse(allowed(elapsed=4.0))
        self.assertFalse(allowed(baseline=0.04))

    def test_boundary_confirm_keeps_hard_limits_and_thresholds_untouched(self):
        # 1.2.24 只加越界后一条确认通道：两个结束阈值、八个硬约束常量与既有门限逐字不动。
        self.assertEqual(self.rotation.BLOOM_BOUNDARY_CONFIRM_RATIO, 0.65)
        self.assertEqual(self.rotation.BLOOM_TAIL_CONSUMED_RATIO, 0.65)
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
        self.assertEqual(self.rotation.BLOOM_TARGET_RELOCK_FRAMES, 3)

    def test_boundary_marker_confirm_takes_visible_marker_after_full_consumption(self):
        # 1.2.25：1.2.24 批 run2 实测 8.032 起宽限、读数 0.032 → 0.057（均 <=0.06）、
        # consumed_ratio=0.943，却因开花标记仍在屏被 bloom_ended 与 1.2.24 通道同时挡住，
        # 一路烧到 tail progress lost（tail_extra=+1.360）。
        self.assertEqual(self.rotation.BLOOM_BOUNDARY_MARKER_RATIO, 0.90)
        samples = self.boundary_samples()
        self.assertTrue(Rotation.bloom_boundary_confirm_allowed(
            0.036, True, True, True, False, 8.0, False, True, 8.08, 8.0, samples, 0.996))

    def test_boundary_marker_confirm_rejects_unfinished_or_unreadable_forms(self):
        # 反例护栏：消耗不足（峰值过低 / 真未打完）、读数超结束阈值、标记形态外的门一律不放行。
        samples = self.boundary_samples()

        def allowed(**overrides):
            params = dict(forte=0.036, same_character=True, marker_visible=True, marker_seen=True,
                          in_liberation=False, elapsed=8.0, resumed=False, tail_grace=True,
                          output_wall=8.08, output_budget=8.0, samples=samples, baseline=0.996)
            params.update(overrides)
            return Rotation.bloom_boundary_confirm_allowed(
                params['forte'], params['same_character'], params['marker_visible'],
                params['marker_seen'], params['in_liberation'], params['elapsed'],
                params['resumed'], params['tail_grace'], params['output_wall'],
                params['output_budget'], params['samples'], params['baseline'])

        self.assertTrue(allowed())
        # 1.2.24 批 outside_tail 段形态：0.873 远高于结束阈值，必须继续超时。
        self.assertFalse(allowed(forte=0.873, baseline=0.996))
        self.assertFalse(allowed(baseline=0.20))
        self.assertFalse(allowed(forte=-1.0))
        self.assertFalse(allowed(tail_grace=False))
        self.assertFalse(allowed(output_wall=7.9))
        self.assertFalse(allowed(marker_seen=False))
        self.assertFalse(allowed(same_character=False))
        self.assertFalse(allowed(in_liberation=True))
        self.assertFalse(allowed(elapsed=4.0))

    def test_boundary_marker_confirm_keeps_hard_limits_and_thresholds_untouched(self):
        # 1.2.25 只放宽标记可见形态：两个门限、八个硬约束常量与 1.2.19–1.2.24 判据逐字不动。
        self.assertEqual(self.rotation.BLOOM_BOUNDARY_MARKER_RATIO, 0.90)
        self.assertEqual(self.rotation.BLOOM_BOUNDARY_CONFIRM_RATIO, 0.65)
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
        self.assertEqual(self.rotation.BLOOM_TARGET_RELOCK_FRAMES, 3)
        self.assertEqual(self.rotation.BLOOM_OUTPUT_UNTARGETED_SPAN, 2.0)

    def test_boundary_trend_takes_still_decreasing_reading_after_budget(self):
        # 1.2.26：1.2.25 批 +0.406 那朵实测边界时 forte=0.156、decreases=13、drop_total=0.247、
        # consumed_ratio=0.943 —— 读数仍在单调下降，只是还没到 BLOOM_COMPLETE_FORTE。
        self.assertEqual(self.rotation.BLOOM_BOUNDARY_TREND_FORTE, 0.20)
        self.assertEqual(self.rotation.BLOOM_BOUNDARY_TREND_RATIO, 0.90)
        self.assertEqual(self.rotation.BLOOM_BOUNDARY_TREND_DECREASES, 3)
        self.assertEqual(self.rotation.BLOOM_BOUNDARY_TREND_DROP, 0.15)
        self.assertEqual(self.rotation.BLOOM_BOUNDARY_TREND_MAX_AGE, 0.25)
        samples = self.boundary_samples()
        self.assertTrue(Rotation.bloom_boundary_trend_allowed(
            0.156, True, True, False, 8.0, False, True, 8.08, 8.0, samples, 0.996))

    def test_boundary_trend_rejects_unfinished_flat_or_stale_forms(self):
        # 反例护栏：预算内、宽限未开始、读数仍高（真未打完）、平窗无下降、样本过期、
        # 消耗不足、标记未见、解放态、不在场、5.1 秒前一律交回原超时。
        samples = self.boundary_samples()

        def allowed(**overrides):
            params = dict(forte=0.156, same_character=True, marker_seen=True,
                          in_liberation=False, elapsed=8.0, resumed=False, tail_grace=True,
                          output_wall=8.08, output_budget=8.0, samples=samples, baseline=0.996)
            params.update(overrides)
            return Rotation.bloom_boundary_trend_allowed(
                params['forte'], params['same_character'], params['marker_seen'],
                params['in_liberation'], params['elapsed'], params['resumed'],
                params['tail_grace'], params['output_wall'], params['output_budget'],
                params['samples'], params['baseline'])

        self.assertTrue(allowed())
        self.assertFalse(allowed(output_wall=7.9))
        self.assertFalse(allowed(tail_grace=False))
        # 1.2.24 批 outside_tail 段：0.873 远高于趋势门，必须继续超时。
        self.assertFalse(allowed(forte=0.873))
        self.assertFalse(allowed(forte=-1.0))
        # 1.2.10 那种纹丝不动的平窗不算下降证据。
        flat = [(7.5 + step * 0.05, 0.20) for step in range(12)]
        self.assertFalse(allowed(samples=flat, forte=0.20))
        # 样本过期（1.2.25 批边界断流形态 sample_age=0.89）。
        self.assertFalse(allowed(samples=samples[:-6]))
        self.assertFalse(allowed(baseline=0.20))
        self.assertFalse(allowed(marker_seen=False))
        self.assertFalse(allowed(same_character=False))
        self.assertFalse(allowed(in_liberation=True))
        self.assertFalse(allowed(elapsed=4.0))

    def test_boundary_trend_keeps_hard_limits_and_thresholds_untouched(self):
        # 1.2.26 只加一条趋势门：两个结束阈值、八个硬约束常量与 1.2.19–1.2.25 门限逐字不动。
        self.assertEqual(self.rotation.BLOOM_BOUNDARY_TREND_FORTE, 0.20)
        self.assertEqual(self.rotation.BLOOM_BOUNDARY_MARKER_RATIO, 0.90)
        self.assertEqual(self.rotation.BLOOM_BOUNDARY_CONFIRM_RATIO, 0.65)
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
        self.assertEqual(self.rotation.BLOOM_TARGET_RELOCK_FRAMES, 3)
        self.assertEqual(self.rotation.BLOOM_OUTPUT_UNTARGETED_SPAN, 2.0)


if __name__ == '__main__':
    unittest.main()
