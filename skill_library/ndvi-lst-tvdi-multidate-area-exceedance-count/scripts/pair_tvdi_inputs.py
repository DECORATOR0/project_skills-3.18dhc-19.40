import argparse
import json
import os
import re
from pathlib import Path

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def detect_kind(name: str):
    upper = name.upper()
    if "NDVI" in upper:
        return "NDVI"
    if "LST" in upper:
        return "LST"
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output-subdir", default="tvdi_outputs")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    files = sorted([p for p in data_dir.iterdir() if p.is_file() and p.suffix.lower() == ".tif"])

    by_date = {}
    for p in files:
        m = DATE_RE.search(p.name)
        kind = detect_kind(p.name)
        if not m or not kind:
            continue
        date = m.group(1)
        by_date.setdefault(date, {})[kind] = str(p)

    dates = sorted(d for d, kinds in by_date.items() if "NDVI" in kinds and "LST" in kinds)
    ndvi_paths = [by_date[d]["NDVI"] for d in dates]
    lst_paths = [by_date[d]["LST"] for d in dates]
    output_paths = [str(Path(args.output_subdir) / f"tvdi_{d}.tif") for d in dates]

    result = {
        "dates": dates,
        "ndvi_paths": ndvi_paths,
        "lst_paths": lst_paths,
        "output_paths": output_paths,
        "count": len(dates)
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
