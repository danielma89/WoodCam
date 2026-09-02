"""Transformações canônicas de orientação para peças do layout."""


def normalize_rotation_deg(value):
    try:
        rotation_deg = int(round(float(value)))
    except (TypeError, ValueError):
        rotation_deg = 0
    return rotation_deg % 360


def placement_rotation_deg(placement):
    explicit_rotation = getattr(placement, "rotation_deg", None)
    if explicit_rotation is not None:
        return normalize_rotation_deg(explicit_rotation)
    return 90 if bool(getattr(placement, "rotated", False)) else 0


def rotation_swaps_axes(rotation_deg):
    return normalize_rotation_deg(rotation_deg) in (90, 270)


def transform_local_point(x_mm, y_mm, length_mm, width_mm, rotation_deg):
    """Rotaciona um ponto em torno da origem e o normaliza no primeiro quadrante."""
    rotation_deg = normalize_rotation_deg(rotation_deg)
    x_mm = float(x_mm)
    y_mm = float(y_mm)
    length_mm = float(length_mm)
    width_mm = float(width_mm)
    if rotation_deg == 90:
        return width_mm - y_mm, x_mm
    if rotation_deg == 180:
        return length_mm - x_mm, width_mm - y_mm
    if rotation_deg == 270:
        return y_mm, length_mm - x_mm
    return x_mm, y_mm


def transform_part_point(part, x_mm, y_mm, rotation_deg):
    return transform_local_point(
        x_mm,
        y_mm,
        float(getattr(part, "length_mm", 0.0) or 0.0),
        float(getattr(part, "width_mm", 0.0) or 0.0),
        rotation_deg,
    )


def transform_placement_point(placement, x_mm, y_mm):
    return transform_part_point(
        placement.part,
        x_mm,
        y_mm,
        placement_rotation_deg(placement),
    )


def rotated_layout_side(source_side, rotation_deg):
    """Mapeia top/bottom/left/right depois da rotação anti-horária."""
    rotation_deg = normalize_rotation_deg(rotation_deg)
    mappings = {
        0: {
            "top": "top",
            "bottom": "bottom",
            "left": "left",
            "right": "right",
        },
        90: {
            "top": "left",
            "bottom": "right",
            "left": "bottom",
            "right": "top",
        },
        180: {
            "top": "bottom",
            "bottom": "top",
            "left": "right",
            "right": "left",
        },
        270: {
            "top": "right",
            "bottom": "left",
            "left": "top",
            "right": "bottom",
        },
    }
    return mappings[rotation_deg].get(source_side, source_side)
