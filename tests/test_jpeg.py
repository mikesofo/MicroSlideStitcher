"""0.8.1: JPEG copy for sharing, JPEG quality, conversion, and the JPEG size limit."""
from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from micro_slide_stitcher import stitch
from micro_slide_stitcher.cli import main
from micro_slide_stitcher.stitch import convert_to_jpeg, read_panorama, save_jpeg, save_panorama


def _img(h=300, w=500):
    rng = np.random.default_rng(1)
    return cv2.resize(rng.integers(0, 255, (h // 10, w // 10, 3), dtype=np.uint8), (w, h),
                      interpolation=cv2.INTER_CUBIC)


def test_a_tiff_run_also_writes_a_jpeg_copy_by_default(synthetic_video, tmp_path):
    out = tmp_path / "scan.tif"
    diag = tmp_path / "diag"
    assert main(["--input", str(synthetic_video["video"]), "--output", str(out),
                 "--normalize", "none", "--blur-threshold", "5",
                 "--diagnostics", str(diag)]) == 0
    jpg = tmp_path / "scan.jpg"
    assert out.exists() and jpg.exists()
    a, b = read_panorama(out), cv2.imread(str(jpg))
    assert a.shape == b.shape
    assert jpg.stat().st_size < out.stat().st_size
    outputs = json.loads((diag / "summary.json").read_text())["outputs"]
    assert outputs["jpeg"] == str(jpg)


def test_the_jpeg_copy_can_be_switched_off(synthetic_video, tmp_path):
    out = tmp_path / "scan.tif"
    assert main(["--input", str(synthetic_video["video"]), "--output", str(out),
                 "--normalize", "none", "--blur-threshold", "5", "--jpeg-copy", "no",
                 "--diagnostics", str(tmp_path / "diag")]) == 0
    assert out.exists() and not (tmp_path / "scan.jpg").exists()


def test_quality_changes_the_file_size(tmp_path):
    img = _img()
    lo = save_jpeg(img, tmp_path / "lo.jpg", 50)
    hi = save_jpeg(img, tmp_path / "hi.jpg", 98)
    assert lo["bytes"] < hi["bytes"]


def test_jpeg_quality_is_validated():
    with pytest.raises(SystemExit):
        main(["--input", "x.mp4", "--output", "y.jpg", "--jpeg-quality", "0"])


def test_a_jpeg_output_writes_no_second_copy(tmp_path):
    w = save_panorama(_img(), tmp_path / "scan.jpg", jpeg_copy=True)
    assert "jpeg" not in w and (tmp_path / "scan.jpg").exists()
    assert list(tmp_path.glob("*.jpg")) == [tmp_path / "scan.jpg"]


def test_an_existing_tiff_can_be_converted_without_restitching(tmp_path):
    img = _img()
    save_panorama(img, tmp_path / "scan.tif")
    info = convert_to_jpeg(tmp_path / "scan.tif", tmp_path / "out.jpg", 90)
    back = cv2.imread(str(tmp_path / "out.jpg"))
    assert back.shape == img.shape and not info["reduced"]
    assert float(np.mean(np.abs(back.astype(int) - img.astype(int)))) < 6


def test_too_large_for_jpeg_is_reduced_not_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(stitch, "JPEG_MAX_SIDE", 200)
    img = _img(300, 500)
    w = save_panorama(img, tmp_path / "scan.tif", jpeg_copy=True)
    jpg = cv2.imread(str(tmp_path / "scan.jpg"))
    assert max(jpg.shape[:2]) == 200 and w["jpeg_reduced"] == "200x120"
    assert read_panorama(tmp_path / "scan.tif").shape == img.shape   # full size unaffected


@pytest.fixture
def window(qtbot, tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QSettings
    from micro_slide_stitcher.gui.mainwindow import MainWindow
    win = MainWindow(settings=QSettings(str(tmp_path / "gui.ini"), QSettings.IniFormat))
    qtbot.addWidget(win)
    win.show()
    return win


def test_gui_save_as_jpeg_converts_the_current_panorama(qtbot, window, synthetic_video,
                                                         tmp_path):
    out = tmp_path / "scan.tif"
    diag = tmp_path / "diag"
    assert main(["--input", str(synthetic_video["video"]), "--output", str(out),
                 "--normalize", "none", "--blur-threshold", "5", "--jpeg-copy", "no",
                 "--diagnostics", str(diag)]) == 0
    window.load_diagnostics(diag, quiet=True)
    target = tmp_path / "shared.jpg"
    window.save_as_jpeg(target)
    qtbot.waitUntil(lambda: target.exists() and window.btn_jpeg.isEnabled(), timeout=20000)
    assert cv2.imread(str(target)).shape == read_panorama(out).shape
