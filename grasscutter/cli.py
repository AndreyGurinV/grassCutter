"""CLI for grassCutter cart control."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from .controller import CartController


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="grasscutter", description="4-wheel BLDC cart control")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--mock", action="store_true", help="Force mock I2C/GPIO")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Print controller status as JSON")
    sub.add_parser("arm", help="Enable contactor (GPIO ESTOP_EN HIGH)")
    sub.add_parser("disarm", help="Disable contactor and zero throttle")
    sub.add_parser("stop", help="Zero all throttles (keep armed state)")

    d = sub.add_parser("drive", help="Tank drive: forward v and turn w in [-1,1]")
    d.add_argument("--v", type=float, required=True, help="Forward speed [-1..1]")
    d.add_argument("--w", type=float, default=0.0, help="Turn rate [-1..1], + = right")
    d.add_argument("--hold", type=float, default=0.0, help="Hold seconds then stop")

    w = sub.add_parser("wheel", help="Set one wheel signed speed")
    w.add_argument("index", type=int, choices=(0, 1, 2, 3), help="0=FL 1=FR 2=RL 3=RR")
    w.add_argument("speed", type=float, help="Signed speed [-1..1]")
    w.add_argument("--hold", type=float, default=0.0)

    c = sub.add_parser("calibrate-sweep", help="Sweep one DAC channel volts (bench only)")
    c.add_argument("channel", type=int, choices=(0, 1, 2, 3))
    c.add_argument("--from-v", type=float, default=0.0)
    c.add_argument("--to-v", type=float, default=4.2)
    c.add_argument("--step", type=float, default=0.2)
    c.add_argument("--delay", type=float, default=0.5)

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    mock = args.mock or None
    with CartController(mock=True if mock else None) as cart:
        if args.cmd == "status":
            print(json.dumps(cart.status(), indent=2))
            return 0
        if args.cmd == "arm":
            cart.arm()
            print(json.dumps(cart.status(), indent=2))
            return 0
        if args.cmd == "disarm":
            cart.disarm()
            print(json.dumps(cart.status(), indent=2))
            return 0
        if args.cmd == "stop":
            cart.stop()
            print(json.dumps(cart.status(), indent=2))
            return 0
        if args.cmd == "drive":
            cart.arm()
            cart.drive(args.v, args.w)
            print(json.dumps(cart.status(), indent=2))
            if args.hold > 0:
                time.sleep(args.hold)
                cart.stop()
            return 0
        if args.cmd == "wheel":
            cart.arm()
            cart.set_wheel(args.index, args.speed)
            print(json.dumps(cart.status(), indent=2))
            if args.hold > 0:
                time.sleep(args.hold)
                cart.stop()
            return 0
        if args.cmd == "calibrate-sweep":
            cart.arm()
            v = args.from_v
            step = abs(args.step) if args.to_v >= args.from_v else -abs(args.step)
            while True:
                cart._dac.set_volts(args.channel, v)  # noqa: SLF001 — bench helper
                print(f"ch{args.channel} = {v:.3f} V")
                time.sleep(args.delay)
                if (step > 0 and v >= args.to_v) or (step < 0 and v <= args.to_v):
                    break
                v += step
            cart.stop()
            return 0
        return 1


if __name__ == "__main__":
    sys.exit(main())
