"""High-level cart controller: DAC speed, reverse interlock, E-STOP fail-safe."""

from __future__ import annotations

import atexit
import logging
import os
import signal
import threading
import time
from typing import Sequence

from .config import Config, load_config
from .gpio_backend import LOW, GpioBackend, create_gpio
from .kinematics import (
    FL,
    FR,
    RL,
    RR,
    WheelCommand,
    speed_to_volts,
    tank_drive,
)
from .mcp4728 import MCP4728, MockBus, open_smbus

log = logging.getLogger(__name__)

WHEEL_NAMES = ("FL", "FR", "RL", "RR")


class CartController:
    def __init__(
        self,
        config: Config | None = None,
        *,
        gpio: GpioBackend | None = None,
        dac: MCP4728 | None = None,
        mock: bool | None = None,
    ) -> None:
        self.cfg = config or load_config()
        if mock is None:
            mock = os.environ.get("GRASSCUTTER_MOCK", "").lower() in ("1", "true", "yes")
        self._mock = mock
        self._gpio = gpio or create_gpio(force_mock=mock)
        if dac is not None:
            self._dac = dac
        elif mock:
            self._dac = MCP4728(MockBus(), self.cfg.mcp4728_address, self.cfg.dac_vdd_volts)
        else:
            try:
                bus = open_smbus(self.cfg.i2c_bus)
                self._dac = MCP4728(bus, self.cfg.mcp4728_address, self.cfg.dac_vdd_volts)
            except Exception as exc:  # noqa: BLE001
                log.warning("I2C open failed (%s); using MockBus", exc)
                self._dac = MCP4728(MockBus(), self.cfg.mcp4728_address, self.cfg.dac_vdd_volts)

        self._lock = threading.RLock()
        self._armed = False
        self._dir_reverse = [False, False, False, False]
        self._speeds = [0.0, 0.0, 0.0, 0.0]
        self._closed = False

        self._setup_gpio()
        self._install_failsafe()
        self.stop()

    def _setup_gpio(self) -> None:
        g = self.cfg.gpio
        for pin in self.cfg.dir_pins:
            self._gpio.setup_output(pin, LOW)
        self._gpio.setup_output(g.estop_en, LOW)
        self._gpio.setup_output(g.status_led, LOW)

    def _install_failsafe(self) -> None:
        atexit.register(self._failsafe)

        def _handler(signum: int, _frame: object) -> None:
            log.warning("Signal %s — fail-safe stop", signum)
            self._failsafe()
            raise SystemExit(128 + signum)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handler)
            except ValueError:
                # Not in main thread
                pass

    def _failsafe(self) -> None:
        if self._closed:
            return
        try:
            self._dac.zero_all()
        except Exception:  # noqa: BLE001
            log.exception("failsafe: DAC zero failed")
        try:
            self._gpio.write(self.cfg.gpio.estop_en, LOW)
            self._gpio.write(self.cfg.gpio.status_led, LOW)
            for pin in self.cfg.dir_pins:
                self._gpio.write(pin, LOW)
        except Exception:  # noqa: BLE001
            log.exception("failsafe: GPIO clear failed")
        self._armed = False
        self._speeds = [0.0, 0.0, 0.0, 0.0]

    @property
    def armed(self) -> bool:
        return self._armed

    def arm(self) -> None:
        with self._lock:
            self.stop()
            self._gpio.write(self.cfg.gpio.estop_en, 1)
            self._gpio.write(self.cfg.gpio.status_led, 1)
            self._armed = True
            log.info("ARMED — contactor enable HIGH")

    def disarm(self) -> None:
        with self._lock:
            self.stop()
            self._gpio.write(self.cfg.gpio.estop_en, LOW)
            self._gpio.write(self.cfg.gpio.status_led, LOW)
            self._armed = False
            log.info("DISARMED — contactor enable LOW")

    def stop(self) -> None:
        with self._lock:
            self._dac.zero_all()
            self._speeds = [0.0, 0.0, 0.0, 0.0]

    def close(self) -> None:
        with self._lock:
            self._failsafe()
            self._gpio.cleanup()
            self._closed = True

    def __enter__(self) -> CartController:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def set_wheel(self, wheel: int, speed: float) -> None:
        """Set one wheel signed speed in [-1, 1]. Requires armed for non-zero."""
        if wheel not in (FL, FR, RL, RR):
            raise ValueError("wheel must be 0..3 (FL FR RL RR)")
        speed = max(-1.0, min(1.0, float(speed)))
        with self._lock:
            if speed != 0.0 and not self._armed:
                raise RuntimeError("not armed — call arm() first")
            self._apply_wheel(wheel, speed)

    def set_wheels(self, speeds: Sequence[float]) -> None:
        if len(speeds) != 4:
            raise ValueError("need 4 speeds")
        with self._lock:
            if any(s != 0.0 for s in speeds) and not self._armed:
                raise RuntimeError("not armed — call arm() first")
            for i, s in enumerate(speeds):
                self._apply_wheel(i, max(-1.0, min(1.0, float(s))))

    def drive(self, v: float, w: float = 0.0) -> None:
        cmds = tank_drive(
            v,
            w,
            max_speed=self.cfg.max_speed,
            max_turn=self.cfg.max_turn,
        )
        self.set_wheels([c.speed for c in cmds])

    def _apply_wheel(self, wheel: int, speed: float) -> None:
        want_reverse = speed < 0
        if want_reverse != self._dir_reverse[wheel] and abs(self._speeds[wheel]) > 1e-6:
            # Coming from non-zero opposite — zero first
            self._write_volts(wheel, 0.0)
            self._speeds[wheel] = 0.0
            time.sleep(self.cfg.direction_change_delay_s)
        elif want_reverse != self._dir_reverse[wheel]:
            # Already zero speed, still wait briefly for controller settle
            time.sleep(self.cfg.direction_change_delay_s)

        if want_reverse != self._dir_reverse[wheel]:
            self._set_reverse(wheel, want_reverse)
            time.sleep(0.05)

        mag = abs(speed)
        volts = speed_to_volts(mag, self.cfg.throttle) if mag > 0 else self.cfg.throttle.v_idle
        if not self._armed:
            volts = self.cfg.throttle.v_idle
        self._write_volts(wheel, volts)
        self._speeds[wheel] = speed

    def _set_reverse(self, wheel: int, reverse: bool) -> None:
        pin = self.cfg.dir_pins[wheel]
        self._gpio.write(pin, 1 if reverse else 0)
        self._dir_reverse[wheel] = reverse
        log.debug("%s reverse=%s", WHEEL_NAMES[wheel], reverse)

    def _write_volts(self, wheel: int, volts: float) -> None:
        self._dac.set_volts(wheel, volts)

    def status(self) -> dict:
        with self._lock:
            volts = [self._dac.code_to_volts(c) for c in self._dac.codes]
            return {
                "armed": self._armed,
                "mock": self._mock,
                "speeds": list(self._speeds),
                "reverse": list(self._dir_reverse),
                "dac_codes": list(self._dac.codes),
                "dac_volts": volts,
                "estop_en": bool(self._gpio.read(self.cfg.gpio.estop_en)),
            }
