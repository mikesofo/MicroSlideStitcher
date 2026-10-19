"""The MicroSlideStitcher main window.

The GUI is a front end for the command line interface. It builds an argument list,
launches ``python -m micro_slide_stitcher`` in a background thread, streams the log
lines the run prints and then reads the diagnostics directory the run wrote. No
pipeline logic is duplicated here.
"""
from __future__ import annotations

import html
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThread, QUrl, Signal
from PySide6.QtGui import (QAction, QDesktopServices, QGuiApplication, QImage, QKeySequence,
                           QShortcut, QTextOption)
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QScrollArea, QSplitter, QTabWidget, QVBoxLayout,
                               QWidget)

from .. import __version__
from ..images import IMAGE_SUFFIXES, probe_image_sequence
from .about import AboutDialog
from .cli_spec import SPECS
from .command import build_argv, command_string, wrapped_command_string
from .manual import ManualWindow
from .results import RunResults, load_results, summary_rows
from .runner import PipelineRunner, child_environment
from .widgets import FrameQualityTimeline, ImageLoader, LogPane, PanoramaPanel, SettingsPanel

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts", ".webm", ".mpg", ".mpeg",
                  ".3gp", ".wmv"}
PLACEHOLDER_INPUT = "/path/to/scan_10x.mp4"
PREVIEW_MAX_DIM = 960
REVEAL_CHECK_SECONDS = 0.25

STYLE = """
QMainWindow, QWidget { font-size: 10.5pt; }
QGroupBox { font-weight: 600; border: 1px solid #c8cfd8; border-radius: 6px;
            margin-top: 14px; padding: 12px 10px 10px 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #21456e; }
QLabel#hint, QLabel#dimmed { color: #5a6472; }
QLabel#placeholder { color: #5a6472; font-size: 11pt; }
QLabel#verdict { font-size: 13pt; font-weight: 700; padding: 4px 0; }
QLabel#error { background: #fdecea; border: 1px solid #e0a9a4; border-radius: 5px;
               padding: 9px 11px; color: #7e1f18; }
QPlainTextEdit#command { background: #f4f6f9; border: 1px solid #c8cfd8; border-radius: 5px; }
QPushButton { padding: 6px 14px; }
QPushButton#primary { font-weight: 600; }
"""


class ProbeThread(QThread):
    """Runs the pipeline's own ffprobe metadata step off the GUI thread."""

    ok = Signal(dict)
    fail = Signal(str)

    def __init__(self, path: Path, input_mode: str, parent=None) -> None:
        super().__init__(parent)
        self.path = Path(path)
        self.input_mode = input_mode
        self._abort = False

    def abort(self) -> None:
        self._abort = True

    def run(self) -> None:  # noqa: D102
        from ..probe import ProbeError, probe_video
        try:
            if self.input_mode == "images":
                meta = probe_image_sequence(self.path, "name", 1, None)
            else:
                meta = probe_video(self.path)
        except (ProbeError, OSError, ValueError) as exc:
            if not self._abort:
                self.fail.emit(str(exc))
            return
        if not self._abort:
            self.ok.emit(meta.to_dict())


def _launch_detached(command: list[str]) -> bool:
    """Start one desktop handler without ever waiting on the GUI event thread."""
    try:
        proc = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env=child_environment(), start_new_session=True)
    except OSError:
        return False
    try:
        return proc.wait(timeout=REVEAL_CHECK_SECONDS) == 0
    except subprocess.TimeoutExpired:
        return True


class PathRevealThread(QThread):
    """Try desktop handlers in a worker so an unavailable one cannot freeze the GUI."""

    succeeded = Signal(str)
    failed = Signal()

    def __init__(self, path: Path, kind: str, parent=None) -> None:
        super().__init__(parent)
        self.path = Path(path)
        self.kind = kind

    def _commands(self) -> list[tuple[str, list[str]]]:
        path = str(self.path)
        commands: list[tuple[str, list[str]]] = []
        if self.kind == "directory":
            gdbus = shutil.which("gdbus")
            if gdbus:
                uri = QUrl.fromLocalFile(path).toString()
                commands.append((
                    "FileManager1 D-Bus ShowFolders",
                    [gdbus, "call", "--session", "--dest", "org.freedesktop.FileManager1",
                     "--object-path", "/org/freedesktop/FileManager1", "--method",
                     "org.freedesktop.FileManager1.ShowFolders", f"['{uri}']", "''"],
                ))
            commands += self._standard_commands(path, ("dolphin", "nautilus", "thunar",
                                                        "nemo", "pcmanfm", "xdg-open"))
        else:
            commands += self._standard_commands(path, ("firefox", "chromium",
                                                        "google-chrome-stable", "brave"))
        return commands

    @staticmethod
    def _standard_commands(path: str, applications: tuple[str, ...]) -> list[tuple[str, list[str]]]:
        commands: list[tuple[str, list[str]]] = []
        for launcher, label in (("xdg-open", "xdg-open"), ("gio", "gio open")):
            executable = shutil.which(launcher)
            if executable:
                commands.append((label, [executable] + (["open"] if launcher == "gio" else []) + [path]))
        if applications[0] == "firefox":
            browser = os.environ.get("BROWSER", "").strip()
            if browser:
                try:
                    commands.append((f"$BROWSER ({browser})", shlex.split(browser) + [path]))
                except ValueError:
                    pass
        for application in applications:
            executable = shutil.which(application)
            if executable:
                commands.append((application, [executable, path]))
                break
        return commands

    def run(self) -> None:  # noqa: D102 - QThread entry point
        for mechanism, command in self._commands():
            if _launch_detached(command):
                self.succeeded.emit(mechanism)
                return
        self.failed.emit()


def _value_label(text: str = "—") -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lab.setMinimumWidth(120)
    return lab


class SidePanelScroll(QScrollArea):
    """A vertical-only scroll area that keeps its content exactly as wide as the viewport.

    Without this, a word-wrapping label inside a QScrollArea can demand more width than
    the viewport has (Qt's height-for-width limitation) and the panel gets clipped once
    the vertical scrollbar appears.
    """

    def resizeEvent(self, event) -> None:  # noqa: D102
        super().resizeEvent(event)
        inner = self.widget()
        if inner is not None:
            inner.setMaximumWidth(self.viewport().width())


class MainWindow(QMainWindow):
    """Everything the histopathologist needs for a routine stitch, in one window."""

    runFinished = Signal(int)   # exit code; -1 = cancelled (used by the tests)
    resultsLoaded = Signal()

    def __init__(self, parent=None, settings: QSettings | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"MicroSlideStitcher {__version__} — slide panorama stitcher")
        self.setStyleSheet(STYLE)
        self.setMinimumSize(1180, 720)
        self.resize(1280, 820)
        self.setAcceptDrops(True)

        # `settings` lets the tests keep their state out of the real user configuration.
        self.qsettings = settings or QSettings("MicroSlideStitcher", "gui")
        self.input_path: Path | None = None
        self.input_mode: str | None = None
        self.metadata: dict = {}
        self.results: RunResults | None = None
        self._runner: PipelineRunner | None = None
        self._probe: ProbeThread | None = None
        self._loader: ImageLoader | None = None
        self._reveal: PathRevealThread | None = None
        self._reveal_path_to_copy: Path | None = None
        self._diag_manual = False
        self._running_command = ""
        self.last_diagnostics: Path | None = None
        self.about_dialog: AboutDialog | None = None
        self.manual_window: ManualWindow | None = None

        self._build_ui()
        self.settings_panel.set_input_mode(None)
        self._restore_settings()
        self._refresh_command()
        self._set_running(False)

    # =============================================================== construction
    def _build_ui(self) -> None:
        help_menu = self.menuBar().addMenu("&Help")
        self.manual_action = QAction("&User manual", self)
        # F1 is the platform's "help contents" key; one shortcut only, so Qt never
        # reports an ambiguous overload and silently swallows the key press.
        self.manual_action.setShortcut(QKeySequence(Qt.Key_F1))
        self.manual_action.setToolTip(
            "How to record a scan that will stitch, how to run it, and how to read the results (F1)")
        self.manual_action.triggered.connect(self.show_manual)
        help_menu.addAction(self.manual_action)
        help_menu.addSeparator()
        self.about_action = QAction("About MicroSlideStitcher", self)
        self.about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(self.about_action)

        central = QWidget(self)
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(12)

        splitter = QSplitter(Qt.Horizontal, central)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_column())
        splitter.addWidget(self._build_right_column())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([472, 800])
        outer.addWidget(splitter)

        self.status_label = QLabel("Drop a video or image folder onto the window, or choose one.")
        self.status_label.setObjectName("hint")
        self.statusBar().addWidget(self.status_label, 1)
        self.btn_manual = QPushButton("User manual (F1)", self)
        self.btn_manual.setToolTip(
            "Open the built-in user manual: recording, running, reading the results, every setting")
        self.btn_manual.clicked.connect(self.show_manual)
        self.statusBar().addPermanentWidget(self.btn_manual)
        self.btn_about = QPushButton("About", self)
        self.btn_about.setToolTip("About MicroSlideStitcher, licence and bug-report diagnostics")
        self.btn_about.clicked.connect(self.show_about_dialog)
        self.statusBar().addPermanentWidget(self.btn_about)
        self.statusBar().setSizeGripEnabled(False)
        QShortcut(QKeySequence("Ctrl+O"), self, self.choose_input)
        QShortcut(QKeySequence("Ctrl+R"), self, self.start_full_run)
        QShortcut(QKeySequence("Ctrl+P"), self, self.start_preview_run)
        QShortcut(QKeySequence("Esc"), self, self.cancel_run)

    def show_about_dialog(self) -> None:
        """Show one reusable modeless About dialog from either GUI entry point."""
        if self.about_dialog is None:
            self.about_dialog = AboutDialog(self)
        self.about_dialog.show()
        self.about_dialog.raise_()
        self.about_dialog.activateWindow()

    def show_manual(self) -> None:
        """Show one reusable modeless user-manual window (Help -> User manual, or F1)."""
        if self.manual_window is None:
            self.manual_window = ManualWindow(self)
        self.manual_window.show()
        self.manual_window.raise_()
        self.manual_window.activateWindow()

    # ---------------------------------------------------------------- left column
    def _build_left_column(self) -> QWidget:
        panel = QWidget(self)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.setSpacing(10)

        # --- input -----------------------------------------------------------
        gb_in = QGroupBox("1 · Input video or image sequence", panel)
        v = QVBoxLayout(gb_in)
        v.setSpacing(8)
        self.drop_hint = QLabel("Drag a video file or image folder anywhere onto this window, or:", gb_in)
        self.drop_hint.setObjectName("hint")
        self.drop_hint.setWordWrap(True)
        v.addWidget(self.drop_hint)

        self.btn_choose = QPushButton("&Choose video…   (Ctrl+O)", gb_in)
        self.btn_choose.setToolTip("Pick the video recorded through the eyepiece. "
                                   "The file is only ever read, never modified.")
        self.btn_choose.clicked.connect(self.choose_input)
        v.addWidget(self.btn_choose)
        self.btn_choose_images = QPushButton("Choose image &folder…", gb_in)
        self.btn_choose_images.setToolTip(
            "Pick a folder of overlapping still images. PNG, JPEG, TIFF, BMP and WebP are "
            "accepted; ffmpeg is not used for image sequences.")
        self.btn_choose_images.clicked.connect(self.choose_image_folder)
        v.addWidget(self.btn_choose_images)

        self.lbl_input = QLabel("No input loaded yet.", gb_in)
        self.lbl_input.setWordWrap(True)
        self.lbl_input.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.lbl_input)

        meta_form = QFormLayout()
        meta_form.setLabelAlignment(Qt.AlignLeft)
        meta_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        meta_form.setHorizontalSpacing(14)
        meta_form.setVerticalSpacing(5)
        self.meta_labels = {}
        self.meta_captions = {}
        for key, label in (("dimensions", "Dimensions"), ("fps", "Frame rate"),
                           ("duration", "Duration"), ("codec", "Codec"),
                           ("frames", "Frames (est.)"), ("audio", "Audio track")):
            value = _value_label()
            self.meta_labels[key] = value
            caption = QLabel(label + ":")
            caption.setObjectName("dimmed")
            self.meta_captions[key] = caption
            meta_form.addRow(caption, value)
        v.addLayout(meta_form)

        lay.addWidget(gb_in)

        # --- output ----------------------------------------------------------
        gb_out = QGroupBox("2 · Where the results go", panel)
        ov = QVBoxLayout(gb_out)
        ov.setContentsMargins(10, 6, 10, 8)
        ov.setSpacing(4)

        self.ed_output = QLineEdit(gb_out)
        self.ed_output.setToolTip("--output: panorama path; the extension decides the format "
                                  "(.tif/.tiff = compressed TIFF, .png, .jpg)")
        self.ed_output.setPlaceholderText("…/panorama.tif")
        self.ed_output.textEdited.connect(self._on_output_edited)
        self.ed_output.textChanged.connect(self._refresh_command)
        btn_out = QPushButton("Browse…", gb_out)
        btn_out.setToolTip("Choose the panorama file")
        btn_out.clicked.connect(self.choose_output)
        ov.addWidget(self._caption("Panorama file  (--output):", self.ed_output))
        ov.addWidget(self._with_button(self.ed_output, btn_out))
        ov.addSpacing(6)

        self.ed_diag = QLineEdit(gb_out)
        self.ed_diag.setToolTip("--diagnostics: directory for summary.json, frame_metrics.csv, "
                                "report.html, pipeline.log and the previews")
        self.ed_diag.textEdited.connect(self._on_diag_edited)
        self.ed_diag.textChanged.connect(self._refresh_command)
        btn_diag = QPushButton("Browse…", gb_out)
        btn_diag.setToolTip("Choose the diagnostics directory")
        btn_diag.clicked.connect(self.choose_diagnostics)
        ov.addWidget(self._caption("Diagnostics folder  (--diagnostics):", self.ed_diag))
        ov.addWidget(self._with_button(self.ed_diag, btn_diag))
        lay.addWidget(gb_out)

        # --- settings --------------------------------------------------------
        gb_set = QGroupBox("3 · Settings — these are the CLI flags", panel)
        sv = QVBoxLayout(gb_set)
        sv.setContentsMargins(10, 6, 10, 8)
        self.settings_panel = SettingsPanel(gb_set)
        self.settings_panel.changed.connect(self._refresh_command)
        sv.addWidget(self.settings_panel)
        lay.addWidget(gb_set)
        lay.addStretch(1)

        scroll = SidePanelScroll(self)
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(472)
        scroll.setMaximumWidth(560)
        return scroll

    @staticmethod
    def _caption(text: str, buddy: QWidget | None = None) -> QLabel:
        """A caption on its own line: it never wraps and never steals the field's width."""
        lab = QLabel(text)
        lab.setWordWrap(False)
        lab.setTextInteractionFlags(Qt.NoTextInteraction)
        if buddy is not None:
            lab.setBuddy(buddy)
        return lab

    @staticmethod
    def _with_button(field: QWidget, button: QWidget) -> QWidget:
        holder = QWidget()
        h = QHBoxLayout(holder)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(5)
        h.addWidget(field, 1)
        h.addWidget(button, 0)
        return holder

    # --------------------------------------------------------------- right column
    def _build_right_column(self) -> QWidget:
        panel = QWidget(self)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # --- command preview -------------------------------------------------
        gb_cmd = QGroupBox("Equivalent command line — the GUI runs exactly this", panel)
        cv = QVBoxLayout(gb_cmd)
        cv.setSpacing(7)
        self.cmd_view = QPlainTextEdit(gb_cmd)
        self.cmd_view.setObjectName("command")
        self.cmd_view.setReadOnly(True)
        self.cmd_view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.cmd_view.setWordWrapMode(QTextOption.WrapAnywhere)
        self.cmd_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.cmd_view.setFixedHeight(112)
        self.cmd_view.setToolTip("Read-only, selectable. This is the command the run buttons "
                                 "execute; copy it to reproduce the run in a terminal.")
        self.cmd_view.setAccessibleName("Equivalent command line")
        font = self.cmd_view.font()
        font.setFamilies(["DejaVu Sans Mono", "Monospace"])
        font.setPointSizeF(9.8)
        self.cmd_view.setFont(font)
        cv.addWidget(self.cmd_view)

        row = QHBoxLayout()
        self.chk_all_flags = QCheckBox("Show every flag, including the ones at their default "
                                       "value", gb_cmd)
        self.chk_all_flags.setToolTip("Off: only settings that differ from the CLI defaults "
                                      "are listed (shortest command). On: the full command.")
        self.chk_all_flags.toggled.connect(self._refresh_command)
        row.addWidget(self.chk_all_flags, 1)
        self.btn_copy = QPushButton("Cop&y command", gb_cmd)
        self.btn_copy.setToolTip("Copy the command line above to the clipboard")
        self.btn_copy.clicked.connect(self.copy_command)
        row.addWidget(self.btn_copy, 0)
        cv.addLayout(row)
        lay.addWidget(gb_cmd)

        # --- run controls ----------------------------------------------------
        gb_run = QGroupBox("Run", panel)
        rv = QVBoxLayout(gb_run)
        rv.setSpacing(8)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.btn_preview = QPushButton("Quick &preview   (960 px, PNG)", gb_run)
        self.btn_preview.setObjectName("primary")
        self.btn_preview.setToolTip(
            "Fast, downscaled test run: forces --max-dimension 960 and a PNG output next to "
            "your panorama file (…_preview.png, with its own …_preview_diagnostics folder). "
            "Takes seconds and tells you whether the scan is stitchable at all.   Ctrl+P")
        self.btn_preview.clicked.connect(self.start_preview_run)
        self.btn_full = QPushButton("&Full resolution", gb_run)
        self.btn_full.setObjectName("primary")
        self.btn_full.setToolTip("Run the pipeline with exactly the settings shown above, "
                                 "writing the panorama file you chose.   Ctrl+R")
        self.btn_full.clicked.connect(self.start_full_run)
        self.btn_cancel = QPushButton("Cancel run", gb_run)
        self.btn_cancel.setToolTip("Terminate the running pipeline process (and its ffmpeg "
                                   "child). Diagnostics written so far are kept.   Esc")
        self.btn_cancel.clicked.connect(self.cancel_run)
        buttons.addWidget(self.btn_preview)
        buttons.addWidget(self.btn_full)
        buttons.addWidget(self.btn_cancel)
        buttons.addStretch(1)
        rv.addLayout(buttons)

        self.progress = QProgressBar(gb_run)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p %")
        self.progress.setFixedHeight(20)
        rv.addWidget(self.progress)

        self.lbl_stage = QLabel("Idle.", gb_run)
        self.lbl_stage.setObjectName("hint")
        self.lbl_stage.setWordWrap(True)
        rv.addWidget(self.lbl_stage)

        self.path_error_row = QWidget(gb_run)
        error_row = QHBoxLayout(self.path_error_row)
        error_row.setContentsMargins(0, 0, 0, 0)
        error_row.setSpacing(8)
        self.lbl_error = QLabel("", self.path_error_row)
        self.lbl_error.setObjectName("error")
        self.lbl_error.setWordWrap(True)
        self.lbl_error.setOpenExternalLinks(True)
        self.lbl_error.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.lbl_error.hide()
        error_row.addWidget(self.lbl_error, 1)
        self.btn_copy_path = QPushButton("Copy path", self.path_error_row)
        self.btn_copy_path.setToolTip("Copy the path so it can be pasted into a file manager.")
        self.btn_copy_path.clicked.connect(self._copy_reveal_path)
        self.btn_copy_path.hide()
        error_row.addWidget(self.btn_copy_path, 0, Qt.AlignBottom)
        rv.addWidget(self.path_error_row)
        lay.addWidget(gb_run)

        # --- tabs ------------------------------------------------------------
        self.tabs = QTabWidget(panel)
        self.tabs.setDocumentMode(True)
        self.log_pane = LogPane(self.tabs)
        self.tabs.addTab(self.log_pane, "Run &log")
        self.tabs.addTab(self._build_timeline_tab(), "Frame &quality")
        self.tabs.addTab(self._build_panorama_tab(), "Pa&norama")
        self.tabs.addTab(self._build_summary_tab(), "Results &summary")
        lay.addWidget(self.tabs, 1)
        return panel

    def _build_timeline_tab(self) -> QWidget:
        w = QWidget(self)
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(7)
        head = QLabel("Focus score of every sampled candidate image/frame, straight from "
                      "frame_metrics.csv. The x-axis is video time or image index. Drag the "
                      "blue line to choose --blur-threshold; the setting and command line "
                      "above follow it.", w)
        head.setWordWrap(True)
        head.setObjectName("hint")
        v.addWidget(head)
        self.timeline = FrameQualityTimeline(w)
        self.timeline.thresholdChanged.connect(self._on_threshold_dragged)
        v.addWidget(self.timeline, 1)
        self.lbl_timeline = QLabel("No run loaded yet.", w)
        self.lbl_timeline.setWordWrap(True)
        v.addWidget(self.lbl_timeline)
        return w

    def _build_panorama_tab(self) -> QWidget:
        w = QWidget(self)
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(7)

        bar = QHBoxLayout()
        bar.setSpacing(7)
        self.btn_fit = QPushButton("Fit to window", w)
        self.btn_fit.setToolTip("Show the whole panorama (keyboard: F in the image)")
        self.btn_11 = QPushButton("1:1", w)
        self.btn_11.setToolTip("Show the preview at 100 % (keyboard: 1)")
        self.btn_zin = QPushButton("+", w)
        self.btn_zin.setFixedWidth(38)
        self.btn_zin.setToolTip("Zoom in (mouse wheel also works)")
        self.btn_zout = QPushButton("–", w)
        self.btn_zout.setFixedWidth(38)
        self.btn_zout.setToolTip("Zoom out (mouse wheel also works)")
        self.btn_full_res = QPushButton("Load full-res file", w)
        self.btn_full_res.setToolTip("Replace the fast preview PNG with the real output file "
                                     "(TIFF/PNG). Large panoramas take a moment; loading "
                                     "happens in the background.")
        self.btn_report = QPushButton("Open report.html", w)
        self.btn_report.setToolTip("Open the run's HTML report in your normal web browser")
        self.btn_folder = QPushButton("Open folder", w)
        self.btn_folder.setToolTip("Open the folder that contains the panorama file(s)")
        for b, slot in ((self.btn_fit, lambda: self.panorama.view.fit_to_window()),
                        (self.btn_11, lambda: self.panorama.view.zoom_one_to_one()),
                        (self.btn_zin, lambda: self.panorama.view.zoom_by(1.25)),
                        (self.btn_zout, lambda: self.panorama.view.zoom_by(1 / 1.25)),
                        (self.btn_full_res, self.load_full_resolution),
                        (self.btn_report, self.open_report),
                        (self.btn_folder, self.open_output_folder)):
            b.clicked.connect(slot)
            # never let a label be elided, even in the 1280 px-wide layout
            b.setMinimumWidth(b.sizeHint().width())
            bar.addWidget(b)
        bar.addStretch(1)

        seg_row = QHBoxLayout()
        seg_row.setSpacing(7)
        cap = QLabel("Segment:", w)
        cap.setObjectName("dimmed")
        seg_row.addWidget(cap)
        self.cmb_segment = QComboBox(w)
        self.cmb_segment.setMinimumWidth(340)
        self.cmb_segment.setToolTip("A scan that could not be chained into one panorama is "
                                    "written as several pieces; pick one to view it.")
        self.cmb_segment.currentIndexChanged.connect(self._on_segment_changed)
        seg_row.addWidget(self.cmb_segment, 0)
        seg_row.addStretch(1)
        self.lbl_zoom = QLabel("", w)
        self.lbl_zoom.setObjectName("dimmed")
        seg_row.addWidget(self.lbl_zoom)

        self.lbl_segment = QLabel("No panorama loaded yet.", w)
        self.lbl_segment.setWordWrap(True)
        self.lbl_segment.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.panorama = PanoramaPanel(
            "No panorama yet.\n\nRun a quick preview or a full-resolution stitch; the "
            "panorama preview written into the diagnostics directory is shown here.", w)
        self.panorama.view.scaleChanged.connect(
            lambda s: self.lbl_zoom.setText(f"zoom {s * 100:.0f} %"))

        v.addLayout(seg_row)
        v.addWidget(self.lbl_segment)
        v.addLayout(bar)
        v.addWidget(self.panorama, 1)
        return w

    def _build_summary_tab(self) -> QWidget:
        w = QWidget(self)
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)
        self.lbl_verdict = QLabel("No run yet.", w)
        self.lbl_verdict.setObjectName("verdict")
        self.lbl_verdict.setWordWrap(True)
        v.addWidget(self.lbl_verdict)

        self.summary_host = QWidget(w)
        self.summary_grid = QGridLayout(self.summary_host)
        self.summary_grid.setHorizontalSpacing(18)
        self.summary_grid.setVerticalSpacing(7)
        self.summary_grid.setContentsMargins(2, 2, 2, 2)
        self.summary_grid.setColumnStretch(1, 1)

        scroll = QScrollArea(w)
        scroll.setWidget(self.summary_host)
        scroll.setWidgetResizable(True)
        # never scroll sideways: the value labels wrap to the visible width instead
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.StyledPanel)
        v.addWidget(scroll, 1)
        hint = QLabel("Every value on this page is read from summary.json - nothing is "
                      "re-computed by the GUI.", w)
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        v.addWidget(hint)
        return w

    # ============================================================ input handling
    def dragEnterEvent(self, event) -> None:  # noqa: D102
        if self._is_video_drop(event):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: D102
        if self._is_video_drop(event):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: D102
        path = self._dropped_path(event)
        if path is not None:
            event.acceptProposedAction()
            self.load_input(path)

    @staticmethod
    def _dropped_path(event) -> Path | None:
        data = event.mimeData()
        if not data.hasUrls():
            return None
        for url in data.urls():
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            if p.is_dir() or (p.is_file() and
                              (p.suffix.lower() in VIDEO_SUFFIXES or p.suffix.lower() in IMAGE_SUFFIXES)):
                return p
        return None

    def _is_video_drop(self, event) -> bool:
        return self._dropped_path(event) is not None

    def choose_input(self) -> None:
        start = self.qsettings.value("last_input_dir", str(Path.home()))
        pattern = " ".join(f"*{s}" for s in sorted(VIDEO_SUFFIXES))
        path, _ = QFileDialog.getOpenFileName(self, "Choose the eyepiece video", str(start),
                                              f"Video files ({pattern});;All files (*)")
        if path:
            self.load_input(Path(path))

    def choose_image_folder(self) -> None:
        start = self.qsettings.value("last_input_dir", str(Path.home()))
        path = QFileDialog.getExistingDirectory(self, "Choose the overlapping image folder",
                                                str(start))
        if path:
            self.load_input(Path(path))

    def load_input(self, path: Path) -> None:
        """Set a video or image folder and start its metadata step in the background."""
        path = Path(path)
        mode = "images" if path.is_dir() or (path.is_file() and
                                             path.suffix.lower() in IMAGE_SUFFIXES) else "video"
        if not path.is_dir() and not path.is_file():
            self._show_error(f"Not a readable file or folder: {path}")
            return
        self.input_path = path
        self.input_mode = mode
        self.settings_panel.set_input_mode(mode)
        self.qsettings.setValue("last_input_dir", str(path.parent))
        self.lbl_input.setText(f"<b>{path.name}</b><br>{path.parent}")
        if mode == "images":
            labels = (("dimensions", "Dimensions"), ("fps", "Image count"),
                      ("duration", "Order"), ("codec", "Uniform size"),
                      ("frames", "Candidates"), ("audio", "Source"))
            pending = "reading image metadata…"
        else:
            labels = (("dimensions", "Dimensions"), ("fps", "Frame rate"),
                      ("duration", "Duration"), ("codec", "Codec"),
                      ("frames", "Frames (est.)"), ("audio", "Audio track"))
            pending = "reading with ffprobe…"
        for key, label in labels:
            self.meta_captions[key].setText(label + ":")
            self.meta_labels[key].setText(pending)
        self._autofill_paths(path)
        self._refresh_command()
        self._status(f"Loaded {path.name}; {pending}")
        if self._probe is not None and self._probe.isRunning():
            self._probe.abort()
        self._probe = ProbeThread(path, mode, self)
        self._probe.ok.connect(self._on_metadata)
        self._probe.fail.connect(self._on_metadata_failed)
        self._probe.start()

    def _on_metadata(self, meta: dict) -> None:
        self.metadata = meta
        if meta.get("input_type") == "images":
            self.meta_labels["dimensions"].setText(f"{meta.get('width')} × {meta.get('height')} px")
            self.meta_labels["fps"].setText(str(meta.get("image_count", "?")))
            self.meta_labels["duration"].setText(str(meta.get("image_order", "?")))
            self.meta_labels["codec"].setText(
                "yes" if meta.get("uniformly_sized") else "yes after downscaling")
            self.meta_labels["frames"].setText(str(meta.get("candidate_count", "?")))
            self.meta_labels["audio"].setText("image folder")
            self._status(
                f"{meta.get('image_count', '?')} images: {meta.get('width')}×"
                f"{meta.get('height')} px, {meta.get('image_order')} order; "
                f"{meta.get('candidate_count', '?')} candidate images.")
            return
        self.meta_labels["dimensions"].setText(f"{meta.get('width')} × {meta.get('height')} px")
        self.meta_labels["fps"].setText(f"{meta.get('fps', 0):.3f} fps")
        dur = float(meta.get("duration_s", 0.0))
        self.meta_labels["duration"].setText(
            f"{dur:.2f} s" if dur < 60 else f"{dur:.2f} s  ({int(dur // 60)} min "
                                            f"{dur % 60:.1f} s)")
        self.meta_labels["codec"].setText(
            f"{meta.get('codec', '?')}  ({meta.get('pix_fmt', '?')})")
        self.meta_labels["frames"].setText(str(meta.get("frame_count_estimate", "?")))
        self.meta_labels["audio"].setText("yes" if meta.get("has_audio") else "no")
        fps = float(self.settings_panel.values().get("--sampling-fps") or 0)
        n = int(dur * fps)
        self._status(
            f"{Path(str(meta.get('path', ''))).name}: {meta.get('width')}×{meta.get('height')}, "
            f"{meta.get('fps', 0):.2f} fps, {dur:.1f} s — about {n} frames will be sampled at "
            f"{fps:g} fps.")

    def _on_metadata_failed(self, message: str) -> None:
        for lab in self.meta_labels.values():
            lab.setText("—")
        tool = "Image metadata reader" if self.input_mode == "images" else "ffprobe"
        self._show_error(f"{tool} could not read the input: {message}")

    # ------------------------------------------------------------------ path fields
    def _autofill_paths(self, source: Path) -> None:
        out_dir = Path(self.qsettings.value("last_output_dir", "") or (Path.home() / "panoramas"))
        current = self.ed_output.text().strip()
        if not current or self.qsettings.value("autofill_output", True, type=bool):
            self.ed_output.setText(str(out_dir / f"{source.stem}_panorama.tif"))
        if not self._diag_manual:
            self._derive_diagnostics()

    def _derive_diagnostics(self) -> None:
        out = Path(self.ed_output.text().strip() or "panorama.tif")
        self.ed_diag.setText(str(out.parent / f"{out.stem}_diagnostics"))
        self.ed_diag.setCursorPosition(0)
        self.ed_output.setCursorPosition(0)

    def _on_output_edited(self, _text: str) -> None:
        if not self._diag_manual:
            self._derive_diagnostics()
        self._refresh_command()

    def _on_diag_edited(self, _text: str) -> None:
        self._diag_manual = True
        self._refresh_command()

    def choose_output(self) -> None:
        start = self.ed_output.text().strip() or str(Path.home() / "panorama.tif")
        path, _ = QFileDialog.getSaveFileName(
            self, "Panorama output file", start,
            "TIFF (*.tif *.tiff);;PNG (*.png);;JPEG (*.jpg *.jpeg)")
        if path:
            self.ed_output.setText(path)
            if not self._diag_manual:
                self._derive_diagnostics()
            self._refresh_command()

    def choose_diagnostics(self) -> None:
        start = self.ed_diag.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Diagnostics directory", start)
        if path:
            self.ed_diag.setText(path)
            self._diag_manual = True
            self._refresh_command()

    # ========================================================== command preview
    def effective_paths(self, preview: bool) -> tuple[Path, Path]:
        """(output, diagnostics) for a full or quick-preview run."""
        out = Path(self.ed_output.text().strip() or "panorama.tif")
        diag = Path(self.ed_diag.text().strip() or (out.parent / f"{out.stem}_diagnostics"))
        if preview:
            out = out.with_name(f"{out.stem}_preview.png")
            diag = diag.with_name(f"{diag.name.removesuffix('_diagnostics')}"
                                  f"_preview_diagnostics")
        return out, diag

    def run_settings(self, preview: bool) -> dict:
        values = dict(self.settings_panel.values())
        if self.input_mode == "images" and values.get("--input-mode") == "auto":
            # The folder already unambiguously identifies this mode; make the preview explicit.
            values["--input-mode"] = "images"
        for flag in self.settings_panel.suppressed_flags():
            # ``None`` deliberately suppresses the flag even with “show every flag” on.
            values[flag] = None
        if preview:
            values["--max-dimension"] = PREVIEW_MAX_DIM
        return values

    def command_text(self, preview: bool = False, wrapped: bool = False) -> str:
        out, diag = self.effective_paths(preview)
        source = str(self.input_path) if self.input_path else PLACEHOLDER_INPUT
        fn = wrapped_command_string if wrapped else command_string
        return fn(source, out, diag, self.run_settings(preview),
                  include_defaults=self.chk_all_flags.isChecked())

    def _refresh_command(self) -> None:
        if self._running_command:
            return
        self.cmd_view.setPlainText(self.command_text(wrapped=True))
        if self.input_path is None:
            self.cmd_view.setToolTip("No input chosen yet - the --input path shown is a "
                                     "placeholder.")

    def copy_command(self) -> None:
        text = self._running_command or self.command_text()
        QGuiApplication.clipboard().setText(text)
        self._status("Command line copied to the clipboard.", 4000)

    def _on_threshold_dragged(self, value: float) -> None:
        widget = self.settings_panel.widget_for("--blur-threshold")
        widget.setValue(float(value))          # triggers changed -> _refresh_command
        keep, drop = self.timeline.counts()
        self.lbl_timeline.setText(
            f"--blur-threshold {value:g}: {keep} of {keep + drop} sampled frames would pass "
            f"the focus gate ({drop} rejected). Re-run to apply it.")

    # ================================================================== running
    def start_preview_run(self) -> None:
        self.start_run(preview=True)

    def start_full_run(self) -> None:
        self.start_run(preview=False)

    def start_run(self, preview: bool) -> None:
        if self._runner is not None and self._runner.isRunning():
            return
        if self.input_path is None or (not self.input_path.is_file() and
                                       not self.input_path.is_dir()):
            self._show_error("Choose an input video or image folder first (drag it onto the "
                             "window or use a Choose button).")
            return
        out, diag = self.effective_paths(preview)
        if out.suffix.lower() not in (".tif", ".tiff", ".png", ".jpg", ".jpeg"):
            self._show_error(f"The output file must end in .tif, .tiff, .png or .jpg - "
                             f"“{out.name}” does not.")
            return
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            diag.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._show_error(f"Cannot create the output folder: {exc}")
            return
        self.qsettings.setValue("last_output_dir", str(out.parent))

        argv = build_argv(self.input_path, out, diag, self.run_settings(preview),
                          include_defaults=self.chk_all_flags.isChecked())
        self._running_command = command_string(
            self.input_path, out, diag, self.run_settings(preview),
            include_defaults=self.chk_all_flags.isChecked())
        self.cmd_view.setPlainText(wrapped_command_string(
            self.input_path, out, diag, self.run_settings(preview),
            include_defaults=self.chk_all_flags.isChecked()))
        self.last_diagnostics = diag
        self._hide_error()
        self.log_pane.reset()
        self.log_pane.append_line(
            f"# {'quick preview' if preview else 'full resolution'} run", "INFO")
        self.log_pane.append_line(f"$ {self._running_command}", "INFO")
        self.tabs.setCurrentIndex(0)
        self.progress.setValue(0)
        self.lbl_stage.setText("Starting the pipeline…")
        self._set_running(True)

        self._runner = PipelineRunner(argv, diag, cwd=out.parent, parent=self)
        self._runner.line.connect(self.log_pane.append_line)
        self._runner.progress.connect(self._on_progress)
        self._runner.done.connect(self._on_run_done)
        self._runner.start()

    def _on_progress(self, percent: int, stage: str) -> None:
        self.progress.setValue(max(0, min(100, percent)))
        self.lbl_stage.setText(stage)
        self._status(f"{percent} % — {stage}")

    def _on_run_done(self, code: int, message: str) -> None:
        self._set_running(False)
        self._running_command = ""
        diag = self.last_diagnostics
        self.lbl_stage.setText(message)
        self._status(message, 8000)
        if code == 0 and diag is not None:
            self.load_diagnostics(diag)
        elif code == -1:
            self.progress.setValue(0)
            if diag is not None:
                self._show_error(
                    f"{message}<br>Diagnostics directory: "
                    f"<a href=\"file://{diag}\">{diag}</a>")
        else:
            self.progress.setValue(100)
            detail = ""
            if diag is not None:
                failure = diag / "failure.txt"
                if failure.exists():
                    try:
                        detail = failure.read_text(encoding="utf-8").strip()
                    except OSError:  # pragma: no cover
                        detail = ""
            body = f"<b>{message}</b>"
            if detail:
                body += f"<br><br>{detail}"
            if diag is not None:
                body += (f"<br><br>Diagnostics (preserved): "
                         f"<a href=\"file://{diag}\">{diag}</a>")
            self._show_error(body, html=True)
            if code == 5:
                QMessageBox.critical(
                    self, "ffmpeg is required for video input",
                    "This video run cannot start because ffmpeg/ffprobe was not found.\n\n"
                    "Install ffmpeg, then try again:\n"
                    "Arch / Manjaro: sudo pacman -S --needed ffmpeg\n"
                    "Debian / Ubuntu: sudo apt install ffmpeg\n"
                    "Fedora: sudo dnf install ffmpeg\n"
                    "openSUSE: sudo zypper install ffmpeg\n\n"
                    "Image-sequence input does not require ffmpeg.")
            if diag is not None:
                self.load_diagnostics(diag, quiet=True)
        self._refresh_command()
        self.runFinished.emit(code)

    def cancel_run(self) -> None:
        if self._runner is not None and self._runner.isRunning():
            self.lbl_stage.setText("Cancelling — terminating the pipeline process…")
            self.btn_cancel.setEnabled(False)
            self._runner.cancel()

    def is_running(self) -> bool:
        return self._runner is not None and self._runner.isRunning()

    def _set_running(self, running: bool) -> None:
        self.btn_preview.setEnabled(not running)
        self.btn_full.setEnabled(not running)
        self.btn_choose.setEnabled(not running)
        self.btn_choose_images.setEnabled(not running)
        self.btn_cancel.setEnabled(running)
        self.ed_output.setEnabled(not running)
        self.ed_diag.setEnabled(not running)
        self.settings_panel.set_enabled_for_run(not running)
        if not running:      # show the start of the path again, not its tail
            self.ed_output.setCursorPosition(0)
            self.ed_diag.setCursorPosition(0)

    # ================================================================== results
    def load_diagnostics(self, diagnostics: Path, quiet: bool = False) -> RunResults:
        """Read a diagnostics directory (after a run, or an earlier one) and show it."""
        res = load_results(diagnostics)
        self.results = res
        self.last_diagnostics = Path(diagnostics)

        # timeline
        image_mode = res.summary.get("input_mode") == "images"
        self.timeline.set_points(res.frames, image_indices=image_mode)
        if res.frames:
            self.timeline.set_threshold(
                float(self.settings_panel.values()["--blur-threshold"]))
            keep, drop = self.timeline.counts()
            worst = min(p.focus_score for p in res.frames)
            best = max(p.focus_score for p in res.frames)
            self.lbl_timeline.setText(
                f"{len(res.frames)} sampled {'images' if image_mode else 'frames'}, focus score {worst:.1f}–{best:.1f}. "
                f"At the current --blur-threshold "
                f"{self.settings_panel.values()['--blur-threshold']:g}, {keep} would pass and "
                f"{drop} would be rejected.")
        else:
            self.lbl_timeline.setText("No frame_metrics.csv in this diagnostics directory.")

        # summary
        self._populate_summary(res)
        # segments / panorama
        self._populate_segments(res)

        if not quiet and res.segments:
            self.tabs.setCurrentIndex(3)
        self.resultsLoaded.emit()
        return res

    def _clear_grid(self) -> None:
        while self.summary_grid.count():
            item = self.summary_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

    def _populate_summary(self, res: RunResults) -> None:
        self._clear_grid()
        verdict = res.verdict
        colour = {"SUCCESSFUL": "#1f8b4c", "PARTIALLY SUCCESSFUL": "#8a5300",
                  "FAILED": "#b3261e"}.get(verdict, "#5a6472")
        if res.summary:
            self.lbl_verdict.setText(f"Verdict: <span style='color:{colour}'>{verdict}</span>")
        else:
            self.lbl_verdict.setText("No summary.json — the run did not finish. "
                                     + (res.error or ""))
        row = 0
        for key, value in (summary_rows(res) if res.summary else []):
            cap = QLabel(key + ":")
            cap.setObjectName("dimmed")
            cap.setWordWrap(True)
            cap.setMinimumWidth(210)
            cap.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            val = _value_label(str(value))
            self.summary_grid.addWidget(cap, row, 0)
            self.summary_grid.addWidget(val, row, 1)
            row += 1
        warnings = res.warnings
        if warnings:
            head = QLabel(f"Warnings and notes from the run ({len(warnings)}):")
            head.setStyleSheet("font-weight:600; padding-top:10px;")
            head.setWordWrap(True)
            self.summary_grid.addWidget(head, row, 0, 1, 2)
            row += 1
            for w in warnings:
                lab = _value_label("•  " + w)
                lab.setStyleSheet("color:#7a4b00;")
                self.summary_grid.addWidget(lab, row, 0, 1, 2)
                row += 1
        self.summary_grid.setRowStretch(row, 1)

    def _populate_segments(self, res: RunResults) -> None:
        self.cmb_segment.blockSignals(True)
        self.cmb_segment.clear()
        for seg in res.segments:
            span = (f"image indices {seg.image_index_first}–{seg.image_index_last}"
                    if seg.input_mode == "images" else
                    f"t = {seg.time_start_s:.1f}–{seg.time_end_s:.1f} s")
            self.cmb_segment.addItem(
                f"Segment {seg.rank} — {seg.n_frames} frames, {seg.width}×{seg.height} px, "
                f"{span}", seg.rank)
        self.cmb_segment.blockSignals(False)
        multi = len(res.segments) > 1
        self.cmb_segment.setEnabled(multi)
        self.cmb_segment.setVisible(bool(res.segments))
        if res.segments:
            self.cmb_segment.setCurrentIndex(0)
            self._show_segment(0)
        else:
            self.lbl_segment.setText("This run wrote no panorama. "
                                     + (res.error or "See the log and the HTML report."))
            self.panorama.show_placeholder(
                "No panorama was written by this run.\n\nThe diagnostics directory is still "
                "there: check the log tab, failure.txt and report.html.")

    def _on_segment_changed(self, index: int) -> None:
        if index >= 0:
            self._show_segment(index)

    def _show_segment(self, index: int) -> None:
        if self.results is None or index < 0 or index >= len(self.results.segments):
            return
        seg = self.results.segments[index]
        extra = ""
        if len(self.results.segments) > 1:
            extra = (f"  ·  This scan is split into {len(self.results.segments)} pieces; "
                     f"each is a valid panorama of its stretch of the slide.")
        self.lbl_segment.setText(seg.info_line + extra + f"\nFile: {seg.output}")
        self._load_image(seg.preview_path, f"preview of segment {seg.rank}")

    def _load_image(self, path: str, what: str) -> None:
        p = Path(path)
        if not p.exists():
            self.panorama.show_placeholder(f"{what} not found at {p}")
            return
        if self._loader is not None and self._loader.isRunning():
            self._loader.abort()
        self.panorama.show_placeholder(f"Loading {what}…")
        self._loader = ImageLoader(p, self)
        self._loader.loaded.connect(self._on_image_loaded)
        self._loader.failed.connect(
            lambda path_, msg: self.panorama.show_placeholder(f"Could not load {path_}: {msg}"))
        self._loader.start()

    def _on_image_loaded(self, image: QImage, path: str) -> None:
        self.panorama.show_image(image)
        self._status(f"Showing {path} ({image.width()}×{image.height()} px)",
                                     6000)

    def load_full_resolution(self) -> None:
        if self.results is None or not self.results.segments:
            return
        seg = self.results.segments[self.cmb_segment.currentIndex()]
        self._load_image(seg.output, f"full-resolution panorama of segment {seg.rank}")

    def open_report(self) -> None:
        if self.results is None or self.results.report_html is None:
            self._show_error("There is no report.html yet — run the pipeline first.")
            return
        self._reveal_path(self.results.report_html, "report.html")

    def open_output_folder(self) -> None:
        target = None
        if self.results is not None:
            target = self.results.output_dir
        if target is None:
            target = Path(self.ed_output.text().strip() or ".").parent
        self._reveal_path(target, "directory")

    def _reveal_path(self, path: str | Path, kind: str) -> None:
        """Open a report or folder, reporting success only after a real handler accepts it."""
        target = Path(path).expanduser().resolve()
        label = "report.html" if kind == "report.html" else "output folder"
        if not target.exists():
            missing_label = "report.html" if kind == "report.html" else "folder"
            text = f"That {missing_label} does not exist yet — run the pipeline first."
            self._show_error(text)
            self.log_pane.append_line(f"{text} Target: {target}", "ERROR")
            self._status(text)
            return
        if not getattr(sys, "frozen", False) and QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(target))):
            self._note_reveal_success(target, label, "Qt desktop services")
            return
        if getattr(sys, "frozen", False):
            self.log_pane.append_line(
                "frozen build: using sanitised external handlers", "INFO")
        if self._reveal is not None and self._reveal.isRunning():
            self._status(f"Looking for a handler for {target}…")
            return
        self._reveal_path_to_copy = target
        self._reveal = PathRevealThread(target, "directory" if kind == "directory" else "report", self)
        self._reveal.succeeded.connect(
            lambda mechanism: self._note_reveal_success(target, label, mechanism))
        self._reveal.failed.connect(lambda: self._show_reveal_error(target, label))
        self._reveal.start()
        self._status(f"Looking for a handler for {target}…")

    def _note_reveal_success(self, path: Path, label: str, mechanism: str) -> None:
        text = f"Opened {label} with {mechanism}: {path}"
        self.log_pane.append_line(text, "INFO")
        self._status(text, 5000)

    def _show_reveal_error(self, path: Path, label: str) -> None:
        path_text = html.escape(str(path))
        self._show_error(
            f"<b>Could not open {html.escape(label)}.</b> No compatible handler was found for:"
            f"<br><span style='font-family:monospace'>{path_text}</span>"
            "<br>Install xdg-utils, then try again: "
            "<code>sudo pacman -S --needed xdg-utils</code>",
            html=True)
        self._reveal_path_to_copy = path
        self.btn_copy_path.show()
        self.log_pane.append_line(f"Could not open {label}; no handler found: {path}", "ERROR")
        self._status(f"Could not open {label}; copy the path and open it manually.")

    def _copy_reveal_path(self) -> None:
        if self._reveal_path_to_copy is None:
            return
        QGuiApplication.clipboard().setText(str(self._reveal_path_to_copy))
        self._status(f"Copied path: {self._reveal_path_to_copy}", 5000)

    # ==================================================================== misc
    def _status(self, text: str, timeout: int = 0) -> None:
        """One place for the status line (a permanent label, so nothing flickers away)."""
        self.status_label.setText(text)

    def _show_error(self, message: str, html: bool = False) -> None:
        self.lbl_error.setTextFormat(Qt.RichText if html else Qt.RichText)
        self.lbl_error.setText(message)
        self.lbl_error.show()
        self.btn_copy_path.hide()

    def _hide_error(self) -> None:
        self.lbl_error.hide()
        self.btn_copy_path.hide()

    # -------------------------------------------------------------- persistence
    def _restore_settings(self) -> None:
        qs = self.qsettings
        geometry = qs.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        values = {}
        for spec in SPECS:
            key = f"flags/{spec.flag}"
            if qs.contains(key):
                raw = qs.value(key)
                if spec.kind == "bool":
                    values[spec.flag] = str(raw).lower() in ("true", "1")
                elif raw in ("", "None", None):
                    values[spec.flag] = None
                else:
                    values[spec.flag] = raw
        if values:
            self.settings_panel.set_values(values, emit=False)
        self.chk_all_flags.setChecked(qs.value("show_all_flags", False, type=bool))
        self.settings_panel.set_advanced_expanded(qs.value("advanced_open", False, type=bool))
        last_out = qs.value("last_output_path", "")
        if last_out:
            self.ed_output.setText(str(last_out))
            self._derive_diagnostics()

    def save_settings(self) -> None:
        qs = self.qsettings
        qs.setValue("geometry", self.saveGeometry())
        for flag, value in self.settings_panel.values().items():
            qs.setValue(f"flags/{flag}", "None" if value is None else value)
        qs.setValue("show_all_flags", self.chk_all_flags.isChecked())
        qs.setValue("advanced_open", self.settings_panel.advanced_expanded())
        qs.setValue("last_output_path", self.ed_output.text().strip())
        if self.input_path is not None:
            qs.setValue("last_input_dir", str(self.input_path.parent))
        qs.sync()

    def closeEvent(self, event) -> None:  # noqa: D102
        if self.is_running():
            answer = QMessageBox.question(
                self, "A run is still in progress",
                "The pipeline is still running. Cancel it and close the window?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.cancel_run()
            self._runner.wait(8000)
        for thread in (self._probe, self._loader, self._reveal):
            if thread is not None and thread.isRunning():
                thread.abort()
                thread.wait(3000)
        self.save_settings()
        super().closeEvent(event)
