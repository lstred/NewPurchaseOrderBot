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
) -> tuple[float, list[str]]:
    """Return (target, list_of_matching_keys) — all keys that match this SKU's attributes.

    Caller uses this to detect conflicts (multiple non-global targets) and let the user
    choose which takes precedence.
    """
    data = get_all_targets()
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

    # Snooze until date
    until = entry.get("until")
    if until:
        try:
            until_d = date.fromisoformat(until)
            if date.today() <= until_d:
                return True
        except ValueError:
            pass

    # Snooze until PO qty changes
    saved_qty = entry.get("po_qty_at_snooze", 0.0)
    if abs(current_po_qty - saved_qty) > 0.01:
        # PO qty changed — auto-unsnooze
        unsnooze_alert(alert_key)
        return False

    # Expired
    return False


def unsnooze_alert(alert_key: str) -> None:
    data = _load(_SNOOZE_FILE)
    data.pop(alert_key, None)
    _save(_SNOOZE_FILE, data)


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
