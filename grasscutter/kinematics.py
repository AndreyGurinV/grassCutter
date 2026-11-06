"""Throttle mapping and skid-steer kinematics."""

from __future__ import annotations

from dataclasses import dataclass

from .config import ThrottleCal

# Wheel index: 0=FL, 1=FR, 2=RL, 3=RR
FL, FR, RL, RR = 0, 1, 2, 3


@dataclass(frozen=True)
class WheelCommand:
    """Signed speed in [-1, 1]; sign encodes direction (neg = reverse)."""

    speed: float

    @property
    def forward(self) -> bool:
        return self.speed >= 0

    @property
    def magnitude(self) -> float:
        return abs(self.speed)


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def speed_to_volts(magnitude: float, cal: ThrottleCal) -> float:
    """Map |speed| in [0,1] to SIG volts using idle/start/max calibration."""
    m = clamp(magnitude, 0.0, 1.0)
    if m <= 0.0:
        return cal.v_idle
    # Linear from v_start at small speed to v_max at 1.0
    return cal.v_start + m * (cal.v_max - cal.v_start)


def tank_drive(
    v: float,
    w: float,
    *,
    max_speed: float = 1.0,
    max_turn: float = 1.0,
) -> tuple[WheelCommand, WheelCommand, WheelCommand, WheelCommand]:
    """
    Skid-steer: left = v - w, right = v + w.
    v > 0 forward, w > 0 turn right (right side faster when going forward).
    """
    v = clamp(v, -max_speed, max_speed)
    w = clamp(w, -max_turn, max_turn)
    left = clamp(v - w, -1.0, 1.0)
    right = clamp(v + w, -1.0, 1.0)
    return (
        WheelCommand(left),   # FL
        WheelCommand(right),  # FR
        WheelCommand(left),   # RL
        WheelCommand(right),  # RR
    )
