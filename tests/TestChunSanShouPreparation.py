import ast
import importlib.util
import tempfile
import time
import unittest
import zipfile
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'custom_teams/ChunSanShou_Video'


def load_rotation(path):
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if isinstance(node, ast.ClassDef)
             and node.name in {'Phase', 'RotationTimeout', 'ConcertoTimeout', 'CamellyaTeamVision',
                               'BloomTrace', 'Rotation'}]
    nodes += [node for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == 'PHASE_ACTOR' for target in node.targets)]
    namespace = {'time': time, 'StrEnum': StrEnum}
    # 只隔离 GUI 依赖，决策与循环使用导入包中未经替换的实际 Rotation 方法。
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


ENGINE = load_rotation(SOURCE / 'Camellya.py')
Rotation = ENGINE['Rotation']
RotationTimeout = ENGINE['RotationTimeout']
Phase = ENGINE['Phase']


class TestChunSanShouPreparation(unittest.TestCase):
    def setUp(self):
        self.clock = [100.0]
        self.addCleanup(patch.stopall)
        patch('time.monotonic', side_effect=lambda: self.clock[0]).start()
        self.char = SimpleNamespace(name='Camellya', index=0, logger=Mock(),
                                    wait_down=Mock(), check_combat=Mock(),
                                    liberation_available=Mock(return_value=False), has_cd=Mock(return_value=True),
                                    click_liberation=Mock(return_value=True), ephemeral_ready=Mock(return_value=True),
                                    get_current_con=Mock(return_value=1), is_con_full=Mock(return_value=True),
                                    get_forte=Mock(return_value=0.38), heavy_attack=Mock(),
                                    click=Mock(), sleep=Mock(side_effect=self.advance))
        self.task = SimpleNamespace(chars=[self.char], use_liberation=True, in_liberation=False,
                                    in_team=Mock(return_value=(True, 0, 3)),
                                    get_cd=Mock(return_value=0), mouse_up=Mock(), middle_click=Mock(),
                                    next_frame=Mock(), combat_end_condition=None)
        self.rotation = Rotation(self.task)
        self.rotation.phase = Phase.CAMELLYA_BURST
        self.rotation.wait_camellya_action_ready = Mock(return_value=True)
        self.rotation.capture_liberation_diagnostics = Mock()
        self.rotation.capture_bloom_diagnostics = Mock()
        self.rotation.log_liberation_unavailable = Mock()
        self.rotation.confirm_active_bloom = Mock(return_value=None)
        self.rotation.recovery_target_locked = Mock(return_value=True)

    def advance(self, seconds, **kwargs):
        self.clock[0] += seconds

    def cooldown(self, duration):
        self.task.get_cd.side_effect = lambda *args: max(0, 100 + duration - self.clock[0])
        self.char.has_cd.side_effect = lambda *args: self.task.get_cd() > 0.2
        self.char.liberation_available.side_effect = lambda: self.task.get_cd() == 0
        return self.rotation.camellya_liberation(self.char)

    def test_observed_1265_and_1269_cooldowns_are_supplemented_once(self):
        for duration in (0.37, 1.265, 1.269, 4.7):
            with self.subTest(duration=duration):
                self.setUp()
                self.assertFalse(self.cooldown(duration))
                self.rotation.prepare_bloom(self.char)
                self.char.click_liberation.assert_called_once_with(wait_if_cd_ready=0)
                self.assertGreaterEqual(self.clock[0], 100 + duration)
                self.assertLess(self.clock[0], 100 + duration + 0.11)
                self.rotation.prepare_bloom(self.char)
                self.char.click_liberation.assert_called_once()

    def test_preparation_attacks_continue_while_waiting_for_cooldown(self):
        self.cooldown(1.269)
        self.rotation.prepare_bloom(self.char)
        self.assertGreater(self.char.click.call_count, 10)
        self.char.heavy_attack.assert_not_called()

    def test_long_cooldown_does_not_delay_ready_bloom(self):
        self.cooldown(5.1)
        self.assertFalse(self.rotation.liberation_recheck_pending)
        self.rotation.prepare_bloom(self.char)
        self.char.click_liberation.assert_not_called()
        self.assertEqual(self.clock[0], 100)

    def test_stuck_cooldown_only_uses_predicted_expiry_window(self):
        self.cooldown(1.269)
        self.task.get_cd.side_effect = None
        self.task.get_cd.return_value = 1.269
        self.char.liberation_available.side_effect = None
        self.char.liberation_available.return_value = False
        self.rotation.prepare_bloom(self.char)
        self.char.click_liberation.assert_not_called()
        self.assertLess(self.clock[0], 101.5)
        self.assertFalse(self.rotation.liberation_recheck_pending)

    def test_replaced_cooldown_cancels_pending_cast(self):
        self.cooldown(1.269)
        self.task.get_cd.side_effect = None
        self.task.get_cd.return_value = 25
        self.rotation.prepare_bloom(self.char)
        self.char.click_liberation.assert_not_called()
        self.assertEqual(self.clock[0], 100)

    def test_dark_icon_does_not_invent_energy_or_wait_five_seconds(self):
        self.char.has_cd.return_value = False
        self.rotation.camellya_liberation(self.char)
        self.rotation.prepare_bloom(self.char)
        self.char.click_liberation.assert_not_called()
        self.assertLessEqual(self.clock[0], 100.55)

    def test_failed_supplement_is_not_retried(self):
        self.cooldown(1.269)
        self.char.click_liberation.return_value = False
        self.rotation.prepare_bloom(self.char)
        self.rotation.prepare_bloom(self.char)
        self.char.click_liberation.assert_called_once()

    def test_already_attempted_liberation_never_schedules_supplement(self):
        self.char.liberation_available.return_value = True
        self.char.click_liberation.return_value = False
        self.rotation.camellya_liberation(self.char)
        self.assertFalse(self.rotation.liberation_recheck_pending)
        self.rotation.prepare_bloom(self.char)
        self.char.click_liberation.assert_called_once()

    def test_disabled_liberation_cancels_pending_without_input(self):
        self.cooldown(1.269)
        self.task.use_liberation = False
        self.rotation.prepare_bloom(self.char)
        self.char.click_liberation.assert_not_called()
        self.assertFalse(self.rotation.liberation_recheck_pending)

    def test_liberation_rechecks_resources_after_animation(self):
        self.cooldown(1.269)
        self.char.is_con_full.return_value = False
        self.char.ephemeral_ready.return_value = False

        def cast(**kwargs):
            self.advance(4)
            self.char.is_con_full.return_value = True
            self.char.ephemeral_ready.return_value = True
            return True

        self.char.click_liberation.side_effect = cast
        self.rotation.prepare_bloom(self.char)
        self.char.click_liberation.assert_called_once()
        self.char.heavy_attack.assert_not_called()
        self.assertGreaterEqual(self.clock[0], 105.269)

    def test_active_bloom_cancels_pending_liberation(self):
        self.cooldown(1.269)
        self.rotation.confirm_active_bloom.return_value = {'forte': 0.3}
        self.rotation.prepare_bloom(self.char)
        self.char.click_liberation.assert_not_called()
        self.assertIsNotNone(self.rotation.bloom_resume)
        self.assertFalse(self.rotation.liberation_recheck_pending)

    def test_supplement_obeys_phase_hard_limit(self):
        self.cooldown(1.269)
        self.rotation.phase_started = 77
        with self.assertRaisesRegex(RotationTimeout, 'phase timeout'):
            self.rotation.prepare_bloom(self.char)
        self.char.click_liberation.assert_not_called()

    def test_pending_cast_revalidates_current_actor(self):
        self.cooldown(1.269)
        self.rotation.wait_camellya_action_ready.side_effect = lambda char: self.task.in_team()[1] == char.index
        self.task.in_team.return_value = (True, 1, 3)
        self.rotation.prepare_bloom(self.char)
        self.char.click_liberation.assert_not_called()

    def test_transient_target_loss_waits_then_continues_same_phase(self):
        self.rotation.recovery_target_locked.side_effect = lambda: self.clock[0] >= 100.15
        self.assertTrue(self.rotation.wait_bloom_target(self.char, 102))
        self.assertLess(self.clock[0], 100.3)
        self.assertEqual(self.rotation.phase, Phase.CAMELLYA_BURST)
        self.task.mouse_up.assert_called_once()
        self.task.middle_click.assert_not_called()
        self.char.heavy_attack.assert_not_called()
        self.char.click_liberation.assert_not_called()

    def test_target_loss_uses_at_most_one_retarget_then_confirms_two_frames(self):
        self.rotation.recovery_target_locked.side_effect = lambda: self.task.middle_click.called
        self.assertTrue(self.rotation.wait_bloom_target(self.char, 102))
        self.task.middle_click.assert_called_once()
        self.assertGreaterEqual(self.clock[0], 100.3)
        self.assertLess(self.clock[0], 100.75)

    def test_persistent_target_loss_cannot_renew_recovery_budget(self):
        self.rotation.recovery_target_locked.return_value = False
        self.assertFalse(self.rotation.wait_bloom_target(self.char, 102))
        ended = self.clock[0]
        self.assertLessEqual(ended, 100.75)
        self.assertFalse(self.rotation.wait_bloom_target(self.char, 105))
        self.assertEqual(self.clock[0], ended)
        self.task.middle_click.assert_called_once()
        self.char.click.assert_not_called()

    def test_lock_flicker_never_confirms_recovery(self):
        self.rotation.recovery_target_locked.side_effect = lambda: int(round((self.clock[0] - 100) / 0.05)) % 2 == 1
        self.assertFalse(self.rotation.wait_bloom_target(self.char, 102))
        self.char.heavy_attack.assert_not_called()

    def test_recovery_stops_at_remaining_resource_deadline(self):
        self.rotation.recovery_target_locked.return_value = False
        self.assertFalse(self.rotation.wait_bloom_target(self.char, 100.12))
        self.assertAlmostEqual(self.clock[0], 100.12)
        self.task.middle_click.assert_not_called()

    def test_hud_or_actor_change_aborts_without_retarget(self):
        for hud in ((False, -1, 3), (True, 1, 3)):
            with self.subTest(hud=hud):
                self.task.in_team.return_value = hud
                with self.assertRaisesRegex(RotationTimeout, 'actor or HUD changed'):
                    self.rotation.wait_bloom_target(self.char, 102)
        self.task.middle_click.assert_not_called()

    def test_actor_change_during_recovery_aborts_before_input(self):
        self.rotation.recovery_target_locked.return_value = False
        self.task.next_frame.side_effect = lambda: setattr(self.task.in_team, 'return_value', (True, 1, 3))
        with self.assertRaisesRegex(RotationTimeout, 'actor or HUD changed'):
            self.rotation.wait_bloom_target(self.char, 102)
        self.task.middle_click.assert_not_called()

    def test_end_condition_stops_recovery_without_retarget(self):
        self.rotation.recovery_target_locked.return_value = False
        self.task.combat_end_condition = Mock(return_value=True)
        self.assertFalse(self.rotation.wait_bloom_target(self.char, 102))
        self.task.middle_click.assert_not_called()
        self.char.heavy_attack.assert_not_called()

    def test_cancellation_propagates_from_recovery(self):
        self.rotation.recovery_target_locked.return_value = False
        self.char.sleep.side_effect = RuntimeError('cancelled')
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.rotation.wait_bloom_target(self.char, 102)
        self.task.middle_click.assert_not_called()
        self.task.mouse_up.assert_called_once()

    def test_transient_loss_before_heavy_preserves_burst_and_no_skill_replay(self):
        self.char.ephemeral_ready.return_value = False
        self.char.is_con_full.return_value = False
        self.char.get_current_con.return_value = 0.842
        self.rotation.recovery_target_locked.side_effect = lambda: self.clock[0] >= 105.2

        def heavy(duration):
            self.advance(duration)
            self.char.ephemeral_ready.return_value = True
            self.char.is_con_full.return_value = True

        self.char.heavy_attack.side_effect = heavy
        self.rotation.prepare_bloom(self.char)
        self.char.heavy_attack.assert_called_once_with(1.2)
        self.assertEqual(self.rotation.phase, Phase.CAMELLYA_BURST)
        self.char.click_liberation.assert_not_called()
        self.assertLess(self.clock[0], 106.6)

    def test_target_recovery_consumes_topup_budget(self):
        self.char.ephemeral_ready.return_value = False
        self.char.is_con_full.return_value = False
        self.rotation.recovery_target_locked.side_effect = lambda: self.clock[0] >= 105.5
        self.char.heavy_attack.side_effect = self.advance
        with self.assertRaisesRegex(RotationTimeout, 'bloom not ready'):
            self.rotation.prepare_bloom(self.char)
        self.assertLessEqual(self.clock[0], 108.3)
        self.char.heavy_attack.assert_called_once()

    def test_persistent_loss_never_sends_resource_heavy(self):
        self.char.ephemeral_ready.return_value = False
        self.char.is_con_full.return_value = False
        self.rotation.recovery_target_locked.return_value = False
        with self.assertRaisesRegex(RotationTimeout, 'target lost'):
            self.rotation.prepare_bloom(self.char)
        self.char.heavy_attack.assert_not_called()
        self.assertLess(self.clock[0], 105.9)

    def test_reset_clears_both_pending_decisions(self):
        self.cooldown(1.269)
        self.rotation.bloom_target_recovery_used = True
        self.rotation.reset()
        self.assertFalse(self.rotation.liberation_recheck_pending)
        self.assertIsNone(self.rotation.liberation_recheck_deadline)
        self.assertFalse(self.rotation.bloom_target_recovery_used)

    def test_recovery_relocks_before_global_combat_check_and_attacks(self):
        self.char.is_con_full.return_value = False
        self.rotation.recovery_target_locked.side_effect = lambda: self.clock[0] >= 100.1

        def check_combat():
            self.assertTrue(self.rotation.recovery_target_locked())

        self.char.check_combat.side_effect = check_combat
        self.char.click.side_effect = lambda **kwargs: setattr(self.char.is_con_full, 'return_value', True)
        reason, _ = self.rotation.wait_bloom_ready(self.char, 2, recovery=True)
        self.assertFalse(reason)
        self.char.click.assert_called_once()
        self.char.check_combat.assert_called_once()

    def test_recovered_target_rechecks_resources_before_heavy(self):
        self.char.ephemeral_ready.return_value = False
        self.char.is_con_full.return_value = False

        def lock():
            if self.clock[0] >= 105.2:
                self.char.ephemeral_ready.return_value = True
                self.char.is_con_full.return_value = True
                return True
            return False

        self.rotation.recovery_target_locked.side_effect = lock
        self.rotation.prepare_bloom(self.char)
        self.char.heavy_attack.assert_not_called()
        self.assertLess(self.clock[0], 105.4)

    def test_recovered_target_rechecks_active_bloom_before_heavy(self):
        self.char.ephemeral_ready.return_value = False
        self.char.is_con_full.return_value = False
        self.rotation.recovery_target_locked.side_effect = lambda: self.clock[0] >= 105.2
        self.rotation.confirm_active_bloom.side_effect = lambda char: (
            {'forte': 0.3} if self.clock[0] >= 105.2 else None)
        self.rotation.prepare_bloom(self.char)
        self.assertIsNotNone(self.rotation.bloom_resume)
        self.char.heavy_attack.assert_not_called()

    def test_target_recovery_obeys_phase_limit(self):
        self.rotation.recovery_target_locked.return_value = False
        self.rotation.phase_started = 76.2
        with self.assertRaisesRegex(RotationTimeout, 'phase timeout'):
            self.rotation.wait_bloom_target(self.char, 102)
        self.task.middle_click.assert_not_called()


class TestChunSanShouBuild(unittest.TestCase):
    def test_real_importer_loads_three_matching_classes(self):
        from ok.util.config import Config
        from src.char.Camellya import Camellya
        from src.char.Sanhua import Sanhua
        from src.char.ShoreKeeper import ShoreKeeper
        from src.char.CharFactory import apply_team_char_classes
        from src.char.CustomCharLoader import clear_team_char_cache, import_custom_team, inspect_team_archive
        from src.task.BaseCombatTask import BaseCombatTask

        spec = importlib.util.spec_from_file_location('chunsanshou_build', SOURCE / 'build.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        scratch = ROOT / 'downloads'
        scratch.mkdir(exist_ok=True)
        previous_config = Config.config_folder
        with tempfile.TemporaryDirectory(dir=scratch) as folder:
            try:
                Config.config_folder = folder
                clear_team_char_cache()
                info = inspect_team_archive(module.build_archive(Path(folder) / 'team.zip'))
                import_custom_team(info)
                task = BaseCombatTask.__new__(BaseCombatTask)
                classes = (Camellya, Sanhua, ShoreKeeper)
                labels = ('char_camellya', 'char_sanhua', 'char_shorekeeper')
                task.chars = [cls(task, index, char_name=label)
                              for index, (cls, label) in enumerate(zip(classes, labels))]
                apply_team_char_classes(task, task.chars)
                identities = set()
                for char, builtin in zip(task.chars, classes):
                    self.assertIsNot(type(char), builtin)
                    self.assertIsInstance(char, builtin)
                    self.assertTrue(char._team_members_match())
                    identities.add(char._chunsanshou_package)
                self.assertEqual(len(identities), 1)
                self.assertEqual(task.chars[0]._rotation_class.BLOOM_PREPARE_TIMEOUT, 5)
            finally:
                clear_team_char_cache()
                Config.config_folder = previous_config

    def test_archive_is_deterministic_and_contains_import_sources(self):
        spec = importlib.util.spec_from_file_location('chunsanshou_build', SOURCE / 'build.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        scratch = ROOT / 'downloads'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as folder:
            first = module.build_archive(Path(folder) / 'first.zip')
            second = module.build_archive(Path(folder) / 'second.zip')
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(set(archive.namelist()), set(module.MEMBERS))
                for name in module.MEMBERS:
                    self.assertEqual(archive.read(name), (SOURCE / name).read_bytes())


if __name__ == '__main__':
    unittest.main()
