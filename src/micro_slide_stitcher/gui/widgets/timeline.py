"""Frame-quality timeline: focus score against video time or image index.

The data comes from ``<diagnostics>/frame_metrics.csv`` exactly as the pipeline wrote it.
Dragging the threshold line emits `thresholdChanged`, which the main window feeds back into
the --blur-threshold setting (and therefore into the command-line preview).
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

ACCEPT_COLOR = QColor("#1f8b4c")
REJECT_COLOR = QColor("#c0392b")
LINE_COLOR = QColor("#8f9ba8")
THRESHOLD_COLOR = QColor("#0b63c5")
GRID_COLOR = QColor("#dfe3e8")
TEXT_COLOR = QColor("#1d2026")
MUTED_COLOR = QColor("#5a6472")

MARGIN_LEFT = 78
MARGIN_RIGHT = 18
MARGIN_TOP = 34
MARGIN_BOTTOM = 52
GRAB_PX = 7


class FrameQualityTimeline(QWidget):
    """Scatter plot of focus score vs. time with an interactive horizontal threshold."""

    thresholdChanged = Signal(float)          # live, while dragging
    thresholdCommitted = Signal(float)        # on release / keyboard change

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._points: list = []               # list[results.FramePoint]
        self._threshold = 10.0
        self._x_label = "timestamp in the source video (s)"
        self._x_suffix = " s"
        self._dragging = False
        self._ymax = 1.0
        self._tmax = 1.0
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAutoFillBackground(True)
        self.setAccessibleName("Frame quality timeline")

    # ------------------------------------------------------------------- data API
    def set_points(self, points: list, *, image_indices: bool = False) -> None:
        self._points = list(points)
        self._x_label = ("image index in the ordered source sequence" if image_indices else
                         "timestamp in the source video (s)")
        self._x_suffix = "" if image_indices else " s"
        self._ymax = max([p.focus_score for p in self._points] + [self._threshold, 1.0]) * 1.12
        self._tmax = max([p.timestamp_s for p in self._points] + [1.0])
        self.update()

    def clear_points(self) -> None:
        self._points = []
        self.update()

    @property
    def has_data(self) -> bool:
        return bool(self._points)

    def threshold(self) -> float:
        return self._threshold

    def set_threshold(self, value: float, emit: bool = False) -> None:
        value = max(0.0, float(value))
        if abs(value - self._threshold) < 1e-9:
            return
        self._threshold = value
        self._ymax = max([p.focus_score for p in self._points] + [value, 1.0]) * 1.12
        if emit:
            self.thresholdChanged.emit(self._threshold)
        self.update()

    def counts(self) -> tuple[int, int]:
        """(would be accepted, would be rejected) at the current threshold line."""
        keep = sum(1 for p in self._points if p.focus_score >= self._threshold)
        return keep, len(self._points) - keep

    # ------------------------------------------------------------------ geometry
    def _plot_rect(self) -> QRectF:
        return QRectF(MARGIN_LEFT, MARGIN_TOP,
                      max(self.width() - MARGIN_LEFT - MARGIN_RIGHT, 10),
                      max(self.height() - MARGIN_TOP - MARGIN_BOTTOM, 10))

    def _x_of(self, t: float) -> float:
        r = self._plot_rect()
        return r.left() + r.width() * (t / self._tmax if self._tmax else 0.0)

    def _y_of(self, score: float) -> float:
        r = self._plot_rect()
        return r.bottom() - r.height() * min(score / self._ymax, 1.0)

    def _score_of_y(self, y: float) -> float:
        r = self._plot_rect()
        frac = (r.bottom() - y) / r.height()
        return max(0.0, min(1.0, frac)) * self._ymax

    # --------------------------------------------------------------- interaction
    def mousePressEvent(self, event) -> None:  # noqa: D102
        if not self._points or event.button() != Qt.LeftButton:
            return
        if abs(event.position().y() - self._y_of(self._threshold)) <= GRAB_PX * 2:
            self._dragging = True
            self.setCursor(Qt.SizeVerCursor)
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: D102
        if self._dragging:
            self.set_threshold(round(self._score_of_y(event.position().y()), 2), emit=True)
            event.accept()
            return
        near = (self._points
                and abs(event.position().y() - self._y_of(self._threshold)) <= GRAB_PX * 2)
        self.setCursor(Qt.SizeVerCursor if near else Qt.ArrowCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: D102
        if self._dragging:
            self._dragging = False
            self.setCursor(Qt.ArrowCursor)
            self.thresholdCommitted.emit(self._threshold)
            event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: D102
        step = 1.0 if event.modifiers() & Qt.ShiftModifier else 0.1
        if event.key() in (Qt.Key_Up, Qt.Key_Right):
            self.set_threshold(self._threshold + step, emit=True)
            self.thresholdCommitted.emit(self._threshold)
        elif event.key() in (Qt.Key_Down, Qt.Key_Left):
            self.set_threshold(self._threshold - step, emit=True)
            self.thresholdCommitted.emit(self._threshold)
        else:
            super().keyPressEvent(event)

    def drag_threshold_to(self, score: float) -> None:
        """Programmatic equivalent of a drag (used by the tests)."""
        self.set_threshold(score, emit=True)
        self.thresholdCommitted.emit(self._threshold)

    # ----------------------------------------------------------------- rendering
    def paintEvent(self, event) -> None:  # noqa: D102
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#ffffff"))
        r = self._plot_rect()

        font = QFont(self.font())
        font.setPointSizeF(max(font.pointSizeF(), 9.5))
        p.setFont(font)
        fm = QFontMetrics(font)

        if not self._points:
            p.setPen(MUTED_COLOR)
            p.drawText(self.rect().adjusted(24, 0, -24, 0), Qt.AlignCenter | Qt.TextWordWrap,
                       "No frame metrics yet.\n\nRun a preview or a full-resolution stitch, or "
                       "load an earlier diagnostics directory, and the focus score of every "
                       "sampled frame appears here.")
            return

        # axes and grid
        p.setPen(QPen(GRID_COLOR, 1))
        for i in range(5):
            y = r.bottom() - r.height() * i / 4
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        p.setPen(QPen(LINE_COLOR, 1.2))
        p.drawLine(QPointF(r.left(), r.top()), QPointF(r.left(), r.bottom()))
        p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.bottom()))

        # y ticks
        p.setPen(TEXT_COLOR)
        for i in range(5):
            value = self._ymax * i / 4
            y = r.bottom() - r.height() * i / 4
            text = f"{value:.0f}" if self._ymax >= 20 else f"{value:.1f}"
            p.drawText(QRectF(4, y - 9, MARGIN_LEFT - 12, 18),
                       Qt.AlignRight | Qt.AlignVCenter, text)
        # x ticks
        n_ticks = max(2, min(9, int(r.width() // 90)))
        for i in range(n_ticks + 1):
            t = self._tmax * i / n_ticks
            x = self._x_of(t)
            p.drawText(QRectF(x - 40, r.bottom() + 6, 80, 18),
                       Qt.AlignCenter, f"{t:.0f}" if not self._x_suffix else f"{t:.1f} s")

        # axis labels
        p.setPen(MUTED_COLOR)
        p.drawText(QRectF(r.left(), r.bottom() + 26, r.width(), 20),
                   Qt.AlignCenter, self._x_label)
        p.save()
        p.translate(16, r.center().y())
        p.rotate(-90)
        p.drawText(QRectF(-r.height() / 2, -14, r.height(), 16),
                   Qt.AlignCenter, "focus score (variance of Laplacian)")
        p.restore()

        # connecting line
        p.setPen(QPen(QColor("#c3ccd6"), 1.2))
        prev = None
        for pt in self._points:
            cur = QPointF(self._x_of(pt.timestamp_s), self._y_of(pt.focus_score))
            if prev is not None:
                p.drawLine(prev, cur)
            prev = cur

        # points, coloured by the *pipeline's* accept decision, ringed when the current
        # threshold line would decide differently
        for pt in self._points:
            c = ACCEPT_COLOR if pt.accepted else REJECT_COLOR
            would_keep = pt.focus_score >= self._threshold
            center = QPointF(self._x_of(pt.timestamp_s), self._y_of(pt.focus_score))
            p.setPen(QPen(c.darker(140), 1))
            p.setBrush(c if would_keep else QColor("#ffffff"))
            p.drawEllipse(center, 4.2, 4.2)

        # threshold line + handle
        y = self._y_of(self._threshold)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(THRESHOLD_COLOR, 2.0, Qt.DashLine))
        p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        keep, drop = self.counts()
        label = f"--blur-threshold {self._threshold:g}  →  {keep} kept / {drop} rejected"
        w = fm.horizontalAdvance(label) + 16
        box = QRectF(r.left() + 8, max(r.top() + 2, y - 24), min(w, r.width() - 16), 20)
        p.setBrush(QColor(255, 255, 255, 235))
        p.setPen(QPen(THRESHOLD_COLOR, 1))
        p.drawRect(box)
        p.setPen(THRESHOLD_COLOR)
        p.drawText(box, Qt.AlignCenter, label)

        # legend + hint
        p.setPen(TEXT_COLOR)
        legend_y = 8
        items = [(ACCEPT_COLOR, "accepted by the run"), (REJECT_COLOR, "rejected by the run")]
        x = r.left()
        for color, text in items:
            p.setBrush(color)
            p.setPen(QPen(color.darker(140), 1))
            p.drawEllipse(QPointF(x + 6, legend_y + 9), 4.2, 4.2)
            p.setPen(TEXT_COLOR)
            p.drawText(QRectF(x + 16, legend_y, fm.horizontalAdvance(text) + 8, 18),
                       Qt.AlignVCenter | Qt.AlignLeft, text)
            x += 24 + fm.horizontalAdvance(text)
        hint = "drag the blue line (or use ↑/↓ when focused) to set --blur-threshold"
        p.setPen(MUTED_COLOR)
        p.drawText(QRectF(x + 12, legend_y, max(r.right() - x - 12, 40), 18),
                   Qt.AlignVCenter | Qt.AlignRight, hint)
        p.end()

    def event(self, ev):  # noqa: D102 - tooltips for individual frames
        if ev.type() == ev.Type.ToolTip and self._points:
            pos = ev.pos()
            best = min(self._points,
                       key=lambda pt: (self._x_of(pt.timestamp_s) - pos.x()) ** 2
                       + (self._y_of(pt.focus_score) - pos.y()) ** 2)
            if abs(self._x_of(best.timestamp_s) - pos.x()) < 14:
                QToolTip.showText(ev.globalPos(),
                                  f"frame {best.index} at {best.timestamp_s:.0f}"
                                  f"{self._x_suffix}\n"
                                  f"focus score {best.focus_score:g}\n"
                                  f"{'accepted' if best.accepted else best.reason}", self)
                return True
        return super().event(ev)
