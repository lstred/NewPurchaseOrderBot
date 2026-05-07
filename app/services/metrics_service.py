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
    # po_events: {base_sku: [{order_number, eta_date, quantity_sy, supplier_number}, ...]}
    # Used for the PO table and on-demand timeline building (replaces pre-built timeline dict)
    po_events: dict = field(default_factory=dict)
    # timeline: lazily populated by get_sku_timeline() — only built for SKUs actually viewed
    timeline: dict = field(default_factory=dict)
    filter_values: pd.DataFrame = field(default_factory=pd.DataFrame)
    summary: dict = field(default_factory=dict)
    refresh_info: dict = field(default_factory=dict)  # {refreshed: [...], cached: [...], ts_ok: bool}
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
        from app.data import cache as _cache

        # ── Smart refresh: consult sysTableUpdates ────────────────────────────
        current_ts  = _cache.fetch_timestamps()
        all_datasets = set(_cache.DATASET_TABLES.keys())
        stale = _cache.compute_stale(current_ts, str(start_date), str(end_date))
        will_cache = all_datasets - stale

        def _use_or_load(key: str, loader_fn):
            """Return cached DF if not stale, else call loader_fn and cache result."""
            if key not in stale:
                cached = _cache.get_df(key)
                if cached is not None:
                    return cached
            df = loader_fn()
            _cache.set_df(key, df)
            return df

        # ── filter_values ─────────────────────────────────────────────────────
        bundle.filter_values = _use_or_load("filter_values", load_filter_values)

        # ── items (full, unfiltered — needed for complete alias resolution) ───
        bundle.items = _use_or_load("items", load_items)

        if bundle.items.empty:
            bundle.error = "No item data returned. Check database connection."
            return bundle

        # Always build sku_metrics for ALL non-'1xx' items.
        # Sidebar filters (CC, supplier, price class, etc.) are UI-only and applied
        # in OverviewTab._filter_metrics() — not at load time.  This ensures every
        # price class / supplier / cost centre is accessible via the sidebar regardless
        # of what was selected when the user last hit "Refresh Data".
        items = _apply_item_filters(bundle.items, {})
        active_skus = set(items["base_sku"].unique())

        # ── orders (use FULL items for alias resolution, not filtered) ────────
        bundle.orders = _use_or_load(
            "orders",
            lambda: load_orders(start_date, end_date, items_df=bundle.items),
        )

        # ── open purchase orders ──────────────────────────────────────────────
        bundle.open_pos = _use_or_load(
            "open_pos",
            lambda: load_open_pos(items_df=bundle.items),
        )

        # ── physical rolls ────────────────────────────────────────────────────
        bundle.rolls = _use_or_load(
            "rolls",
            lambda: load_rolls(items_df=bundle.items),
        )

        # ── pending POs (OPENPO_D) ────────────────────────────────────────────
        bundle.pending_pos = _use_or_load(
            "pending_pos",
            lambda: load_pending_pos(items_df=bundle.items),
        )

        # ── Filter all datasets to the active-SKU scope ───────────────────────
        def _filt(df: pd.DataFrame) -> pd.DataFrame:
            if df.empty or "base_sku" not in df.columns:
                return df
            return df[df["base_sku"].isin(active_skus)]

        orders   = _filt(bundle.orders)
        open_pos = _filt(bundle.open_pos)
        rolls    = _filt(bundle.rolls)
        pending  = _filt(bundle.pending_pos)

        days_in_range = max((end_date - start_date).days + 1, 1)

        # Update launch dates FIRST so avg_daily_sales uses the correct per-SKU window
        _update_launch_dates(orders, rolls)
        launch_dates = get_all_launch_dates()

        # Aggregate per base_sku
        bundle.sku_metrics = _compute_sku_metrics(
            items, orders, open_pos, rolls, pending, days_in_range, start_date, end_date,
            launch_dates,
        )

        # Build lightweight po_events dict (cheap — just aggregates open_pos rows)
        # Full timelines are built lazily via get_sku_timeline() when a SKU is viewed
        bundle.po_events = _build_po_events(open_pos)

        # Portfolio-level summary
        bundle.summary = _compute_summary(bundle.sku_metrics)

        # Persist timestamps only after a fully successful load
        _cache.commit(current_ts, str(start_date), str(end_date))

        bundle.refresh_info = {
            "refreshed": sorted(stale),
            "cached":    sorted(will_cache),
            "ts_ok":     bool(current_ts),
        }

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
    launch_dates: dict,
) -> pd.DataFrame:
    today = date.today()

    # Floor date — launch dates older than this are capped so new-item avg_daily
    # is not diluted by the full query window.
    _FLOOR = date(2025, 8, 5)

    # Vectorised effective_days lookup (replaces per-row apply over thousands of SKUs)
    _today_ts = pd.Timestamp(end_date)

    def _effective_days_series(skus: pd.Series) -> pd.Series:
        """For each SKU, return days_in_range capped to (end_date - max(launch, FLOOR)) + 1."""
        # Map SKU → launch date (NaT if missing)
        ld = skus.map(launch_dates)
        ld = pd.to_datetime(ld, errors="coerce")
        floor_ts = pd.Timestamp(_FLOOR)
        # max(launch, FLOOR); where missing keep NaT
        eff_start = ld.where(ld.notna(), other=pd.NaT)
        eff_start = eff_start.where(eff_start >= floor_ts, floor_ts)
        eff_days = (_today_ts - eff_start).dt.days + 1
        eff_days = eff_days.fillna(days_in_range).clip(lower=1).astype(int)
        return eff_days

    # --- Sales aggregation ---
    if not orders.empty:
        # Pre-compute boolean-weighted columns so .agg can use plain "sum" (much
        # faster than per-group lambdas which iterate every row in Python).
        _o = orders
        # Original code used `.count()` on a boolean series filtered by itself,
        # which counts True values. `.sum()` of a bool column does the same job
        # at vectorised C speed.
        _bo_flag     = _o["backorder_flag"].astype(bool)
        _strict_flag = _o["strict_bo_flag"].astype(bool)
        _filled_flag = _o["filled_flag"].astype(bool)
        _qty         = pd.to_numeric(_o["quantity_sy"], errors="coerce").fillna(0.0)
        _agg_input = pd.DataFrame({
            "base_sku":         _o["base_sku"],
            "quantity_sy":      _qty,
            "order_line_id":    _o["order_line_id"],
            "_bo_int":          _bo_flag.astype("int64"),
            "_strict_qty":      _qty * _strict_flag,
            "_filled_int":      _filled_flag.astype("int64"),
            "order_entry_date": _o["order_entry_date"],
        })
        sales_agg = (
            _agg_input.groupby("base_sku")
            .agg(
                total_qty_sy=("quantity_sy", "sum"),
                orders_count=("order_line_id", "nunique"),
                backorder_count=("_bo_int", "sum"),
                strict_bo_qty_sy=("_strict_qty", "sum"),
                last_order_date=("order_entry_date", "max"),
                filled_count=("_filled_int", "sum"),
            )
            .reset_index()
        )
        sales_agg["effective_days"] = _effective_days_series(sales_agg["base_sku"])
        sales_agg["avg_daily_sales_sy"] = sales_agg["total_qty_sy"] / sales_agg["effective_days"]
        sales_agg["fill_rate"] = (
            sales_agg["filled_count"] / sales_agg["orders_count"].clip(lower=1)
        ).clip(0, 1)
        # Vectorised days_since_last_sale (replaces per-row apply)
        _last = pd.to_datetime(sales_agg["last_order_date"], errors="coerce")
        sales_agg["days_since_last_sale"] = (_today_ts - _last).dt.days
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
        # Vectorized weighted average age — much faster than groupby.apply()
        _r = rolls.copy()
        _r["_age_w"] = _r["quantity_sy"] * _r["age_days"]
        inv_agg = (
            _r.groupby("base_sku")
            .agg(inventory_sy=("quantity_sy", "sum"), _age_w_sum=("_age_w", "sum"))
            .reset_index()
        )
        inv_agg["inventory_age_days"] = (
            inv_agg["_age_w_sum"] / inv_agg["inventory_sy"].replace(0, np.nan)
        ).fillna(0.0)
        inv_agg = inv_agg.drop(columns=["_age_w_sum"])
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
    # Strip base_sku whitespace — defense-in-depth; loaders already do this,
    # but aggregation DataFrames may carry any residual padding from cache.
    items = items.copy()
    items["base_sku"] = items["base_sku"].str.strip()
    for _agg in (sales_agg, inv_agg, po_agg, pend_agg):
        if not _agg.empty and "base_sku" in _agg.columns:
            _agg["base_sku"] = _agg["base_sku"].str.strip()

    # Sort so direct items (sku == base_sku) come first before drop_duplicates.
    # This ensures the base item's own price_class / cost_center / etc. are used
    # rather than an alias item's attributes — which fixes filtering by price class
    # when alias items from other price classes point to this base SKU.
    _is_direct = items["sku"].str.strip() == items["base_sku"].str.strip()
    item_base = (
        items.assign(_is_direct=_is_direct)
        .sort_values("_is_direct", ascending=False)  # True (direct) first
        .drop_duplicates("base_sku")
        .drop(columns=["_is_direct"])
    )[
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

    # Runout risk: total supply (inventory + on_order) covers less than 1.5× lead-time demand.
    # Mirrors the overstock formula: both use supply vs. multiples of lead_time_demand.
    m["days_until_stockout"] = (
        m["inventory_sy"] / m["avg_daily_sales_sy"].replace(0, np.nan)
    ).fillna(_INF)
    _runout_threshold = m["avg_daily_sales_sy"] * m["lead_time_days"] * 1.5
    m["runout_risk"] = (
        ((m["inventory_sy"] + m["on_order_sy"]) < _runout_threshold)
        & (m["inventory_sy"] > 0)
        & (m["avg_daily_sales_sy"] > 0)
    )

    # SKU rating A/B/C/D by orders_count quartile
    m = _assign_ratings(m)

    # Launch dates — floor at Aug 5 2025 so displayed date is consistent with
    # what avg_daily_sales is calculated against (effective_days uses same floor).
    _FLOOR_DISPLAY = date(2025, 8, 5)
    _ld_series = pd.to_datetime(m["base_sku"].map(launch_dates), errors="coerce")
    _floor_ts = pd.Timestamp(_FLOOR_DISPLAY)
    _ld_floored = _ld_series.where(_ld_series >= _floor_ts, _floor_ts)
    # Convert back to python date objects (preserves NaT → NaT → will be NaT in m)
    m["launch_date"] = _ld_floored.dt.date.where(_ld_series.notna(), None)

    # Stock-turn targets — vectorised resolution.  Original code used df.apply(axis=1)
    # which loops in Python over every SKU.  Here we layer overrides from least
    # specific (sup) up to most specific (sku); later .where() keeps higher
    # precedence values, exactly mirroring resolve_target() precedence.
    from app.data.store import get_all_targets as _get_all_targets
    _all_targets = _get_all_targets()  # single disk read
    _global_target = float(_all_targets.get("global", 4.0))

    def _scoped_map(prefix: str) -> dict[str, float]:
        return {k[len(prefix):]: float(v) for k, v in _all_targets.items()
                if k.startswith(prefix)}

    targets = pd.Series(_global_target, index=m.index, dtype="float64")
    for col, prefix in [
        ("supplier_number", "sup:"),
        ("product_line",    "pl:"),
        ("price_class",     "pc:"),
        ("cost_center",     "cc:"),
        ("base_sku",        "sku:"),
    ]:
        sub = _scoped_map(prefix)
        if not sub or col not in m.columns:
            continue
        overrides = m[col].astype(str).map(sub)
        targets = overrides.where(overrides.notna(), targets)
    m["stockturn_target"] = targets

    # Alert flags — vectorised is_new check
    _ld_for_new = pd.to_datetime(m["launch_date"], errors="coerce")
    m["is_new"] = ((_today_ts - _ld_for_new).dt.days < 180).fillna(False)

    # Overstock: project inventory AFTER the next on-order PO arrives.
    # Before the PO lands, avg_daily × lead_time worth of stock will have sold.
    # projected_post_receipt = max(inventory - daily×lead_time, 0) + on_order
    # Flag overstock when that figure exceeds 3× one lead-time's worth of demand.
    _lt_demand = m["avg_daily_sales_sy"] * m["lead_time_days"]
    _inv_at_arrival = (m["inventory_sy"] - _lt_demand).clip(lower=0)
    _proj_post_receipt = _inv_at_arrival + m["on_order_sy"]
    m["overstock_flag"] = (
        (_proj_post_receipt > _lt_demand * 3)
        & (m["avg_daily_sales_sy"] > 0)
        & (m["inventory_sy"] > 0)
        & ~m["is_new"]
    ).fillna(False)
    # Excess-order: same projected formula but 2.5× threshold and requires open PO
    m["excess_order_flag"] = (
        (_proj_post_receipt > _lt_demand * 2.5)
        & (m["on_order_sy"] > 0)
        & (m["avg_daily_sales_sy"] > 0)
        & ~m["is_new"]
    ).fillna(False)
    m["stockout_flag"] = (
        (m["inventory_sy"] == 0)
        & (m["avg_daily_sales_sy"] > 0)
    )

    return m.rename(columns={"base_sku": "sku"})


# ---------------------------------------------------------------------------
# PO events (lightweight — replaces pre-built timeline dict)
# ---------------------------------------------------------------------------

def _build_po_events(open_pos: pd.DataFrame) -> dict:
    """
    Build {base_sku: [{'order_number', 'eta_date', 'quantity_sy', 'supplier_number'}, ...]}
    from the filtered open_pos DataFrame.  Cheap to build (no loops per day).
    """
    events: dict = {}
    if open_pos.empty:
        return events
    for _, row in open_pos.iterrows():
        sku = str(row.get("base_sku", row.get("sku", ""))).strip()
        eta = row.get("eta_date")
        qty = float(row.get("quantity_sy", 0))
        if qty <= 0 or not pd.notna(eta):
            continue
        events.setdefault(sku, []).append({
            "order_number":   str(row.get("order_number", "")),
            "eta_date":       eta,
            "quantity_sy":    qty,
            "supplier_number": str(row.get("supplier_number", "")),
        })
    return events


def get_sku_timeline(sku: str, bundle: "DatasetBundle") -> Optional[pd.DataFrame]:
    """
    Return (and cache) the 180-day inventory projection DataFrame for a single SKU.
    Builds lazily the first time a SKU is requested — avoids building 17 000+ DFs up front.
    """
    if sku in bundle.timeline:
        return bundle.timeline[sku]

    row_df = bundle.sku_metrics[bundle.sku_metrics["sku"] == sku]
    if row_df.empty:
        return None

    row = row_df.iloc[0]
    df = _build_single_timeline(
        inv_sy=float(row.get("inventory_sy", 0)),
        avg_daily=float(row.get("avg_daily_sales_sy", 0)),
        bo_qty=float(row.get("strict_bo_qty_sy", 0)),
        lead_time=int(row.get("lead_time_days", 30)),
        po_events=bundle.po_events.get(sku, []),
    )
    bundle.timeline[sku] = df  # cache for subsequent views
    return df


def _build_single_timeline(
    inv_sy: float,
    avg_daily: float,
    bo_qty: float,
    lead_time: int,
    po_events: list,           # list of {'eta_date': date, 'quantity_sy': float}
    horizon: int = 180,
) -> pd.DataFrame:
    """Build a horizon-day forward projection for one SKU."""
    today = date.today()
    dates = [today + timedelta(days=i) for i in range(horizon + 1)]

    # Map eta_date → total incoming qty for fast day lookup
    receipt_by_day: dict[date, float] = {}
    for ev in po_events:
        d = ev["eta_date"]
        if pd.notna(d):
            receipt_by_day[d] = receipt_by_day.get(d, 0.0) + ev["quantity_sy"]

    records = []
    current_inv = inv_sy
    remaining_bo = bo_qty
    has_pos = bool(po_events)

    for i, d in enumerate(dates):
        incoming = receipt_by_day.get(d, 0.0)
        consumed = avg_daily
        if i == 0:
            current_inv = max(current_inv - remaining_bo, 0)
        current_inv = max(current_inv + incoming - consumed, 0)
        records.append({
            "date":         d,
            "inventory_sy": round(current_inv, 2),
            "incoming_sy":  round(incoming, 2),
            "consumed_sy":  round(consumed, 2),
            "stockout":     current_inv <= 0 and avg_daily > 0,
        })

    # If no POs: mark hypothetical reorder/receipt day
    if not has_pos:
        days_left = inv_sy / avg_daily if avg_daily > 0 else _INF
        reorder_idx  = int(days_left) if days_left < _INF else -1
        receipt_idx  = reorder_idx + lead_time if reorder_idx >= 0 else -1
        for idx, rec in enumerate(records):
            rec["reorder_point"]       = (idx == reorder_idx)
            rec["hypothetical_receipt"] = (idx == receipt_idx)
    else:
        for rec in records:
            rec["reorder_point"]       = False
            rec["hypothetical_receipt"] = False

    return pd.DataFrame(records)


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
