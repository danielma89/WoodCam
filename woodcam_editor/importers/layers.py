"""Pure mapping from source-layer descriptors to document-local Layer IDs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any, Mapping

from woodcam_editor.importers.part_shape import ImportLayerDescriptor, ImportResult


@dataclass(frozen=True)
class LayerRemapResult:
    entities: tuple[Any, ...]
    layers: tuple[Any, ...]
    source_to_layer_id: Mapping[str, str]

    def __iter__(self):
        # Allows the ergonomic ``entities, layers = remap_import_layers(...)``.
        yield self.entities
        yield self.layers


def _source_layer_key(entity: Any) -> str:
    metadata = dict(getattr(entity, "metadata", {}) or {})
    return str(metadata.get("source_layer_key", "") or "")


def _unique_layer_id(
    source_key: str,
    source_fingerprint: str,
    occupied: set[str],
) -> str:
    digest = hashlib.sha1(
        (str(source_fingerprint) + "\0" + str(source_key)).encode("utf-8")
    ).hexdigest()[:12]
    base = "layer-import-" + digest
    candidate = base
    suffix = 2
    while candidate in occupied:
        candidate = f"{base}-{suffix}"
        suffix += 1
    occupied.add(candidate)
    return candidate


def remap_import_layers(
    result: ImportResult,
    vector_document: Any,
) -> LayerRemapResult:
    """Return remapped copies and new Layers without mutating either input.

    Old importers/results that do not expose source layers remain compatible:
    their entities are returned unchanged and no Layers are created.
    """

    from woodcam_editor.domain.document import LAYER_PURPOSES, Layer

    descriptors = dict(getattr(result, "layers", {}) or {})
    for entity in result.entities:
        source_key = _source_layer_key(entity)
        if source_key and source_key not in descriptors:
            descriptors[source_key] = ImportLayerDescriptor(source_key, source_key)
    if not descriptors:
        return LayerRemapResult(tuple(result.entities), (), {})

    existing_layers = dict(getattr(vector_document, "layers_by_id", {}) or {})
    occupied_ids = set(existing_layers)
    source_fingerprint = str(
        dict(getattr(result, "source_metadata", {}) or {}).get("source_fingerprint", "")
        or dict(getattr(result, "source_metadata", {}) or {}).get("source_path", "")
        or "import"
    )
    next_order = max(
        (int(getattr(layer, "order", 0) or 0) for layer in existing_layers.values()),
        default=-1,
    ) + 1
    new_layers = []
    source_to_layer_id = {}
    for offset, (source_key, descriptor) in enumerate(descriptors.items()):
        layer_id = _unique_layer_id(source_key, source_fingerprint, occupied_ids)
        purpose = descriptor.purpose if descriptor.purpose in LAYER_PURPOSES else "design"
        metadata = dict(descriptor.metadata)
        metadata.update(
            {
                "source_layer_key": source_key,
                "source_layer_name": descriptor.name,
                "source_kind": dict(result.source_metadata).get("source_kind", ""),
            }
        )
        new_layers.append(
            Layer(
                id=layer_id,
                name=descriptor.name,
                color=descriptor.color or "#2563eb",
                visible=bool(metadata.get("visible", True)),
                locked=False,
                order=next_order + offset,
                purpose=purpose,
                metadata=metadata,
            )
        )
        source_to_layer_id[source_key] = layer_id

    remapped_entities = []
    for entity in result.entities:
        source_key = _source_layer_key(entity)
        target_layer_id = source_to_layer_id.get(source_key)
        if not target_layer_id:
            remapped_entities.append(entity)
            continue
        metadata = dict(getattr(entity, "metadata", {}) or {})
        metadata["source_layer_key"] = source_key
        remapped_entities.append(
            replace(entity, layer_id=target_layer_id, metadata=metadata)
        )
    return LayerRemapResult(
        tuple(remapped_entities),
        tuple(new_layers),
        dict(source_to_layer_id),
    )
