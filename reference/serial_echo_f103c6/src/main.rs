//! UART check for STM32F103C6: USART1 (TX = PA9, RX = PA10), 115200 8N1.
//!
//! Every second it prints "Hello from STM32 #N" and echoes back every byte it receives,
//! so both directions of a USB-TTL connection can be tested. The LED on PC13 blinks too.

#![no_std]
#![no_main]

use cortex_m_rt::entry;
use embassy_stm32::gpio::{Level, Output, Speed};
use embassy_stm32::usart::{Config, Uart};
use embedded_hal_nb::nb;
use embedded_hal_nb::serial::{Read as _, Write as _};
use panic_halt as _;

const TX_BUFFER: usize = 128;

/// Bytes waiting for the transmitter. The greeting and the echo share this one queue, so
/// nothing is ever sent with a blocking call.
struct TxQueue {
    data: [u8; TX_BUFFER],
    head: usize,
    len: usize,
}

impl TxQueue {
    const fn new() -> Self {
        Self { data: [0; TX_BUFFER], head: 0, len: 0 }
    }

    /// Adds a byte to the end; when the queue is full the byte is dropped.
    fn push(&mut self, byte: u8) {
        if self.len < TX_BUFFER {
            self.data[(self.head + self.len) % TX_BUFFER] = byte;
            self.len += 1;
        }
    }

    fn push_all(&mut self, bytes: &[u8]) {
        for &byte in bytes {
            self.push(byte);
        }
    }

    /// Adds a number in decimal without any formatting machinery (keeps the binary small).
    fn push_number(&mut self, mut value: u32) {
        let mut digits = [0u8; 10];
        let mut len = 0;
        loop {
            digits[len] = b'0' + (value % 10) as u8;
            len += 1;
            value /= 10;
            if value == 0 {
                break;
            }
        }
        digits[..len].reverse();
        self.push_all(&digits[..len]);
    }

    /// The next byte to send, if any; it stays in the queue until `pop`.
    fn peek(&self) -> Option<u8> {
        (self.len > 0).then(|| self.data[self.head])
    }

    fn pop(&mut self) {
        if self.len > 0 {
            self.head = (self.head + 1) % TX_BUFFER;
            self.len -= 1;
        }
    }
}

#[entry]
fn main() -> ! {
    let p = embassy_stm32::init(Default::default()); // HSI, 8 MHz
    let mut led = Output::new(p.PC13, Level::High, Speed::Low);
    let mut uart = Uart::new_blocking(p.USART1, p.PA10, p.PA9, Config::default()).unwrap();

    let mut counter: u32 = 0;
    let mut tx = TxQueue::new();
    loop {
        led.toggle();
        tx.push_all(b"Hello from STM32 #");
        tx.push_number(counter);
        tx.push_all(b"\r\n");
        counter = counter.wrapping_add(1);

        // ~1 s of 20 us slices. The USART has a single receive register (no FIFO) and at
        // 115200 baud a byte arrives every ~87 us. Receiving must never wait for sending, so
        // every slice first takes what has arrived and then hands the transmitter at most one
        // byte, and only if it is free. The greeting goes out the same way, byte by byte.
        for _ in 0..50_000 {
            loop {
                match uart.read() {
                    Ok(byte) => tx.push(byte), // echo it back
                    Err(nb::Error::WouldBlock) => break,
                    Err(nb::Error::Other(_)) => {} // overrun/noise: the flag is cleared by reading
                }
            }
            if let Some(byte) = tx.peek() {
                if uart.write(byte).is_ok() {
                    tx.pop();
                }
            }
            cortex_m::asm::delay(160); // 20 us at 8 MHz
        }
    }
}
