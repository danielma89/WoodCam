"""Conversões puras entre coordenadas de máquina e a vista do modelo 3D.

O G-code usa o zero Z escolhido pelo operador. A malha fonte, porém, continua
nas coordenadas originais do documento. A prévia deve alinhar o percurso a
essa malha sem alterar nem a fonte nem os movimentos persistidos.
"""

from __future__ import annotations


_THREE_D_MODES = {"rough3d", "finish3d"}


def model_gap_above(settings):
    """Retorna a folga superior efetiva usada na usinagem 3D."""
    model_height = float(settings.get("_mesh_model_height", 0.0) or 0.0)
    if settings.get("model_position_mode") == "gap_below":
        thickness = float(settings.get("material_thickness", 0.0) or 0.0)
        gap_below = float(settings.get("model_gap_below", 0.0) or 0.0)
        return max(0.0, thickness - model_height - gap_below)
    return max(0.0, float(settings.get("model_gap_above", 0.0) or 0.0))


def preview_material_top_z(settings):
    """Z visual do topo do material no mesmo referencial da malha fonte."""
    mode = settings.get("operation_mode")
    model_height = float(settings.get("_mesh_model_height", 0.0) or 0.0)
    if mode not in _THREE_D_MODES or model_height <= 0.0:
        if settings.get("z_zero_mode") == "machine_bed":
            return float(settings.get("material_thickness", 0.0) or 0.0)
        return 0.0

    source_max_z = settings.get("_mesh_source_max_z")
    if source_max_z is None:
        source_max_z = model_height
    source_max_z = float(source_max_z)
    return source_max_z + model_gap_above(settings)


def preview_z_offset(settings):
    """Translação apenas visual para movimentos já em coordenada de máquina."""
    if settings.get("operation_mode") not in _THREE_D_MODES:
        return 0.0
    model_height = float(settings.get("_mesh_model_height", 0.0) or 0.0)
    if model_height <= 0.0:
        return 0.0
    machine_material_top = (
        float(settings.get("material_thickness", 0.0) or 0.0)
        if settings.get("z_zero_mode") == "machine_bed"
        else 0.0
    )
    return preview_material_top_z(settings) - machine_material_top


def moves_for_preview(settings, moves):
    """Copia movimentos e alinha Z à malha, preservando os dados de máquina."""
    offset = preview_z_offset(settings)
    if abs(offset) <= 1e-12:
        return [dict(move) for move in moves]
    displayed = []
    for move in moves:
        converted = dict(move)
        if converted.get("z") is not None:
            converted["z"] = float(converted["z"]) + offset
        displayed.append(converted)
    return displayed


__all__ = [
    "model_gap_above",
    "moves_for_preview",
    "preview_material_top_z",
    "preview_z_offset",
]
