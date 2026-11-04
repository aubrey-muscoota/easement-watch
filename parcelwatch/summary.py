"""Markdown summary of alerts.json, for the Actions job summary or an issue.

With --alerts-only, exits 1 when at least one parcel is flagged (so a
workflow can open an issue) and prints only the flagged parcels.
"""
from __future__ import annotations

import json
import os
import sys

from .report import STATUS_TEXT


def render(data: dict, alerts_only: bool = False) -> str:
    run_url = ""
    if os.environ.get("GITHUB_RUN_ID"):
        run_url = (f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
                   f"/actions/runs/{os.environ['GITHUB_RUN_ID']}")
    lines = [f"## Parcel change report",
             f"Baseline {data['baseline'][0]} to {data['baseline'][1]}, "
             f"recent {data['recent'][0]} to {data['recent'][1]}.", "",
             "| Parcel | Result | Loss (ha) | Observed |", "|---|---|---|---|"]
    for p in data["parcels"]:
        if alerts_only and p["status"] != "alert":
            continue
        lines.append(f"| {p['name']} | {STATUS_TEXT[p['status']]} | {p['loss_ha']} | {p['coverage']:.0%} |")
        for q in p["patches"][:5] if alerts_only else []:
            lines.append(f"| ↳ patch | {q['area_ha']} ha | near {q['lat']:.5f}, {q['lon']:.5f} | |")
    if run_url:
        lines += ["", f"Pictures and full report: the `parcel-report` artifact on [this run]({run_url})."]
    lines += ["", "_Prototype. Flags are leads to check, not findings. "
              "Contains modified Copernicus Sentinel data._"]
    return "\n".join(lines) + "\n"


def main() -> int:
    data = json.load(open(sys.argv[1]))
    alerts_only = "--alerts-only" in sys.argv
    print(render(data, alerts_only), end="")
    flagged = any(p["status"] == "alert" for p in data["parcels"])
    return 1 if (alerts_only and flagged) else 0


if __name__ == "__main__":
    sys.exit(main())
