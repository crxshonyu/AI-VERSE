"""Hardware-free integration checks for real-motor gates and lost-frame HOLD."""

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from airhockey.app import run_application
from airhockey.config import load_config
from airhockey.motor import DryRunMotor
from airhockey.simulation import demo_config
from airhockey.vision import Detection, Frame, np


class ApplicationReview(unittest.TestCase):
    def test_simulation_cannot_open_real_motor_even_via_python_api(self):
        args = argparse.Namespace(motor="serial", source="sim", arm=True, home=True)
        with self.assertRaisesRegex(ValueError, "Real motor"):
            run_application(args)

    def test_camera_real_motor_requires_both_explicit_flags(self):
        for arm, home in [(False, True), (True, False), (False, False)]:
            args = argparse.Namespace(motor="serial", source="camera", arm=arm, home=home)
            with self.assertRaisesRegex(ValueError, "Real motor"):
                run_application(args)

    def run_loss_scenario(self, loss_kind, poll_stall_s=0.0):
        class Clock:
            now = 100.0

            def __call__(self):
                return self.now

        clock = Clock()

        class Camera:
            width, height = 640, 480
            mode_info = {"mock": True}
            calls = 0
            main_index = -1

            def read(self, timeout_s):
                self.calls += 1
                self.main_index = self.calls - 10  # nine warmup reads
                if self.main_index >= 17:
                    raise EOFError
                clock.now += 0.02
                stamp = clock.now
                if loss_kind == "stale" and self.main_index >= 13:
                    stamp -= 0.5
                return Frame(np.zeros((480, 640, 3), np.uint8), stamp, self.calls, {})

            def close(self):
                pass

        camera = Camera()

        class Detector:
            last_mask = None

            def detect(self, image, predicted_mm=None):
                i = camera.main_index
                if loss_kind == "missing" and i >= 13:
                    return None
                # Changing lateral speed makes each predicted target different;
                # otherwise deadband can hide a stale dispatch in this test.
                x = 200.0 + i * i * 0.5 if poll_stall_s else 300.0
                return Detection(x, 200.0 + i * 8, (320.0, 200.0), 6.0, 1.0)

        actions = []

        class Motor(DryRunMotor):
            stalled = False

            def __init__(self, **kwargs):
                super().__init__(clock=clock, **kwargs)

            def poll(self, now=None):
                state = super().poll(now)
                if poll_stall_s and camera.main_index == 11 and not self.stalled:
                    self.stalled = True
                    clock.now += poll_stall_s
                return state

            def set_target(self, x_mm, now=None, *, expires_at_s=None):
                accepted = super().set_target(x_mm, now, expires_at_s=expires_at_s)
                if accepted:
                    actions.append((camera.main_index, "target", x_mm))
                return accepted

            def hold(self, now=None):
                actions.append((camera.main_index, "hold", None))
                super().hold(now)

            def stop(self, reason="operator"):
                actions.append((camera.main_index, "stop", reason))
                super().stop(reason)

        class Operators:
            def poll(self):
                return []

        with tempfile.TemporaryDirectory(prefix="airhockey_test_") as temporary:
            root = Path(temporary)
            cfg = root / "config.json"
            cfg.write_text(json.dumps(demo_config(load_config())), encoding="utf-8")
            args = argparse.Namespace(
                config=str(cfg),
                source="camera",
                motor="dry-run",
                arm=False,
                home=False,
                auto_resume=False,
                gpio=False,
                show=False,
                mask=False,
                port=None,
                record=None,
                output_dir=str(root / "run"),
                start=True,
                seconds=0,
            )
            with (
                patch("airhockey.vision.PiCameraSource", return_value=camera),
                patch("airhockey.vision.PuckDetector", return_value=Detector()),
                patch("airhockey.app.DryRunMotor", Motor),
                patch("airhockey.app.OperatorInput", return_value=Operators()),
                patch("airhockey.app.time.monotonic", clock),
                redirect_stdout(io.StringIO()),
            ):
                result = run_application(args)
            self.assertEqual(result, 0)
            summary = json.loads((root / "run" / "summary.json").read_text())
            self.assertIsNone(summary["error"])
        self.assertTrue(any(action == "target" and i < 13 for i, action, _ in actions))
        loss_frame = 11 if poll_stall_s else 13
        self.assertTrue(any(action == "hold" and i == loss_frame for i, action, _ in actions))
        self.assertFalse(any(action == "target" and i >= loss_frame for i, action, _ in actions))
        self.assertTrue(any(action == "stop" for _, action, _ in actions))

    def test_missing_puck_holds_without_reusing_last_target(self):
        self.run_loss_scenario("missing")

    def test_stale_camera_holds_without_reusing_last_target(self):
        self.run_loss_scenario("stale")

    def test_motor_poll_delay_is_counted_before_target_dispatch(self):
        # Image starts fresh; a telemetry poll stalls long enough to cross the
        # 80 ms track-age deadline. The app must refresh the clock after poll.
        self.run_loss_scenario("missing", poll_stall_s=0.1)


if __name__ == "__main__":
    unittest.main()
