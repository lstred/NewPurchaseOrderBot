"""
Starter Query Library
=====================

Hand-crafted, pre-tested SQL templates for the most common questions an
inventory analyst asks. These power the suggestion chips and the
"📚 Library" dialog in the AI tab.

Why this exists
---------------
LLMs reliably handle ad-hoc questions but struggle with complex
multi-CTE rollups (base_sku + LEFT JOIN of sales/inv/on_order, UoM
normalization to SY, app-derived metrics like Days of Inv (Proj)). For
the *canonical* questions, we ship vetted SQL the user can run with one
click — guaranteed correctness, zero AI cost, instant results.

Each entry is a dict:
    {
        "key":         unique stable id (used by chip + Library lookup)
        "label":       short user-facing chip/list label (with emoji)
        "name":        full descriptive name (for saved-query seeding)
        "description": one-line explainer (shown in Library + tooltips)
        "category":    grouping for the Library dialog
        "sql":         SQL with {from}/{to}/{cc}/{n} placeholders
        "params":      list of free placeholder names the caller must
                       supply at runtime ({from}/{to} are auto-filled
                       from the app window)
    }

Placeholder conventions:
    {from}  -- top-bar From date (YYYY-MM-DD), auto-filled from app
    {to}    -- top-bar To date   (YYYY-MM-DD), auto-filled from app
    {cc}    -- cost-center code (e.g. '010')
    {n}     -- generic numeric threshold (e.g. days-of-inv, top-N)

All SQL has been validated against the actual NRF_REPORTS schema and
follows the LEFT-JOIN-from-sku-base pattern documented in
`app/ai/schema.py` so SKUs with zero sales are never silently dropped.
"""

from __future__ import annotations

from typing import Optional


# --- Reusable SQL fragments (kept as module constants for DRY) -------------

# Per-base-sku rollup of sales in SY for the {from}..{to} window.
_SALES_CTE = """sales AS (
    SELECT COALESCE(NULLIF(LTRIM(RTRIM(i.IIXREF)),''), i.ItemNumber) AS base_sku,
           SUM(
             CASE LTRIM(RTRIM(UPPER(o.UNIT_OF_MEASURE)))
               WHEN 'SY' THEN o.QUANTITY_ORDERED
               WHEN 'SF' THEN CASE WHEN LTRIM(RTRIM(i.ICCTR)) IN ('010','011','012','013') THEN o.QUANTITY_ORDERED/9.0 ELSE o.QUANTITY_ORDERED END
               WHEN 'LY' THEN CASE WHEN i.IWIDTH > 0 THEN o.QUANTITY_ORDERED * i.IWIDTH / 36.0   ELSE o.QUANTITY_ORDERED END
               WHEN 'LF' THEN CASE WHEN i.IWIDTH > 0 THEN o.QUANTITY_ORDERED * i.IWIDTH / 108.0  ELSE o.QUANTITY_ORDERED END
               WHEN 'IN' THEN CASE WHEN i.IWIDTH > 0 THEN o.QUANTITY_ORDERED * i.IWIDTH / 1296.0 ELSE o.QUANTITY_ORDERED END
               ELSE o.QUANTITY_ORDERED
             END
           ) AS total_sales_sy,
           COUNT(*) AS line_count
    FROM dbo._ORDERS o
    JOIN dbo.ITEM i ON o.ITEM_MFGR_COLOR_PAT = i.ItemNumber
    WHERE o.[ACCOUNT#I] > 1
      AND o.N_NOT_INVENTORY = 'Y'
      AND o.ORDER_ENTRY_DATE_YYYYMMDD BETWEEN
            TRY_CONVERT(int, REPLACE('{from}','-','')) AND TRY_CONVERT(int, REPLACE('{to}','-',''))
    GROUP BY COALESCE(NULLIF(LTRIM(RTRIM(i.IIXREF)),''), i.ItemNumber)
)"""

# Per-base-sku on-hand inventory in SY (active rolls only).
_INV_CTE = """inv AS (
    SELECT COALESCE(NULLIF(LTRIM(RTRIM(i.IIXREF)),''), i.ItemNumber) AS base_sku,
           SUM(
             CASE LTRIM(RTRIM(UPPER(r.RUM)))
               WHEN 'SY' THEN r.Available
               WHEN 'SF' THEN CASE WHEN LTRIM(RTRIM(i.ICCTR)) IN ('010','011','012','013') THEN r.Available/9.0 ELSE r.Available END
               WHEN 'LY' THEN CASE WHEN i.IWIDTH > 0 THEN r.Available * i.IWIDTH / 36.0   ELSE r.Available END
               WHEN 'LF' THEN CASE WHEN i.IWIDTH > 0 THEN r.Available * i.IWIDTH / 108.0  ELSE r.Available END
               WHEN 'IN' THEN CASE WHEN i.IWIDTH > 0 THEN r.Available * i.IWIDTH / 1296.0 ELSE r.Available END
               ELSE r.Available
             END
           ) AS inventory_sy
    FROM dbo.ROLLS r
    JOIN dbo.ITEM i ON r.ItemNumber = i.ItemNumber
    WHERE r.Available > 0
      AND r.RLOC1 <> 'REM'
      AND r.[RCODE@] <> '#'
      AND r.[RCODE@] NOT LIKE '%I%'
    GROUP BY COALESCE(NULLIF(LTRIM(RTRIM(i.IIXREF)),''), i.ItemNumber)
)"""

# Per-base-sku open-order qty (treat OPENPO_D qty as already SY — schema confirms no UoM column).
_OO_CTE = """oo AS (
    SELECT COALESCE(NULLIF(LTRIM(RTRIM(i.IIXREF)),''), i.ItemNumber) AS base_sku,
           SUM(d.[D@QTYO] - d.[D@QTYP]) AS on_order_sy
    FROM dbo.OPENPO_D d
    JOIN dbo.ITEM i
      ON LTRIM(RTRIM(d.[D@MFGR])) + LTRIM(RTRIM(d.[D@COLO])) + LTRIM(RTRIM(d.[D@PATT])) = i.ItemNumber
    WHERE d.[D@ACCT] = 1
      AND d.[D@DEL8] <> '#'
      AND d.[D@SUPP] <> '001'
      AND d.[D@REF#] > 0
      AND d.[D@QTYO] > d.[D@QTYP]
    GROUP BY COALESCE(NULLIF(LTRIM(RTRIM(i.IIXREF)),''), i.ItemNumber)
)"""

# Per-base-sku launch date = MIN(first sale date, first roll receive date), floored at 2025-08-05.
_LAUNCH_CTE = """launch AS (
    SELECT base_sku,
           CASE WHEN MIN(d) < '2025-08-05' THEN CAST('2025-08-05' AS date) ELSE MIN(d) END AS launch_date
    FROM (
        SELECT COALESCE(NULLIF(LTRIM(RTRIM(i.IIXREF)),''), i.ItemNumber) AS base_sku,
               TRY_CONVERT(date, CAST(o.ORDER_ENTRY_DATE_YYYYMMDD AS VARCHAR), 112) AS d
        FROM dbo._ORDERS o
        JOIN dbo.ITEM i ON o.ITEM_MFGR_COLOR_PAT = i.ItemNumber
        WHERE o.[ACCOUNT#I] > 1 AND o.N_NOT_INVENTORY = 'Y'
        UNION ALL
        SELECT COALESCE(NULLIF(LTRIM(RTRIM(i.IIXREF)),''), i.ItemNumber) AS base_sku,
               TRY_CONVERT(date, CAST(r.RLRCTD AS VARCHAR), 112) AS d
        FROM dbo.ROLLS r
        JOIN dbo.ITEM i ON r.ItemNumber = i.ItemNumber
        WHERE r.Available > 0
    ) x
    WHERE d IS NOT NULL
    GROUP BY base_sku
)"""

# Per-base-sku skeleton: every active item, cost-center 1xx excluded by default.
_SKU_BASE_CTE = """sku_base AS (
    SELECT COALESCE(NULLIF(LTRIM(RTRIM(i.IIXREF)),''), i.ItemNumber) AS base_sku,
           MIN(LTRIM(RTRIM(i.ICCTR))) AS cc,
           MIN(LTRIM(RTRIM(i.INAME))) AS name
    FROM dbo.ITEM i
    WHERE i.IINVEN = 'Y'
      AND LTRIM(RTRIM(i.ICCTR)) NOT LIKE '1%'
      AND LEN(LTRIM(RTRIM(CAST(i.IDISCD AS VARCHAR)))) < 2
    GROUP BY COALESCE(NULLIF(LTRIM(RTRIM(i.IIXREF)),''), i.ItemNumber)
)"""


# --- The Library -----------------------------------------------------------

STARTER_QUERIES: list[dict] = [

    # ─── Sales ────────────────────────────────────────────────────────────
    {
        "key":         "top_skus_by_sales",
        "label":       "📊 Top 20 SKUs by sales",
        "name":        "Top 20 SKUs by Sales (app window)",
        "description": "Highest-selling SKUs in the From/To window, in SY. Includes every active SKU; SKUs with no sales drop to the bottom.",
        "category":    "Sales",
        "params":      [],
        "sql": f"""WITH {_SKU_BASE_CTE},
{_SALES_CTE}
SELECT TOP 20
    b.base_sku,
    b.cc,
    b.name,
    COALESCE(s.total_sales_sy, 0) AS total_sales_sy,
    COALESCE(s.line_count, 0)     AS sales_line_count
FROM sku_base b
LEFT JOIN sales s ON s.base_sku = b.base_sku
ORDER BY COALESCE(s.total_sales_sy, 0) DESC""",
    },

    {
        "key":         "top_skus_by_stockturn",
        "label":       "🔄 Top 20 by stock turn",
        "name":        "Top 20 SKUs by Stock Turn (app window)",
        "description": "(avg_daily_sales × 365) ÷ inventory_sy, ranked. Excludes SKUs with no inventory or no sales.",
        "category":    "Sales",
        "params":      [],
        "sql": f"""WITH {_SKU_BASE_CTE},
{_SALES_CTE},
{_INV_CTE}
SELECT TOP 20
    b.base_sku,
    b.cc,
    b.name,
    COALESCE(s.total_sales_sy, 0)                                                                AS total_sales_sy,
    COALESCE(i.inventory_sy, 0)                                                                  AS inventory_sy,
    COALESCE(s.total_sales_sy, 0) / NULLIF(DATEDIFF(day, '{{from}}', '{{to}}') + 1, 0)               AS avg_daily_sales_sy,
    ((COALESCE(s.total_sales_sy, 0) / NULLIF(DATEDIFF(day, '{{from}}', '{{to}}') + 1, 0)) * 365)
        / NULLIF(i.inventory_sy, 0)                                                              AS stock_turn
FROM sku_base b
LEFT JOIN sales s ON s.base_sku = b.base_sku
LEFT JOIN inv   i ON i.base_sku = b.base_sku
WHERE COALESCE(s.total_sales_sy, 0) > 0
  AND COALESCE(i.inventory_sy, 0)  > 0
ORDER BY stock_turn DESC""",
    },

    {
        "key":         "sales_by_manufacturer",
        "label":       "🏭 Sales by manufacturer",
        "name":        "Sales by Manufacturer (app window)",
        "description": "Total SY sold per manufacturer in the From/To window, ranked.",
        "category":    "Sales",
        "params":      [],
        "sql": """SELECT
    LTRIM(RTRIM(i.IMFGR))                                                AS manufacturer,
    COUNT(DISTINCT o.ITEM_MFGR_COLOR_PAT)                                AS sku_count,
    COUNT(*)                                                             AS line_count,
    SUM(
      CASE LTRIM(RTRIM(UPPER(o.UNIT_OF_MEASURE)))
        WHEN 'SY' THEN o.QUANTITY_ORDERED
        WHEN 'SF' THEN CASE WHEN LTRIM(RTRIM(i.ICCTR)) IN ('010','011','012','013') THEN o.QUANTITY_ORDERED/9.0 ELSE o.QUANTITY_ORDERED END
        WHEN 'LY' THEN CASE WHEN i.IWIDTH > 0 THEN o.QUANTITY_ORDERED * i.IWIDTH / 36.0   ELSE o.QUANTITY_ORDERED END
        WHEN 'LF' THEN CASE WHEN i.IWIDTH > 0 THEN o.QUANTITY_ORDERED * i.IWIDTH / 108.0  ELSE o.QUANTITY_ORDERED END
        WHEN 'IN' THEN CASE WHEN i.IWIDTH > 0 THEN o.QUANTITY_ORDERED * i.IWIDTH / 1296.0 ELSE o.QUANTITY_ORDERED END
        ELSE o.QUANTITY_ORDERED
      END
    )                                                                    AS total_sales_sy
FROM dbo._ORDERS o
JOIN dbo.ITEM i ON o.ITEM_MFGR_COLOR_PAT = i.ItemNumber
WHERE o.[ACCOUNT#I] > 1
  AND o.N_NOT_INVENTORY = 'Y'
  AND o.ORDER_ENTRY_DATE_YYYYMMDD BETWEEN
        TRY_CONVERT(int, REPLACE('{from}','-','')) AND TRY_CONVERT(int, REPLACE('{to}','-',''))
GROUP BY LTRIM(RTRIM(i.IMFGR))
ORDER BY total_sales_sy DESC""",
    },

    # ─── Stock health ─────────────────────────────────────────────────────
    {
        "key":         "stockouts_with_active_sales",
        "label":       "⚠️ Stockouts with active sales",
        "name":        "Stockouts with Active Sales (app window)",
        "description": "SKUs at zero on-hand that had sales in the window — immediate re-buy candidates.",
        "category":    "Stock Health",
        "params":      [],
        "sql": f"""WITH {_SKU_BASE_CTE},
{_SALES_CTE},
{_INV_CTE},
{_OO_CTE}
SELECT
    b.base_sku,
    b.cc,
    b.name,
    COALESCE(s.total_sales_sy, 0)                                                  AS total_sales_sy,
    COALESCE(s.total_sales_sy, 0) / NULLIF(DATEDIFF(day, '{{from}}', '{{to}}') + 1, 0) AS avg_daily_sales_sy,
    COALESCE(i.inventory_sy, 0)                                                    AS inventory_sy,
    COALESCE(o.on_order_sy, 0)                                                     AS on_order_sy
FROM sku_base b
LEFT JOIN sales s ON s.base_sku = b.base_sku
LEFT JOIN inv   i ON i.base_sku = b.base_sku
LEFT JOIN oo    o ON o.base_sku = b.base_sku
WHERE COALESCE(i.inventory_sy, 0) = 0
  AND COALESCE(s.total_sales_sy, 0) > 0
ORDER BY total_sales_sy DESC""",
    },

    {
        "key":         "overstock_high_doi",
        "label":       "🔥 Overstock — Days of Inv (Proj) > 275",
        "name":        "Overstock: Days of Inv (Proj) > 275 (launched > 6 months ago)",
        "description": "SKUs whose net inventory would last more than 275 days at current sales pace, excluding new SKUs (launched within 6 months).",
        "category":    "Stock Health",
        "params":      [],
        "sql": f"""WITH {_SKU_BASE_CTE},
{_SALES_CTE},
{_INV_CTE},
{_OO_CTE},
{_LAUNCH_CTE}
SELECT
    b.base_sku,
    b.cc,
    b.name,
    l.launch_date,
    COALESCE(i.inventory_sy, 0)                                                              AS inventory_sy,
    COALESCE(o.on_order_sy, 0)                                                               AS on_order_sy,
    COALESCE(s.total_sales_sy, 0) / NULLIF(DATEDIFF(day, '{{from}}', '{{to}}') + 1, 0)         AS avg_daily_sales_sy,
    (COALESCE(i.inventory_sy, 0) + COALESCE(o.on_order_sy, 0))
        / NULLIF(COALESCE(s.total_sales_sy, 0) / NULLIF(DATEDIFF(day, '{{from}}', '{{to}}') + 1, 0), 0)
                                                                                             AS days_of_inv_projected
FROM sku_base b
LEFT JOIN sales  s ON s.base_sku = b.base_sku
LEFT JOIN inv    i ON i.base_sku = b.base_sku
LEFT JOIN oo     o ON o.base_sku = b.base_sku
LEFT JOIN launch l ON l.base_sku = b.base_sku
WHERE COALESCE(s.total_sales_sy, 0) > 0
  AND COALESCE(i.inventory_sy, 0) > 0
  AND l.launch_date <= DATEADD(month, -6, CAST(GETDATE() AS date))
  AND (COALESCE(i.inventory_sy, 0) + COALESCE(o.on_order_sy, 0))
        / NULLIF(COALESCE(s.total_sales_sy, 0) / NULLIF(DATEDIFF(day, '{{from}}', '{{to}}') + 1, 0), 0) > 275
ORDER BY days_of_inv_projected DESC""",
    },

    {
        "key":         "runout_risk",
        "label":       "⏳ Runout risk (lead-time × 1.5)",
        "name":        "Runout Risk — Net Inv Below 1.5× Lead-Time Demand",
        "description": "SKUs whose net inventory is below avg_daily × lead_time × 1.5 — at risk of stockout before next PO arrives.",
        "category":    "Stock Health",
        "params":      [],
        "sql": f"""WITH {_SKU_BASE_CTE},
{_SALES_CTE},
{_INV_CTE},
{_OO_CTE}
SELECT
    b.base_sku,
    b.cc,
    b.name,
    COALESCE(NULLIF(i_master.IDELIV, 0), NULLIF(pl.LDELIV, 0), 30)                           AS lead_time_days,
    COALESCE(i.inventory_sy, 0)                                                              AS inventory_sy,
    COALESCE(o.on_order_sy, 0)                                                               AS on_order_sy,
    COALESCE(s.total_sales_sy, 0) / NULLIF(DATEDIFF(day, '{{from}}', '{{to}}') + 1, 0)         AS avg_daily_sales_sy
FROM sku_base b
JOIN dbo.ITEM i_master ON i_master.ItemNumber = b.base_sku
LEFT JOIN dbo.PRODLINE pl ON LTRIM(RTRIM(i_master.IPRODL)) = LTRIM(RTRIM(pl.[LPROD#]))
                         AND LTRIM(RTRIM(i_master.IMFGR))  = LTRIM(RTRIM(pl.[LMFGR#]))
LEFT JOIN sales s ON s.base_sku = b.base_sku
LEFT JOIN inv   i ON i.base_sku = b.base_sku
LEFT JOIN oo    o ON o.base_sku = b.base_sku
WHERE COALESCE(i.inventory_sy, 0) > 0
  AND COALESCE(s.total_sales_sy, 0) > 0
  AND (COALESCE(i.inventory_sy, 0) + COALESCE(o.on_order_sy, 0))
      < ((COALESCE(s.total_sales_sy, 0) / NULLIF(DATEDIFF(day, '{{from}}', '{{to}}') + 1, 0))
         * COALESCE(NULLIF(i_master.IDELIV, 0), NULLIF(pl.LDELIV, 0), 30) * 1.5)
ORDER BY (COALESCE(i.inventory_sy, 0) + COALESCE(o.on_order_sy, 0))
         / NULLIF(COALESCE(s.total_sales_sy, 0) / NULLIF(DATEDIFF(day, '{{from}}', '{{to}}') + 1, 0), 0)
         ASC""",
    },

    {
        "key":         "slow_movers",
        "label":       "🐢 Slow movers (no sales, has inventory)",
        "name":        "Slow Movers — Inventory On Hand, Zero Sales in Window",
        "description": "Active SKUs with inventory but no sales in the From/To window. Sorted by inventory size.",
        "category":    "Stock Health",
        "params":      [],
        "sql": f"""WITH {_SKU_BASE_CTE},
{_SALES_CTE},
{_INV_CTE}
SELECT
    b.base_sku,
    b.cc,
    b.name,
    COALESCE(i.inventory_sy, 0) AS inventory_sy
FROM sku_base b
JOIN inv i ON i.base_sku = b.base_sku
LEFT JOIN sales s ON s.base_sku = b.base_sku
WHERE COALESCE(s.total_sales_sy, 0) = 0
  AND i.inventory_sy > 0
ORDER BY i.inventory_sy DESC""",
    },

    # ─── POs / receipts ───────────────────────────────────────────────────
    {
        "key":         "open_pos_by_cc",
        "label":       "📦 Open POs by cost center",
        "name":        "Open POs by Cost Center",
        "description": "All pending PO lines for a cost-center prefix (e.g. '010'). Use '%' for all.",
        "category":    "POs",
        "params":      ["cc"],
        "sql": """SELECT
    d.[D@REF#]                                                                    AS po_number,
    LTRIM(RTRIM(d.[D@SUPP]))                                                      AS supplier,
    LTRIM(RTRIM(d.[D@MFGR])) + LTRIM(RTRIM(d.[D@COLO])) + LTRIM(RTRIM(d.[D@PATT])) AS sku,
    LTRIM(RTRIM(i.ICCTR))                                                          AS cc,
    LTRIM(RTRIM(i.INAME))                                                          AS name,
    d.[D@QTYO]                                                                     AS qty_ordered,
    d.[D@QTYP]                                                                     AS qty_posted,
    d.[D@QTYO] - d.[D@QTYP]                                                        AS qty_remaining
FROM dbo.OPENPO_D d
JOIN dbo.ITEM i
  ON LTRIM(RTRIM(d.[D@MFGR])) + LTRIM(RTRIM(d.[D@COLO])) + LTRIM(RTRIM(d.[D@PATT])) = i.ItemNumber
WHERE d.[D@ACCT] = 1
  AND d.[D@DEL8] <> '#'
  AND d.[D@SUPP] <> '001'
  AND d.[D@REF#] > 0
  AND d.[D@QTYO] > d.[D@QTYP]
  AND LTRIM(RTRIM(i.ICCTR)) LIKE '{cc}%'
ORDER BY d.[D@REF#] DESC""",
    },

    {
        "key":         "backorders_recent",
        "label":       "🚨 Backorder lines (open)",
        "name":        "Backorder Lines (status B/R, app window)",
        "description": "All lines flagged backorder (DETAIL_LINE_STATUS in B,R) with order entry in the window.",
        "category":    "POs",
        "params":      [],
        "sql": """SELECT
    o.[ORDER#]                                                  AS order_no,
    o.[LINE#I]                                                  AS line_no,
    o.ITEM_MFGR_COLOR_PAT                                       AS sku,
    o.ITEM_DESC_1                                               AS name,
    o.BANK_NAME2                                                AS customer,
    o.QUANTITY_ORDERED                                          AS qty_ordered,
    o.UNIT_OF_MEASURE                                           AS uom,
    o.DETAIL_LINE_STATUS                                        AS status,
    o.ORDER_ENTRY_DATE_YYYYMMDD                                 AS order_entry_yyyymmdd,
    o.[INVOICE#]                                                AS invoice_no
FROM dbo._ORDERS o
WHERE o.DETAIL_LINE_STATUS IN ('B','R')
  AND o.[ACCOUNT#I] > 1
  AND o.N_NOT_INVENTORY = 'Y'
  AND o.ORDER_ENTRY_DATE_YYYYMMDD BETWEEN
        TRY_CONVERT(int, REPLACE('{from}','-','')) AND TRY_CONVERT(int, REPLACE('{to}','-',''))
ORDER BY o.ORDER_ENTRY_DATE_YYYYMMDD DESC, o.[ORDER#] DESC""",
    },

    # ─── Items / catalog ──────────────────────────────────────────────────
    {
        "key":         "discontinued_with_stock",
        "label":       "📉 Discontinued items still in stock",
        "name":        "Discontinued Items With Inventory On Hand",
        "description": "SKUs flagged discontinued (IDISCD set) that still have active rolls — clearance candidates.",
        "category":    "Items",
        "params":      [],
        "sql": """SELECT
    i.ItemNumber                                AS sku,
    LTRIM(RTRIM(i.ICCTR))                       AS cc,
    LTRIM(RTRIM(i.INAME))                       AS name,
    i.IDISCD                                    AS discontinued_date,
    SUM(
      CASE LTRIM(RTRIM(UPPER(r.RUM)))
        WHEN 'SY' THEN r.Available
        WHEN 'SF' THEN CASE WHEN LTRIM(RTRIM(i.ICCTR)) IN ('010','011','012','013') THEN r.Available/9.0 ELSE r.Available END
        WHEN 'LY' THEN CASE WHEN i.IWIDTH > 0 THEN r.Available * i.IWIDTH / 36.0   ELSE r.Available END
        WHEN 'LF' THEN CASE WHEN i.IWIDTH > 0 THEN r.Available * i.IWIDTH / 108.0  ELSE r.Available END
        WHEN 'IN' THEN CASE WHEN i.IWIDTH > 0 THEN r.Available * i.IWIDTH / 1296.0 ELSE r.Available END
        ELSE r.Available
      END
    )                                           AS inventory_sy
FROM dbo.ITEM i
JOIN dbo.ROLLS r ON r.ItemNumber = i.ItemNumber
WHERE LEN(LTRIM(RTRIM(CAST(i.IDISCD AS VARCHAR)))) >= 2
  AND r.Available > 0
  AND r.RLOC1 <> 'REM'
  AND r.[RCODE@] <> '#'
  AND r.[RCODE@] NOT LIKE '%I%'
GROUP BY i.ItemNumber, i.ICCTR, i.INAME, i.IDISCD
ORDER BY inventory_sy DESC""",
    },

    {
        "key":         "inventory_by_cc",
        "label":       "🗂️ Inventory snapshot by cost center",
        "name":        "Inventory Snapshot by Cost Center (SY)",
        "description": "On-hand inventory in SY, rolled up by cost center. Quick portfolio view.",
        "category":    "Items",
        "params":      [],
        "sql": """SELECT
    LTRIM(RTRIM(i.ICCTR))                                 AS cc,
    COUNT(DISTINCT i.ItemNumber)                          AS sku_count,
    COUNT(*)                                              AS roll_count,
    SUM(
      CASE LTRIM(RTRIM(UPPER(r.RUM)))
        WHEN 'SY' THEN r.Available
        WHEN 'SF' THEN CASE WHEN LTRIM(RTRIM(i.ICCTR)) IN ('010','011','012','013') THEN r.Available/9.0 ELSE r.Available END
        WHEN 'LY' THEN CASE WHEN i.IWIDTH > 0 THEN r.Available * i.IWIDTH / 36.0   ELSE r.Available END
        WHEN 'LF' THEN CASE WHEN i.IWIDTH > 0 THEN r.Available * i.IWIDTH / 108.0  ELSE r.Available END
        WHEN 'IN' THEN CASE WHEN i.IWIDTH > 0 THEN r.Available * i.IWIDTH / 1296.0 ELSE r.Available END
        ELSE r.Available
      END
    )                                                     AS inventory_sy
FROM dbo.ROLLS r
JOIN dbo.ITEM i ON r.ItemNumber = i.ItemNumber
WHERE r.Available > 0
  AND r.RLOC1 <> 'REM'
  AND r.[RCODE@] <> '#'
  AND r.[RCODE@] NOT LIKE '%I%'
  AND i.IINVEN = 'Y'
GROUP BY LTRIM(RTRIM(i.ICCTR))
ORDER BY inventory_sy DESC""",
    },

    {
        "key":         "new_skus_180d",
        "label":       "🌱 New SKUs (launched < 180 days)",
        "name":        "New SKUs — Launched In Last 180 Days",
        "description": "Active SKUs whose first sale or first roll receive date is within the last 180 days. The app considers these 'new'.",
        "category":    "Items",
        "params":      [],
        "sql": f"""WITH {_SKU_BASE_CTE},
{_LAUNCH_CTE},
{_INV_CTE},
{_SALES_CTE}
SELECT
    b.base_sku,
    b.cc,
    b.name,
    l.launch_date,
    DATEDIFF(day, l.launch_date, CAST(GETDATE() AS date)) AS age_days,
    COALESCE(i.inventory_sy, 0)                           AS inventory_sy,
    COALESCE(s.total_sales_sy, 0)                         AS total_sales_sy
FROM sku_base b
JOIN launch l ON l.base_sku = b.base_sku
LEFT JOIN inv   i ON i.base_sku = b.base_sku
LEFT JOIN sales s ON s.base_sku = b.base_sku
WHERE l.launch_date > DATEADD(day, -180, CAST(GETDATE() AS date))
ORDER BY l.launch_date DESC""",
    },
]


# Subset of starters surfaced as one-click chips (left to right).
CHIP_KEYS: list[str] = [
    "top_skus_by_sales",
    "top_skus_by_stockturn",
    "stockouts_with_active_sales",
    "overstock_high_doi",
    "runout_risk",
]


def get_starter(key: str) -> Optional[dict]:
    """Look up a starter by its stable key."""
    for q in STARTER_QUERIES:
        if q["key"] == key:
            return q
    return None


def get_chips() -> list[dict]:
    """Return the ordered subset of starters shown as chips."""
    return [q for k in CHIP_KEYS for q in STARTER_QUERIES if q["key"] == k]
