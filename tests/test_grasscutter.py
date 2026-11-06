from __future__ import annotations

import os
import unittest

# Force mock before importing controller hardware bits in tests
os.environ["GRASSCUTTER_MOCK"] = "1"

from grasscutter.config import load_config
from grasscutter.controller import CartController
from grasscutter.kinematics import speed_to_volts, tank_drive
from grasscutter.mcp4728 import MCP4728, MockBus, _pack_channel


class TestKinematics(unittest.TestCase):
    def test_straight(self) -> None:
        fl, fr, rl, rr = tank_drive(0.5, 0.0)
        self.assertAlmostEqual(fl.speed, 0.5)
        self.assertAlmostEqual(fr.speed, 0.5)
        self.assertAlmostEqual(rl.speed, 0.5)
        self.assertAlmostEqual(rr.speed, 0.5)

    def test_turn_right(self) -> None:
        fl, fr, rl, rr = tank_drive(0.5, 0.2)
        self.assertAlmostEqual(fl.speed, 0.3)
        self.assertAlmostEqual(fr.speed, 0.7)

    def test_spin(self) -> None:
        fl, fr, _, _ = tank_drive(0.0, 0.5)
        self.assertAlmostEqual(fl.speed, -0.5)
        self.assertAlmostEqual(fr.speed, 0.5)

    def test_speed_to_volts(self) -> None:
        cfg = load_config()
        self.assertAlmostEqual(speed_to_volts(0.0, cfg.throttle), cfg.throttle.v_idle)
        self.assertAlmostEqual(speed_to_volts(1.0, cfg.throttle), cfg.throttle.v_max)
        self.assertAlmostEqual(speed_to_volts(0.0001, cfg.throttle), cfg.throttle.v_start, places=3)


class TestMCP4728(unittest.TestCase):
    def test_pack_and_write(self) -> None:
        bus = MockBus()
        dac = MCP4728(bus, 0x60, vdd=5.0)
        self.assertEqual(bus.byte_writes, [(0x60, 0x80)])  # VREF=VDD on init
        dac.set_volts(0, 2.5)
        self.assertEqual(len(bus.writes), 1)
        code = dac.volts_to_code(2.5)
        self.assertEqual(code, 2048)  # 2.5/5 * 4095 ≈ 2047.5 → 2048
        msb, lsb = _pack_channel(code)
        addr, cmd, data = bus.writes[0]
        self.assertEqual(addr, 0x60)
        self.assertEqual(cmd, 0x40)  # channel A single write
        self.assertEqual(data, [msb, lsb])

    def test_zero_all(self) -> None:
        bus = MockBus()
        dac = MCP4728(bus, 0x60)
        dac.set_all_volts([1, 2, 3, 4])
        dac.zero_all()
        self.assertEqual(dac.codes, (0, 0, 0, 0))

    def test_vref_select_can_skip_on_init(self) -> None:
        bus = MockBus()
        MCP4728(bus, 0x60, select_vdd_ref=False)
        self.assertEqual(bus.byte_writes, [])


class TestController(unittest.TestCase):
    def test_requires_arm(self) -> None:
        with CartController(mock=True) as cart:
            with self.assertRaises(RuntimeError):
                cart.drive(0.1, 0.0)

    def test_drive_and_failsafe(self) -> None:
        with CartController(mock=True) as cart:
            cart.arm()
            self.assertTrue(cart.armed)
            cart.drive(0.2, 0.0)
            st = cart.status()
            self.assertTrue(all(s > 0 for s in st["speeds"]))
            self.assertTrue(all(v >= cart.cfg.throttle.v_start - 1e-6 for v in st["dac_volts"]))
            cart.disarm()
            self.assertFalse(cart.armed)
            self.assertEqual(cart.status()["speeds"], [0.0, 0.0, 0.0, 0.0])

    def test_reverse_interlock(self) -> None:
        with CartController(mock=True) as cart:
            cart.arm()
            cart.set_wheel(0, 0.5)
            cart.set_wheel(0, -0.5)
            st = cart.status()
            self.assertTrue(st["reverse"][0])
            self.assertLess(st["speeds"][0], 0)


if __name__ == "__main__":
    unittest.main()
