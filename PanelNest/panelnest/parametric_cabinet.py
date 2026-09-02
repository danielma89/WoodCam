"""Biblioteca paramétrica de gabinetes para marcenaria.

Gera gabinetes (armários, estantes, gaveteiros, etc.) como sólidos FreeCAD
com dimensões ajustáveis pelo usuário.  Cada peça já nasce com as propriedades
PanelNest (label, material, espessura, fita de borda) preenchidas.

Modelos disponíveis:
    - Gabinete Base (cozinha/banheiro)
    - Gabinete Aéreo (parede)
    - Estante Aberta
    - Gaveteiro
    - Armário Alto (despensa/roupeiro)
    - Nicho simples

Cada modelo é uma função que recebe dimensões e retorna
uma lista de dicts descrevendo as peças.
"""

from dataclasses import dataclass, field


@dataclass
class CabinetPart:
    """Descrição de uma peça do gabinete paramétrico."""
    name: str           # Ex: "Lateral esquerda"
    role: str           # "side", "top", "bottom", "back", "shelf", "door", "drawer_front", ...
    length_mm: float    # Comprimento (maior dimensão do painel)
    width_mm: float     # Largura (segunda dimensão)
    thickness_mm: float
    x_mm: float = 0.0   # Posição X no gabinete (para visualização)
    y_mm: float = 0.0
    z_mm: float = 0.0
    rotation: str = ""   # "", "x90", "y90", "z90"
    edge_top: bool = False
    edge_bottom: bool = False
    edge_left: bool = False
    edge_right: bool = False
    material: str = ""
    grain_direction: str = "Comprimento da chapa"
    quantity: int = 1
    mirror_name: str = ""  # Nome da peça espelhada (se houver)


@dataclass
class CabinetTemplate:
    """Template de um tipo de gabinete paramétrico."""
    template_id: str
    name: str
    category: str       # "Base", "Aereo", "Alto", "Estante", "Gaveta", "Nicho"
    description: str = ""
    # Dimensões padrão (mm)
    default_width: float = 600.0
    default_height: float = 720.0
    default_depth: float = 560.0
    default_thickness: float = 18.0
    # Limites
    min_width: float = 200.0
    max_width: float = 2400.0
    min_height: float = 150.0
    max_height: float = 2700.0
    min_depth: float = 150.0
    max_depth: float = 800.0
    # Opções
    has_back: bool = True
    back_thickness: float = 3.0
    has_doors: bool = True
    door_count: int = 1
    has_shelves: bool = True
    shelf_count: int = 1
    has_drawers: bool = False
    drawer_count: int = 0
    has_base_board: bool = True  # Rodapé recuado
    base_board_height: float = 100.0
    base_board_recess: float = 50.0


# ---------------------------------------------------------------------------
# Templates pré-definidos
# ---------------------------------------------------------------------------

CABINET_TEMPLATES = [
    CabinetTemplate(
        template_id="BASE_1DOOR",
        name="Gabinete Base - 1 porta",
        category="Base",
        description="Gabinete inferior de cozinha com 1 porta e 1 prateleira",
        default_width=600.0,
        default_height=720.0,
        default_depth=560.0,
        has_doors=True,
        door_count=1,
        shelf_count=1,
    ),
    CabinetTemplate(
        template_id="BASE_2DOOR",
        name="Gabinete Base - 2 portas",
        category="Base",
        description="Gabinete inferior de cozinha com 2 portas e 1 prateleira",
        default_width=800.0,
        default_height=720.0,
        default_depth=560.0,
        has_doors=True,
        door_count=2,
        shelf_count=1,
    ),
    CabinetTemplate(
        template_id="WALL_1DOOR",
        name="Gabinete Aereo - 1 porta",
        category="Aereo",
        description="Armario aereo de cozinha com 1 porta e 2 prateleiras",
        default_width=600.0,
        default_height=700.0,
        default_depth=350.0,
        has_doors=True,
        door_count=1,
        shelf_count=2,
        has_base_board=False,
    ),
    CabinetTemplate(
        template_id="WALL_2DOOR",
        name="Gabinete Aereo - 2 portas",
        category="Aereo",
        description="Armario aereo de cozinha com 2 portas e 2 prateleiras",
        default_width=800.0,
        default_height=700.0,
        default_depth=350.0,
        has_doors=True,
        door_count=2,
        shelf_count=2,
        has_base_board=False,
    ),
    CabinetTemplate(
        template_id="TALL_PANTRY",
        name="Armario Alto / Despensa",
        category="Alto",
        description="Armario alto com 2 portas e 4 prateleiras",
        default_width=600.0,
        default_height=2100.0,
        default_depth=560.0,
        has_doors=True,
        door_count=2,
        shelf_count=4,
        has_base_board=True,
    ),
    CabinetTemplate(
        template_id="BOOKSHELF",
        name="Estante Aberta",
        category="Estante",
        description="Estante sem portas com 4 prateleiras",
        default_width=800.0,
        default_height=1800.0,
        default_depth=300.0,
        has_doors=False,
        door_count=0,
        shelf_count=4,
        has_base_board=True,
        base_board_height=80.0,
    ),
    CabinetTemplate(
        template_id="DRAWER_3",
        name="Gaveteiro 3 gavetas",
        category="Gaveta",
        description="Gaveteiro com 3 gavetas iguais",
        default_width=600.0,
        default_height=720.0,
        default_depth=560.0,
        has_doors=False,
        has_drawers=True,
        drawer_count=3,
        shelf_count=0,
        has_base_board=True,
    ),
    CabinetTemplate(
        template_id="DRAWER_4",
        name="Gaveteiro 4 gavetas",
        category="Gaveta",
        description="Gaveteiro com 4 gavetas iguais",
        default_width=500.0,
        default_height=720.0,
        default_depth=500.0,
        has_doors=False,
        has_drawers=True,
        drawer_count=4,
        shelf_count=0,
        has_base_board=True,
    ),
    CabinetTemplate(
        template_id="NICHE",
        name="Nicho Simples",
        category="Nicho",
        description="Nicho retangular aberto (sem porta, sem fundo)",
        default_width=400.0,
        default_height=300.0,
        default_depth=200.0,
        has_doors=False,
        has_back=False,
        shelf_count=0,
        has_base_board=False,
    ),
    CabinetTemplate(
        template_id="WARDROBE_2DOOR",
        name="Guarda-roupa 2 portas",
        category="Alto",
        description="Guarda-roupa com 2 portas, maleiro e cabideiro",
        default_width=1000.0,
        default_height=2100.0,
        default_depth=560.0,
        has_doors=True,
        door_count=2,
        shelf_count=1,
        has_base_board=True,
    ),
]


def get_template_by_id(template_id):
    for t in CABINET_TEMPLATES:
        if t.template_id == template_id:
            return t
    return None


def list_template_categories():
    cats = []
    seen = set()
    for t in CABINET_TEMPLATES:
        if t.category not in seen:
            cats.append(t.category)
            seen.add(t.category)
    return cats


def templates_by_category(category):
    return [t for t in CABINET_TEMPLATES if t.category == category]


# ---------------------------------------------------------------------------
# Geração de peças
# ---------------------------------------------------------------------------

def generate_cabinet_parts(template, width_mm, height_mm, depth_mm,
                           thickness_mm=18.0, back_thickness_mm=3.0,
                           material="", shelf_count=None, door_count=None,
                           drawer_count=None):
    """Gera a lista de CabinetPart para um gabinete paramétrico.

    Retorna lista de CabinetPart com posições e dimensões calculadas.
    """
    t = thickness_mm
    bt = back_thickness_mm if template.has_back else 0.0

    # Dimensões internas
    inner_width = width_mm - 2 * t
    inner_depth = depth_mm - bt if template.has_back else depth_mm
    base_h = template.base_board_height if template.has_base_board else 0.0
    inner_height = height_mm - 2 * t - base_h

    shelves = shelf_count if shelf_count is not None else template.shelf_count
    doors = door_count if door_count is not None else template.door_count
    drawers = drawer_count if drawer_count is not None else template.drawer_count

    parts = []

    # --- Laterais ---
    side_height = height_mm - base_h
    side_depth = inner_depth

    parts.append(CabinetPart(
        name="Lateral esquerda",
        role="side",
        length_mm=side_height,
        width_mm=side_depth,
        thickness_mm=t,
        x_mm=0, y_mm=0, z_mm=base_h,
        rotation="y90",
        edge_top=True, edge_bottom=template.has_base_board,
        edge_left=True, edge_right=False,
        material=material,
        mirror_name="Lateral direita",
    ))

    parts.append(CabinetPart(
        name="Lateral direita",
        role="side",
        length_mm=side_height,
        width_mm=side_depth,
        thickness_mm=t,
        x_mm=width_mm - t, y_mm=0, z_mm=base_h,
        rotation="y90",
        edge_top=True, edge_bottom=template.has_base_board,
        edge_left=False, edge_right=True,
        material=material,
        mirror_name="Lateral esquerda",
    ))

    # --- Tampo e base ---
    parts.append(CabinetPart(
        name="Tampo superior",
        role="top",
        length_mm=inner_width,
        width_mm=side_depth,
        thickness_mm=t,
        x_mm=t, y_mm=0, z_mm=height_mm - t,
        edge_top=False, edge_bottom=False,
        edge_left=True, edge_right=False,
        material=material,
    ))

    parts.append(CabinetPart(
        name="Base inferior",
        role="bottom",
        length_mm=inner_width,
        width_mm=side_depth,
        thickness_mm=t,
        x_mm=t, y_mm=0, z_mm=base_h,
        edge_top=False, edge_bottom=False,
        edge_left=True if not template.has_base_board else False,
        edge_right=False,
        material=material,
    ))

    # --- Fundo ---
    if template.has_back and bt > 0:
        parts.append(CabinetPart(
            name="Fundo",
            role="back",
            length_mm=inner_width,
            width_mm=height_mm - 2 * t - base_h,
            thickness_mm=bt,
            x_mm=t, y_mm=side_depth, z_mm=base_h + t,
            rotation="x90",
            material=material,
        ))

    # --- Prateleiras ---
    if shelves > 0 and not template.has_drawers:
        shelf_spacing = inner_height / (shelves + 1)
        for i in range(shelves):
            z_pos = base_h + t + shelf_spacing * (i + 1)
            parts.append(CabinetPart(
                name=f"Prateleira {i + 1}",
                role="shelf",
                length_mm=inner_width,
                width_mm=side_depth - 2,  # 2mm de folga
                thickness_mm=t,
                x_mm=t, y_mm=0, z_mm=z_pos,
                edge_left=True,
                material=material,
            ))

    # --- Rodapé ---
    if template.has_base_board and base_h > 0:
        recess = template.base_board_recess
        parts.append(CabinetPart(
            name="Rodape frontal",
            role="base_board",
            length_mm=width_mm - 2 * recess,
            width_mm=base_h,
            thickness_mm=t,
            x_mm=recess, y_mm=0, z_mm=0,
            rotation="x90",
            material=material,
        ))

    # --- Portas ---
    if template.has_doors and doors > 0:
        door_gap = 3.0  # folga entre portas
        if doors == 1:
            door_width = width_mm - door_gap
            parts.append(CabinetPart(
                name="Porta",
                role="door",
                length_mm=height_mm - base_h - door_gap,
                width_mm=door_width,
                thickness_mm=t,
                x_mm=door_gap / 2, y_mm=-t, z_mm=base_h + door_gap / 2,
                edge_top=True, edge_bottom=True,
                edge_left=True, edge_right=True,
                material=material,
            ))
        else:
            single_door_width = (width_mm - door_gap * (doors + 1)) / doors
            for d in range(doors):
                x_pos = door_gap + d * (single_door_width + door_gap)
                parts.append(CabinetPart(
                    name=f"Porta {d + 1}",
                    role="door",
                    length_mm=height_mm - base_h - door_gap,
                    width_mm=single_door_width,
                    thickness_mm=t,
                    x_mm=x_pos, y_mm=-t, z_mm=base_h + door_gap / 2,
                    edge_top=True, edge_bottom=True,
                    edge_left=True, edge_right=True,
                    material=material,
                ))

    # --- Gavetas ---
    if template.has_drawers and drawers > 0:
        drawer_gap = 3.0
        drawer_front_height = (inner_height - drawer_gap * (drawers + 1)) / drawers
        drawer_box_depth = inner_depth - 50  # 50mm de recuo para corrediças
        drawer_box_height = drawer_front_height - 30  # 30mm menor que a frente
        drawer_box_width = inner_width - 26  # 13mm de cada lado para corrediça

        for d in range(drawers):
            z_pos = base_h + t + drawer_gap + d * (drawer_front_height + drawer_gap)

            # Frente da gaveta
            parts.append(CabinetPart(
                name=f"Frente gaveta {d + 1}",
                role="drawer_front",
                length_mm=width_mm - 2 * drawer_gap,
                width_mm=drawer_front_height,
                thickness_mm=t,
                x_mm=drawer_gap, y_mm=-t, z_mm=z_pos,
                edge_top=True, edge_bottom=True,
                edge_left=True, edge_right=True,
                material=material,
            ))

            # Laterais do caixote
            parts.append(CabinetPart(
                name=f"Lateral gaveta {d + 1}",
                role="drawer_side",
                length_mm=drawer_box_depth,
                width_mm=drawer_box_height,
                thickness_mm=t,
                x_mm=t + 13, y_mm=0, z_mm=z_pos + 15,
                quantity=2,
                material=material,
            ))

            # Traseira do caixote
            parts.append(CabinetPart(
                name=f"Traseira gaveta {d + 1}",
                role="drawer_back",
                length_mm=drawer_box_width - 2 * t,
                width_mm=drawer_box_height,
                thickness_mm=t,
                x_mm=t + 13 + t, y_mm=drawer_box_depth - t, z_mm=z_pos + 15,
                material=material,
            ))

            # Fundo do caixote (mais fino)
            parts.append(CabinetPart(
                name=f"Fundo gaveta {d + 1}",
                role="drawer_bottom",
                length_mm=drawer_box_width,
                width_mm=drawer_box_depth,
                thickness_mm=back_thickness_mm,
                x_mm=t + 13, y_mm=0, z_mm=z_pos + 15 - back_thickness_mm,
                material=material,
            ))

    return parts


# ---------------------------------------------------------------------------
# Criação de sólidos FreeCAD
# ---------------------------------------------------------------------------

def create_cabinet_in_freecad(template, parts, cabinet_name="Gabinete"):
    """Cria os sólidos 3D no FreeCAD a partir da lista de CabinetPart.

    Cria um App::Part container com Bodies para cada peça.
    Cada Body recebe as propriedades PanelNest automaticamente.

    Retorna o objeto App::Part criado.
    """
    try:
        import FreeCAD as App
    except ImportError:
        raise RuntimeError("FreeCAD nao esta disponivel.")

    doc = App.ActiveDocument
    if doc is None:
        doc = App.newDocument("PanelNest")

    container = doc.addObject("App::Part", cabinet_name.replace(" ", "_"))
    container.Label = cabinet_name

    part_index = 0
    for cab_part in parts:
        qty = cab_part.quantity
        for q in range(qty):
            part_index += 1
            suffix = f" ({q + 1})" if qty > 1 else ""
            body_label = f"{cab_part.name}{suffix}"
            body_name = f"PN_{cabinet_name.replace(' ', '_')}_{part_index:03d}"

            body = doc.addObject("PartDesign::Body", body_name)
            body.Label = body_label
            container.addObject(body)

            # Criar Pad (extrusão de um retângulo)
            _create_box_pad(doc, body, cab_part, q)

            # Aplicar propriedades PanelNest
            _apply_panelnest_properties(body, cab_part, part_index)

    doc.recompute()
    return container


def _create_box_pad(doc, body, cab_part, occurrence_index=0):
    """Cria um Sketch + Pad dentro do Body para representar a peça."""
    try:
        import FreeCAD as App
        import Part as PartModule
    except ImportError:
        return

    # Criar sketch no plano XY
    sketch = doc.addObject("Sketcher::SketchObject", "Sketch")
    body.addObject(sketch)

    # Retângulo: length_mm x width_mm
    l = cab_part.length_mm
    w = cab_part.width_mm

    # Adicionar linhas do retângulo
    try:
        from FreeCAD import Vector as V
        sketch.addGeometry(PartModule.LineSegment(V(0, 0, 0), V(l, 0, 0)))
        sketch.addGeometry(PartModule.LineSegment(V(l, 0, 0), V(l, w, 0)))
        sketch.addGeometry(PartModule.LineSegment(V(l, w, 0), V(0, w, 0)))
        sketch.addGeometry(PartModule.LineSegment(V(0, w, 0), V(0, 0, 0)))

        # Constraints para fechar o retângulo
        for i in range(4):
            sketch.addConstraint(
                _make_coincident_constraint(i, (i + 1) % 4)
            )
    except Exception:
        # Fallback: usar addGeometry simples sem constraints
        pass

    # Criar Pad
    pad = doc.addObject("PartDesign::Pad", "Pad")
    body.addObject(pad)
    pad.Profile = sketch
    pad.Length = cab_part.thickness_mm
    sketch.Visibility = False

    # Posicionar o body
    try:
        x = cab_part.x_mm
        y = cab_part.y_mm
        z = cab_part.z_mm
        if cab_part.quantity > 1 and occurrence_index > 0:
            # Segunda ocorrência: espelhar (ex: laterais de gaveta)
            if cab_part.role == "drawer_side":
                from .models import SheetSettings
                # Offset para o lado oposto
                pass
        body.Placement = App.Placement(
            App.Vector(x, y, z),
            _rotation_from_string(cab_part.rotation),
        )
    except Exception:
        pass


def _rotation_from_string(rot_str):
    """Converte string de rotação em App.Rotation."""
    try:
        import FreeCAD as App
    except ImportError:
        return None

    if rot_str == "x90":
        return App.Rotation(App.Vector(1, 0, 0), 90)
    elif rot_str == "y90":
        return App.Rotation(App.Vector(0, 1, 0), 90)
    elif rot_str == "z90":
        return App.Rotation(App.Vector(0, 0, 1), 90)
    return App.Rotation()


def _make_coincident_constraint(edge_a, edge_b):
    """Cria constraint Coincident entre fim de edge_a e início de edge_b."""
    try:
        import Sketcher
        return Sketcher.Constraint("Coincident", edge_a, 2, edge_b, 1)
    except Exception:
        return None


def _apply_panelnest_properties(body, cab_part, part_index):
    """Aplica as propriedades PanelNest customizadas ao Body."""
    try:
        import FreeCAD as App
    except ImportError:
        return

    props = {
        "PanelNestLabel": (
            "App::PropertyString",
            f"PN-{part_index:03d} {cab_part.name}",
        ),
        "PanelNestMaterial": ("App::PropertyString", cab_part.material),
        "PanelNestGrainDirection": ("App::PropertyString", cab_part.grain_direction),
        "PanelNestCutMethod": ("App::PropertyString", "Auto"),
        "PanelNestAllowRotation": ("App::PropertyBool", True),
        "PanelNestEdgeBandTop": ("App::PropertyBool", cab_part.edge_top),
        "PanelNestEdgeBandBottom": ("App::PropertyBool", cab_part.edge_bottom),
        "PanelNestEdgeBandLeft": ("App::PropertyBool", cab_part.edge_left),
        "PanelNestEdgeBandRight": ("App::PropertyBool", cab_part.edge_right),
    }

    for prop_name, (prop_type, value) in props.items():
        try:
            if not hasattr(body, prop_name):
                body.addProperty(prop_type, prop_name, "PanelNest", "")
            setattr(body, prop_name, value)
        except Exception:
            pass
