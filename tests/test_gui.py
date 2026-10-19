"""Tests for the optional PySide6 front end (offscreen, no display needed).

Everything here treats the GUI as what it is: an argument builder plus a reader of the
diagnostics directory. The command-line preview is asserted character for character,
because that string is both what the user is taught and what actually runs.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="the GUI extra (pip install -e '.[gui]') is not installed")

from PySide6.QtCore import QMimeData, QPointF, QSettings, Qt, QUrl          # noqa: E402
from PySide6.QtGui import (QDragEnterEvent, QDropEvent, QKeySequence)          # noqa: E402
from PySide6.QtWidgets import QApplication                                    # noqa: E402

from micro_slide_stitcher import __version__                                  # noqa: E402
from micro_slide_stitcher.gui import MISSING_PYSIDE_MESSAGE, main as gui_main  # noqa: E402
from micro_slide_stitcher.gui import cli_spec                                  # noqa: E402
from micro_slide_stitcher.gui.about import (AFFILIATION, AUTHOR, DISCLAIMER, EMAIL,
                                             AboutDialog)                       # noqa: E402
from micro_slide_stitcher.gui.command import command_string                    # noqa: E402
from micro_slide_stitcher.gui import mainwindow                                # noqa: E402
from micro_slide_stitcher.gui import manual as manual_module                    # noqa: E402
from micro_slide_stitcher.gui.manual import ManualWindow                       # noqa: E402
from micro_slide_stitcher.gui.mainwindow import MainWindow, PathRevealThread   # noqa: E402
from micro_slide_stitcher.gui.results import FramePoint, load_results          # noqa: E402
from micro_slide_stitcher.gui.runner import (ProgressState, child_environment,
                                              progress_from_line)               # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
RUN_TIMEOUT_MS = 600_000


# --------------------------------------------------------------------------- fixtures
@pytest.fixture
def gui_settings(tmp_path):
    """A private QSettings file, so tests never touch the real user configuration."""
    return QSettings(str(tmp_path / "gui_settings.ini"), QSettings.IniFormat)


@pytest.fixture
def window(qtbot, gui_settings):
    win = MainWindow(settings=gui_settings)
    win.resize(1280, 800)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    yield win
    if win.is_running():
        win._runner.cancel()
        win._runner.wait(10_000)


def _expected(window: MainWindow, values: dict, *, preview: bool = False) -> str:
    out, diag = window.effective_paths(preview)
    settings = dict(cli_spec.defaults())
    settings.update(values)
    if preview:
        settings["--max-dimension"] = 960
    return command_string(window.input_path, out, diag, settings,
                          include_defaults=window.chk_all_flags.isChecked())


# ------------------------------------------------------------- CLI without PySide6
def test_cli_and_gui_behave_without_pyside6(tmp_path):
    """The CLI must not need PySide6, and the GUI must say so clearly when it is absent."""
    blocker = tmp_path / "blocker"
    blocker.mkdir()
    (blocker / "PySide6.py").write_text(
        "raise ImportError('PySide6 blocked for this test')\n", encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(blocker) + os.pathsep + env.get("PYTHONPATH", "")

    cli = subprocess.run([PY, "-m", "micro_slide_stitcher", "--version"],
                         capture_output=True, text=True, env=env, cwd=str(ROOT))
    assert cli.returncode == 0, cli.stderr
    assert "MicroSlideStitcher" in (cli.stdout + cli.stderr)

    gui = subprocess.run([PY, "-m", "micro_slide_stitcher.gui"],
                         capture_output=True, text=True, env=env, cwd=str(ROOT))
    assert gui.returncode == 4
    assert "PySide6 is not installed" in gui.stderr
    assert "pip install -e '.[gui]'" in gui.stderr
    assert "PySide6 is not installed" in MISSING_PYSIDE_MESSAGE


def test_gui_main_returns_4_when_pyside6_missing(monkeypatch, capsys):
    import micro_slide_stitcher.gui as gui
    monkeypatch.setattr(gui, "pyside6_available", lambda: False)
    assert gui.main([]) == 4
    assert "PySide6 is not installed" in capsys.readouterr().err


# ------------------------------------------------------------------- construction
def test_help_and_version_do_not_open_a_window():
    """`--help` must answer on stdout instead of blocking in the event loop."""
    for args, needle in ((["--help"], "usage: micro-slide-stitcher-gui"),
                         (["-h"], "positional arguments"),
                        (["--version"], f"MicroSlideStitcher GUI {__version__}")):
        proc = subprocess.run([PY, "-m", "micro_slide_stitcher.gui"] + args,
                              capture_output=True, text=True, timeout=120, cwd=str(ROOT),
                              env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        assert proc.returncode == 0, proc.stderr
        assert needle in proc.stdout


def test_window_constructs_and_is_usable_at_1280x800(window):
    assert window.isVisible()
    assert window.minimumWidth() <= 1280 and window.minimumHeight() <= 800
    assert window.tabs.count() == 4
    assert [window.tabs.tabText(i).replace("&", "") for i in range(4)] == [
        "Run log", "Frame quality", "Panorama", "Results summary"]
    assert window.cmd_view.isReadOnly()
    # nothing loaded yet: buttons in a sane state
    assert window.btn_preview.isEnabled() and not window.btn_cancel.isEnabled()


# ------------------------------------------------------------------------ about
def test_about_dialog_opens_from_help_menu(window, qtbot):
    assert window.about_action.text() == "About MicroSlideStitcher"
    window.about_action.trigger()
    qtbot.waitUntil(lambda: window.about_dialog is not None and window.about_dialog.isVisible())
    assert window.about_dialog.windowTitle() == "About MicroSlideStitcher"


def test_about_dialog_opens_from_window_button(window, qtbot):
    qtbot.mouseClick(window.btn_about, Qt.LeftButton)
    qtbot.waitUntil(lambda: window.about_dialog is not None and window.about_dialog.isVisible())
    assert window.about_dialog is not None


def test_about_dialog_content_version_diagnostics_and_licence(qtbot):
    dialog = AboutDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)

    # This direct comparison fails if the visible version is ever hard-coded or drifts.
    assert dialog.version_label.text() == f"Version {__version__}"
    assert dialog.author_label.text() == f"Author: {AUTHOR}"
    assert dialog.affiliation_label.text() == AFFILIATION
    assert EMAIL in dialog.email_label.text()
    assert DISCLAIMER == dialog.disclaimer_label.text()
    assert "MIT Licence · © 2026 Michail Sofopoulos" == dialog.licence_label.text()

    qtbot.mouseClick(dialog.btn_copy_diagnostics, Qt.LeftButton)
    clipboard = QApplication.clipboard().text()
    assert clipboard
    for field in ("Python:", "PySide6:", "Qt:", "OpenCV:"):
        assert field in clipboard

    qtbot.mouseClick(dialog.btn_show_licence, Qt.LeftButton)
    qtbot.waitUntil(dialog.licence_text.isVisible)
    assert "Permission is hereby granted, free of charge" in dialog.licence_text.toPlainText()


def test_every_control_has_a_tooltip_from_the_cli_help(window):
    parser_help = {}
    from micro_slide_stitcher.cli import build_parser
    for action in build_parser()._actions:
        for opt in action.option_strings:
            parser_help[opt] = " ".join((action.help or "").split())
    for spec in cli_spec.SPECS:
        widget = window.settings_panel.widget_for(spec.flag)
        tip = widget.toolTip()
        assert tip, f"{spec.flag} has no tooltip"
        assert spec.flag in tip
        # the tooltip is the CLI's own help text (with %(default)s expanded)
        raw = parser_help[spec.flag].replace("%(default)s", str(spec.default))
        assert raw in tip


# ------------------------------------------------------------------ command preview
@pytest.mark.parametrize("values, expect_flags", [
    ({}, []),
    ({"--sampling-fps": 3.0}, ["--sampling-fps", "3"]),
    ({"--detector": "sift", "--normalize": "none"},
     ["--detector", "sift", "--normalize", "none"]),
    ({"--blur-threshold": 4.5, "--max-dimension": 1280},
     ["--blur-threshold", "4.5", "--max-dimension", "1280"]),
    ({"--no-rejoin": True, "--keep-intermediates": True},
     ["--no-rejoin", "--keep-intermediates"]),
    ({"--blend": "multiband", "--dedup-threshold": 0.999, "--min-inliers": 20,
      "--min-segment-frames": 5, "--rejoin-window": 4},
     ["--blend", "multiband", "--dedup-threshold", "0.999", "--min-inliers", "20",
      "--min-segment-frames", "5", "--rejoin-window", "4"]),
])
def test_command_preview_matches_the_flags_exactly(window, tmp_path, values, expect_flags):
    video = tmp_path / "scan.mp4"
    video.write_bytes(b"not really a video")
    window.input_path = video
    window.ed_output.setText(str(tmp_path / "pano.tif"))
    window._derive_diagnostics()
    window.settings_panel.set_values(values)

    shown = window.command_text()
    assert shown == _expected(window, values)

    parts = shlex.split(shown)
    assert parts[:5] == [PY, "-m", "micro_slide_stitcher", "--input", str(video)]
    assert parts[5:9] == ["--output", str(tmp_path / "pano.tif"),
                          "--diagnostics", str(tmp_path / "pano_diagnostics")]
    assert parts[9:] == expect_flags          # only non-default flags, in canonical order
    # the wrapped display form is the same command, just line-broken
    assert shlex.split(window.cmd_view.toPlainText().replace("\\\n", " ")) == parts


def test_command_preview_can_show_every_default(window, tmp_path):
    window.input_path = tmp_path / "scan.mp4"
    window.ed_output.setText(str(tmp_path / "p.png"))
    window._derive_diagnostics()
    window.chk_all_flags.setChecked(True)
    parts = shlex.split(window.command_text())
    for spec in cli_spec.SPECS:
        if spec.kind in ("bool", "opt_int"):
            continue      # no "off" spelling on the command line
        assert spec.flag in parts
    assert "--sampling-fps" in parts and parts[parts.index("--sampling-fps") + 1] == "2"


def test_quick_preview_forces_960_and_png(window, tmp_path):
    window.input_path = tmp_path / "scan.mp4"
    window.ed_output.setText(str(tmp_path / "pano.tif"))
    window._derive_diagnostics()
    out, diag = window.effective_paths(preview=True)
    assert out.name == "pano_preview.png"
    assert diag.name == "pano_preview_diagnostics"
    parts = shlex.split(window.command_text(preview=True))
    assert parts[parts.index("--max-dimension") + 1] == "960"
    assert parts[parts.index("--output") + 1].endswith("pano_preview.png")
    # the interactive (non-preview) command is unaffected
    assert "--max-dimension" not in shlex.split(window.command_text())


def test_copy_button_puts_the_command_on_the_clipboard(window, tmp_path):
    window.input_path = tmp_path / "scan.mp4"
    window.ed_output.setText(str(tmp_path / "p.png"))
    window._derive_diagnostics()
    window.copy_command()
    assert QApplication.clipboard().text() == window.command_text()


# --------------------------------------------------------------- opening results
def test_failed_qt_open_falls_through_to_gio_and_names_it(qtbot, window, tmp_path,
                                                          monkeypatch):
    report = tmp_path / "report.html"
    report.write_text("<html></html>", encoding="utf-8")
    attempted = []
    monkeypatch.setattr(mainwindow.QDesktopServices, "openUrl", lambda url: False)
    monkeypatch.setattr(
        mainwindow.shutil, "which",
        lambda name: f"/usr/bin/{name}" if name in ("xdg-open", "gio") else None)

    def launch(command):
        attempted.append(command)
        return command[0].endswith("gio")

    monkeypatch.setattr(mainwindow, "_launch_detached", launch)
    window._reveal_path(report, "report.html")
    qtbot.waitUntil(lambda: "Opened report.html with gio open" in window.status_label.text())

    assert [command[0] for command in attempted] == ["/usr/bin/xdg-open", "/usr/bin/gio"]
    assert "Opened report.html with gio open" in window.log_pane.toPlainText()
    assert str(report.resolve()) in window.status_label.text()


def test_frozen_reveal_skips_qt_and_uses_sanitised_external_handlers(
        qtbot, window, tmp_path, monkeypatch):
    report = tmp_path / "report.html"
    report.write_text("<html></html>", encoding="utf-8")
    captured = {}

    class ImmediateProcess:
        def wait(self, timeout):
            assert timeout == mainwindow.REVEAL_CHECK_SECONDS
            return 0

    def popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return ImmediateProcess()

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "/tmp/pyi")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/bundled")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib")
    monkeypatch.setattr(
        mainwindow.QDesktopServices, "openUrl",
        lambda url: pytest.fail("Qt openUrl must not run in a frozen build"))
    monkeypatch.setattr(
        PathRevealThread, "_commands",
        lambda self: [("xdg-open", ["/usr/bin/xdg-open", str(self.path)])])
    monkeypatch.setattr(mainwindow.subprocess, "Popen", popen)

    window._reveal_path(report, "report.html")
    qtbot.waitUntil(lambda: "Opened report.html with xdg-open" in window.status_label.text())

    assert captured["command"] == ["/usr/bin/xdg-open", str(report.resolve())]
    assert captured["env"]["LD_LIBRARY_PATH"] == "/usr/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in captured["env"]
    assert not any(key.startswith("_PYI") for key in captured["env"])
    assert "frozen build: using sanitised external handlers" in window.log_pane.toPlainText()


def test_reveal_failure_surfaces_exact_path_without_success(qtbot, window, tmp_path,
                                                            monkeypatch):
    target = tmp_path / "output folder"
    target.mkdir()
    monkeypatch.setattr(mainwindow.QDesktopServices, "openUrl", lambda url: False)
    monkeypatch.setattr(PathRevealThread, "_commands",
                        lambda self: [("xdg-open", ["/usr/bin/xdg-open", str(self.path)])])
    monkeypatch.setattr(mainwindow, "_launch_detached", lambda command: False)

    window._reveal_path(target, "directory")
    qtbot.waitUntil(window.lbl_error.isVisible)

    exact = str(target.resolve())
    assert exact in window.lbl_error.text()
    assert "sudo pacman -S --needed xdg-utils" in window.lbl_error.text()
    assert window.btn_copy_path.isVisible()
    assert "Opened" not in window.status_label.text()
    qtbot.mouseClick(window.btn_copy_path, Qt.LeftButton)
    assert QApplication.clipboard().text() == exact


def test_missing_reveal_target_has_distinct_message_and_starts_no_handler(
        window, tmp_path, monkeypatch):
    target = tmp_path / "not-created-yet"
    monkeypatch.setattr(
        mainwindow.QDesktopServices, "openUrl",
        lambda url: pytest.fail("Qt openUrl must not run for a missing target"))
    monkeypatch.setattr(
        mainwindow, "_launch_detached",
        lambda command: pytest.fail("external handlers must not run for a missing target"))

    window._reveal_path(target, "directory")

    assert "That folder does not exist yet — run the pipeline first." in window.lbl_error.text()
    assert "No compatible handler" not in window.lbl_error.text()
    assert "That folder does not exist yet" in window.status_label.text()


def test_nonfrozen_reveal_tries_qt_first_and_names_the_mechanism(
        window, tmp_path, monkeypatch):
    report = tmp_path / "report.html"
    report.write_text("<html></html>", encoding="utf-8")
    calls = []
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(mainwindow.QDesktopServices, "openUrl",
                        lambda url: calls.append(url) or True)
    monkeypatch.setattr(mainwindow, "_launch_detached",
                        lambda command: pytest.fail("fallback must not run after Qt success"))

    window._reveal_path(report, "report.html")

    assert len(calls) == 1
    assert "Opened report.html with Qt desktop services" in window.status_label.text()
    assert str(report.resolve()) in window.status_label.text()
    assert "Qt desktop services" in window.log_pane.toPlainText()


def test_reveal_subprocess_uses_the_sanitised_child_environment(monkeypatch):
    captured = {}

    class ImmediateProcess:
        def wait(self, timeout):
            assert timeout == mainwindow.REVEAL_CHECK_SECONDS
            return 0

    def popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return ImmediateProcess()

    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "/tmp/pyi")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/bundled")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib")
    monkeypatch.setattr(mainwindow.subprocess, "Popen", popen)

    assert mainwindow._launch_detached(["/usr/bin/xdg-open", "/tmp/output"])
    assert captured["env"]["LD_LIBRARY_PATH"] == "/usr/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in captured["env"]
    assert not any(key.startswith("_PYI") for key in captured["env"])
    assert captured["start_new_session"] is True


def test_child_environment_is_unchanged_without_pyinstaller_variables(monkeypatch):
    monkeypatch.delenv("_PYI_APPLICATION_HOME_DIR", raising=False)
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    monkeypatch.setenv("MICRO_SLIDE_STITCHER_TEST", "kept")
    assert child_environment()["MICRO_SLIDE_STITCHER_TEST"] == "kept"


# ------------------------------------------------------------------ input handling
def test_file_load_populates_metadata(qtbot, window, synthetic_video):
    window.load_input(synthetic_video["video"])
    qtbot.waitUntil(lambda: bool(window.metadata), timeout=30_000)
    assert window.meta_labels["dimensions"].text() == "480 × 360 px"
    assert "30.000 fps" in window.meta_labels["fps"].text()
    assert window.meta_labels["codec"].text().startswith("h264")
    assert float(window.meta_labels["duration"].text().split(" s")[0]) > 1.0
    assert int(window.meta_labels["frames"].text()) > 10
    # loading a video also fills in the output/diagnostics paths
    assert window.ed_output.text().endswith(".tif")
    assert window.ed_diag.text().endswith("_diagnostics")


def test_drag_and_drop_loads_the_video(qtbot, window, synthetic_video):
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(synthetic_video["video"]))])
    enter = QDragEnterEvent(QPointF(20, 20).toPoint(), Qt.CopyAction, md,
                            Qt.LeftButton, Qt.NoModifier)
    window.dragEnterEvent(enter)
    assert enter.isAccepted()

    drop = QDropEvent(QPointF(20, 20), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    window.dropEvent(drop)
    assert window.input_path == synthetic_video["video"]
    qtbot.waitUntil(lambda: bool(window.metadata), timeout=30_000)
    assert str(synthetic_video["video"]) in window.command_text()


def test_image_folder_load_populates_metadata_and_command_preview(qtbot, window, synthetic_images):
    window.load_input(synthetic_images["images"])
    qtbot.waitUntil(lambda: bool(window.metadata), timeout=30_000)
    assert window.input_mode == "images"
    assert window.meta_labels["fps"].text() == "10"
    assert window.meta_captions["fps"].text() == "Image count:"
    assert window.meta_labels["duration"].text() == "name"
    parts = shlex.split(window.command_text())
    assert parts[:5] == [PY, "-m", "micro_slide_stitcher", "--input",
                         str(synthetic_images["images"])]
    assert parts[parts.index("--input-mode") + 1] == "images"


def test_image_input_disables_video_sampling_and_omits_it_from_command(
        qtbot, window, synthetic_images):
    sampling = window.settings_panel.widget_for("--sampling-fps")
    ordering = window.settings_panel.widget_for("--image-order")
    stride = window.settings_panel.widget_for("--image-stride")
    # Before a source is loaded, the input mode is genuinely unresolved.
    assert sampling.isEnabled() and ordering.isEnabled() and stride.isEnabled()

    window.load_input(synthetic_images["images"])
    qtbot.waitUntil(lambda: bool(window.metadata), timeout=30_000)
    assert not sampling.isEnabled()
    assert sampling.toolTip() == (
        "--sampling-fps\n\nApplies to video input only and is ignored for image sequences.")
    assert ordering.isEnabled() and stride.isEnabled()

    # Even a previously saved/non-default sampling rate cannot leak into an image command.
    window.settings_panel.set_values({"--sampling-fps": 7.0})
    window.chk_all_flags.setChecked(True)
    parts = shlex.split(window.command_text())
    assert "--sampling-fps" not in parts
    assert "--image-order" in parts and "--image-stride" in parts


def test_video_input_disables_image_controls_and_omits_them_from_command(
        qtbot, window, synthetic_video):
    ordering = window.settings_panel.widget_for("--image-order")
    stride = window.settings_panel.widget_for("--image-stride")
    sampling = window.settings_panel.widget_for("--sampling-fps")

    window.load_input(synthetic_video["video"])
    qtbot.waitUntil(lambda: bool(window.metadata), timeout=30_000)
    assert sampling.isEnabled()
    assert not ordering.isEnabled() and not stride.isEnabled()
    assert ordering.toolTip() == (
        "--image-order\n\nApplies to image-sequence input only and has no effect for "
        "video input.")
    assert stride.toolTip() == (
        "--image-stride\n\nApplies to image-sequence input only and has no effect for "
        "video input.")

    window.settings_panel.set_values({"--image-order": "mtime", "--image-stride": 3})
    window.chk_all_flags.setChecked(True)
    parts = shlex.split(window.command_text())
    assert "--image-order" not in parts and "--image-stride" not in parts
    assert "--sampling-fps" in parts


def test_non_video_drop_is_ignored(window, tmp_path):
    doc = tmp_path / "notes.txt"
    doc.write_text("hello", encoding="utf-8")
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(doc))])
    enter = QDragEnterEvent(QPointF(20, 20).toPoint(), Qt.CopyAction, md,
                            Qt.LeftButton, Qt.NoModifier)
    window.dragEnterEvent(enter)
    assert not enter.isAccepted()
    assert window.input_path is None


# --------------------------------------------------------------------- timeline
def _points() -> list[FramePoint]:
    return [FramePoint(i, i * 0.5, score, score >= 10, "accepted" if score >= 10 else "blurry")
            for i, score in enumerate([4.0, 12.0, 30.0, 41.0, 7.5, 55.0])]


def test_threshold_drag_updates_setting_and_command(window, tmp_path):
    window.input_path = tmp_path / "scan.mp4"
    window.ed_output.setText(str(tmp_path / "p.png"))
    window._derive_diagnostics()
    window.timeline.set_points(_points())
    assert window.timeline.has_data

    window.timeline.drag_threshold_to(31.5)
    assert window.settings_panel.values()["--blur-threshold"] == pytest.approx(31.5)
    parts = shlex.split(window.command_text())
    assert parts[parts.index("--blur-threshold") + 1] == "31.5"
    keep, drop = window.timeline.counts()
    assert (keep, drop) == (2, 4)
    assert "--blur-threshold 31.5" in window.lbl_timeline.text()

    window.timeline.drag_threshold_to(5.0)
    assert shlex.split(window.command_text())[-1] == "5"
    assert window.timeline.counts() == (5, 1)


def test_timeline_maps_mouse_position_to_score(window):
    window.timeline.set_points(_points())
    window.timeline.resize(700, 320)
    rect = window.timeline._plot_rect()
    y_of_20 = window.timeline._y_of(20.0)
    assert window.timeline._score_of_y(y_of_20) == pytest.approx(20.0, abs=0.5)
    assert window.timeline._score_of_y(rect.bottom()) == pytest.approx(0.0, abs=0.5)


# ---------------------------------------------------------------- progress parsing
def test_progress_is_derived_from_log_lines():
    state = ProgressState()
    lines = [
        "2026-08-22 18:05:34 INFO    Probed scan.mp4: 1920x1080, 30.000 fps, 49.17 s, "
        "codec=h264, ~1476 frames, audio=True",
        "2026-08-22 18:05:34 INFO    Extracting frames at 2.000 fps (filter: fps=2.0)",
        "2026-08-22 18:05:46 INFO    Extracted 98 candidate frames into /tmp/_work",
        "2026-08-22 18:05:50 INFO    Frame gating: 93/98 accepted (5 rejected)",
        "2026-08-22 18:06:00 INFO    Built 2 raw segment(s) (sizes [70, 23]) before rejoin",
        "2026-08-22 18:06:03 INFO    Canvas bounds: x[0,10] y[0,10] -> 10x10 px (~0.1 GiB)",
        "2026-08-22 18:06:04 DEBUG   blended 35/70 frames",
        "2026-08-22 18:06:09 INFO    Wrote panorama /tmp/p.png (2136x9226, 12.7 MB)",
        "2026-08-22 18:06:11 INFO    VERDICT: PARTIALLY SUCCESSFUL",
    ]
    seen = []
    for line in lines:
        assert progress_from_line(line, state)
        seen.append(state.percent)
    assert seen == sorted(seen)
    assert state.n_candidates == 98 and state.n_accepted == 93
    assert state.percent == 100
    assert "PARTIALLY SUCCESSFUL" in state.stage


# ------------------------------------------------------------------ real GUI runs
def test_end_to_end_quick_preview_run(qtbot, window, synthetic_video, tmp_path):
    """A real quick-preview run through the GUI: log, summary, timeline and viewer."""
    window.load_input(synthetic_video["video"])
    qtbot.waitUntil(lambda: bool(window.metadata), timeout=30_000)
    window.ed_output.setText(str(tmp_path / "pano.tif"))
    window._derive_diagnostics()
    window.settings_panel.set_values({"--blur-threshold": 5.0, "--normalize": "none",
                                      "--min-inliers": 20})

    with qtbot.waitSignal(window.runFinished, timeout=RUN_TIMEOUT_MS) as sig:
        qtbot.mouseClick(window.btn_preview, Qt.LeftButton)
        assert window.is_running()
    assert sig.args == [0], window.log_pane.toPlainText()[-2000:]

    out, diag = window.effective_paths(preview=True)
    assert out.exists() and out.suffix == ".png"
    summary = json.loads((diag / "summary.json").read_text())
    assert summary["settings"]["max_dimension"] == "960"

    # log pane really carries the pipeline's lines
    text = window.log_pane.toPlainText()
    assert "MicroSlideStitcher" in text and "VERDICT" in text
    assert "--max-dimension 960" in text

    # progress reached the end
    assert window.progress.value() == 100

    # summary panel is populated from summary.json (not re-derived)
    assert window.results is not None
    assert window.results.verdict == summary["verdict"]
    assert summary["verdict"] in window.lbl_verdict.text()
    labels = [window.summary_grid.itemAt(i).widget().text()
              for i in range(window.summary_grid.count())]
    assert any("Verdict:" in t for t in labels)
    assert any(str(summary["counts"]["accepted"]) in t for t in labels)

    # timeline populated from frame_metrics.csv
    assert window.timeline.has_data
    assert len(window.results.frames) == summary["counts"]["candidates"]

    # viewer shows the preview PNG
    qtbot.waitUntil(lambda: window.panorama.view.has_image, timeout=60_000)
    assert window.cmb_segment.count() == len(summary["segments"])
    assert "coverage" in window.lbl_segment.text()

    # report + folder buttons have something to open
    assert window.results.report_html is not None and window.results.report_html.exists()
    assert window.results.output_dir == out.parent

    # full-resolution load works too
    window.load_full_resolution()
    qtbot.waitUntil(lambda: window.panorama.view.has_image, timeout=60_000)


def test_cancel_terminates_a_run(qtbot, window, gap_video_disconnected, tmp_path):
    window.load_input(gap_video_disconnected["video"])
    qtbot.waitUntil(lambda: bool(window.metadata), timeout=30_000)
    window.ed_output.setText(str(tmp_path / "cancelled.tif"))
    window._derive_diagnostics()
    # deliberately slow settings so there is a run to cancel
    window.settings_panel.set_values({"--sampling-fps": 10.0, "--detector": "sift",
                                      "--blur-threshold": 5.0, "--normalize": "none"})

    with qtbot.waitSignal(window.runFinished, timeout=RUN_TIMEOUT_MS) as sig:
        qtbot.mouseClick(window.btn_full, Qt.LeftButton)
        qtbot.waitUntil(lambda: "Extracting frames" in window.log_pane.toPlainText(),
                        timeout=120_000)
        window.cancel_run()
    assert sig.args == [-1]
    assert not window.is_running()
    assert window._runner._proc.poll() is not None       # the child really died
    diag = Path(window.ed_diag.text())
    assert (diag / "cancelled.txt").exists()             # workspace left explained
    assert not (diag / "summary.json").exists()
    assert not (tmp_path / "cancelled.tif").exists()
    assert window.btn_full.isEnabled() and not window.btn_cancel.isEnabled()


def test_multi_segment_run_produces_per_segment_views(qtbot, window, gap_video_disconnected,
                                                     tmp_path):
    window.load_input(gap_video_disconnected["video"])
    qtbot.waitUntil(lambda: bool(window.metadata), timeout=30_000)
    window.ed_output.setText(str(tmp_path / "split.png"))
    window._derive_diagnostics()
    window.settings_panel.set_values({"--sampling-fps": 2.0, "--blur-threshold": 5.0,
                                      "--normalize": "none", "--min-inliers": 20})

    with qtbot.waitSignal(window.runFinished, timeout=RUN_TIMEOUT_MS) as sig:
        qtbot.mouseClick(window.btn_full, Qt.LeftButton)
    assert sig.args == [0], window.log_pane.toPlainText()[-2000:]

    summary = json.loads((tmp_path / "split_diagnostics" / "summary.json").read_text())
    assert summary["counts"]["segments_written"] == 2
    assert (tmp_path / "split.png").exists() and (tmp_path / "split_seg02.png").exists()

    assert window.cmb_segment.count() == 2
    assert window.cmb_segment.isEnabled()
    first = window.lbl_segment.text()
    assert "Segment 1" in first and "split.png" in first
    qtbot.waitUntil(lambda: window.panorama.view.has_image, timeout=60_000)

    window.cmb_segment.setCurrentIndex(1)
    second = window.lbl_segment.text()
    assert "Segment 2" in second and "split_seg02.png" in second
    assert second != first
    for seg in window.results.segments:
        assert seg.n_frames > 0 and seg.width > 0 and seg.coverage_percent > 0
        assert seg.time_end_s >= seg.time_start_s
    qtbot.waitUntil(lambda: window.panorama.view.has_image, timeout=60_000)


def test_pipeline_failure_is_surfaced_with_a_clickable_diagnostics_path(qtbot, window,
                                                                       synthetic_video,
                                                                       tmp_path):
    """Exit code 3: show the failure and keep the diagnostics path clickable."""
    window.load_input(synthetic_video["video"])
    qtbot.waitUntil(lambda: bool(window.metadata), timeout=30_000)
    window.ed_output.setText(str(tmp_path / "fail.png"))
    window._derive_diagnostics()
    # an impossible focus gate: no frame passes, so the pipeline exits 3
    window.settings_panel.set_values({"--blur-threshold": 1e5, "--sampling-fps": 1.0})

    with qtbot.waitSignal(window.runFinished, timeout=RUN_TIMEOUT_MS) as sig:
        qtbot.mouseClick(window.btn_full, Qt.LeftButton)
    assert sig.args == [3]
    diag = Path(window.ed_diag.text())
    assert (diag / "failure.txt").exists()
    assert window.lbl_error.isVisible()
    body = window.lbl_error.text()
    assert "exit code 3" in body
    assert "passed quality gating" in body
    assert f'href="file://{diag}"' in body
    assert window.lbl_error.openExternalLinks()
    # the timeline still works: the run wrote frame_metrics.csv before failing
    assert window.timeline.has_data


# ---------------------------------------------------------- diagnostics reading
def _fake_diagnostics(root: Path) -> Path:
    """A minimal two-segment diagnostics directory, in the schema cli.py writes."""
    diag = root / "diag"
    diag.mkdir(parents=True)
    (diag / "frame_metrics.csv").write_text(textwrap.dedent("""\
        index,filename,timestamp_s,focus_score,duplicate_score,mean_intensity,accepted,reason
        0,cand_00001.png,0.0,148.3,0.0,140.7,True,accepted
        1,cand_00002.png,0.5,4.2,0.29,140.5,False,blurry (focus 4.2 < 10.0)
        """), encoding="utf-8")
    summary = {
        "verdict": "PARTIALLY SUCCESSFUL",
        "input": "/videos/scan.mp4",
        "counts": {"candidates": 2, "accepted": 1, "rejected_blur": 1, "rejected_duplicate": 0,
                   "rejected_other": 0, "frames_stitched": 1, "segments": 2,
                   "segments_written": 2, "segments_skipped": 1, "frames_written_total": 1,
                   "rejoins_accepted": 0, "rejoin_attempts": 9},
        "registration_stats": {"pairs_attempted": 3, "pairs_registered": 2, "pairs_failed": 1,
                               "pairs_bridged": 0, "sift_fallbacks": 1,
                               "inliers": {"n": 2, "min": 27.0, "median": 832.0, "max": 2711.0},
                               "mean_reproj_error_px": {"n": 2, "median": 1.18}},
        "timing": {"total_s": 36.7, "extract_s": 11.1, "gate_s": 4.4, "register_s": 12.9,
                   "stitch_s": 8.2},
        "video_metadata": {"width": 1920, "height": 1080, "fps": 30.0, "duration_s": 49.2,
                           "codec": "h264"},
        "warnings": ["chain break after frame 72"],
        "outputs": {"report": str(diag / "report.html")},
        "rejoin": {"enabled": True, "window": 3, "attempts": []},
        "skipped_segments": [{"n_frames": 2, "time_start_s": 1.0, "time_end_s": 1.5}],
        "segments": [
            {"rank": 1, "n_frames": 70, "frame_index_first": 0, "frame_index_last": 72,
             "time_start_s": 0.0, "time_end_s": 36.0, "width": 2136, "height": 9226,
             "coverage_percent": 54.89, "output": str(root / "pano.png"),
             "preview_name": "panorama_preview.png", "camera_path_name": "camera_path.png"},
            {"rank": 2, "n_frames": 23, "frame_index_first": 73, "frame_index_last": 97,
             "time_start_s": 36.5, "time_end_s": 48.5, "width": 1328, "height": 2650,
             "coverage_percent": 73.7, "output": str(root / "pano_seg02.png"),
             "preview_name": "panorama_preview_seg02.png",
             "camera_path_name": "camera_path_seg02.png"},
        ],
    }
    (diag / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (diag / "report.html").write_text("<html></html>", encoding="utf-8")
    return diag


def test_load_results_reads_summary_and_metrics(tmp_path):
    diag = _fake_diagnostics(tmp_path)
    res = load_results(diag)
    assert res.verdict == "PARTIALLY SUCCESSFUL"
    assert len(res.frames) == 2 and res.frames[0].accepted and not res.frames[1].accepted
    assert [s.rank for s in res.segments] == [1, 2]
    assert res.segments[1].preview_path.endswith("panorama_preview_seg02.png")
    assert res.report_html == diag / "report.html"
    assert res.output_dir == tmp_path


def test_summary_panel_and_segment_selector_from_a_diagnostics_dir(window, tmp_path):
    diag = _fake_diagnostics(tmp_path)
    window.load_diagnostics(diag)
    assert "PARTIALLY SUCCESSFUL" in window.lbl_verdict.text()
    texts = [window.summary_grid.itemAt(i).widget().text()
             for i in range(window.summary_grid.count())]
    joined = "\n".join(texts)
    assert "1 accepted, 1 blurry" in joined
    assert "2 found, 2 written, 1 skipped" in joined
    assert "9 wide-baseline attempt(s), 0 accepted" in joined
    assert "832" in joined                     # inlier median, straight from summary.json
    assert "1.18 px" in joined                 # reprojection error median
    assert "36.7 s total" in joined
    assert "chain break after frame 72" in joined
    assert window.cmb_segment.count() == 2
    assert "2136 × 9226 px" in window.lbl_segment.text()
    assert window.timeline.has_data


def test_settings_are_persisted_between_windows(qtbot, gui_settings, tmp_path):
    first = MainWindow(settings=gui_settings)
    qtbot.addWidget(first)
    first.settings_panel.set_values({"--sampling-fps": 4.0, "--detector": "sift",
                                     "--max-dimension": 1280, "--no-rejoin": True})
    first.ed_output.setText(str(tmp_path / "remembered.tif"))
    first.input_path = tmp_path / "video.mp4"
    first.save_settings()
    first.close()

    second = MainWindow(settings=QSettings(gui_settings.fileName(), QSettings.IniFormat))
    qtbot.addWidget(second)
    values = second.settings_panel.values()
    assert values["--sampling-fps"] == pytest.approx(4.0)
    assert values["--detector"] == "sift"
    assert values["--max-dimension"] == 1280
    assert values["--no-rejoin"] is True
    assert second.ed_output.text() == str(tmp_path / "remembered.tif")
    assert second.qsettings.value("last_input_dir") == str(tmp_path)
    second.close()


def test_reset_to_defaults_restores_the_cli_defaults(window):
    window.settings_panel.set_values({"--sampling-fps": 7.0, "--detector": "sift"})
    window.settings_panel.reset_to_defaults()
    assert window.settings_panel.values() == cli_spec.defaults()


# ------------------------------------------------------- in-app user manual (0.6.0)
def test_manual_opens_from_the_help_menu_action(window):
    """Help -> User manual creates and shows one reusable window."""
    assert window.manual_window is None
    assert window.manual_action.text() == "&User manual"
    assert window.manual_action.shortcut() == QKeySequence(Qt.Key_F1)
    menu_actions = [a.text() for a in window.menuBar().actions()[0].menu().actions()]
    assert "&User manual" in menu_actions
    window.manual_action.trigger()
    manual = window.manual_window
    assert manual is not None and manual.isVisible()
    # Triggering again must reuse the same window, not stack duplicates.
    window.manual_action.trigger()
    assert window.manual_window is manual
    manual.close()


def test_manual_opens_with_the_f1_key(window, qtbot):
    assert window.manual_window is None
    window.activateWindow()
    qtbot.keyClick(window, Qt.Key_F1)
    qtbot.waitUntil(lambda: window.manual_window is not None, timeout=3000)
    assert window.manual_window.isVisible()
    window.manual_window.close()


def test_manual_table_of_contents_anchors_all_resolve(qtbot):
    """Every contents link must have a target that the browser can actually scroll to."""
    manual = ManualWindow()
    qtbot.addWidget(manual)
    manual.resize(1000, 700)
    manual.show()
    qtbot.waitExposed(manual)
    document_html = manual_module.manual_html()
    positions = []
    for anchor in manual_module.anchors():
        assert f'<a name="{anchor}">' in document_html
        assert f'href="#{anchor}"' in document_html or anchor == "top"
        manual.go_to_anchor(anchor)
        qtbot.wait(10)
        positions.append(manual.view.verticalScrollBar().value())
    # Sections appear in order, so the scroll positions must strictly increase:
    # an unresolved anchor would leave the view where it was.
    assert positions == sorted(positions)
    assert len(set(positions)) == len(positions)
    assert positions[-1] > 0
    # ...and the history that Back/Forward use was really filled.
    assert manual.view.isBackwardAvailable()
    manual.view.backward()
    assert manual.view.isForwardAvailable()
    manual.close()


def test_manual_find_box_highlights_every_match_and_jumps(qtbot):
    manual = ManualWindow()
    qtbot.addWidget(manual)
    manual.resize(1000, 700)
    manual.show()
    qtbot.waitExposed(manual)
    manual.ed_find.setText("stabilisation")
    qtbot.wait(20)
    count = manual.match_count()
    assert count >= 2, "the recording section must mention video stabilisation"
    assert f"{count} match" in manual.lbl_matches.text()
    highlights = manual.view.extraSelections()
    assert len(highlights) == count
    assert highlights[0].format.background().color().name() == \
        manual_module.FIND_HIGHLIGHT.name()
    assert manual.view.textCursor().selectedText().lower() == "stabilisation"

    first = manual.view.textCursor().position()
    assert manual.find_next()
    second = manual.view.textCursor().position()
    assert second != first
    assert manual.find_previous()

    manual.ed_find.setText("thisphrasedoesnotoccuranywhere")
    qtbot.wait(20)
    assert manual.match_count() == 0
    assert manual.lbl_matches.text() == "no match"
    assert manual.view.extraSelections() == []
    manual.close()


def test_manual_saves_itself_as_a_standalone_html_file(qtbot, tmp_path):
    manual = ManualWindow()
    qtbot.addWidget(manual)
    target = manual.save_html(tmp_path / "for_a_colleague")
    assert target == tmp_path / "for_a_colleague.html"
    written = target.read_text(encoding="utf-8")
    assert len(written) > 20_000
    assert target.stat().st_size > 0
    assert written.lstrip().startswith("<!DOCTYPE html>")
    assert "<style>" in written                      # readable outside Qt as well
    assert DISCLAIMER in written                     # same wording as the About dialog
    assert "--sampling-fps" in written               # the generated flag table came along
    manual.close()


def test_manual_flag_table_covers_every_flag_of_the_real_parser(qtbot):
    """Section 8 is generated from cli.build_parser(), so it cannot drift.

    This test is the contract: add a flag to the CLI and it must appear in the
    manual, exactly as cli_spec guarantees for the settings tooltips.
    """
    from micro_slide_stitcher.cli import build_parser

    table = manual_module.flag_table_html()
    manual = ManualWindow()
    qtbot.addWidget(manual)
    rendered = manual.view.document().toPlainText()

    parser_flags = [opt for action in build_parser()._actions
                    for opt in action.option_strings]
    assert len(parser_flags) > 25
    missing_from_table = [f for f in parser_flags if f">{f}<" not in table]
    assert missing_from_table == [], \
        f"flags absent from the generated manual table: {missing_from_table}"
    missing_from_render = [f for f in parser_flags if f not in rendered]
    assert missing_from_render == [], \
        f"flags absent from the rendered manual: {missing_from_render}"
    # The defaults shown are the parser's own, not a second copy.
    assert ">2.0<" in table or "2.0" in table
    for spec in cli_spec.SPECS:
        assert f">{spec.flag}<" in table

    # The check above has teeth: prove that the same comparison rejects a table
    # which is missing a flag.  (A hand-written table would drift like this; a
    # generated one cannot, which is exactly why section 8 is generated.)
    truncated = table.replace("<code>--blur-threshold</code>", "<code>--nope</code>")
    assert [f for f in parser_flags if f">{f}<" not in truncated] == ["--blur-threshold"]
    manual.close()


def test_manual_never_pulls_in_qtwebengine():
    """QtWebEngine is deliberately not a dependency: QTextBrowser only."""
    import micro_slide_stitcher.gui.manual as loaded
    assert "QTextBrowser" in Path(loaded.__file__).read_text(encoding="utf-8")
    for name in sys.modules:
        assert "WebEngine" not in name, f"{name} was imported"
    gui_dir = ROOT / "src" / "micro_slide_stitcher" / "gui"
    for source in gui_dir.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                assert "WebEngine" not in stripped and "QtWebView" not in stripped, \
                    f"{source.name} imports a web engine: {stripped}"
    # And a clean interpreter that builds the manual must not load one either.
    code = textwrap.dedent("""
        import os, sys
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication([sys.argv[0]])
        from micro_slide_stitcher.gui.manual import ManualWindow
        w = ManualWindow(); w.resize(900, 600); w.show()
        app.processEvents()
        assert w.view.document().characterCount() > 15000
        bad = [m for m in sys.modules if "WebEngine" in m]
        print("WEBENGINE_MODULES=" + repr(bad))
    """)
    proc = subprocess.run([PY, "-c", code], capture_output=True, text=True,
                          cwd=str(ROOT), env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    assert proc.returncode == 0, proc.stderr
    assert "WEBENGINE_MODULES=[]" in proc.stdout


def test_manual_content_is_consistent_with_the_about_dialog(qtbot):
    text = manual_module.manual_html()
    assert DISCLAIMER in text
    assert AUTHOR in text
    assert AFFILIATION in text
    assert EMAIL in text
    assert __version__ in text
    rendered = ManualWindow().view.document().toPlainText()
    for phrase in ("not for diagnosis", "LOCK", "white balance", "50", "stabilisation",
                   "1080", "focus_score", "frame_metrics.csv", "PARTIALLY SUCCESSFUL",
                   "report.html", "xcb-util-cursor", "MIT Licence"):
        assert phrase in rendered or phrase.lower() in rendered.lower(), phrase
