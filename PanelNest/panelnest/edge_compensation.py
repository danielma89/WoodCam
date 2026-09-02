"""
Compensação de espessura de fita de borda (edge banding) no corte.

Quando uma peça recebe fita de borda, a dimensão final inclui a espessura
da fita. Por isso, a peça deve ser cortada com dimensões menores para que,
após a colagem da fita, a dimensão final seja a especificada no projeto.

Exemplo: peça de 600mm com fita de 0.5mm em 2 faces longitudinais
→ cortar com 599mm (600 - 0.5 - 0.5)

A compensação é aplicada sobre uma CÓPIA da peça — o PanelPart original
nunca é modificado. As dimensões nominais (do modelo 3D) são preservadas.
"""
from dataclasses import replace


def compensated_cut_dimensions(part, tape_thickness_mm: float) -> tuple:
    """Retorna (cut_length_mm, cut_width_mm) após subtrair a espessura da fita
    por face com fita de borda.

    Convenção de faces:
    - top / bottom → faces na direção da LARGURA (diminuem width_mm)
    - left / right → faces na direção do COMPRIMENTO (diminuem length_mm)

    Args:
        part: PanelPart com campos edge_band_top/bottom/left/right e
              length_mm / width_mm.
        tape_thickness_mm: espessura da fita de borda em mm (ex: 0.5).

    Returns:
        Tupla (cut_length_mm, cut_width_mm) já compensada.
    """
    if tape_thickness_mm <= 0.0:
        return (part.length_mm, part.width_mm)

    cut_length = part.length_mm
    cut_width = part.width_mm

    # Faces laterais (left/right) encurtam o comprimento
    if getattr(part, "edge_band_left", False):
        cut_length -= tape_thickness_mm
    if getattr(part, "edge_band_right", False):
        cut_length -= tape_thickness_mm

    # Faces superior/inferior (top/bottom) encurtam a largura
    if getattr(part, "edge_band_top", False):
        cut_width -= tape_thickness_mm
    if getattr(part, "edge_band_bottom", False):
        cut_width -= tape_thickness_mm

    # Garantir que as dimensões compensadas sejam positivas
    cut_length = max(cut_length, 1.0)
    cut_width = max(cut_width, 1.0)

    return (cut_length, cut_width)


def apply_cut_compensation(part, tape_thickness_mm: float):
    """Retorna uma cópia do PanelPart com dimensões ajustadas para o corte.

    O PanelPart original NÃO é modificado. A cópia tem:
    - length_mm e width_mm = dimensões de corte compensadas
    - Os demais campos inalterados

    Retorna `part` sem modificação se tape_thickness_mm <= 0.
    """
    if tape_thickness_mm <= 0.0:
        return part

    cut_length, cut_width = compensated_cut_dimensions(part, tape_thickness_mm)
    return replace(part, length_mm=cut_length, width_mm=cut_width)


def compensation_summary(part, tape_thickness_mm: float) -> str:
    """Retorna uma string descritiva da compensação para exibir em relatórios.

    Exemplo: "600×400 → 599×399 (fita 0.5mm)"
    Retorna string vazia se não houver compensação.
    """
    if tape_thickness_mm <= 0.0:
        return ""

    cut_l, cut_w = compensated_cut_dimensions(part, tape_thickness_mm)
    if cut_l == part.length_mm and cut_w == part.width_mm:
        return ""

    return (
        f"{part.length_mm:.0f}×{part.width_mm:.0f} → "
        f"{cut_l:.0f}×{cut_w:.0f} (fita {tape_thickness_mm}mm)"
    )
