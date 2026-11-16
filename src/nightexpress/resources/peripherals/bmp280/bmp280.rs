//! BMP280 barometer and thermometer over I2C (also the BME280, without its humidity). Added to
//! the project by NightExpress ("Add peripheral"); it is yours to change (0BSD).
//!
//! Works with any `embedded_hal::i2c::I2c`. Integer maths from the Bosch datasheet, so no
//! floating point is needed. The chip measures on its own (normal mode); a reading only
//! fetches the latest result.

#![allow(dead_code)] // not every function is used by every program

use embedded_hal::i2c::I2c;

/// SDO at GND (most HW-611 / GY-BMP280 modules).
pub const ADDRESS: u8 = 0x76;
/// SDO at 3.3 V.
pub const ADDRESS_SDO_HIGH: u8 = 0x77;

/// CHIP_ID values: BMP280 (and its engineering samples), BME280.
const KNOWN_IDS: [u8; 4] = [0x58, 0x56, 0x57, 0x60];

const REG_CALIBRATION: u8 = 0x88; // 24 bytes, dig_T1..dig_P9, little-endian
const REG_CHIP_ID: u8 = 0xD0;
const REG_RESET: u8 = 0xE0;
const REG_STATUS: u8 = 0xF3;
const IM_UPDATE: u8 = 1 << 0; // set while the chip copies its calibration after a reset
const REG_CTRL_MEAS: u8 = 0xF4;
const REG_CONFIG: u8 = 0xF5;
const REG_DATA: u8 = 0xF7; // pressure (3 bytes), then temperature (3 bytes)
const RESET: u8 = 0xB6;
/// Temperature ×2, pressure ×16, normal mode: Bosch's "indoor navigation" setting.
const CTRL_MEAS: u8 = (0b010 << 5) | (0b101 << 2) | 0b11;
/// 0.5 ms between measurements, IIR filter ×16: a steady value, about 26 readings a second.
const CONFIG: u8 = 0b100 << 2;
/// How many times to look before giving up (the copy takes about 2 ms).
const READY_TRIES: u32 = 10_000;
/// What the data registers hold before the first measurement.
const NOT_MEASURED: i32 = 0x80000;

#[derive(Debug)]
pub enum Error<E> {
    /// The bus failed: nothing answers at 0x76 or 0x77 (wiring, power) or a bus error.
    Bus(E),
    /// Something else answered: CHIP_ID returned this value.
    UnknownDevice(u8),
    /// No measurement yet (right after `init`), or the chip did not get ready after its reset.
    NotReady,
}

/// One reading: temperature in hundredths of a degree Celsius, pressure in pascals
/// (101 325 Pa = 1013.25 hPa at sea level).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Reading {
    pub temperature_centi_c: i32,
    pub pressure_pa: i32,
}

/// The factory calibration stored in each chip.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Calibration {
    pub t1: u16,
    pub t2: i16,
    pub t3: i16,
    pub p1: u16,
    pub p2: i16,
    pub p3: i16,
    pub p4: i16,
    pub p5: i16,
    pub p6: i16,
    pub p7: i16,
    pub p8: i16,
    pub p9: i16,
}

pub struct Bmp280<I> {
    i2c: I,
    address: u8,
    calibration: Calibration,
}

impl<I: I2c> Bmp280<I> {
    pub fn new(i2c: I) -> Self {
        Self { i2c, address: ADDRESS, calibration: Calibration::default() }
    }

    /// Find the chip (0x76, then 0x77), read its calibration and start measuring.
    /// Returns CHIP_ID.
    pub fn init(&mut self) -> Result<u8, Error<I::Error>> {
        let mut found = None;
        for address in [ADDRESS, ADDRESS_SDO_HIGH] {
            self.address = address;
            match self.read_registers(REG_CHIP_ID, 1) {
                Ok(id) => {
                    found = Some(id[0]);
                    if KNOWN_IDS.contains(&id[0]) {
                        break;
                    }
                }
                Err(error) if address == ADDRESS_SDO_HIGH && found.is_none() => return Err(error),
                Err(_) => {}
            }
        }
        let id = found.unwrap_or_default();
        if !KNOWN_IDS.contains(&id) {
            return Err(Error::UnknownDevice(id));
        }
        self.write_register(REG_RESET, RESET)?;
        // After the reset the chip restarts and copies its calibration from NVM (~2 ms): read
        // too early, the first words come back as zeros (seen on a HW-611). So wait for
        // IM_UPDATE to clear, and read again while dig_T1, never 0 in a real chip, is 0.
        let mut raw = None;
        for _ in 0..READY_TRIES {
            let ready = matches!(self.read_registers(REG_STATUS, 1), Ok(s) if s[0] & IM_UPDATE == 0);
            if !ready {
                continue; // also while the restarting chip does not answer
            }
            let data = self.read_registers(REG_CALIBRATION, 24)?;
            if data[0] != 0 || data[1] != 0 {
                raw = Some(data);
                break;
            }
        }
        let raw = raw.ok_or(Error::NotReady)?;
        let unsigned = |i: usize| u16::from_le_bytes([raw[2 * i], raw[2 * i + 1]]);
        let signed = |i: usize| i16::from_le_bytes([raw[2 * i], raw[2 * i + 1]]);
        self.calibration = Calibration {
            t1: unsigned(0),
            t2: signed(1),
            t3: signed(2),
            p1: unsigned(3),
            p2: signed(4),
            p3: signed(5),
            p4: signed(6),
            p5: signed(7),
            p6: signed(8),
            p7: signed(9),
            p8: signed(10),
            p9: signed(11),
        };
        self.write_register(REG_CONFIG, CONFIG)?; // in sleep mode, as the datasheet asks
        self.write_register(REG_CTRL_MEAS, CTRL_MEAS)?;
        Ok(id)
    }

    /// The latest temperature and pressure.
    pub fn read(&mut self) -> Result<Reading, Error<I::Error>> {
        let raw = self.read_registers(REG_DATA, 6)?;
        let value = |i: usize| {
            (i32::from(raw[i]) << 12) | (i32::from(raw[i + 1]) << 4) | (i32::from(raw[i + 2]) >> 4)
        };
        let (adc_p, adc_t) = (value(0), value(3));
        if adc_t == NOT_MEASURED {
            return Err(Error::NotReady);
        }
        Ok(compensate(&self.calibration, adc_t, adc_p))
    }

    /// The address the chip was found at (0x76 or 0x77).
    pub fn address(&self) -> u8 {
        self.address
    }

    pub fn calibration(&self) -> Calibration {
        self.calibration
    }

    /// Give the bus back.
    pub fn release(self) -> I {
        self.i2c
    }

    fn read_registers(&mut self, register: u8, count: usize) -> Result<[u8; 24], Error<I::Error>> {
        let mut out = [0u8; 24];
        self.i2c
            .write_read(self.address, &[register], &mut out[..count])
            .map_err(Error::Bus)?;
        Ok(out)
    }

    fn write_register(&mut self, register: u8, value: u8) -> Result<(), Error<I::Error>> {
        self.i2c.write(self.address, &[register, value]).map_err(Error::Bus)
    }
}

/// The datasheet's calculation (the 64-bit integer one): raw 20-bit temperature and pressure to
/// hundredths of a degree and pascals.
pub fn compensate(c: &Calibration, adc_t: i32, adc_p: i32) -> Reading {
    let (t1, t2, t3) = (i32::from(c.t1), i32::from(c.t2), i32::from(c.t3));
    let var1 = (((adc_t >> 3) - (t1 << 1)) * t2) >> 11;
    let var2 = (((((adc_t >> 4) - t1) * ((adc_t >> 4) - t1)) >> 12) * t3) >> 14;
    let t_fine = var1 + var2;
    let temperature_centi_c = (t_fine * 5 + 128) >> 8;

    let p = |v: i16| i64::from(v);
    let mut var1 = i64::from(t_fine) - 128_000;
    let mut var2 = var1 * var1 * p(c.p6);
    var2 += (var1 * p(c.p5)) << 17;
    var2 += p(c.p4) << 35;
    var1 = ((var1 * var1 * p(c.p3)) >> 8) + ((var1 * p(c.p2)) << 12);
    var1 = (((1i64 << 47) + var1) * i64::from(c.p1)) >> 33;
    if var1 == 0 {
        return Reading { temperature_centi_c, pressure_pa: 0 }; // avoid dividing by zero
    }
    let mut pressure = 1_048_576 - i64::from(adc_p);
    pressure = (((pressure << 31) - var2) * 3125) / var1;
    let var1 = (p(c.p9) * (pressure >> 13) * (pressure >> 13)) >> 25;
    let var2 = (p(c.p8) * pressure) >> 19;
    pressure = ((pressure + var1 + var2) >> 8) + (p(c.p7) << 4); // in 1/256 Pa
    Reading { temperature_centi_c, pressure_pa: (pressure >> 8) as i32 }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn datasheet_example() {
        // The worked example of the BMP280 datasheet (section 3.12): 25.08 °C, 100 653 Pa.
        let c = Calibration {
            t1: 27504,
            t2: 26435,
            t3: -1000,
            p1: 36477,
            p2: -10685,
            p3: 3024,
            p4: 2855,
            p5: 140,
            p6: -7,
            p7: 15500,
            p8: -14600,
            p9: 6000,
        };
        assert_eq!(
            compensate(&c, 519888, 415148),
            Reading { temperature_centi_c: 2508, pressure_pa: 100653 }
        );
    }
}
