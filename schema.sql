-- Health log schema. Shared by every source (bearable, telegram, manual).
-- Every row carries `source` so one source can be re-imported without touching the others.
-- What is tracked (symptom names, medicines, factors) comes from tracker.json, not from this file.

CREATE TABLE IF NOT EXISTS checkins (
    date   TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (date, source)
);
CREATE TABLE IF NOT EXISTS symptoms (
    date     TEXT NOT NULL,
    slot     TEXT,              -- Bearable only: pre / am / mid / pm
    symptom  TEXT NOT NULL,     -- a name from tracker.json (or whatever an import brought in)
    severity INTEGER NOT NULL,  -- 0 none .. 3 severe
    source   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meds (
    date    TEXT NOT NULL,
    time    TEXT,
    name    TEXT NOT NULL,
    amount  REAL,               -- as logged, in `unit`
    unit    TEXT,
    dose_mg REAL,               -- total active ingredient, e.g. 2 tablets of 500 mg = 1000
    source  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS factors (
    date   TEXT NOT NULL,
    factor TEXT NOT NULL,       -- see the scales table
    value  TEXT NOT NULL,
    score  INTEGER,             -- ordinal position from the scales table
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metrics (
    date   TEXT NOT NULL,
    metric TEXT NOT NULL,       -- steps, hr_min, hr_avg, hr_max
    value  REAL NOT NULL,
    source TEXT NOT NULL
);
-- Free-text comment for a special day. Written ONLY from words the owner typed after "note";
-- never generated or inferred.
CREATE TABLE IF NOT EXISTS notes (
    date     TEXT NOT NULL,
    text     TEXT NOT NULL,
    source   TEXT NOT NULL,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- What each factor value means. Filled from tracker.json on start-up (tracker.write_scales); front-ends
-- build their buttons from the config, and the database keeps the meaning next to the data.
CREATE TABLE IF NOT EXISTS scales (
    factor  TEXT NOT NULL,
    score   INTEGER,
    value   TEXT NOT NULL,
    meaning TEXT,
    legacy  INTEGER NOT NULL DEFAULT 0,   -- 1 = only in old Bearable data, not offered for new entries
    PRIMARY KEY (factor, value)
);
INSERT OR REPLACE INTO scales (factor, score, value, meaning, legacy) VALUES
    ('caffeine_mg', NULL, 'mg',       'Day total = SUM(caffeine_intake.mg); not stored in factors', 0),
    ('caffeine',    0,    'None',     'Bearable word scale, replaced by caffeine_mg', 1),
    ('caffeine',    1,    'Little',   'Bearable word scale, replaced by caffeine_mg', 1),
    ('caffeine',    2,    'Moderate', 'Bearable word scale, replaced by caffeine_mg', 1),
    ('caffeine',    3,    'A lot',    'Bearable word scale, replaced by caffeine_mg', 1);

-- Caffeine. Drinks are described in everyday words ("tiny cola zero", "matcha, 5 g"); the logger turns
-- that into mg using the two lookup tables below. Anything it has to look up or estimate is saved here,
-- so the same drink is never worked out twice.
CREATE TABLE IF NOT EXISTS caffeine_items (
    item       TEXT PRIMARY KEY,      -- 'Coca-Cola Zero', 'Matcha powder', ...
    basis      TEXT NOT NULL,         -- per_100ml / per_g / per_serving
    mg         REAL NOT NULL,         -- caffeine per basis unit
    confidence TEXT NOT NULL,         -- label (read off the pack) / published / estimate
    source     TEXT,
    added_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS caffeine_servings (
    phrase   TEXT PRIMARY KEY,        -- lower-case size words: 'tiny can', 'can', 'cup of my tea'
    item     TEXT,                    -- NULL = a size that fits any drink
    amount   REAL NOT NULL,
    unit     TEXT NOT NULL,           -- ml / g / serving
    note     TEXT,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS caffeine_intake (
    date   TEXT NOT NULL,
    time   TEXT,
    said   TEXT NOT NULL,             -- the words as typed, verbatim
    item   TEXT NOT NULL REFERENCES caffeine_items (item),
    amount REAL NOT NULL,
    unit   TEXT NOT NULL,
    mg     REAL NOT NULL,
    source TEXT NOT NULL
);

-- A few published figures to start from; the logger adds the rest as they come up.
INSERT OR IGNORE INTO caffeine_items (item, basis, mg, confidence, source) VALUES
    ('Coca-Cola',              'per_100ml',  9.6, 'published', '34 mg per 355 ml'),
    ('Coca-Cola Zero',         'per_100ml',  9.6, 'published', '34 mg per 355 ml; check your local can label'),
    ('Pepsi',                  'per_100ml', 10.7, 'published', '38 mg per 355 ml (US figure); other sources say up to 12.4'),
    ('Pepsi Zero / Max',       'per_100ml', 19.5, 'published', 'roughly double regular Pepsi'),
    ('Matcha powder',          'per_g',     30,   'estimate',  'published range 19-44 mg/g, typical 25-35; varies by grade'),
    ('Espresso, single shot',  'per_serving', 63, 'published', 'about 63 mg per 30 ml shot');

INSERT OR IGNORE INTO caffeine_servings (phrase, item, amount, unit, note) VALUES
    ('tiny can', NULL, 150, 'ml', 'the small 150 ml can'),
    ('can',      NULL, 330, 'ml', 'the usual can; say the size when it differs');

-- Telegram bot bookkeeping. Every typed save is a batch; bot_rows lists the rows it wrote (by rowid) so
-- the save can be undone. bot_state holds the poll offset, which reminders went out, and the daily
-- Claude call count.
CREATE TABLE IF NOT EXISTS bot_batches (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bot_rows (
    batch INTEGER NOT NULL,
    tbl   TEXT NOT NULL,
    rid   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bot_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- One row per logged day, for reading the log by eye: the worst severity of the day, which symptoms
-- were above none, medicines, factors, caffeine and notes.
DROP VIEW IF EXISTS daily;
CREATE VIEW daily AS
WITH s AS (SELECT date, MAX(severity) AS worst,
    GROUP_CONCAT(CASE WHEN severity > 0 THEN symptom || ' ' || severity END, ', ') AS symptoms
  FROM (SELECT date, symptom, MAX(severity) AS severity FROM symptoms GROUP BY date, symptom) GROUP BY date),
f AS (SELECT date, GROUP_CONCAT(factor || '=' || value, ', ') AS factors FROM factors GROUP BY date),
m AS (SELECT date, GROUP_CONCAT(name || COALESCE(' ' || CAST(dose_mg AS INT) || ' mg', ''), ', ') AS meds
  FROM meds GROUP BY date),
x AS (SELECT date,
    MAX(CASE WHEN metric = 'steps'  THEN CAST(value AS INT) END) AS steps,
    MAX(CASE WHEN metric = 'hr_avg' THEN ROUND(value) END) AS hr_avg
  FROM metrics GROUP BY date),
k AS (SELECT date, ROUND(SUM(mg)) AS caffeine_mg FROM caffeine_intake GROUP BY date),
n AS (SELECT date, GROUP_CONCAT(text, ' | ') AS note FROM notes GROUP BY date)
SELECT c.date, s.worst, s.symptoms, m.meds, f.factors, k.caffeine_mg, x.steps, x.hr_avg, n.note
FROM (SELECT DISTINCT date FROM checkins) c
LEFT JOIN s USING (date) LEFT JOIN f USING (date) LEFT JOIN m USING (date)
LEFT JOIN x USING (date) LEFT JOIN n USING (date) LEFT JOIN k USING (date);
