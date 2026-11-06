from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ThrottleCal:
    v_idle: float
    v_start: float
    v_max: float


@dataclass(frozen=True)
class GpioMap:
    dir_fl: int
    dir_fr: int
    dir_rl: int
    dir_rr: int
    estop_en: int
    status_led: int


@dataclass(frozen=True)
class Config:
    i2c_bus: int
    mcp4728_address: int
    gpio: GpioMap
    dac_vdd_volts: float
    throttle: ThrottleCal
    direction_change_delay_s: float
    max_speed: float
    max_turn: float
    raw: dict[str, Any]

    @property
    def dir_pins(self) -> tuple[int, int, int, int]:
        g = self.gpio
        return (g.dir_fl, g.dir_fr, g.dir_rl, g.dir_rr)


def _find_config(path: Path | None) -> Path:
    if path is not None:
        return path
    here = Path(__file__).resolve().parents[1]
    for candidate in (
        Path("config/config.yaml"),
        here / "config" / "config.yaml",
        here / "config" / "config.example.yaml",
    ):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "No config found. Copy config/config.example.yaml to config/config.yaml"
    )


def load_config(path: Path | None = None) -> Config:
    cfg_path = _find_config(path)
    with cfg_path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    gpio = raw["gpio"]
    thr = raw["throttle"]
    drive = raw.get("drive", {})
    i2c = raw["i2c"]

    return Config(
        i2c_bus=int(i2c["bus"]),
        mcp4728_address=int(i2c["mcp4728_address"]),
        gpio=GpioMap(
            dir_fl=int(gpio["dir_fl"]),
            dir_fr=int(gpio["dir_fr"]),
            dir_rl=int(gpio["dir_rl"]),
            dir_rr=int(gpio["dir_rr"]),
            estop_en=int(gpio["estop_en"]),
            status_led=int(gpio["status_led"]),
        ),
        dac_vdd_volts=float(raw["dac_vdd_volts"]),
        throttle=ThrottleCal(
            v_idle=float(thr["v_idle"]),
            v_start=float(thr["v_start"]),
            v_max=float(thr["v_max"]),
        ),
        direction_change_delay_s=float(raw["direction_change_delay_s"]),
        max_speed=float(drive.get("max_speed", 1.0)),
        max_turn=float(drive.get("max_turn", 1.0)),
        raw=raw,
    )
