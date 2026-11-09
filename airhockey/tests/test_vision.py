"""Meaningful synthetic-image tests; no Raspberry Pi hardware is required."""

import copy
import tempfile
import unittest
from pathlib import Path

from airhockey.config import DEFAULTS
from airhockey.vision import Geometry, LensCorrection, PuckDetector, cv2, np


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(DEFAULTS)
        self.vision = self.config["vision"]
        self.vision["image_points"] = [[40, 20], [600, 20], [600, 460], [40, 460]]
        self.geometry = Geometry(self.config["table"], self.vision, (640, 480))

    def test_metric_coordinates_and_inverse(self):
        actual = self.geometry.pixel_to_mm(320, 240)
        self.assertTrue(np.allclose(actual, (300, 500), atol=1e-4))
        self.assertEqual(self.geometry.mm_to_pixel(300, 500), (320, 240))

    def test_orange_circle_is_detected_in_mm(self):
        image = np.zeros((480, 640, 3), np.uint8)
        cv2.circle(image, (320, 240), 12, (0, 128, 255), -1)
        puck = PuckDetector(self.geometry, self.vision).detect(image)
        self.assertIsNotNone(puck)
        self.assertAlmostEqual(puck.x_mm, 300, delta=1)
        self.assertAlmostEqual(puck.y_mm, 500, delta=1)
        self.assertGreater(puck.confidence, 0.7)

    def test_table_mask_and_association_reject_distractors(self):
        image = np.zeros((480, 640, 3), np.uint8)
        cv2.circle(image, (15, 240), 10, (0, 128, 255), -1)
        detector = PuckDetector(self.geometry, self.vision)
        self.assertIsNone(detector.detect(image))
        cv2.circle(image, (320, 240), 12, (0, 128, 255), -1)
        self.assertIsNone(detector.detect(image, predicted_mm=(0, 0)))
        self.assertIsNotNone(detector.detect(image, predicted_mm=(300, 500)))

    def test_crossed_corner_order_is_rejected(self):
        bad = dict(self.vision, image_points=[[40, 20], [600, 460], [600, 20], [40, 460]])
        with self.assertRaises(ValueError):
            Geometry(self.config["table"], bad, (640, 480))

    def test_calibration_resolution_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            Geometry(self.config["table"], self.vision, (320, 240))
        with self.assertRaises(ValueError):
            self.geometry.prepare(np.zeros((240, 320, 3), np.uint8))

    def test_lens_points_and_remap_use_same_coordinate_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "lens.npz"
            camera_matrix = np.array([[500, 0, 320], [0, 500, 240], [0, 0, 1]], np.float64)
            distortion = np.array([-0.18, 0.04, 0.0, 0.0, 0.0])
            np.savez(
                filename, camera_matrix=camera_matrix, dist_coeffs=distortion, image_size=[640, 480]
            )
            vision = dict(self.vision, lens_file=str(filename))
            geometry = Geometry(self.config["table"], vision, (640, 480))
            expected = [(0, 0), (600, 0), (600, 1000), (0, 1000)]
            for mm, corrected_pixel in zip(expected, geometry.corners, strict=True):
                self.assertTrue(np.allclose(geometry.pixel_to_mm(*corrected_pixel), mm, atol=1e-3))
            self.assertEqual(
                geometry.prepare(np.zeros((480, 640, 3), np.uint8)).shape, (480, 640, 3)
            )
            with self.assertRaises(ValueError):
                LensCorrection((320, 240), filename)

    def test_red_hue_wrap_requires_and_accepts_two_ranges(self):
        vision = dict(
            self.vision,
            hsv_ranges=[[[0, 120, 80], [7, 255, 255]], [[173, 120, 80], [179, 255, 255]]],
        )
        for hue in [0, 178]:
            hsv = np.zeros((480, 640, 3), np.uint8)
            cv2.circle(hsv, (320, 240), 12, (hue, 255, 255), -1)
            image = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
            self.assertIsNotNone(PuckDetector(self.geometry, vision).detect(image))
        invalid = dict(self.vision, hsv_ranges=[[[173, 120, 80], [7, 255, 255]]])
        with self.assertRaises(ValueError):
            PuckDetector(self.geometry, invalid)


if __name__ == "__main__":
    unittest.main()
