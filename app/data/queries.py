"""All raw SQL strings for the application."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Item master — active stocking items only, with base-SKU alias resolution
# ---------------------------------------------------------------------------
ITEMS_SQL = """
SELECT
    i.ItemNumber                            AS sku,
    COALESCE(NULLIF(LTRIM(RTRIM(i.IIXREF)), ''), i.ItemNumber) AS base_sku,
    LTRIM(RTRIM(i.INAME))                  AS sku_description,
    LTRIM(RTRIM(i.ICCTR))                  AS cost_center,
    LTRIM(RTRIM(i.IPRCCD))                 AS price_class,
    LTRIM(RTRIM(i.[ISUPP#]))               AS supplier_number,
    LTRIM(RTRIM(i.IPRODL))                 AS product_line,
    LTRIM(RTRIM(i.IMFGR))                  AS manufacturer,
    LTRIM(RTRIM(i.IPATT))                  AS item_pattern,
    CAST(COALESCE(NULLIF(i.IDELIV, 0), 0) AS INT) AS item_lead_time_days,
    COALESCE(CAST(i.IWIDTH AS FLOAT), 0)   AS item_width_inches,
    COALESCE(p.[$DESC], '')                AS price_class_desc,
    COALESCE(pl.LNAME, '')                 AS product_line_desc,
    COALESCE(CAST(NULLIF(pl.LDELIV, 0) AS INT), 0) AS product_line_lead_time_days
FROM dbo.ITEM i
LEFT JOIN dbo.PRICE p
       ON LTRIM(RTRIM(p.[$PRCCD])) = LTRIM(RTRIM(i.IPRCCD))
      AND LTRIM(RTRIM(p.[$LIST#])) = 'LP'
LEFT JOIN dbo.PRODLINE pl
       ON LTRIM(RTRIM(pl.[LPROD#])) = LTRIM(RTRIM(i.IPRODL))
      AND LTRIM(RTRIM(pl.[LMFGR#])) = LTRIM(RTRIM(i.IMFGR))
WHERE i.IINVEN = 'Y'
  AND (i.IDISCD IS NULL OR LEN(LTRIM(RTRIM(CAST(i.IDISCD AS VARCHAR)))) < 2)
"""

# ---------------------------------------------------------------------------
# Sales & PO orders — customer lines only (ACCOUNT#I > 1), date-filtered
# ---------------------------------------------------------------------------
ORDERS_SQL = """
SELECT
    o.[ITEM_MFGR_COLOR_PAT]               AS sku,
    o.[QUANTITY_ORDERED]                   AS quantity_ordered,
    LTRIM(RTRIM(o.[UNIT_OF_MEASURE]))      AS unit_of_measure,
    COALESCE(o.[ITEM_WIDTH_INCHES_IF_R], 0) AS item_width_inches,
    LTRIM(RTRIM(o.[DETAIL_LINE_STATUS]))   AS detail_line_status,
    CAST(o.[ORDER_ENTRY_DATE_YYYYMMDD] AS VARCHAR(8)) AS order_entry_date_raw,
    o.[ORDER_SHIP_DATE]                    AS order_ship_date,
    o.[INVOICE_SHIP_DATE]                  AS invoice_ship_date,
    CAST(o.[INVOICE#] AS FLOAT)            AS invoice_number,
    LTRIM(RTRIM(CAST(o.[ORDER#] AS VARCHAR))) AS order_number,
    LTRIM(RTRIM(CAST(o.[LINE#I] AS VARCHAR))) AS line_number,
    LTRIM(RTRIM(o.[COST_CENTER_DESC]))     AS cost_center_desc
FROM dbo._ORDERS o
WHERE o.[N_NOT_INVENTORY] = 'Y'
  AND CAST(o.[ACCOUNT#I] AS INT) > 1
  AND o.[QUANTITY_ORDERED] > 0
  AND CAST(o.[ORDER_ENTRY_DATE_YYYYMMDD] AS BIGINT) BETWEEN :start_ymd AND :end_ymd
"""

# ---------------------------------------------------------------------------
# Open Purchase Orders — warehouse POs (ACCOUNT#I = 1), not-yet-received
# ---------------------------------------------------------------------------
OPEN_PO_ORDERS_SQL = """
SELECT
    o.[ITEM_MFGR_COLOR_PAT]               AS sku,
    o.[QUANTITY_ORDERED]                   AS quantity_ordered,
    LTRIM(RTRIM(o.[UNIT_OF_MEASURE]))      AS unit_of_measure,
    COALESCE(o.[ITEM_WIDTH_INCHES_IF_R], 0) AS item_width_inches,
    LTRIM(RTRIM(CAST(o.[ORDER#] AS VARCHAR))) AS order_number,
    o.[PO_ETA_DATE]                        AS eta_date,
    CAST(o.[INVOICE#] AS FLOAT)            AS invoice_number,
    LTRIM(RTRIM(CAST(o.[SUPPLIER#] AS VARCHAR))) AS supplier_number
FROM dbo._ORDERS o
WHERE o.[N_NOT_INVENTORY] = 'Y'
  AND CAST(o.[ACCOUNT#I] AS INT) = 1
  AND o.[QUANTITY_ORDERED] > 0
  AND (CAST(o.[INVOICE#] AS FLOAT) = 0 OR o.[INVOICE#] IS NULL)
"""

# ---------------------------------------------------------------------------
# Physical inventory rolls
# ---------------------------------------------------------------------------
ROLLS_SQL = """
SELECT
    r.ItemNumber                            AS sku,
    r.Available                             AS available_quantity,
    LTRIM(RTRIM(r.RUM))                    AS unit_of_measure,
    COALESCE(r.RLRCTD, GETDATE())          AS receive_date
FROM dbo.ROLLS r
JOIN dbo.ITEM i ON i.ItemNumber = r.ItemNumber
WHERE i.IINVEN = 'Y'
  AND r.Available > 0
  AND LTRIM(RTRIM(r.RLOC1)) <> 'REM'
  AND LTRIM(RTRIM(COALESCE(r.[RCODE@], ''))) NOT LIKE '%I%'
  AND LTRIM(RTRIM(COALESCE(r.[RCODE@], ''))) <> '#'
"""

# ---------------------------------------------------------------------------
# Pending PO detail (OPENPO_D) — qty not yet received
# ---------------------------------------------------------------------------
PENDING_PO_SQL = """
SELECT
    LTRIM(RTRIM(d.[D@MFGR])) + LTRIM(RTRIM(d.[D@COLO])) + LTRIM(RTRIM(d.[D@PATT])) AS sku,
    d.[D@QTYO]                             AS qty_ordered,
    d.[D@QTYP]                             AS qty_posted,
    d.[D@QTYO] - d.[D@QTYP]               AS pending_qty,
    LTRIM(RTRIM(d.[D@SUPP]))               AS supplier_code,
    TRY_CAST(LTRIM(RTRIM(d.[D@REF#])) AS BIGINT) AS ref_number
FROM dbo.OPENPO_D d
WHERE CAST(d.[D@ACCT] AS INT) = 1
  AND LTRIM(RTRIM(COALESCE(d.[D@DEL8], ''))) <> '#'
  AND LTRIM(RTRIM(d.[D@SUPP])) <> '001'
  AND TRY_CAST(LTRIM(RTRIM(d.[D@REF#])) AS BIGINT) > 0
  AND d.[D@QTYO] > d.[D@QTYP]
"""

# ---------------------------------------------------------------------------
# Distinct filter values
# ---------------------------------------------------------------------------
FILTER_VALUES_SQL = """
SELECT DISTINCT
    LTRIM(RTRIM(i.ICCTR))  AS cost_center,
    LTRIM(RTRIM(i.[ISUPP#])) AS supplier_number,
    LTRIM(RTRIM(i.IPRCCD)) AS price_class,
    COALESCE(p.[$DESC], '') AS price_class_desc,
    LTRIM(RTRIM(i.IPRODL)) AS product_line,
    COALESCE(pl.LNAME, '') AS product_line_desc
FROM dbo.ITEM i
LEFT JOIN dbo.PRICE p
       ON LTRIM(RTRIM(p.[$PRCCD])) = LTRIM(RTRIM(i.IPRCCD))
      AND LTRIM(RTRIM(p.[$LIST#])) = 'LP'
LEFT JOIN dbo.PRODLINE pl
       ON LTRIM(RTRIM(pl.[LPROD#])) = LTRIM(RTRIM(i.IPRODL))
      AND LTRIM(RTRIM(pl.[LMFGR#])) = LTRIM(RTRIM(i.IMFGR))
WHERE i.IINVEN = 'Y'
  AND (i.IDISCD IS NULL OR LEN(LTRIM(RTRIM(CAST(i.IDISCD AS VARCHAR)))) < 2)
  AND LTRIM(RTRIM(i.ICCTR)) NOT LIKE '1%'
"""

# ---------------------------------------------------------------------------
# Validate connection
# ---------------------------------------------------------------------------
PING_SQL = "SELECT 1 AS ping"

# ---------------------------------------------------------------------------
# Daily purchase orders — warehouse POs entered on a specific date,
# grouped by operator.  Used by the Daily POs tab.
# ---------------------------------------------------------------------------
DAILY_POS_SQL = """
SELECT
    LTRIM(RTRIM(CAST(o.[ORDER#]    AS VARCHAR))) AS order_number,
    LTRIM(RTRIM(CAST(o.[LINE#I]   AS VARCHAR))) AS line_number,
    o.[ITEM_MFGR_COLOR_PAT]                     AS sku,
    LTRIM(RTRIM(COALESCE(o.OPERATOR_INITIALS, ''))) AS operator_initials,
    o.[QUANTITY_ORDERED]                         AS quantity_ordered,
    LTRIM(RTRIM(o.[UNIT_OF_MEASURE]))            AS unit_of_measure,
    COALESCE(o.[ITEM_WIDTH_INCHES_IF_R], 0)      AS item_width_inches,
    o.[PO_ETA_DATE]                              AS eta_date,
    LTRIM(RTRIM(CAST(o.[SUPPLIER#] AS VARCHAR))) AS supplier_number,
    COALESCE(o.[ENTENDED_PRICE_NO_FUNDS], 0)     AS extended_price,
    COALESCE(o.[COST_PER_UM], 0)                 AS cost_per_um,
    LTRIM(RTRIM(i.ICCTR))                        AS cost_center,
    LTRIM(RTRIM(i.IPRCCD))                       AS price_class,
    COALESCE(p.[$DESC], '')                      AS price_class_desc,
    LTRIM(RTRIM(COALESCE(i.INAME, '')))          AS sku_description,
    CAST(COALESCE(NULLIF(i.IDELIV, 0), 0) AS INT) AS item_lead_time_days,
    COALESCE(CAST(NULLIF(pl.LDELIV, 0) AS INT), 0) AS product_line_lead_time_days,
    COALESCE(CAST(i.IWIDTH AS FLOAT), 0)         AS item_width_master,
    COALESCE(NULLIF(LTRIM(RTRIM(i.IIXREF)), ''), i.ItemNumber) AS base_sku
FROM dbo._ORDERS o
JOIN dbo.ITEM i
    ON o.[ITEM_MFGR_COLOR_PAT] = i.ItemNumber
LEFT JOIN dbo.PRICE p
    ON LTRIM(RTRIM(p.[$PRCCD])) = LTRIM(RTRIM(i.IPRCCD))
   AND LTRIM(RTRIM(p.[$LIST#])) = 'LP'
LEFT JOIN dbo.PRODLINE pl
    ON LTRIM(RTRIM(pl.[LPROD#])) = LTRIM(RTRIM(i.IPRODL))
   AND LTRIM(RTRIM(pl.[LMFGR#])) = LTRIM(RTRIM(i.IMFGR))
WHERE o.[N_NOT_INVENTORY] = 'Y'
  AND CAST(o.[ACCOUNT#I] AS INT) = 1
  AND o.[QUANTITY_ORDERED] > 0
  AND CAST(o.[ORDER_ENTRY_DATE_YYYYMMDD] AS BIGINT) = :date_ymd
  AND i.IINVEN = 'Y'
  AND (i.IDISCD IS NULL OR LEN(LTRIM(RTRIM(CAST(i.IDISCD AS VARCHAR)))) < 2)
  AND LTRIM(RTRIM(i.ICCTR)) NOT LIKE '1%'
"""
