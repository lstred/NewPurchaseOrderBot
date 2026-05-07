# -*- mode: python ; coding: utf-8 -*-
#
# InventoryControl — onedir build
# ---------------------------------
# Why onedir (not onefile)?
#   PyInstaller onefile binaries self-extract to %TEMP% on every launch,
#   which is the #1 behavioural pattern Windows Defender / SmartScreen /
#   most enterprise EDR products flag as suspicious. A onedir build is
#   a normal folder of DLLs next to the .exe — no self-extraction,
#   dramatically fewer false positives, faster startup, and easier to
#   inspect for IT/security review.
#
# Why no UPX?
#   UPX-packed binaries are heuristically scored as malware by almost
#   every AV engine because real malware uses UPX to hide payloads.
#   Skipping UPX trades ~20 MB of disk space for clean AV scans.
#
# Why version_info.txt?
#   Embeds CompanyName / FileDescription / ProductName / Copyright into
#   the EXE's PE resource. Unsigned PyInstaller EXEs without metadata
#   trip SmartScreen "Unknown Publisher" warnings far more often.

block_cipher = None

from PyInstaller.utils.hooks import collect_submodules

# NumPy 2.x reorganised internals into numpy._core.* — PyInstaller's static
# analysis misses several of these (e.g. numpy._core._exceptions). Pull the
# whole subpackage tree so every transitive import is bundled.
_numpy_hidden = [m for m in collect_submodules('numpy')
                 if '_pyinstaller' not in m and 'tests' not in m]

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'pyodbc',
        'sqlalchemy.dialects.mssql',
        'sqlalchemy.dialects.mssql.pyodbc',
        'sqlalchemy.dialects.mssql.base',
        'sqlalchemy.pool',
        'pandas._libs.tslibs.np_datetime',
        'pandas._libs.tslibs.nattype',
        'pandas._libs.tslibs.timestamps',
        'pandas._libs.tslibs.timedeltas',
        'pandas._libs.tslibs.offsets',
        'pandas._libs.tslibs.period',
        'pandas._libs.tslibs.parsing',
        'plotly',
        'plotly.graph_objects',
        'plotly.subplots',
        'plotly.express',
        'PyQt6.QtWebEngineWidgets',
        'PyQt6.QtWebEngineCore',
        'PyQt6.QtWebChannel',
    ] + _numpy_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'scipy',
        'sklearn',
        'IPython',
        'notebook',
        'jupyter',
        'tkinter',
        'pytest',
        'unittest',
        'test',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='InventoryControl',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # AV-friendly: no UPX
    console=False,                  # GUI app, no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,                      # Add .ico path here if/when one exists
    version='version_info.txt',     # Embeds PE version resource
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,                      # AV-friendly: no UPX
    upx_exclude=[],
    name='InventoryControl',
)
