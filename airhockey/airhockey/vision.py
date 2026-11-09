"""Camera capture, fixed-plane calibration and colour/shape puck detection.

Units: BGR uint8 images, OpenCV HSV H=0..179, table positions in mm.
image_points are RAW image coordinates in physical corner order:
human-left, human-right, robot-right, robot-left. No auto-reordering.
"""

from __future__ import annotations

import math
import time
import warnings
from dataclasses import dataclass
from typing import Any, Optional

try:
    import cv2
    import numpy as np
except ImportError as exc:
    raise ImportError(
        "영상 기능에는 OpenCV/numpy가 필요합니다: sudo apt install python3-opencv python3-numpy"
    ) from exc


@dataclass(frozen=True)
class Frame:
    image: Any
    timestamp_s: float
    sequence: int
    metadata: dict


@dataclass(frozen=True)
class Detection:
    x_mm: float
    y_mm: float
    pixel: tuple[float, float]
    radius_px: float
    confidence: float

    @property
    def position_mm(self) -> tuple[float, float]:
        return self.x_mm, self.y_mm


def _boot_to_monotonic_offset() -> float:
    """libcamera timestamps use CLOCK_BOOTTIME; Python monotonic excludes suspend."""
    before = time.monotonic_ns()
    boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    after = time.monotonic_ns()
    return ((before + after) / 2 - boot) / 1e9


class PiCameraSource:
    """One capture request at a time; no growing application frame queue.

    RGB888 deliberately produces OpenCV BGR bytes in Picamera2. A request's
    image and metadata are read together and its buffers are always released.
    A capture timeout is fatal: reopen after fixing camera/cable/load issues.
    SensorTimestamp is a sensor reference, NOT exact puck exposure midpoint;
    Camera Module 3's rolling shutter still creates position uncertainty.
    """

    def __init__(self, config: dict):
        try:
            from libcamera import controls
            from picamera2 import Picamera2
        except ImportError as exc:
            raise RuntimeError(
                "CSI 카메라는 Raspberry Pi OS의 python3-picamera2 패키지가 필요합니다."
            ) from exc
        self._camera = None
        self._started = False
        self._closed = False
        self._sequence = 0
        self._last_sensor_ns: Optional[int] = None
        self.width = int(config.get("width", 640))
        self.height = int(config.get("height", 480))
        fps = float(config.get("fps", 60))
        if self.width <= 0 or self.height <= 0 or not (0 < fps <= 240):
            raise ValueError("잘못된 camera.width/height/fps")
        exposure = int(config.get("exposure_us", 2000))
        if exposure < 0 or exposure > 1e6 / fps:
            raise ValueError("exposure_us는 0(자동) 또는 요청 프레임 주기보다 짧아야 합니다.")
        gain = float(config.get("analogue_gain", 4.0))
        if not math.isfinite(gain) or gain <= 0:
            raise ValueError("analogue_gain는 양수여야 합니다.")
        try:
            self._camera = Picamera2(camera_num=int(config.get("camera_num", 0)))
            modes = self._camera.sensor_modes
            requested = config.get("sensor_size")
            if requested:
                candidates = [
                    m
                    for m in modes
                    if tuple(m["size"]) == tuple(requested)
                    and int(m["bit_depth"]) == int(config.get("sensor_bit_depth", 10))
                ]
            else:
                candidates = [m for m in modes if float(m["fps"]) + 0.05 >= fps]
                # Preserve as much sensor field as possible among modes which
                # can meet fps, then avoid needless ISP bandwidth for 640x480.
                candidates.sort(
                    key=lambda m: (
                        -(
                            m.get("crop_limits", (0, 0, *m["size"]))[2]
                            * m.get("crop_limits", (0, 0, *m["size"]))[3]
                        ),
                        m["size"][0] * m["size"][1],
                    )
                )
            if not candidates:
                summary = "; ".join(
                    f"{m['size']} {m['bit_depth']}bit <= {m['fps']:.1f}fps" for m in modes
                )
                raise ValueError(
                    f"요청 카메라 모드/fps를 지원하지 않습니다. fps를 낮추세요. 지원: {summary}"
                )
            mode = candidates[0]
            if float(mode["fps"]) + 0.05 < fps:
                raise ValueError(f"sensor_size 모드 최대 {mode['fps']:.1f} fps < 요청 {fps} fps")
            self.mode_info = {k: mode.get(k) for k in ("size", "bit_depth", "fps", "crop_limits")}
            ctrls = {"FrameDurationLimits": (round(1e6 / fps), round(1e6 / fps))}
            if exposure:
                ctrls.update(AeEnable=False, ExposureTime=exposure, AnalogueGain=gain)
            else:
                ctrls["AeEnable"] = True
            if "AfMode" in self._camera.camera_controls:
                ctrls["AfMode"] = controls.AfModeEnum.Manual
                lens = float(config.get("lens_position", 1.0))
                if not math.isfinite(lens) or lens < 0:
                    raise ValueError("lens_position는 0 이상 디옵터 값이어야 합니다.")
                ctrls["LensPosition"] = lens
            camera_cfg = self._camera.create_video_configuration(
                main={"size": (self.width, self.height), "format": "RGB888"},
                sensor={"output_size": mode["size"], "bit_depth": mode["bit_depth"]},
                buffer_count=int(config.get("buffer_count", 4)),
                queue=False,
                controls=ctrls,
            )
            self._camera.configure(camera_cfg)
            actual = tuple(self._camera.camera_configuration()["main"]["size"])
            if actual != (self.width, self.height):
                raise ValueError(
                    f"카메라 실제 해상도 {actual} != 설정 {(self.width, self.height)}. 보정 해상도를 일치시키세요."
                )
            props = self._camera.camera_properties
            full = props.get("PixelArraySize")
            crop = mode.get("crop_limits")
            if full and crop and tuple(crop[2:]) != tuple(full):
                warnings.warn(
                    f"선택 센서 모드는 crop={crop}, 전체={full}. 이 모드로 코너/렌즈를 보정하세요.",
                    stacklevel=2,
                )
            self._camera.start()
            self._started = True
        except BaseException:
            self.close()
            raise

    def read(self, timeout_s: float = 1.0) -> Frame:
        if self._closed:
            raise RuntimeError("카메라가 닫혔습니다.")
        if timeout_s <= 0 or not math.isfinite(timeout_s):
            raise ValueError("timeout_s must be positive")
        request = None
        try:
            job = self._camera.capture_request(wait=False)
            # get_result(timeout=...) is the current documented Picamera2 API.
            request = job.get_result(timeout=timeout_s)
            metadata = dict(request.get_metadata())
            sensor_ns = int(metadata["SensorTimestamp"])
            if self._last_sensor_ns is not None and sensor_ns <= self._last_sensor_ns:
                raise RuntimeError("중복되거나 역행한 카메라 타임스탬프")
            timestamp = sensor_ns / 1e9 + _boot_to_monotonic_offset()
            if timestamp > time.monotonic() + 0.1:
                raise RuntimeError("카메라 타임스탬프/monotonic 시계가 일치하지 않습니다.")
            image = request.make_array("main").copy()
            self._last_sensor_ns = sensor_ns
            self._sequence += 1
            return Frame(image, timestamp, self._sequence, metadata)
        except TimeoutError as exc:
            # Timed-out jobs must not remain alive for a subsequent read.
            self.close()
            raise TimeoutError(
                "카메라 프레임 시간 초과: 케이블, 조명, FPS/시스템 부하를 확인하세요."
            ) from exc
        finally:
            if request is not None:
                request.release()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        camera, self._camera = self._camera, None
        if camera is not None:
            try:
                if self._started:
                    camera.stop()
            finally:
                camera.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class VideoSource:
    """Offline clip input. Recorded timing is an approximation for constant-FPS clips.

    File input is for replay only, never for armed motor control. With realtime
    False timestamps advance on the clip timeline even if processing is faster.
    """

    def __init__(self, path: str, realtime: bool = True):
        self._cap = cv2.VideoCapture(str(path))
        if not self._cap.isOpened():
            self._cap.release()
            raise ValueError(f"영상을 열 수 없습니다: {path}")
        self.fps = float(self._cap.get(cv2.CAP_PROP_FPS))
        if not math.isfinite(self.fps) or self.fps <= 0:
            self._cap.release()
            raise ValueError("영상의 FPS 메타데이터가 없습니다.")
        self.width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._sequence = 0
        self._start = time.monotonic()
        self._realtime = realtime

    def read(self, timeout_s: float = 1.0) -> Frame:
        ok, image = self._cap.read()
        if not ok:
            raise EOFError("영상 재생 종료")
        timestamp = self._start + self._sequence / self.fps
        if self._realtime:
            delay = timestamp - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        self._sequence += 1
        return Frame(image, timestamp, self._sequence, {"source": "video", "nominal_fps": self.fps})

    def close(self):
        self._cap.release()


class LensCorrection:
    """Optional standard OpenCV pinhole+radial/tangential calibration.

    Same K is used for image remapping and point undistortion. NPZ contains
    camera_matrix, dist_coeffs, image_size=(width,height); no pickle loading.
    Recalibrate after changing sensor mode, resolution, crop or focus.
    """

    def __init__(self, frame_size, lens_file=None):
        self.size = tuple(int(v) for v in frame_size)
        self.K = self.dist = self.maps = None
        if lens_file:
            with np.load(str(lens_file), allow_pickle=False) as lens:
                if tuple(int(v) for v in lens["image_size"]) != self.size:
                    raise ValueError("렌즈 보정 해상도와 camera 해상도가 다릅니다.")
                self.K = np.asarray(lens["camera_matrix"], dtype=np.float64)
                self.dist = np.asarray(lens["dist_coeffs"], dtype=np.float64)
            if (
                self.K.shape != (3, 3)
                or not np.isfinite(self.K).all()
                or not np.isfinite(self.dist).all()
            ):
                raise ValueError("잘못된 렌즈 보정 파일")
            if self.K[0, 0] <= 0 or self.K[1, 1] <= 0:
                raise ValueError("잘못된 카메라 초점거리")
            self.maps = cv2.initUndistortRectifyMap(
                self.K, self.dist, None, self.K, self.size, cv2.CV_16SC2
            )

    def prepare(self, image):
        if (image.shape[1], image.shape[0]) != self.size:
            raise ValueError(
                f"입력 영상 크기 {(image.shape[1], image.shape[0])} != 보정 크기 {self.size}"
            )
        if self.maps is None:
            return image
        return cv2.remap(image, self.maps[0], self.maps[1], cv2.INTER_LINEAR)

    def points(self, raw_points):
        points = np.asarray(raw_points, dtype=np.float32).reshape(-1, 1, 2)
        if self.K is not None:
            points = cv2.undistortPoints(points, self.K, self.dist, P=self.K)
        return points.reshape(-1, 2)


def validate_corners(points, frame_size) -> Any:
    corners = np.asarray(points, dtype=np.float32)
    if corners.shape != (4, 2) or not np.isfinite(corners).all():
        raise ValueError("image_points에는 네 코너의 [x,y] 좌표가 필요합니다.")
    w, h = frame_size
    if (
        np.any(corners[:, 0] < 0)
        or np.any(corners[:, 0] >= w)
        or np.any(corners[:, 1] < 0)
        or np.any(corners[:, 1] >= h)
    ):
        raise ValueError("코너 좌표가 영상 밖에 있습니다.")
    if not cv2.isContourConvex(corners) or abs(cv2.contourArea(corners)) < w * h * 0.01:
        raise ValueError("코너 순서/면적을 확인하세요: 인간 왼쪽→인간 오른쪽→로봇 오른쪽→로봇 왼쪽")
    return corners


class Geometry:
    def __init__(self, table_config: dict, vision_config: dict, frame_size):
        self.size = tuple(int(v) for v in frame_size)
        if tuple(vision_config.get("calibration_size", ())) != self.size:
            raise ValueError(
                "vision.calibration_size와 입력 해상도가 다릅니다. 다시 코너 보정하세요."
            )
        if vision_config.get("image_points") is None:
            raise ValueError(
                "테이블 코너가 아직 없습니다. python3 -m airhockey.calibration corners를 실행하세요."
            )
        self.width_mm = float(table_config["width_mm"])
        self.length_mm = float(table_config["length_mm"])
        if min(self.width_mm, self.length_mm) <= 0:
            raise ValueError("테이블 길이는 양수 mm여야 합니다.")
        self.lens = LensCorrection(self.size, vision_config.get("lens_file"))
        self.raw_corners = validate_corners(vision_config["image_points"], self.size)
        self.corners = self.lens.points(self.raw_corners)
        destination = np.array(
            [[0, 0], [self.width_mm, 0], [self.width_mm, self.length_mm], [0, self.length_mm]],
            np.float32,
        )
        self.H = cv2.getPerspectiveTransform(self.corners, destination)
        self.H_inv = np.linalg.inv(self.H)
        if not np.isfinite(self.H).all() or not np.isfinite(self.H_inv).all():
            raise ValueError("유효하지 않은 테이블 투영행렬")
        self.roi_mask = np.zeros((self.size[1], self.size[0]), dtype=np.uint8)
        cv2.fillConvexPoly(self.roi_mask, np.round(self.corners).astype(np.int32), 255)

    def prepare(self, image):
        return self.lens.prepare(image)

    def pixel_to_mm(self, x: float, y: float) -> tuple[float, float]:
        p = cv2.perspectiveTransform(np.array([[[x, y]]], np.float32), self.H)[0, 0]
        return float(p[0]), float(p[1])

    def mm_to_pixel(self, x: float, y: float) -> tuple[int, int]:
        p = cv2.perspectiveTransform(np.array([[[x, y]]], np.float32), self.H_inv)[0, 0]
        return int(round(float(p[0]))), int(round(float(p[1])))


class PuckDetector:
    def __init__(self, geometry: Geometry, config: dict):
        self.geometry = geometry
        self.config = config
        self.ranges = []
        for lo, hi in config.get("hsv_ranges", [[[5, 100, 80], [25, 255, 255]]]):
            a, b = np.asarray(lo, dtype=np.int32), np.asarray(hi, dtype=np.int32)
            if (
                a.shape != (3,)
                or b.shape != (3,)
                or np.any(a < 0)
                or np.any(b > [179, 255, 255])
                or np.any(a > b)
            ):
                raise ValueError(
                    "HSV 범위 오류. 빨강처럼 H가 179→0을 넘으면 범위를 두 개로 나누세요."
                )
            self.ranges.append((a.astype(np.uint8), b.astype(np.uint8)))
        if not self.ranges:
            raise ValueError("hsv_ranges가 비어 있습니다.")
        kernel = int(config.get("morph_kernel", 3))
        if kernel < 1 or kernel % 2 == 0:
            raise ValueError("morph_kernel은 1 이상의 홀수")
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel, kernel))
        self.last_mask = None

    def detect(self, prepared_image, predicted_mm=None) -> Optional[Detection]:
        hsv = cv2.cvtColor(prepared_image, cv2.COLOR_BGR2HSV)
        mask = np.zeros(hsv.shape[:2], np.uint8)
        for lower, upper in self.ranges:
            mask |= cv2.inRange(hsv, lower, upper)
        mask &= self.geometry.roi_mask
        if self.kernel.shape[0] > 1:
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self.kernel)
        self.last_mask = mask
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if (
                not float(self.config.get("min_area_px", 20))
                <= area
                <= float(self.config.get("max_area_px", 6000))
            ):
                continue
            perimeter = cv2.arcLength(contour, True)
            if perimeter <= 0:
                continue
            circularity = min(1.0, 4 * math.pi * area / (perimeter * perimeter))
            if circularity < float(self.config.get("min_circularity", 0.45)):
                continue
            moments = cv2.moments(contour)
            if moments["m00"] <= 0:
                continue
            cx, cy = moments["m10"] / moments["m00"], moments["m01"] / moments["m00"]
            x, y = self.geometry.pixel_to_mm(cx, cy)
            if not (0 <= x <= self.geometry.width_mm and 0 <= y <= self.geometry.length_mm):
                continue
            (_, _), radius = cv2.minEnclosingCircle(contour)
            fill = min(1.0, area / (math.pi * radius * radius)) if radius else 0
            confidence = 0.6 * circularity + 0.4 * fill
            if predicted_mm is not None:
                distance = math.hypot(x - predicted_mm[0], y - predicted_mm[1])
                gate = float(self.config.get("max_association_mm", 140))
                if distance > gate:
                    continue
                score = confidence - 0.65 * distance / max(gate, 1.0)
            else:
                score = confidence
            candidates.append((score, Detection(x, y, (cx, cy), float(radius), confidence)))
        if not candidates:
            return None
        return max(candidates, key=lambda pair: pair[0])[1]
