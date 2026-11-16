//! Blink for STM32F103C6 (board `stm32f103c6-minimal`).
//!
//! The LED on PC13 is wired to 3.3V through a resistor, so it lights up when
//! the pin is driven LOW (active low). If your board has the LED on another
//! pin, change `p.PC13` below.

#![no_std]
#![no_main]

use cortex_m_rt::entry;
use embassy_stm32::gpio::{Level, Output, Speed};
use panic_halt as _; // on panic! the core halts in an infinite loop

#[entry]
fn main() -> ! {
    // Default clocks: internal HSI oscillator, 8 MHz.
    let p = embassy_stm32::init(Default::default());

    // Level::High = LED off (active low).
    let mut led = Output::new(p.PC13, Level::High, Speed::Low);

    loop {
        led.toggle();
        // At least 4,000,000 cycles ~ 0.5 s at 8 MHz -> blinks once per second.
        cortex_m::asm::delay(4_000_000);
    }
}
