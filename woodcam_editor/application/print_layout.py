"""Pure paper and scale planning for Editor 2D TechDraw pages.

The module deliberately knows nothing about Qt, FreeCAD or TechDraw.  It only
answers which ISO page/orientation can contain a physical sheet and which
scale must be used.  Presentation and host adapters consume the immutable
result without changing ``VectorDocument``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence, Tuple


Bounds = Tuple[float, float, float, float]


@dataclass(frozen=True)
class PaperSize:
    name: str
    width_mm: float
    height_mm: float
    template_name: str


# FreeCAD ships blank landscape TechDraw templates for the complete ISO A
# series.  Rotating the drawing on a landscape page is physically equivalent
# to using the same paper in portrait orientation and avoids private template
# files that could disappear after the FCStd is moved to another computer.
ISO_PAPERS: Tuple[PaperSize, ...] = (
    PaperSize("A4", 297.0, 210.0, "A4_Landscape_blank.svg"),
    PaperSize("A3", 420.0, 297.0, "A3_Landscape_blank.svg"),
    PaperSize("A2", 594.0, 420.0, "A2_Landscape_blank.svg"),
    PaperSize("A1", 841.0, 594.0, "A1_Landscape_blank.svg"),
    PaperSize("A0", 1189.0, 841.0, "A0_Landscape_blank.svg"),
)


@dataclass(frozen=True)
class PrintLayout:
    bounds: Bounds
    paper: PaperSize
    scale: float
    rotation_degrees: int
    fit_scale: float
    fits: bool
    margin_mm: float

    @property
    def sheet_width_mm(self) -> float:
        return self.bounds[2] - self.bounds[0]

    @property
    def sheet_height_mm(self) -> float:
        return self.bounds[3] - self.bounds[1]

    @property
    def scale_percent(self) -> float:
        return self.scale * 100.0


def normalize_bounds(value: Sequence[float]) -> Bounds:
    if len(value) != 4:
        raise ValueError("Os limites da chapa precisam conter quatro valores.")
    min_x, min_y, max_x, max_y = map(float, value)
    if max_x <= min_x or max_y <= min_y:
        raise ValueError("A chapa precisa ter largura e altura positivas.")
    return min_x, min_y, max_x, max_y


def paper_by_name(name: str) -> PaperSize:
    normalized = str(name or "").strip().upper()
    for paper in ISO_PAPERS:
        if paper.name == normalized:
            return paper
    raise ValueError("Papel ISO desconhecido: %s." % name)


def _fit_for_orientation(
    width_mm: float,
    height_mm: float,
    paper: PaperSize,
    margin_mm: float,
) -> tuple[float, int]:
    usable_width = paper.width_mm - 2.0 * margin_mm
    usable_height = paper.height_mm - 2.0 * margin_mm
    if usable_width <= 0.0 or usable_height <= 0.0:
        raise ValueError("A margem informada não deixa área útil no papel.")
    normal = min(usable_width / width_mm, usable_height / height_mm)
    rotated = min(usable_width / height_mm, usable_height / width_mm)
    if rotated > normal + 1.0e-12:
        return rotated, 90
    return normal, 0


def resolve_print_layout(
    bounds: Sequence[float],
    paper: str | PaperSize,
    *,
    scale_mode: str = "fit",
    custom_scale_percent: float = 100.0,
    margin_mm: float = 10.0,
) -> PrintLayout:
    """Resolve one sheet on one page.

    ``fit`` keeps the entire sheet on one page and never enlarges above 1:1.
    ``actual`` requests physical 1:1.  ``custom`` uses the explicit percent.
    ``fits`` reports clipping without silently changing an explicit choice.
    """

    normalized_bounds = normalize_bounds(bounds)
    resolved_paper = paper if isinstance(paper, PaperSize) else paper_by_name(paper)
    margin = float(margin_mm)
    if margin < 0.0:
        raise ValueError("A margem de impressão não pode ser negativa.")
    width = normalized_bounds[2] - normalized_bounds[0]
    height = normalized_bounds[3] - normalized_bounds[1]
    fit_scale, rotation = _fit_for_orientation(
        width,
        height,
        resolved_paper,
        margin,
    )
    mode = str(scale_mode or "fit").strip().lower()
    if mode == "fit":
        scale = min(1.0, fit_scale)
    elif mode == "actual":
        scale = 1.0
    elif mode == "custom":
        scale = float(custom_scale_percent) / 100.0
        if scale <= 0.0:
            raise ValueError("A escala personalizada precisa ser positiva.")
    else:
        raise ValueError("Modo de escala desconhecido: %s." % scale_mode)
    return PrintLayout(
        normalized_bounds,
        resolved_paper,
        scale,
        rotation,
        fit_scale,
        scale <= fit_scale + 1.0e-12,
        margin,
    )


def recommend_paper(
    sheet_bounds: Iterable[Sequence[float]],
    *,
    margin_mm: float = 10.0,
) -> PaperSize:
    """Return the smallest standard paper that fits every sheet at 1:1.

    If no ISO A page fits, A0 is returned because it provides the largest
    legible one-page scale.  The caller can explain that reduction is needed.
    """

    values = tuple(normalize_bounds(value) for value in sheet_bounds)
    if not values:
        raise ValueError("Nenhuma chapa foi informada para impressão.")
    for paper in ISO_PAPERS:
        if all(
            resolve_print_layout(
                bounds,
                paper,
                scale_mode="actual",
                margin_mm=margin_mm,
            ).fits
            for bounds in values
        ):
            return paper
    return ISO_PAPERS[-1]


def resolve_print_layouts(
    sheet_bounds: Iterable[Sequence[float]],
    paper: str | PaperSize,
    *,
    scale_mode: str = "fit",
    custom_scale_percent: float = 100.0,
    margin_mm: float = 10.0,
) -> Tuple[PrintLayout, ...]:
    return tuple(
        resolve_print_layout(
            bounds,
            paper,
            scale_mode=scale_mode,
            custom_scale_percent=custom_scale_percent,
            margin_mm=margin_mm,
        )
        for bounds in sheet_bounds
    )
