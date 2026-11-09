"""Malformed numeric CLI input must fail before camera/motor work begins."""

import argparse
import io
import math
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from airhockey import calibration
from airhockey.__main__ import main
from airhockey.app import run_application


class CommandValidationTests(unittest.TestCase):
    def test_run_duration_must_be_finite_and_nonnegative(self):
        for raw in ("nan", "inf", "-inf", "-1"):
            with (
                self.subTest(value=raw),
                patch("airhockey.app.run_application") as run,
                redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit) as caught:
                    main(["run", f"--seconds={raw}"])
                self.assertEqual(caught.exception.code, 2)
                run.assert_not_called()

    def test_zero_and_positive_run_duration_are_passed_through(self):
        for raw in ("0", "2.5"):
            with patch("airhockey.app.run_application", return_value=0) as run:
                self.assertEqual(main(["run", f"--seconds={raw}"]), 0)
                self.assertEqual(run.call_args.args[0].seconds, float(raw))

    def test_python_api_has_same_duration_gate_before_configuration_access(self):
        for value in (math.nan, math.inf, -math.inf, -1):
            args = argparse.Namespace(
                source="sim",
                motor="dry-run",
                arm=False,
                home=False,
                auto_resume=False,
                seconds=value,
            )
            with self.subTest(value=value), patch("airhockey.app.load_config") as load:
                with self.assertRaises(ValueError):
                    run_application(args)
                load.assert_not_called()

    def test_snapshot_nonfinite_or_negative_warmup_does_not_open_camera(self):
        for value in (math.nan, math.inf, -math.inf, -1):
            args = argparse.Namespace(config="unused.json", output="unused.png", warmup=value)
            with (
                self.subTest(value=value),
                patch("airhockey.calibration._load", return_value=(None, {"camera": {}})),
                patch("airhockey.calibration.PiCameraSource") as camera,
            ):
                with self.assertRaises(ValueError):
                    calibration.snapshot(args)
                camera.assert_not_called()

    def test_lens_nonfinite_square_size_fails_before_image_discovery(self):
        for value in (math.nan, math.inf, -math.inf, 0):
            args = argparse.Namespace(
                cols=9, rows=6, square_mm=value, images=["unused/*.png"], output="unused.npz"
            )
            with self.subTest(value=value), patch("airhockey.calibration.glob.glob") as glob:
                with self.assertRaises(ValueError):
                    calibration.lens(args)
                glob.assert_not_called()


if __name__ == "__main__":
    unittest.main()
