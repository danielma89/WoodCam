from dataclasses import dataclass, field

from .constants import (
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
    LAYOUT_FAMILY_MAXRECTS,
)


@dataclass
class HardwareItem:
    """Item do catálogo de ferragens do projeto."""
    hw_id: str          # identificador único, ex: "DOB35"
    name: str           # nome legível, ex: "Dobradiça 35mm"
    unit: str = "un"    # unidade: un, par, m, kg...
    cost: float = 0.0   # custo unitário em R$
    hole_diameter_mm: float = 0.0  # Ø do furo para detecção automática; 0 = sem detecção


DEFAULT_HARDWARE_CATALOG = [
    HardwareItem("DOB35",  "Dobradiça 35mm",       "un",  0.0, hole_diameter_mm=35.0),
    HardwareItem("DOB26",  "Mini dobradiça 26mm",  "un",  0.0, hole_diameter_mm=26.0),
    HardwareItem("MFX15",  "Minifix (corpo)",      "un",  0.0, hole_diameter_mm=15.0),
    HardwareItem("CAV8",   "Cavilha 8mm",          "un",  0.0, hole_diameter_mm=8.0),
    HardwareItem("CAV6",   "Cavilha 6mm",          "un",  0.0, hole_diameter_mm=6.0),
    HardwareItem("S32_5",  "Prateleira sistema 32","un",  0.0, hole_diameter_mm=5.0),
]


@dataclass
class PanelPart:
    part_id: str
    object_name: str
    label: str
    length_mm: float
    width_mm: float
    thickness_mm: float
    quantity: int = 1
    material: str = ""
    cut_method: str = "Auto"
    allow_rotation: bool = True
    grain_direction: str = "Livre"
    grain_rotated: bool = False  # True = veio girado 90° em relação ao padrão do material
    grain_match_group: str = ""  # Peças com mesmo grupo devem ficar adjacentes na chapa (continuidade do veio)
    edge_band_top: bool = False
    edge_band_bottom: bool = False
    edge_band_left: bool = False
    edge_band_right: bool = False
    source_type: str = ""
    holes: list = field(default_factory=list)  # lista de dicts: {x_mm, y_mm, diameter_mm, depth_mm, face}
    hardware: list = field(default_factory=list)  # lista de dicts: {hw_id, qty}
    profile_points: list = field(default_factory=list)  # contorno 2D real: [(x,y),...] em mm, origem (0,0); vazio = retangular


@dataclass
class SheetSettings:
    length_mm: float = DEFAULT_SHEET_LENGTH_MM
    width_mm: float = DEFAULT_SHEET_WIDTH_MM
    margin_mm: float = DEFAULT_SHEET_MARGIN_MM
    spacing_mm: float = DEFAULT_SHEET_SPACING_MM
    full_sheet_count: int = DEFAULT_FULL_SHEET_COUNT
    allow_extra_full_sheets: bool = True
    full_sheet_thickness_mm: float = 0.0
    full_sheet_material: str = ""
    cnc_kerf_mm: float = DEFAULT_CNC_KERF_MM
    cnc_tech_margin_mm: float = DEFAULT_CNC_TECH_MARGIN_MM
    saw_kerf_mm: float = DEFAULT_SAW_KERF_MM
    saw_tech_margin_mm: float = DEFAULT_SAW_TECH_MARGIN_MM
    cnc_layout_strategy: str = DEFAULT_CNC_LAYOUT_STRATEGY
    saw_layout_strategy: str = DEFAULT_SAW_LAYOUT_STRATEGY
    cnc_nesting_mode: str = DEFAULT_CNC_NESTING_MODE
    show_layout_part_dimensions: bool = True
    show_layout_part_labels: bool = True
    show_layout_edge_bands: bool = True
    assembly_guide_public_base_url: str = ""
    assembly_guide_cloudflare_project_name: str = ""
    assembly_guide_cloudflare_auto_publish: bool = False
    assembly_guide_preserve_current_visual: bool = False
    remnants: list = field(default_factory=list)
    hardware_catalog: list = field(default_factory=list)  # lista de HardwareItem
    edge_band_thickness_mm: float = 0.0
    edge_band_label: str = ""
    edge_band_price_per_m: float = 0.0
    edge_band_waste_factor: float = 0.10
    full_sheet_cost: float = 0.0
    company_name: str = ""
    company_contact: str = ""
    company_address: str = ""
    project_client: str = ""
    project_responsible: str = ""
    project_notes: str = ""
    cnc_common_line: bool = False  # Common-Line: peças compartilham linha de corte (sem kerf entre elas)
    cnc_stay_down: bool = False  # Stay-Down: router não levanta entre passes adjacentes
    cnc_origin_corner: str = DEFAULT_CNC_ORIGIN_CORNER  # Canto onde o CNC zera (inferior_esquerdo, inferior_direito, superior_esquerdo, superior_direito)


@dataclass
class CutProcessProfile:
    name: str
    kerf_mm: float = 0.0
    technical_margin_mm: float = 0.0
    layout_family: str = LAYOUT_FAMILY_MAXRECTS
    strategy_name: str = ""
    fitness_name: str = ""
    split_name: str = ""
    strip_layout: bool = False


@dataclass
class SheetStockPiece:
    label: str
    length_mm: float
    width_mm: float
    thickness_mm: float = 0.0
    material: str = ""
    quantity: int = 1
    kind: str = "Retalho"
    is_full_sheet: bool = False
    cost_per_sheet: float = 0.0
    db_id: object = None  # ID no banco SQLite (int ou None)


@dataclass
class LayoutPlacement:
    part: PanelPart
    x_mm: float
    y_mm: float
    placed_length_mm: float
    placed_width_mm: float
    rotated: bool = False
    rotation_deg: object = None


@dataclass
class LayoutCutStep:
    step_index: int
    orientation: str
    cut_kind: str
    start_x_mm: float
    start_y_mm: float
    end_x_mm: float
    end_y_mm: float
    position_mm: float
    span_mm: float
    target_part_id: str = ""
    description: str = ""
    sequence_zone: str = ""


@dataclass
class LayoutSheet:
    group_id: str
    sheet_index: int
    material: str
    thickness_mm: float
    cut_method: str
    layout_strategy: str
    source_label: str
    source_kind: str
    source_length_mm: float
    source_width_mm: float
    source_thickness_mm: float
    placements: list = field(default_factory=list)
    cut_steps: list = field(default_factory=list)


@dataclass
class GeneratedRemnant:
    group_id: str
    sheet_index: int
    source_label: str
    source_kind: str
    label: str
    x_mm: float
    y_mm: float
    length_mm: float
    width_mm: float
    area_mm2: float
    material: str
    thickness_mm: float
    cut_method: str
    layout_strategy: str
