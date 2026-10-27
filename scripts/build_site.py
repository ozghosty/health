"""Build the static dashboard into _site/: the files in site/ plus data.json, which
combines the public parts of config.yaml with the recent days from data/health.json.

    python scripts/build_site.py
    python -m http.server -d _site 8000      # preview at http://localhost:8000
"""

import datetime as dt
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_site"


def repo_slug():
    """The repo the copy button points at: the one the Action runs in, so forks point at themselves."""
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, cwd=ROOT).stdout
    m = re.search(r"github\.com[/:]([^/]+/[^/.\s]+)", url)
    return m.group(1) if m else ""


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    data_file = ROOT / "data" / "health.json"
    data = json.loads(data_file.read_text(encoding="utf-8")) if data_file.exists() else {"days": []}

    history = cfg.get("dashboard", {}).get("history_days", 365)
    today = dt.datetime.now(ZoneInfo(cfg["profile"].get("timezone", "UTC"))).date()
    cutoff = (today - dt.timedelta(days=history)).isoformat()
    # BMI follows the height in config.yaml, so changing it fixes every day without a re-fetch
    height = cfg["profile"].get("height_cm")
    for d in data.get("days", []):
        if d.get("weightKg") and height:
            d["bmi"] = round(d["weightKg"] / (height / 100) ** 2, 1)
    sources = cfg.get("sources", {})
    out = {
        "updated": data.get("updated"),
        "profile": cfg.get("profile", {}),
        "goals": cfg.get("goals", {}),
        "site": {**cfg.get("site", {}), "repo": repo_slug()},
        "dashboard": cfg.get("dashboard", {}),
        "blood_pressure": cfg.get("blood_pressure", {}),
        "sleep": cfg.get("sleep", {}),
        "thresholds": cfg.get("thresholds", {}),
        "sources": [name for name, s in sources.items() if s.get("enabled")],
        "days": [d for d in data.get("days", []) if d["date"] >= cutoff],
    }

    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(ROOT / "site", OUT)
    (OUT / "data.json").write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"Built {OUT.relative_to(ROOT)}/ with {len(out['days'])} day(s)")


if __name__ == "__main__":
    main()
