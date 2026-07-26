"""Copy PanelNest's flat part reading into the independent Editor 2D.

This adapter intentionally calls no PanelNest layout/nesting API.  PanelNest
only identifies the useful panel profile and drilling data; WoodCAM stages the
result as separate 2D pieces for its own preview and organizer.
"""

from __future__ import annotations

from dataclasses import replace
import math
from typing import Any, Iterable

from .part_shape import ImportIssue, ImportLayerDescriptor, ImportResult


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _profile_points(part: Any):
    raw = list(getattr(part, "profile_points", []) or [])
    points = []
    for point in raw:
        try:
            points.append((float(point[0]), float(point[1])))
        except (IndexError, TypeError, ValueError):
            continue
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    if len(points) >= 3:
        return points
    length = _number(getattr(part, "length_mm", 0.0))
    width = _number(getattr(part, "width_mm", 0.0))
    if length <= 0.0 or width <= 0.0:
        return []
    return [(0.0, 0.0), (length, 0.0), (length, width), (0.0, width)]


def _hole_values(hole: Any):
    if not isinstance(hole, dict):
        return None
    diameter = _number(hole.get("diameter_mm", hole.get("diameter", 0.0)))
    x_value = _number(hole.get("x_mm", hole.get("x", 0.0)))
    y_value = _number(hole.get("y_mm", hole.get("y", 0.0)))
    if diameter <= 0.0:
        return None
    return x_value, y_value, diameter


def _inner_profile_loops(part: Any):
    """Return closed non-circular cutouts supplied by PanelNest, if any."""

    loops = []
    for raw_loop in list(getattr(part, "inner_profile_loops", []) or []):
        loop = []
        for point in raw_loop or ():
            try:
                loop.append((float(point[0]), float(point[1])))
            except (IndexError, TypeError, ValueError):
                continue
        if len(loop) > 1 and loop[0] == loop[-1]:
            loop.pop()
        if len(loop) >= 3:
            loops.append(tuple(loop))
    return tuple(loops)


def _has_explicit_profile(part: Any) -> bool:
    """Whether PanelNest supplied geometry instead of only a bounding box."""

    points = []
    for point in list(getattr(part, "profile_points", []) or []):
        try:
            points.append((float(point[0]), float(point[1])))
        except (IndexError, TypeError, ValueError):
            continue
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    return len(points) >= 3


def import_panelnest_parts(
    parts: Iterable[Any],
    *,
    layer_id: str,
    staging_gap: float = 25.0,
    inner_profiles_by_source: Any = None,
    source_profiles_by_source: Any = None,
    exact_entities_by_source: Any = None,
) -> ImportResult:
    """Build separate flat Editor entities from PanelNest ``PanelPart`` data."""

    from woodcam_editor.domain.entities import CircleEntity, GroupEntity, PathEntity
    from woodcam_editor.domain.primitives import Vec2

    gap = max(1.0, _number(staging_gap, 25.0))
    entities = []
    issues = []
    imported_count = 0
    occurrences = []
    for part_index, part in enumerate(parts or ()):
        profile = _profile_points(part)
        label = str(getattr(part, "label", "") or getattr(part, "part_id", "Peça"))
        source_key = str(
            getattr(part, "object_name", "") or getattr(part, "part_id", "") or ""
        )
        quantity = max(1, int(round(_number(getattr(part, "quantity", 1), 1.0))))
        exact_entities = ()
        # The PanelNest record is excellent for identifying *which* panels
        # exist, but it intentionally reduces geometry to a profile/holes
        # payload.  Whenever the immutable FreeCAD source was readable, use
        # its exact broad-face wires for every occurrence.  This preserves
        # arcs, dogbones and internal windows as one contour instead of
        # recreating them as separate circles/segments.
        if isinstance(exact_entities_by_source, dict):
            exact_entities = tuple(exact_entities_by_source.get(source_key, ()) or ())
        if exact_entities:
            try:
                min_x = min(entity.bounds().min_x for entity in exact_entities)
                max_x = max(entity.bounds().max_x for entity in exact_entities)
                min_y = min(entity.bounds().min_y for entity in exact_entities)
                max_y = max(entity.bounds().max_y for entity in exact_entities)
            except Exception:
                exact_entities = ()
        if not profile:
            issues.append(
                ImportIssue(part_index, "PanelNestPart", f"{label}: sem perfil plano válido.")
            )
            continue
        if not exact_entities:
            min_x = min(point[0] for point in profile)
            max_x = max(point[0] for point in profile)
            min_y = min(point[1] for point in profile)
            max_y = max(point[1] for point in profile)
        holes = tuple(
            value
            for value in (_hole_values(hole) for hole in list(getattr(part, "holes", []) or []))
            if value is not None
        )
        inner_loops = _inner_profile_loops(part)
        source_profile = (
            source_profiles_by_source.get(source_key)
            if isinstance(source_profiles_by_source, dict)
            else None
        )
        if isinstance(source_profile, dict):
            if not inner_loops:
                inner_loops = tuple(source_profile.get("inner_loops", ()) or ())
            source_holes = source_profile.get("holes")
            if source_holes is not None:
                holes = tuple(
                    value
                    for value in (_hole_values(hole) for hole in source_holes)
                    if value is not None
                )
        elif not inner_loops and isinstance(inner_profiles_by_source, dict):
            # Compatibility with the first PanelNest bridge API.
            inner_loops = tuple(inner_profiles_by_source.get(source_key, ()) or ())
        for occurrence in range(quantity):
            occurrences.append(
                (
                    part, profile, label, min_x, max_x, min_y, max_y,
                    holes, inner_loops, occurrence, exact_entities,
                )
            )

    # This is deliberately only a compact staging grid, never a nesting
    # calculation.  It keeps a large import manageable in the Editor while
    # leaving the real contour-aware optimization to the WoodCAM organizer.
    total_area = sum(
        ((max_x - min_x) + gap) * ((max_y - min_y) + gap)
        for _part, _profile, _label, min_x, max_x, min_y, max_y, _holes, _inners, _occurrence, _exact
        in occurrences
    )
    widest = max(
        ((max_x - min_x) + gap
         for _part, _profile, _label, min_x, max_x, _min_y, _max_y, _holes, _inners, _occurrence, _exact
         in occurrences),
        default=gap,
    )
    target_row_width = max(widest, math.sqrt(total_area) * 1.25)
    cursor_x = 0.0
    cursor_y = 0.0
    row_height = 0.0

    for part, profile, label, min_x, max_x, min_y, max_y, holes, inner_loops, occurrence, exact_entities in occurrences:
        part_width = max_x - min_x
        part_height = max_y - min_y
        if cursor_x > 0.0 and cursor_x + part_width > target_row_width:
            cursor_x = 0.0
            cursor_y += row_height + gap
            row_height = 0.0
        offset_x = cursor_x - min_x
        offset_y = cursor_y - min_y
        metadata = {
            "source_format": "panelnest_parts",
            "panelnest_part_id": str(getattr(part, "part_id", "") or ""),
            "panelnest_label": label,
            "panelnest_occurrence": occurrence + 1,
            # Classification compares containment only inside this physical
            # panel instance, never across a whole cabinet import.
            "panelnest_instance_id": "%s#%d" % (
                str(getattr(part, "part_id", "") or label), occurrence + 1,
            ),
            "panelnest_thickness_mm": _number(getattr(part, "thickness_mm", 0.0)),
        }
        if exact_entities:
            from woodcam_editor.domain.primitives import Affine2D

            translation = Affine2D.translation(Vec2(offset_x, offset_y))
            occurrence_entities = []
            for source_entity in exact_entities:
                source_metadata = dict(getattr(source_entity, "metadata", {}) or {})
                source_metadata.update(metadata)
                occurrence_entities.append(
                    replace(
                        source_entity.transformed(translation),
                        layer_id=layer_id,
                        metadata=source_metadata,
                    )
                )
            entities.extend(occurrence_entities)
            # PanelNest tells us which source records belong to one physical
            # sheet part. Preserve that identity in the VectorDocument rather
            # than leaving its outer contour, holes and cut-outs as unrelated
            # selectable fragments. CAM and piece recognition still consume
            # the exact leaf vectors; the group changes only interaction.
            if len(occurrence_entities) > 1:
                entities.append(
                    GroupEntity(
                        layer_id=layer_id,
                        child_ids=tuple(entity.id for entity in occurrence_entities),
                        metadata={
                            "name": label,
                            "source_kind": "panelnest_part_compound",
                            **metadata,
                        },
                    )
                )
        else:
            outer = PathEntity.from_points(
                layer_id,
                tuple(Vec2(x_value + offset_x, y_value + offset_y) for x_value, y_value in profile),
                closed=True,
                metadata=metadata,
            )
            entities.append(outer)
            for inner_loop in inner_loops:
                entities.append(
                    PathEntity.from_points(
                        layer_id,
                        tuple(
                            Vec2(x_value + offset_x, y_value + offset_y)
                            for x_value, y_value in inner_loop
                        ),
                        closed=True,
                        metadata=metadata,
                    )
                )
            for hole_x, hole_y, diameter in holes:
                entities.append(
                    CircleEntity(
                        layer_id,
                        Vec2(hole_x + offset_x, hole_y + offset_y),
                        diameter * 0.5,
                        metadata=metadata,
                    )
                )
        cursor_x += part_width + gap
        row_height = max(row_height, part_height)
        imported_count += 1
    if imported_count:
        issues.append(
            ImportIssue(
                imported_count,
                "PanelNestPart",
                "Peças lidas pelo PanelNest sem gerar layout/nesting; use Peças → Reconhecer peças e furos antes de organizar.",
                severity="info",
            )
        )
    source_key = "panelnest:flat-parts"
    marked_entities = []
    for entity in entities:
        metadata = dict(getattr(entity, "metadata", {}) or {})
        metadata["source_layer_key"] = source_key
        marked_entities.append(replace(entity, metadata=metadata))
    return ImportResult(
        tuple(marked_entities),
        tuple(issues),
        {"source_kind": "panelnest_parts", "nesting_executed": False},
        {
            source_key: ImportLayerDescriptor(
                source_key=source_key,
                name="PanelNest — peças planas",
                purpose="design",
            )
        },
    )
