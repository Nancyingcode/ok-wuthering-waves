import unittest
from unittest.mock import Mock, call

from src.task.FarmEchoTask import FarmEchoTask


def make_task(config=None):
    task = FarmEchoTask.__new__(FarmEchoTask)
    task.tr = str
    task.config = config or {}
    task.info = {}
    task.combat_wait_time = 0
    task.bypass_end_wait = True
    task._in_realm = False
    task._has_treasure = False
    task._just_entered_boss_realm = True
    task.is_revived = False
    task.yolo_time_out = 8
    task.yolo_threshold = 0.5
    task.logger = Mock()
    for name in (
        'in_realm', 'manage_boss_parameters', 'init_parameters', 'teleport_to_boss_enabled',
        'teleport_to_configured_boss_and_prepare', 'in_realm_check', 'manage_boss_interactions',
        'in_combat', 'check_boss_name', 'combat_once', 'pick_echo', 'middle_click',
        'yolo_find_echo', 'run_in_circle_to_find_echo', 'walk_find_echo', 'incr_drop',
        'sleep', 'log_info', 'log_debug', 'walk_to_treasure_and_restart',
        'scroll_and_click_buttons', 'go_to_boss_minimap', 'execute_treasure_hunt'
    ):
        setattr(task, name, Mock())
    task.in_realm.return_value = False
    task.teleport_to_boss_enabled.return_value = False
    task.in_combat.return_value = True
    task.combat_once.return_value = True
    task.walk_to_treasure_and_restart.return_value = False
    return task


class TestFarmEchoPickup(unittest.TestCase):

    def test_no_combat_retries_before_pickup_without_using_farm_count(self):
        for method in ('Walk', 'Yolo', 'Run in Circle'):
            with self.subTest(method=method):
                task = make_task({'Repeat Farm Count': 1, 'Echo Pickup Method': method})
                task.in_combat.return_value = False
                task.pick_echo.return_value = True

                def enter_on_retry(*args, **kwargs):
                    task.pick_echo.assert_not_called()
                    task.walk_find_echo.assert_not_called()
                    task.yolo_find_echo.assert_not_called()
                    task.run_in_circle_to_find_echo.assert_not_called()
                    task.incr_drop.assert_not_called()
                    return task.combat_once.call_count == 2

                task.combat_once.side_effect = enter_on_retry

                task.do_run()

                self.assertEqual(2, task.combat_once.call_count)
                self.assertEqual(2, task.go_to_boss_minimap.call_count)
                task.pick_echo.assert_called_once()
                task.incr_drop.assert_called_once_with(True)

    def test_repeated_missing_combat_stops_without_pickup(self):
        task = make_task({'Repeat Farm Count': 1})
        task.in_combat.return_value = False
        task.combat_once.side_effect = [None, False, None]

        task.do_run()

        self.assertEqual(3, task.combat_once.call_count)
        task.pick_echo.assert_not_called()
        task.walk_find_echo.assert_not_called()
        task.yolo_find_echo.assert_not_called()
        task.run_in_circle_to_find_echo.assert_not_called()
        task.incr_drop.assert_not_called()
        self.assertTrue(task.log_info.call_args.kwargs.get('notify'))

    def test_missing_combat_in_realm_restarts_before_pickup(self):
        task = make_task({'Repeat Farm Count': 1})
        task.in_realm.return_value = True
        task.in_combat.return_value = False
        task.in_world = Mock(return_value=False)
        task.send_key = Mock()
        task.wait_in_team_and_world = Mock()
        task.wait_click_feature = Mock()
        task.combat_once.side_effect = [False, True]
        task.pick_echo.return_value = True
        steps = Mock()
        steps.attach_mock(task.wait_click_feature, 'restart')
        steps.attach_mock(task.pick_echo, 'pickup')

        task.do_run()

        self.assertEqual(2, task.combat_once.call_count)
        self.assertEqual(['restart', 'pickup'], [item[0] for item in steps.mock_calls])
        task.incr_drop.assert_called_once_with(True)
        task.go_to_boss_minimap.assert_not_called()

    def test_successful_combat_resets_entry_retry_budget(self):
        task = make_task({'Repeat Farm Count': 2})
        task.combat_once.side_effect = [False, False, True, False, False, True]
        task.pick_echo.return_value = True

        task.do_run()

        self.assertEqual(6, task.combat_once.call_count)
        self.assertEqual(2, task.pick_echo.call_count)
        self.assertEqual([call(True), call(True)], task.incr_drop.call_args_list)

    def test_pick_echo_on_face_skips_middle_click(self):
        task = make_task({"Repeat Farm Count": 1})
        task.pick_echo.return_value = True

        task.do_run()

        task.pick_echo.assert_called_once()
        task.middle_click.assert_not_called()
        task.incr_drop.assert_called_once_with(True)

    def test_pick_echo_failure_triggers_middle_click_before_movement(self):
        task = make_task({"Repeat Farm Count": 1, "Echo Pickup Method": "Run in Circle"})
        task.pick_echo.return_value = False
        task.run_in_circle_to_find_echo.return_value = True
        steps = Mock()
        steps.attach_mock(task.middle_click, 'middle_click')
        steps.attach_mock(task.run_in_circle_to_find_echo, 'run_in_circle_to_find_echo')

        task.do_run()

        self.assertEqual(
            [call.middle_click(after_sleep=0.2), call.run_in_circle_to_find_echo(circle_count=2)],
            steps.mock_calls
        )
        task.incr_drop.assert_called_once_with(True)

    def test_pick_echo_failure_triggers_middle_click_before_yolo(self):
        task = make_task({"Repeat Farm Count": 1, "Echo Pickup Method": "Yolo"})
        task.pick_echo.return_value = False
        task.yolo_find_echo.return_value = (True, False)
        steps = Mock()
        steps.attach_mock(task.middle_click, 'middle_click')
        steps.attach_mock(task.yolo_find_echo, 'yolo_find_echo')

        task.do_run()

        self.assertEqual(
            [
                call.middle_click(after_sleep=0.2),
                call.yolo_find_echo(turn=False, use_color=False, time_out=8, threshold=0.5)
            ],
            steps.mock_calls
        )
        task.incr_drop.assert_called_once_with(True)


if __name__ == '__main__':
    unittest.main()
