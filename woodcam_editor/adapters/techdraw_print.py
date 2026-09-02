"""Non-destructive Editor 2D -> TechDraw printing bridge.

Each page contains a ``TechDraw::DrawViewSymbol`` with a vector SVG snapshot.
No ``Part::Feature`` is created, so the sheet boundary can never be selected as
a CAM contour.  The authoritative ``VectorDocument`` remains untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence, Tuple

from woodcam_editor.application.print_layout import (
    PrintLayout,
    resolve_print_layouts,
)
from woodcam_editor.exporters.svg import document_sheet_to_svg


@dataclass(frozen=True)
class TechDrawPrintResult:
    pages: Tuple[Any, ...]
    symbols: Tuple[Any, ...]
    layouts: Tuple[PrintLayout, ...]


def _freecad_module():
    try:
        import FreeCAD  # type: ignore
    except ImportError as error:  # pragma: no cover - FreeCADCmd boundary
        raise RuntimeError("O envio ao TechDraw exige o FreeCAD.") from error
    return FreeCAD


def _template_path(FreeCAD: Any, layout: PrintLayout) -> str:
    path = (
        Path(str(FreeCAD.getResourceDir()))
        / "Mod"
        / "TechDraw"
        / "Templates"
        / "ISO"
        / layout.paper.template_name
    )
    if not path.is_file():
        raise RuntimeError("Modelo TechDraw não encontrado: %s" % path)
    return str(path)


def _add_string_property(obj: Any, name: str, value: Any) -> None:
    if name not in tuple(getattr(obj, "PropertiesList", ()) or ()):
        obj.addProperty("App::PropertyString", name, "WoodCAM 2D")
    setattr(obj, name, str(value))


def create_techdraw_print_pages(
    freecad_document: Any,
    vector_document: Any,
    sheet_bounds: Iterable[Sequence[float]],
    *,
    sheet_indices: Iterable[int] | None = None,
    paper_name: str = "A4",
    scale_mode: str = "fit",
    custom_scale_percent: float = 100.0,
    margin_mm: float = 10.0,
    include_sheet_fill: bool = True,
    toolpath_components: Any = None,
) -> TechDrawPrintResult:
    """Create persistent TechDraw pages from a read-only vector snapshot."""

    if freecad_document is None:
        raise RuntimeError("Abra ou crie um documento antes de enviar ao TechDraw.")
    bounds_values = tuple(tuple(map(float, value)) for value in sheet_bounds)
    if not bounds_values:
        raise ValueError("Nenhuma chapa configurada para impressão.")
    indices = (
        tuple(int(value) for value in sheet_indices)
        if sheet_indices is not None
        else tuple(range(len(bounds_values)))
    )
    if len(indices) != len(bounds_values):
        raise ValueError("Cada chapa precisa conservar seu índice de origem.")
    layouts = resolve_print_layouts(
        bounds_values,
        paper_name,
        scale_mode=scale_mode,
        custom_scale_percent=custom_scale_percent,
        margin_mm=margin_mm,
    )
    invalid = tuple(layout for layout in layouts if not layout.fits)
    if invalid:
        raise ValueError(
            "A escala escolhida não cabe no papel %s; use Ajustar à página ou reduza a escala."
            % paper_name
        )

    FreeCAD = _freecad_module()
    revision_before = int(getattr(vector_document, "revision", 0))
    entity_ids_before = tuple(
        sorted(str(value) for value in getattr(vector_document, "entities_by_id", {}))
    )
    pages = []
    symbols = []
    transaction_open = False
    try:
        freecad_document.openTransaction("Criar páginas TechDraw do WoodCAM")
        transaction_open = True
        for layout, source_index in zip(layouts, indices):
            sheet_number = source_index + 1
            page = freecad_document.addObject(
                "TechDraw::DrawPage", "WoodCAM_Print_Sheet_%02d" % sheet_number
            )
            page.Label = "WoodCAM — Chapa %02d (%s, %.1f%%)" % (
                sheet_number,
                layout.paper.name,
                layout.scale_percent,
            )
            template = freecad_document.addObject(
                "TechDraw::DrawSVGTemplate",
                "WoodCAM_Print_Template_%02d" % sheet_number,
            )
            template.Label = "Modelo %s — Chapa %02d" % (
                layout.paper.name,
                sheet_number,
            )
            template.Template = _template_path(FreeCAD, layout)
            page.Template = template
            page.Scale = 1.0
            if hasattr(page, "KeepUpdated"):
                page.KeepUpdated = False

            symbol = freecad_document.addObject(
                "TechDraw::DrawViewSymbol",
                "WoodCAM_Print_Content_%02d" % sheet_number,
            )
            symbol.Label = "Conteúdo da Chapa %02d" % sheet_number
            symbol.Symbol = document_sheet_to_svg(
                vector_document,
                layout.bounds,
                visible_only=True,
                toolpath_components=toolpath_components,
                include_sheet_fill=include_sheet_fill,
            )
            page.addView(symbol)
            symbol.ScaleType = "Custom"
            symbol.Scale = float(layout.scale)
            symbol.Rotation = float(layout.rotation_degrees)
            symbol.X = float(layout.paper.width_mm * 0.5)
            symbol.Y = float(layout.paper.height_mm * 0.5)
            symbol.LockPosition = True

            _add_string_property(page, "WoodCAMManagedType", "techdraw_print_page")
            _add_string_property(
                page,
                "WoodCAMSourceUUID",
                getattr(vector_document, "document_uuid", ""),
            )
            _add_string_property(page, "WoodCAMSourceRevision", revision_before)
            _add_string_property(page, "WoodCAMSheetIndex", source_index)
            _add_string_property(page, "WoodCAMPaper", layout.paper.name)
            _add_string_property(page, "WoodCAMPrintScale", "%.12g" % layout.scale)
            _add_string_property(
                page,
                "WoodCAMPrintRotation",
                layout.rotation_degrees,
            )
            pages.append(page)
            symbols.append(symbol)

        freecad_document.recompute()
        freecad_document.commitTransaction()
        transaction_open = False
    except Exception:
        if transaction_open:
            freecad_document.abortTransaction()
        raise

    # Printing is a derived host operation.  Guard the contract explicitly so
    # a future adapter edit cannot accidentally mutate the vector source.
    if int(getattr(vector_document, "revision", 0)) != revision_before or tuple(
        sorted(str(value) for value in getattr(vector_document, "entities_by_id", {}))
    ) != entity_ids_before:
        raise RuntimeError("O envio ao TechDraw alterou indevidamente o documento vetorial.")

    return TechDrawPrintResult(tuple(pages), tuple(symbols), layouts)
