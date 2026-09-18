"""Verify the real indexed query through Qt and save desktop evidence."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from pathlib import Path
import time

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


def main() -> None:
    """Exercise real search, clipboard and exact PDF page preview in the desktop."""
    app = QApplication([])
    window = MainWindow(initial_query="strain bandgap In2Se3")
    output = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
    output.mkdir(parents=True, exist_ok=True)
    window.show()

    def wait_idle():
        deadline = time.monotonic() + 30
        while window.busy and time.monotonic() < deadline:
            QTest.qWait(20)
        assert not window.busy, "Desktop task timed out."
        QTest.qWait(100)

    try:
        QTest.qWait(30)  # Allow the initial connection task to start.
        wait_idle()
        assert len(window.results) > 0
        assert window.current_result.zotero_key == "5VRINQRW"
        assert window.current_result.page == 5
        assert window.preview.toPlainText() == window.current_result.text
        window.copy_reference()
        assert "PDF page 5" in app.clipboard().text()
        assert window.grab().save(str(output / "search.png"))
        window.show_pdf_page()
        wait_idle()
        assert window.pdf_dialog.isVisible()
        assert window.pdf_dialog.grab().save(str(output / "source-page.png"))
        window.pdf_dialog.close()
        print(f"PASS: desktop query returned {len(window.results)} pages; first result page 5; clipboard and PDF render verified.")
        print(f"Screenshots: {output}")
    finally:
        wait_idle()
        window.close()
        app.processEvents()


if __name__ == "__main__":
    main()
