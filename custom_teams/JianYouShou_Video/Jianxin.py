from src.char.Iuno import Iuno as BuiltinIuno
from src.char.Jianxin import Jianxin as BuiltinJianxin


class Jianxin(BuiltinJianxin):
    VIDEO_TEAM_ID = 'BV18kW3zoEei-v1.0.3'
    NORMAL_TWO_TIME = 1.0
    SHORT_STAGES = {
        'opening_jianxin_1': 'opening_shore',
        'opening_jianxin_2': 'shore_buff',
        'loop_jianxin_1': 'loop_shore_2',
        'loop_jianxin_2': 'shore_buff',
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
        # 鉴心默认被归类为治疗；通用 16 秒锁会阻止本队与守岸人反复短切。
        if self._video() is not None:
            return False
        return super().healer_full_con_switch_locked()

    def do_perform(self):
        video = self._video()
        if video is None:
            return super().do_perform()
        if not video.video_enter(self):
            return
        next_phase = self.SHORT_STAGES.get(video.video_phase)
        if next_phase is not None:
            video.video_normal(self, self.NORMAL_TWO_TIME)
            video.video_handoff(self, next_phase)
            return

        if 'liberation_attempted' not in video.video_done:
            video.video_support_liberation(self)
            video.video_done.add('liberation_attempted')
        if not video.video_support_ready(self):
            self.logger.warning('Video Jianxin concerto incomplete; continuing charge before outro')
            return
        self.click_echo(time_out=0)
        video.video_handoff(self, 'iuno_burst')

    def on_combat_end(self, chars):
        video = self.task.has_char(BuiltinIuno)
        if getattr(video, 'VIDEO_TEAM_ID', None) == self.VIDEO_TEAM_ID:
            video.video_reset()
