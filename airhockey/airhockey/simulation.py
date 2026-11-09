"""Synthetic BGR camera source: exercises the SAME vision pipeline as a camera.

Idealised short substeps, simple circular paddle contact, no friction/spin.
This is software test data, not evidence of physical defensive performance.
"""

from __future__ import annotations

import copy
import math
import time

from .vision import Frame, cv2, np


def demo_config(original):
    config = copy.deepcopy(original)
    w, h = config["camera"]["width"], config["camera"]["height"]
    tw, tl = config["table"]["width_mm"], config["table"]["length_mm"]
    scale = min((w - 100) / tw, (h - 60) / tl)
    x0, y0 = (w - tw * scale) / 2, (h - tl * scale) / 2
    config["vision"].update(
        image_points=[
            [x0, y0],
            [x0 + tw * scale, y0],
            [x0 + tw * scale, y0 + tl * scale],
            [x0, y0 + tl * scale],
        ],
        calibration_size=[w, h],
        lens_file=None,
        hsv_ranges=[[[5, 100, 80], [25, 255, 255]]],
        min_area_px=10,
        max_area_px=6000,
    )
    config["gpio"]["enabled"] = False
    return config


class SimulationSource:
    def __init__(self, config):
        self.config = config
        self.width, self.height = config["camera"]["width"], config["camera"]["height"]
        self.fps = config["camera"]["fps"]
        self._next = time.monotonic()
        self._last = self._next
        self._sequence = 0
        self._shot = -1
        self.running = False
        self.paddle_x = config["table"]["width_mm"] / 2
        self.events = []
        self._due_clear = []
        self.waiting_goal = False
        self.reset_shot()

    def reset_shot(self):
        self._shot += 1
        t = self.config["table"]
        self.x = t["width_mm"] * (0.25, 0.70, 0.45, 0.65)[self._shot % 4]
        self.y = t["length_mm"] * 0.16
        self.vx = (450.0, -650.0, 750.0, -500.0)[self._shot % 4]
        self.vy = (600.0, 700.0, 550.0, 750.0)[self._shot % 4]
        self.waiting_goal = False

    def set_active(self, active):
        if active and not self.running and self.waiting_goal:
            self.reset_shot()
        self.running = bool(active)

    def set_paddle(self, x):
        self.paddle_x = x

    def _score(self, name, now):
        self.events.append((name, True, now))
        self._due_clear.append((now + 0.04, name))
        self.waiting_goal = True
        # Keep the active-state edge until the game consumes this sensor event.
        # Otherwise the next still-RUNNING frame would restart a shot early.

    def _step(self, dt, now):
        table = self.config["table"]
        r, w, length = (table[k] for k in ("puck_radius_mm", "width_mm", "length_mm"))
        self.x += self.vx * dt
        self.y += self.vy * dt
        if self.x < r:
            self.x, self.vx = 2 * r - self.x, abs(self.vx)
        elif self.x > w - r:
            self.x, self.vx = 2 * (w - r) - self.x, -abs(self.vx)
        dx, dy = self.x - self.paddle_x, self.y - table["paddle_y_mm"]
        distance = math.hypot(dx, dy)
        contact_radius = r + table["paddle_radius_mm"]
        if 1e-9 < distance < contact_radius:
            nx, ny = dx / distance, dy / distance
            normal_speed = self.vx * nx + self.vy * ny
            if normal_speed < 0:
                self.vx -= 2 * normal_speed * nx
                self.vy -= 2 * normal_speed * ny
                self.x = self.paddle_x + nx * (contact_radius + 0.1)
                self.y = table["paddle_y_mm"] + ny * (contact_radius + 0.1)
        inside_goal = abs(self.x - w / 2) < table["goal_width_mm"] / 2 - r
        if self.y > length + r:
            self._score("robot_goal", now)
        elif self.y < -r:
            self._score("human_goal", now)
        elif self.y > length - r and not inside_goal:
            self.y, self.vy = 2 * (length - r) - self.y, -abs(self.vy)
        elif self.y < r and not inside_goal:
            self.y, self.vy = 2 * r - self.y, abs(self.vy)
        # Simulated player reattacks after the puck returns to their half.
        elif self.vy < 0 and self.y < length * 0.20:
            self.reset_shot()

    def pop_events(self):
        result, self.events = self.events, []
        return result

    def read(self, timeout_s=1.0):
        delay = self._next - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        now = time.monotonic()
        dt = min(0.05, max(0.0, now - self._last))
        self._last = now
        self._next = max(self._next + 1.0 / self.fps, now)
        for stamp, name in list(self._due_clear):
            if stamp <= now:
                self.events.append((name, False, stamp))
                self._due_clear.remove((stamp, name))
        if self.running and not self.waiting_goal:
            steps = max(1, math.ceil(dt / (1 / 500.0)))
            for _ in range(steps):
                self._step(dt / steps, now)
                if self.waiting_goal:
                    break
        image = np.full((self.height, self.width, 3), (24, 22, 20), np.uint8)
        pts = np.asarray(self.config["vision"]["image_points"], dtype=np.int32)
        cv2.fillConvexPoly(image, pts, (72, 62, 52))
        cv2.polylines(image, [pts], True, (190, 175, 140), 2)
        table = self.config["table"]
        x0, y0 = self.config["vision"]["image_points"][0]
        scale = (self.config["vision"]["image_points"][1][0] - x0) / table["width_mm"]

        def pixel(x, y):
            return round(x0 + x * scale), round(y0 + y * scale)

        cv2.line(
            image,
            pixel(0, table["length_mm"] / 2),
            pixel(table["width_mm"], table["length_mm"] / 2),
            (110, 100, 90),
            1,
        )
        cv2.circle(
            image,
            pixel(table["width_mm"] / 2, table["length_mm"] / 2),
            round(70 * scale),
            (110, 100, 90),
            1,
        )
        for y in (0, table["length_mm"]):
            cv2.line(
                image,
                pixel((table["width_mm"] - table["goal_width_mm"]) / 2, y),
                pixel((table["width_mm"] + table["goal_width_mm"]) / 2, y),
                (255, 200, 80),
                5,
            )
        cv2.circle(
            image,
            pixel(self.paddle_x, table["paddle_y_mm"]),
            max(3, round(table["paddle_radius_mm"] * scale)),
            (240, 185, 30),
            -1,
        )
        if not self.waiting_goal:
            cv2.circle(
                image,
                pixel(self.x, self.y),
                max(3, round(table["puck_radius_mm"] * scale)),
                (0, 140, 255),
                -1,
            )
        # Orange distractor deliberately outside the table ROI.
        cv2.circle(image, (25, self.height - 25), 8, (0, 140, 255), -1)
        self._sequence += 1
        return Frame(
            image,
            now,
            self._sequence,
            {
                "source": "synthetic",
                "shot": self._shot,
                "ground_truth_x_mm": self.x,
                "ground_truth_y_mm": self.y,
            },
        )

    def close(self):
        pass
