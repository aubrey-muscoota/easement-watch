"""Find patches of vegetation loss inside a parcel.

The comparison uses each window's "seasonal greenness": the 90th-percentile
NDVI of every pixel over all clear looks in the window. Forest stays green
all season, so a big drop in seasonal greenness means trees or cover were
removed. Crop rotation mostly does not trigger it, because a field that
greens up at any point in the window keeps a high 90th percentile.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

PIXEL_HA = 0.01  # one 10 m Sentinel-2 pixel


@dataclass
class Settings:
    min_baseline_ndvi: float = 0.6   # pixel must have been green before
    min_drop: float = 0.3            # seasonal greenness must fall by this much
    min_patch_ha: float = 0.25       # ignore patches smaller than this
    min_clear_looks: int = 2         # clear observations needed per window
    min_coverage: float = 0.5        # share of parcel that must be observable


@dataclass
class Patch:
    area_ha: float
    mean_drop: float
    row: float
    col: float
    lon: float = 0.0
    lat: float = 0.0


@dataclass
class Result:
    status: str                      # "alert", "no_change" or "no_data"
    coverage: float
    loss_ha: float
    parcel_ha: float
    patches: list = field(default_factory=list)
    loss_mask: np.ndarray | None = None
    drop: np.ndarray | None = None


def greenness(stack: np.ndarray) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN pixels
        return np.nanpercentile(stack, 90, axis=0)


def detect(base: np.ndarray, base_n: np.ndarray, recent: np.ndarray, recent_n: np.ndarray,
           parcel: np.ndarray, s: Settings = Settings()) -> Result:
    observed = parcel & (base_n >= s.min_clear_looks) & (recent_n >= s.min_clear_looks)
    parcel_px = int(parcel.sum())
    coverage = observed.sum() / parcel_px if parcel_px else 0.0
    drop = recent - base
    if coverage < s.min_coverage:
        return Result("no_data", coverage, 0.0, parcel_px * PIXEL_HA, drop=drop)

    loss = observed & (base >= s.min_baseline_ndvi) & (drop <= -s.min_drop)
    labels, n = ndimage.label(loss, structure=np.ones((3, 3)))
    patches, keep = [], np.zeros_like(loss)
    for i in range(1, n + 1):
        m = labels == i
        area = m.sum() * PIXEL_HA
        if area < s.min_patch_ha:
            continue
        keep |= m
        r, c = ndimage.center_of_mass(m)
        patches.append(Patch(round(area, 2), round(float(drop[m].mean()), 2), r, c))
    patches.sort(key=lambda p: -p.area_ha)
    loss_ha = round(sum(p.area_ha for p in patches), 2)
    status = "alert" if patches else "no_change"
    return Result(status, round(float(coverage), 3), loss_ha, round(parcel_px * PIXEL_HA, 1),
                  patches, keep, drop)
