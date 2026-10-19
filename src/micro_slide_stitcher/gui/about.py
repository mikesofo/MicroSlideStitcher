"""About dialog and support diagnostics for the optional desktop GUI."""
from __future__ import annotations

import platform
import sys

import cv2
import PySide6
from PySide6.QtCore import Qt, qVersion
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
                               QPlainTextEdit, QPushButton, QScrollArea, QToolButton,
                               QVBoxLayout, QWidget)

from .. import __version__

AUTHOR = "Michail Sofopoulos"
AFFILIATION = "Histopathology / Dermatopathology · Athens, Greece"
EMAIL = "msofopoulos@gmail.com"
DISCLAIMER = (
    "MicroSlideStitcher is not a validated diagnostic whole-slide-imaging device and must "
    "not be used for primary diagnosis or measurement. Geometry, colour and focus are not "
    "calibrated; always go back to the glass slide for diagnostic work."
)
MIT_LICENCE = """MIT License

Copyright (c) 2026 Michail Sofopoulos

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def diagnostics_text() -> str:
    """Return the useful environment block to include in a bug report."""
    frozen = bool(getattr(sys, "frozen", False))
    return "\n".join((
        f"MicroSlideStitcher {__version__} diagnostics",
        f"Python: {sys.version.split()[0]}",
        f"PySide6: {PySide6.__version__}",
        f"Qt: {qVersion()}",
        f"OpenCV: {cv2.__version__}",
        f"Platform: {platform.platform()}",
        f"Frozen PyInstaller build: {'yes' if frozen else 'no'}",
    ))


class AboutDialog(QDialog):
    """A compact, scroll-safe about and support panel."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About MicroSlideStitcher")
        self.setModal(False)
        self.setMinimumSize(420, 420)
        self.resize(610, 620)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget(self.scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(9)

        title = QLabel("MicroSlideStitcher", content)
        title_font = title.font()
        title_font.setPointSizeF(18)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        self.version_label = QLabel(f"Version {__version__}", content)
        self.version_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.version_label)

        self.author_label = QLabel(f"Author: {AUTHOR}", content)
        self.author_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.author_label)

        self.affiliation_label = QLabel(AFFILIATION, content)
        self.affiliation_label.setWordWrap(True)
        self.affiliation_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.affiliation_label)

        self.email_label = QLabel(f'Contact: <a href="mailto:{EMAIL}">{EMAIL}</a>', content)
        self.email_label.setOpenExternalLinks(True)
        self.email_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.email_label.setWordWrap(True)
        layout.addWidget(self.email_label)

        self.licence_label = QLabel("MIT Licence · © 2026 Michail Sofopoulos", content)
        self.licence_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.licence_label)

        disclaimer_frame = QFrame(content)
        disclaimer_frame.setObjectName("disclaimer")
        disclaimer_frame.setFrameShape(QFrame.StyledPanel)
        disclaimer_layout = QVBoxLayout(disclaimer_frame)
        disclaimer_layout.setContentsMargins(10, 9, 10, 9)
        disclaimer_layout.setSpacing(4)
        disclaimer_title = QLabel("Important diagnostic-use limitation", disclaimer_frame)
        disclaimer_title_font = disclaimer_title.font()
        disclaimer_title_font.setBold(True)
        disclaimer_title.setFont(disclaimer_title_font)
        disclaimer_layout.addWidget(disclaimer_title)
        self.disclaimer_label = QLabel(DISCLAIMER, disclaimer_frame)
        self.disclaimer_label.setWordWrap(True)
        self.disclaimer_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        disclaimer_layout.addWidget(self.disclaimer_label)
        layout.addWidget(disclaimer_frame)

        self.btn_show_licence = QToolButton(content)
        self.btn_show_licence.setText("Show licence")
        self.btn_show_licence.setCheckable(True)
        self.btn_show_licence.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.btn_show_licence.toggled.connect(self._toggle_licence)
        layout.addWidget(self.btn_show_licence, 0, Qt.AlignLeft)

        self.licence_text = QPlainTextEdit(content)
        self.licence_text.setObjectName("licence")
        self.licence_text.setPlainText(MIT_LICENCE)
        self.licence_text.setReadOnly(True)
        self.licence_text.setMinimumHeight(230)
        self.licence_text.hide()
        layout.addWidget(self.licence_text)

        diagnostics_heading = QLabel("Bug-report diagnostics", content)
        diagnostics_heading_font = diagnostics_heading.font()
        diagnostics_heading_font.setBold(True)
        diagnostics_heading.setFont(diagnostics_heading_font)
        layout.addWidget(diagnostics_heading)
        self.diagnostics_view = QPlainTextEdit(content)
        self.diagnostics_view.setPlainText(diagnostics_text())
        self.diagnostics_view.setReadOnly(True)
        self.diagnostics_view.setMinimumHeight(142)
        self.diagnostics_view.setMaximumHeight(142)
        layout.addWidget(self.diagnostics_view)

        diagnostics_row = QHBoxLayout()
        self.btn_copy_diagnostics = QPushButton("Copy diagnostics", content)
        self.btn_copy_diagnostics.setToolTip(
            "Copy the application and environment details for a bug report.")
        self.btn_copy_diagnostics.clicked.connect(self.copy_diagnostics)
        diagnostics_row.addWidget(self.btn_copy_diagnostics)
        diagnostics_row.addStretch(1)
        layout.addLayout(diagnostics_row)
        layout.addStretch(1)

        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self.setStyleSheet("""
            QFrame#disclaimer {
                background: #fff5e6;
                border: 1px solid #a85e17;
                border-radius: 5px;
                color: #40240c;
            }
            QPlainTextEdit#licence {
                background: #f7f7f7;
                border: 1px solid #c8cfd8;
            }
        """)

    def _toggle_licence(self, visible: bool) -> None:
        self.licence_text.setVisible(visible)
        self.btn_show_licence.setText("Hide licence" if visible else "Show licence")

    def copy_diagnostics(self) -> None:
        """Copy a fresh diagnostics block because build information can be monkeypatched in tests."""
        text = diagnostics_text()
        self.diagnostics_view.setPlainText(text)
        QGuiApplication.clipboard().setText(text)
