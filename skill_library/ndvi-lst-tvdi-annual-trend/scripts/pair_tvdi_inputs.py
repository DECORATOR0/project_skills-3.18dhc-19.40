import json
import os
import re
import sys
from collections import defaultdict

PATTERN = re.compile(r"^(?P<prefix>.+?)_(?P<date>\d{4}-\d{2}-\d{2})_(?P<var>NDVI|LST)\.tif$", re.IGNORECASE)


def main():
    payload = json.load(sys.stdin)
    data_dir = payload["data_dir"]
    file_list = payload["file_list"]
    output_subdir = payload.get("output_subdir", "tvdi_out")

    by_date = defaultdict(dict)
    for name in file_list:
        base = os.path.basename(name)
        m = PATTERN.match(base)
        if not m:
            continue
        date = m.group("date")
        var = m.group("var").upper()
        by_date[date][var] = os.path.join(data_dir, base)

    dates = sorted(d for d, vals in by_date.items() if "NDVI" in vals and "LST" in vals)
    years = sorted({d[:4] for d in dates})

    ndvi_paths = []
    lst_paths = []
    tvdi_output_paths = []
    tvdi_by_year = defaultdict(list)

    for date in dates:
        year = date[:4]
        ndvi_paths.append(by_date[date]["NDVI"])
        lst_paths.append(by_date[date]["LST"])
        out_rel = f"{output_subdir}/tvdi_{date}.tif"
        tvdi_output_paths.append(out_rel)
        tvdi_by_year[year].append(os.path.join("benchmark/out", out_rel))

    annual_average_outputs = {
        year: os.path.join("benchmark/out", output_subdir, f"tvdi_annual_avg_{year}.tif")
        for year in years
    }

    result = {
        "matched_dates": dates,
        "years": years,
        "ndvi_paths": ndvi_paths,
        "lst_paths": lst_paths,
        "tvdi_output_paths": tvdi_output_paths,
        "tvdi_by_year": {y: tvdi_by_year[y] for y in years},
        "annual_average_outputs": annual_average_outputs,
    }
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
