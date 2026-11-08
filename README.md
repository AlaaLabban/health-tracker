# health-tracker

A self-hosted daily health log that is quick enough to keep up with. One Telegram card a day: tap **Normal day** and you are done, or open the symptom grid when something is off. Medicine, sleep, stress, activity and the rest are one tap each, with sensible defaults already selected. Everything lands in one SQLite file on your own server, and a one-page summary for your doctor comes out of it.

What the log tracks is entirely yours to define in `tracker.json`: the symptoms, the medicine you take most, other medicines, and any daily factors with their own scales. The code knows nothing about any particular condition.

## Why it looks like this

Symptom apps usually fail the same way: a long form, mostly unchanged from yesterday, behind a reminder at a fixed time that clashes with something. This log is built against that:

- **One tap for a normal day.** Normal days are what make any comparison possible, so they have to cost nothing.
- **Defaults are shown selected** and saved as shown once the day is touched; you only tap what differs.
- **Reminders escalate instead of firing once:** the card in the evening, a fresh card later if the day is still empty, and optionally **a phone call the next afternoon** through an Asterisk server (see [docs/PHONE.md](docs/PHONE.md)), which stops as soon as the day is logged.
- **Type instead of tapping** when it is easier: "tiny cola zero", "matcha, I used 5 g", "headache 2, took 2 paracetamol". `claude -p` (no tools) turns the words into rows; caffeine mg are computed in code from lookup tables that grow as you log, so the same drink is never estimated twice.

## Features

| Feature | Notes |
|---|---|
| Daily Telegram card, edited in place | Normal day, symptom grid (None/Mild/Moderate/Severe), tablet counter, a row per factor |
| Free text through `claude -p` | Drinks, medicine, symptoms, factors; asks one question when a size cannot be estimated; ↩️ undo |
| Notes | Only from text you type after `note`, saved verbatim, never generated |
| Telegram Mini App (optional) | Taps change colour instantly on the phone; one Save sends the day. Requests are checked against Telegram's signed `initData` and your user id |
| Escalating reminders | Card, fresh card, then an optional phone call the next day |
| Bearable import | `import_bearable.py` loads a Bearable CSV export; re-running replaces only its own rows |
| Doctor summary | `make_summary.py`: one A4 page with a day-by-day calendar, monthly and per-symptom tables |

## Quick start

```bash
cp tracker.example.json tracker.json      # edit what you track
cp bot.env.example bot.env && chmod 600 bot.env
./set-token.sh                            # the BotFather token, not echoed
python3 telegram_bot.py                   # or install health-bot.service
```

Send the bot `/start`, read your chat id from the log, put it in `bot.env` as `CHAT_ID`, restart. The card arrives at `ASK_AT`; `/log` sends it now.

Free text needs the Claude Code CLI on the host, logged in under its own `CLAUDE_CONFIG_DIR`. The bot refuses any account but `ALLOWED_EMAIL` and scrubs `ANTHROPIC_*` variables, so it never bills an API key.

```bash
python3 import_bearable.py bearable-export.csv          # optional
python3 make_summary.py health.db summary.html --name "Your Name"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
  --no-pdf-header-footer --print-to-pdf=summary.pdf summary.html
```

## Files

```
tracker.example.json   what is tracked: symptoms, medicines, factors (copy to tracker.json)
tracker.py             loads the config; every front-end builds itself from it
schema.sql             tables, the scales table and the `daily` view
telegram_bot.py        the bot, the Mini App API and the reminder schedule
webapp.html            the Mini App page
import_bearable.py     Bearable CSV import
make_summary.py        one-page doctor summary
docs/PHONE.md          the optional phone-call reminder
```

## Your data stays yours

The database, `tracker.json`, `bot.env` and any exports are git-ignored. Run `git config core.hooksPath .githooks` once so `scripts/check-secrets.sh` checks every commit for tokens, addresses and paths.

Everything you log is health data. Keep the server and its backups private, and remember that Telegram bot chats are not end-to-end encrypted.

## License

MIT, see [LICENSE](LICENSE).
