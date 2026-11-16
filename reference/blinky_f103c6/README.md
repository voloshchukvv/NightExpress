# blinky_f103c6: reference project

A hand-verified Cargo project for **STM32F103C6T6A** (32 KB flash, 10 KB RAM, Cortex-M3).
The `blink` template of NightExpress is made from it, and CI uses it to check that the dependencies still build.

| Parameter | Value |
|---|---|
| HAL | `embassy-stm32 0.6.0`, features `stm32f103c6`, `memory-x` |
| Target | `thumbv7m-none-eabi` |
| LED | PC13, active low |
| Size (release) | ~1.1 KB flash |
| Flashing | **ST-Link + probe-rs (verified 14.11.2026)** or the UART bootloader through USB-TTL |

## 1. Build

```sh
cargo build --release
```

On the first build rustup installs the stable toolchain, the `thumbv7m-none-eabi` target and `llvm-tools` by itself (see `rust-toolchain.toml`).

## 2a. Flashing through an ST-Link (the main way, verified)

```text
ST-Link V2       Board (SWD header)
SWDIO ────────── DIO  (PA13)
SWCLK ────────── CLK  (PA14)
GND   ────────── GND
3.3V  ────────── 3.3V
```

BOOT0 = 0. Power the board from one source only.

```sh
brew install probe-rs-tools        # once (or cargo install probe-rs-tools)
probe-rs list                      # should show STLink V2
probe-rs read --chip STM32F103C6 b32 0xE0042000 1     # 10006412 → F103 low-density rev A
probe-rs download --chip STM32F103C6 --verify target/thumbv7m-none-eabi/release/blinky_f103c6
probe-rs reset --chip STM32F103C6
```

`probe-rs info` does not detect an F1 chip by itself (and ignores `--chip`), so use `read` to check it, as shown above.

## 2b. ELF → BIN (only needed for UART)

```sh
"$(echo "$(rustc --print sysroot)"/lib/rustlib/*/bin/llvm-objcopy)" \
  -O binary target/thumbv7m-none-eabi/release/blinky_f103c6 blinky.bin
```

The file should be ~1 KB. The first 4 bytes `00 28 00 20` are the initial stack `0x20002800` (the end of 10 KB of RAM).

## 3. Connecting a USB-TTL adapter

The adapter logic must be **3.3 V**.

```text
USB-TTL                 STM32F103
TX  ──────────────────► PA10
RX  ◄────────────────── PA9
GND ─────────────────── GND
3.3V ────────────────── 3.3V   (or power the board from USB, then leave 3.3V unconnected)
```

## 4. Flashing through the UART bootloader (not yet verified on hardware)

`stm32loader` is used only as a developer tool for this stage. Its license is GPL-3.0, so it is not built into NightExpress.

```sh
# once, in the venv of the NightExpress project
../../.venv/bin/pip install stm32loader

# 1) jumper BOOT0 = 1 (BOOT1 = 0), press Reset
# 2) check the connection: it should show the bootloader version and chip id 0x412 (F1 low-density)
../../.venv/bin/stm32loader -p /dev/cu.usbserial-XXXX -f F1

# 3) erase, write, verify and start
../../.venv/bin/stm32loader -p /dev/cu.usbserial-XXXX -f F1 -e -w -v -g 0x08000000 blinky.bin

# 4) set BOOT0 = 0 again and press Reset: the program then starts by itself after every reset
```

The port name can be found with `ls /dev/cu.usbserial* /dev/cu.wchusbserial*`. If there are several adapters, make sure you picked the one connected to the STM32: the bootloader sends bytes to the port that may disturb another device. On the developer's machine the STM32 is on `/dev/cu.usbserial-120` (PL2303), while `/dev/cu.usbserial-A50285BI` (FT232R) is connected to another device. On Windows the ports are `COM3`, `COM4` and so on, on Linux `/dev/ttyUSB0`.

## If it does not work

| Symptom | What to check |
|---|---|
| `Can't init into bootloader` / timeout | BOOT0 = 1? Reset pressed? TX/RX not swapped? Common GND? |
| The port does not appear | The CH340 driver (macOS/Windows); on Linux `brltty` and the `dialout` group |
| Flashed, but the LED does not blink | BOOT0 back to 0? Is the LED really on PC13? Some boards have the LED on PB12 or PA1 |
| Chip id is not `0x412` | It is not an F103 low-density or it is a clone (CKS32/APM32/GD32): report it and we add it to the catalog |
