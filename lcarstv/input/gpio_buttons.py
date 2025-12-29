from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class RepeatGate:
    """Time-based gate to prevent hold/repeat spam.

    This is intentionally pure-Python so it can be unit tested without GPIO libs.
    """

    min_interval_sec: float = 0.15
    time_fn: Callable[[], float] = time.monotonic
    _last_allowed: float | None = None

    def allow(self) -> bool:
        now = float(self.time_fn())
        if self._last_allowed is None:
            self._last_allowed = now
            return True
        if (now - self._last_allowed) >= float(self.min_interval_sec):
            self._last_allowed = now
            return True
        return False


class GpioButtons:
    """Raspberry Pi GPIO button input.

    - Prefers gpiozero if available.
    - Falls back to RPi.GPIO.
    - Uses edge callbacks (no polling/busy loops).

    Wiring expectation (default): button shorts GPIO pin -> GND, with internal pull-ups.
    """

    def __init__(
        self,
        *,
        on_up: Callable[[], None],
        on_down: Callable[[], None],
        on_quit: Callable[[], None] | None = None,
        btn_up_pin: int,
        btn_down_pin: int,
        btn_quit_pin: int | None = None,
        pull_up: bool = True,
        bounce_sec: float = 0.05,
        repeat_guard_sec: float = 0.15,
    ) -> None:
        self._on_up = on_up
        self._on_down = on_down
        self._on_quit = on_quit

        self._pull_up = bool(pull_up)
        self._bounce_sec = float(bounce_sec)
        self._repeat_guard_sec = float(repeat_guard_sec)

        self._pins: list[int] = [int(btn_up_pin), int(btn_down_pin)]
        if btn_quit_pin is not None:
            self._pins.append(int(btn_quit_pin))

        self._backend: str | None = None
        self._gpiozero_buttons: list[object] | None = None
        self._rpi_gpio = None

        # Per-button repeat gates.
        self._gate_up = RepeatGate(min_interval_sec=self._repeat_guard_sec)
        self._gate_down = RepeatGate(min_interval_sec=self._repeat_guard_sec)
        self._gate_quit = RepeatGate(min_interval_sec=self._repeat_guard_sec)

        # Backend select.
        if self._try_init_gpiozero(btn_up_pin, btn_down_pin, btn_quit_pin):
            return
        if self._try_init_rpi_gpio(btn_up_pin, btn_down_pin, btn_quit_pin):
            return
        raise RuntimeError(
            "GPIO requested but neither gpiozero nor RPi.GPIO is available. "
            "Install one of them (recommended: gpiozero)."
        )

    def _try_init_gpiozero(self, btn_up_pin: int, btn_down_pin: int, btn_quit_pin: int | None) -> bool:
        try:
            from gpiozero import Button  # type: ignore
        except Exception:
            return False

        def _wrap(cb: Callable[[], None], gate: RepeatGate) -> Callable[[], None]:
            def _inner() -> None:
                if gate.allow():
                    cb()

            return _inner

        # gpiozero: pull_up=True means active_low=True (pressed connects to GND).
        up = Button(
            int(btn_up_pin),
            pull_up=self._pull_up,
            bounce_time=self._bounce_sec,
        )
        down = Button(
            int(btn_down_pin),
            pull_up=self._pull_up,
            bounce_time=self._bounce_sec,
        )

        up.when_pressed = _wrap(self._on_up, self._gate_up)
        down.when_pressed = _wrap(self._on_down, self._gate_down)

        btns: list[object] = [up, down]
        if btn_quit_pin is not None and self._on_quit is not None:
            quit_btn = Button(
                int(btn_quit_pin),
                pull_up=self._pull_up,
                bounce_time=self._bounce_sec,
            )
            quit_btn.when_pressed = _wrap(self._on_quit, self._gate_quit)
            btns.append(quit_btn)

        self._backend = "gpiozero"
        self._gpiozero_buttons = btns
        return True

    def _try_init_rpi_gpio(self, btn_up_pin: int, btn_down_pin: int, btn_quit_pin: int | None) -> bool:
        try:
            import RPi.GPIO as GPIO  # type: ignore
        except Exception:
            return False

        GPIO.setmode(GPIO.BCM)

        pud = GPIO.PUD_UP if self._pull_up else GPIO.PUD_DOWN

        # Buttons are normally-open; pressed shorts to the opposite rail.
        for pin in (btn_up_pin, btn_down_pin):
            GPIO.setup(int(pin), GPIO.IN, pull_up_down=pud)
        if btn_quit_pin is not None and self._on_quit is not None:
            GPIO.setup(int(btn_quit_pin), GPIO.IN, pull_up_down=pud)

        bouncetime_ms = max(0, int(self._bounce_sec * 1000.0))

        edge = GPIO.FALLING if self._pull_up else GPIO.RISING

        def _cb_up(_channel: int) -> None:
            if self._gate_up.allow():
                self._on_up()

        def _cb_down(_channel: int) -> None:
            if self._gate_down.allow():
                self._on_down()

        def _cb_quit(_channel: int) -> None:
            if self._on_quit is None:
                return
            if self._gate_quit.allow():
                self._on_quit()

        GPIO.add_event_detect(int(btn_up_pin), edge, callback=_cb_up, bouncetime=bouncetime_ms)
        GPIO.add_event_detect(int(btn_down_pin), edge, callback=_cb_down, bouncetime=bouncetime_ms)
        if btn_quit_pin is not None and self._on_quit is not None:
            GPIO.add_event_detect(int(btn_quit_pin), edge, callback=_cb_quit, bouncetime=bouncetime_ms)

        self._backend = "RPi.GPIO"
        self._rpi_gpio = GPIO
        return True

    def close(self) -> None:
        """Release GPIO resources. Safe to call multiple times."""

        if self._backend == "gpiozero" and self._gpiozero_buttons is not None:
            for b in self._gpiozero_buttons:
                try:
                    # gpiozero Button has .close()
                    b.close()  # type: ignore[attr-defined]
                except Exception:
                    pass
            self._gpiozero_buttons = None
            self._backend = None
            return

        if self._backend == "RPi.GPIO" and self._rpi_gpio is not None:
            GPIO = self._rpi_gpio
            for p in self._pins:
                try:
                    GPIO.remove_event_detect(int(p))
                except Exception:
                    pass
            try:
                GPIO.cleanup([int(p) for p in self._pins])
            except Exception:
                pass
            self._rpi_gpio = None
            self._backend = None

