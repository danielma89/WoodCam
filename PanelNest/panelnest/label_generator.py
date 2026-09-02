# -*- coding: utf-8 -*-
"""
Gerador de etiquetas profissionais para PanelNest.
Formatos: SVG, PDF (batch A4), ZPL (Zebra).
Sem dependências externas além de stdlib + Qt (opcional para PDF).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import List, Tuple

try:
    from PySide2 import QtSvg, QtPrintSupport, QtGui, QtCore
except ImportError:
    try:
        from PySide6 import QtSvg, QtPrintSupport, QtGui, QtCore
    except ImportError:
        QtSvg = None
        QtPrintSupport = None
        QtGui = None
        QtCore = None

# ── Constantes de dimensão ────────────────────────────────────────────
LABEL_W_MM = 90
LABEL_H_MM = 50
PX_PER_MM = 3.78  # 96 DPI ≈ 3.78 px/mm

TAPE_COLOR = "#1BB0C2"
BORDER_COLOR = "#222222"
BG_COLOR = "#FFFFFF"


def _mm(v: float) -> float:
    return v * PX_PER_MM


def _text(
    parent: ET.Element,
    x: float,
    y: float,
    text: str,
    *,
    size: float = 10,
    weight: str = "normal",
    fill: str = "#222222",
    anchor: str = "start",
) -> ET.Element:
    el = ET.SubElement(
        parent,
        "text",
        {
            "x": str(x),
            "y": str(y),
            "font-family": "Arial, Helvetica, sans-serif",
            "font-size": str(size),
            "font-weight": weight,
            "fill": fill,
            "text-anchor": anchor,
        },
    )
    el.text = text
    return el


def _rect(
    parent: ET.Element,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    fill: str = "none",
    stroke: str = "#333333",
    stroke_width: float = 1,
    rx: float = 0,
) -> ET.Element:
    return ET.SubElement(
        parent,
        "rect",
        {
            "x": str(x),
            "y": str(y),
            "width": str(w),
            "height": str(h),
            "fill": fill,
            "stroke": stroke,
            "stroke-width": str(stroke_width),
            "rx": str(rx),
        },
    )


def _edge_band_diagram_svg(part, x: float, y: float, w: float, h: float) -> list:
    """Retorna lista de elementos SVG para o diagrama de fita de borda.

    Retângulo externo cinza com bordas coloridas nas faces com fita.
    """
    elements = []

    # Retângulo base (peça)
    rect = ET.Element(
        "rect",
        {
            "x": str(x),
            "y": str(y),
            "width": str(w),
            "height": str(h),
            "fill": "#F5F5F5",
            "stroke": "#AAAAAA",
            "stroke-width": "1",
        },
    )
    elements.append(rect)

    tw = 4  # espessura da borda de fita em px

    # Topo
    if getattr(part, "edge_band_top", False):
        line = ET.Element(
            "line",
            {
                "x1": str(x),
                "y1": str(y),
                "x2": str(x + w),
                "y2": str(y),
                "stroke": TAPE_COLOR,
                "stroke-width": str(tw),
                "stroke-linecap": "round",
            },
        )
        elements.append(line)

    # Base
    if getattr(part, "edge_band_bottom", False):
        line = ET.Element(
            "line",
            {
                "x1": str(x),
                "y1": str(y + h),
                "x2": str(x + w),
                "y2": str(y + h),
                "stroke": TAPE_COLOR,
                "stroke-width": str(tw),
                "stroke-linecap": "round",
            },
        )
        elements.append(line)

    # Esquerda
    if getattr(part, "edge_band_left", False):
        line = ET.Element(
            "line",
            {
                "x1": str(x),
                "y1": str(y),
                "x2": str(x),
                "y2": str(y + h),
                "stroke": TAPE_COLOR,
                "stroke-width": str(tw),
                "stroke-linecap": "round",
            },
        )
        elements.append(line)

    # Direita
    if getattr(part, "edge_band_right", False):
        line = ET.Element(
            "line",
            {
                "x1": str(x + w),
                "y1": str(y),
                "x2": str(x + w),
                "y2": str(y + h),
                "stroke": TAPE_COLOR,
                "stroke-width": str(tw),
                "stroke-linecap": "round",
            },
        )
        elements.append(line)

    return elements


def _grain_arrow_svg(grain_direction: str, cx: float, cy: float, size: float = 8) -> list:
    """Retorna elementos SVG para seta de veio.

    'Comprimento da chapa' → seta vertical ↕
    'Largura da chapa'     → seta horizontal ↔
    'Livre'                → nenhum elemento
    """
    elements = []
    gd = str(grain_direction or "").strip()

    if gd == "Livre" or not gd:
        return elements

    vertical = gd == "Comprimento da chapa"
    color = "#555555"
    stroke_w = "1.5"

    if vertical:
        # Linha vertical
        line = ET.Element(
            "line",
            {
                "x1": str(cx),
                "y1": str(cy - size),
                "x2": str(cx),
                "y2": str(cy + size),
                "stroke": color,
                "stroke-width": stroke_w,
            },
        )
        elements.append(line)
        # Ponta superior
        top_arrow = ET.Element(
            "polygon",
            {
                "points": f"{cx},{cy - size - 3} {cx - 2.5},{cy - size + 2} {cx + 2.5},{cy - size + 2}",
                "fill": color,
            },
        )
        elements.append(top_arrow)
        # Ponta inferior
        bot_arrow = ET.Element(
            "polygon",
            {
                "points": f"{cx},{cy + size + 3} {cx - 2.5},{cy + size - 2} {cx + 2.5},{cy + size - 2}",
                "fill": color,
            },
        )
        elements.append(bot_arrow)
    else:
        # Linha horizontal
        line = ET.Element(
            "line",
            {
                "x1": str(cx - size),
                "y1": str(cy),
                "x2": str(cx + size),
                "y2": str(cy),
                "stroke": color,
                "stroke-width": stroke_w,
            },
        )
        elements.append(line)
        # Ponta esquerda
        left_arrow = ET.Element(
            "polygon",
            {
                "points": f"{cx - size - 3},{cy} {cx - size + 2},{cy - 2.5} {cx - size + 2},{cy + 2.5}",
                "fill": color,
            },
        )
        elements.append(left_arrow)
        # Ponta direita
        right_arrow = ET.Element(
            "polygon",
            {
                "points": f"{cx + size + 3},{cy} {cx + size - 2},{cy - 2.5} {cx + size - 2},{cy + 2.5}",
                "fill": color,
            },
        )
        elements.append(right_arrow)

    return elements


def generate_label_svg(part, sheet_ref: str = "", project_name: str = "") -> str:
    """Gera uma etiqueta SVG para uma peça.

    Parâmetros
    ----------
    part : PanelPart
        A peça a ser etiquetada.
    sheet_ref : str
        Referência da chapa (ex: 'Chapa 2').
    project_name : str
        Nome do projeto exibido no topo.

    Retorna
    -------
    str
        Documento SVG completo como string.
    """
    w = _mm(LABEL_W_MM)
    h = _mm(LABEL_H_MM)
    pad = _mm(3)

    svg = ET.Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "width": f"{LABEL_W_MM}mm",
            "height": f"{LABEL_H_MM}mm",
            "viewBox": f"0 0 {w:.1f} {h:.1f}",
        },
    )

    # Fundo branco com borda
    _rect(svg, 0, 0, w, h, fill=BG_COLOR, stroke=BORDER_COLOR, stroke_width=1.5, rx=_mm(2))

    # Linha separadora do cabeçalho
    header_h = _mm(10) if project_name else _mm(7)

    y_cursor = pad + _mm(3.5)

    # Nome do projeto (pequeno, cinza)
    if project_name:
        _text(svg, pad, y_cursor, project_name, size=_mm(2.2), fill="#666666")
        y_cursor += _mm(4.5)

    # Nome da peça (grande, negrito)
    label_text = str(getattr(part, "label", "") or "")
    _text(svg, pad, y_cursor, label_text, size=_mm(4), weight="bold", fill="#111111")
    y_cursor += _mm(5.5)

    # Dimensões
    length = getattr(part, "length_mm", 0) or 0
    width = getattr(part, "width_mm", 0) or 0
    thickness = getattr(part, "thickness_mm", 0) or 0
    dims_str = f"{length:.0f} \u00d7 {width:.0f} \u00d7 {thickness:.0f}mm"
    _text(svg, pad, y_cursor, dims_str, size=_mm(3.2), fill="#333333")
    y_cursor += _mm(4.5)

    # Material
    material = str(getattr(part, "material", "") or "")
    if material:
        _text(svg, pad, y_cursor, material, size=_mm(2.8), fill="#555555")
        y_cursor += _mm(4)

    # Referência da chapa
    if sheet_ref:
        _text(svg, pad, y_cursor, sheet_ref, size=_mm(2.4), fill="#888888")
        y_cursor += _mm(3.5)

    # ── Área de diagramas (direita da etiqueta) ──────────────────────
    diag_area_x = w - _mm(32)
    diag_w = _mm(18)
    diag_h = _mm(12)
    diag_y = h - _mm(3) - diag_h - _mm(5)

    # Diagrama de fita de borda
    diag_elements = _edge_band_diagram_svg(
        part,
        diag_area_x,
        diag_y,
        diag_w,
        diag_h,
    )
    for el in diag_elements:
        svg.append(el)

    # Seta de veio (à direita do diagrama)
    grain_direction = str(getattr(part, "grain_direction", "Livre") or "Livre")
    arrow_cx = diag_area_x + diag_w + _mm(6)
    arrow_cy = diag_y + diag_h / 2
    arrow_elements = _grain_arrow_svg(grain_direction, arrow_cx, arrow_cy, size=_mm(4))
    for el in arrow_elements:
        svg.append(el)

    # Legenda da fita (rodapé esquerdo)
    has_any_band = any([
        getattr(part, "edge_band_top", False),
        getattr(part, "edge_band_bottom", False),
        getattr(part, "edge_band_left", False),
        getattr(part, "edge_band_right", False),
    ])
    if has_any_band:
        sides = []
        if getattr(part, "edge_band_top", False):
            sides.append("T")
        if getattr(part, "edge_band_bottom", False):
            sides.append("B")
        if getattr(part, "edge_band_left", False):
            sides.append("E")
        if getattr(part, "edge_band_right", False):
            sides.append("D")
        fita_str = "Fita: " + "+".join(sides)
        _text(svg, pad, h - pad - _mm(1), fita_str, size=_mm(2.2), fill=TAPE_COLOR)

    # Legenda do veio (rodapé direito)
    if grain_direction and grain_direction != "Livre":
        veio_label = "veio ↕" if grain_direction == "Comprimento da chapa" else "veio ↔"
        _text(
            svg,
            w - pad,
            h - pad - _mm(1),
            veio_label,
            size=_mm(2.2),
            fill="#555555",
            anchor="end",
        )

    return ET.tostring(svg, encoding="unicode", xml_declaration=True)


def generate_label_svg_batch(
    parts_with_refs: List[Tuple],
    project_name: str = "",
) -> List[str]:
    """Gera etiquetas SVG para uma lista de peças com referências de chapa.

    Parâmetros
    ----------
    parts_with_refs : list[tuple[PanelPart, str]]
        Lista de (peça, referência_chapa).
    project_name : str
        Nome do projeto exibido em todas as etiquetas.

    Retorna
    -------
    list[str]
        Lista de strings SVG, uma por peça física (respeitando quantity).
    """
    labels = []
    for part, sheet_ref in parts_with_refs:
        quantity = int(getattr(part, "quantity", 1) or 1)
        for _ in range(quantity):
            labels.append(
                generate_label_svg(part, sheet_ref=sheet_ref, project_name=project_name)
            )
    return labels


def generate_labels_pdf_a4(
    parts_with_refs: List[Tuple],
    output_path: str,
    project_name: str = "",
) -> None:
    """Gera arquivo PDF A4 com 6 etiquetas por página (3 colunas × 2 linhas).

    Parâmetros
    ----------
    parts_with_refs : list[tuple[PanelPart, str]]
        Lista de (peça, referência_chapa).
    output_path : str
        Caminho do arquivo PDF de saída.
    project_name : str
        Nome do projeto para todas as etiquetas.

    Levanta
    -------
    RuntimeError
        Se Qt não estiver disponível.
    """
    if QtSvg is None or QtGui is None or QtCore is None:
        raise RuntimeError("Qt não disponível para geração de PDF")

    svg_list = generate_label_svg_batch(parts_with_refs, project_name=project_name)
    if not svg_list:
        return

    # Configuração do PDF
    printer = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.HighResolution)
    printer.setOutputFormat(QtPrintSupport.QPrinter.PdfFormat)
    printer.setOutputFileName(output_path)

    try:
        # PySide2 / PySide6 diferenças
        page_size_a4 = QtGui.QPageSize(QtGui.QPageSize.A4)
        printer.setPageSize(page_size_a4)
    except AttributeError:
        try:
            printer.setPageSize(QtPrintSupport.QPrinter.A4)
        except Exception:
            pass

    printer.setPageOrientation(QtGui.QPageLayout.Portrait)

    # Margens e layout: 10mm margem, 3 colunas × 2 linhas, 90×50mm cada
    margin_mm = 10.0
    cols = 3
    rows = 2
    labels_per_page = cols * rows

    # Converter mm para pixels (72 DPI para PDF Qt)
    dpi = printer.resolution()
    mm_to_px = dpi / 25.4

    label_w_px = int(LABEL_W_MM * mm_to_px)
    label_h_px = int(LABEL_H_MM * mm_to_px)
    margin_px = int(margin_mm * mm_to_px)

    painter = QtGui.QPainter()
    painter.begin(printer)

    page_rect = printer.pageRect(QtPrintSupport.QPrinter.DevicePixel)
    page_w = page_rect.width()
    page_h = page_rect.height()

    for idx, svg_str in enumerate(svg_list):
        if idx > 0 and idx % labels_per_page == 0:
            printer.newPage()

        pos_in_page = idx % labels_per_page
        col = pos_in_page % cols
        row = pos_in_page // cols

        x = margin_px + col * (label_w_px + margin_px)
        y = margin_px + row * (label_h_px + margin_px)

        renderer = QtSvg.QSvgRenderer()
        svg_bytes = svg_str.encode("utf-8")
        renderer.load(QtCore.QByteArray(svg_bytes))

        target_rect = QtCore.QRectF(x, y, label_w_px, label_h_px)
        renderer.render(painter, target_rect)

    painter.end()


def generate_label_zpl(part, sheet_ref: str = "") -> str:
    """Gera ZPL II para impressoras Zebra (etiqueta 90×50mm, 203 DPI).

    Parâmetros
    ----------
    part : PanelPart
        A peça a ser etiquetada.
    sheet_ref : str
        Referência da chapa.

    Retorna
    -------
    str
        Comandos ZPL como string.
    """
    # 203 DPI: 1mm ≈ 8 dots
    DOTS_PER_MM = 8
    label_w_dots = LABEL_W_MM * DOTS_PER_MM   # 720
    label_h_dots = LABEL_H_MM * DOTS_PER_MM   # 400

    label_text = str(getattr(part, "label", "") or "")
    length = getattr(part, "length_mm", 0) or 0
    width = getattr(part, "width_mm", 0) or 0
    thickness = getattr(part, "thickness_mm", 0) or 0
    material = str(getattr(part, "material", "") or "")

    dims_str = f"{length:.0f}x{width:.0f}x{thickness:.0f}mm"

    # Extrair código curto da etiqueta (ex: "PN-001" de "PN-001 - Lateral dir.")
    qr_data = label_text.split(" - ")[0].strip() if " - " in label_text else label_text
    if not qr_data:
        qr_data = "PANELNEST"

    lines = [
        "^XA",
        f"^PW{label_w_dots}",
        f"^LL{label_h_dots}",
        "^CI28",           # UTF-8
        # Nome da peça (negrito, fonte grande)
        "^FO20,20",
        "^A0N,40,40",
        f"^FD{label_text}^FS",
        # Dimensões
        "^FO20,75",
        "^A0N,28,28",
        f"^FD{dims_str}^FS",
    ]

    # Material
    if material:
        lines += [
            "^FO20,115",
            "^A0N,24,24",
            f"^FD{material}^FS",
        ]

    # Referência da chapa
    if sheet_ref:
        lines += [
            "^FO20,150",
            "^A0N,22,22",
            f"^FD{sheet_ref}^FS",
        ]

    # QR code (canto inferior direito)
    qr_x = label_w_dots - 200
    qr_y = label_h_dots - 200
    lines += [
        f"^FO{qr_x},{qr_y}",
        "^BQN,2,4",
        f"^FDQA,{qr_data}^FS",
    ]

    # Linha separadora
    lines += [
        "^FO10,60",
        f"^GB{label_w_dots - 20},2,2^FS",
    ]

    lines.append("^XZ")

    return "\n".join(lines)
