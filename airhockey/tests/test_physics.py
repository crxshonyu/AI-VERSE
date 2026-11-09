"""Independent event/dynamics checks; no camera, GPIO, or motor is required."""

import copy
import math
import random
import unittest

from airhockey.config import DEFAULTS, defense_y
from airhockey.physics import (
    Track,
    advance_reflect,
    move_time_s,
    predict_impact,
    reflected_path,
)
from airhockey.tracking import PuckTracker


def collision_reference(x, vx, duration, left, right):
    """Integrate actual wall-hit events without using triangle-wave folding."""
    remaining = duration
    while remaining > 1e-11 and abs(vx) > 1e-12:
        wall = right if vx > 0 else left
        dt = (wall - x) / vx
        if dt > remaining:
            return x + vx * remaining, vx
        x = wall
        remaining -= dt
        vx = -vx
    return x, vx


def dynamics_reference(x, velocity, target, vmax, accel):
    """Small-step braking-distance controller; independent of travel-time formula.

    Includes current signed velocity, velocity saturation, and final rest.
    A 20 ms comparison tolerance permits numerical switching chatter.
    """
    dt = 0.0001
    for step in range(250000):
        if abs(x - target) < 0.002 and abs(velocity) < 0.05:
            return step * dt
        stopping_position = x + velocity * abs(velocity) / (2 * accel)
        a = accel if target > stopping_position else -accel
        next_velocity = max(-vmax, min(vmax, velocity + a * dt))
        x += (velocity + next_velocity) * 0.5 * dt
        velocity = next_velocity
    raise AssertionError("reference controller did not settle")


class PhysicalPredictionReview(unittest.TestCase):
    def setUp(self):
        self.table = copy.deepcopy(DEFAULTS["table"])

    def test_many_bounces_against_event_integration(self):
        rng = random.Random(2049)
        left, right = 15.0, 585.0
        for _ in range(500):
            x = rng.uniform(left, right)
            vx = rng.uniform(-5000, 5000)
            duration = rng.uniform(0.001, 3)
            expected_x, expected_vx = collision_reference(x, vx, duration, left, right)
            actual_x, actual_vx = advance_reflect(x, vx, duration, left, right)
            self.assertAlmostEqual(actual_x, expected_x, places=7)
            self.assertAlmostEqual(actual_vx, expected_vx, places=7)
            path, _ = reflected_path(x, 0, vx, 100, duration, left, right)
            self.assertAlmostEqual(path[-1][0], expected_x, places=7)
            self.assertAlmostEqual(path[-1][1], 100 * duration, places=7)

    def test_exact_wall_hit_velocity_points_inward(self):
        self.assertEqual(advance_reflect(500, 85, 1, 15, 585), (585, -85))
        self.assertEqual(advance_reflect(100, -85, 1, 15, 585), (15, 85))

    def test_deadline_uses_capture_age_once(self):
        track = Track(10.0, 520.0, 300.0, 1200.0, 1000.0)
        fresh = predict_impact(track, self.table, 10.0, extra_latency_s=0.025)
        delayed = predict_impact(track, self.table, 10.080, extra_latency_s=0.025)
        self.assertIsNotNone(fresh)
        self.assertIsNotNone(delayed)
        self.assertAlmostEqual(fresh.x_mm, delayed.x_mm)
        self.assertAlmostEqual(fresh.remaining_s - delayed.remaining_s, 0.080)
        duration = (defense_y(self.table) - 300.0) / 1000.0
        expected, _ = collision_reference(520.0, 1200.0, duration, 15.0, 585.0)
        self.assertAlmostEqual(delayed.x_mm, expected)
        self.assertAlmostEqual(delayed.remaining_s, duration - 0.080 - 0.025)

    def test_no_future_intercept_after_passage_or_outgoing(self):
        passed = Track(1, 300, defense_y(self.table) + 1, 0, 1000)
        outgoing = Track(1, 300, 500, 0, -1000)
        obsolete = Track(1, 300, 500, 0, 1000)
        self.assertIsNone(predict_impact(passed, self.table, 1))
        self.assertIsNone(predict_impact(outgoing, self.table, 1))
        self.assertIsNone(predict_impact(obsolete, self.table, 2))
        self.assertIsNone(predict_impact(Track(1, math.nan, 500, 0, 1000), self.table, 1))

    def test_acceleration_time_agrees_with_dynamic_integration(self):
        cases = [
            (0, 0, 300),
            (0, -100, 30),
            (0, 100, 0),
            (0, 100, 2),
            (300, -120, 20),
            (100, 120, 0),
            (0, 0, 0.1),
            (0, 0, 10),
        ]
        for start, velocity, target in cases:
            with self.subTest(start=start, velocity=velocity, target=target):
                analytic = move_time_s(start, velocity, target, 120, 600)
                simulated = dynamics_reference(start, velocity, target, 120, 600)
                self.assertAlmostEqual(analytic, simulated, delta=0.020)

    def test_infeasible_motor_parameters(self):
        self.assertEqual(move_time_s(0, 0, 10, 0, 100), math.inf)
        self.assertEqual(move_time_s(0, 121, 10, 120, 100), math.inf)
        self.assertEqual(move_time_s(0, math.nan, 10, 120, 100), math.inf)


class TrackingReview(unittest.TestCase):
    def setUp(self):
        self.table = copy.deepcopy(DEFAULTS["table"])
        self.tracker = PuckTracker(self.table, copy.deepcopy(DEFAULTS["tracking"]))

    def update_line(self, count=5, start_time=100.0):
        for i in range(count):
            t = start_time + i / 60.0
            self.tracker.update(100 + i * 5, 100 + i * 10, t)
        return self.tracker.state

    def test_irregular_capture_intervals_and_long_uptime(self):
        origin = 2_000_000.0
        for dt in [0.0, 0.012, 0.028, 0.049, 0.065]:
            self.tracker.update(100 + 900 * dt, 100 + 1200 * dt, origin + dt)
        self.assertAlmostEqual(self.tracker.state.vx_mm_s, 900, places=4)
        self.assertAlmostEqual(self.tracker.state.vy_mm_s, 1200, places=4)

    def test_duplicate_and_older_frame_cannot_refresh_state(self):
        state = self.update_line()
        history_length = len(self.tracker.history)
        self.assertIsNone(self.tracker.update(200, 300, state.timestamp_s))
        self.assertIsNone(self.tracker.update(210, 310, state.timestamp_s - 0.01))
        self.assertEqual(len(self.tracker.history), history_length)
        self.assertEqual(self.tracker.state.timestamp_s, state.timestamp_s)

    def test_missed_track_is_invalidated_and_needs_fresh_samples(self):
        state = self.update_line()
        self.tracker.miss(state.timestamp_s + 0.2)
        self.assertIsNone(self.tracker.state)
        self.assertIsNone(self.tracker.association(state.timestamp_s + 0.2))
        self.assertIsNone(self.tracker.update(200, 200, 101))
        self.assertIsNone(self.tracker.update(205, 210, 101 + 1 / 60))
        self.assertIsNotNone(self.tracker.update(210, 220, 101 + 2 / 60))

    def test_wall_bounce_reacquires_post_collision_velocity(self):
        xs = [510, 530, 550, 570, 580, 560, 540, 520]
        for i, x in enumerate(xs):
            state = self.tracker.update(x, 100 + 10 * i, 100 + i / 60)
            if i == 5:
                self.assertIsNone(state)
                self.assertEqual(self.tracker.reason, "collision_reacquire")
        self.assertIsNotNone(state)
        self.assertAlmostEqual(state.vx_mm_s, -1200, places=5)
        self.assertAlmostEqual(state.vy_mm_s, 600, places=5)

    def test_paddle_direction_change_reacquires(self):
        self.update_line()
        prior = self.tracker.state.timestamp_s
        self.assertIsNone(self.tracker.update(125, 120, prior + 1 / 60))
        self.assertEqual(self.tracker.reason, "collision_reacquire")
        self.assertIsNone(self.tracker.update(130, 100, prior + 2 / 60))
        state = self.tracker.update(135, 80, prior + 3 / 60)
        self.assertLess(state.vy_mm_s, 0)


if __name__ == "__main__":
    unittest.main()
