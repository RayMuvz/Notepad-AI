import os
import sys
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QAction, QIcon, QKeySequence, QTextDocument
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QFileDialog,
    QMessageBox,
    QTextEdit,
    QTabWidget,
    QWidget,
    QVBoxLayout,
    QStatusBar,
    QInputDialog,
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QHBoxLayout,
    QCheckBox,
    QDockWidget,
    QScrollArea,
    QFrame,
)
from PyQt6.QtPrintSupport import QPrintDialog, QPrinter

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


APP_NAME = "Notepad_AI"
APP_VERSION = "1.0.0"

APPDATA_DIR = Path(os.getenv("APPDATA", "")) / APP_NAME
CONFIG_PATH = APPDATA_DIR / "config.json"
SESSION_PATH = APPDATA_DIR / "session.json"


class AIWorkerSignals(QObject):
    chunk = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)


class AIWorker(threading.Thread):
    """
    Background thread that streams AI responses character-by-character.
    """

    def __init__(self, api_key: str, model: str, messages: list[Dict], signals: AIWorkerSignals):
        super().__init__(daemon=True)
        self.api_key = api_key
        self.model = model
        self.messages = messages
        self.signals = signals

    def run(self):
        if OpenAI is None:
            self.signals.error.emit("OpenAI Python package is not installed. Run 'pip install openai'.")
            self.signals.finished.emit()
            return

        try:
            client = OpenAI(api_key=self.api_key)

            stream = client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                stream=True,
            )

            for chunk in stream:
                try:
                    delta = chunk.choices[0].delta
                    content = getattr(delta, "content", None)
                    if content:
                        self.signals.chunk.emit(content)
                except Exception:
                    continue

            self.signals.finished.emit()

        except Exception as e:
            self.signals.error.emit(str(e))
            self.signals.finished.emit()


class FindReplaceDialog(QDialog):
    def __init__(self, parent: QMainWindow, editor: QTextEdit):
        super().__init__(parent)
        self.editor = editor
        self.setWindowTitle("Find and Replace")
        self.setModal(True)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        find_layout = QHBoxLayout()
        find_label = QLabel("Find:")
        self.find_edit = QLineEdit()
        find_layout.addWidget(find_label)
        find_layout.addWidget(self.find_edit)

        replace_layout = QHBoxLayout()
        replace_label = QLabel("Replace:")
        self.replace_edit = QLineEdit()
        replace_layout.addWidget(replace_label)
        replace_layout.addWidget(self.replace_edit)

        self.case_checkbox = QCheckBox("Match case")

        buttons_layout = QHBoxLayout()
        self.find_next_btn = QPushButton("Find Next")
        self.replace_btn = QPushButton("Replace")
        self.replace_all_btn = QPushButton("Replace All")
        self.close_btn = QPushButton("Close")
        buttons_layout.addWidget(self.find_next_btn)
        buttons_layout.addWidget(self.replace_btn)
        buttons_layout.addWidget(self.replace_all_btn)
        buttons_layout.addStretch()
        buttons_layout.addWidget(self.close_btn)

        layout.addLayout(find_layout)
        layout.addLayout(replace_layout)
        layout.addWidget(self.case_checkbox)
        layout.addLayout(buttons_layout)

        self.find_next_btn.clicked.connect(self.find_next)
        self.replace_btn.clicked.connect(self.replace_one)
        self.replace_all_btn.clicked.connect(self.replace_all)
        self.close_btn.clicked.connect(self.close)

    def _find_flags(self):
        flags = QTextDocument.FindFlag(0)
        if self.case_checkbox.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        return flags

    def find_next(self):
        text = self.find_edit.text()
        if not text:
            return
        if not self.editor.find(text, self._find_flags()):
            cursor = self.editor.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            self.editor.setTextCursor(cursor)
            self.editor.find(text, self._find_flags())

    def replace_one(self):
        text = self.find_edit.text()
        replace = self.replace_edit.text()
        if not text:
            return
        cursor = self.editor.textCursor()
        if cursor.hasSelection() and cursor.selectedText() == text:
            cursor.insertText(replace)
        self.find_next()

    def replace_all(self):
        text = self.find_edit.text()
        replace = self.replace_edit.text()
        if not text:
            return

        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        cursor.movePosition(cursor.MoveOperation.Start)
        self.editor.setTextCursor(cursor)

        count = 0
        while self.editor.find(text, self._find_flags()):
            cursor = self.editor.textCursor()
            cursor.insertText(replace)
            count += 1

        cursor.endEditBlock()
        QMessageBox.information(self, "Replace All", f"Replaced {count} occurrence(s).")


class NotepadTextEdit(QTextEdit):
    ai_triggered = pyqtSignal()

    def keyPressEvent(self, event):
        # Shift+Enter triggers AI
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() == Qt.KeyboardModifier.ShiftModifier
        ):
            self.ai_triggered.emit()
            return

        # Prevent edits inside AI response regions
        tab = getattr(self, "_notepad_tab", None)
        if tab and tab.ai_blocks:
            cursor = self.textCursor()
            pos = cursor.position()

            # If there is a selection, block it if it overlaps any response range
            if cursor.hasSelection():
                sel_start = cursor.selectionStart()
                sel_end = cursor.selectionEnd()
                for block in tab.ai_blocks:
                    rs, re = block["response_start"], block["response_end"]
                    if sel_start < re and sel_end > rs:
                        # Allow delete of whole block if selection fully covers it
                        is_full_block = (
                            sel_start <= block["prompt_start"]
                            and sel_end >= block["response_end"]
                        )
                        if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and is_full_block:
                            self._delete_ai_block(block)
                            return
                        return  # block partial edits

            # Block typing when cursor is strictly inside any response range
            # (but allow typing immediately after the response_end position)
            if not cursor.hasSelection() and event.text():
                for block in tab.ai_blocks:
                    if block["response_start"] <= pos < block["response_end"]:
                        return

        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event):
        # Toggle collapse/expand on double-clicking within a prompt region
        tab = getattr(self, "_notepad_tab", None)
        if tab and tab.ai_blocks:
            cursor = self.cursorForPosition(event.pos())
            pos = cursor.position()
            for block in tab.ai_blocks:
                if block["prompt_start"] <= pos <= block["prompt_end"]:
                    self._toggle_block_collapsed(block)
                    return
        super().mouseDoubleClickEvent(event)

    def _toggle_block_collapsed(self, block: Dict):
        cursor = self.textCursor()

        if not block["collapsed"]:
            # Collapse: remove response text but keep it in memory
            cursor.setPosition(block["response_start"])
            cursor.setPosition(block["response_end"], cursor.MoveMode.KeepAnchor)
            response_text = cursor.selectedText()
            block["response_text"] = response_text
            length = block["response_end"] - block["response_start"]
            cursor.removeSelectedText()
            block["collapsed"] = True
            old_end = block["response_end"]
            block["response_end"] = block["response_start"]
            # Shift later blocks left
            self._shift_blocks(block, -length)
        else:
            # Expand: insert stored response text back
            response_text = block.get("response_text", "")
            if not response_text:
                return
            cursor.setPosition(block["response_start"])
            cursor.insertText(response_text)
            length = len(response_text)
            block["collapsed"] = False
            block["response_end"] = block["response_start"] + length
            # Shift later blocks right
            self._shift_blocks(block, length)

    def _shift_blocks(self, current_block: Dict, delta: int):
        """Shift positions of blocks that come after current_block in the document."""
        tab = getattr(self, "_notepad_tab", None)
        if not tab:
            return
        current_end = current_block["response_end"]
        for b in tab.ai_blocks:
            if b is current_block:
                continue
            if b["prompt_start"] >= current_end:
                b["prompt_start"] += delta
                b["prompt_end"] += delta
                b["response_start"] += delta
                b["response_end"] += delta

    def _delete_ai_block(self, block: Dict):
        tab = getattr(self, "_notepad_tab", None)
        if not tab:
            return
        cursor = self.textCursor()
        cursor.setPosition(block["prompt_start"])
        cursor.setPosition(block["response_end"], cursor.MoveMode.KeepAnchor)
        length = block["response_end"] - block["prompt_start"]
        cursor.removeSelectedText()
        tab.ai_blocks.remove(block)
        # Shift subsequent blocks
        dummy = {
            "response_end": block["prompt_start"],
        }
        self._shift_blocks(dummy, -length)


class NotepadTab:
    def __init__(self, editor: NotepadTextEdit):
        self.editor = editor
        self.file_path: Optional[Path] = None
        self.dirty: bool = False
        self.last_response_pos: int = 0
        self.log_mode: bool = False
        # List of AI blocks: each is a dict with prompt/response ranges and state
        self.ai_blocks: list[Dict] = []
        # Per-tab chat history for OpenAI (list of {"role": ..., "content": ...})
        self.chat_history: list[Dict] = []


class NotepadAIMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(900, 600)

        icon_path = Path(__file__).with_name("notepad.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        QApplication.setStyle("Fusion")

        self.config: Dict = {}
        self.load_config()

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self.update_window_title)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self.tabs)
        self.setCentralWidget(central)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        # Status bar sections: position, length, zoom, EOL, encoding
        from PyQt6.QtWidgets import QLabel

        self.status_pos_label = QLabel("Ln 1, Col 1")
        self.status_len_label = QLabel("0 characters")
        self.status_zoom_label = QLabel("100%")
        self.status_eol_label = QLabel("Windows (CRLF)")
        self.status_encoding_label = QLabel("UTF-8")

        self.status_bar.addWidget(self.status_pos_label)
        self.status_bar.addWidget(self.status_len_label)
        self.status_bar.addPermanentWidget(self.status_zoom_label)
        self.status_bar.addPermanentWidget(self.status_eol_label)
        self.status_bar.addPermanentWidget(self.status_encoding_label)

        self.zoom_factor = 1.0

        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(10_000)
        self.autosave_timer.timeout.connect(self.autosave)
        self.autosave_timer.start()

        self.word_wrap_enabled = True

        self.build_menus()
        self.apply_theme(self.config.get("dark_mode", False))
        self.ensure_api_key()
        self.restore_session_or_new_tab()

    def restore_session_or_new_tab(self):
        """Restore open tabs and AI blocks from the last session, or start with a new tab."""
        if not SESSION_PATH.exists():
            self.new_tab()
            return

        try:
            with open(SESSION_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            self.new_tab()
            return

        tabs_data = data.get("tabs", [])
        current_index = data.get("current_index", 0)

        if not tabs_data:
            self.new_tab()
            return

        self.tabs.blockSignals(True)
        self.tabs.clear()

        for tab_info in tabs_data:
            text = tab_info.get("text", "")
            file_path_str = tab_info.get("file_path")
            dirty = tab_info.get("dirty", False)
            last_response_pos = tab_info.get("last_response_pos", 0)
            log_mode = tab_info.get("log_mode", False)
            ai_blocks = tab_info.get("ai_blocks", [])
            chat_history = tab_info.get("chat_history", [])

            editor = NotepadTextEdit()
            editor.setAcceptRichText(False)
            editor.setPlainText(text)
            editor.textChanged.connect(self.on_text_changed)
            editor.ai_triggered.connect(self.trigger_ai_for_current_tab)

            if self.word_wrap_enabled:
                editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            else:
                editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

            tab = NotepadTab(editor)
            tab.file_path = Path(file_path_str) if file_path_str else None
            tab.dirty = dirty
            tab.last_response_pos = last_response_pos
            tab.log_mode = log_mode
            tab.ai_blocks = ai_blocks
            tab.chat_history = chat_history

            setattr(editor, "_notepad_tab", tab)

            if tab.file_path:
                title = tab.file_path.name
            else:
                title = "Untitled"
            if tab.dirty and not title.endswith("*"):
                title += "*"

            idx = self.tabs.addTab(editor, title)
            if tab.file_path:
                self.tabs.setTabToolTip(idx, str(tab.file_path))

        self.tabs.blockSignals(False)

        if 0 <= current_index < self.tabs.count():
            self.tabs.setCurrentIndex(current_index)
        self.update_window_title()

    def add_ai_history_entry(self, prompt: str):
        """
        Add a collapsible prompt/response entry to the AI history dock.
        """
        if not hasattr(self, "ai_history_layout"):
            return

        # Remove the trailing stretch so we can append before it.
        # (The last item is always the stretch we added in __init__.)
        count = self.ai_history_layout.count()
        stretch_item = None
        if count > 0:
            stretch_item = self.ai_history_layout.itemAt(count - 1)
            self.ai_history_layout.removeItem(stretch_item)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Header with prompt preview and a simple "Collapse"/"Expand" toggle
        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)

        preview = prompt.replace("\n", " ")
        if len(preview) > 60:
            preview = preview[:57] + "..."

        prompt_label = QLabel(preview if preview else "(empty prompt)")
        prompt_label.setStyleSheet("font-weight: bold;")

        toggle_button = QPushButton("Collapse")
        toggle_button.setCheckable(True)
        toggle_button.setChecked(False)

        header_layout.addWidget(prompt_label)
        header_layout.addStretch()
        header_layout.addWidget(toggle_button)

        # Body with full prompt and streaming response
        body_widget = QWidget()
        body_layout = QVBoxLayout(body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(2)

        full_prompt_label = QLabel("Prompt:")
        full_prompt_label.setStyleSheet("font-weight: bold;")
        full_prompt = QTextEdit()
        full_prompt.setReadOnly(True)
        full_prompt.setPlainText(prompt)
        full_prompt.setMaximumHeight(80)

        response_label = QLabel("Response:")
        response_label.setStyleSheet("font-weight: bold;")
        response_edit = QTextEdit()
        response_edit.setReadOnly(True)
        response_edit.setMinimumHeight(80)

        body_layout.addWidget(full_prompt_label)
        body_layout.addWidget(full_prompt)
        body_layout.addWidget(response_label)
        body_layout.addWidget(response_edit)

        def on_toggle(checked: bool):
            body_widget.setVisible(not checked)
            toggle_button.setText("Expand" if checked else "Collapse")

        toggle_button.toggled.connect(on_toggle)

        layout.addWidget(header_widget)
        layout.addWidget(body_widget)
        container.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Plain)

        self.ai_history_layout.addWidget(container)
        # Re-add the stretch at the bottom.
        if stretch_item is not None:
            self.ai_history_layout.addItem(stretch_item)
        else:
            self.ai_history_layout.addStretch()

        # Keep a reference to the widgets for streaming updates.
        self._last_ai_history_widgets = {
            "container": container,
            "response_edit": response_edit,
        }

    # -------------------- Configuration --------------------
    def load_config(self):
        try:
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            else:
                self.config = {}
        except Exception:
            self.config = {}

    def save_config(self):
        try:
            APPDATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "Config Error", f"Failed to save config: {e}")

    def ensure_api_key(self):
        api_key = self.config.get("openai_api_key")
        if api_key:
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("Configure OpenAI API Key")
        msg.setText(
            "To use the AI features (Shift+Enter), Notepad_AI needs your OpenAI API key.\n\n"
            "You can enter it now or configure it later via Help > Configure API."
        )
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        result = msg.exec()

        if result == QMessageBox.StandardButton.Ok:
            self.configure_api_key()

    def configure_api_key(self):
        key, ok = QInputDialog.getText(
            self,
            "OpenAI API Key",
            "Enter your OpenAI API Key (sk-...):",
            QLineEdit.EchoMode.Password,
        )
        if ok and key:
            self.config["openai_api_key"] = key.strip()
            if "openai_model" not in self.config:
                self.config["openai_model"] = "gpt-4o"
            self.save_config()
            QMessageBox.information(self, "API Key Saved", "API key has been saved.")

    # -------------------- UI / Menus --------------------
    def build_menus(self):
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")

        # File menu modeled after Windows 11 Notepad
        new_tab_action = QAction("New &tab", self)
        new_tab_action.setShortcut(QKeySequence("Ctrl+N"))
        new_tab_action.triggered.connect(self.new_tab)
        file_menu.addAction(new_tab_action)

        new_window_action = QAction("New &window", self)
        new_window_action.setShortcut(QKeySequence("Ctrl+Shift+N"))
        new_window_action.triggered.connect(self.new_window)
        file_menu.addAction(new_window_action)

        open_action = QAction("&Open", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)

        save_action = QAction("&Save", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self.save_file)
        file_menu.addAction(save_action)

        save_as_action = QAction("Save &as", self)
        save_as_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save_as_action.triggered.connect(self.save_file_as)
        file_menu.addAction(save_as_action)

        save_all_action = QAction("Save a&ll", self)
        save_all_action.setShortcut(QKeySequence("Ctrl+Alt+S"))
        save_all_action.triggered.connect(self.save_all_tabs)
        file_menu.addAction(save_all_action)

        file_menu.addSeparator()

        page_setup_action = QAction("Page set&up", self)
        page_setup_action.triggered.connect(self.page_setup)
        file_menu.addAction(page_setup_action)

        print_action = QAction("&Print", self)
        print_action.setShortcut(QKeySequence.StandardKey.Print)
        print_action.triggered.connect(self.print_file)
        file_menu.addAction(print_action)

        file_menu.addSeparator()

        close_tab_action = QAction("Close &tab", self)
        close_tab_action.setShortcut(QKeySequence("Ctrl+W"))
        close_tab_action.triggered.connect(self.close_current_tab)
        file_menu.addAction(close_tab_action)

        close_window_action = QAction("Close &window", self)
        close_window_action.setShortcut(QKeySequence("Ctrl+Shift+W"))
        close_window_action.triggered.connect(self.close)
        file_menu.addAction(close_window_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        edit_menu = menu_bar.addMenu("&Edit")

        undo_action = QAction("&Undo", self)
        undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        undo_action.triggered.connect(lambda: self.current_editor().undo())
        edit_menu.addAction(undo_action)

        redo_action = QAction("&Redo", self)
        redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        redo_action.triggered.connect(lambda: self.current_editor().redo())
        edit_menu.addAction(redo_action)

        edit_menu.addSeparator()

        cut_action = QAction("Cu&t", self)
        cut_action.setShortcut(QKeySequence.StandardKey.Cut)
        cut_action.triggered.connect(lambda: self.current_editor().cut())
        edit_menu.addAction(cut_action)

        copy_action = QAction("&Copy", self)
        copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        copy_action.triggered.connect(lambda: self.current_editor().copy())
        edit_menu.addAction(copy_action)

        paste_action = QAction("&Paste", self)
        paste_action.setShortcut(QKeySequence.StandardKey.Paste)
        paste_action.triggered.connect(lambda: self.current_editor().paste())
        edit_menu.addAction(paste_action)

        delete_action = QAction("&Delete", self)
        delete_action.setShortcut(QKeySequence("Del"))
        delete_action.triggered.connect(self.delete_selection)
        edit_menu.addAction(delete_action)

        search_bing_action = QAction("Search with &Bing", self)
        search_bing_action.setShortcut(QKeySequence("Ctrl+E"))
        search_bing_action.triggered.connect(self.search_with_bing)
        edit_menu.addAction(search_bing_action)

        edit_menu.addSeparator()

        find_action = QAction("&Find...", self)
        find_action.setShortcut(QKeySequence.StandardKey.Find)
        find_action.triggered.connect(self.find_replace)
        edit_menu.addAction(find_action)

        find_next_action = QAction("Find &next", self)
        find_next_action.setShortcut(QKeySequence("F3"))
        find_next_action.triggered.connect(self.find_next_shortcut)
        edit_menu.addAction(find_next_action)

        find_prev_action = QAction("Find pre&vious", self)
        find_prev_action.setShortcut(QKeySequence("Shift+F3"))
        find_prev_action.triggered.connect(self.find_prev_shortcut)
        edit_menu.addAction(find_prev_action)

        replace_action = QAction("R&eplace...", self)
        replace_action.setShortcut(QKeySequence.StandardKey.Replace)
        replace_action.triggered.connect(self.find_replace)
        edit_menu.addAction(replace_action)

        edit_menu.addSeparator()

        goto_action = QAction("&Go to", self)
        goto_action.setShortcut(QKeySequence("Ctrl+G"))
        goto_action.triggered.connect(self.goto_line)
        edit_menu.addAction(goto_action)

        select_all_action = QAction("Select &all", self)
        select_all_action.setShortcut(QKeySequence.StandardKey.SelectAll)
        select_all_action.triggered.connect(lambda: self.current_editor().selectAll())
        edit_menu.addAction(select_all_action)

        time_date_action = QAction("Time/&Date", self)
        time_date_action.setShortcut(QKeySequence("F5"))
        time_date_action.triggered.connect(self.insert_time_date)
        edit_menu.addAction(time_date_action)

        font_action = QAction("&Font", self)
        font_action.triggered.connect(self.change_font)
        edit_menu.addAction(font_action)

        view_menu = menu_bar.addMenu("&View")

        zoom_menu = view_menu.addMenu("&Zoom")
        zoom_in_action = QAction("Zoom &in", self)
        zoom_in_action.setShortcut(QKeySequence("Ctrl++"))
        zoom_in_action.triggered.connect(self.zoom_in)
        zoom_menu.addAction(zoom_in_action)

        zoom_out_action = QAction("Zoom &out", self)
        zoom_out_action.setShortcut(QKeySequence("Ctrl+-"))
        zoom_out_action.triggered.connect(self.zoom_out)
        zoom_menu.addAction(zoom_out_action)

        zoom_reset_action = QAction("Restore default zoom", self)
        zoom_reset_action.setShortcut(QKeySequence("Ctrl+0"))
        zoom_reset_action.triggered.connect(self.zoom_reset)
        zoom_menu.addAction(zoom_reset_action)

        self.status_bar_action = QAction("&Status bar", self, checkable=True)
        self.status_bar_action.setChecked(True)
        self.status_bar_action.triggered.connect(self.toggle_status_bar)
        view_menu.addAction(self.status_bar_action)

        self.word_wrap_action = QAction("&Word wrap", self, checkable=True, checked=True)
        self.word_wrap_action.triggered.connect(self.toggle_word_wrap)
        view_menu.addAction(self.word_wrap_action)

        help_menu = menu_bar.addMenu("&Help")

        api_config_action = QAction("Configure &API", self)
        api_config_action.triggered.connect(self.configure_api_key)
        help_menu.addAction(api_config_action)

        about_action = QAction("&About Notepad_AI", self)
        about_action.triggered.connect(self.about)
        help_menu.addAction(about_action)

    # -------------------- Tabs / Editors --------------------
    def new_tab(self):
        editor = NotepadTextEdit()
        editor.setAcceptRichText(False)
        editor.textChanged.connect(self.on_text_changed)
        editor.ai_triggered.connect(self.trigger_ai_for_current_tab)

        if self.word_wrap_enabled:
            editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        else:
            editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

        tab = NotepadTab(editor)
        index = self.tabs.addTab(editor, "Untitled")
        self.tabs.setCurrentIndex(index)
        self.tabs.setTabToolTip(index, "")
        setattr(editor, "_notepad_tab", tab)

        self.update_window_title()

    def close_tab(self, index: int):
        if index < 0:
            return
        widget = self.tabs.widget(index)
        tab = self.get_tab_by_widget(widget)
        if tab and not self.maybe_save_tab(tab):
            return
        self.tabs.removeTab(index)
        if self.tabs.count() == 0:
            self.new_tab()

    def current_tab(self) -> Optional[NotepadTab]:
        widget = self.tabs.currentWidget()
        return self.get_tab_by_widget(widget)

    def current_editor(self) -> Optional[NotepadTextEdit]:
        tab = self.current_tab()
        return tab.editor if tab else None

    def get_tab_by_widget(self, widget: QWidget) -> Optional[NotepadTab]:
        if widget is None:
            return None
        if not hasattr(widget, "_notepad_tab"):
            setattr(widget, "_notepad_tab", NotepadTab(widget))
        return getattr(widget, "_notepad_tab")

    # -------------------- Status / Title --------------------
    def on_text_changed(self):
        tab = self.current_tab()
        if not tab:
            return
        if not tab.dirty:
            tab.dirty = True
            index = self.tabs.indexOf(tab.editor)
            if index != -1:
                title = self.tabs.tabText(index)
                if not title.endswith("*"):
                    self.tabs.setTabText(index, title + "*")
        self.update_status_bar()

    def update_status_bar(self):
        editor = self.current_editor()
        if not editor:
            return
        cursor = editor.textCursor()
        line = cursor.blockNumber() + 1
        col = cursor.columnNumber() + 1
        text = editor.toPlainText()
        length = len(text)
        self.status_pos_label.setText(f"Ln {line}, Col {col}")
        self.status_len_label.setText(f"{length} characters")
        self.status_zoom_label.setText(f"{int(self.zoom_factor * 100)}%")

    def update_window_title(self):
        tab = self.current_tab()
        if not tab:
            self.setWindowTitle(APP_NAME)
            return
        if tab.file_path:
            title = str(tab.file_path)
        else:
            title = "Untitled"
        if tab.dirty:
            title += " *"
        self.setWindowTitle(f"{title} - {APP_NAME}")

    # -------------------- File Operations --------------------
    def open_file(self):
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Open",
            "",
            "Text Files (*.txt);;All Files (*)",
        )
        if not path_str:
            return

        path = Path(path_str)
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            QMessageBox.warning(self, "Open Error", f"Could not open file:\n{e}")
            return

        editor = NotepadTextEdit()
        editor.setAcceptRichText(False)
        editor.setPlainText(text)
        editor.textChanged.connect(self.on_text_changed)
        editor.ai_triggered.connect(self.trigger_ai_for_current_tab)

        if self.word_wrap_enabled:
            editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        else:
            editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

        tab = NotepadTab(editor)
        tab.file_path = path
        tab.dirty = False

        lines = text.splitlines()
        if lines and lines[0].strip() == ".LOG":
            tab.log_mode = True
            timestamp = datetime.now().strftime("%H:%M %m/%d/%Y")
            editor.moveCursor(editor.textCursor().MoveOperation.End)
            editor.insertPlainText(f"\n{timestamp}\n")
            tab.dirty = True

        setattr(editor, "_notepad_tab", tab)

        index = self.tabs.addTab(editor, path.name)
        self.tabs.setTabToolTip(index, str(path))
        self.tabs.setCurrentIndex(index)
        self.update_window_title()

    def maybe_save_tab(self, tab: NotepadTab) -> bool:
        if not tab.dirty:
            return True
        ret = QMessageBox.question(
            self,
            "Unsaved Changes",
            "The document has been modified.\nDo you want to save your changes?",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
        )
        if ret == QMessageBox.StandardButton.Yes:
            return self.save_specific_tab(tab)
        if ret == QMessageBox.StandardButton.No:
            return True
        return False

    def save_specific_tab(self, tab: NotepadTab) -> bool:
        if tab.file_path is None:
            return self.save_file_as()
        try:
            with open(tab.file_path, "w", encoding="utf-8") as f:
                f.write(tab.editor.toPlainText())
        except Exception as e:
            QMessageBox.warning(self, "Save Error", f"Could not save file:\n{e}")
            return False
        tab.dirty = False
        index = self.tabs.indexOf(tab.editor)
        if index != -1:
            title = tab.file_path.name
            self.tabs.setTabText(index, title)
            self.tabs.setTabToolTip(index, str(tab.file_path))
        self.update_window_title()
        return True

    def save_file(self):
        tab = self.current_tab()
        if not tab:
            return
        if tab.file_path is None:
            self.save_file_as()
        else:
            self.save_specific_tab(tab)

    def save_file_as(self) -> bool:
        tab = self.current_tab()
        if not tab:
            return False
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save As",
            "",
            "Text Files (*.txt);;All Files (*)",
        )
        if not path_str:
            return False
        path = Path(path_str)
        tab.file_path = path
        return self.save_specific_tab(tab)

    def print_file(self):
        editor = self.current_editor()
        if not editor:
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec() == QPrintDialog.DialogCode.Accepted:
            editor.print(printer)

    # -------------------- Autosave --------------------
    def autosave(self):
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            tab = self.get_tab_by_widget(widget)
            if not tab or not tab.dirty:
                continue
            if tab.file_path:
                try:
                    with open(tab.file_path, "w", encoding="utf-8") as f:
                        f.write(tab.editor.toPlainText())
                    tab.dirty = False
                    title = tab.file_path.name
                    self.tabs.setTabText(i, title)
                except Exception:
                    continue

    # -------------------- Edit helpers --------------------
    def delete_selection(self):
        editor = self.current_editor()
        if not editor:
            return
        cursor = editor.textCursor()
        if cursor.hasSelection():
            cursor.removeSelectedText()

    def find_replace(self):
        editor = self.current_editor()
        if not editor:
            return
        dlg = FindReplaceDialog(self, editor)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def insert_time_date(self):
        editor = self.current_editor()
        if not editor:
            return
        timestamp = datetime.now().strftime("%H:%M %m/%d/%Y")
        editor.insertPlainText(timestamp)

    def toggle_word_wrap(self):
        self.word_wrap_enabled = self.word_wrap_action.isChecked()
        mode = (
            QTextEdit.LineWrapMode.WidgetWidth
            if self.word_wrap_enabled
            else QTextEdit.LineWrapMode.NoWrap
        )
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            if isinstance(widget, QTextEdit):
                widget.setLineWrapMode(mode)

    def toggle_status_bar(self):
        visible = self.status_bar_action.isChecked()
        self.status_bar.setVisible(visible)

    def zoom_in(self):
        editor = self.current_editor()
        if not editor:
            return
        editor.zoomIn(1)
        self.zoom_factor *= 1.1
        self.update_status_bar()

    def zoom_out(self):
        editor = self.current_editor()
        if not editor:
            return
        editor.zoomOut(1)
        self.zoom_factor /= 1.1
        self.update_status_bar()

    def zoom_reset(self):
        editor = self.current_editor()
        if not editor:
            return
        # Reset by applying inverse of current zoom factor
        if self.zoom_factor != 0:
            import math

            steps = int(round(math.log(self.zoom_factor, 1.1)))
            if steps > 0:
                for _ in range(steps):
                    editor.zoomOut(1)
            elif steps < 0:
                for _ in range(-steps):
                    editor.zoomIn(1)
        self.zoom_factor = 1.0
        self.update_status_bar()

    # -------------------- Theme --------------------
    def apply_theme(self, dark: bool):
        app = QApplication.instance()
        if not app:
            return
        if dark:
            from PyQt6.QtGui import QPalette, QColor

            palette = QPalette()
            palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
            palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
            palette.setColor(QPalette.ColorRole.Base, QColor(20, 20, 20))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor(40, 40, 40))
            palette.setColor(QPalette.ColorRole.ToolTipBase, Qt.GlobalColor.white)
            palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)
            palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
            palette.setColor(QPalette.ColorRole.Button, QColor(45, 45, 45))
            palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
            palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
            palette.setColor(QPalette.ColorRole.Highlight, QColor(38, 79, 120))
            palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.white)
            app.setPalette(palette)
        else:
            app.setPalette(app.style().standardPalette())

    def toggle_dark_mode(self):
        dark = self.dark_mode_action.isChecked()
        self.config["dark_mode"] = dark
        self.save_config()
        self.apply_theme(dark)

    def new_window(self):
        # Launch a new instance of Notepad_AI
        try:
            import subprocess, sys as _sys

            subprocess.Popen([_sys.executable, str(Path(__file__))])
        except Exception:
            pass

    def save_all_tabs(self):
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            tab = self.get_tab_by_widget(widget)
            if tab:
                self.tabs.setCurrentIndex(i)
                self.save_specific_tab(tab)

    def close_current_tab(self):
        index = self.tabs.currentIndex()
        if index >= 0:
            self.close_tab(index)

    def page_setup(self):
        QMessageBox.information(
            self,
            "Page setup",
            "Page setup is not customizable in this version of Notepad_AI.",
        )

    def search_with_bing(self):
        editor = self.current_editor()
        if not editor:
            return
        selection = editor.textCursor().selectedText()
        if not selection:
            return
        import webbrowser, urllib.parse

        q = urllib.parse.quote(selection)
        webbrowser.open(f"https://www.bing.com/search?q={q}")

    def find_next_shortcut(self):
        editor = self.current_editor()
        if not editor:
            return
        # Reuse current selection as search term
        cursor = editor.textCursor()
        text = cursor.selectedText()
        if text:
            editor.find(text)

    def find_prev_shortcut(self):
        editor = self.current_editor()
        if not editor:
            return
        cursor = editor.textCursor()
        text = cursor.selectedText()
        if text:
            editor.find(text, QTextDocument.FindFlag.FindBackward)

    def goto_line(self):
        editor = self.current_editor()
        if not editor:
            return
        max_line = editor.document().blockCount()
        line_str, ok = QInputDialog.getText(
            self, "Go to line", f"Enter line number (1-{max_line}):"
        )
        if not ok or not line_str.strip().isdigit():
            return
        line = int(line_str)
        if line < 1:
            line = 1
        if line > max_line:
            line = max_line
        cursor = editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        cursor.movePosition(cursor.MoveOperation.Down, cursor.MoveMode.MoveAnchor, line - 1)
        editor.setTextCursor(cursor)
        self.update_status_bar()

    def change_font(self):
        from PyQt6.QtWidgets import QFontDialog

        editor = self.current_editor()
        if not editor:
            return
        font, ok = QFontDialog.getFont(editor.font(), self, "Font")
        if ok:
            editor.setFont(font)

    # -------------------- AI Integration --------------------
    def trigger_ai_for_current_tab(self):
        tab = self.current_tab()
        editor = tab.editor if tab else None
        if not tab or not editor:
            return

        api_key = self.config.get("openai_api_key")
        model = self.config.get("openai_model", "gpt-4o")
        if not api_key:
            QMessageBox.information(
                self,
                "API Key Required",
                "AI features require an OpenAI API key.\nConfigure it via Help > Configure API.",
            )
            return

        cursor = editor.textCursor()
        current_pos = cursor.position()
        doc_text = editor.toPlainText()
        last_pos = max(0, min(tab.last_response_pos, len(doc_text)))
        if current_pos < last_pos:
            last_pos = 0
        prompt = doc_text[last_pos:current_pos]
        if not prompt.strip():
            QMessageBox.information(self, "No Text Selected", "Type some text before Shift+Enter.")
            return

        # Insert two newlines so the streamed response appears below the prompt
        cursor = editor.textCursor()
        cursor.setPosition(current_pos)
        editor.setTextCursor(cursor)
        editor.insertPlainText("\n\n")
        response_start = editor.textCursor().position()
        response_end = response_start

        # Track this prompt/response as a collapsible AI block
        ai_block = {
            "prompt_start": last_pos,
            "prompt_end": last_pos + len(prompt),
            "response_start": response_start,
            "response_end": response_end,
            "response_text": "",
            "collapsed": False,
        }
        tab.ai_blocks.append(ai_block)

        # Build per-tab chat messages for OpenAI context
        messages: list[Dict] = []
        messages.append(
            {
                "role": "system",
                "content": (
                    "You are a helpful AI assistant embedded in a plain text editor called Notepad_AI. "
                    "The user is working in a single document; answer concisely in plain natural language "
                    "with no markdown, no bullet symbols (*, -, #, etc.), and no code fences."
                ),
            }
        )
        messages.extend(tab.chat_history)
        messages.append({"role": "user", "content": prompt})

        signals = AIWorkerSignals()
        worker = AIWorker(api_key, model, messages, signals)

        def on_chunk(text: str):
            c = editor.textCursor()
            c.setPosition(ai_block["response_end"])
            editor.setTextCursor(c)
            editor.insertPlainText(text)
            new_end = editor.textCursor().position()
            ai_block["response_text"] += text
            ai_block["response_end"] = new_end
            tab.last_response_pos = new_end

        def on_error(msg: str):
            QMessageBox.warning(self, "AI Error", msg)

        def on_finished():
            # Keep last_response_pos at the end of this response block
            end_pos = ai_block["response_end"]
            tab.last_response_pos = end_pos
            # Update per-tab chat history with this full turn
            assistant_text = ai_block["response_text"]
            if assistant_text.strip():
                tab.chat_history.append({"role": "user", "content": prompt})
                tab.chat_history.append({"role": "assistant", "content": assistant_text})
            # Move cursor to the end so the user can immediately type a new prompt
            c = editor.textCursor()
            c.setPosition(end_pos)
            editor.setTextCursor(c)

        signals.chunk.connect(on_chunk)
        signals.error.connect(on_error)
        signals.finished.connect(on_finished)

        worker.start()

    # -------------------- Help --------------------
    def about(self):
        QMessageBox.about(
            self,
            "About Notepad_AI",
            f"Notepad_AI\n\nA modern Windows 11-style Notepad clone with integrated OpenAI assistance.\n\nVersion {APP_VERSION}",
        )

    # -------------------- Close handling --------------------
    def closeEvent(self, event):
        for i in range(self.tabs.count()):
            widget = self.tabs.widget(i)
            tab = self.get_tab_by_widget(widget)
            if tab and not self.maybe_save_tab(tab):
                event.ignore()
                return
        # Persist session (open tabs, AI blocks, etc.) before exiting
        self.save_session()
        event.accept()

    def save_session(self):
        """Save current tabs and AI block metadata so the session can be restored on next launch."""
        try:
            APPDATA_DIR.mkdir(parents=True, exist_ok=True)
            tabs_data = []
            for i in range(self.tabs.count()):
                widget = self.tabs.widget(i)
                tab = self.get_tab_by_widget(widget)
                if not tab:
                    continue
                tab_info = {
                    "text": tab.editor.toPlainText(),
                    "file_path": str(tab.file_path) if tab.file_path else None,
                    "dirty": tab.dirty,
                    "last_response_pos": tab.last_response_pos,
                    "log_mode": tab.log_mode,
                    "ai_blocks": tab.ai_blocks,
                    "chat_history": tab.chat_history,
                }
                tabs_data.append(tab_info)

            data = {
                "tabs": tabs_data,
                "current_index": self.tabs.currentIndex(),
            }

            with open(SESSION_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            # Failing to save the session should not block app exit.
            pass


def main():
    app = QApplication(sys.argv)
    window = NotepadAIMainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

import os
import sys
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QAction, QIcon, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QFileDialog,
    QMessageBox,
    QTextEdit,
    QTabWidget,
    QWidget,
    QVBoxLayout,
    QStatusBar,
    QInputDialog,
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QHBoxLayout,
    QCheckBox,
)

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


APP_NAME = "Notepad_AI"
ORG_NAME = "YourCompany"
APP_VERSION = "1.0.0"

APPDATA_DIR = Path(os.getenv("APPDATA", "")) / APP_NAME
CONFIG_PATH = APPDATA_DIR / "config.json"
APP_DIR = Path(__file__).parent
APP_ICON_PATH = APP_DIR / "notepad.ico"


class AIWorkerSignals(QObject):
    chunk = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)


class AIWorker(threading.Thread):
    def __init__(self, api_key: str, model: str, prompt: str, signals: AIWorkerSignals):
        super().__init__(daemon=True)
        self.api_key = api_key
        self.model = model
        self.prompt = prompt
        self.signals = signals

    def run(self):
        if OpenAI is None:
            self.signals.error.emit("OpenAI Python package is not installed.")
            self.signals.finished.emit()
            return

        try:
            client = OpenAI(api_key=self.api_key)

            stream = client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a helpful AI assistant for a text editor.",
                    },
                    {"role": "user", "content": self.prompt},
                ],
                stream=True,
            )

            for chunk in stream:
                try:
                    delta = chunk.choices[0].delta
                    if hasattr(delta, "content") and delta.content:
                        self.signals.chunk.emit(delta.content)
                except Exception:
                    continue

            self.signals.finished.emit()

        except Exception as e:
            self.signals.error.emit(str(e))
            self.signals.finished.emit()


class FindReplaceDialog(QDialog):
    def __init__(self, parent: QMainWindow, editor: QTextEdit):
        super().__init__(parent)
        self.editor = editor
        self.setWindowTitle("Find and Replace")
        self.setModal(True)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        find_layout = QHBoxLayout()
        find_label = QLabel("Find:")
        self.find_edit = QLineEdit()
        find_layout.addWidget(find_label)
        find_layout.addWidget(self.find_edit)

        replace_layout = QHBoxLayout()
        replace_label = QLabel("Replace:")
        self.replace_edit = QLineEdit()
        replace_layout.addWidget(replace_label)
        replace_layout.addWidget(self.replace_edit)

        self.case_checkbox = QCheckBox("Match case")

        buttons_layout = QHBoxLayout()
        self.find_next_btn = QPushButton("Find Next")
        self.replace_btn = QPushButton("Replace")
        self.replace_all_btn = QPushButton("Replace All")
        self.close_btn = QPushButton("Close")
        buttons_layout.addWidget(self.find_next_btn)
        buttons_layout.addWidget(self.replace_btn)
        buttons_layout.addWidget(self.replace_all_btn)
        buttons_layout.addStretch()
        buttons_layout.addWidget(self.close_btn)

        layout.addLayout(find_layout)
        layout.addLayout(replace_layout)
        layout.addWidget(self.case_checkbox)
        layout.addLayout(buttons_layout)

        self.find_next_btn.clicked.connect(self.find_next)
        self.replace_btn.clicked.connect(self.replace_one)
        self.replace_all_btn.clicked.connect(self.replace_all)
        self.close_btn.clicked.connect(self.close)

    def _find_flags(self):
        flags = QTextDocument.FindFlag(0)
        if self.case_checkbox.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        return flags

    def find_next(self):
        text = self.find_edit.text()
        if not text:
            return
        if not self.editor.find(text, self._find_flags()):
            cursor = self.editor.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            self.editor.setTextCursor(cursor)
            self.editor.find(text, self._find_flags())

    def replace_one(self):
        text = self.find_edit.text()
        replace = self.replace_edit.text()
        if not text:
            return
        cursor = self.editor.textCursor()
        if cursor.hasSelection() and cursor.selectedText() == text:
            cursor.insertText(replace)
        self.find_next()

    def replace_all(self):
        text = self.find_edit.text()
        replace = self.replace_edit.text()
        if not text:
            return

        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        cursor.movePosition(cursor.MoveOperation.Start)
        self.editor.setTextCursor(cursor)

        count = 0
        while self.editor.find(text, self._find_flags()):
            cursor = self.editor.textCursor()
            cursor.insertText(replace)
            count += 1

        cursor.endEditBlock()
        QMessageBox.information(self, "Replace All", f"Replaced {count} occurrence(s).")


class NotepadTextEdit(QTextEdit):
    ai_triggered = pyqtSignal()

    def keyPressEvent(self, event):
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() == Qt.KeyboardModifier.ShiftModifier
        ):
            self.ai_triggered.emit()
            return
        super().keyPressEvent(event)


class NotepadTab:
    def __init__(self, editor: NotepadTextEdit):
        self.editor = editor
        self.file_path: Optional[Path] = None
        self.dirty: bool = False
        self.last_response_pos: int = 0
        self.log_mode: bool = False


class NotepadAIMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(900, 600)
        if APP_ICON_PATH.exists():
            app_icon = QIcon(str(APP_ICON_PATH))
            self.setWindowIcon(app_icon)
            QApplication.setWindowIcon(app_icon)

        self.config: Dict = {}
        self.load_config()

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self.update_window_title)

        # Central editor area
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(self.tabs)
        self.setCentralWidget(central)

        # AI history dock (collapsible prompt/response items)
        self.ai_history_dock = QDockWidget("AI Conversations", self)
        self.ai_history_dock.setObjectName("AIHistoryDock")
        self.ai_history_dock.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea
        )

        history_container = QWidget()
        self.ai_history_layout = QVBoxLayout(history_container)
        self.ai_history_layout.setContentsMargins(4, 4, 4, 4)
        self.ai_history_layout.setSpacing(4)
        self.ai_history_layout.addStretch()

        history_scroll = QScrollArea()
        history_scroll.setWidgetResizable(True)
        history_scroll.setFrameShape(QFrame.Shape.NoFrame)
        history_scroll.setWidget(history_container)

        self.ai_history_dock.setWidget(history_scroll)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.ai_history_dock)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(10_000)
        self.autosave_timer.timeout.connect(self.autosave)
        self.autosave_timer.start()

        self.word_wrap_enabled = True

        self.build_menus()
        self.ensure_api_key()
        self.new_tab()

    def load_config(self):
        try:
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
            else:
                self.config = {}
        except Exception:
            self.config = {}

    def save_config(self):
        try:
            APPDATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "Config Error", f"Failed to save config: {e}")

    def ensure_api_key(self):
        api_key = self.config.get("openai_api_key")
        if api_key:
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("Configure OpenAI API Key")
        msg.setText(
            "To use the AI features (Shift+Enter), Notepad_AI needs your OpenAI API key.\n\n"
            "You can enter it now or configure it later via Help > Configure API."
        )
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        result = msg.exec()

        if result == QMessageBox.StandardButton.Ok:
            self.configure_api_key()

    def configure_api_key(self):
        key, ok = QInputDialog.getText(
            self,
            "OpenAI API Key",
            "Enter your OpenAI API Key (sk-...):",
            QLineEdit.EchoMode.Password,
        )
        if ok and key:
            self.config["openai_api_key"] = key.strip()
            if "openai_model" not in self.config:
                self.config["openai_model"] = "gpt-4o"
            self.save_config()
            QMessageBox.information(self, "API Key Saved", "API key has been saved securely.")

    def build_menus(self):
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")

        new_action = QAction("&New", self)
        new_action.setShortcut(QKeySequence.StandardKey.New)
        new_action.triggered.connect(self.new_tab)
        file_menu.addAction(new_action)

        open_action = QAction("&Open...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)

        save_action = QAction("&Save", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self.save_file)
        file_menu.addAction(save_action)

        save_as_action = QAction("Save &As...", self)
        save_as_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save_as_action.triggered.connect(self.save_file_as)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()

        print_action = QAction("&Print...", self)
        print_action.setShortcut(QKeySequence.StandardKey.Print)
        print_action.triggered.connect(self.print_file)
        file_menu.addAction(print_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        edit_menu = menu_bar.addMenu("&Edit")

        undo_action = QAction("&Undo", self)
        undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        undo_action.triggered.connect(self.current_editor().undo)
        edit_menu.addAction(undo_action)

        redo_action = QAction("&Redo", self)
        redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        redo_action.triggered.connect(self.current_editor().redo)
        edit_menu.addAction(redo_action)

        edit_menu.addSeparator()

        cut_action = QAction("Cu&t", self)
        cut_action.setShortcut(QKeySequence.StandardKey.Cut)
        cut_action.triggered.connect(self.current_editor().cut)
        edit_menu.addAction(cut_action)

        copy_action = QAction("&Copy", self)
        copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        copy_action.triggered.connect(self.current_editor().copy)
        edit_menu.addAction(copy_action)

        paste_action = QAction("&Paste", self)
        paste_action.setShortcut(QKeySequence.StandardKey.Paste)
        paste_action.triggered.connect(self.current_editor().paste)
        edit_menu.addAction(paste_action)

        delete_action = QAction("&Delete", self)
        delete_action.setShortcut(QKeySequence("Del"))
        delete_action.triggered.connect(self.delete_selection)
        edit_menu.addAction(delete_action)

        edit_menu.addSeparator()

        find_action = QAction("&Find...", self)
        find_action.setShortcut(QKeySequence.StandardKey.Find)
        find_action.triggered.connect(self.find_replace)
        edit_menu.addAction(find_action)

        replace_action = QAction("R&eplace...", self)
        replace_action.setShortcut(QKeySequence.StandardKey.Replace)
        replace_action.triggered.connect(self.find_replace)
        edit_menu.addAction(replace_action)

        edit_menu.addSeparator()

        time_date_action = QAction("Time/&Date", self)
        time_date_action.setShortcut(QKeySequence("F5"))
        time_date_action.triggered.connect(self.insert_time_date)
        edit_menu.addAction(time_date_action)

        format_menu = menu_bar.addMenu("F&ormat")

        self.word_wrap_action = QAction("&Word Wrap", self, checkable=True, checked=True)
        self.word_wrap_action.triggered.connect(self.toggle_word_wrap)
        format_menu.addAction(self.word_wrap_action)

        menu_bar.addMenu("&View")

        help_menu = menu_bar.addMenu("&Help")

        api_config_action = QAction("Configure &API", self)
        api_config_action.triggered.connect(self.configure_api_key)
        help_menu.addAction(api_config_action)

        about_action = QAction("&About Notepad_AI", self)
        about_action.triggered.connect(self.about)
        help_menu.addAction(about_action)

    def new_tab(self):
        editor = NotepadTextEdit()
        editor.setAcceptRichText(False)
        editor.textChanged.connect(self.on_text_changed)
        editor.ai_triggered.connect(self.trigger_ai_for_current_tab)

        if self.word_wrap_enabled:
            editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        else:
            editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

        tab = NotepadTab(editor)
        index = self.tabs.addTab(editor, "Untitled")
        self.tabs.setCurrentIndex(index)
        self.tabs.setTabToolTip(index, "")
        setattr(editor, "_notepad_tab", tab)

        self.update_window_title()

    def close_tab(self, index: int):
        if index < 0:
            return
        widget = self.tabs.widget(index)
        tab = self.get_tab_by_widget(widget)
        if tab and not self.maybe_save_tab(tab):
            return
        self.tabs.removeTab(index)
        if self.tabs.count() == 0:
            self.new_tab()

    def current_tab(self) -> NotepadTab:
        widget = self.tabs.currentWidget()
        return self.get_tab_by_widget(widget)

    def current_editor(self) -> NotepadTextEdit:
        tab = self.current_tab()
        return tab.editor if tab else None

    def get_tab_by_widget(self, widget: QWidget) -> Optional[NotepadTab]:
        if widget is None:
            return None
        if not hasattr(widget, "_notepad_tab"):
            setattr(widget, "_notepad_tab", NotepadTab(widget))
        return getattr(widget, "_notepad_tab")

    def on_text_changed(self):
        tab = self.current_tab()
        if not tab:
            return
        if not tab.dirty:
            tab.dirty = True
            index = self.tabs.indexOf(tab.editor)
            if index != -1:
                title = self.tabs.tabText(index)
                if not title.endswith("*"):
                    self.tabs.setTabText(index, title + "*")
        self.update_status_bar()

    def update_status_bar(self):
        editor = self.current_editor()
        if not editor:
            return
        cursor = editor.textCursor()
        line = cursor.blockNumber() + 1
        col = cursor.columnNumber() + 1
        self.status_bar.showMessage(f"Ln {line}, Col {col}")

    def update_window_title(self):
        tab = self.current_tab()
        if not tab:
            self.setWindowTitle(APP_NAME)
            return
        if tab.file_path:
            title = str(tab.file_path)
        else:
            title = "Untitled"
        if tab.dirty:
            title += " *"
        self.setWindowTitle(f"{title} - {APP_NAME}")

    def open_file(self):
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Open",
            "",
            "Text Files (*.txt);;All Files (*)",
        )
        if not path_str:
            return

        path = Path(path_str)
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            QMessageBox.warning(self, "Open Error", f"Could not open file:\n{e}")
            return

        editor = NotepadTextEdit()
        editor.setAcceptRichText(False)
        editor.setPlainText(text)
        editor.textChanged.connect(self.on_text_changed)
        editor.ai_triggered.connect(self.trigger_ai_for_current_tab)

        if self.word_wrap_enabled:
            editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        else:
            editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

        tab = NotepadTab(editor)
        tab.file_path = path
        tab.dirty = False

        lines = text.splitlines()
        if lines and lines[0].strip() == ".LOG":
            tab.log_mode = True
            timestamp = datetime.now().strftime("%H:%M %m/%d/%Y")
            editor.moveCursor(editor.textCursor().MoveOperation.End)
            editor.insertPlainText(f"\n{timestamp}\n")
            tab.dirty = True

        setattr(editor, "_notepad_tab", tab)

        index = self.tabs.addTab(editor, path.name)
        self.tabs.setTabToolTip(index, str(path))
        self.tabs.setCurrentIndex(index)
        self.update_window_title()
