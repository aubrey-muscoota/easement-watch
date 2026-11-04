"""Run change detection for every parcel in a GeoJSON file.

  python -m parcelwatch.run parcels/demo.geojson --out out \
      --baseline 2023-06-01:2023-09-15 --recent 2025-06-01:2025-09-15

Without --baseline/--recent it compares the last 90 days with the same
90 days a year earlier, which is what a scheduled run uses.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import rasterize
from shapely.geometry import mapping, shape
import shapely

from . import composite, report
from .detect import Settings, detect
from .scenes import list_scenes, tile_for

if os.path.exists("/root/.ccr/ca-bundle.crt"):  # sandbox proxy; harmless elsewhere
    os.environ.setdefault("CURL_CA_BUNDLE", "/root/.ccr/ca-bundle.crt")


def _window(text: str) -> tuple[date, date]:
    a, b = text.split(":")
    return date.fromisoformat(a), date.fromisoformat(b)


def _year_earlier(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 February
        return d.replace(year=d.year - 1, day=28)


def default_windows(today: date, days: int = 90):
    recent = (today - timedelta(days=days), today)
    baseline = (_year_earlier(recent[0]), _year_earlier(recent[1]))
    return baseline, recent


def run_parcel(feat: dict, baseline, recent, s: Settings, out: Path, max_cloud: float) -> dict:
    props = feat.get("properties") or {}
    pid = str(props.get("id") or props.get("name") or "parcel")
    name = str(props.get("name") or pid)
    geom = shape(feat["geometry"])
    c = geom.centroid
    tile = props.get("tile") or tile_for(c.x, c.y)
    print(f"[{pid}] {name}: tile {tile}", file=sys.stderr)

    base_sc = list_scenes(tile, *baseline, max_cloud=max_cloud)
    rec_sc = list_scenes(tile, *recent, max_cloud=max_cloud)
    rec = {"id": pid, "name": name, "tile": tile, "status": "no_data", "coverage": 0.0,
           "loss_ha": 0.0, "parcel_ha": 0.0, "patches": [],
           "baseline_looks": 0, "recent_looks": 0}
    if not base_sc or not rec_sc:
        print(f"[{pid}] no scenes in one of the windows", file=sys.stderr)
        return rec

    with rasterio.open(base_sc[0].band("B04")) as src:
        crs = src.crs
    to_utm = Transformer.from_crs(4326, crs, always_xy=True)
    to_ll = Transformer.from_crs(crs, 4326, always_xy=True)
    g_utm = shapely.transform(geom, lambda xy: np.column_stack(to_utm.transform(xy[:, 0], xy[:, 1])))
    grid = composite.grid_for(base_sc[0], g_utm.bounds)
    parcel = rasterize([(mapping(g_utm), 1)], out_shape=(grid.height, grid.width),
                       transform=grid.transform, all_touched=False).astype(bool)

    b = composite.build(base_sc, grid, parcel)
    r = composite.build(rec_sc, grid, parcel)
    res = detect(b.ndvi, b.n_clear, r.ndvi, r.n_clear, parcel, s)

    for p in res.patches:
        x, y = grid.transform * (p.col + 0.5, p.row + 0.5)
        p.lon, p.lat = to_ll.transform(x, y)

    img = None
    if b.best_scene and r.best_scene:
        img = f"{pid}.png"
        loss = res.loss_mask if res.loss_mask is not None else np.zeros_like(parcel)
        report.panel(composite.read_rgb(b.best_scene, grid), composite.read_rgb(r.best_scene, grid),
                     parcel, loss, out / img)

    rec.update(status=res.status, coverage=res.coverage, loss_ha=res.loss_ha, parcel_ha=res.parcel_ha,
               baseline_looks=len(b.scenes), recent_looks=len(r.scenes), image=img,
               before_scene=b.best_scene.id if b.best_scene else None,
               after_scene=r.best_scene.id if r.best_scene else None,
               patches=[{"area_ha": p.area_ha, "mean_drop": p.mean_drop,
                         "lon": round(p.lon, 6), "lat": round(p.lat, 6)} for p in res.patches])
    print(f"[{pid}] {res.status}: {res.loss_ha} ha loss, coverage {res.coverage:.0%}", file=sys.stderr)
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("parcels", help="GeoJSON FeatureCollection of parcel polygons (EPSG:4326)")
    ap.add_argument("--out", default="out")
    ap.add_argument("--baseline", type=_window, help="YYYY-MM-DD:YYYY-MM-DD")
    ap.add_argument("--recent", type=_window, help="YYYY-MM-DD:YYYY-MM-DD")
    ap.add_argument("--max-cloud", type=float, default=70.0, help="skip scenes cloudier than this (%%)")
    ap.add_argument("--min-patch-ha", type=float, default=Settings.min_patch_ha)
    ap.add_argument("--min-drop", type=float, default=Settings.min_drop)
    a = ap.parse_args(argv)

    baseline, recent = default_windows(date.today())
    baseline, recent = a.baseline or baseline, a.recent or recent
    s = Settings(min_patch_ha=a.min_patch_ha, min_drop=a.min_drop)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    feats = json.loads(Path(a.parcels).read_text())["features"]
    parcels = [run_parcel(f, baseline, recent, s, out, a.max_cloud) for f in feats]
    run = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
           "baseline": [str(d) for d in baseline], "recent": [str(d) for d in recent]}
    report.write(out, run, parcels)
    n = sum(p["status"] == "alert" for p in parcels)
    print(f"{n} of {len(parcels)} parcels flagged. Report: {out / 'index.html'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
