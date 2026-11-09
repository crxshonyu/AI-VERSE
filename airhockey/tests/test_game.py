"""Score pulses and operator-controlled match transitions without GPIO hardware."""

import unittest

from airhockey.game import Game, GoalDebouncer


class GoalSensorReview(unittest.TestCase):
    def setUp(self):
        self.goals = GoalDebouncer(clear_s=0.15, min_pulse_s=0.002, now_s=0)
        self.goals.poll(0.2)

    def test_between_frame_pulse_is_preserved(self):
        # A 4 ms pulse begins and ends before the next 60 Hz video frame.
        self.goals.edge("robot_goal", True, 0.201)
        self.goals.edge("robot_goal", False, 0.205)
        self.assertEqual(self.goals.poll(0.2167), [("robot_goal", 0.201)])
        self.assertEqual(self.goals.poll(0.3), [])

    def test_subthreshold_glitch_is_rejected(self):
        self.goals.edge("robot_goal", True, 0.201)
        self.goals.edge("robot_goal", False, 0.202)
        self.assertEqual(self.goals.poll(0.3), [])

    def test_held_blockage_never_creates_repeated_scores(self):
        self.goals.edge("human_goal", True, 1)
        self.assertEqual(self.goals.poll(1.01), [("human_goal", 1)])
        for t in [1.1, 2, 10, 1000]:
            self.assertEqual(self.goals.poll(t), [])
        self.assertFalse(self.goals.all_clear(1000))

    def test_release_must_stay_clear_before_rearming(self):
        self.goals.edge("robot_goal", True, 1)
        self.assertEqual(self.goals.poll(1.01), [("robot_goal", 1)])
        self.goals.edge("robot_goal", False, 1.1)
        self.goals.edge("robot_goal", True, 1.2)  # only 100 ms clear
        self.assertEqual(self.goals.poll(1.21), [])
        self.goals.edge("robot_goal", False, 1.3)
        self.assertFalse(self.goals.all_clear(1.4))
        self.assertTrue(self.goals.all_clear(1.46))
        self.goals.edge("robot_goal", True, 1.5)
        self.assertEqual(self.goals.poll(1.51), [("robot_goal", 1.5)])

    def test_initially_blocked_sensor_does_not_score(self):
        self.goals.initial("human_goal", True, 1)
        self.assertFalse(self.goals.all_clear(100))
        self.assertEqual(self.goals.poll(100), [])
        self.goals.edge("human_goal", False, 101)
        self.goals.edge("human_goal", True, 101.2)
        self.assertEqual(self.goals.poll(101.21), [("human_goal", 101.2)])

    def test_sensor_timestamp_order_and_independent_sensors(self):
        self.goals.edge("robot_goal", True, 1.02)
        self.goals.edge("human_goal", True, 1.01)
        self.goals.edge("human_goal", False, 0.9)  # obsolete edge ignored
        self.assertEqual(self.goals.poll(1.03), [("human_goal", 1.01), ("robot_goal", 1.02)])
        self.assertTrue(self.goals.sensors["human_goal"].active)


class GameStateReview(unittest.TestCase):
    def test_blocked_start_and_resume_are_refused(self):
        game = Game(150)
        self.assertFalse(game.start(0, sensors_clear=False))
        self.assertEqual(game.state, "READY")
        self.assertTrue(game.start(1, sensors_clear=True))
        self.assertTrue(game.goal("robot_goal", 2))
        self.assertFalse(game.start(3, sensors_clear=False))
        self.assertEqual(game.state, "GOAL")
        self.assertTrue(game.start(4, sensors_clear=True))

    def test_scorers_match_opposite_goal_and_pause_after_score(self):
        game = Game(150)
        game.start(0)
        self.assertTrue(game.goal("robot_goal", 1))
        self.assertEqual((game.human_score, game.robot_score), (1, 0))
        self.assertEqual(game.state, "GOAL")
        self.assertFalse(game.goal("robot_goal", 2))
        game.start(3)
        self.assertTrue(game.goal("human_goal", 4))
        self.assertEqual((game.human_score, game.robot_score), (1, 1))

    def test_pause_and_puck_retrieval_count_toward_session_time(self):
        game = Game(150)
        game.start(10)
        game.pause()
        self.assertEqual(game.remaining(60), 100)
        game.tick(160)
        self.assertEqual(game.state, "FINISHED")
        self.assertFalse(game.start(161))
        self.assertEqual(game.remaining(170), 0)

    def test_expired_goal_is_not_counted(self):
        game = Game(150)
        game.start(10)
        self.assertFalse(game.goal("robot_goal", 160))
        self.assertEqual((game.human_score, game.robot_score), (0, 0))
        self.assertEqual(game.state, "FINISHED")

    def test_reset_is_explicit_new_match_and_fault_cannot_resume(self):
        game = Game(150)
        game.start(0)
        game.goal("robot_goal", 1)
        game.fault("link")
        self.assertFalse(game.start(2))
        game.reset()
        self.assertEqual(game.state, "READY")
        self.assertEqual((game.human_score, game.robot_score), (0, 0))
        self.assertIsNone(game.started_at)

    def test_predeadline_pulse_validated_after_expiration_counts_once(self):
        game = Game(150)
        game.start(10)
        goals = GoalDebouncer(now_s=10)
        goals.poll(11)
        goals.edge("robot_goal", True, 159.999)
        game.tick(160.000)
        self.assertEqual(game.state, "FINISHED")
        events = goals.poll(160.003)
        self.assertEqual(events, [("robot_goal", 159.999)])
        self.assertTrue(game.goal(*events[0]))
        self.assertEqual(game.state, "FINISHED")
        self.assertEqual(game.human_score, 1)
        self.assertFalse(game.goal("robot_goal", 159.999))

    def test_expiration_during_pause_has_no_delayed_scoring_window(self):
        game = Game(150)
        game.start(10)
        game.pause()
        game.tick(160.003)
        self.assertFalse(game.goal("robot_goal", 159.999))
        self.assertEqual(game.human_score, 0)

    def test_old_preround_event_does_not_score_after_resume(self):
        game = Game(150)
        game.start(10)
        self.assertFalse(game.goal("robot_goal", 9.9))
        game.pause()
        self.assertTrue(game.start(30))
        self.assertFalse(game.goal("robot_goal", 29.9))
        self.assertEqual(game.state, "RUNNING")
        self.assertTrue(game.goal("robot_goal", 30.1))
        self.assertEqual(game.human_score, 1)


if __name__ == "__main__":
    unittest.main()
