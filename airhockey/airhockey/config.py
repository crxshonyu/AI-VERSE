"""JSON configuration. Dimensions and rates are examples, never measurements."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

DEFAULTS = {
    "camera": {
        "width": 640,
        "height": 480,
        "fps": 60,
        "exposure_us": 2000,
        "analogue_gain": 4.0,
        "lens_position": 1.0,
        "camera_num": 0,
        "sensor_size": None,
        "sensor_bit_depth": 10,
    },
    "table": {
        "width_mm": 600.0,
        "length_mm": 1000.0,
        "puck_radius_mm": 15.0,
        "paddle_radius_mm": 30.0,
        "paddle_y_mm": 920.0,
        "goal_width_mm": 200.0,
    },
    "vision": {
        "image_points": None,
        "calibration_size": [640, 480],
        "lens_file": None,
        "hsv_ranges": [[[5, 100, 80], [25, 255, 255]]],
        "min_area_px": 20,
        "max_area_px": 6000,
        "min_circularity": 0.45,
        "max_association_mm": 140.0,
        "morph_kernel": 3,
    },
    "tracking": {
        "history_samples": 5,
        "min_samples": 3,
        "history_seconds": 0.10,
        "max_gap_s": 0.12,
        "max_speed_mm_s": 5000.0,
        "turn_min_speed_mm_s": 80.0,
        "wall_margin_mm": 45.0,
    },
    "control": {
        "prediction_horizon_s": 2.0,
        "min_incoming_speed_mm_s": 40.0,
        "max_frame_age_s": 0.12,
        "max_track_age_s": 0.08,
        "camera_timeout_s": 0.12,
        "extra_latency_s": 0.020,
        "target_deadband_mm": 2.0,
        "command_hz": 30.0,
        "return_to_center": True,
    },
    "motor": {
        "port": "/dev/ttyACM0",
        "baudrate": 115200,
        "x_min_mm": 30.0,
        "x_max_mm": 570.0,
        "max_speed_mm_s": 120.0,
        "max_accel_mm_s2": 600.0,
        "home_timeout_s": 25.0,
    },
    "game": {"duration_s": 150.0, "goal_clear_s": 0.15},
    "gpio": {
        "enabled": False,
        "pull_up": True,
        "human_goal_pin": 17,
        "robot_goal_pin": 27,
        "rgb_pins": [22, 23, 24],
        "rgb_active_high": True,
        "min_goal_pulse_s": 0.002,
    },
}


def _merge(base, incoming, prefix=""):
    if not isinstance(incoming, dict):
        raise ValueError("configuration must contain JSON objects")
    for key, value in incoming.items():
        if key not in base:
            raise ValueError(f"Unknown configuration key: {prefix}{key}")
        if isinstance(base[key], dict):
            _merge(base[key], value, prefix + key + ".")
        else:
            base[key] = value


def _number(value, name, *, minimum=0.0, strictly_positive=False, maximum=None):
    """Reject bool, numeric strings and NaN before they reach control logic."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if value < minimum or (strictly_positive and value <= 0):
        raise ValueError(f"{name} is outside its allowed range")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")


def _integer(value, name, *, minimum=0, maximum=None):
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer, not a boolean or fraction")
    _number(value, name, minimum=minimum, maximum=maximum)


def _pair_of_integers(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} must contain [width, height]")
    for index, item in enumerate(value):
        _integer(item, f"{name}[{index}]", minimum=1)


def validate(config):
    """Validate units, types and cross-field limits without opening hardware.

    JSON booleans are intentionally strict: the string "false" is truthy in
    Python, which must never silently enable GPIO or change sensor polarity.
    """
    c, t, m, tr, ctl, v, gpio = (
        config[k] for k in ("camera", "table", "motor", "tracking", "control", "vision", "gpio")
    )
    positive = [("table", k) for k in t] + [
        ("camera", "width"),
        ("camera", "height"),
        ("camera", "fps"),
        ("camera", "analogue_gain"),
        ("motor", "max_speed_mm_s"),
        ("motor", "max_accel_mm_s2"),
        ("motor", "home_timeout_s"),
        ("game", "duration_s"),
        ("game", "goal_clear_s"),
        ("tracking", "history_seconds"),
        ("tracking", "max_gap_s"),
        ("tracking", "max_speed_mm_s"),
        ("control", "prediction_horizon_s"),
        ("control", "command_hz"),
        ("control", "max_frame_age_s"),
        ("control", "max_track_age_s"),
        ("control", "camera_timeout_s"),
    ]
    for section, key in positive:
        _number(config[section][key], f"{section}.{key}", strictly_positive=True)
    for section, key in [
        ("motor", "x_min_mm"),
        ("motor", "x_max_mm"),
        ("camera", "lens_position"),
        ("control", "extra_latency_s"),
        ("camera", "exposure_us"),
        ("control", "target_deadband_mm"),
        ("control", "min_incoming_speed_mm_s"),
        ("tracking", "wall_margin_mm"),
        ("tracking", "turn_min_speed_mm_s"),
        ("gpio", "min_goal_pulse_s"),
    ]:
        _number(config[section][key], f"{section}.{key}")
    for key in ("width", "height", "sensor_bit_depth"):
        _integer(c[key], f"camera.{key}", minimum=1)
    _integer(c["camera_num"], "camera.camera_num")
    _integer(c["exposure_us"], "camera.exposure_us")
    _number(c["fps"], "camera.fps", strictly_positive=True, maximum=240)
    if c["sensor_size"] is not None:
        _pair_of_integers(c["sensor_size"], "camera.sensor_size")
    _integer(m["baudrate"], "motor.baudrate", minimum=1)
    if not isinstance(m["port"], str) or not m["port"].strip():
        raise ValueError("motor.port must be a nonempty device name")
    for section, key in (
        ("gpio", "enabled"),
        ("gpio", "pull_up"),
        ("gpio", "rgb_active_high"),
        ("control", "return_to_center"),
    ):
        if type(config[section][key]) is not bool:
            raise ValueError(f"{section}.{key} must be JSON true/false (without quotes)")
    _pair_of_integers(v["calibration_size"], "vision.calibration_size")
    if v["lens_file"] is not None and (
        not isinstance(v["lens_file"], str) or not v["lens_file"].strip()
    ):
        raise ValueError("vision.lens_file must be a path string or null")
    points = v["image_points"]
    if points is not None:
        if not isinstance(points, (list, tuple)) or len(points) != 4:
            raise ValueError("vision.image_points must contain four [x, y] points or null")
        for index, point in enumerate(points):
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError(f"vision.image_points[{index}] must be [x, y]")
            for axis, coordinate in enumerate(point):
                _number(
                    coordinate,
                    f"vision.image_points[{index}][{axis}]",
                    maximum=v["calibration_size"][axis] - 1,
                )
    for key in ("min_area_px", "max_area_px", "max_association_mm"):
        _number(v[key], f"vision.{key}", strictly_positive=True)
    if v["max_area_px"] < v["min_area_px"]:
        raise ValueError("vision.max_area_px must be >= min_area_px")
    _number(v["min_circularity"], "vision.min_circularity", maximum=1)
    _integer(v["morph_kernel"], "vision.morph_kernel", minimum=1)
    if v["morph_kernel"] % 2 != 1:
        raise ValueError("vision.morph_kernel must be odd")
    ranges = v["hsv_ranges"]
    if not isinstance(ranges, (list, tuple)) or not ranges:
        raise ValueError("vision.hsv_ranges must be a nonempty list")
    for index, bounds in enumerate(ranges):
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            raise ValueError(f"vision.hsv_ranges[{index}] must be [lower, upper]")
        for bound in bounds:
            if not isinstance(bound, (list, tuple)) or len(bound) != 3:
                raise ValueError("Each HSV bound must contain three integer channels")
            for channel, limit in zip(bound, (179, 255, 255), strict=True):
                _integer(channel, "HSV channel", maximum=limit)
        if any(lo > hi for lo, hi in zip(*bounds, strict=True)):
            raise ValueError("HSV lower must be <= upper; split a hue wrap into two ranges")
    if c["exposure_us"] > 1_000_000 / c["fps"]:
        raise ValueError("exposure_us must fit within the requested frame period")
    if not (2 * t["puck_radius_mm"] < min(t["width_mm"], t["length_mm"])):
        raise ValueError("puck is larger than the table")
    if not (0 < defense_y(t) < t["length_mm"] - t["puck_radius_mm"]):
        raise ValueError("paddle_y and radii give an invalid interception line")
    if not (
        t["paddle_radius_mm"]
        <= m["x_min_mm"]
        < m["x_max_mm"]
        <= t["width_mm"] - t["paddle_radius_mm"]
    ):
        raise ValueError("motor travel must keep the entire paddle inside the table")
    if not (t["goal_width_mm"] <= t["width_mm"]):
        raise ValueError("goal cannot be wider than the table")
    if not (
        type(tr["history_samples"]) is int
        and type(tr["min_samples"]) is int
        and 3 <= tr["min_samples"] <= tr["history_samples"]
    ):
        raise ValueError("tracking requires at least three distinct samples")
    if not (0 < ctl["command_hz"] <= 60):
        raise ValueError("command_hz must be in (0, 60]")
    if ctl["camera_timeout_s"] > 0.15:
        raise ValueError("camera_timeout_s must be <= 0.15 for the motor watchdog")
    pins = [gpio["human_goal_pin"], gpio["robot_goal_pin"]]
    if gpio["rgb_pins"] is not None:
        if not isinstance(gpio["rgb_pins"], (list, tuple)) or len(gpio["rgb_pins"]) != 3:
            raise ValueError("gpio.rgb_pins must contain exactly three BCM pins or null")
        pins += list(gpio["rgb_pins"])
    for pin in pins:
        _integer(pin, "GPIO BCM pin", maximum=27)
    if len(pins) != len(set(pins)):
        raise ValueError("GPIO BCM pins must be distinct integers between 0 and 27")
    return config


def defense_y(table):
    """Puck centre at the front tangent of a centred circular paddle."""
    return table["paddle_y_mm"] - table["paddle_radius_mm"] - table["puck_radius_mm"]


def load_config(path=None):
    config = copy.deepcopy(DEFAULTS)
    if path:
        path = Path(path).expanduser().resolve()
        _merge(config, json.loads(path.read_text(encoding="utf-8")))
        validate(config)
        lens = config["vision"]["lens_file"]
        if lens and not Path(lens).is_absolute():
            config["vision"]["lens_file"] = str((path.parent / lens).resolve())
    return validate(config)
