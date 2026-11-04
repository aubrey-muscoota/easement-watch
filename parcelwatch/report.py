"""Pictures, JSON and an HTML page for one run."""
from __future__ import annotations

import html
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

SCALE = 3  # upscale 10 m pixels so small parcels are readable


def _outline(mask: np.ndarray) -> np.ndarray:
    return mask & ~ndimage.binary_erosion(mask)


def _up(a: np.ndarray) -> np.ndarray:
    return np.kron(a, np.ones((SCALE, SCALE, 1), dtype=a.dtype)) if a.ndim == 3 else \
        np.kron(a, np.ones((SCALE, SCALE), dtype=a.dtype))


def _stretch(rgb: np.ndarray) -> np.ndarray:
    """Light contrast stretch so TCI images are not too dark."""
    a = rgb.astype("float32")
    hi = np.percentile(a[a > 0], 99) if (a > 0).any() else 255
    return np.clip(a * (235.0 / max(hi, 1)), 0, 255).astype("uint8")


def panel(before_rgb, after_rgb, parcel, loss, path: Path) -> None:
    """Before | after | after with loss in red. Parcel boundary in yellow."""
    edge = _up(_outline(parcel).astype("uint8")).astype(bool)
    edge = ndimage.binary_dilation(edge)
    imgs = []
    for rgb in (before_rgb, after_rgb, after_rgb):
        imgs.append(_up(_stretch(rgb)))
    grey = imgs[2].mean(axis=2, keepdims=True).astype("uint8").repeat(3, axis=2)
    lossu = _up(loss.astype("uint8")).astype(bool)
    grey[lossu] = (0.35 * grey[lossu] + 0.65 * np.array([235, 40, 40])).astype("uint8")
    imgs[2] = grey
    for im in imgs:
        im[edge] = (255, 220, 0)
    gap = np.full((imgs[0].shape[0], 6, 3), 255, "uint8")
    Image.fromarray(np.hstack([imgs[0], gap, imgs[1], gap, imgs[2]])).save(path, optimize=True)


STATUS_TEXT = {
    "alert": "Possible vegetation loss",
    "no_change": "No change found",
    "no_data": "Not enough clear imagery",
}


def write(out: Path, run: dict, parcels: list[dict]) -> None:
    (out / "alerts.json").write_text(json.dumps({**run, "parcels": parcels}, indent=2, default=str))

    rows, sections = [], []
    for p in parcels:
        name = html.escape(p["name"])
        rows.append(
            f"<tr class='{p['status']}'><td>{name}</td><td>{STATUS_TEXT[p['status']]}</td>"
            f"<td>{p['loss_ha']}</td><td>{p['parcel_ha']}</td><td>{p['coverage']:.0%}</td></tr>")
        lis = "".join(
            f"<li>{q['area_ha']} ha, greenness down {abs(q['mean_drop']):.2f}, near "
            f"<a href='https://www.openstreetmap.org/?mlat={q['lat']:.5f}&mlon={q['lon']:.5f}#map=16/{q['lat']:.5f}/{q['lon']:.5f}'>"
            f"{q['lat']:.5f}, {q['lon']:.5f}</a></li>" for q in p["patches"][:10])
        img = f"<img src='{html.escape(p['image'])}' alt='Before, after and change for {name}'>" if p.get("image") else ""
        sections.append(f"""
<section><h2>{name}</h2>
<p class='meta'>Tile {p['tile']} · before image {p.get('before_scene') or 'none'} · after image {p.get('after_scene') or 'none'}
· {p['baseline_looks']} baseline and {p['recent_looks']} recent scenes</p>
{img}
<p class='legend'>Left: before. Middle: after. Right: after, with possible loss in red. Yellow line: parcel boundary.</p>
{'<ul>' + lis + '</ul>' if lis else ''}
</section>""")

    page = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Parcel change report</title>
<style>
body{{font:15px/1.5 system-ui,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#1d1d1b;background:#fff}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left}}
tr.alert td:nth-child(2){{color:#b3261e;font-weight:600}}tr.no_data td:nth-child(2){{color:#8a6d00}}
img{{max-width:100%;height:auto;border:1px solid #ddd}}.meta,.legend,footer{{color:#666;font-size:13px}}
section{{margin-top:32px}}
</style></head><body>
<h1>Parcel change report</h1>
<p>Baseline {run['baseline'][0]} to {run['baseline'][1]} compared with {run['recent'][0]} to {run['recent'][1]}.
Generated {run['generated']}.</p>
<table><tr><th>Parcel</th><th>Result</th><th>Loss (ha)</th><th>Parcel (ha)</th><th>Observed</th></tr>{''.join(rows)}</table>
{''.join(sections)}
<footer><p>Prototype. Flags are leads for a site visit or a closer look, not findings. Sentinel-2 pixels are 10 m,
so small structures, selective cutting and changes under cloud can be missed, and farming or drought can cause false flags.</p>
<p>Contains modified Copernicus Sentinel data {run['recent'][1][:4]}.</p></footer>
</body></html>"""
    (out / "index.html").write_text(page)
