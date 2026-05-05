"""
Core metrics service.

compute_all(filters, start_date, end_date) → DatasetBundle
  .items          — item master
  .sku_metrics    — per-SKU summary metrics
  .orders         — raw order lines
  .open_pos       — open PO lines with eta
  .rolls          — roll inventory
  .timeline       — per-SKU daily projected inventory timeline
  .filter_values  — distinct sidebar filter options
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from app.data.loaders import (
    load_filter_values,
    load_items,
    load_open_pos,
    load_orders,
    load_pending_pos,
    load_rolls,
)
from app.data.store import get_all_launch_dates, resolve_target, set_launch_date

_INF = float("inf")


@dataclass
class DatasetBundle:
    items: pd.DataFrame = field(default_factory=pd.DataFrame)
    sku_metrics: pd.DataFrame = field(default_factory=pd.DataFrame)
    orders: pd.DataFrame = field(default_factory=pd.DataFrame)
    open_pos: pd.DataFrame = field(default_factory=pd.DataFrame)
    rolls: pd.DataFrame = field(default_factory=pd.DataFrame)
    pending_pos: pd.DataFrame = field(default_factory=pd.DataFrame)
    timeline: dict[str, pd.DataFrame] = field(default_factory=dict)
    filter_values: pd.DataFrame = field(default_factory=pd.DataFrame)
    summary: dict = field(default_factory=dict)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_all(
    filters: dict,
    start_date: date,
    end_date: date,
) -> DatasetBundle:
    bundle = DatasetBundle()

    try:
        bundle.filter_values = load_filter_values()
        bundle.items = load_items()

        if bundle.items.empty:
            bundle.error = "No item data returned. Check database connection."
            return bundle

        # Apply filters to item master
        items = _apply_item_filters(bundle.items, filters)

        bundle.orders = load_orders(start_date, end_date, items_df=items)
        bundle.open_pos = load_open_pos(items_df=items)
        bundle.rolls = load_rolls(items_df=items)
        bundle.pending_pos = load_pending_pos(items_df=items)

        # Filter order/roll data to SKUs in filtered item set
        active_skus = set(items["base_sku"].unique())
        orders = bundle.orders[bundle.orders["base_sku"].isin(active_skus)]
        open_pos = bundle.open_pos[bundle.open_pos["base_sku"].isin(active_skus)]
        rolls = bundle.rolls[bundle.rolls["base_sku"].isin(active_skus)]
        pending = bundle.pending_pos[bundle.pending_pos["base_sku"].isin(active_skus)]

        days_in_range = max((end_date - start_date).days + 1, 1)

        # Aggregate per base_sku
        bundle.sku_metrics = _compute_sku_metrics(
            items, orders, open_pos, rolls, pending, days_in_range, start_date, end_date
        )

        # Update launch dates
        _update_launch_dates(orders, rolls)

        # Build timeline
        bundle.timeline = _build_timelines(bundle.sku_metrics, open_pos, end_date)

        # Portfolio-level summary
        bundle.summary = _compute_summary(bundle.sku_metrics)

    except Exception as exc:
        bundle.error = str(exc)

    return bundle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_item_filters(items: pd.DataFrame, filters: dict) -> pd.DataFrame:
    df = items.copy()

    # Always exclude cost centers starting with '1'
    df = df[~df["cost_center"].astype(str).str.startswith("1")]

    if filters.get("cost_centers"):
        df = df[df["cost_center"].isin(filters["cost_centers"])]
    if filters.get("suppliers"):
        df = df[df["supplier_number"].isin(filters["suppliers"])]
    if filters.get("price_classes"):
        df = df[df["price_class"].isin(filters["price_classes"])]
    if filters.get("product_lines"):
        df = df[df["product_line"].isin(filters["product_lines"])]
    if filters.get("sku_search"):
        q = filters["sku_search"].strip().upper()
        df = df[
            df["sku"].str.upper().str.contains(q, na=False)
            | df["sku_description"].str.upper().str.contains(q, na=False)
        ]
    return df


def _compute_sku_metrics(
    items: pd.DataFrame,
    orders: pd.DataFrame,
    open_pos: pd.DataFrame,
    rolls: pd.DataFrame,
    pending: pd.DataFrame,
    days_in_range: int,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    today = date.today()

    # --- Sales aggregation ---
    if not orders.empty:
        sales_agg = (
            orders.groupby("base_sku")
            .agg(
                total_qty_sy=("quantity_sy", "sum"),
                orders_count=("order_line_id", "nunique"),
                backorder_count=("backorder_flag", lambda x: x[x].count()),
                strict_bo_qty_sy=("quantity_sy", lambda x: x[orders.loc[x.index, "strict_bo_flag"]].sum()),
                last_order_date=("order_entry_date", "max"),
                filled_count=("filled_flag", lambda x: x[x].count()),
            )
            .reset_index()
        )
        sales_agg["avg_daily_sales_sy"] = sales_agg["total_qty_sy"] / days_in_range
        sales_agg["fill_rate"] = (
            sales_agg["filled_count"] / sales_agg["orders_count"].clip(lower=1)
        ).clip(0, 1)
        sales_agg["days_since_last_sale"] = sales_agg["last_order_date"].apply(
            lambda d: (today - d).days if pd.notna(d) else None
        )
    else:
        sales_agg = pd.DataFrame(
            columns=[
                "base_sku", "total_qty_sy", "orders_count", "backorder_count",
                "strict_bo_qty_sy", "last_order_date", "filled_count",
                "avg_daily_sales_sy", "fill_rate", "days_since_last_sale",
            ]
        )

    # --- Inventory (rolls) ---
    if not rolls.empty:
        inv_agg = (
            rolls.groupby("base_sku")
            .apply(
                lambda g: pd.Series({
                    "inventory_sy": g["quantity_sy"].sum(),
                    "inventory_age_days": (
                        (g["quantity_sy"] * g["age_days"]).sum() / g["quantity_sy"].sum()
                        if g["quantity_sy"].sum() > 0 else 0.0
                    ),
                })
            )
            .reset_index()
        )
    else:
        inv_agg = pd.DataFrame(columns=["base_sku", "inventory_sy", "inventory_age_days"])

    # --- Open POs ---
    if not open_pos.empty:
        po_agg = (
            open_pos.groupby("base_sku")
            .agg(on_order_sy=("quantity_sy", "sum"))
            .reset_index()
        )
    else:
        po_agg = pd.DataFrame(columns=["base_sku", "on_order_sy"])

    # --- Pending POs (OPENPO_D) ---
    if not pending.empty:
        pend_agg = (
            pending.groupby("base_sku")
            .agg(po_pending_qty=("pending_qty_sy", "sum"))
            .reset_index()
        )
    else:
        pend_agg = pd.DataFrame(columns=["base_sku", "po_pending_qty"])

    # --- Merge ---
    item_base = items.drop_duplicates("base_sku")[
        [
            "base_sku", "sku_description", "cost_center", "price_class",
            "price_class_desc", "supplier_number", "product_line",
            "product_line_desc", "item_lead_time_days", "product_line_lead_time_days",
        ]
    ].copy()

    m = item_base.copy()
    for agg_df, col in [
        (sales_agg, "base_sku"),
        (inv_agg, "base_sku"),
        (po_agg, "base_sku"),
        (pend_agg, "base_sku"),
    ]:
        if not agg_df.empty:
            m = m.merge(agg_df, on="base_sku", how="left")

    # Fill nulls
    for col in [
        "total_qty_sy", "orders_count", "backorder_count", "strict_bo_qty_sy",
        "filled_count", "avg_daily_sales_sy", "inventory_sy", "inventory_age_days",
        "on_order_sy", "po_pending_qty",
    ]:
        if col not in m.columns:
            m[col] = 0.0
        m[col] = m[col].fillna(0.0)

    if "fill_rate" not in m.columns:
        m["fill_rate"] = 1.0
    m["fill_rate"] = m["fill_rate"].fillna(1.0)

    if "days_since_last_sale" not in m.columns:
        m["days_since_last_sale"] = None

    # Derived
    m["net_inventory_sy"] = m["inventory_sy"] + m["on_order_sy"] + m["po_pending_qty"]
    m["days_of_inventory"] = (
        m["inventory_sy"] / m["avg_daily_sales_sy"].replace(0, np.nan)
    ).fillna(_INF)
    m["stock_turn"] = (
        (m["avg_daily_sales_sy"] * 365) / m["inventory_sy"].replace(0, np.nan)
    ).fillna(0.0)

    # Lead time (use item-level if > 0, else product-line-level, else 30)
    m["lead_time_days"] = m["item_lead_time_days"].where(
        m["item_lead_time_days"] > 0, m["product_line_lead_time_days"]
    ).replace(0, 30)

    # Runout risk: current inventory runs out before next PO arrives
    m["days_until_stockout"] = (
        m["inventory_sy"] / m["avg_daily_sales_sy"].replace(0, np.nan)
    ).fillna(_INF)
    m["runout_risk"] = (
        (m["days_until_stockout"] < m["lead_time_days"])
        & (m["on_order_sy"] == 0)
        & (m["inventory_sy"] > 0)
        & (m["avg_daily_sales_sy"] > 0)
    )

    # SKU rating A/B/C/D by orders_count quartile
    m = _assign_ratings(m)

    # Launch dates
    launch_dates = get_all_launch_dates()
    m["launch_date"] = m["base_sku"].map(launch_dates)

    # Stock-turn targets (resolved per-SKU attributes)
    m["stockturn_target"] = m.apply(
        lambda row: resolve_target(
            cost_center=str(row.get("cost_center", "")),
            price_class=str(row.get("price_class", "")),
            product_line=str(row.get("product_line", "")),
            supplier=str(row.get("supplier_number", "")),
            sku=str(row.get("base_sku", "")),
        )[0],
        axis=1,
    )

    # Alert flags
    m["is_new"] = m["launch_date"].apply(
        lambda d: (today - d).days < 180 if pd.notna(d) else False
    )
    target_doi = 365.0 / m["stockturn_target"].replace(0, 4.0)
    m["overstock_flag"] = (
        (m["days_of_inventory"].replace(_INF, np.nan) > target_doi * 2)
        & (m["inventory_sy"] > 0)
        & ~m["is_new"]
    ).fillna(False)
    m["excess_order_flag"] = (
        (m["net_inventory_sy"] / m["avg_daily_sales_sy"].replace(0, np.nan) > target_doi * 2.5)
        & (m["on_order_sy"] > 0)
        & ~m["is_new"]
    ).fillna(False)
    m["stockout_flag"] = (
        (m["inventory_sy"] == 0)
        & (m["avg_daily_sales_sy"] > 0)
    )

    return m.rename(columns={"base_sku": "sku"})


def _assign_ratings(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or df["orders_count"].sum() == 0:
        df["sku_rating"] = "D"
        return df
    q75 = df["orders_count"].quantile(0.75)
    q50 = df["orders_count"].quantile(0.50)
    q25 = df["orders_count"].quantile(0.25)

    def _rate(cnt):
        if cnt >= q75:
            return "A"
        if cnt >= q50:
            return "B"
        if cnt >= q25:
            return "C"
        return "D"

    df["sku_rating"] = df["orders_count"].apply(_rate)
    return df


def _update_launch_dates(orders: pd.DataFrame, rolls: pd.DataFrame) -> None:
    if not orders.empty:
        earliest_orders = (
            orders.groupby("base_sku")["order_entry_date"].min()
        )
        for sku, d in earliest_orders.items():
            if pd.notna(d):
                set_launch_date(str(sku), d)

    if not rolls.empty:
        earliest_rolls = rolls.groupby("base_sku")["receive_date"].min()
        for sku, d in earliest_rolls.items():
            if pd.notna(d):
                set_launch_date(str(sku), d)


def _build_timelines(
    sku_metrics: pd.DataFrame,
    open_pos: pd.DataFrame,
    end_date: date,
) -> dict[str, pd.DataFrame]:
    """Build a 180-day forward projection per SKU."""
    today = date.today()
    horizon = 180
    dates = [today + timedelta(days=i) for i in range(horizon + 1)]

    timelines: dict[str, pd.DataFrame] = {}

    if sku_metrics.empty:
        return timelines

    # Group open POs by SKU → list of (eta_date, qty_sy)
    po_events: dict[str, list[tuple[date, float]]] = {}
    if not open_pos.empty:
        for _, row in open_pos.iterrows():
            sku = str(row.get("base_sku", row.get("sku", "")))
            eta = row.get("eta_date")
            qty = float(row.get("quantity_sy", 0))
            if qty > 0 and pd.notna(eta):
                po_events.setdefault(sku, []).append((eta, qty))

    for _, row in sku_metrics.iterrows():
        sku = str(row["sku"])
        inv_sy = float(row.get("inventory_sy", 0))
        avg_daily = float(row.get("avg_daily_sales_sy", 0))
        bo_qty = float(row.get("strict_bo_qty_sy", 0))
        lead_time = int(row.get("lead_time_days", 30))

        records = []
        current_inv = inv_sy
        remaining_bo = bo_qty

        for i, d in enumerate(dates):
            # PO receipts arriving today
            incoming = sum(qty for (eta, qty) in po_events.get(sku, []) if eta == d)
            # Daily consumption
            consumed = avg_daily

            # Apply backorder demand immediately on day 0 (already owed)
            if i == 0:
                current_inv = max(current_inv - remaining_bo, 0)

            current_inv = max(current_inv + incoming - consumed, 0)

            records.append({
                "date": d,
                "inventory_sy": round(current_inv, 2),
                "incoming_sy": round(incoming, 2),
                "consumed_sy": round(consumed, 2),
                "stockout": current_inv <= 0 and avg_daily > 0,
            })

        # If no POs and will stock out: show next reorder point
        if not po_events.get(sku):
            days_left = inv_sy / avg_daily if avg_daily > 0 else _INF
            for rec in records:
                idx = records.index(rec)
                if idx == int(days_left):
                    rec["reorder_point"] = True
                else:
                    rec["reorder_point"] = False
                rec["hypothetical_receipt"] = (
                    True if idx == int(days_left) + lead_time else False
                )
        else:
            for rec in records:
                rec["reorder_point"] = False
                rec["hypothetical_receipt"] = False

        timelines[sku] = pd.DataFrame(records)

    return timelines


def _compute_summary(sku_metrics: pd.DataFrame) -> dict:
    if sku_metrics.empty:
        return {}

    total_inv = sku_metrics["inventory_sy"].sum()
    total_daily = sku_metrics["avg_daily_sales_sy"].sum()
    total_orders = sku_metrics["orders_count"].sum()
    total_filled = sku_metrics["filled_count"].sum() if "filled_count" in sku_metrics.columns else 0

    doi_values = sku_metrics.loc[
        sku_metrics["days_of_inventory"] < _INF, "days_of_inventory"
    ]

    return {
        "total_skus": len(sku_metrics),
        "stock_turn": round((total_daily * 365) / total_inv, 2) if total_inv > 0 else 0.0,
        "fill_rate": round(total_filled / total_orders, 4) if total_orders > 0 else 1.0,
        "days_of_inventory": round(doi_values.median(), 1) if not doi_values.empty else 0.0,
        "aging_bad_sku_count": int((sku_metrics["days_since_last_sale"].fillna(0) >= 540).sum()),
        "runout_sku_count": int(sku_metrics["runout_risk"].sum()),
        "overstock_count": int(sku_metrics["overstock_flag"].sum()),
        "excess_order_count": int(sku_metrics["excess_order_flag"].sum()),
        "stockout_count": int(sku_metrics["stockout_flag"].sum()),
        "total_inventory_sy": round(total_inv, 1),
    }
