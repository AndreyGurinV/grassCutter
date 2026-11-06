"""GPIO helpers with a mock backend for non-Pi hosts."""

from __future__ import annotations

import logging
import os
from typing import Protocol

log = logging.getLogger(__name__)

BCM = "BCM"
OUT = "OUT"
HIGH = 1
LOW = 0


class GpioBackend(Protocol):
    def setup_output(self, pin: int, initial: int = LOW) -> None: ...
    def write(self, pin: int, value: int) -> None: ...
    def read(self, pin: int) -> int: ...
    def cleanup(self) -> None: ...


class MockGpio:
    def __init__(self) -> None:
        self.pins: dict[int, int] = {}

    def setup_output(self, pin: int, initial: int = LOW) -> None:
        self.pins[pin] = int(initial)
        log.debug("MockGpio setup %s = %s", pin, initial)

    def write(self, pin: int, value: int) -> None:
        self.pins[pin] = int(value)
        log.debug("MockGpio write %s = %s", pin, value)

    def read(self, pin: int) -> int:
        return int(self.pins.get(pin, LOW))

    def cleanup(self) -> None:
        self.pins.clear()


class RPiGpio:
    def __init__(self) -> None:
        import RPi.GPIO as GPIO

        self._GPIO = GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

    def setup_output(self, pin: int, initial: int = LOW) -> None:
        level = self._GPIO.HIGH if initial else self._GPIO.LOW
        self._GPIO.setup(pin, self._GPIO.OUT, initial=level)

    def write(self, pin: int, value: int) -> None:
        self._GPIO.output(pin, self._GPIO.HIGH if value else self._GPIO.LOW)

    def read(self, pin: int) -> int:
        return 1 if self._GPIO.input(pin) else 0

    def cleanup(self) -> None:
        self._GPIO.cleanup()


def create_gpio(*, force_mock: bool | None = None) -> GpioBackend:
    if force_mock is None:
        force_mock = os.environ.get("GRASSCUTTER_MOCK", "").lower() in ("1", "true", "yes")
    if force_mock:
        log.info("Using MockGpio (GRASSCUTTER_MOCK)")
        return MockGpio()
    try:
        return RPiGpio()
    except Exception as exc:  # noqa: BLE001 — fall back on any import/init failure
        log.warning("RPi.GPIO unavailable (%s); using MockGpio", exc)
        return MockGpio()
