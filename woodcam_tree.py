"""Organizacao unica dos objetos WoodCAM na arvore do FreeCAD.

Os nomes internos antigos continuam sendo reconhecidos para que documentos
FCStd existentes sejam migrados sem recriar geometria.  A organizacao visual
de producao possui uma unica raiz ``WoodCAM`` e tres pastas funcionais.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ROOT_NAME = "WoodCAM"
ROOT_LABEL = "WoodCAM"
PARTS_GROUP_NAME = "WoodCAM_Parts"
PARTS_GROUP_LABEL = "Peças"
OPERATIONS_GROUP_NAME = "WoodCAM2D_Operations"
OPERATIONS_GROUP_LABEL = "Operações"
WORK_AREA_GROUP_NAME = "WoodCAM2D_WorkArea"
WORK_AREA_GROUP_LABEL = "Área de trabalho"

LEGACY_PARTS_GROUP_NAMES = (
    "WoodCAM2D_VectorDrawing",
    "WoodCAM2DPanelNestExchange",
)
TRANSIENT_OPERATION_NAMES = (
    "WoodCAM2D_Simulation",
)


@dataclass(frozen=True)
class WoodCAMTree:
    root: Any
    parts: Any
    operations: Any
    work_area: Any


def _ensure_group(document: Any, name: str, label: str) -> Any:
    group = document.getObject(name)
    if group is None:
        group = document.addObject("App::DocumentObjectGroup", name)
    group.Label = label
    return group


def _copy_dynamic_properties(source: Any, destination: Any) -> None:
    """Preserva o manifesto do intercâmbio antigo ao compactar sua pasta."""

    for property_name in list(getattr(source, "PropertiesList", ()) or ()):
        if not str(property_name).startswith("WoodCAM"):
            continue
        try:
            property_type = source.getTypeIdOfProperty(property_name)
            if property_name not in list(getattr(destination, "PropertiesList", ()) or ()):
                group_name = source.getGroupOfProperty(property_name) or "WoodCAM"
                destination.addProperty(property_type, property_name, group_name)
            setattr(destination, property_name, getattr(source, property_name))
        except Exception:
            # A organizacao da arvore nunca pode impedir a abertura de um
            # documento antigo por causa de uma propriedade desconhecida.
            continue


def _move_children(document: Any, source: Any, destination: Any) -> None:
    if source is None or source is destination:
        return
    for child in list(getattr(source, "Group", ()) or ()):
        destination.addObject(child)
    _copy_dynamic_properties(source, destination)
    try:
        document.removeObject(source.Name)
    except Exception:
        # Se outra extensao do FreeCAD ainda reivindicar o grupo, deixamos o
        # objeto intacto; os filhos ja estao na pasta canonica.
        pass


def ensure_woodcam_tree(document: Any) -> WoodCAMTree:
    """Cria/migra a arvore sem tocar em Shape, Sketch ou geometria vetorial."""

    if document is None:
        raise ValueError("Documento FreeCAD ausente.")

    root = _ensure_group(document, ROOT_NAME, ROOT_LABEL)
    parts = _ensure_group(document, PARTS_GROUP_NAME, PARTS_GROUP_LABEL)
    operations = _ensure_group(
        document,
        OPERATIONS_GROUP_NAME,
        OPERATIONS_GROUP_LABEL,
    )
    work_area = _ensure_group(
        document,
        WORK_AREA_GROUP_NAME,
        WORK_AREA_GROUP_LABEL,
    )

    for legacy_name in LEGACY_PARTS_GROUP_NAMES:
        legacy = document.getObject(legacy_name)
        if legacy is not None and legacy is not parts:
            _move_children(document, legacy, parts)

    # Documentos criados antes da árvore única deixavam a simulação na raiz
    # global do FreeCAD. Reparentear é somente organização visual: os objetos,
    # Shapes e movimentos permanecem intactos.
    for transient_name in TRANSIENT_OPERATION_NAMES:
        transient = document.getObject(transient_name)
        if transient is not None and transient is not operations:
            operations.addObject(transient)

    canonical = (parts, operations, work_area)
    for child in list(getattr(root, "Group", ()) or ()):
        if child not in canonical:
            parts.addObject(child)
    # ``addObject`` conserva a ordem histórica dos filhos já presentes. A
    # atribuição explícita mantém a árvore previsível também em documentos nos
    # quais Área de trabalho foi criada antes da primeira operação.
    root.Group = list(canonical)
    return WoodCAMTree(root, parts, operations, work_area)
