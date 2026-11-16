//! MPU-6050 accelerometer and gyroscope over I2C. Added to the project by NightExpress
//! ("Add peripheral"); it is yours to change (0BSD).
//!
//! Works with any `embedded_hal::i2c::I2c`. The values are integers, so no floating point
//! is needed on chips without an FPU.

#![allow(dead_code)] // not every function is used by every program

use embedded_hal::i2c::I2c;

/// The address with AD0 at GND (the usual GY-521 module).
pub const ADDRESS: u8 = 0x68;
/// The address with AD0 at 3.3 V.
pub const ADDRESS_AD0_HIGH: u8 = 0x69;

const WHO_AM_I: u8 = 0x75;
const PWR_MGMT_1: u8 = 0x6B;
const CONFIG: u8 = 0x1A;
const GYRO_CONFIG: u8 = 0x1B;
const ACCEL_CONFIG: u8 = 0x1C;
const ACCEL_CONFIG2: u8 = 0x1D; // MPU-6500 and later only: the accelerometer's own filter
const INT_ENABLE: u8 = 0x38;
const INT_STATUS: u8 = 0x3A;
const ACCEL_XOUT_H: u8 = 0x3B;
const GYRO_XOUT_H: u8 = 0x43;

/// Low-pass filter: about 44 Hz, so the readings shake less (the MPU-6050's DLPF_CFG = 3).
const LOW_PASS_44_HZ: u8 = 0x03;
/// How many times to look for a new sample before giving up (a sample comes every 1 ms).
const DATA_READY_TRIES: u32 = 10_000;

/// WHO_AM_I of the MPU-6050 itself. GY-521 modules often carry compatible chips or clones
/// that answer something else (0x70, 0x71, 0x73, 0x98…), so any other value is accepted.
pub const MPU6050_ID: u8 = 0x68;

#[derive(Debug)]
pub enum Error<E> {
    /// The bus failed: nothing answers at the address (wiring, power, address) or a bus error.
    Bus(E),
    /// WHO_AM_I read as 0x00 or 0xFF: no chip of this family drives the bus at the address.
    UnknownDevice(u8),
    /// The chip did not produce a new sample in time (it is not awake: call `init` first).
    NotReady,
}

/// One reading: acceleration in thousandths of g, rotation in thousandths of a degree per
/// second, temperature in hundredths of a degree Celsius.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Reading {
    pub accel_mg: [i32; 3],
    pub gyro_mdps: [i32; 3],
    pub temperature_centi_c: i32,
}

pub struct Mpu6050<I> {
    i2c: I,
    address: u8,
    id: u8,
    gyro_offset: [i32; 3], // raw units, measured by `calibrate_gyro`
}

impl<I: I2c> Mpu6050<I> {
    pub fn new(i2c: I) -> Self {
        Self::with_address(i2c, ADDRESS)
    }

    pub fn with_address(i2c: I, address: u8) -> Self {
        Self { i2c, address, id: MPU6050_ID, gyro_offset: [0; 3] }
    }

    /// Check that the chip is there, wake it up (it starts asleep), choose the ranges ±2 g and
    /// ±250 °/s and a 44 Hz low-pass filter. Returns the WHO_AM_I value.
    pub fn init(&mut self) -> Result<u8, Error<I::Error>> {
        let id = self.read_register(WHO_AM_I)?;
        if id == 0x00 || id == 0xFF {
            return Err(Error::UnknownDevice(id));
        }
        self.write_register(PWR_MGMT_1, 0x01)?; // awake, clocked by the X gyro (more stable)
        self.write_register(GYRO_CONFIG, 0x00)?; // ±250 °/s: 131 per °/s
        self.write_register(ACCEL_CONFIG, 0x00)?; // ±2 g: 16384 per g
        self.write_register(CONFIG, LOW_PASS_44_HZ)?;
        if id != MPU6050_ID {
            self.write_register(ACCEL_CONFIG2, LOW_PASS_44_HZ)?;
        }
        self.write_register(INT_ENABLE, 0x01)?; // flag each new sample in INT_STATUS
        self.id = id;
        self.wait_for_sample()?; // the first reading is then a real one, not zeros
        Ok(id)
    }

    /// Measure what the gyroscope shows at rest and subtract it from every reading from now
    /// on. The sensor must lie still meanwhile; `samples` = 200 takes about 0.2 s.
    /// Returns the offset in thousandths of a degree per second.
    pub fn calibrate_gyro(&mut self, samples: u16) -> Result<[i32; 3], Error<I::Error>> {
        let mut sum = [0i32; 3];
        for _ in 0..samples.max(1) {
            self.wait_for_sample()?;
            let mut raw = [0u8; 6];
            self.i2c
                .write_read(self.address, &[GYRO_XOUT_H], &mut raw)
                .map_err(Error::Bus)?;
            for (axis, total) in sum.iter_mut().enumerate() {
                *total += i32::from(i16::from_be_bytes([raw[2 * axis], raw[2 * axis + 1]]));
            }
        }
        let n = i32::from(samples.max(1));
        self.gyro_offset = sum.map(|total| total / n);
        Ok(self.gyro_offset.map(|raw| raw * 1000 / 131))
    }

    /// Read acceleration, temperature and rotation in one go (14 registers from ACCEL_XOUT_H).
    pub fn read(&mut self) -> Result<Reading, Error<I::Error>> {
        let mut raw = [0u8; 14];
        self.i2c
            .write_read(self.address, &[ACCEL_XOUT_H], &mut raw)
            .map_err(Error::Bus)?;
        let word = |i: usize| i32::from(i16::from_be_bytes([raw[i], raw[i + 1]]));
        let gyro = |axis: usize| (word(8 + 2 * axis) - self.gyro_offset[axis]) * 1000 / 131;
        Ok(Reading {
            accel_mg: [word(0) * 1000 / 16384, word(2) * 1000 / 16384, word(4) * 1000 / 16384],
            temperature_centi_c: self.temperature_centi_c(word(6)),
            gyro_mdps: [gyro(0), gyro(1), gyro(2)],
        })
    }

    /// The WHO_AM_I value `init` read (0x68 for a real MPU-6050).
    pub fn id(&self) -> u8 {
        self.id
    }

    fn temperature_centi_c(&self, raw: i32) -> i32 {
        if self.id == MPU6050_ID {
            raw * 100 / 340 + 3653 // MPU-6050 datasheet: °C = raw / 340 + 36.53
        } else {
            // MPU-6500 and the clones on GY-521 modules: °C = raw / 333.87 + 21.
            raw * 10_000 / 33_387 + 2100
        }
    }

    fn wait_for_sample(&mut self) -> Result<(), Error<I::Error>> {
        for _ in 0..DATA_READY_TRIES {
            if self.read_register(INT_STATUS)? & 0x01 != 0 {
                return Ok(()); // reading INT_STATUS also clears the flag
            }
        }
        Err(Error::NotReady)
    }

    /// Give the bus back, e.g. to share it with another module.
    pub fn release(self) -> I {
        self.i2c
    }

    fn read_register(&mut self, register: u8) -> Result<u8, Error<I::Error>> {
        let mut value = [0u8];
        self.i2c
            .write_read(self.address, &[register], &mut value)
            .map_err(Error::Bus)?;
        Ok(value[0])
    }

    fn write_register(&mut self, register: u8, value: u8) -> Result<(), Error<I::Error>> {
        self.i2c.write(self.address, &[register, value]).map_err(Error::Bus)
    }
}
