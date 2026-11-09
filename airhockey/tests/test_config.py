"""Reject values that silently change hardware controls or tracking gates."""

import copy
import math
import unittest

from airhockey.config import DEFAULTS, validate


class ConfigurationTests(unittest.TestCase):
    def invalid(self, section, key, value):
        config = copy.deepcopy(DEFAULTS)
        config[section][key] = value
        with self.assertRaises(ValueError, msg=f"{section}.{key}={value!r}"):
            validate(config)

    def test_boolean_controls_do_not_accept_truthy_strings_or_numbers(self):
        fields = [
            ("gpio", "enabled"),
            ("gpio", "pull_up"),
            ("gpio", "rgb_active_high"),
            ("control", "return_to_center"),
        ]
        for section, key in fields:
            for value in ("false", "true", 0, 1, None):
                with self.subTest(section=section, key=key, value=value):
                    self.invalid(section, key, value)

    def test_nonfinite_detector_thresholds_cannot_disable_filters(self):
        for key in ("min_area_px", "max_area_px", "min_circularity", "max_association_mm"):
            for value in (math.nan, math.inf, -math.inf, True):
                with self.subTest(key=key, value=value):
                    self.invalid("vision", key, value)

    def test_detector_threshold_ranges_are_validated_before_camera_start(self):
        cases = [
            ("min_area_px", -1),
            ("max_area_px", 1),
            ("min_circularity", -0.1),
            ("min_circularity", 1.1),
            ("max_association_mm", 0),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                self.invalid("vision", key, value)

    def test_gpio_pin_numbers_are_real_integers_and_rgb_has_three_channels(self):
        for value in (True, 17.5, -1, 28):
            self.invalid("gpio", "human_goal_pin", value)
        for value in ([22], [22, 23], [22, 23, 24, 25], [22, 22, 24], [True, 23, 24], "22,23,24"):
            with self.subTest(rgb_pins=value):
                self.invalid("gpio", "rgb_pins", value)
        config = copy.deepcopy(DEFAULTS)
        config["gpio"]["rgb_pins"] = None
        validate(config)  # LED output can be intentionally absent.

    def test_camera_selection_and_baudrate_do_not_coerce_bad_values(self):
        for value in (-1, True, 0.5, "0"):
            self.invalid("camera", "camera_num", value)
        for value in (0, -1, True, 115200.5):
            self.invalid("motor", "baudrate", value)
        for value in ([1536, 864.2], [1536], [0, 864], [True, 864]):
            self.invalid("camera", "sensor_size", value)

    def test_hsv_values_are_integer_channels_not_silent_truncations(self):
        for value in (
            [],
            [[[5.5, 100, 80], [25, 255, 255]]],
            [[[True, 100, 80], [25, 255, 255]]],
            [[[170, 100, 80], [5, 255, 255]]],
            [[[5, 100, 80], [180, 255, 255]]],
        ):
            with self.subTest(hsv_ranges=value):
                self.invalid("vision", "hsv_ranges", value)

    def test_morphology_kernel_is_positive_odd_integer(self):
        for value in (0, -1, 2, 3.5, True, "3"):
            self.invalid("vision", "morph_kernel", value)


if __name__ == "__main__":
    unittest.main()
