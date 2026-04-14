from __future__ import annotations

from typing import Any

import numpy as np
from osgeo import gdal


def read_image(file_path: str) -> np.ndarray:
    ds = gdal.Open(file_path)
    if ds is None:
        raise RuntimeError(f"Failed to open {file_path}")
    
    bands = ds.RasterCount
    if bands == 1:
        img = ds.GetRasterBand(1).ReadAsArray()
    else:
        img = np.stack([ds.GetRasterBand(i + 1).ReadAsArray() for i in range(bands)], axis=0)
        img = np.transpose(img, (1, 2, 0))

    ds = None
    return img


def read_image_uint8(file_path: str) -> np.ndarray:
    ds = gdal.Open(file_path)
    if ds is None:
        raise RuntimeError(f"Failed to open {file_path}")
    
    bands = ds.RasterCount
    if bands == 1:
        img = ds.GetRasterBand(1).ReadAsArray()
    else:
        img = np.stack([ds.GetRasterBand(i + 1).ReadAsArray() for i in range(bands)], axis=0)
        img = np.transpose(img, (1, 2, 0))

    ds = None

    img = img.astype(np.float32)
    min_val = np.min(img)
    max_val = np.max(img)

    if max_val > min_val:
        img = (img - min_val) / (max_val - min_val) * 255
    else:
        img = np.zeros_like(img)

    return img.astype(np.uint8)


def get_geotransform(file_path) -> tuple:
    ds = gdal.Open(file_path)
    if ds is None:
        raise RuntimeError(f"Failed to open {file_path}")
    geo = ds.GetGeoTransform()
    proj = ds.GetProjection()
    ds = None
    if geo == (0, 1.0, 0, 0, 0, 1.0):
        return None, None
    else:
        return geo, proj


def batch_size_from_values(values: dict[str, Any]) -> int | None:
    lengths = {
        len(value)
        for value in values.values()
        if isinstance(value, (list, tuple))
    }
    if not lengths:
        return None
    if len(lengths) != 1:
        detail = ", ".join(
            f"{name}={len(value)}"
            for name, value in values.items()
            if isinstance(value, (list, tuple))
        )
        raise ValueError(f"Batch arguments must have the same length. Got: {detail}")
    return next(iter(lengths))


def expand_batch_value(
    value: Any,
    batch_size: int | None,
    name: str,
    *,
    allow_scalar_broadcast: bool = True,
) -> list[Any]:
    if batch_size is None:
        return [value]
    if isinstance(value, (list, tuple)):
        items = list(value)
        if len(items) != batch_size:
            raise ValueError(
                f"Batch argument `{name}` must have length {batch_size}, got {len(items)}."
            )
        return items
    if not allow_scalar_broadcast and batch_size > 1:
        raise ValueError(
            f"Batch argument `{name}` must be provided as a list with length {batch_size}."
        )
    return [value] * batch_size


def collapse_batch_results(results: list[Any], batch_size: int | None) -> Any:
    if batch_size is None:
        return results[0]
    return results
