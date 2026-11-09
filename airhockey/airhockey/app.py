"""Main control loop. There is intentionally no background motor heartbeat.

The motor controller times out if this loop hangs in capture, processing or I/O.
All real motor commands require a live camera plus explicit --arm --home.
"""

from __future__ import annotations

import csv
import json
import math
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .config import load_config
from .game import Game, GoalDebouncer
from .io import GPIOInterface, OperatorInput
from .motor import DryRunMotor, SerialMotor
from .physics import move_time_s, predict_impact
from .tracking import PuckTracker

FIELDS = [
    "elapsed_s",
    "capture_monotonic_s",
    "frame_id",
    "frame_age_ms",
    "processing_ms",
    "detected",
    "track_samples",
    "x_mm",
    "y_mm",
    "vx_mm_s",
    "vy_mm_s",
    "target_x_mm",
    "command_x_mm",
    "remaining_s",
    "bounces",
    "move_time_s",
    "reachable_estimate",
    "motor_x_est_mm",
    "motor_v_est_mm_s",
    "game_state",
    "human_score",
    "robot_score",
    "reason",
]


def annotate(
    image,
    geometry,
    detection,
    track,
    impact,
    motor_state,
    game,
    elapsed,
    fps,
    frame_age_ms,
    reachable,
    reason,
    simulated=False,
):
    from .vision import cv2, np

    view = image.copy()
    corners = np.round(geometry.corners).astype(np.int32)
    cv2.polylines(view, [corners], True, (190, 190, 130), 1)
    if impact:
        points = [geometry.mm_to_pixel(x, y) for x, y in impact.path]
        cv2.polylines(view, [np.asarray(points, np.int32)], False, (50, 230, 255), 2)
        cv2.drawMarker(
            view,
            geometry.mm_to_pixel(impact.x_mm, impact.y_mm),
            (40, 255, 80) if reachable else (50, 70, 255),
            cv2.MARKER_CROSS,
            18,
            2,
        )
    if detection:
        centre = tuple(round(v) for v in detection.pixel)
        cv2.circle(view, centre, round(detection.radius_px) + 3, (80, 255, 80), 1)
    # Status strings remain ASCII because OpenCV's built-in fonts lack Hangul.
    header = "SYNTHETIC TEST / NO HARDWARE" if simulated else "CAMERA AIR HOCKEY"
    texts = [
        header,
        f"{game.state}  HUMAN {game.human_score} : {game.robot_score} ROBOT",
        f"loop {fps:.1f} fps | age {frame_age_ms:.1f} ms | {elapsed:.1f}s",
        f"{reason}",
        f"paddle estimate {motor_state.position_mm:.1f} mm",
    ]
    if track:
        texts.append(f"v=({track.vx_mm_s:.0f},{track.vy_mm_s:.0f}) mm/s")
    if impact:
        texts.append(
            f"hit x={impact.x_mm:.0f} | ETA={impact.remaining_s * 1000:.0f}ms | b={impact.bounces}"
        )
    for index, text in enumerate(texts):
        y = 18 + index * 18
        cv2.putText(view, text, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 3)
        cv2.putText(view, text, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (240, 245, 245), 1)
    return view


def run_application(args):
    if args.motor == "serial" and (args.source != "camera" or not args.arm or not args.home):
        raise ValueError("Real motor requires live camera and explicit --arm --home")
    if args.auto_resume and args.source != "sim":
        raise ValueError("Automatic goal resume is simulation-only")
    if not math.isfinite(args.seconds) or args.seconds < 0:
        raise ValueError("Run duration must be finite and non-negative")
    from .simulation import SimulationSource, demo_config
    from .vision import Geometry, PiCameraSource, PuckDetector, VideoSource, cv2

    config = load_config(args.config)
    if args.source == "sim":
        config = demo_config(config)
    if args.source != "camera":
        config["gpio"]["enabled"] = False
    elif args.gpio:
        config["gpio"]["enabled"] = True
    if args.show and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise RuntimeError("--show needs a local display; omit it for terminal/SSH operation")
    if args.port:
        config["motor"]["port"] = args.port
    if args.record and Path(args.record).suffix.lower() != ".avi":
        raise ValueError("--record expects an .avi filename (MJPG)")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    output = Path(args.output_dir or f"runs/{stamp}").expanduser()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError(f"Output directory must be empty to preserve prior results: {output}")
    (output / "effective_config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    source = motor = gpio = video_writer = log_handle = None
    last_view = None
    game = Game(config["game"]["duration_s"])
    stop_requested = False

    def request_stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True
        # Interrupt blocking startup/homing too; finally disables the motor.
        raise KeyboardInterrupt

    old_signals = {s: signal.signal(s, request_stop) for s in (signal.SIGINT, signal.SIGTERM)}
    count = detections = predictions = unreachable = stale_count = 0
    frame_ages = []
    process_times = []
    first_capture = last_capture = None
    started = time.monotonic()
    error = None
    try:
        if args.source == "sim":
            source = SimulationSource(config)
        elif args.source == "video":
            source = VideoSource(args.video, realtime=True)
        else:
            source = PiCameraSource(config["camera"])
            print("Camera sensor mode:", source.mode_info, flush=True)
            # Camera startup/AE/AWB settling happens BEFORE any motor connection.
            source.read(timeout_s=2.0)
            for _ in range(8):
                source.read(timeout_s=0.25)
        geometry = Geometry(config["table"], config["vision"], (source.width, source.height))
        detector = PuckDetector(geometry, config["vision"])
        tracker = PuckTracker(config["table"], config["tracking"])
        m = config["motor"]
        motor_options = {
            key: m[key] for key in ("x_min_mm", "x_max_mm", "max_speed_mm_s", "max_accel_mm_s2")
        }
        if args.motor == "serial":
            motor = SerialMotor(m["port"], baudrate=m["baudrate"], **motor_options)
            print("Connecting motor; verifying travel/speed/acceleration...", flush=True)
            motor.connect()
            print("Explicit homing in progress. Keep paddle travel clear.", flush=True)
            motor.home(timeout_s=m["home_timeout_s"])
            print("Homing complete. Position is a step-count estimate.", flush=True)
        else:
            motor = DryRunMotor(**motor_options)
            motor.connect()
        goals = GoalDebouncer(
            config["game"]["goal_clear_s"], config["gpio"]["min_goal_pulse_s"], time.monotonic()
        )
        gpio = GPIOInterface(config["gpio"], goals)
        operators = OperatorInput()
        log_handle = (output / "telemetry.csv").open(
            "w", newline="", encoding="utf-8", buffering=16384
        )
        log = csv.DictWriter(log_handle, fieldnames=FIELDS)
        log.writeheader()
        if args.record:
            path = Path(args.record).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            video_writer = cv2.VideoWriter(
                str(path),
                cv2.VideoWriter_fourcc(*"MJPG"),
                float(getattr(source, "fps", config["camera"]["fps"])),
                (source.width, source.height),
            )
            if not video_writer.isOpened():
                raise RuntimeError("Cannot open MJPG recording; check codec/path")
        ctl = config["control"]
        last_sent = None
        last_command = -math.inf
        holding = False
        last_status = -math.inf
        last_display = -math.inf
        display_fps = 0.0
        status_frames = 0
        pending_start = bool(args.start)
        goal_at = None
        stale_streak = 0
        started = time.monotonic()
        last_status = started
        print(
            f"Source={args.source}, motor={args.motor}, GPIO={config['gpio']['enabled']}",
            flush=True,
        )
        print("Commands + Enter: start, pause, resume, new, quit. Preview: s/p/r/n/q.", flush=True)
        print(f"Logs: {output.resolve()}", flush=True)
        while not stop_requested:
            loop_now = time.monotonic()
            if args.seconds and loop_now - started >= args.seconds:
                break
            motor_state = motor.poll(loop_now)
            if args.source == "sim":
                source.set_active(game.state == "RUNNING")
                source.set_paddle(motor_state.position_mm)
            try:
                frame = source.read(timeout_s=ctl["camera_timeout_s"])
            except EOFError:
                break
            compute_start = time.monotonic()
            if first_capture is None:
                first_capture = frame.timestamp_s
            last_capture = frame.timestamp_s
            prepared = geometry.prepare(frame.image)
            # Handle sensor events BEFORE start/resume, so an earlier blocked
            # sensor cannot become a goal in a newly resumed game.
            if args.source == "sim":
                for name, active, timestamp in source.pop_events():
                    goals.edge(name, active, timestamp)
            now = time.monotonic()
            previous_game_state = game.state
            for name, timestamp in gpio.poll(now):
                if game.goal(name, timestamp):
                    goal_at = now
                    print(
                        f"GOAL: human {game.human_score} : robot {game.robot_score}. Recover puck; type resume.",
                        flush=True,
                    )
            game.tick(now)
            commands = operators.poll()
            if args.show:
                key = cv2.waitKey(1) & 0xFF
                mapped = {
                    ord("s"): "start",
                    ord("p"): "pause",
                    ord("r"): "resume",
                    ord("n"): "new",
                    ord("q"): "quit",
                    27: "quit",
                }
                if key in mapped:
                    commands.append(mapped[key])
            if pending_start and goals.all_clear(now):
                commands.append("start")
                pending_start = False
            if (
                args.auto_resume
                and game.state == "GOAL"
                and goal_at is not None
                and now - goal_at >= 1.0
            ):
                commands.append("resume")
            for command in commands:
                if command in ("start", "resume"):
                    if not game.start(now, goals.all_clear(now)):
                        print("Cannot start/resume:", game.reason, flush=True)
                    tracker.reset("operator_resume")
                elif command == "pause":
                    game.pause()
                elif command == "new":
                    game.reset()
                    tracker.reset("new_match")
                elif command in ("quit", "q", "stop"):
                    stop_requested = True
                elif command:
                    print("Use start, pause, resume, new or quit", flush=True)
            if stop_requested:
                break
            if game.state != previous_game_state:
                tracker.reset("game_transition")
                motor.hold()
                holding = True
                last_sent = None
            age = now - frame.timestamp_s
            fresh_frame = -0.01 <= age <= ctl["max_frame_age_s"]
            if fresh_frame:
                detection = detector.detect(prepared, tracker.association(frame.timestamp_s))
                stale_streak = 0
            else:
                detection = None
                stale_count += 1
                stale_streak += 1
                tracker.reset("stale_frame")
            track = impact = None
            desired = None
            reachable = None
            move_time = None
            reason = game.state.lower()
            if game.state == "RUNNING" and fresh_frame:
                if detection:
                    track = tracker.update(detection.x_mm, detection.y_mm, frame.timestamp_s)
                    reason = tracker.reason
                else:
                    tracker.miss(frame.timestamp_s)
                    reason = "no_puck_hold"
            else:
                tracker.reset("not_running_or_stale")
                if not fresh_frame:
                    reason = "stale_frame_hold"
            # The processing time itself counts towards the remaining deadline.
            now = time.monotonic()
            motor_state = motor.poll(now)
            # Reading/writing serial state can take time; assess freshness at
            # its completion, not at the timestamp taken before motor.poll().
            now = time.monotonic()
            age = now - frame.timestamp_s
            if track and age <= min(ctl["max_track_age_s"], ctl["max_frame_age_s"]):
                impact = predict_impact(
                    track,
                    config["table"],
                    now,
                    extra_latency_s=ctl["extra_latency_s"],
                    horizon_s=ctl["prediction_horizon_s"],
                    min_incoming_speed_mm_s=ctl["min_incoming_speed_mm_s"],
                )
                if impact:
                    desired = min(m["x_max_mm"], max(m["x_min_mm"], impact.x_mm))
                    move_time = move_time_s(
                        motor_state.position_mm,
                        motor_state.velocity_mm_s,
                        desired,
                        m["max_speed_mm_s"],
                        m["max_accel_mm_s2"],
                    )
                    reachable = (
                        move_time <= impact.remaining_s and abs(desired - impact.x_mm) <= 1e-6
                    )
                    reason = "defend" if reachable else "defend_unreachable_estimate"
                elif track.vy_mm_s < -ctl["min_incoming_speed_mm_s"] and ctl["return_to_center"]:
                    desired = (m["x_min_mm"] + m["x_max_mm"]) / 2
                    reason = "return_to_center"
                else:
                    reason = "no_future_intercept"
            elif track:
                reason = "processing_too_old_hold"
            command_expires = frame.timestamp_s + min(
                ctl["max_track_age_s"], ctl["max_frame_age_s"]
            )
            if impact:
                command_expires = min(
                    command_expires,
                    frame.timestamp_s + impact.capture_eta_s - ctl["extra_latency_s"],
                )
            if desired is not None and time.monotonic() >= command_expires:
                desired = None
                reason = "command_expired_hold"
            if stale_streak >= 5:
                raise RuntimeError(
                    "Five consecutive stale camera frames; reduce load/FPS and restart"
                )
            if desired is None:
                if not holding:
                    motor.hold()
                    holding = True
                    last_sent = None
            elif now - last_command >= 1.0 / ctl["command_hz"]:
                if last_sent is None or abs(desired - last_sent) >= ctl["target_deadband_mm"]:
                    accepted = motor.set_target(desired, expires_at_s=command_expires)
                    if accepted:
                        last_command, last_sent = time.monotonic(), desired
                        holding = False
                    else:
                        # The motor adapter rechecks the deadline after its own
                        # serial poll. Expiry causes HOLD without a new MOVE.
                        last_sent = None
                        holding = True
                        reason = "command_expired_hold"
            gpio.show(game.color())
            count += 1
            status_frames += 1
            detections += int(detection is not None)
            predictions += int(impact is not None)
            unreachable += int(impact is not None and not reachable)
            processing_ms = (time.monotonic() - compute_start) * 1000
            frame_ages.append(max(0, age) * 1000)
            process_times.append(processing_ms)
            # Bound telemetry summaries for multi-hour exhibitions (CSV is complete).
            if len(frame_ages) > 20000:
                frame_ages.pop(0)
                process_times.pop(0)
            row = dict.fromkeys(FIELDS, "")
            row.update(
                elapsed_s=round(now - started, 6),
                capture_monotonic_s=frame.timestamp_s,
                frame_id=frame.sequence,
                frame_age_ms=round(age * 1000, 3),
                processing_ms=round(processing_ms, 3),
                detected=int(detection is not None),
                track_samples=track.samples if track else 0,
                motor_x_est_mm=round(motor_state.position_mm, 3),
                motor_v_est_mm_s=round(motor_state.velocity_mm_s, 3),
                game_state=game.state,
                human_score=game.human_score,
                robot_score=game.robot_score,
                reason=reason,
                command_x_mm="" if last_sent is None else round(last_sent, 3),
            )
            if detection:
                row.update(x_mm=round(detection.x_mm, 3), y_mm=round(detection.y_mm, 3))
            if track:
                row.update(vx_mm_s=round(track.vx_mm_s, 3), vy_mm_s=round(track.vy_mm_s, 3))
            if impact:
                row.update(
                    target_x_mm=round(impact.x_mm, 3),
                    remaining_s=round(impact.remaining_s, 6),
                    bounces=impact.bounces,
                    move_time_s=round(move_time, 6),
                    reachable_estimate=int(reachable),
                )
            log.writerow(row)
            if now - last_status >= 1.0:
                interval = now - last_status if math.isfinite(last_status) else now - started
                display_fps = status_frames / max(interval, 1e-6)
                status_frames, last_status = 0, now
                log_handle.flush()
                target_text = (
                    f"x={impact.x_mm:.1f} ETA={impact.remaining_s * 1000:.0f}ms" if impact else "-"
                )
                print(
                    f"{now - started:6.1f}s {game.state:8s} {display_fps:5.1f}fps "
                    f"age={age * 1000:5.1f}ms target={target_text} {reason}",
                    flush=True,
                )
            # Control has already been dispatched. Rendering is limited to20Hz
            # unless explicitly recording, and has no effect on timestamps.
            if video_writer or now - last_display >= 0.05:
                last_view = annotate(
                    prepared,
                    geometry,
                    detection,
                    track,
                    impact,
                    motor_state,
                    game,
                    now - started,
                    display_fps,
                    age * 1000,
                    reachable,
                    reason,
                    args.source == "sim",
                )
                last_display = now
                if args.show:
                    cv2.imshow("Air hockey", last_view)
                    if args.mask and detector.last_mask is not None:
                        cv2.imshow("Puck mask", detector.last_mask)
                if video_writer:
                    video_writer.write(last_view)
        print(f"Finished. Score human {game.human_score} : robot {game.robot_score}", flush=True)
    except KeyboardInterrupt:
        print("Operator stop; disabling motor.", flush=True)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        game.fault(error)
        print("STOPPED:", error, flush=True)
        if gpio:
            try:
                gpio.show(game.color())
            except Exception:
                pass
    finally:
        # Stop/disable FIRST, before filesystem writes or slow camera shutdown.
        if motor:
            try:
                motor.stop("application_exit")
            except Exception:
                pass
        for obj in (motor, source, gpio):
            if obj:
                try:
                    obj.close()
                except Exception:
                    pass
        if video_writer:
            video_writer.release()
        if log_handle:
            log_handle.close()
        if last_view is not None:
            cv2.imwrite(str(output / "last_frame.png"), last_view)
        if args.show:
            cv2.destroyAllWindows()
        duration = time.monotonic() - started

        def percentile(values, fraction):
            if not values:
                return None
            values = sorted(values)
            return values[min(len(values) - 1, int((len(values) - 1) * fraction))]

        summary = {
            "software_version": __version__,
            "source": args.source,
            "motor": args.motor,
            "hardware_test": args.source == "camera",
            "duration_s": duration,
            "frames": count,
            "detections": detections,
            "predictions": predictions,
            "unreachable_prediction_frames": unreachable,
            "stale_frames": stale_count,
            "loop_fps": count / max(duration, 1e-6),
            "capture_fps": (
                (count - 1) / (last_capture - first_capture)
                if count > 1 and last_capture > first_capture
                else None
            ),
            "p95_frame_age_ms": percentile(frame_ages, 0.95),
            "p95_processing_ms": percentile(process_times, 0.95),
            "summary_window": "last at most 20000 frames; full telemetry in CSV",
            "human_score": game.human_score,
            "robot_score": game.robot_score,
            "game_state": game.state,
            "error": error,
            "notes": "Dry-run and synthetic results are not hardware speed/accuracy measurements. "
            "Serial position is an open-loop step count, not encoder feedback.",
        }
        (output / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for s, handler in old_signals.items():
            signal.signal(s, handler)
    return 1 if error else 0
