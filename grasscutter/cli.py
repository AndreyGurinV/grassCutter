"""CLI for grassCutter cart control."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from typing import Any, TextIO

from .controller import CartController

log = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="grasscutter", description="4-wheel BLDC cart control")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--mock", action="store_true", help="Force mock I2C/GPIO")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Print controller status as JSON")
    sub.add_parser("arm", help="Enable contactor (GPIO ESTOP_EN HIGH)")
    sub.add_parser("disarm", help="Disable contactor and zero throttle")
    sub.add_parser("stop", help="Zero all throttles (keep armed state)")
    sub.add_parser(
        "session",
        help="Persistent JSON-lines session on stdin/stdout (for HTTP server)",
    )

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


def _emit(obj: dict[str, Any], out: TextIO = sys.stdout) -> None:
    out.write(json.dumps(obj, separators=(",", ":")) + "\n")
    out.flush()


def _handle_session_command(cart: CartController, msg: dict[str, Any]) -> dict[str, Any]:
    cmd = msg.get("cmd")
    if not isinstance(cmd, str):
        return {"ok": False, "error": "cmd required"}

    if cmd == "status":
        return {"ok": True, "status": cart.status()}
    if cmd == "arm":
        cart.arm()
        return {"ok": True, "status": cart.status()}
    if cmd == "disarm":
        cart.disarm()
        return {"ok": True, "status": cart.status()}
    if cmd == "stop":
        cart.stop()
        return {"ok": True, "status": cart.status()}
    if cmd == "drive":
        v = float(msg.get("v", 0.0))
        w = float(msg.get("w", 0.0))
        cart.arm()
        cart.drive(v, w)
        return {"ok": True, "status": cart.status()}
    if cmd == "wheel":
        index = int(msg["index"])
        speed = float(msg["speed"])
        cart.arm()
        cart.set_wheel(index, speed)
        return {"ok": True, "status": cart.status()}
    if cmd == "calibrate-sweep":
        channel = int(msg["channel"])
        from_v = float(msg.get("from_v", 0.0))
        to_v = float(msg.get("to_v", 4.2))
        step = float(msg.get("step", 0.2))
        delay = float(msg.get("delay", 0.5))
        cart.arm()
        v = from_v
        signed_step = abs(step) if to_v >= from_v else -abs(step)
        lines: list[str] = []
        while True:
            cart._dac.set_volts(channel, v)  # noqa: SLF001 — bench helper
            lines.append(f"ch{channel} = {v:.3f} V")
            time.sleep(delay)
            if (signed_step > 0 and v >= to_v) or (signed_step < 0 and v <= to_v):
                break
            v += signed_step
        cart.stop()
        return {"ok": True, "stdout": "\n".join(lines), "status": cart.status()}
    if cmd == "quit":
        cart.stop()
        return {"ok": True, "event": "bye"}
    return {"ok": False, "error": f"unknown cmd: {cmd}"}


def run_session(cart: CartController, inp: TextIO = sys.stdin, out: TextIO = sys.stdout) -> int:
    """Keep controller open; one JSON request/response per line until quit/EOF."""
    _emit({"ok": True, "event": "ready", "status": cart.status()}, out)
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if not isinstance(msg, dict):
                raise ValueError("object required")
        except (json.JSONDecodeError, ValueError) as exc:
            _emit({"ok": False, "error": f"invalid JSON: {exc}"}, out)
            continue
        try:
            resp = _handle_session_command(cart, msg)
        except Exception as exc:  # noqa: BLE001 — surface to caller
            log.exception("session command failed")
            _emit({"ok": False, "error": str(exc)}, out)
            continue
        _emit(resp, out)
        if msg.get("cmd") == "quit":
            return 0
    return 0


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
        if args.cmd == "session":
            return run_session(cart)
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
