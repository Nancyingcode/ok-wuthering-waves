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
        return Rotation.budding_forte_from_observations(observations)

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
    # 尾段判据：小幅抬升不再清窗，只有确认回满才重置；累计降幅与相对起点用于「打完了却量不出来」。
    BLOOM_TAIL_RESET_FORTE = 0.25
    BLOOM_TAIL_DROP_TOTAL = 0.03
    BLOOM_TAIL_CONSUMED_RATIO = 0.65
    # 1.2.6：贴零稳态。窗口尾部连续贴零（<= BLOOM_ENDED_FORTE）的读数跨度达到该值即视为已打完，
    # 最近一次下降过旧不再阻断尾段出口；八个硬约束常量与既有门限一律不变。
    BLOOM_TAIL_ZERO_SPAN = 0.75
    # 1.2.7：中高位稳态（P3）。读数在容差内完全不动、整段零下降、样本新鲜、重击补按已用尽，
    # 且仍在开花中（同角色 + 开花标记可见）时，输出预算耗尽后按完成收尾；1.2.6 贴零判据、
    # 尾段门限与八个硬约束常量一律不动。
    BLOOM_TAIL_STEADY_MIN = 0.20
    # 上限刻意压在 0.75：0.799 这类满格平台按验收基线 D1/D3 仍必须走补按与超时，不进稳态出口。
    BLOOM_TAIL_STEADY_MAX = 0.75
    BLOOM_TAIL_STEADY_SPAN = 1.0
    BLOOM_TAIL_STEADY_TOLERANCE = 0.005
    BLOOM_TAIL_STEADY_RETRIES = 2
    # 1.2.8：三格地板出口。实测两次收尾时回路稳定停在三格（forte≈0.082），它同时高于
    # BLOOM_COMPLETE_FORTE=0.06 与 BLOOM_ENDED_FORTE=0.05，又 <=0.25 使尾段 reason 只会落在
    # stale_decrease / insufficient_decreases，两条既有出口都不成立；这里新增一条与 1.2.7
    # 中高位出口并列的独立地板出口，两个结束确认阈值与八个硬约束常量一律不动。
    # 上限只覆盖三格（0.082）并留识别抖动余量：四格 ≈0.109 与中高位 0.20 以上仍按原判据。
    BLOOM_TAIL_FLOOR_MAX = 0.10
    BLOOM_TAIL_FLOOR_SPAN = 1.0
    BLOOM_TAIL_FLOOR_RETRIES = 2
    # 1.2.18：贴地三格「仍在 progressing」的停滞出口。1.2.17 run2 实测一段 forte=0.082、
    # steady_range=0.0、steady_hold=0.485、consumed_ratio=0.918、decreases=6、drop_age=0.657，
    # reason 因此落在 progressing；1.2.8 地板出口要求 reason 为 stale_decrease/insufficient_decreases
    # 且 steady_hold >= 1.0，被双重挡住，只能烧满 8+2 秒抛 tail grace timeout（tail_extra=+2.078）。
    # 尾部平窗跨度取 0.4 秒：对应实测 0.485 秒的下侧，仍在下降的形态（跨度不足）交回原判据。
    BLOOM_TAIL_FLOOR_STALL_SPAN = 0.4
    # 1.2.19：整段零读数且全程丢锁的早退。1.2.18 run2 的 streak=84 段实测 samples=0、
    # estimate_fallback=79、estimate_hold=7.986、estimate_drops=0，同段识别遥测只有一帧
    # budding_forte=0.996/estimated=True，而 target_state=unlocked、target_match=('no_target', …)、
    # check_target Retry/Release 69/65、heavy_retries=0、bloom target wait 一次都没调用：
    # 8 秒里根本没有锁定目标，回路读数不可能下降，重击补按也判不出停滞。
    # 丢锁跨度取 2.0 秒，与 1.2.11 的开环等待同量级；任何可读帧一律交回原判据。
    BLOOM_OUTPUT_UNTARGETED_SPAN = 2.0
    # 1.2.9：预算边界读数断流出口。三段失败里只有 9.000 那段属判据层：最后可读样本 8.046
    # forte=0.156、consumed_ratio=0.836、9.000 时 sample_age=0.954，首帧不可读起于 8.562，随后被
    # 0.4 秒复核到期判成 tail progress lost。8.016 / 8.047 整段零可读样本（samples=0、
    # consumed_ratio=-1），没有消耗证据，属识别层，不在这里放行。
    BLOOM_TAIL_UNREADABLE_MAX_AGE = 1.0
    BLOOM_TAIL_UNREADABLE_MIN_SAMPLES = 3
    BLOOM_TAIL_UNREADABLE_FORTE_MAX = 0.25
    # 1.2.10：已消耗稳态尾段出口。1.2.9 实测 8.000 那次读数稳定停在 0.279（13 个样本、span=1.437、
    # decreases=0、sample_age=0.0、consumed_ratio=0.697、steady_flat=True、steady_hold=1.437），
    # 它高于 1.2.8 地板窗 0.10，又因 <=0.25 使 reason 永远落在 insufficient_decreases，1.2.7 的
    # outside_tail 门与 1.2.9 的不可读门都接不住。该段 heavy_retries=0，因此这里不设补按门：
    # 只要读数在预算耗尽后仍完全不动、且相对本段峰值已消耗 >=65%，就按完成收尾。
    # 真未打完的 outside_tail（0.823 / 0.700，消耗 10.7% / 26.3%）仍按原超时退出，是这条出口的反例护栏。
    BLOOM_TAIL_CONSUMED_SPAN = 1.0
    # 1.2.20：已消耗尾段被一次上跳重置平窗时的出口。1.2.19 run1 实测 output_wall=8.000、
    # forte=0.279、steady_range=0.0、steady_hold=0.61、steady_flat=False、drop_total=0.025、
    # consumed_ratio=0.72、history_reset=forte_rise（7.375 时 0.279->0.304 一格上跳）。1.2.10 要求
    # steady_hold >= 1.0 且 steady_flat=True，而 7.375 到 8.000 只剩 0.625 秒，平窗永远攒不满；
    # forte=0.279 > 0.10 让地板窗接不住，读数可读又让不可读门接不住。平窗跨度取 0.4 秒。
    BLOOM_TAIL_CONSUMED_STALL_SPAN = 0.4
    # 1.2.11：零读数未开环早退。1.2.10 两轮里两次未收尾都是 cast confirmed 之后整段 8 秒零可读
    # （samples=0、consumed_ratio=-1、history=[]、heavy_retries=0），同帧识别遥测是
    # team_skill={'state': 'no_bloom_ring'}、legacy_red_percent=0.000000、开花标记不可见 —— 这一段
    # 回路环根本没上屏，判据层没有任何输入可判。继续烧完 8 秒预算只会白占相位时间，
    # 这里给一个明确早退把控制权交回轮转；不新开预算、不改任何收尾门限。
    BLOOM_NOT_ENGAGED_AFTER = 2.0
    # 1.2.13：开花回路环读数容差。实测 visible_ticks=39/40 时整帧仍判 unknown（单格被特效吃掉），
    # 随后 8 秒窗口攒不到样本、被兜成 output timeout。这里允许高可见度下个别刻度缺失。
    BLOOM_FORTE_MIN_VISIBLE = 28
    BLOOM_FORTE_FALLBACK_MIN_VISIBLE = 34
    BLOOM_FORTE_MISSING_TOLERANCE = 2
    # 1.2.14：估算读数收敛约束。1.2.13 把缺失刻度按「仍充能」计入，得到的是偏高上界；实测整段每次
    # 采样都走估算时，读数会长期钉在 0.996 / 0.799 这类高位（consumed_ratio 0.0 / 0.199），与
    # 「真没打完」无法区分。这里给连续估算设上限：超过上限仍无精确读数，就当作本帧读不到，交回
    # 1.2.9 的不可读门与原超时，不让上界无限期替代精确读数。
    # 1.2.15：上限改按「连续估算秒数」表达，让降级时机与帧率解耦。
    # 1.2.16：1.2.15 取的 1.2 秒在现场帧率下根本够不到。run 0588d04d 的 8 段开花里连续估算帧数最高
    # 23 帧而 estimate_fallback 全为 0，说明 23 帧累计不足 1.2 秒（帧间隔 < 0.052 秒，≥19 帧/秒）；
    # 而 1.2.14 的 10 帧上限在现场正是约 0.5 秒触发过（streak 18/24/68 均降级）。窗口降到 0.5 秒，
    # 等于恢复 1.2.14 已验证的触发时机，同时保留按秒判定、不随帧率漂移。
    BLOOM_FORTE_ESTIMATE_MAX_SECONDS = 0.5
    # 1.2.17：估算读数仍在消耗时不降级。1.2.16 run1 的 estimate_fallback=62 段实测识别序列从
    # 0.996 一路跌到 0.502 / 0.106，说明上界仍跟着真实消耗走；此时按「连续估算超时」降级会把
    # 真实进度一起抹成 samples=0，判据层零输入后必然烧满预算。这里给估算读数也认下降证据：
    # 本帧较上一估算帧下降 >= 0.015 即视为有效进度、降级窗口从本帧重开。
    BLOOM_FORTE_ESTIMATE_DROP = 0.015
    # 1.2.5：撤下 1.2.4 的满格滑动窗口快速通道，恢复 1.2.3 的平台期 0.4 秒 + 非满格 0.9 秒两条判据。
    BLOOM_COMPLETE_FORTE = 0.06
    CONCERTO_TOPUP_TIMEOUT = 2
    CONCERTO_CONFLICT_SETTLE = 0.25
    CONCERTO_CONFLICT_VALUE = 0.985
    BLOOM_PREPARE_TIMEOUT = 5
    LIBERATION_SHORT_CD = BLOOM_PREPARE_TIMEOUT
    LIBERATION_RECHECK_BUDGET = 0.5
    BLOOM_TARGET_RECOVERY_TIMEOUT = 0.75
    BLOOM_FEEDBACK_TIMEOUT = 1.2
    # 输出段丢锁空等必须与相位预算隔离：单次与累计各自封顶，等待不再吃掉相位预算。
    BLOOM_TARGET_WAIT_MAX = 2.0
    BLOOM_TARGET_WAIT_TOTAL = 3.0
    # 1.2.21：输出段丢锁后的轻量重锁。1.2.20 批 run1 的 bloom output untargeted 段实测
    # output_wall=3.562、unlocked_for=2.052（output 第 1.42 秒起丢锁），同段 check_target
    # Release 80 次 / Retry 85 次，而 target lost try retarget 一次都没打印、wait_count=0：
    # 锁早在输出段就丢了，却始终没有任何重锁动作，读数只能整段不可读、等 1.2.19 的 2 秒早退收段。
    # 连续丢锁达到 3 帧才补一次 0.5 秒短重锁；等待额度仍从上面的单次 2 秒 / 累计 3 秒池里出，
    # 不新开预算、不改任何收尾判据；整段最多两次，避免把「真锁不上」拖成另一种超时类型。
    BLOOM_TARGET_RELOCK_FRAMES = 3
    BLOOM_TARGET_RELOCK_TIMEOUT = 0.5
    BLOOM_TARGET_RELOCK_MAX = 2
    # 1.2.24：越过输出软预算后的单帧结束确认。1.2.23 批 8.437s 那朵实测 tail_grace 起于 8.016、
    # 完成确认落在 8.324（forte 0.082 -> 0.032），0.308 秒过冲全部来自「连续两帧」的确认成本；
    # 同段 budget_guard=28 全为空转复核，把复核点从 8+2 硬上限前移压不下一个字节。
    # 预算内的结束确认仍必须连续两帧；边界后只多认一种「单帧 + 已消耗旁证」形态，
    # 旁证门限复用 BLOOM_TAIL_CONSUMED_RATIO，真未打完与读数不可读形态一律交回原判据。
    BLOOM_BOUNDARY_CONFIRM_RATIO = 0.65
    # 1.2.25：标记仍在屏时的单帧结束确认门限。1.2.24 批 run2 实测 8.032 起宽限、读数已是 0.032
    # （<=0.06）、consumed_ratio=0.943，但开花标记仍在屏，bloom_ended 与 1.2.24 的 boundary_confirm
    # 都被 marker_visible 挡住，只能拖到 9.297 判 tail progress lost（tail_extra=+1.360）。
    # 标记可见本身不足以否定「已打完」，但必须要求已消耗 >=90%；真未打完形态（1.2.21 批
    # forte=0.675 / 消耗 32.3%）仍一律交回原超时。
    BLOOM_BOUNDARY_MARKER_RATIO = 0.90
    # 1.2.26：边界后仍在单调下降时的趋势收尾。1.2.25 批唯一那朵 +0.406（resumed=True、
    # resume_forte=0.996）实测 8.000 时 forte=0.156、reason='progressing'、decreases=13、
    # drop_total=0.247、consumed_ratio=0.943，读数一路降到 0.057 才在 8.343 命中 1.2.25 通道：
    # 过冲全部花在「读数从 0.156 落到 <=0.06」这段真实下降上，而 1.2.25 只认结束阈值。
    # 这里补一段「仍在下降」的独立门：0.20 与 1.2.8 地板窗同量级，只要求明显低于中段读数。
    BLOOM_BOUNDARY_TREND_FORTE = 0.20
    BLOOM_BOUNDARY_TREND_RATIO = 0.90
    BLOOM_BOUNDARY_TREND_DECREASES = 3
    BLOOM_BOUNDARY_TREND_DROP = 0.15
    BLOOM_BOUNDARY_TREND_MAX_AGE = 0.25

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
            # 单帧不可读只标记本帧缺失：保留已有下降证据，仅淘汰过期样本。
            # 旧实现 samples.clear() 会在边界前一次颜色断档就抹掉整段消耗（实测 09:07:48 只剩 1 样本）。
            samples[:] = [(at, value) for at, value in samples if elapsed - at <= 1.5]
            return 'forte_unknown'
        if samples:
            last_time, last_forte = samples[-1]
            if elapsed - last_time > 1.5:
                reset_reason = 'sample_gap'
            elif forte - last_forte >= Rotation.BLOOM_TAIL_RESET_FORTE:
                reset_reason = 'forte_rise'
            if reset_reason:
                samples.clear()
            elif forte - last_forte >= 0.015:
                # 小幅抬升多为颜色抖动或读数跳格：保留下降历史，只记录一次抬升供入口复核。
                reset_reason = 'forte_rise'
        samples.append((elapsed, forte))
        samples[:] = [(at, value) for at, value in samples if elapsed - at <= 1.5]
        return reset_reason

    @staticmethod
    def bloom_tail_baseline(previous, forte):
        # 1.2.5：相对消耗的基线取本段已观测峰值。1.2.4 用「首个可读帧」时，cast 结束那一两帧
        # 回路环正在动或识别偏低，低值被钉死（实测反推 0.403），整段相对判据因此失效。
        # 不可读帧不参与基线更新，保留已记录的峰值。
        if not 0 <= forte <= 1:
            return previous
        return forte if previous is None else max(previous, forte)

    @staticmethod
    def bloom_tail_consumption(samples):
        values = [(at, value) for at, value in samples if 0 <= value <= 1]
        if not values:
            return {'drop_total': 0.0, 'last_drop': 0.0, 'peak': -1.0, 'current': -1.0}
        peak = max(value for _, value in values)
        current = values[-1][1]
        last_drop = values[-2][1] - current if len(values) > 1 else 0.0
        return {'drop_total': peak - current, 'last_drop': last_drop,
                'peak': peak, 'current': current}

    @classmethod
    def bloom_tail_status(cls, samples, elapsed, baseline=None):
        values = [(at, value) for at, value in samples if 0 <= value <= 1]
        last_time, forte = values[-1] if values else (elapsed, -1)
        consumption = cls.bloom_tail_consumption(values)
        decreases = [values[index][0] for index in range(1, len(values))
                     if values[index - 1][1] - values[index][1] >= 0.015]
        sample_age = elapsed - last_time if values else -1
        drop_age = elapsed - decreases[-1] if decreases else -1
        span = last_time - values[0][0] if values else 0
        # 1.2.6：贴零稳态跨度。窗口尾部连续贴零样本的跨度；实测 18:25:38 forte=0.000 与
        # 18:28:46 forte=0.032 都已耗尽，却因最近一次下降超过 0.75 秒判 stale_decrease，
        # 而 bloom_ended 又要求开花标记消失才成立，两侧都不给出口。
        zero_hold = -1.0
        for index in range(len(values) - 1, -1, -1):
            if values[index][1] > cls.BLOOM_ENDED_FORTE:
                break
            zero_hold = last_time - values[index][0]
        # 尾段门限：绝对残量 <=0.25，或以本段观测峰值（bloom_tail_baseline）为基准已消耗 >=65%；峰值不可读时不开放相对判据。
        consumed_ratio = -1.0
        if baseline is not None and 0.05 < baseline <= 1 and 0 <= forte <= 1:
            consumed_ratio = (baseline - forte) / baseline
        inside_tail = 0 <= forte <= 0.25 or consumed_ratio >= cls.BLOOM_TAIL_CONSUMED_RATIO
        zero_steady = len(values) >= 3 and zero_hold >= cls.BLOOM_TAIL_ZERO_SPAN
        steady = cls.bloom_steady_status(values)
        if len(values) < 3:
            reason = 'insufficient_samples'
        elif not inside_tail:
            reason = 'outside_tail'
        elif sample_age > 0.5:
            reason = 'stale_sample'
        elif zero_steady:
            reason = 'progressing'
        elif drop_age > 0.75:
            reason = 'stale_decrease'
        elif consumption['drop_total'] < cls.BLOOM_TAIL_DROP_TOTAL:
            reason = 'insufficient_decreases'
        elif span < 0.25:
            reason = 'short_observation'
        else:
            reason = 'progressing'
        return {'reason': reason, 'samples': len(values), 'forte': round(forte, 3),
                'sample_age': round(sample_age, 3), 'decreases': len(decreases),
                'drop_age': round(drop_age, 3), 'span': round(span, 3),
                'zero_hold': round(zero_hold, 3),
                'steady_hold': steady['steady_hold'],
                'steady_range': steady['steady_range'],
                'steady_flat': steady['steady_flat'],
                'drop_total': round(consumption['drop_total'], 3),
                'consumed_ratio': round(consumed_ratio, 3)}

    @classmethod
    def bloom_steady_status(cls, samples):
        """1.2.7：中高位稳态跨度。从窗口尾部向前扫最近一段「读数在容差内完全一致」的样本。"""
        values = [(at, value) for at, value in samples if 0 <= value <= 1]
        steady = {'steady_hold': -1.0, 'steady_range': -1.0, 'steady_flat': False}
        if len(values) < 3:
            return steady
        last_time, forte = values[-1]
        index = len(values) - 1
        while index > 0 and abs(values[index - 1][1] - forte) <= cls.BLOOM_TAIL_STEADY_TOLERANCE:
            index -= 1
        window = [value for _, value in values[index:]]
        steady['steady_hold'] = round(last_time - values[index][0], 3)
        steady['steady_range'] = round(max(window) - min(window), 3)
        steady['steady_flat'] = (len(window) >= 3
                                 and steady['steady_range'] <= cls.BLOOM_TAIL_STEADY_TOLERANCE
                                 and steady['steady_hold'] >= cls.BLOOM_TAIL_STEADY_SPAN)
        return steady

    @classmethod
    def bloom_steady_completion_allowed(cls, samples, output_wall, output_budget, forte, retries,
                                        same_character, marker_visible, in_liberation):
        """1.2.7：中高位稳态完成判定。只在输出预算耗尽之后可用，且必须仍在开花中。

        与「在打但读数不动」的区分证据：样本新鲜（采集链活着）、读数在 0.5% 容差内完全不动
        且整段零下降（实测消耗 12-16%/s，1 秒内必跨一格）、两次重击补按已用尽（队内已无纠正
        手段）、同角色在场且开花标记仍可见。
        """
        if output_wall < output_budget:
            return False
        if not same_character or not marker_visible or in_liberation:
            return False
        if retries < cls.BLOOM_TAIL_STEADY_RETRIES:
            return False
        if not cls.BLOOM_TAIL_STEADY_MIN <= forte <= cls.BLOOM_TAIL_STEADY_MAX:
            return False
        status = cls.bloom_tail_status(samples, output_wall)
        if status['reason'] != 'outside_tail':
            return False
        if status['sample_age'] < 0 or status['sample_age'] > 0.5:
            return False
        if status['decreases'] != 0:
            return False
        if status['drop_total'] > cls.BLOOM_TAIL_STEADY_TOLERANCE:
            return False
        return status['steady_flat']

    @classmethod
    def bloom_floor_completion_allowed(cls, samples, output_wall, output_budget, forte, retries,
                                       same_character, marker_visible, in_liberation, baseline=None):
        """1.2.8：三格地板完成判定。与 1.2.7 的中高位出口并列，只接贴地三格这一种形态。

        实测 21:43:54 与 21:44:31 两次同形：forte=0.082（约 8.15%，三格）、steady_range=0.0、
        steady_hold >= 1.0、consumed_ratio=0.918、heavy_retries=2、开花标记仍在屏。该读数高于
        BLOOM_COMPLETE_FORTE 与 BLOOM_ENDED_FORTE，所以 bloom_ended 与 1.2.6 的贴零判据都不成立；
        它又 <=0.25 使尾段 reason 只落在 stale_decrease / insufficient_decreases，1.2.7 的
        outside_tail 门同样接不住。
        区分「还没打完」的三道门：读数必须落在三格窗内、本段相对观测峰值已消耗 >=65%、
        尾段必须已经没有任何 progressing 证据；两次重击补按已用尽且仍在开花中才放行。
        """
        if output_wall < output_budget:
            return False
        if not same_character or not marker_visible or in_liberation:
            return False
        if retries < cls.BLOOM_TAIL_FLOOR_RETRIES:
            return False
        if not cls.BLOOM_COMPLETE_FORTE < forte <= cls.BLOOM_TAIL_FLOOR_MAX:
            return False
        status = cls.bloom_tail_status(samples, output_wall, baseline)
        if status['sample_age'] < 0 or status['sample_age'] > 0.5:
            return False
        if status['reason'] not in ('stale_decrease', 'insufficient_decreases'):
            return False
        if status['consumed_ratio'] < cls.BLOOM_TAIL_CONSUMED_RATIO:
            return False
        if status['steady_range'] < 0 or status['steady_range'] > cls.BLOOM_TAIL_STEADY_TOLERANCE:
            return False
        if status['steady_hold'] < cls.BLOOM_TAIL_FLOOR_SPAN:
            return False
        return status['steady_flat']

    @classmethod
    def bloom_floor_stall_allowed(cls, samples, output_wall, output_budget, forte, retries, same_character,
                                  marker_visible, in_liberation, baseline=None):
        """1.2.18：贴地三格「仍在 progressing」的停滞出口。与 1.2.8 地板出口并列，只接尾部已平这一种形态。

        1.2.17 run2 实测：forte=0.082（三格）、steady_range=0.0、steady_hold=0.485、consumed_ratio=0.918、
        decreases=6、drop_age=0.657，尾段 reason 因此落在 progressing。1.2.8 地板出口要求
        reason in ('stale_decrease', 'insufficient_decreases') 且 steady_hold >= 1.0，这条被双重挡住，
        8 秒软边界不接、10 秒硬上限直接抛 tail grace timeout（wall=10.078、tail_extra=+2.078）。
        区分「还在往下跌」与「打不动」的证据：尾部平窗跨度、消耗比与补按用尽；读数不在地板窗、
        尾部仍在变动或消耗不足的形态一律交回原判据。
        """
        if output_wall < output_budget:
            return False
        if not same_character or not marker_visible or in_liberation:
            return False
        if retries < cls.BLOOM_TAIL_FLOOR_RETRIES:
            return False
        if not cls.BLOOM_COMPLETE_FORTE < forte <= cls.BLOOM_TAIL_FLOOR_MAX:
            return False
        status = cls.bloom_tail_status(samples, output_wall, baseline)
        if status['sample_age'] < 0 or status['sample_age'] > 0.5:
            return False
        if status['reason'] != 'progressing':
            return False
        if status['consumed_ratio'] < cls.BLOOM_TAIL_CONSUMED_RATIO:
            return False
        if status['steady_range'] < 0 or status['steady_range'] > cls.BLOOM_TAIL_STEADY_TOLERANCE:
            return False
        return status['steady_hold'] >= cls.BLOOM_TAIL_FLOOR_STALL_SPAN

    @classmethod
    def bloom_unreadable_grace_allowed(cls, samples, output_wall, output_budget, forte, same_character,
                                       marker_visible, in_liberation, baseline=None):
        """1.2.9：预算边界读数断流的受控出口。只接「可读证据齐全、边界后回路环读不到」这一种形态。

        实测 9.000 段：最后可读样本 8.046 forte=0.156、consumed_ratio=0.836、9.000 时 sample_age=0.954，
        首帧不可读出现在 8.562，随后 0.4 秒复核到期被判 tail progress lost——判据层能救的只有它。
        8.016 / 8.047 两段整段零可读样本（samples=0、consumed_ratio=-1、基线从未建立），没有消耗证据，
        一律仍判 output timeout；这里不新开预算，宽限到期仍由 check_hard_budget 抛 tail grace timeout。
        """
        if output_wall < output_budget:
            return False
        if output_wall - output_budget > cls.BLOOM_TAIL_GRACE:
            return False
        if 0 <= forte <= 1:
            return False
        if not same_character or not marker_visible or in_liberation:
            return False
        status = cls.bloom_tail_status(samples, output_wall, baseline)
        if status['reason'] not in ('insufficient_samples', 'stale_sample'):
            return False
        # 顺带堵住「窗口只剩一两个样本」：此时无法证明这段输出真打掉了回路，交回原超时。
        if status['samples'] < cls.BLOOM_TAIL_UNREADABLE_MIN_SAMPLES:
            return False
        # sample_age 即最后可读样本年龄，也等于连续不可读时长：断流过久说明早就断流，不是边界抖动。
        if status['sample_age'] < 0 or status['sample_age'] > cls.BLOOM_TAIL_UNREADABLE_MAX_AGE:
            return False
        if not 0 <= status['forte'] <= cls.BLOOM_TAIL_UNREADABLE_FORTE_MAX:
            return False
        return status['consumed_ratio'] >= cls.BLOOM_TAIL_CONSUMED_RATIO

    @classmethod
    def bloom_consumed_tail_allowed(cls, samples, output_wall, output_budget, forte, same_character,
                                    marker_visible, in_liberation, baseline=None):
        """1.2.10：已消耗稳态尾段出口。只接「预算耗尽后读数仍完全不动、且本段已消耗 >=65%」这一种形态。

        实测 8.000 段：forte=0.279、samples=13、sample_age=0.0、consumed_ratio=0.697、decreases=0、
        steady_flat=True、steady_hold=1.437，且该段 heavy_retries=0。它进不了 1.2.8 地板窗（>0.10），
        又因 <=0.25 让 reason 落在 insufficient_decreases、永远不是 1.2.7 要求的 outside_tail，
        1.2.9 只接不可读帧，于是三条既有出口全部漏掉它。
        区分「还没打完」的证据是消耗比与零下降：0.823 / 0.700 两条 outside_tail 的消耗只有
        10.7% / 26.3%，一律仍判 output timeout；读数不动 >=1.0 秒才认，避免把正在输出的一段截断。
        """
        if output_wall < output_budget:
            return False
        if not same_character or not marker_visible or in_liberation:
            return False
        if not 0 <= forte <= 1:
            return False
        status = cls.bloom_tail_status(samples, output_wall, baseline)
        if status['reason'] not in ('insufficient_decreases', 'stale_decrease'):
            return False
        if status['sample_age'] < 0 or status['sample_age'] > 0.5:
            return False
        if status['consumed_ratio'] < cls.BLOOM_TAIL_CONSUMED_RATIO:
            return False
        if status['steady_range'] < 0 or status['steady_range'] > cls.BLOOM_TAIL_STEADY_TOLERANCE:
            return False
        if status['steady_hold'] < cls.BLOOM_TAIL_CONSUMED_SPAN:
            return False
        return status['steady_flat']

    @classmethod
    def bloom_consumed_tail_stall_allowed(cls, samples, output_wall, output_budget, forte, retries,
                                          same_character, marker_visible, in_liberation, baseline=None):
        """1.2.20：已消耗尾段被一次上跳重置平窗时的出口。与 1.2.10 并列，只接预算边界后的同一形态。

        1.2.19 run1 实测 output_wall=8.000、forte=0.279、samples=45、steady_range=0.0、steady_hold=0.61、
        steady_flat=False、drop_total=0.025、consumed_ratio=0.72，history_reset=forte_rise（7.375 时
        0.279 -> 0.304，一格上跳）：1.2.10 要求 steady_hold >= 1.0 且 steady_flat=True，而 7.375 到
        8.000 只剩 0.625 秒，平窗永远攒不满，只能烧满预算判 output timeout。forte=0.279 高于地板窗
        0.10 让 1.2.8 接不住，读数可读又让 1.2.9 接不住。
        区分「还没打完」的证据：净降幅必须已经小于 BLOOM_TAIL_DROP_TOTAL、尾部平窗跨度、已消耗 >=65%、
        两次补按已用尽；仍在下降或消耗不足的形态一律交回原判据。
        """
        if output_wall < output_budget:
            return False
        if not same_character or not marker_visible or in_liberation:
            return False
        if retries < cls.BLOOM_TAIL_FLOOR_RETRIES:
            return False
        if not 0 <= forte <= 1:
            return False
        status = cls.bloom_tail_status(samples, output_wall, baseline)
        if status['reason'] != 'insufficient_decreases':
            return False
        if status['sample_age'] < 0 or status['sample_age'] > 0.5:
            return False
        if status['consumed_ratio'] < cls.BLOOM_TAIL_CONSUMED_RATIO:
            return False
        if status['drop_total'] > cls.BLOOM_TAIL_DROP_TOTAL:
            return False
        if status['steady_range'] < 0 or status['steady_range'] > cls.BLOOM_TAIL_STEADY_TOLERANCE:
            return False
        return status['steady_hold'] >= cls.BLOOM_TAIL_CONSUMED_STALL_SPAN

    @classmethod
    def bloom_output_never_engaged(cls, samples, elapsed, forte, same_character, marker_visible,
                                  in_liberation, marker_seen=False):
        """1.2.11：开花输出整段零读数时的早退判据。

        1.2.10 两轮两次未收尾同形：cast confirmed 后整段 8 秒 `samples=0`、`consumed_ratio=-1`、
        `history=[]`、`heavy_retries=0`，同帧识别遥测 `team_skill.state='no_bloom_ring'`、
        `legacy_red_percent=0.000000`、`camellya_budding` 不可见。回路环没上屏，`bloom_tail_status`
        只会判 `insufficient_samples`，1.2.8 地板 / 1.2.9 不可读 / 1.2.10 已消耗三条出口都要求
        至少一个可读样本，因此这段只能白烧到 8 秒再抛 output timeout。
        区分「真在开花、只是某一帧读不到」的证据：本段必须一个可读样本都没有，且开花标记不可见；
        只要出现过任一可读读数就交回原判据，避免截断真实输出。
        """
        if elapsed < cls.BLOOM_NOT_ENGAGED_AFTER:
            return False
        if not same_character or marker_visible or in_liberation:
            return False
        # 1.2.12：本段见过标记说明开花确实开过环，标记消失要交给 bloom_marker_end_allowed 收尾，
        # 不能再当作「没开环」提前收段。
        if marker_seen:
            return False
        if 0 <= forte <= 1:
            return False
        return not samples

    @classmethod
    def bloom_output_untargeted(cls, elapsed, target_unlocked_since, marker_visible, same_character,
                               in_liberation, samples):
        """1.2.19：整段零读数且全程丢锁时的早退判据。

        1.2.18 run2 的 streak=84 段实测 samples=0、estimate_fallback=79、estimate_hold=7.986、
        estimate_drops=0，同段识别遥测只有一帧 budding_forte=0.996/estimated=True，而
        target_state=unlocked、target_match=('no_target', …)、check_target Retry/Release 69/65、
        heavy_retries=0、bloom target wait 一次都没调用：这段 8 秒里没有锁定目标，读数不可能下降。
        与 1.2.11 并列而不重叠：1.2.11 要求开花标记不可见（判定没开环），这里要求标记仍在屏
        （确实开着花、只是没打到），且窗口必须零可读样本；判的是早退，不是完成。
        """
        if elapsed < cls.BLOOM_NOT_ENGAGED_AFTER:
            return False
        if target_unlocked_since is None:
            return False
        if elapsed - target_unlocked_since < cls.BLOOM_OUTPUT_UNTARGETED_SPAN:
            return False
        if not same_character or not marker_visible or in_liberation:
            return False
        return not samples

    @classmethod
    def bloom_target_relock_allowed(cls, unlocked_frames, relock_count, same_character,
                                    marker_visible, in_liberation):
        """1.2.21：输出段丢锁后是否补一次轻量重锁。

        1.2.20 批 run1 的 untargeted 段实测 output_wall=3.562、unlocked_for=2.052，同段
        check_target Release 80 次 / Retry 85 次，而 target lost try retarget 一次都没打印、
        wait_count=0：锁定在输出段内丢掉了，却始终没有重锁动作，读数随之整段不可读。
        这里只补「重锁动作」，不新开预算：等待额度沿用 BLOOM_TARGET_WAIT_MAX / TOTAL 池，
        与 1.2.19 的丢锁早退并列 —— 重锁成功时早退窗口自然不复位，失败仍交回原早退与超时。
        """
        if not same_character or not marker_visible or in_liberation:
            return False
        if relock_count >= cls.BLOOM_TARGET_RELOCK_MAX:
            return False
        return unlocked_frames >= cls.BLOOM_TARGET_RELOCK_FRAMES

    @classmethod
    def bloom_boundary_trend_allowed(cls, forte, same_character, marker_seen, in_liberation,
                                     elapsed, resumed, tail_grace, output_wall, output_budget,
                                     samples, baseline=None):
        """1.2.26：越过输出软预算后，读数仍在单调下降时的趋势收尾。

        1.2.25 批唯一那朵 tail_extra=+0.406（resumed=True、resume_forte=0.996）实测边界时
        forte=0.156、reason='progressing'、decreases=13、drop_total=0.247、consumed_ratio=0.943，
        读数降到 0.057 才在 8.343 命中 1.2.25 通道：0.34 秒过冲全花在「从 0.156 落到 0.06」
        这段真实下降上。这里只在宽限已开始后接一段仍在下降的形态：电流读数 <= 0.20、窗口内
        至少三次下降、累计降幅 >= 0.15、样本新鲜、按本段峰值已消耗 >= 90%。真未打完的
        outside_tail（1.2.24 批 0.873 / 消耗 12.4%）被读数与消耗两门同时挡住。
        """
        if output_wall < output_budget:
            return False
        if not tail_grace:
            return False
        if not same_character or in_liberation:
            return False
        if not marker_seen:
            return False
        if not (resumed or elapsed >= 5.1):
            return False
        if not 0 <= forte <= cls.BLOOM_BOUNDARY_TREND_FORTE:
            return False
        status = cls.bloom_tail_status(samples, output_wall, baseline)
        if not 0 <= status['sample_age'] <= cls.BLOOM_BOUNDARY_TREND_MAX_AGE:
            return False
        if status['decreases'] < cls.BLOOM_BOUNDARY_TREND_DECREASES:
            return False
        if status['drop_total'] < cls.BLOOM_BOUNDARY_TREND_DROP:
            return False
        return status['consumed_ratio'] >= cls.BLOOM_BOUNDARY_TREND_RATIO

    @classmethod
    def bloom_boundary_confirm_allowed(cls, forte, same_character, marker_visible, marker_seen,
                                       in_liberation, elapsed, resumed, tail_grace, output_wall,
                                       output_budget, samples, baseline=None):
        """1.2.24：越过输出软预算后的单帧结束确认。

        1.2.23 批 8.437s 那朵实测 tail_grace 起于 8.016、完成确认落在 8.324，forte 由 0.082
        掉到 0.032：这段边界后确实还在真实消耗，但收尾本身被「连续两帧」拖了一帧以上。
        同段 budget_guard=28 只是空转复核（守的是 8+2 秒硬上限），所以过冲压不下去。
        这里只在预算边界之后接受单帧证据，并要求已消耗到 65% 作旁证；预算内规则、两个结束
        阈值与 1.2.7–1.2.23 的全部出口一律不动。
        """
        if output_wall < output_budget:
            return False
        # 1.2.24 只压缩「宽限已开始后仍在补确认」的过冲；首次越过边界一律交回原 check_budget 判宽限，
        # 否则 1.2.5 低首帧形态会被这条通道提前收走（离线用例 test_low_first_readable_frame… 已复现）。
        if not tail_grace:
            return False
        if not same_character or in_liberation:
            return False
        if not marker_seen:
            return False
        if not 0 <= forte <= cls.BLOOM_COMPLETE_FORTE:
            return False
        if not (resumed or elapsed >= 5.1):
            return False
        status = cls.bloom_tail_status(samples, output_wall, baseline)
        consumed = status['consumed_ratio']
        if marker_visible:
            # 1.2.25：标记仍在屏不等于没打完。1.2.24 批 run2 实测读数已到 0.032/0.057（均 <=0.06）、
            # consumed_ratio=0.943，却因标记可见被 bloom_ended 与本通道同时挡住，一路烧到
            # tail progress lost（tail_extra=+1.360）。这里要求已消耗 >=90% 才认单帧结束。
            return consumed >= cls.BLOOM_BOUNDARY_MARKER_RATIO
        return consumed >= cls.BLOOM_BOUNDARY_CONFIRM_RATIO

    @classmethod
    def bloom_marker_end_allowed(cls, forte, same_character, marker_visible, marker_seen,
                                 in_liberation, elapsed, resumed):
        """1.2.12：读数整段不可读时，以开花标记消失作为结束证据。

        1.2.10 两轮两次未收尾都是 cast 后整段 8 秒零可读（samples=0、consumed_ratio=-1），而
        bloom_ended 要求 0 <= forte <= BLOOM_COMPLETE_FORTE，读数不可读时这条永远不成立；
        1.2.11 的未开环早退又要求标记不可见，于是「开花真打完、标记已消失」这种形态只能烧满
        8 秒判 output timeout。这里补一条与 bloom_ended 并列的结束确认：本段必须见过标记
        （没见过说明没开环，交给 not_engaged）、当前标记已消失、同角色在场、非解放态，
        连续两帧仍由 ended_frames 保证。读数可读的形态一律走原阈值，不用标记替代数值证据。
        """
        if not same_character or marker_visible or in_liberation:
            return False
        if not marker_seen:
            return False
        if 0 <= forte <= 1:
            return False
        return resumed or elapsed >= 5.1

    @classmethod
    def budding_forte_from_observations(cls, observations):
        """1.2.13：把逐格观测换算成回路读数。纯 Python 实现，便于离线回放刻度缺失形态。

        旧实现只要充能段里有一格 `not found` 就整帧判 unknown；实测 visible_ticks=39/40 时仍如此，
        于是 8 秒窗口攒不到样本、被兜成 output timeout。这里在高可见度下把缺失格按「仍然充能」
        计入，得到的是偏高的上界读数：上界只会让「已打完」更难成立，不会让收尾阈值提前触发。
        """
        unknown = {'value': -1.0, 'state': 'unknown', 'segments': 0, 'visible_ticks': 0}
        visible = sum(found for found, _ in observations)
        unknown_visible = {**unknown, 'visible_ticks': visible}
        if visible < cls.BLOOM_FORTE_MIN_VISIBLE:
            return unknown_visible
        tolerated = visible >= cls.BLOOM_FORTE_FALLBACK_MIN_VISIBLE
        prefix = 0
        estimated = False
        missing = 0
        for found, filled in observations:
            if not found:
                if not tolerated:
                    return unknown_visible
                missing += 1
                if missing > cls.BLOOM_FORTE_MISSING_TOLERANCE:
                    return unknown_visible
                prefix += 1
                estimated = True
                continue
            if not filled:
                break
            prefix += 1
        if prefix == 0:
            if any(filled for _, filled in observations):
                return unknown_visible
            return {'value': 0.0, 'state': 'empty', 'segments': 0, 'visible_ticks': visible}
        # 只取左侧连续充能刻度，后方孤立的发光特效不计入剩余回路。
        value = (14.5 + 13.5 * (prefix - 1) + 3) / 546
        return {'value': min(1.0, value), 'state': 'filled_estimated' if estimated else 'filled',
                'segments': prefix, 'visible_ticks': visible, 'estimated': estimated}

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
            plateau = observation.get('plateau')
            if forte >= 0.9:
                # 满格平台期单独计时：识别抖动（上下 1-2 格）会让逐帧差值反复越过 0.015，
                # 旧判据因此不断重置停滞起点（1.2.1 实测拖到 1.271 秒才首次补按）。
                # 平台期只认「已真实消耗掉峰值的 3% 以上」为状态变化；向上抖动只抬高峰值，不重置起点。
                # 1.2.3：向上抖动只抬高峰值、不重置起点。1.2.2 把「相对峰值再抬升 >=0.015」
                # 也算状态变化，0.922 那档每次上抖都重置 since，首次补按被拖到 1.112/1.231/2.172 秒。
                if plateau is None or plateau['peak'] - forte >= 0.03:
                    plateau = {'peak': forte, 'since': elapsed, 'samples': 1}
                else:
                    plateau['samples'] += 1
                    plateau['peak'] = max(plateau['peak'], forte)
            else:
                plateau = None
            observation['plateau'] = plateau
            # 1.2.5：撤下 1.2.4 的滑动窗口快速通道——窗口判的是降幅速率而非等级，帧稀疏或读数上跳时
            # 峰值挂在高位反而更晚触发（1.2.4 实测 count=1 的 output 六次里只有一次进 0.4 档）。
            # 恢复 1.2.3 的平台期 0.4 秒与非满格 0.9 秒两条判据。
            changed = previous is None or abs(previous - forte) >= 0.015
            if changed:
                observation.update(forte=forte, since=elapsed, samples=1)
            else:
                observation['samples'] += 1
            stalled = observation['samples'] >= 3 and elapsed - observation['since'] >= 0.9
            # 起步阶段回路常停在满格（实测 0.947 / 0.996 达 0.9-3.1 秒）：满格不动本身就是补按证据，不再等满 0.9 秒。
            near_full_stalled = (plateau is not None and plateau['samples'] >= 3
                                 and elapsed - plateau['since'] >= 0.4)
            if stalled or near_full_stalled:
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
            # 补按后清空平台期记录：第二次补按仍需再攒 0.4 秒，避免连发。
            observation['plateau'] = None

    @staticmethod
    def bloom_resume_budget(forte):
        return min(Rotation.BLOOM_OUTPUT_TIMEOUT,
                   max(2.0, min(1.0, max(0.0, float(forte))) * Rotation.BLOOM_OUTPUT_TIMEOUT
                       + Rotation.BLOOM_TAIL_GRACE))

    @classmethod
    def bloom_estimate_converged(cls, estimate_since, elapsed):
        """1.2.15 / 1.2.16：连续估算多久才降级为不可读。

        1.2.14 用「连续 10 帧」表达上限，帧率一变降级时机就跟着漂；降级该由「估算持续了多久」
        决定，而不是由采样次数决定。1.2.16 把窗口由 1.2 秒降到 0.5 秒：现场约 20 帧/秒时 1.2 秒
        相当于 20 余帧，实测最长连续估算 23 帧仍够不到，收敛整体失效。
        """
        if estimate_since is None:
            return False
        return elapsed - estimate_since >= cls.BLOOM_FORTE_ESTIMATE_MAX_SECONDS

    @classmethod
    def bloom_estimate_progressing(cls, previous, forte):
        """1.2.17：估算读数是否仍在真实消耗（决定要不要重开降级窗口）。

        1.2.16 把窗口降到 0.5 秒后 run1 出现 estimate_fallback=62 的一段，而同段识别序列是
        0.996 -> 0.502 -> 0.106：上界一直在跟着消耗走。这种帧被判不可读只会把真实进度抹成
        samples=0，让判据层零输入、必然烧满预算，所以估算读数也要认下降证据。
        """
        return previous is not None and previous - forte >= cls.BLOOM_FORTE_ESTIMATE_DROP

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
        target_wait_state = {'failed': False}
        observation = {'forte': None, 'since': 0.0, 'samples': 0, 'retries': 0}
        progress = []
        tail_baseline = None
        tail_grace = False
        steady_completion = False
        floor_completion = False
        floor_stall = False
        unreadable_grace = False
        consumed_tail = False
        consumed_stall = False
        not_engaged = False
        untargeted = False
        target_unlocked_since = None
        unlocked_frames = 0
        target_relock_count = 0
        target_relock_readable = 0
        budget_guard = 0
        boundary_confirm = False
        boundary_marker = False
        boundary_trend = False
        marker_end_completion = False
        marker_seen = False
        ring_fallback = False
        estimate_streak = 0
        ring_estimate_streak = 0
        estimate_since = None
        estimate_last = None
        estimate_drops = 0
        estimate_hold = 0.0
        estimate_fallback_frames = 0
        recheck_started = None
        progress_reset = None
        progress_reset_detail = None
        outcome = 'interrupted'
        original_target_enemy = self.task.target_enemy
        had_instance_override = 'target_enemy' in self.task.__dict__
        original_in_combat = self.task.in_combat
        had_in_combat_override = 'in_combat' in self.task.__dict__
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

        def guard_budget():
            """1.2.23：阻塞调用前复核硬预算。

            1.2.22 批实测 tail_extra=+0.171、1.2.17 批更出现过 +2.078：识别、补按与换帧都会阻塞，
            硬预算原先只在帧边界复核，边界因此被推过一个调用周期。宽限窗内的复核次数记入
            budget_guard 确证字段，为 0 说明本段预算从未耗尽。
            """
            nonlocal budget_guard
            if time.monotonic() - wall_started - target_wait_wall >= output_budget:
                budget_guard += 1
            check_hard_budget()

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
            status = self.bloom_tail_status(progress, output_wall, tail_baseline)
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
            nonlocal tail_baseline
            if not wait:
                return original_target_enemy(wait=wait, time_out=time_out, check_health=check_health)
            check_budget()
            remaining = min(deadline, self.phase_started + self.PHASE_TIMEOUT) - time.monotonic()
            requested = self.task.target_enemy_time_out if time_out is None else time_out
            total_remaining = self.BLOOM_TARGET_WAIT_TOTAL - target_wait_wall
            # 丢锁空等与相位预算隔离：单次 2 秒、累计 3 秒封顶，等待不再吃掉相位预算。
            allowed = max(0.0, min(requested, remaining, self.BLOOM_TARGET_WAIT_MAX, total_remaining))
            if allowed <= 0:
                char.logger.info(f'椿散守 bloom target wait skipped count={wait_count + 1} '
                                 f'requested={requested:.3f} phase_remaining={remaining:.3f} '
                                 f'total_remaining={total_remaining:.3f}')
                raise RotationTimeout('bloom state did not end: target wait budget exhausted')
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
                tail_baseline = None
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
            target_wait_state['failed'] = not result
            check_budget()
            return result

        def bloom_in_combat(target=False):
            # 全局 in_combat 的 except Exception 会把段内 RotationTimeout 改写成脱战；
            # 开花段直接走 do_check_in_combat，让预算超时和丢锁证据冒泡回 bloom_output 的局部恢复。
            previous_sleep_check = self.task.in_sleep_check
            self.task.in_sleep_check = True
            try:
                result = self.task.do_check_in_combat(target)
                if not result and target_wait_state['failed']:
                    # 丢锁失败只一次性交回局部恢复，避免后续真实脱战被改写成超时。
                    target_wait_state['failed'] = False
                    raise RotationTimeout('bloom state did not end: target lost during output')
                return result
            finally:
                self.task.in_sleep_check = previous_sleep_check

        # 通用锁敌可等待 30 秒；导入包仅在本队伍开花段计时并限制预算，退出时恢复实例原状。
        # 待通用锁敌提供预算及计时回调后，可移除此临时包装。
        self.task.target_enemy = timed_target_enemy
        self.task.in_combat = bloom_in_combat
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
                # 1.2.12：本段是否见过开花标记。它区分「开花真打完」与「从未开环」两种零读数形态。
                marker_seen = marker_seen or bool(current_marker)
                reading = char.get_forte_reading(budding=True) if hasattr(char, 'get_forte_reading') else None
                current_forte = float(reading['value']) if reading else float(char.get_forte(budding=True))
                # 1.2.13：本段是否用过「缺失刻度按充能计入」的上界回退读数（版本确证字段）。
                if reading and reading.get('estimated'):
                    ring_fallback = True
                    estimate_streak += 1
                    ring_estimate_streak = max(ring_estimate_streak, estimate_streak)
                    # 1.2.17：本帧估算读数仍在下降说明上界跟着真实消耗在走，降级窗口从本帧重开，
                    # 否则连续估算超时会把这段真实进度一起抹成 samples=0。
                    if self.bloom_estimate_progressing(estimate_last, current_forte):
                        estimate_since = elapsed
                        estimate_drops += 1
                    elif estimate_since is None:
                        estimate_since = elapsed
                    estimate_last = current_forte
                    estimate_hold = max(estimate_hold, elapsed - estimate_since)
                else:
                    estimate_streak = 0
                    estimate_since = None
                    estimate_last = None
                # 1.2.14：连续估算超过上限就当作本帧读不到，交回不可读门与原超时；
                # 否则偏高上界会长期替代精确读数，把「真没打完」伪装成「读数纹丝不动」。
                # 1.2.15：上限按秒判定，连续帧数只作遥测；降级帧数另记 estimate_fallback 确证字段。
                if self.bloom_estimate_converged(estimate_since, elapsed):
                    current_forte = -1.0
                    estimate_fallback_frames += 1
                sampled_output_wall = time.monotonic() - wall_started - target_wait_wall
                resource_observed = resource_observed or current_forte > self.BLOOM_ENDED_FORTE
                # 1.2.5：基线随样本更新为本段观测峰值，consumed = 1 - forte / peak；
                # cast 后首帧偏低不再把相对判据钉死在低基线（1.2.4 实测反推基线仅 0.403）。
                tail_baseline = self.bloom_tail_baseline(tail_baseline, current_forte)
                # 本帧消耗先入账，软预算判定才能看到边界那一帧的下降（诊断分析.txt 第 51 行）。
                previous_sample = progress[-1] if progress else None
                reset_reason = self.record_bloom_progress(progress, current_forte, sampled_output_wall)
                if reset_reason:
                    progress_reset = reset_reason
                    progress_reset_detail = {'reason': reset_reason, 'at': sampled_output_wall,
                                             'previous': previous_sample, 'current': current_forte}
                in_team, current_index, _ = self.task.in_team()
                same_character = in_team and current_index == char.index
                # 1.2.11：零读数未开环早退。整段连一个可读样本都没有、开花标记也不可见时，判据层没有
                # 任何输入可判；此时继续烧预算只会白占相位时间，直接收段交回轮转。
                # 1.2.19：只在读不到时采一次锁敌证据，避免给可读帧增加检测开销。
                # 1.2.22：重锁计数与读数可读性解耦。1.2.21 批 run2 的 target lost 段实测
                # output_wall=5.703、estimate_drops=6、relock=0：该段读数多数可读，旧逻辑先看
                # 「读数可读」就清零计数，重锁门永远攒不到 3 帧，锁丢了也没人补。
                # 1.2.19 的丢锁计时口径（target_unlocked_since）逐字不动，两套证据分开维护。
                target_locked = bool(self.task.has_target())
                if 0 <= current_forte <= 1:
                    target_unlocked_since = None
                elif target_locked:
                    target_unlocked_since = None
                elif target_unlocked_since is None:
                    target_unlocked_since = elapsed
                unlocked_frames = 0 if target_locked else unlocked_frames + 1
                if self.bloom_output_untargeted(elapsed, target_unlocked_since,
                                                bool(current_marker), same_character,
                                                self.task.in_liberation, progress):
                    outcome = 'bloom output untargeted'
                    untargeted = True
                    char.logger.info(f'椿散守 bloom output untargeted elapsed={elapsed:.3f} '
                                     f'output_wall={sampled_output_wall:.3f} budget={output_budget:.3f} '
                                     f'forte={current_forte:.3f} marker=True samples={len(progress)} '
                                     f'unlocked_for={elapsed - target_unlocked_since:.3f}')
                    self.capture_bloom_diagnostics(char, 'untargeted', 'output_target_unlocked')
                    break
                # 1.2.21：输出段丢锁后的轻量重锁。1.2.20 批 run1 的 untargeted 段 3.5 秒里
                # check_target Release/Retry=80/85，却一次 target lost try retarget 都没打印：
                # 锁定在输出第 1.42 秒就丢了，之后没有任何补锁动作，读数只能整段不可读。
                # 这里只补重锁动作，等待额度仍由 BLOOM_TARGET_WAIT_MAX/TOTAL 池统一封顶。
                if self.bloom_target_relock_allowed(unlocked_frames, target_relock_count,
                                                    same_character, bool(current_marker),
                                                    self.task.in_liberation):
                    target_relock_count += 1
                    # 1.2.22：重锁会出现在读数可读的帧上，那正是 1.2.21 漏掉的形态，单独计数供验收。
                    reading_readable = 0 <= current_forte <= 1
                    if reading_readable:
                        target_relock_readable += 1
                    char.logger.info(f'椿散守 bloom target relock count={target_relock_count} '
                                     f'unlocked_frames={unlocked_frames} elapsed={elapsed:.3f} '
                                     f'output_wall={sampled_output_wall:.3f} forte={current_forte:.3f} '
                                     f'readable={reading_readable}')
                    self.capture_bloom_diagnostics(char, 'target_relock', 'output_target_unlocked')
                    relocked = self.task.target_enemy(wait=True,
                                                      time_out=self.BLOOM_TARGET_RELOCK_TIMEOUT)
                    if relocked:
                        # 重锁成功才重置 1.2.19 的丢锁计时，让早退只在真的锁不上时成立。
                        target_unlocked_since = None
                        unlocked_frames = 0
                    char.logger.info(f'椿散守 bloom target relock result count={target_relock_count} '
                                     f'recovered={bool(relocked)} total_wait={target_wait_wall:.3f}')
                    continue
                if self.bloom_output_never_engaged(progress, elapsed, current_forte, same_character,
                                                   bool(current_marker), self.task.in_liberation,
                                                   marker_seen):
                    outcome = 'bloom not engaged'
                    not_engaged = True
                    char.logger.info(f'椿散守 bloom not engaged elapsed={elapsed:.3f} '
                                     f'output_wall={sampled_output_wall:.3f} budget={output_budget:.3f} '
                                     f'forte={current_forte:.3f} marker=False samples={len(progress)}')
                    self.capture_bloom_diagnostics(char, 'not_engaged', 'bloom_ring_absent')
                    break
                # 结束候选帧两格读数约 5.68%，三格约 8.15%；仅结束确认容忍两格，进入和停滞阈值不变。
                # 必须仍看到当前椿、标记消失且连续两帧成立，不能单凭小回路值认定完成。
                # 1.2.12：读数整段不可读时，标记消失是本段唯一可得的结束证据；两条并列但互斥
                # （一条要求读数可读，一条要求读数不可读），连续两帧仍由 ended_frames 统一保证。
                marker_end = self.bloom_marker_end_allowed(current_forte, same_character,
                                                           bool(current_marker), marker_seen,
                                                           self.task.in_liberation, elapsed, resumed)
                bloom_ended = (same_character and not current_marker
                               and 0 <= current_forte <= self.BLOOM_COMPLETE_FORTE
                               and (resumed or elapsed >= 5.1)) or marker_end
                check_hard_budget()
                ended_frames = ended_frames + 1 if bloom_ended else 0
                completed = ended_frames >= 2
                if completed:
                    outcome = 'completed'
                    marker_end_completion = marker_end
                    char.logger.info(f'椿散守 bloom completion confirmed elapsed={elapsed:.3f} '
                                     f'forte={current_forte:.3f} ended_frames={ended_frames} resumed={resumed}')
                    if marker_end:
                        char.logger.info(f'椿散守 bloom marker end completion elapsed={elapsed:.3f} '
                                         f'output_wall={sampled_output_wall:.3f} '
                                         f'forte={current_forte:.3f} ended_frames={ended_frames}')
                        self.capture_bloom_diagnostics(char, 'marker_end', 'marker_gone_unreadable')
                    break
                # 1.2.24：越过输出软预算后接受「单帧 + 已消耗旁证」的结束确认，把两帧确认的成本
                # 从过冲里去掉；预算内仍必须连续两帧，1.2.7–1.2.23 的全部出口与门限不动。
                if self.bloom_boundary_confirm_allowed(current_forte, same_character,
                                                       bool(current_marker), marker_seen,
                                                       self.task.in_liberation, elapsed, resumed,
                                                       tail_grace,
                                                       sampled_output_wall, output_budget,
                                                       progress, tail_baseline):
                    outcome = 'completed'
                    boundary_confirm = True
                    # 1.2.25：区分两种放行形态，验收时能核对新门是否真的顶替了被挡住的旧通道。
                    boundary_marker = bool(current_marker)
                    char.logger.info(f'椿散守 bloom boundary confirm elapsed={elapsed:.3f} '
                                     f'marker_visible={boundary_marker} '
                                     f'output_wall={sampled_output_wall:.3f} '
                                     f'budget={output_budget:.3f} forte={current_forte:.3f} '
                                     f'status={self.bloom_tail_status(progress, sampled_output_wall, tail_baseline)}')
                    self.capture_bloom_diagnostics(char, 'boundary_confirm', 'single_frame_after_budget')
                    break
                # 1.2.26：边界后读数仍在单调下降时按完成收尾。1.2.25 批 +0.406 那朵在 8.000 时
                # forte=0.156、decreases=13、drop_total=0.247，读数掉到 0.057 才被 1.2.25 接住，
                # 过冲全部花在这段真实下降里；本门只认明显下降且已消耗 >=90% 的形态。
                if self.bloom_boundary_trend_allowed(current_forte, same_character, marker_seen,
                                                     self.task.in_liberation, elapsed, resumed,
                                                     tail_grace, sampled_output_wall,
                                                     output_budget, progress, tail_baseline):
                    outcome = 'completed'
                    boundary_trend = True
                    char.logger.info(f'椿散守 bloom boundary trend completion elapsed={elapsed:.3f} '
                                     f'output_wall={sampled_output_wall:.3f} '
                                     f'budget={output_budget:.3f} forte={current_forte:.3f} '
                                     f'status={self.bloom_tail_status(progress, sampled_output_wall, tail_baseline)}')
                    self.capture_bloom_diagnostics(char, 'boundary_trend', 'still_decreasing_after_budget')
                    break
                # 1.2.7：输出预算耗尽后先看中高位稳态完成判定，避免「已经打完、只是读数冻结」被
                # 软预算改写成 output timeout；判据只在预算边界之后可用，不提前截断真实输出。
                if self.bloom_steady_completion_allowed(progress, sampled_output_wall, output_budget,
                                                        current_forte, observation['retries'], same_character,
                                                        bool(current_marker), self.task.in_liberation):
                    outcome = 'completed'
                    steady_completion = True
                    char.logger.info(f'椿散守 bloom steady completion elapsed={elapsed:.3f} '
                                     f'output_wall={sampled_output_wall:.3f} budget={output_budget:.3f} '
                                     f'forte={current_forte:.3f} heavy_retries={observation["retries"]} '
                                     f'status={self.bloom_tail_status(progress, sampled_output_wall, tail_baseline)}')
                    self.capture_bloom_diagnostics(char, 'steady_completion', 'mid_band_frozen')
                    break
                # 1.2.8：三格地板出口。0.082 既进不了 bloom_ended（> BLOOM_COMPLETE_FORTE），
                # 也不会被判 outside_tail，只会在宽限里耗尽预算；与 1.2.7 中高位出口并列判定。
                if self.bloom_floor_completion_allowed(progress, sampled_output_wall, output_budget,
                                                       current_forte, observation['retries'], same_character,
                                                       bool(current_marker), self.task.in_liberation,
                                                       tail_baseline):
                    outcome = 'completed'
                    floor_completion = True
                    floor_status = self.bloom_tail_status(progress, sampled_output_wall, tail_baseline)
                    char.logger.info(f'椿散守 bloom floor completion elapsed={elapsed:.3f} '
                                     f'output_wall={sampled_output_wall:.3f} budget={output_budget:.3f} '
                                     f'forte={current_forte:.3f} heavy_retries={observation["retries"]} '
                                     f'status={floor_status}')
                    self.capture_bloom_diagnostics(char, 'floor_completion', 'three_grid_floor')
                    break
                # 1.2.18：贴地三格仍在 progressing 的停滞出口。1.2.17 run2 实测 forte=0.082、
                # steady_range=0.0、steady_hold=0.485、consumed_ratio=0.918，1.2.8 地板出口被 reason
                # 与 steady_hold>=1.0 双重挡住，只能烧满宽限抛 tail grace timeout。
                if self.bloom_floor_stall_allowed(progress, sampled_output_wall, output_budget,
                                                  current_forte, observation['retries'], same_character,
                                                  bool(current_marker), self.task.in_liberation,
                                                  tail_baseline):
                    outcome = 'completed'
                    floor_stall = True
                    stall_status = self.bloom_tail_status(progress, sampled_output_wall, tail_baseline)
                    char.logger.info(f'椿散守 bloom floor stall completion elapsed={elapsed:.3f} '
                                     f'output_wall={sampled_output_wall:.3f} budget={output_budget:.3f} '
                                     f'forte={current_forte:.3f} heavy_retries={observation["retries"]} '
                                     f'status={stall_status}')
                    self.capture_bloom_diagnostics(char, 'floor_stall', 'floor_progressing_stall')
                    break
                # 1.2.9：预算边界读数断流出口。必须排在 check_recheck_deadline() 之前：9.000 段就是
                # 8.562 起不可读、0.4 秒复核到期被判 tail progress lost，判据再准也来不及上场。
                if self.bloom_unreadable_grace_allowed(progress, sampled_output_wall, output_budget,
                                                       current_forte, same_character,
                                                       bool(current_marker), self.task.in_liberation,
                                                       tail_baseline):
                    outcome = 'completed'
                    unreadable_grace = True
                    grace_status = self.bloom_tail_status(progress, sampled_output_wall, tail_baseline)
                    char.logger.info(f'椿散守 bloom unreadable grace completion elapsed={elapsed:.3f} '
                                     f'output_wall={sampled_output_wall:.3f} budget={output_budget:.3f} '
                                     f'forte={current_forte:.3f} '
                                     f'heavy_retries={observation["retries"]} status={grace_status}')
                    self.capture_bloom_diagnostics(char, 'unreadable_grace', 'boundary_read_blackout')
                    break
                # 1.2.10：已消耗稳态尾段出口。8.000 那段的 reason 是 insufficient_decreases（0.279 <=0.25），
                # 地板窗与 outside_tail 门都接不住；同样排在复核到期之前，否则会被判成 tail progress lost。
                if self.bloom_consumed_tail_allowed(progress, sampled_output_wall, output_budget,
                                                    current_forte, same_character,
                                                    bool(current_marker), self.task.in_liberation,
                                                    tail_baseline):
                    outcome = 'completed'
                    consumed_tail = True
                    consumed_status = self.bloom_tail_status(progress, sampled_output_wall, tail_baseline)
                    char.logger.info(f'椿散守 bloom consumed tail completion elapsed={elapsed:.3f} '
                                     f'output_wall={sampled_output_wall:.3f} budget={output_budget:.3f} '
                                     f'forte={current_forte:.3f} '
                                     f'heavy_retries={observation["retries"]} status={consumed_status}')
                    self.capture_bloom_diagnostics(char, 'consumed_tail', 'stalled_after_consumption')
                    break
                # 1.2.20：已消耗尾段被一次上跳重置平窗时的出口。1.2.19 run1 实测 output_wall=8.000、
                # forte=0.279、steady_range=0.0、steady_hold=0.61、steady_flat=False、drop_total=0.025、
                # consumed_ratio=0.72、history_reset=forte_rise；1.2.10 要求 steady_hold>=1.0 与
                # steady_flat=True，边界前平窗攒不满，只能烧满预算判 output timeout。
                if self.bloom_consumed_tail_stall_allowed(progress, sampled_output_wall, output_budget,
                                                         current_forte, observation['retries'],
                                                         same_character, bool(current_marker),
                                                         self.task.in_liberation, tail_baseline):
                    outcome = 'completed'
                    consumed_stall = True
                    consumed_stall_status = self.bloom_tail_status(progress, sampled_output_wall,
                                                                  tail_baseline)
                    char.logger.info(f'椿散守 bloom consumed tail stall completion elapsed={elapsed:.3f} '
                                     f'output_wall={sampled_output_wall:.3f} budget={output_budget:.3f} '
                                     f'forte={current_forte:.3f} '
                                     f'heavy_retries={observation["retries"]} '
                                     f'status={consumed_stall_status}')
                    self.capture_bloom_diagnostics(char, 'consumed_stall', 'consumed_tail_flat_stall')
                    break
                # 软复核到期后只接受已经取得的结束证据，不再等待或发送输入；硬预算仍在最前。
                check_recheck_deadline()
                check_budget()
                sampled_wait_count = wait_count
                executor = self.task.__dict__.get('_executor')
                sampled_frame = getattr(executor, 'frame', None)
                # 1.2.23：这四个调用都会阻塞（识别、补按、换帧），各自的复核点前移到调用之前，
                # 避免 8+2 秒边界只能在下一次帧边界才发现（1.2.17 批 tail_extra=+2.078）。
                guard_budget()
                char.check_combat()
                frame_changed = executor is not None and sampled_frame is not executor.frame
                if sampled_wait_count != wait_count or frame_changed:
                    continue
                check_budget()
                guard_budget()
                char.check_target(True)
                frame_changed = executor is not None and sampled_frame is not executor.frame
                if sampled_wait_count != wait_count or frame_changed:
                    continue
                check_budget()
                guard_budget()
                self.maintain_bloom_heavy(char, observation, elapsed, forte=current_forte,
                                          bloom_visible=same_character and bool(current_marker)
                                                        and not self.task.in_liberation,
                                          check_budget=check_budget)
                guard_budget()
                self.task.next_frame()
        except RotationTimeout as error:
            outcome = str(error)
            output_wall = time.monotonic() - wall_started - target_wait_wall
            log_tail('exit', output_wall, self.bloom_tail_status(progress, output_wall, tail_baseline))
            self.capture_bloom_diagnostics(char, 'state_timeout', outcome)
            raise
        finally:
            if had_in_combat_override:
                self.task.in_combat = original_in_combat
            else:
                del self.task.in_combat
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
                             f'relock={target_relock_count} '
                             f'relock_readable={target_relock_readable} '
                             f'budget_guard={budget_guard} '
                             f'boundary_confirm={boundary_confirm} '
                             f'boundary_marker={boundary_marker} '
                             f'boundary_trend={boundary_trend} '
                             f'heavy_retries={observation["retries"]} tail_grace={tail_grace} '
                             f'steady={steady_completion} floor={floor_completion} floor_stall={floor_stall} '
                             f'unreadable_grace={unreadable_grace} '
                             f'consumed_tail={consumed_tail} consumed_stall={consumed_stall} '
                             f'not_engaged={not_engaged} untargeted={untargeted} '
                             f'marker_end={marker_end_completion} '
                             f'ring_fallback={ring_fallback} '
                             f'ring_estimate_streak={ring_estimate_streak} '
                             f'estimate_fallback={estimate_fallback_frames} '
                             f'estimate_hold={estimate_hold:.3f} '
                             f'estimate_drops={estimate_drops} '
                             f'tail_extra={max(0.0, wall - target_wait_wall - output_budget):.3f} '
                             f'resumed={resumed} resume_forte={resume_forte:.3f} '
                             f'output_budget={output_budget:.3f} ended_frames={ended_frames}')

PACKAGE_ID = 'chunsanshou-v1.2.26-bloom-boundary-trend'


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

    def get_forte_reading(self, budding=False):
        """1.2.13：返回带 state/estimated 的完整读数。

        get_forte 只给数值，收尾判据无从知道这一帧是「精确读数」还是「缺失刻度估算的上界」。
        开花段用本方法拿全量读数，才能把 ring_fallback 确证字段与上界语义一并带进日志。
        """
        if self._team_members_match():
            if budding:
                return CamellyaTeamVision.budding_forte(self)
            return CamellyaTeamVision.forte(self)
        return None
