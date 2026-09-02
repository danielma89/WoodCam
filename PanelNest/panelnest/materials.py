"""Banco de materiais visuais do PanelNest.

Cada material tem:
  id        — identificador único
  name      — nome exibido ao usuário
  color     — tupla RGB float 0-1 (cor base do material)
  category  — grupo para organização no seletor
"""

from dataclasses import dataclass


@dataclass
class Material:
    id: str
    name: str
    color: tuple        # (R, G, B) floats 0.0–1.0 — cor da face principal
    category: str
    cut_edge_color: tuple = None  # cor da borda cortada (miolo exposto); None = calcular automaticamente
    texture_id: str = None  # ID da textura no ambientcg.com (ex: "Wood049"); None = sem textura

    def get_cut_edge_color(self) -> tuple:
        """Cor do miolo exposto quando o painel é cortado (sem fita nesse lado)."""
        if self.cut_edge_color is not None:
            return self.cut_edge_color
        # Cálculo automático: desaturar e clarear levemente para simular corte cru
        r, g, b = self.color
        # Mistura 50% com o beige de MDF cru
        raw = (0.80, 0.71, 0.55)
        return (
            r * 0.55 + raw[0] * 0.45,
            g * 0.55 + raw[1] * 0.45,
            b * 0.55 + raw[2] * 0.45,
        )


# ---------------------------------------------------------------------------
# Catálogo de materiais
# ---------------------------------------------------------------------------

# Cor do miolo exposto do MDF/MDP (beige cru, igual para todas as cores de face)
_MDF_CORE = (0.80, 0.71, 0.55)
_MDP_CORE = (0.76, 0.67, 0.50)

MATERIALS = [
    # --- MDF liso --- (miolo sempre bege cru independente da cor da face)
    Material("mdf_branco",      "MDF Branco",           (0.95, 0.95, 0.93), "MDF Liso", _MDF_CORE),
    Material("mdf_off_white",   "MDF Off White / Fendi",(0.90, 0.87, 0.80), "MDF Liso", _MDF_CORE),
    Material("mdf_cru",         "MDF Cru",              (0.82, 0.73, 0.58), "MDF Liso", _MDF_CORE),
    Material("mdf_cinza_claro", "MDF Cinza Claro",      (0.75, 0.75, 0.75), "MDF Liso", _MDF_CORE),
    Material("mdf_cinza_medio", "MDF Cinza Médio",      (0.55, 0.55, 0.55), "MDF Liso", _MDF_CORE),
    Material("mdf_cimento",     "MDF Cimento",          (0.50, 0.50, 0.48), "MDF Liso", _MDF_CORE),
    Material("mdf_preto",       "MDF Preto",            (0.10, 0.10, 0.10), "MDF Liso", _MDF_CORE),
    Material("mdf_azul_navy",   "MDF Azul Navy",        (0.10, 0.17, 0.30), "MDF Liso", _MDF_CORE),
    Material("mdf_verde_musgo", "MDF Verde Musgo",      (0.28, 0.38, 0.25), "MDF Liso", _MDF_CORE),
    Material("mdf_terracota",   "MDF Terracota",        (0.62, 0.30, 0.20), "MDF Liso", _MDF_CORE),

    # --- Amadeirado --- texturas de veio reais (ambientcg.com CC0)
    Material("carvalho_natural","Carvalho Natural",      (0.62, 0.42, 0.22), "Amadeirado", None, "Wood049"),
    Material("carvalho_escuro", "Carvalho Escuro",       (0.38, 0.24, 0.12), "Amadeirado", None, "Wood027"),
    Material("freijo",          "Freijó",                (0.65, 0.45, 0.25), "Amadeirado", None, "Wood092"),
    Material("nogueira",        "Nogueira",              (0.42, 0.28, 0.16), "Amadeirado", None, "Wood051"),
    Material("pinho",           "Pinho / Pinus",         (0.88, 0.76, 0.52), "Amadeirado", None, "Wood052"),
    Material("amendoa",         "Amêndoa",               (0.72, 0.54, 0.34), "Amadeirado", None, "Wood026"),
    Material("teca",            "Teca",                  (0.55, 0.38, 0.18), "Amadeirado", None, "Wood069"),
    Material("wengue",          "Wengué",                (0.20, 0.14, 0.08), "Amadeirado", None, "Wood067"),

    # --- MDP / Aglomerado --- (sem textura, miolo bege)
    Material("mdp_branco",      "MDP Branco",            (0.94, 0.94, 0.92), "MDP", _MDP_CORE),
    Material("mdp_cru",         "MDP Cru",               (0.78, 0.70, 0.55), "MDP", _MDP_CORE),
    Material("mdp_preto",       "MDP Preto",             (0.12, 0.12, 0.12), "MDP", _MDP_CORE),

    # --- Compensado / Madeira maciça ---
    Material("compensado",      "Compensado",            (0.80, 0.65, 0.42), "Madeira", None, "Wood090B"),
    Material("madeira_clara",   "Madeira Clara",         (0.85, 0.70, 0.48), "Madeira", None, "Wood095"),
    Material("madeira_escura",  "Madeira Escura",        (0.30, 0.18, 0.09), "Madeira", None, "Wood013"),
]

CATEGORIES = list(dict.fromkeys(m.category for m in MATERIALS))


def get_by_id(material_id: str):
    for m in MATERIALS:
        if m.id == material_id:
            return m
    return None


def get_by_name(name: str):
    name_lower = name.lower()
    for m in MATERIALS:
        if m.name.lower() == name_lower:
            return m
    return None
