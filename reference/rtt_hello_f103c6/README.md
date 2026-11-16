# rtt_hello_f103c6

The **RTT Hello** example logs a message every half second through the debug probe and
blinks the LED on `PC13`. The messages are read by the ST-Link over SWD (RTT) and
decoded on the computer by [defmt](https://defmt.ferrous-systems.com): no UART adapter is
needed.

## Target board

**STM32F103C6 (Blue Pill)** uses the `STM32F103C6` microcontroller with
32 KB of flash and 10 KB of RAM. The Rust target is `thumbv7m-none-eabi`.

## Build and see the output

Flash it through an ST-Link: the messages appear in the "RTT" tab. From the command line:

```sh
cargo build --release
probe-rs run --chip STM32F103C6 target/thumbv7m-none-eabi/release/rtt_hello_f103c6
```

The firmware ELF is written to `target/thumbv7m-none-eabi/release/rtt_hello_f103c6`.

Change `DEFMT_LOG` in `.cargo/config.toml` to see more (`debug`, `trace`) or fewer messages.
