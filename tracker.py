"""What this log tracks, read from tracker.json (falls back to tracker.example.json).

The code knows nothing about any particular condition: the symptoms, the one-tap medicine, the other
medicines and the daily factors all come from the config, and every front-end (the Telegram card, the Mini
App, the free-text parser, the importer and the summary) builds itself from it.

Factor values are stored in the `scales` table on start-up, so the database itself records what every
stored value meant. A factor with "none_label" offers that choice first (e.g. "Not fasted"), and choosing
it stores nothing.
"""
import json
import os
from pathlib import Path

HERE = Path(__file__).parent
SEVERITY = ["None", "Mild", "Moderate", "Severe"]


def load(path=None):
    path = Path(path or os.environ.get("TRACKER_CONFIG") or HERE / "tracker.json")
    if not path.exists():
        path = HERE / "tracker.example.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    for f in cfg["factors"]:
        for i, v in enumerate(f["values"]):
            v.setdefault("score", i)
    return cfg


def options(factor):
    """Values offered for a factor, in order: the stored value of each button."""
    values = [v["value"] for v in factor["values"]]
    return (["No"] + values) if factor.get("none_label") else values


def labels(factor):
    """Button labels, in the same order as options()."""
    names = [v.get("label", v["value"]) for v in factor["values"]]
    return ([factor["none_label"]] + names) if factor.get("none_label") else names


def default(factor):
    return factor.get("default") or options(factor)[0]


def write_scales(con, cfg):
    """Record the configured factor values in `scales`; Bearable's legacy caffeine words are kept."""
    con.execute("DELETE FROM scales WHERE legacy = 0 AND factor <> 'caffeine_mg'")
    for f in cfg["factors"]:
        for v in f["values"]:
            con.execute("INSERT OR REPLACE INTO scales (factor, score, value, meaning, legacy) VALUES (?, ?, ?, ?, 0)",
                        (f["key"], v["score"], v["value"], v.get("meaning")))


def med_mg(cfg, name, unit):
    """mg of active ingredient per logged unit, or None when the medicine or unit is not configured."""
    for m in cfg.get("meds", []):
        if m["name"].lower() == name.lower() and m["unit"] == unit:
            return m["mg_per_unit"]
    return None
