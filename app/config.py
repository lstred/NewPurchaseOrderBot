"""
AppConfig — connection string resolution and application defaults.

Resolution order for SQLSERVER_ODBC (highest → lowest priority):
  1. Environment variable  SQLSERVER_ODBC
  2. %APPDATA%\\PurchaseOrderBot\\config.json  →  key "SQLSERVER_ODBC"
  3. config_local.py in the project root      →  attribute SQLSERVER_ODBC
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_DEFAULT_ODBC = (
    "Driver={ODBC Driver 18 for SQL Server};"
    "Server=NRFVMSSQL04;"
    "Database=NRF_REPORTS;"
    "Trusted_Connection=Yes;"
    "Encrypt=no;"
)

APPDATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "PurchaseOrderBot"
_APPDATA_CONFIG = APPDATA_DIR / "config.json"


def _resolve_connection_string() -> str:
    # 1. Environment variable
    env_val = os.environ.get("SQLSERVER_ODBC")
    if env_val:
        return env_val

    # 2. %APPDATA%\PurchaseOrderBot\config.json
    if _APPDATA_CONFIG.exists():
        try:
            data = json.loads(_APPDATA_CONFIG.read_text(encoding="utf-8"))
            if "SQLSERVER_ODBC" in data:
                return data["SQLSERVER_ODBC"]
        except (json.JSONDecodeError, OSError):
            pass

    # 3. config_local.py alongside project root
    try:
        import config_local  # type: ignore
        val = getattr(config_local, "SQLSERVER_ODBC", None)
        if val:
            return val
    except ImportError:
        pass

    return _DEFAULT_ODBC


@dataclass
class AppConfig:
    connection_string: str = field(default_factory=_resolve_connection_string)

    # Stock turn
    stockturn_target: float = 4.0

    # Cost centers
    default_cost_centers: list[str] = field(default_factory=lambda: ["010"])

    # Historical demand window in months
    default_date_months: int = 18

    # SKU rating quartile thresholds (A/B/C/D)
    rating_buckets: tuple[float, float, float] = (0.25, 0.50, 0.75)

    # SQLAlchemy query cache TTL in seconds (6 minutes)
    cache_ttl_seconds: int = 360

    def _resolve_cost_centers(self, candidates: Optional[list[str]] = None) -> list[str]:
        """Return cost centers, always excluding those starting with '1'."""
        source = candidates if candidates is not None else self.default_cost_centers
        return [cc for cc in source if not str(cc).startswith("1")]


# Module-level singleton used throughout the app
config = AppConfig()
