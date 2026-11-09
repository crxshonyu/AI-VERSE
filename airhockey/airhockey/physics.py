"""Short-horizon constant-velocity model with elastic sidewall reflection.

No drag, puck spin, cushion compliance, top/bottom bounce or player collision
is inferred in this model. Fresh observations supersede predictions each frame.
All positions are puck/paddle CENTRES in millimetres, times in seconds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import defense_y


@dataclass(frozen=True)
class Track:
    timestamp_s: float
    x_mm: float
    y_mm: float
    vx_mm_s: float
    vy_mm_s: float
    samples: int = 3


@dataclass(frozen=True)
class Impact:
    x_mm: float
    y_mm: float
    remaining_s: float
    capture_eta_s: float
    frame_age_s: float
    bounces: int
    path: tuple


def reflect_x(x_unfolded, left, right):
    """Triangle-wave folding, valid for negative x and any bounce count."""
    if not all(math.isfinite(v) for v in (x_unfolded, left, right)) or right <= left:
        raise ValueError("reflection requires finite x and positive wall spacing")
    width = right - left
    phase = (x_unfolded - left) % (2.0 * width)
    return left + (phase if phase <= width else 2.0 * width - phase)


def advance_reflect(x, vx, seconds, left, right):
    """Exact ideal sidewall motion, also returning post-bounce velocity."""
    width = right - left
    u = x + vx * seconds
    phase = (u - left) % (2.0 * width)
    position = reflect_x(u, left, right)
    if math.isclose(phase, 0.0, abs_tol=1e-9):
        velocity = abs(vx)
    elif math.isclose(phase, width, abs_tol=1e-9):
        velocity = -abs(vx)
    else:
        velocity = vx if phase < width else -vx
    return position, velocity


def reflected_path(x, y, vx, vy, seconds, left, right):
    """Vertices for an overlay; calculation does not depend on overlay limits."""
    vertices = [(x, y)]
    remaining = seconds
    bounces = 0
    # At a wall the velocity must point inward before taking a time step.
    if x <= left + 1e-9 and vx < 0:
        vx = -vx
    if x >= right - 1e-9 and vx > 0:
        vx = -vx
    while remaining > 1e-9 and abs(vx) > 1e-9:
        wall = right if vx > 0 else left
        to_wall = max(0.0, (wall - x) / vx)
        if to_wall >= remaining - 1e-9:
            break
        x, y = wall, y + vy * to_wall
        remaining -= to_wall
        vx = -vx
        bounces += 1
        if len(vertices) < 64:
            vertices.append((x, y))
        if bounces > 10000:
            raise ValueError("implausible bounce count")
    vertices.append((x + vx * remaining, y + vy * remaining))
    return tuple(vertices), bounces


def predict_impact(
    track, table, now_s, *, extra_latency_s=0.02, horizon_s=2.0, min_incoming_speed_mm_s=40.0
):
    """Predict the first forward crossing; deduct capture age exactly once.

    extra_latency_s is a measured/tunable allowance for serial and scheduling,
    not the motor travel time. Motor acceleration is assessed separately.
    """
    vals = (
        track.timestamp_s,
        track.x_mm,
        track.y_mm,
        track.vx_mm_s,
        track.vy_mm_s,
        now_s,
        extra_latency_s,
        horizon_s,
    )
    if not all(math.isfinite(v) for v in vals) or extra_latency_s < 0:
        return None
    if track.vy_mm_s <= min_incoming_speed_mm_s:
        return None
    r, width = table["puck_radius_mm"], table["width_mm"]
    left, right = r, width - r
    if not left - 5.0 <= track.x_mm <= right + 5.0:
        return None
    y_hit = defense_y(table)
    total = (y_hit - track.y_mm) / track.vy_mm_s
    age = now_s - track.timestamp_s
    if age < -0.010 or total <= 0 or total > horizon_s:
        return None
    age = max(0.0, age)
    remaining = total - age - extra_latency_s
    if remaining <= 0:
        return None
    x = min(right, max(left, track.x_mm))
    target = reflect_x(x + track.vx_mm_s * total, left, right)
    path, bounces = reflected_path(x, track.y_mm, track.vx_mm_s, track.vy_mm_s, total, left, right)
    return Impact(target, y_hit, remaining, total, age, bounces, path)


def move_time_s(position_mm, velocity_mm_s, target_mm, max_speed_mm_s, max_accel_mm_s2):
    """Ideal 1-D minimum time to reach target AND stop (bang-bang/trapezoid).

    Includes initial motion away from the target and overshoot needed to stop.
    This is an estimate using reported step rate, not a closed-loop guarantee.
    It does not model friction, step loss, driver torque, or firmware jitter.
    """
    if not all(
        math.isfinite(v)
        for v in (position_mm, velocity_mm_s, target_mm, max_speed_mm_s, max_accel_mm_s2)
    ):
        return math.inf
    if max_speed_mm_s <= 0 or max_accel_mm_s2 <= 0:
        return math.inf
    delta = target_mm - position_mm
    distance = abs(delta)
    if distance < 1e-9 and abs(velocity_mm_s) < 1e-9:
        return 0.0
    direction = 1.0 if delta >= 0 else -1.0
    speed = velocity_mm_s * direction
    vmax, accel = max_speed_mm_s, max_accel_mm_s2
    if abs(speed) > vmax + 1e-6:
        return math.inf
    elapsed = 0.0
    if speed < 0:
        elapsed += -speed / accel
        distance += speed * speed / (2.0 * accel)
        speed = 0.0
    braking = speed * speed / (2.0 * accel)
    if braking > distance:
        # Must overshoot, brake to rest, then return to the target.
        overshoot = braking - distance
        return elapsed + speed / accel + move_time_s(0, 0, overshoot, vmax, accel)
    peak = math.sqrt(accel * distance + 0.5 * speed * speed)
    if peak <= vmax:
        return elapsed + (2.0 * peak - speed) / accel
    accelerating = (vmax * vmax - speed * speed) / (2.0 * accel)
    decelerating = vmax * vmax / (2.0 * accel)
    cruising = max(0.0, distance - accelerating - decelerating)
    return elapsed + (vmax - speed) / accel + cruising / vmax + vmax / accel
