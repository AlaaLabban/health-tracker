#!/usr/bin/env python3
"""Import a Bearable CSV export into the health log (SQLite).

Usage: python3 import_bearable.py bearable-export-DD-MM-YYYY.csv [health.db]

Re-running is safe: every row this script writes is tagged source='bearable' and those rows are replaced
wholesale, so entries from other sources (telegram, manual) are never touched.

Bearable layout notes (learned from a real export):
- Symptoms are logged per time slot: pre (night), am, mid, pm, scored 0 None / 1 Mild / 2 Moderate /
  3 Severe. They are imported under Bearable's own names; list the same names in tracker.json if you
  want the bot to keep logging them.
- Heart rate is one row per day as "min/avg/max".
- Lifestyle and Active rows pack several factors into one string joined by " | ",
  e.g. "☕ Caffeine - Little | 💣 Stress - None".
- Sleep rows are synced placeholders with no duration; only the separate "Sleep quality" row
  (1 Awful .. 5 Great) carries data.
- A day counts as checked in only if it has Symptom rows. Synced steps and heart rate keep arriving after
  logging stops, so they must not count as a check-in.
"""
import csv
import re
import sqlite3
import sys
from pathlib import Path

import tracker

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

# Bearable's own word scales
ORDINAL = {
    "caffeine": ["None", "Little", "Moderate", "A lot"],
    "stress": ["None", "Little", "Moderate", "A lot"],
    "activity": ["Sedentary", "Low", "Moderate", "High", "Very high"],
}
FACTOR_NAMES = {"Caffeine": "caffeine", "Stress": "stress", "Activity level": "activity"}


def parse_factor(part, fasted_scores):
    """'☕ Caffeine - Little' -> ('caffeine', 'Little', 1); 'Fasted (X)' -> ('fasted', 'X', score or None)."""
    part = re.sub(r"^\W+", "", part.strip())
    if part.startswith("Fasted"):
        kind = re.search(r"\((.*)\)", part).group(1)
        return "fasted", kind, fasted_scores.get(kind)
    name, _, value = part.partition(" - ")
    key = FACTOR_NAMES.get(name.strip())
    if key is None:
        raise ValueError(f"unknown factor: {part!r}")
    value = value.strip()
    return key, value, ORDINAL[key].index(value)


def main(csv_path, db_path):
    cfg = tracker.load()
    fasted = next((f for f in cfg["factors"] if f["key"] == "fasted"), {"values": []})
    fasted_scores = {v["value"]: v["score"] for v in fasted["values"]}
    db = sqlite3.connect(db_path)
    db.executescript(SCHEMA)
    tracker.write_scales(db, cfg)
    for table in ("checkins", "symptoms", "meds", "factors", "metrics"):
        db.execute(f"DELETE FROM {table} WHERE source = 'bearable'")

    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for r in rows:
        date, cat, slot = r["date formatted"], r["category"], r["time of day"] or None
        amount, detail = r["rating/amount"], r["detail"]
        if cat == "Symptom":
            db.execute("INSERT OR IGNORE INTO checkins VALUES (?, 'bearable')", (date,))
            name = re.sub(r" \(.*\)$", "", detail)
            db.execute("INSERT INTO symptoms VALUES (?, ?, ?, ?, 'bearable')",
                       (date, slot, name, int(amount)))
        elif cat == "Meds/Supplements":
            name, unit = re.match(r"(.*) \((.*)\)$", detail).groups()
            per = tracker.med_mg(cfg, name, unit)
            db.execute("INSERT INTO meds VALUES (?, ?, ?, ?, ?, ?, 'bearable')",
                       (date, slot, name, float(amount), unit, float(amount) * per if per else None))
        elif cat in ("Lifestyle", "Active"):
            for part in detail.split("|"):
                db.execute("INSERT INTO factors VALUES (?, ?, ?, ?, 'bearable')",
                           (date, *parse_factor(part, fasted_scores)))
        elif cat == "Sleep quality":
            db.execute("INSERT INTO factors VALUES (?, 'sleep_quality', ?, ?, 'bearable')",
                       (date, detail, int(amount)))
        elif cat == "Health measurements":
            if detail.startswith("Step count"):
                db.execute("INSERT INTO metrics VALUES (?, 'steps', ?, 'bearable')",
                           (date, float(amount)))
            elif detail.startswith("Heart rate"):
                for metric, v in zip(("hr_min", "hr_avg", "hr_max"), amount.split("/")):
                    db.execute("INSERT INTO metrics VALUES (?, ?, ?, 'bearable')",
                               (date, metric, float(v)))
            else:
                raise ValueError(f"unknown measurement: {detail!r}")
        elif cat == "Sleep":
            pass  # synced placeholder, no duration in the export
        else:
            raise ValueError(f"unknown category: {cat!r}")

    db.commit()
    for table in ("checkins", "symptoms", "meds", "factors", "metrics"):
        n = db.execute(f"SELECT COUNT(*) FROM {table} WHERE source = 'bearable'").fetchone()[0]
        print(f"{table:9} {n}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    here = Path(__file__).parent
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else here / "health.db")
