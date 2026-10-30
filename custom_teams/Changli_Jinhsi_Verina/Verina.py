from src.char.BaseChar import SwitchPriority
from src.char.Verina import Verina as NativeVerina


class Verina(NativeVerina):
    """龙凤维(今汐/长离/维里奈)里的维里奈。非本队时整套走原版。

    【她不驱动任何一段轴】: 宏体在同目录的 Changli.py 里, 她那一格(AAA -> E -> Q -> R -> 跳 -> 空中 AAA)
    是两条宏表的第 3~12 格, 启动宏和循环宏一模一样。

    走到 do_perform 说明宏散了: 启动轴待跑就立刻让场 —— "战斗前后切换到治疗角色"打开时,
    进战斗第一件事就是把她这个奶换上来, 她每多打一下启动宏开头长离那个 E 就晚一下; 否则打一轮
    原版, 打完框架会把场子交给长离, 宏接上。
    """

    @property
    def is_in_longfeng(self) -> bool:
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Changli', 'Jinhsi'}:
                team_members += 1
        return team_members == 2

    def macro(self):
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
            self.logger.info('verina: opener pending -> handing the field to Changli right away')
            return self.switch_next_char()
        self.logger.info('verina do_perform in longfeng team (macro fell apart) -> native rotation')
        return super().do_perform()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时返回 NO —— 框架的奶妈分支本来就偏向先上她。"""
        if self.is_in_longfeng:
            macro = self.macro()
            if macro is not None and macro.opener_pending():
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)
