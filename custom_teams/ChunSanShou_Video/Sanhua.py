from src.char.Sanhua import Sanhua as BuiltinSanhua
from src.char.BaseChar import SwitchPriority

PACKAGE_ID = 'chunsanshou-v1.2.0-target-wait-budget'


class TeamMember:
    _chunsanshou_package = PACKAGE_ID

    def _team_members_match(self):
        chars = getattr(self.task, 'chars', ())
        names = {char.name for char in chars if char is not None}
        same_package = all(getattr(type(char), '_chunsanshou_package', None) == self._chunsanshou_package
                           for char in chars)
        return len(chars) == 3 and names == {'Camellya', 'Sanhua', 'ShoreKeeper'} and same_package

    def _active_team_rotation(self):
        rotation = getattr(self.task, '_chunsanshou_import_rotation', None)
        if rotation is None:
            return None
        same_members = len(self.task.chars) == 3 and all(
            current is previous for current, previous in zip(self.task.chars, rotation.members)
        )
        if not same_members or not self._team_members_match():
            self.task._chunsanshou_import_rotation = None
            return None
        return rotation

    def do_perform(self):
        if not self._team_members_match():
            self.task._chunsanshou_import_rotation = None
            return super().do_perform()
        rotation = self._active_team_rotation()
        if rotation is None:
            # 导入器分别加载三个文件，由椿的类提供唯一一份共享状态机。
            provider = next(char for char in self.task.chars if char.name == 'Camellya')
            rotation = provider._rotation_class(self.task)
            self.task._chunsanshou_import_rotation = rotation
        rotation.perform(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        rotation = self._active_team_rotation()
        if rotation is not None and rotation.switch_from is current_char and rotation.switch_to is not None:
            rotation.check_switch_timeout()
            return SwitchPriority.MUST if rotation.switch_to is self else SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def healer_full_con_switch_locked(self):
        rotation = self._active_team_rotation()
        if rotation is not None and rotation.switch_to is self:
            # 仅计划中的短回切豁免奶位锁，普通换人仍使用原规则。
            return False
        return super().healer_full_con_switch_locked()

    def wait_switch(self):
        rotation = self._active_team_rotation()
        if rotation is not None:
            rotation.check_switch_timeout()
        return super().wait_switch()

    def on_combat_end(self, chars):
        self.task._chunsanshou_import_rotation = None
        return super().on_combat_end(chars)


class Sanhua(TeamMember, BuiltinSanhua):
    pass
