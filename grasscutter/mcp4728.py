"""MCP4728 quad 12-bit DAC over I2C (Vref = VDD → 0…VDD outs)."""

from __future__ import annotations

import logging
from typing import Protocol

log = logging.getLogger(__name__)

# Single-write: C2..C0=010, W1W0=00, DAC1 DAC0, UDAC=0
_CMD_SINGLE = 0x40
# Sequential write starting at channel A
_CMD_SEQ = 0x50
# Write VREF select bits for all channels (C2 C1 C0 = 100): VREF=0 → VDD
_CMD_VREF_SELECT = 0x80

CHANNEL_A = 0
CHANNEL_B = 1
CHANNEL_C = 2
CHANNEL_D = 3


class Bus(Protocol):
    def write_byte(self, addr: int, value: int) -> None: ...

    def write_i2c_block_data(self, addr: int, cmd: int, data: list[int]) -> None: ...


class MockBus:
    def __init__(self) -> None:
        self.writes: list[tuple[int, int, list[int]]] = []
        self.byte_writes: list[tuple[int, int]] = []

    def write_byte(self, addr: int, value: int) -> None:
        self.byte_writes.append((addr, value))
        log.debug("MockBus write_byte addr=0x%02x value=0x%02x", addr, value)

    def write_i2c_block_data(self, addr: int, cmd: int, data: list[int]) -> None:
        self.writes.append((addr, cmd, list(data)))
        log.debug("MockBus write addr=0x%02x cmd=0x%02x data=%s", addr, cmd, data)


def open_smbus(bus_id: int) -> Bus:
    from smbus2 import SMBus

    return SMBus(bus_id)


def _pack_channel(code: int, *, vref_vdd: bool = True) -> tuple[int, int]:
    """Pack 12-bit code for MCP4728 data bytes (Adafruit-compatible)."""
    code = max(0, min(4095, int(code)))
    vref = 0 if vref_vdd else 1  # 0 = VDD, 1 = internal 2.048 V
    pd = 0
    gain = 0
    msb = (vref << 7) | (pd << 5) | (gain << 4) | ((code >> 8) & 0x0F)
    lsb = code & 0xFF
    return msb, lsb


class MCP4728:
    """Four channels, codes 0..4095. Vout ≈ code/4095 * vdd when Vref=VDD."""

    def __init__(
        self,
        bus: Bus,
        address: int = 0x60,
        vdd: float = 5.0,
        *,
        select_vdd_ref: bool = True,
    ) -> None:
        self._bus = bus
        self.address = address
        self.vdd = vdd
        self._codes = [0, 0, 0, 0]
        # Factory EEPROM defaults to internal 2.048 V VREF; force VDD for 0…VDD span.
        if select_vdd_ref:
            self.select_vref_vdd()

    @property
    def codes(self) -> tuple[int, int, int, int]:
        return (self._codes[0], self._codes[1], self._codes[2], self._codes[3])

    def select_vref_vdd(self) -> None:
        """Select VREF=VDD for channels A–D (DS22187 Fig. 5-12: C2=1,C1=0,C0=0)."""
        # One data byte after address: 1 0 0 X | VREF_A VREF_B VREF_C VREF_D; 0 = VDD
        self._bus.write_byte(self.address, _CMD_VREF_SELECT | 0x00)
        log.debug("MCP4728 VREF set to VDD for all channels")

    def volts_to_code(self, volts: float) -> int:
        volts = max(0.0, min(self.vdd, float(volts)))
        return int(round((volts / self.vdd) * 4095))

    def code_to_volts(self, code: int) -> float:
        return (int(code) / 4095.0) * self.vdd

    def set_code(self, channel: int, code: int) -> None:
        if channel not in (0, 1, 2, 3):
            raise ValueError(f"channel must be 0..3, got {channel}")
        code = max(0, min(4095, int(code)))
        self._codes[channel] = code
        msb, lsb = _pack_channel(code)
        cmd = _CMD_SINGLE | ((channel & 0x03) << 1)
        self._bus.write_i2c_block_data(self.address, cmd, [msb, lsb])

    def set_volts(self, channel: int, volts: float) -> None:
        self.set_code(channel, self.volts_to_code(volts))

    def set_all_codes(self, codes: tuple[int, int, int, int] | list[int]) -> None:
        if len(codes) != 4:
            raise ValueError("need 4 codes")
        self._codes = [max(0, min(4095, int(c))) for c in codes]
        payload: list[int] = []
        for code in self._codes:
            msb, lsb = _pack_channel(code)
            payload.extend([msb, lsb])
        self._bus.write_i2c_block_data(self.address, _CMD_SEQ, payload)

    def set_all_volts(self, volts: tuple[float, float, float, float] | list[float]) -> None:
        self.set_all_codes([self.volts_to_code(v) for v in volts])

    def zero_all(self) -> None:
        self.set_all_codes([0, 0, 0, 0])
