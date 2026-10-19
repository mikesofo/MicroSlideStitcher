"""Offscreen functional smoke test that runs inside a frozen MicroSlideStitcher executable."""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", required=True, type=Path)
    ap.add_argument("--output-dir", required=True, type=Path)
    ap.add_argument("--video-ffmpeg-absent", type=Path,
                    help="also verify the GUI's missing-ffmpeg modal and exit 5")
    ns = ap.parse_args(argv)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    from micro_slide_stitcher.gui.mainwindow import MainWindow

    app = QApplication.instance() or QApplication([sys.argv[0]])
    out = ns.output_dir
    out.mkdir(parents=True, exist_ok=True)
    win = MainWindow()
    win.resize(1280, 820)
    win.show()
    app.processEvents()
    win.load_input(ns.images)
    deadline = time.monotonic() + 30
    while not win.metadata and time.monotonic() < deadline:
        app.processEvents(QEventLoop.AllEvents, 50)
        time.sleep(0.02)
    if not win.metadata or win.input_mode != "images":
        print("SELFTEST FAIL: image folder did not load", file=sys.stderr)
        return 20
    win.ed_output.setText(str(out / "quick_preview.png"))
    win.ed_diag.setText(str(out / "diagnostics"))
    expected_output, expected_diag = win.effective_paths(preview=True)
    win.start_preview_run()
    deadline = time.monotonic() + 120
    while win.is_running() and time.monotonic() < deadline:
        app.processEvents(QEventLoop.AllEvents, 50)
        time.sleep(0.02)
    app.processEvents()
    if win.is_running() or not expected_output.exists():
        print("SELFTEST FAIL: quick preview did not complete", file=sys.stderr)
        return 21
    win.grab().save(str(out / "mainwindow.png"))
    win.show_about_dialog()
    app.processEvents()
    about = win.about_dialog
    assert about is not None
    diagnostics = about.diagnostics_view.toPlainText()
    about.grab().save(str(out / "about.png"))
    (out / "about_diagnostics.txt").write_text(diagnostics + "\n", encoding="utf-8")
    if "Frozen PyInstaller build: yes" not in diagnostics:
        print("SELFTEST FAIL: about dialog did not report frozen build", file=sys.stderr)
        return 22
    # In-app user manual: it must build from the module-level HTML string inside the
    # frozen bundle, its table-of-contents anchors must resolve, and section 8's flag
    # table must be generated from the real parser -- not silently empty.
    win.show_manual()
    app.processEvents()
    manual = win.manual_window
    assert manual is not None
    manual.resize(1000, 760)
    app.processEvents()
    manual.go_to_anchor("s8")
    app.processEvents()
    manual.grab().save(str(out / "manual_flagtable.png"))
    manual.ed_find.setText("blur-threshold")
    app.processEvents()
    manual.update_find()
    matches = manual.match_count()
    manual.grab().save(str(out / "manual_find.png"))
    manual.ed_find.clear()
    manual.go_to_anchor("top")
    app.processEvents()
    manual.grab().save(str(out / "manual.png"))
    manual_text = manual.view.document().toPlainText()
    (out / "manual_text.txt").write_text(manual_text, encoding="utf-8")
    from micro_slide_stitcher.gui import manual as _manual

    flags = sorted({opt for action in _manual.flag_actions()
                    for opt in action.option_strings})
    missing = [flag for flag in flags if flag not in manual_text]
    manual_html_text = manual.view.toHtml()
    anchors_missing = [a for a in _manual.anchors() if a not in manual_html_text]
    if (len(manual_text) < 8000 or missing or anchors_missing or len(flags) < 15
            or "Every setting there is" not in manual_text or not matches):
        print("SELFTEST FAIL: user manual did not render inside the frozen build "
              f"(chars={len(manual_text)}, flags={len(flags)}, matches={matches}, "
              f"missing={missing[:5]}, anchors_missing={anchors_missing[:5]})",
              file=sys.stderr)
        return 25
    (out / "manual_flags.txt").write_text(
        f"flags_in_table={len(flags)}\nmanual_chars={len(manual_text)}\n"
        f"find_matches_blur_threshold={matches}\n" + "\n".join(flags) + "\n",
        encoding="utf-8")
    manual.close()
    app.processEvents()
    target = expected_diag / "report.html"
    win._reveal_path(target, "report.html")
    deadline = time.monotonic() + 8
    while win._reveal is not None and win._reveal.isRunning() and time.monotonic() < deadline:
        app.processEvents(QEventLoop.AllEvents, 50)
        time.sleep(0.02)
    app.processEvents()
    (out / "reveal_log.txt").write_text(win.log_pane.toPlainText(), encoding="utf-8")
    if "frozen build: using sanitised external handlers" not in win.log_pane.toPlainText():
        print("SELFTEST FAIL: frozen reveal branch not logged", file=sys.stderr)
        return 23
    if ns.video_ffmpeg_absent:
        from PySide6.QtWidgets import QMessageBox
        video_out = out / "missing_ffmpeg.png"
        win.input_path = ns.video_ffmpeg_absent
        win.input_mode = "video"
        win.ed_output.setText(str(video_out))
        win.ed_diag.setText(str(out / "missing_ffmpeg_diagnostics"))
        modal_text: list[str] = []

        def dismiss_modal() -> None:
            for widget in app.topLevelWidgets():
                if isinstance(widget, QMessageBox) and widget.isVisible():
                    modal_text.append(widget.text())
                    widget.accept()

        timer = QTimer()
        timer.timeout.connect(dismiss_modal)
        timer.start(20)
        win.start_preview_run()
        deadline = time.monotonic() + 30
        while win.is_running() and time.monotonic() < deadline:
            app.processEvents(QEventLoop.AllEvents, 50)
            time.sleep(0.02)
        # The QThread's done signal is queued; give the GUI thread a chance to
        # run _on_run_done(), which creates the modal QMessageBox.
        modal_deadline = time.monotonic() + 3
        while not modal_text and time.monotonic() < modal_deadline:
            app.processEvents(QEventLoop.AllEvents, 50)
            time.sleep(0.02)
        timer.stop()
        dismiss_modal()
        if (win._runner is None or win._runner.exit_code != 5 or not modal_text
                or "ffmpeg/ffprobe was not found" not in modal_text[0]):
            print("SELFTEST FAIL: missing-ffmpeg GUI modal / exit code not observed", file=sys.stderr)
            return 24
        (out / "missing_ffmpeg_modal.txt").write_text(modal_text[0] + "\n", encoding="utf-8")
    print("SELFTEST PASS: image folder, quick preview, About frozen state, user manual "
          "with a generated flag table, frozen reveal branch"
          + (", and missing-ffmpeg GUI modal" if ns.video_ffmpeg_absent else ""))
    if win._reveal is not None and win._reveal.isRunning():
        win._reveal.abort()
        win._reveal.wait(3000)
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
