import argparse
import json
import os
import re
from collections import defaultdict

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir_path", required=True)
    parser.add_argument("--output_subdir", default="tvdi_out")
    args = parser.parse_args()

    files = [f for f in os.listdir(args.dir_path) if f.lower().endswith('.tif')]
    grouped = defaultdict(dict)

    for fname in files:
        m = DATE_RE.search(fname)
        if not m:
            continue
        date = m.group(1)
        upper = fname.upper()
        full = os.path.join(args.dir_path, fname)
        if '_NDVI' in upper:
            grouped[date]['ndvi'] = full
        elif '_LST' in upper:
            grouped[date]['lst'] = full

    dates = sorted(d for d, v in grouped.items() if 'ndvi' in v and 'lst' in v)
    result = {
        'dates': dates,
        'ndvi_path': [grouped[d]['ndvi'] for d in dates],
        'lst_path': [grouped[d]['lst'] for d in dates],
        'output_path': [os.path.join(args.output_subdir, f'tvdi_{d}.tif') for d in dates],
    }

    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
