import re
import unicodedata
import csv
import os
from dataclasses import replace

try:
    import FreeCAD as App
except ImportError:
    App = None

from .constants import (
    PART_LABEL_PATTERN,
    PARTS_SPREADSHEET_NAME,
    ORGANIZATION_SPREADSHEET_NAME,
    EDGE_BAND_SPREADSHEET_NAME,
    LABELS_SPREADSHEET_NAME,
    VALIDATION_SPREADSHEET_NAME,
    SHEET_SUMMARY_SPREADSHEET_NAME,
    LAYOUT_WARNINGS_SPREADSHEET_NAME,
    CUT_PLAN_SPREADSHEET_NAME,
    GENERATED_REMNANTS_SPREADSHEET_NAME,
    COST_REPORT_SPREADSHEET_NAME,
    EDGE_BAND_CONSUMPTION_SPREADSHEET_NAME,
    HARDWARE_SPREADSHEET_NAME,
    PANELNEST_EXPORT_TABLE_SPECS,
    WORKBENCH_ID,
    GENERATED_REMNANT_LABEL_PREFIX,
)
from .freecad_utils import ensure_document
from .models import SheetStockPiece
from .metadata import (
    _format_mm,
    _format_m2,
    _format_m,
    _format_percent,
    _material_value,
    _normalize_stock_label,
    _format_thickness_label,
    normalize_cut_method,
    normalize_grain_direction,
    get_sheet_settings,
    update_sheet_settings,
)
from .models import GeneratedRemnant
from .edge_band import (
    _edge_band_summary,
    _edge_band_abbreviation_summary,
    _normalize_occurrence_edge_band_flags,
    _object_occurrence_edge_band_map,
    _part_edge_band_flags_dict,
)
from .parts import (
    collect_parts,
    group_parts,
    organize_parts,
    _base_label_for_object,
    _is_user_project_container,
)
from .nesting import (
    get_cut_process_profile,
    _layout_mode_label,
    _layout_sheet_utilization_ratio,
    _layout_sheet_used_area_mm2,
    _layout_sheet_display_name,
    _stock_piece_display_name,
    collect_generated_remnants,
    layout_sheet_utilization_lines,
    layout_overall_utilization_ratio,
    create_layout_sheets,
    _sheet_usable_area_mm2,
    _effective_margin_mm,
    _expanded_report_parts,
    _cut_kind_for_orientation,
    _cut_phase_label,
    _sequence_zone_display_label,
    _layout_sheet_sequence_zone_labels,
    _build_available_stock_sheets,
    _layout_sheet_key,
    _thickness_matches,
    _material_matches,
    _part_fits_stock_piece,
)
from .edge_band import _allow_rotation_summary, _part_is_square


def _sheet_cell(column_index, row_index):
    return f"{chr(64 + column_index)}{row_index}"


def _spreadsheet_cell_text(sheet, column_index, row_index):
    cell_name = _sheet_cell(column_index, row_index)
    for getter_name in ("getContents", "get"):
        getter = getattr(sheet, getter_name, None)
        if getter is None:
            continue
        try:
            value = getter(cell_name)
        except Exception:
            continue
        if value is None:
            return ""
        text = str(value).strip()
        if text.startswith("'"):
            text = text[1:].strip()
        return text
    return ""


def _parse_sheet_number(text):
    normalized = str(text or "").strip()
    if not normalized:
        return 0.0
    normalized = normalized.replace(" ", "")
    if "," in normalized and "." in normalized:
        if normalized.rfind(",") > normalized.rfind("."):
            normalized = normalized.replace(".", "").replace(",", ".")
        else:
            normalized = normalized.replace(",", "")
    elif "," in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    return float(normalized or 0.0)


def _slugify_export_fragment(text):
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", normalized)
    normalized = normalized.strip("_").lower()
    return normalized or WORKBENCH_ID.lower()


def get_panelnest_export_base_name(document=None):
    doc = document or ensure_document()
    if getattr(doc, "FileName", ""):
        base_name = os.path.splitext(os.path.basename(doc.FileName))[0]
    else:
        base_name = getattr(doc, "Label", "") or getattr(doc, "Name", "") or WORKBENCH_ID
    return _slugify_export_fragment(base_name)


def spreadsheet_to_rows(sheet, empty_row_limit=25, empty_column_limit=10):
    column_index = 1
    empty_columns = 0
    headers = []

    while empty_columns < empty_column_limit:
        value = _spreadsheet_cell_text(sheet, column_index, 1)
        if value:
            headers.append(value)
            empty_columns = 0
        else:
            empty_columns += 1
        column_index += 1

    if not headers:
        return []

    rows = []
    row_index = 1
    empty_rows = 0
    last_non_empty_row = 0

    while empty_rows < empty_row_limit:
        row = [_spreadsheet_cell_text(sheet, column_number, row_index) for column_number in range(1, len(headers) + 1)]
        rows.append(row)
        if any(row):
            last_non_empty_row = len(rows)
            empty_rows = 0
        else:
            empty_rows += 1
        row_index += 1

    return rows[:last_non_empty_row]


def collect_panelnest_export_tables(document=None):
    doc = document or ensure_document()
    tables = []

    for sheet_name, slug in PANELNEST_EXPORT_TABLE_SPECS:
        sheet = doc.getObject(sheet_name)
        if sheet is None:
            continue
        rows = spreadsheet_to_rows(sheet)
        if not rows:
            continue
        tables.append(
            {
                "sheet_name": sheet_name,
                "label": getattr(sheet, "Label", sheet_name) or sheet_name,
                "slug": slug,
                "rows": rows,
                "row_count": max(len(rows) - 1, 0),
                "column_count": len(rows[0]) if rows else 0,
            }
        )

    return tables


def _remove_sheet_if_exists(document, sheet_name):
    doc = document or ensure_document()
    existing_sheet = doc.getObject(sheet_name)
    if existing_sheet is None:
        return False
    doc.removeObject(existing_sheet.Name)
    return True


PANELNEST_SHEETS_GROUP_NAME = "PanelNest_Planilhas"


def _add_sheet_to_panelnest_group(doc, sheet):
    """Move uma planilha gerada pelo PanelNest para o grupo de organização
    'PanelNest_Planilhas' na tree. Cria o grupo se não existir.
    """
    if doc is None or sheet is None:
        return
    group = doc.getObject(PANELNEST_SHEETS_GROUP_NAME)
    if group is None or getattr(group, "TypeId", "") != "App::DocumentObjectGroup":
        group = doc.addObject("App::DocumentObjectGroup", PANELNEST_SHEETS_GROUP_NAME)
        try:
            group.Label = "PanelNest — Planilhas"
        except Exception:
            pass
    try:
        if sheet not in list(getattr(group, "Group", [])):
            group.addObject(sheet)
    except Exception:
        pass


def _clear_layout_export_sheets(document=None):
    doc = document or ensure_document()
    for sheet_name in (
        SHEET_SUMMARY_SPREADSHEET_NAME,
        CUT_PLAN_SPREADSHEET_NAME,
        LAYOUT_WARNINGS_SPREADSHEET_NAME,
        GENERATED_REMNANTS_SPREADSHEET_NAME,
    ):
        _remove_sheet_if_exists(doc, sheet_name)


def prepare_panelnest_export_tables(document=None, parts=None):
    doc = document or ensure_document()
    parts = parts if parts is not None else collect_parts(include_hidden=True)
    if not parts:
        raise ValueError(
            "Nenhuma peca valida foi encontrada. Selecione objetos solidos ou deixe visiveis apenas as pecas desejadas."
        )

    settings = None
    layout_sheets = None

    create_parts_spreadsheet(parts, sheet_name=PARTS_SPREADSHEET_NAME)
    create_organization_spreadsheet(parts, sheet_name=ORGANIZATION_SPREADSHEET_NAME)
    create_part_labels_spreadsheet(parts, sheet_name=LABELS_SPREADSHEET_NAME)
    create_edge_band_spreadsheet(parts, sheet_name=EDGE_BAND_SPREADSHEET_NAME)

    try:
        settings = get_sheet_settings()
    except ValueError:
        _clear_layout_export_sheets(doc)
    else:
        try:
            layout_sheets = create_layout_sheets(parts, settings=settings)
        except ValueError:
            _clear_layout_export_sheets(doc)
        else:
            create_sheet_summary_spreadsheet(
                layout_sheets,
                settings,
                sheet_name=SHEET_SUMMARY_SPREADSHEET_NAME,
            )
            create_cut_plan_spreadsheet(
                layout_sheets,
                sheet_name=CUT_PLAN_SPREADSHEET_NAME,
            )
            create_layout_warnings_spreadsheet(
                collect_layout_warnings(parts, layout_sheets, settings),
                sheet_name=LAYOUT_WARNINGS_SPREADSHEET_NAME,
            )
            create_generated_remnants_spreadsheet(
                layout_sheets,
                settings,
                sheet_name=GENERATED_REMNANTS_SPREADSHEET_NAME,
            )

    from .validation import collect_project_validation_issues, create_project_validation_spreadsheet
    validation_issues = collect_project_validation_issues(
        parts=parts,
        settings=settings,
        document=doc,
        layout_sheets=layout_sheets,
    )
    create_project_validation_spreadsheet(
        parts=parts,
        issues=validation_issues,
        settings=settings,
        document=doc,
        layout_sheets=layout_sheets,
        sheet_name=VALIDATION_SPREADSHEET_NAME,
    )
    tables = collect_panelnest_export_tables(document=doc)

    return {
        "document": doc,
        "parts": parts,
        "settings": settings,
        "layout_sheets": layout_sheets,
        "validation_issues": validation_issues,
        "tables": tables,
    }


def export_panelnest_csv_bundle(output_dir, document=None, delimiter=";"):
    doc = document or ensure_document()
    prepared = prepare_panelnest_export_tables(document=doc)
    tables = prepared["tables"]
    if not tables:
        raise ValueError(
            "Nenhuma planilha do PanelNest foi encontrada no documento ativo. Gere ou colete os dados antes de exportar."
        )

    os.makedirs(output_dir, exist_ok=True)
    base_name = get_panelnest_export_base_name(document=doc)
    exported_files = []

    for table in tables:
        file_name = f"{base_name}_{table['slug']}.csv"
        file_path = os.path.join(output_dir, file_name)
        with open(file_path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL)
            writer.writerows(table["rows"])
        exported_files.append(
            {
                "label": table["label"],
                "path": file_path,
                "row_count": table["row_count"],
            }
        )

    return {
        "output_dir": output_dir,
        "base_name": base_name,
        "parts": prepared["parts"],
        "settings": prepared["settings"],
        "layout_sheets": prepared["layout_sheets"],
        "validation_issues": prepared["validation_issues"],
        "tables": tables,
        "files": exported_files,
    }



def _part_group_id_map(parts):
    group_id_map = {}
    grouped_parts = group_parts([replace(part) for part in list(parts or [])])
    for group in grouped_parts:
        for part in group["parts"]:
            base_object_name = str(getattr(part, "object_name", "") or "").split("#", 1)[0]
            if base_object_name:
                group_id_map[base_object_name] = group["group_id"]
    return group_id_map


def collect_part_label_records(parts=None, document=None):
    doc = document or ensure_document()
    parts = parts if parts is not None else collect_parts()
    if not parts:
        return []

    group_id_map = _part_group_id_map(parts)
    records = []

    for part in parts:
        base_object_name = str(getattr(part, "object_name", "") or "")
        if not base_object_name:
            continue

        quantity = max(1, int(getattr(part, "quantity", 1) or 1))
        obj = doc.getObject(base_object_name) if doc is not None else None
        occurrence_map = _object_occurrence_edge_band_map(obj, quantity) if obj is not None else {}
        base_flags = _part_edge_band_flags_dict(part)
        base_label = (
            _base_label_for_object(obj)
            if obj is not None
            else PART_LABEL_PATTERN.sub("", str(getattr(part, "label", "") or "")).strip()
        )
        if not base_label:
            base_label = str(getattr(part, "label", "") or base_object_name).strip()

        group_id = group_id_map.get(base_object_name, "")
        material_text = _material_value(part.material) or "Sem material"
        cut_method_text = normalize_cut_method(part.cut_method)
        grain_text = normalize_grain_direction(part.grain_direction)
        dimensions_text = (
            f"{_format_mm(part.length_mm)} x {_format_mm(part.width_mm)} x "
            f"{_format_mm(part.thickness_mm)} mm"
        )

        for occurrence_index in range(1, quantity + 1):
            occurrence_text = f"{occurrence_index:02d}/{quantity:02d}" if quantity > 1 else ""
            current_flags = _normalize_occurrence_edge_band_flags(
                occurrence_map.get(occurrence_index, base_flags)
            )
            current_part = replace(
                part,
                object_name=(
                    f"{base_object_name}#{occurrence_index:02d}"
                    if quantity > 1
                    else base_object_name
                ),
                quantity=1,
                edge_band_top=current_flags["top"],
                edge_band_bottom=current_flags["bottom"],
                edge_band_left=current_flags["left"],
                edge_band_right=current_flags["right"],
            )
            records.append(
                {
                    "group_id": group_id,
                    "label_code": (
                        f"{part.part_id} [{occurrence_text}]"
                        if occurrence_text
                        else part.part_id
                    ),
                    "part_id": part.part_id,
                    "occurrence": occurrence_text,
                    "part_label": base_label,
                    "dimensions_text": dimensions_text,
                    "length_mm": part.length_mm,
                    "width_mm": part.width_mm,
                    "thickness_mm": part.thickness_mm,
                    "material": material_text,
                    "cut_method": cut_method_text,
                    "grain_direction": grain_text,
                    "edge_band_text": _edge_band_summary(current_part) or "Sem fita",
                    "edge_band_abbrev": _edge_band_abbreviation_summary(current_part) or "Sem fita",
                    "object_name": current_part.object_name,
                }
            )

    return records


def create_part_labels_spreadsheet(parts, sheet_name=LABELS_SPREADSHEET_NAME):
    doc = ensure_document()

    try:
        import Spreadsheet  # noqa: F401
    except ImportError:
        pass

    existing_sheet = doc.getObject(sheet_name)
    if existing_sheet is not None:
        doc.removeObject(existing_sheet.Name)

    records = collect_part_label_records(parts=parts, document=doc)
    if not records:
        doc.recompute()
        return None

    sheet = doc.addObject("Spreadsheet::Sheet", sheet_name)
    _add_sheet_to_panelnest_group(doc, sheet)
    sheet.Label = "Etiquetas PanelNest"

    headers = (
        "Grupo",
        "Etiqueta",
        "ID da peca",
        "Ocorrencia",
        "Rotulo",
        "Dimensoes (mm)",
        "Comprimento (mm)",
        "Largura (mm)",
        "Espessura (mm)",
        "Material",
        "Metodo de corte",
        "Veio/fibra",
        "Fita de borda",
        "Objeto",
    )
    for column_index, header in enumerate(headers, start=1):
        sheet.set(_sheet_cell(column_index, 1), header)

    for row_index, record in enumerate(records, start=2):
        values = (
            record["group_id"],
            record["label_code"],
            record["part_id"],
            f"[{record['occurrence']}]" if record["occurrence"] else "",
            record["part_label"],
            record["dimensions_text"],
            _format_mm(record["length_mm"]),
            _format_mm(record["width_mm"]),
            _format_mm(record["thickness_mm"]),
            record["material"],
            record["cut_method"],
            record["grain_direction"],
            record["edge_band_text"],
            record["object_name"],
        )
        for column_index, value in enumerate(values, start=1):
            sheet.set(_sheet_cell(column_index, row_index), value)

    doc.recompute()
    return sheet


def refresh_part_reporting_sheets(parts=None, document=None):
    doc = document or ensure_document()
    parts = parts if parts is not None else collect_parts()
    refreshed = {}

    if doc.getObject(PARTS_SPREADSHEET_NAME) is not None:
        refreshed["parts"] = create_parts_spreadsheet(parts)
    if doc.getObject(ORGANIZATION_SPREADSHEET_NAME) is not None:
        refreshed["organization"] = create_organization_spreadsheet(parts)
    if doc.getObject(LABELS_SPREADSHEET_NAME) is not None:
        refreshed["labels"] = create_part_labels_spreadsheet(parts)
    if doc.getObject(VALIDATION_SPREADSHEET_NAME) is not None:
        refreshed["validation"] = create_project_validation_spreadsheet(
            parts=parts,
            document=doc,
        )
    if doc.getObject(EDGE_BAND_SPREADSHEET_NAME) is not None or any(
        _part_edge_band_metrics(part)["banded_side_count"] > 0 for part in parts
    ):
        refreshed["edge_band"] = create_edge_band_spreadsheet(parts)

    return refreshed



def create_sheet_summary_spreadsheet(
    layout_sheets,
    settings,
    sheet_name=SHEET_SUMMARY_SPREADSHEET_NAME,
):
    doc = ensure_document()

    try:
        import Spreadsheet  # noqa: F401
    except ImportError:
        pass

    existing_sheet = doc.getObject(sheet_name)
    if existing_sheet is not None:
        doc.removeObject(existing_sheet.Name)

    sheet = doc.addObject("Spreadsheet::Sheet", sheet_name)
    _add_sheet_to_panelnest_group(doc, sheet)
    sheet.Label = "Resumo de Chapas PanelNest"
    generated_remnants = collect_generated_remnants(layout_sheets, settings)
    generated_remnants_by_sheet = {}
    for remnant in generated_remnants:
        generated_remnants_by_sheet.setdefault(
            (
                remnant.group_id,
                remnant.sheet_index,
                remnant.source_label,
                remnant.source_kind,
            ),
            [],
        ).append(remnant)

    headers = (
        "Grupo",
        "Tipo",
        "Chapa",
        "Origem",
        "Formato / Esp. (mm)",
        "Material",
        "Espessura (mm)",
        "Metodo de corte",
        "Estrategia",
        "Modo de layout",
        "Kerf (mm)",
        "Margem tecnica (mm)",
        "Qtd de pecas",
        "Area das pecas (m2)",
        "Area util (m2)",
        "Aproveitamento (%)",
        "Chapas equivalentes",
        "Qtd retalhos gerados",
        "Area retalhos (m2)",
    )
    for column_index, header in enumerate(headers, start=1):
        sheet.set(_sheet_cell(column_index, 1), header)

    row_index = 2
    grouped_layout = {}
    for layout_sheet in layout_sheets:
        grouped_layout.setdefault(layout_sheet.group_id, []).append(layout_sheet)

    for group_id, group_layout_sheets in grouped_layout.items():
        total_parts = 0
        total_used_area_mm2 = 0.0
        total_generated_remnants = 0
        total_generated_remnant_area_mm2 = 0.0

        for layout_sheet in group_layout_sheets:
            process_profile = get_cut_process_profile(
                layout_sheet.cut_method,
                settings,
                layout_sheet.layout_strategy,
            )
            full_sheet_usable_area_mm2 = _sheet_usable_area_mm2(
                settings.length_mm,
                settings.width_mm,
                settings,
                layout_sheet.cut_method,
            )
            used_area_mm2 = _layout_sheet_used_area_mm2(layout_sheet)
            usable_area_mm2 = _sheet_usable_area_mm2(
                layout_sheet.source_length_mm,
                layout_sheet.source_width_mm,
                settings,
                layout_sheet.cut_method,
            )
            part_count = len(layout_sheet.placements)
            sheet_generated_remnants = generated_remnants_by_sheet.get(_layout_sheet_key(layout_sheet), [])
            generated_remnant_count = len(sheet_generated_remnants)
            generated_remnant_area_mm2 = sum(remnant.area_mm2 for remnant in sheet_generated_remnants)
            total_parts += part_count
            total_used_area_mm2 += used_area_mm2
            total_generated_remnants += generated_remnant_count
            total_generated_remnant_area_mm2 += generated_remnant_area_mm2
            utilization_ratio = _layout_sheet_utilization_ratio(layout_sheet, settings)
            values = (
                group_id,
                layout_sheet.source_kind,
                f"{layout_sheet.sheet_index:02d}",
                layout_sheet.source_label,
                f"{_format_mm(layout_sheet.source_length_mm)} x {_format_mm(layout_sheet.source_width_mm)} x "
                f"{_format_thickness_label(layout_sheet.source_thickness_mm)}",
                layout_sheet.material,
                _format_mm(layout_sheet.thickness_mm),
                layout_sheet.cut_method,
                layout_sheet.layout_strategy,
                _layout_mode_label(layout_sheet.cut_method, settings, layout_sheet.layout_strategy),
                _format_mm(process_profile.kerf_mm),
                _format_mm(process_profile.technical_margin_mm),
                str(part_count),
                _format_m2(used_area_mm2),
                _format_m2(usable_area_mm2),
                _format_percent(utilization_ratio),
                _format_mm(
                    used_area_mm2 / full_sheet_usable_area_mm2 if full_sheet_usable_area_mm2 else 0.0
                ),
                str(generated_remnant_count),
                _format_m2(generated_remnant_area_mm2),
            )
            for column_index, value in enumerate(values, start=1):
                sheet.set(_sheet_cell(column_index, row_index), value)
            row_index += 1

        total_usable_area_mm2 = sum(
            _sheet_usable_area_mm2(
                layout_sheet.source_length_mm,
                layout_sheet.source_width_mm,
                settings,
                layout_sheet.cut_method,
            )
            for layout_sheet in group_layout_sheets
        )
        group_process_profile = get_cut_process_profile(
            group_layout_sheets[0].cut_method,
            settings,
            group_layout_sheets[0].layout_strategy,
        )
        full_sheet_usable_area_mm2 = _sheet_usable_area_mm2(
            settings.length_mm,
            settings.width_mm,
            settings,
            group_layout_sheets[0].cut_method,
        )
        total_ratio = (
            total_used_area_mm2 / total_usable_area_mm2 if total_usable_area_mm2 else 0.0
        )
        total_equivalent_sheets = (
            total_used_area_mm2 / full_sheet_usable_area_mm2 if full_sheet_usable_area_mm2 else 0.0
        )
        total_values = (
            group_id,
            "Total",
            str(len(group_layout_sheets)),
            "",
            "",
            group_layout_sheets[0].material,
            _format_mm(group_layout_sheets[0].thickness_mm),
            group_layout_sheets[0].cut_method,
            group_layout_sheets[0].layout_strategy,
            _layout_mode_label(
                group_layout_sheets[0].cut_method,
                settings,
                group_layout_sheets[0].layout_strategy,
            ),
            _format_mm(group_process_profile.kerf_mm),
            _format_mm(group_process_profile.technical_margin_mm),
            str(total_parts),
            _format_m2(total_used_area_mm2),
            _format_m2(total_usable_area_mm2),
            _format_percent(total_ratio),
            _format_mm(total_equivalent_sheets),
            str(total_generated_remnants),
            _format_m2(total_generated_remnant_area_mm2),
        )
        for column_index, value in enumerate(total_values, start=1):
            sheet.set(_sheet_cell(column_index, row_index), value)
        row_index += 2

    doc.recompute()
    return sheet


def create_generated_remnants_spreadsheet(
    layout_sheets,
    settings,
    sheet_name=GENERATED_REMNANTS_SPREADSHEET_NAME,
):
    doc = ensure_document()

    existing_sheet = doc.getObject(sheet_name)
    if existing_sheet is not None:
        doc.removeObject(existing_sheet.Name)

    generated_remnants = collect_generated_remnants(layout_sheets, settings)
    if not generated_remnants:
        doc.recompute()
        return None

    try:
        import Spreadsheet  # noqa: F401
    except ImportError:
        pass

    sheet = doc.addObject("Spreadsheet::Sheet", sheet_name)
    _add_sheet_to_panelnest_group(doc, sheet)
    sheet.Label = "Retalhos Gerados PanelNest"

    headers = (
        "Grupo",
        "Chapa",
        "Origem",
        "Retalho",
        "Comprimento (mm)",
        "Largura (mm)",
        "Area (m2)",
        "X (mm)",
        "Y (mm)",
        "Material",
        "Espessura (mm)",
        "Metodo de corte",
        "Estrategia",
    )
    for column_index, header in enumerate(headers, start=1):
        sheet.set(_sheet_cell(column_index, 1), header)

    row_index = 2
    for remnant in generated_remnants:
        values = (
            remnant.group_id,
            f"{remnant.sheet_index:02d} - {remnant.source_label}",
            remnant.source_kind,
            remnant.label,
            _format_mm(remnant.length_mm),
            _format_mm(remnant.width_mm),
            _format_m2(remnant.area_mm2),
            _format_mm(remnant.x_mm),
            _format_mm(remnant.y_mm),
            remnant.material,
            _format_mm(remnant.thickness_mm),
            remnant.cut_method,
            remnant.layout_strategy,
        )
        for column_index, value in enumerate(values, start=1):
            sheet.set(_sheet_cell(column_index, row_index), value)
        row_index += 1

    doc.recompute()
    return sheet


def read_generated_remnants_spreadsheet(sheet_name=GENERATED_REMNANTS_SPREADSHEET_NAME):
    doc = ensure_document()
    sheet = doc.getObject(sheet_name)
    if sheet is None:
        raise ValueError(
            "A planilha de retalhos gerados nao foi encontrada. Gere o layout antes de importar os retalhos."
        )

    remnants = []
    row_index = 2
    empty_rows = 0

    while empty_rows < 25:
        group_id = _spreadsheet_cell_text(sheet, 1, row_index)
        chapa_text = _spreadsheet_cell_text(sheet, 2, row_index)
        source_kind = _spreadsheet_cell_text(sheet, 3, row_index)
        label = _spreadsheet_cell_text(sheet, 4, row_index)
        length_text = _spreadsheet_cell_text(sheet, 5, row_index)
        width_text = _spreadsheet_cell_text(sheet, 6, row_index)

        if not any((group_id, chapa_text, source_kind, label, length_text, width_text)):
            empty_rows += 1
            row_index += 1
            continue

        empty_rows = 0
        try:
            length_mm = _parse_sheet_number(length_text)
            width_mm = _parse_sheet_number(width_text)
            area_mm2 = _parse_sheet_number(_spreadsheet_cell_text(sheet, 7, row_index)) * 1_000_000.0
            x_mm = _parse_sheet_number(_spreadsheet_cell_text(sheet, 8, row_index))
            y_mm = _parse_sheet_number(_spreadsheet_cell_text(sheet, 9, row_index))
            thickness_mm = _parse_sheet_number(_spreadsheet_cell_text(sheet, 11, row_index))
        except ValueError:
            row_index += 1
            continue

        if length_mm <= 0 or width_mm <= 0:
            row_index += 1
            continue

        source_label = chapa_text
        sheet_index = 0
        if " - " in chapa_text:
            left_part, right_part = chapa_text.split(" - ", 1)
            try:
                sheet_index = int(left_part)
                source_label = right_part.strip() or chapa_text
            except ValueError:
                source_label = chapa_text

        remnants.append(
            GeneratedRemnant(
                group_id=group_id,
                sheet_index=sheet_index,
                source_label=source_label,
                source_kind=source_kind,
                label=label or f"Retalho {len(remnants) + 1:02d}",
                x_mm=x_mm,
                y_mm=y_mm,
                length_mm=length_mm,
                width_mm=width_mm,
                area_mm2=area_mm2 if area_mm2 > 0 else (length_mm * width_mm),
                material=_material_value(_spreadsheet_cell_text(sheet, 10, row_index)),
                thickness_mm=thickness_mm,
                cut_method=_spreadsheet_cell_text(sheet, 12, row_index),
                layout_strategy=_spreadsheet_cell_text(sheet, 13, row_index),
            )
        )
        row_index += 1

    return remnants


def _generated_remnant_stock_label(length_mm, width_mm, thickness_mm, material):
    label = (
        f"{GENERATED_REMNANT_LABEL_PREFIX} {_format_mm(length_mm)} x {_format_mm(width_mm)}"
    )
    if thickness_mm > 0:
        label += f" x {_format_mm(thickness_mm)} mm"
    if material:
        label += f" {material}"
    return label


def _save_remnants_to_db(remnants):
    """Salva lista de retalhos no banco SQLite persistente. Silencioso em caso de erro."""
    try:
        from .remnant_db import open_remnant_db, save_remnant
        doc = ensure_document()
        project_name = doc.Name if doc is not None else ""
        conn = open_remnant_db()
        try:
            for remnant in remnants:
                save_remnant(conn, remnant, project_name=project_name)
        finally:
            conn.close()
    except Exception:
        pass


def save_layout_remnants_to_db(layout_sheets, settings):
    """Salva todos os retalhos gerados pelo layout no banco histórico SQLite.
    Chamado automaticamente pelo generate_layout. Silencioso em caso de erro."""
    try:
        from .nesting import collect_generated_remnants
        remnants = collect_generated_remnants(layout_sheets, settings)
        _save_remnants_to_db(remnants)
    except Exception:
        pass


def sync_generated_remnants_to_stock(sheet_name=GENERATED_REMNANTS_SPREADSHEET_NAME):
    settings = get_sheet_settings()
    generated_remnants = read_generated_remnants_spreadsheet(sheet_name=sheet_name)
    if not generated_remnants:
        raise ValueError("A planilha de retalhos gerados nao contem itens importaveis.")

    preserved_remnants = [
        remnant
        for remnant in settings.remnants
        if not str(getattr(remnant, "label", "")).startswith(GENERATED_REMNANT_LABEL_PREFIX)
    ]
    consolidated_remnants = {}
    for remnant in generated_remnants:
        key = (
            round(remnant.length_mm, 4),
            round(remnant.width_mm, 4),
            round(remnant.thickness_mm, 4),
            _material_value(remnant.material),
        )
        if key not in consolidated_remnants:
            consolidated_remnants[key] = {
                "label": _generated_remnant_stock_label(
                    remnant.length_mm,
                    remnant.width_mm,
                    remnant.thickness_mm,
                    remnant.material,
                ),
                "length_mm": remnant.length_mm,
                "width_mm": remnant.width_mm,
                "thickness_mm": remnant.thickness_mm,
                "material": _material_value(remnant.material),
                "quantity": 0,
            }
        consolidated_remnants[key]["quantity"] += 1

    merged_remnants = list(preserved_remnants) + sorted(
        consolidated_remnants.values(),
        key=lambda item: (
            -(item["length_mm"] * item["width_mm"]),
            item["label"].casefold(),
        ),
    )
    updated_settings, config = update_sheet_settings(remnants=merged_remnants)

    # Persistir retalhos no banco SQLite para uso em projetos futuros
    _save_remnants_to_db(generated_remnants)

    return {
        "updated_settings": updated_settings,
        "config": config,
        "generated_count": len(generated_remnants),
        "generated_types": len(consolidated_remnants),
        "replaced_generated_types": len(settings.remnants) - len(preserved_remnants),
    }


def create_cut_plan_spreadsheet(
    layout_sheets,
    sheet_name=CUT_PLAN_SPREADSHEET_NAME,
):
    doc = ensure_document()

    existing_sheet = doc.getObject(sheet_name)
    if existing_sheet is not None:
        doc.removeObject(existing_sheet.Name)

    cut_plan_sheets = [
        layout_sheet
        for layout_sheet in layout_sheets
        if layout_sheet.cut_steps and normalize_cut_method(layout_sheet.cut_method) == "Seccionadora"
    ]
    if not cut_plan_sheets:
        doc.recompute()
        return None

    try:
        import Spreadsheet  # noqa: F401
    except ImportError:
        pass

    sheet = doc.addObject("Spreadsheet::Sheet", sheet_name)
    _add_sheet_to_panelnest_group(doc, sheet)
    sheet.Label = "Plano de Corte PanelNest"

    headers = (
        "Grupo",
        "Chapa",
        "Metodo de corte",
        "Estrategia",
        "Passo",
        "Etapa",
        "Zona",
        "Tipo de corte",
        "Orientacao",
        "Posicao (mm)",
        "Inicio X (mm)",
        "Inicio Y (mm)",
        "Fim X (mm)",
        "Fim Y (mm)",
        "Comprimento (mm)",
        "Peca alvo",
        "Descricao",
    )
    for column_index, header in enumerate(headers, start=1):
        sheet.set(_sheet_cell(column_index, 1), header)

    row_index = 2
    for layout_sheet in cut_plan_sheets:
        zone_aliases = _layout_sheet_sequence_zone_labels(layout_sheet)
        sheet_name_label = (
            f"{layout_sheet.group_id} - {layout_sheet.source_label} "
            f"({_format_mm(layout_sheet.source_length_mm)} x {_format_mm(layout_sheet.source_width_mm)} x "
            f"{_format_thickness_label(layout_sheet.source_thickness_mm)})"
        )
        for cut_step in layout_sheet.cut_steps:
            values = (
                layout_sheet.group_id,
                sheet_name_label,
                layout_sheet.cut_method,
                layout_sheet.layout_strategy,
                str(cut_step.step_index),
                _cut_phase_label(cut_step),
                _sequence_zone_display_label(cut_step.sequence_zone, zone_aliases),
                cut_step.cut_kind,
                cut_step.orientation,
                _format_mm(cut_step.position_mm),
                _format_mm(cut_step.start_x_mm),
                _format_mm(cut_step.start_y_mm),
                _format_mm(cut_step.end_x_mm),
                _format_mm(cut_step.end_y_mm),
                _format_mm(cut_step.span_mm),
                cut_step.target_part_id,
                cut_step.description,
            )
            for column_index, value in enumerate(values, start=1):
                sheet.set(_sheet_cell(column_index, row_index), value)
            row_index += 1
        row_index += 1

    doc.recompute()
    return sheet


def collect_layout_warnings(parts, layout_sheets, settings):
    warnings = []
    groups = group_parts(parts)
    available_stock_sheets = _build_available_stock_sheets(settings)
    used_stock_labels = {layout_sheet.source_label for layout_sheet in layout_sheets}

    for stock_piece in available_stock_sheets:
        if stock_piece.label in used_stock_labels or stock_piece.kind != "Retalho":
            continue

        matched_group = False
        size_fit_found = False
        mismatch_reasons = set()
        compatible_group_ids = []

        for group in groups:
            if not _thickness_matches(stock_piece, group["thickness_mm"]):
                mismatch_reasons.add(
                    f"espessura do retalho {_format_mm(stock_piece.thickness_mm)} mm "
                    f"incompativel com o grupo {_format_mm(group['thickness_mm'])} mm"
                )
                continue
            if not _material_matches(stock_piece, group["material"]):
                mismatch_reasons.add(
                    f"material do retalho '{stock_piece.material}' "
                    f"incompativel com o grupo '{group['material'] or 'Sem material'}'"
                )
                continue

            matched_group = True
            compatible_group_ids.append(group["group_id"])
            if any(_part_fits_stock_piece(part, stock_piece, settings) for part in group["parts"]):
                size_fit_found = True

        if not matched_group:
            reason = "; ".join(sorted(mismatch_reasons)) or "nao encontrou grupo compativel"
            warnings.append(
                {
                    "kind": "Retalho ignorado",
                    "group_id": "",
                    "stock": _stock_piece_display_name(stock_piece),
                    "reason": reason,
                }
            )
        elif not size_fit_found:
            fallback_sources = []
            for layout_sheet in layout_sheets:
                if layout_sheet.group_id not in compatible_group_ids:
                    continue
                if layout_sheet.source_kind not in {"Chapa inteira", "Chapa extra"}:
                    continue
                display_name = _layout_sheet_display_name(layout_sheet)
                if display_name not in fallback_sources:
                    fallback_sources.append(display_name)

            reason = "o retalho nao comporta nenhuma peca dos grupos compativeis"
            if fallback_sources:
                if len(fallback_sources) == 1:
                    reason += f"; por isso, as pecas foram redistribuidas para {fallback_sources[0]}"
                else:
                    reason += (
                        f"; por isso, as pecas foram redistribuidas entre {fallback_sources[0]} "
                        f"e mais {len(fallback_sources) - 1} chapa(s)"
                    )
            warnings.append(
                {
                    "kind": "Retalho ignorado",
                    "group_id": "",
                    "stock": _stock_piece_display_name(stock_piece),
                    "reason": reason,
                }
            )

    for layout_sheet in layout_sheets:
        if layout_sheet.source_kind != "Chapa extra":
            continue
        warnings.append(
            {
                "kind": "Chapa extra aberta",
                "group_id": layout_sheet.group_id,
                "stock": _layout_sheet_display_name(layout_sheet),
                "reason": "o estoque configurado nao foi suficiente para acomodar todas as pecas do grupo",
            }
        )

    return warnings


def create_layout_warnings_spreadsheet(
    warnings,
    sheet_name=LAYOUT_WARNINGS_SPREADSHEET_NAME,
):
    doc = ensure_document()

    existing_sheet = doc.getObject(sheet_name)
    if existing_sheet is not None:
        doc.removeObject(existing_sheet.Name)

    if not warnings:
        doc.recompute()
        return None

    try:
        import Spreadsheet  # noqa: F401
    except ImportError:
        pass

    sheet = doc.addObject("Spreadsheet::Sheet", sheet_name)
    _add_sheet_to_panelnest_group(doc, sheet)
    sheet.Label = "Alertas de Layout PanelNest"

    headers = ("Tipo", "Grupo", "Estoque", "Motivo")
    for column_index, header in enumerate(headers, start=1):
        sheet.set(_sheet_cell(column_index, 1), header)

    for row_index, warning in enumerate(warnings, start=2):
        values = (
            warning.get("kind", ""),
            warning.get("group_id", ""),
            warning.get("stock", ""),
            warning.get("reason", ""),
        )
        for column_index, value in enumerate(values, start=1):
            sheet.set(_sheet_cell(column_index, row_index), value)

    doc.recompute()
    return sheet



def collect_parts_to_spreadsheet(objects=None, sheet_name=PARTS_SPREADSHEET_NAME):
    parts = collect_parts(objects)
    if not parts:
        raise ValueError(
            "Nenhuma peca valida foi encontrada. Selecione objetos solidos ou deixe visiveis apenas as pecas desejadas."
        )
    sheet = create_parts_spreadsheet(parts, sheet_name=sheet_name)
    return parts, sheet

def create_parts_spreadsheet(parts, sheet_name=PARTS_SPREADSHEET_NAME):
    doc = ensure_document()

    try:
        import Spreadsheet  # noqa: F401
    except ImportError:
        pass

    existing_sheet = doc.getObject(sheet_name)
    if existing_sheet is not None:
        doc.removeObject(existing_sheet.Name)

    sheet = doc.addObject("Spreadsheet::Sheet", sheet_name)
    _add_sheet_to_panelnest_group(doc, sheet)
    sheet.Label = "Pecas PanelNest"
    part_groups = {}
    for group in group_parts(parts):
        for part in group["parts"]:
            part_groups[part.object_name] = group["group_id"]

    headers = (
        "Grupo",
        "ID da peca",
        "Nome do objeto",
        "Rotulo",
        "Comprimento (mm)",
        "Largura (mm)",
        "Espessura (mm)",
        "Qtd",
        "Material",
        "Metodo de corte",
        "Rotacao permitida",
        "Veio/fibra",
        "Fita de borda",
        "TypeId",
    )
    for column_index, header in enumerate(headers, start=1):
        sheet.set(_sheet_cell(column_index, 1), header)

    for row_index, part in enumerate(parts, start=2):
        values = (
            part_groups.get(part.object_name, ""),
            part.part_id,
            part.object_name,
            part.label,
            _format_mm(part.length_mm),
            _format_mm(part.width_mm),
            _format_mm(part.thickness_mm),
            str(part.quantity),
            part.material,
            part.cut_method,
            _allow_rotation_summary(part),
            normalize_grain_direction(part.grain_direction),
            _edge_band_summary(part),
            part.source_type,
        )
        for column_index, value in enumerate(values, start=1):
            sheet.set(_sheet_cell(column_index, row_index), value)

    doc.recompute()
    return sheet


def create_organization_spreadsheet(parts, sheet_name=ORGANIZATION_SPREADSHEET_NAME):
    doc = ensure_document()
    parts = _expanded_report_parts(parts)

    try:
        import Spreadsheet  # noqa: F401
    except ImportError:
        pass

    existing_sheet = doc.getObject(sheet_name)
    if existing_sheet is not None:
        doc.removeObject(existing_sheet.Name)

    sheet = doc.addObject("Spreadsheet::Sheet", sheet_name)
    _add_sheet_to_panelnest_group(doc, sheet)
    sheet.Label = "Organizacao PanelNest"

    headers = (
        "Grupo",
        "Material",
        "Espessura (mm)",
        "Metodo de corte",
        "Rotacao permitida",
        "Veio/fibra",
        "ID da peca",
        "Rotulo",
        "Comprimento (mm)",
        "Largura (mm)",
        "Qtd",
        "Fita de borda",
        "Objeto",
    )

    for column_index, header in enumerate(headers, start=1):
        sheet.set(_sheet_cell(column_index, 1), header)

    row_index = 2
    for group in group_parts(parts):
        for part in group["parts"]:
            values = (
                group["group_id"],
                group["material"],
                _format_mm(group["thickness_mm"]),
                group["cut_method"],
                _allow_rotation_summary(part),
                normalize_grain_direction(part.grain_direction),
                part.part_id,
                part.label,
                _format_mm(part.length_mm),
                _format_mm(part.width_mm),
                str(part.quantity),
                _edge_band_summary(part),
                part.object_name,
            )
            for column_index, value in enumerate(values, start=1):
                sheet.set(_sheet_cell(column_index, row_index), value)
            row_index += 1

        row_index += 1

    doc.recompute()
    return sheet


def _part_edge_band_metrics(part):
    top_mm = part.length_mm if part.edge_band_top else 0.0
    bottom_mm = part.length_mm if part.edge_band_bottom else 0.0
    left_mm = part.width_mm if part.edge_band_left else 0.0
    right_mm = part.width_mm if part.edge_band_right else 0.0
    banded_side_count = sum(1 for value in (top_mm, bottom_mm, left_mm, right_mm) if value > 0.0)
    linear_length_mm = top_mm + bottom_mm + left_mm + right_mm
    quantity = max(1, int(getattr(part, "quantity", 1) or 1))
    summary_parts = []
    if top_mm > 0.0:
        summary_parts.append(f"Superior {_format_mm(top_mm)}")
    if bottom_mm > 0.0:
        summary_parts.append(f"Inferior {_format_mm(bottom_mm)}")
    if left_mm > 0.0:
        summary_parts.append(f"Esquerda {_format_mm(left_mm)}")
    if right_mm > 0.0:
        summary_parts.append(f"Direita {_format_mm(right_mm)}")
    return {
        "top_mm": top_mm,
        "bottom_mm": bottom_mm,
        "left_mm": left_mm,
        "right_mm": right_mm,
        "banded_side_count": banded_side_count,
        "linear_length_mm": linear_length_mm,
        "total_linear_length_mm": linear_length_mm * quantity,
        "quantity": quantity,
        "summary": ", ".join(summary_parts),
    }


def create_edge_band_spreadsheet(parts, sheet_name=EDGE_BAND_SPREADSHEET_NAME):
    doc = ensure_document()
    parts = _expanded_report_parts(parts)

    try:
        import Spreadsheet  # noqa: F401
    except ImportError:
        pass

    existing_sheet = doc.getObject(sheet_name)
    if existing_sheet is not None:
        doc.removeObject(existing_sheet.Name)

    grouped_parts = group_parts(parts)
    report_rows = []
    overall_piece_count = 0
    overall_quantity = 0
    overall_side_count = 0
    overall_linear_length_mm = 0.0

    for group in grouped_parts:
        group_piece_count = 0
        group_quantity = 0
        group_side_count = 0
        group_linear_length_mm = 0.0

        for part in group["parts"]:
            metrics = _part_edge_band_metrics(part)
            if metrics["banded_side_count"] <= 0:
                continue

            report_rows.append(("Peca", group, part, metrics))
            group_piece_count += 1
            group_quantity += metrics["quantity"]
            group_side_count += metrics["banded_side_count"]
            group_linear_length_mm += metrics["total_linear_length_mm"]

        if group_piece_count > 0:
            report_rows.append(
                (
                    "Total",
                    group,
                    {
                        "piece_count": group_piece_count,
                        "quantity": group_quantity,
                        "side_count": group_side_count,
                        "linear_length_mm": group_linear_length_mm,
                    },
                )
            )
            report_rows.append(("Blank",))
            overall_piece_count += group_piece_count
            overall_quantity += group_quantity
            overall_side_count += group_side_count
            overall_linear_length_mm += group_linear_length_mm

    if not report_rows:
        doc.recompute()
        return None

    while report_rows and report_rows[-1][0] == "Blank":
        report_rows.pop()

    sheet = doc.addObject("Spreadsheet::Sheet", sheet_name)
    _add_sheet_to_panelnest_group(doc, sheet)
    sheet.Label = "Acabamento PanelNest"

    headers = (
        "Tipo",
        "Grupo",
        "Material",
        "Espessura (mm)",
        "ID da peca",
        "Rotulo",
        "Comprimento (mm)",
        "Largura (mm)",
        "Qtd",
        "Borda superior (mm)",
        "Borda inferior (mm)",
        "Borda esquerda (mm)",
        "Borda direita (mm)",
        "Qtd de bordas",
        "Metragem por peca (m)",
        "Metragem total (m)",
        "Resumo bordas",
        "Objeto",
    )
    for column_index, header in enumerate(headers, start=1):
        sheet.set(_sheet_cell(column_index, 1), header)

    row_index = 2
    for row in report_rows:
        row_kind = row[0]

        if row_kind == "Blank":
            row_index += 1
            continue

        if row_kind == "Peca":
            _row_kind, group, part, metrics = row
            values = (
                "Peca",
                group["group_id"],
                group["material"],
                _format_mm(group["thickness_mm"]),
                part.part_id,
                part.label,
                _format_mm(part.length_mm),
                _format_mm(part.width_mm),
                str(metrics["quantity"]),
                _format_mm(metrics["top_mm"]) if metrics["top_mm"] > 0.0 else "",
                _format_mm(metrics["bottom_mm"]) if metrics["bottom_mm"] > 0.0 else "",
                _format_mm(metrics["left_mm"]) if metrics["left_mm"] > 0.0 else "",
                _format_mm(metrics["right_mm"]) if metrics["right_mm"] > 0.0 else "",
                str(metrics["banded_side_count"]),
                _format_m(metrics["linear_length_mm"]),
                _format_m(metrics["total_linear_length_mm"]),
                metrics["summary"],
                part.object_name,
            )
        else:
            _row_kind, group, totals = row
            values = (
                "Total",
                group["group_id"],
                group["material"],
                _format_mm(group["thickness_mm"]),
                "",
                f"{totals['piece_count']} peca(s) com borda",
                "",
                "",
                str(totals["quantity"]),
                "",
                "",
                "",
                "",
                str(totals["side_count"]),
                "",
                _format_m(totals["linear_length_mm"]),
                "",
                "",
            )

        for column_index, value in enumerate(values, start=1):
            sheet.set(_sheet_cell(column_index, row_index), value)
        row_index += 1

    total_values = (
        "Total geral",
        "",
        "",
        "",
        "",
        f"{overall_piece_count} peca(s) com borda",
        "",
        "",
        str(overall_quantity),
        "",
        "",
        "",
        "",
        str(overall_side_count),
        "",
        _format_m(overall_linear_length_mm),
        "",
        "",
    )
    for column_index, value in enumerate(total_values, start=1):
        sheet.set(_sheet_cell(column_index, row_index), value)

    doc.recompute()
    return sheet


def refresh_part_reporting_sheets(parts=None, document=None):
    doc = document or ensure_document()
    parts = parts if parts is not None else collect_parts()
    refreshed = {}

    if doc.getObject(PARTS_SPREADSHEET_NAME) is not None:
        refreshed["parts"] = create_parts_spreadsheet(parts)
    if doc.getObject(ORGANIZATION_SPREADSHEET_NAME) is not None:
        refreshed["organization"] = create_organization_spreadsheet(parts)
    if doc.getObject(LABELS_SPREADSHEET_NAME) is not None:
        refreshed["labels"] = create_part_labels_spreadsheet(parts)
    if doc.getObject(VALIDATION_SPREADSHEET_NAME) is not None:
        refreshed["validation"] = create_project_validation_spreadsheet(
            parts=parts,
            document=doc,
        )
    if doc.getObject(EDGE_BAND_SPREADSHEET_NAME) is not None or any(
        _part_edge_band_metrics(part)["banded_side_count"] > 0 for part in parts
    ):
        refreshed["edge_band"] = create_edge_band_spreadsheet(parts)

    return refreshed




def create_cost_report_spreadsheet(layout_sheets, settings, sheet_name=COST_REPORT_SPREADSHEET_NAME):
    """Cria a planilha PanelNestCustos com custo por chapa e resumo de desperdício.

    Colunas: Chapa | Material | Espessura | Qtd peças | Aproveitamento % |
             Área útil m² | Área desperdício m² | Custo/chapa R$ | Custo total R$ | Custo desperdício R$

    Exige que SheetStockPiece.cost_per_sheet e SheetSettings não sejam zero para
    mostrar valores monetários reais — caso contrário exibe "-".
    """
    from .nesting import _layout_sheet_utilization_ratio, _layout_sheet_used_area_mm2, _layout_sheet_usable_rect

    doc = ensure_document()
    _remove_sheet_if_exists(doc, sheet_name)
    sheet = doc.addObject("Spreadsheet::Sheet", sheet_name)
    _add_sheet_to_panelnest_group(doc, sheet)
    sheet.Label = sheet_name

    header_bg = (0.18, 0.33, 0.52)
    header_fg = (1.0, 1.0, 1.0)
    total_bg = (0.90, 0.94, 0.98)
    number_bg = (0.97, 0.97, 0.97)

    headers = [
        "Chapa", "Material", "Esp.(mm)", "Qtd peças",
        "Aprov. %", "Área útil m²", "Desperdício m²",
        "Custo/chapa R$", "Custo total R$", "Custo desp. R$",
    ]

    def _cell(col, row):
        return chr(ord("A") + col) + str(row)

    def _set(col, row, value):
        sheet.set(_cell(col, row), str(value) if not isinstance(value, str) else value)

    def _style(col, row, bg=None, fg=None, bold=False, align=None):
        try:
            cell = _cell(col, row)
            if bg:
                sheet.setBackground(cell, bg + (1.0,))
            if fg:
                sheet.setForeground(cell, fg + (1.0,))
            if bold:
                sheet.set(cell, sheet.get(cell))  # force re-read for bold
                try:
                    sheet.setStyle(cell, "bold")
                except Exception:
                    pass
            if align:
                try:
                    sheet.setAlignment(cell, align)
                except Exception:
                    pass
        except Exception:
            pass

    # Cabeçalho
    row = 1
    for col, h in enumerate(headers):
        _set(col, row, h)
        _style(col, row, bg=header_bg, fg=header_fg, bold=True)
    row += 1

    total_sheets_count = 0
    total_useful_area = 0.0
    total_waste_area = 0.0
    total_cost = 0.0
    total_waste_cost = 0.0

    # Agrupar layout_sheets por (material, thickness_mm)
    groups = {}
    for ls in layout_sheets:
        key = (ls.material, ls.thickness_mm)
        groups.setdefault(key, []).append(ls)

    for (material, thickness), sheets_group in sorted(groups.items()):
        for ls in sheets_group:
            utilization = _layout_sheet_utilization_ratio(ls, settings)
            usable_rect = _layout_sheet_usable_rect(ls, settings)
            usable_area = usable_rect["length_mm"] * usable_rect["width_mm"]
            used_area = _layout_sheet_used_area_mm2(ls)
            waste_area = max(0.0, usable_area - used_area)

            # Custo (opcional — depende de cost_per_sheet nos estoques)
            cost = getattr(ls, "_cost_per_sheet", 0.0)
            waste_cost = cost * (waste_area / usable_area) if usable_area > 0 and cost > 0 else 0.0

            piece_count = len(ls.placements)
            name = f"Chapa {ls.sheet_index + 1} ({ls.source_kind})"

            row_data = [
                name,
                material or "-",
                f"{thickness:.1f}",
                str(piece_count),
                f"{utilization * 100:.1f}",
                f"{used_area / 1_000_000:.4f}",
                f"{waste_area / 1_000_000:.4f}",
                f"{cost:.2f}" if cost > 0 else "-",
                f"{cost:.2f}" if cost > 0 else "-",
                f"{waste_cost:.2f}" if waste_cost > 0 else "-",
            ]
            for col, val in enumerate(row_data):
                _set(col, row, val)
                if col >= 4:
                    _style(col, row, bg=number_bg, align="right|vcenter")
            row += 1

            total_sheets_count += 1
            total_useful_area += used_area
            total_waste_area += waste_area
            total_cost += cost
            total_waste_cost += waste_cost

    # Linha separadora + totais
    row += 1
    total_row = [
        f"TOTAL ({total_sheets_count} chapas)", "", "", "",
        "",
        f"{total_useful_area / 1_000_000:.4f}",
        f"{total_waste_area / 1_000_000:.4f}",
        "",
        f"{total_cost:.2f}" if total_cost > 0 else "-",
        f"{total_waste_cost:.2f}" if total_waste_cost > 0 else "-",
    ]
    if total_useful_area + total_waste_area > 0:
        pct = total_useful_area / (total_useful_area + total_waste_area) * 100
        total_row[4] = f"{pct:.1f}"

    for col, val in enumerate(total_row):
        _set(col, row, val)
        _style(col, row, bg=total_bg, bold=True)

    doc.recompute()
    return sheet


def create_edge_band_consumption_spreadsheet(
    parts,
    settings,
    sheet_name=EDGE_BAND_CONSUMPTION_SPREADSHEET_NAME,
):
    """Planilha PanelNestFita — consumo de fita de borda por grupo/material.

    Colunas: Grupo | Material | Esp. | Qtd peças c/ fita | Lados | Metros líquidos |
             Desperdício % | Metros c/ desperdício | Custo est. R$

    Se settings.edge_band_price_per_m == 0, a coluna de custo exibe "-".
    """
    doc = ensure_document()
    _remove_sheet_if_exists(doc, sheet_name)

    parts_expanded = _expanded_report_parts(parts)
    grouped_parts = group_parts(parts_expanded)

    # Acumular métricas por grupo
    group_rows = []
    grand_piece_count = 0
    grand_qty = 0
    grand_sides = 0
    grand_linear_mm = 0.0

    for group in grouped_parts:
        g_piece_count = 0
        g_qty = 0
        g_sides = 0
        g_linear_mm = 0.0

        for part in group["parts"]:
            metrics = _part_edge_band_metrics(part)
            if metrics["banded_side_count"] <= 0:
                continue
            g_piece_count += 1
            g_qty += metrics["quantity"]
            g_sides += metrics["banded_side_count"] * metrics["quantity"]
            g_linear_mm += metrics["total_linear_length_mm"]

        if g_piece_count == 0:
            continue

        group_rows.append({
            "group_id": group["group_id"],
            "material": group["material"] or "Sem material",
            "thickness_mm": group["thickness_mm"],
            "piece_count": g_piece_count,
            "quantity": g_qty,
            "sides": g_sides,
            "linear_mm": g_linear_mm,
        })
        grand_piece_count += g_piece_count
        grand_qty += g_qty
        grand_sides += g_sides
        grand_linear_mm += g_linear_mm

    if not group_rows:
        return None

    sheet = doc.addObject("Spreadsheet::Sheet", sheet_name)
    _add_sheet_to_panelnest_group(doc, sheet)
    sheet.Label = "Fita de Borda PanelNest"

    waste_factor = float(getattr(settings, "edge_band_waste_factor", 0.10) or 0.10)
    price_per_m = float(getattr(settings, "edge_band_price_per_m", 0.0) or 0.0)
    label = str(getattr(settings, "edge_band_label", "") or "")
    has_price = price_per_m > 0.0

    header_bg = (0.18, 0.33, 0.18)
    total_bg = (0.15, 0.28, 0.15)
    alt_bg = (0.94, 0.97, 0.94)

    def _set(col, row, val):
        sheet.set(_sheet_cell(col + 1, row), str(val) if val is not None else "")

    def _style(col, row, bg=None, bold=False, fg=None):
        cell = _sheet_cell(col + 1, row)
        if bg:
            r, g, b = bg
            sheet.setBackground(cell, (r, g, b, 1.0))
            if fg is None:
                fg = (1.0, 1.0, 1.0)
        if fg:
            sheet.setForeground(cell, (*fg, 1.0))
        if bold:
            sheet.setStyle(cell, "bold")

    headers = [
        "Grupo", "Material", "Esp. (mm)", "Pecas c/ fita",
        "Qtd total", "Lados totais",
        "Metros liquidos (m)", f"Desperdicio ({waste_factor*100:.0f}%)",
        "Metros c/ desperdicio (m)",
        "Custo est. R$" if has_price else "Custo est. R$ (sem preco)",
    ]
    for col, h in enumerate(headers):
        _set(col, 1, h)
        _style(col, 1, bg=header_bg, bold=True)

    row = 2

    # Linha informativa com tipo de fita
    if label:
        _set(0, row, f"Tipo de fita: {label}")
        _style(0, row, bold=True, fg=(0.1, 0.4, 0.1))
        row += 1

    for i, g in enumerate(group_rows):
        net_m = g["linear_mm"] / 1000.0
        gross_m = net_m * (1.0 + waste_factor)
        cost = gross_m * price_per_m if has_price else None
        bg = alt_bg if i % 2 == 0 else None
        values = [
            g["group_id"],
            g["material"],
            _format_mm(g["thickness_mm"]),
            str(g["piece_count"]),
            str(g["quantity"]),
            str(g["sides"]),
            f"{net_m:.3f}",
            f"{(gross_m - net_m):.3f}",
            f"{gross_m:.3f}",
            f"{cost:.2f}" if cost is not None else "-",
        ]
        for col, val in enumerate(values):
            _set(col, row, val)
            if bg:
                _style(col, row, bg=bg, fg=(0.1, 0.1, 0.1))
        row += 1

    # Linha de total
    grand_net_m = grand_linear_mm / 1000.0
    grand_gross_m = grand_net_m * (1.0 + waste_factor)
    grand_cost = grand_gross_m * price_per_m if has_price else None
    total_values = [
        f"TOTAL ({len(group_rows)} grupo(s))",
        "",
        "",
        str(grand_piece_count),
        str(grand_qty),
        str(grand_sides),
        f"{grand_net_m:.3f}",
        f"{(grand_gross_m - grand_net_m):.3f}",
        f"{grand_gross_m:.3f}",
        f"{grand_cost:.2f}" if grand_cost is not None else "-",
    ]
    for col, val in enumerate(total_values):
        _set(col, row, val)
        _style(col, row, bg=total_bg, bold=True)

    doc.recompute()
    return sheet


def create_hardware_spreadsheet(parts, settings, sheet_name=HARDWARE_SPREADSHEET_NAME):
    """Planilha PanelNestFerragens — total de ferragens por tipo.

    Colunas: ID | Nome | Unidade | Qtd total | Custo unit. R$ | Custo total R$

    Só gerada se pelo menos uma peça tiver ferragens associadas.
    """
    doc = ensure_document()
    _remove_sheet_if_exists(doc, sheet_name)

    catalog = getattr(settings, "hardware_catalog", []) or []
    # Monta dict hw_id -> item do catálogo
    catalog_map = {}
    for item in catalog:
        hw_id = getattr(item, "hw_id", None) or (item.get("hw_id") if isinstance(item, dict) else None)
        if hw_id:
            catalog_map[hw_id] = item

    def _hw_name(hw_id):
        item = catalog_map.get(hw_id)
        if item is None:
            return hw_id
        return getattr(item, "name", None) or (item.get("name", hw_id) if isinstance(item, dict) else hw_id)

    def _hw_unit(hw_id):
        item = catalog_map.get(hw_id)
        if item is None:
            return "un"
        return getattr(item, "unit", "un") or (item.get("unit", "un") if isinstance(item, dict) else "un")

    def _hw_cost(hw_id):
        item = catalog_map.get(hw_id)
        if item is None:
            return 0.0
        return float(getattr(item, "cost", 0.0) or (item.get("cost", 0.0) if isinstance(item, dict) else 0.0))

    # Acumula {hw_id: total_qty} respeitando quantity da peça
    totals = {}
    part_detail_rows = []  # (part_id, label, hw_id, qty_per, total)

    for part in parts:
        hw_list = getattr(part, "hardware", None) or []
        if not hw_list:
            continue
        part_qty = max(1, int(getattr(part, "quantity", 1) or 1))
        for entry in hw_list:
            hw_id = entry.get("hw_id", "")
            qty_per = int(entry.get("qty", 1) or 1)
            total = qty_per * part_qty
            if hw_id:
                totals[hw_id] = totals.get(hw_id, 0) + total
                part_detail_rows.append((part.part_id, part.label, hw_id, qty_per, total))

    if not totals:
        return None

    sheet = doc.addObject("Spreadsheet::Sheet", sheet_name)
    _add_sheet_to_panelnest_group(doc, sheet)
    sheet.Label = "Ferragens PanelNest"

    header_bg = (0.18, 0.28, 0.45)
    total_bg = (0.12, 0.20, 0.38)
    alt_bg = (0.92, 0.94, 0.98)

    def _set(col, row, val):
        sheet.set(_sheet_cell(col + 1, row), str(val) if val is not None else "")

    def _style(col, row, bg=None, bold=False, fg=None):
        cell = _sheet_cell(col + 1, row)
        if bg:
            r, g, b = bg
            sheet.setBackground(cell, (r, g, b, 1.0))
            if fg is None:
                fg = (1.0, 1.0, 1.0)
        if fg:
            sheet.setForeground(cell, (*fg, 1.0))
        if bold:
            sheet.setStyle(cell, "bold")

    # ---- Seção 1: Resumo por tipo de ferragem ----
    summary_headers = ["ID", "Nome", "Unidade", "Qtd total", "Custo unit. R$", "Custo total R$"]
    for col, h in enumerate(summary_headers):
        _set(col, 1, h)
        _style(col, 1, bg=header_bg, bold=True)

    row = 2
    grand_cost = 0.0
    for i, (hw_id, qty_total) in enumerate(sorted(totals.items(), key=lambda x: _hw_name(x[0]))):
        unit_cost = _hw_cost(hw_id)
        total_cost = unit_cost * qty_total
        grand_cost += total_cost
        bg = alt_bg if i % 2 == 0 else None
        values = [
            hw_id,
            _hw_name(hw_id),
            _hw_unit(hw_id),
            str(qty_total),
            f"{unit_cost:.2f}" if unit_cost > 0 else "-",
            f"{total_cost:.2f}" if unit_cost > 0 else "-",
        ]
        for col, val in enumerate(values):
            _set(col, row, val)
            if bg:
                _style(col, row, bg=bg, fg=(0.1, 0.1, 0.1))
        row += 1

    # Linha de total
    total_row_values = [
        f"TOTAL ({len(totals)} tipo(s))", "", "", "", "",
        f"{grand_cost:.2f}" if grand_cost > 0 else "-",
    ]
    for col, val in enumerate(total_row_values):
        _set(col, row, val)
        _style(col, row, bg=total_bg, bold=True)
    row += 2

    # ---- Seção 2: Detalhe por peça ----
    detail_headers = ["Peça ID", "Rótulo", "Ferragem", "Qtd/peça", "Qtd total (c/ repetição)"]
    for col, h in enumerate(detail_headers):
        _set(col, row, h)
        _style(col, row, bg=header_bg, bold=True)
    row += 1

    for i, (part_id, label, hw_id, qty_per, total) in enumerate(part_detail_rows):
        bg = alt_bg if i % 2 == 0 else None
        values = [part_id, label, _hw_name(hw_id), str(qty_per), str(total)]
        for col, val in enumerate(values):
            _set(col, row, val)
            if bg:
                _style(col, row, bg=bg, fg=(0.1, 0.1, 0.1))
        row += 1

    doc.recompute()
    return sheet
