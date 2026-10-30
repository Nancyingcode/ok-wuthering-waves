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


PHASE_ACTOR = {
    Phase.SHORE_START: 'ShoreKeeper',
    Phase.CAMELLYA_SEED: 'Camellya',
    Phase.SANHUA_PREPARE: 'Sanhua',
    Phase.SHORE_CONCERTO: 'ShoreKeeper',
    Phase.CAMELLYA_WARMUP: 'Camellya',
    Phase.SHORE_RECHARGE: 'ShoreKeeper',
    Phase.SANHUA_OUTRO: 'Sanhua',
    Phase.CAMELLYA_BURST: 'Camellya',
}


class RotationTimeout(Exception):
    pass


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


def supports_team(task):
    from src.char.Camellya import Camellya
    from src.char.Sanhua import Sanhua
    from src.char.ShoreKeeper import ShoreKeeper

    chars = getattr(task, 'chars', ())
    # 自定义队伍代码自行管理动作和交接，不能只接管其中一人。
    return len(chars) == 3 and {type(char) for char in chars} == {Camellya, Sanhua, ShoreKeeper}


def reset_rotation(task):
    task._camellya_sanhua_shorekeeper = None


def active_rotation(task):
    rotation = getattr(task, '_camellya_sanhua_shorekeeper', None)
    if not isinstance(rotation, Rotation):
        return None
    same_members = len(task.chars) == 3 and all(
        current is previous for current, previous in zip(task.chars, rotation.members)
    )
    if not same_members or not supports_team(task):
        reset_rotation(task)
        return None
    return rotation


def planned_switch_target(task, current_char):
    rotation = active_rotation(task)
    if rotation is not None and rotation.switch_from is current_char:
        rotation.check_switch_timeout()
        return rotation.switch_to
    return None


def check_switch_timeout(task):
    rotation = active_rotation(task)
    if rotation is not None:
        rotation.check_switch_timeout()


def perform_team_rotation(char):
    task = char.task
    if not supports_team(task):
        reset_rotation(task)
        return False
    rotation = active_rotation(task)
    if rotation is None:
        rotation = Rotation(task)
        task._camellya_sanhua_shorekeeper = rotation
    rotation.perform(char)
    return True


class Rotation:
    PHASE_TIMEOUT = 24
    SWITCH_TIMEOUT = 6
    BLOOM_OUTPUT_TIMEOUT = 8
    BLOOM_TAIL_GRACE = 2
    BLOOM_TAIL_RECHECK = 0.4
    BLOOM_WALL_TIMEOUT = 16
    BLOOM_ENTRY_RECHECK = 0.4
    LIBERATION_SHORT_CD = 0.5

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
        self.liberation_recheck_pending = False
        self.liberation_recheck_deadline = None
        self.liberation_observation = None

    def check_timeout(self):
        # 动作窗口使用角色的冻结计时；墙钟上限只负责识别卡住后的脱困。
        if time.monotonic() - self.phase_started >= self.PHASE_TIMEOUT:
            raise RotationTimeout(f'{self.phase}: phase timeout')

    def check_switch_timeout(self):
        if self.switch_started is not None and time.monotonic() - self.switch_started >= self.SWITCH_TIMEOUT:
            raise RotationTimeout(f'{self.phase}: switch timeout')

    def handoff(self, char, next_phase):
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
            # 开场已有满协奏时，守岸人这次变奏直接用于暖机，避免再次筹备同一次交接。
            if next_phase == Phase.CAMELLYA_SEED and has_intro:
                self.phase = Phase.CAMELLYA_WARMUP
            # 普通切人不能冒充散华延奏；缺失变奏时只做短暖机再补交接。
            if next_phase == Phase.CAMELLYA_BURST and not has_intro:
                self.phase = Phase.CAMELLYA_WARMUP
            char.logger.info(f'椿散守 phase={self.phase} intro_source={self.intro_source}')

        self.switch_from = char
        self.switch_to = target
        self.switch_started = time.monotonic()
        try:
            char.switch_next_char(post_action=confirm)
            if not confirmed:
                raise RotationTimeout(f'{self.phase}: switch not confirmed')
        finally:
            self.switch_from = None
            self.switch_to = None
            self.switch_started = None

    def perform(self, char):
        previous_skip = self.task.skip_combat_check
        try:
            self.check_timeout()
            if char is not self.chars[PHASE_ACTOR[self.phase]]:
                self.recover(char, 'unexpected active character')
                return
            self.perform_phase(char)
        except RotationTimeout as error:
            self.recover(char, str(error))
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
        char.logger.warning(f'椿散守 recovery: {reason}')
        self.reset()
        if char is not self.chars['ShoreKeeper']:
            try:
                self.handoff(char, Phase.SHORE_START)
            except RotationTimeout as error:
                char.logger.warning(f'椿散守 recovery deferred: {error}')
                self.reset()

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
        should_finish_shore_concerto = char is self.chars['ShoreKeeper'] and 0.8 <= concerto < 1
        should_finish_sanhua_concerto = (char is self.chars['Sanhua'] and self.phase == Phase.SANHUA_OUTRO
                                       and 0.9 <= concerto < 1)
        if should_finish_shore_concerto or should_finish_sanhua_concerto:
            # 近满时原地补尾，避免重新绕一遍椿和散华；额外预算只允许使用一次。
            actor = 'shore' if should_finish_shore_concerto else 'sanhua'
            self.check_timeout()
            char.logger.info(f'椿散守 {actor} concerto retry: concerto={concerto:.3f} budget=2')
            char.continues_normal_attack(2, until_con_full=True)
            self.task.next_frame()
            self.check_timeout()
            if char.is_con_full():
                char.logger.info(f'椿散守 {actor} concerto recovered')
                return
            concerto = char.get_current_con()
        raise RotationTimeout(f'{self.phase}: concerto not ready concerto={concerto:.3f}')

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

        capture_id = f'chunsanshou_bloom/v1_0_10_{time.time_ns()}_{stage}'
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
        capture_id = f'chunsanshou_liberation/v1_0_10_{event_id}_{stage}'
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
                # 只补初次尚未按出的大招；短冷却可在正常准备期间复查，已尝试的不能补按。
                if not deferred and attempt == 1:
                    if not char.has_cd('liberation'):
                        self.liberation_recheck_pending = True
                    else:
                        cd = self.task.get_cd('liberation', char.index)
                        if 0 < cd <= self.LIBERATION_SHORT_CD:
                            self.liberation_recheck_pending = True
                            self.liberation_recheck_deadline = time.monotonic() + self.LIBERATION_SHORT_CD
                            char.logger.info(f'椿散守 liberation short cooldown scheduled cd={cd:.3f} budget=1')
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

    def wait_bloom_ready(self, char, duration):
        deadline = time.monotonic() + duration
        while True:
            self.check_timeout()
            char.check_combat()
            self.task.next_frame()
            if self.liberation_recheck_deadline is not None:
                cd = self.task.get_cd('liberation', char.index)
                if cd <= 0:
                    char.logger.info(f'椿散守 liberation deferred recheck stage=cooldown_expired cd={cd:.3f} budget=1')
                    recheck_started = time.monotonic()
                    self.camellya_liberation(char, deferred=True)
                    # 补大动画不挤占原有开花准备窗口；整段仍受 phase timeout 约束。
                    deadline += time.monotonic() - recheck_started
                    self.task.next_frame()
                elif time.monotonic() >= self.liberation_recheck_deadline or cd > self.LIBERATION_SHORT_CD:
                    self.liberation_recheck_pending = False
                    self.liberation_recheck_deadline = None
                    char.logger.info(f'椿散守 liberation short cooldown cancelled cd={cd:.3f} reason=not_expired')
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
            char.click(interval=0.1)
            char.sleep(0.05)

    def prepare_bloom(self, char):
        reason, concerto = self.wait_bloom_ready(char, 5)
        if self.liberation_recheck_pending:
            char.logger.info('椿散守 liberation deferred recheck stage=bloom_prepare budget=1')
            self.camellya_liberation(char, deferred=True)
            # 补放大招会改变资源与画面，开花必须重新确认当前条件。
            reason, concerto = self.wait_bloom_ready(char, 0)
        if not reason:
            return
        self.capture_bloom_diagnostics(char, 'before_recovery', reason, concerto)
        char.logger.warning(f'椿散守 bloom readiness retry: missing={reason} concerto={concerto:.3f}')
        if reason == 'concerto':
            char.continues_normal_attack(2, until_con_full=True)
            reason, concerto = self.wait_bloom_ready(char, 0)
        else:
            forte = char.get_forte()
            can_consume_forte = 0.01 < forte <= 1
            char.logger.info(f'椿散守 bloom skill recovery: forte={forte:.3f} heavy={can_consume_forte}')
            # 只在现有回路识别有效且有余量时尝试一次短重击；未知回路不当成已空或已满。
            if can_consume_forte:
                try:
                    char.heavy_attack(1.2)
                finally:
                    self.task.mouse_up()
            reason, concerto = self.wait_bloom_ready(char, 1 if can_consume_forte else 2)
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
            # 散华交接后优先开大，不复用通用变奏分支的 4.6 秒重击。
            self.camellya_liberation(char)
            char.has_intro = False
            char.has_sub_dps_intro = False
            if char.resonance_available() and not char.ephemeral_ready():
                char.click_resonance()
            self.prepare_bloom(char)
            self.cast_bloom(char)
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
        if reset is None or reset['reason'] != 'forte_rise' or not samples:
            return False
        previous = reset['previous']
        last_time, forte = samples[-1]
        # 边界前最后一帧重置时只能留下一个样本；补采样在短窗口内完成，消耗判据仍不放宽。
        recent_reset = 0 <= elapsed - reset['at'] <= Rotation.BLOOM_TAIL_RECHECK
        fresh_sample = 0 <= elapsed - last_time <= 0.5
        low_before_reset = previous is not None and 0 <= previous[1] <= 0.25
        return recent_reset and fresh_sample and low_before_reset and 0 < forte <= 0.25

    def maintain_bloom_heavy(self, char, observation, elapsed, forte=None):
        if forte is None:
            forte = float(char.get_forte(budding=True))
        if not 0.05 < forte <= 1:
            # 未知和接近空的回路不能证明重击中断；断开的观测不能累计成停滞。
            observation['forte'] = None
            observation['samples'] = 0
            return
        previous = observation['forte']
        changed = previous is None or abs(previous - forte) >= 0.015
        if changed:
            observation.update(forte=forte, since=elapsed, samples=1)
            return
        observation['samples'] += 1
        stalled = observation['samples'] >= 3 and elapsed - observation['since'] >= 0.9
        if stalled and observation['retries'] < 2:
            observation['retries'] += 1
            char.logger.info(f'椿散守 bloom heavy retry count={observation["retries"]} '
                             f'forte={forte:.3f} stalled={elapsed - observation["since"]:.3f}')
            self.task.mouse_up()
            char.sleep(0.1, check_combat=False)
            self.check_timeout()
            self.task.mouse_down()
            char.sleep(0.1, check_combat=False)
            observation['forte'] = None
            observation['samples'] = 0

    def bloom_output(self, char):
        started = time.time()
        wall_started = time.monotonic()
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

        def check_budget():
            nonlocal tail_grace, recheck_started, progress_reset
            self.check_timeout()
            wall = time.monotonic() - wall_started
            if wall >= self.BLOOM_WALL_TIMEOUT:
                raise RotationTimeout('bloom state did not end: wall timeout')
            output_wall = wall - target_wait_wall
            if output_wall < self.BLOOM_OUTPUT_TIMEOUT:
                return
            if output_wall >= self.BLOOM_OUTPUT_TIMEOUT + self.BLOOM_TAIL_GRACE:
                raise RotationTimeout('bloom state did not end: tail grace timeout')
            status = self.bloom_tail_status(progress, output_wall)
            progressing = status['reason'] == 'progressing'
            if recheck_started is not None and wall - recheck_started >= self.BLOOM_TAIL_RECHECK:
                if not tail_grace:
                    raise RotationTimeout('bloom state did not end: entry recheck timeout')
                raise RotationTimeout('bloom state did not end: tail progress lost')
            if not progressing:
                if not tail_grace:
                    if recheck_started is None:
                        can_recheck = self.bloom_entry_recheck_allowed(progress, output_wall, progress_reset_detail)
                        if can_recheck:
                            can_recheck = (0 < float(char.get_forte(budding=True)) <= 0.25
                                           and bool(self.task.find_one('camellya_budding', threshold=0.7)))
                        if not can_recheck:
                            raise RotationTimeout('bloom state did not end: output timeout')
                        recheck_started = wall
                        log_tail('entry_recheck_start', output_wall, status)
                    elif progress_reset != 'forte_rise' or not progress or not 0 <= progress[-1][1] <= 0.25:
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

        def timed_target_enemy(wait=True, time_out=None, check_health=False):
            nonlocal target_wait_wall, target_wait_active, wait_count, progress_reset, recheck_started
            nonlocal progress_reset_detail
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
                char.logger.info(f'椿散守 bloom target wait end count={wait_count} elapsed={duration:.3f} '
                                 f'total_wait={target_wait_wall:.3f} recovered={result}')
            check_budget()
            return result

        # 通用锁敌可等待 30 秒；导入包仅在本队伍开花段计时并限制预算，退出时恢复实例原状。
        # 待通用锁敌提供预算及计时回调后，可移除此临时包装。
        self.task.target_enemy = timed_target_enemy
        char.last_forte = 0
        try:
            self.task.mouse_down()
            while True:
                check_budget()
                elapsed = max(0.0, char.time_elapsed_accounting_for_freeze(started) - target_wait_active)
                if elapsed >= 5.1 and not self.task.find_one('camellya_budding', threshold=0.7):
                    outcome = 'completed'
                    break
                char.check_combat()
                check_budget()
                char.check_target(True)
                check_budget()
                elapsed = max(0.0, char.time_elapsed_accounting_for_freeze(started) - target_wait_active)
                forte = float(char.get_forte(budding=True))
                output_wall = time.monotonic() - wall_started - target_wait_wall
                previous_sample = progress[-1] if progress else None
                reset_reason = self.record_bloom_progress(progress, forte, output_wall)
                if reset_reason:
                    progress_reset = reset_reason
                    progress_reset_detail = {'reason': reset_reason, 'at': output_wall,
                                             'previous': previous_sample, 'current': forte}
                self.maintain_bloom_heavy(char, observation, elapsed, forte=forte)
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
                             f'tail_extra={max(0.0, wall - target_wait_wall - self.BLOOM_OUTPUT_TIMEOUT):.3f}')
