"""SQLite store shared by all modules: candidates, drafts, token ledger, action log."""
import datetime as dt
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "agent.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier TEXT NOT NULL,
    item_id TEXT NOT NULL,
    name TEXT NOT NULL,
    cost INTEGER NOT NULL,
    shipping INTEGER NOT NULL DEFAULT 0,
    material TEXT, size TEXT, options TEXT,
    keyword1 TEXT, keyword2 TEXT, keyword3 TEXT,
    target_price INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE (supplier, item_id)
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    title TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    price INTEGER NOT NULL,
    floor INTEGER NOT NULL,
    source TEXT NOT NULL,            -- 'llm' or 'formula'
    flags TEXT NOT NULL DEFAULT '',  -- things a person should look at
    status TEXT NOT NULL DEFAULT 'pending',  -- pending / approved / rejected / published
    decided_by TEXT, decided_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS token_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, model TEXT, purpose TEXT,
    prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER
);
CREATE TABLE IF NOT EXISTS action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, action TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    product_order_id TEXT PRIMARY KEY,   -- Naver's id for one ordered line; no buyer details are stored
    status TEXT, paid_at TEXT, last_changed TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sourcing_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, keyword TEXT NOT NULL, market TEXT NOT NULL, result TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL
);
"""

ADDED_COLUMNS = {"candidates": {"extra": "TEXT"}, "drafts": {"reason": "TEXT"}}   # added after the first release

DRAFT_SELECT = (
    "SELECT d.*, c.supplier, c.item_id, c.name, c.cost, c.shipping, c.keyword1, c.extra "
    "FROM drafts d JOIN candidates c ON c.id = d.candidate_id "
)


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    for table, columns in ADDED_COLUMNS.items():   # bring an older agent.db up to date
        existing = {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    con.commit()
    return con


def log_action(con, action, detail=""):
    con.execute("INSERT INTO action_log (ts, action, detail) VALUES (?, ?, ?)", (now(), action, detail))
    con.commit()


def recent_actions(con, limit=50):
    return con.execute("SELECT ts, action, detail FROM action_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def get_setting(con, key, default=None):
    row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(con, key, value):
    con.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value))
    con.commit()


def upsert_order(con, product_order_id, status, paid_at, last_changed):
    con.execute(
        "INSERT INTO orders (product_order_id, status, paid_at, last_changed) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (product_order_id) DO UPDATE SET status = excluded.status, "
        "paid_at = COALESCE(excluded.paid_at, orders.paid_at), last_changed = excluded.last_changed",
        (product_order_id, status, paid_at, last_changed),
    )


def all_orders(con):
    return con.execute("SELECT product_order_id, status, paid_at, last_changed FROM orders").fetchall()


def add_run(con, keyword, market, result):
    con.execute("INSERT INTO sourcing_runs (ts, keyword, market, result) VALUES (?, ?, ?, ?)",
                (now(), keyword, market, json.dumps(result, ensure_ascii=False)))
    con.commit()


def recent_runs(con, limit=5):
    rows = con.execute("SELECT ts, keyword, market, result FROM sourcing_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"ts": r["ts"], "keyword": r["keyword"], "market": r["market"], **json.loads(r["result"])} for r in rows]


def add_chat(con, question, answer):
    con.execute("INSERT INTO chats (ts, question, answer) VALUES (?, ?, ?)", (now(), question, answer))
    con.commit()


def recent_chats(con, limit=20):
    """The latest questions and answers, oldest first."""
    rows = con.execute("SELECT ts, question, answer FROM chats ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return rows[::-1]


def upsert_candidate(con, row):
    """Insert a candidate, or update it if (supplier, item_id) already exists. Returns its id."""
    cols = ["supplier", "item_id", "name", "cost", "shipping", "material", "size", "options",
            "keyword1", "keyword2", "keyword3", "target_price", "extra"]
    values = [row.get(c) for c in cols]
    con.execute(
        f"INSERT INTO candidates ({', '.join(cols)}, created_at) VALUES ({', '.join('?' * len(cols))}, ?) "
        "ON CONFLICT (supplier, item_id) DO UPDATE SET "
        + ", ".join(f"{c} = excluded.{c}" for c in cols[2:]),
        values + [now()],
    )
    con.commit()
    return con.execute("SELECT id FROM candidates WHERE supplier = ? AND item_id = ?",
                       (row["supplier"], row["item_id"])).fetchone()["id"]


def has_open_draft(con, candidate_id):
    row = con.execute("SELECT 1 FROM drafts WHERE candidate_id = ? AND status IN ('pending', 'approved', 'published')",
                      (candidate_id,)).fetchone()
    return row is not None


def add_draft(con, candidate_id, title, tags, price, floor, source, flags, reason=None):
    con.execute(
        "INSERT INTO drafts (candidate_id, title, tags, price, floor, source, flags, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (candidate_id, title, json.dumps(tags, ensure_ascii=False), price, floor, source, flags, reason, now()),
    )
    con.commit()


def drafts_by_status(con, status):
    return con.execute(DRAFT_SELECT + "WHERE d.status = ? ORDER BY d.id", (status,)).fetchall()


def get_draft(con, draft_id):
    return con.execute(DRAFT_SELECT + "WHERE d.id = ?", (draft_id,)).fetchone()


def draft_counts(con):
    counts = {"pending": 0, "approved": 0, "rejected": 0, "published": 0}
    for row in con.execute("SELECT status, COUNT(*) AS n FROM drafts GROUP BY status"):
        counts[row["status"]] = row["n"]
    return counts


def set_decision(con, draft_id, status, decided_by, title=None, flags=None, price=None):
    if title is not None:
        con.execute("UPDATE drafts SET title = ?, flags = ? WHERE id = ?", (title, flags or "", draft_id))
    if price is not None:
        con.execute("UPDATE drafts SET price = ? WHERE id = ?", (price, draft_id))
    con.execute("UPDATE drafts SET status = ?, decided_by = ?, decided_at = ? WHERE id = ?",
                (status, decided_by, now(), draft_id))
    con.commit()
    log_action(con, f"draft_{status}", f"draft_id={draft_id} by={decided_by}")


def log_tokens(con, model, purpose, usage):
    con.execute(
        "INSERT INTO token_ledger (ts, model, purpose, prompt_tokens, completion_tokens, total_tokens) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (now(), model, purpose, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
         usage.get("total_tokens", 0)),
    )
    con.commit()


def tokens_since(con, iso_date):
    row = con.execute("SELECT COALESCE(SUM(total_tokens), 0) AS n FROM token_ledger WHERE ts >= ?",
                      (iso_date,)).fetchone()
    return row["n"]
