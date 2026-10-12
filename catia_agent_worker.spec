from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs


root = Path(SPECPATH)
model_asset = root / "build_assets" / "qllama-bge-small-en-v1.5.gguf"
if not model_asset.is_file():
    raise FileNotFoundError(
        "Run scripts/prepare_worker_model.py before building the worker."
    )

analysis = Analysis(
    [str(root / "worker.py")],
    pathex=[str(root)],
    binaries=collect_dynamic_libs("llama_cpp"),
    datas=[(str(model_asset), "catia_agent/assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["fastapi", "uvicorn", "fastmcp", "mcp"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="CatiaAgentWorker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)