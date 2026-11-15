"""Compatibility imports for sensor models, now implemented in separate modules."""

from .base import SensorError, SimulationInputs, _RegisterSensor, _axes
from .icm45686 import ICM45686
from .ms5611 import MS5611, _ms_crc4
from .bmi088_accel import BMI088Accel
from .bmi088_gyro import BMI088Gyro
from .bmp581 import BMP581

__all__ = ["ICM45686", "MS5611", "BMI088Accel", "BMI088Gyro", "BMP581",
           "SensorError", "SimulationInputs"]
