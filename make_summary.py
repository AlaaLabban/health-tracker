#!/usr/bin/env python3
"""Build a one-page summary for a doctor (summary.html) from the health log.

Usage: python3 make_summary.py [health.db] [summary.html] [--name "Patient name"]
Print it to an A4 PDF with any browser, or headless Chrome (see README.md).

Each day is classified by its worst symptom severity:
  severe / moderate / mild - the highest severity of any symptom that day
  clear                    - checked in, every symptom none
  missing                  - no check-in that day
The calendar marks days with the one-tap medicine from tracker.json. A ruled box at the end is for
writing in, by hand, anything that happened after the last logged day.
"""
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta
from html import escape
from pathlib import Path

import tracker

KIND_LABEL = {"severe": "Severe", "moderate": "Moderate", "mild": "Mild",
              "clear": "No symptoms", "missing": "Not logged"}
KIND_OF = {3: "severe", 2: "moderate", 1: "mild", 0: "clear"}


def load(db):
    sym = defaultdict(dict)
    for d, s, v in db.execute("SELECT date, symptom, MAX(severity) FROM symptoms GROUP BY 1, 2"):
        sym[d][s] = v
    meds = defaultdict(list)
    for d, n, a, u, mg in db.execute("SELECT date, name, amount, unit, dose_mg FROM meds ORDER BY date, time"):
        meds[d].append((n, a, u, mg))
    sleep = dict(db.execute("SELECT date, value FROM factors WHERE factor = 'sleep_quality'"))
    checked = {d for (d,) in db.execute("SELECT DISTINCT date FROM checkins")}
    return sym, meds, sleep, checked


def kind(day, sym, checked):
    if day not in checked:
        return "missing"
    return KIND_OF[max(sym.get(day, {}).values(), default=0)]


def fmt(d):
    return date.fromisoformat(d).strftime("%-d %b")


def calendar_svg(start, end, sym, meds, checked, quick):
    cell, gap, left, top = 16, 3, 34, 22
    first_monday = start - timedelta(days=start.weekday())
    weeks = (end - first_monday).days // 7 + 1
    width = left + weeks * (cell + gap) + 4
    height = top + 7 * (cell + gap) + 4
    out = [f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
           'role="img" aria-label="Daily calendar of logged symptoms">']
    for i, name in enumerate("MTWTFSS"):
        if i % 2 == 0:
            y = top + i * (cell + gap) + cell - 4
            out.append(f'<text x="{left - 8}" y="{y}" class="ax" text-anchor="end">{name}</text>')
    seen_month, last_label_x, label_idx = None, -99, None
    d = start
    while d <= end:
        w = (d - first_monday).days // 7
        x, y = left + w * (cell + gap), top + d.weekday() * (cell + gap)
        if d.month != seen_month:
            if x - last_label_x < 30 and label_idx is not None:  # collision: the later, fuller month wins
                out.pop(label_idx)
            label_idx = len(out)
            out.append(f'<text x="{x}" y="{top - 8}" class="ax">{d.strftime("%b")}</text>')
            last_label_x = x
            seen_month = d.month
        iso = d.isoformat()
        k = kind(iso, sym, checked)
        tip = f"{d.strftime('%a %-d %b')}: {KIND_LABEL[k]}"
        out.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="3" '
                   f'class="c-{k}"><title>{escape(tip)}</title></rect>')
        if any(n == quick for n, *_ in meds.get(iso, [])):
            out.append(f'<circle cx="{x + cell / 2}" cy="{y + cell / 2}" r="3.5" class="pill"/>')
        d += timedelta(days=1)
    out.append("</svg>")
    return "\n".join(out)


def main(db_path, out_path, name=""):
    cfg = tracker.load()
    quick = cfg["quick_med"]["name"]
    db = sqlite3.connect(db_path)
    sym, meds, sleep, checked = load(db)
    if not checked:
        sys.exit("no logged days yet")
    start, end = date.fromisoformat(min(checked)), date.fromisoformat(max(checked))
    days = [(start + timedelta(i)).isoformat() for i in range((end - start).days + 1)]
    kinds = {d: kind(d, sym, checked) for d in days}
    symptom_days = [d for d in days if kinds[d] in ("mild", "moderate", "severe")]
    quick_days = [d for d in days if any(n == quick for n, *_ in meds.get(d, []))]
    missing = [d for d in days if kinds[d] == "missing"]
    names = sorted({s for d in days for s in sym.get(d, {})},
                   key=lambda s: [x["name"] for x in cfg["symptoms"]].index(s)
                   if s in [x["name"] for x in cfg["symptoms"]] else 99)

    per_symptom = []
    for s in names:
        ds = [d for d in days if sym.get(d, {}).get(s, 0) > 0]
        if ds:
            worst = max(sym[d][s] for d in ds)
            per_symptom.append((s, len(ds), tracker.SEVERITY[worst].lower(), fmt(ds[0]), fmt(ds[-1])))

    months = []
    for m in sorted({d[:7] for d in days}):
        md = [d for d in days if d[:7] == m]
        months.append((date.fromisoformat(m + "-01").strftime("%b %Y"),
                       sum(d in checked for d in md),
                       sum(d in symptom_days for d in md),
                       sum(kinds[d] == "severe" for d in md),
                       sum(d in quick_days for d in md)))

    sym_sleep = [sleep[d] for d in symptom_days if d in sleep]
    rest_sleep = [sleep[d] for d in days if d in checked and d not in symptom_days and d in sleep]
    bad = ("Poor", "Awful")

    other_meds = defaultdict(int)
    for d in days:
        for n, *_ in meds.get(d, []):
            if n != quick:
                other_meds[n] += 1

    doses = sorted({mg for d in quick_days for n, _, _, mg in meds[d] if n == quick and mg})
    sym_rows = "\n".join(f"<tr><td>{escape(s)}</td><td>{n}</td><td>{w}</td><td>{a} – {b}</td></tr>"
                         for s, n, w, a, b in per_symptom) or '<tr><td colspan="4">No symptoms logged.</td></tr>'
    month_rows = "\n".join(f"<tr><td>{m}</td><td>{n}</td><td>{s}</td><td>{sv}</td><td>{q}</td></tr>"
                           for m, n, s, sv, q in months)
    svg = calendar_svg(start, end, sym, meds, checked, quick)
    legend = "".join(f'<span><i class="sw c-{k}"></i>{v}</span>' for k, v in KIND_LABEL.items())
    who = f"{escape(name)} · " if name else ""
    other = ", ".join(f"{escape(n)} ({c} days)" for n, c in sorted(other_meds.items())) or "none logged"
    sleep_line = (f"<li>Sleep rated Poor/Awful on {sum(s in bad for s in sym_sleep)} of {len(sym_sleep)} symptom days "
                  f"vs {sum(s in bad for s in rest_sleep)} of {len(rest_sleep)} other logged days.</li>"
                  if sym_sleep and rest_sleep else "")

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(cfg.get("title", "Health log"))} summary</title>
<style>
:root {{
  --surface: #ffffff; --ink: #0b0b0b; --ink-2: #52514e; --ink-3: #8a8983; --rule: #e4e3df;
  --severe: #c2410c; --moderate: #eb6834; --mild: #f4b183; --clear: #e9e8e4;
}}
@page {{ size: A4; margin: 12mm 14mm; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--surface); color: var(--ink);
  font: 10pt/1.38 -apple-system, "Helvetica Neue", Arial, sans-serif; }}
main {{ max-width: 760px; margin: 0 auto; padding: 16px; }}
h1 {{ font-size: 17pt; margin: 0 0 2px; letter-spacing: -0.01em; }}
h2 {{ font-size: 10pt; text-transform: uppercase; letter-spacing: .06em; color: var(--ink-2);
  margin: 12px 0 5px; padding-bottom: 3px; border-bottom: 1px solid var(--rule); }}
.meta {{ color: var(--ink-2); font-size: 9.5pt; }}
ul {{ margin: 0; padding-left: 18px; }} li {{ margin: 2px 0; }}
table {{ border-collapse: collapse; width: 100%; font-size: 9.5pt; font-variant-numeric: tabular-nums; }}
th, td {{ text-align: right; padding: 3px 8px; border-bottom: 1px solid var(--rule); }}
th:first-child, td:first-child {{ text-align: left; }}
th {{ color: var(--ink-2); font-weight: 600; }}
.cal {{ overflow-x: auto; }}
.ax {{ font-size: 9px; fill: var(--ink-2); }}
.c-severe {{ fill: var(--severe); }} .c-moderate {{ fill: var(--moderate); }}
.c-mild {{ fill: var(--mild); }} .c-clear {{ fill: var(--clear); }}
.c-missing {{ fill: none; stroke: var(--ink-3); stroke-dasharray: 2 2; }}
.pill {{ fill: var(--ink); stroke: var(--surface); stroke-width: 1.5; }}
.legend {{ display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 9pt; color: var(--ink-2); margin-top: 4px; }}
.legend span {{ display: inline-flex; align-items: center; gap: 5px; }}
.sw {{ width: 11px; height: 11px; border-radius: 3px; display: inline-block; }}
.sw.c-severe {{ background: var(--severe); }} .sw.c-moderate {{ background: var(--moderate); }}
.sw.c-mild {{ background: var(--mild); }} .sw.c-clear {{ background: var(--clear); }}
.sw.c-missing {{ border: 1px dashed var(--ink-3); }}
.dot {{ width: 7px; height: 7px; border-radius: 50%; background: var(--ink); display: inline-block; }}
.fill {{ border-bottom: 1px solid var(--ink-3); height: 20px; }}
.two {{ display: grid; grid-template-columns: auto 1fr; gap: 0 28px; align-items: start; }}
@media (max-width: 640px) {{ .two {{ grid-template-columns: 1fr; }} }}
.note {{ color: var(--ink-2); font-size: 9pt; }}
</style></head><body><main>
<h1>{escape(cfg.get("title", "Health log"))}: summary</h1>
<div class="meta">{who}{fmt(days[0])} {start.year} – {fmt(days[-1])} {end.year} ({len(checked)} of {len(days)} days logged) · Prepared {date.today().strftime("%-d %b %Y")}</div>

<h2>Key points</h2>
<ul>
<li><b>Days with any symptom: {len(symptom_days)}</b> of {len(checked)} logged days
({sum(kinds[d] == "severe" for d in days)} severe, {sum(kinds[d] == "moderate" for d in days)} moderate, {sum(kinds[d] == "mild" for d in days)} mild).</li>
<li><b>{escape(quick)}: taken on {len(quick_days)} days</b>{f" ({', '.join(f'{m:g} mg' for m in doses)} per day)" if doses else ""};
never more than {max((q for *_, q in months), default=0)} days in one month.</li>
<li>Other medicines: {other}.</li>
{sleep_line}
</ul>

<div class="two"><div>
<h2>Day by day</h2>
<div class="cal">{svg}</div>
</div><div>
<h2>By month</h2>
<table><thead><tr><th>Month</th><th>Logged</th><th>Symptoms</th><th>Severe</th><th>{escape(quick)}</th></tr></thead>
<tbody>{month_rows}</tbody></table>
</div></div>
<div class="legend">{legend}<span><i class="dot"></i>{escape(quick)} taken</span></div>
<p class="note">Days not logged: {", ".join(fmt(d) for d in missing) or "none"}.</p>

<h2>By symptom</h2>
<table><thead><tr><th>Symptom</th><th>Days</th><th>Worst</th><th>First – last</th></tr></thead>
<tbody>{sym_rows}</tbody></table>

<h2>Since {fmt(days[-1])} (not logged, fill in by hand)</h2>
<div class="fill"></div><div class="fill"></div>
</main></body></html>
"""
    Path(out_path).write_text(html, encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    args = sys.argv[1:]
    name = ""
    if "--name" in args:
        i = args.index("--name")
        name = args[i + 1]
        del args[i:i + 2]
    here = Path(__file__).parent
    main(args[0] if args else here / "health.db", args[1] if len(args) > 1 else here / "summary.html", name)
