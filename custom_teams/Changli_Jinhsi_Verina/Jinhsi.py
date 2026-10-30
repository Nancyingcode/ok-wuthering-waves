from src.char.BaseChar import SwitchPriority
from src.char.Jinhsi import Jinhsi as NativeJinhsi


class Jinhsi(NativeJinhsi):
    """龙凤维(今汐/长离/维里奈)里的今汐。非本队时整套走原版。

    【她不驱动任何一段轴】: 两条轴的入口都是长离, 宏体在同目录的 Changli.py 里 ——
    自定义角色文件互相 import 不到, 但同一个 task 下用 task.chars 拿得到彼此的实例。
    宏在跑的时候她全程由宏按数字键切进切出; 宏没覆盖的那一格(变奏入场一直打到龙喷)也写在
    Changli.py 的 seg_jinhsi_spout 里。这里一个动作都不用写。

    走到 do_perform 说明宏散了(切人失败/脱战重进): 启动轴待跑就立刻让场; 否则打一轮
    内置轮转补资源；恢复期间只有一次新的变奏入场才接回宏，避免从未完成的技能阶段重启。
    """

    @property
    def is_in_longfeng(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Changli', 'Verina'}:
                team_members += 1
        return team_members == 2

    def macro(self):
        """持宏的长离。【按能力找】: 长离那份自定义没启用时返回 None, 老老实实走原版。"""
        for char in self.task.chars:
            if char is None or char is self:
                continue
            if hasattr(char, 'perform_opener') and hasattr(char, 'quick_switch'):
                return char
        return None

    def do_perform(self):
        if not self.is_in_longfeng:
            return super().do_perform()
        macro = self.macro()
        if macro is None:
            self.logger.warning('longfeng: Changli custom code is off, falling back to the native rotation')
            return super().do_perform()
        if macro.opener_pending():
            self.logger.info('jinhsi: opener pending -> handing the field to Changli right away')
            return self.switch_next_char()
        if hasattr(macro, 'can_resume_from_jinhsi') and macro.can_resume_from_jinhsi(self):
            return macro.resume_from_jinhsi(self)
        self.logger.info('jinhsi do_perform in longfeng team (macro recovery) -> native rotation')
        return super().do_perform()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时返回 NO。【要写在 super() 之前】: 原版在有变奏/凌霄时返回 MUST。"""
        if self.is_in_longfeng:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
