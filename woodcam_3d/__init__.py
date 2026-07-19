"""Núcleo puro de usinagem 3D do WoodCAM.

O pacote não depende de Qt nem de FreeCAD. Adaptadores convertem uma malha
selecionada em :class:`HeightField`; as estratégias retornam o mesmo contrato
de movimentos já consumido pela prévia, simulação e pelo pós-processador.
"""

from .surface import (
    HeightField,
    MeshData,
    expand_height_field_support,
    extend_height_field,
    height_field_from_grayscale,
    height_field_from_mesh,
)
from .toolpaths import (
    FinishingOptions,
    RoughingOptions,
    build_3d_finishing_moves,
    build_3d_roughing_moves,
    compensated_height_field,
)

__all__ = [
    "FinishingOptions",
    "HeightField",
    "MeshData",
    "RoughingOptions",
    "build_3d_finishing_moves",
    "build_3d_roughing_moves",
    "compensated_height_field",
    "expand_height_field_support",
    "extend_height_field",
    "height_field_from_grayscale",
    "height_field_from_mesh",
]
