"""
Data loaders — fetch raw DataFrames from SQL Server, apply UOM conversion,
alias resolution, and return clean DataFrames ready for metrics_service.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from app.data.db import read_dataframe
from app.data.queries import (
    FILTER_VALUES_SQL,
    ITEMS_SQL,
    OPEN_PO_ORDERS_SQL,
    ORDERS_SQL,
    PENDING_PO_SQL,
    ROLLS_SQL,
)

# ---------------------------------------------------------------------------
# UOM → SY conversion
# ---------------------------------------------------------------------------
_SF_COST_CENTERS = {"010", "011", "012", "013"}
_SY_UOMS = {"SY", "SQY", "SQYD", "SQYDS"}
_SF_UOMS = {"SF", "SQF", "FT2", "SQFT"}
_LY_UOMS = {"LY", "YD", "YDS", "YARD"}
_LF_UOMS = {"LF", "FT", "FEET", "FOOT"}
_IN_UOMS = {"IN", "INCH", "INCHES"}


def _to_sy(qty: float, uom: str, width_in: float, cost_center: str) -> float:
    uom = (uom or "").strip().upper()
    if uom in _SY_UOMS:
        return qty
    if uom in _SF_UOMS:
        return qty / 9.0 if cost_center in _SF_COST_CENTERS else qty
    if uom in _LY_UOMS:
        return (qty * width_in / 36.0) if width_in > 0 else qty
    if uom in _LF_UOMS:
        return (qty * width_in / 108.0) if width_in > 0 else qty
    if uom in _IN_UOMS:
        return (qty * width_in / 1296.0) if width_in > 0 else qty
    return qty


def _vectorised_to_sy(
    df: pd.DataFrame,
    qty_col: str,
    uom_col: str,
    width_col: str,
    cc_col: str,
) -> pd.Series:
    uom = df[uom_col].fillna("").str.upper().str.strip()
    qty = df[qty_col].fillna(0).astype(float)
    width = df[width_col].fillna(0).astype(float)
    cc = df[cc_col].fillna("").astype(str)

    result = qty.copy()
    mask_sf = uom.isin(_SF_UOMS)
    mask_sf_cc = mask_sf & cc.isin(_SF_COST_CENTERS)
    result = result.where(~mask_sf_cc, qty / 9.0)

    mask_ly = uom.isin(_LY_UOMS)
    has_w = width > 0
    result = result.where(~(mask_ly & has_w), qty * width / 36.0)

    mask_lf = uom.isin(_LF_UOMS)
    result = result.where(~(mask_lf & has_w), qty * width / 108.0)

    mask_in = uom.isin(_IN_UOMS)
    result = result.where(~(mask_in & has_w), qty * width / 1296.0)

    return result


# ---------------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------------

def load_items() -> pd.DataFrame:
    """Return item master with price class / product line joined."""
    df = read_dataframe(ITEMS_SQL)
    if not df.empty:
        # Strip AS/400 CHAR-column trailing spaces so every join key is clean
        df["sku"] = df["sku"].str.strip()
        df["base_sku"] = df["base_sku"].str.strip()
    return df


def load_orders(
    start_date: date,
    end_date: date,
    items_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    start_ymd = int(start_date.strftime("%Y%m%d"))
    end_ymd = int(end_date.strftime("%Y%m%d"))
    df = read_dataframe(ORDERS_SQL, {"start_ymd": start_ymd, "end_ymd": end_ymd})

    if df.empty:
        return df

    df["order_entry_date"] = pd.to_datetime(
        df["order_entry_date_raw"].astype(str).str.zfill(8), format="%Y%m%d", errors="coerce"
    ).dt.date

    today = date.today()
    df = df[df["order_entry_date"].notna() & (df["order_entry_date"] <= today)]

    # Strip CHAR-column padding BEFORE alias map lookup so keys match item sku
    df["sku"] = df["sku"].str.strip()

    # Alias resolution
    if items_df is not None and not items_df.empty:
        alias_map = items_df.set_index("sku")["base_sku"].to_dict()
        df["base_sku"] = df["sku"].map(alias_map).fillna(df["sku"])
        df["base_sku"] = df["base_sku"].str.strip()  # defense-in-depth
        # Also carry width from item master where order-level width is 0
        width_map = items_df.set_index("base_sku")["item_width_inches"].to_dict()
        df["width_resolved"] = df["base_sku"].map(width_map).fillna(0)
        df["item_width_inches"] = df["item_width_inches"].where(
            df["item_width_inches"] > 0, df["width_resolved"]
        )
        cc_map = items_df.set_index("base_sku")["cost_center"].to_dict()
        df["cost_center"] = df["base_sku"].map(cc_map).fillna("")
    else:
        df["base_sku"] = df["sku"]
        df["cost_center"] = ""

    df["order_line_id"] = df["order_number"].astype(str) + "-" + df["line_number"].astype(str)
    df["backorder_flag"] = df["detail_line_status"].str.upper().isin(["B", "R"])
    df["strict_bo_flag"] = df["detail_line_status"].str.upper() == "B"
    df["filled_flag"] = ~df["backorder_flag"]

    df["quantity_sy"] = _vectorised_to_sy(
        df, "quantity_ordered", "unit_of_measure", "item_width_inches", "cost_center"
    )
    df["quantity_sy"] = df["quantity_sy"].clip(lower=0)
    return df


def load_open_pos(items_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Warehouse POs not yet invoiced."""
    df = read_dataframe(OPEN_PO_ORDERS_SQL)
    if df.empty:
        return df

    # Strip CHAR-column padding before alias map lookup
    df["sku"] = df["sku"].str.strip()

    if items_df is not None and not items_df.empty:
        alias_map = items_df.set_index("sku")["base_sku"].to_dict()
        df["base_sku"] = df["sku"].map(alias_map).fillna(df["sku"])
        df["base_sku"] = df["base_sku"].str.strip()  # defense-in-depth
        width_map = items_df.set_index("base_sku")["item_width_inches"].to_dict()
        df["width_resolved"] = df["base_sku"].map(width_map).fillna(0)
        df["item_width_inches"] = df["item_width_inches"].where(
            df["item_width_inches"] > 0, df["width_resolved"]
        )
        cc_map = items_df.set_index("base_sku")["cost_center"].to_dict()
        df["cost_center"] = df["base_sku"].map(cc_map).fillna("")
    else:
        df["base_sku"] = df["sku"]
        df["cost_center"] = ""

    df["quantity_sy"] = _vectorised_to_sy(
        df, "quantity_ordered", "unit_of_measure", "item_width_inches", "cost_center"
    )
    df["quantity_sy"] = df["quantity_sy"].clip(lower=0)
    df["eta_date"] = pd.to_datetime(df["eta_date"], errors="coerce").dt.date
    return df


def load_rolls(items_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    df = read_dataframe(ROLLS_SQL)
    if df.empty:
        return df

    # Strip CHAR-column padding before alias map lookup
    df["sku"] = df["sku"].str.strip()

    if items_df is not None and not items_df.empty:
        alias_map = items_df.set_index("sku")["base_sku"].to_dict()
        df["base_sku"] = df["sku"].map(alias_map).fillna(df["sku"])
        df["base_sku"] = df["base_sku"].str.strip()  # defense-in-depth
        width_map = items_df.set_index("base_sku")["item_width_inches"].to_dict()
        df["item_width_inches"] = df["base_sku"].map(width_map).fillna(0)
        cc_map = items_df.set_index("base_sku")["cost_center"].to_dict()
        df["cost_center"] = df["base_sku"].map(cc_map).fillna("")
    else:
        df["base_sku"] = df["sku"]
        df["item_width_inches"] = 0.0
        df["cost_center"] = ""

    df["quantity_sy"] = _vectorised_to_sy(
        df, "available_quantity", "unit_of_measure", "item_width_inches", "cost_center"
    )
    df["quantity_sy"] = df["quantity_sy"].clip(lower=0)
    df["receive_date"] = pd.to_datetime(df["receive_date"], errors="coerce").dt.date
    today = date.today()
    df["age_days"] = df["receive_date"].apply(
        lambda d: (today - d).days if pd.notna(d) else 0
    )
    return df


def load_pending_pos(items_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    df = read_dataframe(PENDING_PO_SQL)
    if df.empty:
        return df

    # OPENPO_D components are trimmed in SQL, but strip here for consistency
    df["sku"] = df["sku"].str.strip()

    if items_df is not None and not items_df.empty:
        alias_map = items_df.set_index("sku")["base_sku"].to_dict()
        df["base_sku"] = df["sku"].map(alias_map).fillna(df["sku"])
        df["base_sku"] = df["base_sku"].str.strip()  # defense-in-depth
        width_map = items_df.set_index("base_sku")["item_width_inches"].to_dict()
        df["item_width_inches"] = df["base_sku"].map(width_map).fillna(0)
        cc_map = items_df.set_index("base_sku")["cost_center"].to_dict()
        df["cost_center"] = df["base_sku"].map(cc_map).fillna("")
    else:
        df["base_sku"] = df["sku"]
        df["item_width_inches"] = 0.0
        df["cost_center"] = ""

    # OPENPO_D quantities are in native UOM — but we don't have UOM column there,
    # treat them as SY (they're typically already in SY for flooring).
    df["pending_qty_sy"] = df["pending_qty"].clip(lower=0)
    return df


def load_filter_values() -> pd.DataFrame:
    return read_dataframe(FILTER_VALUES_SQL)
