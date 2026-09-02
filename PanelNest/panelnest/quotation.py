"""Geração de orçamento/cotação para o cliente.

Produz um HTML formatado profissionalmente com:
- Dados da empresa e do cliente
- Lista de peças com dimensões e materiais
- Custo de material (chapas + fita de borda)
- Custo de ferragens
- Custo de corte (CNC e seccionadora separados)
- Desperdício estimado
- Total geral com margem configurável
"""
import datetime

from .models import SheetSettings
from .freecad_utils import ensure_document


def build_quotation_data(parts, layout_sheets, settings):
    """Compila todos os dados necessários para o orçamento.

    Retorna dict com seções: empresa, cliente, pecas, materiais, ferragens,
    fita_de_borda, corte, desperdicio, totais.
    """
    from .nesting import (
        _layout_sheet_utilization_ratio,
        _layout_sheet_used_area_mm2,
        _layout_sheet_usable_rect,
    )

    # --- Empresa e cliente ---
    empresa = {
        "nome": getattr(settings, "company_name", "") or "",
        "contato": getattr(settings, "company_contact", "") or "",
        "endereco": getattr(settings, "company_address", "") or "",
    }
    cliente = {
        "nome": getattr(settings, "project_client", "") or "",
        "responsavel": getattr(settings, "project_responsible", "") or "",
        "notas": getattr(settings, "project_notes", "") or "",
    }

    # --- Peças ---
    pecas = []
    for part in parts:
        qty = max(1, int(getattr(part, "quantity", 1) or 1))
        pecas.append({
            "id": part.part_id,
            "label": part.label,
            "length_mm": part.length_mm,
            "width_mm": part.width_mm,
            "thickness_mm": part.thickness_mm,
            "material": part.material or "Sem material",
            "quantity": qty,
            "area_m2": (part.length_mm * part.width_mm * qty) / 1_000_000,
        })
    total_pecas = sum(p["quantity"] for p in pecas)
    total_area_pecas_m2 = sum(p["area_m2"] for p in pecas)

    # --- Materiais (chapas) ---
    chapas = []
    cost_per_sheet = float(getattr(settings, "full_sheet_cost", 0.0) or 0.0)
    total_chapas_cost = 0.0
    total_chapas_count = 0

    sheet_groups = {}
    for ls in layout_sheets:
        key = (ls.material or "Sem material", ls.thickness_mm, ls.source_kind)
        sheet_groups.setdefault(key, []).append(ls)

    for (mat, thick, kind), sheets in sorted(sheet_groups.items()):
        count = len(sheets)
        per_sheet = getattr(sheets[0], "_cost_per_sheet", cost_per_sheet) if sheets else cost_per_sheet
        total = per_sheet * count
        chapas.append({
            "material": mat,
            "thickness_mm": thick,
            "kind": kind,
            "count": count,
            "cost_per_sheet": per_sheet,
            "total_cost": total,
        })
        total_chapas_cost += total
        total_chapas_count += count

    # --- Fita de borda ---
    eb_price_per_m = float(getattr(settings, "edge_band_price_per_m", 0.0) or 0.0)
    eb_waste_factor = float(getattr(settings, "edge_band_waste_factor", 0.10) or 0.10)
    total_linear_mm = 0.0
    for part in parts:
        qty = max(1, int(getattr(part, "quantity", 1) or 1))
        if getattr(part, "edge_band_top", False):
            total_linear_mm += part.length_mm * qty
        if getattr(part, "edge_band_bottom", False):
            total_linear_mm += part.length_mm * qty
        if getattr(part, "edge_band_left", False):
            total_linear_mm += part.width_mm * qty
        if getattr(part, "edge_band_right", False):
            total_linear_mm += part.width_mm * qty

    total_linear_m = total_linear_mm / 1000.0
    total_linear_with_waste = total_linear_m * (1.0 + eb_waste_factor)
    fita_cost = total_linear_with_waste * eb_price_per_m

    # --- Ferragens ---
    hw_catalog = {hw.hw_id: hw for hw in getattr(settings, "hardware_catalog", []) or []}
    hw_totals = {}
    for part in parts:
        qty = max(1, int(getattr(part, "quantity", 1) or 1))
        for hw_entry in getattr(part, "hardware", []) or []:
            hw_id = hw_entry.get("hw_id", "")
            hw_qty = int(hw_entry.get("qty", 0) or 0)
            if hw_id and hw_qty > 0:
                hw_totals.setdefault(hw_id, 0)
                hw_totals[hw_id] += hw_qty * qty

    ferragens = []
    total_ferragens_cost = 0.0
    for hw_id, total_qty in sorted(hw_totals.items()):
        hw = hw_catalog.get(hw_id)
        name = hw.name if hw else hw_id
        unit = hw.unit if hw else "un"
        unit_cost = hw.cost if hw else 0.0
        line_cost = unit_cost * total_qty
        ferragens.append({
            "hw_id": hw_id,
            "name": name,
            "unit": unit,
            "quantity": total_qty,
            "unit_cost": unit_cost,
            "total_cost": line_cost,
        })
        total_ferragens_cost += line_cost

    # --- Desperdício ---
    total_waste_area_m2 = 0.0
    total_usable_area_m2 = 0.0
    for ls in layout_sheets:
        usable_rect = _layout_sheet_usable_rect(ls, settings)
        usable = usable_rect["length_mm"] * usable_rect["width_mm"]
        used = _layout_sheet_used_area_mm2(ls)
        total_usable_area_m2 += usable / 1_000_000
        total_waste_area_m2 += max(0.0, usable - used) / 1_000_000

    utilization_pct = 0.0
    if total_usable_area_m2 > 0:
        utilization_pct = ((total_usable_area_m2 - total_waste_area_m2) / total_usable_area_m2) * 100

    # --- Totais ---
    subtotal = total_chapas_cost + fita_cost + total_ferragens_cost

    return {
        "empresa": empresa,
        "cliente": cliente,
        "data": datetime.date.today().strftime("%d/%m/%Y"),
        "pecas": pecas,
        "total_pecas": total_pecas,
        "total_area_pecas_m2": total_area_pecas_m2,
        "chapas": chapas,
        "total_chapas_count": total_chapas_count,
        "total_chapas_cost": total_chapas_cost,
        "fita": {
            "linear_m": total_linear_m,
            "linear_with_waste_m": total_linear_with_waste,
            "waste_factor_pct": eb_waste_factor * 100,
            "price_per_m": eb_price_per_m,
            "total_cost": fita_cost,
        },
        "ferragens": ferragens,
        "total_ferragens_cost": total_ferragens_cost,
        "desperdicio": {
            "waste_area_m2": total_waste_area_m2,
            "utilization_pct": utilization_pct,
        },
        "subtotal": subtotal,
    }


def build_quotation_html(quotation_data, margin_pct=0.0, extra_costs=None):
    """Gera HTML profissional do orçamento para enviar ao cliente.

    margin_pct: margem de lucro (ex: 30.0 = 30%)
    extra_costs: lista de dicts {descricao, valor} para custos adicionais (mão de obra, frete, etc.)
    """
    d = quotation_data
    if extra_costs is None:
        extra_costs = []

    subtotal = d["subtotal"]
    extras_total = sum(float(e.get("valor", 0) or 0) for e in extra_costs)
    pre_margin = subtotal + extras_total
    margin_value = pre_margin * (margin_pct / 100.0) if margin_pct > 0 else 0.0
    total_final = pre_margin + margin_value

    def _money(v):
        if v == 0:
            return "-"
        return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def _num(v, decimals=2):
        return f"{v:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")

    empresa_html = ""
    if d["empresa"]["nome"]:
        empresa_html = f"""
        <div class="company">
            <h2>{_esc(d['empresa']['nome'])}</h2>
            {'<p>' + _esc(d['empresa']['contato']) + '</p>' if d['empresa']['contato'] else ''}
            {'<p>' + _esc(d['empresa']['endereco']) + '</p>' if d['empresa']['endereco'] else ''}
        </div>"""

    cliente_html = ""
    if d["cliente"]["nome"]:
        cliente_html = f"""
        <div class="client-info">
            <strong>Cliente:</strong> {_esc(d['cliente']['nome'])}<br>
            {'<strong>Responsável:</strong> ' + _esc(d['cliente']['responsavel']) + '<br>' if d['cliente']['responsavel'] else ''}
            {'<strong>Obs:</strong> ' + _esc(d['cliente']['notas']) + '<br>' if d['cliente']['notas'] else ''}
        </div>"""

    # Tabela de peças
    pecas_rows = ""
    for p in d["pecas"]:
        pecas_rows += f"""
            <tr>
                <td>{_esc(p['id'])}</td>
                <td>{_esc(p['label'])}</td>
                <td>{_esc(p['material'])}</td>
                <td class="r">{_num(p['length_mm'], 1)} × {_num(p['width_mm'], 1)} × {_num(p['thickness_mm'], 1)}</td>
                <td class="r">{p['quantity']}</td>
                <td class="r">{_num(p['area_m2'], 3)}</td>
            </tr>"""

    # Tabela de chapas
    chapas_rows = ""
    for c in d["chapas"]:
        chapas_rows += f"""
            <tr>
                <td>{_esc(c['material'])}</td>
                <td class="r">{_num(c['thickness_mm'], 1)}</td>
                <td>{_esc(c['kind'])}</td>
                <td class="r">{c['count']}</td>
                <td class="r">{_money(c['cost_per_sheet'])}</td>
                <td class="r">{_money(c['total_cost'])}</td>
            </tr>"""

    # Tabela de ferragens
    ferragens_rows = ""
    for f in d["ferragens"]:
        ferragens_rows += f"""
            <tr>
                <td>{_esc(f['name'])}</td>
                <td>{_esc(f['unit'])}</td>
                <td class="r">{f['quantity']}</td>
                <td class="r">{_money(f['unit_cost'])}</td>
                <td class="r">{_money(f['total_cost'])}</td>
            </tr>"""

    # Custos extras
    extras_rows = ""
    for e in extra_costs:
        extras_rows += f"""
            <tr>
                <td>{_esc(str(e.get('descricao', '')))}</td>
                <td class="r">{_money(float(e.get('valor', 0)))}</td>
            </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Orçamento — PanelNest</title>
<style>
    body {{ font-family: 'Segoe UI', Tahoma, sans-serif; font-size: 13px; color: #222; max-width: 900px; margin: 0 auto; padding: 24px; }}
    h1 {{ color: #1a3a5c; border-bottom: 3px solid #1a3a5c; padding-bottom: 8px; font-size: 22px; }}
    h2 {{ color: #1a3a5c; font-size: 16px; margin-top: 24px; }}
    h3 {{ color: #2d5a8c; font-size: 14px; margin-top: 18px; margin-bottom: 6px; }}
    .company {{ text-align: center; margin-bottom: 20px; }}
    .company h2 {{ border: none; font-size: 20px; }}
    .client-info {{ background: #f0f5fa; padding: 12px 16px; border-radius: 6px; margin-bottom: 16px; }}
    .meta {{ display: flex; justify-content: space-between; margin-bottom: 16px; color: #555; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 16px; font-size: 12px; }}
    th {{ background: #1a3a5c; color: white; padding: 7px 10px; text-align: left; font-weight: 600; }}
    td {{ border-bottom: 1px solid #ddd; padding: 5px 10px; }}
    tr:nth-child(even) td {{ background: #f7f9fb; }}
    .r {{ text-align: right; }}
    .summary {{ background: #e8f0f8; padding: 14px 18px; border-radius: 6px; margin-top: 20px; }}
    .summary table {{ margin: 0; }}
    .summary td {{ border: none; padding: 4px 10px; }}
    .total-row td {{ font-weight: 700; font-size: 15px; border-top: 2px solid #1a3a5c; padding-top: 8px; }}
    .footer {{ text-align: center; margin-top: 30px; font-size: 11px; color: #888; }}
    @media print {{ body {{ padding: 0; }} .no-print {{ display: none; }} }}
</style>
</head>
<body>
{empresa_html}

<h1>Orçamento</h1>

<div class="meta">
    <span><strong>Data:</strong> {d['data']}</span>
    <span><strong>Peças:</strong> {d['total_pecas']} | <strong>Chapas:</strong> {d['total_chapas_count']}</span>
</div>

{cliente_html}

<h3>Peças ({d['total_pecas']} peças — {_num(d['total_area_pecas_m2'], 2)} m² total)</h3>
<table>
    <tr><th>Cód.</th><th>Peça</th><th>Material</th><th class="r">Dimensões (mm)</th><th class="r">Qtd</th><th class="r">Área m²</th></tr>
    {pecas_rows}
</table>

<h3>Chapas de Material</h3>
<table>
    <tr><th>Material</th><th class="r">Esp.(mm)</th><th>Tipo</th><th class="r">Qtd</th><th class="r">Custo/un</th><th class="r">Total</th></tr>
    {chapas_rows}
    <tr class="total-row"><td colspan="5">Subtotal Chapas</td><td class="r">{_money(d['total_chapas_cost'])}</td></tr>
</table>

<h3>Fita de Borda</h3>
<table>
    <tr><th>Descrição</th><th class="r">Valor</th></tr>
    <tr><td>Metragem linear</td><td class="r">{_num(d['fita']['linear_m'], 1)} m</td></tr>
    <tr><td>Com desperdício ({_num(d['fita']['waste_factor_pct'], 0)}%)</td><td class="r">{_num(d['fita']['linear_with_waste_m'], 1)} m</td></tr>
    <tr><td>Preço/metro</td><td class="r">{_money(d['fita']['price_per_m'])}</td></tr>
    <tr class="total-row"><td>Subtotal Fita</td><td class="r">{_money(d['fita']['total_cost'])}</td></tr>
</table>

{'<h3>Ferragens</h3><table><tr><th>Ferragem</th><th>Un.</th><th class="r">Qtd</th><th class="r">Custo/un</th><th class="r">Total</th></tr>' + ferragens_rows + '<tr class="total-row"><td colspan="4">Subtotal Ferragens</td><td class="r">' + _money(d["total_ferragens_cost"]) + '</td></tr></table>' if ferragens_rows else ''}

{'<h3>Custos Adicionais</h3><table><tr><th>Descrição</th><th class="r">Valor</th></tr>' + extras_rows + '</table>' if extras_rows else ''}

<div class="summary">
    <h3 style="margin-top:0">Resumo</h3>
    <table>
        <tr><td>Aproveitamento do material</td><td class="r"><strong>{_num(d['desperdicio']['utilization_pct'], 1)}%</strong></td></tr>
        <tr><td>Desperdício estimado</td><td class="r">{_num(d['desperdicio']['waste_area_m2'], 2)} m²</td></tr>
        <tr><td>Subtotal Material</td><td class="r">{_money(subtotal)}</td></tr>
        {'<tr><td>Custos adicionais</td><td class="r">' + _money(extras_total) + '</td></tr>' if extras_total > 0 else ''}
        {'<tr><td>Margem (' + _num(margin_pct, 1) + '%)</td><td class="r">' + _money(margin_value) + '</td></tr>' if margin_pct > 0 else ''}
        <tr class="total-row"><td>TOTAL</td><td class="r">{_money(total_final)}</td></tr>
    </table>
</div>

<div class="footer">
    Gerado por PanelNest — {d['data']}
</div>
</body>
</html>"""
    return html


def _esc(text):
    """Escape HTML."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
