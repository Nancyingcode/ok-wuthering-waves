"""Offline decisions for the saved import team; no game or real input is started."""
import ast
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'custom_teams/Changli_Jinhsi_Verina'


class NativeChangli:
    def do_perform(self):
        self.native_calls += 1


class NativeStub(NativeChangli):
    def get_switch_priority(self, *args):
        return 17

    def on_combat_end(self, chars):
        self.native_ended = True


class NotInCombatException(Exception):
    pass


def load_candidate():
    path = SOURCE / 'Changli.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [n for n in tree.body if not isinstance(n, ast.ImportFrom)]
    namespace = {'NativeChangli': NativeStub,
                 'SwitchPriority': SimpleNamespace(MUST=400, HIGH=300),
                 'NotInCombatException': NotInCombatException}
    exec(compile(tree, str(path), 'exec'), namespace)
    return namespace['Changli'], namespace


def teammate(name, index):
    return SimpleNamespace(name=name, index=index, is_con_full=Mock(return_value=False),
                           send_resonance_key=Mock(), record_resonance_use=Mock(),
                           send_liberation_key=Mock(), record_liberation_use=Mock())


class DecisionChecks(unittest.TestCase):

    def setUp(self):
        cls, namespace = load_candidate()
        self.now = 100.0
        namespace['time'] = SimpleNamespace(time=lambda: self.now)
        self.obj = cls.__new__(cls)
        obj = self.obj
        obj.name = 'Changli'
        obj.index = 0
        obj.cur = obj
        obj.native_calls = 0
        obj.has_intro = False
        obj.macro_desync = 0
        obj.macro_marks = []
        obj.macro_t0 = self.now
        obj.poking = False
        obj.holding_attack = False
        obj.loop_count = 0
        obj.logger = Mock()
        obj.macro_wait = Mock(side_effect=self.advance)
        obj.macro_mark = Mock()
        obj.is_con_full = Mock(return_value=False)
        obj.time_elapsed_accounting_for_freeze = lambda start: self.now - start
        self.jx = teammate('Jinhsi', 1)
        self.vr = teammate('Verina', 2)
        obj.task = SimpleNamespace(
            chars=[obj, self.jx, self.vr],
            next_frame=Mock(side_effect=lambda: self.advance(0.03)),
            in_team=Mock(return_value=(True, 0, 3)),
            send_key=Mock(), mouse_down=Mock(), mouse_up=Mock(),
            skip_combat_check=False,
            raise_not_in_combat=Mock(side_effect=RuntimeError('unknown field')),
        )
        self.obj.stop_on_result = Mock(return_value=False)
        self.obj.add_freeze_duration = Mock()
        self.obj.task.key_config = {'Jump Key': 'space'}
        self.obj.task.ocr = Mock(return_value=[])
        self.obj.task.box_of_screen = Mock(side_effect=lambda *args: args)
        self.obj.task.reset_to_false = Mock()
        self.vr.liberation_available = Mock(return_value=False)
        self.vr.resonance_available = Mock(return_value=False)
        self.vr.is_mouse_forte_full = Mock(return_value=False)
        self.vr.can_heavy_attack = Mock(return_value=True)
        self.vr.HEAVY_ATTACK_TIME = 0.7

    def advance(self, seconds):
        self.now += seconds

    def test_unknown_switch_never_fakes_handoff(self):
        obj = self.obj
        obj.on_field_char = Mock(return_value=None)
        obj.handoff = Mock()
        self.assertIsNone(obj.macro_switch(self.jx, 0.02, False, 0.1))
        obj.handoff.assert_not_called()
        self.assertIs(obj.cur, obj)

    def test_partial_party_hud_does_not_confirm_switch(self):
        obj = self.obj
        obj.task.in_team.return_value = (True, 1, 1)
        obj.on_field_char = Mock(return_value=None)
        obj.handoff = Mock()
        self.assertIsNone(obj.macro_switch(self.jx, 0.02, False, 0.1))
        obj.handoff.assert_not_called()

    def test_late_visible_target_can_complete_switch(self):
        obj = self.obj
        obj.on_field_char = Mock(return_value=self.jx)
        obj.handoff = Mock()
        self.assertTrue(obj.macro_switch(self.jx, 0.02, False, 0.1))
        obj.handoff.assert_called_once_with(obj, self.jx, False)
        self.assertEqual(obj.macro_desync, 0)

    def test_expected_intro_does_not_override_low_concerto(self):
        obj = self.obj
        obj.task.in_team.return_value = (True, 1, 3)
        obj.handoff = Mock()
        self.assertTrue(obj.macro_switch(self.jx, 0.02, True, 0.1))
        obj.handoff.assert_called_once_with(obj, self.jx, False)

    def test_quick_switch_reads_concerto_before_number_key(self):
        obj = self.obj
        events = []
        obj.is_con_full.side_effect = lambda: events.append('read') or True
        obj.task.send_key.side_effect = lambda *a, **kw: events.append('switch')
        obj.task.in_team.return_value = (True, 1, 3)
        obj.handoff = Mock()
        self.assertTrue(obj.quick_switch(self.jx, read_con=True))
        self.assertLess(events.index('read'), events.index('switch'))
        obj.handoff.assert_called_once_with(obj, self.jx, True)

    def test_macro_stops_before_next_attack_on_failed_switch(self):
        obj = self.obj
        obj.macro_switch = Mock(return_value=None)
        obj.macro_key = Mock()
        self.assertFalse(obj.run_macro((('>JX', 78, 100, ''), ('A', 78, 100, '')), 'test'))
        obj.macro_key.assert_not_called()

    def test_macro_stops_after_unconfirmed_skill(self):
        obj = self.obj
        obj.macro_key = Mock(return_value=None)
        obj.macro_switch = Mock()
        self.assertFalse(obj.run_macro((('E!', 78, 3000, ''), ('>JX', 78, 100, '')), 'test'))
        obj.macro_switch.assert_not_called()

    def test_unavailable_verina_liberation_does_not_spam_or_record(self):
        obj = self.obj
        obj.cur = self.vr
        obj.wait_hud_up = Mock(return_value=True)
        obj.lib_ready_now = Mock(return_value=False)
        obj.fire_r = Mock()
        self.assertTrue(obj.macro_key('R!', 0.094, 2.3))
        obj.fire_r.assert_not_called()
        self.vr.send_liberation_key.assert_not_called()
        self.vr.record_liberation_use.assert_not_called()

    def test_dragon_rescue_starts_before_original_three_second_wait(self):
        obj = self.obj
        obj.cur = self.jx
        obj.wait_hud_up = Mock(return_value=True)
        obj.wait_hud_back = Mock(return_value=True)
        began = self.now
        rescue_times = []
        obj.press_until_hud_gone = Mock(side_effect=lambda *a, **kw: rescue_times.append(self.now - began) or True)
        self.assertTrue(obj.macro_key('E!', 0.078, 3.0))
        self.assertLess(rescue_times[0], 1.0)
        self.assertGreaterEqual(self.now - began, 3.078)
        self.jx.record_resonance_use.assert_called_once()

    def test_failed_dragon_never_records_success(self):
        obj = self.obj
        obj.cur = self.jx
        obj.wait_hud_up = Mock(return_value=True)
        obj.watch_hud_gone = Mock(return_value=False)
        obj.press_until_hud_gone = Mock(return_value=False)
        obj.wait_hud_back = Mock()
        self.assertIsNone(obj.macro_key('E!', 0.078, 3.0))
        self.jx.record_resonance_use.assert_not_called()
        obj.wait_hud_back.assert_not_called()

    def test_rescue_recognizes_delayed_initial_dragon(self):
        obj = self.obj
        obj.task.in_team.return_value = (False, -1, 1)
        self.assertTrue(obj.press_until_hud_gone(self.jx, 1.0, 'test', initial_press=True))
        self.jx.send_resonance_key.assert_not_called()

    def test_unrelated_hidden_hud_does_not_prove_dragon(self):
        obj = self.obj
        obj.task.in_team.return_value = (False, -1, 1)
        self.jx.resonance_available = Mock(return_value=False)
        self.assertFalse(obj.press_until_hud_gone(self.jx, 0.2, 'test'))

    def test_failed_liberation_never_starts_second_heavy(self):
        obj = self.obj
        obj.fire_r = Mock(return_value=False)
        self.assertFalse(obj.changli_lib_then_heavy('test'))
        obj.task.mouse_down.assert_not_called()

    def test_liberation_requires_consecutive_hidden_frames(self):
        obj = self.obj
        obj.task.in_team.side_effect = [(True, 0, 3), (False, -1, 1), (True, 0, 3), (False, -1, 1), (False, -1, 1), (False, -1, 1)]
        self.assertTrue(obj.fire_lib(self.vr, window=1.0))
        self.assertEqual(obj.task.in_team.call_count, 6)
        self.vr.record_liberation_use.assert_called_once()

    def test_hidden_hud_without_input_is_not_a_liberation(self):
        obj = self.obj
        obj.task.in_team.return_value = (False, -1, 1)
        self.assertFalse(obj.fire_lib(self.vr, window=0.2))
        self.vr.record_liberation_use.assert_not_called()

    def test_missing_forte_does_not_trigger_blind_first_heavy(self):
        obj = self.obj
        obj.cl_fill_until_ready = Mock(return_value=False)
        obj.hold_heavy = Mock()
        obj.lib_ready_now = Mock(return_value=False)
        self.assertFalse(obj.seg_changli_zrz(self.jx, 'test'))
        obj.hold_heavy.assert_not_called()

    def test_missing_forte_can_recover_using_ready_liberation(self):
        obj = self.obj
        obj.cl_fill_until_ready = Mock(return_value=False)
        obj.hold_heavy = Mock()
        obj.lib_ready_now = Mock(return_value=True)
        obj.changli_lib_then_heavy = Mock(return_value=True)
        obj.pass_intro = Mock(return_value=True)
        self.assertTrue(obj.seg_changli_zrz(self.jx, 'test'))
        obj.hold_heavy.assert_not_called()
        obj.changli_lib_then_heavy.assert_called_once()

    def test_concerto_poll_advances_frames(self):
        obj = self.obj
        self.jx.get_current_con = Mock(side_effect=[0.4, 1.0])
        self.assertTrue(obj.wait_con_at_least(self.jx, 1.0, cap=0.5, poke=False))
        self.assertEqual(obj.task.next_frame.call_count, 2)

    def test_opener_failure_propagates_and_cleans_up(self):
        obj = self.obj
        obj.macro_enter = Mock(return_value=(self.jx, self.vr, False))
        obj.macro_exit = Mock()
        obj.run_macro = Mock(return_value=False)
        self.assertFalse(obj.perform_opener(obj))
        obj.macro_exit.assert_called_once_with(False)

    def test_failed_manual_stage_does_not_start_next_character(self):
        obj = self.obj
        obj.macro_enter = Mock(return_value=(self.jx, self.vr, False))
        obj.macro_exit = Mock()
        obj.seg_changli_zrz = Mock(return_value=False)
        obj.seg_jinhsi_spout = Mock()
        self.assertFalse(obj.perform_loop(obj))
        obj.seg_jinhsi_spout.assert_not_called()
        obj.macro_exit.assert_called_once_with(False)

    def prepare_jinhsi_segment(self):
        obj = self.obj
        obj.spam_key = Mock()
        obj.macro_spam = Mock()
        obj.try_f_break = Mock()
        obj.jx_normal_e = Mock(return_value='ok')
        obj.lib_ready_now = Mock(return_value=False)
        obj.seg_jx_e2 = Mock(return_value=True)
        obj.pass_intro = Mock(return_value=True)
        obj.quick_switch = Mock(return_value=True)
        obj.press_e_then_switch = Mock(return_value=True)
        obj.wait_con_at_least = Mock(return_value=True)
        return obj

    def test_default_jinhsi_segment_skips_mid_switch(self):
        obj = self.prepare_jinhsi_segment()
        self.assertIs(obj.seg_jinhsi_spout(self.jx, 'test'), False)
        obj.quick_switch.assert_not_called()
        obj.wait_con_at_least.assert_not_called()

    def test_optional_mid_switch_does_not_wait_for_second_full_concerto(self):
        obj = self.prepare_jinhsi_segment()
        obj.B_MID_CL_E = True
        self.assertIs(obj.seg_jinhsi_spout(self.jx, 'test'), False)
        self.assertEqual(obj.pass_intro.call_args.args[2], 0.0)

    def test_failed_jinhsi_dragon_does_not_switch(self):
        obj = self.prepare_jinhsi_segment()
        obj.seg_jx_e2.return_value = False
        self.assertIsNone(obj.seg_jinhsi_spout(self.jx, 'test'))
        obj.pass_intro.assert_not_called()

    def test_unknown_field_does_not_run_native_rotation(self):
        obj = self.obj
        obj.opener_blocked_by = Mock(return_value=None)
        obj.perform_opener = Mock(return_value=False)
        obj.on_field_char = Mock(return_value=None)
        with self.assertRaisesRegex(RuntimeError, 'unknown field'):
            obj.do_perform()
        self.assertEqual(obj.native_calls, 0)


class RecoveryChecks(DecisionChecks):
    def setUp(self):
        super().setUp()
        self.obj.recovery_pending = False
        self.obj.recovery_attempts = 0
        self.obj.macro_stage = 'test'
        for char in (self.jx, self.vr):
            char.has_intro = False
            char.incarnation = False
            char.incarnation_cd = False

    def prepare_jinhsi_segment(self):
        obj = super().prepare_jinhsi_segment()
        obj.cur = self.jx
        self.jx.has_intro = True
        return obj

    def test_expected_intro_does_not_override_low_concerto(self):
        obj = self.obj
        obj.task.in_team.return_value = (True, 1, 3)
        obj.handoff = Mock()
        self.assertIsNone(obj.macro_switch(self.jx, .02, True, .1))
        obj.handoff.assert_not_called()
        obj.task.send_key.assert_not_called()

    def test_low_concerto_cannot_send_required_intro_switch(self):
        obj = self.obj
        obj.wait_con_at_least = Mock(return_value=False)
        obj.handoff = Mock()
        self.assertFalse(obj.pass_intro(obj, self.jx, 2.5, 'L4A', poke=True, require_intro=True))
        obj.task.send_key.assert_not_called()
        obj.handoff.assert_not_called()

    def test_late_full_concerto_is_rechecked_before_switch(self):
        obj = self.obj
        obj.wait_con_at_least = Mock(return_value=False)
        obj.is_con_full.return_value = True
        obj.task.in_team.return_value = (True, 1, 3)
        obj.handoff = Mock()
        self.assertTrue(obj.pass_intro(obj, self.jx, 2.5, 'L4A', require_intro=True))
        obj.handoff.assert_called_once_with(obj, self.jx, True)

    def test_assumed_concerto_cannot_bypass_required_intro(self):
        self.assertFalse(self.obj.quick_switch(self.jx, assume_con=True, require_intro=True))
        self.obj.task.send_key.assert_not_called()

    def test_ordinary_quickswap_remains_allowed_without_concerto(self):
        obj = self.obj
        obj.task.in_team.return_value = (True, 1, 3)
        obj.handoff = Mock()
        self.assertTrue(obj.quick_switch(self.jx))
        obj.handoff.assert_called_once_with(obj, self.jx, False)

    def test_already_on_field_is_not_evidence_of_intro(self):
        obj = self.obj
        obj.cur = self.jx
        self.assertFalse(obj.quick_switch(self.jx, require_intro=True))
        obj.task.send_key.assert_not_called()

    def test_no_intro_segment_never_inputs_enhanced_skill(self):
        obj = self.prepare_jinhsi_segment()
        self.jx.has_intro = False
        self.jx.incarnation = True
        self.assertIsNone(obj.seg_jinhsi_spout(self.jx, 'L4B'))
        obj.spam_key.assert_not_called()
        obj.jx_normal_e.assert_not_called()
        self.assertTrue(self.jx.incarnation)

    def test_intro_on_wrong_field_never_inputs_skill(self):
        obj = self.prepare_jinhsi_segment()
        obj.cur = obj
        self.assertIsNone(obj.seg_jinhsi_spout(self.jx, 'L4B'))
        obj.spam_key.assert_not_called()

    def test_intro_is_consumed_once_even_if_normal_e_fails(self):
        obj = self.prepare_jinhsi_segment()
        self.jx.incarnation = True
        self.jx.incarnation_cd = True
        obj.jx_normal_e.return_value = 'none'
        self.assertIsNone(obj.seg_jinhsi_spout(self.jx, 'B'))
        self.assertFalse(self.jx.has_intro)
        self.assertFalse(self.jx.incarnation)
        self.assertFalse(self.jx.incarnation_cd)
        count = obj.spam_key.call_count
        self.assertIsNone(obj.seg_jinhsi_spout(self.jx, 'B retry'))
        self.assertEqual(obj.spam_key.call_count, count)

    def test_failed_dragon_retains_known_partial_phase_for_native(self):
        obj = self.prepare_jinhsi_segment()
        obj.seg_jx_e2.return_value = False
        self.assertIsNone(obj.seg_jinhsi_spout(self.jx, 'B'))
        self.assertTrue(self.jx.incarnation)
        self.assertFalse(self.jx.has_intro)

    def test_early_dragon_clears_native_phase_before_animation_wait(self):
        obj = self.prepare_jinhsi_segment()
        obj.jx_normal_e.return_value = 'e2'
        obj.wait_hud_back = Mock(return_value=False)
        self.assertIsNone(obj.seg_jinhsi_spout(self.jx, 'B'))
        self.assertFalse(self.jx.incarnation)
        self.assertFalse(self.jx.has_intro)

    def test_dragon_confirmation_clears_phase_even_if_hud_does_not_return(self):
        obj = self.obj
        self.jx.incarnation = True
        self.jx.incarnation_cd = True
        self.jx.has_intro = True
        obj.send_echo_now = Mock()
        obj.press_until_hud_gone = Mock(return_value=True)
        obj.wait_hud_back = Mock(return_value=False)
        self.assertFalse(obj.seg_jx_e2(self.jx, 'test'))
        self.assertFalse(self.jx.incarnation)
        self.assertFalse(self.jx.incarnation_cd)
        self.assertFalse(self.jx.has_intro)

    def test_macro_enhanced_skill_also_requires_new_intro(self):
        obj = self.obj
        obj.cur = self.jx
        obj.spam_key = Mock()
        self.assertIsNone(obj.macro_key('E+', .08, .5))
        obj.spam_key.assert_not_called()
        self.jx.has_intro = True
        self.assertTrue(obj.macro_key('E+', .08, .5))
        self.assertFalse(self.jx.has_intro)
        self.assertIsNone(obj.macro_key('E+', .08, .5))
        obj.spam_key.assert_called_once()

    def test_macro_normal_skill_records_native_partial_phase(self):
        obj = self.obj
        obj.cur = self.jx
        obj.press_res_until_cd = Mock(return_value=True)
        self.assertTrue(obj.macro_key('E#', .08, .5))
        self.assertTrue(self.jx.incarnation)

    def test_macro_dragon_clears_native_partial_phase(self):
        obj = self.obj
        obj.cur = self.jx
        self.jx.incarnation = True
        obj.wait_hud_up = Mock(return_value=True)
        obj.watch_hud_gone = Mock(return_value=True)
        obj.wait_hud_back = Mock(return_value=True)
        self.assertTrue(obj.macro_key('E!', .08, 3.0))
        self.assertFalse(self.jx.incarnation)

    def test_l3_failure_latches_recovery_instead_of_starting_l4(self):
        obj = self.obj
        obj.opener_blocked_by = Mock(return_value='already ran')
        obj.perform_loop = Mock(return_value=False)
        obj.on_field_char = Mock(return_value=obj)
        obj.do_perform()
        self.assertTrue(obj.recovery_pending)
        self.assertEqual(obj.native_calls, 1)
        obj.do_perform()
        obj.perform_loop.assert_called_once()
        self.assertEqual(obj.native_calls, 2)
        self.assertTrue(obj.logger.warning.called)

    def test_pending_recovery_uses_native_switch_priority(self):
        obj = self.obj
        obj.opener_pending = Mock(return_value=False)
        self.assertEqual(obj.get_switch_priority(), 300)
        obj.recovery_pending = True
        self.assertEqual(obj.get_switch_priority(), 17)

    def prepare_recovery(self):
        obj = self.prepare_jinhsi_segment()
        obj.recovery_pending = True
        obj.on_field_char = Mock(return_value=self.jx)
        obj.macro_enter = Mock(return_value=(self.jx, self.vr, False))
        obj.macro_exit = Mock()
        obj.run_macro = Mock(return_value=True)
        return obj

    def test_recovery_resumes_at_jinhsi_not_changli_fill(self):
        obj = self.prepare_recovery()
        obj.seg_changli_zrz = Mock()
        self.assertTrue(obj.resume_from_jinhsi(self.jx))
        self.assertFalse(obj.recovery_pending)
        self.assertEqual(obj.recovery_attempts, 1)
        obj.seg_changli_zrz.assert_not_called()
        obj.run_macro.assert_called_once()
        obj.macro_exit.assert_called_once_with(False)

    def test_recovery_without_new_intro_does_not_take_control(self):
        obj = self.prepare_recovery()
        self.jx.has_intro = False
        self.assertFalse(obj.resume_from_jinhsi(self.jx))
        obj.macro_enter.assert_not_called()
        obj.spam_key.assert_not_called()

    def test_recovery_failure_never_continues_macro_or_reuses_intro(self):
        obj = self.prepare_recovery()
        obj.jx_normal_e.return_value = 'none'
        self.assertFalse(obj.resume_from_jinhsi(self.jx))
        self.assertTrue(obj.recovery_pending)
        self.assertFalse(obj.can_resume_from_jinhsi(self.jx))
        obj.run_macro.assert_not_called()
        obj.macro_exit.assert_called_once_with(False)

    def test_recovery_exception_releases_macro_ownership(self):
        obj = self.prepare_recovery()
        obj.jx_normal_e.side_effect = RuntimeError('cancelled')
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            obj.resume_from_jinhsi(self.jx)
        obj.macro_exit.assert_called_once_with(False)
        self.assertTrue(obj.recovery_pending)

    def test_failed_recovery_attempts_are_bounded_per_combat(self):
        obj = self.prepare_recovery()
        obj.jx_normal_e.return_value = 'none'
        for _ in range(obj.RECOVERY_RESUME_LIMIT):
            self.jx.has_intro = True
            self.assertFalse(obj.resume_from_jinhsi(self.jx))
        self.jx.has_intro = True
        self.assertFalse(obj.can_resume_from_jinhsi(self.jx))
        self.assertFalse(obj.resume_from_jinhsi(self.jx))
        self.assertEqual(obj.macro_enter.call_count, 2)

    def test_combat_end_resets_recovery_budget(self):
        obj = self.obj
        obj.recovery_pending = True
        obj.recovery_attempts = 2
        obj.release_attack = Mock()
        obj.on_combat_end(obj.task.chars)
        self.assertFalse(obj.recovery_pending)
        self.assertEqual(obj.recovery_attempts, 0)
        obj.release_attack.assert_called_once()
        self.assertTrue(obj.native_ended)

    def test_new_opener_resets_recovery_budget(self):
        obj = self.obj
        obj.recovery_pending = True
        obj.recovery_attempts = 2
        obj.opener_blocked_by = Mock(return_value=None)
        obj.perform_opener = Mock(return_value=True)
        obj.do_perform()
        self.assertFalse(obj.recovery_pending)
        self.assertEqual(obj.recovery_attempts, 0)


class NativeJinhsiStub:
    def do_perform(self):
        self.native_calls += 1


class JinhsiDispatchChecks(unittest.TestCase):
    def setUp(self):
        path = SOURCE / 'Jinhsi.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        tree.body = [n for n in tree.body if not isinstance(n, ast.ImportFrom)]
        namespace = {'NativeJinhsi': NativeJinhsiStub, 'SwitchPriority': SimpleNamespace(NO=0)}
        exec(compile(tree, str(path), 'exec'), namespace)
        cls = namespace['Jinhsi']
        self.jx = cls.__new__(cls)
        self.jx.name = 'Jinhsi'
        self.jx.native_calls = 0
        self.jx.logger = Mock()
        self.jx.switch_next_char = Mock()
        macro_cls, _ = load_candidate()
        self.macro = macro_cls.__new__(macro_cls)
        self.macro.name = 'Changli'
        self.macro.recovery_pending = True
        self.macro.recovery_attempts = 0
        self.macro.opener_pending = Mock(return_value=False)
        self.macro.resume_from_jinhsi = Mock(return_value=True)
        self.jx.task = SimpleNamespace(chars=[self.macro, self.jx, SimpleNamespace(name='Verina')])
        self.jx.has_intro = False

    def test_partial_native_state_does_not_reenter_macro(self):
        self.jx.incarnation = True
        self.jx.do_perform()
        self.assertEqual(self.jx.native_calls, 1)
        self.macro.resume_from_jinhsi.assert_not_called()

    def test_fresh_intro_dispatches_to_recovery(self):
        self.jx.has_intro = True
        self.jx.do_perform()
        self.assertEqual(self.jx.native_calls, 0)
        self.macro.resume_from_jinhsi.assert_called_once_with(self.jx)

    def test_exhausted_budget_uses_native(self):
        self.jx.has_intro = True
        self.macro.recovery_attempts = 2
        self.jx.do_perform()
        self.assertEqual(self.jx.native_calls, 1)
        self.macro.resume_from_jinhsi.assert_not_called()

    def test_opener_keeps_priority_over_recovery(self):
        self.jx.has_intro = True
        self.macro.opener_pending.return_value = True
        self.jx.do_perform()
        self.jx.switch_next_char.assert_called_once()
        self.macro.resume_from_jinhsi.assert_not_called()


class RecoveryReplayChecks(RecoveryChecks):
    # Only collect the cross-character replay here; the base cases run above.
    def test_log_failure_then_native_intro_rejoins_macro(self):
        obj = self.obj
        path = SOURCE / 'Jinhsi.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        tree.body = [n for n in tree.body if not isinstance(n, ast.ImportFrom)]
        namespace = {'NativeJinhsi': NativeJinhsiStub, 'SwitchPriority': SimpleNamespace(NO=0)}
        exec(compile(tree, str(path), 'exec'), namespace)
        cls = namespace['Jinhsi']
        jx = cls.__new__(cls)
        jx.__dict__.update(vars(self.jx))
        jx.task = obj.task
        jx.logger = Mock()
        jx.native_calls = 0
        obj.task.chars = [obj, jx, self.vr]
        obj.opener_blocked_by = Mock(return_value='already ran')
        obj.perform_loop = Mock(return_value=False)
        obj.on_field_char = Mock(return_value=obj)
        obj.do_perform()
        self.assertTrue(obj.recovery_pending)
        self.assertEqual(obj.native_calls, 1)

        # Reproduce the native framework's fresh intro handoff after the L3 failure.
        obj.switch_out = Mock()
        obj.is_sub_dps = False
        jx.switch_out = Mock()
        jx.is_sub_dps = False
        obj.task.add_freeze_duration = Mock()
        obj.intro_motion_freeze_duration = 0.8
        jx.intro_motion_freeze_duration = 0.8
        obj.handoff(obj, jx, True)
        obj.on_field_char.return_value = jx
        obj.spam_key = Mock()
        obj.macro_spam = Mock()
        obj.try_f_break = Mock()
        obj.jx_normal_e = Mock(return_value='ok')
        obj.lib_ready_now = Mock(return_value=False)
        obj.send_echo_now = Mock()
        obj.press_until_hud_gone = Mock(return_value=True)
        obj.wait_hud_back = Mock(return_value=True)
        jx.get_current_con = Mock(return_value=1.0)
        jx.is_con_full.return_value = True
        obj.task.in_team.return_value = (True, 0, 3)
        obj.run_macro = Mock(return_value=True)
        jx.do_perform()

        self.assertFalse(obj.recovery_pending)
        self.assertFalse(jx.incarnation)
        self.assertFalse(jx.has_intro)
        self.assertIs(obj.cur, obj)
        self.assertEqual(jx.native_calls, 0)
        self.assertEqual(obj.perform_loop.call_count, 1)
        obj.run_macro.assert_called_once()
        self.assertFalse(obj.task.skip_combat_check)
        self.assertFalse(obj.task._ww_macro_busy)
        self.assertFalse(obj.holding_attack)


class HandoffChecks(RecoveryChecks):
    def setUp(self):
        super().setUp()
        obj = self.obj
        obj.cur = self.vr
        self.vr.current_con = 0.0
        self.vr.is_sub_dps = False
        self.vr.switch_out = Mock()
        self.jx.intro_motion_freeze_duration = 0.8
        obj.task.add_freeze_duration = Mock()
        self.visible_index = self.vr.index
        obj.task.in_team.side_effect = lambda: (True, self.visible_index, 3)
        self.events = []
        self.began = self.now
        obj.task.send_key.side_effect = self.switch_key
        obj.task.get_current_con = Mock(return_value=.99)
        obj.click = Mock(side_effect=lambda: self.events.append('fill'))

    def switch_key(self, key, **kwargs):
        self.events.append('switch')
        self.visible_index = key - 1

    def test_already_full_requires_fresh_confirmation_without_attacks(self):
        obj = self.obj
        obj.task.get_current_con.return_value = 1.0
        self.assertTrue(obj.macro_switch(self.jx, .02, True, .1))
        self.assertEqual(obj.task.get_current_con.call_count, 2)
        obj.click.assert_not_called()
        self.vr.is_con_full.assert_not_called()
        self.assertTrue(self.jx.has_intro)
        self.assertLess(self.now - self.began, .5)

    def test_waits_for_concerto_and_resumes_same_macro_step(self):
        obj = self.obj
        obj.recovery_pending = True
        obj.recovery_attempts = 1
        obj.task.get_current_con.side_effect = lambda: 1.0 if self.now - self.began >= 1.0 else .99
        obj.macro_key = Mock(side_effect=lambda act, *args: self.events.append(act) or False)
        table = (('Q', 20, 0, ''), ('>JX^', 20, 100, ''), ('E+', 20, 0, ''))
        self.assertTrue(obj.run_macro(table, 'test'))
        self.assertEqual([e for e in self.events if e in ('Q', 'E+')], ['Q', 'E+'])
        self.assertEqual(self.events.count('switch'), 1)
        self.assertLess(self.events.index('fill'), self.events.index('switch'))
        self.assertLess(self.events.index('switch'), self.events.index('E+'))
        self.assertTrue(self.jx.has_intro)
        self.assertTrue(obj.recovery_pending)
        self.assertEqual(obj.recovery_attempts, 1)
        self.assertGreaterEqual(self.now - self.began, 1.0)

    def test_three_second_fill_is_allowed_within_local_budget(self):
        obj = self.obj
        obj.task.get_current_con.side_effect = lambda: 1.0 if self.now - self.began >= 3.2 else .95
        self.assertTrue(obj.macro_switch(self.jx, .02, True, .1))
        self.assertGreaterEqual(self.now - self.began, 3.2)
        self.assertLess(self.now - self.began, 4.0)
        self.assertTrue(self.jx.has_intro)

    def test_single_full_flash_does_not_authorize_intro(self):
        obj = self.obj
        self.vr.is_con_full.return_value = True
        first = True

        def read_concerto():
            nonlocal first
            value = 1.0 if first else .99
            first = False
            return value

        obj.task.get_current_con.side_effect = read_concerto
        self.assertIsNone(obj.macro_switch(self.jx, .02, True, .1))
        obj.task.send_key.assert_not_called()
        self.assertFalse(self.jx.has_intro)
        self.assertEqual(self.vr.current_con, .99)

    def test_full_confirmation_streak_resets_after_low_frame(self):
        obj = self.obj
        obj.task.get_current_con.side_effect = [1.0, .99, 1.0, 1.0]
        self.assertTrue(obj.macro_switch(self.jx, .02, True, .1))
        self.assertEqual(obj.task.get_current_con.call_count, 4)
        obj.click.assert_called_once()

    def test_stale_full_cache_cannot_override_live_low_ring(self):
        obj = self.obj
        self.vr.current_con = 1.0
        self.vr.is_con_full.return_value = True
        self.assertIsNone(obj.macro_switch(self.jx, .02, True, .1))
        obj.task.send_key.assert_not_called()
        self.vr.is_con_full.assert_not_called()
        self.assertEqual(self.vr.current_con, .99)

    def test_persistent_low_ring_aborts_before_next_macro_action(self):
        obj = self.obj
        obj.macro_key = Mock(return_value=False)
        table = (('Q', 20, 0, ''), ('>JX^', 20, 100, ''), ('E+', 20, 0, ''))
        self.assertFalse(obj.run_macro(table, 'test'))
        self.assertEqual([call.args[0] for call in obj.macro_key.call_args_list], ['Q'])
        self.assertLessEqual(self.now - self.began, obj.VR_CON_HANDOFF_CAP + .1)
        self.assertEqual(obj.recovery_attempts, 0)
        self.assertEqual(self.events.count('switch'), 0)

    def test_hidden_hud_has_no_fill_or_switch_inputs(self):
        obj = self.obj
        obj.task.in_team.side_effect = None
        obj.task.in_team.return_value = (False, -1, 1)
        self.assertIsNone(obj.macro_switch(self.jx, .02, True, .1))
        obj.task.get_current_con.assert_not_called()
        obj.click.assert_not_called()
        obj.task.send_key.assert_not_called()
        self.assertLessEqual(self.now - self.began, obj.VR_CON_HANDOFF_CAP + .1)

    def test_partial_party_hud_does_not_authorize_fill(self):
        obj = self.obj
        obj.task.in_team.side_effect = None
        obj.task.in_team.return_value = (True, self.vr.index, 1)
        self.assertFalse(obj.prepare_verina_intro(self.vr))
        obj.task.get_current_con.assert_not_called()
        obj.click.assert_not_called()

    def test_wrong_character_aborts_before_fill(self):
        obj = self.obj
        self.visible_index = obj.index
        self.assertIsNone(obj.macro_switch(self.jx, .02, True, .1))
        obj.task.get_current_con.assert_not_called()
        obj.click.assert_not_called()
        obj.task.send_key.assert_not_called()

    def test_character_change_stops_further_fill(self):
        obj = self.obj
        obj.task.in_team.side_effect = [(True, self.vr.index, 3), (True, obj.index, 3)]
        self.assertFalse(obj.prepare_verina_intro(self.vr))
        obj.click.assert_called_once()
        self.assertEqual(obj.task.get_current_con.call_count, 1)

    def test_hud_loss_resets_confirmation_without_input(self):
        obj = self.obj
        frames = [(True, self.vr.index, 3), (False, -1, 1),
                  (True, self.vr.index, 3), (True, self.vr.index, 3)]
        obj.task.in_team.side_effect = frames
        obj.task.get_current_con.return_value = 1.0
        self.assertTrue(obj.prepare_verina_intro(self.vr))
        self.assertEqual(obj.task.in_team.call_count, 4)
        self.assertEqual(obj.task.get_current_con.call_count, 3)
        obj.click.assert_not_called()

    def test_transient_full_then_hidden_hud_does_not_leave_full_cache(self):
        obj = self.obj
        reads = 0

        def hud():
            nonlocal reads
            reads += 1
            return (True, self.vr.index, 3) if reads == 1 else (False, -1, 1)

        obj.task.in_team.side_effect = hud
        obj.task.get_current_con.return_value = 1.0
        self.assertFalse(obj.prepare_verina_intro(self.vr))
        self.assertNotEqual(self.vr.current_con, 1.0)
        obj.click.assert_not_called()

    def test_expired_frame_does_not_trigger_late_click_or_switch(self):
        obj = self.obj
        obj.task.next_frame.side_effect = lambda: self.advance(4.1)
        obj.task.get_current_con.return_value = 1.0
        self.assertIsNone(obj.macro_switch(self.jx, .02, True, .1))
        obj.click.assert_not_called()
        obj.task.send_key.assert_not_called()

    def test_fill_poll_releases_mouse_when_cancelled_during_tap(self):
        obj = self.obj
        del obj.click

        def stop_during_hold(seconds):
            if obj.holding_attack:
                raise RuntimeError('cancelled')
            self.advance(seconds)

        obj.macro_wait.side_effect = stop_during_hold
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            obj.prepare_verina_intro(self.vr)
        obj.task.mouse_up.assert_called_once()
        self.assertFalse(obj.holding_attack)

    def test_other_intro_sources_keep_existing_guard(self):
        obj = self.obj
        obj.cur = obj
        obj.prepare_verina_intro = Mock()
        self.assertIsNone(obj.macro_switch(self.jx, .02, True, .1))
        obj.prepare_verina_intro.assert_not_called()
        obj.task.send_key.assert_not_called()

    def test_unmarked_quickswap_does_not_start_concerto_fill(self):
        obj = self.obj
        obj.prepare_verina_intro = Mock()
        self.assertTrue(obj.macro_switch(self.jx, .02, False, .1))
        obj.prepare_verina_intro.assert_not_called()
        self.assertFalse(self.jx.has_intro)


class ResourceChecks(HandoffChecks):
    def continue_macro(self):
        self.obj.macro_key = Mock(side_effect=lambda act, *args: self.events.append(act) or False)
        return self.obj.run_macro((('Q', 20, 0, ''), ('>JX^', 20, 100, ''), ('E+', 20, 0, '')), 'replay')

    def test_ready_e_fills_and_continues_original_macro(self):
        self.vr.resonance_available.side_effect = lambda: not self.vr.send_resonance_key.called
        self.obj.task.get_current_con.side_effect = lambda: 1.0 if self.vr.record_resonance_use.called else .68
        self.assertTrue(self.continue_macro())
        self.vr.record_resonance_use.assert_called_once()
        self.assertEqual(self.events, ['Q', 'switch', 'E+'])
        self.assertEqual(self.obj.recovery_attempts, 0)
        self.assertFalse(self.obj.recovery_pending)

    def test_late_r_is_rechecked_and_animation_returns_to_same_step(self):
        obj = self.obj
        cast_at = []
        self.vr.liberation_available.side_effect = lambda: self.now - self.began >= 1.0
        self.vr.send_liberation_key.side_effect = lambda: cast_at.append(self.now)
        obj.task.in_team.side_effect = lambda: (False, -1, 1) if cast_at and self.now - cast_at[0] < 2.0 else (True, self.visible_index, 3)
        obj.task.get_current_con.side_effect = lambda: 1.0 if cast_at and self.now - cast_at[0] >= 2.0 else .68
        self.assertTrue(self.continue_macro())
        self.vr.record_liberation_use.assert_called_once()
        obj.add_freeze_duration.assert_called_once()
        self.assertEqual([e for e in self.events if e != 'fill'], ['Q', 'switch', 'E+'])
        self.assertGreaterEqual(cast_at[0] - self.began, 1.0)
        self.assertEqual(obj.recovery_attempts, 0)

    def test_confirmed_animation_preserves_remaining_resource_budget(self):
        obj = self.obj
        cast_at = []
        self.vr.liberation_available.side_effect = lambda: self.now - self.began >= 2.5
        self.vr.send_liberation_key.side_effect = lambda: cast_at.append(self.now)
        obj.task.in_team.side_effect = lambda: (False, -1, 1) if cast_at and self.now - cast_at[0] < 2.1 else (True, self.visible_index, 3)
        obj.task.get_current_con.side_effect = lambda: 1.0 if cast_at and self.now - cast_at[0] >= 2.4 else .68
        self.assertTrue(obj.macro_switch(self.jx, .02, True, .1))
        self.assertGreater(self.now - self.began, 4.0)
        self.assertLess(self.now - self.began, 7.5)

    def test_confirmed_forte_uses_one_heavy_then_rechecks_concerto(self):
        obj = self.obj
        self.vr.is_mouse_forte_full.return_value = True
        obj.task.get_current_con.side_effect = lambda: 1.0 if obj.task.mouse_up.called else .68
        self.assertTrue(obj.macro_switch(self.jx, .02, True, .1))
        obj.task.mouse_down.assert_called_once()
        obj.task.mouse_up.assert_called_once()
        self.assertFalse(obj.holding_attack)
        self.assertEqual([c.args[0] for c in obj.task.send_key.call_args_list], [2])
        self.assertGreater(self.vr.last_heavy, self.began)

    def test_remaining_forte_after_heavy_allows_one_jump_attack(self):
        obj = self.obj
        self.vr.is_mouse_forte_full.return_value = True

        def key(key, **kwargs):
            if key == 'space':
                self.events.append('jump')
            else:
                self.switch_key(key, **kwargs)

        obj.task.send_key.side_effect = key
        obj.task.get_current_con.side_effect = lambda: 1.0 if 'jump' in self.events and self.events[-1] == 'fill' else .68
        self.assertTrue(obj.macro_switch(self.jx, .02, True, .1))
        self.assertEqual(self.events.count('jump'), 1)
        obj.task.mouse_down.assert_called_once()

    def test_one_frame_forte_flash_does_not_hold_attack(self):
        self.vr.is_mouse_forte_full.side_effect = lambda: self.now - self.began < .05
        self.assertFalse(self.obj.prepare_verina_intro(self.vr))
        self.obj.task.mouse_down.assert_not_called()

    def test_recent_heavy_lockout_prevents_another_heavy(self):
        self.vr.is_mouse_forte_full.return_value = True
        self.vr.can_heavy_attack.return_value = False
        self.assertFalse(self.obj.prepare_verina_intro(self.vr))
        self.obj.task.mouse_down.assert_not_called()

    def test_no_resources_keeps_original_four_second_budget(self):
        self.assertFalse(self.obj.prepare_verina_intro(self.vr))
        self.assertLessEqual(self.now - self.began, 4.1)
        self.assertGreater(self.vr.resonance_available.call_count, 2)
        self.vr.send_resonance_key.assert_not_called()
        self.vr.send_liberation_key.assert_not_called()

    def test_stuck_ready_skills_do_not_repeat_entire_windows(self):
        self.vr.liberation_available.return_value = True
        self.vr.resonance_available.return_value = True
        self.assertFalse(self.obj.prepare_verina_intro(self.vr))
        self.assertLessEqual(self.now - self.began, 4.1)
        for send in (self.vr.send_resonance_key, self.vr.send_liberation_key):
            self.assertGreater(send.call_count, 0)
            self.assertLessEqual(send.call_count, 6)
        self.vr.record_liberation_use.assert_not_called()
        self.vr.record_resonance_use.assert_not_called()

    def test_missing_hud_does_not_check_or_send_resources(self):
        self.obj.task.in_team.side_effect = lambda: (False, -1, 1)
        self.assertFalse(self.obj.prepare_verina_intro(self.vr))
        self.vr.liberation_available.assert_not_called()
        self.vr.resonance_available.assert_not_called()
        self.vr.is_mouse_forte_full.assert_not_called()
        self.obj.click.assert_not_called()

    def test_field_change_during_skill_stops_further_inputs(self):
        self.vr.resonance_available.return_value = True
        self.vr.send_resonance_key.side_effect = lambda: setattr(self, 'visible_index', 0)
        self.assertFalse(self.obj.prepare_verina_intro(self.vr))
        self.vr.send_resonance_key.assert_called_once()
        self.vr.record_resonance_use.assert_not_called()
        self.obj.task.send_key.assert_not_called()

    def test_hidden_before_first_r_input_is_not_r_confirmation(self):
        self.vr.liberation_available.return_value = True
        self.obj.task.in_team.side_effect = lambda: (True, 2, 3) if self.now - self.began < .05 else (False, -1, 1)
        self.assertFalse(self.obj.prepare_verina_intro(self.vr))
        self.vr.send_liberation_key.assert_not_called()
        self.vr.record_liberation_use.assert_not_called()
        self.obj.add_freeze_duration.assert_not_called()

    def test_r_animation_missing_forever_is_bounded_and_has_no_extra_inputs(self):
        self.vr.liberation_available.return_value = True
        self.obj.task.in_team.side_effect = lambda: (False, -1, 1) if self.vr.send_liberation_key.called else (True, 2, 3)
        self.assertFalse(self.obj.prepare_verina_intro(self.vr))
        self.vr.send_liberation_key.assert_called_once()
        self.obj.click.assert_not_called()
        self.obj.task.send_key.assert_not_called()
        self.assertLess(self.now - self.began, 7.6)

    def test_r_ready_too_late_does_not_start_a_partial_window(self):
        self.vr.liberation_available.side_effect = lambda: self.now - self.began > 3.8
        self.assertFalse(self.obj.prepare_verina_intro(self.vr))
        self.vr.send_liberation_key.assert_not_called()

    def test_already_full_does_not_probe_or_spend_resources(self):
        self.obj.task.get_current_con.return_value = 1.0
        self.assertTrue(self.obj.prepare_verina_intro(self.vr))
        self.vr.liberation_available.assert_not_called()
        self.vr.resonance_available.assert_not_called()
        self.vr.is_mouse_forte_full.assert_not_called()

    def test_cancel_during_heavy_releases_mouse(self):
        self.vr.is_mouse_forte_full.return_value = True

        def wait(seconds):
            if self.obj.holding_attack:
                raise RuntimeError('cancelled')
            self.advance(seconds)

        self.obj.macro_wait.side_effect = wait
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.obj.prepare_verina_intro(self.vr)
        self.obj.task.mouse_up.assert_called_once()
        self.assertFalse(self.obj.holding_attack)

    def test_e_readiness_flash_without_input_is_not_recorded_as_use(self):
        self.vr.resonance_available.side_effect = lambda: self.now - self.began < .05
        self.assertFalse(self.obj.prepare_verina_intro(self.vr))
        self.vr.send_resonance_key.assert_not_called()
        self.vr.record_resonance_use.assert_not_called()

    def test_r_readiness_flash_does_not_send_an_unavailable_skill(self):
        self.vr.liberation_available.side_effect = lambda: self.now - self.began < .05
        self.assertFalse(self.obj.prepare_verina_intro(self.vr))
        self.vr.send_liberation_key.assert_not_called()
        self.vr.record_liberation_use.assert_not_called()

    def test_concerto_fills_before_first_r_input_cancels_skill_and_continues(self):
        self.vr.liberation_available.return_value = True
        self.obj.task.get_current_con.side_effect = lambda: .99 if self.now - self.began < .05 else 1.0
        self.assertTrue(self.obj.macro_switch(self.jx, .02, True, .1))
        self.vr.send_liberation_key.assert_not_called()
        self.assertTrue(self.jx.has_intro)


class ResultChecks(RecoveryChecks):
    def setUp(self):
        super().setUp()
        del self.obj.stop_on_result
        self.obj.task.in_team.return_value = (False, -1, 1)
        self.screen_text = '确认成绩'
        self.obj.task.ocr.side_effect = self.ocr

    def ocr(self, *, box, match):
        return [SimpleNamespace(name=self.screen_text)] if match.fullmatch(self.screen_text) else []

    def test_confirmed_result_avoids_failure_recovery_and_error_path(self):
        obj = self.obj
        with self.assertRaisesRegex(NotInCombatException, 'result screen confirmed'):
            obj.recover_after_failure()
        obj.logger.warning.assert_not_called()
        obj.task.raise_not_in_combat.assert_not_called()
        obj.task.reset_to_false.assert_called_once()
        self.assertFalse(obj.recovery_pending)
        self.assertEqual(obj.recovery_attempts, 0)
        obj.task.send_key.assert_not_called()

    def test_result_labels_cover_existing_simplified_traditional_english_text(self):
        for text in ('确认成绩', '確認成績', 'Confirm Results', '挑战成功', '挑戰失敗', 'Challenge Complete'):
            with self.subTest(text=text):
                self.screen_text = text
                with self.assertRaises(NotInCombatException):
                    self.obj.stop_on_result()

    def test_pause_and_generic_confirm_are_not_results(self):
        for text in ('确认', '重新挑战', '退出挑战', '暂停', 'Remaining Time', ''):
            with self.subTest(text=text):
                self.screen_text = text
                self.assertFalse(self.obj.stop_on_result())
        self.obj.task.reset_to_false.assert_not_called()

    def test_full_party_hud_prevents_result_ocr(self):
        self.obj.task.in_team.return_value = (True, 0, 3)
        self.assertFalse(self.obj.stop_on_result())
        self.obj.task.ocr.assert_not_called()

    def test_single_frame_result_flash_does_not_end_combat(self):
        self.obj.task.ocr.side_effect = [[object()], [], []]
        self.assertFalse(self.obj.stop_on_result())
        self.obj.task.reset_to_false.assert_not_called()

    def test_party_returns_after_first_match_does_not_end_combat(self):
        self.obj.task.in_team.side_effect = [(False, -1, 1), (True, 1, 3)]
        self.assertFalse(self.obj.stop_on_result())
        self.obj.task.reset_to_false.assert_not_called()

    def test_hidden_hud_without_result_keeps_failure_visible(self):
        self.screen_text = ''
        with self.assertRaisesRegex(RuntimeError, 'unknown field'):
            self.obj.recover_after_failure()
        self.assertTrue(self.obj.logger.warning.called)
        self.obj.task.raise_not_in_combat.assert_called_once()
        self.obj.task.reset_to_false.assert_not_called()

    def test_zero_e_inputs_with_hidden_hud_does_not_blame_entry_skill(self):
        self.screen_text = ''
        self.assertEqual(self.obj.jx_normal_e(self.jx, .2, 'test'), 'none')
        self.jx.send_resonance_key.assert_not_called()
        text = ' '.join(str(c) for c in self.obj.logger.info.call_args_list)
        self.assertIn('HUD unavailable', text)
        self.assertNotIn('强化E1', text)
        self.obj.task.reset_to_false.assert_not_called()

    def test_result_during_normal_e_skips_skill_failure_message(self):
        with self.assertRaises(NotInCombatException):
            self.obj.jx_normal_e(self.jx, .2, 'test')
        self.jx.send_resonance_key.assert_not_called()
        self.obj.logger.warning.assert_not_called()
        self.assertNotIn('_CD?', str(self.obj.macro_mark.call_args_list))

    def test_result_during_switch_does_not_warn_unknown_character(self):
        with self.assertRaises(NotInCombatException):
            self.obj.macro_switch(self.jx, .02, False, .1)
        self.obj.logger.warning.assert_not_called()
        self.assertIs(self.obj.cur, self.obj)

    def test_macro_ownership_and_held_mouse_release_on_result(self):
        obj = self.obj
        obj.macro_key = Mock(return_value=None)
        obj.holding_attack = True
        with self.assertRaises(NotInCombatException):
            obj.perform_opener(obj)
        self.assertFalse(obj.task.skip_combat_check)
        self.assertFalse(obj.task._ww_macro_busy)
        self.assertFalse(obj.holding_attack)
        self.assertFalse(obj.recovery_pending)
        obj.task.mouse_up.assert_called_once()
        obj.logger.warning.assert_not_called()

    def test_animation_result_does_not_warn_as_skill_failure(self):
        with self.assertRaises(NotInCombatException):
            self.obj.wait_hud_back(time_out=.2)
        self.obj.logger.warning.assert_not_called()
        self.assertNotIn('timeout', str(self.obj.macro_mark.call_args_list))

    def test_result_during_skill_cd_confirmation_skips_failure_message(self):
        with self.assertRaises(NotInCombatException):
            self.obj.press_res_until_cd(self.vr, .2, 'VeE')
        self.assertNotIn('_CD?', str(self.obj.macro_mark.call_args_list))


class ArchiveChecks(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('longfengwei_archive_builder', SOURCE / 'build.py')
        self.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.builder)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def build(self, name='team.zip'):
        return self.builder.build_archive(Path(self.temp.name) / name)

    def test_archive_contains_exact_saved_sources_and_version(self):
        with zipfile.ZipFile(self.build()) as archive:
            self.assertEqual(set(archive.namelist()), {'team.json', 'Changli.py', 'Jinhsi.py', 'Verina.py'})
            self.assertIsNone(archive.testzip())
            for name in archive.namelist():
                self.assertEqual(archive.read(name), (SOURCE / name).read_bytes())
            manifest = json.loads(archive.read('team.json'))
            self.assertEqual(manifest['version'], '1.0.4-candidate')
            self.assertEqual(manifest['author'], '楽山')

    def test_same_sources_build_byte_identical_archives(self):
        self.assertEqual(self.build('first.zip').read_bytes(), self.build('second.zip').read_bytes())

    def test_real_archive_inspector_accepts_team_and_native_class_imports(self):
        from src.char.CustomCharLoader import inspect_team_archive

        parsed = inspect_team_archive(self.build())
        self.assertEqual(parsed['team'], ('Changli', 'Jinhsi', 'Verina'))
        for name, source in parsed['codes'].items():
            with self.subTest(name=name):
                namespace = {}
                exec(compile(source, name + '.py', 'exec'), namespace)
                self.assertEqual(namespace[name].__name__, name)
                if name == 'Changli':
                    self.assertEqual(namespace[name].CODE_VERSION, '1.0.4-candidate')


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(RecoveryChecks))
    suite.addTests(loader.loadTestsFromTestCase(JinhsiDispatchChecks))
    suite.addTest(RecoveryReplayChecks('test_log_failure_then_native_intro_rejoins_macro'))
    # Derived cases share fixtures, while inherited decisions are already collected above.
    for cls in (HandoffChecks, ResourceChecks, ResultChecks):
        for name in cls.__dict__:
            if name.startswith('test_'):
                suite.addTest(cls(name))
    suite.addTests(loader.loadTestsFromTestCase(ArchiveChecks))
    return suite


if __name__ == '__main__':
    unittest.main(verbosity=2)
