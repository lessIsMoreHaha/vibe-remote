# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for vibe-remote v2 single exe (IM service + Flask Web UI).

Entry point: main.py
Build:       .venv/Scripts/python.exe -m PyInstaller vibe-remote.spec --clean
Output:      dist/vibe-remote.exe
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

block_cipher = None
SPEC_DIR = Path(SPECPATH)

# --- Data files to bundle ---
datas = [
    (str(SPEC_DIR / 'vibe' / 'i18n'), 'vibe/i18n'),
    (str(SPEC_DIR / 'vibe' / 'templates'), 'vibe/templates'),
    (str(SPEC_DIR / 'config'), 'config'),
    (str(SPEC_DIR / 'core'), 'core'),
    (str(SPEC_DIR / 'modules'), 'modules'),
]

# ui/dist — only include if built assets exist
ui_dist = SPEC_DIR / 'ui' / 'dist'
if ui_dist.exists() and any(ui_dist.iterdir()):
    datas.append((str(ui_dist), 'vibe/ui/dist'))

# --- Collect entire packages that are hard to enumerate manually ---
_sdk_datas, _sdk_binaries, _sdk_hiddenimports = collect_all('claude_agent_sdk')

# Exclude bundled claude binary (217MB Linux ELF — useless on Windows, SDK falls back to PATH)
_sdk_datas = [(src, dst) for src, dst in _sdk_datas if '_bundled' not in src]
_sdk_binaries = [(src, dst) for src, dst in _sdk_binaries if '_bundled' not in src]

# mcp — collect submodules skipping mcp.cli tree (requires typer which may not be installed)
_mcp_skip = lambda name: name == 'mcp.cli' or name.startswith('mcp.cli.')
_mcp_hiddenimports = collect_submodules('mcp', filter=lambda name: not _mcp_skip(name))
_mcp_datas = collect_data_files('mcp', include_py_files=False)
_mcp_datas = [(s, d) for s, d in _mcp_datas if 'mcp/cli/' not in d.replace('\\', '/')]
_mcp_binaries = []

# --- Hidden imports ---
hiddenimports = [
    # C extensions
    'yaml._yaml',
    'yarl',
    'multidict',
    'aiohttp._helpers',
    # Flask ecosystem
    'flask',
    'jinja2',
    'markupsafe',
    'werkzeug',
    'click',
    'blinker',
    'itsdangerous',
    # IM platform SDKs
    'slack_sdk',
    'slack_sdk.socket_mode',
    'slack_sdk.socket_mode.aiohttp',
    'slack_sdk.socket_mode.request',
    'slack_sdk.socket_mode.client',
    'discord',
    'lark_oapi',
    # Claude agent SDK — collected via collect_all('claude_agent_sdk') above
    # Sentry
    'sentry_sdk',
    'sentry_sdk.integrations.flask',
    # Async HTTP
    'aiohttp',
    'aiohttp_socks',
    'python_socks',
    # Misc
    'anyio',
    'apscheduler',
    'apscheduler.schedulers.asyncio',
    'apscheduler.triggers.interval',
    'apscheduler.triggers.cron',
    'markdown_to_mrkdwn',
    'typing_extensions',
    # pycryptodome (used by some SDKs)
    'Crypto',
    'Crypto.Cipher',
    'Crypto.PublicKey',
    # jsonschema (used by config validation)
    'jsonschema',
    'jsonschema.specifications',
    'referencing',
    'rpds_py',
    # pydantic — deep transitive dep of mcp.types (used by claude_agent_sdk)
    'pydantic',
    'pydantic.deprecated',
    'pydantic.deprecated.class_validators',
    'pydantic_core',
    'pydantic_settings',
]

# --- Excludes (reduce binary size) ---
excludes = [
    'tkinter',
    'matplotlib',
    'numpy',
    'scipy',
    'pandas',
    'PIL',
    'IPython',
    'jupyter',
    'pytest',
    'setuptools',
]

a = Analysis(
    [str(SPEC_DIR / 'main.py')],
    pathex=[str(SPEC_DIR)],
    binaries=_sdk_binaries,
    datas=datas + _sdk_datas + _mcp_datas,
    hiddenimports=hiddenimports + _sdk_hiddenimports + _mcp_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='vibe-remote',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
