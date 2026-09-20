"""Shared PyInstaller entry point; dispatches by the executable filename."""

from pathlib import Path
import multiprocessing
import sys


def main() -> None:
    # Required by frozen Windows executables before Torch or another dependency
    # can create a multiprocessing helper process. Without this guard a helper
    # may re-enter the application dispatcher during shutdown.
    multiprocessing.freeze_support()
    executable = Path(sys.executable).stem.lower()
    if executable.endswith("mcp"):
        from mcp_main import main as selected_main
    elif executable.endswith("core"):
        from core_tray_main import main as selected_main
    else:
        from main import main as selected_main
    selected_main()


if __name__ == "__main__":
    main()
