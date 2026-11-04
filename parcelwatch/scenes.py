"""Find Sentinel-2 L2A scenes in the public AWS sentinel-cogs bucket.

Lists the bucket directly instead of calling a STAC API, so it needs no
account and no API key. Layout:
  sentinel-s2-l2a-cogs/{zone}/{band}/{square}/{yyyy}/{m}/{scene_id}/
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from datetime import date

import mgrs

BUCKET = "https://sentinel-cogs.s3.us-west-2.amazonaws.com"
PREFIX = "sentinel-s2-l2a-cogs"


@dataclass
class Scene:
    id: str
    date: date
    cloud_cover: float
    nodata_pct: float
    base_url: str

    def band(self, name: str) -> str:
        return f"{self.base_url}/{name}.tif"


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def tile_for(lon: float, lat: float) -> str:
    """MGRS 100 km tile (for example 18TWL) that contains a point."""
    return mgrs.MGRS().toMGRS(lat, lon, MGRSPrecision=0)


def _months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def list_scenes(tile: str, start: date, end: date, max_cloud: float = 70.0) -> list[Scene]:
    zone, band, square = tile[:-3], tile[-3], tile[-2:]
    scenes = []
    for y, m in _months(start, end):
        prefix = f"{PREFIX}/{zone}/{band}/{square}/{y}/{m}/"
        xml = _get(f"{BUCKET}/?list-type=2&delimiter=/&prefix={prefix}").decode()
        for scene_prefix in re.findall(r"<Prefix>([^<]+/)</Prefix>", xml):
            if scene_prefix == prefix:
                continue
            sid = scene_prefix.rstrip("/").rsplit("/", 1)[-1]
            d = date(int(sid[10:14]), int(sid[14:16]), int(sid[16:18]))
            if not (start <= d <= end):
                continue
            base = f"{BUCKET}/{scene_prefix.rstrip('/')}"
            props = json.loads(_get(f"{base}/{sid}.json"))["properties"]
            cc = float(props.get("eo:cloud_cover", 100))
            nd = float(props.get("s2:nodata_pixel_percentage", 0))
            if cc <= max_cloud and nd < 95:
                scenes.append(Scene(sid, d, cc, nd, base))
    # The bucket sometimes holds reprocessed copies (_0_ and _1_) of the
    # same acquisition; keep the latest processing for each date+satellite.
    best: dict[tuple, Scene] = {}
    for s in sorted(scenes, key=lambda s: s.id):
        best[(s.id[:3], s.date)] = s
    return sorted(best.values(), key=lambda s: s.date)
