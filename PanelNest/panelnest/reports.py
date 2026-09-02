import base64
import html
import os
import re
import shutil
import tempfile
import subprocess
from datetime import datetime
from io import BytesIO

try:
    import FreeCAD as App
except ImportError:
    App = None

try:
    import FreeCADGui as Gui
except ImportError:
    Gui = None

try:
    import Part
except ImportError:
    Part = None

from .constants import (
    WORKBENCH_ID,
    EDGE_BAND_SIDE_LABELS,
    EDGE_BAND_SIDE_ABBREVIATIONS,
    EDGE_BAND_HIGHLIGHT_COLOR,
    ASSEMBLY_GUIDE_CONTEXT_COLOR,
    ASSEMBLY_GUIDE_TARGET_COLOR,
    ASSEMBLY_GUIDE_TARGET_LINE_COLOR,
    ASSEMBLY_GUIDE_IMAGE_WIDTH_PX,
    ASSEMBLY_GUIDE_IMAGE_HEIGHT_PX,
    LAYOUT_SHEET_GAP_MM,
    LAYOUT_GROUP_GAP_MM,
    INTERNAL_PROPERTY_NAME,
    PART_PROPERTY_GROUP,
)
from .freecad_utils import (
    ensure_document,
    _add_object_to_group,
    _group_member_count,
    _mark_internal_object,
    _is_internal_object,
    _remove_object_tree,
    _safe_gui_refresh,
)
from .metadata import (
    _format_mm,
    _format_m2,
    _format_percent,
    _material_value,
    normalize_cut_method,
    get_sheet_settings,
)
from .edge_band import (
    _edge_band_summary,
    _edge_band_abbreviation_summary,
    _normalize_color_triplet,
    _part_edge_band_flags_dict,
    _normalize_occurrence_edge_band_flags,
    _object_occurrence_edge_band_map,
)
from .orientation import (
    placement_rotation_deg,
    rotated_layout_side,
    transform_placement_point,
)
from .visual import (
    capture_panelnest_assembly_preview_map,
    _assembly_record_key,
    _assembly_record_anchor,
    _assembly_record_occurrence_text,
    _slugify_fragment,
)
from .metadata import (
    _format_m,
    _format_thickness_label,
    resolve_assembly_guide_public_base_url,
    resolve_cloudflare_pages_project_name,
    suggest_cloudflare_pages_project_name,
    get_cloudflare_publish_preferences,
    update_cloudflare_publish_preferences,
)


def _lazy_collect_panelnest_export_tables(document=None):
    from .spreadsheets import collect_panelnest_export_tables
    return collect_panelnest_export_tables(document=document)


def _lazy_collect_part_label_records(parts=None, document=None):
    from .spreadsheets import collect_part_label_records
    return collect_part_label_records(parts=parts, document=document)


def _lazy_get_panelnest_export_base_name(document=None):
    from .spreadsheets import get_panelnest_export_base_name
    return get_panelnest_export_base_name(document=document)


def _company_meta_inline_html(settings):
    """Retorna texto inline simples com dados de empresa/projeto para cabeçalhos de labels."""
    if not settings:
        return ""
    parts = []
    if getattr(settings, "company_name", ""):
        parts.append(html.escape(settings.company_name))
    if getattr(settings, "project_client", ""):
        parts.append(f"Cliente: {html.escape(settings.project_client)}")
    if getattr(settings, "project_responsible", ""):
        parts.append(f"Resp.: {html.escape(settings.project_responsible)}")
    if getattr(settings, "company_contact", ""):
        parts.append(html.escape(settings.company_contact))
    return " &nbsp;·&nbsp; ".join(parts)


def _company_meta_rows_html(settings):
    """Retorna linhas HTML de metadados de empresa/projeto para cabeçalhos de relatório."""
    if not settings:
        return ""
    rows = []
    if getattr(settings, "company_name", ""):
        rows.append(
            f"<div class='meta-row'><span class='meta-label'>Empresa</span>"
            f"<span class='meta-value'>{html.escape(settings.company_name)}</span></div>"
        )
    if getattr(settings, "company_contact", ""):
        rows.append(
            f"<div class='meta-row'><span class='meta-label'>Contato</span>"
            f"<span class='meta-value'>{html.escape(settings.company_contact)}</span></div>"
        )
    if getattr(settings, "company_address", ""):
        rows.append(
            f"<div class='meta-row'><span class='meta-label'>Endereço</span>"
            f"<span class='meta-value'>{html.escape(settings.company_address)}</span></div>"
        )
    if getattr(settings, "project_client", ""):
        rows.append(
            f"<div class='meta-row'><span class='meta-label'>Cliente</span>"
            f"<span class='meta-value'>{html.escape(settings.project_client)}</span></div>"
        )
    if getattr(settings, "project_responsible", ""):
        rows.append(
            f"<div class='meta-row'><span class='meta-label'>Responsável</span>"
            f"<span class='meta-value'>{html.escape(settings.project_responsible)}</span></div>"
        )
    if getattr(settings, "project_notes", ""):
        rows.append(
            f"<div class='meta-row'><span class='meta-label'>Observações</span>"
            f"<span class='meta-value'>{html.escape(settings.project_notes)}</span></div>"
        )
    return "".join(rows)


def build_panelnest_report_html(document=None, tables=None):
    from .metadata import get_sheet_settings

    doc = document or ensure_document()
    tables = tables if tables is not None else _lazy_collect_panelnest_export_tables(document=doc)
    if not tables:
        raise ValueError("Nao ha planilhas do PanelNest disponiveis para montar o relatorio.")

    try:
        _settings = get_sheet_settings()
    except Exception:
        _settings = None

    document_label = getattr(doc, "Label", "") or getattr(doc, "Name", WORKBENCH_ID)
    exported_at = datetime.now().strftime("%d/%m/%Y %H:%M")
    sections = []
    header_aliases = {
        "ID da peca": "ID",
        "Dimensoes (mm)": "Dimensoes",
        "Ocorrencia": "Ocorr.",
        "Rotulo/Referencia": "Referencia",
        "Severidade": "Sev.",
        "Formato / Esp. (mm)": "Formato / Esp.",
        "Espessura (mm)": "Esp. (mm)",
        "Metodo de corte": "Metodo",
        "Modo de layout": "Layout",
        "Margem tecnica (mm)": "Margem tec. (mm)",
        "Qtd de pecas": "Pecas",
        "Area das pecas (m2)": "Area pecas (m2)",
        "Area util (m2)": "Area util (m2)",
        "Aproveitamento (%)": "Aprov. (%)",
        "Chapas equivalentes": "Chapas eq.",
        "Qtd retalhos gerados": "Qtd retalhos",
        "Area retalhos (m2)": "Area retalhos (m2)",
        "Tipo de corte": "Tipo",
        "Orientacao": "Orient.",
        "Posicao (mm)": "Pos. (mm)",
        "Inicio X (mm)": "Ini X (mm)",
        "Inicio Y (mm)": "Ini Y (mm)",
        "Fim X (mm)": "Fim X (mm)",
        "Fim Y (mm)": "Fim Y (mm)",
        "Comprimento (mm)": "Comp. (mm)",
        "Largura (mm)": "Larg. (mm)",
        "Borda superior (mm)": "Sup. (mm)",
        "Borda inferior (mm)": "Inf. (mm)",
        "Borda esquerda (mm)": "Esq. (mm)",
        "Borda direita (mm)": "Dir. (mm)",
        "Qtd de bordas": "Qtd bordas",
        "Metragem por peca (m)": "Metragem/peca (m)",
        "Metragem total (m)": "Metragem total (m)",
        "Resumo bordas": "Resumo bordas",
    }

    for table in tables:
        rows = table["rows"]
        if not rows:
            continue
        headers = rows[0]
        display_headers = [header_aliases.get(str(header or ""), str(header or "")) for header in headers]
        body_rows = rows[1:] if len(rows) > 1 else []
        body_html = []
        for row in body_rows:
            if not any(row):
                body_html.append(
                    f"<tr class='blank-row'><td colspan='{len(headers)}'>&nbsp;</td></tr>"
                )
                continue
            body_html.append(
                "<tr>"
                + "".join(f"<td>{html.escape(str(cell or ''))}</td>" for cell in row)
                + "</tr>"
            )
        if not body_html:
            body_html.append(f"<tr><td colspan='{len(headers)}'>Sem registros.</td></tr>")

        sections.append(
            "<section class='section'>"
            "<div class='section-head'>"
            "<h2>%s</h2>"
            "<span class='section-count'>%s registro(s)</span>"
            "</div>"
            "<div class='table-wrap'>"
            "<table class='report-table'>"
            "<thead><tr>%s</tr></thead>"
            "<tbody>%s</tbody>"
            "</table>"
            "</div>"
            "</section>"
            % (
                html.escape(table["label"]),
                len(body_rows) or 0,
                "".join(f"<th>{html.escape(header)}</th>" for header in display_headers),
                "".join(body_html),
            )
        )

    return """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <title>Relatorio PanelNest</title>
  <style>
    @page { size: A4 landscape; margin: 10mm; }
    body {
      font-family: DejaVu Sans, Arial, sans-serif;
      color: #2c2c2c;
      font-size: 9pt;
      line-height: 1.35;
      margin: 0;
    }
    .report-head {
      margin-bottom: 6mm;
      padding-bottom: 4mm;
      border-bottom: 0.4mm solid #dfc9c2;
    }
    .report-kicker {
      margin: 0 0 1mm 0;
      color: #9a6a61;
      font-size: 8.5pt;
      text-transform: uppercase;
      letter-spacing: 0.18mm;
    }
    h1 {
      margin: 0 0 3mm 0;
      font-size: 20pt;
      color: #2d2b2b;
    }
    .meta-grid {
      display: table;
      width: 100%%;
      border-spacing: 0 1.2mm;
      color: #666666;
      font-size: 9pt;
    }
    .meta-row { display: table-row; }
    .meta-label, .meta-value { display: table-cell; vertical-align: top; }
    .meta-label {
      width: 34mm;
      color: #8a5d56;
      font-weight: 600;
      padding-right: 3mm;
    }
    .meta-value { color: #4f4a49; }
    .section {
      margin-top: 5mm;
      page-break-inside: avoid;
      break-inside: avoid;
    }
    .section-head {
      display: table;
      width: 100%%;
      margin: 0 0 2mm 0;
    }
    .section-head h2,
    .section-head .section-count {
      display: table-cell;
      vertical-align: bottom;
    }
    .section-head h2 {
      margin: 0;
      font-size: 12.5pt;
      color: #8f3f38;
    }
    .section-head .section-count {
      text-align: right;
      color: #8a7c79;
      font-size: 8.5pt;
      white-space: nowrap;
    }
    .table-wrap {
      border: 0.25mm solid #d5c0ba;
      border-radius: 1.2mm;
      overflow: hidden;
    }
    table.report-table {
      width: 100%%;
      border-collapse: collapse;
      table-layout: auto;
      font-size: 8pt;
    }
    thead { display: table-header-group; }
    th, td {
      border: 0.2mm solid #d8c7c2;
      padding: 1.3mm 1.5mm;
      vertical-align: top;
    }
    th {
      background: #f1ded7;
      color: #6c3d37;
      font-weight: 700;
      white-space: normal;
      word-break: normal;
      overflow-wrap: normal;
      hyphens: auto;
    }
    td {
      color: #3f3a39;
      overflow-wrap: anywhere;
      word-break: break-word;
    }
    tr:nth-child(even) td { background: #fbf6f4; }
    tr.blank-row td {
      background: #ffffff;
      border-left-color: #ffffff;
      border-right-color: #ffffff;
      padding: 0.8mm 0;
    }
  </style>
</head>
<body>
  <div class="report-head">
    <p class="report-kicker">Relatorio de producao</p>
    <h1>Relatorio PanelNest</h1>
    <div class="meta-grid">
      <div class="meta-row"><span class="meta-label">Documento</span><span class="meta-value">%s</span></div>
      <div class="meta-row"><span class="meta-label">Gerado em</span><span class="meta-value">%s</span></div>
      <div class="meta-row"><span class="meta-label">Planilhas</span><span class="meta-value">%s</span></div>
      %s
    </div>
  </div>
  %s
</body>
</html>
""" % (
        html.escape(document_label),
        html.escape(exported_at),
        html.escape(", ".join(table["label"] for table in tables)),
        _company_meta_rows_html(_settings),
        "".join(sections),
    )


def build_panelnest_labels_html(document=None, records=None, parts=None):
    from .metadata import get_sheet_settings

    doc = document
    if doc is None:
        try:
            doc = ensure_document()
        except Exception:
            if records is None:
                raise
            class _FallbackDocument:
                Label = WORKBENCH_ID
                Name = WORKBENCH_ID
            doc = _FallbackDocument()
    records = records if records is not None else _lazy_collect_part_label_records(parts=parts, document=doc)
    if not records:
        raise ValueError("Nao ha pecas disponiveis para montar as etiquetas do PanelNest.")

    try:
        _label_settings = get_sheet_settings()
    except Exception:
        _label_settings = None

    document_label = getattr(doc, "Label", "") or getattr(doc, "Name", WORKBENCH_ID)
    exported_at = datetime.now().strftime("%d/%m/%Y %H:%M")
    cards_html = []
    qr_cache = {}
    code39_patterns = {
        "0": "nnnwwnwnn",
        "1": "wnnwnnnnw",
        "2": "nnwwnnnnw",
        "3": "wnwwnnnnn",
        "4": "nnnwwnnnw",
        "5": "wnnwwnnnn",
        "6": "nnwwwnnnn",
        "7": "nnnwnnwnw",
        "8": "wnnwnnwnn",
        "9": "nnwwnnwnn",
        "A": "wnnnnwnnw",
        "B": "nnwnnwnnw",
        "C": "wnwnnwnnn",
        "D": "nnnnwwnnw",
        "E": "wnnnwwnnn",
        "F": "nnwnwwnnn",
        "G": "nnnnnwwnw",
        "H": "wnnnnwwnn",
        "I": "nnwnnwwnn",
        "J": "nnnnwwwnn",
        "K": "wnnnnnnww",
        "L": "nnwnnnnww",
        "M": "wnwnnnnwn",
        "N": "nnnnwnnww",
        "O": "wnnnwnnwn",
        "P": "nnwnwnnwn",
        "Q": "nnnnnnwww",
        "R": "wnnnnnwwn",
        "S": "nnwnnnwwn",
        "T": "nnnnwnwwn",
        "U": "wwnnnnnnw",
        "V": "nwwnnnnnw",
        "W": "wwwnnnnnn",
        "X": "nwnnwnnnw",
        "Y": "wwnnwnnnn",
        "Z": "nwwnwnnnn",
        "-": "nwnnnnwnw",
        ".": "wwnnnnwnn",
        " ": "nwwnnnwnn",
        "$": "nwnwnwnnn",
        "/": "nwnwnnnwn",
        "+": "nwnnnwnwn",
        "%": "nnnwnwnwn",
        "*": "nwnnwnwnn",
    }

    def _chip(text, *modifiers):
        classes = ["label-chip"]
        for modifier in modifiers:
            if modifier:
                classes.append(str(modifier))
        return "<span class='%s'>%s</span>" % (
            " ".join(classes),
            html.escape(str(text or "")),
        )

    def _edge_band_chips(record):
        tokens = [token.strip() for token in str(record.get("edge_band_abbrev", "") or "").split(",")]
        tokens = [token for token in tokens if token]
        if not tokens or tokens == ["Sem fita"]:
            return _chip("Sem fita", "is-muted")
        return "".join(_chip(token, "is-band") for token in tokens)

    def _label_scan_payload(record):
        custom_payload = str((record or {}).get("scan_payload", "") or "").strip()
        if custom_payload:
            return custom_payload
        payload_parts = [
            f"DOC={document_label}",
            f"GRUPO={record.get('group_id', '')}",
            f"ID={record.get('part_id', '')}",
            f"ETIQUETA={record.get('label_code', '')}",
            f"OCORRENCIA={record.get('occurrence', '') or '01/01'}",
            f"OBJETO={record.get('object_name', '')}",
        ]
        return "|".join(str(item or "") for item in payload_parts)

    def _label_qr_data_uri(payload):
        payload = str(payload or "").strip()
        if not payload:
            return ""
        cached = qr_cache.get(payload)
        if cached is not None:
            return cached
        try:
            import qrcode
        except Exception:
            qr_cache[payload] = ""
            return ""
        try:
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=2,
                border=1,
            )
            qr.add_data(payload)
            qr.make(fit=True)
            image = qr.make_image(fill_color="black", back_color="white")
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            data_uri = f"data:image/png;base64,{encoded}"
        except Exception:
            data_uri = ""
        if not data_uri:
            python_candidates = []
            for candidate in (
                shutil.which("python3"),
                "/usr/bin/python3",
                "python3",
            ):
                candidate = str(candidate or "").strip()
                if not candidate or candidate in python_candidates:
                    continue
                if candidate == "python3" or os.path.exists(candidate):
                    python_candidates.append(candidate)

            for python_executable in python_candidates:
                try:
                    command = [
                        python_executable,
                        "-c",
                        (
                            "import base64, qrcode; "
                            "from io import BytesIO; "
                            "qr=qrcode.QRCode(version=None,error_correction=qrcode.constants.ERROR_CORRECT_M,box_size=2,border=1); "
                            "qr.add_data(__import__('sys').argv[1]); "
                            "qr.make(fit=True); "
                            "img=qr.make_image(fill_color='black', back_color='white'); "
                            "buf=BytesIO(); "
                            "img.save(buf, format='PNG'); "
                            "print(base64.b64encode(buf.getvalue()).decode('ascii'))"
                        ),
                        payload,
                    ]
                    completed = subprocess.run(
                        command,
                        check=False,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    encoded = (completed.stdout or "").strip()
                    if completed.returncode == 0 and encoded:
                        data_uri = f"data:image/png;base64,{encoded}"
                        break
                except Exception:
                    continue
        qr_cache[payload] = data_uri
        return data_uri

    def _label_barcode_value(record):
        value = str(record.get("part_id", "") or "").strip().upper()
        if not value:
            value = str(record.get("label_code", "") or "").strip().upper()
        cleaned = []
        for char in value:
            cleaned.append(char if char in code39_patterns and char != "*" else "-")
        barcode_value = "".join(cleaned).strip("- ")
        return barcode_value or "PN"

    def _label_code39_html(value):
        barcode_value = _label_barcode_value({"part_id": value})
        encoded = f"*{barcode_value}*"
        spans = []
        for char_index, char in enumerate(encoded):
            pattern = code39_patterns.get(char)
            if not pattern:
                continue
            for bar_index, width_code in enumerate(pattern):
                width_mm = "0.52mm" if width_code == "w" else "0.20mm"
                css_class = "label-bar-space" if bar_index % 2 else "label-bar"
                spans.append(
                    "<span class='%s' style='width:%s'></span>" % (css_class, width_mm)
                )
            if char_index != len(encoded) - 1:
                spans.append("<span class='label-bar-space' style='width:0.20mm'></span>")
        return (
            "<div class='label-code-box'>"
            "<div class='label-barcode' aria-label='Codigo de barras %s'>%s</div>"
            "<div class='label-code-caption'>Codigo</div>"
            "<div class='label-code-text'>%s</div>"
            "</div>"
            % (
                html.escape(barcode_value),
                "".join(spans),
                html.escape(barcode_value),
            )
        )

    for record in records:
        occurrence_html = ""
        if record["occurrence"]:
            occurrence_html = (
                "<span class='label-occurrence'>%s</span>"
                % html.escape(record["occurrence"])
            )

        group_chip_html = _chip(record["group_id"] or "Sem grupo", "is-secondary")
        material_chip_html = _chip(
            record["material"],
            "is-attention" if record["material"] == "Sem material" else "is-secondary",
        )
        cut_chip_html = _chip(
            record["cut_method"],
            "is-attention" if record["cut_method"] == "Auto" else "is-secondary",
        )
        grain_chip_html = _chip(
            f"Veio {record['grain_direction']}",
            "is-muted" if record["grain_direction"] == "Livre" else "is-secondary",
        )
        scan_payload = _label_scan_payload(record)
        qr_data_uri = _label_qr_data_uri(scan_payload)
        code_value = _label_barcode_value(record)
        qr_html = (
            "<div class='label-code-box'>"
            "<img class='label-qr' src='%s' alt='QR da etiqueta %s' />"
            "<div class='label-code-caption'>Escanear</div>"
            "<div class='label-code-text'>%s</div>"
            "</div>"
            % (
                html.escape(qr_data_uri, quote=True),
                html.escape(record["part_id"]),
                html.escape(record["label_code"]),
            )
        ) if qr_data_uri else _label_code39_html(code_value)

        cards_html.append(
            "<article class='label-card'>"
            "<div class='label-accent'></div>"
            "<div class='label-head'>"
            "<div class='label-id-block'>"
            "<div class='label-id'>%s</div>"
            "<div class='label-name'>%s</div>"
            "<div class='label-meta-line'>Etiqueta %s</div>"
            "</div>"
            "<div class='label-occurrence-wrap'>%s</div>"
            "</div>"
            "<div class='label-dimensions'>%s</div>"
            "<div class='label-chip-row'>%s%s%s</div>"
            "<div class='label-chip-row'>%s</div>"
            "<table class='label-lower'><tr>"
            "<td class='label-lower-main'>"
            "<div class='label-section'>"
            "<div class='label-section-title'>Fita de borda</div>"
            "<div class='label-chip-row'>%s</div>"
            "</div>"
            "</td>"
            "<td class='label-lower-code'>%s</td>"
            "</tr></table>"
            "</article>"
            % (
                html.escape(record["part_id"]),
                html.escape(record["part_label"]),
                html.escape(record["label_code"]),
                occurrence_html,
                html.escape(record["dimensions_text"]),
                group_chip_html,
                material_chip_html,
                cut_chip_html,
                grain_chip_html,
                _edge_band_chips(record),
                qr_html,
            )
        )

    return """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <title>Etiquetas PanelNest</title>
  <style>
    @page { size: A4 portrait; margin: 7mm; }
    body {
      font-family: DejaVu Sans, Arial, sans-serif;
      color: #2c2c2c;
      font-size: 9pt;
      line-height: 1.3;
      margin: 0;
    }
    .labels-head {
      margin: 0 0 4.2mm 0;
      padding: 0 0 2.6mm 0;
      border-bottom: 0.35mm solid #d8c7c2;
    }
    .labels-kicker {
      margin: 0 0 1mm 0;
      color: #a16557;
      font-size: 8pt;
      text-transform: uppercase;
      letter-spacing: 0.18mm;
      font-weight: 700;
    }
    h1 {
      margin: 0 0 1.5mm 0;
      font-size: 17pt;
      color: #2d2b2b;
    }
    .labels-meta {
      color: #615653;
      font-size: 8.2pt;
    }
    .label-grid {
      font-size: 0;
    }
    .label-card {
      display: inline-block;
      vertical-align: top;
      box-sizing: border-box;
      width: calc(50%% - 2.2mm);
      min-height: 46mm;
      margin: 0 4.4mm 4.2mm 0;
      padding: 0 4.4mm 4.2mm 4.4mm;
      border: 0.35mm solid #b9988d;
      border-radius: 1.9mm;
      background: linear-gradient(180deg, #fffdfa 0%%, #fff8f5 100%%);
      box-shadow: 0 0 0 0.1mm #f3e5df inset;
      break-inside: avoid;
      page-break-inside: avoid;
      font-size: 9pt;
    }
    .label-card:nth-child(2n) {
      margin-right: 0;
    }
    .label-accent {
      height: 1.8mm;
      margin: 0 -4.4mm 3.1mm -4.4mm;
      border-radius: 1.5mm 1.5mm 0 0;
      background: linear-gradient(90deg, #8f3f38 0%%, #d5856f 100%%);
    }
    .label-head {
      display: table;
      width: 100%%;
      margin-bottom: 1.5mm;
      border-spacing: 0;
    }
    .label-id-block, .label-occurrence-wrap {
      display: table-cell;
      vertical-align: top;
    }
    .label-occurrence-wrap {
      width: 1%%;
      text-align: right;
    }
    .label-id {
      color: #8f3f38;
      font-size: 15pt;
      font-weight: 800;
      letter-spacing: 0.16mm;
    }
    .label-name {
      margin-top: 0.5mm;
      font-size: 10.3pt;
      font-weight: 700;
      color: #2f2b2a;
      overflow-wrap: anywhere;
    }
    .label-meta-line {
      margin-top: 0.5mm;
      color: #81655f;
      font-size: 7.2pt;
      font-weight: 700;
      letter-spacing: 0.08mm;
      text-transform: uppercase;
    }
    .label-occurrence {
      display: inline-block;
      padding: 0.6mm 1.5mm;
      border-radius: 1.5mm;
      background: #f1ded7;
      color: #6c3d37;
      font-size: 8pt;
      font-weight: 700;
      white-space: nowrap;
    }
    .label-dimensions {
      display: inline-block;
      margin-bottom: 1.8mm;
      padding: 0.8mm 1.5mm;
      border: 0.2mm solid #ecd9d1;
      border-radius: 1.5mm;
      background: #fffefd;
      color: #3b3736;
      font-size: 10.6pt;
      font-weight: 800;
      letter-spacing: 0.08mm;
    }
    .label-chip-row {
      margin-bottom: 1.1mm;
    }
    .label-chip {
      display: inline-block;
      margin: 0 1mm 1mm 0;
      padding: 0.75mm 1.5mm;
      border-radius: 1.7mm;
      background: #f2ebe7;
      color: #5f4741;
      font-size: 7.8pt;
      font-weight: 700;
      white-space: nowrap;
    }
    .label-chip.is-secondary {
      background: #f3ece9;
      color: #6a4b44;
    }
    .label-chip.is-muted {
      background: #f4f1ef;
      color: #756b68;
    }
    .label-chip.is-attention {
      background: #f8ead3;
      color: #7a4d12;
    }
    .label-chip.is-band {
      background: #e4f1f4;
      color: #1b6370;
    }
    .label-section {
      margin-top: 0.5mm;
      padding-top: 1.3mm;
      border-top: 0.2mm solid #ead9d3;
    }
    .label-section-title {
      margin-bottom: 0.8mm;
      color: #8f6c65;
      font-size: 7.6pt;
      font-weight: 700;
      letter-spacing: 0.14mm;
      text-transform: uppercase;
    }
    .label-lower {
      width: 100%%;
      margin-top: 0.6mm;
      border-collapse: collapse;
      border-spacing: 0;
    }
    .label-lower-main,
    .label-lower-code {
      vertical-align: top;
      padding: 0;
    }
    .label-lower-code {
      width: 27mm;
      padding-left: 2mm;
      text-align: right;
    }
    .label-code-box {
      padding: 1.15mm;
      border: 0.25mm solid #e4cdc4;
      border-radius: 1.6mm;
      background: linear-gradient(180deg, #fffefe 0%%, #fff9f7 100%%);
      text-align: center;
    }
    .label-qr {
      display: block;
      width: 18.5mm;
      height: 18.5mm;
      margin: 0 auto 0.8mm auto;
      image-rendering: pixelated;
    }
    .label-barcode {
      display: block;
      height: 8.8mm;
      margin: 0 auto 0.8mm auto;
      padding: 0.7mm 0.4mm 0 0.4mm;
      white-space: nowrap;
      overflow: hidden;
      background: #fffefe;
      box-sizing: border-box;
    }
    .label-bar,
    .label-bar-space {
      display: inline-block;
      height: 7.8mm;
      vertical-align: top;
    }
    .label-bar {
      background: #2c2c2c;
    }
    .label-bar-space {
      background: transparent;
    }
    .label-code-caption {
      color: #8f6c65;
      font-size: 6.7pt;
      font-weight: 700;
      letter-spacing: 0.12mm;
      text-transform: uppercase;
    }
    .label-code-text {
      margin-top: 0.5mm;
      color: #594844;
      font-size: 6.8pt;
      font-weight: 700;
      word-break: break-word;
    }
  </style>
</head>
<body>
  <div class="labels-head">
    <p class="labels-kicker">Impressao de etiquetas</p>
    <h1>Etiquetas PanelNest</h1>
    <div class="labels-meta">%s | Gerado em %s | %s etiqueta(s)</div>
    <div class="labels-meta" style="margin-top:2mm">%s</div>
  </div>
  <div class="label-grid">%s</div>
</body>
</html>
""" % (
        html.escape(document_label),
        html.escape(exported_at),
        html.escape(str(len(records))),
        _company_meta_inline_html(_label_settings),
        "".join(cards_html),
    )


def build_panelnest_assembly_guide_html(document=None, records=None, preview_map=None, parts=None):
    doc = document
    if doc is None:
        try:
            doc = ensure_document()
        except Exception:
            if records is None:
                raise

            class _FallbackDocument:
                Label = WORKBENCH_ID
                Name = WORKBENCH_ID

            doc = _FallbackDocument()

    records = records if records is not None else _lazy_collect_part_label_records(parts=parts, document=doc)
    if not records:
        raise ValueError("Nao ha pecas disponiveis para montar o guia de montagem do PanelNest.")

    preview_map = preview_map if preview_map is not None else {}
    document_label = getattr(doc, "Label", "") or getattr(doc, "Name", WORKBENCH_ID)
    exported_at = datetime.now().strftime("%d/%m/%Y %H:%M")
    cards_html = []

    def _meta_row(label, value):
        return (
            "<div class='guide-meta-row'>"
            "<span class='guide-meta-label'>%s</span>"
            "<span class='guide-meta-value'>%s</span>"
            "</div>"
            % (
                html.escape(str(label or "")),
                html.escape(str(value or "-")),
            )
        )

    for record in records:
        anchor = _assembly_record_anchor(record)
        preview_data_uri = preview_map.get(_assembly_record_key(record), "")
        occurrence_text = str(record.get("occurrence", "") or "").strip()
        preview_html = (
            "<img class='guide-preview-image' src='%s' alt='Movel com a peca %s destacada' />"
            % (
                html.escape(preview_data_uri, quote=True),
                html.escape(record.get("part_id", "")),
            )
        ) if preview_data_uri else "<div class='guide-preview-empty'>Pre-visualizacao indisponivel.</div>"

        cards_html.append(
            "<article class='guide-card' id='%s'>"
            "<div class='guide-card-head'>"
            "<div>"
            "<div class='guide-id'>%s</div>"
            "<h2>%s</h2>"
            "<div class='guide-dimensions'>%s</div>"
            "</div>"
            "<div class='guide-card-badges'>%s%s</div>"
            "</div>"
            "<div class='guide-preview-wrap'>%s</div>"
            "<div class='guide-preview-note'>Movel completo com a peca destacada em laranja.</div>"
            "<div class='guide-meta-grid'>%s%s%s%s%s%s</div>"
            "</article>"
            % (
                html.escape(anchor),
                html.escape(record.get("part_id", "")),
                html.escape(record.get("part_label", "") or record.get("part_id", "")),
                html.escape(record.get("dimensions_text", "")),
                (
                    "<span class='guide-badge'>%s</span>"
                    % html.escape(record.get("group_id", "") or "Sem grupo")
                ),
                (
                    "<span class='guide-badge is-muted'>%s</span>"
                    % html.escape(occurrence_text)
                    if occurrence_text
                    else ""
                ),
                preview_html,
                _meta_row("Material", record.get("material", "") or "Sem material"),
                _meta_row("Corte", record.get("cut_method", "") or "Auto"),
                _meta_row("Veio", record.get("grain_direction", "") or "Livre"),
                _meta_row("Fita", record.get("edge_band_text", "") or "Sem fita"),
                _meta_row("Etiqueta", record.get("label_code", "") or record.get("part_id", "")),
                _meta_row("Objeto", record.get("object_name", "") or "-"),
            )
        )

    return """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Guia de Montagem PanelNest</title>
  <style>
    :root {
      --panelnest-ink: #2b2928;
      --panelnest-accent: #b05b46;
      --panelnest-accent-soft: #efd8d0;
      --panelnest-surface: #fffdfb;
      --panelnest-border: #e6d1c8;
      --panelnest-muted: #766a66;
      --panelnest-target: #ffd8c6;
      --panelnest-target-border: #d26f4c;
      --panelnest-shadow: 0 14px 32px rgba(87, 54, 42, 0.12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: DejaVu Sans, Arial, sans-serif;
      color: var(--panelnest-ink);
      background:
        radial-gradient(circle at top right, rgba(176, 91, 70, 0.08), transparent 28%%),
        linear-gradient(180deg, #fffaf7 0%%, #fffdfb 100%%);
      padding: 20px 16px 48px;
    }
    .guide-shell {
      max-width: 1180px;
      margin: 0 auto;
    }
    .guide-head {
      background: rgba(255, 253, 251, 0.92);
      border: 1px solid var(--panelnest-border);
      border-radius: 18px;
      padding: 20px 22px;
      box-shadow: var(--panelnest-shadow);
      margin-bottom: 18px;
    }
    .guide-kicker {
      margin: 0 0 6px 0;
      color: #a56c5f;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-weight: 700;
    }
    .guide-head h1 {
      margin: 0 0 6px 0;
      font-size: 34px;
      line-height: 1.05;
    }
    .guide-subtitle {
      margin: 0;
      color: var(--panelnest-muted);
      font-size: 14px;
    }
    .guide-intro {
      margin: 12px 0 0 0;
      color: #5f5450;
      font-size: 14px;
      line-height: 1.45;
    }
    .guide-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 18px;
    }
    .guide-card {
      background: var(--panelnest-surface);
      border: 1px solid var(--panelnest-border);
      border-radius: 18px;
      padding: 16px;
      box-shadow: var(--panelnest-shadow);
      scroll-margin-top: 24px;
      transition: border-color 0.18s ease, box-shadow 0.18s ease, transform 0.18s ease;
    }
    .guide-card:target {
      border-color: var(--panelnest-target-border);
      box-shadow: 0 0 0 3px rgba(210, 111, 76, 0.16), 0 18px 40px rgba(87, 54, 42, 0.18);
      background: linear-gradient(180deg, #fffaf7 0%%, #fff8f4 100%%);
      transform: translateY(-2px);
    }
    .guide-card-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      margin-bottom: 12px;
    }
    .guide-id {
      color: var(--panelnest-accent);
      font-weight: 800;
      font-size: 24px;
      line-height: 1;
      margin-bottom: 6px;
    }
    .guide-card h2 {
      margin: 0 0 6px 0;
      font-size: 20px;
      line-height: 1.15;
    }
    .guide-dimensions {
      color: #4e4644;
      font-weight: 700;
      font-size: 15px;
    }
    .guide-card-badges {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: flex-end;
    }
    .guide-badge {
      display: inline-flex;
      align-items: center;
      padding: 6px 10px;
      border-radius: 999px;
      background: var(--panelnest-accent-soft);
      color: #7f493f;
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }
    .guide-badge.is-muted {
      background: #f4efeb;
      color: #6a615e;
    }
    .guide-preview-wrap {
      border: 1px solid var(--panelnest-border);
      border-radius: 14px;
      overflow: hidden;
      background:
        linear-gradient(180deg, #e2e7ec 0%%, #d7dde4 100%%);
      min-height: 180px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .guide-preview-image {
      display: block;
      width: 100%%;
      height: auto;
      background: transparent;
    }
    .guide-preview-empty {
      padding: 20px;
      color: var(--panelnest-muted);
      font-size: 14px;
      text-align: center;
    }
    .guide-preview-note {
      margin-top: 8px;
      color: #86655f;
      font-size: 12px;
      font-weight: 600;
    }
    .guide-meta-grid {
      margin-top: 14px;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px 12px;
    }
    .guide-meta-row {
      background: #fff;
      border: 1px solid #eee0da;
      border-radius: 12px;
      padding: 10px 12px;
      min-height: 58px;
    }
    .guide-meta-label {
      display: block;
      color: #9a6b60;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-weight: 700;
      margin-bottom: 5px;
    }
    .guide-meta-value {
      display: block;
      color: #342f2e;
      font-size: 14px;
      line-height: 1.35;
      font-weight: 600;
      word-break: break-word;
    }
    @media (max-width: 820px) {
      body { padding: 16px 12px 32px; }
      .guide-head { padding: 18px; }
      .guide-head h1 { font-size: 28px; }
      .guide-grid { grid-template-columns: 1fr; }
      .guide-card-head { flex-direction: column; }
      .guide-card-badges { justify-content: flex-start; }
      .guide-meta-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main class="guide-shell">
    <section class="guide-head">
      <p class="guide-kicker">Guia de montagem</p>
      <h1>Montagem PanelNest</h1>
      <p class="guide-subtitle">%s | Gerado em %s | %s peca(s)</p>
      <p class="guide-intro">
        Cada bloco abaixo mostra o movel inteiro com a peca correspondente destacada em laranja.
        Ao abrir esta pagina pelo QR da etiqueta, o navegador salta direto para a peca escaneada.
      </p>
    </section>
    <section class="guide-grid">%s</section>
  </main>
</body>
</html>
""" % (
        html.escape(document_label),
        html.escape(exported_at),
        html.escape(str(len(records))),
        "".join(cards_html),
    )


def _layout_sheet_offset_map(layout_sheets, settings):
    from .nesting import _layout_sheet_key

    offsets = {}
    sheets_by_group = {}
    for layout_sheet in layout_sheets:
        sheets_by_group.setdefault(layout_sheet.group_id, []).append(layout_sheet)

    group_offset_y_mm = 0.0
    for group_sheets in sheets_by_group.values():
        sheet_offset_x_mm = 0.0
        for layout_sheet in group_sheets:
            offsets[_layout_sheet_key(layout_sheet)] = (sheet_offset_x_mm, group_offset_y_mm)
            sheet_offset_x_mm += layout_sheet.source_length_mm + LAYOUT_SHEET_GAP_MM
        group_offset_y_mm += max(
            (layout_sheet.source_width_mm for layout_sheet in group_sheets),
            default=settings.width_mm,
        ) + LAYOUT_GROUP_GAP_MM

    return offsets


def _make_rectangle_outline_shape(x_mm, y_mm, length_mm, width_mm, z_mm=0.0):
    points = [
        App.Vector(x_mm, y_mm, z_mm),
        App.Vector(x_mm + length_mm, y_mm, z_mm),
        App.Vector(x_mm + length_mm, y_mm + width_mm, z_mm),
        App.Vector(x_mm, y_mm + width_mm, z_mm),
        App.Vector(x_mm, y_mm, z_mm),
    ]
    return Part.makePolygon(points)


def _make_profile_outline_shape(x_mm, y_mm, profile_points, z_mm=0.0):
    points = [
        App.Vector(x_mm + point_x, y_mm + point_y, z_mm)
        for point_x, point_y in profile_points
    ]
    if not points:
        return None
    points.append(points[0])
    return Part.makePolygon(points)


def _layout_sheet_export_slug(layout_sheet):
    from .spreadsheets import _slugify_export_fragment
    source_slug = _slugify_export_fragment(layout_sheet.source_label)
    return (
        f"layout_{_slugify_export_fragment(layout_sheet.group_id)}_"
        f"chapa_{layout_sheet.sheet_index:02d}_{source_slug}"
    )


def _ensure_dxf_layer_group(document, layer_name, object_name=None):
    group_name = object_name or layer_name
    group = document.getObject(group_name)
    if group is None:
        group = document.addObject("App::DocumentObjectGroup", group_name)
        group.Label = layer_name
    return group


def _add_object_to_group(group, obj):
    proxy = getattr(group, "Proxy", None)
    if proxy is not None and hasattr(proxy, "addObject"):
        try:
            proxy.addObject(group, obj)
            return
        except Exception:
            pass
    add_object = getattr(group, "addObject", None)
    if add_object is not None:
        try:
            add_object(obj)
        except Exception:
            pass


def _group_member_count(group):
    members = getattr(group, "Group", None)
    if members is None:
        return 0
    try:
        return len(members)
    except Exception:
        return 0


def _map_dxf_layer_name(layer_name):
    normalized = str(layer_name or "").strip()
    if normalized.startswith("PanelNestDxfBase_"):
        return "PN_CHAPA"
    if normalized.startswith("PanelNestDxfPeca_"):
        return "PN_PECAS"
    if normalized.startswith("PanelNestDxfFita_"):
        return "PN_FITAS"
    if normalized.startswith("PanelNestDxfCorte_"):
        return "PN_CORTES"
    if normalized.startswith("PanelNestDxfTexto_"):
        return "PN_TEXTOS"
    return normalized


_DXF_CLOSED_POLYLINE_LAYERS = frozenset({
    "PN_CHAPA",
    "PN_PECAS",
    "PN_FITAS",
    "CORTE",
})


def _dxf_record_value(record, code, default=None):
    target = str(code).strip()
    for current_code, value in record[1:]:
        if str(current_code).strip() == target:
            return value
    return default


def _dxf_line_record_info(record):
    if not record or str(record[0][0]).strip() != "0":
        return None
    if str(record[0][1]).strip().upper() != "LINE":
        return None
    try:
        return {
            "layer": str(_dxf_record_value(record, "8", "") or "").strip(),
            "start": (
                float(_dxf_record_value(record, "10", 0.0)),
                float(_dxf_record_value(record, "20", 0.0)),
                float(_dxf_record_value(record, "30", 0.0)),
            ),
            "end": (
                float(_dxf_record_value(record, "11", 0.0)),
                float(_dxf_record_value(record, "21", 0.0)),
                float(_dxf_record_value(record, "31", 0.0)),
            ),
        }
    except (TypeError, ValueError):
        return None


def _dxf_points_match(point_a, point_b, tolerance=0.0001):
    return all(
        abs(float(value_a) - float(value_b)) <= tolerance
        for value_a, value_b in zip(point_a, point_b)
    )


def _dxf_closed_polyline_records(layer, vertices):
    record = [
        ("0", "LWPOLYLINE"),
        ("8", layer),
        ("90", str(len(vertices))),
        ("70", "1"),
    ]
    for point_x, point_y, _point_z in vertices:
        record.extend(
            [
                ("10", f"{point_x:.9f}"),
                ("20", f"{point_y:.9f}"),
            ]
        )
    return [record]


def _join_closed_dxf_line_contours(lines):
    """Converte sequências fechadas de LINE em LWPOLYLINE fechada.

    A conversão é limitada às camadas de contorno. Cortes abertos e demais
    entidades permanecem intactos.
    """
    if len(lines) < 4:
        return lines

    pairs = [
        (lines[index], lines[index + 1])
        for index in range(0, len(lines) - 1, 2)
    ]
    entities_marker = None
    entities_end = None
    for index in range(len(pairs) - 1):
        if (
            str(pairs[index][0]).strip() == "0"
            and str(pairs[index][1]).strip().upper() == "SECTION"
            and str(pairs[index + 1][0]).strip() == "2"
            and str(pairs[index + 1][1]).strip().upper() == "ENTITIES"
        ):
            entities_marker = index + 2
            break
    if entities_marker is None:
        return lines
    for index in range(entities_marker, len(pairs)):
        if (
            str(pairs[index][0]).strip() == "0"
            and str(pairs[index][1]).strip().upper() == "ENDSEC"
        ):
            entities_end = index
            break
    if entities_end is None:
        return lines

    entity_pairs = pairs[entities_marker:entities_end]
    records = []
    current = []
    for pair in entity_pairs:
        if str(pair[0]).strip() == "0" and current:
            records.append(current)
            current = []
        current.append(pair)
    if current:
        records.append(current)

    converted_records = []
    record_index = 0
    while record_index < len(records):
        first_info = _dxf_line_record_info(records[record_index])
        if (
            first_info is None
            or first_info["layer"] not in _DXF_CLOSED_POLYLINE_LAYERS
        ):
            converted_records.append(records[record_index])
            record_index += 1
            continue

        chain_records = [records[record_index]]
        vertices = [first_info["start"], first_info["end"]]
        next_index = record_index + 1
        while (
            next_index < len(records)
            and not _dxf_points_match(vertices[-1], vertices[0])
        ):
            next_info = _dxf_line_record_info(records[next_index])
            if next_info is None or next_info["layer"] != first_info["layer"]:
                break
            if _dxf_points_match(next_info["start"], vertices[-1]):
                next_vertex = next_info["end"]
            elif _dxf_points_match(next_info["end"], vertices[-1]):
                next_vertex = next_info["start"]
            else:
                break
            chain_records.append(records[next_index])
            vertices.append(next_vertex)
            next_index += 1

        is_closed = (
            len(vertices) >= 4
            and _dxf_points_match(vertices[-1], vertices[0])
        )
        if is_closed:
            converted_records.extend(
                _dxf_closed_polyline_records(first_info["layer"], vertices[:-1])
            )
            record_index = next_index
        else:
            converted_records.extend(chain_records)
            record_index += len(chain_records)

    converted_pairs = []
    for record in converted_records:
        converted_pairs.extend(record)
    result_pairs = (
        pairs[:entities_marker]
        + converted_pairs
        + pairs[entities_end:]
    )
    # LWPOLYLINE foi introduzida no DXF AutoCAD 2000 (AC1015).
    for index in range(len(result_pairs) - 1):
        if (
            str(result_pairs[index][0]).strip() == "9"
            and str(result_pairs[index][1]).strip().upper() == "$ACADVER"
            and str(result_pairs[index + 1][0]).strip() == "1"
        ):
            result_pairs[index + 1] = (result_pairs[index + 1][0], "AC1015")
            break
    flattened = []
    for code, value in result_pairs:
        flattened.extend((code, value))
    return flattened


def _normalize_exported_dxf_layers(path):
    if not path or not os.path.exists(path):
        return

    with open(path, "r", encoding="latin-1", errors="ignore") as handle:
        lines = handle.read().splitlines()

    def _find_layer_name(record_lines):
        for idx in range(0, len(record_lines) - 1, 2):
            if record_lines[idx].strip() == "2":
                return record_lines[idx + 1]
        return None

    def _replace_layer_name(record_lines, new_name):
        updated = list(record_lines)
        for idx in range(0, len(updated) - 1, 2):
            if updated[idx].strip() == "2":
                updated[idx + 1] = new_name
                break
        return updated

    def _rewrite_layer_table(all_lines):
        result = []
        idx = 0
        while idx < len(all_lines):
            if (
                idx + 3 < len(all_lines)
                and all_lines[idx].strip() == "0"
                and all_lines[idx + 1].strip() == "TABLE"
                and all_lines[idx + 2].strip() == "2"
                and all_lines[idx + 3].strip() == "LAYER"
            ):
                table_lines = all_lines[idx : idx + 4]
                idx += 4
                seen_layers = set()
                while idx < len(all_lines):
                    if (
                        idx + 1 < len(all_lines)
                        and all_lines[idx].strip() == "0"
                        and all_lines[idx + 1].strip() == "ENDTAB"
                    ):
                        break
                    if (
                        idx + 1 < len(all_lines)
                        and all_lines[idx].strip() == "0"
                        and all_lines[idx + 1].strip() == "LAYER"
                    ):
                        record_start = idx
                        idx += 2
                        while idx < len(all_lines):
                            if idx + 1 < len(all_lines) and all_lines[idx].strip() == "0":
                                break
                            idx += 2
                        record_lines = all_lines[record_start:idx]
                        layer_name = _find_layer_name(record_lines)
                        mapped_name = _map_dxf_layer_name(layer_name)
                        if mapped_name and mapped_name not in seen_layers:
                            seen_layers.add(mapped_name)
                            table_lines.extend(_replace_layer_name(record_lines, mapped_name))
                        continue
                    table_lines.append(all_lines[idx])
                    idx += 1
                if idx + 1 < len(all_lines):
                    table_lines.extend(all_lines[idx : idx + 2])
                    idx += 2
                result.extend(table_lines)
                continue
            result.append(all_lines[idx])
            idx += 1
        return result

    lines = _rewrite_layer_table(lines)

    for idx in range(0, len(lines) - 1, 2):
        if lines[idx].strip() == "8":
            mapped_name = _map_dxf_layer_name(lines[idx + 1])
            if mapped_name:
                lines[idx + 1] = mapped_name

    lines = _join_closed_dxf_line_contours(lines)

    with open(path, "w", encoding="latin-1", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def build_layout_dxf_export_bundle(
    layout_sheets,
    settings,
    document=None,
    include_cut_lines=True,
    include_cut_labels=False,
):
    from .nesting import _layout_sheet_key, _cut_line_color, _cut_line_width
    from .layout_model import (
        _build_layout_part_color_map,
        _part_fill_color,
        _placed_profile_points,
        _darken_color,
        _apply_view_style,
        _apply_cut_view_style,
        _apply_part_annotation_text_style,
        _apply_screen_text_style,
        _build_cut_marker_positions,
        _layout_edge_band_marker_specs,
        _layout_edge_band_label_spec,
        LAYOUT_EDGE_BAND_LABEL_FONT_SIZE_MM,
    )
    from .spreadsheets import _slugify_export_fragment
    from .constants import PART_LABEL_PATTERN

    if App is None or Part is None:
        raise RuntimeError("A exportacao DXF precisa rodar dentro do FreeCAD com o modulo Part carregado.")

    if not layout_sheets:
        raise ValueError("Nao ha layout disponivel para exportar em DXF.")

    document = document or App.newDocument(f"{WORKBENCH_ID}_DXF_EXPORT")
    offsets = _layout_sheet_offset_map(layout_sheets, settings)
    part_color_map = _build_layout_part_color_map(layout_sheets)
    bundle = {"all_objects": [], "sheets": []}

    layer_groups = {
        "chapa": _ensure_dxf_layer_group(document, "PN_CHAPA"),
        "pecas": _ensure_dxf_layer_group(document, "PN_PECAS"),
        "fitas": _ensure_dxf_layer_group(document, "PN_FITAS"),
        "cortes": _ensure_dxf_layer_group(document, "PN_CORTES"),
        "textos": _ensure_dxf_layer_group(document, "PN_TEXTOS"),
        "furos": _ensure_dxf_layer_group(document, "PN_FUROS"),
    }

    try:
        import Draft
    except ImportError:
        Draft = None

    for layout_sheet in layout_sheets:
        offset_x_mm, offset_y_mm = offsets.get(_layout_sheet_key(layout_sheet), (0.0, 0.0))
        sheet_objects = []

        base_outline = document.addObject(
            "Part::Feature",
            f"PanelNestDxfBase_{_slugify_export_fragment(layout_sheet.group_id)}_{layout_sheet.sheet_index:02d}",
        )
        base_outline.Label = (
            f"DXF Base {layout_sheet.group_id} - {layout_sheet.source_label}"
        )
        base_outline.Shape = _make_rectangle_outline_shape(
            offset_x_mm,
            offset_y_mm,
            layout_sheet.source_length_mm,
            layout_sheet.source_width_mm,
            z_mm=0.0,
        )
        _apply_view_style(
            base_outline,
            fill_color=(1.0, 1.0, 1.0),
            line_color=(0.35, 0.28, 0.25),
            transparency=100,
        )
        _add_object_to_group(layer_groups["chapa"], base_outline)
        sheet_objects.append(base_outline)

        for part_index, placement in enumerate(layout_sheet.placements, start=1):
            part_outline = document.addObject(
                "Part::Feature",
                f"PanelNestDxfPeca_{_slugify_export_fragment(layout_sheet.group_id)}_{layout_sheet.sheet_index:02d}_{part_index:02d}",
            )
            part_outline.Label = f"{placement.part.part_id} - {PART_LABEL_PATTERN.sub('', placement.part.label)}"
            placed_profile = _placed_profile_points(placement)
            if placed_profile:
                part_outline.Shape = _make_profile_outline_shape(
                    offset_x_mm + placement.x_mm,
                    offset_y_mm + placement.y_mm,
                    placed_profile,
                    z_mm=0.0,
                )
            else:
                part_outline.Shape = _make_rectangle_outline_shape(
                    offset_x_mm + placement.x_mm,
                    offset_y_mm + placement.y_mm,
                    placement.placed_length_mm,
                    placement.placed_width_mm,
                    z_mm=0.0,
                )
            part_fill = _part_fill_color(
                placement.part,
                color_map=part_color_map,
                rotated=placement.rotated,
            )
            _apply_view_style(
                part_outline,
                fill_color=(1.0, 1.0, 1.0),
                line_color=_darken_color(part_fill, 0.44),
                transparency=100,
            )
            _add_object_to_group(layer_groups["pecas"], part_outline)
            sheet_objects.append(part_outline)

            for edge_index, edge_spec in enumerate(
                _layout_edge_band_marker_specs(placement, settings, z_mm=0.0),
                start=1,
            ):
                edge_outline = document.addObject(
                    "Part::Feature",
                    (
                        f"PanelNestDxfFita_{_slugify_export_fragment(layout_sheet.group_id)}_"
                        f"{layout_sheet.sheet_index:02d}_{part_index:02d}_{edge_index:02d}"
                    ),
                )
                edge_outline.Label = (
                    f"{placement.part.part_id} - fita {edge_spec['source_side_label']}"
                )
                edge_outline.Shape = _make_rectangle_outline_shape(
                    offset_x_mm + edge_spec["x_mm"],
                    offset_y_mm + edge_spec["y_mm"],
                    edge_spec["length_mm"],
                    edge_spec["width_mm"],
                    z_mm=0.0,
                )
                _apply_view_style(
                    edge_outline,
                    fill_color=(1.0, 1.0, 1.0),
                    line_color=_darken_color(EDGE_BAND_HIGHLIGHT_COLOR, 0.42),
                    transparency=100,
                )
                _add_object_to_group(layer_groups["fitas"], edge_outline)
                sheet_objects.append(edge_outline)

                if Draft is not None:
                    edge_label_spec = _layout_edge_band_label_spec(edge_spec, z_mm=0.0)
                    if edge_label_spec is not None:
                        label_center = edge_label_spec["placement"]
                        label_rotation = App.Rotation(
                            App.Vector(0, 0, 1),
                            edge_label_spec.get("rotation_deg", 0.0),
                        )
                        label_placement = App.Placement(
                            App.Vector(
                                offset_x_mm + label_center.x,
                                offset_y_mm + label_center.y,
                                0.0,
                            ),
                            label_rotation,
                        )
                        label_lines = edge_label_spec.get("lines", [])
                        edge_label = None
                        try:
                            edge_label = Draft.make_text(
                                label_lines,
                                placement=label_placement,
                                screen=False,
                            )
                        except TypeError:
                            try:
                                edge_label = Draft.make_text(
                                    label_lines,
                                    placement=label_placement.Base,
                                    screen=False,
                                )
                            except Exception:
                                edge_label = None
                        except Exception:
                            edge_label = None

                        if edge_label is not None:
                            try:
                                edge_label.Placement = label_placement
                            except Exception:
                                pass
                            edge_label.Label = (
                                f"{placement.part.part_id} - fita "
                                f"{edge_spec['source_side_abbreviation']}"
                            )
                            _apply_part_annotation_text_style(
                                edge_label,
                                text_color=(0.10, 0.24, 0.27),
                                font_size_mm=edge_label_spec.get(
                                    "font_size_mm",
                                    LAYOUT_EDGE_BAND_LABEL_FONT_SIZE_MM,
                                ),
                            )
                            _add_object_to_group(layer_groups["textos"], edge_label)
                            sheet_objects.append(edge_label)

            # Furos na camada PN_FUROS
            holes = getattr(placement.part, "holes", None) or []
            for hole_index, hole in enumerate(holes, start=1):
                radius_mm = float(hole.get("diameter_mm", 0)) / 2.0
                if radius_mm <= 0:
                    continue
                hx_src = float(hole.get("x_mm", 0))
                hy_src = float(hole.get("y_mm", 0))
                local_x, local_y = transform_placement_point(
                    placement,
                    hx_src,
                    hy_src,
                )
                hx = offset_x_mm + placement.x_mm + local_x
                hy = offset_y_mm + placement.y_mm + local_y
                try:
                    hole_feature = document.addObject(
                        "Part::Feature",
                        f"PanelNestDxfFuro_{_slugify_export_fragment(layout_sheet.group_id)}_"
                        f"{layout_sheet.sheet_index:02d}_{part_index:02d}_{hole_index:02d}",
                    )
                    hole_feature.Label = (
                        f"{placement.part.part_id} - furo D{hole.get('diameter_mm', 0):.1f}"
                    )
                    hole_feature.Shape = Part.makeCircle(
                        radius_mm,
                        App.Vector(hx, hy, 0.0),
                        App.Vector(0, 0, 1),
                    )
                    _apply_view_style(
                        hole_feature,
                        fill_color=(1.0, 1.0, 1.0),
                        line_color=(0.1, 0.1, 0.6),
                        transparency=100,
                    )
                    _add_object_to_group(layer_groups["furos"], hole_feature)
                    sheet_objects.append(hole_feature)
                except Exception:
                    pass

        if include_cut_lines:
            marker_positions = {}
            if include_cut_labels and layout_sheet.cut_steps:
                marker_positions = _build_cut_marker_positions(layout_sheet, settings, 0.0)

            for cut_index, cut_step in enumerate(layout_sheet.cut_steps, start=1):
                cut_feature = document.addObject(
                    "Part::Feature",
                    f"PanelNestDxfCorte_{_slugify_export_fragment(layout_sheet.group_id)}_{layout_sheet.sheet_index:02d}_{cut_index:02d}",
                )
                cut_feature.Label = f"Corte {cut_step.step_index:02d} - {cut_step.cut_kind}"
                cut_feature.Shape = Part.makeLine(
                    App.Vector(offset_x_mm + cut_step.start_x_mm, offset_y_mm + cut_step.start_y_mm, 0.0),
                    App.Vector(offset_x_mm + cut_step.end_x_mm, offset_y_mm + cut_step.end_y_mm, 0.0),
                )
                _apply_cut_view_style(
                    cut_feature,
                    _cut_line_color(cut_step),
                    line_width=_cut_line_width(cut_step),
                    draw_style="Solid",
                )
                _add_object_to_group(layer_groups["cortes"], cut_feature)
                sheet_objects.append(cut_feature)

                if Draft is None or not include_cut_labels:
                    continue

                marker_data = marker_positions.get(cut_step.step_index)
                if not marker_data:
                    continue

                origin = marker_data.get("origin")
                if origin is None:
                    continue

                try:
                    marker = Draft.make_text(
                        f"{cut_step.step_index:02d}",
                        placement=App.Vector(offset_x_mm + origin.x, offset_y_mm + origin.y, 0.0),
                    )
                except TypeError:
                    marker = Draft.make_text(
                        f"{cut_step.step_index:02d}",
                        point=App.Vector(offset_x_mm + origin.x, offset_y_mm + origin.y, 0.0),
                    )
                except Exception:
                    marker = None

                if marker is not None:
                    marker.Label = f"Passo {cut_step.step_index:02d}"
                    _apply_screen_text_style(marker)
                    _add_object_to_group(layer_groups["textos"], marker)
                    sheet_objects.append(marker)

        bundle["sheets"].append(
            {
                "layout_sheet": layout_sheet,
                "slug": _layout_sheet_export_slug(layout_sheet),
                "objects": sheet_objects,
            }
        )
        bundle["all_objects"].extend(sheet_objects)

    document.recompute()
    bundle["layer_groups"] = [
        group
        for group in layer_groups.values()
        if _group_member_count(group) > 0
    ]
    bundle["export_objects"] = bundle["all_objects"] + bundle["layer_groups"]
    return bundle


def _export_panelnest_dxf_variant(
    output_dir,
    base_name,
    layout_sheets,
    settings,
    include_cut_lines=True,
    include_cut_labels=False,
    variant_suffix="",
):
    import importDXF

    suffix_fragment = f"_{variant_suffix}" if variant_suffix else ""
    temp_document = App.newDocument(f"{WORKBENCH_ID}_DXF_EXPORT")
    try:
        bundle = build_layout_dxf_export_bundle(
            layout_sheets,
            settings,
            document=temp_document,
            include_cut_lines=include_cut_lines,
            include_cut_labels=include_cut_labels,
        )
        combined_path = os.path.join(output_dir, f"{base_name}_layout_panelnest{suffix_fragment}.dxf")
        importDXF.export(bundle["export_objects"], combined_path, nospline=True, lwPoly=False)
        _normalize_exported_dxf_layers(combined_path)

        files = [{"label": "Layout PanelNest", "path": combined_path}]
        for layout_sheet in layout_sheets:
            sheet_document = App.newDocument(f"{WORKBENCH_ID}_DXF_SHEET")
            try:
                sheet_bundle = build_layout_dxf_export_bundle(
                    [layout_sheet],
                    settings,
                    document=sheet_document,
                    include_cut_lines=include_cut_lines,
                    include_cut_labels=include_cut_labels,
                )
                sheet_slug = sheet_bundle["sheets"][0]["slug"]
                file_path = os.path.join(
                    output_dir,
                    f"{base_name}_{sheet_slug}{suffix_fragment}.dxf",
                )
                importDXF.export(
                    sheet_bundle["export_objects"],
                    file_path,
                    nospline=True,
                    lwPoly=False,
                )
                _normalize_exported_dxf_layers(file_path)
                files.append(
                    {
                        "label": f"{layout_sheet.group_id} - {layout_sheet.source_label}",
                        "path": file_path,
                    }
                )
            finally:
                sheet_name = sheet_document.Name
                App.closeDocument(sheet_name)
    finally:
        temp_name = temp_document.Name
        App.closeDocument(temp_name)

    return files


def export_panelnest_dxf_bundle(
    output_dir,
    document=None,
    settings=None,
    include_cut_lines=True,
    include_cut_labels=False,
    variant_suffix="",
):
    if App is None:
        raise RuntimeError("A exportacao DXF precisa rodar dentro do FreeCAD.")

    try:
        import importDXF
    except ImportError as exc:
        raise RuntimeError(f"O modulo importDXF do FreeCAD nao esta disponivel: {exc}")

    source_document = document or ensure_document()
    settings = settings or get_sheet_settings()
    parts = collect_parts(include_hidden=True)
    if not parts:
        raise ValueError("Nenhuma peca valida foi encontrada para montar o DXF do layout.")

    layout_sheets = create_layout_sheets(parts, settings=settings)
    if not layout_sheets:
        raise ValueError("Nao foi possivel gerar um layout para exportar em DXF.")

    os.makedirs(output_dir, exist_ok=True)
    base_name = _lazy_get_panelnest_export_base_name(document=source_document)
    original_document_name = getattr(App.ActiveDocument, "Name", "")

    try:
        files = _export_panelnest_dxf_variant(
            output_dir,
            base_name,
            layout_sheets,
            settings,
            include_cut_lines=include_cut_lines,
            include_cut_labels=include_cut_labels,
            variant_suffix=variant_suffix,
        )
    finally:
        if original_document_name:
            try:
                App.setActiveDocument(original_document_name)
            except Exception:
                pass

    return {
        "output_dir": output_dir,
        "base_name": base_name,
        "files": files,
        "sheet_count": len(layout_sheets),
    }


def export_panelnest_dxf_package(output_dir, document=None, settings=None):
    from .parts import collect_parts
    from .nesting import create_layout_sheets

    source_document = document or ensure_document()
    settings = settings or get_sheet_settings()
    parts = collect_parts(include_hidden=True)
    if not parts:
        raise ValueError("Nenhuma peca valida foi encontrada para montar o DXF do layout.")

    layout_sheets = create_layout_sheets(parts, settings=settings)
    if not layout_sheets:
        raise ValueError("Nao foi possivel gerar um layout para exportar em DXF.")

    base_name = _lazy_get_panelnest_export_base_name(document=source_document)
    has_cut_steps = any(layout_sheet.cut_steps for layout_sheet in layout_sheets)
    variants = [
        {
            "name": "limpo",
            "suffix": "limpo",
            "include_cut_lines": False,
            "include_cut_labels": False,
        }
    ]
    if has_cut_steps:
        variants.append(
            {
                "name": "cortes",
                "suffix": "cortes",
                "include_cut_lines": True,
                "include_cut_labels": False,
            }
        )

    files = []
    variant_results = []
    original_document_name = getattr(App.ActiveDocument, "Name", "")
    try:
        for variant in variants:
            variant_files = _export_panelnest_dxf_variant(
                output_dir,
                base_name,
                layout_sheets,
                settings,
                include_cut_lines=variant["include_cut_lines"],
                include_cut_labels=variant["include_cut_labels"],
                variant_suffix=variant["suffix"],
            )
            variant_results.append(
                {
                    "name": variant["name"],
                    "file_count": len(variant_files),
                    "files": variant_files,
                }
            )
            files.extend(variant_files)
    finally:
        if original_document_name:
            try:
                App.setActiveDocument(original_document_name)
            except Exception:
                pass

    return {
        "output_dir": output_dir,
        "base_name": base_name,
        "files": files,
        "sheet_count": len(layout_sheets),
        "variants": variant_results,
        "has_cut_steps": has_cut_steps,
    }


def _svg_mm(value):
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _svg_path_data(points):
    if not points:
        return ""
    commands = [f"M {_svg_mm(points[0][0])} {_svg_mm(points[0][1])}"]
    commands.extend(
        f"L {_svg_mm(point_x)} {_svg_mm(point_y)}"
        for point_x, point_y in points[1:]
    )
    commands.append("Z")
    return " ".join(commands)


def _layout_sheet_svg_document(layout_sheet, document_label=""):
    from .layout_model import _placed_profile_points
    from .constants import PART_LABEL_PATTERN

    sheet_length = float(layout_sheet.source_length_mm)
    sheet_width = float(layout_sheet.source_width_mm)
    sheet_title = (
        f"{layout_sheet.group_id} - {layout_sheet.source_label} "
        f"({sheet_length:.0f} x {sheet_width:.0f} mm)"
    )
    piece_elements = []
    hole_elements = []
    cut_elements = []

    for part_index, placement in enumerate(layout_sheet.placements, start=1):
        profile = _placed_profile_points(placement)
        if not profile:
            profile = [
                (0.0, 0.0),
                (placement.placed_length_mm, 0.0),
                (placement.placed_length_mm, placement.placed_width_mm),
                (0.0, placement.placed_width_mm),
            ]
        absolute_points = [
            (placement.x_mm + point_x, placement.y_mm + point_y)
            for point_x, point_y in profile
        ]
        part_id = str(placement.part.part_id or f"PN-{part_index:03d}")
        part_label = PART_LABEL_PATTERN.sub("", str(placement.part.label or "")).strip()
        element_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", part_id) or f"part_{part_index:03d}"
        title = html.escape(
            f"{part_id} - {part_label}" if part_label else part_id,
            quote=False,
        )
        piece_elements.append(
            f'<path id="{html.escape(element_id, quote=True)}" '
            f'd="{_svg_path_data(absolute_points)}" '
            'fill="#d7a7bd" fill-opacity="0.28" '
            'stroke="#4b3440" stroke-width="1" '
            'vector-effect="non-scaling-stroke">'
            f"<title>{title}</title></path>"
        )

        for hole_index, hole in enumerate(getattr(placement.part, "holes", None) or [], start=1):
            radius = float(hole.get("diameter_mm", 0.0) or 0.0) / 2.0
            if radius <= 0.0:
                continue
            source_x = float(hole.get("x_mm", 0.0) or 0.0)
            source_y = float(hole.get("y_mm", 0.0) or 0.0)
            local_x, local_y = transform_placement_point(
                placement,
                source_x,
                source_y,
            )
            hole_elements.append(
                f'<circle id="{html.escape(element_id, quote=True)}_hole_{hole_index:02d}" '
                f'cx="{_svg_mm(placement.x_mm + local_x)}" '
                f'cy="{_svg_mm(placement.y_mm + local_y)}" '
                f'r="{_svg_mm(radius)}" fill="none" '
                'stroke="#2f5f91" stroke-width="1" '
                'vector-effect="non-scaling-stroke"/>'
            )

    for cut_step in layout_sheet.cut_steps:
        cut_elements.append(
            f'<line x1="{_svg_mm(cut_step.start_x_mm)}" '
            f'y1="{_svg_mm(cut_step.start_y_mm)}" '
            f'x2="{_svg_mm(cut_step.end_x_mm)}" '
            f'y2="{_svg_mm(cut_step.end_y_mm)}" '
            'stroke="#d24a35" stroke-width="1" stroke-dasharray="8 4" '
            'vector-effect="non-scaling-stroke"/>'
        )

    description = document_label or "Layout PanelNest"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{_svg_mm(sheet_length)}mm" height="{_svg_mm(sheet_width)}mm" '
        f'viewBox="0 0 {_svg_mm(sheet_length)} {_svg_mm(sheet_width)}">\n'
        f"  <title>{html.escape(sheet_title)}</title>\n"
        f"  <desc>{html.escape(description)}</desc>\n"
        f'  <g id="PanelNest" transform="translate(0 {_svg_mm(sheet_width)}) scale(1 -1)">\n'
        f'    <g id="PN_CHAPA"><rect x="0" y="0" '
        f'width="{_svg_mm(sheet_length)}" height="{_svg_mm(sheet_width)}" '
        'fill="none" stroke="#222" stroke-width="1" '
        'vector-effect="non-scaling-stroke"/></g>\n'
        f'    <g id="PN_PECAS">{"".join(piece_elements)}</g>\n'
        f'    <g id="PN_FUROS">{"".join(hole_elements)}</g>\n'
        f'    <g id="PN_CORTES">{"".join(cut_elements)}</g>\n'
        "  </g>\n"
        "</svg>\n"
    )


def export_panelnest_svg_package(output_dir, document=None, settings=None):
    from .parts import collect_parts
    from .nesting import create_layout_sheets
    from .spreadsheets import _slugify_export_fragment

    source_document = document or ensure_document()
    settings = settings or get_sheet_settings()
    parts = collect_parts(include_hidden=True)
    if not parts:
        raise ValueError("Nenhuma peca valida foi encontrada para montar o SVG do layout.")

    layout_sheets = create_layout_sheets(parts, settings=settings)
    if not layout_sheets:
        raise ValueError("Nao foi possivel gerar um layout para exportar em SVG.")

    os.makedirs(output_dir, exist_ok=True)
    base_name = _lazy_get_panelnest_export_base_name(document=source_document)
    document_label = str(
        getattr(source_document, "Label", "")
        or getattr(source_document, "Name", "")
        or "Layout PanelNest"
    )
    files = []
    for layout_sheet in layout_sheets:
        file_name = (
            f"{base_name}_layout_{_slugify_export_fragment(layout_sheet.group_id)}_"
            f"chapa_{layout_sheet.sheet_index:02d}_"
            f"{_slugify_export_fragment(layout_sheet.source_label)}.svg"
        )
        file_path = os.path.join(output_dir, file_name)
        svg_text = _layout_sheet_svg_document(
            layout_sheet,
            document_label=document_label,
        )
        with open(file_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(svg_text)
        files.append(
            {
                "label": f"{layout_sheet.group_id} - {layout_sheet.source_label}",
                "path": file_path,
            }
        )

    return {
        "output_dir": output_dir,
        "base_name": base_name,
        "files": files,
        "sheet_count": len(layout_sheets),
    }



def export_panelnest_dxf_per_part(output_dir, layout_sheets, settings, as_zip=True):
    """Exporta um DXF por peça individual do layout.

    Cada DXF tem coordenada origem em (0,0), comprimento em X, largura em Y.
    Layers:
    - "CORTE"       — contorno retangular da peça
    - "FITA_SUP"    — linha superior (top) se tiver fita
    - "FITA_INF"    — linha inferior (bottom) se tiver fita
    - "FITA_ESQ"    — linha esquerda (left) se tiver fita
    - "FITA_DIR"    — linha direita (right) se tiver fita
    - "TEXTO"       — label da peça como entidade texto

    Se as_zip=True, empacota tudo em {output_dir}/panelnest_dxf_pecas.zip.
    Retorna o caminho do ZIP ou a lista de caminhos DXF.

    Requer FreeCAD e importDXF disponíveis.
    """
    if App is None:
        raise RuntimeError("A exportação DXF por peça precisa rodar dentro do FreeCAD.")

    try:
        import importDXF
    except ImportError as exc:
        raise RuntimeError(f"O módulo importDXF do FreeCAD não está disponível: {exc}")

    import zipfile
    import math as _math

    os.makedirs(output_dir, exist_ok=True)
    generated_files = []

    # Coleta peças únicas por part_id — um DXF por peça (não por ocorrência)
    seen_part_ids = {}
    unique_placements = []
    for ls in layout_sheets:
        for placement in ls.placements:
            pid = getattr(placement.part, "part_id", None) or getattr(placement.part, "label", "")
            if pid not in seen_part_ids:
                seen_part_ids[pid] = True
                unique_placements.append((placement, ls))

    original_doc_name = getattr(App.ActiveDocument, "Name", "") if App.ActiveDocument else ""

    seen_slugs = {}
    for placement, ls in unique_placements:
        part = placement.part
        L = placement.placed_length_mm
        W = placement.placed_width_mm
        label = getattr(part, "label", "peca")
        part_id = getattr(part, "part_id", "")

        # Slugificar para nome de arquivo único
        slug_base = re.sub(r"[^\w\-]", "_", f"{part_id}_{label}").strip("_")[:50]
        slug_count = seen_slugs.get(slug_base, 0)
        seen_slugs[slug_base] = slug_count + 1
        slug = slug_base if slug_count == 0 else f"{slug_base}_{slug_count}"
        dxf_path = os.path.join(output_dir, f"{slug}.dxf")

        # Criar documento temporário para a peça
        tmp_doc = App.newDocument(f"{WORKBENCH_ID}_PART_DXF")
        try:
            export_objects = []

            def _add_wire(pts, layer_name):
                """Cria um Wire no documento e atribui layer."""
                import Part as PartModule
                vecs = [App.Vector(x, y, 0) for x, y in pts]
                edges = []
                for i in range(len(vecs)):
                    edges.append(PartModule.makeLine(vecs[i], vecs[(i + 1) % len(vecs)]))
                wire = PartModule.Wire(edges)
                obj = tmp_doc.addObject("Part::Feature", layer_name)
                obj.Shape = wire
                try:
                    obj.ViewObject.LineColor = (0.0, 0.0, 0.0)
                except Exception:
                    pass
                export_objects.append(obj)

            # Contorno da peça — usa perfil real se disponível, senão retângulo
            _dxf_profile = getattr(part, "profile_points", None) or []
            if _dxf_profile:
                _add_wire(_dxf_profile, "CORTE")
            else:
                _add_wire([(0, 0), (L, 0), (L, W), (0, W)], "CORTE")

            # Marcadores de fita (linhas nas bordas com fita)
            fita_offset = 1.0  # 1mm dentro da borda
            if getattr(part, "edge_band_top", False):
                _add_wire([(0, W - fita_offset), (L, W - fita_offset)], "FITA_SUP")
            if getattr(part, "edge_band_bottom", False):
                _add_wire([(0, fita_offset), (L, fita_offset)], "FITA_INF")
            if getattr(part, "edge_band_left", False):
                _add_wire([(fita_offset, 0), (fita_offset, W)], "FITA_ESQ")
            if getattr(part, "edge_band_right", False):
                _add_wire([(L - fita_offset, 0), (L - fita_offset, W)], "FITA_DIR")

            # Furos na camada FUROS
            import Part as _PartMod
            for hole in getattr(part, "holes", None) or []:
                radius_mm = float(hole.get("diameter_mm", 0)) / 2.0
                if radius_mm <= 0:
                    continue
                hx = float(hole.get("x_mm", 0))
                hy = float(hole.get("y_mm", 0))
                try:
                    hole_obj = tmp_doc.addObject("Part::Feature", "FUROS")
                    hole_obj.Shape = _PartMod.makeCircle(
                        radius_mm, App.Vector(hx, hy, 0), App.Vector(0, 0, 1)
                    )
                    export_objects.append(hole_obj)
                except Exception:
                    pass

            # Texto com label no centro (via Draft se disponível)
            try:
                import Draft
                text_obj = Draft.make_text([label], placement=App.Placement(
                    App.Vector(L / 2, W / 2, 0), App.Rotation()
                ))
                text_obj.Label = "TEXTO"
                tmp_doc.recompute()
                export_objects.append(text_obj)
            except Exception:
                pass

            tmp_doc.recompute()
            importDXF.export(export_objects, dxf_path, nospline=True, lwPoly=False)
            _normalize_exported_dxf_layers(dxf_path)
            generated_files.append(dxf_path)

        finally:
            try:
                App.closeDocument(tmp_doc.Name)
            except Exception:
                pass

    # Restaurar documento ativo
    if original_doc_name:
        try:
            App.setActiveDocument(original_doc_name)
        except Exception:
            pass

    if not as_zip:
        return generated_files

    # Empacotar em ZIP
    zip_path = os.path.join(output_dir, "panelnest_dxf_pecas.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in generated_files:
            zf.write(f, os.path.basename(f))

    # Remover DXFs individuais após zipar
    for f in generated_files:
        try:
            os.remove(f)
        except Exception:
            pass

    return zip_path


def build_panelnest_layout_preview_html(document=None):
    """Gera HTML com SVG do plano de corte (nesting) para prévia no navegador."""
    from .parts import collect_parts
    from .metadata import get_sheet_settings
    from .nesting import create_layout_sheets

    doc = document or ensure_document()
    settings = None
    layout_sheets = []
    parts = []

    try:
        parts = collect_parts(include_hidden=True)
        settings = get_sheet_settings()
        layout_sheets = create_layout_sheets(parts, settings=settings)
    except Exception:
        pass

    if not layout_sheets:
        return _layout_preview_no_data_html()

    sheets_svg = []
    for sheet in layout_sheets:
        sheets_svg.append(_sheet_to_svg(sheet, settings))

    doc_label = str(getattr(doc, "Label", "") or "Projeto") if doc else "Projeto"
    company_header_html = ""
    if settings:
        parts_company = []
        if getattr(settings, "company_name", ""):
            parts_company.append(html.escape(settings.company_name))
        if getattr(settings, "project_client", ""):
            parts_company.append(f"Cliente: {html.escape(settings.project_client)}")
        if getattr(settings, "project_responsible", ""):
            parts_company.append(f"Resp.: {html.escape(settings.project_responsible)}")
        if parts_company:
            company_header_html = f"<p class='company-info'>{' &nbsp;·&nbsp; '.join(parts_company)}</p>"

    utilization_lines = []
    for sheet in layout_sheets:
        used = sum(p.placed_length_mm * p.placed_width_mm for p in sheet.placements)
        total = sheet.source_length_mm * sheet.source_width_mm
        pct = round(100 * used / total, 1) if total > 0 else 0.0
        n_parts = len(sheet.placements)
        utilization_lines.append(
            f"<li><b>{html.escape(sheet.source_label)}</b> — "
            f"{n_parts} peça(s) &nbsp;·&nbsp; {pct}% aproveitamento</li>"
        )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Plano de Corte — {html.escape(doc_label)}</title>
<style>
  body {{ font-family: sans-serif; background: #f5f5f5; margin: 0; padding: 16px; color: #222; }}
  h1 {{ font-size: 1.3rem; margin-bottom: 4px; }}
  .subtitle {{ color: #666; font-size: 0.9rem; margin-bottom: 16px; }}
  .summary {{ background: #fff; border: 1px solid #ddd; border-radius: 6px; padding: 12px 16px; margin-bottom: 20px; }}
  .summary ul {{ margin: 6px 0 0 0; padding-left: 20px; }}
  .company-info {{ color: #555; font-size: 0.85rem; margin: 2px 0 12px 0; }}
  .sheet-wrap {{ background: #fff; border: 1px solid #ddd; border-radius: 6px; padding: 16px; margin-bottom: 24px; }}
  .sheet-title {{ font-weight: bold; font-size: 1rem; margin-bottom: 10px; }}
  .sheet-svg {{ overflow: auto; }}
  svg {{ display: block; max-width: 100%; height: auto; }}
  @media print {{ body {{ background: white; }} .sheet-wrap {{ break-inside: avoid; }} }}
</style>
</head>
<body>
<h1>Plano de Corte — {html.escape(doc_label)}</h1>
{company_header_html}
<p class="subtitle">{len(layout_sheets)} chapa(s) &nbsp;·&nbsp; {len(parts)} tipo(s) de peça</p>
<div class="summary">
  <strong>Resumo de aproveitamento</strong>
  <ul>{''.join(utilization_lines)}</ul>
</div>
{''.join(sheets_svg)}
</body>
</html>"""


def _layout_preview_no_data_html():
    return """<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Plano de Corte</title></head>
<body style="font-family:sans-serif;padding:32px">
<h2>Nenhum layout gerado</h2>
<p>Execute <b>Gerar Layout</b> antes de abrir a prévia do plano de corte.</p>
</body></html>"""


def _sheet_to_svg(sheet, settings):
    """Renderiza uma LayoutSheet como bloco HTML com SVG embutido."""
    SW = sheet.source_length_mm   # largura da chapa no SVG (eixo X)
    SH = sheet.source_width_mm    # altura da chapa no SVG (eixo Y)

    # Escala para caber em ~900px de largura
    MAX_PX = 900
    scale = MAX_PX / SW if SW > 0 else 1.0
    svg_w = SW * scale
    svg_h = SH * scale

    margin = (getattr(settings, "margin_mm", 10) if settings else 10) * scale

    # Paleta de cores por peça (rotaciona entre tons suaves)
    PALETTE = [
        "#b3d9f7", "#b3f7d9", "#f7e4b3", "#d9b3f7",
        "#f7b3d9", "#c8f7b3", "#f7c8b3", "#b3c8f7",
        "#e8f7b3", "#f7b3b3",
    ]

    pieces_svg = []
    for i, p in enumerate(sheet.placements):
        color = PALETTE[i % len(PALETTE)]
        x = p.x_mm * scale
        y = p.y_mm * scale
        pw = p.placed_length_mm * scale
        ph = p.placed_width_mm * scale

        label_code = str(getattr(p.part, "part_id", "") or "")
        raw_label = str(getattr(p.part, "label", "") or "")
        # Remove prefixo PN-XXX do label para exibição curta
        import re as _re
        short_label = _re.sub(r"^PN-\d+\s*-\s*", "", raw_label).strip() or raw_label

        dim_text = f"{p.placed_length_mm:.0f}×{p.placed_width_mm:.0f}"
        rotated = getattr(p, "rotated", False)

        # Fita de borda — barras coloridas nas bordas
        BAND_W = max(3, 6 * scale / MAX_PX * SW)
        band_parts = []
        part = p.part
        active_layout_band_sides = {
            rotated_layout_side(source_side, placement_rotation_deg(p))
            for source_side, active in (
                ("top", getattr(part, "edge_band_top", False)),
                ("bottom", getattr(part, "edge_band_bottom", False)),
                ("left", getattr(part, "edge_band_left", False)),
                ("right", getattr(part, "edge_band_right", False)),
            )
            if active
        }
        if "top" in active_layout_band_sides:
            band_parts.append(f'<rect x="{x}" y="{y}" width="{pw}" height="{BAND_W}" fill="#e05c00" opacity="0.85"/>')
        if "bottom" in active_layout_band_sides:
            band_parts.append(f'<rect x="{x}" y="{y+ph-BAND_W}" width="{pw}" height="{BAND_W}" fill="#e05c00" opacity="0.85"/>')
        if "left" in active_layout_band_sides:
            band_parts.append(f'<rect x="{x}" y="{y}" width="{BAND_W}" height="{ph}" fill="#e05c00" opacity="0.85"/>')
        if "right" in active_layout_band_sides:
            band_parts.append(f'<rect x="{x+pw-BAND_W}" y="{y}" width="{BAND_W}" height="{ph}" fill="#e05c00" opacity="0.85"/>')

        # Seta de veio
        grain = str(getattr(part, "grain_direction", "") or "")
        grain_svg = ""
        if grain and grain != "Livre":
            cx, cy = x + pw / 2, y + ph / 2
            arr = min(pw, ph) * 0.3
            if grain == "Comprimento":
                grain_svg = (
                    f'<line x1="{cx-arr}" y1="{cy}" x2="{cx+arr}" y2="{cy}" '
                    f'stroke="#1a5fa8" stroke-width="1.5" marker-end="url(#arr)"/>'
                )
            else:
                grain_svg = (
                    f'<line x1="{cx}" y1="{cy+arr}" x2="{cx}" y2="{cy-arr}" '
                    f'stroke="#1a5fa8" stroke-width="1.5" marker-end="url(#arr)"/>'
                )

        # Texto — rotaciona 90° quando a peça foi rotacionada
        cx_text = x + pw / 2
        cy_text = y + ph / 2
        # font-size baseado no lado menor da peça para caber sempre
        min_side = min(pw, ph)
        fs = max(7, min(13, min_side * 0.18))
        fs2 = max(6, fs * 0.8)

        if rotated:
            # texto vertical: rotate -90° em torno do centro da peça
            transform = f'transform="rotate(-90,{cx_text:.1f},{cy_text:.1f})"'
            # com rotação, o espaço disponível para texto é pw (agora na vertical)
            text_space = pw
        else:
            transform = ""
            text_space = ph

        line_h = fs + 2
        t1_y = cy_text - line_h
        t2_y = cy_text + 1
        t3_y = cy_text + line_h + 3

        def _txt(y_pos, size, weight, color_fill, content):
            attrs = f'x="{cx_text:.1f}" y="{y_pos:.1f}" text-anchor="middle" font-size="{size}" font-family="sans-serif" fill="{color_fill}" font-weight="{weight}"'
            if transform:
                attrs += f' {transform}'
            return f'<text {attrs}>{html.escape(content)}</text>'

        text_svg = _txt(t1_y + fs, fs, "bold", "#222", label_code)
        if text_space > fs * 3:
            text_svg += _txt(t2_y + fs, fs2, "normal", "#444", short_label)
        if text_space > fs * 4.5:
            text_svg += _txt(t3_y + fs, fs2, "normal", "#666", dim_text)

        # Furos — transforma coordenadas quando a peça está girada 90° CCW
        holes_svg = []
        for hole in getattr(p.part, "holes", []) or []:
            hx_src = float(hole.get("x_mm", 0))
            hy_src = float(hole.get("y_mm", 0))
            local_x, local_y = transform_placement_point(p, hx_src, hy_src)
            hx = x + local_x * scale
            hy = y + local_y * scale
            hr = float(hole.get("diameter_mm", 0)) / 2.0 * scale
            if hr < 1:
                continue
            holes_svg.append(
                f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="{hr:.1f}" '
                f'fill="white" stroke="#333" stroke-width="0.8" opacity="0.85"/>'
            )

        _profile = getattr(p.part, "profile_points", None) or []
        if _profile:
            _pts_placed = [
                transform_placement_point(p, px, py)
                for px, py in _profile
            ]
            _svg_pts = " ".join(
                f"{x + px * scale:.1f},{y + py * scale:.1f}" for px, py in _pts_placed
            )
            _piece_shape = (
                f'<polygon points="{_svg_pts}" '
                f'fill="{color}" stroke="#555" stroke-width="0.8" opacity="0.9"/>'
            )
        else:
            _piece_shape = (
                f'<rect x="{x}" y="{y}" width="{pw}" height="{ph}" '
                f'fill="{color}" stroke="#555" stroke-width="0.8"/>'
            )
        pieces_svg.append(
            _piece_shape
            + "".join(band_parts)
            + grain_svg
            + "".join(holes_svg)
            + text_svg
        )

    # Margem da chapa
    margin_rect = (
        f'<rect x="{margin}" y="{margin}" '
        f'width="{svg_w - 2*margin}" height="{svg_h - 2*margin}" '
        f'fill="none" stroke="#aaa" stroke-width="0.8" stroke-dasharray="4,3"/>'
    )

    # Linhas de corte numeradas
    cuts_svg = []
    for cut in sheet.cut_steps:
        x1 = cut.start_x_mm * scale
        y1 = cut.start_y_mm * scale
        x2 = cut.end_x_mm * scale
        y2 = cut.end_y_mm * scale
        is_main = str(getattr(cut, "cut_kind", "") or "").lower() in ("corte_principal", "main", "primary", "corte")
        stroke = "#c0392b" if is_main else "#e67e22"
        cuts_svg.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{stroke}" stroke-width="1.2" stroke-dasharray="5,3" opacity="0.85"/>'
        )
        lbl_x = x1 + (x2 - x1) * 0.08 + 4
        lbl_y = y1 + (y2 - y1) * 0.08 - 3
        cuts_svg.append(
            f'<circle cx="{lbl_x:.1f}" cy="{lbl_y:.1f}" r="7" fill="{stroke}" opacity="0.9"/>'
            f'<text x="{lbl_x:.1f}" y="{lbl_y + 4:.1f}" text-anchor="middle" '
            f'font-size="8" font-family="sans-serif" fill="white" font-weight="bold">'
            f'{cut.step_index}</text>'
        )

    used_area = sum(p.placed_length_mm * p.placed_width_mm for p in sheet.placements)
    total_area = SW * SH
    pct = round(100 * used_area / total_area, 1) if total_area > 0 else 0.0

    sheet_title = (
        f"{html.escape(sheet.source_label)} &nbsp;·&nbsp; "
        f"{sheet.source_length_mm:.0f}×{sheet.source_width_mm:.0f} mm &nbsp;·&nbsp; "
        f"{len(sheet.placements)} peça(s) &nbsp;·&nbsp; {pct}% aproveitamento"
    )

    svg_content = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {svg_w:.1f} {svg_h:.1f}" '
        f'width="{svg_w:.0f}" height="{svg_h:.0f}">'
        '<defs><marker id="arr" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">'
        '<path d="M0,0 L0,6 L6,3 z" fill="#1a5fa8"/></marker></defs>'
        f'<rect width="{svg_w:.1f}" height="{svg_h:.1f}" fill="#f0ece4" stroke="#333" stroke-width="1.5"/>'
        + margin_rect
        + "".join(pieces_svg)
        + "".join(cuts_svg)
        + "</svg>"
    )

    return (
        f'<div class="sheet-wrap">'
        f'<div class="sheet-title">{sheet_title}</div>'
        f'<div class="sheet-svg">{svg_content}</div>'
        f'</div>'
    )


def build_panelnest_cut_sheet_html(document=None):
    """Gera HTML A4 imprimível com plano de corte por chapa: SVG + sequência numerada de cortes."""
    from .parts import collect_parts
    from .metadata import get_sheet_settings
    from .nesting import create_layout_sheets

    doc = document or ensure_document()
    try:
        parts = collect_parts(include_hidden=True)
        settings = get_sheet_settings()
        layout_sheets = create_layout_sheets(parts, settings=settings)
    except Exception:
        layout_sheets = []
        settings = None
        parts = []

    if not layout_sheets:
        return """<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Plano de Corte</title></head>
<body style="font-family:sans-serif;padding:32px">
<h2>Nenhum layout gerado</h2>
<p>Execute <b>Gerar Layout</b> antes de abrir o plano de corte.</p>
</body></html>"""

    doc_label = str(getattr(doc, "Label", "") or "Projeto") if doc else "Projeto"
    exported_at = datetime.now().strftime("%d/%m/%Y %H:%M")
    company_info = _company_meta_inline_html(settings)

    pages = []
    for sheet in layout_sheets:
        svg_block = _sheet_to_svg_for_cut_sheet(sheet, settings)
        cut_table = _cut_steps_table_html(sheet)
        parts_table = _placed_parts_table_html(sheet)

        material_line = []
        if sheet.material:
            material_line.append(html.escape(sheet.material))
        if sheet.thickness_mm:
            material_line.append(f"{sheet.thickness_mm:.0f} mm")
        if sheet.cut_method:
            material_line.append(html.escape(sheet.cut_method))
        material_text = " &nbsp;·&nbsp; ".join(material_line)

        used_area = sum(p.placed_length_mm * p.placed_width_mm for p in sheet.placements)
        total_area = sheet.source_length_mm * sheet.source_width_mm
        pct = round(100 * used_area / total_area, 1) if total_area > 0 else 0.0

        pages.append(f"""
<div class="page">
  <div class="page-header">
    <div class="header-left">
      <div class="doc-title">{html.escape(doc_label)}</div>
      <div class="company-line">{company_info}</div>
    </div>
    <div class="header-right">
      <div class="sheet-id">{html.escape(sheet.group_id)} — Chapa {sheet.sheet_index}</div>
      <div class="sheet-meta">{html.escape(sheet.source_label)}</div>
      {f'<div class="sheet-meta">{material_text}</div>' if material_text else ''}
      <div class="sheet-meta">{sheet.source_length_mm:.0f} × {sheet.source_width_mm:.0f} mm &nbsp;·&nbsp; {pct}% aproveitamento</div>
      <div class="sheet-date">{html.escape(exported_at)}</div>
    </div>
  </div>
  <div class="layout-svg">{svg_block}</div>
  <div class="tables-row">
    <div class="table-col">
      <div class="table-label">Peças nesta chapa</div>
      {parts_table}
    </div>
    {'<div class="table-col"><div class="table-label">Sequência de cortes</div>' + cut_table + '</div>' if sheet.cut_steps else ''}
  </div>
</div>""")

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Plano de Corte — {html.escape(doc_label)}</title>
<style>
  @page {{ size: A4 landscape; margin: 8mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: Arial, sans-serif; font-size: 8.5pt; color: #222; margin: 0; background: #fff; }}
  .page {{
    width: 277mm; min-height: 190mm;
    page-break-after: always;
    break-after: page;
    padding: 0;
    display: flex;
    flex-direction: column;
  }}
  .page-header {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    border-bottom: 0.5mm solid #8f3f38;
    padding-bottom: 3mm;
    margin-bottom: 4mm;
  }}
  .header-left {{ flex: 1; }}
  .header-right {{ text-align: right; }}
  .doc-title {{ font-size: 13pt; font-weight: bold; color: #2d2b2b; }}
  .company-line {{ color: #666; font-size: 7.5pt; margin-top: 1mm; }}
  .sheet-id {{ font-size: 11pt; font-weight: bold; color: #8f3f38; }}
  .sheet-meta {{ color: #555; font-size: 7.5pt; margin-top: 0.8mm; }}
  .sheet-date {{ color: #999; font-size: 7pt; margin-top: 1mm; }}
  .layout-svg {{ flex: 1; display: flex; justify-content: center; margin-bottom: 3mm; }}
  .layout-svg svg {{ max-width: 100%; max-height: 110mm; width: auto; height: auto; display: block; }}
  .tables-row {{ display: flex; gap: 6mm; }}
  .table-col {{ flex: 1; }}
  .table-label {{ font-weight: bold; font-size: 7.5pt; color: #8f3f38; margin-bottom: 1.5mm; text-transform: uppercase; letter-spacing: 0.1mm; }}
  table.ct {{ width: 100%; border-collapse: collapse; font-size: 7pt; }}
  table.ct th {{ background: #f1ded7; color: #6c3d37; font-weight: 700; padding: 1mm 1.5mm; border: 0.2mm solid #d8c7c2; white-space: nowrap; }}
  table.ct td {{ padding: 1mm 1.5mm; border: 0.2mm solid #d8c7c2; color: #3f3a39; vertical-align: top; }}
  table.ct tr:nth-child(even) td {{ background: #fbf6f4; }}
  .step-num {{ display: inline-block; width: 14px; height: 14px; border-radius: 50%; background: #c0392b; color: white; text-align: center; line-height: 14px; font-size: 7pt; font-weight: bold; }}
  .step-num.secondary {{ background: #e67e22; }}
  @media print {{
    body {{ background: white; }}
    .page {{ page-break-after: always; }}
  }}
</style>
</head>
<body>
{''.join(pages)}
</body>
</html>"""


def _sheet_to_svg_for_cut_sheet(sheet, settings):
    """SVG compacto para o plano de corte A4 — sem título externo, cortes numerados."""
    SW = sheet.source_length_mm
    SH = sheet.source_width_mm
    MAX_W = 680
    MAX_H = 330
    scale_w = MAX_W / SW if SW > 0 else 1.0
    scale_h = MAX_H / SH if SH > 0 else 1.0
    scale = min(scale_w, scale_h)
    svg_w = SW * scale
    svg_h = SH * scale
    margin = (getattr(settings, "margin_mm", 10) if settings else 10) * scale

    PALETTE = [
        "#b3d9f7", "#b3f7d9", "#f7e4b3", "#d9b3f7",
        "#f7b3d9", "#c8f7b3", "#f7c8b3", "#b3c8f7",
        "#e8f7b3", "#f7b3b3",
    ]

    pieces_svg = []
    for i, p in enumerate(sheet.placements):
        color = PALETTE[i % len(PALETTE)]
        x = p.x_mm * scale
        y = p.y_mm * scale
        pw = p.placed_length_mm * scale
        ph = p.placed_width_mm * scale
        label_code = str(getattr(p.part, "part_id", "") or "")
        min_side = min(pw, ph)
        fs = max(6, min(11, min_side * 0.20))
        cx, cy = x + pw / 2, y + ph / 2
        rotated = getattr(p, "rotated", False)
        transform = f'transform="rotate(-90,{cx:.1f},{cy:.1f})"' if rotated else ""
        holes_svg_cs = []
        for hole in getattr(p.part, "holes", []) or []:
            hx_src = float(hole.get("x_mm", 0))
            hy_src = float(hole.get("y_mm", 0))
            local_x, local_y = transform_placement_point(p, hx_src, hy_src)
            hx = x + local_x * scale
            hy = y + local_y * scale
            hr = float(hole.get("diameter_mm", 0)) / 2.0 * scale
            if hr < 1:
                continue
            holes_svg_cs.append(
                f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="{hr:.1f}" '
                f'fill="white" stroke="#333" stroke-width="0.7" opacity="0.85"/>'
            )
        _profile_cs = getattr(p.part, "profile_points", None) or []
        if _profile_cs:
            _pts_cs = [
                transform_placement_point(p, px, py)
                for px, py in _profile_cs
            ]
            _svg_pts_cs = " ".join(
                f"{x + px * scale:.1f},{y + py * scale:.1f}" for px, py in _pts_cs
            )
            _shape_cs = (
                f'<polygon points="{_svg_pts_cs}" '
                f'fill="{color}" stroke="#555" stroke-width="0.7"/>'
            )
        else:
            _shape_cs = (
                f'<rect x="{x}" y="{y}" width="{pw}" height="{ph}" '
                f'fill="{color}" stroke="#555" stroke-width="0.7"/>'
            )
        pieces_svg.append(
            _shape_cs
            + "".join(holes_svg_cs)
            + f'<text x="{cx:.1f}" y="{cy + fs*0.35:.1f}" text-anchor="middle" font-size="{fs}" '
            f'font-family="sans-serif" fill="#222" font-weight="bold" {transform}>{html.escape(label_code)}</text>'
        )

    cuts_svg = []
    for cut in sheet.cut_steps:
        x1, y1 = cut.start_x_mm * scale, cut.start_y_mm * scale
        x2, y2 = cut.end_x_mm * scale, cut.end_y_mm * scale
        is_main = str(getattr(cut, "cut_kind", "") or "").lower() in ("corte_principal", "main", "primary", "corte")
        stroke = "#c0392b" if is_main else "#e67e22"
        cuts_svg.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{stroke}" stroke-width="1.0" stroke-dasharray="4,2" opacity="0.9"/>'
        )
        lbl_x = x1 + (x2 - x1) * 0.06 + 5
        lbl_y = y1 + (y2 - y1) * 0.06 - 4
        r = 5
        cuts_svg.append(
            f'<circle cx="{lbl_x:.1f}" cy="{lbl_y:.1f}" r="{r}" fill="{stroke}"/>'
            f'<text x="{lbl_x:.1f}" y="{lbl_y + 3.5:.1f}" text-anchor="middle" '
            f'font-size="6" font-family="sans-serif" fill="white" font-weight="bold">{cut.step_index}</text>'
        )

    margin_rect = (
        f'<rect x="{margin}" y="{margin}" width="{svg_w-2*margin}" height="{svg_h-2*margin}" '
        f'fill="none" stroke="#aaa" stroke-width="0.6" stroke-dasharray="3,2"/>'
    )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {svg_w:.1f} {svg_h:.1f}" '
        f'width="{svg_w:.0f}" height="{svg_h:.0f}">'
        '<defs><marker id="arr" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">'
        '<path d="M0,0 L0,5 L5,2.5 z" fill="#1a5fa8"/></marker></defs>'
        f'<rect width="{svg_w:.1f}" height="{svg_h:.1f}" fill="#f0ece4" stroke="#333" stroke-width="1.2"/>'
        + margin_rect
        + "".join(pieces_svg)
        + "".join(cuts_svg)
        + "</svg>"
    )


def _cut_steps_table_html(sheet):
    if not sheet.cut_steps:
        return "<p style='color:#999;font-size:7pt'>Sem sequência de cortes para este layout.</p>"
    rows = []
    for cut in sheet.cut_steps:
        is_main = str(getattr(cut, "cut_kind", "") or "").lower() in ("corte_principal", "main", "primary", "corte")
        cls = "step-num" if is_main else "step-num secondary"
        orient = "H" if str(getattr(cut, "orientation", "") or "").upper().startswith("H") else "V"
        pos = f"{cut.position_mm:.0f} mm"
        span = f"{cut.span_mm:.0f} mm"
        desc = html.escape(str(getattr(cut, "description", "") or cut.cut_kind or ""))
        rows.append(
            f"<tr><td><span class='{cls}'>{cut.step_index}</span></td>"
            f"<td>{orient}</td><td>{pos}</td><td>{span}</td><td>{desc}</td></tr>"
        )
    return (
        "<table class='ct'>"
        "<thead><tr><th>#</th><th>Dir.</th><th>Posição</th><th>Span</th><th>Descrição</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody>"
        "</table>"
    )


def _placed_parts_table_html(sheet):
    if not sheet.placements:
        return "<p style='color:#999;font-size:7pt'>Sem peças.</p>"
    rows = []
    for p in sheet.placements:
        part_id = html.escape(str(getattr(p.part, "part_id", "") or ""))
        raw_label = str(getattr(p.part, "label", "") or "")
        short_label = re.sub(r"^PN-\d+\s*-\s*", "", raw_label).strip() or raw_label
        dims = f"{p.placed_length_mm:.0f} × {p.placed_width_mm:.0f}"
        rotation_deg = placement_rotation_deg(p)
        rot = f" ↺{rotation_deg}°" if rotation_deg else ""
        bands = []
        part = p.part
        if getattr(part, "edge_band_top", False): bands.append("T")
        if getattr(part, "edge_band_bottom", False): bands.append("B")
        if getattr(part, "edge_band_left", False): bands.append("E")
        if getattr(part, "edge_band_right", False): bands.append("D")
        band_text = "/".join(bands) if bands else "—"
        rows.append(
            f"<tr><td>{part_id}</td><td>{html.escape(short_label)}</td>"
            f"<td>{dims}{rot}</td><td>{band_text}</td></tr>"
        )
    return (
        "<table class='ct'>"
        "<thead><tr><th>ID</th><th>Nome</th><th>Dimensões</th><th>Fita</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody>"
        "</table>"
    )


# ---------------------------------------------------------------------------
# Desenho técnico individual por peça (SVG com cotas)
# ---------------------------------------------------------------------------

def generate_part_technical_drawing_svg(part, scale=None):
    """Gera SVG de desenho técnico de uma peça com cotas e marcações de fita.

    Retorna string SVG completa (A4 landscape, com legendas e cotas).
    scale: escala em px/mm. Se None, calcula automaticamente para caber na página.
    """
    L = part.length_mm
    W = part.width_mm
    T = part.thickness_mm

    # Área de desenho A4 landscape (mm)
    page_w, page_h = 297, 210
    margin = 30  # mm de margem
    legend_h = 30  # mm para legenda inferior
    cota_space = 20  # mm para cotas em cada lado
    draw_w = page_w - 2 * margin - cota_space
    draw_h = page_h - 2 * margin - legend_h - cota_space

    if scale is None:
        sx = draw_w / max(L, 1)
        sy = draw_h / max(W, 1)
        scale = min(sx, sy, 1.0)  # máximo 1:1

    # Coordenadas do retângulo da peça (em mm de página)
    rect_x = margin + cota_space
    rect_y = margin
    rect_w = L * scale
    rect_h = W * scale

    # PX per mm (SVG em mm)
    def _fmt(v):
        return f"{v:.2f}"

    # Fita de borda
    eb_color = "#1BB0C2"
    eb_width = max(1.5, 3 * scale)

    eb_lines = ""
    if getattr(part, "edge_band_top", False):
        eb_lines += f'<line x1="{_fmt(rect_x)}" y1="{_fmt(rect_y)}" x2="{_fmt(rect_x + rect_w)}" y2="{_fmt(rect_y)}" stroke="{eb_color}" stroke-width="{_fmt(eb_width)}" stroke-linecap="round"/>\n'
    if getattr(part, "edge_band_bottom", False):
        eb_lines += f'<line x1="{_fmt(rect_x)}" y1="{_fmt(rect_y + rect_h)}" x2="{_fmt(rect_x + rect_w)}" y2="{_fmt(rect_y + rect_h)}" stroke="{eb_color}" stroke-width="{_fmt(eb_width)}" stroke-linecap="round"/>\n'
    if getattr(part, "edge_band_left", False):
        eb_lines += f'<line x1="{_fmt(rect_x)}" y1="{_fmt(rect_y)}" x2="{_fmt(rect_x)}" y2="{_fmt(rect_y + rect_h)}" stroke="{eb_color}" stroke-width="{_fmt(eb_width)}" stroke-linecap="round"/>\n'
    if getattr(part, "edge_band_right", False):
        eb_lines += f'<line x1="{_fmt(rect_x + rect_w)}" y1="{_fmt(rect_y)}" x2="{_fmt(rect_x + rect_w)}" y2="{_fmt(rect_y + rect_h)}" stroke="{eb_color}" stroke-width="{_fmt(eb_width)}" stroke-linecap="round"/>\n'

    # Furos
    holes_svg = ""
    for hole in getattr(part, "holes", []) or []:
        hx = float(hole.get("x_mm", 0))
        hy = float(hole.get("y_mm", 0))
        hd = float(hole.get("diameter_mm", 5))
        hr = (hd / 2) * scale
        cx = rect_x + hx * scale
        cy = rect_y + hy * scale
        holes_svg += f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(hr)}" fill="none" stroke="#c00" stroke-width="0.4"/>\n'
        holes_svg += f'<line x1="{_fmt(cx - hr)}" y1="{_fmt(cy)}" x2="{_fmt(cx + hr)}" y2="{_fmt(cy)}" stroke="#c00" stroke-width="0.2"/>\n'
        holes_svg += f'<line x1="{_fmt(cx)}" y1="{_fmt(cy - hr)}" x2="{_fmt(cx)}" y2="{_fmt(cy + hr)}" stroke="#c00" stroke-width="0.2"/>\n'

    # Cotas - comprimento (topo)
    cota_y_top = rect_y - 5
    dim_length = f"""
    <line x1="{_fmt(rect_x)}" y1="{_fmt(cota_y_top)}" x2="{_fmt(rect_x + rect_w)}" y2="{_fmt(cota_y_top)}" stroke="#333" stroke-width="0.3" marker-start="url(#arrow-start)" marker-end="url(#arrow-end)"/>
    <line x1="{_fmt(rect_x)}" y1="{_fmt(cota_y_top - 3)}" x2="{_fmt(rect_x)}" y2="{_fmt(cota_y_top + 3)}" stroke="#333" stroke-width="0.25"/>
    <line x1="{_fmt(rect_x + rect_w)}" y1="{_fmt(cota_y_top - 3)}" x2="{_fmt(rect_x + rect_w)}" y2="{_fmt(cota_y_top + 3)}" stroke="#333" stroke-width="0.25"/>
    <text x="{_fmt(rect_x + rect_w / 2)}" y="{_fmt(cota_y_top - 2)}" text-anchor="middle" font-size="3.5" fill="#333" font-family="sans-serif">{_fmt(L)} mm</text>
    """

    # Cotas - largura (esquerda)
    cota_x_left = rect_x - 5
    dim_width = f"""
    <line x1="{_fmt(cota_x_left)}" y1="{_fmt(rect_y)}" x2="{_fmt(cota_x_left)}" y2="{_fmt(rect_y + rect_h)}" stroke="#333" stroke-width="0.3" marker-start="url(#arrow-start)" marker-end="url(#arrow-end)"/>
    <line x1="{_fmt(cota_x_left - 3)}" y1="{_fmt(rect_y)}" x2="{_fmt(cota_x_left + 3)}" y2="{_fmt(rect_y)}" stroke="#333" stroke-width="0.25"/>
    <line x1="{_fmt(cota_x_left - 3)}" y1="{_fmt(rect_y + rect_h)}" x2="{_fmt(cota_x_left + 3)}" y2="{_fmt(rect_y + rect_h)}" stroke="#333" stroke-width="0.25"/>
    <text x="{_fmt(cota_x_left - 3)}" y="{_fmt(rect_y + rect_h / 2)}" text-anchor="middle" font-size="3.5" fill="#333" font-family="sans-serif" transform="rotate(-90 {_fmt(cota_x_left - 3)} {_fmt(rect_y + rect_h / 2)})">{_fmt(W)} mm</text>
    """

    # Seta de veio
    grain_svg = ""
    grain_dir = getattr(part, "grain_direction", "Livre") or "Livre"
    grain_rotated = getattr(part, "grain_rotated", False)
    if grain_dir != "Livre":
        gx = rect_x + rect_w + 6
        gy = rect_y + rect_h / 2
        if (grain_dir == "Comprimento da chapa" and not grain_rotated) or \
           (grain_dir == "Largura da chapa" and grain_rotated):
            # Seta horizontal
            grain_svg = f'''
            <line x1="{_fmt(gx)}" y1="{_fmt(gy - 8)}" x2="{_fmt(gx)}" y2="{_fmt(gy + 8)}" stroke="#666" stroke-width="0.6" marker-start="url(#arrow-start)" marker-end="url(#arrow-end)"/>
            <text x="{_fmt(gx + 2)}" y="{_fmt(gy + 1)}" font-size="2.5" fill="#666" font-family="sans-serif">veio</text>
            '''
        else:
            # Seta vertical
            grain_svg = f'''
            <line x1="{_fmt(gx - 8)}" y1="{_fmt(gy)}" x2="{_fmt(gx + 8)}" y2="{_fmt(gy)}" stroke="#666" stroke-width="0.6" marker-start="url(#arrow-start)" marker-end="url(#arrow-end)"/>
            <text x="{_fmt(gx)}" y="{_fmt(gy - 2)}" font-size="2.5" fill="#666" font-family="sans-serif" text-anchor="middle">veio</text>
            '''

    # Legenda
    label = getattr(part, "label", "")
    part_id = getattr(part, "part_id", "")
    material = getattr(part, "material", "") or "—"
    bands = []
    if getattr(part, "edge_band_top", False): bands.append("Sup")
    if getattr(part, "edge_band_bottom", False): bands.append("Inf")
    if getattr(part, "edge_band_left", False): bands.append("Esq")
    if getattr(part, "edge_band_right", False): bands.append("Dir")
    fita_text = " + ".join(bands) if bands else "Sem fita"

    legend_y = page_h - legend_h
    legend_svg = f"""
    <rect x="{_fmt(margin)}" y="{_fmt(legend_y)}" width="{_fmt(page_w - 2 * margin)}" height="{_fmt(legend_h - 5)}" fill="none" stroke="#333" stroke-width="0.4"/>
    <text x="{_fmt(margin + 4)}" y="{_fmt(legend_y + 7)}" font-size="4" fill="#1a3a5c" font-weight="bold" font-family="sans-serif">{html.escape(part_id)} — {html.escape(PART_LABEL_PATTERN.sub('', label))}</text>
    <text x="{_fmt(margin + 4)}" y="{_fmt(legend_y + 13)}" font-size="3" fill="#333" font-family="sans-serif">Material: {html.escape(material)} | Espessura: {_fmt(T)} mm | Fita: {html.escape(fita_text)}</text>
    <text x="{_fmt(margin + 4)}" y="{_fmt(legend_y + 19)}" font-size="3" fill="#333" font-family="sans-serif">Dimensões: {_fmt(L)} × {_fmt(W)} × {_fmt(T)} mm</text>
    <text x="{_fmt(page_w - margin - 4)}" y="{_fmt(legend_y + 7)}" font-size="2.5" fill="#888" font-family="sans-serif" text-anchor="end">Escala: 1:{_fmt(1 / scale) if scale < 1 else '1'}</text>
    <text x="{_fmt(page_w - margin - 4)}" y="{_fmt(legend_y + 13)}" font-size="2" fill="#aaa" font-family="sans-serif" text-anchor="end">PanelNest</text>
    """

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {page_w} {page_h}" width="{page_w}mm" height="{page_h}mm">
<defs>
    <marker id="arrow-end" markerWidth="4" markerHeight="3" refX="4" refY="1.5" orient="auto">
        <polygon points="0 0, 4 1.5, 0 3" fill="#333"/>
    </marker>
    <marker id="arrow-start" markerWidth="4" markerHeight="3" refX="0" refY="1.5" orient="auto">
        <polygon points="4 0, 0 1.5, 4 3" fill="#333"/>
    </marker>
</defs>

<!-- Borda da página -->
<rect x="{_fmt(margin - 2)}" y="{_fmt(margin - 2)}" width="{_fmt(page_w - 2 * margin + 4)}" height="{_fmt(page_h - 2 * margin + 4)}" fill="none" stroke="#ccc" stroke-width="0.3"/>

<!-- Peça -->
<rect x="{_fmt(rect_x)}" y="{_fmt(rect_y)}" width="{_fmt(rect_w)}" height="{_fmt(rect_h)}" fill="#faf5ee" stroke="#262d33" stroke-width="0.5"/>

<!-- Fita de borda -->
{eb_lines}

<!-- Furos -->
{holes_svg}

<!-- Cotas -->
{dim_length}
{dim_width}

<!-- Veio -->
{grain_svg}

<!-- Legenda -->
{legend_svg}
</svg>"""
    return svg


def export_part_technical_drawings(parts, output_dir, as_zip=True):
    """Exporta SVG de desenho técnico para cada peça.

    Retorna caminho do ZIP ou lista de caminhos SVG.
    """
    import zipfile as _zipfile

    os.makedirs(output_dir, exist_ok=True)
    generated = []
    seen_ids = set()

    for part in parts:
        pid = getattr(part, "part_id", "") or getattr(part, "label", "peca")
        if pid in seen_ids:
            continue
        seen_ids.add(pid)

        svg = generate_part_technical_drawing_svg(part)
        slug = re.sub(r"[^\w\-]", "_", f"{pid}_{getattr(part, 'label', '')}").strip("_")[:50]
        path = os.path.join(output_dir, f"{slug}_tecnico.svg")
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
        generated.append(path)

    if as_zip and generated:
        zip_path = os.path.join(output_dir, "panelnest_desenhos_tecnicos.zip")
        with _zipfile.ZipFile(zip_path, "w", _zipfile.ZIP_DEFLATED) as zf:
            for p in generated:
                zf.write(p, os.path.basename(p))
        for p in generated:
            try:
                os.unlink(p)
            except Exception:
                pass
        return zip_path

    return generated
