# Inventory Control — Distribution Readme

## What this is
A standalone Windows desktop app that reads inventory and sales data from the
NRF SQL Server (`NRFVMSSQL04 / NRF_REPORTS`) and provides a dashboard for
monitoring stock turn, fill rate, problem areas, and daily POs.

## How to run
1. Unzip the `InventoryControl` folder anywhere on your PC (Desktop, Documents,
   a network share — anywhere you have read/write access).
2. Double-click **`InventoryControl.exe`** inside that folder.
3. The app connects automatically using your Windows login (Trusted Connection).

> **Important:** keep all the files in the `InventoryControl` folder together.
> The `.exe` needs the surrounding DLLs and resources to run.

## Requirements
- Windows 10 or Windows 11 (64-bit)
- **Microsoft ODBC Driver 18 for SQL Server** must be installed.
  Download (free, signed by Microsoft):
  https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server
- Network access to `NRFVMSSQL04`.
- Your Windows account must have read access to the `NRF_REPORTS` database.

## First-launch SmartScreen prompt
Because this app is internally built and not signed with a public code-signing
certificate, Windows SmartScreen may show a "Windows protected your PC" prompt
the first time you run it. Click **More info → Run anyway**. This only happens
once per PC.

## Where settings are stored
All user settings (column widths, color rules, snoozed alerts, stock-turn
targets, operator name mappings) are stored at:

```
%APPDATA%\PurchaseOrderBot\
```

Deleting that folder fully resets the app to defaults; nothing else on your
system is touched.

## Need help?
Contact the IT / data team.
