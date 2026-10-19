"""Pannable / zoomable panorama viewer (QGraphicsView, no QtWebEngine, no OpenGL)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QImage, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import (QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView,
                               QLabel, QSizePolicy, QVBoxLayout, QWidget)

MIN_SCALE = 0.02
MAX_SCALE = 24.0


def numpy_bgr_to_qimage(bgr: np.ndarray) -> QImage:
    """OpenCV BGR uint8 array -> QImage (deep copy, so the buffer can be freed)."""
    if bgr.ndim == 2:
        bgr = np.dstack([bgr] * 3)
    rgb = np.ascontiguousarray(bgr[:, :, ::-1])
    h, w = rgb.shape[:2]
    img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
    return img.copy()


class ImageLoader(QThread):
    """Loads a possibly huge panorama (TIFF included) off the GUI thread."""

    loaded = Signal(QImage, str)   # (image, path)
    failed = Signal(str, str)      # (path, message)

    def __init__(self, path: str | Path, parent=None) -> None:
        super().__init__(parent)
        self.path = str(path)
        self._abort = False

    def abort(self) -> None:
        self._abort = True

    def run(self) -> None:  # noqa: D102
        try:
            import cv2
            data = cv2.imread(self.path, cv2.IMREAD_COLOR)
            if self._abort:
                return
            if data is None:
                self.failed.emit(self.path, "the image could not be decoded")
                return
            self.loaded.emit(numpy_bgr_to_qimage(data), self.path)
        except Exception as exc:  # pragma: no cover - defensive
            if not self._abort:
                self.failed.emit(self.path, str(exc))


class PanZoomView(QGraphicsView):
    """Mouse-wheel zoom, drag to pan, fit-to-window and 1:1, keyboard accessible."""

    scaleChanged = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.SmoothTransformation)
        self._scene.addItem(self._item)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.SmoothPixmapTransform | QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setFrameShape(QFrame.StyledPanel)
        self.setBackgroundBrush(Qt.darkGray)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._scale = 1.0
        self._has_image = False
        self._fit_pending = False
        # While True the view re-fits whenever it is resized or shown, so an image loaded
        # into a hidden tab is not left at a nonsense zoom once the tab appears.
        self._fit_mode = True

    # ------------------------------------------------------------------- image API
    def set_image(self, image: QImage) -> None:
        pix = QPixmap.fromImage(image)
        self._item.setPixmap(pix)
        self._scene.setSceneRect(QRectF(pix.rect()))
        self._has_image = not pix.isNull()
        self._fit_mode = True
        self.fit_to_window()

    def clear_image(self) -> None:
        self._item.setPixmap(QPixmap())
        self._has_image = False

    @property
    def has_image(self) -> bool:
        return self._has_image

    # ----------------------------------------------------------------- zoom / pan
    def _apply_scale(self, scale: float) -> None:
        scale = max(MIN_SCALE, min(MAX_SCALE, scale))
        self.setTransform(QTransform.fromScale(scale, scale))
        self._scale = scale
        self.scaleChanged.emit(scale)

    def fit_to_window(self) -> None:
        self._fit_mode = True
        if not self._has_image:
            return
        rect = self._scene.sceneRect()
        if rect.isEmpty():
            return
        vw = max(self.viewport().width() - 2, 1)
        vh = max(self.viewport().height() - 2, 1)
        self._apply_scale(min(vw / rect.width(), vh / rect.height()))
        self.centerOn(rect.center())

    def zoom_one_to_one(self) -> None:
        self._fit_mode = False
        self._apply_scale(1.0)

    def zoom_by(self, factor: float) -> None:
        self._fit_mode = False
        self._apply_scale(self._scale * factor)

    def current_scale(self) -> float:
        return self._scale

    def wheelEvent(self, event) -> None:  # noqa: D102
        if not self._has_image:
            return
        delta = event.angleDelta().y()
        if delta:
            self.zoom_by(1.15 if delta > 0 else 1 / 1.15)
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: D102
        key = event.key()
        if key in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_by(1.15)
        elif key == Qt.Key_Minus:
            self.zoom_by(1 / 1.15)
        elif key == Qt.Key_F:
            self.fit_to_window()
        elif key == Qt.Key_1:
            self.zoom_one_to_one()
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: D102
        super().resizeEvent(event)
        if self._fit_pending:
            self._fit_pending = False
        if self._fit_mode:
            self.fit_to_window()

    def showEvent(self, event) -> None:  # noqa: D102
        super().showEvent(event)
        if self._fit_mode:
            QTimer.singleShot(0, self.fit_to_window)


class PanoramaPanel(QWidget):
    """A PanZoomView plus a placeholder message when nothing is loaded."""

    def __init__(self, placeholder: str, parent=None) -> None:
        super().__init__(parent)
        self.view = PanZoomView(self)
        self.placeholder = QLabel(placeholder, self)
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.placeholder.setObjectName("placeholder")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.placeholder)
        lay.addWidget(self.view)
        self.view.hide()

    def show_image(self, image: QImage) -> None:
        self.placeholder.hide()
        self.view.show()
        self.view.set_image(image)

    def show_placeholder(self, text: str | None = None) -> None:
        if text:
            self.placeholder.setText(text)
        self.view.hide()
        self.view.clear_image()
        self.placeholder.show()
