"""
Persistence helpers — stock-turn targets, snooze state, launch dates.
All stored as JSON under %APPDATA%\\PurchaseOrderBot\\.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from app.config import APPDATA_DIR

_TARGETS_FILE = APPDATA_DIR / "stockturn_targets.json"
_SNOOZE_FILE = APPDATA_DIR / "snooze.json"
_LAUNCH_FILE = APPDATA_DIR / "launch_dates.json"

_DEFAULT_TARGET = 4.0


def _ensure_dir() -> None:
    APPDATA_DIR.mkdir(parents=True, exist_ok=True)


def _load(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save(path: Path, data: dict) -> None:
    _ensure_dir()
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------------------
# Stock-turn targets
# Keys can be:  "global", "cc:{cost_center}", "pc:{price_class}",
#               "pl:{product_line}", "sup:{supplier}", "sku:{sku}"
# ---------------------------------------------------------------------------

def get_target(key: str = "global") -> float:
    data = _load(_TARGETS_FILE)
    return float(data.get(key, data.get("global", _DEFAULT_TARGET)))


def set_target(key: str, value: float) -> None:
    data = _load(_TARGETS_FILE)
    data[key] = round(float(value), 2)
    _save(_TARGETS_FILE, data)


def get_all_targets() -> dict[str, float]:
    data = _load(_TARGETS_FILE)
    if "global" not in data:
        data["global"] = _DEFAULT_TARGET
    return {k: float(v) for k, v in data.items()}


def resolve_target(
    cost_center: str = "",
    price_class: str = "",
    product_line: str = "",
    supplier: str = "",
    sku: str = "",
    _targets_cache: Optional[dict] = None,
) -> tuple[float, list[str]]:
    """Return (target, list_of_matching_keys) — all keys that match this SKU's attributes.

    Pass _targets_cache (from get_all_targets()) to avoid repeated disk reads when
    resolving targets for thousands of SKUs in a batch.
    """
    data = _targets_cache if _targets_cache is not None else get_all_targets()
    candidates: list[tuple[str, float]] = []

    for key_prefix, value in [
        (f"sku:{sku}", None),
        (f"cc:{cost_center}", None),
        (f"pc:{price_class}", None),
        (f"pl:{product_line}", None),
        (f"sup:{supplier}", None),
    ]:
        if key_prefix in data:
            candidates.append((key_prefix, data[key_prefix]))

    if not candidates:
        return data.get("global", _DEFAULT_TARGET), ["global"]

    if len(candidates) == 1:
        return candidates[0][1], [candidates[0][0]]

    # Multiple matches — return all so UI can ask the user
    keys = [c[0] for c in candidates]
    # Default: use most specific (sku > cc > pc > pl > sup)
    order = ["sku:", "cc:", "pc:", "pl:", "sup:"]
    for prefix in order:
        for k, v in candidates:
            if k.startswith(prefix):
                return v, keys
    return candidates[0][1], keys


# ---------------------------------------------------------------------------
# Snooze state
# Key: "{alert_type}:{sku}"  (e.g. "overstock:ABC123")
# Value: {"until": "YYYY-MM-DD" | None, "condition": "po_qty_changed" | None,
#         "po_qty_at_snooze": float}
# ---------------------------------------------------------------------------

def snooze_alert(
    alert_key: str,
    until_date: Optional[date] = None,
    po_qty_at_snooze: float = 0.0,
) -> None:
    data = _load(_SNOOZE_FILE)
    data[alert_key] = {
        "until": until_date.isoformat() if until_date else None,
        "po_qty_at_snooze": po_qty_at_snooze,
        "snoozed_at": date.today().isoformat(),
    }
    _save(_SNOOZE_FILE, data)


def is_snoozed(alert_key: str, current_po_qty: float = 0.0) -> bool:
    data = _load(_SNOOZE_FILE)
    entry = data.get(alert_key)
    if not entry:
        return False

    # Auto-unsnooze if PO qty has changed (always checked first)
    saved_qty = entry.get("po_qty_at_snooze", 0.0)
    if abs(current_po_qty - saved_qty) > 0.01:
        unsnooze_alert(alert_key)
        return False

    # Date-based snooze
    until = entry.get("until")
    if until is not None:
        try:
            until_d = date.fromisoformat(until)
            if date.today() <= until_d:
                return True
            # Expired — clean up
            unsnooze_alert(alert_key)
            return False
        except ValueError:
            pass

    # "Until PO qty changes" mode (until=None) — qty hasn't changed, still snoozed
    return True


def unsnooze_alert(alert_key: str) -> None:
    data = _load(_SNOOZE_FILE)
    data.pop(alert_key, None)
    _save(_SNOOZE_FILE, data)


def delete_target(key: str) -> None:
    """Remove a stock-turn target by key."""
    data = _load(_TARGETS_FILE)
    data.pop(key, None)
    _save(_TARGETS_FILE, data)


def get_all_snoozes() -> dict[str, Any]:
    return _load(_SNOOZE_FILE)


# ---------------------------------------------------------------------------
# Launch dates
# Key: sku  Value: "YYYY-MM-DD"
# ---------------------------------------------------------------------------

def get_launch_date(sku: str) -> Optional[date]:
    data = _load(_LAUNCH_FILE)
    val = data.get(sku)
    if val:
        try:
            return date.fromisoformat(val)
        except ValueError:
            pass
    return None


def set_launch_date(sku: str, d: date) -> None:
    data = _load(_LAUNCH_FILE)
    existing = data.get(sku)
    # Only move the launch date earlier, never later
    if existing:
        try:
            existing_d = date.fromisoformat(existing)
            if d >= existing_d:
                return
        except ValueError:
            pass
    data[sku] = d.isoformat()
    _save(_LAUNCH_FILE, data)


def get_all_launch_dates() -> dict[str, date]:
    data = _load(_LAUNCH_FILE)
    result: dict[str, date] = {}
    for sku, val in data.items():
        try:
            result[sku] = date.fromisoformat(val)
        except ValueError:
            pass
    return result


# ---------------------------------------------------------------------------
# Column visibility preferences
# Key: table_id (e.g. "overview")  Value: {column_name: bool}
# ---------------------------------------------------------------------------

_COLUMN_PREFS_FILE = APPDATA_DIR / "column_prefs.json"


def get_column_prefs(table_id: str) -> dict[str, bool]:
    """Return {column_name: is_visible} for the given table."""
    data = _load(_COLUMN_PREFS_FILE)
    return {k: bool(v) for k, v in data.get(table_id, {}).items()}


def set_column_prefs(table_id: str, prefs: dict[str, bool]) -> None:
    data = _load(_COLUMN_PREFS_FILE)
    data[table_id] = {k: bool(v) for k, v in prefs.items()}
    _save(_COLUMN_PREFS_FILE, data)


# ---------------------------------------------------------------------------
# Table color / threshold rules
# Key: table_id (e.g. "overview")  Value: list of rule dicts
# ---------------------------------------------------------------------------

_TABLE_RULES_FILE = APPDATA_DIR / "table_rules.json"


def get_table_rules(table_id: str) -> list[dict]:
    """Return the list of color-threshold rules for the given table."""
    data = _load(_TABLE_RULES_FILE)
    return list(data.get(table_id, []))


def set_table_rules(table_id: str, rules: list[dict]) -> None:
    data = _load(_TABLE_RULES_FILE)
    data[table_id] = rules
    _save(_TABLE_RULES_FILE, data)


# ---------------------------------------------------------------------------
# Column width preferences
# Key: table_id  Value: {column_name: pixel_width}
# ---------------------------------------------------------------------------

_COLUMN_WIDTHS_FILE = APPDATA_DIR / "column_widths.json"


def get_column_widths(table_id: str) -> dict[str, int]:
    """Return {column_name: width_pixels} for the given table."""
    data = _load(_COLUMN_WIDTHS_FILE)
    return {k: int(v) for k, v in data.get(table_id, {}).items()}


def set_column_widths(table_id: str, widths: dict[str, int]) -> None:
    data = _load(_COLUMN_WIDTHS_FILE)
    data[table_id] = {k: int(v) for k, v in widths.items()}
    _save(_COLUMN_WIDTHS_FILE, data)


# ---------------------------------------------------------------------------
# Operator name mappings
# Key: initials (upper-case)   Value: full name
# ---------------------------------------------------------------------------

_OPERATOR_NAMES_FILE = APPDATA_DIR / "operator_names.json"


def get_operator_names() -> dict[str, str]:
    """Return {initials: full_name}."""
    return {k: str(v) for k, v in _load(_OPERATOR_NAMES_FILE).items()}


def save_all_operator_names(names: dict[str, str]) -> None:
    """Overwrite the entire operator-name mapping."""
    _save(_OPERATOR_NAMES_FILE, {k.upper().strip(): v.strip() for k, v in names.items() if k.strip()})


# ---------------------------------------------------------------------------
# AI provider configuration
# ---------------------------------------------------------------------------

_AI_CONFIG_FILE = APPDATA_DIR / "ai_config.json"


def get_ai_config() -> dict:
    """Return {provider, api_key, model} for the AI tab."""
    data = _load(_AI_CONFIG_FILE)
    return {
        "provider": data.get("provider", "anthropic"),
        "api_key": data.get("api_key", ""),
        "model": data.get("model", "claude-sonnet-4-5"),
    }


def set_ai_config(cfg: dict) -> None:
    _save(_AI_CONFIG_FILE, {
        "provider": str(cfg.get("provider", "anthropic")),
        "api_key": str(cfg.get("api_key", "")),
        "model": str(cfg.get("model", "")),
    })


# ---------------------------------------------------------------------------
# Saved AI SQL queries (library)
# Schema (list of dicts):
#   {"id": str, "name": str, "description": str, "sql": str, "created": iso8601}
# ---------------------------------------------------------------------------

import uuid as _uuid
from datetime import datetime as _datetime

_SAVED_QUERIES_FILE = APPDATA_DIR / "saved_queries.json"


def get_saved_queries() -> list[dict]:
    data = _load(_SAVED_QUERIES_FILE)
    items = data.get("queries", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    out = []
    for q in items:
        if not isinstance(q, dict) or not q.get("sql"):
            continue
        out.append({
            "id":          str(q.get("id") or _uuid.uuid4()),
            "name":        str(q.get("name", "(unnamed)")).strip() or "(unnamed)",
            "description": str(q.get("description", "")).strip(),
            "sql":         str(q.get("sql", "")).strip(),
            "created":     str(q.get("created", _datetime.now().isoformat(timespec='seconds'))),
        })
    out.sort(key=lambda x: x["name"].lower())
    return out


def _save_queries(queries: list[dict]) -> None:
    _save(_SAVED_QUERIES_FILE, {"queries": queries})


def add_saved_query(name: str, description: str, sql: str) -> dict:
    queries = get_saved_queries()
    new = {
        "id":          str(_uuid.uuid4()),
        "name":        str(name).strip() or "(unnamed)",
        "description": str(description).strip(),
        "sql":         str(sql).strip(),
        "created":     _datetime.now().isoformat(timespec='seconds'),
    }
    queries.append(new)
    _save_queries(queries)
    return new


def update_saved_query(query_id: str, name: str, description: str, sql: str) -> None:
    queries = get_saved_queries()
    for q in queries:
        if q["id"] == query_id:
            q["name"] = str(name).strip() or "(unnamed)"
            q["description"] = str(description).strip()
            q["sql"] = str(sql).strip()
            break
    _save_queries(queries)


def delete_saved_query(query_id: str) -> None:
    queries = [q for q in get_saved_queries() if q["id"] != query_id]
    _save_queries(queries)
