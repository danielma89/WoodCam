import json
from dataclasses import replace

try:
    import FreeCAD as App
except ImportError:
    App = None

try:
    import FreeCADGui as Gui
except ImportError:
    Gui = None

from .constants import (
    CONFIG_OBJECT_NAME,
    PART_LABEL_PATTERN,
    PART_PROPERTY_GROUP,
    CUT_METHOD_OPTIONS,
    GRAIN_DIRECTION_OPTIONS,
    DEFAULT_APPLY_DIALOG_PREFS,
    DEFAULT_SHEET_LENGTH_MM,
    DEFAULT_SHEET_WIDTH_MM,
    DEFAULT_SHEET_MARGIN_MM,
    DEFAULT_SHEET_SPACING_MM,
    DEFAULT_FULL_SHEET_COUNT,
    DEFAULT_CNC_KERF_MM,
    DEFAULT_CNC_TECH_MARGIN_MM,
    DEFAULT_SAW_KERF_MM,
    DEFAULT_SAW_TECH_MARGIN_MM,
    DEFAULT_CNC_LAYOUT_STRATEGY,
    DEFAULT_CNC_NESTING_MODE,
    DEFAULT_SAW_LAYOUT_STRATEGY,
    DEFAULT_CNC_ORIGIN_CORNER,
    INTERNAL_PROPERTY_NAME,
)
from .models import PanelPart
from .freecad_utils import (
    _has_property,
    _mark_internal_object,
    _is_internal_object,
    _ensure_float_property_with_default,
    _ensure_int_property_with_default,
    _ensure_bool_property_with_default,
    _ensure_string_property_with_default,
    _ensure_string_property,
    _ensure_float_property,
    _ensure_bool_property,
    _ensure_enum_property,
    ensure_document,
    _add_object_to_group,
)
from .geometry import (
    _object_dimensions_and_quantity,
    get_part_source_objects,
    _dedupe_objects,
)
from .metadata import (
    _material_value,
    _safe_material,
    _safe_cut_method,
    _safe_allow_rotation,
    _safe_grain_direction,
    _safe_grain_rotated,
    _safe_grain_match_group,
    _safe_hardware,
    _material_sort_value,
    _cut_method_sort_value,
    normalize_cut_method,
    normalize_grain_direction,
)
from .edge_band import (
    _edge_band_flags,
    refresh_part_edge_band_visuals,
    _set_object_occurrence_edge_band_map,
)

def ensure_sheet_config():
    doc = ensure_document()
    config = doc.getObject(CONFIG_OBJECT_NAME)
    if config is None:
        config = doc.addObject("App::FeaturePython", CONFIG_OBJECT_NAME)
        config.Label = "Configuracao PanelNest"

    _mark_internal_object(config, "config")
    _ensure_float_property_with_default(
        config,
        "PanelNestSheetLengthMm",
        "Comprimento padrao da chapa em milimetros",
        DEFAULT_SHEET_LENGTH_MM,
    )
    _ensure_float_property_with_default(
        config,
        "PanelNestSheetWidthMm",
        "Largura padrao da chapa em milimetros",
        DEFAULT_SHEET_WIDTH_MM,
    )
    _ensure_float_property_with_default(
        config,
        "PanelNestSheetMarginMm",
        "Margem externa usada no layout inicial",
        DEFAULT_SHEET_MARGIN_MM,
    )
    _ensure_float_property_with_default(
        config,
        "PanelNestSheetSpacingMm",
        "Espacamento entre pecas no layout inicial",
        DEFAULT_SHEET_SPACING_MM,
    )
    _ensure_int_property_with_default(
        config,
        "PanelNestFullSheetCount",
        "Quantidade de chapas inteiras disponiveis antes de abrir novas",
        DEFAULT_FULL_SHEET_COUNT,
    )
    _ensure_bool_property_with_default(
        config,
        "PanelNestAllowExtraFullSheets",
        "Permitir abrir novas chapas inteiras automaticamente",
        True,
    )
    _ensure_float_property_with_default(
        config,
        "PanelNestFullSheetThicknessMm",
        "Espessura padrao das chapas inteiras disponiveis; 0 significa generico",
        0.0,
    )
    _ensure_string_property_with_default(
        config,
        "PanelNestFullSheetMaterial",
        "Material padrao das chapas inteiras disponiveis",
        "",
    )
    _ensure_float_property_with_default(
        config,
        "PanelNestCncKerfMm",
        "Espessura de corte aplicada ao perfil CNC",
        DEFAULT_CNC_KERF_MM,
    )
    _ensure_float_property_with_default(
        config,
        "PanelNestCncTechMarginMm",
        "Margem tecnica adicional aplicada ao perfil CNC",
        DEFAULT_CNC_TECH_MARGIN_MM,
    )
    _ensure_float_property_with_default(
        config,
        "PanelNestSawKerfMm",
        "Espessura de corte aplicada ao perfil Serra",
        DEFAULT_SAW_KERF_MM,
    )
    _ensure_float_property_with_default(
        config,
        "PanelNestSawTechMarginMm",
        "Margem tecnica adicional aplicada ao perfil Serra",
        DEFAULT_SAW_TECH_MARGIN_MM,
    )
    _ensure_string_property_with_default(
        config,
        "PanelNestCncLayoutStrategy",
        "Estrategia de layout usada pelo perfil CNC",
        DEFAULT_CNC_LAYOUT_STRATEGY,
    )
    _ensure_string_property_with_default(
        config,
        "PanelNestCncNestingMode",
        "Modo de nesting CNC: rapido retangular ou otimizado pelo contorno real",
        DEFAULT_CNC_NESTING_MODE,
    )
    _ensure_string_property_with_default(
        config,
        "PanelNestSawLayoutStrategy",
        "Estrategia de layout usada pelo perfil Serra",
        DEFAULT_SAW_LAYOUT_STRATEGY,
    )
    _ensure_bool_property_with_default(
        config,
        "PanelNestCncCommonLine",
        "Common-Line: pecas compartilham linha de corte (sem kerf entre elas)",
        False,
    )
    _ensure_bool_property_with_default(
        config,
        "PanelNestCncStayDown",
        "Stay-Down: router nao levanta entre passes adjacentes",
        False,
    )
    _ensure_string_property_with_default(
        config,
        "PanelNestCncOriginCorner",
        "Canto de origem do CNC (inferior_esquerdo, inferior_direito, superior_esquerdo, superior_direito)",
        DEFAULT_CNC_ORIGIN_CORNER,
    )
    _ensure_bool_property_with_default(
        config,
        "PanelNestShowLayoutPartDimensions",
        "Mostrar medidas das pecas no layout",
        True,
    )
    _ensure_bool_property_with_default(
        config,
        "PanelNestShowLayoutPartLabels",
        "Mostrar etiquetas das pecas no layout",
        True,
    )
    _ensure_bool_property_with_default(
        config,
        "PanelNestShowLayoutEdgeBands",
        "Mostrar marcacoes de fita de borda nas pecas do layout",
        True,
    )
    _ensure_string_property_with_default(
        config,
        "PanelNestRemnantsJson",
        "Lista JSON de retalhos disponiveis",
        "[]",
    )
    _ensure_string_property_with_default(
        config,
        "PanelNestApplyDialogJson",
        "Preferencias da janela Aplicar Dados",
        json.dumps(DEFAULT_APPLY_DIALOG_PREFS, ensure_ascii=True),
    )
    _ensure_string_property_with_default(
        config,
        "PanelNestAssemblyGuidePublicBaseUrl",
        "URL publica base do guia de montagem usada nos QRs das etiquetas",
        "",
    )
    _ensure_string_property_with_default(
        config,
        "PanelNestAssemblyGuideCloudflareProjectName",
        "Nome do projeto Cloudflare Pages usado para publicar o guia de montagem",
        "",
    )
    _ensure_bool_property_with_default(
        config,
        "PanelNestAssemblyGuideCloudflareAutoPublish",
        "Publicar automaticamente o guia de montagem no Cloudflare Pages via Wrangler",
        False,
    )
    _ensure_bool_property_with_default(
        config,
        "PanelNestAssemblyGuidePreserveCurrentVisual",
        "Preservar o visual atual do FreeCAD nas capturas do guia de montagem",
        False,
    )
    _ensure_float_property_with_default(
        config,
        "PanelNestEdgeBandThicknessMm",
        "Espessura da fita de borda para compensacao dimensional",
        0.0,
    )
    _ensure_string_property_with_default(
        config,
        "PanelNestEdgeBandLabel",
        "Descricao da fita de borda usada no projeto (ex: Fita PVC 22mm Branca)",
        "",
    )
    _ensure_string_property_with_default(
        config,
        "PanelNestHardwareCatalogJson",
        "Catalogo de ferragens do projeto em JSON (dobricas, corrediclas, parafusos...)",
        "[]",
    )
    _ensure_float_property_with_default(
        config,
        "PanelNestEdgeBandPricePerM",
        "Preco da fita de borda por metro linear em reais",
        0.0,
    )
    _ensure_float_property_with_default(
        config,
        "PanelNestEdgeBandWasteFactor",
        "Fator de desperdicio da fita de borda (0.10 = 10%)",
        0.10,
    )
    _ensure_float_property_with_default(
        config,
        "PanelNestFullSheetCost",
        "Custo por chapa inteira em reais",
        0.0,
    )
    _ensure_string_property_with_default(
        config,
        "PanelNestCompanyName",
        "Nome da empresa para cabecalhos de documentos",
        "",
    )
    _ensure_string_property_with_default(
        config,
        "PanelNestCompanyContact",
        "Contato da empresa para cabecalhos de documentos",
        "",
    )
    _ensure_string_property_with_default(
        config,
        "PanelNestCompanyAddress",
        "Endereco da empresa para cabecalhos de documentos",
        "",
    )
    _ensure_string_property_with_default(
        config,
        "PanelNestProjectClient",
        "Nome do cliente do projeto",
        "",
    )
    _ensure_string_property_with_default(
        config,
        "PanelNestProjectResponsible",
        "Responsavel tecnico pelo projeto",
        "",
    )
    _ensure_string_property_with_default(
        config,
        "PanelNestProjectNotes",
        "Observacoes gerais do projeto",
        "",
    )
    return config



def _is_user_project_container(obj):
    if obj is None or _is_internal_object(obj):
        return False

    type_id = str(getattr(obj, "TypeId", "") or "")
    if type_id in {
        "App::Part",
        "App::DocumentObjectGroup",
        "App::DocumentObjectGroupPython",
        "App::LinkGroup",
    }:
        return True
    return type_id.startswith("Assembly::")


def _ancestor_user_project_containers(obj):
    containers = []
    stack = [(parent, 1) for parent in getattr(obj, "InList", [])]
    seen = set()

    while stack:
        parent, depth = stack.pop()
        name = getattr(parent, "Name", None)
        if not name or name in seen:
            continue
        seen.add(name)

        if _is_user_project_container(parent):
            containers.append((depth, parent))

        for grandparent in getattr(parent, "InList", []):
            stack.append((grandparent, depth + 1))

    return containers


def _top_user_project_container(obj):
    containers = _ancestor_user_project_containers(obj)
    if not containers:
        return None

    containers.sort(key=lambda item: (item[0], getattr(item[1], "Name", "")))
    return containers[-1][1]


def _next_project_container_identity(doc):
    doc_label = str(getattr(doc, "Label", "") or getattr(doc, "Name", "") or "").strip()
    if doc_label.lower() in {"", "sem nome", "unnamed", "untitled"}:
        doc_label = ""

    index = 1
    while True:
        object_name = f"Projeto{index:02d}"
        if doc.getObject(object_name) is None:
            if doc_label:
                label = f"Projeto - {doc_label}" if index == 1 else f"Projeto {index:02d} - {doc_label}"
            else:
                label = f"Projeto {index:02d}"
            return object_name, label
        index += 1


def _ensure_project_container_for_labeled_objects(source_objects):
    source_objects = _dedupe_objects(source_objects)
    if len(source_objects) <= 1:
        return None

    container_by_object = {obj.Name: _top_user_project_container(obj) for obj in source_objects}
    existing_containers = [container for container in container_by_object.values() if container is not None]

    if existing_containers and len(existing_containers) != len(source_objects):
        if App is not None:
            App.Console.PrintMessage(
                "PanelNest: nao criou container automatico porque a selecao mistura pecas soltas "
                "e pecas que ja pertencem a um projeto.\n"
            )
        return None

    if existing_containers:
        return None

    doc = source_objects[0].Document if source_objects and getattr(source_objects[0], "Document", None) else ensure_document()
    object_name, label = _next_project_container_identity(doc)
    container = doc.addObject("App::Part", object_name)
    container.Label = label

    for obj in source_objects:
        _add_object_to_group(container, obj)

    doc.recompute()

    if App is not None:
        App.Console.PrintMessage(
            f'PanelNest: agrupou {len(source_objects)} peca(s) no container "{container.Label}".\n'
        )
    return container



def extract_part(obj, index):
    from .geometry import extract_object_holes, extract_part_profile
    length_mm, width_mm, thickness_mm, quantity = _object_dimensions_and_quantity(obj)
    edge_band_top, edge_band_bottom, edge_band_left, edge_band_right = _edge_band_flags(obj)
    try:
        holes = extract_object_holes(obj)
    except Exception:
        holes = []
    try:
        profile = extract_part_profile(obj) or []
    except Exception:
        profile = []
    hardware = _safe_hardware(obj)
    return PanelPart(
        part_id=f"PN-{index:03d}",
        object_name=obj.Name,
        label=getattr(obj, "Label", obj.Name),
        length_mm=length_mm,
        width_mm=width_mm,
        thickness_mm=thickness_mm,
        quantity=quantity,
        material=_safe_material(obj),
        cut_method=_safe_cut_method(obj),
        allow_rotation=_safe_allow_rotation(obj),
        grain_direction=_safe_grain_direction(obj),
        grain_rotated=_safe_grain_rotated(obj),
        grain_match_group=_safe_grain_match_group(obj),
        edge_band_top=edge_band_top,
        edge_band_bottom=edge_band_bottom,
        edge_band_left=edge_band_left,
        edge_band_right=edge_band_right,
        source_type=getattr(obj, "TypeId", ""),
        holes=holes,
        hardware=hardware,
        profile_points=profile,
    )


def _part_group_key(part):
    return (
        _material_sort_value(part.material),
        round(part.thickness_mm, 4),
        _cut_method_sort_value(part.cut_method),
    )


def _part_sort_key(part):
    return (
        _part_group_key(part),
        -part.length_mm,
        -part.width_mm,
        0 if part.allow_rotation else 1,
        GRAIN_DIRECTION_OPTIONS.index(normalize_grain_direction(part.grain_direction)),
        _base_label_for_sort(part.label),
        part.object_name.casefold(),
    )


def _base_label_for_sort(label):
    return PART_LABEL_PATTERN.sub("", label).casefold()


def organize_parts(parts):
    ordered_parts = sorted(parts, key=_part_sort_key)
    for index, part in enumerate(ordered_parts, start=1):
        part.part_id = f"PN-{index:03d}"
    return ordered_parts


def group_parts(parts):
    groups = []
    current_group = None

    for part in organize_parts(list(parts)):
        group_key = _part_group_key(part)
        if current_group is None or current_group["key"] != group_key:
            current_group = {
                "key": group_key,
                "material": _material_value(part.material) or "Sem material",
                "thickness_mm": part.thickness_mm,
                "cut_method": normalize_cut_method(part.cut_method),
                "parts": [],
            }
            groups.append(current_group)
        current_group["parts"].append(part)

    for index, group in enumerate(groups, start=1):
        group["group_id"] = f"GRP-{index:02d}"

    return groups


def collect_parts(objects=None, include_hidden=False):
    source_objects = get_part_source_objects(objects, include_hidden=include_hidden)
    parts = [extract_part(obj, index) for index, obj in enumerate(source_objects, start=1)]
    return organize_parts(parts)


def _has_property(obj, property_name):
    return property_name in getattr(obj, "PropertiesList", [])


def _ensure_float_property_with_default(obj, property_name, description, default_value):
    if not _has_property(obj, property_name):
        obj.addProperty("App::PropertyFloat", property_name, PART_PROPERTY_GROUP, description)
        setattr(obj, property_name, float(default_value))


def _ensure_int_property_with_default(obj, property_name, description, default_value):
    if not _has_property(obj, property_name):
        obj.addProperty("App::PropertyInteger", property_name, PART_PROPERTY_GROUP, description)
        setattr(obj, property_name, int(default_value))


def _ensure_bool_property_with_default(obj, property_name, description, default_value):
    if not _has_property(obj, property_name):
        obj.addProperty("App::PropertyBool", property_name, PART_PROPERTY_GROUP, description)
        setattr(obj, property_name, bool(default_value))


def _ensure_string_property_with_default(obj, property_name, description, default_value):
    if not _has_property(obj, property_name):
        obj.addProperty("App::PropertyString", property_name, PART_PROPERTY_GROUP, description)
        setattr(obj, property_name, str(default_value))


def _ensure_string_property(obj, property_name, description):
    if not _has_property(obj, property_name):
        obj.addProperty("App::PropertyString", property_name, PART_PROPERTY_GROUP, description)


def _ensure_float_property(obj, property_name, description):
    if not _has_property(obj, property_name):
        obj.addProperty("App::PropertyFloat", property_name, PART_PROPERTY_GROUP, description)


def _ensure_bool_property(obj, property_name, description):
    if not _has_property(obj, property_name):
        obj.addProperty("App::PropertyBool", property_name, PART_PROPERTY_GROUP, description)


def _ensure_enum_property(obj, property_name, description, values, default_value):
    if not _has_property(obj, property_name):
        obj.addProperty("App::PropertyEnumeration", property_name, PART_PROPERTY_GROUP, description)

    current_value = getattr(obj, property_name, default_value)
    setattr(obj, property_name, values)
    setattr(obj, property_name, current_value if current_value in values else default_value)


def _mark_internal_object(obj, internal_type):
    if not _has_property(obj, INTERNAL_PROPERTY_NAME):
        obj.addProperty(
            "App::PropertyString",
            INTERNAL_PROPERTY_NAME,
            PART_PROPERTY_GROUP,
            "Uso interno do PanelNest",
        )
    setattr(obj, INTERNAL_PROPERTY_NAME, internal_type)


def _is_internal_object(obj):
    return _has_property(obj, INTERNAL_PROPERTY_NAME) and bool(
        getattr(obj, INTERNAL_PROPERTY_NAME, "")
    )


def ensure_part_properties(obj):
    _ensure_string_property(obj, "PanelNestId", "Identificador de peca gerado pelo PanelNest")
    _ensure_string_property(obj, "PanelNestBaseLabel", "Rotulo original do objeto antes da etiquetagem")
    _ensure_string_property(obj, "PanelNestMaterial", "Nome do material da peca, por exemplo MDF 18mm")
    _ensure_string_property(obj, "PanelNestHardwareJson", "Ferragens associadas a esta peca (JSON)")
    _ensure_float_property(obj, "PanelNestLengthMm", "Comprimento detectado da peca em milimetros")
    _ensure_float_property(obj, "PanelNestWidthMm", "Largura detectada da peca em milimetros")
    _ensure_float_property(obj, "PanelNestThicknessMm", "Espessura detectada da peca em milimetros")
    _ensure_enum_property(
        obj,
        "PanelNestCutMethod",
        "Processo de corte preferencial para esta peca",
        CUT_METHOD_OPTIONS,
        CUT_METHOD_OPTIONS[0],
    )
    _ensure_bool_property_with_default(
        obj,
        "PanelNestAllowRotation",
        "Permitir girar a peca durante o layout",
        True,
    )
    _ensure_enum_property(
        obj,
        "PanelNestGrainDirection",
        "Sentido do veio/fibra em relacao a chapa",
        GRAIN_DIRECTION_OPTIONS,
        GRAIN_DIRECTION_OPTIONS[0],
    )
    _ensure_string_property(obj, "PanelNestGrainMatchGroup", "Grupo de alinhamento de veio (pecas adjacentes na chapa)")
    _ensure_bool_property(obj, "PanelNestEdgeBandTop", "Aplicar fita de borda no lado superior")
    _ensure_bool_property(obj, "PanelNestEdgeBandBottom", "Aplicar fita de borda no lado inferior")
    _ensure_bool_property(obj, "PanelNestEdgeBandLeft", "Aplicar fita de borda no lado esquerdo")
    _ensure_bool_property(obj, "PanelNestEdgeBandRight", "Aplicar fita de borda no lado direito")
    _ensure_string_property_with_default(
        obj,
        "PanelNestEdgeBandOccurrencesJson",
        "Uso interno: fitas por ocorrencia em patterns",
        "",
    )
    _ensure_string_property_with_default(
        obj,
        "PanelNestVisualBaseShapeColorJson",
        "Uso interno: cor base antes do destaque de fita",
        "",
    )
    _ensure_string_property_with_default(
        obj,
        "PanelNestVisualBaseDiffuseColorsJson",
        "Uso interno: cores por face antes do destaque de fita",
        "",
    )
    _ensure_string_property_with_default(
        obj,
        "PanelNestEdgeBandMaterial",
        "Material da fita de borda (nome para lookup de cor visual)",
        "",
    )
    _ensure_string_property_with_default(
        obj,
        "PanelNestEdgeBandMaterialJson",
        "Uso interno: cor da fita por lado {top,bottom,left,right: nome_material}",
        "",
    )
    _ensure_bool_property_with_default(
        obj,
        "PanelNestGrainRotated",
        "Veio girado 90 graus em relacao ao padrao do material",
        False,
    )


def _base_label_for_object(obj):
    if _has_property(obj, "PanelNestBaseLabel") and getattr(obj, "PanelNestBaseLabel", ""):
        return obj.PanelNestBaseLabel

    label = getattr(obj, "Label", obj.Name)
    return PART_LABEL_PATTERN.sub("", label)


def apply_part_labels(objects=None):
    doc = ensure_document()
    source_objects = get_part_source_objects(objects)
    parts = prepare_parts_metadata(source_objects)
    if not parts:
        raise ValueError(
            "Nenhuma peca visivel valida foi encontrada. Selecione solidos ou deixe visiveis apenas as pecas desejadas."
        )

    for part in parts:
        obj = doc.getObject(part.object_name)
        if obj is None:
            continue

        ensure_part_properties(obj)
        base_label = _base_label_for_object(obj)
        obj.PanelNestBaseLabel = base_label
        obj.PanelNestId = part.part_id
        obj.PanelNestLengthMm = part.length_mm
        obj.PanelNestWidthMm = part.width_mm
        obj.PanelNestThicknessMm = part.thickness_mm
        obj.Label = f"{part.part_id} - {base_label}"

    doc.recompute()
    _ensure_project_container_for_labeled_objects(source_objects)
    return parts


def prepare_parts_metadata(objects=None):
    doc = ensure_document()
    source_objects = get_part_source_objects(objects)
    if not source_objects:
        raise ValueError(
            "Nenhuma peca visivel valida foi encontrada. Selecione solidos ou deixe visiveis apenas as pecas desejadas."
        )

    for index, obj in enumerate(source_objects, start=1):
        ensure_part_properties(obj)
        length_mm, width_mm, thickness_mm, _quantity = _object_dimensions_and_quantity(obj)

        if not getattr(obj, "PanelNestBaseLabel", ""):
            obj.PanelNestBaseLabel = PART_LABEL_PATTERN.sub("", getattr(obj, "Label", obj.Name))
        if not getattr(obj, "PanelNestMaterial", ""):
            obj.PanelNestMaterial = "MDF"
        if not getattr(obj, "PanelNestGrainDirection", ""):
            obj.PanelNestGrainDirection = GRAIN_DIRECTION_OPTIONS[0]

        obj.PanelNestLengthMm = length_mm
        obj.PanelNestWidthMm = width_mm
        obj.PanelNestThicknessMm = thickness_mm

    doc.recompute()
    refresh_part_edge_band_visuals(source_objects)
    return collect_parts(source_objects)


def apply_metadata_values(
    objects=None,
    material=None,
    cut_method=None,
    allow_rotation=None,
    grain_direction=None,
    edge_band_top=None,
    edge_band_bottom=None,
    edge_band_left=None,
    edge_band_right=None,
):
    doc = ensure_document()
    source_objects = get_part_source_objects(objects)
    if not source_objects:
        raise ValueError(
            "Nenhuma peca visivel valida foi encontrada. Selecione paineis solidos ou deixe visiveis apenas as pecas desejadas."
        )

    for obj in source_objects:
        ensure_part_properties(obj)
        should_reset_occurrence_map = any(
            value is not None
            for value in (edge_band_top, edge_band_bottom, edge_band_left, edge_band_right)
        )

        if material is not None:
            # Não sobrescreve se a peça já tem um material visual aplicado via paleta
            existing_mat = str(getattr(obj, "PanelNestMaterial", "") or "").strip()
            _has_visual_material = False
            if existing_mat:
                try:
                    from panelnest.materials import get_by_name
                    _has_visual_material = get_by_name(existing_mat) is not None
                except Exception:
                    pass
            if not _has_visual_material:
                obj.PanelNestMaterial = material
        if cut_method is not None:
            obj.PanelNestCutMethod = normalize_cut_method(cut_method)
        if allow_rotation is not None:
            obj.PanelNestAllowRotation = bool(allow_rotation)
        if grain_direction is not None:
            obj.PanelNestGrainDirection = normalize_grain_direction(grain_direction)
        if edge_band_top is not None:
            obj.PanelNestEdgeBandTop = bool(edge_band_top)
        if edge_band_bottom is not None:
            obj.PanelNestEdgeBandBottom = bool(edge_band_bottom)
        if edge_band_left is not None:
            obj.PanelNestEdgeBandLeft = bool(edge_band_left)
        if edge_band_right is not None:
            obj.PanelNestEdgeBandRight = bool(edge_band_right)
        if should_reset_occurrence_map:
            _set_object_occurrence_edge_band_map(obj, {})

        length_mm, width_mm, thickness_mm, _quantity = _object_dimensions_and_quantity(obj)
        obj.PanelNestLengthMm = length_mm
        obj.PanelNestWidthMm = width_mm
        obj.PanelNestThicknessMm = thickness_mm
        if not getattr(obj, "PanelNestBaseLabel", ""):
            obj.PanelNestBaseLabel = PART_LABEL_PATTERN.sub("", getattr(obj, "Label", obj.Name))

    doc.recompute()
    refresh_part_edge_band_visuals(source_objects)
    return collect_parts(source_objects)


def apply_part_rows(row_data):
    doc = ensure_document()
    updated_objects = []

    for row in row_data:
        object_name = row.get("object_name", "")
        obj = doc.getObject(object_name)
        if obj is None:
            continue

        ensure_part_properties(obj)
        obj.PanelNestMaterial = row.get("material", "") or ""
        obj.PanelNestCutMethod = normalize_cut_method(row.get("cut_method", CUT_METHOD_OPTIONS[0]))
        obj.PanelNestAllowRotation = bool(row.get("allow_rotation", True))
        obj.PanelNestGrainDirection = normalize_grain_direction(
            row.get("grain_direction", GRAIN_DIRECTION_OPTIONS[0])
        )
        obj.PanelNestEdgeBandTop = bool(row.get("edge_band_top", False))
        obj.PanelNestEdgeBandBottom = bool(row.get("edge_band_bottom", False))
        obj.PanelNestEdgeBandLeft = bool(row.get("edge_band_left", False))
        obj.PanelNestEdgeBandRight = bool(row.get("edge_band_right", False))
        _set_object_occurrence_edge_band_map(obj, {})

        length_mm, width_mm, thickness_mm, _quantity = _object_dimensions_and_quantity(obj)
        obj.PanelNestLengthMm = length_mm
        obj.PanelNestWidthMm = width_mm
        obj.PanelNestThicknessMm = thickness_mm
        if not getattr(obj, "PanelNestBaseLabel", ""):
            obj.PanelNestBaseLabel = PART_LABEL_PATTERN.sub("", getattr(obj, "Label", obj.Name))

        updated_objects.append(obj)

    doc.recompute()
    refresh_part_edge_band_visuals(updated_objects)
    return collect_parts(updated_objects)


# ---------------------------------------------------------------------------
# Detecção automática de ferragens por diâmetro de furo
# ---------------------------------------------------------------------------

_HOLE_DIAMETER_TOLERANCE_MM = 0.5


def detect_hardware_from_holes(parts, hardware_catalog):
    """Detecta ferragens automaticamente cruzando furos das peças com o catálogo.

    Para cada furo de uma peça, procura no catálogo uma ferragem cujo
    hole_diameter_mm corresponda (dentro de tolerância de ±0.5mm).
    Agrupa por hw_id e conta quantos furos de cada tipo a peça tem.

    Retorna dict: {part.object_name: [{hw_id, qty}, ...]}
    Só inclui peças que tiveram match; não sobrescreve hardware manual
    já existente.
    """
    diameter_map = {}
    for hw in hardware_catalog:
        d = float(getattr(hw, "hole_diameter_mm", 0.0) or 0.0)
        if d > 0:
            diameter_map[d] = getattr(hw, "hw_id", "")

    if not diameter_map:
        return {}

    result = {}
    for part in parts:
        if not part.holes:
            continue
        existing_hw_ids = {
            str(h.get("hw_id", "")) for h in (part.hardware or [])
        }
        hw_counts = {}
        for hole in part.holes:
            hole_d = float(hole.get("diameter_mm", 0))
            if hole_d <= 0:
                continue
            matched_hw_id = None
            for cat_d, cat_hw_id in diameter_map.items():
                if abs(hole_d - cat_d) <= _HOLE_DIAMETER_TOLERANCE_MM:
                    matched_hw_id = cat_hw_id
                    break
            if matched_hw_id and matched_hw_id not in existing_hw_ids:
                hw_counts[matched_hw_id] = hw_counts.get(matched_hw_id, 0) + 1

        if hw_counts:
            result[part.object_name] = [
                {"hw_id": hw_id, "qty": qty}
                for hw_id, qty in sorted(hw_counts.items())
            ]

    return result


def apply_detected_hardware(parts, hardware_catalog):
    """Detecta ferragens por furos e salva nos objetos FreeCAD.

    Retorna (detected_count, part_count) — quantas ferragens detectadas
    e em quantas peças.
    """
    detected = detect_hardware_from_holes(parts, hardware_catalog)
    if not detected:
        return 0, 0

    doc = ensure_document()
    total_hw = 0
    part_count = 0

    for obj_name, hw_list in detected.items():
        obj = doc.getObject(obj_name)
        if obj is None:
            continue

        existing = _safe_hardware(obj)
        existing_ids = {str(h.get("hw_id", "")) for h in existing}

        merged = list(existing)
        added = False
        for hw_entry in hw_list:
            if hw_entry["hw_id"] not in existing_ids:
                merged.append(hw_entry)
                total_hw += 1
                added = True

        if added:
            save_part_hardware(obj, merged)
            part_count += 1

    return total_hw, part_count
