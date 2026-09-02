import re

WORKBENCH_ID = "PanelNest"
WORKBENCH_MENU_TEXT = "PanelNest"
WORKBENCH_TOOLTIP = "Planejamento de corte, etiquetagem e nesting de chapas para marcenaria"
PARTS_SPREADSHEET_NAME = "PanelNestParts"
ORGANIZATION_SPREADSHEET_NAME = "PanelNestOrganizacao"
EDGE_BAND_SPREADSHEET_NAME = "PanelNestAcabamento"
LABELS_SPREADSHEET_NAME = "PanelNestEtiquetas"
VALIDATION_SPREADSHEET_NAME = "PanelNestValidacao"
SHEET_SUMMARY_SPREADSHEET_NAME = "PanelNestResumoChapas"
LAYOUT_WARNINGS_SPREADSHEET_NAME = "PanelNestAlertasLayout"
CUT_PLAN_SPREADSHEET_NAME = "PanelNestPlanoCorte"
GENERATED_REMNANTS_SPREADSHEET_NAME = "PanelNestRetalhosGerados"
COST_REPORT_SPREADSHEET_NAME = "PanelNestCustos"
EDGE_BAND_CONSUMPTION_SPREADSHEET_NAME = "PanelNestFita"
HARDWARE_SPREADSHEET_NAME = "PanelNestFerragens"
PANELNEST_EXPORT_TABLE_SPECS = (
    (PARTS_SPREADSHEET_NAME, "pecas_panelnest"),
    (ORGANIZATION_SPREADSHEET_NAME, "organizacao_panelnest"),
    (EDGE_BAND_SPREADSHEET_NAME, "acabamento_panelnest"),
    (LABELS_SPREADSHEET_NAME, "etiquetas_panelnest"),
    (VALIDATION_SPREADSHEET_NAME, "validacao_panelnest"),
    (SHEET_SUMMARY_SPREADSHEET_NAME, "resumo_chapas_panelnest"),
    (CUT_PLAN_SPREADSHEET_NAME, "plano_corte_panelnest"),
    (LAYOUT_WARNINGS_SPREADSHEET_NAME, "alertas_layout_panelnest"),
    (GENERATED_REMNANTS_SPREADSHEET_NAME, "retalhos_gerados_panelnest"),
    (COST_REPORT_SPREADSHEET_NAME, "custos_panelnest"),
    (EDGE_BAND_CONSUMPTION_SPREADSHEET_NAME, "fita_panelnest"),
    (HARDWARE_SPREADSHEET_NAME, "ferragens_panelnest"),
)
CONFIG_OBJECT_NAME = "PanelNestConfig"
LAYOUT_ROOT_NAME = "PanelNestLayout"
PART_PROPERTY_GROUP = "PanelNest"
INTERNAL_PROPERTY_NAME = "PanelNestManagedType"
GENERATED_REMNANT_LABEL_PREFIX = "[Gerado]"
PART_LABEL_PATTERN = re.compile(r"^PN-\d{3}\s*-\s*")
CLOUDFLARE_PAGES_PROJECT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
DEFAULT_SHEET_LENGTH_MM = 2750.0
DEFAULT_SHEET_WIDTH_MM = 1850.0
DEFAULT_SHEET_MARGIN_MM = 10.0
DEFAULT_SHEET_SPACING_MM = 5.0
DEFAULT_FULL_SHEET_COUNT = 1
DEFAULT_THICKNESS_MATCH_TOLERANCE_MM = 0.75
DIMENSION_EQUALITY_TOLERANCE_MM = 0.01
DEFAULT_CNC_KERF_MM = 3.0
DEFAULT_CNC_TECH_MARGIN_MM = 0.0
DEFAULT_SAW_KERF_MM = 4.0
DEFAULT_SAW_TECH_MARGIN_MM = 0.0
DEFAULT_CNC_LAYOUT_STRATEGY = "Auto"
DEFAULT_SAW_LAYOUT_STRATEGY = "Auto"
DEFAULT_CNC_NESTING_MODE = "Otimizado por formas"
CNC_ORIGIN_CORNERS = (
    "inferior_esquerdo",
    "inferior_direito",
    "superior_esquerdo",
    "superior_direito",
)
CNC_ORIGIN_CORNER_LABELS = {
    "inferior_esquerdo": "Inferior esquerdo",
    "inferior_direito": "Inferior direito",
    "superior_esquerdo": "Superior esquerdo",
    "superior_direito": "Superior direito",
}
DEFAULT_CNC_ORIGIN_CORNER = "inferior_esquerdo"
LAYOUT_SHEET_GAP_MM = 60.0
LAYOUT_GROUP_GAP_MM = 400.0
GENERATED_REMNANT_MIN_LENGTH_MM = 180.0
GENERATED_REMNANT_MIN_WIDTH_MM = 120.0
GENERATED_REMNANT_MIN_AREA_MM2 = 120000.0
CUT_METHOD_OPTIONS = ["Auto", "CNC", "Seccionadora"]
CUT_METHOD_ALIASES = {
    "Auto": "Auto",
    "CNC": "CNC",
    "Table Saw": "Seccionadora",
    "Circular Saw": "Seccionadora",
    "Serra de mesa": "Seccionadora",
    "Serra circular": "Seccionadora",
    "Seccionadora": "Seccionadora",
}
CUT_METHOD_SORT_ORDER = {
    "Auto": 0,
    "CNC": 1,
    "Seccionadora": 2,
}
GRAIN_DIRECTION_OPTIONS = ["Livre", "Comprimento da chapa", "Largura da chapa"]
GRAIN_DIRECTION_ALIASES = {
    "Livre": "Livre",
    "Free": "Livre",
    "Comprimento da chapa": "Comprimento da chapa",
    "Along sheet length": "Comprimento da chapa",
    "Largura da chapa": "Largura da chapa",
    "Along sheet width": "Largura da chapa",
}
LAYOUT_FAMILY_MAXRECTS = "maxrects"
LAYOUT_FAMILY_GUILLOTINE = "guillotine"
LAYOUT_FAMILY_STRIP = "strip"
CNC_LAYOUT_STRATEGY_OPTIONS = [
    "Auto",
    "MaxRects - BSSF",
    "MaxRects - BAF",
    "MaxRects - BLSF",
]
CNC_LAYOUT_STRATEGY_ALIASES = {
    "Auto": "Auto",
    "MaxRects - BSSF": "MaxRects - BSSF",
    "MaxRects - BAF": "MaxRects - BAF",
    "MaxRects - BLSF": "MaxRects - BLSF",
    "BSSF": "MaxRects - BSSF",
    "BAF": "MaxRects - BAF",
    "BLSF": "MaxRects - BLSF",
}
CNC_NESTING_MODE_OPTIONS = [
    "Rápido (retangular)",
    "Otimizado por formas",
]
CNC_NESTING_MODE_ALIASES = {
    "Rápido (retangular)": "Rápido (retangular)",
    "Rapido (retangular)": "Rápido (retangular)",
    "Rápido": "Rápido (retangular)",
    "Rapido": "Rápido (retangular)",
    "Retangular": "Rápido (retangular)",
    "Otimizado por formas": "Otimizado por formas",
    "Formas": "Otimizado por formas",
    "Shape": "Otimizado por formas",
}
SAW_LAYOUT_STRATEGY_OPTIONS = [
    "Auto",
    "Guilhotina - BSSF SAS",
    "Guilhotina - BSSF LAS",
    "Faixas classicas",
]
SAW_LAYOUT_STRATEGY_ALIASES = {
    "Auto": "Auto",
    "Guilhotina - BSSF SAS": "Guilhotina - BSSF SAS",
    "Guilhotina - BSSF LAS": "Guilhotina - BSSF LAS",
    "Guillotine - BSSF SAS": "Guilhotina - BSSF SAS",
    "Guillotine - BSSF LAS": "Guilhotina - BSSF LAS",
    "Faixas classicas": "Faixas classicas",
    "Faixas": "Faixas classicas",
}
DEFAULT_APPLY_DIALOG_PREFS = {
    "apply_material": True,
    "apply_cut_method": True,
    "apply_rotation": False,
    "apply_grain": False,
    "apply_edge_band": False,
    "material": "",
    "cut_method": "CNC",
    "allow_rotation": True,
    "grain_direction": "Livre",
    "edge_band_top": False,
    "edge_band_bottom": False,
    "edge_band_left": False,
    "edge_band_right": False,
}
EDGE_BAND_SIDE_LABELS = {
    "top": "Superior",
    "bottom": "Inferior",
    "left": "Esquerda",
    "right": "Direita",
}
EDGE_BAND_SIDE_KEYS = ("top", "bottom", "left", "right")
EDGE_BAND_SIDE_ABBREVIATIONS = {
    "top": "SUP",
    "bottom": "INF",
    "left": "ESQ",
    "right": "DIR",
}
EDGE_BAND_HIGHLIGHT_COLOR = (0.23, 0.79, 0.84)
ASSEMBLY_GUIDE_CONTEXT_COLOR = (0.83, 0.84, 0.86)
ASSEMBLY_GUIDE_TARGET_COLOR = (0.92, 0.42, 0.25)
ASSEMBLY_GUIDE_CONTEXT_LINE_COLOR = (0.22, 0.24, 0.27)
ASSEMBLY_GUIDE_TARGET_LINE_COLOR = (0.63, 0.27, 0.16)
ASSEMBLY_GUIDE_CONTEXT_LINE_WIDTH = 2.1
ASSEMBLY_GUIDE_TARGET_LINE_WIDTH = 2.8
ASSEMBLY_GUIDE_CONTEXT_TRANSPARENCY = 82
ASSEMBLY_GUIDE_TARGET_TRANSPARENCY = 0
ASSEMBLY_GUIDE_MANUAL_TARGET_TRANSPARENCY = 46
ASSEMBLY_GUIDE_IMAGE_WIDTH_PX = 840
ASSEMBLY_GUIDE_IMAGE_HEIGHT_PX = 520
PART_VISUAL_PALETTE = (
    (0.87, 0.56, 0.47),
    (0.89, 0.60, 0.50),
    (0.91, 0.63, 0.53),
    (0.93, 0.66, 0.56),
    (0.95, 0.69, 0.59),
    (0.96, 0.71, 0.63),
    (0.95, 0.70, 0.67),
    (0.94, 0.68, 0.71),
    (0.92, 0.66, 0.74),
    (0.89, 0.63, 0.76),
    (0.86, 0.60, 0.77),
    (0.82, 0.57, 0.77),
)
SHEET_SOURCE_COLORS = {
    "Retalho": (0.84, 0.79, 0.68),
    "Chapa inteira": (0.77, 0.82, 0.86),
    "Chapa extra": (0.88, 0.76, 0.72),
}
VALIDATION_SEVERITY_ORDER = {
    "Erro": 0,
    "Alerta": 1,
    "Info": 2,
    "OK": 3,
}
# Constants used in layout_model (defined mid-file in original panelnest.py ~line 10067)
CUT_MARKER_DIGIT_WIDTH_MM = 14.0
CUT_MARKER_DIGIT_HEIGHT_MM = 22.0
CUT_MARKER_DIGIT_GAP_MM = 3.0
CUT_MARKER_STROKE_MM = 2.4
CUT_MARKER_BADGE_PADDING_X_MM = 6.0
CUT_MARKER_BADGE_PADDING_Y_MM = 5.0
CUT_MARKER_SIDE_GAP_MM = 18.0
CUT_MARKER_LANE_GAP_MM = 8.0
CUT_MARKER_PLATE_DEPTH_MM = 0.6
CUT_MARKER_TEXT_DEPTH_MM = 0.9
CUT_MARKER_SCREEN_FONT_SIZE_MM = 22.0
CUT_MARKER_SCREEN_LAYOUT_WIDTH_FACTOR = 1.6
CUT_MARKER_SCREEN_LAYOUT_HEIGHT_FACTOR = 1.35
CUT_MARKER_ELBOW_GAP_MM = 10.0
PART_LABEL_FONT_SIZE_MM = 24.0
PART_LABEL_FALLBACK_FONT_SIZE_MM = 18.0
PART_LABEL_COMPACT_FONT_SIZE_MM = 14.0
PART_LABEL_MINI_FONT_SIZE_MM = 11.5
PART_LABEL_TINY_FONT_SIZE_MM = 9.0
PART_LABEL_MICRO_FONT_SIZE_MM = 7.2
PART_DIMENSION_FONT_SIZE_MM = 22.0
PART_DIMENSION_FALLBACK_FONT_SIZE_MM = 16.5
PART_DIMENSION_COMPACT_FONT_SIZE_MM = 13.0
PART_DIMENSION_MINI_FONT_SIZE_MM = 10.0
PART_DIMENSION_TINY_FONT_SIZE_MM = 7.8
PART_ANNOTATION_SCREEN_CHAR_WIDTH_FACTOR = 0.72
PART_ANNOTATION_SCREEN_LINE_HEIGHT_FACTOR = 1.30
PART_ANNOTATION_PADDING_X_MM = 18.0
PART_ANNOTATION_PADDING_Y_MM = 14.0
PART_DIMENSION_EDGE_GAP_MM = 12.0
PART_LABEL_MIN_LENGTH_MM = 180.0
PART_LABEL_MIN_WIDTH_MM = 90.0
PART_LABEL_MIN_AREA_MM2 = 42000.0
PART_LABEL_CODE_ONLY_MIN_LENGTH_MM = 60.0
PART_LABEL_CODE_ONLY_MIN_WIDTH_MM = 24.0
PART_LABEL_CODE_ONLY_MIN_AREA_MM2 = 2600.0
PART_DIMENSION_MIN_LENGTH_MM = 52.0
PART_DIMENSION_MIN_WIDTH_MM = 18.0
PART_DIMENSION_MIN_AREA_MM2 = 2200.0
LAYOUT_EDGE_BAND_MARKER_PREFERRED_WIDTH_MM = 10.0
LAYOUT_EDGE_BAND_MARKER_MIN_WIDTH_MM = 3.0
LAYOUT_EDGE_BAND_MARKER_MAX_WIDTH_MM = 14.0
LAYOUT_EDGE_BAND_MARKER_HEIGHT_MM = 0.8
