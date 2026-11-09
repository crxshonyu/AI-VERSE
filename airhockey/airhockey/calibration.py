"""Calibration commands. All corner and HSV input pixels refer to RAW snapshots.

Examples:
 python3 -m airhockey.calibration snapshot --config config.json --output table.png
 python3 -m airhockey.calibration corners --config config.json --image table.png
 python3 -m airhockey.calibration corners --config config.json --image table.png --points 40,20 600,20 600,460 40,460
 python3 -m airhockey.calibration hsv --config config.json --image table.png --point 320,200 --save
 python3 -m airhockey.calibration lens --images 'calib/*.png' --cols 9 --rows 6 --square-mm 25 --output lens.npz
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import time
import warnings
from pathlib import Path

from .vision import Geometry, PiCameraSource, cv2, np, validate_corners


def _load(path):
    p = Path(path).expanduser().resolve()
    with p.open(encoding="utf-8") as handle:
        config = json.load(handle)
    return p, config


def _save(path, config):
    # Keep the old file until a complete replacement is written.
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_image(path):
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"이미지를 열 수 없습니다: {path}")
    return image


def _point(text):
    try:
        x, y = text.split(",")
        result = float(x), float(y)
        if not all(math.isfinite(v) for v in result):
            raise ValueError
        return result
    except ValueError as exc:
        raise argparse.ArgumentTypeError("좌표는 x,y 형식입니다. 예: 320,240") from exc


def _interactive_points(image, count, title):
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        raise RuntimeError("화면 없는 SSH에서는 --points 또는 --point로 좌표를 입력하세요.")
    points = []
    name = "Airhockey calibration: raw pixels"
    try:
        cv2.namedWindow(name, cv2.WINDOW_AUTOSIZE)

        def click(event, x, y, flags, userdata):
            if event == cv2.EVENT_LBUTTONDOWN and len(points) < count:
                points.append((float(x), float(y)))
            elif event == cv2.EVENT_RBUTTONDOWN and points:
                points.pop()

        cv2.setMouseCallback(name, click)
        print(title)
        print("왼쪽 클릭: 선택, 오른쪽 클릭/r: 취소, Enter: 저장, Esc/q: 종료")
        while True:
            view = image.copy()
            for index, point in enumerate(points):
                p = tuple(int(v) for v in point)
                cv2.circle(view, p, 5, (0, 255, 255), -1)
                cv2.putText(
                    view,
                    str(index + 1),
                    (p[0] + 6, p[1] - 6),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 255),
                    2,
                )
            if len(points) > 1:
                cv2.polylines(
                    view, [np.asarray(points, np.int32)], len(points) == count, (0, 255, 255), 1
                )
            cv2.imshow(name, view)
            key = cv2.waitKey(20) & 0xFF
            if key in (27, ord("q")):
                raise RuntimeError("보정을 취소했습니다.")
            if key == ord("r"):
                points.clear()
            if key in (10, 13) and len(points) == count:
                return points
            if cv2.getWindowProperty(name, cv2.WND_PROP_VISIBLE) < 1:
                raise RuntimeError("보정 창이 닫혔습니다.")
    except cv2.error as exc:
        raise RuntimeError(
            "OpenCV GUI를 사용할 수 없습니다. --points/--point로 좌표를 입력하세요."
        ) from exc
    finally:
        try:
            cv2.destroyAllWindows()
        except cv2.error:
            pass


def snapshot(args):
    if not math.isfinite(args.warmup) or args.warmup < 0:
        raise ValueError("warmup must be finite and non-negative")
    _, config = _load(args.config)
    with PiCameraSource(config["camera"]) as camera:
        deadline = time.monotonic() + float(args.warmup)
        frame = camera.read(timeout_s=2.0)
        while time.monotonic() < deadline:
            frame = camera.read(timeout_s=1.0)
        output = Path(args.output).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(output), frame.image):
            raise RuntimeError(f"이미지 저장 실패: {output}")
        # Raw pixels: lens correction is deliberately not applied to this file.
        metadata = {
            "raw_pixels": True,
            "size": [camera.width, camera.height],
            "camera_settings": config["camera"],
            "sensor_mode": camera.mode_info,
            "SensorTimestamp": frame.metadata.get("SensorTimestamp"),
            "ExposureTime": frame.metadata.get("ExposureTime"),
            "LensPosition": frame.metadata.get("LensPosition"),
            "FrameDuration": frame.metadata.get("FrameDuration"),
        }
        output.with_suffix(output.suffix + ".json").write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )
        print(
            f"RAW 이미지 저장: {output}; 크기={camera.width}x{camera.height}; 모드={camera.mode_info}"
        )
        print("이 이미지의 원본 픽셀을 기준으로 코너와 퍽 색을 지정하세요.")


def corners(args):
    path, config = _load(args.config)
    image = _read_image(args.image)
    size = image.shape[1], image.shape[0]
    expected = int(config["camera"]["width"]), int(config["camera"]["height"])
    if size != expected:
        raise ValueError(
            f"이미지 {size} != camera 설정 {expected}; 크기 변경 없이 다시 촬영하세요."
        )
    if args.points is None:
        points = _interactive_points(
            image,
            4,
            "순서: 1 인간측 왼쪽, 2 인간측 오른쪽, 3 로봇측 오른쪽, 4 로봇측 왼쪽의 테이블 안쪽 벽 교차점",
        )
    else:
        points = args.points
    points = validate_corners(points, size).tolist()
    config.setdefault("vision", {}).update(image_points=points, calibration_size=list(size))
    # Validate distortion and homography before modifying the user's config.
    check_vision = dict(config["vision"])
    if check_vision.get("lens_file"):
        lens = Path(check_vision["lens_file"]).expanduser()
        check_vision["lens_file"] = str(lens if lens.is_absolute() else path.parent / lens)
    geometry = Geometry(config["table"], check_vision, size)
    _save(path, config)
    preview = geometry.prepare(image).copy()
    cv2.polylines(preview, [np.round(geometry.corners).astype(np.int32)], True, (0, 255, 255), 2)
    for i, point in enumerate(geometry.corners):
        cv2.putText(
            preview,
            str(i + 1),
            tuple(np.round(point).astype(int)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 0, 255),
            2,
        )
    output = Path(args.output) if args.output else path.parent / "calibration_preview.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), preview):
        raise RuntimeError(f"설정은 저장했지만 미리보기 저장 실패: {output}")
    print(f"코너 보정 저장: {path}\n왜곡 보정/테이블 영역 확인용: {output}")
    print(
        "테이블 width_mm/length_mm은 안쪽 벽 간 실측값이어야 합니다. 퍽 반지름은 별도 설정입니다."
    )


def hsv(args):
    path, config = _load(args.config)
    image = _read_image(args.image)
    point = args.point or _interactive_points(image, 1, "퍽 중심의 균일한 색 부분을 선택하세요.")[0]
    x, y = (int(round(v)) for v in point)
    if not (0 <= x < image.shape[1] and 0 <= y < image.shape[0]):
        raise ValueError("선택 좌표가 이미지 밖입니다.")
    radius = int(args.radius)
    if radius < 0 or radius > 50:
        raise ValueError("radius는 0..50 픽셀이어야 합니다.")
    crop = image[max(0, y - radius) : y + radius + 1, max(0, x - radius) : x + radius + 1]
    values = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(float)
    # A colour marker/puck needs saturation; do not silently calibrate grey glare.
    vivid = values[(values[:, 1] >= 40) & (values[:, 2] >= 35)]
    if len(vivid) < max(1, len(values) // 4):
        raise ValueError("이 영역은 너무 무채색/어둡습니다. 밝고 선명한 퍽 색 영역을 고르세요.")
    angle = vivid[:, 0] * 2 * math.pi / 180
    centre = (
        math.atan2(np.mean(np.sin(angle)), np.mean(np.cos(angle))) * 180 / (2 * math.pi)
    ) % 180
    residual = (vivid[:, 0] - centre + 90) % 180 - 90
    lower_h = math.floor(centre + float(np.percentile(residual, 5)) - 8)
    upper_h = math.ceil(centre + float(np.percentile(residual, 95)) + 8)
    lower_s = max(40, int(np.percentile(vivid[:, 1], 5)) - 50)
    lower_v = max(30, int(np.percentile(vivid[:, 2], 5)) - 60)
    if upper_h - lower_h >= 179:
        hue_ranges = [(0, 179)]
    elif lower_h < 0:
        hue_ranges = [(0, upper_h), (lower_h + 180, 179)]
    elif upper_h > 179:
        hue_ranges = [(0, upper_h - 180), (lower_h, 179)]
    else:
        hue_ranges = [(lower_h, upper_h)]
    ranges = [[[int(a), lower_s, lower_v], [int(b), 255, 255]] for a, b in hue_ranges]
    print("권장 vision.hsv_ranges = " + json.dumps(ranges))
    print("정지 사진에서 시작한 범위입니다. 실제 조명/속도에서 마스크와 오검출을 다시 확인하세요.")
    if args.save:
        config.setdefault("vision", {})["hsv_ranges"] = ranges
        _save(path, config)
        print(f"저장: {path}")


def lens(args):
    if args.cols < 3 or args.rows < 3 or not math.isfinite(args.square_mm) or args.square_mm <= 0:
        raise ValueError("체커보드 내부 교차점 cols/rows >=3, square-mm >0 필요")
    names = sorted({name for pattern in args.images for name in glob.glob(pattern)})
    if not names:
        raise ValueError("체커보드 이미지 파일이 없습니다.")
    points3d = np.zeros((args.rows * args.cols, 3), np.float32)
    points3d[:, :2] = np.mgrid[0 : args.cols, 0 : args.rows].T.reshape(-1, 2) * args.square_mm
    object_points, image_points = [], []
    size = None
    accepted = []
    for name in names:
        image = _read_image(name)
        current = image.shape[1], image.shape[0]
        if size is None:
            size = current
        if current != size:
            raise ValueError("렌즈 보정 사진의 크기가 서로 다릅니다.")
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        found, corners_found = cv2.findChessboardCorners(
            gray,
            (args.cols, args.rows),
            cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE,
        )
        if not found:
            print(f"건너뜀 (체커보드 미검출): {name}")
            continue
        precise = cv2.cornerSubPix(
            gray,
            corners_found,
            (11, 11),
            (-1, -1),
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 40, 0.001),
        )
        object_points.append(points3d.copy())
        image_points.append(precise)
        accepted.append(name)
    if len(object_points) < 8:
        raise ValueError(
            f"체커보드 검출 {len(object_points)}장. 위치/기울기가 다른 사진을 최소 8장, 권장 15~25장 준비하세요."
        )
    rms, K, dist, rvecs, tvecs = cv2.calibrateCamera(object_points, image_points, size, None, None)
    if not np.isfinite(K).all() or not np.isfinite(dist).all() or not math.isfinite(rms):
        raise ValueError("렌즈 보정 계산 실패")
    output = Path(args.output).expanduser()
    if output.suffix.lower() != ".npz":
        raise ValueError("렌즈 파일 출력명은 .npz여야 합니다.")
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez(output, camera_matrix=K, dist_coeffs=dist, image_size=np.array(size), rms_px=rms)
    print(f"렌즈 보정 저장: {output}; {len(accepted)}장, RMS 재투영 오차={rms:.3f} px")
    if rms > 1.0:
        warnings.warn(
            "재투영 RMS가 1px보다 큽니다. 흐림, 포즈 다양성, 가장자리 커버리지와 모델 적합성을 확인하세요.",
            stacklevel=2,
        )
    for name, obj, measured, rv, tv in zip(
        accepted, object_points, image_points, rvecs, tvecs, strict=True
    ):
        projected, _ = cv2.projectPoints(obj, rv, tv, K, dist)
        error = np.sqrt(
            np.mean(np.sum((projected.reshape(-1, 2) - measured.reshape(-1, 2)) ** 2, axis=1))
        )
        print(f"  {name}: {error:.3f} px")
    print("vision.lens_file에 이 파일 경로를 지정한 후 코너 보정을 다시 실행하세요.")
    print("렌즈/센서 모드/해상도/초점/촬영 크롭이 바뀌면 다시 보정해야 합니다.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="에어하키 카메라/색/테이블 보정")
    commands = parser.add_subparsers(dest="command", required=True)
    snap = commands.add_parser("snapshot", help="보정 전 RAW 사진 저장")
    snap.add_argument("--config", default="config/local.json")
    snap.add_argument("--output", required=True)
    snap.add_argument("--warmup", type=float, default=1.0)
    snap.set_defaults(func=snapshot)
    corner = commands.add_parser("corners", help="네 모서리를 실측 mm 좌표에 대응")
    corner.add_argument("--config", default="config/local.json")
    corner.add_argument("--image", required=True)
    corner.add_argument("--points", nargs=4, type=_point)
    corner.add_argument("--output", help="보정 결과 미리보기 PNG 경로")
    corner.set_defaults(func=corners)
    colour = commands.add_parser("hsv", help="퍽 색 HSV 범위 추정")
    colour.add_argument("--config", default="config/local.json")
    colour.add_argument("--image", required=True)
    colour.add_argument("--point", type=_point)
    colour.add_argument("--radius", type=int, default=3)
    colour.add_argument("--save", action="store_true")
    colour.set_defaults(func=hsv)
    intrinsics = commands.add_parser("lens", help="체커보드 RAW 사진으로 렌즈 왜곡 보정")
    intrinsics.add_argument("--images", nargs="+", required=True)
    intrinsics.add_argument("--cols", type=int, default=9)
    intrinsics.add_argument("--rows", type=int, default=6)
    intrinsics.add_argument("--square-mm", type=float, default=25)
    intrinsics.add_argument("--output", default="lens.npz")
    intrinsics.set_defaults(func=lens)
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (ValueError, RuntimeError, OSError, TimeoutError, cv2.error) as exc:
        parser.exit(2, f"보정 오류: {exc}\n")


if __name__ == "__main__":
    main()
