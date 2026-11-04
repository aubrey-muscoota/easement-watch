"""Cloud-masked NDVI composites for one parcel window."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import Window, from_bounds

from .detect import greenness
from .scenes import Scene

# Sentinel-2 scene classification (SCL) values treated as clear land/water.
# 4 vegetation, 5 bare soil, 6 water, 7 unclassified. Everything else
# (cloud, shadow, cirrus, snow, saturated, nodata) is masked out.
CLEAR_SCL = (4, 5, 6, 7)

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_MULTIRANGE", "YES")
os.environ.setdefault("GDAL_HTTP_MERGE_CONSECUTIVE_RANGES", "YES")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")


@dataclass
class Grid:
    """Pixel grid in the tile's UTM CRS that covers the parcel."""
    crs: object
    transform: object
    width: int
    height: int


def grid_for(scene: Scene, bounds_utm: tuple, pad_m: float = 200) -> Grid:
    with rasterio.open(scene.band("B04")) as src:
        minx, miny, maxx, maxy = bounds_utm
        win = from_bounds(minx - pad_m, miny - pad_m, maxx + pad_m, maxy + pad_m, src.transform)
        win = win.round_offsets().round_lengths()
        win = win.intersection(Window(0, 0, src.width, src.height))
        return Grid(src.crs, src.window_transform(win), int(win.width), int(win.height))


def _read(url: str, grid: Grid, resampling=Resampling.nearest, bands=(1,)) -> np.ndarray:
    """Read bands from a COG onto the parcel grid. Returns (bands, h, w)."""
    with rasterio.open(url) as src:
        left, top = grid.transform * (0, 0)
        right, bottom = grid.transform * (grid.width, grid.height)
        win = from_bounds(left, bottom, right, top, src.transform)
        return src.read(
            list(bands),
            window=win,
            out_shape=(len(bands), grid.height, grid.width),
            resampling=resampling,
            boundless=True,
            fill_value=0,
        )


def read_scene(scene: Scene, grid: Grid) -> tuple[np.ndarray, np.ndarray]:
    """Return (ndvi, clear) for one scene on the parcel grid."""
    red = _read(scene.band("B04"), grid)[0].astype("float32")
    nir = _read(scene.band("B08"), grid)[0].astype("float32")
    scl = _read(scene.band("SCL"), grid)[0]  # 20 m, nearest-resampled to 10 m
    clear = np.isin(scl, CLEAR_SCL) & (red > 0) & (nir > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        ndvi = (nir - red) / (nir + red)
    ndvi[~clear] = np.nan
    return ndvi, clear


def read_rgb(scene: Scene, grid: Grid) -> np.ndarray:
    """True-colour image (TCI, 8-bit) on the parcel grid, shape (h, w, 3)."""
    rgb = _read(scene.band("TCI"), grid, Resampling.bilinear, bands=(1, 2, 3))
    return np.moveaxis(rgb, 0, -1)


@dataclass
class Composite:
    ndvi: np.ndarray        # per-pixel seasonal greenness (90th pct NDVI)
    n_clear: np.ndarray     # clear observations per pixel
    scenes: list            # scenes used
    best_scene: Scene | None  # clearest scene over the parcel, for pictures


def build(scenes: list[Scene], grid: Grid, parcel_mask: np.ndarray, workers: int = 8) -> Composite:
    with ThreadPoolExecutor(workers) as ex:
        results = list(ex.map(lambda s: read_scene(s, grid), scenes))
    stack = np.stack([r[0] for r in results]) if results else np.full((1, grid.height, grid.width), np.nan)
    n_clear = np.sum(~np.isnan(stack), axis=0)
    ndvi = greenness(stack)
    # Picture scene: mostly clear over the parcel, and closest to the
    # composite. Thin haze passes the SCL mask but pulls NDVI away from the
    # composite, so this skips hazy days.
    best, best_key = None, None
    for sc, (nd, clear) in zip(scenes, results):
        m = parcel_mask & clear
        frac = float(m.sum() / parcel_mask.sum()) if parcel_mask.any() else 0.0
        if frac < 0.9 * max(float((parcel_mask & c).sum() / max(parcel_mask.sum(), 1)) for _, c in results):
            continue
        err = float(np.nanmean(np.abs(nd[m] - ndvi[m]))) if m.any() else 9.0
        if best_key is None or err < best_key:
            best, best_key = sc, err
    used = [s for s, (_, c) in zip(scenes, results) if c[parcel_mask].any()]
    return Composite(ndvi, n_clear, used, best)
