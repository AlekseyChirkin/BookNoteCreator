# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['web_book_parser.py'],
    pathex=[],
    binaries=[],
    datas=[
                ('templates', 'templates'),
        ('playwright-browsers', 'playwright-browsers'),

        ('static', 'static'),
    ],
    hiddenimports=[
        'playwright',
        'playwright.sync_api',
        'bs4',
        'lxml',
        'flask',
        'werkzeug',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='BookNoteCreator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='BookNoteCreator.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='BookNoteCreator',
)
