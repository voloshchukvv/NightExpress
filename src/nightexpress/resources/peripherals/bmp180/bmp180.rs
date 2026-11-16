//! BMP180 barometer and thermometer over I2C. Added to the project by NightExpress
//! ("Add peripheral"); it is yours to change (0BSD).
//!
//! Works with any `embedded_hal::i2c::I2c`. Integer maths from the Bosch datasheet, so no
//! floating point is needed. No delay is needed either: the chip says when a measurement is
//! done (the SCO bit), and the driver waits for that.

#![allow(dead_code)] // not every function is used by every program

use embedded_hal::i2c::I2c;

/// The address of every BMP180 (it cannot be changed).
pub const ADDRESS: u8 = 0x77;
/// What the BMP180 (and the BMP085 before it) answers in CHIP_ID.
pub const CHIP_ID: u8 = 0x55;

const REG_CALIBRATION: u8 = 0xAA; // 11 words, AC1..MD
const REG_CHIP_ID: u8 = 0xD0;
const REG_CTRL_MEAS: u8 = 0xF4;
const REG_OUT_MSB: u8 = 0xF6;
const MEASURE_TEMPERATURE: u8 = 0x2E;
const MEASURE_PRESSURE: u8 = 0x34;
const SCO: u8 = 1 << 5; // set while a measurement runs
/// How many times to look before giving up (a measurement takes 4.5 to 25.5 ms).
const READY_TRIES: u32 = 10_000;

/// How many samples one pressure reading averages: more is less noise, but slower.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Oversampling {
    /// 1 sample, 4.5 ms
    UltraLowPower = 0,
    /// 2 samples, 7.5 ms
    Standard = 1,
    /// 4 samples, 13.5 ms
    HighResolution = 2,
    /// 8 samples, 25.5 ms
    UltraHighResolution = 3,
}

#[derive(Debug)]
pub enum Error<E> {
    /// The bus failed: nothing answers at 0x77 (wiring, power) or a bus error.
    Bus(E),
    /// Something else answered: CHIP_ID returned this value.
    UnknownDevice(u8),
    /// A measurement did not finish in time.
    NotReady,
}

/// One reading: temperature in tenths of a degree Celsius, pressure in pascals
/// (101 325 Pa = 1013.25 hPa at sea level).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Reading {
    pub temperature_deci_c: i32,
    pub pressure_pa: i32,
}

/// The factory calibration stored in each chip.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Calibration {
    pub ac1: i16,
    pub ac2: i16,
    pub ac3: i16,
    pub ac4: u16,
    pub ac5: u16,
    pub ac6: u16,
    pub b1: i16,
    pub b2: i16,
    pub mb: i16,
    pub mc: i16,
    pub md: i16,
}

pub struct Bmp180<I> {
    i2c: I,
    oversampling: Oversampling,
    calibration: Calibration,
}

impl<I: I2c> Bmp180<I> {
    pub fn new(i2c: I) -> Self {
        Self { i2c, oversampling: Oversampling::Standard, calibration: Calibration::default() }
    }

    pub fn set_oversampling(&mut self, oversampling: Oversampling) {
        self.oversampling = oversampling;
    }

    /// Check that the chip is there and read its calibration. Returns CHIP_ID.
    pub fn init(&mut self) -> Result<u8, Error<I::Error>> {
        let mut id = [0u8];
        self.read_registers(REG_CHIP_ID, &mut id)?;
        if id[0] != CHIP_ID {
            return Err(Error::UnknownDevice(id[0]));
        }
        let mut raw = [0u8; 22];
        self.read_registers(REG_CALIBRATION, &mut raw)?;
        let word = |i: usize| [raw[2 * i], raw[2 * i + 1]];
        let signed = |i: usize| i16::from_be_bytes(word(i));
        let unsigned = |i: usize| u16::from_be_bytes(word(i));
        self.calibration = Calibration {
            ac1: signed(0),
            ac2: signed(1),
            ac3: signed(2),
            ac4: unsigned(3),
            ac5: unsigned(4),
            ac6: unsigned(5),
            b1: signed(6),
            b2: signed(7),
            mb: signed(8),
            mc: signed(9),
            md: signed(10),
        };
        Ok(id[0])
    }

    /// Measure the temperature, then the pressure (which needs the temperature).
    pub fn read(&mut self) -> Result<Reading, Error<I::Error>> {
        let mut raw = [0u8; 3];
        self.measure(MEASURE_TEMPERATURE, &mut raw[..2])?;
        let ut = i32::from(u16::from_be_bytes([raw[0], raw[1]]));
        let oss = self.oversampling as u8;
        self.measure(MEASURE_PRESSURE + (oss << 6), &mut raw)?;
        let up = ((i32::from(raw[0]) << 16) | (i32::from(raw[1]) << 8) | i32::from(raw[2]))
            >> (8 - oss);
        Ok(compensate(&self.calibration, ut, up, oss))
    }

    pub fn calibration(&self) -> Calibration {
        self.calibration
    }

    /// Give the bus back.
    pub fn release(self) -> I {
        self.i2c
    }

    fn measure(&mut self, command: u8, out: &mut [u8]) -> Result<(), Error<I::Error>> {
        self.i2c.write(ADDRESS, &[REG_CTRL_MEAS, command]).map_err(Error::Bus)?;
        let mut ready = false;
        for _ in 0..READY_TRIES {
            let mut ctrl = [0u8];
            self.read_registers(REG_CTRL_MEAS, &mut ctrl)?;
            if ctrl[0] & SCO == 0 {
                ready = true;
                break;
            }
        }
        if !ready {
            return Err(Error::NotReady);
        }
        self.read_registers(REG_OUT_MSB, out)
    }

    fn read_registers(&mut self, register: u8, out: &mut [u8]) -> Result<(), Error<I::Error>> {
        self.i2c.write_read(ADDRESS, &[register], out).map_err(Error::Bus)
    }
}

/// The datasheet's calculation: raw temperature `ut` and pressure `up` (already shifted by
/// `8 - oss`) to tenths of a degree and pascals.
pub fn compensate(c: &Calibration, ut: i32, up: i32, oss: u8) -> Reading {
    let x1 = ((ut - i32::from(c.ac6)) * i32::from(c.ac5)) >> 15;
    let x2 = (i32::from(c.mc) << 11) / (x1 + i32::from(c.md));
    let b5 = x1 + x2;
    let temperature_deci_c = (b5 + 8) >> 4;

    let b6 = b5 - 4000;
    let x1 = (i32::from(c.b2) * ((b6 * b6) >> 12)) >> 11;
    let x2 = (i32::from(c.ac2) * b6) >> 11;
    let x3 = x1 + x2;
    let b3 = (((i32::from(c.ac1) * 4 + x3) << oss) + 2) / 4;
    let x1 = (i32::from(c.ac3) * b6) >> 13;
    let x2 = (i32::from(c.b1) * ((b6 * b6) >> 12)) >> 16;
    let x3 = (x1 + x2 + 2) >> 2;
    let b4 = (u32::from(c.ac4) * ((x3 + 32768) as u32)) >> 15;
    let b7 = ((up - b3) as u32).wrapping_mul(50000 >> oss);
    let p = if b7 < 0x8000_0000 { (b7 * 2) / b4 } else { (b7 / b4) * 2 } as i32;
    let x1 = ((p >> 8) * (p >> 8) * 3038) >> 16;
    let x2 = (-7357 * p) >> 16;
    Reading { temperature_deci_c, pressure_pa: p + ((x1 + x2 + 3791) >> 4) }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn datasheet_example() {
        // The worked example of the BMP180 datasheet (section 3.5): 15.0 °C, 69 964 Pa.
        let c = Calibration {
            ac1: 408,
            ac2: -72,
            ac3: -14383,
            ac4: 32741,
            ac5: 32757,
            ac6: 23153,
            b1: 6190,
            b2: 4,
            mb: -32768,
            mc: -8711,
            md: 2868,
        };
        assert_eq!(
            compensate(&c, 27898, 23843, 0),
            Reading { temperature_deci_c: 150, pressure_pa: 69964 }
        );
    }
}
