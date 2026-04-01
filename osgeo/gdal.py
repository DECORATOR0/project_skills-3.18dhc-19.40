"""
Minimal import-time compatibility shim for environments without GDAL.

This keeps tool modules importable for local runtime paths that do not actually
execute GDAL-dependent functions.
"""
from __future__ import annotations


GDT_Float32 = "GDT_Float32"
GDT_Byte = "GDT_Byte"
GRA_Bilinear = "GRA_Bilinear"


def _missing(*args, **kwargs):
    raise ModuleNotFoundError(
        "GDAL runtime is not available in this environment. "
        "A GDAL-dependent tool was invoked."
    )


Open = _missing
GetDriverByName = _missing
Warp = _missing
