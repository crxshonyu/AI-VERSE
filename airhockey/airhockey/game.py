"""Operator-controlled match state and edge-latched goal scoring."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SensorState:
    active: bool = False
    active_since: float = 0.0
    clear_since: float = 0.0
    last_edge: float = float("-inf")
    armed: bool = False


class GoalDebouncer:
    """One goal per blockage, even when a puck stays in the goal for minutes.

    Both edges are timestamped by GPIO callbacks. Short valid pulses between
    video frames are retained; a stable clear period is required to re-arm.
    """

    def __init__(self, clear_s=0.15, min_pulse_s=0.002, now_s=0.0):
        self.clear_s, self.min_pulse_s = clear_s, min_pulse_s
        self.sensors = {
            name: SensorState(clear_since=now_s) for name in ("human_goal", "robot_goal")
        }
        self.pending = []

    def initial(self, name, active, now_s):
        self.sensors[name] = SensorState(bool(active), now_s, now_s, now_s, False)

    def _advance(self, name, now_s):
        s = self.sensors[name]
        if not s.active:
            if now_s - s.clear_since >= self.clear_s:
                s.armed = True
        elif s.armed and now_s - s.active_since >= self.min_pulse_s:
            self.pending.append((name, s.active_since))
            s.armed = False

    def edge(self, name, active, now_s):
        s = self.sensors[name]
        if now_s < s.last_edge:
            return
        # Advance the previous level before processing its transition.
        self._advance(name, now_s)
        s.last_edge = now_s
        if s.active == bool(active):
            return
        s.active = bool(active)
        if s.active:
            s.active_since = now_s
        else:
            s.clear_since = now_s

    def poll(self, now_s):
        for name in self.sensors:
            self._advance(name, now_s)
        result, self.pending = self.pending, []
        return sorted(result, key=lambda item: item[1])

    def all_clear(self, now_s):
        return all(
            not s.active and now_s - s.clear_since >= self.clear_s for s in self.sensors.values()
        )


class Game:
    def __init__(self, duration_s=150.0):
        self.duration_s = duration_s
        self.reset()

    def reset(self):
        self.state = "READY"
        self.human_score = self.robot_score = 0
        self.started_at = None
        self.reason = "operator_start_required"
        self.last_scorer = None
        self.active_since = None
        self.closed_interval = None

    def tick(self, now_s):
        if self.started_at is not None and self.state not in ("FINISHED", "FAULT"):
            if now_s - self.started_at >= self.duration_s:
                if self.state == "RUNNING" and self.active_since is not None:
                    self.closed_interval = (self.active_since, self.started_at + self.duration_s)
                self.active_since = None
                self.state = "FINISHED"
                self.reason = "time_limit"

    def start(self, now_s, sensors_clear=True):
        self.tick(now_s)
        if self.state not in ("READY", "PAUSED", "GOAL"):
            return False
        if not sensors_clear:
            self.reason = "clear_goal_sensors_before_resume"
            return False
        if self.started_at is None:
            self.started_at = now_s
        self.state, self.reason = "RUNNING", "playing"
        self.active_since = now_s
        self.closed_interval = None
        return True

    def pause(self, reason="operator_pause"):
        if self.state == "RUNNING":
            self.state, self.reason = "PAUSED", reason
            self.active_since = None

    def goal(self, sensor, now_s):
        if self.active_since is not None and now_s < self.active_since:
            return False
        self.tick(now_s)
        # A timestamped pulse just before the deadline may finish its debounce
        # just after it. Accept that one historical goal, keeping FINISHED.
        delayed_finish_goal = (
            self.state == "FINISHED"
            and self.closed_interval is not None
            and self.closed_interval[0] <= now_s < self.closed_interval[1]
        )
        if self.state != "RUNNING" and not delayed_finish_goal:
            return False
        if sensor == "robot_goal":
            self.human_score += 1
            self.last_scorer = "human"
        elif sensor == "human_goal":
            self.robot_score += 1
            self.last_scorer = "robot"
        else:
            raise ValueError("unknown goal sensor")
        self.active_since = None
        self.closed_interval = None
        if not delayed_finish_goal:
            self.state, self.reason = "GOAL", "recover_puck_then_resume"
        return True

    def fault(self, reason):
        self.state, self.reason = "FAULT", str(reason)
        self.active_since = self.closed_interval = None

    def remaining(self, now_s):
        if self.started_at is None:
            return self.duration_s
        return max(0.0, self.duration_s - (now_s - self.started_at))

    def color(self):
        if self.state == "GOAL":
            return (1, 0, 0) if self.last_scorer == "human" else (1, 1, 0)
        return {
            "READY": (1, 1, 1),
            "RUNNING": (0, 1, 0),
            "PAUSED": (0, 0, 1),
            "FINISHED": (0, 1, 1),
            "FAULT": (1, 0, 1),
        }[self.state]
