#![no_std]
#![no_main]

use cortex_m_rt::entry;
use embassy_stm32 as _; // Supplies the linker memory layout for the selected chip.
use panic_halt as _;

#[entry]
fn main() -> ! {
    loop {
        cortex_m::asm::wfi();
    }
}
