"""
Database engine and query helpers.

Uses SQLAlchemy with the mssql+pyodbc dialect and Windows Trusted Connection.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import config

_engine: Optional[Engine] = None


def _get_engine() -> Engine:
    global _engine
    if _engine is None:
        odbc_url = f"mssql+pyodbc:///?odbc_connect={quote_plus(config.connection_string)}"
        _engine = create_engine(odbc_url, fast_executemany=True, pool_pre_ping=True)
    return _engine


def read_dataframe(sql: str, params: Optional[dict] = None) -> pd.DataFrame:
    """Execute *sql* and return results as a DataFrame."""
    engine = _get_engine()
    with engine.connect() as conn:
        return pd.read_sql_query(text(sql), conn, params=params or {})


def validate_connection() -> bool:
    """Return True if the database connection is reachable, False otherwise."""
    try:
        engine = _get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
