"""SQLite 访问层（WAL 模式）。

价格数据不进 SQLite（存 Parquet）；这里只存事件/衍生指标/回测结果等关系型数据。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable, Optional

from app.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY,
  fingerprint TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  event_date TEXT NOT NULL,
  category TEXT NOT NULL,
  importance TEXT DEFAULT 'medium',
  description TEXT DEFAULT '',
  url TEXT DEFAULT '',
  source TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now', 'localtime')),
  updated_at TEXT DEFAULT (datetime('now', 'localtime')),
  UNIQUE(event_date, category, name)
);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date);
CREATE INDEX IF NOT EXISTS idx_events_cat ON events(category, event_date);

CREATE TABLE IF NOT EXISTS daily_breadth(
  date TEXT NOT NULL,
  universe TEXT NOT NULL,
  advancers INT, decliners INT, unchanged INT,
  pct_above_ma50 REAL, pct_above_ma200 REAL, total INT,
  PRIMARY KEY(date, universe));

CREATE TABLE IF NOT EXISTS sector_daily(
  date TEXT NOT NULL,
  sector TEXT NOT NULL,
  close REAL, change_pct REAL,
  mom_5d REAL, mom_21d REAL, mom_63d REAL, mom_126d REAL, mom_252d REAL,
  PRIMARY KEY(date, sector));

CREATE TABLE IF NOT EXISTS fedwatch_daily(
  date TEXT PRIMARY KEY,
  front_price REAL, implied_rate REAL, current_effr REAL,
  next_meeting TEXT, implied_change_bp REAL, hike_prob REAL, cut_prob REAL);

CREATE TABLE IF NOT EXISTS institutional_holdings(
  manager_cik TEXT, quarter TEXT, symbol TEXT,
  shares REAL, value_usd REAL, change_shares REAL, report_date TEXT,
  PRIMARY KEY(manager_cik, quarter, symbol));

CREATE TABLE IF NOT EXISTS valuation_snapshots(
  symbol TEXT, as_of TEXT, method TEXT,
  value REAL, detail_json TEXT,
  PRIMARY KEY(symbol, as_of, method));

CREATE TABLE IF NOT EXISTS backtest_results(
  id TEXT PRIMARY KEY,
  created_at TEXT DEFAULT (datetime('now', 'localtime')),
  strategy_id TEXT, params_json TEXT,
  tickers_json TEXT, start TEXT, end TEXT,
  metrics_json TEXT,
  curve_parquet TEXT);

CREATE TABLE IF NOT EXISTS backtest_scan(
  id INTEGER PRIMARY KEY,
  base_id TEXT, grid_json TEXT, metrics_rows_json TEXT,
  created_at TEXT DEFAULT (datetime('now', 'localtime')));

CREATE TABLE IF NOT EXISTS sync_log(
  id INTEGER PRIMARY KEY,
  dataset TEXT, status TEXT,
  started_at TEXT, finished_at TEXT, message TEXT);
"""

# SQLite 之外维护的轻量计数器（FMP 配额等）也放这里
_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta_kv(
  key TEXT PRIMARY KEY, value TEXT);
"""


@contextmanager
def get_conn():
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path, timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
        conn.executescript(_META_SCHEMA)
        conn.commit()


def execute(sql: str, params: Iterable[Any] = ()) -> None:
    with get_conn() as conn:
        conn.execute(sql, tuple(params))
        conn.commit()


def executemany(sql: str, rows: Iterable[Iterable[Any]]) -> int:
    with get_conn() as conn:
        cur = conn.executemany(sql, [tuple(r) for r in rows])
        conn.commit()
        return cur.rowcount


def query_all(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
    rows = query_all(sql, params)
    return rows[0] if rows else None


def upsert(sql: str, rows: Iterable[Iterable[Any]]) -> int:
    """executemany with INSERT ... ON CONFLICT 语义由调用方 SQL 决定。"""
    return executemany(sql, rows)


def meta_get(key: str) -> Optional[str]:
    row = query_one("SELECT value FROM meta_kv WHERE key = ?", (key,))
    return row["value"] if row else None


def meta_set(key: str, value: str) -> None:
    execute(
        "INSERT INTO meta_kv(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
