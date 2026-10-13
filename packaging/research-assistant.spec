# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

project = Path(SPECPATH).parent
version_ns = {}
exec((project / "app" / "version.py").read_text(encoding="utf-8"), version_ns)
bundle_name = f"ResearchAssistant-{version_ns['__version__']}-windows-x64"

analysis = Analysis(
    [str(project / "bundle_main.py")],
    pathex=[str(project)], binaries=[], datas=[],
    hiddenimports=["main", "core_tray_main", "mcp_main", "app.core_service.__main__",
                   "app.external_api.mcp_server", "sentence_transformers", "faiss"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False, optimize=0,
)
pyz = PYZ(analysis.pure)

gui_exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True,
              name="ResearchAssistant", debug=False, bootloader_ignore_signals=False,
              strip=False, upx=False, console=False)
core_exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True,
               name="ResearchAssistantCore", debug=False, bootloader_ignore_signals=False,
               strip=False, upx=False, console=False)
mcp_analysis = Analysis(
    [str(project / "mcp_main.py")],
    pathex=[str(project)], binaries=[], datas=[], hiddenimports=[],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["PySide6", "torch", "sentence_transformers", "faiss"],
    noarchive=False, optimize=0,
)
mcp_pyz = PYZ(mcp_analysis.pure)
mcp_exe = EXE(mcp_pyz, mcp_analysis.scripts, mcp_analysis.binaries,
              mcp_analysis.datas, [], name="ResearchAssistantMCP",
              debug=False, bootloader_ignore_signals=False,
              strip=False, upx=False, console=True)

coll = COLLECT(gui_exe, core_exe, mcp_exe, analysis.binaries, analysis.datas,
               strip=False, upx=False, upx_exclude=[],
               name=bundle_name)
