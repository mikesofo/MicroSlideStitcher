"""Declarative description of the CLI flags the GUI exposes.

The GUI is a *front end* for the command line interface: it never re-implements a
default or a help text.  Both are pulled out of ``cli.build_parser()`` at import
time, so a change to the CLI is picked up automatically (and a removed/renamed
flag fails loudly here instead of silently producing a wrong command line).

Only presentation metadata (label, widget kind, ranges, basic/advanced grouping)
lives in this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..cli import build_parser

# widget kinds
FLOAT = "float"
INT = "int"
OPT_INT = "opt_int"   # spin box with a "not set" (None) state
CHOICE = "choice"
BOOL = "bool"


@dataclass
class FlagSpec:
    """One CLI flag as the GUI presents it."""
    flag: str                     # e.g. "--sampling-fps"
    label: str                    # e.g. "Sampling rate (fps)"
    kind: str                     # one of the constants above
    advanced: bool = False
    minimum: float = 0.0
    maximum: float = 0.0
    step: float = 1.0
    decimals: int = 2
    unset_text: str = "off"       # OPT_INT only
    suffix: str = ""
    # filled in from the parser:
    dest: str = ""
    default: Any = None
    help: str = ""
    choices: list[str] = field(default_factory=list)
    #: CHOICE only: human-readable label per choice value, for people who did not
    #: write the CLI.  The *value* sent to the command line is always the CLI's own.
    choice_labels: dict[str, str] = field(default_factory=dict)

    def label_for_choice(self, value: str) -> str:
        """What the combo box shows for one choice value."""
        return self.choice_labels.get(value, value)

    @property
    def tooltip(self) -> str:
        """Tooltip text: the CLI's own help string plus the flag itself."""
        return f"{self.flag}\n\n{self.help}"


#: The flags the GUI exposes, in the order they appear on the generated command line.
SPECS: list[FlagSpec] = [
    FlagSpec("--input-mode", "Input mode", CHOICE),
    FlagSpec("--scan-mode", "Scan type", CHOICE, choice_labels={
        "sweep": "Single sweep \u2014 one continuous pan",
        "serpentine": "Serpentine \u2014 back and forth in rows "
                      "(slower, corrects drift)"}),
    FlagSpec("--frame-selection", "Frames taken from video", CHOICE, choice_labels={
        "motion": "Sharpest per movement - skips blurred frames",
        "fixed": "Fixed rate (sampling rate below)"}),
    FlagSpec("--sampling-fps", "Sampling rate", FLOAT,
             minimum=0.1, maximum=30.0, step=0.5, decimals=2, suffix=" fps"),
    FlagSpec("--image-order", "Image ordering", CHOICE),
    FlagSpec("--image-stride", "Image stride", INT,
             minimum=1, maximum=1000, step=1),
    FlagSpec("--detector", "Feature detector", CHOICE),
    FlagSpec("--normalize", "Exposure handling", CHOICE),
    FlagSpec("--blur-threshold", "Blur threshold (focus score)", FLOAT,
             minimum=0.0, maximum=5000.0, step=1.0, decimals=1),
    FlagSpec("--max-dimension", "Downscale long edge to", OPT_INT,
             minimum=240, maximum=8000, step=160, unset_text="off (full resolution)",
             suffix=" px"),
    FlagSpec("--jpeg-copy", "JPEG copy for sharing", CHOICE, choice_labels={
        "yes": "Also save a JPEG copy (.jpg)", "no": "Do not save a JPEG copy"}),
    FlagSpec("--blend", "Blending mode", CHOICE, advanced=True),
    FlagSpec("--jpeg-quality", "JPEG quality", INT, advanced=True, minimum=1, maximum=100,
             step=5),
    FlagSpec("--dedup-threshold", "Near-duplicate threshold", FLOAT, advanced=True,
             minimum=0.0, maximum=1.0, step=0.001, decimals=3),
    FlagSpec("--min-overlap", "Minimum overlap, horizontal AND vertical", FLOAT,
             advanced=True, minimum=0.0, maximum=0.9, step=0.01, decimals=2),
    FlagSpec("--min-inliers", "Minimum RANSAC inliers", INT, advanced=True,
             minimum=4, maximum=1000, step=5),
    FlagSpec("--min-segment-frames", "Minimum frames per written segment", INT,
             advanced=True, minimum=2, maximum=100000, step=1),
    FlagSpec("--rejoin-window", "Rejoin window K", INT, advanced=True,
             minimum=1, maximum=15, step=1),
    FlagSpec("--no-rejoin", "Disable wide-baseline segment rejoin", BOOL, advanced=True),
    FlagSpec("--bundle-adjust", "Refine all frame positions together (bundle adjustment)",
             BOOL, advanced=True),
    FlagSpec("--ba-max-iterations", "Bundle adjustment iteration cap", INT, advanced=True,
             minimum=1, maximum=5000, step=10),
    FlagSpec("--keep-intermediates", "Keep extracted candidate frames", BOOL,
             advanced=True),
]


def _fill_from_parser() -> None:
    parser = build_parser()
    actions = {}
    for action in parser._actions:                      # noqa: SLF001 - argparse has no public API
        for opt in action.option_strings:
            actions[opt] = action
    for spec in SPECS:
        action = actions.get(spec.flag)
        if action is None:  # pragma: no cover - guards against a CLI rename
            raise RuntimeError(
                f"the GUI exposes {spec.flag} but the CLI parser no longer defines it; "
                "update micro_slide_stitcher.gui.cli_spec")
        spec.dest = action.dest
        spec.default = action.default
        raw = " ".join((action.help or "").split())
        try:
            spec.help = raw % {"default": action.default}
        except (KeyError, ValueError, TypeError):  # pragma: no cover - defensive
            spec.help = raw
        spec.choices = [str(c) for c in (action.choices or [])]
        if spec.kind == CHOICE and not spec.choices:  # pragma: no cover - defensive
            raise RuntimeError(f"{spec.flag} is presented as a choice but has no choices")


_fill_from_parser()

SPEC_BY_FLAG: dict[str, FlagSpec] = {s.flag: s for s in SPECS}


def defaults() -> dict[str, Any]:
    """The CLI's own defaults for every exposed flag, keyed by flag string."""
    return {s.flag: s.default for s in SPECS}


def help_for(flag: str) -> str:
    """Raw CLI help text of one flag (used for tooltips)."""
    return SPEC_BY_FLAG[flag].help
