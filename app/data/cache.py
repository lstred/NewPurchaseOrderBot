"""
Smart refresh cache — checks dbo.sysTableUpdates before reloading each dataset.

sysTableUpdates schema:
  TABLE_NAME   VARCHAR  — 'DW0001F' = _ORDERS; others match table name exactly
  LAST_UPDATE  DATETIME — timestamp of last modification

On each refresh() call, the app:
  1. Queries sysTableUpdates for current LAST_UPDATE values
  2. Compares against previously saved values (persisted in %APPDATA%)
  3. Only reloads datasets whose source tables have changed
  4. Reuses in-memory DataFrames for unchanged datasets

If sysTableUpdates is unreachable (error/empty), all datasets are treated as stale
and reloaded normally — safe fallback, no data integrity risk.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from app.config import APPDATA_DIR

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

_STATE_FILE = APPDATA_DIR / "refresh_state.json"

# ---------------------------------------------------------------------------
# Dataset → table mapping
# ---------------------------------------------------------------------------

# Maps logical dataset name → list of TABLE_NAME values in sysTableUpdates.
# If ANY dependency changes, that dataset is marked stale and reloaded.
DATASET_TABLES: dict[str, list[str]] = {
    "items":         ["ITEM", "PRICE", "PRODLINE"],
    "filter_values": ["ITEM", "PRICE", "PRODLINE"],
    "orders":        ["DW0001F"],
    "open_pos":      ["DW0001F"],
    "rolls":         ["ROLLS"],
    "pending_pos":   ["OPENPO_D"],
}

# If any of these item-master tables change, cascade and invalidate everything,
# because alias resolution (IIXREF maps) may have changed.
_ITEM_TABLES: frozenset[str] = frozenset({"ITEM", "PRICE", "PRODLINE"})

# All table names we watch
_WATCHED_TABLES = frozenset({"DW0001F", "ITEM", "ROLLS", "OPENPO_D", "PRODLINE", "PRICE"})

# SQL that fetches current timestamps
TABLE_UPDATES_SQL = """
SELECT
    LTRIM(RTRIM(TABLE_NAME))             AS table_name,
    CAST(LAST_UPDATE AS VARCHAR(30))     AS last_update
FROM dbo.sysTableUpdates
WHERE LTRIM(RTRIM(TABLE_NAME)) IN (
    'DW0001F', 'ITEM', 'ROLLS', 'OPENPO_D', 'PRODLINE', 'PRICE'
)
"""

# ---------------------------------------------------------------------------
# In-memory DataFrame store
# ---------------------------------------------------------------------------

_df_store: dict[str, pd.DataFrame] = {}


def get_df(key: str) -> Optional[pd.DataFrame]:
    """Return a cached DataFrame, or None if not yet cached."""
    return _df_store.get(key)


def set_df(key: str, df: pd.DataFrame) -> None:
    """Store a DataFrame in the in-memory cache."""
    _df_store[key] = df.copy()


def clear() -> None:
    """Evict all cached DataFrames (call on connection reset)."""
    _df_store.clear()


# ---------------------------------------------------------------------------
# State persistence helpers
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(data: dict) -> None:
    try:
        APPDATA_DIR.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(
            json.dumps(data, indent=2, default=str), encoding="utf-8"
        )
    except OSError as exc:
        log.warning("Could not save refresh state: %s", exc)


# ---------------------------------------------------------------------------
# Timestamp query
# ---------------------------------------------------------------------------

def fetch_timestamps() -> dict[str, str]:
    """
    Query sysTableUpdates and return {TABLE_NAME: last_update_str}.
    Returns an empty dict on any failure — caller treats this as all-stale.
    """
    try:
        from app.data.db import read_dataframe  # deferred to avoid circular import
        df = read_dataframe(TABLE_UPDATES_SQL)
        if df.empty:
            return {}
        return {
            str(row["table_name"]).strip(): str(row["last_update"]).strip()
            for _, row in df.iterrows()
        }
    except Exception as exc:
        log.warning("sysTableUpdates query failed (%s); treating all datasets as stale.", exc)
        return {}


# ---------------------------------------------------------------------------
# Staleness computation
# ---------------------------------------------------------------------------

def compute_stale(
    current_ts: dict[str, str],
    start_date_str: str,
    end_date_str: str,
) -> set[str]:
    """
    Return the set of dataset names that must be reloaded.

    Rules (applied in order):
      1. If timestamp query failed (empty) → everything is stale.
      2. If any item-master table (ITEM, PRICE, PRODLINE) changed
         → everything is stale (alias maps may be different).
      3. If DW0001F changed OR date range changed → orders + open_pos stale.
      4. If ROLLS changed → rolls stale.
      5. If OPENPO_D changed → pending_pos stale.
      6. Any dataset with no cached DataFrame yet → stale (first run).
    """
    all_datasets = set(DATASET_TABLES.keys())

    # Rule 1 — timestamp query unavailable
    if not current_ts:
        return all_datasets

    state = _load_state()
    saved_ts: dict[str, str] = state.get("timestamps", {})
    saved_range: str = state.get("date_range", "")
    new_range = f"{start_date_str}:{end_date_str}"

    # Rule 2 — item-master changed → invalidate everything
    if any(current_ts.get(t, "") != saved_ts.get(t, "") for t in _ITEM_TABLES):
        return all_datasets

    stale: set[str] = set()

    # Rule 3 — orders table or date range changed
    if current_ts.get("DW0001F", "") != saved_ts.get("DW0001F", "") or new_range != saved_range:
        stale |= {"orders", "open_pos"}

    # Rule 4 — rolls
    if current_ts.get("ROLLS", "") != saved_ts.get("ROLLS", ""):
        stale.add("rolls")

    # Rule 5 — pending POs
    if current_ts.get("OPENPO_D", "") != saved_ts.get("OPENPO_D", ""):
        stale.add("pending_pos")

    # Rule 6 — no cached DF yet (first run after app start)
    for key in all_datasets:
        if get_df(key) is None:
            stale.add(key)

    return stale


# ---------------------------------------------------------------------------
# Commit timestamps after a successful load
# ---------------------------------------------------------------------------

def commit(current_ts: dict[str, str], start_date_str: str, end_date_str: str) -> None:
    """
    Persist the current timestamps as the new baseline.
    Only call this after all datasets have been successfully loaded.
    """
    if not current_ts:
        return  # Nothing to commit if the timestamp query failed
    state = _load_state()
    saved_ts = state.get("timestamps", {})
    saved_ts.update(current_ts)
    _save_state({
        "timestamps": saved_ts,
        "date_range": f"{start_date_str}:{end_date_str}",
    })
