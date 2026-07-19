"""Pure preparation of independent import batches for the Editor 2D."""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Optional, Tuple

from woodcam_editor.domain import Affine2D, BBox2D, Vec2, new_id


def _entities_bounds(entities) -> Optional[BBox2D]:
    bounds = None
    for entity in entities:
        method = getattr(entity, "bounds", None)
        if not callable(method):
            continue
        current = method()
        bounds = current if bounds is None else bounds.union(current)
    return bounds


def prepare_import_batch(
    document,
    entities: Iterable[object],
    *,
    batch_id: Optional[str] = None,
    gap: float = 50.0,
) -> Tuple[Tuple[object, ...], str, Vec2]:
    """Mark one import and move it rigidly beside intersecting existing work.

    The first import keeps its source XY. Later imports also keep source XY when
    already disjoint. Only a colliding batch is translated, as one rigid unit,
    to the right of all existing geometry. Geometry is never edited in-place.
    """

    entities = tuple(entities)
    if not entities:
        raise ValueError("prepare_import_batch requires at least one entity")
    gap = max(0.0, float(gap))
    batch_id = str(batch_id or new_id("import-batch"))
    marked = []
    for entity in entities:
        metadata = dict(getattr(entity, "metadata", {}) or {})
        metadata["import_batch_id"] = batch_id
        marked.append(replace(entity, metadata=metadata))
    marked = tuple(marked)

    incoming_bounds = _entities_bounds(marked)
    existing = tuple(getattr(document, "entities_by_id", {}).values())
    existing_bounds = _entities_bounds(existing)
    if incoming_bounds is None or existing_bounds is None:
        return marked, batch_id, Vec2(0.0, 0.0)
    intersects = any(
        incoming_bounds.intersects(entity.bounds())
        for entity in existing
        if callable(getattr(entity, "bounds", None))
    )
    if not intersects:
        return marked, batch_id, Vec2(0.0, 0.0)

    delta = Vec2(existing_bounds.max_x + gap - incoming_bounds.min_x, 0.0)
    transform = Affine2D.translation(delta)
    return (
        tuple(entity.transformed(transform) for entity in marked),
        batch_id,
        delta,
    )


__all__ = ["prepare_import_batch"]
