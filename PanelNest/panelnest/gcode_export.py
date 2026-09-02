"""
Exportação G-code NBM (Nested-Based Manufacturing) para CNC.
Formato compatível com roteadores CNC comuns em marcenarias brasileiras.
Um arquivo .nbm por chapa do layout.
"""

import os
import unicodedata
from datetime import date

from .orientation import placement_rotation_deg, transform_placement_point


def export_nbm_bundle(layout_sheets, settings, output_dir):
    """
    Exporta um arquivo .nbm por layout_sheet.

    Retorna lista de caminhos de arquivo criados.
    output_dir é criado se não existir.
    """
    os.makedirs(output_dir, exist_ok=True)
    created_paths = []
    for index, layout_sheet in enumerate(layout_sheets or []):
        material_slug = _nbm_slugify(str(getattr(layout_sheet, "material", "") or ""))
        thickness = float(getattr(layout_sheet, "thickness_mm", 0) or 0)
        file_name = f"chapa_{index + 1}_{material_slug}_{thickness:.0f}mm.nbm"
        output_path = os.path.join(output_dir, file_name)
        export_nbm_sheet(layout_sheet, settings, output_path)
        created_paths.append(output_path)
    return created_paths


def export_nbm_sheet(layout_sheet, settings, output_path):
    """
    Gera o arquivo NBM para uma única chapa.
    Escreve o conteúdo em output_path.
    """
    content = _nbm_content(layout_sheet, settings)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _nbm_content(layout_sheet, settings):
    """
    Gera o conteúdo completo do arquivo NBM para uma chapa.
    """
    material = str(getattr(layout_sheet, "material", "") or "")
    thickness = float(getattr(layout_sheet, "thickness_mm", 0) or 0)
    length = float(getattr(layout_sheet, "source_length_mm", 0) or 0)
    width = float(getattr(layout_sheet, "source_width_mm", 0) or 0)
    source_label = str(getattr(layout_sheet, "source_label", "") or "")
    placements = list(getattr(layout_sheet, "placements", None) or [])
    kerf = float(getattr(settings, "cnc_kerf_mm", 3.0) or 3.0)

    nome_da_chapa = source_label if source_label else f"{material} {thickness:.0f}mm"

    lines = [
        f"; PanelNest NBM Export",
        f"; Chapa: {nome_da_chapa}",
        f"; Material: {material} {thickness:.0f}mm",
        f"; Data: {_nbm_date()}",
        f"; Pecas: {len(placements)}",
        "",
        "[PROGRAMA]",
        f"CHAPA_COMP={length:.1f}",
        f"CHAPA_LARG={width:.1f}",
        f"ESPESSURA={thickness:.1f}",
        f"MATERIAL={material}",
        f"FRESA=1 DIAM={kerf:.1f} AVANCO=18000 PROF={thickness + 1:.1f}",
        "",
        "[PECAS]",
    ]

    for index, placement in enumerate(placements, start=1):
        lines.append(_nbm_part_block(placement, index))

    drill_lines = _nbm_drill_section(placements)
    if drill_lines:
        lines.extend(["", "[FUROS]"] + drill_lines)

    lines.extend(["", "[FIM]", ""])
    return "\n".join(lines)


def _nbm_part_block(placement, index):
    """
    Gera o bloco NBM para uma peça individual.
    """
    part = getattr(placement, "part", None)
    label = str(getattr(part, "label", "") or "") if part is not None else ""
    placed_length = float(getattr(placement, "placed_length_mm", 0) or 0)
    placed_width = float(getattr(placement, "placed_width_mm", 0) or 0)
    x = float(getattr(placement, "x_mm", 0) or 0)
    y = float(getattr(placement, "y_mm", 0) or 0)
    rotation_deg = placement_rotation_deg(placement)

    block_lines = []
    if rotation_deg:
        block_lines.append(
            f"; {index}. {label} ({placed_length:.1f} x {placed_width:.1f}) "
            f"[ROTACIONADO] [ANGULO={rotation_deg}]"
        )
    else:
        block_lines.append(f"; {index}. {label} ({placed_length:.1f} x {placed_width:.1f})")
    block_lines.append(
        f"RETANGULO X={x:.3f} Y={y:.3f} COMP={placed_length:.3f} LARG={placed_width:.3f} FRESA=1"
    )
    return "\n".join(block_lines)


def _nbm_drill_section(placements):
    """
    Gera linhas DRILL e REBAIXO para todos os furos em todas as peças.
    Coordenadas absolutas na chapa (origem inferior-esquerdo).
    Inclui furos de perfis de usinagem (dobradiça, excêntrico, cavilha, etc.).
    """
    drill_lines = []
    for placement in placements:
        part = getattr(placement, "part", None)
        if part is None:
            continue
        holes = list(getattr(part, "holes", None) or [])
        if not holes:
            continue
        x_off = float(getattr(placement, "x_mm", 0) or 0)
        y_off = float(getattr(placement, "y_mm", 0) or 0)
        part_id = str(getattr(part, "part_id", "") or "")
        for hole in holes:
            local_x = float(hole.get("x_mm", 0))
            local_y = float(hole.get("y_mm", 0))
            local_x, local_y = transform_placement_point(
                placement,
                local_x,
                local_y,
            )
            hx = x_off + local_x
            hy = y_off + local_y
            diam = float(hole.get("diameter_mm", 0))
            depth = float(hole.get("depth_mm", 0))
            desc = str(hole.get("description", "") or "")
            face = str(hole.get("face", "top") or "top")
            if diam <= 0:
                continue
            depth_str = f" PROF={depth:.1f}" if depth > 0 else ""
            comment = f"; {part_id}"
            if desc:
                comment += f" - {desc}"
            drill_lines.append(comment)
            # Furos grandes (>= 15mm) são rebaixos (counterbore para dobradiças)
            if diam >= 15.0:
                drill_lines.append(
                    f"REBAIXO X={hx:.3f} Y={hy:.3f} DIAM={diam:.1f}{depth_str} FACE={face}"
                )
            else:
                drill_lines.append(
                    f"DRILL X={hx:.3f} Y={hy:.3f} DIAM={diam:.1f}{depth_str} FACE={face}"
                )
    return drill_lines


def _nbm_slugify(text):
    """
    Converte texto para ASCII seguro para nomes de arquivo.
    Ex: "MDF 18mm" -> "MDF_18mm"
    """
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    result = ""
    for char in ascii_text:
        if char.isalnum() or char in ("-", "_", "."):
            result += char
        elif char in (" ", "\t"):
            result += "_"
    return result or "chapa"


def _nbm_date():
    """
    Retorna a data atual em formato YYYY-MM-DD.
    """
    return date.today().isoformat()
