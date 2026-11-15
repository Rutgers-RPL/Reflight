"""Datasheet-bounded SPI sensor models."""

from .base import SensorError
from .icm45686 import ICM45686
from .ms5611 import MS5611
from .bmi088_accel import BMI088Accel
from .bmi088_gyro import BMI088Gyro
from .bmp581 import BMP581
from .registry import make_sensor, build_spi_bus

__all__ = ["ICM45686", "MS5611", "BMI088Accel", "BMI088Gyro", "BMP581",
           "SensorError", "make_sensor", "build_spi_bus"]
