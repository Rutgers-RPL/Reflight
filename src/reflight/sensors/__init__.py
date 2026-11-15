"""Datasheet-bounded SPI sensor models."""

from .models import ICM45686, MS5611, BMI088Accel, BMI088Gyro, BMP581, SensorError
from .registry import make_sensor, build_spi_bus

__all__ = ["ICM45686", "MS5611", "BMI088Accel", "BMI088Gyro", "BMP581",
           "SensorError", "make_sensor", "build_spi_bus"]
