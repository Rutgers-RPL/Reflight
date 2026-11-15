"""Read-only CMSIS-SVD register metadata."""

from .register_map import (FieldInfo, PeripheralInfo, RegisterInfo, RegisterMap,
                           SVDLoadError, SVDLookupError)

__all__ = ["FieldInfo", "PeripheralInfo", "RegisterInfo", "RegisterMap",
           "SVDLoadError", "SVDLookupError"]
