"""
validate_db.py — Verify SQL Server connection and check every table/column used by the app.

Usage:
    python validate_db.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.data.db import validate_connection, read_dataframe


CHECKS = [
    # (label, sql)
    (
        "_ORDERS — sales orders sample",
        """
        SELECT TOP 5
            [ORDER#], [LINE#I], ITEM_MFGR_COLOR_PAT, QUANTITY_ORDERED,
            UNIT_OF_MEASURE, ORDER_SHIP_DATE, INVOICE_SHIP_DATE,
            [ACCOUNT#I], BANK_NAME2, N_NOT_INVENTORY,
            ORDER_ENTRY_DATE_YYYYMMDD, DETAIL_LINE_STATUS,
            PO_ETA_DATE, [SUPPLIER#], [INVOICE#],
            ENTENDED_PRICE_NO_FUNDS, LINE_GPD_WITHOUT_FUNDS,
            ITEM_WIDTH_INCHES_IF_R, ITEM_DESC_1, ITEM_CLASS_1_DESC
        FROM dbo._ORDERS
        WHERE [ACCOUNT#I] > 1 AND N_NOT_INVENTORY = 'Y' AND QUANTITY_ORDERED > 0
        """,
    ),
    (
        "_ORDERS — PO lines (ACCOUNT#I=1)",
        """
        SELECT TOP 5 [ORDER#], [LINE#I], ITEM_MFGR_COLOR_PAT,
            QUANTITY_ORDERED, UNIT_OF_MEASURE, PO_ETA_DATE,
            [SUPPLIER#], [INVOICE#], DETAIL_LINE_STATUS
        FROM dbo._ORDERS
        WHERE [ACCOUNT#I] = 1 AND N_NOT_INVENTORY = 'Y'
        """,
    ),
    (
        "ITEM — item master",
        """
        SELECT TOP 5
            ItemNumber, IPRCCD, ICCTR, IPRODL, IMFGR, INAME,
            IPATT, [ISUPP#], IDELIV, IWIDTH, IINVEN, IIXREF, IDISCD
        FROM dbo.ITEM
        WHERE IINVEN = 'Y'
        """,
    ),
    (
        "ROLLS — physical inventory",
        """
        SELECT TOP 5
            ItemNumber, Available, RUM, [RROLL#], RLOC1, [RCODE@], RLRCTD
        FROM dbo.ROLLS
        WHERE Available > 0 AND RLOC1 != 'REM'
        """,
    ),
    (
        "OPENPO_D — pending PO detail",
        """
        SELECT TOP 5
            [D@MFGR], [D@COLO], [D@PATT], [D@QTYO], [D@QTYP],
            [D@ACCT], [D@DEL8], [D@SUPP], [D@REF#]
        FROM dbo.OPENPO_D
        WHERE [D@ACCT] = 1 AND [D@DEL8] != '#'
        """,
    ),
    (
        "PRODLINE — product lines",
        """
        SELECT TOP 5 [LPROD#], [LMFGR#], LNAME, LDELIV
        FROM dbo.PRODLINE
        """,
    ),
    (
        "PRICE — price classes",
        """
        SELECT TOP 5 [$PRCCD], [$LIST#], [$DESC]
        FROM dbo.PRICE
        WHERE [$LIST#] = 'LP'
        """,
    ),
    (
        "CLASSES — code lookup (credit types)",
        """
        SELECT TOP 5 CLCAT, CLCODE, CLDESC
        FROM dbo.CLASSES
        WHERE CLCAT = 'CC'
        """,
    ),
    (
        "ITEMSTK — item stock targets",
        """
        SELECT TOP 5 ItemNumber, JSTOCK
        FROM dbo.ITEMSTK
        """,
    ),
    (
        "_INVENTORY — inventory cost view",
        """
        SELECT TOP 5 Item, TotalCost
        FROM dbo._INVENTORY
        WHERE TotalCost > 0
        """,
    ),
    (
        "OPENIV — open receipts",
        """
        SELECT TOP 5 NREFTY, NDATE, [NPO#], NRECEI, NMFGR, NCOLOR, NPAT
        FROM dbo.OPENIV
        WHERE NREFTY = 'R'
        """,
    ),
]


def run_checks() -> bool:
    print("=" * 70)
    print("  Inventory Control — SQL Server Validation")
    print("=" * 70)

    # Step 1: Connection
    print("\n[1] Testing connection...")
    ok = validate_connection()
    if not ok:
        print("  ✗ CONNECTION FAILED — cannot reach NRFVMSSQL04 / NRF_REPORTS")
        print("    Check config_local.py or %APPDATA%\\PurchaseOrderBot\\config.json")
        return False
    print("  ✓ Connection OK")

    # Step 2: Table / column checks
    print(f"\n[2] Running {len(CHECKS)} table checks...\n")
    all_ok = True
    for label, sql in CHECKS:
        try:
            df = read_dataframe(sql)
            if df.empty:
                print(f"  ⚠ {label}: Query returned 0 rows (columns present but no data)")
            else:
                cols = ", ".join(df.columns.tolist())
                print(f"  ✓ {label}: {len(df)} row(s)")
                print(f"      Columns: {cols}")
                print(f"      Sample row: {df.iloc[0].to_dict()}")
        except Exception as exc:
            print(f"  ✗ {label}: FAILED — {exc}")
            all_ok = False
        print()

    print("=" * 70)
    if all_ok:
        print("  ✓ All checks passed.")
    else:
        print("  ✗ Some checks FAILED — see above for details.")
    print("=" * 70)
    return all_ok


if __name__ == "__main__":
    success = run_checks()
    sys.exit(0 if success else 1)
