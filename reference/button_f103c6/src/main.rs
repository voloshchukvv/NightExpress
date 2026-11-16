//! Button for STM32F103C6 (board `stm32f103c6-minimal`).
//!
//! The LED on PC13 is lit while the button on PA0 is held down.
//! This board has no user button: connect one between PA0 and GND.
//! The internal pull-up keeps the pin HIGH while the button is released;
//! a press connects it to GND, so "pressed" reads as LOW.

#![no_std]
#![no_main]

use cortex_m_rt::entry;
use embassy_stm32::gpio::{Input, Level, Output, Pull, Speed};
use panic_halt as _; // on panic! the core halts in an infinite loop

#[entry]
fn main() -> ! {
    // Default clocks: internal HSI oscillator, 8 MHz.
    let p = embassy_stm32::init(Default::default());

    let button = Input::new(p.PA0, Pull::Up);
    // Level::High = LED off (active low).
    let mut led = Output::new(p.PC13, Level::High, Speed::Low);

    loop {
        let pressed = button.is_low();
        // Active-low LED: LOW lights it up.
        led.set_level(if pressed { Level::Low } else { Level::High });
    }
}
