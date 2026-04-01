from __future__ import annotations

from importlib.machinery import PathFinder
from importlib.util import module_from_spec
from pathlib import Path
import sys


def _load_real_osgeo():
    current_file = Path(__file__).resolve()
    current_root = current_file.parent.parent.resolve()
    search_paths: list[str] = []
    for entry in sys.path:
        candidate = Path(entry or ".").resolve()
        if candidate == current_root:
            continue
        search_paths.append(str(candidate))

    spec = PathFinder.find_spec(__name__, search_paths)
    if spec is None or spec.loader is None or not spec.origin:
        return None
    if Path(spec.origin).resolve() == current_file:
        return None

    module = module_from_spec(spec)
    sys.modules[__name__] = module
    spec.loader.exec_module(module)
    return module


_real_osgeo = _load_real_osgeo()

if _real_osgeo is None:
    from . import gdal

    __all__ = ["gdal"]
else:
    globals().update(_real_osgeo.__dict__)
