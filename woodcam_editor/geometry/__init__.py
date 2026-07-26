"""Pure geometric algorithms used by the vector domain.

Modifier exports are loaded lazily because the low-level domain spans import
``math2d`` during package initialisation.
"""

from .math2d import *  # noqa: F401,F403

_MODIFIER_EXPORTS = {
    "FilletKind",
    "ModifierError",
    "NoApplicableCornersError",
    "ModifierPreview",
    "ModifierWarning",
    "GeometryProjection",
    "project_point_to_geometry",
    "preview_connect_endpoint_to_geometry",
    "preview_corner_fillet",
    "preview_auto_corner_reliefs",
    "preview_dogbone",
    "preview_extend_line_span",
    "preview_offset_closed_path",
    "preview_create_offset_contour",
    "preview_tbone",
    "preview_splice_open_path_to_contour",
    "preview_trim_at_point",
    "preview_trim_line_span",
}

_MATH_EXPORTS = {
    "TAU",
    "PointLocation",
    "SegmentIntersection",
    "almost_equal",
    "angle_on_sweep",
    "clamp",
    "closest_point_on_segment",
    "deduplicate_consecutive",
    "directed_sweep",
    "distance_point_to_segment",
    "normalize_angle",
    "orientation",
    "point_in_polygon",
    "point_on_segment",
    "polygon_centroid",
    "polyline_length",
    "projection_parameter",
    "segment_intersections",
    "signed_area",
}

__all__ = sorted(_MATH_EXPORTS | _MODIFIER_EXPORTS)


def __getattr__(name):
    if name in _MODIFIER_EXPORTS:
        from . import modifiers

        value = getattr(modifiers, name)
        globals()[name] = value
        return value
    raise AttributeError(name)
