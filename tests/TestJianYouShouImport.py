"""Offline checks for the saved import team; no game or user settings are used."""
import ast
from contextlib import contextmanager
import itertools
from pathlib import Path
import shutil
import sys
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'custom_teams/JianYouShou_Video'


def source_module(name, path):
    module = ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding='utf-8'), str(path), 'exec'), module.__dict__)
    return module


def load_team():
    config = SimpleNamespace(config_folder='')
    ok = ModuleType('ok')
    ok.Config = config
    ok.Logger = SimpleNamespace(get_logger=lambda name: Mock())
    util = ModuleType('ok.util')
    util_config = ModuleType('ok.util.config')
    util_config.Config = config
    runtime = {'ok': ok, 'ok.util': util, 'ok.util.config': util_config,
               'cv2': ModuleType('cv2'), 'numpy': ModuleType('numpy')}
    # 只替换外部运行库；实际基类、角色源码和加载器在局部导入环境中执行。
    with patch.dict(sys.modules, runtime), patch.object(sys, 'dont_write_bytecode', True):
        base = source_module('src.char.BaseChar', ROOT / 'src/char/BaseChar.py')
        sys.modules[base.__name__] = base
        runtime[base.__name__] = base
        builtins = {}
        for name in ('Iuno', 'Jianxin', 'ShoreKeeper'):
            module = source_module(f'src.char.{name}', ROOT / f'src/char/{name}.py')
            sys.modules[module.__name__] = module
            runtime[module.__name__] = module
            builtins[name] = getattr(module, name)
        loader = source_module('jianyoushou_loader', ROOT / 'src/char/CustomCharLoader.py')
        custom = {name: loader._load_class_from_file(cls, SOURCE / f'{name}.py', f'jianyoushou_{name}')
                  for name, cls in builtins.items()}
    return SimpleNamespace(base=base, builtins=builtins, custom=custom, loader=loader,
                           config=config, runtime=runtime)


ENV = load_team()
BUILD = source_module('jianyoushou_build', SOURCE / 'build.py')
combat_tree = ast.parse((ROOT / 'src/task/BaseCombatTask.py').read_text(encoding='utf-8'))
combat = next(node for node in combat_tree.body if isinstance(node, ast.ClassDef) and node.name == 'BaseCombatTask')
combat_globals = {'time': time, 'SwitchPriority': ENV.base.SwitchPriority, 'logger': Mock(),
                  'planned_switch_target': lambda task, char: None,
                  'check_switch_timeout': lambda task: None}
for method_name in ('_choose_switch_target', '_apply_intro_flags', 'switch_next_char'):
    method = next(node for node in combat.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    exec(compile(ast.Module(body=[method], type_ignores=[]), 'real_switch_lifecycle', 'exec'), combat_globals)


@contextmanager
def scratch_folder():
    parent = (ROOT / 'build/jianyoushou-tests').resolve()
    folder = parent / uuid.uuid4().hex
    folder.mkdir(parents=True)
    try:
        yield folder
    finally:
        if not folder.resolve().is_relative_to(parent):
            raise ValueError('Refusing to clean outside the test directory')
        shutil.rmtree(folder)


def inherited_mkdtemp(suffix=None, prefix=None, dir=None):
    # Windows 沙箱不能访问 Python 0700 临时目录；仅隔离配置下的测试目录继承父 ACL。
    folder = Path(dir) / f'{prefix or "test-"}{uuid.uuid4().hex}{suffix or ""}'
    allowed = (ROOT / 'build/jianyoushou-tests').resolve()
    if not folder.resolve().is_relative_to(allowed):
        raise ValueError('Temporary settings must remain inside the test directory')
    folder.mkdir()
    return str(folder)


class FakeTask:
    _choose_switch_target = combat_globals['_choose_switch_target']
    _apply_intro_flags = combat_globals['_apply_intro_flags']
    switch_next_char = combat_globals['switch_next_char']

    def __init__(self):
        self.chars = []
        self.char_config = {}
        self.use_liberation = True
        self.skip_combat_check = False
        self.in_liberation = False
        self.now = 100.0
        self.inputs = []
        self.screenshots = []
        self.concerto = 0
        self.on_sleep = None
        self.switch_started = None
        self.animation = 0.5
        self.switch_char_time_out = 1
        self.update_lib_portrait_icon = Mock()
        self.add_freeze_duration = Mock()
        self.log_debug = Mock()
        self._log_switch_candidate = Mock()

    def has_char(self, cls):
        return next((char for char in self.chars if isinstance(char, cls)), None)

    def sleep(self, seconds):
        self.now += max(0.01, seconds)
        if self.on_sleep is not None:
            self.on_sleep()
        self.check_combat()

    def next_frame(self):
        self.sleep(0.01)

    def time_elapsed_accounting_for_freeze(self, start, intro_motion_freeze=False):
        return self.now - start

    def click(self, **kwargs):
        self.inputs.append(('A', self.now))

    def mouse_down(self):
        self.inputs.append(('down', self.now))

    def mouse_up(self):
        self.inputs.append(('up', self.now))

    def screenshot(self, name):
        self.screenshots.append(name)

    def is_con_full(self):
        return self.concerto == 1

    def get_current_con(self):
        return self.concerto

    def in_team(self):
        if self.switch_started is None:
            return True, 0, 3
        if self.now - self.switch_started < self.animation:
            return False, 0, 3
        return True, 2, 3

    def send_key(self, key):
        if self.switch_started is None:
            self.switch_started = self.now

    def check_combat(self):
        if not self.in_team()[0] and not self.skip_combat_check:
            raise RuntimeError('false combat exit during intro')

    def raise_not_in_combat(self, reason):
        raise RuntimeError(reason)

    def _log_switch_choice(self, current, target, has_intro, reason):
        return target

    def _oldest_switch_target(self, candidates):
        return min(candidates, key=lambda char: char.index)

    def _target_has_switch_cd(self, char):
        return False

    def _choose_switch_target_by_buff_time(self, current, candidates):
        if len(candidates) != 1:
            raise AssertionError('The video phase must leave one eligible switch target')
        return candidates[0]


def make_team(order=('Iuno', 'Jianxin', 'ShoreKeeper')):
    task = FakeTask()
    roles = {'Iuno': ENV.base.CharType.SUB_DPS, 'Jianxin': ENV.base.CharType.HEALER,
             'ShoreKeeper': ENV.base.CharType.HEALER}
    for index, name in enumerate(order):
        char = ENV.custom[name](task, index, char_name=f'char_{name.lower()}', char_type=roles[name])
        char.wait_intro = Mock()
        char.wait_down = Mock()
        char.resonance_available = Mock(return_value=False)
        char.liberation_available = Mock(return_value=True)
        char.is_mouse_forte_full = Mock(return_value=False)
        char.switch_next_char = Mock()
        char.send_resonance_key = lambda: task.inputs.append(('E', task.now))
        task.chars.append(char)
    return task, {char.name: char for char in task.chars}


class TestJianYouShouImport(unittest.TestCase):
    def test_reproducible_archive_and_real_loader_roundtrip(self):
        with scratch_folder() as directory, patch.dict(sys.modules, ENV.runtime), patch.object(
                sys, 'dont_write_bytecode', True), patch.object(ENV.loader, '_registered_char_classes',
                return_value=ENV.builtins), patch.object(ENV.config, 'config_folder', str(directory)), patch(
                'tempfile.mkdtemp', side_effect=inherited_mkdtemp):
            first = BUILD.build_archive(directory / 'first.zip')
            second = BUILD.build_archive(directory / 'second.zip')
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(set(archive.namelist()), set(BUILD.MEMBERS))
                for name in archive.namelist():
                    self.assertEqual(archive.read(name), (SOURCE / name).read_bytes())
            info = ENV.loader.inspect_team_archive(first)
            self.assertEqual(info['manifest']['version'], '1.0.3')
            self.assertEqual(info['team'], ('Iuno', 'Jianxin', 'ShoreKeeper'))
            ENV.loader.import_custom_team(info)
            self.assertEqual(ENV.loader.get_team_code_mode(info['team']), 'import')
            for name, builtin in ENV.builtins.items():
                self.assertTrue(issubclass(ENV.loader.load_team_char_class(builtin, info['team']), builtin))
            exported = ENV.loader.export_custom_team(info['team'], directory, 'Test', 'Video', 'Codex', '1')
            self.assertEqual(ENV.loader.inspect_team_archive(exported)['codes'], info['codes'])
        ENV.loader.clear_team_char_cache()

    def test_all_slot_orders_phases_intro_flags_and_healer_lockouts(self):
        for order in itertools.permutations(ENV.builtins):
            task, chars = make_team(order)
            for char in task.chars:
                char.last_full_con_switch_time = task.now - 1
            with patch('time.time', side_effect=lambda: task.now):
                for phase, owner in chars['Iuno'].PHASE_OWNER.items():
                    chars['Iuno'].video_advance(phase)
                    for current in task.chars:
                        if current.name != owner:
                            for intro in (False, True):
                                self.assertIs(task._choose_switch_target(current, intro), chars[owner])

    def test_e_window_keeps_attacking_when_cd_ocr_never_changes(self):
        for available in (False, True):
            task, chars = make_team()
            actor = chars['Iuno']
            actor.resonance_available = Mock(return_value=available)
            with patch('time.time', side_effect=lambda: task.now):
                self.assertFalse(actor._moon_skill())
            keys = [key for key, _ in task.inputs]
            self.assertEqual(keys[:6], ['E', 'A', 'E', 'A', 'E', 'A'])
            self.assertLess(task.now, 102)
            self.assertEqual(actor.last_res, -1)

    def test_unconfirmed_e_does_not_block_liberation_extra_normals_or_heavy(self):
        for available in (False, True):
            task, chars = make_team()
            actor = chars['Iuno']
            actor.resonance_available = Mock(return_value=available)
            actor.click_resonance = Mock(return_value=(True, 0, False))
            actor.click_echo = Mock()
            actor._enter_moonbow = Mock(return_value=True)
            actor.click_liberation = Mock(return_value=True)
            actor._finish_heavy = Mock(return_value=True)
            with patch('time.time', side_effect=lambda: task.now):
                self.assertTrue(actor._burst())
            actor.click_liberation.assert_called_once()
            actor._finish_heavy.assert_called_once()
            self.assertEqual(actor.video_burst_step, 'outro')
            self.assertLess(task.now, 111)

    def test_transient_cd_is_recorded_but_unconfirmed_diagnostics_are_deduplicated(self):
        task, chars = make_team()
        actor = chars['Iuno']
        actor.resonance_available = lambda: not (100.04 <= task.now < 100.2)
        with patch('time.time', side_effect=lambda: task.now):
            self.assertTrue(actor._moon_skill())
            actor.resonance_available = Mock(return_value=True)
            actor._moon_skill()
            actor._moon_skill()
        self.assertGreater(actor.last_res, 100)
        self.assertEqual(len(task.screenshots), 1)

    def test_retry_does_not_recheck_iuno_landing(self):
        task, chars = make_team()
        actor = chars['Iuno']
        actor.video_advance('iuno_burst')
        actor.has_intro = True
        actor.click_resonance = Mock()
        actor.click_echo = Mock()
        actor._enter_moonbow = Mock(return_value=True)
        actor._moon_skill = Mock(return_value=False)
        actor.click_liberation = Mock(return_value=False)
        with patch('time.time', side_effect=lambda: task.now):
            actor.do_perform()
            actor.do_perform()
        actor.wait_down.assert_called_once_with(click=False)
        actor.wait_intro.assert_not_called()
        self.assertTrue(actor.has_intro)

    def test_burst_order_preserves_extra_normals_without_third_moon_e(self):
        task, chars = make_team()
        actor = chars['Iuno']
        actions = []
        actor.click_resonance = lambda **kw: actions.append('entry_E')
        actor.click_echo = lambda **kw: actions.append('Q')
        actor._enter_moonbow = lambda: actions.append('jump') or True
        actor.video_normal = lambda char, duration: actions.append('3A')
        actor._moon_skill = lambda: actions.append('E') or False
        actor.click_liberation = lambda **kw: actions.append('R') or True
        actor._finish_heavy = lambda: actions.append('heavy') or True
        self.assertTrue(actor._burst())
        self.assertEqual(actions, ['entry_E', 'Q', 'jump', '3A', 'E', 'R', '3A', 'E', '3A', 'heavy'])

    def native_switch_team(self):
        task, chars = make_team()
        actor = chars['Iuno']
        actor.video_advance('loop_shore_1')
        actor.current_con = 1
        actor.is_current_char = True
        actor.switch_next_char = ENV.base.BaseChar.switch_next_char.__get__(actor)
        actor.is_forte_full = Mock()
        actor.f_break = Mock()
        actor.use_tool_box = Mock()
        return task, chars

    def test_unguarded_native_switch_reproduces_intro_sleep_failure(self):
        task, chars = self.native_switch_team()
        with patch.dict(sys.modules, ENV.runtime), patch('time.time', side_effect=lambda: task.now), self.assertRaisesRegex(
                RuntimeError, 'false combat exit'):
            chars['Iuno'].switch_next_char()

    def test_guarded_native_switch_confirms_hud_and_restores_combat_check(self):
        task, chars = self.native_switch_team()
        with patch.dict(sys.modules, ENV.runtime), patch('time.time', side_effect=lambda: task.now):
            chars['Iuno'].video_switch(chars['Iuno'])
        self.assertTrue(chars['ShoreKeeper'].is_current_char)
        self.assertTrue(chars['ShoreKeeper'].has_intro)
        self.assertFalse(task.skip_combat_check)

    def test_native_switch_timeout_still_fails_and_restores_check(self):
        task, chars = self.native_switch_team()
        task.animation = 100
        with patch.dict(sys.modules, ENV.runtime), patch('time.time', side_effect=lambda: task.now), self.assertRaisesRegex(
                RuntimeError, 'switch too long failed'):
            chars['Iuno'].video_switch(chars['Iuno'])
        self.assertFalse(task.skip_combat_check)
        self.assertLess(task.now, 102)

    def test_cancelled_switch_restores_both_previous_flag_values(self):
        for previous in (False, True):
            task, chars = make_team()
            actor = chars['Iuno']
            actor.video_advance('loop_shore_1')
            actor.current_con = 1
            task.skip_combat_check = previous
            actor.switch_next_char = Mock(side_effect=RuntimeError('cancelled'))
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                actor.video_switch(actor)
            self.assertEqual(task.skip_combat_check, previous)

    def test_cancelled_heavy_releases_mouse(self):
        task, chars = make_team()
        task.on_sleep = Mock(side_effect=RuntimeError('cancelled'))
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            chars['Iuno'].video_heavy(chars['Iuno'])
        self.assertEqual([key for key, _ in task.inputs], ['down', 'up'])

    def test_combat_refresh_preserves_phase_and_combat_end_resets_from_any_actor(self):
        for name in ENV.builtins:
            task, chars = make_team()
            actor = chars['Iuno']
            actor.video_advance('jianxin_buff')
            actor.video_done.add('liberation_attempted')
            for char in task.chars:
                char.reset_state()
            self.assertEqual(actor.video_phase, 'jianxin_buff')
            self.assertEqual(actor.video_done, {'liberation_attempted'})
            chars[name].on_combat_end(task.chars)
            self.assertEqual(actor.video_phase, 'opening_jianxin_1')
            self.assertEqual(actor.video_done, set())

    def test_missing_member_or_disabled_liberation_falls_back_to_builtin(self):
        for partial in (False, True):
            task, chars = make_team()
            if partial:
                chars['ShoreKeeper'].VIDEO_TEAM_ID = 'other'
            else:
                task.use_liberation = False
            for name, native in ENV.builtins.items():
                with patch.object(native, 'do_perform') as fallback:
                    chars[name].do_perform()
                    fallback.assert_called_once_with()


if __name__ == '__main__':
    unittest.main(verbosity=2)
