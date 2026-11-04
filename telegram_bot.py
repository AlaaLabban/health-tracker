#!/usr/bin/env python3
"""Telegram front-end for the health log. Standard library only.

One card a day, edited in place, instead of a long daily form:
  👌 Normal day   – every symptom none, saved in one tap
  🤒 Symptoms     – a tap grid, None/Mild/Moderate/Severe per symptom
  then the one-tap medicine (tablets) and a row per daily factor.
What the card asks comes from tracker.json (see tracker.py).
The card goes out at ASK_AT; if the day still has no symptom rows the next
morning, it is sent again at CHASE_AT. Buttons carry their date, so a late
tap still lands on the right day.

Notes are written ONLY from text typed after "note" (or /note), verbatim.

Days run 04:00 to 04:00, so a drink at 1 AM counts towards the evening before.
A note can be undone from the reply's ↩️ button or /undo.

Config comes from the environment (see bot.env.example).
"""
import http.client
import json
import os
import re
import socket
import sqlite3
import ssl
import sys
import queue
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tracker

HERE = Path(__file__).parent
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.environ.get("CHAT_ID") or 0)
DB_PATH = os.environ.get("DB_PATH", str(HERE / "health.db"))
ASK_AT = os.environ.get("ASK_AT", "22:00")
CHASE_AT = os.environ.get("CHASE_AT", "10:00")          # the next morning
DAY_STARTS_AT = 4                       # hour; earlier than this still counts as the previous day
TIMING = os.environ.get("TIMING") == "1"     # log each Telegram call's round trip
LOCAL_TZ = timezone(timedelta(hours=int(os.environ.get("UTC_OFFSET_HOURS", "3"))))

# Some home connections reset IPv6 connections to Telegram, and a reset during the TLS handshake doesn't
# fall back to IPv4 on its own. FORCE_IPV4=1 resolves IPv4 addresses only.
if os.environ.get("FORCE_IPV4") == "1":
    _getaddrinfo = socket.getaddrinfo
    socket.getaddrinfo = lambda host, port, family=0, *a, **k: _getaddrinfo(host, port, socket.AF_INET, *a, **k)

CFG = tracker.load()
SYMPTOMS = [(s["name"], s["icon"]) for s in CFG["symptoms"]]
SEVERITY = tracker.SEVERITY
QUICK = CFG["quick_med"]                 # the medicine with its own tablet row on the card
QUICK_NAME, QUICK_MG = QUICK["name"], QUICK["mg_per_tablet"]
# Day-card factor rows: key, icon, title, button labels in scale order. The card shows (and, once the day
# is touched, saves) each row's default until another choice is tapped: the first value, unless the config
# sets "default". Pick defaults with care: an untouched row is saved as shown, so a default of "Awful"
# sleep would skew any sleep comparison.
FACTORS = [(f["key"], f["icon"], f["title"], tracker.labels(f)) for f in CFG["factors"]]
FACTOR_CFG = {f["key"]: f for f in CFG["factors"]}



def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- database

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db():
    con = db()
    con.executescript((HERE / "schema.sql").read_text(encoding="utf-8"))
    tracker.write_scales(con, CFG)
    con.commit()
    con.close()


def log_day(now=None):
    now = now or datetime.now(LOCAL_TZ)
    return (now - timedelta(hours=DAY_STARTS_AT)).date().isoformat()


def is_logged(con, day):
    return con.execute("SELECT 1 FROM symptoms WHERE date = ? LIMIT 1", (day,)).fetchone() is not None


def new_batch(con):
    return con.execute("INSERT INTO bot_batches (at) VALUES (datetime('now'))").lastrowid


def add(con, batch, table, row):
    cols = ", ".join(row)
    rid = con.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' * len(row))})",
                      list(row.values())).lastrowid
    con.execute("INSERT INTO bot_rows (batch, tbl, rid) VALUES (?, ?, ?)", (batch, table, rid))
    return rid


def undo(con, batch):
    # newest first, so a drink's intake row goes before the caffeine_items row it references
    rows = con.execute("SELECT tbl, rid FROM bot_rows WHERE batch = ? ORDER BY rowid DESC", (batch,)).fetchall()
    for r in rows:
        con.execute(f"DELETE FROM {r['tbl']} WHERE rowid = ?", (r["rid"],))
    # the bot_batches row stays, so its id is never handed out again and an old Undo button can't hit a newer save
    con.execute("DELETE FROM bot_rows WHERE batch = ?", (batch,))
    return len(rows)


def scale(con, factor):
    return [r["value"] for r in con.execute(
        "SELECT value FROM scales WHERE factor = ? AND legacy = 0 ORDER BY score", (factor,))]


def score_of(con, factor, value):
    r = con.execute("SELECT score FROM scales WHERE factor = ? AND value = ?", (factor, value)).fetchone()
    return None if r is None else r["score"]


def state_get(con, key):
    r = con.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
    return None if r is None else r["value"]


def state_set(con, key, value):
    con.execute("INSERT INTO bot_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


# ---------------------------------------------------------------- telegram

_local = threading.local()     # one kept-alive connection per thread


def tg(method, **params):
    """One Telegram API call over a kept-alive HTTPS connection. A fresh TLS connection from the home
    line takes 0.25-3.6 s and is sometimes reset; a reused one answers in ~0.09 s (measured 2026-11-03)."""
    body = urllib.parse.urlencode({k: json.dumps(v) if isinstance(v, (dict, list)) else v
                                   for k, v in params.items()})
    timeout = int(params.get("timeout", 0)) + 15
    for attempt in range(3):
        try:
            conn = getattr(_local, "conn", None)
            if conn is None:
                conn = _local.conn = http.client.HTTPSConnection("api.telegram.org", context=ssl.create_default_context())
            conn.timeout = timeout
            if conn.sock is not None:
                conn.sock.settimeout(timeout)
            conn.request("POST", f"/bot{TOKEN}/{method}", body,
                          {"Content-Type": "application/x-www-form-urlencoded"})
            t0 = time.time()
            out = json.load(conn.getresponse())   # a 400 carries its reason in the JSON body too
            if TIMING and method != "getUpdates":
                log(f"  {method}: {(time.time() - t0) * 1000:.0f} ms")
            break
        except (OSError, http.client.HTTPException):
            if getattr(_local, "conn", None) is not None:
                _local.conn.close()
            _local.conn = None
            if attempt == 2:
                raise
            time.sleep(0.3)
    if not out.get("ok"):
        raise RuntimeError(f"{method}: {out}")
    return out["result"]


def send(text, buttons=None):
    params = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": buttons}
    return tg("sendMessage", **params)


def edit(msg_id, text, buttons=None):
    params = {"chat_id": CHAT_ID, "message_id": msg_id, "text": text, "parse_mode": "HTML",
              "reply_markup": {"inline_keyboard": buttons or []}}
    try:
        tg("editMessageText", **params)
    except Exception as exc:          # "message is not modified" when a tap changes nothing
        if "not modified" not in str(exc):
            raise


def btn(label, data):
    return {"text": label, "callback_data": data}


def pretty(day):
    return datetime.strptime(day, "%Y-%m-%d").strftime("%a %-d %b")


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- the day card
# One message per day, edited in place. The main view shows what is logged and a button per
# question; a question's view shows its answers as full-word buttons (✅ on the current one).
# Each tap is saved at once, so there is no Save button and nothing to lose; tap again to change.

def options(con, factor):
    return tracker.options(FACTOR_CFG[factor])


def day_state(con, day):
    sym = {name: None for name, _ in SYMPTOMS}
    for r in con.execute("SELECT symptom, MAX(severity) AS v FROM symptoms WHERE date = ? GROUP BY symptom", (day,)):
        sym[r["symptom"]] = r["v"]
    tabs = con.execute("SELECT SUM(amount) AS n FROM meds WHERE date = ? AND name = ?", (day, QUICK_NAME)).fetchone()["n"]
    fac = {}
    for factor, *_ in FACTORS:     # a Telegram answer wins over an imported one for the same day
        r = con.execute("SELECT value FROM factors WHERE date = ? AND factor = ? ORDER BY source = 'telegram' DESC LIMIT 1",
                        (day, factor)).fetchone()
        fac[factor] = r["value"] if r else None
    drinks = con.execute("SELECT said, mg FROM caffeine_intake WHERE date = ? ORDER BY time", (day,)).fetchall()
    notes = [n["text"] for n in con.execute("SELECT text FROM notes WHERE date = ?", (day,))]
    return sym, int(tabs or 0), fac, drinks, notes


def green(text, data, on):
    b = btn(text, data)
    if on:
        b["style"] = "success"      # Bot API button colour; clients without it just show a plain button
    return b


def default_of(con, factor):
    return tracker.default(FACTOR_CFG[factor])


def factor_value(con, fac, factor):
    return fac[factor] or default_of(con, factor)


def label(text):
    """A full-width row naming the choices underneath it; tapping it does nothing."""
    return [btn(text, "x")]


def card(con, day, view="main", lead=None):
    """Main view: Normal day / Symptoms, then the one-tap medicine and the factor rows, each as a label row with
    its choices on the row below. Symptoms view: the same shape, one pair of rows per symptom.
    Green = what is saved (or the default, until a tap)."""
    sym, tabs, fac, drinks, notes = day_state(con, day)
    logged = any(v is not None for v in sym.values())
    if view == "sym":
        rows = []
        for i, (name, icon) in enumerate(SYMPTOMS):
            current = sym[name] or 0
            rows.append(label(f"{icon} {name}"))
            rows.append([green(lab, f"v|{day}|s{i}|{v}", current == v) for v, lab in enumerate(SEVERITY)])
        rows.append([green("✅ Done", f"v|{day}|done|0", True)])
        return f"🤒 <b>Symptoms — {pretty(day)}</b>\nEach tap saves.", rows

    lines = [f"📋 <b>{lead or pretty(day)}</b>"]
    if not logged:
        lines.append("🤒 Symptoms: <i>not logged yet</i>")
    elif not any(sym.values()):
        lines.append("👌 No symptoms")
    else:
        lines.append(" · ".join(f"{icon} {name} <b>{SEVERITY[sym[name]].lower()}</b>" for name, icon in SYMPTOMS if sym[name]))
    if drinks:
        lines.append(f"☕ <b>{sum(d['mg'] for d in drinks):.0f} mg</b> — " + ", ".join(esc(d["said"]) for d in drinks))
    for n in notes:
        lines.append(f"📝 {esc(n)}")
    rows = [[green("👌 Normal day", f"v|{day}|nd|0", logged and not any(sym.values())),
             green("🤒 Symptoms", f"c|{day}|sym", logged and any(sym.values()))],
            label(f"{QUICK['icon']} {QUICK['label']} (tap again to clear)"),
            [green(str(n), f"v|{day}|med|{0 if tabs == n else n}", tabs == n) for n in range(1, QUICK["max_tablets"] + 1)]]
    for k, (factor, icon, title, labels) in enumerate(FACTORS):
        current = factor_value(con, fac, factor)
        rows.append(label(f"{icon} {title}"))
        rows.append([green(lab, f"v|{day}|f{k}|{j}", options(con, factor)[j] == current) for j, lab in enumerate(labels)])
    return "\n".join(lines), rows


def fill_defaults(con, day):
    """The card shows each factor row's default as chosen; once the day is touched, save it as shown."""
    for factor, *_ in FACTORS:
        if con.execute("SELECT 1 FROM factors WHERE date = ? AND factor = ?", (day, factor)).fetchone() is None:
            value = default_of(con, factor)
            if value != "No":
                con.execute("INSERT INTO factors (date, factor, value, score, source) VALUES (?, ?, ?, ?, 'telegram')",
                            (day, factor, value, score_of(con, factor, value)))


def ensure_symptoms(con, day):
    """First symptom answer of the day: write every configured symptom as none, so the day counts as fully logged."""
    if con.execute("SELECT 1 FROM symptoms WHERE date = ? AND source = 'telegram' LIMIT 1", (day,)).fetchone() is None:
        con.execute("INSERT OR IGNORE INTO checkins VALUES (?, 'telegram')", (day,))
        for name, _ in SYMPTOMS:
            con.execute("INSERT INTO symptoms (date, symptom, severity, source) VALUES (?, ?, 0, 'telegram')", (day, name))


def set_value(con, day, field, v):
    """Save one tap; returns the view to show next."""
    fill_defaults(con, day)
    if field == "nd":                       # Normal day: every symptom none, stay on the card
        ensure_symptoms(con, day)
        con.execute("UPDATE symptoms SET severity = 0 WHERE date = ? AND source = 'telegram'", (day,))
        return "main"
    if field == "done":                     # leaving the symptoms view saves what it showed
        ensure_symptoms(con, day)
        return "main"
    if field.startswith("s"):
        ensure_symptoms(con, day)
        con.execute("UPDATE symptoms SET severity = ? WHERE date = ? AND source = 'telegram' AND symptom = ?",
                    (v, day, SYMPTOMS[int(field[1:])][0]))
        return "sym"
    if field == "med":
        con.execute("DELETE FROM meds WHERE date = ? AND source = 'telegram' AND name = ?", (day, QUICK_NAME))
        if v:
            con.execute("INSERT INTO meds (date, time, name, amount, unit, dose_mg, source) "
                        "VALUES (?, ?, ?, ?, 'tablets', ?, 'telegram')",
                        (day, datetime.now(LOCAL_TZ).strftime("%H:%M"), QUICK_NAME, v, v * QUICK_MG))
        return "main"
    factor = FACTORS[int(field[1:])][0]
    value = options(con, factor)[v]
    con.execute("DELETE FROM factors WHERE date = ? AND factor = ? AND source = 'telegram'", (day, factor))
    if value != "No":
        con.execute("INSERT INTO factors (date, factor, value, score, source) VALUES (?, ?, ?, ?, 'telegram')",
                    (day, factor, value, score_of(con, factor, value)))
    return "main"


def send_card(con, day, lead=None):
    """Send a fresh card and remember its message id, so every card sent for a day can be redrawn."""
    m = send(*card(con, day, lead=lead))
    ids = json.loads(state_get(con, f"cards:{day}") or "[]")[-5:] + [m["message_id"]]
    state_set(con, f"cards:{day}", json.dumps(ids))
    con.commit()
    return m


def undo_button(batch):
    return [[btn("↩️ Undo", f"u|{batch}")]]


ACKS = queue.Queue()


def ack_worker():
    """Answers button taps from its own thread and its own kept-alive connection, so the
    acknowledgement and the card redraw travel to Telegram at the same time."""
    try:
        tg("getMe")         # open the connection now; a cold TLS handshake made the first tap after a restart take 1-2 s
    except Exception as exc:
        log(f"ack worker warm-up failed: {exc}")
    while True:
        cq_id = ACKS.get()
        try:
            tg("answerCallbackQuery", callback_query_id=cq_id)
        except Exception as exc:
            log(f"answerCallbackQuery failed: {exc}")


def on_callback(con, cq):
    data, msg_id = cq.get("data", ""), cq["message"]["message_id"]
    parts = data.split("|")
    kind = parts[0]
    # Acknowledge on a side thread, in parallel with the redraw: the Telegram app ignores further taps on
    # the message until this arrives, and doing it inline added ~120 ms to every tap (measured 2026-11-03).
    ACKS.put(cq["id"])
    if kind == "c":
        edit(msg_id, *card(con, parts[1], parts[2]))
    elif kind == "v":
        view = set_value(con, parts[1], parts[2], int(parts[3]))
        con.commit()
        edit(msg_id, *card(con, parts[1], view))
    elif kind == "u":
        n = undo(con, int(parts[1]))
        con.commit()
        edit(msg_id, f"↩️ Undone ({n} row{'s' * (n != 1)} removed)." if n else "↩️ Already undone.")


# ---------------------------------------------------------------- typed messages

def on_text(con, text):
    day = log_day()
    low = text.strip().lower()
    if low.startswith("/start") or low.startswith("/help"):
        send("Tap the buttons on the daily message.\n"
             "• <i>note …</i> saves your words as the day's comment, exactly as typed\n"
             "/today or /log — today's card · /undo — undo the last note")
        return
    if low.startswith("/today"):
        show_day(con, day)
        return
    if low.startswith("/log"):
        send_card(con, day)
        return
    if low.startswith("/undo"):
        r = con.execute("SELECT MAX(batch) AS b FROM bot_rows").fetchone()
        if r["b"] is None:
            send("Nothing to undo.")
            return
        n = undo(con, r["b"])
        con.commit()
        send(f"↩️ Undid the last save ({n} row{'s' * (n != 1)}).")
        return
    note = re.match(r"^(?:/note\b|note\b|📝)[:\s-]*(.+)", text.strip(), flags=re.S | re.I)
    if note:
        batch = new_batch(con)
        add(con, batch, "notes", {"date": day, "text": note.group(1).strip(), "source": "telegram"})
        con.commit()
        send(f"📝 Note saved for {pretty(day)}.", undo_button(batch))
        return
    if text.startswith("/"):
        send("Unknown command. /help lists them.")
        return

    send("Use the buttons on the card (/log sends one), or start with <i>note</i> to save a comment for the day.")

def show_day(con, day):
    send_card(con, day)


# ---------------------------------------------------------------- schedule

def due(con, key, at, now):
    """True once per calendar day after the HH:MM in `at`."""
    hh, mm = map(int, at.split(":"))
    today = now.date().isoformat()
    if (now.hour, now.minute) < (hh, mm) or state_get(con, key) == today:
        return False
    state_set(con, key, today)
    con.commit()
    return True


def tick(con):
    now = datetime.now(LOCAL_TZ)
    day = log_day(now)
    if due(con, "asked", ASK_AT, now) and not is_logged(con, day):
        send_card(con, day, lead=f"How was {pretty(day)}?")
    if due(con, "chased", CHASE_AT, now):
        yesterday = (now.date() - timedelta(days=1)).isoformat()
        if not is_logged(con, yesterday):
            send_card(con, yesterday, lead=f"⏰ Still missing: {pretty(yesterday)}")


def main():
    if not TOKEN:
        sys.exit("TELEGRAM_TOKEN is not set")
    init_db()
    threading.Thread(target=ack_worker, daemon=True).start()
    con = db()
    offset = int(state_get(con, "offset") or 0)
    log(f"health bot up: chat={CHAT_ID or 'unset'} ask={ASK_AT} chase={CHASE_AT}")
    while True:
        try:
            if CHAT_ID:
                tick(con)
            updates = tg("getUpdates", offset=offset, timeout=25, allowed_updates=["message", "callback_query"])
        except Exception as exc:
            log(f"poll failed: {exc}")
            time.sleep(2)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            state_set(con, "offset", str(offset))
            con.commit()
            msg = u.get("message") or (u.get("callback_query") or {}).get("message") or {}
            chat = (msg.get("chat") or {}).get("id")
            if not CHAT_ID:
                log(f"message from chat {chat} ignored: CHAT_ID is not set yet")
                continue
            if chat != CHAT_ID:
                log(f"message from unknown chat {chat} ignored")
                continue
            started = time.time()
            what = (u.get("callback_query") or {}).get("data") or "text"
            try:
                if "callback_query" in u:
                    on_callback(con, u["callback_query"])
                elif msg.get("text"):
                    on_text(con, msg["text"])
                log(f"{what}: ok in {time.time() - started:.1f}s")
            except Exception as exc:
                con.rollback()
                log(f"update {u['update_id']} failed: {exc!r}")
                try:
                    send("⚠️ Something went wrong, nothing was saved.")
                except Exception:
                    pass


if __name__ == "__main__":
    main()
