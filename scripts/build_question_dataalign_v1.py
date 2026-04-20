#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = PROJECT_ROOT / "benchmark" / "question.json"
DEFAULT_DST = PROJECT_ROOT / "benchmark" / "question_dataalign_v1.json"


def _q13_text() -> str:
    return (
        "Based on thermal Band 31 and Band 32 data over the Sahara Desert region "
        "from 2018 to 2023, first list the input TIFF files, then estimate land "
        "surface temperature (LST) using the split-window algorithm. Next, "
        "calculate the daily average LST, derive the annual average LST, and "
        "finally compute the linear trend of the annual averages to assess "
        "long-term temperature changes in the region."
    )


def _q14_text_long() -> str:
    return (
        "Based on thermal Band 31 and Band 32 data over the Ganges Delta from "
        "2015 to 2020, first list the input TIFF files, then estimate land "
        "surface temperature (LST) using the split-window method. Calculate the "
        "daily average LST, derive the annual average LST, and finally apply the "
        "Mann-Kendall trend test to the annual LST time series to obtain "
        "statistical results, including the p-value and Sen's slope."
    )


def _q14_text_short() -> str:
    return (
        "Based on thermal Band 31 and Band 32 data over the Ganges Delta from "
        "2015 to 2020, perform the Mann-Kendall trend test on the annual LST "
        "time series and report the p-value and Sen's slope."
    )


def _q63_text_long() -> str:
    return (
        "Using MODIS surface reflectance bands b02, b05, b17, b18, and b19 over "
        "the Loess Plateau region in July 2022, first list the input TIFF files, "
        "then apply the band ratio method to estimate daily atmospheric water "
        "vapor. Calculate the daily average values and finally compute the mean "
        "atmospheric water vapor for the entire month."
    )


def _q63_text_short() -> str:
    return (
        "Using MODIS bands b02, b05, b17, b18, and b19 over the Loess Plateau in "
        "July 2022, estimate daily atmospheric water vapor and calculate the "
        "monthly mean for July."
    )


def _q66_text_long() -> str:
    return (
        "Using TES-derived land surface temperature and emissivity from ASTER "
        "Bands 10-14 on June 15, 2022, over the Los Angeles metropolitan area, "
        "first list the input TIFF files, then apply the Temperature and "
        "Emissivity Separation (TES) algorithm. Finally, calculate the "
        "percentage-point difference between: Moderate UHI (LST > 300 K and "
        "emissivity < 0.96) and Severe UHI (LST > 305 K and emissivity < 0.95)."
    )


def _q66_text_short() -> str:
    return (
        "On June 15, 2022, using TES-derived land surface temperature and "
        "emissivity from ASTER thermal bands over the Los Angeles metropolitan "
        "area, calculate the percentage-point difference between Moderate UHI "
        "(LST > 300 K and emissivity < 0.96) and Severe UHI (LST > 305 K and "
        "emissivity < 0.95)."
    )


def _q122_text() -> str:
    return (
        "Based on the available sur_refl_b01 observations of Taihu Lake in 2022, "
        "calculate water turbidity and generate distribution maps, compute weekly "
        "mean turbidity values, and conduct short-term trend analysis using the "
        "Mann-Kendall method."
    )


def _q134_text() -> str:
    return (
        "Based on the SR_B3, SR_B5, and QA_PIXEL data of Lake Champlain during "
        "2007 and 2008, first perform cloud masking using QA_PIXEL, then "
        "calculate the NDWI of the lake using SR_B3 (Green band) and SR_B5 "
        "(Near-Infrared band), compute the annual average NDWI for 2007 and "
        "2008, and determine the trend and magnitude of change between the two "
        "years."
    )


def _q135_text() -> str:
    return (
        "Based on the SR_B3, SR_B5, and QA_PIXEL data of Lake Balkhash during "
        "2008 and 2009, first perform cloud masking, then calculate the NDWI of "
        "the lake, compute the average NDWI at each time step, and use Linear "
        "Trend Analysis to determine the overall NDWI trend in the area."
    )


def _q136_text() -> str:
    return (
        "Define the area where NDWI drops by 30% as the severe water loss area. "
        "Based on the available SR_B3, SR_B5, and QA_PIXEL observations of the "
        "Dead Sea during 2008-2009, remove clouds, calculate NDWI, and calculate "
        "the proportion of severe water loss area to the total water area at each "
        "observation time. Find the day with the most severe water loss."
    )


def _q139_text() -> str:
    return (
        "Based on the available B10 band observations of Reykjavik, Iceland, "
        "from February to April 2024, calculate the daily surface temperature "
        "average, find the lowest surface temperature during this period, and "
        "give the date of the lowest surface temperature."
    )


def _q140_text() -> str:
    return (
        "Based on the available B10 band observations of Chicago and Rome in "
        "2024, calculate the average surface temperature in Celsius and "
        "determine which city is warmer. Report the temperature difference "
        "between the two cities."
    )


def _q154_text() -> str:
    return (
        "Based on the available sur_refl_b01 observations of Lake Van in 2022, "
        "calculate water turbidity, save the turbidity maps, calculate the "
        "average value of each image, and use Mann-Kendall to perform "
        "significance trend analysis."
    )


def _q169_text() -> str:
    return (
        "Based on the SR_B3, SR_B5, and QA_PIXEL data of Somerville Lake during "
        "2018 and 2019, remove clouds, calculate the NDWI of the lake, compute "
        "the annual average NDWI for 2018 and 2019, and determine the magnitude "
        "and direction of change between the two years."
    )


def _q170_text() -> str:
    return (
        "Based on the SR_B3, SR_B5, and QA_PIXEL data of Somerville Lake during "
        "2018 and 2019, remove clouds, calculate the NDWI of the lake, calculate "
        "the average NDWI at each time point, and use Linear Trend Analysis to "
        "determine the overall NDWI trend in the area."
    )


def _q172_text() -> str:
    return (
        "Based on the available SR_B3, SR_B5, and QA_PIXEL observations of "
        "Somerville Lake during 2018-2019, remove clouds, calculate NDWI, "
        "calculate the average NDWI at each time point, and assess NDWI "
        "volatility by calculating the coefficient of variation."
    )


def _q174_text() -> str:
    return (
        "Based on the available B10 data for Reykholt from January to March 2024 "
        "(in Kelvin), calculate the daily surface temperature average, convert "
        "the averages to Celsius, and determine the mean surface temperature in "
        "Celsius for the period."
    )


def _q175_text() -> str:
    return (
        "Based on the available B10 band data in Kelvin for Reykholt from "
        "January to March 2024, calculate the daily surface temperature average, "
        "find the lowest surface temperature during this period, and give the "
        "date of the lowest surface temperature."
    )


def _q176_text() -> str:
    return (
        "Based on the available B10 band data in Kelvin for Reykholt and Gazelle "
        "from January to March 2024, calculate the average surface temperature "
        "for each city during the period and determine which city is warmer and "
        "by how much."
    )


def _q177_text() -> str:
    return (
        "Based on the available fire MaxFRP observations in Thailand during "
        "2018, treat areas with MaxFRP > 0 as fire-prone areas and calculate the "
        "mean Fire Radiative Power (FRP) across Thailand in 2018."
    )


def _q178_text() -> str:
    return (
        "Based on the available fire MaxFRP observations in Thailand during "
        "2018, treat areas with MaxFRP > 0 as fire-prone areas, calculate the "
        "linear trend, and determine whether fire activity is increasing and how "
        "severe the trend is."
    )


def _q179_text() -> str:
    return (
        "Based on the available fire MaxFRP observations in Thailand during "
        "August 2018, treat areas with MaxFRP > 0 as fire-prone areas. "
        "Determine the kurtosis of fire pixel counts across the available August "
        "observations and assess which observation day is most fire-prone."
    )


def _q180_text() -> str:
    return (
        "Based on the available fire MaxFRP observations in Thailand during "
        "August 2018, treat areas with MaxFRP > 0 as fire-prone areas. Conduct "
        "a hotspot analysis to determine which areas are most prone to fires."
    )


PATCHES: dict[str, dict[str, Any]] = {
    "1": {
        "evals": [
            "Based on temperature and vegetation data (NDVI and LST) from the agricultural region near Urumqi, Xinjiang between 2019 and 2022, first apply the Temperature-Vegetation Dryness Index (TVDI) method by constructing a scatter plot of NDVI versus LST for each day, and calculate the TVDI value for each pixel to reflect the dryness condition and then calculate the annual average of TVDI and perform linear analysis on the annual average value data to best describe the annual trend.",
            "Based on temperature and vegetation reflectance data (NDVI and LST) from the agricultural region near Urumqi, Xinjiang between 2019 and 2022, calculate the linear trend of the dryness indicator and describe the annual trend.",
        ],
    },
    "2": {
        "evals": [
            "The Chengdu Plain Agricultural Zone in Sichuan Province is a key rice-producing region in southwestern China. On July 12, 2021, MODIS-derived Land Surface Temperature (LST) and Enhanced Vegetation Index (EVI) data were used to assess drought conditions across the area. First, list the input TIFF files, then calculate the Temperature-Vegetation Dryness Index (TVDI), and finally determine the percentage of the agricultural area where TVDI values exceeded the threshold of 0.75, indicating moderate water stress for rice crops.",
            "The Chengdu Plain Agricultural Zone in Sichuan Province is a crucial rice-producing region in southwestern China. On July 12, 2021, researchers analyzed MODIS-derived Land Surface Temperature (LST) and Enhanced Vegetation Index (EVI) data to assess drought conditions across the Chengdu Plain. Using a dryness index threshold of > 0.75 to indicate moderate water stress for rice crops, calculate the percentage of this agricultural area that exceeded the critical value.",
        ],
    },
    "13": {
        "user": _q13_text(),
        "evals": [_q13_text(), "Based on thermal Band 31 and Band 32 data over the Sahara Desert region from 2018 to 2023, calculate the linear trend of land surface temperature using the split-window algorithm."],
    },
    "14": {
        "user": _q14_text_long(),
        "evals": [_q14_text_long(), _q14_text_short()],
    },
    "47": {
        "user": "Using MODIS LST and NDVI data over the Chengdu Plain on July 12, 2021, calculate TVDI and determine the mean TVDI value in areas where the LST exceeds 300 K.",
        "evals": [
            "Based on MODIS-derived Land Surface Temperature (LST) and Enhanced Vegetation Index (EVI) data for the Chengdu Plain Agricultural Zone on July 12, 2021, first calculate the Temperature-Vegetation Dryness Index (TVDI) for each pixel by constructing a scatter plot of EVI versus LST. Then, identify areas where LST exceeds 300 K, and compute the average TVDI value for these high-temperature regions to assess their dryness level.",
            "Using MODIS LST and EVI data over the Chengdu Plain on July 12, 2021, calculate TVDI and determine the mean TVDI value in areas where the LST exceeds 300 K.",
        ],
    },
    "63": {
        "user": _q63_text_long(),
        "evals": [_q63_text_long(), _q63_text_short()],
    },
    "66": {
        "user": _q66_text_long(),
        "evals": [_q66_text_long(), _q66_text_short()],
    },
    "112": {
        "user": "Based on January nighttime light intensity of Leon (2013-2023), compute the annual mean intensity and estimate the linear trend. The relevant data is stored in benchmark/data/question112.",
        "evals": [
            "Based on January nighttime light intensity of Leon (2013-2023), compute the annual mean intensity and estimate the linear trend.",
            "Analyze the nighttime light intensity trend in Leon (2013-2023) using linear regression.",
        ],
    },
    "122": {
        "user": _q122_text() + " The relevant data is stored in benchmark/data/question122.",
        "evals": [_q122_text(), "Analyze Taihu Lake's 2022 turbidity dynamics using the available MODIS sur_refl_b01 observations."],
    },
    "134": {
        "user": _q134_text(),
        "evals": [_q134_text(), "Evaluate Lake Champlain's water index changes during 2007 and 2008 using cloud-masked NDWI analysis."],
    },
    "135": {
        "user": _q135_text(),
        "evals": [_q135_text(), "Assess Lake Balkhash's NDWI trends during 2008 and 2009 using cloud-masked Landsat data."],
    },
    "136": {
        "user": _q136_text(),
        "evals": [_q136_text(), "Determine the Dead Sea's peak water loss event during 2008-2009 using NDWI threshold analysis."],
        "choices": [
            "Peak loss date: 2009-02-15 | Loss proportion: 28.5%",
            "Peak loss date: 2009-03-03 | Loss proportion: 34.0%",
            "Peak loss date: 2009-03-19 | Loss proportion: 31.2%",
            "Peak loss date: 2008-12-13 | Loss proportion: 25.8%",
        ],
    },
    "139": {
        "user": _q139_text(),
        "evals": [_q139_text(), "Determine Reykjavik's coldest available observation day from February to April 2024 using Landsat B10 thermal data."],
        "choices": [
            "Date: 2024-03-22 | Temperature: -35.12 C",
            "Date: 2024-02-26 | Temperature: -38.24 C",
            "Date: 2024-04-05 | Temperature: -33.07 C",
            "Date: 2024-02-17 | Temperature: -36.89 C",
        ],
    },
    "140": {
        "user": _q140_text(),
        "evals": [_q140_text(), "Analyze 2024 surface temperature differences between Chicago and Rome using the available Landsat thermal observations."],
    },
    "154": {
        "user": _q154_text(),
        "evals": [_q154_text(), _q154_text()],
    },
    "163": {
        "user": "Based on the sur_refl_b04 and sur_refl_b06 data in Greenland, calculate the mean NDSI value of Greenland on 2020-09-12. Keep the answer to three decimal places.",
        "evals": [
            "Based on the sur_refl_b04 and sur_refl_b06 data in Greenland, calculate the mean NDSI value of Greenland on 2020-09-12. Keep the answer to three decimal places.",
            "Based on the sur_refl_b04 and sur_refl_b06 data in Greenland, calculate the mean NDSI value of Greenland on 2020-09-12. Keep the answer to three decimal places.",
        ],
    },
    "169": {
        "user": _q169_text(),
        "evals": [_q169_text(), "Based on the SR_B3, SR_B5, and QA_PIXEL data of Somerville Lake during 2018 and 2019, remove clouds and determine the trend of change in annual average NDWI between the two years."],
    },
    "170": {
        "user": _q170_text(),
        "evals": [_q170_text(), "Based on the SR_B3, SR_B5, and QA_PIXEL data of Somerville Lake during 2018 and 2019, remove clouds and use Linear Trend Analysis to determine the overall NDWI trend in the area."],
    },
    "172": {
        "user": _q172_text(),
        "evals": [_q172_text(), _q172_text()],
    },
    "174": {
        "user": _q174_text(),
        "evals": [
            _q174_text(),
            "Based on the available B10 data for Reykholt from January to March 2024 in Kelvin, determine the mean surface temperature in Celsius for the period.",
        ],
    },
    "175": {
        "user": _q175_text(),
        "evals": [
            _q175_text(),
            "Based on the available B10 band data at Reykholt from January to March 2024, determine the date of the lowest surface temperature.",
        ],
    },
    "176": {
        "user": _q176_text(),
        "evals": [
            _q176_text(),
            "Based on the available B10 band data in Kelvin for Reykholt and Gazelle from January to March 2024, calculate the average surface temperature in Celsius and determine which city is warmer and by how much.",
        ],
    },
    "177": {
        "user": _q177_text(),
        "evals": [_q177_text(), _q177_text()],
    },
    "178": {
        "user": _q178_text(),
        "evals": [_q178_text(), _q178_text()],
    },
    "179": {
        "user": _q179_text(),
        "evals": [_q179_text(), "Based on the available fire MaxFRP observations in Thailand during August 2018, assess which observation day is most prone to fire."],
    },
    "180": {
        "user": _q180_text(),
        "evals": [_q180_text(), "Based on the available fire MaxFRP observations in Thailand during August 2018, use hotspot analysis to determine which areas are most prone to fires."],
    },
}


def _actual_file_list(qid: str) -> list[str]:
    qdir = PROJECT_ROOT / "benchmark" / "data" / f"question{qid}"
    return sorted(path.name for path in qdir.iterdir() if path.is_file())


def _refresh_get_filelist_turns(record: dict[str, Any], qid: str) -> int:
    files = _actual_file_list(qid)
    updated = 0
    for turn in record.get("dialogs", []):
        if turn.get("role") == "tool" and turn.get("name") == "get_filelist":
            content = turn.get("content")
            if not isinstance(content, dict):
                content = {}
                turn["content"] = content
            if not content.get("type"):
                content["type"] = "list(str)"
            content["content"] = files
            updated += 1
    return updated


def _apply_patch(record: dict[str, Any], patch: dict[str, Any]) -> None:
    if "user" in patch:
        dialogs = record.get("dialogs", [])
        if dialogs and dialogs[0].get("role") == "user":
            dialogs[0]["content"] = patch["user"]

    if "evals" in patch:
        evals = record.get("evaluation", [])
        for idx, text in enumerate(patch["evals"]):
            if idx < len(evals):
                evals[idx]["question"] = text

    if "choices" in patch:
        record["choices"] = patch["choices"]


def build_question_dataalign_v1(src_path: Path, dst_path: Path) -> dict[str, Any]:
    raw = json.loads(src_path.read_text(encoding="utf-8"))
    refreshed_qids: list[str] = []
    patched_qids: list[str] = []

    for qid, record in raw.items():
        if _refresh_get_filelist_turns(record, qid):
            refreshed_qids.append(qid)
        patch = PATCHES.get(qid)
        if patch:
            _apply_patch(record, patch)
            patched_qids.append(qid)

    dst_path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "dst_path": str(dst_path),
        "patched_qids": patched_qids,
        "patched_count": len(patched_qids),
        "refreshed_get_filelist_qids": refreshed_qids,
        "refreshed_get_filelist_count": len(refreshed_qids),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build prompt/data-aligned Earth-Bench question_dataalign_v1.json.")
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC)
    parser.add_argument("--dst", type=Path, default=DEFAULT_DST)
    args = parser.parse_args()

    summary = build_question_dataalign_v1(args.src, args.dst)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
