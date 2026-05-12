# New App Context — SQL Server, Tables, Fields & Tab Definitions

Reference file for building the new application. Documents the SQL Server connection pattern,
every table and field, and full field-level definitions for the Overview and Stock Turn tabs.

---

## 1. SQL Server Connection Setup

### Stack
- **ORM / Driver**: SQLAlchemy with `mssql+pyodbc` dialect
- **ODBC Driver**: `ODBC Driver 18 for SQL Server` (must be installed on host)
- **Authentication**: Windows Trusted Connection (no username/password)
- **Engine options**: `fast_executemany=True`, `pool_pre_ping=True`

### Default Connection String
```
Driver={ODBC Driver 18 for SQL Server};
Server=NRFVMSSQL04;
Database=NRF_REPORTS;
Trusted_Connection=Yes;
Encrypt=no;
```

### Connection String Resolution Order (highest → lowest priority)
1. Environment variable `SQLSERVER_ODBC`
2. `%APPDATA%\PurchaseOrderBot\config.json` → key `"SQLSERVER_ODBC"`
3. `config_local.py` alongside project root → attribute `SQLSERVER_ODBC`

### SQLAlchemy URL Construction
```python
from urllib.parse import quote_plus
odbc_url = f"mssql+pyodbc:///?odbc_connect={quote_plus(connection_string)}"
engine = create_engine(odbc_url, fast_executemany=True, pool_pre_ping=True)
```

### Helper Pattern
```python
# Execute a query and return a pandas DataFrame
def read_dataframe(connection_string, sql, params=None):
    with engine.connect() as conn:
        return pd.read_sql_query(text(sql), conn, params=params)
```

---

## 2. Database Tables and Fields

All tables are in the `dbo` schema of `NRF_REPORTS`.

---

### `dbo._ORDERS` — Sales Orders & Purchase Orders (line items)

Central fact table. Each row is one order line.

| DB Column | Alias used in app | Description |
|---|---|---|
| `ITEM_MFGR_COLOR_PAT` | `sku` | SKU identifier (FK → `ITEM.ItemNumber`) |
| `QUANTITY_ORDERED` | `quantity_ordered` | Raw ordered quantity in native unit of measure |
| `UNIT_OF_MEASURE` | `unit_of_measure` | UOM code (SY, SF, LY, LF, IN, etc.) |
| `ORDER_SHIP_DATE` | `order_ship_date` | Requested ship date (datetime) |
| `INVOICE_SHIP_DATE` | `invoice_ship_date` | Actual ship date from invoice (datetime) |
| `ORDER#` | `order_number` | Order number |
| `LINE#I` | `line_number` | Line number within order |
| `ACCOUNT#I` | `account_number` | Account number. `1` = internal PO; `> 1` = customer sales orders |
| `BANK_NAME2` | `bank_name2` / `customer_name` | Customer name |
| `CUSTOMER_PO#` | `customer_po` | Customer purchase order number |
| `ORDER_TYPE` | `order_type` | Order type code |
| `RESTOCKING_CHARGE_P` | `restocking_charge_p` | Restocking fee percentage |
| `DISCOUNT_HANDLING_CHARGED` | `discount_handling_charged` | Discount/handling amount charged |
| `ENTENDED_PRICE_NO_FUNDS` | `extended_price_no_funds` / `extended_price_usd` | Extended price (revenue) excluding fund discounts |
| `ITEM_WIDTH_INCHES_IF_R` | `item_width_inches` | Item width in inches (roll goods only) |
| `N_NOT_INVENTORY` | `not_inventory_flag` | `'Y'` = inventory item (always filter on this) |
| `ORDER_ENTRY_DATE_YYYYMMDD` | `order_entry_date_raw` | Order entry date as YYYYMMDD integer/string |
| `DETAIL_LINE_STATUS` | `detail_line_status` | `'B'` = backordered, `'R'` = reserved/backorder, other = shipped/open |
| `PO_ETA_DATE` | `po_eta_date` / `eta_date` | Expected arrival date for purchase orders |
| `SUPPLIER#` | `supplier_number` | Supplier code on the order line |
| `USUAL_SUPPLIER` | `usual_supplier` | Usual supplier code linked to the item |
| `INVOICE#` | `invoice_number` | Invoice number (numeric > 0 means shipped/invoiced) |
| `SALESPERSON_DESC` | `salesperson_desc` / `salesperson` | Salesperson name |
| `COST_CENTER_DESC` | `cost_center_desc` | Cost center description text |
| `CREDIT_TYPE_CODE` | `credit_type_code` | Credit type code (FK → `CLASSES.CLCODE` where `CLCAT='CC'`) |
| `REASON_CODE` | `reason_code` | Reason code for returns/credits |
| `ORDER_REASON_CODE_DESC` | `order_reason_code_desc` | Reason code description |
| `ORDER_DATE` | `order_date` | Order date in standard SQL date format |
| `ORDER_DATE_MMDDYY` | `order_date_mmddyy` | Order date in MM/DD/YY format |
| `LINE_GPP_WITH_FUNDS` | `line_gpp_with_funds` | Gross profit including fund discounts |
| `LINE_GPD_WITHOUT_FUNDS` | `line_gross_profit` / `gross_profit_usd` | Gross profit excluding fund discounts |
| `ORDER_REFERENCE#` | `order_reference` | Order reference number |
| `ITEM_DESC_1` | `item_desc_1` / `item_description` | Item description line 1 |
| `PRICE_PER_UM` | `price_per_um` | Unit price |
| `COST_PER_UM` | `cost_per_um` / `line_cost_per_unit` | Unit cost |
| `ITEM_CLASS_1_DESC` | `item_class_1_desc` / `product_category` | Item classification level 1 |
| `ITEM_CLASS_2_DESC` | `item_class_2_desc` | Item classification level 2 |
| `ITEM_CLASS_3_DESC` | `item_class_3_desc` | Item classification level 3 |
| `INVOICE_DATE_YYYYMMDD` | `invoice_date_raw` | Invoice date as YYYYMMDD |

**Key filters always applied:**
- `N_NOT_INVENTORY = 'Y'` (inventory items only)
- `ITEM.IINVEN = 'Y'` (active inventory flag on item master)
- Sales velocity uses only `ACCOUNT#I > 1` (exclude internal PO lines)
- Open Orders filter uses only supplier `'001'`

**Derived columns:**
- `order_entry_date`: parsed from `ORDER_ENTRY_DATE_YYYYMMDD` using format `%Y%m%d`
- `actual_ship_date`: `INVOICE_SHIP_DATE` when `INVOICE# > 0`, else `ORDER_SHIP_DATE`
- `backorder_flag`: `True` when `DETAIL_LINE_STATUS` is exactly `'B'` or `'R'`
- `order_line_id`: `order_number + "-" + line_number` (composite key for deduplication)
- `quantity_sy`: `QUANTITY_ORDERED` converted to square yards (see Unit Conversion section)

---

### `dbo.ITEM` — Item Master

| DB Column | Alias | Description |
|---|---|---|
| `ItemNumber` | `sku` | Primary key — SKU identifier |
| `IPRCCD` | `price_class` | Price class code (FK → `PRICE.$PRCCD`) |
| `ICCTR` | `cost_center` | Cost center code (e.g. `'010'`, `'012'`) |
| `IPRODL` | `product_line` | Product line code (FK → `PRODLINE.LPROD#`) |
| `IMFGR` | `manufacturer` | Manufacturer code (FK → `PRODLINE.LMFGR#`) |
| `INAME` | `sku_description` | Item description / name |
| `IPATT` | `item_pattern` | Pattern code |
| `ISUPP#` | `supplier_number` | Default supplier for this item |
| `IDELIV` | `item_lead_time_days` | Item-level lead time in days |
| `IWIDTH` | `item_width_inches` | Item width in inches (roll goods) |
| `IINVEN` | `inventory_flag` | `'Y'` = active inventory item |
| `IIXREF` | `iixref` | Cross-reference SKU: if set, this item is an alias; `IIXREF` value is the base SKU |
| `IDISCD` | `discontinued_date_raw` / `discontinued_flag` | Discontinuation date as numeric; non-zero = discontinued |
| `IPOL1`, `IPOL2`, `IPOL3` | — | Policy flags; value `'DI'` = "Dropped Item" |

**Active item filter:** `IINVEN = 'Y'` AND `IDISCD` is null/blank/`'0'`

---

### `dbo.ROLLS` — Physical Inventory Rolls

Each row is a physical roll in the warehouse.

| DB Column | Alias | Description |
|---|---|---|
| `ItemNumber` | `sku` | SKU identifier |
| `Available` | `available_quantity` | Available quantity in native UOM |
| `RUM` | `unit_of_measure` | Unit of measure for this roll |
| `RROLL#` | `roll_number` | Roll number |
| `RLOC1` | `location` | Warehouse location code. `'REM'` = remnant (excluded) |
| `RCODE@` | `status_code` | Status code. `'#'` = inactive/reserved (excluded). Rows with `'I'` also excluded |
| `RLRCTD` | `receive_date` | Date roll was received (used for inventory age calculation) |

**Filters applied:**
- `Available > 0`
- `location != 'REM'`
- `status_code != '#'`
- Status does not contain `'I'`
- Only SKUs where `ITEM.IINVEN = 'Y'`

**Derived:**
- `inventory_sy`: `available_quantity` converted to SY using width from `ITEM.IWIDTH`
- `age_days`: `today - receive_date` in days
- `inventory_age_days` (per SKU): weighted average age = Σ(inventory_sy × age_days) / Σ(inventory_sy)

---

### `dbo.OPENIV` — Open Receipts (Goods Received)

| DB Column | Alias | Description |
|---|---|---|
| `NREFTY` | `ref_type` | Reference type. `'R'` = receipt |
| `NDATE` | `receipt_date` | Receipt date |
| `NPO#` | `purchase_order_number` | PO number (links to `_ORDERS.ORDER#`) |
| `NRECEI` | `quantity_received` | Quantity received |
| `NMFGR` | `mfgr_part` | Manufacturer part of SKU |
| `NCOLOR` | `color_part` | Color part of SKU |
| `NPAT` | `pattern_part` | Pattern part of SKU |

**Filter:** `NREFTY = 'R'`

---

### `dbo.OPENPO_D` — Pending Purchase Order Detail

| DB Column | Alias | Description |
|---|---|---|
| `D@MFGR` | `mfgr` | Manufacturer component of SKU |
| `D@COLO` | `colo` | Color component of SKU |
| `D@PATT` | `patt` | Pattern component of SKU |
| `D@QTYO` | `qty_ordered` | Quantity ordered on this PO line |
| `D@QTYP` | `qty_posted` | Quantity posted/received so far |
| `D@ACCT` | `acct` | Account number. `1` = warehouse PO |
| `D@DEL8` | `del8` | Delivery flag. `'#'` = deleted (excluded) |
| `D@SUPP` | `supp` | Supplier code. `'001'` = excluded from pending |
| `D@REF#` | — | PO reference number (must be valid integer > 0) |

**Derived:** `sku` = `MFGR + COLO + PATT` (concatenated); `po_pending_qty` = `qty_ordered - qty_posted` (in SY)

**Partials filter:** `ACCT=1`, `del8 != '#'`, `qty_posted > 0`
**Pending filter:** `ACCT=1`, `del8 != '#'`, `supp != '001'`, `ref# > 0`

---

### `dbo.OPENPO_M` — PO Message / Fee Lines

| DB Column | Alias | Description |
|---|---|---|
| `M@REF#` | `order_number` | PO reference number |
| `M@LINE` | `line_number` | Line number |
| `M@GL#` | `gl_number` | GL account number. `9140` = restocking fee |
| `M@MISP` | `fee_amount` | Fee amount |
| `M@MSG` | `message_text` | Message text (used for return reason identification) |

---

### `dbo.PRODLINE` — Product Lines

| DB Column | Alias | Description |
|---|---|---|
| `LPROD#` | `product_line` | Product line code |
| `LMFGR#` | `manufacturer` | Manufacturer code |
| `LNAME` | `product_line_desc` | Product line description |
| `LDELIV` | `product_line_lead_time_days` | Default lead time in days for this product line |

**Relationship:** `ITEM.IPRODL + ITEM.IMFGR` → `PRODLINE.LPROD# + PRODLINE.LMFGR#`

---

### `dbo.PRICE` — Price Classes

| DB Column | Alias | Description |
|---|---|---|
| `$PRCCD` | `price_class` | Price class code |
| `$LIST#` | — | List type. Always filter: `$LIST# = 'LP'` |
| `$DESC` | `price_class_desc` | Price class description / name |

**Relationship:** `ITEM.IPRCCD` → `PRICE.$PRCCD` (where `$LIST# = 'LP'`)

---

### `dbo.sysTableUpdates` — Table Modification Timestamps

Used for smart refresh: the app queries this before each data load to skip tables that have not changed since the last refresh.

| DB Column | Alias | Description |
|---|---|
| `TABLE_NAME` | — | Name of the table in NRF_REPORTS; `'DW0001F'` maps to `_ORDERS` |
| `LAST_UPDATE` | — | `DATETIME` of the most recent modification to that table |

**Special mapping:** `TABLE_NAME = 'DW0001F'` represents the `_ORDERS` table.
All other tables watched by the app (`ITEM`, `PRICE`, `PRODLINE`, `ROLLS`, `OPENPO_D`) match exactly.

**Query used:**
```sql
SELECT
    LTRIM(RTRIM(TABLE_NAME))         AS table_name,
    CAST(LAST_UPDATE AS VARCHAR(30)) AS last_update
FROM dbo.sysTableUpdates
WHERE LTRIM(RTRIM(TABLE_NAME)) IN (
    'DW0001F', 'ITEM', 'ROLLS', 'OPENPO_D', 'PRODLINE', 'PRICE'
)
```

---

| DB Column | Alias | Description |
|---|---|---|
| `CLCAT` | — | Category code. `'CC'` = credit type |
| `CLCODE` | `credit_type_code` | The code value |
| `CLDESC` | `credit_type_desc` | Human-readable description of the code |

**Relationship:** `_ORDERS.CREDIT_TYPE_CODE` → `CLASSES.CLCODE` (where `CLCAT = 'CC'`)

---

### `dbo.ITEMSTK` — Item Stock Targets

| DB Column | Alias | Description |
|---|---|---|
| `ItemNumber` | `sku` | SKU identifier |
| `JSTOCK` | `jstock` | Target stock quantity (system-set stock turn target) |

---

### `dbo._INVENTORY` — Inventory Cost View

| DB Column | Alias | Description |
|---|---|---|
| `Item` | `sku` | SKU identifier |
| `TotalCost` | `total_cost` | Total cost of current inventory for this SKU |

**Filter:** `ITEM.IINVEN = 'Y'` AND `TotalCost > 0`

---

## 3. Table Relationships Summary

```
_ORDERS.ITEM_MFGR_COLOR_PAT  ─────→  ITEM.ItemNumber
_ORDERS.CREDIT_TYPE_CODE      ─────→  CLASSES.CLCODE  (where CLCAT='CC')
ITEM.IPRCCD                   ─────→  PRICE.$PRCCD    (where $LIST#='LP')
ITEM.IPRODL + ITEM.IMFGR      ─────→  PRODLINE.LPROD# + PRODLINE.LMFGR#
ITEM.IIXREF                   ─────→  ITEM.ItemNumber (self-ref alias → base SKU)
ROLLS.ItemNumber               ─────→  ITEM.ItemNumber
ITEMSTK.ItemNumber             ─────→  ITEM.ItemNumber
_INVENTORY.Item               ─────→  ITEM.ItemNumber
OPENIV.NPO#                   ─────→  _ORDERS.ORDER#  (receipt match)
OPENPO_D: D@MFGR+D@COLO+D@PATT ───→  ITEM.ItemNumber (SKU = mfgr+color+pattern)
```

---

## 4. Unit Conversion — All Quantities Standardized to Square Yards (SY)

| Input UOM | Condition | Conversion |
|---|---|---|
| SY, SQY, SQYD, SQYDS | Any | qty × 1 (already SY) |
| SF, SQF, FT2, SQFT | Cost center in `010`, `011`, `012`, `013` | qty ÷ 9 |
| SF, SQF, FT2, SQFT | Other cost centers | qty × 1 (no conversion) |
| LY, YD, YDS, YARD | Width available | (qty × width_inches) ÷ 36 |
| LY, YD, YDS, YARD | No width | qty × 1 (raw) |
| LF, FT, FEET, FOOT | Width available | (qty × width_inches) ÷ 108 |
| IN, INCH, INCHES | Width available | (qty × width_inches) ÷ 1296 |
| Other | — | qty × 1 |

Width source priority: `_ORDERS.ITEM_WIDTH_INCHES_IF_R` → `ITEM.IWIDTH` (via base_sku map)

---

## 5. SKU Alias / Base SKU Logic

- `ITEM.IIXREF` stores a cross-reference SKU for alias items.
- If `IIXREF` is populated, that item is an **alias**; `IIXREF` value is the **base SKU**.
- All sales, inventory, and PO data is remapped to the base SKU before aggregation.
- The ITEM table is queried twice: once CC-scoped (for metrics), once globally (for attribute enrichment).

---

## 6. Core Computed Metrics (per-SKU)

| Metric | Formula | Source columns |
|---|---|---|
| `avg_daily_sales_sy` | `total_quantity_sy / days_in_window` | `_ORDERS.QUANTITY_ORDERED` → `quantity_sy` |
| `orders_count` | Distinct order lines (order_line_id) | `_ORDERS` |
| `backorder_count` | Distinct order lines where status is `'B'` or `'R'` | `_ORDERS.DETAIL_LINE_STATUS` |
| `backorder_qty_sy` | Sum of `quantity_sy` where status = `'B'` only | `_ORDERS.DETAIL_LINE_STATUS` |
| `inventory_sy` | Sum of available roll quantities in SY | `ROLLS.Available` |
| `on_order_sy` | Sum of PO quantities in SY (ACCOUNT#I=1) | `_ORDERS.QUANTITY_ORDERED` |
| `po_pending_qty` | Σ(qty_ordered - qty_posted) from OPENPO_D | `OPENPO_D.D@QTYO - D@QTYP` |
| `net_inventory_sy` | `inventory_sy + on_order_sy + partial_received_po` | Derived |
| `days_of_inventory` | `inventory_sy / avg_daily_sales_sy` (inf when no sales) | Derived |
| `inventory_age_days` | Σ(inventory_sy × age_days) / Σ(inventory_sy) | `ROLLS.RLRCTD` |
| `days_since_last_sale` | `today - max(order_entry_date)` | `_ORDERS.ORDER_ENTRY_DATE_YYYYMMDD` |
| `fill_rate` | `1 - (backorder_count / orders_count)` | Derived, clamped 0–1 |
| `stock_turn` | `(avg_daily_sales_sy × 365) / inventory_sy` | Derived |
| `sku_rating` | A/B/C/D quartile bucket by `orders_count` | Derived |
| `runout_risk` | Boolean: inventory runs out before reorder arrives | Derived (lead time + avg daily sales vs inventory) |
| `actual_ship_date` | `INVOICE_SHIP_DATE` if invoiced, else `ORDER_SHIP_DATE` | `_ORDERS` |

---

## 7. Overview Tab — Field Definitions

### Summary KPI Cards

| KPI Card | Metric | Calculation |
|---|---|---|
| **Stock Turn** | `summary["stock_turn"]` | `(Σ avg_daily_sales_sy × 365) / Σ inventory_sy` across all SKUs |
| **Fill Rate** | `summary["fill_rate"]` | `1 - (Σ backorder_count / Σ orders_count)` across all SKUs |
| **Days of Inventory** | `summary["days_of_inventory"]` | Median of per-SKU `days_of_inventory` values |
| **Aging SKUs** | `summary["aging_bad_sku_count"]` | Count of SKUs where `days_since_last_sale >= 540` (18 months) |
| **Runout Risk** | `summary["runout_sku_count"]` | Count of SKUs where `runout_risk = True` |
| **Total SKUs** | `summary["total_skus"]` | Count of all SKUs in current filter scope |

### Sidebar Filters (applied globally across all tabs)
- **Cost Centers** (multiselect) → filters `ITEM.ICCTR`; cost centers starting with `'1'` always excluded
- **Suppliers** (multiselect) → filters `ITEM.ISUPP#`
- **Price Classes** (multiselect) → filters `ITEM.IPRCCD`
- **SKU Rating** (multiselect A/B/C/D) → filters `sku_rating`
- **Search SKU** (text) → substring match on `sku`
- **Date Range**: Fixed `2025-08-04` through today (not user-adjustable in Overview)

### Per-SKU Table — Overview Tab Columns

| Display Column | Internal Field | Description |
|---|---|---|
| SKU | `sku` | Base SKU identifier |
| Description | `sku_description` | `ITEM.INAME` |
| Price Class | `price_class_desc` | `PRICE.$DESC` |
| Cost Center | `cost_center` | `ITEM.ICCTR` |
| Rating | `sku_rating` | A/B/C/D quartile based on `orders_count` |
| Inventory (SY) | `inventory_sy` | Available warehouse inventory in SY from ROLLS |
| On Order (SY) | `on_order_sy` | Open PO quantity in SY (ACCOUNT#I=1 lines) |
| Pending PO | `po_pending_qty` | OPENPO_D net qty (ordered − posted), in SY |
| Net Inventory | `net_inventory_sy` | `inventory_sy + on_order_sy + partial_received_po` |
| Avg Daily Sales (SY) | `avg_daily_sales_sy` | `total_quantity_sy / days_in_range` |
| Orders | `orders_count` | Distinct order-line count |
| Backorders | `backorder_count` | Distinct backorder lines (status `B` or `R`) |
| BO Qty (SY) | `backorder_qty_sy` | Sum of SY quantity with status `'B'` only |
| Days of Inventory | `days_of_inventory` | `inventory_sy / avg_daily_sales_sy` |
| Inventory Age (days) | `inventory_age_days` | Weighted average roll age by SY quantity |
| Runout Risk | `runout_risk` | Boolean flag |
| Days Since Last Sale | `days_since_last_sale` | Calendar days since last `order_entry_date` |

### Details Dialog (drill-down)
Triggered from sidebar buttons or SKU table rows. Shows:
- **Total Inventory (SY)** metric card
- **Weekly Sales chart** (SKU vs its price class, dual Y-axis)
- **Backorders table**: order_number, quantity_sy, actual_ship_date (status `'B'` only)
- **Purchase Orders table**: order_number, quantity_sy, eta_date

---

## 8. Stock Turn Tab — Field Definitions

### Date Range Controls
- **Start date** (`stock_turn_start_date`): defaults to `2025-08-04`
- **End date** (`stock_turn_end_date`): defaults to today
- **Use last full month for MTD** checkbox: when checked, MTD = previous complete calendar month

### Computed Date Windows

| Window | Definition |
|---|---|
| **YTD range** | `stock_start` → `stock_end` (user selected) |
| **MTD range (normal)** | First day of `stock_end` month → `stock_end` |
| **MTD range (full-month mode)** | First → last day of the month prior to `stock_end` |

### Report Table Columns

| Display Column | Internal Field | Formula / Source |
|---|---|---|
| Price Class | `price_class_desc` | `PRICE.$DESC` via ITEM.IPRCCD join |
| SKU | `sku` | Base SKU identifier |
| Description | `sku_description` | `ITEM.INAME` |
| Rating | `sku_rating` | A/B/C/D recalculated from `orders_count` within the YTD date range |
| Units YTD (SY) | `units_ytd_sy` | Sum of `quantity_sy` for orders in YTD range |
| Units MTD (SY) | `units_mtd_sy` | Sum of `quantity_sy` for orders in MTD range |
| Inventory (SY) | `inventory_sy` | Current warehouse inventory from ROLLS |
| On Order (SY) | `on_order_sy` | Open PO quantity in SY from _ORDERS (ACCOUNT#I=1) |
| YTD Turn | `ytd_turn` | `(avg_daily_sales_sy × 365) / inventory_sy` |
| MTD Turn | `mtd_turn` | `(units_mtd_sy × (days_in_month / elapsed_days) × 12) / inventory_sy` |
| Fill Rate (YTD) | `fill_rate` | `1 - (backorder_count / orders_count)` for YTD range |
| Fill Rate (MTD) | `mtd_fill_rate` | `1 - (backorder_count_mtd / orders_count_mtd)` for MTD range |
| Days of Inventory | `days_of_inventory` | `inventory_sy / avg_daily_sales_sy` (YTD range) |
| Inventory Age (days) | `inventory_age_days` | Weighted average roll age |

### Stock Turn Formulas

```
avg_daily_sales_sy = units_ytd_sy / days_in_range
days_in_range      = (stock_end - stock_start).days + 1  (minimum 1)

ytd_turn  = (avg_daily_sales_sy × 365) / inventory_sy
mtd_turn  = (units_mtd_sy × (days_in_month / elapsed_days) × 12) / inventory_sy

fill_rate     = 1 - (backorder_count     / orders_count)      [clamped 0–1]
mtd_fill_rate = 1 - (backorder_count_mtd / orders_count_mtd)  [clamped 0–1]

days_of_inventory = inventory_sy / avg_daily_sales_sy
```

- Both turn metrics → `0` when `inventory_sy = 0`
- `mtd_turn` uses `elapsed_days = days_in_month` when full-month mode is on

### Stock Turn Target
- Configurable per cost center via `%APPDATA%\PurchaseOrderBot\config\stockturn_targets.json`
- Default global target: `4.0` (stored in `AppConfig.stockturn_target`)

### Default Sort Order
1. `price_class_desc` ascending
2. `units_mtd_sy` ascending (lowest sellers first)
3. `sku` ascending

### PDF Export Columns
```
Price Class | SKU | Desc | Inv(SY) | On Order(SY) | YTD Units | MTD Units |
YTD Turn | MTD Turn | Fill% | Fill%_MTD | DOI
```
Plus a group-level summary row per price class.

---

## 9. Key Business Rules

| Rule | Detail |
|---|---|
| Active inventory items | `ITEM.IINVEN = 'Y'` |
| Exclude discontinued items | `LEN(LTRIM(RTRIM(CAST(ITEM.IDISCD AS VARCHAR)))) < 2` (null, blank, or single char like `'0'`) |
| Dropped items | `ITEM.IPOL1` or `IPOL2` or `IPOL3 = 'DI'` AND `IDISCD > 0` |
| Sales orders (customer) | `_ORDERS.ACCOUNT#I > 1` |
| Purchase orders (warehouse) | `_ORDERS.ACCOUNT#I = 1` |
| Open Orders filter | `SUPPLIER# = '001'` AND `ACCOUNT#I != 1` |
| Backorder status | `DETAIL_LINE_STATUS` exactly `'B'` or `'R'` (case-insensitive) |
| Strict backorder qty | Only `'B'` status (not `'R'`) for quantity-level backorder metrics |
| Remnant rolls excluded | `ROLLS.RLOC1 = 'REM'` → excluded |
| Inactive roll status | `ROLLS.RCODE@ = '#'` or contains `'I'` → excluded |
| Valid PO number | `_ORDERS.ORDER# > 0` (numeric) |
| Exclude cost centers starting with '1' | Applied in `_resolve_cost_centers()` |
| Future-dated orders | Excluded (order_entry_date > today) |
| Non-positive quantities | Excluded from all metrics |
| SKU alias resolution | If `ITEM.IIXREF` is set, map SKU → IIXREF as base before any groupby |
| OPENPO_D supplier exclusion | `D@SUPP = '001'` excluded from pending POs |

---

## 10. AppConfig Defaults

```python
connection_string:      (resolved from env/file/config_local)
stockturn_target:       4.0       # default stock turn target
default_cost_centers:   ["010"]
default_date_months:    18        # historical window for demand
rating_buckets:         (0.25, 0.50, 0.75)  # quartile thresholds for A/B/C/D
cache_ttl_seconds:      360       # 6 minutes — how long SQLAlchemy query results are cached
```

---

## 11. File Structure Reference (Planned)

```
app/
  config.py              — AppConfig dataclass, connection string resolution
  data/
    db.py                — SQLAlchemy engine, read_dataframe(), validate_connection()
    queries.py           — All raw SQL strings (ORDERS_BASE, ITEMS, ROLLS, etc.)
    loaders.py           — Data loading functions with filter/param injection
    stockturn_store.py   — Per-cost-center stock turn target persistence (JSON)
    seasonality_store.py — Monthly seasonality % per cost center (JSON)
    launch_store.py      — Price class launch date tracking
    history_store.py     — Metrics snapshot history (CSV)
    backorder_store.py   — Backorder persistence
  services/
    metrics_service.py   — compute_dashboard_data(), all KPI calculations
    sku_rating.py        — assign_sku_ratings() A/B/C/D quartile logic
    reorder.py           — Reorder point / runout risk calculations
  ui/
    dashboard.py         — Streamlit UI (all tabs)
config_local.py          — Local connection string override (not committed)
```

---

## 12. Built Application — Implementation Reference

> **Status:** Fully built and deployed to GitHub (`lstred/NewPurchaseOrderBot`).  
> **Last updated:** 2026-05-05  
> **Python:** 3.11 · **Venv:** `.venv/` in project root  
> **Run:** `.\.venv\Scripts\python.exe main.py`

---

### 12.1 Actual File Structure

```
NewPurchBot/
  main.py                    — Entry point: QApplication, MainWindow, exception hook
  validate_db.py             — Standalone DB validation script (run anytime)
  app.spec                   — PyInstaller spec (onefile exe, no console)
  requirements.txt           — PyQt6, plotly, pandas, SQLAlchemy, pyodbc, PyInstaller
  config_local.py            — Local ODBC override (gitignored)
  .gitignore
  CLAUDE.md                  — This file

  app/
    config.py                — AppConfig dataclass + connection string resolution
    __init__.py

    data/
      db.py                  — Engine singleton, read_dataframe(), validate_connection()
      queries.py             — All SQL strings: ITEMS_SQL, ORDERS_SQL, ROLLS_SQL, etc.
      loaders.py             — load_items/orders/rolls/open_pos/pending_pos/filter_values()
      store.py               — JSON persistence: targets, snooze state, launch dates
      cache.py               — Smart refresh: sysTableUpdates check, in-memory DF store
      __init__.py

    services/
      metrics_service.py     — compute_all() → DatasetBundle; all per-SKU KPI logic
      __init__.py

    ui/
      theme.py               — DARK/LIGHT palettes, full QSS, toggle()
      widgets.py             — KpiCard, DataTable, FilterSidebar, HSep, chart helpers
      main_window.py         — MainWindow: toolbar, tabs, QThread background loader
      tab_overview.py        — Overview tab: 6 KPI cards + 21-column SKU table
      tab_timeline.py        — Inventory Timeline: 180-day Plotly projection per SKU
      tab_problems.py        — Problem Areas: alert cards with snooze + Timeline button
      tab_daily_pos.py       — Daily POs: per-date PO activity grouped by operator initials
      tab_settings.py        — Settings: stock-turn targets at all filter levels
      timeline_popup.py      — Reusable TimelineDialog popup (used from Overview + Problems)
      overview_dialogs.py    — ColumnManagerDialog + ThresholdRulesDialog for Overview table
      __init__.py
```

---

### 12.2 Technology Stack

| Component | Package | Version |
|---|---|---|
| UI framework | PyQt6 | 6.7.1 |
| Charts | plotly + PyQt6-WebEngine | 5.22+ |
| Data | pandas | 2.2.2 |
| DB driver | SQLAlchemy + pyodbc | 2.0.x + 5.1.0 |
| Packaging | PyInstaller | 6.8.0 |

---

### 12.3 Running the App

```powershell
# From project root
.\.venv\Scripts\python.exe main.py

# Validate DB (safe read-only checks)
.\.venv\Scripts\python.exe validate_db.py

# Build exe
.\.venv\Scripts\python.exe -m PyInstaller app.spec
# Output: dist\InventoryControl.exe
```

---

### 12.4 Critical SQL Column Name Quirks

These quirks caused bugs and must be remembered for any future queries.

| Table | Column | How to reference in SQL | Notes |
|---|---|---|---|
| `PRICE` | `$PRCCD` | `p.[$PRCCD]` | Dollar-sign prefix — bracket AND include `$` |
| `PRICE` | `$LIST#` | `p.[$LIST#]` | Dollar-sign prefix — bracket AND include `$` |
| `PRICE` | `$DESC` | `p.[$DESC]` | Dollar-sign prefix — bracket AND include `$` |
| `_ORDERS` | `ORDER#` | `o.[ORDER#]` | Hash in name — must bracket |
| `_ORDERS` | `LINE#I` | `o.[LINE#I]` | Hash in name — must bracket |
| `_ORDERS` | `ACCOUNT#I` | `o.[ACCOUNT#I]` | Hash in name — must bracket |
| `_ORDERS` | `INVOICE#` | `o.[INVOICE#]` | Hash in name — must bracket |
| `_ORDERS` | `SUPPLIER#` | `o.[SUPPLIER#]` | Hash in name — must bracket |
| `ITEM` | `ISUPP#` | `i.[ISUPP#]` | Hash in name — must bracket |
| `ROLLS` | `RCODE@` | `r.[RCODE@]` | At-sign in name — must bracket |
| `ROLLS` | `RROLL#` | `r.[RROLL#]` | Hash in name — must bracket |
| `OPENPO_D` | `D@MFGR` | `d.[D@MFGR]` | At-sign prefix — must bracket |
| `OPENPO_D` | `D@COLO` | `d.[D@COLO]` | At-sign prefix — must bracket |
| `OPENPO_D` | `D@PATT` | `d.[D@PATT]` | At-sign prefix — must bracket |
| `OPENPO_D` | `D@QTYO` | `d.[D@QTYO]` | At-sign prefix — must bracket |
| `OPENPO_D` | `D@QTYP` | `d.[D@QTYP]` | At-sign prefix — must bracket |
| `OPENPO_D` | `D@ACCT` | `d.[D@ACCT]` | At-sign prefix — must bracket |
| `OPENPO_D` | `D@DEL8` | `d.[D@DEL8]` | At-sign prefix — must bracket |
| `OPENPO_D` | `D@SUPP` | `d.[D@SUPP]` | At-sign prefix — must bracket |
| `OPENPO_D` | `D@REF#` | `d.[D@REF#]` | At-sign prefix + hash — must bracket |
| `PRODLINE` | `LPROD#` | `pl.[LPROD#]` | Hash in name — must bracket |
| `PRODLINE` | `LMFGR#` | `pl.[LMFGR#]` | Hash in name — must bracket |

> **Rule:** Any column containing `#`, `@`, or `$` must be wrapped in `[square brackets]` in T-SQL,
> and the special character must be included inside the brackets exactly as it appears in the DB.

---

### 12.5 AppData Persistence Files

All JSON files stored at `%APPDATA%\PurchaseOrderBot\`:

| File | Purpose | Key format |
|---|---|---|
| `config.json` | Optional connection string override | `{"SQLSERVER_ODBC": "..."}` |
| `stockturn_targets.json` | Per-level stock-turn targets | `"global"`, `"cc:010"`, `"pc:PRMPLF"`, `"pl:ROC"`, `"sup:MAR"`, `"sku:ABCDEF"` |
| `snooze.json` | Snoozed problem alerts | `"{type}:{sku}": {"until": "YYYY-MM-DD", "po_qty_at_snooze": 0.0}` |
| `launch_dates.json` | Auto-detected earliest sale/receipt date per SKU | `"{sku}": "YYYY-MM-DD"` |
| `refresh_state.json` | Smart refresh: last-seen sysTableUpdates timestamps + date range | `{"timestamps": {"DW0001F": "...", "ITEM": "..."}, "date_range": "start:end"}` |
| `column_widths.json` | Per-table column widths in pixels | `{"overview": {"SKU": 80, ...}, "overview_pc": {...}, "daily_pos": {...}}` |
| `operator_names.json` | Operator initials → full name mapping for Daily POs tab | `{"ABC": "Alice Brown", "JD": "John Doe"}` |

---

### 12.6 Key Design Decisions

| Decision | Detail |
|---|---|
| **UI framework** | PyQt6 — native desktop, no browser required, suitable for PyInstaller exe |
| **Charts** | Plotly rendered in `QWebEngineView`; falls back to placeholder label if WebEngine not installed |
| **Background loading** | `QThread` + `QObject` worker pattern — SQL queries run off the main thread so UI stays responsive |
| **SKU alias resolution** | Done in Python (loaders.py), not SQL — `ITEM.IIXREF` maps alias → base SKU before any groupby |
| **Snooze auto-unsnooze** | PO quantity check always runs first in `is_snoozed()`; if on-order qty changed, snooze is cleared |
| **Stock-turn conflict resolution** | Most specific key wins: `sku:` > `cc:` > `pc:` > `pl:` > `sup:` > `global` |
| **Fill rate definition** | `filled_count / orders_count` where `filled_count` = lines where status is NOT `'B'` or `'R'` |
| **Backorder qty** | Only `'B'` status (not `'R'`) counted toward `strict_bo_qty_sy` |
| **Cost center exclusion** | Any CC starting with `'1'` is always excluded — applied in `_apply_item_filters()` |
| **Future-dated orders excluded** | `order_entry_date > today` filtered out in `load_orders()` |
| **Smart refresh** | Before each load, `app.data.cache` queries `sysTableUpdates`; only stale datasets are reloaded from SQL. If ITEM/PRICE/PRODLINE change, all datasets are invalidated (alias map cascade). If sysTableUpdates is unreachable, all datasets reload as a safe fallback. Timestamps + last date range persisted in `refresh_state.json`. Status bar shows `↻ refreshed: ...  ▪  ⚡ cached: ...` after each load. |
| **Alias resolution uses full items** | `load_orders/open_pos/rolls/pending_pos` are called with the full (unfiltered) items DF so alias maps are complete regardless of active cost-center filter |
| **Lazy timeline building** | `DatasetBundle.timeline` is populated on-demand via `get_sku_timeline(sku, bundle)` — avoids pre-building 17,000+ DataFrames. `po_events: dict[str, list[dict]]` is built upfront (cheap) and feeds both the PO table and the lazy timeline builder. |
| **PO chart visibility** | PO receipts shown as dotted vertical lines (`add_vline`) + triangle-up scatter markers with hover tooltips. Old `go.Bar` approach was invisible on a 180-day x-axis scale. |

---

### 12.7 Bugs Fixed (for future AI context)

| Bug | File | Root Cause | Fix Applied |
|---|---|---|---|
| `Invalid column name 'PRCCD'` | `queries.py` | PRICE table columns have `$` prefix; referenced without it | Changed `[PRCCD]` → `[$PRCCD]`, `[LIST#]` → `[$LIST#]`, `[DESC]` → `[$DESC]` in both ITEMS_SQL and FILTER_VALUES_SQL |
| Snooze "until PO qty changes" never stuck | `store.py` | `is_snoozed()` fell through to `return False` after PO qty check | Reordered: check PO qty change first (unsnooze if changed), then check date, then return `True` for indefinite snooze |
| Timeline reorder markers on wrong day | `metrics_service.py` | `records.index(rec)` finds first match, breaks on duplicate dict values | Replaced with `enumerate(records)` |
| `QDate` imported inline via `__import__` | `main_window.py` | Leftover hack from development | Moved to proper top-level `from PyQt6.QtCore import QDate` |
| IDISCD filter changed from `IN ('','0')` to `LEN < 2` | `queries.py` | Any 1-character IDISCD value (not just `'0'`) should be treated as not discontinued | Changed both ITEMS_SQL and FILTER_VALUES_SQL to `LEN(LTRIM(RTRIM(CAST(i.IDISCD AS VARCHAR)))) < 2` |
| `sku_selected` double-click now shows timeline popup | `tab_overview.py` | User wanted click-to-popup timeline without leaving the overview | Changed `_on_row_double_clicked` to open `TimelineDialog`; popup has "Open in Timeline Tab" button to still navigate |
| Timeline popup added to Problem Areas | `tab_problems.py` | User wanted timeline accessible from alert cards | Added `timeline_requested` signal to `AlertCard`, "📈 Timeline" button, wired to `TimelineDialog` in `ProblemAreasTab` |
| Smart refresh via sysTableUpdates | `cache.py`, `metrics_service.py`, `main_window.py` | Avoid re-querying unchanged tables on every refresh | `cache.py` fetches timestamps from `sysTableUpdates`, compares to saved state, returns stale dataset set; `compute_all()` only reloads stale ones; status bar shows `↻ refreshed / ⚡ cached` breakdown |
| PO receipt bars invisible on chart | `timeline_popup.py`, `tab_timeline.py` | `go.Bar` traces are ~1px wide on a 180-day scale — invisible | Replaced with `fig.add_vline()` (dotted green lines) + `go.Scatter` triangle-up markers at receipt points; rich hover tooltip shows qty + order numbers |
| PO table always empty | `timeline_popup.py`, `tab_timeline.py` | `populate()` called inside `__init__` before `show()` — WebEngine layout recalculation collapses table rows; data source was also wrong (open_pos filter vs po_events) | Use `bundle.po_events` (same data as chart markers — confirmed correct); defer `populate()` via `QTimer.singleShot(80, ...)` so it runs after dialog is fully shown and layout is stable |
| App lag / near-freezing on refresh | `metrics_service.py`, `widgets.py` | (a) Pre-building 17,000+ timeline DataFrames (3M+ rows) in `compute_all()`; (b) `resizeRowsToContents()` on 17,000-row table; (c) `groupby().apply()` for inventory age; (d) `resolve_target()` reading JSON file per SKU | (a) Removed `_build_timelines()`; timelines now built lazily via `get_sku_timeline()` only when a SKU is actually viewed; (b) Removed `resizeRowsToContents()`, added `setUpdatesEnabled(False/True)` around populate; (c) Vectorized weighted avg using `groupby().agg()`; (d) Single `get_all_targets()` call + `_targets_cache` param passed through |
| `AA_ShareOpenGLContexts` warning | `main.py` | Qt attribute must be set before `QApplication` is created | Added `QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)` before `QApplication(sys.argv)` |

---

### 12.8 Validation Results (2026-05-05)

All 11 tables confirmed live with data:

| Table | Rows returned | Notes |
|---|---|---|
| `_ORDERS` (sales) | 5 | Account#I > 1, N_NOT_INVENTORY='Y' |
| `_ORDERS` (POs) | 5 | Account#I = 1 |
| `ITEM` | 5 | IINVEN='Y' — 17,275 total active items |
| `ROLLS` | 5 | Available > 0, no REM/inactive |
| `OPENPO_D` | 5 | Account=1, not deleted, not 001 supplier |
| `PRODLINE` | 5 | All rows |
| `PRICE` | 5 | $LIST#='LP' |
| `CLASSES` | 5 | CLCAT='CC' |
| `ITEMSTK` | 5 | All rows |
| `_INVENTORY` | 5 | TotalCost > 0 |
| `OPENIV` | 5 | NREFTY='R' |

---

### 12.9 Recent Changes (2026-05-12)

| Change | Files | Reason | Solution |
|---|---|---|---|
| **AI v4.9 — Single OVERSTOCK RISK tag, drop EXPEDITE, enforce 700d floor on red-flag POs** | `app/ai/brief.py`, `app/ai/brief_renderer.py`, `app/ai/schema.py` | After v4.8 shipped, the user reviewed CC 010 in the rendered brief and pointed out **eight rows under the 700-day DOI floor** (e.g. `[CANCEL] SHAINDIPRLD ... 285.7 DOI`, `[CANCEL] TARSLOPWIND ... 172.3 DOI`). Root cause: `_build_actionable_metrics()` step 3 (`redflag_new_pos`) was filtering at `days_of_inventory_projected > 365 OR sku in overstock_set` — both branches let DOI well below 700 through. Separately, the user asked to (a) drop the EXPEDITE category entirely (*"the lead time is the lead time and not much we can do about it"*) and (b) collapse `[CANCEL]` + `[DEFER]` into one tag called `[OVERSTOCK RISK]`. | **(1) `redflag_new_pos` tightened** to use the same constant the rest of the brief uses: `yp[\"days_of_inventory_projected\"].fillna(0) > _INCOMING_OVERSTOCK_DOI_DAYS` (700d). The `overstock_set` branch is gone — `overstock_flag` is set as low as ~180d projected DOI in the metrics service and was the source of all under-700 leaks. **(2) `pos_arriving_after_stockout` dropped from the actionable feed**: removed from `_concern_rows()` `_emit` calls, removed from `reorder_specs` in `_build_cost_center_breakdown()`, and the `BriefData` field is set to an empty DataFrame at the end of `gather_brief_data` alongside the other v4.8-dropped fields. The cross-portfolio `### POs ARRIVING AFTER STOCKOUT` section was removed from `build_brief_prompt()`. The metric is still computed (cheap, single pass through `sm`) but never surfaced — kept the compute path live so future "expedite watchlist" features can re-enable it without re-implementing. **(3) `_CONCERN_SEVERITY` collapsed 5 → 4 entries**: `po_late=90` removed; remaining are `redflag_new_po=96`, `incoming_overstock=92`, `needs_reorder=88`, `clearance=40`. **(4) Action-tag verbiage rewritten** in all four `_emit()` action_hint strings: every overstock-flavoured hint now starts with the literal phrase *"Overstock risk —"* so the AI naturally picks the new `[OVERSTOCK RISK]` tag when quoting / refining. **(5) `brief_renderer.py` action pills updated**: `_ACTION_TAG_RE` regex changed to `r\"\\[(OVERSTOCK RISK\\|NEW\\|REORDER\\|CLEARANCE\\|CANCEL\\|DEFER\\|HOLD)\\]\"` (note `OVERSTOCK RISK` matches the literal space — re.sub handles two-word tag); `_ACTION_TAG_CLASS` cleaned to four canonical entries (`OVERSTOCK RISK`/`REORDER`/`CLEARANCE`/`NEW`) plus three legacy aliases (`CANCEL`/`DEFER`/`HOLD` → `act-overstock`) so cached briefs from v4.8 still render with the same red pill. `EXPEDITE`, `WATCH`, `REVIEW` tags removed entirely. CSS: new `.action-pill.act-overstock` rule (red `#fef2f2` / `#b91c1c` / `#fecaca` — same palette as old `act-cancel` to preserve visual identity); `act-cancel` class kept as compatibility alias; `act-expedite` and `act-watch` rules deleted. **(6) `schema.py` system prompt rewritten for v4.9**: only THREE action tags now allowed (`[OVERSTOCK RISK]`, `[REORDER]`, `[CLEARANCE]`) plus `[NEW]` modifier. Explicit DO-NOT now includes *"Anything below the 700-day projected DOI threshold for OVERSTOCK RISK — if the data shows DOI < 700, do not call it overstock risk"* and *"NEVER recommend 'expediting' an existing PO — for this team, lead time is fixed; if a bridge buy is needed it surfaces as a [REORDER] with a tighter cover target."* All examples in the prompt updated from `[CANCEL]` to `[OVERSTOCK RISK]`. The Recommended Actions mix rule rephrased: *"at least 2 must be `[OVERSTOCK RISK]` and at least 2 must be `[REORDER]` — NEVER all of one type when both are available."* **(7) `build_brief_prompt()` copy refreshed**: top-concerns intro now says *"flag as OVERSTOCK RISK"* instead of *"cancel or defer the inbound"*, the cross-portfolio MASSIVE OVERSTOCK section instructs *"Every row needs an `[OVERSTOCK RISK]` bullet calling for the inbound PO to be defused"*, RED-FLAG NEW POs section heading updated to *"push DOI > 700d"* (was *"push DOI > 700d or land on overstock"*). **(8) Orphan-code audit pass**: `_concern_rows()` docstring still referenced `po_late`; `_build_top_concerns` docstring listed `po_late`; `_build_cost_center_breakdown` docstring mentioned `pos_arriving_after_stockout`; comment block above `_CONCERN_SEVERITY` mentioned 'expediting' as one of the categories. All four updated to match the v4.9 model. Smoke-tested against May 11 2026 dataset under `-W error::FutureWarning`: top_concerns now 123 rows (was 148 in v4.8) across exactly THREE concern types (`incoming_overstock`, `needs_reorder`, `redflag_new_po`); minimum DOI in incoming/redflag rows is **705.3** (was values as low as 172 in v4.8); `pos_arriving_after_stockout` confirmed not present in any CC's `tables` dict; prompt size 128 KB (was 144 KB v4.8 → 288 KB v4.7); pills render correctly: `OVERSTOCK RISK → act-overstock`, `REORDER → act-reorder`, `NEW → act-new`. Zero FutureWarnings. |
| **AI v4.8 — Two-bucket actionable model + valid-PO filter + NEW marker** | `app/ai/brief.py`, `app/ai/brief_renderer.py`, `app/ai/schema.py`, `app/data/store.py` | After v4.7 shipped (severity-floor filtering), the user reviewed the brief and asked for a much narrower, more actionable digest: *"don't want to see the clearance category anymore unless there is nothing else to show for a particular section, also when looking at pos only include pos with order numbers that have more than 2 characters... pos / on order quantities that would lead to us having more than 700 days worth of inventory, also would always like to see things that we should reorder or order more of, those are the only two types of things i want to see... please some mark the items that are new on the daily digest from day to day."* The v4.7 brief was still showing aging-only items, decelerating velocity, dead stock, and many other context-only categories with no buyer action available; placeholder POs with 1-2 char order numbers (e.g. `"X"`, `"-"`) were inflating `on_order_sy` and producing phantom incoming-overstock signals. | **(1) Valid-PO filter cascading throughout `brief.py`.** New constants `_MIN_ORDER_NUMBER_LEN = 3`, `_INCOMING_OVERSTOCK_DOI_DAYS = 700`. New helper `_filter_valid_pos(open_pos)` strips rows where `order_number.str.strip().str.len() < 3`. New helper `_recompute_on_order_from_valid_pos(sm, valid_open_pos)` is a pure function that returns a copy of `sm` with `on_order_sy` recomputed from the filtered POs (`groupby("base_sku")["quantity_sy"].sum()`) and `days_of_inventory_projected` recomputed as `(inv + on_order) / avg.where(avg > 0)` with `±inf → pd.NA` (replaces the deprecated `pd.option_context("mode.use_inf_as_na")`). Both helpers run inside `gather_brief_data` immediately after the eligibility filter, so EVERY downstream concern detection — yesterday_new_pos, excessive_incoming_pos, massive_overstock, redflag_new_pos, oversized_pos_vs_demand, runout_risk, needs_reorder_no_po, top_concerns, and per-CC overlay — sees the corrected `sm`. yesterday_new_pos additionally re-applies the length filter post-load to drop placeholder POs from the daily activity table. **(2) DOI thresholds raised** to match the user's 700-day mandate: `excessive_incoming_pos` now requires `days_of_inventory_projected >= 700` (was 365); `massive_overstock` now requires `(inventory_sy + on_order_sy) >= 3000 SY` AND `on_order_sy > 0` AND `days_of_inventory_projected >= 700` (was 540 with no inbound requirement). The "no current sales velocity" branch was dropped — items with no demand and no inbound aren't actionable today. The per-CC `massive_overstock` overlay applies the same gate. **(3) `_CONCERN_SEVERITY` collapsed from 13 entries to 5**: only `redflag_new_po=96`, `incoming_overstock=92`, `po_late=90`, `needs_reorder=88`, `clearance=40` remain. `_concern_rows()` correspondingly emits only five concern types: `pos_arriving_after_stockout` (po_late), `redflag_new_pos`, `needs_reorder_no_po`, `excessive_incoming_pos` AND `massive_overstock` BOTH as `incoming_overstock` (with `(impact/10000).clip(upper=10)` exposure boost on the latter), and `aging_inventory` as `clearance` fallback. **DROPPED concerns**: `active_stockouts` (folded into needs_reorder), `oversized_pos_vs_demand` (subsumed by incoming_overstock), `receipts_pushed_to_overstock` (no inbound action), `overstock_with_open_po` (subsumed by incoming_overstock), `dead_stock` (no inbound action), `decelerating_velocity` (context-only), `runout_risk` (folded into needs_reorder). The corresponding `BriefData` fields are **set to empty DataFrames** at the end of `gather_brief_data` so they are still populated by `_build_actionable_metrics` (avoiding ripple changes) but rendered as `(none)` in the prompt. **(4) `_build_top_concerns()`** now uses `severity_floor=80.0, safety_ceiling=150` — clearance at sev 40 is automatically excluded from the cross-portfolio top concerns; only the four actionable types pass the floor. Ceiling raised 120 → 150 to absorb the larger incoming-overstock pool now that DOI threshold is 700. **(5) `_build_cost_center_breakdown()` rewritten with two-bucket + clearance-fallback model.** Per CC: an internal `buckets: set` tracks which actionable buckets have rows. `incoming_specs = [excessive_incoming_pos, massive_overstock, redflag_new_pos]`; `reorder_specs = [needs_reorder_no_po, pos_arriving_after_stockout]`. Both run unconditionally per CC and are added to `tables` only if non-empty. The per-CC `massive_overstock` overlay now requires `on_order_sy > 0` AND `days_of_inventory_projected >= 700` (was 540 with no inbound requirement). After both bucket types finish, `aging_clearance` (top 25 aged-≥365d items in that CC) is attached **ONLY IF `not buckets`** — i.e., clearance is the LAST RESORT for CCs that have nothing actionable. CCs with no actionable rows AND no aged stock get no section at all. The `buckets` set is stripped from each slot before return so the prompt builder never sees internal bookkeeping. **(6) NEW marker via daily snapshots.** `app/data/store.py` gained `_BRIEF_SNAPSHOTS_FILE = APPDATA_DIR / "brief_snapshots.json"`, `_BRIEF_SNAPSHOT_RETENTION = 30`, and three helpers: `get_brief_snapshot(target_date) -> set[str]` returns that day's saved SKU set, `get_prev_brief_snapshot(target_date) -> set[str]` returns the most recent snapshot strictly older than `target_date` (so a same-day re-run doesn't see itself), `save_brief_snapshot(target_date, skus)` writes today's set with rolling 30-day retention. `brief.py` calls `_annotate_new_items(data, target_date)` and `_persist_snapshot(data, target_date)` at the end of `gather_brief_data`: the annotator adds an `is_new` boolean column to `top_concerns` and to every CC table by checking each row's SKU against the prior snapshot; the persister writes today's union-of-all-SKUs set. **First-ever brief special case**: when no prior snapshot exists, `is_new` is set to False on every row (otherwise EVERY item would be marked new — useless signal). Snapshot save is wrapped in try/except — best-effort, never breaks the brief. **(7) `brief_renderer.py` [NEW] action pill** added: `_ACTION_TAG_RE` regex extended to include `NEW`, `_ACTION_TAG_CLASS` maps `"NEW" → "act-new"`, inline `re.sub` regex updated, new CSS rule `.action-pill.act-new { background: #ecfdf5; color: #047857; border-color: #6ee7b7; box-shadow: 0 0 0 1px rgba(16,185,129,0.18); }` (premium green pill with subtle ring to draw the eye). **(8) `schema.py` system prompt rewritten** to enforce the v4.8 mandate: explicit "TWO BUYER-ACTIONABLE CATEGORIES" section, explicit DO-NOT list (aging without action, decelerating, receipts), explicit "NEW-ITEM MARKER" rules ("If `is_new=True`, render `[NEW]` immediately after the action tag... NEVER add `[NEW]` to a row whose `is_new` is False or missing"), Top Concerns section reworded to drop "v4.7 / row cap" history and emphasize the two-bucket model, Cost Center Breakdown updated with clearance-fallback handling ("If a CC's only table is `aging_clearance` ... emit a single short `[CLEARANCE]` bullet list and nothing more"), Recommended Actions rewritten to enforce a healthy CANCEL/DEFER vs REORDER/EXPEDITE mix ("at least 2 must be `[CANCEL]` / `[DEFER]` and at least 2 must be `[REORDER]` / `[EXPEDITE]` — NEVER all of one type"). Old prompt body (~120 lines) deleted. **(9) `build_brief_prompt()`** in `brief.py` rewritten to match: TOP CONCERNS section now describes the two-bucket model and `is_new` column; per-CC section instructs the AI to render `[NEW]` badge from data column; CROSS-PORTFOLIO SUPPORTING TABLES section replaces the old "DEEPER ACTIONABLE METRICS" wall — only POs ARRIVING AFTER STOCKOUT, RED-FLAG NEW POs, NEEDS REORDER, INCOMING-OVERSTOCK POs, MASSIVE OVERSTOCK WITH VALID INBOUND PO, AGING INVENTORY (clearance fallback). All references to dropped categories (decelerating, dead stock, receipts, oversized, active stockouts, runout, overstock-with-open-po) removed from the prompt. **(10) Orphan/quality cleanup**: deprecated `pd.option_context("mode.use_inf_as_na", False)` replaced with explicit `replace([float("inf"), float("-inf")], pd.NA)` (FutureWarning-clean under pandas 2.x). Smoke-tested: all four modules import cleanly; `recommended_settings` chain still works; renderer regex still parses; schema prompt under 6 KB. |
| **AI v4.7 — Severity-floor filtering replaces row caps (HAL/ROSALIE class always surfaced)** | `app/ai/brief.py`, `app/ai/schema.py` | After v4.6 shipped (diversity quota + dynamic severity boost), the user reported HALNOVEBRADBURY *and* SKYHUDSROSALIE STILL not appearing in the rendered brief — even though both were verified verbatim in the 130 KB user prompt at sensible per-CC table positions. Diagnostic: the v4.6 diversity quota (max 6 per concern type) and the row caps on every concern table (`top_concerns.head(25)`, per-CC `sub.head(15)`, `massive_overstock.head(40)`, `overstock_with_open_po.head(30)`, etc.) were silently truncating real cash positions just below the cap line. The user's prescription: *"rather than limiting it by the number of records you do it by the severity, show everything of a certain severity."* | **Architectural pivot: row caps → severity-floor filtering throughout `brief.py`.** **(1) `_build_top_concerns()` rewritten** — the `top_n=25` row cap and `_MAX_PER_CONCERN=6` diversity quota are gone. New signature: `severity_floor=80.0, safety_ceiling=120`. Emits EVERY SKU/concern row with effective severity ≥ 80 (covers po_late=100, redflag_new_po=95, massive_overstock=92+exposure_boost, stockout=90, needs_reorder=88, receipt_to_overstock=85, oversized_po=82, incoming_overstock=80). Hard ceiling 120 is a prompt-size safety net, not a quota. Within the floor: sort by severity desc → impact desc → inventory desc, dedup-by-SKU keeps highest severity. **(2) `data.massive_overstock`** cap raised `head(40) → head(150)`; per-CC overlay raised `head(15) → head(40)`. **(3) Per-CC concern tables** in `_build_cost_center_breakdown()` raised `sub.head(15) → sub.head(40)` so every CC carries its full local picture. **(4) Cross-portfolio table caps** raised: `active_stockouts` 30 → 80 (with new `>=1 SY/day` floor to drop noise), `runout_risk` 30 → 80, `overstock_with_open_po` 30 → 100 (with new `exposure >= 1000 SY` floor), `aging_inventory` 30 → 80 (with `>=250 SY` floor), `excessive_incoming_pos` 30 → 100 (with `exposure >= 1000 SY` floor). **(5) Per-CC table renderer** bumped from `max_rows=8 → max_rows=40` so the AI sees every row, and the default `_df_to_compact_table` cap raised 30 → 100. **(6) `schema.py` system prompt** rewritten on three fronts: (a) **Top Concerns** section now mandates *"Write ONE bullet per row in TOP CONCERNS, in the same order as the data. DO NOT summarize, deduplicate, or skip rows."* (b) **MANDATORY MASSIVE-OVERSTOCK COVERAGE** elevated to *"the AI MUST render EVERY ROW of every CC's `massive_overstock` table as its own bullet. No summarising, no 'and several others', no skipping."* (c) **Cost Center Breakdown** removed the "5-12 bullets" cap entirely — *"there is NO upper bound — if a CC has 25 actionable rows, write 25 bullets."* (d) **Recommended Actions** numbered list format clarified to forbid embedded newlines (was rendering each item as two broken bullets). **(7) Orphan cleanup**: removed unused `import json` from `brief.py`. Smoke-tested against May 11 2026 dataset: top_concerns has 120 rows (103 massive_overstock + 14 po_late + 3 redflag), HALNOVEBRADBURY ranks #113 in top_concerns at sev 95.1 / impact 31,138 SY, SKYHUDSROSALIE ranks #91 at sev 95.6 / impact 36,358 SY, all 6 HALNOVE rows present in massive_overstock (HEMMIWAY 65,615 SY, TWAIN 48,289, BRADBURY 31,138, HAWTHRNE 30,414, ELIOT 29,991, EMERSON 27,498), prompt is 288 KB (~72 K tokens — well under gpt-5's 200 K context), zero FutureWarnings under `-W error::FutureWarning`. |
| **AI v4.6 — Diversity quota + dynamic severity boost (fixes silent CC-section starvation)** | `app/ai/brief.py`, `app/ai/schema.py` | After v4.5 shipped, user reported HALNOVEBRADBURY *and* SKYHUDSROSALIE still missing from the daily brief, and CC sections looked thin (~3-4 bullets each). Diagnostic: both SKUs were in CC 040's `massive_overstock` table at rows #2 and #5, but nowhere in global `top_concerns`. Root cause: 15 small `po_late` items (severity 100) + 3 `redflag_new_po` items (severity 95) consumed all 18 slots of the leader board — no `massive_overstock` items survived even though some carried 50,000+ SY exposure. The system prompt said "2-6 bullets per CC" which the AI took literally and wrote 3-4 per CC, skipping rows 4-15 of every per-CC table. | **(1) Diversity quota in `_build_top_concerns()`**: at most 6 items per concern type (`_MAX_PER_CONCERN = 6`, applied via `groupby("concern").cumcount() < 6`) so one noisy bucket of small items can't crowd out high-impact items of other types. **(2) Dynamic severity boost on `massive_overstock`** in `_concern_rows()`: severity = base 92 + `(exposure_sy / 10000).clip(0, 15)` so a 100,000-SY position gets severity 107 (above po_late=100), a 50,000-SY position gets 97 (between stockout=90 and po_late=100), and small items still rank below stockouts. Severity column cast to float64 first to silence pandas dtype FutureWarning. **(3) Lowered `massive_overstock` thresholds** so HAL is always included: `exposure >= 3000 SY` (down from 5000) AND `(DOI_proj >= 540d (down from 730) OR (avg_daily=0 AND on_order > 500 SY (down from 1000))`. Global cap raised 20 → 40 to feed mid-tier CCs. **(4) `top_concerns` cap raised 18 → 25**; prompt rendering cap matched. **(5) System prompt strengthened** in `schema.py`: per-CC bullet count guidance changed from "2-6" → **"5-12 specific actionable bullets... aim for THOROUGH coverage... if a CC's tables hold 8+ distinct actionable items, write 8+ bullets. Never artificially cap a CC at 3-4 bullets when the data shows more."** Mandatory massive-overstock coverage extended from "top 3 of global" → **"top 5 of global AND top 5 of EVERY CC's massive_overstock table"** with explicit `[CANCEL]/[DEFER]/[CLEARANCE]` action tag requirement. Group-bullets-into-subsections threshold raised 4+ → 6+ to suit the larger expected counts. Smoke-tested: top_concerns now leads with 4 massive_overstock items (TARINST12COG221 sev 107, LGHFF1441 sev 101.3, NEX537048A sev 101.1, HALSHORCAMARLLA sev 100.5), HAL lands at row #4 of CC 040 massive_overstock, SKYHUDSROSALIE at row #2; both appear verbatim in the 130 KB prompt. Zero FutureWarnings under `-W error::FutureWarning`. |
| **AI v4.5 — Massive-overstock concern + per-CC overlay (fixes missed-overstock regression)** | `app/ai/brief.py`, `app/ai/schema.py` | User reported HALNOVEBRADBURY (CC 040: 21,265 SY on hand + 9,872 SY on order, DOI(proj) 1,535) used to appear in earlier briefs but stopped surfacing after v4.2-v4.4. Diagnostic confirmed it WAS in `overstock_with_open_po`, `excessive_incoming_pos`, `decelerating_velocity`, and `receipts_pushed_to_overstock` data tables — but was losing the `top_concerns` ranking battle (capped at 12) to 12+ higher-severity items (po_late=100, redflag_new_po=95, stockout=90), and the AI wasn't elevating it from CC 040's per-CC tables either. Root cause was structural: the portfolio has 765 big-exposure SKUs (≥5,000 SY combined inventory+on_order); HAL ranks #16 in CC 040 alone. No single concern table sorted by exposure surfaced it. Sort key on `overstock_with_open_po` was `days_of_inventory_projected DESC` which favored small high-DOI items over large mid-DOI ones. | **(1) New `massive_overstock` concern** in `BriefData` (severity 92 — between `stockout=90` and `redflag_new_po=95`): items where `(inventory_sy + on_order_sy) >= 5000 SY` AND `(DOI_proj >= 730 days OR (avg_daily=0 AND on_order > 1000 SY))`. Sorted by `exposure_sy` desc. Top 20 portfolio-wide. New `BriefData.massive_overstock: pd.DataFrame` field; new `exposure_sy` column added to expose total cash on the floor. Wired into `_concern_rows()` with action hint *"Freeze inbound + open clearance plan — massive cash on the floor with multi-year cover."* and `impact_col="exposure_sy"`. Surfaced as its own `## MASSIVE OVERSTOCK` section in the user prompt. **(2) Per-CC massive_overstock overlay** in `_build_cost_center_breakdown()`: the global top-20 starves mid-tier CCs of their own biggest items (none of CC 040's items make the global top — 042/043/050/051/033 dominate). New code recomputes massive_overstock per-CC from `sm` directly with slightly relaxed thresholds (`exposure >= 3000 SY` AND `(DOI_proj >= 540 days OR (avg_daily=0 AND on_order > 500 SY))`), takes top-15 per CC, and overlays onto each CC's section if richer than the global slice. **HALNOVEBRADBURY now lands at #5 in CC 040's `massive_overstock` table** inside the prompt. **(3) `top_concerns` cap raised 12 → 18** (`_build_top_concerns(top_n=18)`) to give massive_overstock items a real shot at the cross-portfolio leader board. **(4) Per-CC table head cap raised 10 → 15** so big items don't get cut from per-CC sections. **(5) `overstock_with_open_po` and `excessive_incoming_pos` re-sorted** by `(inventory_sy + on_order_sy) DESC` first (cash exposure), then `days_of_inventory_projected DESC` — so the largest cash positions never lose head(30) to small high-DOI noise. **(6) `oversized_pos_vs_demand` FutureWarning fix**: `op["po_months_of_demand"].fillna(9999).round(1)` was triggering pandas downcasting warning on object dtype; replaced with `pd.to_numeric(..., errors="coerce").fillna(9999.0).round(1)`. **(7) System prompt** in `schema.py` got a **MANDATORY MASSIVE-OVERSTOCK COVERAGE** rule: every row in the data's MASSIVE OVERSTOCK table represents a very large cash position; AT LEAST ONE Top Concerns bullet MUST address each of the top 3 rows when present, even if other concerns rank above them numerically. Smoke-tested: HAL surfaces in CC 040 massive_overstock at row #5; full prompt builds at 126 KB (~32 K tokens — still well within budget); zero FutureWarnings under `-W error::FutureWarning` |
| **AI v4.4 — User-tunable reasoning controls (Advanced dialog)** | `app/ai/providers.py`, `app/data/store.py`, `app/ai/brief.py`, `app/ui/tab_brief.py` | v4.2.2 hard-pinned `reasoning_effort=minimal`, `max_completion_tokens=16000`, and `timeout=900s` for every gpt-5 / o-series call. That fixed the empty-reply / read-timeout failure mode for default daily use, but it also took away every escape valve for power users: anyone wanting to try `gpt-5` at `reasoning_effort=high` for a higher-quality brief, or `o3-pro` (which thinks for 20+ minutes), or simply a beefier `max_tokens` ceiling on a stubborn brief, had no way to reach those settings without editing source. | **(1) `providers.recommended_settings(provider, model)`** new function returns model-aware defaults `{max_tokens, reasoning_effort, timeout_sec, is_reasoning, supports_temp, effort_levels}`: `gpt-5` → minimal/16k/900s, `gpt-5-mini` → minimal/12k/600s, `gpt-5-nano` → minimal/8k/300s, `o1/o3/o4` → low/24k/1800s (mini variants 12k/900s; pro variants medium/32k/3600s), standard chat (gpt-4o/4.1) → 4k/180s with `supports_temp=True`, claude-opus → 4k/600s, claude-sonnet → 4k/300s, claude-haiku → 2k/180s, gemini-pro → 4k/600s, gemini-flash → 2k/180s. **(2) All three `call_*` functions** + `call_provider` now accept `options: dict | None`; `_merge_overrides()` lets any of `max_tokens` / `reasoning_effort` / `timeout_sec` override the recommended defaults (None falls back). The hardcoded `is_reasoning = m.startswith("gpt-5") or ...` in `call_openai` is replaced by `cfg["is_reasoning"]` from the recommendation table, and reasoning effort is only set on the request body if non-None. **(3) `store.get_model_overrides(provider, model)` / `set_model_overrides(provider, model, overrides|None)`** persist per-model overrides to `%APPDATA%/PurchaseOrderBot/ai_model_overrides.json` keyed as `"openai::gpt-5"`. Empty/None clears the entry. **(4) `brief.generate_brief()`** gained an `options` kwarg threaded down to `call_provider`. **(5) New `_AdvancedAIDialog`** opened from a ⚙ button next to the model picker in the gradient bar: shows provider/model header, "Reasoning model" vs "Standard chat model" subtitle, three rows (Max output tokens spinbox 500–128 000, Reasoning effort combo populated from `effort_levels` and disabled for non-reasoning models, HTTP read timeout spinbox 30–7200 s) with "Recommended: <b>X</b>" hints under each, "Reset to recommended" button, "Save as default for this model" checkbox, and Apply/Cancel. Unchecked = one-shot override for the next brief only; checked = persisted via `set_model_overrides`. The empty-reply error message now points users at the ⚙ Advanced button. Smoke-tested: `recommended_settings('openai','gpt-5')` returns minimal/16k/900s; `set_model_overrides`/`get_model_overrides` round-trip; `_AdvancedAIDialog` + `BriefTab` import cleanly. |
| **AI v4.3 — Oversized-PO metric, real-list rendering, action-tag pills** | `app/ai/brief.py`, `app/ai/brief_renderer.py`, `app/ai/schema.py` | (1) SKUs like SKYHUDSROSALIE were missed: open PO grossly oversized vs trailing demand, but `avg_daily_sales_sy` was so low that `excessive_incoming_pos` (which requires `avg_daily > 0` AND `DOI_proj > 365`) skipped them. (2) The `Recommended Actions` section rendered as one wall-of-text paragraph instead of a numbered list — the model emitted `1) ... 2) ... 3) ...` inline and the renderer's OL regex only accepted `1.` (period). (3) Bullets all looked identical; no visual cue to distinguish "cancel this PO" from "expedite this PO" from "place a new PO". | **(1) New metric `oversized_pos_vs_demand`** in `_build_actionable_metrics()`: filters `sm[on_order_sy > 0]`, joins to trailing 90-day order sums (the same series already used for decelerating-velocity — zero extra cost), computes `po_months_of_demand = on_order_sy / (sales_90d / 30)`, and flags rows where `po_months_of_demand >= 18` OR `sales_90d <= 0`. Sort: zero-demand POs first, then by largest `on_order_sy`. Top 20. Severity 82 (between receipt-to-overstock 85 and incoming-overstock 80). Wired into `_concern_rows()` (action hint *"Cancel/defer this PO — quantity dwarfs trailing 90-day demand."*), `_build_cost_center_breakdown()` table_specs, and `build_brief_prompt()` as `### OVERSIZED POs vs trailing 90-day demand`. New `BriefData.oversized_pos_vs_demand` field. **(2) Renderer hardened** in `brief_renderer.py`: `_OL_RE` now matches both `1.` and `1)` markers; new `_INLINE_OL_RE` detects 2+ inline `\d+[.)]\s+` markers in the same paragraph or first-line list item and splits them into proper `<li>` siblings (rescues the wall-of-text failure mode). **(3) Action-tag pills**: `_inline()` now post-processes the literal substrings `[CANCEL]`, `[DEFER]`, `[HOLD]`, `[EXPEDITE]`, `[REORDER]`, `[CLEARANCE]`, `[WATCH]`, `[REVIEW]` into colored `<span class="action-pill act-*">` badges (red/orange/blue/purple/grey). System prompt rewritten with explicit FORMATTING RULES requiring (a) one list item per line, never inline `1) ... 2) ...`, (b) every actionable bullet to begin with one of the action tags, with the example: `- [CANCEL] **[CC 020]** SKYHUDSROSALIE — PO #84231 for 2,400 SY but only 18 SY sold in last 90 days`. Smoke-tested: feeding `"1) [CANCEL] foo 2) [EXPEDITE] bar 3) [REORDER] baz"` to `_markdown_to_html()` now yields a real `<ol>` with three `<li>`s and three colored pills. |
| **AI v4.2.2 — gpt-5 reasoning effort tuned to "minimal" (fixes empty replies + read timeouts)** | `app/ai/providers.py` | After v4.2.1 fixed the temperature crash, two new gpt-5 failure modes appeared: (a) brief completed in ~355s but the visible reply was 1 token (rendered as `_(no content generated)_`) — gpt-5 burned its entire 8 K then 32 K `max_completion_tokens` budget on hidden reasoning with nothing left for output; (b) follow-up runs hit `TimeoutError: The read operation timed out` after the 600 s read cap because gpt-5 with default reasoning effort can take 5–10 min on a 26 K-token brief prompt. Both stem from the same root cause — gpt-5 defaults to deep, slow reasoning even for tasks (executive synthesis from a structured prompt) that don't need it. | **Pin reasoning effort to its lowest setting** in `call_openai` for reasoning models: `gpt-5*` → `reasoning_effort: "minimal"` (gpt-5's new fast-chat mode), `o1/o3/o4*` → `"low"` (those families don't support "minimal"). Also reduced `max_completion_tokens` to 16 000 (more than enough for a 1.5 K-token brief once reasoning is bounded), and bumped the safety read timeout to 900 s. Net effect: gpt-5 now responds in seconds-to-tens-of-seconds and produces the full visible brief. The empty-reply guard added in v4.2.1.1 still surfaces a clear `AIError` if a future model ever runs the budget dry. |
| **AI v4.2.1 — gpt-5 temperature fix + Needs-Reorder metric + Top Concerns/CC clarity** | `app/ai/providers.py`, `app/ai/brief.py`, `app/ai/schema.py` | (1) Generating a brief with `gpt-5` returned `HTTP 400 Unsupported value: 'temperature' does not support 0 with this model. Only the default (1) value is supported.` — gpt-5 / o-series reasoning models reject custom temperature. (2) v4.2 brief talked extensively about cancelling/expediting POs but never surfaced SKUs that have **active demand and zero open PO** (the buys the team should be placing today). (3) Top Concerns listed items from cost centers that didn't get their own ## CC section, leaving the reader unsure why some CCs were broken out and others weren't. | **(1) `providers.py` `call_openai`**: only sets `temperature: 0` when the model is NOT `gpt-5*` / `o1*` / `o3*` / `o4*`. Reasoning models now hit the API with no temperature override and use the default (1). **(2) New actionable metric `needs_reorder_no_po`** in `brief.py`: filters `sku_metrics` for `(on_order_sy <= 0) AND (po_pending_qty <= 0) AND (avg_daily_sales_sy > 0) AND (inventory_sy < 1.5 × avg_daily × lead_time)`; computes `suggested_order_sy = max(avg_daily × 60 - inventory_sy, 0)` rounded to whole SY; drops suggestions < 25 SY as noise; ranks by `suggested_order_sy` desc and keeps top 20. Wired into `_concern_rows()` with severity 88 (just above receipt-to-overstock at 85, just below stockout at 90) and action hint *"Place a new PO — active demand, no open PO, cover < 1.5x lead time"*; included in `_build_cost_center_breakdown()` table_specs so it shows under each CC; rendered as its own `### NEEDS REORDER — no PO on the books, cover < 1.5x lead time` section in the user prompt. **(3) System prompt clarified**: Top Concerns now bears the disclaimer *"(portfolio-wide — items also appear under their cost center section)"*; every bullet must lead with `**[CC xxx]**` so the reader sees the owning CC at a glance; Cost Center Breakdown bears *"(every cost center referenced in Top Concerns appears here with its full local context)"* and the **MANDATORY** rule: every CC named in any Top Concern bullet MUST have its own section below — never leave a CC mentioned at the top with no section. CCs with no Top Concern bullets AND no actionable items are still omitted entirely. Added explicit guidance to surface **NEEDS REORDER** items in the per-CC sections. Smoke-tested: `inspect.getsource(call_openai)` now contains the `gpt-5` gating; `from app.ai.brief import gather_brief_data, build_brief_prompt` → OK |
| **AI v4.2 — per-cost-center sections + trim/age filters + top-concerns-first + deeper actionable metrics** | `app/data/queries.py`, `app/services/metrics_service.py`, `app/ai/brief.py`, `app/ai/schema.py` | (1) v4.1 brief mixed all cost centers together — buyers wanted per-CC sections so each merchandise group gets its own actionable bullets, but only when that CC actually has concerns (skip the empty ones). (2) Trim items (border / accessory SKUs identified by `ITEM.ICLAST` length > 1) were polluting the brief with low-importance noise. (3) New SKUs < 6 months from launch_date have unstable velocity and were generating false-positive overstock signals. (4) AI was leading with high-level aggregate counts ("you have 600 stockouts") instead of specific actionable items. | **(1) `queries.py` + `metrics_service.py`**: added `LTRIM(RTRIM(COALESCE(i.ICLAST, '')))) AS iclast` to `ITEMS_SQL`; propagated `iclast` through the `item_base` column selection in `compute_metrics()`; defensive guard injects empty-string column on cached items frames missing the field (pre-cache-bust grace). **(2) `brief.py` `_apply_eligibility_filter()`**: drops trim items (`iclast.str.len() > 1`) AND SKUs younger than 180 days (using `launch_date`); reports drop counts in `data.filter_summary` so the brief can disclose its scope. Filter applied ONCE upstream of every problem table — yesterday SQL pulls and `bundle.orders` slice are also filtered to the eligible SKU set. **(3) Five new actionable metric tables** added via `_build_actionable_metrics()`: `pos_arriving_after_stockout` (lead_time > days_until_stockout — direct lost sales), `redflag_new_pos` (POs entered yesterday on already-overstock SKUs or pushing DOI > 365), `decelerating_velocity` (30d sales ≤ 30% of 90d/3 baseline — early aging warning), `dead_stock` (aged ≥ 365d AND no sale in 90d), `receipts_pushed_to_overstock` (POs that arrived yesterday on already-overstock SKUs). Each is top-15 only — small enough for the AI to reason per-SKU. **(4) `_build_top_concerns()`** flattens every problem table into one (concern, severity, sku, …, action_hint) frame, severity-tiered (`po_late=100 > redflag_new_po=95 > stockout=90 > receipt_to_overstock=85 > incoming_overstock=80 > overstock_with_po=75 > dead_stock=70 > decelerating=60 > aging=55 > runout=50`), deduped per SKU keeping its highest-severity concern, top-12 across portfolio. Each row carries a verbatim action hint the AI can quote or refine ("Cancel PO 84231 from supplier 0042"). **(5) `_build_cost_center_breakdown()`** groups every problem table by `cost_center` into `data.cost_center_problems = {cc_code: {name, kpis, tables: {...}}}`; CCs with no concerns are NOT added to the dict — the AI only sees CCs worth writing about. Per-CC KPIs (skus / inventory_sy / on_order_sy / stockouts / overstock / aging) included for context. **(6) `build_brief_prompt()` restructured**: leads with ELIGIBILITY FILTER disclosure → PORTFOLIO SNAPSHOT → **TOP CONCERNS table** (the lead) → yesterday activity → PER-COST-CENTER BREAKDOWN (one block per CC with its scoped tables) → cross-portfolio fallback tables. **(7) `schema.py` system prompt rewritten** for the new structure: explicit output spec is `# Executive Summary` (2-3 sentences) → `## Top Concerns` (5-10 SKU-specific bullets, each answers WHICH SKU + WHAT'S WRONG + WHAT TO DO + WHY) → `## Yesterday's Notable Changes` (omit categories with nothing meaningful) → `## Cost Center Breakdown` (one `## CC <code>` per non-empty CC, OMIT entirely if no bullets worth writing) → `## Recommended Actions (top 5)` numbered. Hard rules: "DO NOT write aggregate counts like 'we have 600 stockouts' — name the specific SKUs and specific actions"; "If a section has nothing notable, OMIT IT (do not write filler)"; "Mention scope only if relevant to a specific recommendation". Smoke-tested: `from app.ai.brief import gather_brief_data, build_brief_prompt, _apply_eligibility_filter; from app.ai.schema import build_brief_system_prompt` → OK |
| **AI v4.1 — bugfix + layout polish + AI Memory Notes removed + GPT-5 cost analysis** | `app/ai/brief_renderer.py`, `app/ui/tab_brief.py` (rewritten), `app/ai/brief.py`, `app/ai/schema.py`, `app/data/store.py` | (1) v4.0 crashed with `ValueError: Invalid format string` on first **Generate Brief** click — `_banner()` used POSIX `%-d` which Windows doesn't support, AND the broken `if hasattr(...)` line ran *before* the try/except fallback. (2) Sidebar layout looked weak: AI Memory Notes panel ate ~360 px of horizontal space for a feature the user no longer wanted, and Export buttons hidden in the same sidebar were easy to miss. (3) User asked: how much better are premium models like **GPT-5** for daily briefs and at what cost? | **(1) Fixed `_banner()`** — replaced the broken POSIX-format line with `target_date.strftime("%A, %B %d, %Y")` followed by a `re.sub(r"\b0(\d)", r"\1", ...)` that strips the leading zero. Now Windows-compatible. **(2) Layout rebuilt** — removed the right-hand sidebar entirely; brief viewer is now full-width. Export buttons (📄 PDF · 📧 HTML · 📋 Copy) moved to a polished outline-button row directly under the gradient bar (right-aligned, with the status line left-aligned). Top-bar combos restyled with translucent white-on-gradient styling so the controls match the gradient banner. New gradient-text "Ready when you are" placeholder card. **(3) AI Memory Notes feature deleted entirely** — `tab_brief.py` notes panel + `_refresh_notes / _on_add_note / _on_edit_note / _on_delete_note` slots gone; `brief.py` no longer imports or injects notes; `schema.py` `build_brief_system_prompt()` simplified to `(target_date)` only — no `notes` arg; `app/data/store.py` `_AI_NOTES_FILE`, `get_ai_notes`, `_save_ai_notes`, `add_ai_note`, `update_ai_note`, `delete_ai_note`, `clear_ai_notes`, `import uuid`, `from datetime import datetime` all removed (~60 lines). The on-disk `ai_notes.json` is left in place as harmless legacy. **(4) GPT-5 added** to `_PROVIDER_MODELS` (openai now lists `gpt-5`, `gpt-5-mini` first) and to `_PRICING` in `brief.py` — `gpt-5` ($1.25 / $10 per 1M), `gpt-5-mini` ($0.25 / $2), `gpt-5-nano` ($0.05 / $0.40). Per-brief cost (~7 K in / ~2.5 K out) for an executive daily brief: **gpt-5 ≈ $0.034**, **gpt-5-mini ≈ $0.007**, **claude-sonnet-4-5 ≈ $0.058**, **gpt-4o ≈ $0.043**, **gemini-2.5-pro ≈ $0.034**. Quality ranking for *"identify the 2-3 most important things in 50+ rows of inventory data and explain why"*: **gpt-5 ≈ claude-sonnet-4-5 > gemini-2.5-pro > gpt-4o > gpt-5-mini ≈ gpt-4o-mini**. GPT-5 is the new recommended default — better at multi-priority synthesis than gpt-4o for ~½ the cost. Smoke-tested: `from app.ai.brief import BriefResult; from app.ai.brief_renderer import _banner; _banner(BriefResult(target_date=date(2026,5,11), ...))` produces `"📋 Daily Brief — Monday, May 11, 2026"` correctly on Windows. `hasattr(store, 'get_ai_notes')` → `False` (notes orphan-free) |
| **AI v4.0 — pivot from Q&A chat to Daily Brief generator** | `app/ai/brief.py` (new), `app/ai/brief_renderer.py` (new), `app/ai/schema.py` (rewritten to ~3 KB brief-only prompt), `app/ui/tab_brief.py` (new, replaces tab_ai.py), `app/ui/main_window.py`, `app/data/store.py` (saved-query helpers removed), DELETED: `app/ui/tab_ai.py`, `app/ai/starter_queries.py`, `app/ui/tab_fillrate.py` | The v3.x Q&A chat / starter library was clever but seldom used — buyers wanted a single-button **executive briefing** focused on the two business priorities: (1) avoid 12-month inventory and (2) avoid stockouts when customers call. Daily, not on-demand. Polished, exportable, email-ready. | Complete architectural pivot. **(1) `brief.py`** orchestrates the brief: `gather_brief_data(target_date, bundle)` pulls **yesterday's new POs from `_ORDERS` (ACCOUNT#I=1 + ORDER_ENTRY_DATE_YYYYMMDD=ymd)** and **yesterday's receipts from `OPENIV` (NREFTY='R' + NDATE=ymd)** + slices `bundle.orders` for yesterday's sales / backorders + filters `sku_metrics` for `stockout_flag / runout_risk / overstock_flag` cohorts and `inventory_age_days >= 365` aging. `build_brief_prompt(data)` formats every cohort as compact pipe-delimited tables (~6-8 K user-message tokens). `generate_brief(target_date, bundle, provider, api_key, model)` is the public sync entry — calls `providers.call_provider()`, returns a `BriefResult(target_date, markdown, provider, model, tokens_in, tokens_out, cost_usd, elapsed_sec, generated_at, error)`. **(2) `brief_renderer.py`** converts the AI's Markdown into premium HTML with three modes (`app` / `pdf` / `email`): full Markdown subset (headings, ul/ol, bold/italic, code, pipe-tables, paragraphs); **severity-aware section headers** auto-classified by keyword → CSS class (`sev-danger` red, `sev-warning` amber, `sev-info` blue, `sev-action` violet, `sev-summary` green, `sev-neutral` grey) each with border / background / foreground triplet; **gradient banner header** (indigo → violet → pink) with date + provider/model badge; **8-card KPI strip** at top showing portfolio health (Stockouts / Runout Risk / Overstock / Aging Stock / Inventory $ / On Order $ / Backorders / Coverage %); **footer** with model + token + cost line. PDF mode adds `@page { size: Letter; margin: 0.6in; }`; email mode inlines styles for Outlook compatibility. **(3) `schema.py`** stripped from ~27 KB Q&A monster prompt to a **~3 KB brief-only system prompt**: defines the two business priorities, prescribes the exact section order (Executive Summary → Yesterday's Changes → Stockout Watch → Overstock & Aging Watch → Recommended Actions), tone (direct/buyer-grade), and "never invent SKUs/POs/quantities" guard. New `build_brief_system_prompt(notes, target_date)` injects user memory notes verbatim — same "teach the AI once" mechanism preserved from v3.x. Old `SCHEMA_PROMPT` / `build_system_prompt` / `_build_date_context` / 12-query starter library all deleted. **(4) `tab_brief.py`** replaces tab_ai.py: gradient top bar with date picker (defaults yesterday), provider/model dropdowns (auto-saved on change), `✨ Generate Brief` button → `_BriefWorker(QThread)` (with the same RuntimeError-guarded re-entrancy + `_on_thread_finished` + `closeEvent` drain pattern as `main_window.py` and `tab_daily_pos.py`); `QTextBrowser` viewer renders the HTML; sidebar has **📄 Export PDF** (uses stdlib `QPrinter(HighResolution)` + `QTextDocument.print(printer)` with `QPageSize.Letter` — zero extra deps, PyInstaller-friendly), **📧 Export HTML for Email** (writes email-mode HTML to ~/Documents), **📋 Copy HTML** (clipboard for direct paste into Outlook), and the **AI Memory Notes panel** (preserved from v3.x — `+ Add` / Edit / Delete with `QInputDialog.getMultiLineText`). **(5) `main_window.py`** swapped `from app.ui.tab_ai import AITab` → `from app.ui.tab_brief import BriefTab`; tab labelled "📋 Daily Brief" between Daily POs and Settings; `_on_data_ready` now calls `self._brief_tab.refresh(bundle)`. **(6) `store.py`** stripped orphaned `_SAVED_QUERIES_FILE / get_saved_queries / _save_queries / add_saved_query / update_saved_query / delete_saved_query`; `get_ai_config / set_ai_config / get_ai_notes / add_ai_note / update_ai_note / delete_ai_note / clear_ai_notes` preserved. **(7) Token cost estimate per brief** (~6-8 K input + ~1.5-2.5 K output): **gpt-4o ~$0.07** ($2.50/$10 per 1M), **gpt-4o-mini ~$0.005** ($0.15/$0.60), **claude-sonnet-4-5 ~$0.09** ($3/$15), **gemini-2.5-flash ~$0.012** ($0.30/$2.50). Recommended: gpt-4o or claude-sonnet-4-5 for daily executive use; gpt-4o-mini if running multiple times per day. **(8) Orphan cleanup**: `tab_ai.py` (1383 lines), `starter_queries.py` (520 lines), `tab_fillrate.py` (already orphaned since v3.x — referenced in CLAUDE.md but not by any live import) all deleted via `Remove-Item`. Smoke-tested: `from app.ai.brief import generate_brief, BriefResult, gather_brief_data, build_brief_prompt; from app.ai.brief_renderer import render_to_html; from app.ui.tab_brief import BriefTab; from app.ui.main_window import MainWindow` → OK |
| Checkbox filter sidebar | `widgets.py` | QListWidget multi-select was hard to use; deselect required Ctrl+click | Rewrote `FilterSidebar` with `_CheckList` (scrollable checkboxes) for CC, Supplier, Price Class, Product Line; horizontal checkboxes for A/B/C/D rating; QTimer 250ms debounce; "Clear" links per group; width 215px |
| Overview: Price Class view | `tab_overview.py` | User wanted price-class aggregation as default; per-SKU view still accessible | Default view now "By Price Class" with `QStackedWidget` switching between PC table and SKU table; toggle buttons in toolbar |
| Overview: Drill-down dialog | `tab_overview.py` | User wanted to see SKUs within a price class | `PriceClassDetailDialog` — double-click any PC row opens modal with KPI summary cards, full SKU-level table, totals strip |
| Filter cross-selection fix | `queries.py` | FILTER_VALUES_SQL included CC "1xx" items whose suppliers appeared in sidebar but had no matching SKUs in sku_metrics | Added `AND LTRIM(RTRIM(i.ICCTR)) NOT LIKE '1%'` to FILTER_VALUES_SQL WHERE clause |
| Removed "Pending PO" column | `tab_overview.py` | `on_order_sy` (from `_ORDERS`) and `po_pending_qty` (from `OPENPO_D`) showed identical values in practice — confusing duplicate | Removed "Pending PO" column from PC table, SKU table, and detail dialog; `po_pending_qty` still used in `net_inventory_sy` calculation |
| Duplicate base-SKU rows fix | `metrics_service.py` | SQL Server CHAR columns may return padded strings; two items with same IIXREF but different item numbers both showed as the same base_sku | Added `items["base_sku"] = items["base_sku"].str.strip()` before `drop_duplicates("base_sku")` in `_build_sku_metrics()` |
| ALX003 / price-class filter returns empty | `metrics_service.py` | `drop_duplicates("base_sku")` kept whichever row SQL Server returned first — if an alias item (from a different price_class) appeared before the direct item, the base SKU inherited the alias's price_class, so filtering sku_metrics by the real price_class found nothing | Sort items by `(sku == base_sku)` descending before `drop_duplicates` so the direct/base item's attributes (price_class, cost_center, etc.) always take priority over any alias pointing to that base SKU |
| Color rules applied to both tables | `tab_overview.py` | Rules were only set on the SKU table; PC table (default view) never showed colors | `_apply_saved_rules` and `_open_rules_dialog` now call `set_rules()` on both `self._table` and `self._pc_table`; dialog offers combined column list from both views |
| Column manager view-mode aware | `tab_overview.py` | "Columns" button always opened dialog for SKU table regardless of active view; PC table columns could never be managed | `_open_column_manager` now checks `_view_mode`: opens dialog for `_pc_table` (key `"overview_pc"`) when in price-class view, or `_table` (key `"overview"`) when in SKU view; `_apply_saved_column_prefs` restores both independently |
| Columns + Color Rules in detail dialog | `tab_overview.py` | `PriceClassDetailDialog` had no Columns or Color Rules buttons | Added ⚙ Columns and ◈ Color Rules toolbar buttons; column prefs saved under `"overview_detail"` key; color rules shared with `"overview"` key; `_df` stored on dialog for rule repopulation |
| Color rules text contrast on theme switch | `widgets.py` | Dark bg rule showed white text in dark mode, black text in light mode (hard to read) | Added `_contrasting_color(bg_hex)` — when a rule sets bg_color but no fg_color, auto-computes white or #1a1a1a based on bg luminance; theme-independent |
| Column widths persisted across sessions | `widgets.py`, `store.py` | Resizing columns reset on app restart | `DataTable(table_id=...)` saves widths via debounced `sectionResized` → `column_widths.json`; `restore_column_widths()` called after prefs restored |
| Filter cascade removes unavailable options | `widgets.py` | Unavailable filter options were greyed out (disabled) — confusing UX | Changed `_CheckList.set_valid()` to `setVisible(False)` on invalid items (they disappear from list); `show_all()` restores on reset; `get_selected()` only returns visible+checked |
| Numeric column sorting | `widgets.py` | Columns like Overstock, Inventory (SY), Fill Rate sorted lexicographically instead of numerically | Added `NumericTableWidgetItem` — strips formatting chars (`,`, `%`, `x`) before comparing as float; `∞` sorts after numbers, `—`/blank sort last; used for all `DataTable` cells |
| Cross-column color rules | `widgets.py`, `overview_dialogs.py` | Color rules could only highlight the evaluated column itself | Added `apply_column` field to rule dict; `AddEditRuleDialog` shows "Highlight Column" dropdown (only when target=Cell) to pick a different column to color; `_refresh_list` displays "Cell → ColName"; backwards compatible (missing `apply_column` defaults to same column) |
| Multi-condition AND color rules | `overview_dialogs.py`, `widgets.py` | Each rule could only test one column; no way to say "highlight row if Overstock > 0 AND On Order > 0" | Rules now store `conditions: [{column, op, value}, ...]`; `AddEditRuleDialog` shows dynamic condition rows with `＋ Add Condition` / `×` remove button; `_eval_rule()` tests all conditions with AND logic; backwards-compatible with legacy flat `{column,op,value}` format; `ThresholdRulesDialog` shows conditions summary "ColA > 0  AND  ColB > 0" |
| Date picker width too narrow | `main_window.py` | QDateEdit showed only partial date text (e.g. "26-05-06") | Added `setMinimumWidth(110)` to both `_date_start` and `_date_end` |
| Product Line filter cascade hidden | `widgets.py` | `_compute_valid()` included empty-string values (`""`) from items with no `IPRODL`; `set_valid({"", ...})` matched no checkbox keys → ALL product line options disappeared | Excluded empty strings from `_compute_valid()` result with set comprehension `{v for v in ... if v}`; if resulting set is empty, call `show_all()` instead of `set_valid(empty_set)`; added defensive `str.strip()` on both sides of the `isin()` comparison in `_filter_metrics()` |
| Total Sales (SY) missing from tables | `tab_overview.py` | `total_qty_sy` was computed in `sku_metrics` but never surfaced | Added "Total Sales (SY)" column to PC table, Overview SKU table, and PC detail dialog; also added to totals strip in the detail dialog |
| Overstock column missing from detail dialog | `tab_overview.py` | `overstock_flag` was in `sku_metrics` and the PC summary table but not in `PriceClassDetailDialog` | Added "Overstock" column to detail dialog `table_cols` and `_build_rows()` |
| "Open in Timeline Tab" does nothing from PC detail dialog | `tab_overview.py` | `open_in_tab` signal from `TimelineDialog` not connected in `PriceClassDetailDialog._on_double_click` | Added `_navigate` closure that closes both dialogs and emits `sku_selected`; also added `sku_selected` signal to `ProblemAreasTab` + wired in `main_window.py` |
| PO table always empty (root cause) | `timeline_popup.py` | `populate()` called inside `__init__` before `show()` — WebEngine `insertWidget` triggers layout recalculation that collapses the table before rows are painted | Deferred populate via `QTimer.singleShot(80, ...)` so it runs after dialog is fully shown and layout is stable; uses `bundle.po_events` (same source as chart markers) |
| Overstock definition wrong | `metrics_service.py`, `timeline_popup.py`, `tab_timeline.py` | Old: DOI > 2× target DOI. Didn't account for inventory sold before on-order arrives | New: `projected_post_receipt = max(inv - daily×lead_time, 0) + on_order`; flag overstock when `projected_post_receipt > 3 × daily × lead_time`; recommendation text updated accordingly |
| Launch date display capped at Aug 5, 2025 | `metrics_service.py` | `m["launch_date"]` stored raw uncapped dates (e.g. 2024-05-22) even though `_effective_days()` already floored calculations at Aug 5 2025 | Applied `max(d, _FLOOR_DISPLAY)` to the displayed launch_date column so UI is consistent with what avg_daily is calculated against |
| Runout risk redefined | `metrics_service.py` | Old: `inventory < lead_time_demand AND on_order == 0`. Too narrow — missed cases with a PO on order that still can't cover demand | New: `(inventory + on_order) < 1.5 × avg_daily × lead_time`. Mirrors overstock formula; no longer requires on_order == 0 |
| Monthly sales bar chart | `timeline_popup.py`, `tab_timeline.py` | Users wanted to see last-12-months bar chart in both popup and Timeline tab | Added `_build_monthly_chart(sku, bundle)` helper in `timeline_popup.py` (imported by `tab_timeline.py`); groupby on pre-loaded `bundle.orders` — no extra SQL; 200px-tall chart placed after PO table in both views |
| Daily POs tab | `tab_daily_pos.py`, `queries.py`, `loaders.py`, `store.py`, `main_window.py` | Users wanted to see POs placed on any given day, grouped by operator | New tab "📋 Daily POs": date picker (defaults today), Load button, collapsible per-operator sections, SKU-level DataTable with same column-manager + color-rules as Overview, double-click → TimelineDialog; operator initials → full name mapping stored in `operator_names.json` |
| Daily POs: Overstock column + remove icon | `tab_daily_pos.py` | User wanted Overstock alongside Runout Risk and no warning icon glyphs in any data table | Added `Overstock` column (Yes/No from `overstock_flag`); changed Runout Risk display from `⚠  Yes` to plain `Yes` |
| Daily POs: Backorders column + cross-table width sync | `tab_daily_pos.py` | User wanted Backorders visible alongside inventory metrics; resizing a column in one operator section should immediately resize the same column in all other operator sections and persist | Added `Backorders (SY)` column showing **strict_bo_qty_sy** (quantity in SY, not count); added `_sync_column_width()` method wired to each operator table's `sectionResized` signal via re-entrancy guard (`_syncing_column_width` flag) so resizes propagate to all sibling tables without loops; widths already shared on disk because all tables use the same `table_id="daily_pos"` |
| Problem Areas overhaul | `tab_problems.py` | User wanted a polished triage view limited to the three actionable problem types with toggleable per-type filtering | Rewrote tab to show only **Overstock**, **Runout Risk**, and **Zero Stock & No PO** alerts (dropped Excess Orders + Aging from this view; flags still computed and used elsewhere). Added a **filter pill bar** at the top with one checkable pill per alert type (showing live count), plus All/None convenience buttons. Cards redesigned: 5px colored left border, hover highlight, icon + uppercase label header, monospace-free metric chips for Inventory / On Order / Avg Daily / DOI / Target DOI, snooze + timeline action buttons stacked on the right with full-width labels (no more "inoozi" clipping). Empty states distinguish between "all healthy", "no pills selected", and "no matches". Snooze + Timeline functionality preserved verbatim. The `no_stock` alert key is new — it was always derived inline; existing snooze keys (`overstock:`, `runout_risk:`) unchanged so prior snoozes still work |
| Problem Areas: actionable Overstock + sort + perf | `tab_problems.py` | UI was freezing on large datasets and Overstock listed thousands of items the buyer can't act on right now | (1) **Overstock now requires `on_order_sy > 0` OR `strict_bo_qty_sy > 0`** — only items where there is an open PO or active backorder against them get surfaced, since those are the only ones worth acting on. (2) **Default sort: total_qty_sy DESC** within every alert type so highest-volume items appear first. (3) **Vectorised pre-filter** — replaced row-by-row `iterrows` with boolean masks (`overstock_mask`, `runout_mask`, `nostock_mask`) computed in one pass, then iterate only the small qualifying subset. (4) **Pagination** — render in batches of `_PAGE_SIZE = 100` cards with a "▼ Show next 100" footer button, preventing the UI from creating thousands of widgets at once. (5) **Debounced refresh** — `QTimer.singleShot(120 ms)` collapses bursts of pill toggles / sidebar filter changes into a single rebuild. (6) **`setUpdatesEnabled(False)`** around card creation/teardown to avoid intermediate repaints. Together these changes turn what previously took several seconds (and triggered the "Not Responding" Windows banner) into a snappy sub-100 ms refresh |
| Vectorised metrics_service hot paths | `metrics_service.py`, `loaders.py` | `compute_all()` had several per-row `.apply()` loops over 11k+ SKUs that took noticeable wall-clock time on every refresh | Vectorised five hot paths with **identical output**: (a) `stockturn_target` resolution — replaced `df.apply(axis=1, lambda row: resolve_target(...))` with layered `Series.map()` calls (least → most specific precedence: `sup` → `pl` → `pc` → `cc` → `sku`, each later override winning via `.where(notna(), prev)`); (b) `effective_days` — replaced `.apply(_effective_days)` with a single `Series.map(launch_dates) → pd.to_datetime → (end - start).dt.days + 1` pipeline; (c) `days_since_last_sale` — vectorised via `(today_ts - to_datetime(last)).dt.days`; (d) `launch_date` floor — vectorised via `Series.where(>= floor_ts, floor_ts)`; (e) `is_new` — vectorised via `(today_ts - launch).dt.days < 180`; (f) sales aggregation lambdas (`backorder_count`, `strict_bo_qty_sy`, `filled_count`) — pre-compute boolean-weighted columns and use plain `"sum"` in `.agg()` instead of per-group Python lambdas; (g) `loaders.load_rolls` `age_days` calculation — vectorised via `(today_ts - to_datetime(receive_date)).dt.days`. Smoke-tested: 11,838 SKUs, overstock=4774, runout=973 — identical to pre-change values |
| Removed Fill Rate tab | `main_window.py` | Fill rate is already shown as a column option in multiple tables and KPI cards; the dedicated tab was redundant | Removed `FillRateTab` import, instantiation, `addTab`, and `refresh()` call. `tab_fillrate.py` left in place (unreferenced) so fill-rate logic stays available if needed; no metrics or column data affected |
| Refresh crash: deleted QThread | `main_window.py` | After the first refresh, the worker QThread is `deleteLater`'d, but `self._thread` still held a Python ref to the dead C++ object. The next click on **Refresh Data** called `self._thread.isRunning()` and raised `RuntimeError: wrapped C/C++ object of type QThread has been deleted` | (1) Wrapped the `isRunning()` guard in `try/except RuntimeError` so a stale ref no longer crashes — falls through to start a fresh thread. (2) Added `_on_thread_finished()` slot wired to `thread.finished` that nulls out `self._thread` / `self._worker` once Qt has scheduled the C++ objects for deletion, so subsequent refreshes start from a clean state |
| Standalone .exe distribution (AV-friendly) | `app.spec`, `version_info.txt`, `DISTRIBUTION_README.md`, `.gitignore` | Needed a standalone Windows build that any user can run without installing Python, and that does not get flagged by Windows Defender / SmartScreen / corporate EDR | (1) Switched PyInstaller spec from `--onefile` to **`--onedir`** — onefile builds self-extract to `%TEMP%` on every launch, the #1 behavioural pattern AV products flag as suspicious. (2) **Disabled UPX** (`upx=False` on both EXE and COLLECT) — UPX-packed binaries are heuristically scored as malware by most AV engines because real malware uses UPX to hide payloads. (3) **Embedded full PE version resource** via `version_info.txt` (CompanyName=NRF Distributors, FileDescription, ProductName, LegalCopyright, FileVersion 1.0.0.0) — unsigned EXEs without metadata trip SmartScreen "Unknown Publisher" warnings far more often. (4) **Added numpy submodule sweep** via `collect_submodules('numpy')` (filtered to drop `_pyinstaller` hook paths and `tests`) — NumPy 2.x reorganised internals and PyInstaller's static analysis was missing `numpy._core._exceptions`, causing the EXE to crash on launch. (5) Excluded test/dev modules (`pytest`, `unittest`, `test`) to shrink output. (6) Built artefact: `dist\InventoryControl\InventoryControl.exe` (~27 MB) + DLL folder, packaged as `dist\InventoryControl.zip` (~187 MB compressed). (7) `DISTRIBUTION_README.md` shipped inside the zip explains: unzip, double-click the EXE, requires ODBC Driver 18 for SQL Server, settings live in `%APPDATA%\PurchaseOrderBot\`. (8) `build/` and `dist/` added to `.gitignore` so build artefacts don't bloat the repo. Smoke-tested: EXE launches, stays alive, ~218 MB RAM, responsive |
| Daily POs second-click crash | `tab_daily_pos.py` | Same QThread deletion crash that `main_window.py` had — clicking **Load** a second time after a load completed raised `RuntimeError: wrapped C/C++ object of type QThread has been deleted`. On other peoples' machines this manifested as the app showing "Loading…" forever and never recovering | Applied the same fix as Phase 6 to `tab_daily_pos.py`: wrap the `self._thread.isRunning()` guard in `try/except RuntimeError` (resets refs to None on stale-ref error), add `_on_thread_finished()` slot wired to `thread.finished` that nulls `self._thread` / `self._worker`, and add a `closeEvent()` that calls `quit()` + `wait(5000)` to drain any in-flight worker before the tab is destroyed. The "always loading on first machine" symptom on other peoples' computers was the SAME bug — the second click never proceeded because the dead QThread crashed the slot before the user saw any error |
| DOI Current vs DOI Projected | `metrics_service.py`, `tab_overview.py`, `tab_daily_pos.py` | "Days of Inventory" was ambiguous — unclear whether it counted just current warehouse stock or also included on-order POs. Buyers asked for both numbers side-by-side | Added second metric `days_of_inventory_projected = (inventory_sy + on_order_sy + po_pending_qty) / avg_daily_sales_sy` alongside the existing `days_of_inventory` (which stays as **current inventory only**). Both are now exposed as columns in: Overview SKU table, Overview Price-Class table, Price-Class detail dialog, and Daily POs operator tables. Column headers labeled **"Days of Inv"** and **"Days of Inv (Proj)"** so meaning is unambiguous. Existing column / KPI / rule references unchanged (additive only — preserves backward compatibility with saved column prefs and color rules) |
| Column expansion across Daily POs | `tab_daily_pos.py` | Daily POs operator tables were missing **Total Sales (SY)** which buyers needed to see velocity context next to each PO line | Added `Total Sales (SY)` column (sourced from `total_qty_sy` in `sku_metrics`) right after `Avg Daily (SY)`. Note: `populate()` calculates total SY for the stats label using `r[5]` (Qty (SY) at index 5) — verified unchanged after column additions |
| Daily POs nested-table scrolling | `tab_daily_pos.py` | The page had a `QScrollArea` containing per-operator sections, each with its OWN `DataTable` that grew its own vertical scrollbar — scrolling down required the user to scroll the outer page until they hit a table, then scroll inside the table, then continue scrolling the page below. Annoying and confusing | Each operator table is now sized to **exactly fit its rows** via a new `_fit_table_height(self)` helper (`header_height + row_count × default_row_height + frame + horizontal_scrollbar_reserve`) called from `populate()`. The inner table's vertical scrollbar is disabled (`Qt.ScrollBarPolicy.ScrollBarAlwaysOff`) and the table has a `QSizePolicy.Fixed` vertical policy so the outer `QScrollArea` is the single scrolling surface for the whole page |
| AI tab MVP — natural-language SQL | `app/ai/__init__.py`, `app/ai/schema.py`, `app/ai/providers.py`, `app/ui/tab_ai.py`, `app/ui/tab_settings.py`, `app/data/store.py`, `main_window.py` | Buyers wanted a way to ask data questions in plain English and get answers without writing SQL. Token cost matters | New tab "🤖 AI" between Daily POs and Settings. Architecture: (1) `app/ai/schema.py` — single `SCHEMA_PROMPT` constant (~4.8 KB) summarising every table, the `[bracket]` rule for `#`/`@`/`$` columns, common filters (active items, sales vs PO accounts, exclusion of `1xx` cost centres, etc.), and join paths. Sent as the SYSTEM prompt on every request — no chat history kept, so input tokens stay flat. (2) `app/ai/providers.py` — minimal HTTP clients for **Anthropic** (Claude Messages API), **OpenAI** (Chat Completions), **Google** (Gemini generateContent) using only stdlib `urllib.request` (no SDK dependencies → PyInstaller-friendly). (3) `tab_ai.py` — `QLineEdit` + `Ask` button → `_AIWorker` (QThread, follows the same try/except-RuntimeError + `_on_thread_finished` pattern as Daily POs) → returned text is sanitised (strip ` ```sql ` fences, trim prose) → validated by `validate_sql()` which requires `^\s*(WITH|SELECT)\b` and rejects `INSERT|UPDATE|DELETE|DROP|TRUNCATE|EXEC|MERGE|ALTER|CREATE|GRANT|REVOKE|XP_|SP_` plus blocks `;` (no multi-statement) → executed via `read_dataframe(sql)` → rendered in a `DataTable` with dynamic columns (new `DataTable.set_columns(list)` method added to `widgets.py`). The generated SQL is shown in an editable `QTextEdit` with a **▶ Run SQL** button so the user can tweak before re-running — same safety validation applied. (4) Settings tab — new "AI Provider" `QGroupBox` with provider dropdown (anthropic/google/openai), Model `QLineEdit` (placeholder shows recommended model per provider), API key `QLineEdit` in `EchoMode.Password`, **Save AI Settings** button, and a multi-line info label with cost-per-1M-tokens for each recommended model + clickable links to console.anthropic.com / aistudio.google.com / platform.openai.com. (5) `app/data/store.py` — `get_ai_config()` / `set_ai_config()` persist to `%APPDATA%\PurchaseOrderBot\ai_config.json`. (6) `main_window.py` — wired `self._ai_tab = AITab()` + `addTab(self._ai_tab, "🤖  AI")` between Daily POs and Settings |
| AI tab v2 — chat memory, saved query library, clarifying questions, self-correction | `app/ai/providers.py`, `app/ai/schema.py`, `app/ui/tab_ai.py`, `app/ui/tab_settings.py`, `app/data/store.py` | First user feedback: (a) typing the model name as free text led to typos like `gpt-40-mini` (zero) → silent HTTP 404 from OpenAI; (b) **Save AI Settings** gave no visible feedback so users couldn't tell if it worked; (c) the AI guessed at field names instead of asking; (d) asking the same question twice paid the AI cost both times — wanted to save successful queries; (e) wanted to parameterise saved queries and reuse them on demand | (1) **Settings** — replaced the model `QLineEdit` with an **editable `QComboBox` populated per-provider** with the known good model IDs (`claude-sonnet-4-5/-opus-4-5/-haiku-4-5`, `gemini-2.5-flash/-pro/-flash-lite`, `gpt-4o-mini/gpt-4o/gpt-4.1-mini/gpt-4.1`); switching the provider auto-loads the right list and picks the recommended default. Added an inline **Saved!** confirmation label that auto-clears after 6 s — and shows a **warning** colour if the API key field was empty. (2) **`providers.py`** refactored: every `call_*()` now accepts `messages: list[{role,content}]` (or a string for backward-compat) and a separate `system` prompt. Maps cleanly to each API: Anthropic `system`+`messages`, OpenAI `[{system}, ...]` flat list, Gemini `system_instruction`+`contents` with role coerced to `user`/`model`. (3) **`schema.py`** — added explicit format rules to the system prompt: AI must output **either** `QUESTION: <one or two short questions>` (when ambiguous — e.g. unclear unit, missing date range) **or** `SQL: <query>` — never both, no markdown, no prose. New `build_system_prompt(saved_queries)` helper appends a brief "PREVIOUSLY CONFIRMED WORKING QUERIES" section listing only the **name + 1-line description** of each saved query (NOT the SQL body) — costs ~50 chars per saved query but lets the AI reference the library by name without re-deriving similar SQL. (4) **`tab_ai.py`** rewritten with a horizontal splitter: **Saved Queries sidebar** (`QListWidget` with Run / Edit / Delete) on the left, main column on the right with **Conversation transcript** (`QTextEdit`, role-coloured You/AI bubbles), editable **Generated SQL** panel (with **💾 Save Query** + **▶ Run SQL** buttons), and the **Results** `DataTable`. Conversation is preserved in `self._history` and re-sent on every turn, enabling true back-and-forth — `New Chat` button clears it. (5) Added `parse_response(text)` returning `('question'\|'sql'\|'text', body)` based on the format markers; questions are appended to the transcript and the AI awaits the user's reply, SQL is executed automatically. (6) **Self-correction loop**: when AI-generated SQL throws a SQL Server error, the error message is silently appended to the conversation as a user turn ("That query failed with this error … please fix it") so the next message from the AI is a corrected query. (7) **Saved-query library** — new `app/data/store.py` helpers `get_saved_queries`/`add_saved_query`/`update_saved_query`/`delete_saved_query` persist to `%APPDATA%\PurchaseOrderBot\saved_queries.json` with schema `{id,name,description,sql,created}`. **Parameterised SQL** uses `{name}` placeholders; `find_parameters()` extracts them and a `ParameterDialog` prompts the user for values at run time (defaults remembered per session in `self._param_defaults`). The same dialog is reused when running a saved query and when manually re-running parameterised SQL via **▶ Run SQL**. (8) Pre-save validator runs against the SQL with placeholders substituted as `1` so a parameterised query like `WHERE cost_center='{cc}'` validates as `WHERE cost_center='1'` — catches forbidden keywords without breaking on the placeholder syntax |
| AI tab v3 — persistent memory bank ("teach once, never again") | `app/ai/schema.py`, `app/ui/tab_ai.py`, `app/data/store.py` | After v2, the user reported that even after explaining "exclude supplier 001 when discussing suppliers" in chat, clearing the chat lost the rule and the AI re-included supplier 001 the next time. They wanted a way for the AI to **learn nuances permanently** without having to re-explain on every fresh conversation | (1) New persistent store: `%APPDATA%\PurchaseOrderBot\ai_notes.json` with `get_ai_notes / add_ai_note / update_ai_note / delete_ai_note / clear_ai_notes` helpers in `store.py`. Notes dedupe on case-insensitive exact match to keep the prompt lean. (2) `schema.py` `build_system_prompt(saved_queries, notes)` now injects a **"USER PREFERENCES & NOTES (always apply unless the user overrides them in the current turn)"** section listing every note verbatim. The system prompt is rebuilt from disk on every chat turn, so notes apply across sessions, app restarts, and `New Chat` resets. (3) New **third response format**: the AI is instructed that when the user is teaching a rule (phrases like "remember", "always", "never", "from now on", "note that"), it must reply with `REMEMBER: <single concise factual sentence>` — `parse_response()` detects this marker and returns `("remember", fact)`. (4) The chat handler auto-saves the fact via `store.add_ai_note()`, refreshes the Memory list, and posts a green "🧠 Saved to memory" confirmation in the transcript. (5) New **Memory panel** in the AI tab sidebar (vertical `QSplitter` below Saved Queries) showing each note with `+ Add` / `Edit` / `Delete` buttons; double-click also opens the editor. `QInputDialog.getMultiLineText` is used for add/edit to handle multi-line rules cleanly. (6) Updated the info hint at the top of the chat area to mention the "remember" workflow so the feature is discoverable. (7) `_set_busy()` extended to gate the new memory buttons during long-running AI calls. Smoke-tested: `parse_response("REMEMBER: ...")` → `("remember", ...)`; `build_system_prompt(qs, notes)` correctly emits the new section before the saved-query section |
| AI tab v3.1 — app-metrics awareness (Days of Inv, Net Inv, Stock Turn, …) | `app/ai/schema.py` | After v3, user asked the AI for **DaysOfInv** of QEP10113. AI returned `i.IDELIV = 112` (raw item lead-time field), but the Overview tab actually shows **274** because `days_of_inv` is a **computed** metric: `inventory_sy / avg_daily_sales_sy`. The AI was confusing raw DB columns with the app's derived KPIs (Days of Inv, Days of Inv (Proj), Net Inv, Avg Daily SY, Stock Turn, Fill Rate, Runout Risk, etc.). User wanted the AI to know about these app-level variables while keeping token cost low | Added a **"COMPUTED APP METRICS"** glossary (~2 KB) to `SCHEMA_PROMPT` that defines every metric the user sees in the UI with an exact pseudo-SQL formula matching `app/services/metrics_service.py`. Includes: full **UoM → SY** `CASE` expression (mirrors `_to_sy()` in `loaders.py` — SF/9 only for cost centers `010-013`, LY×IWIDTH/36, LF×IWIDTH/108, IN×IWIDTH/1296), per-SKU formulas for `inventory_sy / on_order_sy / po_pending_qty / total_sales_sy / effective_days / avg_daily_sales_sy / net_inventory_sy / days_of_inventory / days_of_inv_projected / stock_turn / lead_time_days / runout_risk`, with the active-roll, pending-PO, and sales-window filters spelled out. The prompt also instructs the AI to **ASK** for a `{from}/{to}` date window via `QUESTION:` if not specified (app default `2025-08-05` → today), so saved queries become reusable parameterised queries. Explicit "DO NOT return `i.IDELIV` when the user asked for Days of Inv" guard rule prevents the original mistake. Token cost: ~2 KB per call (~$0.006 with Claude Sonnet 4.5) — completely eliminates app-vs-DB-column ambiguity |
| AI tab v3.9 — pre-baked Starter Library + chip-as-direct-execution + Library dialog + pre-execution table validator + gpt-4o default | `app/ai/starter_queries.py` (new), `app/ui/tab_ai.py`, `app/ai/providers.py` | After v3.8 the user reported that the suggestion chips and several saved queries *still* returned 0 rows or wrong results. Root cause: even with action-first prompts, DEFAULT INTERPRETATION CHEAT-SHEET, and 3 worked examples, asking an LLM to translate *"Top 20 SKUs by sales last 90 days"* → multi-CTE rollup with UoM-to-SY normalization + base_sku grouping + LEFT JOIN of sales/inventory/on_order is fundamentally probabilistic. User asked for a from-scratch rebuild targeting 99 % satisfaction with creative freedom | The architectural pivot: **stop asking the LLM to rewrite the same canonical queries every time and ship them pre-baked instead.** (1) **New `app/ai/starter_queries.py`** — a 12-query hand-curated library covering Sales (top SKUs by sales / by stock turn, sales by manufacturer), Stock Health (stockouts with active sales, overstock with Days of Inv (Proj) > 275 launched > 6 mo ago, runout risk at 1.5× lead-time, slow movers), POs (open POs by cost-center prefix, backorder lines), and Items (discontinued with stock, inventory snapshot by CC, new SKUs < 180 days). Every query: (a) follows the canonical `sku_base + LEFT JOIN sales/inv/oo` skeleton documented in `schema.py` so SKUs with zero sales are never silently dropped; (b) inlines the full UoM→SY CASE block (no `to_sy()` shorthand); (c) uses `{from}` / `{to}` placeholders auto-filled from the app's top-bar window + `{cc}` for prompted params; (d) excludes `1xx` cost centers by default; (e) handles all the active-roll / pending-PO filters explicitly. Module exports `STARTER_QUERIES` (full list with `category` for grouping), `CHIP_KEYS` (5-element subset surfaced as chips), `get_starter(key)`, and `get_chips()`. Reusable CTE constants `_SKU_BASE_CTE / _SALES_CTE / _INV_CTE / _OO_CTE / _LAUNCH_CTE` keep the SQL DRY. (2) **`tab_ai.py` — chips now bypass the AI entirely**: the `chip_specs` array of free-text prompts (which the AI had to translate every time) was replaced with a loop over `get_chips()` that wires each chip to `_run_starter(key)`. New `_resolve_starter_sql(spec)` substitutes `{from}` / `{to}` from `_get_app_window()` (with `2025-08-05`/today fallback if the tab is detached), prompts for any remaining named params via the existing `ParameterDialog`, and returns ready-to-run SQL. New `_run_starter(key)` writes that SQL to the view (with `_sql_from_ai = False` so manual Run doesn't trigger AI auto-fix), posts a *"⚡ Starter: <name>"* breadcrumb in the transcript, and routes through the existing `_execute_sql(sql, source="starter: <name>")` pipeline — instant deterministic results, zero LLM cost. The `_apply_suggestion()` slot is kept (for any future free-text chips) but no current chip uses it. (3) **New `LibraryDialog`** opened by a *"📚 Library…"* button at the end of the chip row: 680×480 modal with a categorised `QListWidget` (disabled bold section headers per category, double-click or *▶ Run* to execute), tooltip showing required params, and the same dark-card styling as the rest of the tab. Selecting an entry calls `_run_starter(key)` so the Library and chips share one execution path. (4) **Pre-execution table validator** in `_execute_sql` for `source="AI"`: new `_find_missing_tables(sql)` pulls every `dbo.<Table>` reference out of the SQL and checks each against `INFORMATION_SCHEMA.COLUMNS` (reusing the existing `_describe_table` cache + new `__missing__` sentinel to avoid re-querying known-bad names). If any table is missing, the SQL never reaches the DB — instead an auto-fix message is sent to the AI explaining which tables are unknown, prompting it to use `INSPECT` for the correct name. Catches an entire class of failure (typo'd table names, hallucinated tables) before the user sees a red error. (5) **`providers.py`** — bumped `DEFAULT_MODELS["openai"]` from `gpt-4o-mini` to `gpt-4o`. Mini was the weakest of the three provider defaults; for users targeting 99 % satisfaction the price difference (~5×) is more than worth it. Anthropic stays on `claude-sonnet-4-5` (already the strongest default). Token cost: **zero** for any chip / Library click — chip queries no longer roundtrip the LLM. AI is now reserved exclusively for genuinely novel free-text questions, where the v3.6–v3.8 infrastructure (action-first prompt, DEFAULT INTERPRETATION, worked examples, INSPECT, auto-retry, app-window injection, zero-row diagnostic) still applies. Smoke-tested: 12 starters load, 5 chips wire correctly, `LibraryDialog` constructs, openai default = `gpt-4o` |
| AI tab v3.8 — confident defaults, few-shot examples, app-window injection, futuristic UI | `app/ai/schema.py`, `app/ui/tab_ai.py` | Despite v3.6/v3.7 the AI was *still* asking *"which table should I use?"* on questions like *"show me POs ordered last month over $10k"*. The retry chain also stalled at 1/3 because every retry came back with another `QUESTION:` instead of corrected SQL. User asked for an overhaul: smarter defaults, less asking, more polished/futuristic look | (1) **Schema prompt rewritten as action-first instead of ask-first**: section (A) now says *"Use this RARELY — only when the user's request is genuinely ambiguous AND no reasonable default exists. PREFER ACTING over asking: pick the most likely interpretation, run the query, and let the user refine."* + explicit *"DO NOT ask which TABLE to use — use the DEFAULT INTERPRETATION CHEAT-SHEET below."* (2) **New DEFAULT INTERPRETATION CHEAT-SHEET** (~1 KB) maps every common phrasing the user actually uses to its canonical table+filter: *POs/orders entered → OPENPO_D*, *open POs → OPENPO_D + open filter*, *received → OPENIV*, *sales/invoices → _ORDERS [ACCOUNT#I]>1*, *warehouse POs → _ORDERS [ACCOUNT#I]=1*, *inventory/stock → ROLLS active*, *items/SKUs → ITEM active+non-disc+non-1xx-CC*, *Days of Inv/Stock Turn/Net Inv/etc → COMPUTED APP METRICS formulas*, *launch date/new SKUs → APP-DERIVED MIN-of-MINs*, plus supplier/cost-center/mfgr/PL/lead-time/price/backorders/discontinued mappings. Closes with the GUIDING PRINCIPLE: *"When the user says 'show me POs ordered last month over $10k' — you have everything you need. Just write the query."* (3) **Three WORKED EXAMPLES** appended (~1.5 KB) — fully-formed Q→SQL pairs for *"top 20 SKUs by sales last 90 days"*, *"open POs for cost center 010 due this month"*, and the exact failing case from the v3.7 image *"POs with Days of Inv (Proj) > 275 placed in last 14 days"*. Few-shot examples are by far the highest-leverage way to shape LLM output — the AI now copies the canonical pattern instead of inventing one. (4) **App working window injection**: `build_system_prompt(saved_queries, notes, today, app_window)` now accepts the app's top-bar From/To dates and injects them as `{from}` / `{to}` placeholders + YYYYMMDD ints into the system prompt. New `_get_app_window()` helper on `AITab` walks `self.window()` to pull `_date_start` / `_date_end` from `MainWindow`. All four `build_system_prompt` call sites (Ask, retry, INSPECT, zero-row diagnose) now pass `today=date.today()` + `app_window=self._get_app_window()`. Removed the legacy *"If the user has not specified a sales-window date range, ASK with a QUESTION line"* instruction — it now says *"USE THE APP WINDOW. NEVER emit `QUESTION:` asking which date range to use."* (5) **Futuristic gradient banner header**: replaced the plain `SectionTitle("🤖 AI Query")` with a 10-px-padded, 8-px-rounded banner using `qlineargradient(stop:0 #6366f1, stop:0.5 #8b5cf6, stop:1 #ec4899)` (indigo → violet → pink) with white bold ✨ AI Query title and a soft inline subtitle. (6) **Suggestion chips** below the input: 5 clickable rounded pills (📊/📦/🔥/⚠️/🔄 emoji + descriptive text) populate the input on click — *Top 20 SKUs by sales last 90 days*, *Open POs for cost center 010 due this month*, *SKUs with Days of Inv (Proj) > 275 launched over 6 months ago*, *Stockouts with active sales*, *Top 10 SKUs by stock turn YTD*. Hover lifts the border + text to the accent colour. (7) **Polished input UX**: bumped `_input` and buttons to 36 px height; placeholder rephrased to *"Ask anything about inventory, sales, POs, SKUs…"*; Ask button gets the ✨ prefix; tightened the info hint into a single 11 px line. Token cost: prompt grew 20,963 → ~26,956 chars (~5.2 K → ~6.7 K tokens, still within the 10 K budget). Result: AI no longer asks which table to use; it has worked examples to mimic; it knows the user's current From/To window without asking; and the AI tab feels distinctly more "AI" with the gradient banner + clickable chips |
| AI tab v3.7 — manual-Run auto-fix + launch-date relative phrasing + GROUP BY guard | `app/ai/schema.py`, `app/ui/tab_ai.py` | After v3.6 the user asked *"show me POs ordered in the last 14 days that have a Days of Inv (proj) over 275 and have a launch date of over 6 months ago"*. Three failures stacked: (a) the AI asked `QUESTION: what date for launch date?` even though "over 6 months ago" is itself a relative phrase resolvable from CURRENT_DATE; (b) the AI eventually produced SQL that referenced `sales.total_sales_sy` in the outer SELECT without aggregating or grouping → SQL Server error 8120 *"Column is invalid in the select list because it is not contained in either an aggregate function or the GROUP BY clause"*; (c) when the user clicked **Run SQL** manually on that errored AI-generated SQL, the auto-fix pipeline DID NOT fire because `_on_run_manual` hard-coded `source="manual"` — only the Ask button's auto-pipeline (`source="AI"`) triggered retries. So the user saw "1 of 3 attempts used" but no further retries on manual Run | (1) **`tab_ai.py`** — new instance flag `self._sql_from_ai: bool` tracks whether the SQL currently in the view came from the AI. Set to `True` (with `blockSignals`) every time `_on_finished` writes AI SQL into `_sql_view`; set to `False` when loading a saved query, on `_on_new_chat`, and on any user keystroke in the SQL view (new `_on_sql_view_edited` slot wired to `_sql_view.textChanged`). `_on_run_manual` now passes `source="AI" if self._sql_from_ai else "manual"`, so manually clicking **Run SQL** on AI-generated SQL routes errors back through `_request_ai_fix` and uses the full 3 retries — but user-typed/edited SQL is treated as user-authored and shows the raw error (no AI roundtrip wasted). (2) **`schema.py`** — new **LAUNCH-DATE RELATIVE PHRASING — RESOLVE YOURSELF, DO NOT ASK** section listing every common phrasing with its `HAVING` clause: *"launched over N months ago"* → `HAVING launch_date <= DATEADD(month, -N, CAST('<CURRENT_DATE>' AS date))`, *"new SKUs"* → `HAVING launch_date > DATEADD(day, -180, …)`, *"launched before YYYY-MM-DD"* → `HAVING launch_date < '<that date>'`. Closes with: *"NEVER emit `QUESTION: what date for launch date?` when the user already gave a relative phrase."* (3) **`schema.py`** — new **GROUP BY / aggregation rule** section explicitly addressing SQL Server error 8120: explains the three legal forms (wrap in aggregate / add to outer GROUP BY / inner CTE already aggregated by join key) and prescribes the canonical pattern: *"each per-sku CTE ends with `GROUP BY base_sku`, and the outer SELECT does NOT add a GROUP BY — it just `LEFT JOIN`s and `COALESCE`s. Do NOT mix `SUM(...)` and bare CTE columns in the same SELECT without grouping by the bare columns."* Token cost: ~1.5 K chars (~370 tokens). Total prompt now 19,448 → 20,963 chars (~5.2 K tokens, still well under the 10 K budget). Result: relative launch-date phrasing resolves without asking; manual Run on AI SQL now self-heals via the same auto-fix pipeline as Ask; SQL Server error 8120 has explicit prevention guidance |
| AI tab v3.6 — CURRENT_DATE + relative-date resolution (no more "what date range?") | `app/ai/schema.py`, `app/ui/tab_ai.py` | After v3.5 the AI still answered every relative date phrase ("last 7 days", "last month", "in May", "MTD", "YTD") with `QUESTION: what specific date range?` — even though the app's top toolbar already loads `To:` = today's current date by default. It also burned a turn guessing column names like `d.D@DATE` (which doesn't exist on `OPENPO_D`) and threw `Invalid column name 'D@REF#'` / `multi-part identifier 'd.D@DATE' could not be bound` after exhausting both retry attempts | (1) **`schema.py`** — new `_build_date_context(today)` helper renders a **CURRENT_DATE block + RELATIVE DATE WINDOWS table** at the top of every system prompt: pre-computes `today / yesterday / last 7 days / last 30 / last 90 / this week (Mon-today) / last week (Mon-Sun) / this month (MTD) / last month / this quarter / last quarter / YTD / last year` as `(YYYYMMDD_start, YYYYMMDD_end)` int pairs ready to drop into `o.ORDER_ENTRY_DATE_YYYYMMDD BETWEEN <s> AND <e>`. Token cost ~600 chars (~150 tokens). The AI now resolves relative phrases itself instead of asking. `build_system_prompt(saved_queries, notes, today=None)` accepts an optional `today` (defaults to `date.today()`) so the date context is always fresh on every call. (2) **Section (A) "clarifying question" tightened** — explicit "DO NOT ask for an explicit date range when the user already used a relative phrase" rule listing every covered phrasing, plus a closing rule "for `in <Month>` / `in <Month YYYY>`, use the first and last day of that month". (3) **INTENT MAPPING upgraded** — the previous "if OPENPO_D has no entry-date column the user expects, ASK with QUESTION:" line was the root cause of the v3.5 image-2 failure (the AI asked, then guessed `d.D@DATE`, then crashed). Replaced with: "If you are not 100% sure which OPENPO_D column holds the entry/order date the user wants, emit `INSPECT: dbo.OPENPO_D` first to see the real column list — do NOT guess names like `D@DATE` (this column does NOT exist). When in doubt about ANY date column on ANY table, INSPECT first." (4) **`tab_ai.py`** — bumped `MAX_AUTO_RETRIES` from 2 to 3: the 1st retry typically fixes typos via the auto-attached column hint (v3.4), the 2nd handles INSPECT round-trips, the 3rd is a safety net for compound errors like the image-2 case where the AI had to learn TWO bad columns at once. (5) **UI date defaults audit** — confirmed `main_window.py` already initialises `_date_end` to `QDate(today.year, today.month, today.day)` on every app launch, so the user-visible `To:` field is always today. No change needed. Result: prompt grows from ~17.5 K → ~19.4 K chars (~4.4 K → ~4.9 K tokens, well under the 10 K budget); zero clarifying-question turns wasted on relative-date phrasing; one extra retry slot for compound column-name failures |
| AI tab v3.5 — APP-DERIVED VARIABLES glossary + universal Excel/CSV export on every table | `app/ai/schema.py`, `app/ui/widgets.py`, `requirements.txt` | Two unrelated polish items in one pass: (a) the AI asked the user *"where can I find launch_date?"* — but `launch_date` is **not** a DB column, it's `MIN(MIN(orders.ORDER_ENTRY_DATE), MIN(rolls.RLRCTD)) per base_sku, floored at 2025-08-05`. Same is true for `effective_days`, `inventory_age_days`, `days_since_last_sale`, `fill_rate`, `is_new`, `sku_rating`, `overstock_flag`, `excess_order_flag`, `stockout_flag`, `stockturn_target` — all derived in `app/services/metrics_service.py`. AI was either guessing or asking. (b) Every `DataTable` in the app needed an Excel/CSV export option | (1) **Schema prompt** got a new **APP-DERIVED VARIABLES** section (~1 KB) listing every UI-visible variable that has *no source DB column* with its exact derivation formula sourced from `_compute_sku_metrics()` — including `launch_date` with the full MIN-of-MINs SQL pattern (`TRY_CONVERT(date, CAST(o.ORDER_ENTRY_DATE_YYYYMMDD AS VARCHAR), 112)` floored at `'2025-08-05'`), `effective_days = DATEDIFF(day, GREATEST(launch_date, '{from}'), '{to}') + 1`, the weighted `inventory_age_days` formula, the strict-vs-non-strict backorder split, `is_new = (today - launch_date) < 180`, `sku_rating` quartiling on `orders_count`, the `overstock`/`excess_order`/`stockout` projected-post-receipt formulas with their multiplier thresholds (3.0×, 2.5×, 0×), and the per-scope `stockturn_target` precedence order (`sku: > cc: > pc: > pl: > sup: > global=4.0`). Closes with explicit guidance: *"Do NOT try to SELECT a non-existent `launch_date` / `is_new` / `fill_rate` column."* (2) **`DataTable`** in `app/ui/widgets.py` — every table in the app (Overview, Daily POs, Problem Areas, AI results, dialogs, etc.) now responds to right-click with a polished context menu: **📊 Open in Excel** (writes to `%TEMP%\<table_id>_<timestamp>.xlsx` and `os.startfile()`'s it — instant gratification, no prompts), **💾 Export to Excel…** + **📄 Export to CSV…** (QFileDialog with smart default filename `<table_id>_YYYYMMDD_HHMMSS`), **📋 Copy selection** (also bound to Ctrl+C via `keyPressEvent`). Helpers: `_table_snapshot()` captures only currently-visible columns and rows in current sort order; `_write_xlsx()` uses `openpyxl.Workbook` with bold dark-grey header fill, frozen top row, auto-sized columns (capped at width 60); `_coerce_excel_value()` reverse-parses comma-formatted numerics back to int/float so Excel sorts/aggregates work natively (leaves `—` / `∞` / `N/A` text alone). Graceful degrade: if openpyxl is missing, *Open in Excel* falls back to CSV; explicit installation hint in *Export to Excel*. (3) **`requirements.txt`** — added `openpyxl==3.1.5`. Result: every table in the app gains a one-click path into Excel without touching any tab-specific code, and the AI no longer guesses about app-derived variables — it has the formulas |
| AI tab v3.4 — schema introspection (INSPECT) + auto-attach real columns on errors | `app/ai/schema.py`, `app/ui/tab_ai.py` | After v3.3, the AI still produced SQL referencing columns it had only *seen mentioned* in the schema prompt — e.g. `r.RUM` against an OPENPO-derived alias, or other column-name guesses. SQL Server returned *"Invalid column name 'RUM'"* and the auto-retry asked for a fix without giving the AI any new information, so it just guessed again. The user wanted the AI to actually *know* the column inventory of every table on demand, not just what fits in the prompt | (1) **Schema prompt** got a new fourth response format: `INSPECT: dbo.<TableName>[, dbo.<TableName2>, ...]` — the AI is told to use it *liberally* whenever uncertain about column names, and that the app will reply on the next turn with `name (TYPE)` for every column. Costs ~50 tokens per inspection vs hundreds for a failed query + retry round-trip. (2) **`tab_ai.py`** — `parse_response()` now returns `("inspect", body)` when it sees the marker. New `_describe_table(name)` runs `SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME=...` (cached per-session in `self._column_cache`) and returns a `"col1 (int), col2 (varchar), …"` string. `_handle_inspect_request()` parses the AI's `dbo.X, dbo.Y` list, validates each as a safe identifier, fetches columns, appends a single user-role message *"Actual column lists from INFORMATION_SCHEMA: dbo.X: …\\ndbo.Y: …"* to the conversation history, then re-invokes the worker with the same system prompt — capped at `_inspect_remaining = 3` rounds per user turn (resets on `_on_send` and `_on_new_chat`). (3) **Auto-augmented column-name retries**: new `_BAD_COLUMN_RE` regex detects `Invalid column name '…'` errors on AI-sourced SQL; `_build_column_hint_for_error()` extracts every `dbo.<Table>` reference from the failing SQL (`_TABLE_REF_RE`) and appends `_format_columns_message()` to the existing `_request_ai_fix()` payload — so the AI sees its own bad SQL **plus the real columns of every table it touched** in the same retry message. One round trip resolves the typo. (4) Hardened: `_SAFE_IDENT_RE` ensures only `[A-Za-z_][A-Za-z0-9_]*` identifiers are interpolated into the introspection SQL (no quotes, no spaces — defends against any prompt-injection vector through `INSPECT:`). Inspection errors degrade gracefully (warning chip in transcript, no crash). Result: when the AI doesn't know a column name, it now either asks via INSPECT first (cheap, proactive) or learns from the auto-attached column list on the very first error (cheap, reactive). The user's *"Invalid column name 'RUM'"* class of error becomes self-healing in one turn |
| AI tab v3.3 — zero-row interactive diagnostic + OPENPO_D / LEFT-JOIN schema clarifications | `app/ai/schema.py`, `app/ui/tab_ai.py` | After v3.2 the AI happily produced *valid* SQL that returned **0 rows** for queries like *"top 20 POs entered in the last 7 days with the highest Days of Inv (Proj)"* over a future-dated window (5/4/26–5/11/26). Three real bugs in the generated SQL: (a) joined `OPENPO_D d → ITEM i ON d.[D@MFGR]=i.ItemNumber` — wrong, OPENPO_D sku is `D@MFGR + D@COLO + D@PATT`; (b) a CASE on `d.[D@MFGR]` for UoM (D@MFGR is the manufacturer code, OPENPO_D has NO UoM column); (c) chained INNER JOINs across sales / inventory / on_order CTEs — when the sales window is empty the whole result is empty even if inventory and on-order data exist; (d) "POs entered in last 7 days" filtered the sales table instead of OPENPO_D. User wanted the AI to *interactively diagnose* zero-row results instead of forcing the user to debug SQL by hand, while keeping token cost low | (1) **Schema prompt** got three targeted additions (~700 tokens, total prompt ~3.5 K tokens): the OPENPO_D join example now spells out the full concat key with `LTRIM(RTRIM())` and warns *"NEVER join OPENPO_D on `D@MFGR` alone"* + *"OPENPO_D has NO UoM column — treat its qty as already in the item's native UoM"*; an **INTENT MAPPING** block tells the AI that *"POs entered / open POs / pending POs"* maps to `dbo.OPENPO_D` (date filter goes there, NOT on `_ORDERS`); a **JOIN PATTERN** block lays out the canonical *top-N-by-metric* skeleton — start from a `sku_base` CTE filtered from `ITEM`, **LEFT JOIN** every metric CTE, `COALESCE(metric, 0)` in the final SELECT, prune empties via `WHERE COALESCE(inv,0)+COALESCE(oo,0) > 0` — with an explicit warning that ranking by `days_of_inv_projected` over an empty sales window is undefined and the AI should either filter `total_sales_sy > 0` or rank by `(inv + on_order)`. Added a **ZERO-ROW DIAGNOSTIC PROTOCOL** section at the bottom telling the AI: when the user reports 0 rows, reply with a single SQL of the form `SELECT '<step>' AS step, COUNT(*) AS rows FROM (<cte_body>) x` UNION ALL'd across each CTE + the final join — no prose, then on the next turn propose a fix. (2) **`tab_ai.py`** got a polished interactive zero-row panel: a new `QFrame` (`#zeroRowPanel`, warning-coloured 1 px border, rounded 6 px) sits between the status label and the vertical splitter, hidden by default. When `_execute_sql` returns 0 rows for an `AI`-sourced query AND `self._diagnostic_remaining > 0`, the panel slides in with the message *"⚠ The query returned 0 rows. Were you expecting results?"* and two buttons: **Yes — diagnose** and **No, that's fine**. *Yes* appends a compact diagnostic prompt (~80 tokens) to the conversation reminding the AI of the protocol, decrements `_diagnostic_remaining` (capped at 1 per user turn), and restarts the worker — the AI's response is run automatically through the existing pipeline, so the user sees the per-CTE row counts in the Results table immediately and can ask *"now fix it"* to get the corrected query. *No* hides the panel and consumes nothing. Counter resets on every fresh `_on_send` and on `_on_new_chat`. Token cost: zero unless the user clicks *Yes*; one extra round-trip per zero-row when they do. (3) The compounded result: when the AI's SQL silently returns 0 rows, the user gets a single-click "find out why" workflow that walks the AI through its own CTEs and lets it self-correct — no SQL knowledge required, no extra tokens unless the user opts in |
| AI tab v3.2 — pseudo-function guard + auto-retry on bad SQL | `app/ai/schema.py`, `app/ui/tab_ai.py` | After v3.1, the AI took the `to_sy(qty, uom, width, cc)` shorthand from the prompt's metric formulas and pasted it **literally** into its SQL. SQL Server returned error 195 *"'to_sy' is not a recognized built-in function name"* and the user got a long red error blob. Two problems: (1) the prompt didn't make it explicit that `to_sy(...)` is a macro, not a real function; (2) the failed SQL went to the conversation history but the user had to type something to nudge the AI to retry | (1) **Schema prompt rewritten**: the UoM→SY `CASE` block is now presented as the canonical inline expression with `<qty>/<uom>/<width>/<cc>` placeholders + a **fully-expanded `total_sales_sy` example**. A bold reminder at the top and bottom of the section says: *"there is NO `to_sy()` function in SQL Server"* and *"`to_sy(...)` below is the macro above — expand it inline, do NOT call it as a function"*. Added a closing reminder: *"If you write `to_sy(...)` literally in your SQL, the query WILL fail with error 195 — always expand the CASE block inline"*. (2) **Validator hardened**: new `_PSEUDO_FUNCS` regex catches `to_sy(`, `convert_to_sy(`, `to_square_yards(` and returns a precise *"shorthand from the schema prompt — expand the CASE block inline"* error message **before** the SQL ever hits the database. (3) **Auto-retry loop**: extracted a reusable `_start_worker()` and added `_request_ai_fix(error_msg, kind)` which (a) appends the error to the conversation as a user turn, (b) increments a per-turn `_auto_retries` counter, and (c) immediately re-calls the LLM with the same system prompt — capped at `MAX_AUTO_RETRIES = 2` to avoid loops. Both validation errors and SQL Server execution errors now trigger this. Counter resets on every fresh user `_on_send`. Transcript shows *"(auto) reported {kind} error to AI — asking for a fix (attempt N/2)"* so the user sees what's happening. (4) The result: when the AI mistakenly emits `to_sy(...)`, the app catches it, asks the AI to fix it, and the corrected SQL runs automatically — the user never sees the failure unless both retries fail |

---

### 12.10 Architecture Notes

**Data Flow:**
```
SQL Server → loaders.py → metrics_service.compute_all() → DatasetBundle
                                                          ├─ sku_metrics (one row per base_sku)
                                                          ├─ summary (portfolio KPIs)
                                                          ├─ filter_values (sidebar options)
                                                          ├─ po_events (dict[sku, list[dict]])
                                                          └─ open_pos, orders, rolls, etc.
```

**Overview Tab Views:**
- Default: "By Price Class" — aggregates sku_metrics by price_class; shows 1 row per PC
- "By SKU" — shows all rows in sku_metrics (one per base_sku); double-click → TimelineDialog
- PC drill-down: double-click price class row → PriceClassDetailDialog (SKU-level + totals)

**Color Rules:**
- Stored in `%APPDATA%\PurchaseOrderBot\table_rules.json` under key `"overview"`
- Applied via `DataTable.populate()` using `_rule_matches()` (numeric + string comparison)
- Rules referencing common column names ("Fill Rate", "Days of Inv", "Runout Risk", etc.) apply to BOTH PC and SKU table views automatically
- "◈ Color Rules" button opens `ThresholdRulesDialog` with columns from BOTH tables combined

**FilterSidebar debounce + cascade:**
- All checkbox filter changes go through `_on_filter_changed()` which immediately calls `_update_dependent_filters()` (cascade) then starts the 250ms debounce timer
- `_update_dependent_filters()` blocks all checkbox signals during update to prevent re-entrancy, computes valid options for each dimension using cross-filter from `_full_fv` (filter_values DataFrame), disables and auto-unchecks options that are incompatible with current selections
- Search box changes go through `_schedule_emit()` (no cascade, just debounce)
- `_reset()` re-enables all filter items before clearing so none are left permanently disabled

**Smart Refresh:**
- `cache.py` queries `dbo.sysTableUpdates` on each refresh cycle
- Only tables with newer timestamps than saved state are reloaded
- State persisted at `%APPDATA%\PurchaseOrderBot\refresh_state.json`

**AS/400 CHAR column padding (critical — do not remove strip calls):**
- SQL Server returning data from AS/400-origin tables gives CHAR-padded strings (e.g. `'STCBROWN    '` instead of `'STCBROWN'`)
- `ITEM.ItemNumber` (aliased as `sku`) is NOT LTRIM/RTRIMmed in ITEMS_SQL — it returns with trailing spaces
- `_ORDERS.ITEM_MFGR_COLOR_PAT` (aliased as `sku` in orders) similarly has trailing spaces
- `ROLLS.ItemNumber` has the same CHAR padding issue
- **Fix applied (2026-05-06):** All loaders strip `df["sku"]` BEFORE alias map lookup and `df["base_sku"]` AFTER (defense-in-depth). CRITICAL: the strip in `load_orders()` MUST come before the alias resolution block — if it comes after, `base_sku` is set using the unstripped `sku` as fallback and `_filt()` then can't match it against clean `active_skus`, causing all orders to be dropped (avg_daily = 0).
- **OPENPO_D** is the exception: its sku is built with `LTRIM(RTRIM())` per component in SQL so it arrives pre-stripped.
- If zeros appear in Inventory/On Order/Avg Daily despite data existing in the DB, suspect CHAR padding regression — check that `str.strip()` calls are in the right ORDER in all 5 loader functions (strip sku → alias resolution → strip base_sku).

**Sidebar filters vs. load-time filters (critical design principle):**
- `compute_all()` always builds `sku_metrics` for ALL non-'1xx' items, regardless of sidebar state. It calls `_apply_item_filters(bundle.items, {})` — empty dict means only the permanent '1xx' CC exclusion applies.
- Sidebar filter selections (CC, supplier, price class, product line, search, rating) are applied ONLY in the UI display layer via `OverviewTab._filter_metrics()` → called from `apply_filters()`.
- This ensures every price class / supplier / cost centre is selectable from the sidebar and shows correct data, regardless of what filters were active when the user last clicked "Refresh Data".
- Do NOT pass sidebar filters into `_apply_item_filters()` from `compute_all()`. The `filters` param passed into `compute_all()` is kept for future use but must not narrow the items scope.


