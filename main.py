"""Research Assistant desktop entry point."""

import logging
from logging.handlers import RotatingFileHandler
import argparse
from pathlib import Path
import sys

from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow
from app.config import DATABASE_PATH
from app.version import __version__

def main() -> None:
    parser = argparse.ArgumentParser(description="Research Assistant: local Zotero PDF search")
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--query", default="", help="Run an initial keyword query")
    args = parser.parse_args()
    handlers = [logging.StreamHandler()]
    try:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(DATABASE_PATH.parent / "research-assistant.log",
                        maxBytes=1_000_000, backupCount=2, encoding="utf-8"))
    except OSError:
        pass  # The window will report an unavailable application data directory.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", handlers=handlers)
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Research Assistant")
    app.setApplicationVersion(__version__)
    window = MainWindow(args.database, initial_query=args.query)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
