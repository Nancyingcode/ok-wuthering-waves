import time

from src.char.BaseChar import SwitchPriority
from src.char.Iuno import Iuno as BuiltinIuno
from src.char.Jianxin import Jianxin as BuiltinJianxin
from src.char.ShoreKeeper import ShoreKeeper as BuiltinShoreKeeper


class Iuno(BuiltinIuno):
    VIDEO_TEAM_ID = 'BV18kW3zoEei-v1.0.3'
    NORMAL_THREE_TIME = 2.1
    MOON_SKILL_WINDOW = 1.2
    SUPPORT_TIMEOUT = 10
    BURST_TIMEOUT = 24
    PHASE_OWNER = {
        'opening_jianxin_1': 'Jianxin',
        'opening_shore': 'ShoreKeeper',
        'opening_jianxin_2': 'Jianxin',
        'shore_buff': 'ShoreKeeper',
        'jianxin_buff': 'Jianxin',
        'iuno_burst': 'Iuno',
        'loop_shore_1': 'ShoreKeeper',
        'loop_jianxin_1': 'Jianxin',
        'loop_shore_2': 'ShoreKeeper',
        'loop_jianxin_2': 'Jianxin',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.video_reset()

    def video_team(self):
        members = [self.task.has_char(cls) for cls in (BuiltinIuno, BuiltinJianxin, BuiltinShoreKeeper)]
        return self.task.use_liberation and all(
            getattr(char, 'VIDEO_TEAM_ID', None) == self.VIDEO_TEAM_ID for char in members)

    def video_reset(self):
        # reset_state 也会在战斗中重新识别队伍时调用；整轴只在真正结束战斗时复位。
        self.video_phase = 'opening_jianxin_1'
        self.video_done = set()
        self.video_phase_started = None
        self.video_burst_step = 'entry'

    def video_advance(self, phase):
        if phase not in self.PHASE_OWNER:
            raise ValueError(f'Unknown video phase: {phase}')
        self.logger.info(f'Video phase {self.video_phase} -> {phase}')
        self.video_phase = phase
        self.video_done.clear()
        self.video_phase_started = None
        self.video_burst_step = 'entry'

    def video_priority(self, name):
        if self.PHASE_OWNER[self.video_phase] == name:
            return SwitchPriority.MUST
        return SwitchPriority.NO

    def video_enter(self, actor):
        if self.PHASE_OWNER[self.video_phase] != actor.name:
            self.video_switch(actor)
            return False
        if self.video_phase_started is None:
            self.video_phase_started = time.time()
        if 'intro_checked' not in self.video_done:
            # 尤诺在 entry 步骤单独 wait_down；E/R 冷却不代表再次进入变奏或仍未落地。
            if actor.name != 'Iuno':
                actor.wait_intro(time_out=2, click=False)
            self.video_done.add('intro_checked')
        return True

    def video_remaining(self, actor, budget):
        if self.video_phase_started is None:
            return budget
        elapsed = actor.time_elapsed_accounting_for_freeze(self.video_phase_started)
        wall_elapsed = time.time() - self.video_phase_started
        return max(0, min(budget - elapsed, budget + 8 - wall_elapsed))

    def video_support_liberation(self, actor):
        # 辅助 R 等待不能挤占尤诺整轮输出；被打断时只在本次站场预算内重试。
        remaining = min(3, self.video_remaining(actor, self.SUPPORT_TIMEOUT))
        start = time.time()
        while actor.time_elapsed_accounting_for_freeze(start) < remaining and time.time() - start < remaining + 3:
            if actor.liberation_available():
                if actor.click_liberation(wait_if_cd_ready=0, click_f=False):
                    return True
            actor.click()
            actor.sleep(0.1)
            actor.task.next_frame()
        actor.logger.warning('Video support R unavailable/interrupted; prioritizing concerto handoff')
        return False

    def video_support_ready(self, actor, use_heavy=False):
        remaining = self.video_remaining(actor, self.SUPPORT_TIMEOUT)
        if self.video_fill(actor, use_heavy=use_heavy, timeout=min(6, remaining)):
            return True
        if self.video_remaining(actor, self.SUPPORT_TIMEOUT) > 0:
            return False
        actor.logger.warning('Video support budget exhausted; switching without forced intro')
        return True

    def video_handoff(self, actor, phase):
        # 先改变目标；切人未成功时下一次只重试切人，不重打上一段。
        self.video_advance(phase)
        self.video_switch(actor)

    def video_switch(self, actor):
        target_name = self.PHASE_OWNER[self.video_phase]
        protect_shore_intro = target_name == 'ShoreKeeper' and actor.is_con_full()
        if not protect_shore_intro:
            return actor.switch_next_char()

        actor.check_combat()
        previous_skip = actor.task.skip_combat_check
        actor.logger.info(f'Video switch guard begin {actor.name} -> {target_name}')
        try:
            # 原生切人仍负责 HUD、目标槽位确认及超时；仅保护这次变奏中的 sleep_check。
            actor.task.skip_combat_check = True
            return actor.switch_next_char()
        finally:
            actor.task.skip_combat_check = previous_skip
            actor.logger.info(f'Video switch guard end restored={previous_skip}')

    def video_wait(self, actor, condition, timeout, action=None):
        start = time.time()
        while actor.time_elapsed_accounting_for_freeze(start) < timeout and time.time() - start < timeout + 5:
            actor.check_combat()
            if condition():
                return True
            if action is not None:
                action()
            actor.sleep(0.05)
            actor.task.next_frame()
        return bool(condition())

    def video_normal(self, actor, duration):
        # 项目尚无普攻段数识别；持续点击时间是待实机校准的动作近似。
        start = time.time()
        while actor.time_elapsed_accounting_for_freeze(start) < duration and time.time() - start < duration + 5:
            actor.check_combat()
            actor.click()
            actor.sleep(0.1)
            actor.task.next_frame()

    def video_heavy(self, actor):
        try:
            actor.heavy_attack(duration=0.6)
        finally:
            actor.task.mouse_up()

    def video_fill(self, actor, target=1, require_liberation=False, use_heavy=False, timeout=6):
        def ready():
            full = actor.is_con_full() if target == 1 else actor.get_current_con() >= target
            return full and (not require_liberation or actor.liberation_available())

        def attack():
            if actor.resonance_available():
                actor.click_resonance(time_out=0.6, send_click=False, click_f=False)
            elif use_heavy and actor.is_mouse_forte_full():
                self.video_heavy(actor)
            else:
                actor.click()

        return self.video_wait(actor, ready, timeout, attack)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if self.video_team():
            return self.video_priority(self.name)
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def _feature(self, name):
        return bool(self.task.find_feature(name, box='box_extra_action', threshold=0.6))

    def _enter_moonbow(self):
        def recover_entry():
            # 变奏落地或受击硬直会吞掉第一次 E；等待期间继续主动恢复，不能空等图标。
            if self.resonance_available():
                self.click_resonance(time_out=0.6, send_click=False, click_f=False)
            self.click()

        if not self.video_wait(self, lambda: self._feature('iuno_jump'), 4, recover_entry):
            self.logger.warning('Video Iuno entry: jump prompt missing; keeping entry step')
            return False
        changed = self.video_wait(
            self, lambda: not self._feature('iuno_jump'), 2,
            lambda: self.task.jump(after_sleep=0.1))
        if changed:
            self.sleep(0.5)
        else:
            self.logger.warning('Video Iuno entry: jump interrupted; keeping entry step')
        return changed

    def _moon_skill(self):
        start = time.time()
        attempts = 0
        attacks = 0
        previous_ready = self.resonance_available()
        confirmed = False
        next_action = 'skill'
        # 实测冷却 OCR 可在 E 已施放后漏报，且月弓 E 不能按普通 E 冷却阻塞整轴。
        # 参考内置尤诺交替 E/A；状态变化只作诊断，不能让角色反复只按 E 停手。
        while self.time_elapsed_accounting_for_freeze(start) < self.MOON_SKILL_WINDOW and time.time() - start < 3:
            self.check_combat()
            available = self.resonance_available()
            if attempts and previous_ready and not available:
                confirmed = True
                self.record_resonance_use()
                break
            if next_action == 'skill':
                self.send_resonance_key()
                attempts += 1
                next_action = 'normal'
            else:
                self.click()
                attacks += 1
                next_action = 'skill'
            previous_ready = available
            self.sleep(0.1)
            self.task.next_frame()
        # 即使很快观察到冷却，也以普攻接续，不用无输入的固定后摇等待。
        self.video_normal(self, 0.2)
        self.logger.info(
            f'Video Iuno moon E window step={self.video_burst_step} '
            f'attempts={attempts} normal_inputs={attacks} observed_cd_change={confirmed}; continuing rotation')
        if not confirmed:
            self._moon_skill_diagnostic(attempts)
        return confirmed

    def _moon_skill_diagnostic(self, attempts):
        self.logger.info(
            f'Video Iuno moon E unconfirmed step={self.video_burst_step} attempts={attempts}; not blocking output')
        marker = f'e_diagnostic_{self.video_burst_step}'
        if marker not in self.video_done:
            # 每轮每个 E 步骤最多一张原图，供区分受击、动作未结束和识别问题。
            self.video_done.add(marker)
            self.task.screenshot(
                f'jianyou_shou/v1_0_3_{time.time_ns()}_{self.video_burst_step}_unconfirmed_window')

    def _finish_heavy(self):
        if not self.video_wait(self, lambda: self._feature('iuno_heavy'), 2, self.click):
            return False
        self.video_heavy(self)
        consumed = self.video_wait(self, lambda: not self._feature('iuno_heavy'), 1.5)
        if consumed:
            self.last_heavy = time.time()
        return consumed

    def _burst(self):
        if self.video_burst_step == 'entry':
            if 'entry_started' not in self.video_done:
                self.wait_down(click=False)
                self.click_resonance(time_out=0.6, send_click=False, click_f=False)
                self.click_echo(time_out=0)
                self.video_done.add('entry_started')
            if not self._enter_moonbow():
                return False
            self.video_burst_step = 'first_normals'
        if self.video_burst_step == 'first_normals':
            self.video_normal(self, self.NORMAL_THREE_TIME)
            self.video_burst_step = 'first_skill'
        if self.video_burst_step == 'first_skill':
            self._moon_skill()
            self.video_burst_step = 'liberation'
        if self.video_burst_step == 'liberation':
            if not self.click_liberation(wait_if_cd_ready=0, click_f=False):
                self.video_normal(self, 0.3)
                return False
            self.video_burst_step = 'second_normals'
        if self.video_burst_step == 'second_normals':
            self.video_normal(self, self.NORMAL_THREE_TIME)
            self.video_burst_step = 'second_skill'
        if self.video_burst_step == 'second_skill':
            self._moon_skill()
            self.video_burst_step = 'extra_normals'
        if self.video_burst_step == 'extra_normals':
            # 重击被打断时从 heavy 恢复，不重放已完成的三套普攻或插入第三次 E。
            self.video_normal(self, self.NORMAL_THREE_TIME)
            self.video_burst_step = 'heavy'
        if self.video_burst_step == 'heavy':
            if not self._finish_heavy():
                return False
            self.video_burst_step = 'outro'
        return self.video_burst_step == 'outro'

    def do_perform(self):
        if not self.video_team():
            return super().do_perform()
        if not self.video_enter(self):
            return
        completed = self._burst()
        if not completed:
            remaining = self.video_remaining(self, self.BURST_TIMEOUT)
            self.logger.warning(f'Video Iuno retry step={self.video_burst_step} remaining={remaining:.1f}s')
            if remaining > 0:
                self.sleep(0.1)
                return
            self.logger.warning('Video Iuno recovery exhausted; returning to support without forced intro')
        else:
            # 重击协奏可能稍后入账；这里不再补 E，避免改写视频末尾连段。
            self.video_wait(self, self.is_con_full, 2)
        self.video_handoff(self, 'loop_shore_1')

    def on_combat_end(self, chars):
        self.video_reset()
        if not self.video_team():
            return super().on_combat_end(chars)
