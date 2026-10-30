import unittest
from unittest.mock import Mock

from src.task.BaseCombatTask import BaseCombatTask


def make_task():
    task = BaseCombatTask.__new__(BaseCombatTask)
    task.info = {'Combat Count': 2}
    task.chars = [Mock()]
    task.get_current_char = Mock(return_value=task.chars[0])
    task.wait_combat = Mock(return_value=True)
    task.in_combat = Mock(side_effect=[True, False])
    task.switch_healer_enabled = Mock(return_value=True)
    for name in ('load_chars', 'switch_healer', 'combat_end', 'wait_in_team_and_world'):
        setattr(task, name, Mock())
    return task


class TestCombatEntry(unittest.TestCase):
    def test_wait_timeout_does_not_count_combat_or_switch_healer(self):
        for result in (None, False):
            with self.subTest(result=result):
                task = make_task()
                task.wait_combat.return_value = result

                self.assertFalse(task.combat_once(wait_combat_time=5, raise_if_not_found=False))

                self.assertEqual(2, task.info['Combat Count'])
                task.load_chars.assert_not_called()
                task.switch_healer.assert_not_called()
                task.chars[0].perform.assert_not_called()
                task.combat_end.assert_not_called()
                task.wait_in_team_and_world.assert_not_called()

    def test_confirmed_combat_runs_rotation_and_cleanup(self):
        task = make_task()

        self.assertTrue(task.combat_once(wait_combat_time=5, raise_if_not_found=False))

        self.assertEqual(3, task.info['Combat Count'])
        task.chars[0].perform.assert_called_once()
        task.combat_end.assert_called_once()
        self.assertEqual(2, task.switch_healer.call_count)
        task.wait_in_team_and_world.assert_called_once()

    def test_no_wait_checks_current_combat_before_running(self):
        for in_combat in (False, True):
            with self.subTest(in_combat=in_combat):
                task = make_task()
                task.in_combat.side_effect = [in_combat, True, False]

                self.assertEqual(in_combat, task.combat_once(wait_combat_time=0))

                task.wait_combat.assert_not_called()
                self.assertEqual(2 + int(in_combat), task.info['Combat Count'])
                self.assertEqual(int(in_combat), task.chars[0].perform.call_count)


if __name__ == '__main__':
    unittest.main()
