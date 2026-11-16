//! Every driver of src/nightexpress/resources/peripherals, built for the computer so that its
//! `#[cfg(test)]` tests run (tests/unit/test_peripherals.py checks that none is missing here).

#[path = "../../../src/nightexpress/resources/peripherals/bmp180/bmp180.rs"]
pub mod bmp180;
#[path = "../../../src/nightexpress/resources/peripherals/bmp280/bmp280.rs"]
pub mod bmp280;
#[path = "../../../src/nightexpress/resources/peripherals/mpu6050/mpu6050.rs"]
pub mod mpu6050;
