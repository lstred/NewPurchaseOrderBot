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

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
from sqlalchemy import text

from app.ai.providers import call_provider, AIError, DEFAULT_MODELS
from app.ai.schema import build_brief_system_prompt
from app.data.db import read_dataframe
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
    # Yesterday's activity
    yesterday_new_pos: pd.DataFrame = field(default_factory=pd.DataFrame)
    yesterday_receipts: pd.DataFrame = field(default_factory=pd.DataFrame)
    yesterday_sales: pd.DataFrame = field(default_factory=pd.DataFrame)
    yesterday_backorders: pd.DataFrame = field(default_factory=pd.DataFrame)
    # Stockout / supply-side problems
    active_stockouts: pd.DataFrame = field(default_factory=pd.DataFrame)
    runout_risk: pd.DataFrame = field(default_factory=pd.DataFrame)
    # Overstock / aging-side problems
    overstock_with_open_po: pd.DataFrame = field(default_factory=pd.DataFrame)
    aging_inventory: pd.DataFrame = field(default_factory=pd.DataFrame)
    # Notable POs (any open PO whose arrival pushes a SKU into > 12mo of cover)
    excessive_incoming_pos: pd.DataFrame = field(default_factory=pd.DataFrame)


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

def gather_brief_data(target_date: date, bundle: DatasetBundle) -> BriefData:
    """Collect every input table the AI needs to write the brief.

    Pulls live yesterday-activity from SQL + slices the in-memory DatasetBundle
    for the worst-offender lists. All numbers are deterministic Python; the AI
    never has to compute anything.
    """
    data = BriefData(target_date=target_date)
    sm = bundle.sku_metrics
    if sm.empty:
        return data

    ymd = int(target_date.strftime("%Y%m%d"))

    # ------------------------------ Yesterday's NEW POs ------------------------------
    try:
        df = read_dataframe(_YDAY_NEW_POS_SQL, {"ymd": ymd})
        if not df.empty:
            df["sku"] = df["sku"].astype(str).str.strip()
            # Enrich each PO row with the SKU's current avg-daily / inventory / DOI(proj)
            enrich_cols = [
                "sku", "sku_description", "avg_daily_sales_sy",
                "inventory_sy", "on_order_sy", "days_of_inventory_projected",
                "lead_time_days",
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
        data.yesterday_receipts = df
    except Exception:
        pass

    # ------------------------------ Yesterday's sales ------------------------------
    if not bundle.orders.empty and "order_entry_date" in bundle.orders.columns:
        ord_df = bundle.orders
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

    # ------------------------------ Active stockouts ------------------------------
    # SKUs with zero inventory but real demand — the second business priority.
    so = sm[sm["stockout_flag"] & (sm["avg_daily_sales_sy"] > 0)].copy()
    if not so.empty:
        so = so.sort_values("avg_daily_sales_sy", ascending=False).head(30)[
            ["sku", "sku_description", "avg_daily_sales_sy", "on_order_sy",
             "lead_time_days", "days_since_last_sale", "supplier_number",
             "price_class_desc", "cost_center"]
        ]
        data.active_stockouts = so

    # ------------------------------ Runout risk ------------------------------
    rr = sm[sm["runout_risk"]].copy()
    if not rr.empty:
        rr["days_until_stockout"] = rr["days_until_stockout"].replace(
            [float("inf"), float("-inf")], None
        )
        rr = rr.sort_values("days_until_stockout", ascending=True).head(30)[
            ["sku", "sku_description", "inventory_sy", "on_order_sy",
             "avg_daily_sales_sy", "days_until_stockout", "lead_time_days",
             "supplier_number", "price_class_desc", "cost_center"]
        ]
        data.runout_risk = rr

    # ------------------------------ Overstock with open PO ------------------------------
    # Items where today's stock is already overstock AND there is an open PO that
    # will make it worse — directly actionable (cancel/defer the PO).
    ov = sm[
        sm["overstock_flag"] & (sm["on_order_sy"] > 0) & (sm["avg_daily_sales_sy"] > 0)
    ].copy()
    if not ov.empty:
        ov["days_of_inventory_projected"] = ov["days_of_inventory_projected"].replace(
            [float("inf"), float("-inf")], None
        )
        ov = ov.sort_values("days_of_inventory_projected", ascending=False).head(30)[
            ["sku", "sku_description", "inventory_sy", "on_order_sy",
             "avg_daily_sales_sy", "days_of_inventory", "days_of_inventory_projected",
             "stockturn_target", "supplier_number", "price_class_desc", "cost_center"]
        ]
        data.overstock_with_open_po = ov

    # ------------------------------ Aging inventory (>12 months sitting) ------------------------------
    # The first business priority: avoid inventory that sits > 12 months.
    aging = sm[
        (sm["inventory_age_days"] >= 365) & (sm["inventory_sy"] > 0)
    ].copy()
    if not aging.empty:
        aging = aging.sort_values("inventory_age_days", ascending=False).head(30)[
            ["sku", "sku_description", "inventory_sy", "inventory_age_days",
             "avg_daily_sales_sy", "days_since_last_sale", "on_order_sy",
             "supplier_number", "price_class_desc", "cost_center"]
        ]
        data.aging_inventory = aging

    # ------------------------------ Excessive incoming POs ------------------------------
    # Any SKU with an open PO whose arrival will push DOI(proj) > 365 days.
    # These are the POs to flag for cancellation/deferral.
    inc = sm[
        (sm["on_order_sy"] > 0) & (sm["days_of_inventory_projected"] > 365)
        & (sm["avg_daily_sales_sy"] > 0)
    ].copy()
    if not inc.empty:
        inc["days_of_inventory_projected"] = inc["days_of_inventory_projected"].replace(
            [float("inf"), float("-inf")], 9999
        )
        inc = inc.sort_values("days_of_inventory_projected", ascending=False).head(30)[
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

    return data


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _df_to_compact_table(df: pd.DataFrame, max_rows: int = 30) -> str:
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
    """Format collected data into the user-message sent to the AI."""
    k = data.portfolio_kpis
    sections = []

    sections.append(
        f"# DAILY BRIEF DATA — {data.target_date.isoformat()}\n"
        f"\n## PORTFOLIO SNAPSHOT (current)\n"
        f"- Total active SKUs: {k.get('total_skus', 0):,}\n"
        f"- Inventory on hand: {k.get('total_inventory_sy', 0):,.0f} SY\n"
        f"- On order (open POs): {k.get('total_on_order_sy', 0):,.0f} SY\n"
        f"- Stock turn (annualised): {k.get('stock_turn', 0):.2f}x\n"
        f"- Fill rate: {k.get('fill_rate', 0)*100:.1f}%\n"
        f"- Active stockouts (zero stock + real demand): {k.get('stockout_skus', 0)}\n"
        f"- Runout risk (supply < 1.5x lead-time demand): {k.get('runout_risk_skus', 0)}\n"
        f"- Overstock SKUs: {k.get('overstock_skus', 0)}\n"
        f"- SKUs projected at >12 months DOI after open POs land: {k.get('twelve_month_doi_skus', 0)}\n"
        f"- SKUs with rolls aged >= 365 days: {k.get('aging_365d_skus', 0)}\n"
    )

    sections.append("\n## YESTERDAY'S NEW PURCHASE ORDERS\n"
                    "Each row is a PO entered yesterday with the SKU's current state.\n"
                    "Pay attention to rows where days_of_inventory_projected is high — those are POs\n"
                    "that may need to be deferred or cancelled (priority #1: avoid 12-month inventory).\n\n"
                    + _df_to_compact_table(data.yesterday_new_pos))

    sections.append("\n## YESTERDAY'S RECEIPTS (POs that arrived)\n"
                    + _df_to_compact_table(data.yesterday_receipts))

    sections.append("\n## YESTERDAY'S TOP SALES\n"
                    + _df_to_compact_table(data.yesterday_sales))

    sections.append("\n## YESTERDAY'S BACKORDERS\n"
                    "Backordered lines are direct misses on priority #2 (don't be out of stock).\n\n"
                    + _df_to_compact_table(data.yesterday_backorders))

    sections.append("\n## ACTIVE STOCKOUTS (zero inventory, real demand)\n"
                    "These are losing sales right now. Sorted by avg_daily_sales_sy desc.\n\n"
                    + _df_to_compact_table(data.active_stockouts))

    sections.append("\n## RUNOUT RISK (will stockout before reorder arrives)\n"
                    "Sorted by days_until_stockout asc. These need expediting or new POs.\n\n"
                    + _df_to_compact_table(data.runout_risk))

    sections.append("\n## OVERSTOCK WITH OPEN PO (PO will make it worse)\n"
                    "Already overstocked AND another PO is incoming. Top candidates for PO cancellation/defer.\n\n"
                    + _df_to_compact_table(data.overstock_with_open_po))

    sections.append("\n## AGING INVENTORY (rolls >= 365 days old)\n"
                    "Inventory that has been sitting for over a year. Priority #1 violation.\n\n"
                    + _df_to_compact_table(data.aging_inventory))

    sections.append("\n## INCOMING POs THAT PUSH DOI(proj) BEYOND 12 MONTHS\n"
                    "These open POs, when received, will leave the SKU with > 365 days of cover.\n"
                    "Strong candidates for buyer review.\n\n"
                    + _df_to_compact_table(data.excessive_incoming_pos))

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
) -> BriefResult:
    """Run the full pipeline and return a BriefResult.

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
