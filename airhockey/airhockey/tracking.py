"""Small time-based regression with reacquisition after collisions and gaps."""

from __future__ import annotations

import math
from collections import deque

from .physics import Track, advance_reflect


class PuckTracker:
    def __init__(self, table, config):
        self.table, self.config = table, config
        self.history = deque(maxlen=int(config["history_samples"]))
        self.state = None
        self.reason = "waiting"

    def reset(self, reason="reset"):
        self.history.clear()
        self.state = None
        self.reason = reason

    def miss(self, timestamp_s):
        if self.history and timestamp_s - self.history[-1][0] > self.config["max_gap_s"]:
            self.reset("lost")

    def association(self, timestamp_s):
        if not self.history:
            return None
        dt = timestamp_s - self.history[-1][0]
        if dt < 0 or dt > self.config["max_gap_s"]:
            return None
        if self.state:
            r = self.table["puck_radius_mm"]
            x, _ = advance_reflect(
                self.state.x_mm, self.state.vx_mm_s, dt, r, self.table["width_mm"] - r
            )
            return x, self.state.y_mm + self.state.vy_mm_s * dt
        return self.history[-1][1:3]

    def update(self, x_mm, y_mm, timestamp_s):
        if not all(math.isfinite(v) for v in (x_mm, y_mm, timestamp_s)):
            self.reset("nonfinite")
            return None
        r, width, length = (self.table[k] for k in ("puck_radius_mm", "width_mm", "length_mm"))
        if not (r - 5 <= x_mm <= width - r + 5 and 0 <= y_mm <= length):
            self.reset("outside_table")
            return None
        if self.history:
            t0, x0, y0 = self.history[-1]
            dt = timestamp_s - t0
            if dt <= 1e-6:
                # Duplicate/older frames must not add evidence or extend validity.
                self.reason = "duplicate_timestamp"
                return None
            if dt > self.config["max_gap_s"]:
                self.reset("reacquiring_after_gap")
            else:
                vx, vy = (x_mm - x0) / dt, (y_mm - y0) / dt
                if math.hypot(vx, vy) > self.config["max_speed_mm_s"]:
                    self.reset("speed_outlier")
                    # New location only becomes useful after fresh consistent frames.
                elif self.state:
                    old = self.state
                    threshold = self.config["turn_min_speed_mm_s"]
                    margin = self.config["wall_margin_mm"] + abs(old.vx_mm_s) * dt
                    near_wall = min(x0 - r, x_mm - r, width - r - x0, width - r - x_mm) < margin
                    x_turn = (
                        near_wall
                        and vx * old.vx_mm_s < 0
                        and min(abs(vx), abs(old.vx_mm_s)) > threshold
                    )
                    y_turn = vy * old.vy_mm_s < 0 and min(abs(vy), abs(old.vy_mm_s)) > threshold
                    # A paddle strike may change vx without reversing vy.
                    old_speed = math.hypot(old.vx_mm_s, old.vy_mm_s)
                    new_speed = math.hypot(vx, vy)
                    cosine = (vx * old.vx_mm_s + vy * old.vy_mm_s) / max(
                        1e-9, old_speed * new_speed
                    )
                    sharp_turn = min(old_speed, new_speed) > 2 * threshold and cosine < 0.65
                    if x_turn or y_turn or sharp_turn:
                        self.reset("collision_reacquire")
        self.history.append((timestamp_s, x_mm, y_mm))
        while (
            len(self.history) > 1
            and timestamp_s - self.history[0][0] > self.config["history_seconds"]
        ):
            self.history.popleft()
        if len(self.history) < self.config["min_samples"]:
            self.state = None
            if self.reason == "waiting":
                self.reason = "acquiring"
            return None
        # Centre time near zero for numerical conditioning after long system uptime.
        times = [s[0] - timestamp_s for s in self.history]
        mean_t = sum(times) / len(times)
        denominator = sum((t - mean_t) ** 2 for t in times)
        if denominator < 1e-12:
            return None
        vx = (
            sum((t - mean_t) * s[1] for t, s in zip(times, self.history, strict=True)) / denominator
        )
        vy = (
            sum((t - mean_t) * s[2] for t, s in zip(times, self.history, strict=True)) / denominator
        )
        if math.hypot(vx, vy) > self.config["max_speed_mm_s"]:
            self.reset("regression_outlier")
            return None
        self.state = Track(timestamp_s, x_mm, y_mm, vx, vy, len(self.history))
        self.reason = "tracking"
        return self.state
