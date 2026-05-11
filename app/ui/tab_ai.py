"""
AI tab — natural-language → SQL → results, with conversation memory and a
library of saved queries.

Features
--------
* Chat with the configured LLM (Claude / OpenAI / Gemini). Full history is
  preserved per session so the AI can ask clarifying questions and refine.
* Click **New Chat** to reset the conversation.
* Generated SQL is shown in an editable panel; the user can tweak then re-run.
* **Save** any working query to the user's library with a name + description.
* Saved queries are listed in the left sidebar with **Run / Edit / Delete**.
* SQL parameters use `{name}` placeholders. When running a saved query, the
  user is prompted for each parameter value (defaults remembered per session).
* The AI sees the names + descriptions of saved queries (no SQL bodies) so it
  can suggest reusing them — keeps tokens low while building "memory".

Safety
------
Only single-statement SELECT (or WITH ... SELECT) queries are executed.
INSERT/UPDATE/DELETE/DROP/TRUNCATE/EXEC/MERGE/ALTER/CREATE/GRANT/REVOKE/XP_/SP_
are rejected by the validator before any database hit.
"""

from __future__ import annotations

import re
from typing import Optional

import pandas as pd
from PyQt6.QtCore import Qt, QObject, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.ai.providers import call_provider, AIError, DEFAULT_MODELS
from app.ai.schema import build_system_prompt
from app.data import store
from app.data.db import read_dataframe
from app.ui.widgets import DataTable, SectionTitle
import app.ui.theme as theme


# ---------------------------------------------------------------------------
# SQL parsing / validation
# ---------------------------------------------------------------------------

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|EXEC(?:UTE)?|MERGE|ALTER|CREATE|GRANT|REVOKE|XP_|SP_)\b",
    re.IGNORECASE,
)
_STARTS_SELECT = re.compile(r"^\s*(WITH|SELECT)\b", re.IGNORECASE)
_PARAM_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def parse_response(text: str) -> tuple[str, str]:
    """Return ('question', text), ('sql', text), ('remember', fact) or ('text', text)."""
    s = text.strip()
    # Strip code fences if present
    if "```" in s:
        # Pick the first fenced block
        m = re.search(r"```(?:sql)?\s*(.+?)```", s, re.DOTALL | re.IGNORECASE)
        if m:
            s = m.group(1).strip()

    # Look for explicit markers from our prompt format
    rem_match = re.search(r"(?im)^\s*REMEMBER:\s*(.+)$", s)
    q_match = re.search(r"(?im)^\s*QUESTION:\s*(.+)$", s)
    sql_match = re.search(r"(?is)^\s*SQL:\s*(.+)$", s)
    if rem_match:
        return ("remember", rem_match.group(1).strip().rstrip("."))
    if sql_match:
        return ("sql", _clean_sql(sql_match.group(1)))
    if q_match:
        # Use everything starting from QUESTION: line
        return ("question", q_match.group(1).strip())
    # Fallback: if the body starts with SELECT/WITH, treat as SQL
    if _STARTS_SELECT.match(s):
        return ("sql", _clean_sql(s))
    return ("text", s)


def _clean_sql(s: str) -> str:
    s = s.strip().rstrip(";")
    # Drop any leading prose before SELECT/WITH
    m = re.search(r"(?is)\b(SELECT|WITH)\b.*", s)
    return m.group(0).strip() if m else s


# Pseudo-functions that appear in the schema prompt as shorthand. If the AI
# pastes them literally instead of expanding them inline, SQL Server throws
# error 195 ("not a recognized built-in function name"). Catch them up-front
# and feed a precise correction request back into the conversation.
_PSEUDO_FUNCS = re.compile(r"\b(to_sy|convert_to_sy|to_square_yards)\s*\(", re.IGNORECASE)


def validate_sql(sql: str) -> Optional[str]:
    if not sql:
        return "Empty SQL."
    if not _STARTS_SELECT.match(sql):
        return "Only SELECT (or WITH ... SELECT) queries are allowed."
    if _FORBIDDEN.search(sql):
        return "Query contains a forbidden keyword (write/DDL operations are blocked)."
    if ";" in sql:
        return "Multiple statements are not allowed (remove ';')."
    m = _PSEUDO_FUNCS.search(sql)
    if m:
        return (
            f"`{m.group(1)}(...)` is shorthand from the schema prompt — it is NOT a real "
            "SQL Server function. Expand the UoM-to-SY CASE block inline instead."
        )
    return None


def find_parameters(sql: str) -> list[str]:
    """Return ordered list of unique {param_name} placeholders."""
    seen: list[str] = []
    for m in _PARAM_RE.finditer(sql):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def apply_parameters(sql: str, values: dict[str, str]) -> str:
    def _sub(m: re.Match) -> str:
        return values.get(m.group(1), m.group(0))
    return _PARAM_RE.sub(_sub, sql)


# ---------------------------------------------------------------------------
# Background worker — calls the provider on the AI thread
# ---------------------------------------------------------------------------

class _AIWorker(QObject):
    finished = pyqtSignal(object)  # dict: {ok: bool, text: str, error: str}

    def __init__(self, provider: str, api_key: str, model: str, system: str, history: list[dict]):
        super().__init__()
        self._provider = provider
        self._api_key = api_key
        self._model = model
        self._system = system
        self._history = history

    def run(self) -> None:
        result = {"ok": False, "text": "", "error": None}
        try:
            result["text"] = call_provider(
                self._provider, self._api_key, self._model,
                self._system, self._history,
            )
            result["ok"] = True
        except AIError as e:
            result["error"] = str(e)
        except Exception as e:  # noqa: BLE001
            result["error"] = f"{type(e).__name__}: {e}"
        self.finished.emit(result)


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

class SaveQueryDialog(QDialog):
    """Prompt for name + description (and optionally edit the SQL)."""

    def __init__(self, sql: str, name: str = "", description: str = "",
                 title: str = "Save Query", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._name = QLineEdit(name)
        self._name.setPlaceholderText("e.g. Top 20 SKUs by sales")
        self._desc = QLineEdit(description)
        self._desc.setPlaceholderText("Short description (shown in the sidebar tooltip)")
        form.addRow("Name:", self._name)
        form.addRow("Description:", self._desc)
        layout.addLayout(form)
        layout.addWidget(QLabel("SQL (use <code>{param_name}</code> for runtime parameters):"))
        self._sql = QTextEdit()
        self._sql.setPlainText(sql)
        self._sql.setStyleSheet(
            f"background:{theme.get('bg_card')}; color:{theme.get('text')}; "
            f"font-family:Consolas,'Courier New',monospace; font-size:12px;"
        )
        self._sql.setMinimumHeight(180)
        layout.addWidget(self._sql)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def values(self) -> tuple[str, str, str]:
        return (
            self._name.text().strip(),
            self._desc.text().strip(),
            self._sql.toPlainText().strip(),
        )


class ParameterDialog(QDialog):
    """Ask the user for {param} values before running a parameterized query."""

    def __init__(self, params: list[str], defaults: dict[str, str],
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Query Parameters")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Provide a value for each parameter:"))
        form = QFormLayout()
        self._fields: dict[str, QLineEdit] = {}
        for p in params:
            edit = QLineEdit(defaults.get(p, ""))
            edit.setPlaceholderText(f"value for {{{p}}}")
            form.addRow(f"{p}:", edit)
            self._fields[p] = edit
        layout.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def values(self) -> dict[str, str]:
        return {p: f.text().strip() for p, f in self._fields.items()}


# ---------------------------------------------------------------------------
# Tab
# ---------------------------------------------------------------------------

class AITab(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._thread: Optional[QThread] = None
        self._worker: Optional[_AIWorker] = None
        # Conversation history (provider format: role + content)
        self._history: list[dict] = []
        # Remembered parameter defaults (per-session)
        self._param_defaults: dict[str, str] = {}
        # Number of consecutive auto-retries triggered by validation/SQL errors,
        # capped at MAX_AUTO_RETRIES per user turn to prevent infinite loops.
        self._auto_retries: int = 0
        # Zero-row interactive diagnostic state
        self._last_zero_sql: str = ""
        self._diagnostic_remaining: int = 1   # one diagnostic round per user turn
        self._build_ui()
        self._refresh_saved_list()
        self._refresh_notes_list()

    # ---- UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        outer.addWidget(SectionTitle("🤖  AI Query"))

        # Top-level horizontal splitter: sidebar | main
        h = QSplitter(Qt.Orientation.Horizontal)
        h.setChildrenCollapsible(False)
        outer.addWidget(h, stretch=1)

        # ---------- Sidebar: saved queries + memory (vertical split) ----------
        side_split = QSplitter(Qt.Orientation.Vertical)
        side_split.setChildrenCollapsible(False)

        # Saved queries
        sq_wrap = QWidget()
        sl = QVBoxLayout(sq_wrap)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(6)
        sl.addWidget(SectionTitle("Saved Queries"))
        self._saved_list = QListWidget()
        self._saved_list.itemDoubleClicked.connect(lambda *_: self._run_saved())
        sl.addWidget(self._saved_list, stretch=1)
        side_btns = QHBoxLayout()
        side_btns.setSpacing(4)
        self._btn_run_saved = QPushButton("▶ Run")
        self._btn_run_saved.clicked.connect(self._run_saved)
        self._btn_edit_saved = QPushButton("Edit")
        self._btn_edit_saved.clicked.connect(self._edit_saved)
        self._btn_del_saved = QPushButton("Delete")
        self._btn_del_saved.setObjectName("danger")
        self._btn_del_saved.clicked.connect(self._delete_saved)
        side_btns.addWidget(self._btn_run_saved)
        side_btns.addWidget(self._btn_edit_saved)
        side_btns.addWidget(self._btn_del_saved)
        sl.addLayout(side_btns)
        side_split.addWidget(sq_wrap)

        # Memory bank (persistent AI notes)
        mem_wrap = QWidget()
        ml_side = QVBoxLayout(mem_wrap)
        ml_side.setContentsMargins(0, 0, 0, 0)
        ml_side.setSpacing(6)
        ml_side.addWidget(SectionTitle("🧠  Memory"))
        mem_hint = QLabel(
            "Persistent rules the AI applies on every turn. "
            "Type <i>“remember…”</i> in chat or use the buttons."
        )
        mem_hint.setWordWrap(True)
        mem_hint.setStyleSheet(f"color:{theme.get('text_muted')}; font-size:11px;")
        ml_side.addWidget(mem_hint)
        self._notes_list = QListWidget()
        self._notes_list.setWordWrap(True)
        self._notes_list.itemDoubleClicked.connect(lambda *_: self._edit_note())
        ml_side.addWidget(self._notes_list, stretch=1)
        mem_btns = QHBoxLayout()
        mem_btns.setSpacing(4)
        self._btn_add_note = QPushButton("+ Add")
        self._btn_add_note.clicked.connect(self._add_note)
        self._btn_edit_note = QPushButton("Edit")
        self._btn_edit_note.clicked.connect(self._edit_note)
        self._btn_del_note = QPushButton("Delete")
        self._btn_del_note.setObjectName("danger")
        self._btn_del_note.clicked.connect(self._delete_note)
        mem_btns.addWidget(self._btn_add_note)
        mem_btns.addWidget(self._btn_edit_note)
        mem_btns.addWidget(self._btn_del_note)
        ml_side.addLayout(mem_btns)
        side_split.addWidget(mem_wrap)

        side_split.setSizes([280, 280])
        h.addWidget(side_split)

        # ---------- Main column ----------
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(8)

        # Hint
        info = QLabel(
            "Ask in plain English — for example: "
            "<i>“top 20 SKUs by sales last 90 days”</i> · "
            "<i>“open POs for cost center 010 due this month”</i>. "
            "If the AI is unsure it will ask a clarifying question. "
            "Teach it persistent rules with phrases like "
            "<i>“remember to exclude supplier 001 when discussing suppliers”</i> — "
            "saved entries appear in the <b>Memory</b> panel and apply to every future chat."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color:{theme.get('text_muted')};")
        ml.addWidget(info)

        # Input row
        in_row = QHBoxLayout()
        in_row.setSpacing(6)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type your question and press Enter…")
        self._input.returnPressed.connect(self._on_send)
        self._input.setMinimumHeight(34)
        self._send_btn = QPushButton("Ask")
        self._send_btn.clicked.connect(self._on_send)
        self._send_btn.setMinimumHeight(34)
        self._new_chat_btn = QPushButton("New Chat")
        self._new_chat_btn.setMinimumHeight(34)
        self._new_chat_btn.clicked.connect(self._on_new_chat)
        in_row.addWidget(self._input, stretch=1)
        in_row.addWidget(self._send_btn)
        in_row.addWidget(self._new_chat_btn)
        ml.addLayout(in_row)

        # Status label
        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color:{theme.get('text_muted')};")
        ml.addWidget(self._status)

        # Zero-row diagnostic panel (hidden until needed)
        self._zero_panel = QFrame()
        self._zero_panel.setObjectName("zeroRowPanel")
        self._zero_panel.setStyleSheet(
            f"#zeroRowPanel {{ background:{theme.get('bg_card')}; "
            f"border:1px solid {theme.get('warning')}; border-radius:6px; padding:8px; }}"
        )
        zp = QHBoxLayout(self._zero_panel)
        zp.setContentsMargins(10, 6, 10, 6)
        zp.setSpacing(8)
        self._zero_label = QLabel(
            "⚠  The query returned <b>0 rows</b>. Were you expecting results?"
        )
        self._zero_label.setStyleSheet(f"color:{theme.get('text')};")
        zp.addWidget(self._zero_label, stretch=1)
        self._btn_zero_yes = QPushButton("Yes — diagnose")
        self._btn_zero_yes.clicked.connect(self._on_zero_diagnose)
        zp.addWidget(self._btn_zero_yes)
        self._btn_zero_no = QPushButton("No, that’s fine")
        self._btn_zero_no.clicked.connect(self._hide_zero_panel)
        zp.addWidget(self._btn_zero_no)
        self._zero_panel.setVisible(False)
        ml.addWidget(self._zero_panel)

        # Vertical splitter: transcript | sql | results
        v = QSplitter(Qt.Orientation.Vertical)
        v.setChildrenCollapsible(False)

        # Transcript
        tr_wrap = QWidget()
        tw = QVBoxLayout(tr_wrap)
        tw.setContentsMargins(0, 0, 0, 0)
        tw.setSpacing(4)
        tw.addWidget(SectionTitle("Conversation"))
        self._transcript = QTextEdit()
        self._transcript.setReadOnly(True)
        self._transcript.setStyleSheet(
            f"background:{theme.get('bg_card')}; color:{theme.get('text')}; "
            f"font-size:13px;"
        )
        tw.addWidget(self._transcript)
        v.addWidget(tr_wrap)

        # SQL panel
        sql_wrap = QWidget()
        sw = QVBoxLayout(sql_wrap)
        sw.setContentsMargins(0, 0, 0, 0)
        sw.setSpacing(4)
        sw.addWidget(SectionTitle("Generated SQL"))
        self._sql_view = QTextEdit()
        self._sql_view.setPlaceholderText("Generated SQL will appear here…")
        self._sql_view.setStyleSheet(
            f"background:{theme.get('bg_card')}; color:{theme.get('text')}; "
            f"font-family:Consolas,'Courier New',monospace; font-size:12px;"
        )
        sw.addWidget(self._sql_view)
        sql_btns = QHBoxLayout()
        sql_btns.addStretch(1)
        self._save_btn = QPushButton("💾 Save Query")
        self._save_btn.clicked.connect(self._on_save_current)
        sql_btns.addWidget(self._save_btn)
        self._run_btn = QPushButton("▶ Run SQL")
        self._run_btn.clicked.connect(self._on_run_manual)
        sql_btns.addWidget(self._run_btn)
        sw.addLayout(sql_btns)
        v.addWidget(sql_wrap)

        # Results
        res_wrap = QWidget()
        rw = QVBoxLayout(res_wrap)
        rw.setContentsMargins(0, 0, 0, 0)
        rw.setSpacing(4)
        rw.addWidget(SectionTitle("Results"))
        self._table = DataTable([], table_id="ai_results")
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        rw.addWidget(self._table)
        v.addWidget(res_wrap)

        v.setSizes([200, 220, 320])
        ml.addWidget(v, stretch=1)

        h.addWidget(main)
        h.setSizes([240, 900])

    # ---- Saved queries ----------------------------------------------------

    def _refresh_saved_list(self) -> None:
        self._saved_list.clear()
        for q in store.get_saved_queries():
            item = QListWidgetItem(q["name"])
            tip = q["description"] or "(no description)"
            params = find_parameters(q["sql"])
            if params:
                tip += "\n\nParameters: " + ", ".join("{" + p + "}" for p in params)
            item.setToolTip(tip)
            item.setData(Qt.ItemDataRole.UserRole, q)
            self._saved_list.addItem(item)

    def _selected_saved(self) -> Optional[dict]:
        it = self._saved_list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _run_saved(self) -> None:
        q = self._selected_saved()
        if not q:
            return
        sql = q["sql"]
        params = find_parameters(sql)
        if params:
            dlg = ParameterDialog(params, self._param_defaults, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            values = dlg.values()
            self._param_defaults.update(values)
            sql = apply_parameters(sql, values)
        self._sql_view.setPlainText(sql)
        self._execute_sql(sql, source=f"saved: {q['name']}")

    def _edit_saved(self) -> None:
        q = self._selected_saved()
        if not q:
            return
        dlg = SaveQueryDialog(q["sql"], q["name"], q["description"],
                              title="Edit Saved Query", parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name, desc, sql = dlg.values()
            if not name or not sql:
                QMessageBox.warning(self, "Missing fields", "Name and SQL are both required.")
                return
            err = validate_sql(_clean_sql(re.sub(_PARAM_RE, "1", sql)))
            if err:
                QMessageBox.warning(self, "Invalid SQL", err)
                return
            store.update_saved_query(q["id"], name, desc, sql)
            self._refresh_saved_list()

    def _delete_saved(self) -> None:
        q = self._selected_saved()
        if not q:
            return
        if QMessageBox.question(
            self, "Delete saved query",
            f"Delete '{q['name']}'?\n\nThis cannot be undone.",
        ) != QMessageBox.StandardButton.Yes:
            return
        store.delete_saved_query(q["id"])
        self._refresh_saved_list()

    # ---- Memory / notes ---------------------------------------------------

    def _refresh_notes_list(self) -> None:
        self._notes_list.clear()
        notes = store.get_ai_notes()
        if not notes:
            placeholder = QListWidgetItem("(no memory entries yet)")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self._notes_list.addItem(placeholder)
            return
        for n in notes:
            item = QListWidgetItem(n["text"])
            item.setToolTip(n["text"] + f"\n\nAdded: {n.get('created', '')}")
            item.setData(Qt.ItemDataRole.UserRole, n)
            self._notes_list.addItem(item)

    def _selected_note(self) -> Optional[dict]:
        it = self._notes_list.currentItem()
        if not it:
            return None
        return it.data(Qt.ItemDataRole.UserRole)

    def _add_note(self) -> None:
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getMultiLineText(
            self, "Add memory entry",
            "Enter a rule, fact, or preference the AI should always apply:",
            "",
        )
        if ok and text.strip():
            store.add_ai_note(text.strip())
            self._refresh_notes_list()
            self._set_status("✓ Memory entry added.", "success")

    def _edit_note(self) -> None:
        from PyQt6.QtWidgets import QInputDialog
        n = self._selected_note()
        if not n:
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "Edit memory entry", "Update text:", n["text"],
        )
        if ok and text.strip():
            store.update_ai_note(n["id"], text.strip())
            self._refresh_notes_list()

    def _delete_note(self) -> None:
        n = self._selected_note()
        if not n:
            return
        if QMessageBox.question(
            self, "Delete memory entry",
            f"Delete this note?\n\n• {n['text']}\n\nThis cannot be undone.",
        ) != QMessageBox.StandardButton.Yes:
            return
        store.delete_ai_note(n["id"])
        self._refresh_notes_list()

    def _on_save_current(self) -> None:
        sql = self._sql_view.toPlainText().strip()
        if not sql:
            QMessageBox.information(self, "Nothing to save", "There is no SQL to save yet.")
            return
        # Validate (with placeholders substituted out) before saving
        err = validate_sql(_clean_sql(_PARAM_RE.sub("1", sql)))
        if err:
            if QMessageBox.question(
                self, "Invalid SQL",
                f"{err}\n\nSave anyway?",
            ) != QMessageBox.StandardButton.Yes:
                return
        dlg = SaveQueryDialog(sql, "", "", title="Save Query", parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, desc, sql2 = dlg.values()
        if not name or not sql2:
            QMessageBox.warning(self, "Missing fields", "Name and SQL are both required.")
            return
        store.add_saved_query(name, desc, sql2)
        self._refresh_saved_list()
        self._set_status(f"✓ Saved as “{name}”.", "success")

    # ---- Chat -------------------------------------------------------------

    def _on_new_chat(self) -> None:
        self._history = []
        self._transcript.clear()
        self._sql_view.clear()
        self._diagnostic_remaining = 1
        self._hide_zero_panel()
        self._set_status("New chat started.", "muted")

    def _append_transcript(self, role: str, html_body: str) -> None:
        if role == "user":
            color = theme.get("info")
            label = "You"
        elif role == "assistant":
            color = theme.get("success")
            label = "AI"
        else:
            color = theme.get("text_muted")
            label = role
        self._transcript.append(
            f"<div style='margin-top:8px'>"
            f"<span style='color:{color}; font-weight:700'>{label}:</span> {html_body}"
            f"</div>"
        )
        sb = self._transcript.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_send(self) -> None:
        question = self._input.text().strip()
        if not question:
            return
        cfg = store.get_ai_config()
        provider = cfg.get("provider", "anthropic")
        api_key = cfg.get("api_key", "")
        model = cfg.get("model") or DEFAULT_MODELS.get(provider, "")
        if not api_key:
            QMessageBox.warning(
                self, "AI not configured",
                "No API key is configured.\n\nGo to the Settings tab → AI Provider "
                "section, paste your API key for the chosen provider, then click Save."
            )
            return
        try:
            if self._thread is not None and self._thread.isRunning():
                self._set_status("Already running — please wait…", "muted")
                return
        except RuntimeError:
            self._thread = None
            self._worker = None

        # Append user turn to history + transcript
        self._history.append({"role": "user", "content": question})
        self._append_transcript("user", _escape_html(question))
        self._input.clear()
        # Reset retry counter — new user-initiated turn
        self._auto_retries = 0
        self._diagnostic_remaining = 1
        self._hide_zero_panel()

        # Build system prompt with saved-query awareness + persistent memory notes
        sys_prompt = build_system_prompt(
            store.get_saved_queries(),
            store.get_ai_notes(),
        )

        self._start_worker(provider, api_key, model, sys_prompt, f"Asking {provider}…")

    # Hard cap on automatic retry rounds per user turn (prevents loops).
    MAX_AUTO_RETRIES = 2

    def _start_worker(self, provider: str, api_key: str, model: str,
                      sys_prompt: str, status_msg: str) -> None:
        self._set_busy(True, status_msg)
        self._thread = QThread(self)
        self._worker = _AIWorker(provider, api_key, model, sys_prompt, list(self._history))
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _request_ai_fix(self, error_msg: str, kind: str) -> bool:
        """Append an error report to history and (if under retry cap) re-prompt the AI.

        Returns True if a retry was kicked off, False if the cap was hit.
        """
        self._history.append({
            "role": "user",
            "content": (
                f"The SQL you produced failed with this {kind} error:\n{error_msg}\n\n"
                "Please reply with corrected SQL only — no prose."
            ),
        })
        if self._auto_retries >= self.MAX_AUTO_RETRIES:
            self._append_transcript(
                "user",
                f"<i>(auto) reported {kind} error to AI \u2014 retry limit reached, "
                "type your next message to continue.</i>",
            )
            return False
        self._auto_retries += 1
        self._append_transcript(
            "user",
            f"<i>(auto) reported {kind} error to AI \u2014 asking for a fix "
            f"(attempt {self._auto_retries}/{self.MAX_AUTO_RETRIES}).</i>",
        )
        cfg = store.get_ai_config()
        provider = cfg.get("provider", "anthropic")
        api_key = cfg.get("api_key", "")
        model = cfg.get("model") or DEFAULT_MODELS.get(provider, "")
        sys_prompt = build_system_prompt(
            store.get_saved_queries(),
            store.get_ai_notes(),
        )
        self._start_worker(provider, api_key, model, sys_prompt,
                           f"Auto-retrying \u2014 asking {provider} to fix the SQL\u2026")
        return True


    def _on_finished(self, result: dict) -> None:
        self._set_busy(False)
        if not result.get("ok"):
            err = result.get("error") or "Unknown error."
            self._append_transcript("assistant",
                                    f"<span style='color:{theme.get('danger')}'>⚠ {_escape_html(err)}</span>")
            self._set_status(err, "danger")
            return

        text = result.get("text", "").strip()
        kind, body = parse_response(text)
        # Always record full assistant turn in history (so follow-ups work)
        self._history.append({"role": "assistant", "content": text})

        if kind == "question":
            self._append_transcript("assistant", _escape_html(body))
            self._set_status("AI asked a clarifying question — type your answer above.", "muted")
            return

        if kind == "remember":
            saved = store.add_ai_note(body)
            self._refresh_notes_list()
            if saved is None:
                self._append_transcript(
                    "assistant",
                    f"<span style='color:{theme.get('text_muted')}'>(nothing to remember)</span>",
                )
                self._set_status("Memory unchanged.", "muted")
            else:
                self._append_transcript(
                    "assistant",
                    f"<span style='color:{theme.get('success')}'>🧠 Saved to memory:</span> "
                    f"{_escape_html(body)}"
                )
                self._set_status("✓ Added to AI memory — will apply to every future turn.", "success")
            return

        if kind == "sql":
            self._sql_view.setPlainText(body)
            self._append_transcript(
                "assistant",
                f"<i>Generated SQL ({len(body)} chars). Running…</i>"
                f"<pre style='margin:4px 0; padding:6px; background:{theme.get('bg')}; "
                f"border:1px solid {theme.get('border')}; "
                f"font-family:Consolas,monospace; font-size:11px; white-space:pre-wrap;'>"
                f"{_escape_html(body[:1500])}{'…' if len(body) > 1500 else ''}</pre>"
            )
            self._execute_sql(body, source="AI")
            return

        # Plain text fallback
        self._append_transcript("assistant", _escape_html(body))

    def _on_thread_finished(self) -> None:
        self._thread = None
        self._worker = None

    # ---- Zero-row interactive diagnostic ---------------------------------

    def _show_zero_panel(self) -> None:
        self._zero_panel.setVisible(True)
        self._btn_zero_yes.setEnabled(True)
        self._btn_zero_no.setEnabled(True)

    def _hide_zero_panel(self) -> None:
        self._zero_panel.setVisible(False)

    def _on_zero_diagnose(self) -> None:
        """User confirmed they expected results — ask the AI to break down the query."""
        if not self._last_zero_sql or self._diagnostic_remaining <= 0:
            self._hide_zero_panel()
            return
        self._diagnostic_remaining -= 1
        self._hide_zero_panel()
        # Compact, token-efficient diagnostic prompt. The AI already has the
        # failed SQL in history; we only nudge with the protocol reminder.
        self._history.append({
            "role": "user",
            "content": (
                "That query returned 0 rows but I expected results. Use the "
                "ZERO-ROW DIAGNOSTIC PROTOCOL: reply with a single SQL that "
                "counts rows in each CTE and the final join (UNION ALL of "
                "SELECT '<step>' AS step, COUNT(*) AS rows FROM (...) x). "
                "No prose."
            ),
        })
        self._append_transcript(
            "user",
            "<i>(diagnose) asking the AI to count rows per CTE…</i>",
        )
        cfg = store.get_ai_config()
        provider = cfg.get("provider", "anthropic")
        api_key = cfg.get("api_key", "")
        model = cfg.get("model") or DEFAULT_MODELS.get(provider, "")
        sys_prompt = build_system_prompt(
            store.get_saved_queries(), store.get_ai_notes(),
        )
        self._start_worker(provider, api_key, model, sys_prompt,
                           f"Diagnosing zero-row result with {provider}…")

    # ---- SQL execution ----------------------------------------------------

    def _on_run_manual(self) -> None:
        sql = self._sql_view.toPlainText().strip()
        if not sql:
            return
        # If there are unsubstituted parameters, prompt first
        params = find_parameters(sql)
        if params:
            dlg = ParameterDialog(params, self._param_defaults, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            values = dlg.values()
            self._param_defaults.update(values)
            sql = apply_parameters(sql, values)
        self._execute_sql(sql, source="manual")

    def _execute_sql(self, sql: str, source: str = "") -> None:
        sql = _clean_sql(sql)
        err = validate_sql(sql)
        if err:
            self._set_status(f"⚠ {err}", "danger")
            if source == "AI":
                self._request_ai_fix(err, kind="validation")
            return
        self._set_busy(True, f"Running query ({source})…")
        try:
            df = read_dataframe(sql)
            self._render_results(df)
            self._set_status(f"✓ {len(df):,} rows returned.", "success")
            # Offer interactive diagnostic when AI's query came back empty
            if len(df) == 0 and source == "AI" and self._diagnostic_remaining > 0:
                self._last_zero_sql = sql
                self._show_zero_panel()
            else:
                self._hide_zero_panel()
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"
            self._set_status(f"⚠ {msg}", "danger")
            if source == "AI":
                self._set_busy(False)
                self._request_ai_fix(msg, kind="execution")
                return
        finally:
            self._set_busy(False)

    def _render_results(self, df: pd.DataFrame) -> None:
        cols = [str(c) for c in df.columns]
        self._table.set_columns(cols)
        rows: list[list[str]] = []
        for _, row in df.iterrows():
            cells: list[str] = []
            for c in cols:
                v = row[c]
                if pd.isna(v):
                    cells.append("—")
                elif isinstance(v, float):
                    cells.append(f"{v:,.2f}")
                elif isinstance(v, (int,)):
                    cells.append(f"{v:,}")
                else:
                    cells.append(str(v))
            rows.append(cells)
        self._table.populate(rows)

    # ---- Helpers ----------------------------------------------------------

    def _set_status(self, msg: str, kind: str = "muted") -> None:
        color_key = {"success": "success", "danger": "danger", "warning": "warning"}.get(kind, "text_muted")
        self._status.setStyleSheet(f"color:{theme.get(color_key)};")
        self._status.setText(msg)

    def _set_busy(self, busy: bool, msg: str = "") -> None:
        self._send_btn.setEnabled(not busy)
        self._run_btn.setEnabled(not busy)
        self._save_btn.setEnabled(not busy)
        self._input.setEnabled(not busy)
        self._btn_run_saved.setEnabled(not busy)
        self._btn_add_note.setEnabled(not busy)
        self._btn_edit_note.setEnabled(not busy)
        self._btn_del_note.setEnabled(not busy)
        if busy:
            self._set_status(msg or "Working…", "muted")

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            if self._thread is not None and self._thread.isRunning():
                self._thread.quit()
                self._thread.wait(3000)
        except RuntimeError:
            pass
        super().closeEvent(event)


def _escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace("\n", "<br>")
    )
