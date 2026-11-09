import math
import unittest

from airhockey.motor import DryRunMotor, MotorError, SerialMotor


class FakeClock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now


class FakeSerial:
    """Protocol peer; never imports pyserial or talks to a physical device."""

    def __init__(self, *args, **kwargs):
        self.rx = bytearray()
        self.writes = []
        self.closed = False
        self.seq = 0
        self.nonce = ""
        self.homed = False
        self.x = 0.0
        self.hold = False
        self.respond = True
        self.travel = 540.0

    @property
    def in_waiting(self):
        return len(self.rx)

    def reset_input_buffer(self):
        self.rx.clear()

    def read(self, count):
        data = bytes(self.rx[:count])
        del self.rx[:count]
        return data

    def line(self, text):
        self.rx.extend((text + "\n").encode("ascii"))

    def state(self, fault="NONE"):
        mode = (
            "FAULT" if fault != "NONE" else "HOLD" if self.hold else "RUN" if self.homed else "IDLE"
        )
        self.line(f"STATE {self.nonce} {self.seq} {self.x} 0 {int(self.homed)} {mode} {fault}")
        self.seq += 1

    def write(self, data):
        self.writes.append(data)
        if not self.respond:
            return len(data)
        parts = data.decode().strip().split()
        cmd = parts[0]
        if cmd == "HELLO":
            self.nonce = parts[1]
            self.line(f"READY 1 {self.nonce} {self.travel} 120.0 600.0")
            self.state()
        elif cmd == "PING":
            self.line(f"PONG {self.nonce} {parts[2]}")
            self.state()
        elif cmd == "HOME":
            self.homed = True
            self.x = 0.0
            self.state()
        elif cmd == "MOVE":
            self.x = float(parts[2])
            self.hold = False
            self.state()
        elif cmd == "HOLD":
            self.hold = True
            self.state()
        elif cmd == "STOP":
            self.homed = False
        return len(data)

    def close(self):
        self.closed = True


class MotorTests(unittest.TestCase):
    def make_motor(self):
        clock = FakeClock()
        peer = FakeSerial()
        motor = SerialMotor("fake", clock=clock, serial_factory=lambda *a, **k: peer)
        motor.connect()
        return motor, peer, clock

    def test_handshake_home_and_coordinate_conversion(self):
        motor, peer, clock = self.make_motor()
        self.assertFalse(motor.homed)
        motor.home()
        self.assertTrue(motor.homed)
        self.assertTrue(motor.set_target(330.0))
        state = motor.poll()
        self.assertEqual(peer.x, 300.0)
        self.assertEqual(state.position_mm, 330.0)
        self.assertEqual(state.velocity_mm_s, 0.0)
        motor.close()

    def test_firmware_config_mismatch_rejected_before_home(self):
        peer = FakeSerial()
        peer.travel = 600.0
        motor = SerialMotor("fake", serial_factory=lambda *a, **k: peer)
        with self.assertRaisesRegex(MotorError, "mismatch"):
            motor.connect()
        self.assertTrue(peer.closed)
        self.assertFalse(any(w.startswith(b"HOME") for w in peer.writes))

    def test_motion_before_home_rejected(self):
        motor, _, _ = self.make_motor()
        with self.assertRaisesRegex(MotorError, "not homed"):
            motor.set_target(200.0)
        motor.close()

    def test_stale_loop_latches_even_with_queued_telemetry(self):
        motor, peer, clock = self.make_motor()
        motor.home()
        peer.state()  # queued old bytes may not mask a stalled application
        clock.now += 0.3
        with self.assertRaisesRegex(MotorError, "stale"):
            motor.poll()
        self.assertIn(b"STOP\n", peer.writes)
        with self.assertRaises(MotorError):
            motor.set_target(300)
        motor.close()

    def test_bad_targets_stop_controller(self):
        for target in (29.0, 571.0, math.nan, math.inf):
            motor, peer, _ = self.make_motor()
            motor.home()
            with self.assertRaisesRegex(MotorError, "outside"):
                motor.set_target(target)
            self.assertFalse(motor.homed)
            self.assertIn(b"STOP\n", peer.writes)
            motor.close()

    def test_wrong_epoch_and_duplicate_state_ignored(self):
        motor, peer, clock = self.make_motor()
        motor.home()
        before = motor.poll()
        peer.line("STATE deadbeef 900 500 0 1 RUN NONE")
        peer.line(f"STATE {peer.nonce} {motor._last_seq} 500 0 1 RUN NONE")
        after = motor.poll()
        self.assertEqual(after, before)
        motor.close()

    def test_mcu_fault_is_latched(self):
        motor, peer, _ = self.make_motor()
        motor.home()
        peer.state("ESTOP")
        with self.assertRaisesRegex(MotorError, "ESTOP"):
            motor.poll()
        motor.close()

    def test_partial_serial_write_stops(self):
        motor, peer, _ = self.make_motor()
        motor.home()
        peer.write = lambda data: len(data) - 1
        with self.assertRaisesRegex(MotorError, "partial"):
            motor.set_target(300)
        motor.close()

    def test_hold_retains_reference_stop_invalidates_it(self):
        motor, _, _ = self.make_motor()
        motor.home()
        motor.hold()
        self.assertTrue(motor.poll().homed)
        motor.stop()
        self.assertFalse(motor.homed)
        with self.assertRaises(MotorError):
            motor.poll()
        motor.close()

    def test_target_expiring_during_serial_poll_sends_hold_not_move(self):
        motor, peer, clock = self.make_motor()
        motor.home()
        peer.state()
        original_read = peer.read

        def delayed_read(count):
            clock.now += 0.02
            return original_read(count)

        peer.read = delayed_read
        first_write = len(peer.writes)
        accepted = motor.set_target(330.0, expires_at_s=clock.now + 0.01)
        commands = peer.writes[first_write:]
        self.assertFalse(accepted)
        self.assertFalse(any(line.startswith(b"MOVE ") for line in commands))
        self.assertTrue(any(line.startswith(b"HOLD ") for line in commands))
        self.assertTrue(motor.homed)
        self.assertIsNone(motor.fault)
        self.assertEqual(peer.x, 0.0)
        motor.close()

    def test_fresh_serial_target_is_accepted(self):
        motor, peer, clock = self.make_motor()
        motor.home()
        self.assertTrue(motor.set_target(330.0, expires_at_s=clock.now + 0.1))
        self.assertEqual(peer.x, 300.0)
        motor.close()

    def test_nonfinite_serial_deadline_rejected_without_move(self):
        motor, peer, _ = self.make_motor()
        motor.home()
        for deadline in (math.nan, math.inf, -math.inf):
            with self.subTest(deadline=deadline):
                with self.assertRaisesRegex(ValueError, "deadline"):
                    motor.set_target(330.0, expires_at_s=deadline)
        self.assertFalse(any(line.startswith(b"MOVE ") for line in peer.writes))
        motor.close()

    def test_dry_run_acceleration_and_travel_limit(self):
        clock = FakeClock()
        motor = DryRunMotor(clock=clock)
        motor.set_target(500)
        clock.now += 0.05
        first = motor.poll()
        self.assertLessEqual(first.velocity_mm_s, 600 * 0.05 + 1e-6)
        self.assertLess(first.position_mm, 302)
        for _ in range(200):
            clock.now += 0.02
            state = motor.poll()
            self.assertTrue(30 <= state.position_mm <= 570)
            self.assertLessEqual(abs(state.velocity_mm_s), 120 + 1e-6)
        self.assertAlmostEqual(state.position_mm, 500, places=2)
        motor.close()

    def test_dry_run_rejects_nan(self):
        motor = DryRunMotor()
        with self.assertRaises(ValueError):
            motor.set_target(math.nan)

    def test_dry_run_retarget_brakes_through_target_before_reversing(self):
        clock = FakeClock()
        motor = DryRunMotor(clock=clock)
        motor.set_target(500.0)
        clock.now += 0.2
        before = motor.poll()
        self.assertAlmostEqual(before.velocity_mm_s, 120.0, places=6)
        target = before.position_mm + 0.1
        motor.set_target(target)
        clock.now += 0.005
        state = motor.poll()
        self.assertAlmostEqual(state.velocity_mm_s, 117.0, places=6)
        self.assertGreater(state.position_mm, target)
        furthest = state.position_mm
        reversed_direction = False
        for _ in range(500):
            previous = state
            clock.now += 0.005
            state = motor.poll()
            self.assertLessEqual(abs(state.velocity_mm_s - previous.velocity_mm_s), 3.000001)
            furthest = max(furthest, state.position_mm)
            reversed_direction |= state.velocity_mm_s < 0
        self.assertGreater(furthest - target, 10.0)
        self.assertTrue(reversed_direction)
        self.assertAlmostEqual(state.position_mm, target, places=6)
        self.assertEqual(state.velocity_mm_s, 0.0)

    def test_dry_run_normal_boundary_targets_remain_acceleration_limited(self):
        clock = FakeClock()
        motor = DryRunMotor(clock=clock)
        for target in (motor.x_max_mm, motor.x_min_mm):
            motor.set_target(target)
            previous = motor.poll()
            for _ in range(1500):
                clock.now += 0.005
                state = motor.poll()
                self.assertLessEqual(abs(state.velocity_mm_s - previous.velocity_mm_s), 3.000001)
                self.assertTrue(motor.x_min_mm <= state.position_mm <= motor.x_max_mm)
                previous = state
            self.assertEqual(state.position_mm, target)
            self.assertEqual(state.velocity_mm_s, 0.0)
            self.assertTrue(state.homed)

    def test_dry_run_unavoidable_boundary_crossing_is_a_fault(self):
        clock = FakeClock()
        motor = DryRunMotor(clock=clock)
        # A state with insufficient stopping distance cannot become a normal
        # successful positioning result just by clamping its coordinates.
        motor.position_mm = motor.x_max_mm - 0.01
        motor.velocity_mm_s = 120.0
        motor.set_target(motor.x_min_mm)
        clock.now += 0.005
        with self.assertRaisesRegex(MotorError, "travel limit"):
            motor.poll()
        self.assertFalse(motor.homed)
        self.assertEqual(motor.mode, "FAULT")
        self.assertEqual(motor.position_mm, motor.x_max_mm)
        reset = motor.home()
        self.assertTrue(reset.homed)
        self.assertIsNone(reset.fault)
        self.assertEqual(reset.mode, "DRY_RUN")

    def test_dry_run_deadline_acceptance_and_rejection(self):
        clock = FakeClock()
        motor = DryRunMotor(clock=clock)
        self.assertFalse(motor.set_target(330.0, expires_at_s=clock.now))
        self.assertEqual(motor._target, 300.0)
        self.assertTrue(motor.homed)
        self.assertTrue(motor.set_target(330.0, expires_at_s=clock.now + 0.1))
        for deadline in (math.nan, math.inf, -math.inf):
            with self.subTest(deadline=deadline):
                with self.assertRaisesRegex(ValueError, "deadline"):
                    motor.set_target(300.0, expires_at_s=deadline)


if __name__ == "__main__":
    unittest.main()
