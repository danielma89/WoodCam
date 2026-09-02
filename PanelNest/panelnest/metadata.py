import json
from dataclasses import replace
from urllib.parse import urlparse


def _sheet_usable_area_mm2(length_mm, width_mm, settings, cut_method="Auto"):
    # Lazy import to avoid circular dependency with nesting
    try:
        from .nesting import _sheet_usable_area_mm2 as _nesting_usable_area
        return _nesting_usable_area(length_mm, width_mm, settings, cut_method)
    except ImportError:
        effective_margin_mm = getattr(settings, "margin_mm", 0.0)
        usable_length = length_mm - (2 * effective_margin_mm)
        usable_width = width_mm - (2 * effective_margin_mm)
        return usable_length * usable_width

try:
    import FreeCAD as App
except ImportError:
    App = None

from .constants import (
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
    DEFAULT_SAW_LAYOUT_STRATEGY,
    DEFAULT_CNC_NESTING_MODE,
    DEFAULT_CNC_ORIGIN_CORNER,
    CUT_METHOD_OPTIONS,
    CUT_METHOD_ALIASES,
    CUT_METHOD_SORT_ORDER,
    CNC_LAYOUT_STRATEGY_OPTIONS,
    CNC_LAYOUT_STRATEGY_ALIASES,
    CNC_NESTING_MODE_OPTIONS,
    CNC_NESTING_MODE_ALIASES,
    SAW_LAYOUT_STRATEGY_OPTIONS,
    SAW_LAYOUT_STRATEGY_ALIASES,
    GRAIN_DIRECTION_OPTIONS,
    GRAIN_DIRECTION_ALIASES,
    CLOUDFLARE_PAGES_PROJECT_NAME_PATTERN,
    PART_PROPERTY_GROUP,
)
from .models import SheetSettings, SheetStockPiece
from .freecad_utils import (
    _has_property,
    ensure_document,
    _ensure_float_property_with_default,
    _ensure_int_property_with_default,
    _ensure_bool_property_with_default,
    _ensure_string_property_with_default,
    _mark_internal_object,
)

def _validate_sheet_settings(settings):
    if settings.length_mm <= 0:
        raise ValueError("O comprimento da chapa deve ser maior que zero.")
    if settings.width_mm <= 0:
        raise ValueError("A largura da chapa deve ser maior que zero.")
    if settings.margin_mm < 0:
        raise ValueError("A margem externa da chapa nao pode ser negativa.")
    if settings.spacing_mm < 0:
        raise ValueError("O espacamento entre pecas nao pode ser negativo.")
    if settings.full_sheet_count < 0:
        raise ValueError("A quantidade de chapas inteiras nao pode ser negativa.")
    if settings.full_sheet_thickness_mm < 0:
        raise ValueError("A espessura da chapa inteira nao pode ser negativa.")
    if settings.cnc_kerf_mm < 0:
        raise ValueError("O kerf do perfil CNC nao pode ser negativo.")
    if settings.cnc_tech_margin_mm < 0:
        raise ValueError("A margem tecnica do perfil CNC nao pode ser negativa.")
    if settings.saw_kerf_mm < 0:
        raise ValueError("O kerf do perfil Serra nao pode ser negativo.")
    if settings.saw_tech_margin_mm < 0:
        raise ValueError("A margem tecnica do perfil Serra nao pode ser negativa.")
    if normalize_cnc_layout_strategy(settings.cnc_layout_strategy) not in CNC_LAYOUT_STRATEGY_OPTIONS:
        raise ValueError("A estrategia de layout do perfil CNC esta invalida.")
    if normalize_cnc_nesting_mode(settings.cnc_nesting_mode) not in CNC_NESTING_MODE_OPTIONS:
        raise ValueError("O modo de nesting do perfil CNC esta invalido.")
    if normalize_saw_layout_strategy(settings.saw_layout_strategy) not in SAW_LAYOUT_STRATEGY_OPTIONS:
        raise ValueError("A estrategia de layout do perfil Serra esta invalida.")
    _validate_assembly_guide_public_base_url(settings.assembly_guide_public_base_url)
    _validate_cloudflare_pages_project_name(settings.assembly_guide_cloudflare_project_name)
    resolved_project_name = normalize_cloudflare_pages_project_name(
        settings.assembly_guide_cloudflare_project_name
    )
    explicit_public_base_url = normalize_assembly_guide_public_base_url(
        settings.assembly_guide_public_base_url
    )
    if explicit_public_base_url and resolved_project_name:
        parsed_public_url = urlparse(explicit_public_base_url)
        public_host = str(parsed_public_url.netloc or "").strip().lower()
        expected_pages_host = f"{resolved_project_name}.pages.dev"
        if public_host.endswith(".pages.dev") and public_host != expected_pages_host:
            raise ValueError(
                "A URL publica do guia nao corresponde ao projeto Cloudflare Pages informado."
            )
    if settings.assembly_guide_cloudflare_auto_publish and not resolved_project_name:
        raise ValueError(
            "Preencha o nome do projeto Cloudflare Pages antes de ativar a publicacao automatica."
        )
    usable_length = settings.length_mm - (2 * settings.margin_mm)
    usable_width = settings.width_mm - (2 * settings.margin_mm)
    if usable_length <= 0 or usable_width <= 0:
        raise ValueError("A margem externa ocupa toda a area util da chapa.")
    for remnant in settings.remnants:
        if remnant.length_mm <= 0 or remnant.width_mm <= 0:
            raise ValueError(f"O retalho '{remnant.label or 'Sem nome'}' precisa ter dimensoes positivas.")
        if remnant.quantity <= 0:
            raise ValueError(f"O retalho '{remnant.label or 'Sem nome'}' precisa ter quantidade positiva.")
        if remnant.thickness_mm < 0:
            raise ValueError(f"O retalho '{remnant.label or 'Sem nome'}' nao pode ter espessura negativa.")
        if _sheet_usable_area_mm2(remnant.length_mm, remnant.width_mm, settings) <= 0:
            raise ValueError(
                f"O retalho '{remnant.label or 'Sem nome'}' nao tem area util apos aplicar a margem configurada."
            )


def _deserialize_remnants(value):
    if not value:
        return []

    try:
        raw_items = json.loads(value)
    except Exception as exc:
        raise ValueError("A configuracao de retalhos do PanelNest esta invalida.") from exc

    remnants = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        label = _normalize_stock_label(item.get("label", ""), f"Retalho {index:02d}")
        length_mm = float(item.get("length_mm", 0.0) or 0.0)
        width_mm = float(item.get("width_mm", 0.0) or 0.0)
        thickness_mm = float(item.get("thickness_mm", 0.0) or 0.0)
        material = _material_value(item.get("material", ""))
        quantity = int(item.get("quantity", 1) or 1)
        cost_per_sheet = float(item.get("cost_per_sheet", 0.0) or 0.0)
        db_id_raw = item.get("db_id", None)
        db_id = int(db_id_raw) if db_id_raw is not None else None
        remnants.append(
            SheetStockPiece(
                label=label,
                length_mm=length_mm,
                width_mm=width_mm,
                thickness_mm=thickness_mm,
                material=material,
                quantity=quantity,
                kind="Retalho",
                is_full_sheet=False,
                cost_per_sheet=cost_per_sheet,
                db_id=db_id,
            )
        )
    return remnants


def _serialize_remnants(remnants):
    payload = []
    for index, remnant in enumerate(remnants, start=1):
        if isinstance(remnant, dict):
            label = remnant.get("label", "")
            length_mm = remnant.get("length_mm", 0.0)
            width_mm = remnant.get("width_mm", 0.0)
            thickness_mm = remnant.get("thickness_mm", 0.0)
            material = remnant.get("material", "")
            quantity = remnant.get("quantity", 1)
            cost_per_sheet = remnant.get("cost_per_sheet", 0.0)
        else:
            label = remnant.label
            length_mm = remnant.length_mm
            width_mm = remnant.width_mm
            thickness_mm = remnant.thickness_mm
            material = remnant.material
            quantity = remnant.quantity
            cost_per_sheet = getattr(remnant, "cost_per_sheet", 0.0)
        if isinstance(remnant, dict):
            db_id = remnant.get("db_id", None)
        else:
            db_id = getattr(remnant, "db_id", None)
        entry = {
            "label": _normalize_stock_label(label, f"Retalho {index:02d}"),
            "length_mm": float(length_mm),
            "width_mm": float(width_mm),
            "thickness_mm": float(thickness_mm),
            "material": _material_value(material),
            "quantity": int(quantity),
            "cost_per_sheet": float(cost_per_sheet) if cost_per_sheet else 0.0,
        }
        if db_id is not None:
            entry["db_id"] = int(db_id)
        payload.append(entry)
    return json.dumps(payload, ensure_ascii=True)


def _default_hardware_catalog():
    """Retorna o catálogo padrão de ferragens com detecção por furo."""
    from .models import DEFAULT_HARDWARE_CATALOG
    import copy
    return copy.deepcopy(DEFAULT_HARDWARE_CATALOG)


def _deserialize_hardware_catalog(value):
    """Deserializa o catálogo de ferragens de JSON para lista de HardwareItem."""
    from .models import HardwareItem
    if not value:
        return []
    try:
        raw = json.loads(value)
    except Exception:
        return []
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        hw_id = str(item.get("hw_id", "") or "").strip()
        name = str(item.get("name", "") or "").strip()
        if not hw_id or not name:
            continue
        result.append(HardwareItem(
            hw_id=hw_id,
            name=name,
            unit=str(item.get("unit", "un") or "un"),
            cost=float(item.get("cost", 0.0) or 0.0),
            hole_diameter_mm=float(item.get("hole_diameter_mm", 0.0) or 0.0),
        ))
    return result


def _serialize_hardware_catalog(catalog):
    """Serializa lista de HardwareItem (ou dicts) para JSON."""
    payload = []
    for item in (catalog or []):
        if isinstance(item, dict):
            hw_id = str(item.get("hw_id", "") or "").strip()
            name = str(item.get("name", "") or "").strip()
            unit = str(item.get("unit", "un") or "un")
            cost = float(item.get("cost", 0.0) or 0.0)
        else:
            hw_id = str(getattr(item, "hw_id", "") or "").strip()
            name = str(getattr(item, "name", "") or "").strip()
            unit = str(getattr(item, "unit", "un") or "un")
            cost = float(getattr(item, "cost", 0.0) or 0.0)
        if isinstance(item, dict):
            hole_d = float(item.get("hole_diameter_mm", 0.0) or 0.0)
        else:
            hole_d = float(getattr(item, "hole_diameter_mm", 0.0) or 0.0)
        if hw_id and name:
            entry = {"hw_id": hw_id, "name": name, "unit": unit, "cost": cost}
            if hole_d > 0:
                entry["hole_diameter_mm"] = hole_d
            payload.append(entry)
    return json.dumps(payload, ensure_ascii=True)


def _deserialize_apply_dialog_prefs(value):
    if not value:
        return dict(DEFAULT_APPLY_DIALOG_PREFS)

    try:
        raw_value = json.loads(value)
    except Exception:
        return dict(DEFAULT_APPLY_DIALOG_PREFS)

    prefs = dict(DEFAULT_APPLY_DIALOG_PREFS)
    if isinstance(raw_value, dict):
        for key, default_value in prefs.items():
            raw_pref_value = raw_value.get(key, default_value)
            if isinstance(default_value, bool):
                prefs[key] = bool(raw_pref_value)
            else:
                prefs[key] = str(raw_pref_value or default_value)
    prefs["cut_method"] = normalize_cut_method(prefs["cut_method"] or "CNC")
    if prefs["cut_method"] not in {"CNC", "Seccionadora"}:
        prefs["cut_method"] = "CNC"
    prefs["grain_direction"] = normalize_grain_direction(
        prefs["grain_direction"] or GRAIN_DIRECTION_OPTIONS[0]
    )
    return prefs


def normalize_assembly_guide_public_base_url(value):
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    return normalized.rstrip("/")


def _validate_assembly_guide_public_base_url(value):
    normalized = normalize_assembly_guide_public_base_url(value)
    if not normalized:
        return ""

    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            "A URL publica do guia precisa comecar com http:// ou https:// e incluir o dominio."
        )
    return normalized


def normalize_cloudflare_pages_project_name(value):
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    return normalized


def _validate_cloudflare_pages_project_name(value):
    normalized = normalize_cloudflare_pages_project_name(value)
    if not normalized:
        return ""
    if (
        not CLOUDFLARE_PAGES_PROJECT_NAME_PATTERN.fullmatch(normalized)
        or normalized.startswith("-")
        or normalized.endswith("-")
    ):
        raise ValueError(
            "O nome do projeto Cloudflare Pages aceita apenas letras, numeros e hifen."
    )
    return normalized


def normalize_cloudflare_account_id(value):
    return str(value or "").strip()


def normalize_cloudflare_api_token(value):
    return str(value or "").strip()


def resolve_assembly_guide_public_base_url(settings):
    explicit_value = normalize_assembly_guide_public_base_url(
        getattr(settings, "assembly_guide_public_base_url", "")
    )
    if explicit_value:
        return explicit_value
    return ""


def resolve_cloudflare_pages_project_name(settings):
    return normalize_cloudflare_pages_project_name(
        getattr(settings, "assembly_guide_cloudflare_project_name", "")
    )


def suggest_cloudflare_pages_project_name(settings):
    explicit_project_name = resolve_cloudflare_pages_project_name(settings)
    if explicit_project_name:
        return explicit_project_name

    prefs = get_cloudflare_publish_preferences()
    return normalize_cloudflare_pages_project_name(prefs.get("default_project_name", ""))


def get_cloudflare_publish_preferences():
    prefs_group = _panelnest_preferences_group()
    if prefs_group is None:
        return {
            "account_id": "",
            "api_token": "",
            "default_project_name": "",
        }

    try:
        account_id = normalize_cloudflare_account_id(
            prefs_group.GetString("CloudflareAccountId", "")
        )
    except Exception:
        account_id = ""
    try:
        api_token = normalize_cloudflare_api_token(
            prefs_group.GetString("CloudflareApiToken", "")
        )
    except Exception:
        api_token = ""
    try:
        default_project_name = normalize_cloudflare_pages_project_name(
            prefs_group.GetString("CloudflareDefaultProjectName", "")
        )
    except Exception:
        default_project_name = ""
    return {
        "account_id": account_id,
        "api_token": api_token,
        "default_project_name": default_project_name,
    }


def update_cloudflare_publish_preferences(account_id=None, api_token=None, default_project_name=None):
    prefs_group = _panelnest_preferences_group()
    if prefs_group is None:
        return get_cloudflare_publish_preferences()

    if account_id is not None:
        try:
            prefs_group.SetString("CloudflareAccountId", normalize_cloudflare_account_id(account_id))
        except Exception:
            pass
    if api_token is not None:
        try:
            prefs_group.SetString("CloudflareApiToken", normalize_cloudflare_api_token(api_token))
        except Exception:
            pass
    if default_project_name is not None:
        try:
            prefs_group.SetString(
                "CloudflareDefaultProjectName",
                normalize_cloudflare_pages_project_name(default_project_name),
            )
        except Exception:
            pass
    return get_cloudflare_publish_preferences()


def get_sheet_settings():
    from .parts import ensure_sheet_config; config = ensure_sheet_config()
    settings = SheetSettings(
        length_mm=float(getattr(config, "PanelNestSheetLengthMm", DEFAULT_SHEET_LENGTH_MM)),
        width_mm=float(getattr(config, "PanelNestSheetWidthMm", DEFAULT_SHEET_WIDTH_MM)),
        margin_mm=float(getattr(config, "PanelNestSheetMarginMm", DEFAULT_SHEET_MARGIN_MM)),
        spacing_mm=float(getattr(config, "PanelNestSheetSpacingMm", DEFAULT_SHEET_SPACING_MM)),
        full_sheet_count=int(getattr(config, "PanelNestFullSheetCount", DEFAULT_FULL_SHEET_COUNT)),
        allow_extra_full_sheets=bool(getattr(config, "PanelNestAllowExtraFullSheets", True)),
        full_sheet_thickness_mm=float(getattr(config, "PanelNestFullSheetThicknessMm", 0.0)),
        full_sheet_material=_material_value(getattr(config, "PanelNestFullSheetMaterial", "")),
        cnc_kerf_mm=float(getattr(config, "PanelNestCncKerfMm", DEFAULT_CNC_KERF_MM)),
        cnc_tech_margin_mm=float(
            getattr(config, "PanelNestCncTechMarginMm", DEFAULT_CNC_TECH_MARGIN_MM)
        ),
        saw_kerf_mm=float(getattr(config, "PanelNestSawKerfMm", DEFAULT_SAW_KERF_MM)),
        saw_tech_margin_mm=float(
            getattr(config, "PanelNestSawTechMarginMm", DEFAULT_SAW_TECH_MARGIN_MM)
        ),
        cnc_layout_strategy=normalize_cnc_layout_strategy(
            getattr(config, "PanelNestCncLayoutStrategy", DEFAULT_CNC_LAYOUT_STRATEGY)
        ),
        cnc_nesting_mode=normalize_cnc_nesting_mode(
            getattr(config, "PanelNestCncNestingMode", DEFAULT_CNC_NESTING_MODE)
        ),
        saw_layout_strategy=normalize_saw_layout_strategy(
            getattr(config, "PanelNestSawLayoutStrategy", DEFAULT_SAW_LAYOUT_STRATEGY)
        ),
        show_layout_part_dimensions=bool(
            getattr(config, "PanelNestShowLayoutPartDimensions", True)
        ),
        show_layout_part_labels=bool(
            getattr(config, "PanelNestShowLayoutPartLabels", True)
        ),
        show_layout_edge_bands=bool(
            getattr(config, "PanelNestShowLayoutEdgeBands", True)
        ),
        assembly_guide_public_base_url=normalize_assembly_guide_public_base_url(
            getattr(config, "PanelNestAssemblyGuidePublicBaseUrl", "")
        ),
        assembly_guide_cloudflare_project_name=normalize_cloudflare_pages_project_name(
            getattr(config, "PanelNestAssemblyGuideCloudflareProjectName", "")
        ),
        assembly_guide_cloudflare_auto_publish=bool(
            getattr(config, "PanelNestAssemblyGuideCloudflareAutoPublish", False)
        ),
        assembly_guide_preserve_current_visual=bool(
            getattr(config, "PanelNestAssemblyGuidePreserveCurrentVisual", False)
        ),
        remnants=_deserialize_remnants(getattr(config, "PanelNestRemnantsJson", "[]")),
        hardware_catalog=_deserialize_hardware_catalog(getattr(config, "PanelNestHardwareCatalogJson", "[]")) or _default_hardware_catalog(),
        edge_band_thickness_mm=float(getattr(config, "PanelNestEdgeBandThicknessMm", 0.0)),
        edge_band_label=str(getattr(config, "PanelNestEdgeBandLabel", "") or ""),
        edge_band_price_per_m=float(getattr(config, "PanelNestEdgeBandPricePerM", 0.0) or 0.0),
        edge_band_waste_factor=float(getattr(config, "PanelNestEdgeBandWasteFactor", 0.10) or 0.10),
        full_sheet_cost=float(getattr(config, "PanelNestFullSheetCost", 0.0)),
        company_name=str(getattr(config, "PanelNestCompanyName", "") or ""),
        company_contact=str(getattr(config, "PanelNestCompanyContact", "") or ""),
        company_address=str(getattr(config, "PanelNestCompanyAddress", "") or ""),
        project_client=str(getattr(config, "PanelNestProjectClient", "") or ""),
        project_responsible=str(getattr(config, "PanelNestProjectResponsible", "") or ""),
        project_notes=str(getattr(config, "PanelNestProjectNotes", "") or ""),
        cnc_common_line=bool(getattr(config, "PanelNestCncCommonLine", False)),
        cnc_stay_down=bool(getattr(config, "PanelNestCncStayDown", False)),
        cnc_origin_corner=_normalize_cnc_origin_corner(
            getattr(config, "PanelNestCncOriginCorner", DEFAULT_CNC_ORIGIN_CORNER)
        ),
    )
    _validate_sheet_settings(settings)
    return settings


def _normalize_cnc_origin_corner(value):
    from .constants import CNC_ORIGIN_CORNERS
    v = str(value or "").strip()
    if v in CNC_ORIGIN_CORNERS:
        return v
    return DEFAULT_CNC_ORIGIN_CORNER


def get_apply_dialog_prefs():
    from .parts import ensure_sheet_config; config = ensure_sheet_config()
    return _deserialize_apply_dialog_prefs(getattr(config, "PanelNestApplyDialogJson", ""))


def update_apply_dialog_prefs(**prefs):
    from .parts import ensure_sheet_config; config = ensure_sheet_config()
    current_prefs = get_apply_dialog_prefs()
    for key, default_value in DEFAULT_APPLY_DIALOG_PREFS.items():
        if key not in prefs:
            continue
        if isinstance(default_value, bool):
            current_prefs[key] = bool(prefs[key])
        else:
            current_prefs[key] = str(prefs[key] or "")
    current_prefs["cut_method"] = normalize_cut_method(current_prefs.get("cut_method", "CNC") or "CNC")
    if current_prefs["cut_method"] not in {"CNC", "Seccionadora"}:
        current_prefs["cut_method"] = "CNC"
    current_prefs["grain_direction"] = normalize_grain_direction(
        current_prefs.get("grain_direction", GRAIN_DIRECTION_OPTIONS[0]) or GRAIN_DIRECTION_OPTIONS[0]
    )
    config.PanelNestApplyDialogJson = json.dumps(current_prefs, ensure_ascii=True)
    ensure_document().recompute()
    return current_prefs


def _panelnest_preferences_group():
    if App is None or not hasattr(App, "ParamGet"):
        return None
    try:
        return App.ParamGet("User parameter:BaseApp/Preferences/Mod/PanelNest")
    except Exception:
        return None


def _dialog_size_preferences_group(dialog_key):
    root_group = _panelnest_preferences_group()
    if root_group is None or not hasattr(root_group, "GetGroup"):
        return None
    try:
        return root_group.GetGroup("DialogSizes").GetGroup(str(dialog_key))
    except Exception:
        return None


def restore_dialog_size(
    dialog,
    dialog_key,
    default_width=None,
    default_height=None,
    minimum_width=None,
    minimum_height=None,
):
    if dialog is None:
        return

    if minimum_width is not None and hasattr(dialog, "setMinimumWidth"):
        dialog.setMinimumWidth(int(minimum_width))
    if minimum_height is not None and hasattr(dialog, "setMinimumHeight"):
        dialog.setMinimumHeight(int(minimum_height))
    if hasattr(dialog, "setSizeGripEnabled"):
        try:
            dialog.setSizeGripEnabled(True)
        except Exception:
            pass

    width = int(default_width or max(getattr(dialog, "width", lambda: 0)(), 0))
    height = int(default_height or max(getattr(dialog, "height", lambda: 0)(), 0))

    pref_group = _dialog_size_preferences_group(dialog_key)
    if pref_group is not None:
        try:
            saved_width = int(pref_group.GetInt("Width", 0))
            saved_height = int(pref_group.GetInt("Height", 0))
            if saved_width > 0:
                width = saved_width
            if saved_height > 0:
                height = saved_height
        except Exception:
            pass

    if minimum_width is not None:
        width = max(int(minimum_width), int(width))
    if minimum_height is not None:
        height = max(int(minimum_height), int(height))

    if width > 0 and height > 0 and hasattr(dialog, "resize"):
        dialog.resize(int(width), int(height))


def save_dialog_size(dialog, dialog_key):
    if dialog is None:
        return

    pref_group = _dialog_size_preferences_group(dialog_key)
    if pref_group is None:
        return

    try:
        pref_group.SetInt("Width", int(dialog.width()))
        pref_group.SetInt("Height", int(dialog.height()))
    except Exception:
        pass


def update_sheet_settings(
    length_mm=None,
    width_mm=None,
    margin_mm=None,
    spacing_mm=None,
    full_sheet_count=None,
    allow_extra_full_sheets=None,
    full_sheet_thickness_mm=None,
    full_sheet_material=None,
    cnc_kerf_mm=None,
    cnc_tech_margin_mm=None,
    saw_kerf_mm=None,
    saw_tech_margin_mm=None,
    cnc_layout_strategy=None,
    cnc_nesting_mode=None,
    saw_layout_strategy=None,
    show_layout_part_dimensions=None,
    show_layout_part_labels=None,
    show_layout_edge_bands=None,
    assembly_guide_public_base_url=None,
    assembly_guide_cloudflare_project_name=None,
    assembly_guide_cloudflare_auto_publish=None,
    assembly_guide_preserve_current_visual=None,
    remnants=None,
    hardware_catalog=None,
    edge_band_thickness_mm=None,
    edge_band_label=None,
    edge_band_price_per_m=None,
    edge_band_waste_factor=None,
    full_sheet_cost=None,
    company_name=None,
    company_contact=None,
    company_address=None,
    project_client=None,
    project_responsible=None,
    project_notes=None,
    cnc_common_line=None,
    cnc_stay_down=None,
    cnc_origin_corner=None,
):
    doc = ensure_document()
    from .parts import ensure_sheet_config; config = ensure_sheet_config()

    if length_mm is not None:
        config.PanelNestSheetLengthMm = float(length_mm)
    if width_mm is not None:
        config.PanelNestSheetWidthMm = float(width_mm)
    if margin_mm is not None:
        config.PanelNestSheetMarginMm = float(margin_mm)
    if spacing_mm is not None:
        config.PanelNestSheetSpacingMm = float(spacing_mm)
    if full_sheet_count is not None:
        config.PanelNestFullSheetCount = int(full_sheet_count)
    if allow_extra_full_sheets is not None:
        config.PanelNestAllowExtraFullSheets = bool(allow_extra_full_sheets)
    if full_sheet_thickness_mm is not None:
        config.PanelNestFullSheetThicknessMm = float(full_sheet_thickness_mm)
    if full_sheet_material is not None:
        config.PanelNestFullSheetMaterial = _material_value(full_sheet_material)
    if cnc_kerf_mm is not None:
        config.PanelNestCncKerfMm = float(cnc_kerf_mm)
    if cnc_tech_margin_mm is not None:
        config.PanelNestCncTechMarginMm = float(cnc_tech_margin_mm)
    if saw_kerf_mm is not None:
        config.PanelNestSawKerfMm = float(saw_kerf_mm)
    if saw_tech_margin_mm is not None:
        config.PanelNestSawTechMarginMm = float(saw_tech_margin_mm)
    if cnc_layout_strategy is not None:
        config.PanelNestCncLayoutStrategy = normalize_cnc_layout_strategy(cnc_layout_strategy)
    if cnc_nesting_mode is not None:
        config.PanelNestCncNestingMode = normalize_cnc_nesting_mode(cnc_nesting_mode)
    if saw_layout_strategy is not None:
        config.PanelNestSawLayoutStrategy = normalize_saw_layout_strategy(saw_layout_strategy)
    if show_layout_part_dimensions is not None:
        config.PanelNestShowLayoutPartDimensions = bool(show_layout_part_dimensions)
    if show_layout_part_labels is not None:
        config.PanelNestShowLayoutPartLabels = bool(show_layout_part_labels)
    if show_layout_edge_bands is not None:
        config.PanelNestShowLayoutEdgeBands = bool(show_layout_edge_bands)
    if assembly_guide_public_base_url is not None:
        config.PanelNestAssemblyGuidePublicBaseUrl = _validate_assembly_guide_public_base_url(
            assembly_guide_public_base_url
        )
    if assembly_guide_cloudflare_project_name is not None:
        config.PanelNestAssemblyGuideCloudflareProjectName = _validate_cloudflare_pages_project_name(
            assembly_guide_cloudflare_project_name
        )
    if assembly_guide_cloudflare_auto_publish is not None:
        config.PanelNestAssemblyGuideCloudflareAutoPublish = bool(
            assembly_guide_cloudflare_auto_publish
        )
    if assembly_guide_preserve_current_visual is not None:
        config.PanelNestAssemblyGuidePreserveCurrentVisual = bool(
            assembly_guide_preserve_current_visual
        )
    if remnants is not None:
        config.PanelNestRemnantsJson = _serialize_remnants(remnants)
    if hardware_catalog is not None:
        config.PanelNestHardwareCatalogJson = _serialize_hardware_catalog(hardware_catalog)
    if edge_band_thickness_mm is not None:
        config.PanelNestEdgeBandThicknessMm = float(edge_band_thickness_mm)
    if edge_band_label is not None:
        config.PanelNestEdgeBandLabel = str(edge_band_label)
    if edge_band_price_per_m is not None:
        config.PanelNestEdgeBandPricePerM = float(edge_band_price_per_m)
    if edge_band_waste_factor is not None:
        config.PanelNestEdgeBandWasteFactor = float(edge_band_waste_factor)
    if full_sheet_cost is not None:
        config.PanelNestFullSheetCost = float(full_sheet_cost)
    if company_name is not None:
        config.PanelNestCompanyName = str(company_name)
    if company_contact is not None:
        config.PanelNestCompanyContact = str(company_contact)
    if company_address is not None:
        config.PanelNestCompanyAddress = str(company_address)
    if project_client is not None:
        config.PanelNestProjectClient = str(project_client)
    if project_responsible is not None:
        config.PanelNestProjectResponsible = str(project_responsible)
    if project_notes is not None:
        config.PanelNestProjectNotes = str(project_notes)
    if cnc_common_line is not None:
        config.PanelNestCncCommonLine = bool(cnc_common_line)
    if cnc_stay_down is not None:
        config.PanelNestCncStayDown = bool(cnc_stay_down)
    if cnc_origin_corner is not None:
        config.PanelNestCncOriginCorner = _normalize_cnc_origin_corner(cnc_origin_corner)

    settings = get_sheet_settings()
    doc.recompute()
    return settings, config


def _quantity_value(value):
    if hasattr(value, "Value"):
        return float(value.Value)
    return float(value)


def _format_mm(value):
    formatted = f"{value:.2f}"
    return formatted.rstrip("0").rstrip(".")


def _format_m2(area_mm2):
    return f"{area_mm2 / 1_000_000:.3f}"


def _format_m(length_mm):
    return f"{length_mm / 1000.0:.3f}"


def _format_percent(value):
    return f"{value * 100:.1f}"


def _normalize_stock_label(value, fallback):
    if isinstance(value, bool) or value is None:
        return fallback

    label = str(value).strip()
    if not label or label.casefold() in {"false", "true", "none", "null"}:
        return fallback

    return label


def _format_thickness_label(thickness_mm):
    if thickness_mm > 0:
        return f"{_format_mm(thickness_mm)} mm"
    return "esp. generica"


def _material_value(value):
    return (value or "").strip()


def _material_sort_value(value):
    material = _material_value(value)
    return material.casefold() if material else "zzzz_sem_material"


def _safe_material(obj):
    if _has_property(obj, "PanelNestMaterial") and getattr(obj, "PanelNestMaterial", ""):
        return str(obj.PanelNestMaterial)

    # Walk up parent containers (PartDesign::Body, App::Part, App::Link target).
    # We only ascend — never traverse sibling OutList — so material set on a
    # parent container only reaches its own descendants, never neighbors.
    _PARENT_CONTAINER_TYPES = {
        "App::Part",
        "App::DocumentObjectGroup",
        "App::DocumentObjectGroupPython",
        "PartDesign::Body",
    }
    seen = {id(obj)}
    cand = obj
    for _ in range(8):
        type_id = str(getattr(cand, "TypeId", "") or "")
        nxt = None
        if type_id.startswith("App::Link"):
            nxt = getattr(cand, "LinkedObject", None)
        else:
            # ascend to first parent that is a known container type
            for parent in getattr(cand, "InList", []) or []:
                ptype = str(getattr(parent, "TypeId", "") or "")
                if ptype in _PARENT_CONTAINER_TYPES:
                    nxt = parent
                    break
        if nxt is None or id(nxt) in seen:
            break
        seen.add(id(nxt))
        cand = nxt
        if _has_property(cand, "PanelNestMaterial"):
            value = getattr(cand, "PanelNestMaterial", "")
            if value:
                return str(value)

    material = getattr(obj, "Material", "")
    if isinstance(material, dict):
        return str(material.get("Name", ""))
    if hasattr(material, "get"):
        try:
            return str(material.get("Name", ""))
        except Exception:
            return ""
    if material:
        return str(material)
    return ""


def _safe_cut_method(obj):
    if _has_property(obj, "PanelNestCutMethod"):
        value = getattr(obj, "PanelNestCutMethod", CUT_METHOD_OPTIONS[0])
        normalized_value = normalize_cut_method(value)
        return normalized_value if normalized_value else CUT_METHOD_OPTIONS[0]
    return CUT_METHOD_OPTIONS[0]


def normalize_cut_method(value):
    return CUT_METHOD_ALIASES.get(str(value), str(value))


def _cut_method_sort_value(value):
    return CUT_METHOD_SORT_ORDER.get(normalize_cut_method(value), 99)


def normalize_cnc_layout_strategy(value):
    return CNC_LAYOUT_STRATEGY_ALIASES.get(str(value), DEFAULT_CNC_LAYOUT_STRATEGY)


def normalize_cnc_nesting_mode(value):
    return CNC_NESTING_MODE_ALIASES.get(str(value), DEFAULT_CNC_NESTING_MODE)


def normalize_saw_layout_strategy(value):
    return SAW_LAYOUT_STRATEGY_ALIASES.get(str(value), DEFAULT_SAW_LAYOUT_STRATEGY)


def _strategy_candidates_for_cut_method(cut_method, settings):
    normalized_cut_method = normalize_cut_method(cut_method)
    if normalized_cut_method == "CNC":
        strategy = normalize_cnc_layout_strategy(settings.cnc_layout_strategy)
        if strategy == "Auto":
            return list(CNC_LAYOUT_STRATEGY_OPTIONS[1:])
        return [strategy]
    if normalized_cut_method == "Seccionadora":
        strategy = normalize_saw_layout_strategy(settings.saw_layout_strategy)
        if strategy == "Auto":
            return list(SAW_LAYOUT_STRATEGY_OPTIONS[1:])
        return [strategy]
    return [""]


def _settings_with_layout_strategy(settings, cut_method, strategy_name):
    normalized_cut_method = normalize_cut_method(cut_method)
    if normalized_cut_method == "CNC":
        return replace(settings, cnc_layout_strategy=normalize_cnc_layout_strategy(strategy_name))
    if normalized_cut_method == "Seccionadora":
        return replace(settings, saw_layout_strategy=normalize_saw_layout_strategy(strategy_name))
    return settings


def normalize_grain_direction(value):
    return GRAIN_DIRECTION_ALIASES.get(str(value), GRAIN_DIRECTION_OPTIONS[0])


def _safe_allow_rotation(obj):
    if _has_property(obj, "PanelNestAllowRotation"):
        return bool(getattr(obj, "PanelNestAllowRotation", True))
    return True


def _safe_grain_direction(obj):
    if _has_property(obj, "PanelNestGrainDirection"):
        value = getattr(obj, "PanelNestGrainDirection", GRAIN_DIRECTION_OPTIONS[0])
        return normalize_grain_direction(value)
    return GRAIN_DIRECTION_OPTIONS[0]


def _safe_grain_rotated(obj):
    if _has_property(obj, "PanelNestGrainRotated"):
        return bool(getattr(obj, "PanelNestGrainRotated", False))
    return False


def _safe_grain_match_group(obj):
    if _has_property(obj, "PanelNestGrainMatchGroup"):
        return str(getattr(obj, "PanelNestGrainMatchGroup", "") or "").strip()
    return ""


def _safe_hardware(obj):
    """Lê a lista de ferragens do objeto FreeCAD como lista de dicts {hw_id, qty}."""
    if not _has_property(obj, "PanelNestHardwareJson"):
        return []
    raw = getattr(obj, "PanelNestHardwareJson", "") or ""
    if not raw:
        return []
    try:
        items = json.loads(raw)
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            hw_id = str(item.get("hw_id", "") or "").strip()
            qty = int(item.get("qty", 1) or 1)
            if hw_id and qty > 0:
                result.append({"hw_id": hw_id, "qty": qty})
        return result
    except Exception:
        return []


def save_part_hardware(obj, hardware_list):
    """Salva a lista de ferragens [{hw_id, qty}] no objeto FreeCAD."""
    from .parts import _ensure_string_property
    _ensure_string_property(obj, "PanelNestHardwareJson", "Ferragens associadas a esta peca (JSON)")
    payload = [
        {"hw_id": str(item["hw_id"]), "qty": int(item.get("qty", 1))}
        for item in (hardware_list or [])
        if item.get("hw_id")
    ]
    obj.PanelNestHardwareJson = json.dumps(payload, ensure_ascii=True)
