"""Log pane: the pipeline's own log lines, with WARNING / ERROR highlighted."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit

LEVEL_FORMATS = {
    "WARNING": ("#8a5300", True),
    "ERROR": ("#b3261e", True),
    "CRITICAL": ("#b3261e", True),
    "DEBUG": ("#5a6472", False),
    "INFO": ("#1d2026", False),
}
MAX_LINES = 4000


class LogPane(QPlainTextEdit):
    """Read-only, selectable, monospaced, auto-scrolling log view."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setMaximumBlockCount(MAX_LINES)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        font = QFont("DejaVu Sans Mono, Monospace")
        font.setStyleHint(QFont.Monospace)
        font.setPointSizeF(9.5)
        self.setFont(font)
        self.setAccessibleName("Pipeline log")
        self.setPlaceholderText("The pipeline log appears here while a run is in progress "
                                "(the same lines that are written to "
                                "<diagnostics>/pipeline.log).")
        self._warnings = 0
        self._errors = 0

    @property
    def warning_count(self) -> int:
        return self._warnings

    @property
    def error_count(self) -> int:
        return self._errors

    def reset(self) -> None:
        self.clear()
        self._warnings = 0
        self._errors = 0

    def append_line(self, text: str, level: str = "") -> None:
        colour, bold = LEVEL_FORMATS.get(level.upper(), ("#1d2026", False))
        if level.upper() == "WARNING":
            self._warnings += 1
        elif level.upper() in ("ERROR", "CRITICAL"):
            self._errors += 1
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(colour))
        fmt.setFontWeight(QFont.DemiBold if bold else QFont.Normal)
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text + "\n", fmt)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
