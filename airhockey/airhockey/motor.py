"""Motor boundary: dry-run model or a nonce-scoped, watchdog USB protocol.

Coordinates exposed to the app are table millimetres. Firmware coordinates are
millimetres from the homed leftmost *allowed paddle-centre* position. Reported
serial position is a step-count estimate, not encoder feedback.
"""

from __future__ import annotations

import math
import secrets
import time
from dataclasses import dataclass
from typing import Callable, Optional


class MotorError(RuntimeError):
    """A motor fault requires the operator to stop and explicitly re-home."""


@dataclass(frozen=True)
class MotorState:
    timestamp_s: float
    position_mm: float
    velocity_mm_s: float
    homed: bool
    fault: Optional[str]
    mode: str


def _validate_limits(x_min_mm, x_max_mm, speed, acceleration):
    if not all(math.isfinite(v) for v in (x_min_mm, x_max_mm, speed, acceleration)):
        raise ValueError("Motor parameters must be finite")
    if x_max_mm <= x_min_mm or speed <= 0 or acceleration <= 0:
        raise ValueError("Motor travel, speed and acceleration must be positive")


class DryRunMotor:
    """Acceleration-limited software model. No GPIO/serial port is opened."""

    def __init__(
        self,
        x_min_mm=30.0,
        x_max_mm=570.0,
        max_speed_mm_s=120.0,
        max_accel_mm_s2=600.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        _validate_limits(x_min_mm, x_max_mm, max_speed_mm_s, max_accel_mm_s2)
        self.x_min_mm, self.x_max_mm = float(x_min_mm), float(x_max_mm)
        self.max_speed_mm_s = float(max_speed_mm_s)
        self.max_accel_mm_s2 = float(max_accel_mm_s2)
        self._clock = clock
        self.position_mm = (self.x_min_mm + self.x_max_mm) / 2
        self.velocity_mm_s = 0.0
        self._target = self.position_mm
        self.homed = True
        self.fault = None
        self._last = self._clock()
        self.mode = "DRY_RUN"

    def connect(self, timeout_s=4.0):
        return self.poll()

    def home(self, timeout_s=25.0):
        self.position_mm = self.x_min_mm
        self.velocity_mm_s = 0.0
        self._target = self.position_mm
        self.homed, self.fault = True, None
        self.mode = "DRY_RUN"
        self._last = self._clock()
        return self.poll()

    def poll(self, now=None):
        now = self._clock() if now is None else now
        elapsed = max(0.0, min(now - self._last, 2.0))
        self._last = now
        if self.homed and self.fault is None:
            # Retargeting can put the target inside the current braking distance.
            # In that case the model must overshoot and reverse, not teleport to
            # the target or discard its velocity when crossing the target line.
            while elapsed > 1e-9:
                dt = min(elapsed, 0.005)
                elapsed -= dt
                distance = self._target - self.position_mm
                dv = self.max_accel_mm_s2 * dt
                previous_v = self.velocity_mm_s
                position_quantum = dv * dt
                if abs(distance) <= position_quantum and abs(previous_v) <= dv:
                    self.position_mm, self.velocity_mm_s = self._target, 0.0
                    continue
                # Reserve one integration step for braking. This keeps a normal
                # target at a travel boundary from overshooting due only to the
                # discrete controller, while respecting the acceleration bound.
                desired_speed = max(0.0, math.sqrt(2 * self.max_accel_mm_s2 * abs(distance)) - dv)
                desired = math.copysign(min(self.max_speed_mm_s, desired_speed), distance)
                self.velocity_mm_s = previous_v + max(-dv, min(dv, desired - previous_v))
                displacement = (previous_v + self.velocity_mm_s) * 0.5 * dt
                next_position = self.position_mm + displacement
                if not self.x_min_mm <= next_position <= self.x_max_mm:
                    self.position_mm = max(self.x_min_mm, min(self.x_max_mm, next_position))
                    self.stop("dry-run travel limit reached")
                    self.mode = "FAULT"
                    raise MotorError(self.fault)
                self.position_mm = next_position
        return MotorState(
            now, self.position_mm, self.velocity_mm_s, self.homed, self.fault, self.mode
        )

    def set_target(self, x_mm, now=None, *, expires_at_s=None):
        """Return False and brake if the monotonic command deadline has expired."""
        if expires_at_s is not None and not math.isfinite(expires_at_s):
            raise ValueError("Motor command deadline must be finite")
        self.poll(now)
        if self.fault or not self.homed:
            raise MotorError("Dry-run motor is stopped; explicitly home first")
        if not math.isfinite(x_mm) or not self.x_min_mm <= x_mm <= self.x_max_mm:
            raise ValueError("Target is outside allowed paddle travel")
        if expires_at_s is not None and self._clock() >= expires_at_s:
            self.hold()
            return False
        self._target = float(x_mm)
        return True

    def hold(self, now=None):
        self.poll(now)
        # Braking distance of the model; HOLD is deliberately not emergency stop.
        braking = self.velocity_mm_s * abs(self.velocity_mm_s) / (2 * self.max_accel_mm_s2)
        self._target = max(self.x_min_mm, min(self.x_max_mm, self.position_mm + braking))

    def stop(self, reason="operator"):
        self.velocity_mm_s = 0.0
        self._target = self.position_mm
        self.homed, self.fault = False, str(reason)

    def close(self):
        self.stop("closed")


class SerialMotor:
    """USB serial controller matching firmware/airhockey_motor.

    The application must call poll() at least every ~50 ms. A stalled
    application cannot be rescued by a background heartbeat: the MCU watchdog
    deliberately disables the motor if the controlling loop stops progressing.
    """

    def __init__(
        self,
        port,
        x_min_mm=30.0,
        x_max_mm=570.0,
        max_speed_mm_s=120.0,
        max_accel_mm_s2=600.0,
        baudrate=115200,
        stale_timeout_s=0.2,
        clock: Callable[[], float] = time.monotonic,
        serial_factory=None,
    ):
        _validate_limits(x_min_mm, x_max_mm, max_speed_mm_s, max_accel_mm_s2)
        if not 0.1 <= stale_timeout_s < 0.25:
            raise ValueError("Host stale timeout must be >=0.1 and <0.25 s")
        self.port, self.baudrate = port, baudrate
        self.x_min_mm, self.x_max_mm = float(x_min_mm), float(x_max_mm)
        self.max_speed_mm_s = float(max_speed_mm_s)
        self.max_accel_mm_s2 = float(max_accel_mm_s2)
        self.stale_timeout_s = stale_timeout_s
        self._clock, self._serial_factory = clock, serial_factory
        self._serial = None
        self._nonce = secrets.token_hex(4)
        self._rx = bytearray()
        self._ready = False
        self._connected = False
        self._latched_fault = None
        self._last_state = None
        self._last_seq = None
        self._last_ping_at = -math.inf
        self._last_pong_at = -math.inf
        self._ping_seq = 0
        self._pending_pings = {}
        self._homing = False
        self.homed, self.fault = False, None
        self.position_mm = self.x_min_mm
        self.velocity_mm_s = 0.0

    def _write(self, line):
        if self._serial is None:
            raise MotorError("Serial motor is not connected")
        data = (line + "\n").encode("ascii")
        try:
            written = self._serial.write(data)
            if written != len(data):
                raise OSError("partial serial write")
        except Exception as exc:
            self._trip("serial write failed: " + str(exc))

    def _best_effort_stop(self):
        if self._serial is not None:
            try:
                self._serial.write(b"STOP\n")
            except Exception:
                pass  # MCU independently stops on its 250 ms watchdog.

    def _trip(self, reason):
        if self._latched_fault is None:
            self._latched_fault = str(reason)
        self.fault = self._latched_fault
        self.homed = False
        self._best_effort_stop()
        raise MotorError(self._latched_fault)

    def _parse_line(self, line, now):
        fields = line.split()
        if not fields:
            return
        # Only our handshake epoch may affect readiness or state.
        if fields[0] == "READY" and len(fields) == 6 and fields[2] == self._nonce:
            try:
                version = int(fields[1])
                travel, speed, acceleration = map(float, fields[3:])
            except ValueError:
                self._trip("Malformed firmware capabilities")
            expected = (self.x_max_mm - self.x_min_mm, self.max_speed_mm_s, self.max_accel_mm_s2)
            actual = (travel, speed, acceleration)
            if version != 1 or any(
                not math.isfinite(a) or abs(a - b) > 0.11
                for a, b in zip(actual, expected, strict=True)
            ):
                self._trip("Firmware/config mismatch (travel, speed, acceleration or protocol)")
            self._ready = True
            return
        if fields[0] == "PONG" and len(fields) == 3 and fields[1] == self._nonce:
            try:
                seq = int(fields[2])
            except ValueError:
                self._trip("Malformed PONG")
            sent = self._pending_pings.pop(seq, None)
            if sent is not None and 0 <= now - sent < self.stale_timeout_s:
                self._last_pong_at = now
            return
        if fields[0] != "STATE" or len(fields) != 8 or fields[1] != self._nonce:
            return
        try:
            seq = int(fields[2])
            relative_x, velocity = float(fields[3]), float(fields[4])
            homed = int(fields[5])
        except ValueError:
            self._trip("Malformed motor state")
        if not 0 <= seq <= 0xFFFFFFFF or homed not in (0, 1):
            self._trip("Invalid motor state fields")
        if not all(math.isfinite(v) for v in (relative_x, velocity)):
            self._trip("Nonfinite motor state")
        if self._last_seq is not None:
            delta = (seq - self._last_seq) & 0xFFFFFFFF
            if delta == 0 or delta >= 0x80000000:
                return
        mode, fault = fields[6], fields[7]
        if mode not in ("IDLE", "HOME", "RUN", "HOLD", "FAULT"):
            self._trip("Unknown motor mode")
        if homed and not -0.2 <= relative_x <= self.x_max_mm - self.x_min_mm + 0.2:
            self._trip("Reported motor position outside travel")
        self._last_seq = seq
        self.position_mm = relative_x + self.x_min_mm
        self.velocity_mm_s = velocity
        self.homed = bool(homed)
        self.fault = None if fault == "NONE" else fault
        self._last_state = MotorState(now, self.position_mm, velocity, self.homed, self.fault, mode)

    def _read(self, now):
        try:
            waiting = self._serial.in_waiting
            if waiting > 4096:
                self._trip("Serial backlog exceeds real-time limit")
            chunk = self._serial.read(min(waiting, 4096)) if waiting else b""
        except MotorError:
            raise
        except Exception as exc:
            self._trip("serial read failed: " + str(exc))
        self._rx.extend(chunk)
        if len(self._rx) > 4096:
            self._trip("Serial input overflow")
        while b"\n" in self._rx:
            raw, _, tail = self._rx.partition(b"\n")
            self._rx = bytearray(tail)
            if len(raw) > 160:
                self._trip("Serial line overflow")
            try:
                self._parse_line(raw.decode("ascii").strip(), now)
            except UnicodeDecodeError:
                self._trip("Non-ASCII serial protocol data")

    def _ping(self, now):
        if now - self._last_ping_at >= 0.05:
            self._ping_seq = (self._ping_seq + 1) & 0xFFFFFFFF
            self._pending_pings = {
                k: v for k, v in self._pending_pings.items() if now - v < self.stale_timeout_s
            }
            self._pending_pings[self._ping_seq] = now
            self._write(f"PING {self._nonce} {self._ping_seq}")
            self._last_ping_at = now

    def connect(self, timeout_s=4.0):
        if self._serial is not None or self._latched_fault is not None:
            raise MotorError("Use a new controller instance for a new connection")
        if self._serial_factory is None:
            try:
                import serial
            except ImportError as exc:
                raise MotorError("Install python3-serial (pyserial)") from exc
            self._serial_factory = serial.Serial
        try:
            self._serial = self._serial_factory(
                self.port, self.baudrate, timeout=0, write_timeout=0.02, exclusive=True
            )
            self._serial.reset_input_buffer()
            start, last_hello = self._clock(), -math.inf
            while self._clock() - start < timeout_s:
                now = self._clock()
                if now - last_hello >= 0.2:
                    self._write(f"HELLO {self._nonce}")
                    last_hello = now
                self._read(now)
                if self._ready:
                    self._ping(now)
                    self._read(now)
                    if (
                        self._last_state is not None
                        and now - self._last_pong_at < self.stale_timeout_s
                    ):
                        self._connected = True
                        return self._last_state
                time.sleep(0.005)
            self._trip("Motor handshake timed out")
        except Exception:
            self._best_effort_stop()
            if self._serial is not None:
                self._serial.close()
            self._serial = None
            raise

    def poll(self, now=None):
        now = self._clock() if now is None else now
        if self._latched_fault:
            raise MotorError(self._latched_fault)
        if not self._connected:
            raise MotorError("Call connect() before polling the serial motor")
        # Check old receive timestamps BEFORE reading a backlog: data left over
        # during a blocked application must never make a stalled loop look fresh.
        if self._last_state is None or now - self._last_state.timestamp_s > self.stale_timeout_s:
            self._trip("Motor telemetry stale; explicit restart/re-home required")
        if now - self._last_pong_at > self.stale_timeout_s:
            self._trip("Motor heartbeat response stale")
        self._read(now)
        if self.fault is not None:
            self._trip("MCU fault: " + self.fault)
        self._ping(now)
        return self._last_state

    def home(self, timeout_s=25.0):
        if not self._connected or self._latched_fault:
            raise MotorError("Connect a healthy controller before explicit homing")
        self.poll()  # validate freshness and send a due heartbeat before arming
        self._write(f"HOME {self._nonce}")
        self._homing = True
        self.homed = False
        # Require new firmware state, never a stale pre-command homed flag.
        previous_seq = self._last_seq
        start = self._clock()
        try:
            while self._clock() - start < timeout_s:
                state = self.poll()
                if self._last_seq != previous_seq and state.homed and state.mode in ("RUN", "HOLD"):
                    return state
                time.sleep(0.005)
            self._trip("Homing timed out")
        finally:
            self._homing = False

    def set_target(self, x_mm, now=None, *, expires_at_s=None):
        """Send a fresh target, returning False if it expired during poll().

        The deadline is checked immediately before the serial write. A write or
        USB transfer can still take time: this is not a hard arrival deadline on
        the wire. The caller must retain a transport/actuation latency margin.
        """
        if expires_at_s is not None and not math.isfinite(expires_at_s):
            raise ValueError("Motor command deadline must be finite")
        self.poll(now)
        if not self.homed:
            raise MotorError("Motor is not homed")
        if not math.isfinite(x_mm) or not self.x_min_mm <= x_mm <= self.x_max_mm:
            self._trip("Target is outside allowed paddle travel")
        move = f"MOVE {self._nonce} {x_mm - self.x_min_mm:.2f}"
        if expires_at_s is not None and self._clock() >= expires_at_s:
            self._write(f"HOLD {self._nonce}")
            return False
        self._write(move)
        return True

    def hold(self, now=None):
        self.poll(now)
        if self.homed:
            self._write(f"HOLD {self._nonce}")

    def stop(self, reason="operator"):
        self._best_effort_stop()
        self._latched_fault = str(reason)
        self.fault = self._latched_fault
        self.homed = False

    def close(self):
        self.stop("closed")
        if self._serial is not None:
            self._serial.close()
            self._serial = None
        self._connected = False
