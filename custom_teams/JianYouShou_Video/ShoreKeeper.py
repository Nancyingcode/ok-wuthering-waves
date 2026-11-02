from src.char.Iuno import Iuno as BuiltinIuno
from src.char.ShoreKeeper import ShoreKeeper as BuiltinShoreKeeper


class ShoreKeeper(BuiltinShoreKeeper):
    VIDEO_TEAM_ID = 'BV18kW3zoEei-v1.0.3'
    NORMAL_THREE_TIME = 1.4
    NORMAL_TWO_TIME = 0.9
    SHORT_STAGES = {
        'opening_shore': ('opening_jianxin_2', NORMAL_THREE_TIME),
        'loop_shore_1': ('loop_jianxin_1', NORMAL_THREE_TIME),
        'loop_shore_2': ('loop_jianxin_2', NORMAL_TWO_TIME),
    }

    def _video(self):
        iuno = self.task.has_char(BuiltinIuno)
        if getattr(iuno, 'VIDEO_TEAM_ID', None) == self.VIDEO_TEAM_ID and iuno.video_team():
            return iuno
        return None

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        video = self._video()
        if video is not None:
            return video.video_priority(self.name)
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def healer_full_con_switch_locked(self):
        # 本队的下一位由阶段确定，不能再由治疗者通用锁推迟视频中的短切。
        if self._video() is not None:
            return False
        return super().healer_full_con_switch_locked()

    def do_perform(self):
        video = self._video()
        if video is None:
            return super().do_perform()
        if not video.video_enter(self):
            return
        short_stage = self.SHORT_STAGES.get(video.video_phase)
        if short_stage is not None:
            next_phase, duration = short_stage
            video.video_normal(self, duration)
            video.video_handoff(self, next_phase)
            return

        if 'liberation_attempted' not in video.video_done:
            video.video_fill(self, target=0.75, use_heavy=True, timeout=4)
            video.video_support_liberation(self)
            video.video_done.add('liberation_attempted')
        if 'echo' not in video.video_done:
            self.click_echo(time_out=0)
            video.video_done.add('echo')
        if not video.video_support_ready(self, use_heavy=True):
            self.logger.warning('Video Shorekeeper concerto incomplete; continuing charge before outro')
            return
        video.video_handoff(self, 'jianxin_buff')

    def on_combat_end(self, chars):
        video = self.task.has_char(BuiltinIuno)
        if getattr(video, 'VIDEO_TEAM_ID', None) == self.VIDEO_TEAM_ID:
            video.video_reset()
