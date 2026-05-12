"""
Daily Brief generator (v4.0).

Replaces the v3.x Q&A SQL chat with a once-a-day automated executive briefing
on the two business priorities:
  1. Avoid inventory that sits > 12 months (overstock / aging).
  2. Avoid being out of stock when customers call (stockout / runout risk).

The brief is structured to lead with **yesterday's changes** (new POs, receipts,
sales, backorders) that affect either priority, then surface the worst-offender
SKU-level lists, then synthesize a recommended action list.

Architecture:
  - Numbers are computed in Python from the existing DatasetBundle plus a
    couple of targeted yesterday-only SQL pulls. Deterministic, fast, free.
  - The AI's job is purely to *synthesize* a thorough yet at-a-glance
    narrative. It does not query, compute, or do math.
  - System prompt + collected data is ~12–20K tokens; output ~3K tokens.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
from numpy import where as np_where
from sqlalchemy import text

from app.ai.providers import call_provider, AIError, DEFAULT_MODELS
from app.ai.schema import build_brief_system_prompt
from app.data.db import read_dataframe
from app.data.store import get_prev_brief_snapshot, save_brief_snapshot
from app.services.metrics_service import DatasetBundle


# Approx pricing per 1M tokens (USD) — used only for the cost estimate footer.
# Update freely; this is informational only.
_PRICING = {
    # OpenAI
    "gpt-5":           (1.25, 10.00),
    "gpt-5-mini":      (0.25,  2.00),
    "gpt-5-nano":      (0.05,  0.40),
    "gpt-4o":          (2.50, 10.00),
    "gpt-4o-mini":     (0.15,  0.60),
    "gpt-4.1":         (2.00,  8.00),
    "gpt-4.1-mini":    (0.40,  1.60),
    "o1-mini":         (3.00, 12.00),
    # Anthropic
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-opus-4-5":   (15.00, 75.00),
    "claude-haiku-4-5":  (1.00,  5.00),
    # Google
    "gemini-2.5-flash":      (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-pro":        (1.25, 10.00),
}


@dataclass
class BriefData:
    """All data tables fed to the AI for synthesis."""

    target_date: date
    portfolio_kpis: dict = field(default_factory=dict)
    # Filter notes (eligibility upstream of every problem table)
    filter_summary: dict = field(default_factory=dict)
    # Yesterday's activity (already filtered to eligible SKUs)
    yesterday_new_pos: pd.DataFrame = field(default_factory=pd.DataFrame)
    yesterday_receipts: pd.DataFrame = field(default_factory=pd.DataFrame)
    yesterday_sales: pd.DataFrame = field(default_factory=pd.DataFrame)
    yesterday_backorders: pd.DataFrame = field(default_factory=pd.DataFrame)
    # Top-of-brief actionable concerns (cross-portfolio)
    top_concerns: pd.DataFrame = field(default_factory=pd.DataFrame)
    # Aging on-hand inventory (>= 365 days). Drives the per-CC clearance list.
    aging_inventory: pd.DataFrame = field(default_factory=pd.DataFrame)
    # Open POs whose arrival pushes DOI past the lead-time-aware overstock floor.
    excessive_incoming_pos: pd.DataFrame = field(default_factory=pd.DataFrame)
    # POs entered yesterday that breach the same overstock floor.
    redflag_new_pos: pd.DataFrame = field(default_factory=pd.DataFrame)
    # SKUs with active demand and zero valid open PO — buyer must place a buy.
    needs_reorder_no_po: pd.DataFrame = field(default_factory=pd.DataFrame)
    # Massive cash-on-floor overstock with a valid inbound PO — high exposure
    # multi-year-cover positions that need the inbound defused first.
    massive_overstock: pd.DataFrame = field(default_factory=pd.DataFrame)
    # Per-cost-center breakdown — only CCs with at least one concern populated
    # Shape: { cc_code: { 'name': str, 'kpis': dict, 'tables': { table_name: DataFrame } } }
    cost_center_problems: dict = field(default_factory=dict)


@dataclass
class BriefResult:
    target_date: date
    markdown: str
    model: str
    provider: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    elapsed_sec: float = 0.0
    generated_at: datetime = field(default_factory=datetime.now)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Yesterday-only SQL (targeted pulls — not in the standard DatasetBundle)
# ---------------------------------------------------------------------------

_YDAY_NEW_POS_SQL = """
SELECT
    LTRIM(RTRIM(CAST(o.[ORDER#] AS VARCHAR)))           AS order_number,
    LTRIM(RTRIM(o.[ITEM_MFGR_COLOR_PAT]))               AS sku,
    LTRIM(RTRIM(COALESCE(o.[ITEM_DESC_1], '')))         AS description,
    o.[QUANTITY_ORDERED]                                AS qty_native,
    LTRIM(RTRIM(o.[UNIT_OF_MEASURE]))                   AS uom,
    LTRIM(RTRIM(CAST(o.[SUPPLIER#] AS VARCHAR)))        AS supplier,
    o.[PO_ETA_DATE]                                     AS eta_date,
    LTRIM(RTRIM(COALESCE(o.[SALESPERSON_DESC], '')))    AS operator
FROM dbo._ORDERS o
WHERE o.[N_NOT_INVENTORY] = 'Y'
  AND CAST(o.[ACCOUNT#I] AS INT) = 1
  AND o.[QUANTITY_ORDERED] > 0
  AND CAST(o.[ORDER_ENTRY_DATE_YYYYMMDD] AS BIGINT) = :ymd
"""

_YDAY_RECEIPTS_SQL = """
SELECT
    LTRIM(RTRIM(CAST(o.[NPO#] AS VARCHAR)))             AS po_number,
    LTRIM(RTRIM(o.NMFGR)) + LTRIM(RTRIM(o.NCOLOR)) + LTRIM(RTRIM(o.NPAT)) AS sku,
    o.NRECEI                                            AS qty_received,
    o.NDATE                                             AS receipt_date
FROM dbo.OPENIV o
WHERE o.NREFTY = 'R'
  AND CAST(CONVERT(VARCHAR(8), o.NDATE, 112) AS BIGINT) = :ymd
"""


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------

# Items with ICLAST length > 1 are "trim" items — too low-importance to brief.
_TRIM_ICLAST_MAX_LEN = 1
# Only items launched at least this many days ago are mature enough to brief.
_MIN_AGE_DAYS = 180
# Minimum order_number length for POs that count toward actionable concerns.
# Placeholder/manual entries like "0", "1", "99" are filtered out per buyer
# instruction (v4.8). Real PO numbers are always 3+ characters.
_MIN_ORDER_NUMBER_LEN = 3
# DOI thresholds (days) above which an open PO is flagged as "incoming
# overstock" — v5.0 splits by lead time per buyer instruction:
#   - Slow-LT items (lead_time_days  > 99) keep the v4.8 700-day floor.
#   - Fast-LT items (lead_time_days <= 99) use a tighter 365-day floor,
#     because a quick re-buy can correct course inside one cycle.
_INCOMING_OVERSTOCK_DOI_DAYS_SLOW_LT = 700
_INCOMING_OVERSTOCK_DOI_DAYS_FAST_LT = 365
_LEAD_TIME_FAST_THRESHOLD_DAYS = 99


def _overstock_doi_threshold(sm: pd.DataFrame) -> pd.Series:
    """Return the per-row overstock DOI threshold (days) for every SKU.

    v5.0 — fast-LT SKUs (<= 99-day lead time) use a 365-day floor so the buyer
    can react inside a single re-buy cycle; slow-LT SKUs keep the 700-day floor
    set in v4.8.
    """
    lt = pd.to_numeric(sm.get("lead_time_days"), errors="coerce").fillna(0)
    fast = lt <= _LEAD_TIME_FAST_THRESHOLD_DAYS
    return pd.Series(
        np_where(fast, _INCOMING_OVERSTOCK_DOI_DAYS_FAST_LT, _INCOMING_OVERSTOCK_DOI_DAYS_SLOW_LT),
        index=sm.index, dtype="float64",
    )


def _filter_valid_pos(open_pos: pd.DataFrame) -> pd.DataFrame:
    """Drop placeholder POs (order_number shorter than _MIN_ORDER_NUMBER_LEN).

    The buyer's source system contains placeholder PO rows with one- or
    two-character `ORDER#` values (e.g. "0", "1") that are never real orders.
    They distort every concern that depends on `on_order_sy`.  v4.8 strips
    them upstream so the entire brief reasons about real POs only.
    """
    if open_pos is None or open_pos.empty or "order_number" not in open_pos.columns:
        return open_pos
    onum = open_pos["order_number"].astype(str).str.strip()
    return open_pos[onum.str.len() >= _MIN_ORDER_NUMBER_LEN].copy()


def _recompute_on_order_from_valid_pos(
    sm: pd.DataFrame, valid_open_pos: pd.DataFrame
) -> pd.DataFrame:
    """Replace `on_order_sy` and `days_of_inventory_projected` on `sm` using
    only the filtered valid POs.  Pure function — returns a new frame.
    """
    sm = sm.copy()
    if valid_open_pos is None or valid_open_pos.empty or "base_sku" not in valid_open_pos.columns:
        sm["on_order_sy"] = 0.0
    else:
        agg = (
            valid_open_pos.groupby("base_sku")["quantity_sy"].sum()
            .rename("on_order_sy_valid")
        )
        sm["on_order_sy"] = sm["sku"].map(agg).fillna(0.0).astype(float)
    # Recompute DOI projection so every downstream filter sees the corrected value.
    avg = sm["avg_daily_sales_sy"].astype(float)
    inv = sm["inventory_sy"].astype(float).fillna(0.0)
    on_order = sm["on_order_sy"].astype(float).fillna(0.0)
    doi = (inv + on_order) / avg.where(avg > 0, other=pd.NA)
    doi = pd.to_numeric(doi, errors="coerce").replace(
        [float("inf"), float("-inf")], pd.NA
    )
    sm["days_of_inventory_projected"] = doi
    return sm


def _apply_eligibility_filter(sm: pd.DataFrame, target_date: date) -> tuple[pd.DataFrame, dict]:
    """Drop trim items (ICLAST length > 1) and items younger than 6 months.

    Returns (filtered_sm, summary_dict) where summary_dict reports how many
    SKUs were dropped at each stage so the brief can disclose its scope.
    """
    if sm.empty:
        return sm, {"input": 0, "after_trim": 0, "after_age": 0, "trim_dropped": 0, "young_dropped": 0}

    total = int(len(sm))

    if "iclast" in sm.columns:
        iclast_len = sm["iclast"].fillna("").astype(str).str.strip().str.len()
        not_trim = iclast_len <= _TRIM_ICLAST_MAX_LEN
    else:
        not_trim = pd.Series(True, index=sm.index)

    sm_t = sm[not_trim].copy()
    after_trim = int(len(sm_t))

    cutoff = target_date - timedelta(days=_MIN_AGE_DAYS)
    if "launch_date" in sm_t.columns:
        ld = pd.to_datetime(sm_t["launch_date"], errors="coerce")
        # Treat NaT as ineligible (we cannot prove it's mature)
        mature = ld.notna() & (ld.dt.date <= cutoff)
        sm_e = sm_t[mature].copy()
    else:
        sm_e = sm_t

    after_age = int(len(sm_e))
    return sm_e, {
        "input": total,
        "after_trim": after_trim,
        "after_age": after_age,
        "trim_dropped": total - after_trim,
        "young_dropped": after_trim - after_age,
        "min_age_days": _MIN_AGE_DAYS,
    }


def gather_brief_data(target_date: date, bundle: DatasetBundle) -> BriefData:
    """Collect every input table the AI needs to write the brief.

    Eligibility (applied upstream of every problem table):
      - Trim items (ITEM.ICLAST length > 1 character) are excluded.
      - SKUs launched within the last 6 months are excluded.

    Pulls live yesterday-activity from SQL + slices the in-memory DatasetBundle
    for the worst-offender lists. All numbers are deterministic Python; the AI
    never has to compute anything.
    """
    data = BriefData(target_date=target_date)
    sm_all = bundle.sku_metrics
    if sm_all.empty:
        return data

    sm, filter_summary = _apply_eligibility_filter(sm_all, target_date)
    data.filter_summary = filter_summary
    if sm.empty:
        return data

    # v4.8 — strip placeholder POs (order_number shorter than 3 chars) from the
    # entire pipeline. Recompute on_order_sy + DOI projection from the valid
    # subset so every downstream concern reasons about real POs only.
    valid_open_pos = _filter_valid_pos(bundle.open_pos)
    sm = _recompute_on_order_from_valid_pos(sm, valid_open_pos)

    ymd = int(target_date.strftime("%Y%m%d"))
    eligible_skus = set(sm["sku"].astype(str))

    # ------------------------------ Yesterday's NEW POs ------------------------------
    try:
        df = read_dataframe(_YDAY_NEW_POS_SQL, {"ymd": ymd})
        if not df.empty:
            df["sku"] = df["sku"].astype(str).str.strip()
            df = df[df["sku"].isin(eligible_skus)]
            # v4.8 — placeholder POs (order_number shorter than 3 chars) are
            # buyer-irrelevant; drop before any enrichment.
            if "order_number" in df.columns:
                onum = df["order_number"].astype(str).str.strip()
                df = df[onum.str.len() >= _MIN_ORDER_NUMBER_LEN]
        if not df.empty:
            # Enrich each PO row with the SKU's current avg-daily / inventory / DOI(proj)
            enrich_cols = [
                "sku", "sku_description", "avg_daily_sales_sy",
                "inventory_sy", "on_order_sy", "days_of_inventory_projected",
                "lead_time_days", "cost_center",
            ]
            sm_slim = sm[enrich_cols].copy()
            df = df.merge(sm_slim, on="sku", how="left")
            df["days_of_inventory_projected"] = df["days_of_inventory_projected"].replace(
                [float("inf"), float("-inf")], None
            )
        data.yesterday_new_pos = df
    except Exception:
        pass  # never let SQL errors break the brief

    # ------------------------------ Yesterday's receipts ------------------------------
    try:
        df = read_dataframe(_YDAY_RECEIPTS_SQL, {"ymd": ymd})
        if not df.empty:
            df["sku"] = df["sku"].astype(str).str.strip()
            df = df[df["sku"].isin(eligible_skus)]
        if not df.empty:
            # Enrich with current state to spot receipts that just landed on overstock
            enrich = sm[["sku", "sku_description", "inventory_sy", "on_order_sy",
                         "avg_daily_sales_sy", "days_of_inventory_projected",
                         "overstock_flag", "cost_center"]].copy()
            df = df.merge(enrich, on="sku", how="left")
            df["days_of_inventory_projected"] = df["days_of_inventory_projected"].replace(
                [float("inf"), float("-inf")], None
            )
        data.yesterday_receipts = df
    except Exception:
        pass

    # ------------------------------ Yesterday's sales ------------------------------
    if not bundle.orders.empty and "order_entry_date" in bundle.orders.columns:
        ord_df = bundle.orders
        ord_df = ord_df[ord_df["base_sku"].astype(str).isin(eligible_skus)]
        mask = ord_df["order_entry_date"] == target_date
        sales = ord_df[mask & (~ord_df["backorder_flag"])].copy()
        bo = ord_df[mask & ord_df["backorder_flag"]].copy()

        if not sales.empty:
            agg = (
                sales.groupby("base_sku")
                .agg(qty_sy=("quantity_sy", "sum"), lines=("order_line_id", "nunique"))
                .reset_index()
                .rename(columns={"base_sku": "sku"})
            )
            agg = agg.merge(
                sm[["sku", "sku_description", "inventory_sy", "avg_daily_sales_sy",
                    "days_of_inventory", "stockout_flag"]],
                on="sku", how="left",
            )
            agg = agg.sort_values("qty_sy", ascending=False).head(25)
            data.yesterday_sales = agg

        if not bo.empty:
            agg_bo = (
                bo.groupby("base_sku")
                .agg(bo_qty_sy=("quantity_sy", "sum"), bo_lines=("order_line_id", "nunique"))
                .reset_index()
                .rename(columns={"base_sku": "sku"})
            )
            agg_bo = agg_bo.merge(
                sm[["sku", "sku_description", "inventory_sy", "on_order_sy",
                    "avg_daily_sales_sy", "lead_time_days"]],
                on="sku", how="left",
            )
            agg_bo = agg_bo.sort_values("bo_qty_sy", ascending=False).head(25)
            data.yesterday_backorders = agg_bo

    # Yesterday's backorders are computed above; the next concern table is
    # aging inventory. v5.0 — active_stockouts / runout_risk /
    # overstock_with_open_po blocks were removed: stockouts and runout-risk
    # are folded into needs_reorder_no_po, and the open-PO overstock case is
    # subsumed by excessive_incoming_pos (DOI-aware) and massive_overstock.

    # ------------------------------ Aging inventory (>12 months sitting) ------------------------------
    # The first business priority: avoid inventory that sits > 12 months.
    aging = sm[
        (sm["inventory_age_days"] >= 365) & (sm["inventory_sy"] > 0)
    ].copy()
    if not aging.empty:
        # v4.7: severity-floor — keep every aged SKU with >= 250 SY on hand.
        aging = aging[aging["inventory_sy"] >= 250.0]
        aging = aging.sort_values("inventory_age_days", ascending=False).head(80)[
            ["sku", "sku_description", "inventory_sy", "inventory_age_days",
             "avg_daily_sales_sy", "days_since_last_sale", "on_order_sy",
             "supplier_number", "price_class_desc", "cost_center"]
        ]
        data.aging_inventory = aging

    # ------------------------------ Excessive incoming POs ------------------------------
    # v5.0 — any SKU with a valid open PO whose arrival pushes DOI(proj) past
    # its lead-time-aware overstock threshold (700d slow-LT / 365d fast-LT).
    _doi_floor = _overstock_doi_threshold(sm)
    inc = sm[
        (sm["on_order_sy"] > 0) & (sm["days_of_inventory_projected"] > _doi_floor)
        & (sm["avg_daily_sales_sy"] > 0)
    ].copy()
    if not inc.empty:
        inc["days_of_inventory_projected"] = inc["days_of_inventory_projected"].replace(
            [float("inf"), float("-inf")], 9999
        )
        # Same exposure-first sort as overstock_with_open_po so big cash items lead.
        inc["_exposure"] = inc["inventory_sy"].fillna(0) + inc["on_order_sy"].fillna(0)
        # v4.7: severity-floor — keep every incoming-overstock SKU with >= 1,000
        # SY combined exposure.
        inc = inc[inc["_exposure"] >= 1000.0]
        inc = inc.sort_values(["_exposure", "days_of_inventory_projected"], ascending=[False, False]).head(100)[
            ["sku", "sku_description", "inventory_sy", "on_order_sy",
             "avg_daily_sales_sy", "days_of_inventory_projected", "supplier_number",
             "price_class_desc", "cost_center"]
        ]
        data.excessive_incoming_pos = inc

    # ------------------------------ Portfolio KPIs ------------------------------
    sm_safe = sm.copy()
    sm_safe["doi_proj_safe"] = sm_safe["days_of_inventory_projected"].replace(
        [float("inf"), float("-inf")], None
    )
    twelve_mo_skus = int(((sm_safe["doi_proj_safe"] > 365) & sm_safe["doi_proj_safe"].notna()).sum())
    aging_skus = int(((sm["inventory_age_days"] >= 365) & (sm["inventory_sy"] > 0)).sum())

    data.portfolio_kpis = {
        "total_skus":        int(len(sm)),
        "total_inventory_sy": float(sm["inventory_sy"].sum()),
        "total_on_order_sy":  float(sm["on_order_sy"].sum()),
        "stock_turn":        float(bundle.summary.get("stock_turn", 0)),
        "fill_rate":         float(bundle.summary.get("fill_rate", 0)),
        "stockout_skus":     int(sm["stockout_flag"].sum()),
        "runout_risk_skus":  int(sm["runout_risk"].sum()),
        "overstock_skus":    int(sm["overstock_flag"].sum()),
        "twelve_month_doi_skus": twelve_mo_skus,
        "aging_365d_skus":   aging_skus,
    }

    # ------------------------------ Massive overstock (v4.8) ------------------------------
    # v4.8 — only items with a VALID inbound PO qualify (on_order_sy > 0 after
    # the placeholder-PO filter), AND combined inventory+on_order projects past
    # 700 days of cover. Pure on-hand overstock is handled separately as a
    # CC-section clearance fallback. The action here is always cancel/defer
    # the inbound — these are the items the buyer can fix today.
    mo = sm.copy()
    exposure = mo["inventory_sy"].fillna(0) + mo["on_order_sy"].fillna(0)
    doi_proj = mo["days_of_inventory_projected"].replace(
        [float("inf"), float("-inf")], 99999
    ).fillna(99999)
    avg_daily = mo["avg_daily_sales_sy"].fillna(0)
    has_inbound = mo["on_order_sy"].fillna(0) > 0
    big_exposure = exposure >= 3000
    # v5.0 — lead-time-aware overstock floor (700d slow-LT / 365d fast-LT).
    multi_year_cover = doi_proj >= _overstock_doi_threshold(mo)
    dead_with_inbound = (avg_daily <= 0) & (mo["on_order_sy"].fillna(0) > 500)
    mo_mask = has_inbound & big_exposure & (multi_year_cover | dead_with_inbound)
    mo = mo[mo_mask].copy()
    if not mo.empty:
        mo["exposure_sy"] = exposure[mo_mask]
        mo["days_of_inventory_projected"] = mo["days_of_inventory_projected"].replace(
            [float("inf"), float("-inf")], None
        )
        # v4.7: severity-floor — emit ALL rows that meet the threshold, capped
        # only at a safety ceiling of 150 to keep the prompt under 200 KB.
        # Previously head(40); HAL/ROSALIE-class mid-tier positions were being
        # starved out by larger CCs.
        mo = mo.sort_values("exposure_sy", ascending=False).head(150)[
            ["sku", "sku_description", "inventory_sy", "on_order_sy", "exposure_sy",
             "avg_daily_sales_sy", "days_of_inventory", "days_of_inventory_projected",
             "inventory_age_days", "supplier_number", "price_class_desc", "cost_center"]
        ]
        data.massive_overstock = mo

    # ------------------------------ NEW v4.2 actionable metrics ------------------------------
    _build_actionable_metrics(data, sm, bundle, target_date)

    # ------------------------------ Top concerns + per-CC grouping ------------------------------
    data.top_concerns = _build_top_concerns(data, sm)
    data.cost_center_problems = _build_cost_center_breakdown(data, sm)

    # v4.8 \u2014 mark concerns that are NEW since the previous brief so the buyer
    # can recognise fresh items at a glance. Then persist today's snapshot.
    _annotate_new_items(data, target_date)
    _persist_snapshot(data, target_date)

    return data


def _annotate_new_items(data: BriefData, target_date: date) -> None:
    """Add an `is_new` boolean column to top_concerns and every CC table.

    Compares each row's SKU to the most recent prior brief snapshot.  Rows
    whose SKU was NOT in the prior snapshot are flagged ``is_new=True`` so the
    AI can render a `[NEW]` badge.  First-ever brief: every row counts as new.
    """
    prev = get_prev_brief_snapshot(target_date)
    # Special case: if there is literally no prior snapshot, suppress the NEW
    # badges entirely (otherwise EVERY item is "new" \u2014 useless signal).
    suppress = not prev

    def _mark(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty or "sku" not in df.columns:
            return df
        if suppress:
            df = df.copy()
            df["is_new"] = False
            return df
        df = df.copy()
        df["is_new"] = ~df["sku"].astype(str).isin(prev)
        return df

    data.top_concerns = _mark(data.top_concerns)
    for cc_block in (data.cost_center_problems or {}).values():
        tables = cc_block.get("tables") or {}
        for tname, tdf in list(tables.items()):
            tables[tname] = _mark(tdf)


def _persist_snapshot(data: BriefData, target_date: date) -> None:
    """Save today's brief SKU set so tomorrow's brief can flag what's new."""
    skus: set[str] = set()
    if data.top_concerns is not None and not data.top_concerns.empty:
        skus.update(data.top_concerns["sku"].astype(str).tolist())
    for cc_block in (data.cost_center_problems or {}).values():
        for tdf in (cc_block.get("tables") or {}).values():
            if tdf is not None and not tdf.empty and "sku" in tdf.columns:
                skus.update(tdf["sku"].astype(str).tolist())
    try:
        save_brief_snapshot(target_date, skus)
    except Exception:
        # Snapshot persistence is best-effort \u2014 never let it break the brief.
        pass


# ---------------------------------------------------------------------------
# Deeper actionable metrics (v4.2)
# ---------------------------------------------------------------------------

def _build_actionable_metrics(
    data: BriefData, sm: pd.DataFrame, bundle: DatasetBundle, target_date: date
) -> None:
    """Compute the deeper, SKU-specific concerns the brief should call out.

    v5.0 \u2014 trimmed to the only two surviving emitters: red-flag new POs and
    needs-reorder. Earlier candidates (decelerating velocity, lead-time gaps,
    dead stock, receipts-onto-overstock, oversized-PO-vs-demand) were folded
    into the two action buckets and removed in v4.8/4.9.
    """
    if sm.empty:
        return

    # 1. Red-flag NEW POs entered yesterday \u2014 PO arrival pushes DOI past the
    #    lead-time-aware overstock floor (700d slow-LT / 365d fast-LT).
    if not data.yesterday_new_pos.empty and "days_of_inventory_projected" in data.yesterday_new_pos.columns:
        yp = data.yesterday_new_pos.copy()
        yp_floor = _overstock_doi_threshold(yp)
        yp_flag = yp[
            yp["days_of_inventory_projected"].fillna(0) > yp_floor
        ].copy()
        if not yp_flag.empty:
            yp_flag = yp_flag.sort_values(
                "days_of_inventory_projected", ascending=False, na_position="last"
            ).head(15)
            data.redflag_new_pos = yp_flag

    # 2. NEEDS REORDER (no valid PO on the books) \u2014 SKUs the buyer should be
    #    PLACING a PO on. Criteria:
    #      - has demand (avg_daily_sales_sy > 0)
    #      - zero on order AND zero pending PO
    #      - current cover < 1.5x lead-time demand
    nr = sm.copy()
    has_no_po = (nr["on_order_sy"] <= 0) & (nr.get("po_pending_qty", 0) <= 0)
    has_demand = nr["avg_daily_sales_sy"] > 0
    cover_threshold = nr["avg_daily_sales_sy"] * nr["lead_time_days"] * 1.5
    short_cover = nr["inventory_sy"] < cover_threshold
    needs = nr[has_no_po & has_demand & short_cover].copy()
    if not needs.empty:
        # Suggested order quantity: 60-day cover at current velocity, minus what's
        # already on the floor.  Rounded to whole SY.
        needs["suggested_order_sy"] = (
            (needs["avg_daily_sales_sy"] * 60) - needs["inventory_sy"]
        ).clip(lower=0).round(0)
        # Keep only meaningful suggestions (>= 25 SY) so we don't pollute the brief
        needs = needs[needs["suggested_order_sy"] >= 25]
        if not needs.empty:
            needs["days_until_stockout"] = needs["days_until_stockout"].replace(
                [float("inf"), float("-inf")], None
            )
            # Rank by largest suggested PO first \u2014 biggest cash/coverage impact
            needs = needs.sort_values("suggested_order_sy", ascending=False).head(20)
            data.needs_reorder_no_po = needs[
                ["sku", "sku_description", "inventory_sy", "avg_daily_sales_sy",
                 "days_until_stockout", "lead_time_days", "suggested_order_sy",
                 "supplier_number", "price_class_desc", "cost_center"]
            ]


# ---------------------------------------------------------------------------
# Top concerns ranking + per-cost-center breakdown (v4.2)
# ---------------------------------------------------------------------------

# Severity of each concern type — higher = more urgent.
# v5.0 — four concern types survive: two `incoming` flavours, one `needs_reorder`,
# and `clearance` (aging on-hand stock, surfaced as a per-CC shortlist).
# Threshold for `incoming` is now lead-time-aware: 700d when lead_time_days > 99,
# else 365d (so a fast-turn SKU pushed past one full re-buy cycle still flags).
_CONCERN_SEVERITY = {
    "redflag_new_po":      96,  # PO entered yesterday, breaches overstock floor
    "incoming_overstock":  92,  # valid open PO pushes DOI past overstock floor
    "needs_reorder":       88,  # has demand, no valid PO on the books
    "clearance":           40,  # aged on-hand stock, no inbound action available
}


def _concern_rows(data: BriefData) -> pd.DataFrame:
    """Flatten every problem table into one (concern, sku, ...) frame.

    A SKU may appear in multiple concern types — that's intentional, the AI
    should see the full picture per SKU. The top_concerns table dedupes by
    keeping the highest-severity row per SKU.
    """
    rows: list[dict] = []

    def _emit(df: pd.DataFrame, kind: str, action_hint: str, impact_col: str = "inventory_sy"):
        if df is None or df.empty:
            return
        for _, r in df.iterrows():
            sku = str(r.get("sku", "")).strip()
            if not sku:
                continue
            rows.append({
                "concern":     kind,
                "severity":    _CONCERN_SEVERITY.get(kind, 0),
                "sku":         sku,
                "description": r.get("sku_description", ""),
                "cost_center": r.get("cost_center", ""),
                "supplier":    r.get("supplier_number", ""),
                "inventory_sy":     float(r.get("inventory_sy", 0) or 0),
                "on_order_sy":      float(r.get("on_order_sy", 0) or 0),
                "avg_daily_sales":  float(r.get("avg_daily_sales_sy", 0) or 0),
                "doi_projected":    r.get("days_of_inventory_projected"),
                "days_until_stockout": r.get("days_until_stockout"),
                "inventory_age":    r.get("inventory_age_days"),
                "days_since_sale":  r.get("days_since_last_sale"),
                "action_hint":      action_hint,
                "impact":           float(r.get(impact_col, 0) or 0),
            })

    _emit(data.redflag_new_pos, "redflag_new_po",
          "Overstock risk — PO entered yesterday pushes DOI past its overstock floor.",
          impact_col="qty_native")
    _emit(data.needs_reorder_no_po, "needs_reorder",
          "Place a new PO — active demand, no open PO, cover < 1.5x lead time.",
          impact_col="suggested_order_sy")
    _emit(data.excessive_incoming_pos, "incoming_overstock",
          "Overstock risk — open PO arrival pushes DOI past its overstock floor.",
          impact_col="on_order_sy")
    # massive_overstock is wired in as a richer-attribute alias of
    # incoming_overstock when the same SKU also clears the exposure threshold.
    # We emit it under the `incoming_overstock` concern so the buyer sees one
    # consolidated row per SKU, but use exposure_sy as the ranking impact.
    _emit(data.massive_overstock, "incoming_overstock",
          "Overstock risk — huge cash position with multi-year projected cover.",
          impact_col="exposure_sy")
    _emit(data.aging_inventory, "clearance",
          "Markdown / liquidation candidate — aged stock, no inbound action available.")

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)

    # Dynamic severity boost for incoming_overstock by exposure (v4.6→v4.8) ---
    # A 50,000-SY cash position is more urgent than a small late-PO miss, so
    # boost incoming_overstock above redflag_new_po when exposure is genuinely
    # large.  +0 at 0 SY, +5 at 50k, +10 at 100k+.
    df["severity"] = df["severity"].astype("float64")
    inc_mask = df["concern"] == "incoming_overstock"
    if inc_mask.any():
        boost = (df.loc[inc_mask, "impact"].clip(lower=0) / 10000.0).clip(upper=10.0)
        df.loc[inc_mask, "severity"] = df.loc[inc_mask, "severity"] + boost
    return df


def _build_top_concerns(
    data: BriefData,
    sm: pd.DataFrame,
    severity_floor: float = 80.0,
    safety_ceiling: int = 150,
) -> pd.DataFrame:
    """Pick every SKU/concern combination at or above the severity floor.

    v5.0 — only the actionable concern types survive in TOP CONCERNS
    (`incoming_overstock`, `redflag_new_po`, `needs_reorder`); `clearance`
    (severity 40) is excluded by the floor and only ever appears as a per-CC
    shortlist in `_build_cost_center_breakdown`.
    """
    df = _concern_rows(data)
    if df.empty:
        return df
    df = df.sort_values(
        ["severity", "impact", "inventory_sy"], ascending=[False, False, False]
    )
    # One row per SKU — keep its highest-severity concern.
    df = df.drop_duplicates(subset=["sku"], keep="first")
    df = df[df["severity"] >= severity_floor]
    return df.head(safety_ceiling).reset_index(drop=True)


def _build_cost_center_breakdown(data: BriefData, sm: pd.DataFrame) -> dict:
    """Group actionable problem tables by cost_center.

    v5.0 — two action buckets per CC plus a clearance shortlist:
      - `incoming_overstock`  (valid open PO that pushes DOI past the
        lead-time-aware overstock floor, plus `redflag_new_pos` and the
        rich-attribute `massive_overstock` overlay)
      - `needs_reorder`       (must place a new PO; from `needs_reorder_no_po`)
      - `aging_clearance`     (top-2 aged on-hand items, attached to every CC
        that has at least 2 aged items — surfaced alongside the actionable
        buckets, NOT only as a fallback)
    Empty CCs (no incoming, no reorder, fewer than 2 aging items) are omitted.
    """
    out: dict = {}
    if sm.empty:
        return out

    cc_col = "cost_center"
    cc_name_lookup: dict[str, str] = {}
    if "cost_center" in sm.columns and "cost_center_desc" in sm.columns:
        for cc, name in sm.groupby("cost_center")["cost_center_desc"].first().items():
            cc_name_lookup[str(cc)] = str(name or "")

    # Bucket 1 — incoming overstock (valid PO causing DOI > 700d).
    incoming_specs = [
        ("incoming_overstock_pos",  data.excessive_incoming_pos),
        ("massive_overstock",       data.massive_overstock),
        ("redflag_new_pos",         data.redflag_new_pos),
    ]
    # Bucket 2 — reorder (active demand that needs a NEW PO placed).
    reorder_specs = [
        ("needs_reorder",                data.needs_reorder_no_po),
    ]
    # Clearance bucket — v5.0 attaches top-2 per CC alongside actionable rows.
    fallback_specs = [
        ("aging_clearance",         data.aging_inventory),
    ]

    def _attach(name: str, df: pd.DataFrame, bucket: str) -> None:
        if df is None or df.empty or cc_col not in df.columns:
            return
        for cc, sub in df.groupby(cc_col):
            cc_str = str(cc or "").strip() or "(unassigned)"
            slot = out.setdefault(cc_str, {
                "name":   cc_name_lookup.get(cc_str, ""),
                "tables": {},
                "buckets": set(),
                "kpis":   {},
            })
            slot["tables"][name] = sub.head(40).reset_index(drop=True)
            slot["buckets"].add(bucket)

    for n, df in incoming_specs:
        _attach(n, df, "incoming")
    for n, df in reorder_specs:
        _attach(n, df, "reorder")

    # Per-CC massive overstock overlay — v4.8 still requires a valid open PO.
    if cc_col in sm.columns:
        sm_local = sm.copy()
        exposure = sm_local["inventory_sy"].fillna(0) + sm_local["on_order_sy"].fillna(0)
        doi_proj = sm_local["days_of_inventory_projected"].replace(
            [float("inf"), float("-inf")], 99999
        ).fillna(99999)
        avg_daily = sm_local["avg_daily_sales_sy"].fillna(0)
        on_order = sm_local["on_order_sy"].fillna(0)
        # Same shape as the global threshold but slightly smaller exposure floor
        # so smaller CCs still surface their top items. Still requires inbound.
        # v5.0 — lead-time-aware overstock floor (700d slow-LT / 365d fast-LT).
        local_floor = _overstock_doi_threshold(sm_local)
        mask_local = (
            (on_order > 0)
            & (exposure >= 3000)
            & ((doi_proj >= local_floor) | ((avg_daily <= 0) & (on_order > 500)))
        )
        local = sm_local[mask_local].copy()
        if not local.empty:
            local["exposure_sy"] = exposure[mask_local]
            local["days_of_inventory_projected"] = local["days_of_inventory_projected"].replace(
                [float("inf"), float("-inf")], None
            )
            cols = ["sku", "sku_description", "inventory_sy", "on_order_sy", "exposure_sy",
                    "avg_daily_sales_sy", "days_of_inventory", "days_of_inventory_projected",
                    "inventory_age_days", "supplier_number", "price_class_desc", "cost_center"]
            cols = [c for c in cols if c in local.columns]
            for cc, sub in local[cols].groupby(cc_col):
                cc_str = str(cc or "").strip() or "(unassigned)"
                slot = out.setdefault(cc_str, {
                    "name":   cc_name_lookup.get(cc_str, ""),
                    "tables": {},
                    "buckets": set(),
                    "kpis":   {},
                })
                top_local = sub.sort_values("exposure_sy", ascending=False).head(40).reset_index(drop=True)
                existing = slot["tables"].get("massive_overstock")
                if existing is None or len(top_local) > len(existing):
                    slot["tables"]["massive_overstock"] = top_local
                    slot["buckets"].add("incoming")

    # Clearance attachment — v5.0: ALWAYS attach the top-2 aged-stock items per
    # CC when the CC has at least 2 to show, regardless of whether the CC also
    # has actionable buckets. Gives the buyer a steady markdown short-list
    # alongside the actionable rows. CCs with fewer than 2 aged items skip
    # the clearance table to avoid one-off noise.
    _CLEARANCE_PER_CC = 2
    for n, df in fallback_specs:
        if df is None or df.empty or cc_col not in df.columns:
            continue
        for cc, sub in df.groupby(cc_col):
            if len(sub) < _CLEARANCE_PER_CC:
                continue
            cc_str = str(cc or "").strip() or "(unassigned)"
            slot = out.setdefault(cc_str, {
                "name":   cc_name_lookup.get(cc_str, ""),
                "tables": {},
                "buckets": set(),
                "kpis":   {},
            })
            slot["tables"][n] = sub.head(_CLEARANCE_PER_CC).reset_index(drop=True)
            slot["buckets"].add("clearance")

    # Per-CC quick KPIs (computed on full eligible sm, not just problem tables)
    if cc_col in sm.columns:
        for cc, sub in sm.groupby(cc_col):
            cc_str = str(cc or "").strip() or "(unassigned)"
            if cc_str not in out:
                continue
            out[cc_str]["kpis"] = {
                "skus":           int(len(sub)),
                "inventory_sy":   float(sub["inventory_sy"].sum()),
                "on_order_sy":    float(sub["on_order_sy"].sum()),
                "stockouts":      int(sub["stockout_flag"].sum()),
                "overstock":      int(sub["overstock_flag"].sum()),
                "aging":          int(((sub["inventory_age_days"] >= 365) & (sub["inventory_sy"] > 0)).sum()),
            }

    # `buckets` is internal bookkeeping — strip before returning.
    for slot in out.values():
        slot.pop("buckets", None)
    return out


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _df_to_compact_table(df: pd.DataFrame, max_rows: int = 100) -> str:
    """Render a DataFrame as a compact pipe-delimited table (cheap for tokens)."""
    if df is None or df.empty:
        return "(none)"
    df = df.head(max_rows).copy()
    # Round numerics to 1dp
    for c in df.select_dtypes(include=["float", "float64"]).columns:
        df[c] = df[c].round(1)
    # Ensure dates render cleanly
    for c in df.columns:
        if df[c].dtype == "object":
            df[c] = df[c].astype(str)
    cols = list(df.columns)
    lines = [" | ".join(cols)]
    lines.append(" | ".join(["---"] * len(cols)))
    for _, r in df.iterrows():
        lines.append(" | ".join(str(r[c]) if pd.notna(r[c]) else "" for c in cols))
    return "\n".join(lines)


def build_brief_prompt(data: BriefData) -> str:
    """Format collected data into the user-message sent to the AI.

    Structure (must mirror schema.py output requirements):
      1. Filter scope disclosure
      2. Portfolio snapshot
      3. TOP CONCERNS (cross-portfolio, ranked) — the lead of the brief
      4. Yesterday's activity (POs / receipts / sales / backorders)
      5. Per-cost-center problem tables (only CCs with concerns)
      6. Full problem tables (cross-portfolio fallback context)
    """
    k = data.portfolio_kpis
    fs = data.filter_summary
    sections = []

    sections.append(
        f"# DAILY BRIEF DATA — {data.target_date.isoformat()}\n"
        f"\n## ELIGIBILITY FILTER (applied upstream — do not re-filter)\n"
        f"- Trim items excluded (ITEM.ICLAST length > 1): "
        f"{fs.get('trim_dropped', 0):,} SKUs dropped\n"
        f"- New items excluded (launch_date within last {fs.get('min_age_days', 180)} days): "
        f"{fs.get('young_dropped', 0):,} SKUs dropped\n"
        f"- Eligible SKU universe for this brief: {fs.get('after_age', 0):,} of "
        f"{fs.get('input', 0):,}\n"
    )

    sections.append(
        f"\n## PORTFOLIO SNAPSHOT (eligible universe only)\n"
        f"- Active SKUs: {k.get('total_skus', 0):,}\n"
        f"- Inventory on hand: {k.get('total_inventory_sy', 0):,.0f} SY\n"
        f"- On order (open POs): {k.get('total_on_order_sy', 0):,.0f} SY\n"
        f"- Stock turn (annualised): {k.get('stock_turn', 0):.2f}x\n"
        f"- Fill rate: {k.get('fill_rate', 0)*100:.1f}%\n"
        f"- Active stockouts: {k.get('stockout_skus', 0)} | Runout risk: {k.get('runout_risk_skus', 0)} "
        f"| Overstock: {k.get('overstock_skus', 0)} | DOI(proj) > 365: {k.get('twelve_month_doi_skus', 0)} "
        f"| Aged >= 365d: {k.get('aging_365d_skus', 0)}\n"
    )

    # --- TOP CONCERNS (the lead) -------------------------------------------------
    sections.append(
        "\n## TOP CONCERNS (v5.0 — only buyer-actionable items)\n"
        "Every row is one of TWO action types:\n"
        "  - `incoming_overstock` / `redflag_new_po`: an OPEN, valid PO is\n"
        "    pushing days-of-inventory past its overstock floor (700d for\n"
        "    slow-LT items > 99-day lead time, 365d for fast-LT items).\n"
        "    Flag as OVERSTOCK RISK.\n"
        "  - `needs_reorder`: active demand with no valid PO on the books —\n"
        "    the buyer must place a NEW PO.\n"
        "Pure on-hand overstock without inbound is intentionally NOT in this\n"
        "list (no buyer action available beyond markdown). Late-PO/expedite\n"
        "items are also excluded — lead time is fixed; if a bridge buy is\n"
        "needed it surfaces via needs_reorder. Placeholder POs with 1-2 char\n"
        "order numbers were stripped upstream.\n"
        "\n`is_new` column = True if this SKU did not appear in the previous\n"
        "day's brief. Render `[NEW]` immediately after the action tag for those\n"
        "rows so the buyer can spot fresh items vs the usual offenders.\n"
        "DO NOT skip rows.\n\n"
        + _df_to_compact_table(data.top_concerns, max_rows=150)
    )

    # --- Yesterday's activity ----------------------------------------------------
    sections.append("\n## YESTERDAY'S NEW PURCHASE ORDERS\n"
                    "Each row is a PO entered yesterday with the SKU's current state.\n"
                    "Pay attention to rows where days_of_inventory_projected is high — those POs\n"
                    "may need to be deferred or cancelled.\n\n"
                    + _df_to_compact_table(data.yesterday_new_pos))

    sections.append("\n## YESTERDAY'S RECEIPTS (POs that arrived)\n"
                    + _df_to_compact_table(data.yesterday_receipts))

    sections.append("\n## YESTERDAY'S TOP SALES\n"
                    + _df_to_compact_table(data.yesterday_sales))

    sections.append("\n## YESTERDAY'S BACKORDERS\n"
                    "Backordered lines are direct misses on priority #2 (don't be out of stock).\n\n"
                    + _df_to_compact_table(data.yesterday_backorders))

    # --- Per-cost-center sections -----------------------------------------------
    if data.cost_center_problems:
        sections.append(
            "\n## PER-COST-CENTER BREAKDOWN\n"
            "Only cost centers with at least one buyer-actionable concern (or a\n"
            "clearance shortlist) are listed. Each section's tables are\n"
            "pre-filtered to that cost center. Render one `## CC <code>`\n"
            "section per block. Mark rows where the data column `is_new` is\n"
            "True with a `[NEW]` badge after the action tag. v5.0: every CC\n"
            "with at least 2 aged-inventory items also receives a 2-row\n"
            "`aging_clearance` table. Render those as `[CLEARANCE]` bullets at\n"
            "the BOTTOM of the CC's section, after the actionable rows.\n"
            "NEVER add `[NEW]` badges to clearance rows.\n"
        )
        for cc_code in sorted(data.cost_center_problems.keys()):
            block = data.cost_center_problems[cc_code]
            cc_name = block.get("name") or ""
            kpis = block.get("kpis") or {}
            tables = block.get("tables") or {}
            sections.append(
                f"\n### COST CENTER {cc_code}"
                + (f" — {cc_name}" if cc_name else "")
                + "\n"
                f"KPIs: SKUs={kpis.get('skus', 0):,} | Inv={kpis.get('inventory_sy', 0):,.0f} SY "
                f"| OnOrder={kpis.get('on_order_sy', 0):,.0f} SY | Stockouts={kpis.get('stockouts', 0)} "
                f"| Overstock={kpis.get('overstock', 0)} | Aged={kpis.get('aging', 0)}\n"
            )
            for tname, tdf in tables.items():
                sections.append(f"\n#### {tname}\n" + _df_to_compact_table(tdf, max_rows=40))

    # --- Cross-portfolio supporting tables (raw) --------------------------------
    sections.append(
        "\n## CROSS-PORTFOLIO SUPPORTING TABLES\n"
        "Every table below is filtered to v5.0's two action buckets only.\n"
        "Use them only if you need to verify or expand on the per-CC sections.\n"
    )
    sections.append("\n### RED-FLAG NEW POs (entered yesterday, push DOI past lead-time-aware floor)\n"
                    + _df_to_compact_table(data.redflag_new_pos))
    sections.append("\n### NEEDS REORDER — no valid PO on the books, cover < 1.5x lead time\n"
                    "`suggested_order_sy` = 60-day cover target net of current inventory.\n\n"
                    + _df_to_compact_table(data.needs_reorder_no_po))
    sections.append("\n### INCOMING-OVERSTOCK POs (valid PO pushes DOI past lead-time-aware floor)\n"
                    + _df_to_compact_table(data.excessive_incoming_pos))
    sections.append("\n### MASSIVE OVERSTOCK WITH VALID INBOUND PO\n"
                    "Highest-dollar incoming-overstock positions. `exposure_sy` =\n"
                    "inventory_sy + on_order_sy. Every row needs an `[OVERSTOCK RISK]`\n"
                    "bullet calling for the inbound PO to be defused.\n\n"
                    + _df_to_compact_table(data.massive_overstock))
    sections.append("\n### AGING INVENTORY (clearance shortlist — no inbound PO action)\n"
                    "v5.0: per-CC sections show the top 2 of these as `[CLEARANCE]`\n"
                    "bullets. Use this cross-portfolio view only if you need wider\n"
                    "context on what is aging across the business.\n\n"
                    + _df_to_compact_table(data.aging_inventory))

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

def _estimate_tokens(text_str: str) -> int:
    """Rough token estimate: ~4 chars per token for English/SQL-ish content."""
    return max(1, len(text_str) // 4)


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    p = _PRICING.get(model.lower())
    if not p:
        return 0.0
    in_rate, out_rate = p
    return round((tokens_in / 1_000_000) * in_rate + (tokens_out / 1_000_000) * out_rate, 4)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_brief(
    target_date: date,
    bundle: DatasetBundle,
    provider: str,
    api_key: str,
    model: str,
    options: dict | None = None,
) -> BriefResult:
    """Run the full pipeline and return a BriefResult.

    `options` may contain user-tuned generation parameters
    (max_tokens, reasoning_effort, timeout_sec). None falls back to the
    recommended defaults in providers.recommended_settings().

    Synchronous — call from a worker thread.
    """
    started = time.time()
    data = gather_brief_data(target_date, bundle)
    user_msg = build_brief_prompt(data)
    system_msg = build_brief_system_prompt(target_date=target_date)

    if not model:
        model = DEFAULT_MODELS.get((provider or "openai").lower(), "gpt-4o")

    try:
        narrative = call_provider(
            provider, api_key, model, system_msg,
            [{"role": "user", "content": user_msg}],
            options=options,
        )
    except AIError as e:
        return BriefResult(
            target_date=target_date,
            markdown="",
            model=model,
            provider=provider,
            elapsed_sec=round(time.time() - started, 2),
            error=str(e),
        )

    tin = _estimate_tokens(system_msg + user_msg)
    tout = _estimate_tokens(narrative)
    cost = estimate_cost(model, tin, tout)

    return BriefResult(
        target_date=target_date,
        markdown=narrative,
        model=model,
        provider=provider,
        tokens_in=tin,
        tokens_out=tout,
        cost_usd=cost,
        elapsed_sec=round(time.time() - started, 2),
    )
