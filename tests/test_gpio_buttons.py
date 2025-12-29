from __future__ import annotations

import unittest


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += float(dt)


class RepeatGateTests(unittest.TestCase):
    def test_repeat_gate_allows_first_press(self) -> None:
        from lcarstv.input.gpio_buttons import RepeatGate

        clk = FakeClock()
        gate = RepeatGate(min_interval_sec=0.15, time_fn=clk.now)
        self.assertTrue(gate.allow())

    def test_repeat_gate_blocks_until_interval(self) -> None:
        from lcarstv.input.gpio_buttons import RepeatGate

        clk = FakeClock()
        gate = RepeatGate(min_interval_sec=0.15, time_fn=clk.now)

        self.assertTrue(gate.allow())
        self.assertFalse(gate.allow())  # same time

        clk.advance(0.149)
        self.assertFalse(gate.allow())

        clk.advance(0.001)
        self.assertTrue(gate.allow())


if __name__ == "__main__":
    unittest.main()

