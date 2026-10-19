"""Settings panel: one control per exposed CLI flag, tooltips taken from the CLI help."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSpinBox,
                               QToolButton, QVBoxLayout, QWidget)

from ..cli_spec import BOOL, CHOICE, FLOAT, INT, OPT_INT, SPECS, FlagSpec

LABEL_WIDTH = 208
FIELD_WIDTH = 176


class _CaptionLabel(QLabel):
    """Word-wrapping caption that toggles its checkbox when clicked, as a box label does."""

    def __init__(self, text: str, box: QCheckBox, parent=None) -> None:
        super().__init__(text, parent)
        self._box = box
        self.setWordWrap(True)
        self.setBuddy(box)
        self.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Minimum)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if (event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint())
                and self.isEnabled() and self._box.isEnabled()):
            self._box.toggle()
        super().mouseReleaseEvent(event)


class _CheckRow(QWidget):
    """A checkbox whose caption wraps instead of being clipped at the panel edge.

    ``QCheckBox`` never wraps its own text, so a long "label (--flag)" caption is cut
    off mid-flag on a narrow panel. The box therefore carries no text of its own and the
    caption lives in a wrapping label beside it; the label stays clickable so the row
    still behaves like an ordinary checkbox.
    """

    def __init__(self, box: QCheckBox, caption: str, parent=None) -> None:
        super().__init__(parent)
        box.setMinimumWidth(0)
        self.label = _CaptionLabel(caption, box, self)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(box, 0, Qt.AlignTop)
        row.addWidget(self.label, 1)


def _form() -> QFormLayout:
    form = QFormLayout()
    form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
    form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.DontWrapRows)
    form.setHorizontalSpacing(14)
    form.setVerticalSpacing(9)
    form.setContentsMargins(0, 4, 0, 4)
    return form


class SettingsPanel(QWidget):
    """Builds itself from ``cli_spec.SPECS``; emits `changed` on every edit."""

    changed = Signal()
    _VIDEO_ONLY = {"--sampling-fps"}
    _IMAGE_ONLY = {"--image-order", "--image-stride"}

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._widgets: dict[str, QWidget] = {}
        self._labels: dict[str, QLabel] = {}
        self._context_mode: str | None = None
        self._context_disabled: set[str] = set()
        self._run_enabled = True

        basic = _form()
        advanced = _form()
        for spec in SPECS:
            widget = self._make_widget(spec)
            self._widgets[spec.flag] = widget
            label = QLabel(f"{spec.label}")
            label.setToolTip(spec.tooltip)
            label.setWordWrap(True)
            label.setMinimumWidth(LABEL_WIDTH)
            label.setBuddy(widget)
            self._labels[spec.flag] = label
            target = advanced if spec.advanced else basic
            if spec.kind == BOOL:
                # Span both columns and let the caption wrap: these captions carry the flag
                # name too, and neither the words nor the flag may be clipped.
                row_widget = _CheckRow(widget, f"{spec.label}   ({spec.flag})", self)
                label = row_widget.label
                label.setToolTip(spec.tooltip)
                label.setMinimumWidth(LABEL_WIDTH)
                self._labels[spec.flag] = label
                target.addRow(row_widget)
            elif spec.kind == CHOICE and spec.choice_labels:
                # Human-readable choices are short sentences rather than single words, so
                # this row also spans both columns, with the caption above the box.
                holder = QWidget(self)
                box = QVBoxLayout(holder)
                box.setContentsMargins(0, 0, 0, 0)
                box.setSpacing(3)
                box.addWidget(label)
                box.addWidget(widget)
                widget.setMinimumWidth(LABEL_WIDTH + FIELD_WIDTH)
                target.addRow(holder)
            else:
                target.addRow(label, widget)

        self._adv_container = QWidget(self)
        adv_layout = QVBoxLayout(self._adv_container)
        adv_layout.setContentsMargins(4, 2, 0, 0)
        adv_layout.addLayout(advanced)
        self._adv_container.setVisible(False)

        self._adv_button = QToolButton(self)
        self._adv_button.setText(f"Advanced settings  ({sum(1 for s in SPECS if s.advanced)})")
        self._adv_button.setCheckable(True)
        self._adv_button.setChecked(False)
        self._adv_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._adv_button.setArrowType(Qt.RightArrow)
        self._adv_button.setAutoRaise(True)
        self._adv_button.setFocusPolicy(Qt.StrongFocus)
        self._adv_button.setToolTip("Show the less frequently used CLI flags "
                                    "(blending, de-duplication, registration and rejoin limits)")
        self._adv_button.toggled.connect(self._toggle_advanced)

        self._reset = QPushButton("Reset all settings to the CLI defaults", self)
        self._reset.setToolTip("Put every control back to the default value of "
                               "python -m micro_slide_stitcher")
        self._reset.clicked.connect(self.reset_to_defaults)

        line = QFrame(self)
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        outer.addLayout(basic)
        outer.addWidget(line)
        row = QHBoxLayout()
        row.addWidget(self._adv_button)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addWidget(self._adv_container)
        outer.addWidget(self._reset)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

    # ------------------------------------------------------------------- building
    def _make_widget(self, spec: FlagSpec) -> QWidget:
        if spec.kind == FLOAT:
            w = QDoubleSpinBox(self)
            w.setDecimals(spec.decimals)
            w.setRange(spec.minimum, spec.maximum)
            w.setSingleStep(spec.step)
            w.setValue(float(spec.default))
            w.setSuffix(spec.suffix)
            w.setMinimumWidth(FIELD_WIDTH)
            w.valueChanged.connect(lambda *_: self.changed.emit())
        elif spec.kind == INT:
            w = QSpinBox(self)
            w.setRange(int(spec.minimum), int(spec.maximum))
            w.setSingleStep(int(spec.step))
            w.setValue(int(spec.default))
            w.setSuffix(spec.suffix)
            w.setMinimumWidth(FIELD_WIDTH)
            w.valueChanged.connect(lambda *_: self.changed.emit())
        elif spec.kind == OPT_INT:
            w = QSpinBox(self)
            w.setRange(0, int(spec.maximum))
            w.setSingleStep(int(spec.step))
            w.setSpecialValueText(spec.unset_text)
            w.setValue(int(spec.default or 0))
            w.setSuffix(spec.suffix)
            w.setMinimumWidth(FIELD_WIDTH)
            w.valueChanged.connect(lambda *_: self.changed.emit())
        elif spec.kind == CHOICE:
            w = QComboBox(self)
            # The *label* is what a pathologist reads; the *data* is the exact value the
            # command line receives, so a friendlier wording can never change the command.
            for choice in spec.choices:
                w.addItem(spec.label_for_choice(choice), choice)
            w.setCurrentIndex(max(w.findData(str(spec.default)), 0))
            w.setMinimumWidth(FIELD_WIDTH)
            w.currentIndexChanged.connect(lambda *_: self.changed.emit())
        elif spec.kind == BOOL:
            # The caption is supplied by the wrapping _CheckRow, not by the box itself.
            w = QCheckBox(self)
            w.setChecked(bool(spec.default))
            w.toggled.connect(lambda *_: self.changed.emit())
        else:  # pragma: no cover - defensive
            raise ValueError(spec.kind)
        w.setToolTip(spec.tooltip)
        w.setAccessibleName(f"{spec.label} ({spec.flag})")
        return w

    def _toggle_advanced(self, on: bool) -> None:
        self._adv_container.setVisible(on)
        self._adv_button.setArrowType(Qt.DownArrow if on else Qt.RightArrow)

    # --------------------------------------------------------------------- values
    def values(self) -> dict[str, Any]:
        """flag -> value, with the OPT_INT "off" state mapped to None."""
        out: dict[str, Any] = {}
        for spec in SPECS:
            w = self._widgets[spec.flag]
            if spec.kind == BOOL:
                out[spec.flag] = bool(w.isChecked())
            elif spec.kind == CHOICE:
                out[spec.flag] = w.currentData()
            elif spec.kind == OPT_INT:
                out[spec.flag] = None if w.value() == 0 else int(w.value())
            elif spec.kind == INT:
                out[spec.flag] = int(w.value())
            else:
                out[spec.flag] = float(w.value())
        return out

    def set_values(self, values: dict[str, Any], emit: bool = True) -> None:
        for spec in SPECS:
            if spec.flag not in values:
                continue
            value = values[spec.flag]
            w = self._widgets[spec.flag]
            w.blockSignals(True)
            try:
                if spec.kind == BOOL:
                    w.setChecked(bool(value))
                elif spec.kind == CHOICE:
                    row = w.findData(str(value))
                    if row >= 0:
                        w.setCurrentIndex(row)
                elif spec.kind == OPT_INT:
                    w.setValue(0 if value in (None, 0, "", "None") else int(value))
                elif spec.kind == INT:
                    w.setValue(int(value))
                else:
                    w.setValue(float(value))
            except (TypeError, ValueError):
                pass
            finally:
                w.blockSignals(False)
        if emit:
            self.changed.emit()

    def reset_to_defaults(self) -> None:
        self.set_values({s.flag: s.default for s in SPECS})

    def widget_for(self, flag: str) -> QWidget:
        return self._widgets[flag]

    def set_input_mode(self, mode: str | None) -> None:
        """Enable only the controls meaningful for a resolved source mode.

        ``None`` means that no source has been loaded yet, so all controls remain
        available while the input-mode choice is still genuinely ambiguous.
        """
        if mode not in (None, "video", "images"):
            raise ValueError(f"unknown input mode: {mode}")
        self._context_mode = mode
        if mode == "images":
            self._context_disabled = set(self._VIDEO_ONLY)
        elif mode == "video":
            self._context_disabled = set(self._IMAGE_ONLY)
        else:
            self._context_disabled = set()
        self._apply_enabled_state()

    def suppressed_flags(self) -> set[str]:
        """Flags that must not reach the command line for the resolved source."""
        return set(self._context_disabled)

    def _apply_enabled_state(self) -> None:
        for spec in SPECS:
            disabled_for_context = spec.flag in self._context_disabled
            enabled = self._run_enabled and not disabled_for_context
            widget = self._widgets[spec.flag]
            widget.setEnabled(enabled)
            label = self._labels.get(spec.flag)
            if label is not None:
                label.setEnabled(enabled)
            if disabled_for_context:
                tooltip = self._context_tooltip(spec.flag)
            else:
                tooltip = spec.tooltip
            widget.setToolTip(tooltip)
            if label is not None:
                label.setToolTip(tooltip)
        self._reset.setEnabled(self._run_enabled)

    @staticmethod
    def _context_tooltip(flag: str) -> str:
        if flag == "--sampling-fps":
            return ("--sampling-fps\n\nApplies to video input only and is ignored for "
                    "image sequences.")
        return (f"{flag}\n\nApplies to image-sequence input only and has no effect for "
                "video input.")

    def set_advanced_expanded(self, on: bool) -> None:
        self._adv_button.setChecked(bool(on))

    def advanced_expanded(self) -> bool:
        return self._adv_button.isChecked()

    def set_enabled_for_run(self, enabled: bool) -> None:
        self._run_enabled = enabled
        self._apply_enabled_state()
