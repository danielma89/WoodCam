try:
    import FreeCAD as App
except ImportError:
    App = None

from .constants import (
    VALIDATION_SEVERITY_ORDER,
    PARTS_SPREADSHEET_NAME,
    VALIDATION_SPREADSHEET_NAME,
    PART_LABEL_PATTERN,
)
from .freecad_utils import ensure_document
from .metadata import _format_mm, _format_m2, _format_m, _format_percent, _material_value, normalize_cut_method
from .parts import group_parts, collect_parts
from .nesting import _part_orientation_options


def _sheet_cell(column_index, row_index):
    return f"{chr(64 + column_index)}{row_index}"


def _lazy_collect_layout_warnings(parts, layout_sheets, settings):
    from .spreadsheets import collect_layout_warnings
    return collect_layout_warnings(parts, layout_sheets, settings)


def _lazy_create_layout_sheets(parts, settings):
    from .nesting import create_layout_sheets
    return create_layout_sheets(parts, settings=settings)


def _lazy_get_sheet_settings():
    from .metadata import get_sheet_settings
    return get_sheet_settings()


def _validation_issue(
    severity,
    category,
    problem,
    suggestion="",
    group_id="",
    part_id="",
    part_label="",
    object_name="",
):
    return {
        "severity": str(severity or "Info"),
        "category": str(category or ""),
        "group_id": str(group_id or ""),
        "part_id": str(part_id or ""),
        "part_label": str(part_label or ""),
        "problem": str(problem or ""),
        "suggestion": str(suggestion or ""),
        "object_name": str(object_name or ""),
    }


def _validation_issue_sort_key(issue):
    return (
        VALIDATION_SEVERITY_ORDER.get(issue.get("severity", "Info"), 99),
        str(issue.get("category", "")).casefold(),
        str(issue.get("group_id", "")).casefold(),
        str(issue.get("part_id", "")).casefold(),
        str(issue.get("part_label", "")).casefold(),
        str(issue.get("problem", "")).casefold(),
    )


def _deduplicate_validation_issues(issues):
    seen = set()
    deduplicated = []

    for issue in sorted(list(issues or []), key=_validation_issue_sort_key):
        key = (
            issue.get("severity", ""),
            issue.get("category", ""),
            issue.get("group_id", ""),
            issue.get("part_id", ""),
            issue.get("part_label", ""),
            issue.get("problem", ""),
            issue.get("suggestion", ""),
            issue.get("object_name", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(issue)

    return deduplicated


def _validation_part_label(part):
    raw_label = str(getattr(part, "label", "") or "").strip()
    return PART_LABEL_PATTERN.sub("", raw_label).strip() or raw_label


def _validation_piece_quantity(part):
    return max(1, int(getattr(part, "quantity", 1) or 1))


def _validation_parts_reference(parts, limit=4):
    references = []
    for part in list(parts or []):
        reference = str(getattr(part, "part_id", "") or "").strip()
        if not reference:
            reference = _validation_part_label(part)
        if not reference:
            reference = str(getattr(part, "object_name", "") or "").strip()
        if not reference or reference in references:
            continue
        references.append(reference)

    if not references:
        return ""

    preview = ", ".join(references[:limit])
    remaining = len(references) - min(len(references), limit)
    if remaining > 0:
        preview += f" e mais {remaining}"
    return preview


def _grouped_piece_validation_issue(
    severity,
    grouped_parts,
    problem_builder,
    suggestion,
    include_part_reference=True,
):
    issues = []
    for group_id, affected_parts in sorted(
        dict(grouped_parts or {}).items(),
        key=lambda item: str(item[0] or "").casefold(),
    ):
        affected_parts = list(affected_parts or [])
        if not affected_parts:
            continue
        piece_count = sum(_validation_piece_quantity(part) for part in affected_parts)
        issues.append(
            _validation_issue(
                severity,
                "Peca",
                problem_builder(piece_count, group_id),
                suggestion=suggestion,
                group_id=group_id,
                part_label=(
                    _validation_parts_reference(affected_parts)
                    if include_part_reference
                    else ""
                ),
            )
        )
    return issues


def _layout_warning_suggestion(warning):
    kind = str(warning.get("kind", "") or "")
    if kind == "Retalho ignorado":
        return (
            "Revise material, espessura ou dimensoes do retalho. "
            "Se ele nao for reaproveitavel, remova-o do estoque configurado."
        )
    if kind == "Chapa extra aberta":
        return (
            "Aumente o estoque configurado ou recadastre retalhos compativeis "
            "para reduzir a abertura automatica de chapas extras."
        )
    return "Revise a configuracao de estoque e gere o layout novamente."


def _layout_warning_problem(warning):
    kind = str((warning or {}).get("kind", "") or "").strip()
    reason = str((warning or {}).get("reason", "") or "").strip()

    if kind == "Chapa extra aberta":
        return "Estoque insuficiente; chapa extra aberta automaticamente."

    if kind == "Retalho ignorado":
        if reason:
            return f"Retalho ignorado: {reason}"
        return "Retalho ignorado pelo layout atual."

    if kind and reason:
        return f"{kind}: {reason}"
    return reason or "Ha uma pendencia no uso do estoque configurado."


def _layout_warning_severity(warning):
    kind = str((warning or {}).get("kind", "") or "").strip()

    # Abertura automatica de chapa extra e descarte de retalho compativel
    # afetam custo e aproveitamento, mas nao impedem a exportacao.
    if kind in {"Chapa extra aberta", "Retalho ignorado"}:
        return "Alerta"
    return "Alerta"


def collect_project_validation_issues(parts=None, settings=None, document=None, layout_sheets=None):
    doc = document or ensure_document()
    issues = []
    missing_material_parts = {}
    auto_cut_parts = {}

    try:
        parts = parts if parts is not None else collect_parts()
    except Exception as exc:
        return [
            _validation_issue(
                "Erro",
                "Projeto",
                f"Nao foi possivel coletar as pecas do documento: {exc}",
                suggestion=(
                    "Revise os objetos visiveis e confirme se o modelo contem solidos validos para marcenaria."
                ),
            )
        ]

    if settings is None:
        try:
            settings = _lazy_get_sheet_settings()
        except ValueError as exc:
            settings = None
            issues.append(
                _validation_issue(
                    "Erro",
                    "Configuracao",
                    str(exc),
                    suggestion="Abra 'Configurar Chapa' e corrija os valores do estoque e da area util.",
                )
            )

    if not parts:
        issues.append(
            _validation_issue(
                "Erro",
                "Projeto",
                "Nenhuma peca valida foi encontrada para producao.",
                suggestion=(
                    "Modele as pecas como solidos validos ou deixe visiveis apenas as pecas desejadas antes de validar."
                ),
            )
        )
        return _deduplicate_validation_issues(issues)

    from .spreadsheets import _part_group_id_map
    group_id_map = _part_group_id_map(parts)
    base_object_names_by_part_id = {}

    if settings is not None and settings.full_sheet_count <= 0 and not settings.remnants and not settings.allow_extra_full_sheets:
        issues.append(
            _validation_issue(
                "Erro",
                "Estoque",
                "Nao ha chapas inteiras, retalhos ou chapas extras habilitadas para montar o layout.",
                suggestion=(
                    "Configure chapas inteiras ou retalhos, ou habilite a abertura automatica de chapas extras."
                ),
            )
        )

    fit_error_found = False
    for part in parts:
        base_object_name = str(getattr(part, "object_name", "") or "").split("#", 1)[0]
        group_id = group_id_map.get(base_object_name, "")
        part_label = _validation_part_label(part)
        object_name = str(getattr(part, "object_name", "") or "")

        base_object_names_by_part_id.setdefault(part.part_id, set()).add(base_object_name or object_name)

        if part.length_mm <= 0 or part.width_mm <= 0 or part.thickness_mm <= 0:
            issues.append(
                _validation_issue(
                    "Erro",
                    "Peca",
                    "A peca tem dimensoes invalidas para producao.",
                    suggestion="Revise a geometria da peca e confirme comprimento, largura e espessura positivos.",
                    group_id=group_id,
                    part_id=part.part_id,
                    part_label=part_label,
                    object_name=object_name,
                )
            )
            continue

        if not _material_value(part.material):
            missing_material_parts.setdefault(group_id, []).append(part)

        if normalize_cut_method(part.cut_method) == "Auto":
            auto_cut_parts.setdefault(group_id, []).append(part)

        if settings is not None:
            try:
                _part_orientation_options(part, settings.length_mm, settings.width_mm, settings)
            except ValueError as exc:
                fit_error_found = True
                issues.append(
                    _validation_issue(
                        "Erro",
                        "Peca",
                        str(exc),
                        suggestion=(
                            "Revise dimensoes, rotacao, veio/fibra ou configure uma chapa maior antes de produzir."
                        ),
                        group_id=group_id,
                        part_id=part.part_id,
                        part_label=part_label,
                        object_name=object_name,
                    )
                )

    for part_id, object_names in base_object_names_by_part_id.items():
        if len(object_names) <= 1:
            continue
        issues.append(
            _validation_issue(
                "Alerta",
                "Identificacao",
                f"O ID {part_id} aparece em mais de uma peca base.",
                suggestion="Reetiquete as pecas para evitar ambiguidade na producao e na montagem.",
                part_id=part_id,
            )
        )

    issues.extend(
        _grouped_piece_validation_issue(
            "Alerta",
            missing_material_parts,
            lambda piece_count, group_id: (
                f"Grupo {group_id}: material nao definido em {piece_count} peca(s)."
                if group_id
                else f"Material nao definido em {piece_count} peca(s)."
            ),
            "Defina o material das pecas ou aplique esse dado em lote antes de exportar para producao.",
            include_part_reference=False,
        )
    )
    issues.extend(
        _grouped_piece_validation_issue(
            "Alerta",
            auto_cut_parts,
            lambda piece_count, group_id: (
                f"Grupo {group_id}: metodo de corte em Auto em {piece_count} peca(s)."
                if group_id
                else f"Metodo de corte em Auto em {piece_count} peca(s)."
            ),
            "Escolha CNC ou Seccionadora para padronizar o processo de producao.",
            include_part_reference=False,
        )
    )

    if settings is not None and not fit_error_found:
        warnings = []
        if layout_sheets is None:
            try:
                validation_layout_sheets = _lazy_create_layout_sheets(parts, settings)
            except ValueError as exc:
                issues.append(
                    _validation_issue(
                        "Erro",
                        "Layout",
                        str(exc),
                        suggestion=(
                            "Revise estoque, dimensoes, veio/fibra e disponibilidade de chapas antes de gerar o layout."
                        ),
                    )
                )
            else:
                warnings = _lazy_collect_layout_warnings(parts, validation_layout_sheets, settings)
        else:
            warnings = _lazy_collect_layout_warnings(parts, layout_sheets, settings)

        for warning in warnings:
            stock_label = warning.get("stock", "")
            problem = _layout_warning_problem(warning)
            issues.append(
                _validation_issue(
                    _layout_warning_severity(warning),
                    "Layout",
                    problem,
                    suggestion=_layout_warning_suggestion(warning),
                    group_id=warning.get("group_id", ""),
                    part_label=stock_label,
                )
            )

    deduplicated = _deduplicate_validation_issues(issues)
    if not deduplicated:
        deduplicated.append(
            _validation_issue(
                "OK",
                "Projeto",
                "Nenhuma pendencia encontrada.",
                suggestion="Projeto pronto para seguir para layout, etiquetas e exportacao.",
            )
        )

    return deduplicated


def create_project_validation_spreadsheet(
    parts=None,
    issues=None,
    settings=None,
    document=None,
    layout_sheets=None,
    sheet_name=VALIDATION_SPREADSHEET_NAME,
):
    doc = document or ensure_document()

    existing_sheet = doc.getObject(sheet_name)
    if existing_sheet is not None:
        doc.removeObject(existing_sheet.Name)

    try:
        import Spreadsheet  # noqa: F401
    except ImportError:
        pass

    if issues is None:
        issues = collect_project_validation_issues(
            parts=parts,
            settings=settings,
            document=doc,
            layout_sheets=layout_sheets,
        )

    sheet = doc.addObject("Spreadsheet::Sheet", sheet_name)
    try:
        from .spreadsheets import _add_sheet_to_panelnest_group
        _add_sheet_to_panelnest_group(doc, sheet)
    except Exception:
        pass
    sheet.Label = "Validacao PanelNest"

    headers = (
        "Severidade",
        "Categoria",
        "Grupo",
        "ID da peca",
        "Rotulo/Referencia",
        "Problema",
        "Sugestao",
        "Objeto",
    )
    for column_index, header in enumerate(headers, start=1):
        sheet.set(_sheet_cell(column_index, 1), header)

    for row_index, issue in enumerate(issues or [], start=2):
        values = (
            issue.get("severity", ""),
            issue.get("category", ""),
            issue.get("group_id", ""),
            issue.get("part_id", ""),
            issue.get("part_label", ""),
            issue.get("problem", ""),
            issue.get("suggestion", ""),
            issue.get("object_name", ""),
        )
        for column_index, value in enumerate(values, start=1):
            sheet.set(_sheet_cell(column_index, row_index), value)

    doc.recompute()
    return sheet
