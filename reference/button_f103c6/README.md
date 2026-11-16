# button_f103c6: reference for the Button example

While the button on **PA0** is pressed, the LED on **PC13** is lit. The `button` template of
NightExpress is made from it; CI builds it together with the other references.

| Parameter | Value |
|---|---|
| HAL | `embassy-stm32 0.6.0`, features `stm32f103c6`, `memory-x` |
| Button | PA0, internal pull-up, pressed = LOW |
| LED | PC13, active low |

The minimal board (Blue Pill) has no user button, so it is an external one:

```text
PA0 ──── [ button ] ──── GND      (no resistor needed: Pull::Up inside the chip)
```

## Checking on hardware

```sh
cargo build --release
probe-rs download --chip STM32F103C6 --verify --reset target/thumbv7m-none-eabi/release/button_f103c6
```

Hold the button → PC13 is lit; release it → it goes off.

**Status:** ✔ verified on STM32F103C6T6A (15.11.2026): flashed through the UART bootloader (FT232R);
after BOOT0 = 0 and Reset, PC13 is lit while the button on PA0 is held.
The GO command right after flashing did not start the program: a Reset was needed.
