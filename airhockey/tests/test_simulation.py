"""A scored synthetic shot must wait for the game's pause/resume edges."""

import copy
import unittest

from airhockey.config import DEFAULTS
from airhockey.simulation import SimulationSource, demo_config


class SimulationRegression(unittest.TestCase):
    def test_goal_does_not_restart_before_debounce_and_operator_resume(self):
        source = SimulationSource(demo_config(copy.deepcopy(DEFAULTS)))
        source.set_active(True)
        shot = source._shot
        source._score("robot_goal", 10.0)
        source.set_active(True)  # Main game hasn't consumed its sensor yet.
        self.assertTrue(source.waiting_goal)
        self.assertEqual(source._shot, shot)
        source.set_active(False)  # Main game is now GOAL.
        source.set_active(True)  # Explicit resume.
        self.assertFalse(source.waiting_goal)
        self.assertEqual(source._shot, shot + 1)


if __name__ == "__main__":
    unittest.main()
