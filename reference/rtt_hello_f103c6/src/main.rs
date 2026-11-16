//! RTT Hello for STM32F103C6 (board `stm32f103c6-minimal`).
//!
//! The program logs through the debug probe: flash it with an ST-Link and its messages
//! appear in the "RTT" tab, without a UART adapter. The LED on PC13 blinks too.

#![no_std]
#![no_main]

use cortex_m_rt::entry;
use defmt::{info, warn};
use defmt_rtt as _; // the messages go to the debug probe (RTT)
use embassy_stm32::gpio::{Level, Output, Speed};
use panic_probe as _; // a panic is printed in the "RTT" tab, then the core halts

#[entry]
fn main() -> ! {
    // Default clocks: internal oscillator, 8 MHz.
    let p = embassy_stm32::init(Default::default());
    // Level::High = LED off (active low).
    let mut led = Output::new(p.PC13, Level::High, Speed::Low);

    info!("Hello from STM32F103C6!");
    let mut counter: u32 = 0;
    loop {
        led.toggle();
        if counter % 10 == 9 {
            warn!("tick {} (every tenth message is a warning)", counter);
        } else {
            info!("tick {}", counter);
        }
        counter = counter.wrapping_add(1);
        // At least 4,000,000 cycles ~ 0.5 s at 8 MHz.
        cortex_m::asm::delay(4_000_000);
    }
}
