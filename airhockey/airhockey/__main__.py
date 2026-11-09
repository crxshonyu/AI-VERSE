"""Run with `python3 -m airhockey --help`."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from . import __version__
from .config import DEFAULTS


def main(argv=None):
    parser = argparse.ArgumentParser(description="Raspberry Pi camera air-hockey defence")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-config", help="write example JSON (does not overwrite)")
    init.add_argument("path", nargs="?", default="config/local.json")
    run = sub.add_parser("run", help="run simulation, camera or prerecorded video")
    run.add_argument("--config", default="config/local.json")
    run.add_argument("--source", choices=("sim", "camera", "video"), default="sim")
    run.add_argument("--video", help="clip file, only for --source video")
    run.add_argument("--motor", choices=("dry-run", "serial"), default="dry-run")
    run.add_argument("--port", help="override motor.port, e.g. /dev/ttyACM0")
    run.add_argument("--arm", action="store_true", help="allow real motor commands")
    run.add_argument("--home", action="store_true", help="explicitly home real motor")
    run.add_argument("--gpio", action="store_true", help="enable configured Pi GPIO inputs/LED")
    run.add_argument("--start", action="store_true", help="start match after sensors clear")
    run.add_argument(
        "--auto-resume", action="store_true", help="simulation only: resume after goals"
    )
    run.add_argument("--show", action="store_true", help="OpenCV live preview (requires display)")
    run.add_argument("--mask", action="store_true", help="also preview HSV mask; requires --show")
    run.add_argument("--seconds", type=float, default=0, help="0=operator-controlled duration")
    run.add_argument("--output-dir", help="new or empty run directory")
    run.add_argument("--record", help="optional annotated .avi (MJPG) output, can reduce loop FPS")
    args = parser.parse_args(argv)
    if args.command == "init-config":
        path = Path(args.path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            json.dump(DEFAULTS, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        print(f"Example settings created: {path}; replace all dimensions with measurements.")
        return 0
    if args.source == "video" and not args.video:
        parser.error("--source video requires --video FILE")
    if args.motor == "serial" and (args.source != "camera" or not args.arm or not args.home):
        parser.error("real motor requires --source camera --motor serial --arm --home")
    if args.auto_resume and args.source != "sim":
        parser.error("--auto-resume is simulation-only; real games require operator resume")
    if args.gpio and args.source != "camera":
        parser.error("--gpio is available only with the live camera")
    if args.mask and not args.show:
        parser.error("--mask requires --show")
    if not math.isfinite(args.seconds) or args.seconds < 0:
        parser.error("--seconds must be finite and non-negative")
    from .app import run_application

    return run_application(args)


if __name__ == "__main__":
    raise SystemExit(main())
