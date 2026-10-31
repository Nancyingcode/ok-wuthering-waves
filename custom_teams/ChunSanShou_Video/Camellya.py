from src.char.Camellya import Camellya as BuiltinCamellya
from src.char.BaseChar import SwitchPriority

import time
from enum import StrEnum


class Phase(StrEnum):
    SHORE_START = 'shore_start'
    CAMELLYA_SEED = 'camellya_seed'
    SANHUA_PREPARE = 'sanhua_prepare'
    SHORE_CONCERTO = 'shore_concerto'
    CAMELLYA_WARMUP = 'camellya_warmup'
    SHORE_RECHARGE = 'shore_recharge'
    SANHUA_OUTRO = 'sanhua_outro'
    CAMELLYA_BURST = 'camellya_burst'
    CAMELLYA_RETURN = 'camellya_return'


PHASE_ACTOR = {
    Phase.SHORE_START: 'ShoreKeeper',
    Phase.CAMELLYA_SEED: 'Camellya',
    Phase.SANHUA_PREPARE: 'Sanhua',
    Phase.SHORE_CONCERTO: 'ShoreKeeper',
    Phase.CAMELLYA_WARMUP: 'Camellya',
    Phase.SHORE_RECHARGE: 'ShoreKeeper',
    Phase.SANHUA_OUTRO: 'Sanhua',
    Phase.CAMELLYA_BURST: 'Camellya',
    Phase.CAMELLYA_RETURN: 'Camellya',
}


class RotationTimeout(Exception):
    pass


class ConcertoTimeout(RotationTimeout):
    """A concerto handoff failed; kind separates missing resource from HUD disagreement."""

    def __init__(self, phase, value, kind, actor):
        super().__init__(f'{phase}: concerto not ready concerto={value:.3f} kind={kind}')
        self.value = value
        self.kind = kind
        self.actor = actor


class CamellyaTeamVision:
    @staticmethod
    def skill_pixels(crop):
        import cv2
        import numpy as np

        unknown = {'ready': False, 'state': 'unknown', 'ring': 0.0, 'white': 0.0}
        if crop is None or crop.size == 0:
            return unknown
        height, width = crop.shape[:2]
        if min(height, width) < 32:
            return unknown
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        y, x = np.ogrid[:height, :width]
        radius = np.sqrt((x - width / 2) ** 2 + (y - height / 2) ** 2) / height
        ring_mask = (radius >= 0.37) & (radius <= 0.49)
        core = radius < 0.30
        pink = (hsv[:, :, 0] >= 140) & (hsv[:, :, 0] <= 175) & (hsv[:, :, 1] >= 100) & (hsv[:, :, 2] >= 100)
        white = (hsv[:, :, 1] < 65) & (hsv[:, :, 2] >= 205)
        ring_ratio = float(pink[ring_mask].mean())
        white_ratio = float(white[core].mean())
        quadrants = [(x >= width / 2) & (y >= height / 2), (x < width / 2) & (y >= height / 2),
                     (x < width / 2) & (y < height / 2), (x >= width / 2) & (y < height / 2)]
        # 多方向环色排除局部特效；图标亮度排除粉环仍在、技能已变灰的画面。
        ring_visible = ring_ratio >= 0.55 and all(float(pink[ring_mask & q].mean()) >= 0.35 for q in quadrants)
        icon_lit = white_ratio >= 0.12
        if not ring_visible:
            state = 'no_bloom_ring'
        elif not icon_lit:
            state = 'icon_dark'
        else:
            state = 'ready'
        return {'ready': state == 'ready', 'state': state, 'ring': ring_ratio, 'white': white_ratio}

    @staticmethod
    def skill(char):
        box = char.task.box_of_screen_scaled(2560, 1440, 2110, 1236, 2217, 1343, hcenter=True)
        return CamellyaTeamVision.skill_pixels(box.crop_frame(char.task.frame))

    @staticmethod
    def forte_pixels(crop):
        import cv2
        import numpy as np

        unknown = {'value': -1.0, 'state': 'unknown', 'segments': 0}
        if crop is None or crop.size == 0:
            return unknown
        height, width = crop.shape[:2]
        if height < 4 or width < 100:
            return unknown
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        pink = (hsv[:, :, 0] >= 140) & (hsv[:, :, 1] >= 100) & (hsv[:, :, 2] >= 140)
        neutral = (hsv[:, :, 1] < 70) & (hsv[:, :, 2] >= 100)
        minimum = max(2, round(width * 0.006))
        maximum = max(minimum + 1, round(width * 0.025))

        def segments(mask):
            columns = np.flatnonzero(mask.mean(axis=0) >= 0.35)
            groups = np.split(columns, np.where(np.diff(columns) > 1)[0] + 1)
            return [(int(group[0]), int(group[-1])) for group in groups if minimum <= len(group) <= maximum]

        filled = segments(pink)
        empty = segments(neutral)
        visible = filled + empty
        # 先确认足够的回路刻度；判空还需看到未充能刻度，不能把黑屏或遮挡判成零回路。
        scale_visible = len(visible) >= 8 and max((end for _, end in visible), default=0) >= width * 0.8
        filled_prefix_visible = len(filled) >= 8 and filled[-1][1] >= width * 0.15
        hud_visible = scale_visible or filled_prefix_visible
        if not hud_visible:
            return unknown
        if not filled:
            if pink.any():
                return unknown
            return {'value': 0.0, 'state': 'empty', 'segments': 0}
        continuous_fill = filled[0][0] <= width * 0.02 and all(
            right[0] - left[1] <= width * 0.035 for left, right in zip(filled, filled[1:]))
        if not continuous_fill:
            return unknown
        return {'value': min(1.0, (filled[-1][1] + 1) / width), 'state': 'filled', 'segments': len(filled)}

    @staticmethod
    def forte(char):
        box = char.task.box_of_screen_scaled(3840, 2160, 1630, 1994, 2176, 2012, hcenter=True)
        return CamellyaTeamVision.forte_pixels(box.crop_frame(char.task.frame))

    @staticmethod
    def budding_forte_pixels(crop):
        import numpy as np

        unknown = {'value': -1.0, 'state': 'unknown', 'segments': 0, 'visible_ticks': 0}
        if crop is None or crop.size == 0:
            return unknown
        height, width = crop.shape[:2]
        if height < 4 or width < 100:
            return unknown
        blue, green, red = np.moveaxis(crop.astype(np.int16), -1, 0)
        lit = ((red >= 245) & (green >= 100) & (blue >= 140) & (red - green >= 15)
               & (red - blue >= 10) & (blue - green >= -10) & (blue - green <= 55))
        radius = max(1, round(2 * width / 546))

        def sample(channel, position):
            center = min(width - 1, max(0, round(position)))
            return float(channel[:, max(0, center - radius):min(width, center + radius + 1)].mean())

        observations = []
        # 开花白粉色刻度易被整片辉光连成一片；先用刻度与两侧间隙的亮度差确认 HUD。
        # 这些位置来自 3840 截图的固定回路区域，按区域宽度缩放，不以背景粉色估算资源。
        for index in range(40):
            center = (14.5 + 13.5 * index) * width / 546
            gap = 6 * width / 546
            contrast = sample(green, center) - max(sample(green, center - gap), sample(green, center + gap))
            observations.append((contrast >= 10, sample(lit, center) >= 0.3))
        visible = sum(found for found, _ in observations)
        if visible < 28:
            return unknown
        prefix = 0
        for found, filled in observations:
            if not found:
                return {**unknown, 'visible_ticks': visible}
            if not filled:
                break
            prefix += 1
        if prefix == 0:
            if any(filled for _, filled in observations):
                return {**unknown, 'visible_ticks': visible}
            return {'value': 0.0, 'state': 'empty', 'segments': 0, 'visible_ticks': visible}
        # 只取左侧连续充能刻度，后方孤立的发光特效不计入剩余回路。
        value = (14.5 + 13.5 * (prefix - 1) + 3) / 546
        return {'value': min(1.0, value), 'state': 'filled', 'segments': prefix, 'visible_ticks': visible}

    @staticmethod
    def budding_forte(char):
        box = char.task.box_of_screen_scaled(3840, 2160, 1630, 1994, 2176, 2012, hcenter=True)
        return CamellyaTeamVision.budding_forte_pixels(box.crop_frame(char.task.frame))


class Rotation:
    PHASE_TIMEOUT = 24
    SWITCH_TIMEOUT = 6
    SWITCH_RETRY_TIMEOUT = 1.5
    BLOOM_OUTPUT_TIMEOUT = 8
    BLOOM_TAIL_GRACE = 2
    BLOOM_TAIL_RECHECK = 0.4
    BLOOM_WALL_TIMEOUT = 16
    BLOOM_ENTRY_RECHECK = 0.4
    BLOOM_ENDED_FORTE = 0.05
    BLOOM_COMPLETE_FORTE = 0.06
    CONCERTO_TOPUP_TIMEOUT = 2
    CONCERTO_CONFLICT_SETTLE = 0.25
    CONCERTO_CONFLICT_VALUE = 0.985
    BLOOM_PREPARE_TIMEOUT = 5
    LIBERATION_SHORT_CD = BLOOM_PREPARE_TIMEOUT
    LIBERATION_RECHECK_BUDGET = 0.5
    BLOOM_TARGET_RECOVERY_TIMEOUT = 0.75
    BLOOM_FEEDBACK_TIMEOUT = 1.2

    def __init__(self, task):
        self.task = task
        self.members = tuple(task.chars)
        self.chars = {char.name: char for char in self.members}
        self.reset()

    def reset(self):
        self.phase = Phase.SHORE_START
        self.phase_started = time.monotonic()
        self.intro_source = None
        self.switch_from = None
        self.switch_to = None
        self.switch_started = None
        self.switch_timeout_limit = self.SWITCH_TIMEOUT
        self.switch_deadline = None
        self.switch_observation = None
        self.last_switch_elapsed = None
        self.last_switch_timeout_limit = None
        self.pending_phase = None
        self.liberation_recheck_pending = False
        self.liberation_recheck_deadline = None
        self.liberation_observation = None
        self.concerto_recovery_attempts = 0
        self.concerto_extension_used = False
        self.recovery_attempts = 0
        self.bloom_resume = None
        self.bloom_active_seen = False
        self.bloom_ended = False
        self.bloom_end_frames = 0
        self.bloom_target_recovery_used = False

    def phase_deadline(self):
        limit = self.SWITCH_TIMEOUT if self.phase == Phase.CAMELLYA_RETURN else self.PHASE_TIMEOUT
        return self.phase_started + limit

    def check_timeout(self):
        # 动作窗口使用角色的冻结计时；墙钟上限只负责识别卡住后的脱困。
        if time.monotonic() >= self.phase_deadline():
            raise RotationTimeout(f'{self.phase}: phase timeout')

    def check_switch_timeout(self):
        limit = getattr(self, 'switch_timeout_limit', self.SWITCH_TIMEOUT)
        self.check_timeout()
        if self.switch_started is not None and time.monotonic() - self.switch_started >= limit:
            raise RotationTimeout(f'{self.phase}: switch timeout')

    def handoff(self, char, next_phase, timeout=None):
        self.check_timeout()
        target = self.chars[PHASE_ACTOR[next_phase]]
        confirmed = False

        def confirm(switched_to, has_intro):
            nonlocal confirmed
            if switched_to is not target:
                return
            confirmed = True
            self.phase = next_phase
            self.phase_started = time.monotonic()
            self.intro_source = char.name if has_intro else None
            self.pending_phase = None
            self.switch_deadline = None
            self.concerto_recovery_attempts = 0
            self.concerto_extension_used = False
            self.recovery_attempts = 0
            # 开场已有满协奏时，守岸人这次变奏直接用于暖机，避免再次筹备同一次交接。
            if next_phase == Phase.CAMELLYA_SEED and has_intro:
                self.phase = Phase.CAMELLYA_WARMUP
            # 普通切人不能冒充散华延奏；缺失变奏时只做短暖机再补交接。
            if next_phase == Phase.CAMELLYA_BURST and not has_intro:
                self.phase = Phase.CAMELLYA_WARMUP
            char.logger.info(f'椿散守 phase={self.phase} intro_source={self.intro_source}')

        self.pending_phase = next_phase
        self.switch_from = char
        self.switch_to = target
        self.switch_started = time.monotonic()
        if self.switch_deadline is None:
            self.switch_deadline = min(self.switch_started + self.SWITCH_TIMEOUT,
                                       self.phase_deadline())
        remaining = max(0.0, self.switch_deadline - self.switch_started)
        # 留在同一次六秒交接预算内复核 HUD 和重试，不能超时后再新开一轮预算。
        attempt_budget = self.SWITCH_TIMEOUT - self.SWITCH_RETRY_TIMEOUT if timeout is None else timeout
        self.switch_timeout_limit = min(attempt_budget, remaining)
        self.switch_observation = {'keys': 0, 'last_key_at': None, 'blocked': False, 'blocked_samples': 0,
                                   'stages': {}}
        original_send_key = self.task.send_key
        original_click = self.task.click
        original_wait_switch = target.wait_switch
        original_check_combat = self.task.check_combat
        original_in_combat = self.task.in_combat
        original_target_enemy = self.task.target_enemy
        original_f_break = char.f_break
        had_send_override = 'send_key' in self.task.__dict__
        had_click_override = 'click' in self.task.__dict__
        had_wait_override = 'wait_switch' in target.__dict__
        had_combat_override = 'check_combat' in self.task.__dict__
        had_in_combat_override = 'in_combat' in self.task.__dict__
        had_target_override = 'target_enemy' in self.task.__dict__
        had_f_break_override = 'f_break' in char.__dict__
        returning_after_bloom = self.phase == Phase.CAMELLYA_RETURN

        def timed_stage(name, action, *args, **kwargs):
            started = time.monotonic()
            try:
                return action(*args, **kwargs)
            finally:
                elapsed = time.monotonic() - started
                stages = self.switch_observation['stages']
                stages[name] = round(stages.get(name, 0) + elapsed, 4)
                if elapsed >= 0.2:
                    char.logger.info(f'椿散守 switch stage={name} elapsed={elapsed:.3f} '
                                     f'keys={self.switch_observation["keys"]} target={target.name}')

        def send_key(key, *args, **kwargs):
            if returning_after_bloom:
                self.check_switch_timeout()
            if str(key) == str(target.index + 1):
                self.switch_observation['keys'] += 1
                self.switch_observation['last_key_at'] = time.monotonic() - self.switch_started
            return original_send_key(key, *args, **kwargs)

        def click(*args, **kwargs):
            self.check_switch_timeout()
            return original_click(*args, **kwargs)

        def wait_switch():
            blocked = original_wait_switch()
            self.switch_observation['blocked'] = bool(blocked)
            self.switch_observation['blocked_samples'] += int(bool(blocked))
            return blocked

        def in_combat(target=False):
            previous_sleep_check = self.task.in_sleep_check
            self.task.in_sleep_check = True
            try:
                self.check_switch_timeout()
                timer_reader = getattr(self.task, 'get_abyss_remaining_time', None)
                can_check_challenge = callable(timer_reader) and self.task.combat_end_condition is None
                if can_check_challenge and self.task.in_team()[0]:
                    remaining = timer_reader()
                    self.check_switch_timeout()
                    if remaining is not None and remaining > 0:
                        # 手动启动的挑战未必启用全局深塔保护；可见倒计时为正时，丢锁不等于战斗结束。
                        # 通用战斗检测覆盖手动挑战后，移除此回切局部保护。
                        if not self.switch_observation.get('timer_confirmed'):
                            self.switch_observation['timer_confirmed'] = True
                            char.logger.info(f'椿散守 switch live timer remaining={remaining:.3f} '
                                             f'phase={self.phase} target={self.switch_to.name}')
                        result = self.task.scene.set_in_combat()
                        self.check_switch_timeout()
                        return result
                # 全局 in_combat 会吞掉普通异常；回切超时必须交回局部恢复，不能改写成脱战。
                result = self.task.do_check_in_combat(target)
                self.check_switch_timeout()
                if not result:
                    self.capture_switch_diagnostics(char, self.switch_to, 'native combat check returned false')
                return result
            finally:
                self.task.in_sleep_check = previous_sleep_check

        def target_enemy(wait=True, time_out=None, check_health=False):
            self.check_switch_timeout()
            if wait:
                remaining = min(self.switch_deadline, self.phase_deadline(),
                                self.switch_started + self.switch_timeout_limit) - time.monotonic()
                requested = self.task.target_enemy_time_out if time_out is None else time_out
                time_out = min(requested, max(0.0, remaining))
            result = original_target_enemy(wait=wait, time_out=time_out, check_health=check_health)
            # 截止时的 False 是本次交接预算耗尽，不能继续进入全局脱战重置。
            self.check_switch_timeout()
            return result

        self.task.send_key = send_key
        if returning_after_bloom:
            # F-break 内部会连续按 F 和点击；在同一交接预算内保留脱困，截止后不再发送输入。
            self.task.click = click
            self.task.in_combat = in_combat
            self.task.target_enemy = target_enemy
        target.wait_switch = wait_switch
        # 仍沿用原生战斗与 F 提示判断，回切的锁敌等待受同一次交接预算约束。
        self.task.check_combat = lambda *args, **kwargs: timed_stage(
            'check_combat', original_check_combat, *args, **kwargs)
        char.f_break = lambda *args, **kwargs: timed_stage('f_break', original_f_break, *args, **kwargs)
        try:
            self.check_switch_timeout()
            if returning_after_bloom and timeout is not None:
                # 守岸人带变奏的原生换人会跳过显式检查；重试前仍须在预算内确认未结束。
                self.task.check_combat()
            char.switch_next_char(post_action=confirm)
            if not confirmed:
                raise RotationTimeout(f'{self.phase}: switch not confirmed')
        finally:
            if had_send_override:
                self.task.send_key = original_send_key
            else:
                del self.task.send_key
            if returning_after_bloom:
                if had_click_override:
                    self.task.click = original_click
                else:
                    del self.task.click
                if had_in_combat_override:
                    self.task.in_combat = original_in_combat
                else:
                    del self.task.in_combat
                if had_target_override:
                    self.task.target_enemy = original_target_enemy
                else:
                    del self.task.target_enemy
            if had_wait_override:
                target.wait_switch = original_wait_switch
            else:
                del target.wait_switch
            if had_combat_override:
                self.task.check_combat = original_check_combat
            else:
                del self.task.check_combat
            if had_f_break_override:
                char.f_break = original_f_break
            else:
                del char.f_break
            if self.switch_started is not None:
                self.last_switch_elapsed = time.monotonic() - self.switch_started
                self.last_switch_timeout_limit = self.switch_timeout_limit
            self.switch_from = None
            self.switch_to = None
            self.switch_started = None
            self.switch_timeout_limit = self.SWITCH_TIMEOUT

    def perform(self, char):
        previous_skip = self.task.skip_combat_check
        try:
            self.check_timeout()
            if char is not self.chars[PHASE_ACTOR[self.phase]]:
                self.recover(char, 'unexpected active character')
                return
            self.perform_phase(char)
        except RotationTimeout as error:
            try:
                self.recover(char, error)
            except Exception:
                self.reset()
                raise
        except Exception:
            # 战斗结束、死亡和任务取消由原任务处理，不能吞掉后继续按键。
            self.reset()
            raise
        finally:
            self.task.mouse_up()
            self.task.skip_combat_check = previous_skip
            self.chars['Camellya'].waiting_for_forte_drop = False

    def recover(self, char, reason):
        self.task.mouse_up()
        message = str(reason)
        char.logger.warning(f'椿散守 recovery: {message}')
        if isinstance(reason, ConcertoTimeout) and self.try_local_concerto_recovery(char, reason):
            return
        pending_handoff_timed_out = (self.pending_phase is not None
                                     and (self.is_switch_timeout(message) or 'phase timeout' in message))
        if pending_handoff_timed_out and self.try_local_switch_recovery(char, reason):
            return
        self.reset()
        self.task.next_frame()
        in_team, current_index, _ = self.task.in_team()
        # 超时后原角色可能已经下场；只能让新帧确认的当前角色执行兜底切换。
        if not in_team:
            return
        char = next((member for member in self.members if member.index == current_index), None)
        if char is None:
            return
        for member in self.members:
            member.is_current_char = member is char
        if char is not self.chars['ShoreKeeper']:
            try:
                self.handoff(char, Phase.SHORE_START)
            except RotationTimeout as error:
                char.logger.warning(f'椿散守 recovery deferred: {error}')
                self.reset()

    @staticmethod
    def is_switch_timeout(message):
        return 'switch timeout' in message or 'switch not confirmed' in message

    def try_local_concerto_recovery(self, char, error):
        failed_phase = self.phase
        if failed_phase not in (Phase.SHORE_CONCERTO, Phase.SANHUA_OUTRO):
            return False
        if char is not self.chars[PHASE_ACTOR[failed_phase]]:
            return False
        if error.actor != char.name or self.concerto_recovery_attempts >= 1:
            return False
        self.concerto_recovery_attempts += 1
        try:
            # 近满冲突先被动复核；真实缺协奏才使用本阶段尚未用过的两秒补尾。
            full = self.settle_concerto_ring(char)
            if not full and error.kind == 'insufficient' and not self.concerto_extension_used:
                full = self.top_up_concerto(char)
            self.capture_concerto_diagnostics(char, error.value, error.kind)
            self.check_timeout()
            in_team, current_index, _ = self.task.in_team()
            if not in_team or current_index != char.index:
                return False
            char.logger.info(f'椿散守 local concerto recovery phase={failed_phase} '
                             f'value={error.value:.3f} kind={error.kind} full={full} '
                             f'settle={self.CONCERTO_CONFLICT_SETTLE} replay_skills=False')
            if full:
                next_phase = (Phase.CAMELLYA_BURST if failed_phase == Phase.SANHUA_OUTRO
                              else Phase.CAMELLYA_WARMUP)
                self.handoff(char, next_phase)
                return True
            return self.degraded_concerto_handoff(char, failed_phase, error)
        except RotationTimeout as retry_error:
            char.logger.warning(f'椿散守 local concerto retry failed: {retry_error}')
            if self.pending_phase is not None:
                return self.try_local_switch_recovery(char, retry_error)
            return False

    def degraded_concerto_handoff(self, char, failed_phase, error):
        # 协奏未被确认时不能强制变奏；正常切人会把缺少 intro 的椿导到短暖机。
        next_phase = Phase.CAMELLYA_BURST if failed_phase == Phase.SANHUA_OUTRO else Phase.CAMELLYA_WARMUP
        value = getattr(error, 'value', -1.0)
        kind = getattr(error, 'kind', 'unknown')
        try:
            char.logger.warning(f'椿散守 degraded concerto handoff phase={failed_phase} '
                                f'value={value:.3f} kind={kind} next={next_phase}')
            self.handoff(char, next_phase)
            return True
        except RotationTimeout as handoff_error:
            char.logger.warning(f'椿散守 degraded concerto handoff failed: {handoff_error}')
            return self.try_local_switch_recovery(char, handoff_error)

    def try_local_switch_recovery(self, char, reason):
        """Resolve a timed-out switch from the current HUD before replaying any phase."""
        next_phase = self.pending_phase
        target = self.switch_to
        if target is None and next_phase is not None:
            target = self.chars.get(PHASE_ACTOR.get(next_phase))
        if next_phase is None or target is None:
            return False
        self.task.next_frame()
        self.capture_switch_diagnostics(char, target, reason)
        in_team, current_index, _ = self.task.in_team()
        remaining = max(0.0, (self.switch_deadline or time.monotonic()) - time.monotonic())
        if not in_team:
            deadline = time.monotonic() + min(self.SWITCH_RETRY_TIMEOUT, remaining)
            while not in_team and time.monotonic() < deadline:
                self.check_timeout()
                char.sleep(min(0.05, max(0.0, deadline - time.monotonic())), check_combat=False)
                self.task.next_frame()
                in_team, current_index, _ = self.task.in_team()
        if in_team and current_index == target.index:
            self.finish_observed_handoff(char, target, next_phase)
            return True
        remaining = max(0.0, (self.switch_deadline or time.monotonic()) - time.monotonic())
        can_retry = (in_team and current_index == char.index and self.recovery_attempts < 1
                     and remaining > 0)
        if can_retry:
            self.recovery_attempts += 1
            retry_budget = min(self.SWITCH_RETRY_TIMEOUT, remaining)
            char.logger.info(f'椿散守 switch local retry phase={next_phase} '
                             f'target={target.name} budget=1 timeout={retry_budget:.3f}')
            if self.phase != Phase.CAMELLYA_RETURN:
                char.check_combat()
            try:
                self.handoff(char, next_phase, timeout=retry_budget)
                return True
            except RotationTimeout as retry_error:
                char.logger.warning(f'椿散守 switch local retry failed: {retry_error}')
            self.task.next_frame()
            in_team, current_index, _ = self.task.in_team()
            if in_team and current_index == target.index:
                self.finish_observed_handoff(char, target, next_phase)
                return True
        target.has_intro = False
        target.has_sub_dps_intro = False
        return False

    def finish_observed_handoff(self, char, target, next_phase):
        # has_intro 在按键前就会写入；结合已发送切人键、源角色满协奏和新 HUD 才能补记延奏。
        keys_sent = (self.switch_observation or {}).get('keys', 0)
        has_intro = bool(keys_sent and target.has_intro and char.current_con == 1)
        target.has_intro = has_intro
        target.has_sub_dps_intro = has_intro and char.is_sub_dps
        self.task.in_liberation = False
        if has_intro:
            now = time.time()
            self.task.add_freeze_duration(now, target.intro_motion_freeze_duration, -100)
            char.last_outro_time = now
        char.switch_out(con_full=has_intro)
        for member in self.members:
            member.is_current_char = member is target
        target.last_switch_in_time = time.time()
        self.phase = next_phase
        self.phase_started = time.monotonic()
        self.intro_source = char.name if target.has_intro else None
        if next_phase == Phase.CAMELLYA_SEED and target.has_intro:
            self.phase = Phase.CAMELLYA_WARMUP
        if next_phase == Phase.CAMELLYA_BURST and not target.has_intro:
            self.phase = Phase.CAMELLYA_WARMUP
        self.pending_phase = None
        self.switch_deadline = None
        self.recovery_attempts = 0
        self.concerto_recovery_attempts = 0
        self.concerto_extension_used = False
        char.logger.info(f'椿散守 switch observed target={target.name} phase={self.phase} '
                         f'intro_source={self.intro_source}')

    def capture_concerto_diagnostics(self, char, value, kind):
        capture_id = f'chunsanshou_concerto/v1_1_9_{time.time_ns()}_{kind}'
        in_team, current_index = False, -1
        try:
            in_team, current_index, _ = self.task.in_team()
            has_name, no_name = self.task.get_target_names()
            best = self.task.find_best_match_in_box(
                self.task.get_box_by_name(has_name).scale(1.1), [has_name, no_name], threshold=0.6)
            target_state = 'unknown'
            if best:
                target_state = 'locked' if best.name == has_name else 'unlocked'
            char.logger.info(f'椿散守 concerto diagnostic capture={capture_id} phase={self.phase} '
                             f'actor={char.name} value={value:.3f} kind={kind} '
                             f'cached={char.current_con:.3f} extension_used={self.concerto_extension_used} '
                             f'in_team={in_team} current_index={current_index} target_state={target_state} '
                             f'last_switch_elapsed={self.last_switch_elapsed}')
        except Exception as error:
            char.logger.debug(f'椿散守 concerto diagnostic text failed capture={capture_id}: {error}')
        try:
            frame = self.task.frame.copy()
            self.task.screenshot(f'{capture_id}_full', frame=frame)
            con_box = self.task.get_con_box()
            crop = con_box.crop_frame(frame).copy()
            self.task.screenshot(f'{capture_id}_con', frame=crop)
            if in_team and current_index == char.index and char.ring_index >= 0:
                from src.task.BaseCombatTask import con_colors

                area, ring_full = self.task.count_rings(
                    crop, con_colors[char.ring_index], 1500 / 3840 / 2160 * frame.shape[1] * frame.shape[0])
                char.logger.info(f'椿散守 concerto ring capture={capture_id} area={area} '
                                 f'ring_full={ring_full} ring_index={char.ring_index} '
                                 f'calibrated_area={self.task.con_full_size[str(char.ring_index)]}')
        except Exception as error:
            char.logger.warning(f'椿散守 concerto diagnostic image failed capture={capture_id}: {error}')

    def capture_switch_diagnostics(self, char, target, reason):
        stage = 'timeout' if self.is_switch_timeout(str(reason)) else 'combat_check_failed'
        capture_id = f'chunsanshou_switch/v1_1_9_{time.time_ns()}_{stage}'
        elapsed = self.last_switch_elapsed
        timeout_limit = self.last_switch_timeout_limit
        if self.switch_started is not None:
            elapsed = time.monotonic() - self.switch_started
            timeout_limit = self.switch_timeout_limit
        try:
            in_team, current_index, size = self.task.in_team()
        except Exception:
            in_team, current_index, size = False, -1, 0
        concerto = char.current_con
        char.logger.info(f'椿散守 switch diagnostic capture={capture_id} phase={self.phase} '
                         f'from={char.name} target={target.name} reason={reason} '
                         f'in_team={in_team} current_index={current_index} size={size} '
                         f'target_has_intro={target.has_intro} concerto={concerto} '
                         f'elapsed={elapsed} '
                         f'timeout_limit={timeout_limit} '
                         f'input={self.switch_observation}')
        try:
            frame = self.task.frame.copy()
            self.task.screenshot(f'{capture_id}_full', frame=frame)
            con_box = self.task.get_con_box()
            self.task.screenshot(f'{capture_id}_con', frame=con_box.crop_frame(frame).copy())
        except Exception as error:
            char.logger.warning(f'椿散守 switch diagnostic failed capture={capture_id}: {error}')

    def wait_shore_intro(self, char):
        if not char.has_intro:
            return
        previous_skip = self.task.skip_combat_check
        self.task.skip_combat_check = True
        try:
            char.sleep(0.1, check_combat=False)
            if not self.task.in_team_and_world():
                if not self.task.wait_in_team_and_world(time_out=4, raise_if_not_found=False):
                    raise RotationTimeout('shore intro HUD did not return')
            else:
                char.continues_normal_attack(1.2)
        finally:
            self.task.skip_combat_check = previous_skip
        char.has_intro = False

    def shore_resonance(self, char):
        resonance_clicked, _, _ = char.click_resonance()
        if not resonance_clicked:
            char.heavy_click_forte(char.is_mouse_forte_full)

    def echo(self, char):
        if char.click_echo():
            # 椿的快速声骸实现只发送按键，队伍段仍需维护冷却记录。
            char.record_echo_use()

    def fill_concerto(self, char):
        self.check_timeout()
        char.continues_normal_attack(3, until_con_full=True)
        self.task.next_frame()
        if char.is_con_full():
            return
        concerto = char.get_current_con()
        char.logger.info(f'椿散守 concerto probe actor={char.name} phase={self.phase} '
                         f'value={concerto:.3f} cached={char.current_con:.3f}')
        should_finish_shore_concerto = (char is self.chars['ShoreKeeper']
                                       and 0.8 <= concerto < self.CONCERTO_CONFLICT_VALUE)
        should_finish_sanhua_concerto = (char is self.chars['Sanhua'] and self.phase == Phase.SANHUA_OUTRO
                                       and 0.9 <= concerto < self.CONCERTO_CONFLICT_VALUE)
        should_extend = (should_finish_shore_concerto or should_finish_sanhua_concerto)
        if should_extend and not self.concerto_extension_used:
            # 近满时原地补尾，避免重新绕一遍椿和散华；每个阶段只允许一次补尾。
            actor = 'shore' if should_finish_shore_concerto else 'sanhua'
            self.check_timeout()
            char.logger.info(f'椿散守 {actor} concerto retry: concerto={concerto:.3f} budget=2')
            if self.top_up_concerto(char):
                char.logger.info(f'椿散守 {actor} concerto recovered')
                return
            concerto = char.get_current_con()
        if self.CONCERTO_CONFLICT_VALUE <= concerto < 1:
            # 近满读数可能来自面积与闭环冲突，也可能确实不足；原图和原始环检测用于区分。
            kind = 'near_full_unconfirmed'
        else:
            kind = 'insufficient'
        char.logger.info(f'椿散守 concerto failed actor={char.name} phase={self.phase} '
                         f'value={concerto:.3f} kind={kind} '
                         f'extension_used={self.concerto_extension_used}')
        self.capture_concerto_diagnostics(char, concerto, kind)
        raise ConcertoTimeout(self.phase, concerto, kind, char.name)

    def check_recovery_actor(self, char):
        self.check_timeout()
        in_team, current_index, _ = self.task.in_team()
        if not in_team or current_index != char.index:
            raise RotationTimeout(f'{self.phase}: resource recovery actor or HUD changed')

    def recovery_target_locked(self):
        # has_target 的特殊界面修复会等待并按 Esc；补资源只能观察锁定，不能借此追加动作。
        has_name, no_name = self.task.get_target_names()
        scale = 1.2 if self.task.is_browser() else 1.1
        boxes = [self.task.get_box_by_name(has_name).scale(scale),
                 self.task.get_box_by_name('box_target_enemy_long'),
                 self.task.get_box_by_name('target_box_long2')]
        for box in boxes:
            best = self.task.find_best_match_in_box(box, [has_name, no_name], threshold=0.6)
            if best:
                return best.name == has_name
        return False

    def wait_bloom_target(self, char, deadline):
        self.check_recovery_actor(char)
        if self.recovery_target_locked():
            return True
        if self.bloom_target_recovery_used:
            return False
        self.bloom_target_recovery_used = True
        recovery_deadline = min(deadline, time.monotonic() + self.BLOOM_TARGET_RECOVERY_TIMEOUT,
                                self.phase_started + self.PHASE_TIMEOUT)
        self.task.mouse_up()
        char.logger.info(f'椿散守 bloom target recovery start budget='
                         f'{max(0, recovery_deadline - time.monotonic()):.3f}')
        stable_frames = 0
        retargeted = False
        started = time.monotonic()
        # 换怪可能短暂丢锁；本次准备只用一次原预算内的恢复，不能重放爆发或延长补资源。
        while time.monotonic() < recovery_deadline:
            char.sleep(min(0.05, recovery_deadline - time.monotonic()), check_combat=False)
            self.task.next_frame()
            self.check_recovery_actor(char)
            if time.monotonic() >= recovery_deadline:
                break
            end_condition = self.task.combat_end_condition
            if end_condition is not None and end_condition():
                return False
            locked = self.recovery_target_locked()
            stable_frames = stable_frames + 1 if locked else 0
            if stable_frames >= 2:
                char.logger.info(f'椿散守 bloom target recovery confirmed '
                                 f'elapsed={time.monotonic() - started:.3f}')
                return True
            can_retarget = (not locked and not retargeted and time.monotonic() - started >= 0.2
                            and time.monotonic() < recovery_deadline)
            if can_retarget:
                self.task.middle_click()
                retargeted = True
        char.logger.info('椿散守 bloom target recovery expired')
        return False

    def top_up_concerto(self, char):
        if self.concerto_extension_used:
            return False
        self.concerto_extension_used = True
        deadline = min(time.monotonic() + self.CONCERTO_TOPUP_TIMEOUT,
                       self.phase_started + self.PHASE_TIMEOUT)
        char.logger.info(f'椿散守 concerto topup start actor={char.name} phase={self.phase} '
                         f'budget={max(0, deadline - time.monotonic()):.3f} replay_skills=False')
        while True:
            self.check_timeout()
            char.check_combat()
            self.task.next_frame()
            self.check_recovery_actor(char)
            if char.is_con_full():
                char.logger.info(f'椿散守 concerto topup full actor={char.name} phase={self.phase}')
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self.recovery_target_locked():
                return False
            self.check_recovery_actor(char)
            if time.monotonic() >= deadline:
                return False
            char.click(interval=0)
            char.sleep(min(0.1, max(0, deadline - time.monotonic())), check_combat=False)

    def settle_concerto_ring(self, char):
        deadline = time.monotonic() + self.CONCERTO_CONFLICT_SETTLE
        while time.monotonic() < deadline:
            self.check_timeout()
            char.check_combat()
            self.task.next_frame()
            in_team, current_index, _ = self.task.in_team()
            if not in_team or current_index != char.index:
                return False
            if char.is_con_full():
                return True
            char.sleep(0.05)
        return False

    def sanhua_heavy(self, char, liberation=False):
        char.wait_down(click=False)
        if not liberation:
            resonance_clicked, _, _ = char.click_resonance(send_click=False)
            if not resonance_clicked:
                return
        started = time.time()
        self.task.mouse_down()
        try:
            if liberation:
                char.click_liberation(send_click=False)
            remaining = 0.85 - char.time_elapsed_accounting_for_freeze(started)
            if remaining > 0:
                char.sleep(remaining)
        finally:
            self.task.mouse_up()
        char.sleep(0.8)

    def perform_phase(self, char):
        if self.phase == Phase.SHORE_START:
            self.wait_shore_intro(char)
            char.click_liberation()
            self.shore_resonance(char)
            self.handoff(char, Phase.CAMELLYA_SEED)
        elif self.phase == Phase.CAMELLYA_SEED:
            char.click_resonance()
            self.handoff(char, Phase.SANHUA_PREPARE)
        elif self.phase == Phase.SANHUA_PREPARE:
            self.sanhua_heavy(char)
            self.handoff(char, Phase.SHORE_CONCERTO)
        elif self.phase == Phase.SHORE_CONCERTO:
            self.wait_shore_intro(char)
            char.heavy_click_forte(char.is_mouse_forte_full)
            char.click_liberation()
            self.shore_resonance(char)
            self.echo(char)
            self.fill_concerto(char)
            self.handoff(char, Phase.CAMELLYA_WARMUP)
        elif self.phase == Phase.CAMELLYA_WARMUP:
            duration = 1.2 if char.has_intro else 0.2
            char.continues_normal_attack(duration)
            char.has_intro = False
            char.has_sub_dps_intro = False
            if char.resonance_available() and not char.ephemeral_ready():
                char.click_resonance()
            char.heavy_attack(1.2, until_con_full=True)
            self.handoff(char, Phase.SHORE_RECHARGE)
        elif self.phase == Phase.SHORE_RECHARGE:
            self.wait_shore_intro(char)
            char.continues_normal_attack(0.4)
            self.handoff(char, Phase.SANHUA_OUTRO)
        elif self.phase == Phase.SANHUA_OUTRO:
            self.sanhua_heavy(char, liberation=True)
            char.click_resonance(send_click=False)
            self.echo(char)
            self.fill_concerto(char)
            self.handoff(char, Phase.CAMELLYA_BURST)
        elif self.phase == Phase.CAMELLYA_BURST:
            if self.intro_source != 'Sanhua' or not char.has_intro:
                raise RotationTimeout('missing Sanhua intro for burst')
            self.camellya_burst(char)
            self.check_timeout()
            burst_wall = time.monotonic() - self.phase_started
            # 已完成的输出不能再被回切时的 F-break 挤占；独立交接只开一次六秒窗口，重试不续期。
            self.phase = Phase.CAMELLYA_RETURN
            self.phase_started = time.monotonic()
            self.switch_deadline = self.phase_deadline()
            char.logger.info(f'椿散守 phase={self.phase} intro_source={self.intro_source} '
                             f'burst_wall={burst_wall:.3f} budget={self.SWITCH_TIMEOUT}')
            self.handoff(char, Phase.SHORE_START)
        elif self.phase == Phase.CAMELLYA_RETURN:
            self.handoff(char, Phase.SHORE_START)

    def cast_bloom(self, char):
        started = time.monotonic()
        while char.ephemeral_ready():
            self.check_timeout()
            char.check_combat()
            if time.monotonic() - started >= 2:
                self.capture_bloom_diagnostics(char, 'input_not_accepted', 'bloom input not accepted')
                raise RotationTimeout('bloom input not accepted')
            char.send_resonance_key()
            char.sleep(0.1)
            self.task.next_frame()
        char.sleep(1.1)
        self.check_timeout()
        char.check_combat()
        self.task.next_frame()
        self.check_timeout()
        if self.task.find_one('camellya_budding', threshold=0.7):
            return
        self.capture_bloom_diagnostics(char, 'not_confirmed', 'bloom not confirmed')
        deadline = time.monotonic() + self.BLOOM_ENTRY_RECHECK
        stable_frames = 0
        while time.monotonic() < deadline:
            self.check_timeout()
            char.check_combat()
            self.task.next_frame()
            self.check_timeout()
            if time.monotonic() >= deadline:
                break
            if self.task.find_one('camellya_budding', threshold=0.7):
                char.logger.info('椿散守 bloom entry confirmed late retry=0')
                return
            in_team, current_index, _ = self.task.in_team()
            can_act = in_team and current_index == char.index and not self.task.in_liberation
            skill_ready = can_act and char.ephemeral_ready()
            # 角色缓存的满协奏可能尚未清除；补按只能使用当前 HUD 的满协奏证据。
            concerto_full = can_act and bool(self.task.is_con_full())
            can_retry = skill_ready and concerto_full
            stable_frames = stable_frames + 1 if can_retry else 0
            if stable_frames >= 2:
                char.logger.info('椿散守 bloom input retry count=1 skill_ready=True concerto_full=True')
                self.capture_bloom_diagnostics(char, 'input_retry', 'fresh ready E and concerto; budding absent')
                self.check_timeout()
                char.send_resonance_key()
                char.sleep(1.1)
                self.check_timeout()
                char.check_combat()
                self.task.next_frame()
                self.check_timeout()
                if self.task.find_one('camellya_budding', threshold=0.7):
                    char.logger.info('椿散守 bloom entry confirmed retry=1')
                    return
                self.capture_bloom_diagnostics(char, 'retry_not_confirmed', 'bloom retry not confirmed')
                raise RotationTimeout('bloom retry not confirmed')
            char.sleep(0.05)
        char.logger.info('椿散守 bloom input retry skipped: fresh readiness not confirmed')
        raise RotationTimeout('bloom not confirmed')

    def capture_bloom_diagnostics(self, char, stage, reason, concerto=None):
        from src.char.Camellya import camellya_red_color

        capture_id = f'chunsanshou_bloom/v1_1_9_{time.time_ns()}_{stage}'
        char.logger.info(f'椿散守 bloom diagnostic capture={capture_id} phase={self.phase} '
                         f'reason={reason} concerto={concerto} intro_source={self.intro_source}')
        try:
            # 保留当前判定帧，异步保存；诊断不刷新画面、不按键，也不修改角色资源缓存。
            frame = self.task.frame.copy()
            self.task.screenshot(f'{capture_id}_full', frame=frame)
            e_box = self.task.box_of_screen_scaled(2560, 1440, 2110, 1236, 2217, 1343,
                                                   name='camellya_resonance', hcenter=True)
            forte_box = self.task.box_of_screen_scaled(3840, 2160, 1630, 2002, 2176, 2004,
                                                       name='camellya_forte', hcenter=True)
            forte_context = self.task.box_of_screen_scaled(3840, 2160, 1580, 1940, 2230, 2070,
                                                           name='camellya_forte_context', hcenter=True)
            self.task.screenshot(f'{capture_id}_e', frame=e_box.crop_frame(frame).copy())
            self.task.screenshot(f'{capture_id}_forte', frame=forte_context.crop_frame(frame).copy())
            # 与椿现有采样区域、环形遮罩和阈值保持一致，额外识别值仅用于排查。
            red_percent = char.calculate_color_percentage_in_masked(camellya_red_color, e_box, 0.395, 0.496)
            forte = char.get_forte()
            budding_forte = char.get_forte(budding=True)
            skill = CamellyaTeamVision.skill(char)
            normal_forte = CamellyaTeamVision.forte(char)
            budding = CamellyaTeamVision.budding_forte(char)
            char.logger.info(
                f'椿散守 bloom recognition capture={capture_id} frame={frame.shape[1]}x{frame.shape[0]} '
                f'legacy_red_percent={red_percent:.6f} legacy_threshold=0.100000 legacy_ready={red_percent > 0.1} '
                f'forte={forte:.3f} budding_forte={budding_forte:.3f} '
                f'team_skill={skill} team_forte={normal_forte} team_budding_forte={budding} '
                f'e_box=({e_box.x},{e_box.y},{e_box.width},{e_box.height}) '
                f'legacy_forte_sample=({forte_box.x},{forte_box.y},{forte_box.width},{forte_box.height}) '
                f'team_forte_reference=(1630,1994,546,18) images_queued=3'
            )
            self.log_target_diagnostics(char, capture_id, 'bloom')
        except Exception as error:
            # 保存或额外识别失败不能替换原来的开花失败原因、阻止原有恢复路线。
            char.logger.warning(f'椿散守 bloom diagnostic failed capture={capture_id}: {error}')

    def wait_camellya_action_ready(self, char):
        deadline = time.monotonic() + 1.2
        stable_frames = 0
        while time.monotonic() < deadline:
            self.check_timeout()
            char.check_combat()
            self.task.next_frame()
            in_team, current_index, _ = self.task.in_team()
            # 复用已有技能可用判断，连续两帧确认，避免把切人完成当成变奏动画结束。
            can_act = in_team and current_index == char.index and char.down()
            stable_frames = stable_frames + 1 if can_act else 0
            if stable_frames >= 2:
                return True
            char.sleep(0.1)
        return False

    def log_liberation_unavailable(self, char, stage, attempt=0):
        try:
            in_team, current_index, _ = self.task.in_team()
            cd = self.task.get_cd('liberation', char.index)
            white = char.current_liberation()
            if not self.task.use_liberation:
                cause = 'disabled'
            elif not in_team:
                cause = 'hud_unavailable'
            elif current_index != char.index:
                cause = 'different_active_character'
            elif cd > 0.2:
                cause = 'cooldown'
            elif white <= 0:
                cause = 'icon_dark_energy_or_animation_unknown'
            else:
                cause = 'availability_unconfirmed'
            char.logger.info(f'椿散守 liberation diagnostic stage={stage} attempt={attempt} cause={cause} '
                             f'cd={cd:.3f} icon_white={white:.6f} in_team={in_team} current_index={current_index} '
                             f'is_current_char={char.is_current_char} has_intro={char.has_intro}')
        except Exception as error:
            char.logger.warning(f'椿散守 liberation diagnostic failed stage={stage}: {error}')

    def capture_liberation_diagnostics(self, char, event_id, stage, attempt, confirmed=None):
        capture_id = f'chunsanshou_liberation/v1_1_9_{event_id}_{stage}'
        char.logger.info(f'椿散守 liberation capture={capture_id} attempt={attempt} phase={self.phase} '
                         f'intro_source={self.intro_source} confirmed={confirmed} hit=unknown airborne=unknown')
        try:
            frame = self.task.frame.copy()
            self.task.screenshot(f'{capture_id}_full', frame=frame)
            self.log_target_diagnostics(char, capture_id, 'liberation')
        except Exception as error:
            char.logger.warning(f'椿散守 liberation diagnostic failed capture={capture_id}: {error}')

    def log_target_diagnostics(self, char, capture_id, context):
        try:
            has_name, no_name = self.task.get_target_names()
            scale = 1.2 if self.task.is_browser() else 1.1
            boxes = [self.task.get_box_by_name(has_name).scale(scale),
                     self.task.get_box_by_name('box_target_enemy_long'),
                     self.task.get_box_by_name('target_box_long2'),
                     self.task.get_box_by_name(has_name).scale(1.1, 2.0)]
            best = None
            # has_target 的兜底分支会等待并按 Esc；诊断只读取模板，不能为采样改变战斗。
            for box in boxes:
                best = self.task.find_best_match_in_box(box, [has_name, no_name], threshold=0.6)
                if best:
                    break
            target_state = 'unknown'
            target_match = None
            if best:
                target_state = 'locked' if best.name == has_name else 'unlocked'
                target_match = (best.name, round(best.confidence, 3), best.x, best.y, best.width, best.height)
            in_team, current_index, _ = self.task.in_team()
            boss_indicator = bool(self.task.find_one('boss_break_shield') or self.task.find_one('boss_break_lock'))
            char.logger.info(
                f'椿散守 {context} target capture={capture_id} target_state={target_state} '
                f'target_match={target_match} boss_indicator={boss_indicator} in_team={in_team} '
                f'current_index={current_index} in_liberation={self.task.in_liberation} '
                f'cd={self.task.get_cd("liberation", char.index):.3f} '
                f'phase_wall={time.monotonic() - self.phase_started:.3f} '
                f'distance=unknown hit=unknown airborne=unknown'
            )
        except Exception as error:
            char.logger.warning(f'椿散守 {context} target diagnostic failed capture={capture_id}: {error}')

    def camellya_liberation(self, char, deferred=False):
        self.liberation_recheck_pending = False
        self.liberation_recheck_deadline = None
        self.liberation_observation = None
        char.wait_down(click=False)
        if not self.task.use_liberation:
            char.logger.info('椿散守 liberation skipped: disabled')
            self.log_liberation_unavailable(char, 'disabled')
            return False
        for attempt in range(1, 2 if deferred else 3):
            if not self.wait_camellya_action_ready(char):
                char.logger.warning(f'椿散守 liberation skipped: action not ready attempt={attempt}')
                self.log_liberation_unavailable(char, 'action_not_ready', attempt)
                return False
            if not char.liberation_available():
                char.logger.info(f'椿散守 liberation skipped: unavailable attempt={attempt}')
                self.log_liberation_unavailable(char, 'unavailable', attempt)
                # 只补初次尚未按出的大招；短冷却或图标暂暗都只能在有限窗口内复查一次。
                if not deferred and attempt == 1:
                    on_cooldown = char.has_cd('liberation')
                    cd = self.task.get_cd('liberation', char.index) if on_cooldown else 0
                    if not on_cooldown or 0 < cd <= self.LIBERATION_SHORT_CD:
                        # 冷却复查与普攻准备并行，只等待预测到期附近；图标暂暗仍沿用短窗口。
                        budget = self.LIBERATION_RECHECK_BUDGET
                        if on_cooldown:
                            budget = min(cd + 0.15, self.BLOOM_PREPARE_TIMEOUT)
                        self.liberation_recheck_pending = True
                        self.liberation_recheck_deadline = min(time.monotonic() + budget,
                                                              self.phase_started + self.PHASE_TIMEOUT)
                        char.logger.info(f'椿散守 liberation recheck scheduled cd={cd:.3f} '
                                         f'budget={budget:.3f}')
                return False
            event_id = time.time_ns()
            self.capture_liberation_diagnostics(char, event_id, 'before', attempt)
            confirmed = None
            try:
                confirmed = char.click_liberation(wait_if_cd_ready=0)
            finally:
                returned_at = time.monotonic()
                self.capture_liberation_diagnostics(char, event_id, 'after', attempt, confirmed)
            self.check_timeout()
            if confirmed:
                self.liberation_observation = {'event_id': event_id, 'attempt': attempt,
                                              'started': returned_at, 'pending': [0.35, 0.9]}
                char.logger.info(f'椿散守 liberation confirmed attempt={attempt}')
                return True
            self.task.next_frame()
            on_cooldown = bool(char.has_cd('liberation'))
            char.logger.warning(f'椿散守 liberation unconfirmed attempt={attempt} cooldown={on_cooldown}')
            self.log_liberation_unavailable(char, 'unconfirmed', attempt)
            # 未看到动画但已进入冷却时不能重复按，也不能记作成功；开花条件仍需独立验证。
            if on_cooldown:
                return False
            if attempt == 1 and not deferred:
                char.sleep(0.2)
        return False

    def observe_liberation_after_frame(self, char):
        observation = self.liberation_observation
        if observation is None:
            return
        elapsed = time.monotonic() - observation['started']
        if elapsed < observation['pending'][0]:
            return
        try:
            in_team, current_index, _ = self.task.in_team()
            same_character = in_team and current_index == char.index
            if elapsed > 2 or not same_character:
                char.logger.info(f'椿散守 liberation delayed skipped event={observation["event_id"]} '
                                 f'elapsed={elapsed:.3f} pending={observation["pending"]} '
                                 f'reason={"expired" if elapsed > 2 else "character_or_hud_changed"}')
                self.liberation_observation = None
                return
            due = [delay for delay in observation['pending'] if delay <= elapsed]
            delay = due[-1]
            # 同一新帧只保存一次，错过的节点明确记为跳过，不拿同帧冒充两个时刻。
            observation['pending'] = [at for at in observation['pending'] if at > elapsed]
            if not observation['pending']:
                self.liberation_observation = None
            char.logger.info(f'椿散守 liberation delayed event={observation["event_id"]} '
                             f'requested={delay:.3f} elapsed={elapsed:.3f} skipped={due[:-1]}')
            self.capture_liberation_diagnostics(char, observation['event_id'], f'after_{round(delay * 1000)}ms',
                                               observation['attempt'], True)
        except Exception as error:
            self.liberation_observation = None
            char.logger.warning(f'椿散守 liberation delayed diagnostic failed: {error}')

    def bloom_marker(self):
        find_one = getattr(self.task, 'find_one', None)
        if find_one is None:
            return None
        try:
            return find_one('camellya_budding', threshold=0.7) or None
        except Exception:
            return None

    @staticmethod
    def marker_confidence(marker):
        if isinstance(marker, (list, tuple)):
            marker = marker[0] if marker else None
        try:
            return float(getattr(marker, 'confidence', 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def read_bloom_state(self, char):
        marker = self.bloom_marker()
        try:
            forte = float(char.get_forte(budding=True))
        except Exception:
            forte = -1.0
        try:
            skill_ready = bool(char.ephemeral_ready())
        except Exception:
            skill_ready = False
        in_team, current_index, _ = self.task.in_team()
        current = in_team and current_index == char.index
        if not current:
            state = 'unknown'
        elif marker is not None and forte > 0.05:
            state = 'active'
        elif self.bloom_active_seen and current and forte > self.BLOOM_COMPLETE_FORTE:
            # 已确认开花后模板偶发漏识别时，非空开花回路只用于维持接续，不用于首次进入。
            state = 'active'
        elif marker is not None:
            state = 'unknown'
        else:
            state = 'inactive'
        return {'state': state, 'marker': marker, 'forte': forte,
                'skill_ready': skill_ready, 'current': current}

    def bloom_resume_record(self, state):
        return {'started': time.time(), 'wall_started': time.monotonic(),
                'forte': state['forte'], 'marker_confidence': self.marker_confidence(state['marker'])}

    def confirm_active_bloom(self, char):
        self.check_timeout()
        state = self.read_bloom_state(char)
        if state['state'] != 'active':
            return None
        strong = (state['marker'] is not None
                  and self.marker_confidence(state['marker']) >= 0.9
                  and state['forte'] >= 0.1)
        if strong:
            return self.bloom_resume_record(state)
        char.sleep(0.05)
        self.task.next_frame()
        self.check_timeout()
        second = self.read_bloom_state(char)
        if second['state'] != 'active':
            return None
        return self.bloom_resume_record(second)

    def wait_bloom_ready(self, char, duration, recovery=False):
        deadline = time.monotonic() + duration
        while True:
            self.check_timeout()
            if not recovery:
                char.check_combat()
            self.task.next_frame()
            if recovery:
                self.check_recovery_actor(char)
            resume = self.confirm_active_bloom(char)
            if resume is not None:
                self.bloom_resume = resume
                self.bloom_active_seen = True
                self.liberation_recheck_pending = False
                self.liberation_recheck_deadline = None
                char.logger.info(f'椿散守 bloom active observed forte={resume["forte"]:.3f} '
                                 f'probe=wait_bloom_ready')
                return '', 1
            if recovery:
                self.check_recovery_actor(char)
            if self.bloom_active_seen:
                state = self.read_bloom_state(char)
                ended = (state['current'] and state['marker'] is None
                         and 0 <= state['forte'] <= self.BLOOM_COMPLETE_FORTE)
                self.bloom_end_frames = self.bloom_end_frames + 1 if ended else 0
                if self.bloom_end_frames >= 2:
                    self.bloom_ended = True
                    self.liberation_recheck_pending = False
                    self.liberation_recheck_deadline = None
                    char.logger.info(f'椿散守 bloom ended observed forte={state["forte"]:.3f}')
                    return 'bloom_ended', 1
            if self.liberation_recheck_deadline is not None:
                cd = self.task.get_cd('liberation', char.index)
                can_recheck = (self.task.use_liberation and cd <= 0.2
                               and time.monotonic() <= deadline and char.liberation_available())
                if can_recheck:
                    char.logger.info(f'椿散守 liberation deferred recheck stage=available cd={cd:.3f} budget=1')
                    recheck_started = time.monotonic()
                    confirmed = self.camellya_liberation(char, deferred=True)
                    # 补大动画不挤占原有开花准备窗口；整段仍受 phase timeout 约束。
                    deadline += time.monotonic() - recheck_started
                    self.liberation_recheck_pending = False
                    self.liberation_recheck_deadline = None
                    if not confirmed:
                        char.logger.info('椿散守 liberation deferred recheck ended unavailable budget=1')
                    self.task.next_frame()
                elif (not self.task.use_liberation or time.monotonic() >= self.liberation_recheck_deadline
                      or cd > self.LIBERATION_SHORT_CD or time.monotonic() >= deadline):
                    self.liberation_recheck_pending = False
                    self.liberation_recheck_deadline = None
                    char.logger.info(f'椿散守 liberation recheck cancelled cd={cd:.3f} reason=budget_or_long_cd')
            skill_ready = char.ephemeral_ready()
            concerto = char.get_current_con()
            missing = []
            if not skill_ready:
                missing.append('bloom_skill')
            if not char.is_con_full():
                missing.append('concerto')
            reason = '_and_'.join(missing)
            ready_to_leave = not reason and self.liberation_recheck_deadline is None
            if ready_to_leave or time.monotonic() >= deadline:
                return reason, concerto
            if recovery:
                self.check_recovery_actor(char)
                if not self.wait_bloom_target(char, deadline):
                    raise RotationTimeout('bloom resource recovery target lost')
                # 先恢复可见锁定，再沿用战斗检测，避免进入全局长锁敌等待而冲掉局部预算。
                char.check_combat()
                self.check_recovery_actor(char)
                if time.monotonic() >= deadline:
                    return reason, concerto
                char.click(interval=0)
                char.sleep(min(0.1, max(0, deadline - time.monotonic())), check_combat=False)
            else:
                char.click(interval=0.1)
                char.sleep(0.05)

    def prepare_bloom(self, char):
        self.bloom_target_recovery_used = False
        self.bloom_resume = None
        self.bloom_ended = False
        if not self.bloom_active_seen:
            self.bloom_end_frames = 0
        reason, concerto = self.wait_bloom_ready(char, self.BLOOM_PREPARE_TIMEOUT)
        if self.bloom_resume is not None or self.bloom_ended:
            return
        if self.liberation_recheck_pending:
            char.logger.info('椿散守 liberation deferred recheck stage=bloom_prepare budget=1')
            self.camellya_liberation(char, deferred=True)
            # 补放大招会改变资源与画面，开花必须重新确认当前条件。
            reason, concerto = self.wait_bloom_ready(char, 0)
            if self.bloom_resume is not None or self.bloom_ended:
                return
        if not reason:
            return
        self.capture_bloom_diagnostics(char, 'before_recovery', reason, concerto)
        char.logger.warning(f'椿散守 bloom readiness retry: missing={reason} concerto={concerto:.3f}')
        self.check_recovery_actor(char)
        if not self.concerto_extension_used:
            self.concerto_extension_used = True
            # 重击只用于消耗已识别回路；两秒普通攻击补资源与原有补尾共用一次机会。
            # 不先等待再追加第二轮两秒，也不重放 R/E；开花技能和协奏在每帧一起复核。
            forte = char.get_forte()
            missing_skill = 'bloom_skill' in reason
            heavy_budget_available = self.phase_started + self.PHASE_TIMEOUT - time.monotonic() >= 1.2
            can_consume_forte = missing_skill and 0.01 < forte <= 1 and heavy_budget_available
            recovery_deadline = min(time.monotonic() + self.CONCERTO_TOPUP_TIMEOUT
                                    + (1.2 if can_consume_forte else 0),
                                    self.phase_started + self.PHASE_TIMEOUT)
            char.logger.info(f'椿散守 bloom skill recovery: forte={forte:.3f} heavy={can_consume_forte}')
            if can_consume_forte:
                if not self.wait_bloom_target(char, recovery_deadline):
                    raise RotationTimeout('bloom resource recovery target lost')
                # 锁定恢复期间资源和开花状态可能已变化，不能沿用丢锁前的重击决定。
                reason, concerto = self.wait_bloom_ready(char, 0, recovery=True)
                if self.bloom_resume is not None or self.bloom_ended or not reason:
                    return
                forte = char.get_forte()
                can_consume_forte = 'bloom_skill' in reason and 0.01 < forte <= 1
                if can_consume_forte and recovery_deadline - time.monotonic() >= 1.2:
                    try:
                        char.heavy_attack(1.2)
                    finally:
                        self.task.mouse_up()
                self.check_recovery_actor(char)
            duration = min(self.CONCERTO_TOPUP_TIMEOUT,
                           max(0, recovery_deadline - time.monotonic()))
            reason, concerto = self.wait_bloom_ready(char, duration, recovery=True)
        if self.bloom_resume is not None or self.bloom_ended:
            return
        if reason:
            self.capture_bloom_diagnostics(char, 'recovery_failed', reason, concerto)
            raise RotationTimeout(f'bloom not ready: missing={reason} concerto={concerto:.3f}')
        char.logger.info('椿散守 bloom readiness recovered')

    def camellya_burst(self, char):
        original_next_frame = self.task.next_frame
        had_instance_override = 'next_frame' in self.task.__dict__

        def observed_next_frame():
            frame = original_next_frame()
            self.observe_liberation_after_frame(char)
            return frame

        # 导入包没有逐帧诊断回调，仅本段包装刷新接口；未来有正式回调时移除。
        # 诊断借用原动作的新帧，不新增等待、刷新或按键，退出时恢复实例原状。
        self.task.next_frame = observed_next_frame
        try:
            self.bloom_resume = None
            self.bloom_ended = False
            self.bloom_active_seen = False
            self.bloom_end_frames = 0
            resume = self.confirm_active_bloom(char)
            if resume is not None:
                self.bloom_resume = resume
                self.bloom_active_seen = True
                self.liberation_recheck_pending = False
                self.liberation_recheck_deadline = None
                char.logger.info(f'椿散守 bloom resume active forte={resume["forte"]:.3f} '
                                 f'stage=burst_entry')
                self.capture_bloom_diagnostics(char, 'resume_active',
                                               f'already active bloom forte={resume["forte"]:.3f}')
                char.has_intro = False
                char.has_sub_dps_intro = False
                self.bloom_output(char, resume=resume)
                char.click_resonance()
                self.echo(char)
                return
            # 散华交接后优先开大，不复用通用变奏分支的 4.6 秒重击。
            self.camellya_liberation(char)
            char.has_intro = False
            char.has_sub_dps_intro = False
            if char.resonance_available() and not char.ephemeral_ready():
                char.click_resonance()
            if self.bloom_resume is None and not self.bloom_ended:
                self.prepare_bloom(char)
            if self.bloom_resume is not None:
                resume = self.bloom_resume
                char.logger.info(f'椿散守 bloom resume active forte={resume["forte"]:.3f} '
                                 f'stage=bloom_prepare')
                self.capture_bloom_diagnostics(char, 'resume_active',
                                               f'already active bloom forte={resume["forte"]:.3f}')
                self.bloom_output(char, resume=resume)
            elif self.bloom_ended:
                char.logger.info('椿散守 bloom already ended before cast; skip repeat input')
            else:
                self.cast_bloom(char)
                char.logger.info('椿散守 bloom cast confirmed source=cast_bloom')
                self.bloom_output(char)
            char.click_resonance()
            self.echo(char)
        finally:
            if had_instance_override:
                self.task.next_frame = original_next_frame
            else:
                del self.task.next_frame
            if self.liberation_observation is not None:
                char.logger.info(f'椿散守 liberation delayed skipped reason=burst_exit '
                                 f'event={self.liberation_observation["event_id"]} '
                                 f'pending={self.liberation_observation["pending"]}')
            self.liberation_observation = None

    @staticmethod
    def record_bloom_progress(samples, forte, elapsed):
        reset_reason = None
        if not 0 <= forte <= 1:
            samples.clear()
            return 'forte_unknown'
        if samples:
            last_time, last_forte = samples[-1]
            if elapsed - last_time > 0.5:
                reset_reason = 'sample_gap'
            elif forte - last_forte >= 0.015:
                reset_reason = 'forte_rise'
            if reset_reason:
                samples.clear()
        samples.append((elapsed, forte))
        samples[:] = [(at, value) for at, value in samples if elapsed - at <= 1.5]
        return reset_reason

    @staticmethod
    def bloom_tail_status(samples, elapsed):
        last_time, forte = samples[-1] if samples else (elapsed, -1)
        decreases = [right[0] for left, right in zip(samples, samples[1:]) if left[1] - right[1] >= 0.015]
        sample_age = elapsed - last_time if samples else -1
        drop_age = elapsed - decreases[-1] if decreases else -1
        span = last_time - samples[0][0] if samples else 0
        if len(samples) < 3:
            reason = 'insufficient_samples'
        elif not 0 <= forte <= 0.25:
            reason = 'outside_tail'
        elif sample_age > 0.5:
            reason = 'stale_sample'
        elif len(decreases) < 2:
            reason = 'insufficient_decreases'
        elif drop_age > 0.75:
            reason = 'stale_decrease'
        elif span < 0.25:
            reason = 'short_observation'
        else:
            reason = 'progressing'
        return {'reason': reason, 'samples': len(samples), 'forte': round(forte, 3),
                'sample_age': round(sample_age, 3), 'decreases': len(decreases),
                'drop_age': round(drop_age, 3), 'span': round(span, 3)}

    @staticmethod
    def bloom_tail_progressing(samples, elapsed):
        return Rotation.bloom_tail_status(samples, elapsed)['reason'] == 'progressing'

    @staticmethod
    def bloom_entry_recheck_allowed(samples, elapsed, reset):
        if reset is None or not samples:
            return False
        previous = reset['previous']
        last_time, forte = samples[-1]
        # 边界前最后一帧重置时只能留下一个样本；补采样在短窗口内完成，消耗判据仍不放宽。
        recent_reset = 0 <= elapsed - reset['at'] <= Rotation.BLOOM_TAIL_RECHECK
        fresh_sample = 0 <= elapsed - last_time <= 0.5
        low_current = 0 < forte <= 0.25
        if reset['reason'] == 'forte_unknown':
            # 短暂遮挡后已有两次清晰下降时，只补足观测跨度，不沿用遮挡前的消耗历史。
            short_consuming_history = Rotation.bloom_tail_status(samples, elapsed)['reason'] == 'short_observation'
            return recent_reset and fresh_sample and low_current and short_consuming_history
        if reset['reason'] != 'forte_rise':
            return False
        low_before_reset = previous is not None and 0 <= previous[1] <= 0.25
        return recent_reset and fresh_sample and low_before_reset and low_current

    def maintain_bloom_heavy(self, char, observation, elapsed, forte=None, bloom_visible=False,
                             check_budget=None):
        if forte is None:
            forte = float(char.get_forte(budding=True))
        reason = None
        if not 0 <= forte <= 1:
            if not bloom_visible:
                observation['unknown_since'] = None
                observation['forte'] = None
                observation['samples'] = 0
                return
            last_unknown = observation.get('last_unknown', elapsed)
            if observation.get('unknown_since') is None or elapsed - last_unknown > 0.5:
                observation['unknown_since'] = elapsed
                observation['unknown_samples'] = 0
            observation['last_unknown'] = elapsed
            observation['unknown_samples'] += 1
            missing_for = elapsed - observation['unknown_since']
            feedback_missing = (missing_for >= self.BLOOM_FEEDBACK_TIMEOUT
                                and observation['unknown_samples'] >= 3
                                and not observation.get('feedback_retry_used', False))
            if feedback_missing:
                reason = 'forte_unreadable'
        else:
            observation['unknown_since'] = None
        if not 0.05 < forte <= 1:
            if reason is None:
                # 不可读不等于未消耗；保留计时但不累计有效样本，低回路仍交给原收尾判据。
                if 0 <= forte <= 0.05:
                    observation['forte'] = None
                    observation['samples'] = 0
                return
        else:
            previous = observation['forte']
            changed = previous is None or abs(previous - forte) >= 0.015
            if changed:
                observation.update(forte=forte, since=elapsed, samples=1)
                return
            observation['samples'] += 1
            stalled = observation['samples'] >= 3 and elapsed - observation['since'] >= 0.9
            if stalled:
                reason = 'forte_stalled'
        if reason is not None and observation['retries'] < 2:
            check_budget = check_budget or self.check_timeout
            check_budget()
            observation['retries'] += 1
            if reason == 'forte_unreadable':
                # 当前角色与开花标记连续可见时，只补一次缺失反馈的输入；不将它计为消耗或延长输出。
                observation['feedback_retry_used'] = True
            char.logger.info(f'椿散守 bloom heavy retry count={observation["retries"]} '
                             f'forte={forte:.3f} reason={reason} output={elapsed:.3f}')
            self.capture_bloom_diagnostics(char, 'heavy_retry', reason)
            check_budget()
            self.task.mouse_up()
            char.sleep(0.1, check_combat=False)
            check_budget()
            self.task.next_frame()
            check_budget()
            in_team, current_index, _ = self.task.in_team()
            if not in_team or current_index != char.index:
                raise RotationTimeout('bloom heavy recovery actor or HUD changed')
            end_condition = self.task.combat_end_condition
            if end_condition is not None and end_condition():
                raise RotationTimeout('bloom heavy recovery combat ended')
            current_forte = float(char.get_forte(budding=True))
            current_marker = self.task.find_one('camellya_budding', threshold=0.7)
            resource_remaining = 0.05 < current_forte <= 1
            bloom_unreadable = current_forte < 0 and bool(current_marker)
            target_locked = bool(self.task.has_target())
            can_resume = (target_locked and not self.task.in_liberation
                          and (resource_remaining or bloom_unreadable))
            check_budget()
            if can_resume:
                self.task.mouse_down()
                char.sleep(0.1, check_combat=False)
                check_budget()
            char.logger.info(f'椿散守 bloom heavy recovery reason={reason} resumed={can_resume} '
                             f'forte={current_forte:.3f} marker={bool(current_marker)} target={target_locked}')
            observation['forte'] = None
            observation['samples'] = 0
            observation['unknown_since'] = None

    @staticmethod
    def bloom_resume_budget(forte):
        return min(Rotation.BLOOM_OUTPUT_TIMEOUT,
                   max(2.0, min(1.0, max(0.0, float(forte))) * Rotation.BLOOM_OUTPUT_TIMEOUT
                       + Rotation.BLOOM_TAIL_GRACE))

    def bloom_output(self, char, resume=None):
        resumed = resume is not None
        started = resume['started'] if resumed else time.time()
        wall_started = resume['wall_started'] if resumed else time.monotonic()
        resume_forte = float(resume.get('forte', -1)) if resumed else -1.0
        output_budget = self.BLOOM_OUTPUT_TIMEOUT
        if resumed:
            # 已开花段不重新假设 8 秒完整时长；用首帧剩余回路给出保守预算，消耗证据仍由尾段规则复核。
            output_budget = self.bloom_resume_budget(resume_forte)
            deadline = wall_started + output_budget + self.BLOOM_TAIL_GRACE + 1.0
        else:
            deadline = wall_started + self.BLOOM_WALL_TIMEOUT
        target_wait_wall = 0.0
        target_wait_active = 0.0
        wait_count = 0
        observation = {'forte': None, 'since': 0.0, 'samples': 0, 'retries': 0}
        progress = []
        tail_grace = False
        recheck_started = None
        progress_reset = None
        progress_reset_detail = None
        outcome = 'interrupted'
        original_target_enemy = self.task.target_enemy
        had_instance_override = 'target_enemy' in self.task.__dict__
        current_forte = -1.0
        current_marker = None
        current_elapsed = 0.0
        same_character = False
        resource_observed = resumed and resume_forte > self.BLOOM_ENDED_FORTE

        def check_hard_budget():
            self.check_timeout()
            wall = time.monotonic() - wall_started
            if wall >= self.BLOOM_WALL_TIMEOUT:
                raise RotationTimeout('bloom state did not end: wall timeout')
            if wall - target_wait_wall >= output_budget + self.BLOOM_TAIL_GRACE:
                raise RotationTimeout('bloom state did not end: tail grace timeout')

        def check_recheck_deadline():
            recheck_expired = (recheck_started is not None
                               and time.monotonic() - wall_started - recheck_started >= self.BLOOM_TAIL_RECHECK)
            if recheck_expired:
                if not tail_grace:
                    raise RotationTimeout('bloom state did not end: entry recheck timeout')
                raise RotationTimeout('bloom state did not end: tail progress lost')

        def check_budget():
            nonlocal tail_grace, recheck_started, progress_reset
            check_hard_budget()
            wall = time.monotonic() - wall_started
            output_wall = wall - target_wait_wall
            if output_wall < output_budget:
                return
            status = self.bloom_tail_status(progress, output_wall)
            progressing = status['reason'] == 'progressing'
            check_recheck_deadline()
            if not progressing:
                if not tail_grace:
                    if recheck_started is None:
                        can_recheck = self.bloom_entry_recheck_allowed(progress, output_wall, progress_reset_detail)
                        if can_recheck:
                            can_recheck = 0 < current_forte <= 0.25 and bool(current_marker)
                        finishing_observed = (same_character and resource_observed
                                              and (resumed or current_elapsed >= 5.1)
                                              and 0 <= current_forte <= self.BLOOM_COMPLETE_FORTE)
                        if not can_recheck and not finishing_observed:
                            raise RotationTimeout('bloom state did not end: output timeout')
                        recheck_started = wall
                        log_tail('entry_recheck_start' if can_recheck else 'completion_recheck_start',
                                 output_wall, status)
                    else:
                        transition_can_settle = progress_reset in ('forte_rise', 'forte_unknown')
                        resource_near_empty = 0 <= current_forte <= self.BLOOM_COMPLETE_FORTE
                        evidence_lost = current_forte > 0.25 or not (transition_can_settle or resource_near_empty)
                        if evidence_lost:
                            raise RotationTimeout('bloom state did not end: entry evidence lost')
                    return
                if progress_reset == 'retarget':
                    raise RotationTimeout('bloom state did not end: tail progress lost')
                if recheck_started is None:
                    recheck_started = wall
                    log_tail('recheck_start', output_wall, status)
                # 宽限已获准时，颜色闪烁或滑窗暂缺证据允许短时复核；硬上限不延后。
                return
            if recheck_started is not None:
                log_tail('recheck_recovered' if tail_grace else 'entry_recheck_recovered', output_wall, status)
                recheck_started = None
            progress_reset = None
            if not tail_grace:
                tail_grace = True
                char.logger.info(f'椿散守 bloom tail grace start forte={progress[-1][1]:.3f} '
                                 f'output_wall={output_wall:.3f} budget={self.BLOOM_TAIL_GRACE} '
                                 f'samples={len(progress)}')

        def log_tail(event, output_wall, status):
            history = [(round(at, 3), round(value, 3)) for at, value in progress[-12:]]
            char.logger.info(f'椿散守 bloom tail decision event={event} output_wall={output_wall:.3f} '
                             f'status={status} history_reset={progress_reset} history={history} '
                             f'reset_detail={progress_reset_detail} '
                             f'recheck_started={recheck_started} recheck_budget={self.BLOOM_TAIL_RECHECK}')

        def log_target_context(stage, output_wall):
            try:
                has_name, no_name = self.task.get_target_names()
                scale = 1.2 if self.task.is_browser() else 1.1
                boxes = [self.task.get_box_by_name(has_name).scale(scale),
                         self.task.get_box_by_name('box_target_enemy_long'),
                         self.task.get_box_by_name('target_box_long2')]
                best = None
                for box in boxes:
                    best = self.task.find_best_match_in_box(box, [has_name, no_name], threshold=0.6)
                    if best:
                        break
                target_state = 'unknown'
                target_match = None
                if best:
                    target_state = 'locked' if best.name == has_name else 'unlocked'
                    target_match = (best.name, round(best.confidence, 3), best.x, best.y)
                in_team, current_index, _ = self.task.in_team()
                foe = self.task.find_one('boss_break_shield') or self.task.find_one('boss_break_lock')
                char.logger.info(f'椿散守 bloom target context stage={stage} output_wall={output_wall:.3f} '
                                 f'target_state={target_state} target_match={target_match} '
                                 f'boss_indicator={bool(foe)} in_team={in_team} current_index={current_index} '
                                 f'forte={current_forte:.3f} heavy=down '
                                 f'distance=unknown hit=unknown airborne=unknown')
            except Exception as error:
                char.logger.debug(f'椿散守 bloom target context skipped stage={stage}: {error}')

        def timed_target_enemy(wait=True, time_out=None, check_health=False):
            nonlocal target_wait_wall, target_wait_active, wait_count, progress_reset, recheck_started
            nonlocal progress_reset_detail, current_forte, current_marker, resource_observed, ended_frames
            if not wait:
                return original_target_enemy(wait=wait, time_out=time_out, check_health=check_health)
            check_budget()
            remaining = min(deadline, self.phase_started + self.PHASE_TIMEOUT) - time.monotonic()
            requested = self.task.target_enemy_time_out if time_out is None else time_out
            allowed = max(0.0, min(requested, remaining))
            wait_started = time.monotonic()
            active_before = char.time_elapsed_accounting_for_freeze(started)
            wait_count += 1
            result = None
            log_target_context('wait_start', time.monotonic() - wall_started - target_wait_wall)
            char.logger.info(f'椿散守 bloom target wait start count={wait_count} budget={allowed:.3f}')
            try:
                result = original_target_enemy(wait=True, time_out=allowed, check_health=check_health)
            finally:
                duration = max(0.0, time.monotonic() - wait_started)
                target_wait_wall += duration
                # 先由同一冻结时钟求差，避免锁敌与动画冻结重叠时重复扣时。
                target_wait_active += max(0.0, char.time_elapsed_accounting_for_freeze(started) - active_before)
                observation['forte'] = None
                observation['samples'] = 0
                progress.clear()
                progress_reset = 'retarget'
                progress_reset_detail = None
                recheck_started = None
                current_forte = -1.0
                current_marker = None
                resource_observed = False
                ended_frames = 0
                log_target_context('wait_end', time.monotonic() - wall_started - target_wait_wall)
                char.logger.info(f'椿散守 bloom target wait end count={wait_count} elapsed={duration:.3f} '
                                 f'total_wait={target_wait_wall:.3f} recovered={result}')
            check_budget()
            return result

        # 通用锁敌可等待 30 秒；导入包仅在本队伍开花段计时并限制预算，退出时恢复实例原状。
        # 待通用锁敌提供预算及计时回调后，可移除此临时包装。
        self.task.target_enemy = timed_target_enemy
        char.last_forte = 0
        ended_frames = 0
        try:
            self.task.mouse_down()
            while True:
                # 硬预算永远优先；同一新帧的结束证据须先于软超时判断，避免耗尽时被直接抛出。
                check_hard_budget()
                elapsed = max(0.0, char.time_elapsed_accounting_for_freeze(started) - target_wait_active)
                current_elapsed = elapsed
                current_marker = self.task.find_one('camellya_budding', threshold=0.7)
                current_forte = float(char.get_forte(budding=True))
                sampled_output_wall = time.monotonic() - wall_started - target_wait_wall
                resource_observed = resource_observed or current_forte > self.BLOOM_ENDED_FORTE
                in_team, current_index, _ = self.task.in_team()
                same_character = in_team and current_index == char.index
                # 结束候选帧两格读数约 5.68%，三格约 8.15%；仅结束确认容忍两格，进入和停滞阈值不变。
                # 必须仍看到当前椿、标记消失且连续两帧成立，不能单凭小回路值认定完成。
                bloom_ended = (same_character and not current_marker
                               and 0 <= current_forte <= self.BLOOM_COMPLETE_FORTE
                               and (resumed or elapsed >= 5.1))
                check_hard_budget()
                ended_frames = ended_frames + 1 if bloom_ended else 0
                completed = ended_frames >= 2
                if completed:
                    outcome = 'completed'
                    char.logger.info(f'椿散守 bloom completion confirmed elapsed={elapsed:.3f} '
                                     f'forte={current_forte:.3f} ended_frames={ended_frames} resumed={resumed}')
                    break
                # 软复核到期后只接受已经取得的结束证据，不再等待或发送输入；硬预算仍在最前。
                check_recheck_deadline()
                check_budget()
                sampled_wait_count = wait_count
                executor = self.task.__dict__.get('_executor')
                sampled_frame = getattr(executor, 'frame', None)
                char.check_combat()
                frame_changed = executor is not None and sampled_frame is not executor.frame
                if sampled_wait_count != wait_count or frame_changed:
                    continue
                check_budget()
                char.check_target(True)
                frame_changed = executor is not None and sampled_frame is not executor.frame
                if sampled_wait_count != wait_count or frame_changed:
                    continue
                check_budget()
                output_wall = sampled_output_wall
                previous_sample = progress[-1] if progress else None
                reset_reason = self.record_bloom_progress(progress, current_forte, output_wall)
                if reset_reason:
                    progress_reset = reset_reason
                    progress_reset_detail = {'reason': reset_reason, 'at': output_wall,
                                             'previous': previous_sample, 'current': current_forte}
                self.maintain_bloom_heavy(char, observation, elapsed, forte=current_forte,
                                          bloom_visible=same_character and bool(current_marker)
                                                        and not self.task.in_liberation,
                                          check_budget=check_budget)
                self.task.next_frame()
        except RotationTimeout as error:
            outcome = str(error)
            output_wall = time.monotonic() - wall_started - target_wait_wall
            log_tail('exit', output_wall, self.bloom_tail_status(progress, output_wall))
            self.capture_bloom_diagnostics(char, 'state_timeout', outcome)
            raise
        finally:
            if had_instance_override:
                self.task.target_enemy = original_target_enemy
            else:
                del self.task.target_enemy
            self.task.mouse_up()
            wall = max(0.0, time.monotonic() - wall_started)
            elapsed = max(0.0, char.time_elapsed_accounting_for_freeze(started) - target_wait_active)
            char.logger.info(f'椿散守 bloom timing outcome={outcome} wall={wall:.3f} '
                             f'output={elapsed:.3f} output_wall={max(0.0, wall - target_wait_wall):.3f} '
                             f'target_wait={target_wait_wall:.3f} wait_count={wait_count} '
                             f'heavy_retries={observation["retries"]} tail_grace={tail_grace} '
                             f'tail_extra={max(0.0, wall - target_wait_wall - output_budget):.3f} '
                             f'resumed={resumed} resume_forte={resume_forte:.3f} '
                             f'output_budget={output_budget:.3f} ended_frames={ended_frames}')

PACKAGE_ID = 'chunsanshou-v1.1.9-live-return'


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


class Camellya(TeamMember, BuiltinCamellya):
    _rotation_class = Rotation

    def ephemeral_ready(self):
        if self._team_members_match():
            return CamellyaTeamVision.skill(self)['ready']
        return super().ephemeral_ready()

    def get_forte(self, budding=False):
        if self._team_members_match():
            if budding:
                return CamellyaTeamVision.budding_forte(self)['value']
            return CamellyaTeamVision.forte(self)['value']
        return super().get_forte(budding)
