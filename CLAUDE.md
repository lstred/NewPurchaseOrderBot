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

### `dbo.CLASSES` — Code Lookup Table

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
| Exclude discontinued items | `ITEM.IDISCD` is null / blank / `'0'` |
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
      tab_fillrate.py        — Fill Rate: histogram + per-SKU table
      tab_problems.py        — Problem Areas: alert cards with snooze
      tab_settings.py        — Settings: stock-turn targets at all filter levels
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

---

### 12.7 Bugs Fixed (for future AI context)

| Bug | File | Root Cause | Fix Applied |
|---|---|---|---|
| `Invalid column name 'PRCCD'` | `queries.py` | PRICE table columns have `$` prefix; referenced without it | Changed `[PRCCD]` → `[$PRCCD]`, `[LIST#]` → `[$LIST#]`, `[DESC]` → `[$DESC]` in both ITEMS_SQL and FILTER_VALUES_SQL |
| Snooze "until PO qty changes" never stuck | `store.py` | `is_snoozed()` fell through to `return False` after PO qty check | Reordered: check PO qty change first (unsnooze if changed), then check date, then return `True` for indefinite snooze |
| Timeline reorder markers on wrong day | `metrics_service.py` | `records.index(rec)` finds first match, breaks on duplicate dict values | Replaced with `enumerate(records)` |
| `QDate` imported inline via `__import__` | `main_window.py` | Leftover hack from development | Moved to proper top-level `from PyQt6.QtCore import QDate` |
| `df.get("sku_description", pd.Series(...))` length mismatch | `tab_overview.py` | `pd.Series` fallback has length 1, not len(df) | Guarded with `if "sku_description" in df.columns` |

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

