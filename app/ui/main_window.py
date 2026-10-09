"""Windows desktop interface for local, page-level literature search."""

import html
from datetime import datetime
from pathlib import Path
import re
from threading import Event

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QUrl, QSize
from PySide6.QtGui import QAction, QDesktopServices, QPixmap, QColor, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSpinBox, QSplitter, QStackedWidget, QTextEdit,
    QToolButton, QVBoxLayout, QWidget,
)

from app.config import DATABASE_PATH, ZOTERO_FAST_API_URL
from app.evidence.models import EvidencePack, PaperEvidence
from app.external_api.mcp_schemas import FindEvidenceResult
from app.parser.pdf_parser import render_page
from app.search.database import Database
from app.search.indexer import Indexer, update_library
from app.search.hybrid_search import HybridResult
from app.search.text import normalized_with_offsets, query_terms
from app.ui.core_process_manager import UNAVAILABLE, CoreMonitor, CoreProcessManager
from app.ui.core_search_adapter import CoreSearchAdapter
from app.ui.worker import Worker
from app.zotero.client import ZoteroClient

# The desktop GUI's Search/Evidence Pack/Evidence Report modes are all
# clients of the persistent Research Assistant Core Service (V0.6) - see
# app.ui.core_search_adapter.CoreSearchAdapter and
# app.ui.core_process_manager.CoreProcessManager. The GUI no longer
# constructs app.search.desktop_search.DesktopSearchService (retired from
# the production GUI path, kept only for its own tests - see that
# module's docstring).


def plain_title(value: str) -> str:
    """Remove Zotero's inline title markup for plain-text desktop labels."""
    return html.unescape(re.sub(r"<[^>]+>", "", value))


class MainWindow(QMainWindow):
    requested = Signal(str, object)

    def __init__(self, database_path: Path = DATABASE_PATH, *, auto_connect: bool = True,
                 auto_connect_core: bool = True, initial_query: str = "") -> None:
        super().__init__()
        self.database_path = database_path
        self.results: list[HybridResult] = []
        self.current_result: HybridResult | None = None
        self.busy = False
        self.closing = False
        self.cancel_event = Event()
        self.search_offset = 0
        self.last_search = None
        self.evidence_pack: EvidencePack | None = None
        self.evidence_checks: list[tuple[QCheckBox, PaperEvidence]] = []
        self.evidence_report: FindEvidenceResult | None = None
        # The Core Service owns retrieval/evidence resources (V0.6); the GUI
        # only holds a thin CoreSearchAdapter once the Core is known to be
        # reachable, and never falls back to a local heavy-model path.
        self.search_service: CoreSearchAdapter | None = None
        self.core_state = "STARTING"
        self.core_status_payload: dict = {}
        self.core_manager = CoreProcessManager()
        self.core_monitor = CoreMonitor(self.core_manager)
        self.core_monitor.state_changed.connect(self._core_state_changed)
        self.setWindowTitle("Research Assistant")
        self.resize(1280, 850)
        self.setMinimumSize(980, 660)
        self._build_ui()
        self.query.setText(initial_query)
        self.thread = QThread(self)
        self.worker = Worker()
        self.worker.moveToThread(self.thread)
        self.requested.connect(self.worker.run)
        self.worker.finished.connect(self._finished)
        self.worker.failed.connect(self._failed)
        self.worker.progress.connect(self._progress)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.start()
        self._load_cached()
        self._update_search_availability()
        if auto_connect:
            QTimer.singleShot(0, self.refresh_zotero)
        if auto_connect_core:
            QTimer.singleShot(0, self.core_monitor.start)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(26, 22, 26, 18)
        layout.setSpacing(12)
        header = QHBoxLayout()
        name = QLabel("Research Assistant")
        name.setStyleSheet("font-size: 27px; font-weight: 700; color: #162d45")
        header.addWidget(name)
        header.addStretch()
        self.status = QLabel("Zotero · 尚未连接")
        self.status.setObjectName("status")
        header.addWidget(self.status)
        self.semantic_status = QLabel("Semantic index · checking")
        self.semantic_status.setObjectName("semanticStatus")
        header.addWidget(self.semantic_status)
        self.core_status = QLabel("Core · checking")
        self.core_status.setObjectName("coreStatus")
        self.core_status.setToolTip("Research Assistant Core Service: the persistent process that owns "
                                    "retrieval and the semantic model. Search and Evidence modes need it.")
        header.addWidget(self.core_status)
        layout.addLayout(header)
        subtitle = QLabel("本地文献检索  /  每条结果都有原文和 PDF 页码")
        subtitle.setStyleSheet("color: #60758a")
        layout.addWidget(subtitle)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("文献集合"))
        self.collection = QComboBox()
        self.collection.setMinimumWidth(260)
        self.collection.addItem("全部已索引文献 / 索引整个个人文库", None)
        source_row.addWidget(self.collection, 1)
        self.refresh_button = QPushButton("刷新 Zotero")
        self.refresh_button.clicked.connect(self.refresh_zotero)
        source_row.addWidget(self.refresh_button)
        self.index_button = QPushButton("增量更新索引")
        self.index_button.setToolTip("只索引新增或变更的条目；未变更的 PDF 不重复解析。")
        self.index_button.clicked.connect(self.start_index)
        source_row.addWidget(self.index_button)
        self.rebuild_button = QPushButton("Full Rebuild")
        self.rebuild_button.setToolTip("重新解析整个个人文库的全部 PDF，不受集合选择限制。仅更新本地索引，Zotero 保持只读。")
        self.rebuild_button.clicked.connect(self.start_rebuild)
        source_row.addWidget(self.rebuild_button)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_event.set)
        source_row.addWidget(self.cancel_button)
        layout.addLayout(source_row)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("模式"))
        self.app_mode = QComboBox()
        self.app_mode.addItem("Search", "search")
        self.app_mode.addItem("Evidence Pack", "evidence")
        self.app_mode.addItem("Evidence Report", "evidence_report")
        self.app_mode.currentIndexChanged.connect(self._app_mode_changed)
        search_row.addWidget(self.app_mode)
        search_row.addWidget(QLabel("搜索模式"))
        self.search_mode_label = search_row.itemAt(search_row.count() - 1).widget()
        self.search_mode = QComboBox()
        self.search_mode.addItem("Hybrid", "hybrid")
        self.search_mode.addItem("Keyword", "keyword")
        self.search_mode.addItem("Semantic", "semantic")
        self.search_mode.setCurrentIndex(0)
        self.search_mode.currentIndexChanged.connect(self._search_mode_changed)
        search_row.addWidget(self.search_mode)
        self.query_label = QLabel("Query")
        search_row.addWidget(self.query_label)
        self.query = QLineEdit()
        self.query.setPlaceholderText("输入关键词，例如 strain bandgap In2Se3")
        self.query.setClearButtonEnabled(True)
        self.query.returnPressed.connect(self.start_search)
        self.query.setMinimumHeight(42)
        search_row.addWidget(self.query, 1)
        self.search_button = QPushButton("搜索原文")
        self.search_button.setObjectName("primary")
        self.search_button.setMinimumHeight(42)
        self.search_button.clicked.connect(self.start_search)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)
        filters = QHBoxLayout()
        self.year = QLineEdit()
        self.year.setPlaceholderText("年份或范围（如 2020-2024）")
        self.year.setMaximumWidth(140)
        self.journal = QLineEdit()
        self.journal.setPlaceholderText("期刊全名（可选）")
        filters.addWidget(self.year)
        filters.addWidget(self.journal, 1)
        self.search_help = QLabel()
        filters.addWidget(self.search_help)
        layout.addLayout(filters)
        self.evidence_settings = QWidget()
        settings = QHBoxLayout(self.evidence_settings)
        settings.setContentsMargins(0, 0, 0, 0)
        settings.addWidget(QLabel("Evidence Pack 设置"))
        settings.addWidget(QLabel("最多论文"))
        self.max_papers = QSpinBox()
        self.max_papers.setRange(1, 20)
        self.max_papers.setValue(8)
        settings.addWidget(self.max_papers)
        settings.addWidget(QLabel("每篇段落"))
        self.max_passages = QSpinBox()
        self.max_passages.setRange(1, 5)
        self.max_passages.setValue(3)
        settings.addWidget(self.max_passages)
        settings.addWidget(QLabel("候选池"))
        self.candidate_pool = QSpinBox()
        self.candidate_pool.setRange(20, 500)
        self.candidate_pool.setValue(50)
        settings.addWidget(self.candidate_pool)
        settings.addStretch()
        layout.addWidget(self.evidence_settings)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        self.index_counts = QLabel("索引更新：尚未运行")
        self.index_counts.setWordWrap(True)
        self.index_counts.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.index_counts)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        self.results_label = QLabel("输入关键词，查找已索引的原文")
        left_layout.addWidget(self.results_label)
        self.result_list = QListWidget()
        self.result_list.setSpacing(7)
        self.result_list.currentRowChanged.connect(self.select_result)
        left_layout.addWidget(self.result_list)
        self.more_button = QPushButton("加载更多结果")
        self.more_button.clicked.connect(self.load_more)
        self.more_button.hide()
        left_layout.addWidget(self.more_button)
        self.build_evidence_button = QPushButton("使用当前查询构建 Evidence Pack")
        self.build_evidence_button.clicked.connect(self.use_query_for_evidence)
        left_layout.addWidget(self.build_evidence_button)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        self.preview_title = QLabel("原文预览")
        self.preview_title.setWordWrap(True)
        self.preview_title.setTextFormat(Qt.TextFormat.PlainText)
        self.preview_title.setStyleSheet("font-weight: 600; font-size: 15px")
        right_layout.addWidget(self.preview_title)
        self.preview_meta = QLabel("选择左侧结果，查看完整页原文。页码从 PDF 第 1 页开始计数。")
        self.preview_meta.setTextFormat(Qt.TextFormat.PlainText)
        self.preview_meta.setWordWrap(True)
        self.preview_meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right_layout.addWidget(self.preview_meta)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("此处显示从 PDF 提取的完整页文字，保留原始内容。")
        right_layout.addWidget(self.preview, 1)
        actions = QHBoxLayout()
        self.copy_text = QPushButton("复制原文")
        self.copy_text.clicked.connect(self.copy_source)
        self.copy_citation = QPushButton("复制引用信息")
        self.copy_citation.clicked.connect(self.copy_reference)
        self.view_pdf = QPushButton("查看 PDF 原页")
        self.view_pdf.clicked.connect(self.show_pdf_page)
        self.open_pdf = QPushButton("打开文件")
        self.open_pdf.clicked.connect(self.open_source)
        for button in (self.copy_text, self.copy_citation, self.view_pdf, self.open_pdf):
            button.setEnabled(False)
            actions.addWidget(button)
        right_layout.addLayout(actions)
        splitter.addWidget(right)
        splitter.setSizes([610, 590])
        self.search_view = splitter

        self.evidence_view = QWidget()
        evidence_layout = QVBoxLayout(self.evidence_view)
        evidence_header = QHBoxLayout()
        self.evidence_summary = QLabel("输入研究问题或科学主张，然后查找 Candidate Evidence。")
        evidence_header.addWidget(self.evidence_summary, 1)
        self.copy_pack_button = QPushButton("Copy Evidence Pack")
        self.copy_pack_button.clicked.connect(self.copy_evidence_pack)
        evidence_header.addWidget(self.copy_pack_button)
        self.copy_ai_button = QPushButton("Copy for AI")
        self.copy_ai_button.clicked.connect(self.copy_for_ai)
        evidence_header.addWidget(self.copy_ai_button)
        self.export_button = QToolButton()
        self.export_button.setText("Export")
        self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        export_menu = QMenu(self.export_button)
        markdown_action = QAction("Markdown", self.export_button)
        markdown_action.triggered.connect(lambda: self.export_evidence("markdown"))
        json_action = QAction("JSON", self.export_button)
        json_action.triggered.connect(lambda: self.export_evidence("json"))
        export_menu.addActions([markdown_action, json_action])
        self.export_button.setMenu(export_menu)
        evidence_header.addWidget(self.export_button)
        for control in (self.copy_pack_button, self.copy_ai_button, self.export_button):
            control.setEnabled(False)
        evidence_layout.addLayout(evidence_header)
        self.evidence_scroll = QScrollArea()
        self.evidence_scroll.setWidgetResizable(True)
        self.evidence_content = QWidget()
        self.evidence_cards = QVBoxLayout(self.evidence_content)
        self.evidence_cards.addStretch()
        self.evidence_scroll.setWidget(self.evidence_content)
        evidence_layout.addWidget(self.evidence_scroll, 1)

        self.evidence_report_view = QWidget()
        report_layout = QVBoxLayout(self.evidence_report_view)
        report_header = QHBoxLayout()
        self.evidence_report_summary = QLabel(
            "输入研究问题或科学主张，查看按 DIRECT/SECONDARY/LIMITING/CONTEXTUAL/UNCERTAIN 分类的 Evidence Report。")
        self.evidence_report_summary.setWordWrap(True)
        report_header.addWidget(self.evidence_report_summary, 1)
        self.copy_report_button = QPushButton("Copy Evidence Report")
        self.copy_report_button.setEnabled(False)
        self.copy_report_button.clicked.connect(self.copy_evidence_report)
        report_header.addWidget(self.copy_report_button)
        report_layout.addLayout(report_header)
        self.evidence_report_text = QPlainTextEdit()
        self.evidence_report_text.setReadOnly(True)
        self.evidence_report_text.setPlaceholderText(
            "结构化 Evidence Report：分类、原文段落、PDF 页码、Zotero key 和 citation-trace 状态。")
        report_layout.addWidget(self.evidence_report_text, 1)

        self.result_stack = QStackedWidget()
        self.result_stack.addWidget(self.search_view)
        self.result_stack.addWidget(self.evidence_view)
        self.result_stack.addWidget(self.evidence_report_view)
        layout.addWidget(self.result_stack, 1)
        self.message = QLabel("就绪")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setStyleSheet("color: #526b82; font-size: 12px")
        layout.addWidget(self.message)
        self.setStyleSheet("""
            QMainWindow { background: #f4f7fa; }
            QWidget { font-family: 'Segoe UI', 'Microsoft YaHei UI'; font-size: 13px; color: #23384b; }
            QLineEdit, QComboBox, QPlainTextEdit, QListWidget {
                background: white; border: 1px solid #d4dee8; border-radius: 6px; padding: 7px; }
            QPushButton { padding: 8px 13px; border: 1px solid #c6d3df; border-radius: 5px; background: #fff; }
            QPushButton:hover { background: #eaf2f9; }
            QPushButton:disabled { color: #8b99a6; background: #edf1f4; }
            QPushButton#primary { color: white; background: #176b91; border-color: #176b91; }
            QPushButton#primary:hover { background: #105674; }
            QListWidget::item:selected { background: #e3f1fa; border-radius: 5px; }
            QProgressBar::chunk { background: #268cac; }
            QLabel#status { color: #176b91; font-weight: 600; }
            QLabel#semanticStatus { color: #526b82; }
            QLabel#coreStatus { color: #526b82; }
        """)
        self._search_mode_changed()
        self._app_mode_changed()

    def _load_cached(self) -> None:
        try:
            with Database(self.database_path) as db:
                self._set_collections(db.collections())
                stats = db.stats()
            self.message.setText(f"本地索引：{stats['papers']} 篇文献 · {stats['pages']} 页。Zotero 关闭时仍可搜索。")
        except Exception as exc:
            self.message.setText(str(exc))
        self._refresh_semantic_status()

    def _refresh_semantic_status(self) -> None:
        """Reflect the Core's own last-known readiness (V0.6) - the GUI does not
        check semantic index files itself; the Core is the one source of truth."""
        if "semantic_index_ready" not in self.core_status_payload:
            self.semantic_status.setText("Semantic index · checking")
        elif self.core_status_payload["semantic_index_ready"]:
            self.semantic_status.setText("Semantic index · Ready")
        else:
            self.semantic_status.setText("Semantic index · Not built")

    def _core_state_changed(self, state: str, status: dict) -> None:
        """Receive a lifecycle update from CoreMonitor (runs on a background
        thread; Qt queues this call onto the GUI thread automatically)."""
        self.core_state = state
        self.core_status_payload = status
        self._refresh_semantic_status()
        if state == UNAVAILABLE:
            self.core_status.setText("Core · unavailable")
            self.message.setText("Research Assistant Core is not running and could not be started. "
                                 "Search and Evidence modes are unavailable until it is.")
        elif state == "STARTING":
            self.core_status.setText("Core · starting")
        elif state == "LOADING":
            self.core_status.setText("Core · preparing semantic model")
            self.message.setText("Research Assistant is preparing the semantic model...")
        elif state == "READY":
            paper_count = status.get("paper_count")
            self.core_status.setText(f"Core · ready ({paper_count} papers)" if paper_count is not None
                                     else "Core · ready")
            if self.search_service is None and self.core_manager.base_url:
                self.search_service = CoreSearchAdapter(self.core_manager.base_url)
            if self.message.text() == "Research Assistant is preparing the semantic model...":
                self.message.setText("就绪")
        elif state == "ERROR":
            self.core_status.setText("Core · error")
            self.message.setText(
                f"Research Assistant Core failed to initialize: {status.get('error', 'unknown error')}")
        self._update_search_availability()

    def _update_search_availability(self) -> None:
        """Gate the search/evidence action on Core readiness (V0.6, section 9) -
        never on the busy-lock alone, so the reason stays visible."""
        ready = self.core_state == "READY"
        self.search_button.setEnabled(ready and not self.busy)
        self.build_evidence_button.setEnabled(ready and not self.busy)

    def _search_mode_changed(self) -> None:
        descriptions = {
            "keyword": "精确关键词检索 · PDF 原文",
            "semantic": "语义检索 · 查找概念相关段落",
            "hybrid": "混合检索 · 结合关键词与语义相似度",
        }
        self.search_help.setText(f"{descriptions[self.search_mode.currentData()]} · 原始 PDF 文本 · 无 AI 改写")

    def _app_mode_changed(self) -> None:
        mode = self.app_mode.currentData()
        stack_index = {"search": 0, "evidence": 1, "evidence_report": 2}[mode]
        self.result_stack.setCurrentIndex(stack_index)
        is_search = mode == "search"
        self.search_mode.setVisible(is_search)
        self.search_mode_label.setVisible(is_search)
        self.evidence_settings.setVisible(mode == "evidence")
        self.build_evidence_button.setVisible(is_search)
        self.query.setPlaceholderText("输入关键词，例如 strain bandgap In2Se3" if is_search else
                                      "Curvature-induced bandgap modulation is mainly mediated by local strain.")
        self.query_label.setText("Query" if is_search else "Research question or scientific claim")
        self.search_button.setText("搜索原文" if is_search else "Find Evidence")
        if is_search:
            self._search_mode_changed()
        elif mode == "evidence":
            self.search_help.setText("Candidate Evidence · 原始 PDF 文本 · 无 AI 改写或科学判断")
        else:
            self.search_help.setText("Evidence Report · classified, source-traced evidence · 原始 PDF 文本")

    def use_query_for_evidence(self) -> None:
        self.app_mode.setCurrentIndex(self.app_mode.findData("evidence"))
        if self.query.text().strip():
            self.start_search()

    def _set_collections(self, collections) -> None:
        selected = self.collection.currentData()
        self.collection.clear()
        self.collection.addItem("全部已索引文献 / 索引整个个人文库", None)
        lookup = {c.key: c for c in collections}
        for collection in collections:
            names, parent, seen = [collection.name], collection.parent_key, {collection.key}
            while parent in lookup and parent not in seen:
                seen.add(parent)
                names.insert(0, lookup[parent].name)
                parent = lookup[parent].parent_key
            self.collection.addItem(" / ".join(names), collection.key)
        index = self.collection.findData(selected)
        self.collection.setCurrentIndex(max(index, 0))

    def _submit(self, name: str, task) -> None:
        if self.busy:
            return
        self.busy = True
        self.cancel_event.clear()
        for control in (self.index_button, self.rebuild_button, self.refresh_button,
                        self.collection, self.app_mode, self.search_mode, self.year, self.journal,
                        self.max_papers, self.max_passages, self.candidate_pool,
                        self.more_button, self.view_pdf, self.copy_pack_button,
                        self.copy_ai_button, self.export_button):
            control.setEnabled(False)
        self._update_search_availability()
        self.cancel_button.setEnabled(name == "index")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        self.requested.emit(name, task)

    def _idle(self) -> None:
        self.busy = False
        for control in (self.index_button, self.rebuild_button, self.refresh_button,
                        self.collection, self.app_mode, self.search_mode, self.year, self.journal,
                        self.max_papers, self.max_passages, self.candidate_pool,
                        self.more_button):
            control.setEnabled(True)
        self._update_search_availability()
        self.view_pdf.setEnabled(self.current_result is not None)
        self.cancel_button.setEnabled(False)
        self.progress_bar.hide()
        if self.closing:
            QTimer.singleShot(0, self.close)

    def refresh_zotero(self) -> None:
        """Refresh status and collections without blocking the desktop."""
        path = self.database_path
        def task(progress):
            with ZoteroClient(ZOTERO_FAST_API_URL) as client:
                client.check_connection()
                collections = client.collections()
                with Database(path) as db:
                    db.bind_source(client.source_identity)
                    db.save_collections(collections)
                return collections
        self._submit("connect", task)

    def start_index(self) -> None:
        """Index the selected collection or entire personal library."""
        key, path, cancel = self.collection.currentData(), self.database_path, self.cancel_event
        if self.busy:
            return
        self.index_counts.setText("Incremental update: 0 indexed, 0 skipped, 0 updated, 0 failed")
        self._submit("index", lambda progress: update_library(
            lambda: Indexer(path).run(key, progress=progress, cancel=cancel), path,
            progress=progress, cancel=cancel))

    def start_rebuild(self) -> None:
        """Explicit whole-library re-extraction, separate from incremental update."""
        if self.busy:
            return
        path, cancel = self.database_path, self.cancel_event
        self.index_counts.setText("Full Rebuild · entire personal library: 0 indexed, 0 skipped, 0 updated, 0 failed")
        self._submit("index", lambda progress: update_library(
            lambda: Indexer(path).rebuild(progress=progress, cancel=cancel), path,
            progress=progress, cancel=cancel))

    def start_search(self) -> None:
        """Search through the selected unified backend mode without blocking Qt."""
        if self.busy:
            return
        if self.core_state != "READY":
            self.message.setText("Research Assistant Core is still initializing; please wait.")
            return
        app_mode = self.app_mode.currentData()
        if app_mode == "evidence":
            self.start_evidence_pack()
            return
        if app_mode == "evidence_report":
            self.start_evidence_report()
            return
        query = self.query.text().strip()
        try:
            terms = query_terms(query)
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        if not terms:
            self.message.setText("请输入至少一个关键词。")
            return
        mode = self.search_mode.currentData()
        try:
            year_min, year_max = self._year_range(self.year.text().strip())
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        self.search_offset = 0
        self.last_search = (query, mode, self.collection.currentData(), year_min, year_max,
                            self.journal.text().strip())
        self.message.setText("Searching...")
        self._search_batch()

    def start_evidence_pack(self) -> None:
        query = self.query.text().strip()
        if not query:
            self.message.setText("请输入研究问题或科学主张。")
            return
        try:
            year_min, year_max = self._year_range(self.year.text().strip())
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        options = dict(collection=self.collection.currentData(), year_min=year_min,
                       year_max=year_max, journal=self.journal.text().strip() or None,
                       max_papers=self.max_papers.value(),
                       max_passages_per_paper=self.max_passages.value(),
                       candidate_pool=self.candidate_pool.value())
        self.message.setText("Building evidence pack...")
        self._submit("evidence", lambda progress: self.search_service.build_evidence_pack(query, **options))

    def start_evidence_report(self) -> None:
        """Build the V0.3+ Evidence Report via the Core's unchanged /find_evidence."""
        query = self.query.text().strip()
        if not query:
            self.message.setText("请输入研究问题或科学主张。")
            return
        self.message.setText("Building evidence report...")
        self._submit("evidence_report", lambda progress: self.search_service.find_evidence(query, top_k=20))

    @staticmethod
    def _year_range(value: str) -> tuple[int | None, int | None]:
        if not value:
            return None, None
        match = re.fullmatch(r"\s*(\d{4})(?:\s*[-–]\s*(\d{4}))?\s*", value)
        if not match:
            raise ValueError("年份请输入 YYYY 或 YYYY-YYYY。")
        lower = int(match.group(1))
        upper = int(match.group(2) or lower)
        if lower > upper:
            raise ValueError("年份范围的起始年份不能晚于结束年份。")
        return lower, upper

    def load_more(self) -> None:
        if self.last_search and not self.busy:
            self.search_offset = len(self.results)
            self._search_batch()

    def _search_batch(self) -> None:
        query, mode, collection, year_min, year_max, journal = self.last_search
        self._submit("search", lambda progress: self.search_service.search(
            query, mode=mode, top_k=40, collection=collection, year_min=year_min,
            year_max=year_max, journal=journal or None,
            max_chunks_per_paper=2 if mode in {"semantic", "hybrid"} else 40))

    def _progress(self, message: str) -> None:
        self.message.setText(message)
        if "Progress:" in message:
            self.index_counts.setText("Progress:" + message.rsplit("Progress:", 1)[1])

    def _finished(self, name: str, value) -> None:
        self._idle()
        if name == "connect":
            self.status.setText("Zotero · 已连接（只读）")
            self._set_collections(value)
            self.message.setText(f"已读取 {len(value)} 个集合。可搜索现有索引，或选择集合更新索引。")
            if self.query.text().strip() and not self.closing:
                self.start_search()
        elif name == "index":
            self._load_cached()
            self.index_counts.setText(value.describe())
            self.message.setText(value.describe())
            self.status.setText("Zotero · 已连接（只读）")
            if self.query.text().strip() and not self.closing:
                self.start_search()
        elif name == "search":
            if self.search_offset == 0:
                self.results.clear()
                self.result_list.clear()
                self.select_result(-1)
            self.results.extend(value)
            for result in value:
                self._add_result(result)
            mode = self.last_search[1]
            labels = {"keyword": "keyword", "semantic": "semantic", "hybrid": "hybrid"}
            self.results_label.setText(f"{len(self.results)} {labels[mode]} matches · 按相关性排序")
            self.more_button.hide()
            if self.results:
                self.message.setText("原始 PDF 文本 · 页码为 PDF 物理页码；点击「查看 PDF 原页」核对。")
            elif mode == "keyword":
                self.message.setText("没有精确关键词同页匹配。可减少关键词或更新索引。")
            else:
                self.message.setText("没有找到相关段落。可调整查询或更新语义索引。")
            if self.results and self.result_list.currentRow() < 0:
                self.result_list.setCurrentRow(0)
        elif name == "evidence":
            self.evidence_pack = value
            self._render_evidence_pack(value)
            for control in (self.copy_pack_button, self.copy_ai_button, self.export_button):
                control.setEnabled(bool(value.papers))
            if value.papers:
                self.evidence_summary.setText(
                    f"Candidate Evidence · {len(value.papers)} 篇论文 · "
                    f"{sum(len(p.passages) for p in value.papers)} 个原文段落")
                self.message.setText("Evidence Pack 已生成。请勾选要复制或导出的论文。")
            else:
                self.evidence_summary.setText("No relevant literature found.")
                self.message.setText("No relevant literature found. 请检查集合过滤或更新语义索引。")
        elif name == "evidence_report":
            self.evidence_report = value
            self._render_evidence_report(value)
            self.copy_report_button.setEnabled(bool(value.evidence))
            if value.evidence:
                classes = sorted({item.evidence_class for item in value.evidence})
                self.evidence_report_summary.setText(
                    f"Evidence Report · {value.returned_count} of {value.total_classified_count} items · "
                    f"classes: {', '.join(classes)}")
                self.message.setText("Evidence Report 已生成。")
            else:
                self.evidence_report_summary.setText("No relevant literature found.")
                self.message.setText("No relevant literature found. 请检查查询或更新语义索引。")
        elif name == "render":
            title, page, png = value
            dialog = QDialog(self)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            dialog.setWindowTitle(f"PDF 第 {page} 页 · {plain_title(title)}")
            dialog.resize(940, 820)
            layout = QVBoxLayout(dialog)
            label = QLabel(f"PDF 物理页码：{page} · 原文件只读预览")
            layout.addWidget(label)
            scroll = QScrollArea()
            picture = QLabel()
            pixmap = QPixmap()
            pixmap.loadFromData(png)
            picture.setPixmap(pixmap)
            scroll.setWidget(picture)
            layout.addWidget(scroll)
            dialog.show()
            self.pdf_dialog = dialog

    def _clear_evidence_cards(self) -> None:
        while self.evidence_cards.count():
            item = self.evidence_cards.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.evidence_checks.clear()

    def _render_evidence_pack(self, pack: EvidencePack) -> None:
        self._clear_evidence_cards()
        if not pack.papers:
            empty = QLabel("No relevant literature found.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.evidence_cards.addWidget(empty)
            self.evidence_cards.addStretch()
            return
        for index, paper in enumerate(pack.papers):
            card = QWidget()
            card.setStyleSheet("background:white; border:1px solid #d4dee8; border-radius:7px;")
            card_layout = QVBoxLayout(card)
            include = QCheckBox(plain_title(paper.title))
            include.setChecked(True)
            include.setStyleSheet("font-weight:600; font-size:15px; border:none")
            card_layout.addWidget(include)
            self.evidence_checks.append((include, paper))
            relevance = ("Highly relevant candidate" if index < 2 else
                         "Relevant candidate" if index < 5 else "Related candidate")
            details = QLabel(f"{paper.journal or '期刊未提供'} · {paper.year or '年份未提供'}\n"
                             f"Authors: {'; '.join(paper.authors) or '未提供'}\n"
                             f"DOI: {paper.doi or '未提供'} · {relevance}")
            details.setTextFormat(Qt.TextFormat.PlainText)
            details.setWordWrap(True)
            details.setStyleSheet("color:#526b82; border:none")
            card_layout.addWidget(details)
            for number, passage in enumerate(paper.passages, 1):
                heading = QLabel(f"Candidate passage {number} · PDF 第 {passage.physical_pdf_page} 页")
                heading.setStyleSheet("font-weight:600; color:#176b91; border:none")
                card_layout.addWidget(heading)
                source = QPlainTextEdit()
                source.setReadOnly(True)
                source.setPlainText(passage.original_text)
                source.setMaximumHeight(145)
                card_layout.addWidget(source)
                actions = QHBoxLayout()
                copy_button = QPushButton("Copy passage")
                copy_button.clicked.connect(lambda checked=False, text=passage.original_text:
                                            QApplication.clipboard().setText(text))
                open_button = QPushButton("Open PDF")
                open_button.clicked.connect(lambda checked=False, p=passage: self.open_evidence_pdf(p))
                actions.addWidget(copy_button)
                actions.addWidget(open_button)
                actions.addStretch()
                card_layout.addLayout(actions)
            self.evidence_cards.addWidget(card)
        self.evidence_cards.addStretch()

    _EVIDENCE_CLASS_ORDER = ("DIRECT", "SECONDARY", "LIMITING", "CONTEXTUAL", "UNCERTAIN")

    def _render_evidence_report(self, result: FindEvidenceResult) -> None:
        """A readable grouped text report - no card redesign (V0.6, section 13)."""
        self.evidence_report_text.setPlainText(self._format_evidence_report(result))

    @classmethod
    def _format_evidence_report(cls, result: FindEvidenceResult) -> str:
        groups: dict[str, list] = {name: [] for name in cls._EVIDENCE_CLASS_ORDER}
        for item in result.evidence:
            groups.setdefault(item.evidence_class, []).append(item)
        lines = [
            f"Evidence Report for: {result.query}",
            f"{result.returned_count} of {result.total_classified_count} classified items shown "
            f"({result.retrieval_summary.candidate_count} candidates, "
            f"{result.retrieval_summary.duplicate_papers_merged} paper duplicates merged, "
            f"{result.retrieval_summary.duplicate_passages_removed} duplicate passages removed)",
            "",
        ]
        for evidence_class in cls._EVIDENCE_CLASS_ORDER:
            items = groups.get(evidence_class) or []
            if not items:
                continue
            lines.append(f"=== {evidence_class} ({len(items)}) ===")
            lines.append("")
            for item in items:
                paper = item.paper
                lines.append(plain_title(paper.title))
                lines.append(f"{paper.journal or 'journal not provided'} · {paper.year or 'year not provided'} "
                             f"· DOI: {paper.doi or 'not provided'}")
                lines.append(f"Zotero: {paper.zotero_item_key} · PDF page {item.passage.pdf_page}")
                lines.append(f"Rationale: {item.rationale}")
                for trace in item.citation_traces:
                    resolved = f" -> {plain_title(trace.resolved_paper.title)}" if trace.resolved_paper else ""
                    lines.append(f"Citation trace {trace.citation_marker}: "
                                 f"{trace.resolution_status}{resolved}")
                lines.append("")
                lines.append(item.passage.text)
                lines.append("")
                lines.append("-" * 60)
                lines.append("")
        if not any(groups.values()):
            lines.append("No relevant literature found.")
        return "\n".join(lines)

    def copy_evidence_report(self) -> None:
        if self.evidence_report is None:
            self.message.setText("请先生成 Evidence Report。")
            return
        QApplication.clipboard().setText(self.evidence_report_text.toPlainText())
        self.message.setText("已复制 Evidence Report。")

    def _selected_evidence_pack(self) -> EvidencePack | None:
        if not self.evidence_pack:
            self.message.setText("请先生成 Evidence Pack。")
            return None
        papers = tuple(paper for checkbox, paper in self.evidence_checks if checkbox.isChecked())
        if not papers:
            self.message.setText("No papers selected for export.")
            return None
        return EvidencePack(self.evidence_pack.query, papers, self.evidence_pack.candidate_count,
                            self.evidence_pack.duplicate_passages_removed)

    def copy_evidence_pack(self) -> None:
        pack = self._selected_evidence_pack()
        if pack:
            QApplication.clipboard().setText(pack.to_markdown())
            self.message.setText(f"已复制 {len(pack.papers)} 篇论文的 Evidence Pack。")

    def copy_for_ai(self) -> None:
        pack = self._selected_evidence_pack()
        if pack:
            QApplication.clipboard().setText(pack.to_ai_markdown())
            self.message.setText(f"已复制供 AI 使用的材料和提示，共 {len(pack.papers)} 篇论文。")

    def export_evidence(self, format_name: str) -> None:
        pack = self._selected_evidence_pack()
        if not pack:
            return
        suffix = ".md" if format_name == "markdown" else ".json"
        suggested = f"evidence_pack_{datetime.now():%Y%m%d_%H%M%S}{suffix}"
        filter_name = "Markdown (*.md)" if suffix == ".md" else "JSON (*.json)"
        filename, _ = QFileDialog.getSaveFileName(self, "Export Evidence Pack", suggested, filter_name)
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != suffix:
            path = path.with_suffix(suffix)
        content = pack.to_markdown() if suffix == ".md" else pack.to_json()
        try:
            path.write_text(content, encoding="utf-8")
            self.message.setText(f"Evidence Pack 已导出：{path}")
        except OSError as exc:
            self.message.setText(f"Evidence Pack 导出失败：{exc}")

    def open_evidence_pdf(self, passage) -> None:
        path = Path(passage.pdf_path)
        if not path.is_file():
            self.message.setText("PDF 文件已移动或不在本机。请更新索引后重试。")
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.message.setText("无法启动默认 PDF 阅读器。")

    def _failed(self, name: str, message: str) -> None:
        self._idle()
        if name == "connect":
            self.status.setText("Zotero · 未连接；本地搜索可用")
        if name == "evidence":
            self.message.setText(f"Evidence pack generation failed: {message}")
            for control in (self.copy_pack_button, self.copy_ai_button, self.export_button):
                control.setEnabled(bool(self.evidence_pack and self.evidence_pack.papers))
        elif name == "evidence_report":
            self.message.setText(f"Evidence report generation failed: {message}")
            self.copy_report_button.setEnabled(bool(self.evidence_report and self.evidence_report.evidence))
        elif name == "search" and "Semantic index" in message:
            self.message.setText("Semantic index has not been built yet. Run an index update; it also builds the semantic index.")
            self._refresh_semantic_status()
        else:
            self.message.setText(message)
        if name == "connect" and self.query.text().strip() and not self.closing:
            self.start_search()

    def _add_result(self, result: HybridResult) -> None:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)
        title = QLabel(plain_title(result.title))
        title.setWordWrap(True)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setStyleSheet("font-weight: 600; font-size: 14px")
        layout.addWidget(title)
        meta = QLabel(f"PDF 第 {result.page} 页  ·  {result.year or '年份未提供'}  ·  {result.journal}")
        meta.setTextFormat(Qt.TextFormat.PlainText)
        meta.setWordWrap(True)
        meta.setStyleSheet("color: #176b91")
        layout.addWidget(meta)
        snippet = QLabel(" ".join(result.snippet.split()))
        snippet.setTextFormat(Qt.TextFormat.PlainText)
        snippet.setWordWrap(True)
        snippet.setMaximumHeight(76)
        snippet.setStyleSheet("color: #536879")
        layout.addWidget(snippet)
        doi = QLabel(f"DOI: {result.doi or '未提供'}")
        doi.setTextFormat(Qt.TextFormat.PlainText)
        doi.setWordWrap(True)
        doi.setStyleSheet("font-size: 11px; color: #60758a")
        layout.addWidget(doi)
        scores = []
        mode = self.last_search[1] if self.last_search else "keyword"
        if mode == "semantic" and result.semantic_score is not None:
            scores.append(f"Semantic score {result.semantic_score:.3f}")
        elif mode == "hybrid":
            scores.append(f"Hybrid score {result.hybrid_score:.4f}")
            if result.semantic_score is not None:
                scores.append(f"Semantic {result.semantic_score:.3f}")
        if scores:
            score = QLabel(" · ".join(scores))
            score.setStyleSheet("font-size: 10px; color: #82909c")
            layout.addWidget(score)
        item = QListWidgetItem()
        item.setSizeHint(QSize(0, 215))
        self.result_list.addItem(item)
        self.result_list.setItemWidget(item, widget)

    def select_result(self, index: int) -> None:
        self.current_result = self.results[index] if 0 <= index < len(self.results) else None
        result = self.current_result
        for button in (self.copy_text, self.copy_citation, self.open_pdf):
            button.setEnabled(result is not None)
        self.view_pdf.setEnabled(result is not None and not self.busy)
        if result is None:
            self.preview.clear()
            self.preview_title.setText("原文预览")
            self.preview_meta.setText("选择结果查看完整页原文。")
            return
        self.preview_title.setText(plain_title(result.title))
        self.preview_meta.setText(f"PDF 第 {result.page} 页 · {result.year or ''} · {result.journal}\n"
                                  f"DOI: {result.doi or '未提供'}\nZotero: {result.zotero_key} · PDF: {result.attachment_key}")
        self.preview.setPlainText(result.text)
        normalized, offsets = normalized_with_offsets(result.text)
        selections = []
        # QTextCursor positions count UTF-16 units, unlike Python's code points.
        utf16 = [0]
        for char in result.text:
            utf16.append(utf16[-1] + (2 if ord(char) > 0xFFFF else 1))
        terms = query_terms(self.last_search[0]) if self.last_search else []
        for term in terms:
            for match in re.finditer(re.escape(term), normalized, re.I):
                selection = QTextEdit.ExtraSelection()
                selection.cursor = self.preview.textCursor()
                selection.cursor.setPosition(utf16[offsets[match.start()]])
                end = min(len(result.text), offsets[match.end() - 1] + 1)
                selection.cursor.setPosition(utf16[end], QTextCursor.MoveMode.KeepAnchor)
                selection.format.setBackground(QColor("#fff0b2"))
                selections.append(selection)
        self.preview.setExtraSelections(selections)

    def copy_source(self) -> None:
        if self.current_result:
            selected = self.preview.textCursor().selectedText().replace("\u2029", "\n")
            QApplication.clipboard().setText(selected or self.current_result.text)
            self.message.setText("已复制选中文字或完整页原文。")

    def copy_reference(self) -> None:
        if self.current_result:
            r = self.current_result
            QApplication.clipboard().setText(f"{'; '.join(r.authors)} ({r.year or 'n.d.'}). {plain_title(r.title)}. "
                f"{r.journal}. DOI: {r.doi or 'not provided'}. PDF page {r.page}. Zotero: {r.zotero_key}; attachment: {r.attachment_key}.")
            self.message.setText("已复制引用信息和 PDF 页码。")

    def show_pdf_page(self) -> None:
        result = self.current_result
        if result:
            self._submit("render", lambda progress: (result.title, result.page,
                         render_page(Path(result.pdf_path), result.page, result.text)))

    def open_source(self) -> None:
        if self.current_result:
            path = Path(self.current_result.pdf_path)
            if not path.is_file():
                self.message.setText("PDF 文件已移动或不在本机。请更新索引后重试。")
                return
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                self.message.setText("无法启动默认 PDF 阅读器。可使用「查看 PDF 原页」。")

    def closeEvent(self, event) -> None:
        """Cancel long work before shutting down; never terminate a live Qt worker.

        Never stops the Research Assistant Core Service: Claude/Codex/other
        MCP clients may still be using it, so Core lifetime is independent
        of GUI lifetime (V0.6 brief, section 7) - only this GUI's own
        background monitor thread is stopped, and only to avoid emitting
        Qt signals to a window that is about to be destroyed.
        """
        if self.busy:
            self.closing = True
            self.cancel_event.set()
            self.message.setText("正在结束当前任务并安全关闭…")
            event.ignore()
            return
        self.core_monitor.stop()
        self.thread.quit()
        self.thread.wait()
        if self.search_service is not None:
            self.search_service.close()
        event.accept()
