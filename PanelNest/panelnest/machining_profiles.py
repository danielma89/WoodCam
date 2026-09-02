"""Perfis de usinagem CNC para marcenaria.

Catálogo de operações padrão (furos de dobradiça, excêntricos, cavilhas, puxadores)
que podem ser associadas a peças e exportadas no G-code NBM.

Cada perfil define posição relativa (em mm) a partir de uma face de referência,
diâmetro da broca e profundidade.
"""
from dataclasses import dataclass, field


@dataclass
class MachiningOperation:
    """Uma operação de usinagem (furo, rebaixo, rasgo)."""
    op_type: str  # "drill", "counterbore", "slot"
    x_offset_mm: float  # Distância da borda de referência X
    y_offset_mm: float  # Distância da borda de referência Y
    diameter_mm: float
    depth_mm: float
    face: str = "top"  # "top", "bottom", "front", "back", "left", "right"
    description: str = ""


@dataclass
class MachiningProfile:
    """Perfil completo de usinagem para um tipo de ferragem.

    Cada perfil define um conjunto de operações que podem ser aplicadas
    em posição relativa à peça.
    """
    profile_id: str
    name: str
    category: str  # "Dobradiça", "Excêntrico", "Cavilha", "Puxador", "Corrediça"
    description: str = ""
    operations: list = field(default_factory=list)  # List[MachiningOperation]
    # Posição relativa: qual canto/face é a referência
    reference_edge: str = "left"  # "left", "right", "top", "bottom"
    mirror_available: bool = True  # Se pode espelhar para lado oposto


# ---------------------------------------------------------------------------
# Catálogo padrão de perfis de usinagem
# ---------------------------------------------------------------------------

# Dobradiça 35mm (Blum/Hettich) — copo + parafusos
HINGE_35MM = MachiningProfile(
    profile_id="HINGE_35MM",
    name="Dobradiça 35mm",
    category="Dobradiça",
    description="Copo de 35mm + 2 furos de parafuso de fixação",
    reference_edge="left",
    operations=[
        MachiningOperation(
            op_type="counterbore",
            x_offset_mm=21.5,  # Centro do copo a 21.5mm da borda
            y_offset_mm=0,     # Y será definido pelo usuário
            diameter_mm=35.0,
            depth_mm=12.5,
            face="top",
            description="Copo de dobradiça 35mm",
        ),
        MachiningOperation(
            op_type="drill",
            x_offset_mm=21.5,
            y_offset_mm=-24.0,  # 24mm abaixo do centro do copo
            diameter_mm=8.0,
            depth_mm=10.0,
            face="top",
            description="Parafuso fixação inferior",
        ),
        MachiningOperation(
            op_type="drill",
            x_offset_mm=21.5,
            y_offset_mm=24.0,  # 24mm acima do centro do copo
            diameter_mm=8.0,
            depth_mm=10.0,
            face="top",
            description="Parafuso fixação superior",
        ),
    ],
)

# Dobradiça 26mm (mini)
HINGE_26MM = MachiningProfile(
    profile_id="HINGE_26MM",
    name="Dobradiça 26mm (mini)",
    category="Dobradiça",
    description="Copo de 26mm para portas pequenas",
    reference_edge="left",
    operations=[
        MachiningOperation(
            op_type="counterbore",
            x_offset_mm=18.0,
            y_offset_mm=0,
            diameter_mm=26.0,
            depth_mm=11.0,
            face="top",
            description="Copo de dobradiça 26mm",
        ),
        MachiningOperation(
            op_type="drill",
            x_offset_mm=18.0,
            y_offset_mm=-20.0,
            diameter_mm=5.0,
            depth_mm=10.0,
            face="top",
            description="Parafuso fixação",
        ),
        MachiningOperation(
            op_type="drill",
            x_offset_mm=18.0,
            y_offset_mm=20.0,
            diameter_mm=5.0,
            depth_mm=10.0,
            face="top",
            description="Parafuso fixação",
        ),
    ],
)

# Excêntrico Minifix 15mm
MINIFIX_15MM = MachiningProfile(
    profile_id="MINIFIX_15MM",
    name="Minifix 15mm (excêntrico)",
    category="Excêntrico",
    description="Furo para corpo + furo para parafuso do excêntrico Minifix 15mm",
    reference_edge="left",
    operations=[
        MachiningOperation(
            op_type="drill",
            x_offset_mm=34.0,  # 34mm da borda (padrão 32mm system + 2mm)
            y_offset_mm=0,
            diameter_mm=15.0,
            depth_mm=13.5,
            face="top",
            description="Corpo excêntrico Minifix Ø15mm",
        ),
        MachiningOperation(
            op_type="drill",
            x_offset_mm=8.0,
            y_offset_mm=0,
            diameter_mm=5.0,
            depth_mm=34.0,  # Passante ou profundo
            face="front",  # Na face da borda
            description="Parafuso excêntrico Ø5mm",
        ),
    ],
)

# Cavilha 8mm
DOWEL_8MM = MachiningProfile(
    profile_id="DOWEL_8MM",
    name="Cavilha Ø8mm",
    category="Cavilha",
    description="Furo para cavilha de madeira 8mm",
    reference_edge="left",
    operations=[
        MachiningOperation(
            op_type="drill",
            x_offset_mm=0,  # Posição X definida pelo usuário
            y_offset_mm=0,
            diameter_mm=8.0,
            depth_mm=20.0,
            face="front",
            description="Cavilha Ø8mm",
        ),
    ],
)

# Cavilha 6mm (VB)
DOWEL_6MM = MachiningProfile(
    profile_id="DOWEL_6MM",
    name="Cavilha Ø6mm (VB)",
    category="Cavilha",
    description="Furo para cavilha VB 6mm (parafuso confirmat)",
    reference_edge="left",
    operations=[
        MachiningOperation(
            op_type="drill",
            x_offset_mm=0,
            y_offset_mm=0,
            diameter_mm=6.0,
            depth_mm=25.0,
            face="front",
            description="VB Ø6mm",
        ),
    ],
)

# Confirmat (parafuso de montagem)
CONFIRMAT = MachiningProfile(
    profile_id="CONFIRMAT",
    name="Confirmat 7×50mm",
    category="Cavilha",
    description="Pré-furo para parafuso confirmat 7×50mm",
    reference_edge="left",
    operations=[
        MachiningOperation(
            op_type="drill",
            x_offset_mm=0,
            y_offset_mm=0,
            diameter_mm=5.0,
            depth_mm=40.0,
            face="front",
            description="Pré-furo confirmat Ø5mm",
        ),
    ],
)

# Puxador (furo passante)
HANDLE_HOLE = MachiningProfile(
    profile_id="HANDLE_HOLE",
    name="Puxador (furo passante)",
    category="Puxador",
    description="Furo passante para parafuso de puxador",
    reference_edge="left",
    operations=[
        MachiningOperation(
            op_type="drill",
            x_offset_mm=0,
            y_offset_mm=0,
            diameter_mm=5.0,
            depth_mm=0,  # 0 = passante (usa espessura da peça)
            face="top",
            description="Furo passante Ø5mm para puxador",
        ),
    ],
)

# Corrediça telescópica (furos de fixação)
SLIDE_HOLES = MachiningProfile(
    profile_id="SLIDE_HOLES",
    name="Corrediça telescópica",
    category="Corrediça",
    description="Linha de furos para corrediça (sistema 32mm)",
    reference_edge="bottom",
    operations=[
        MachiningOperation(
            op_type="drill",
            x_offset_mm=37.0,
            y_offset_mm=0,
            diameter_mm=5.0,
            depth_mm=12.0,
            face="top",
            description="Fixação corrediça frontal",
        ),
        MachiningOperation(
            op_type="drill",
            x_offset_mm=37.0,
            y_offset_mm=32.0,
            diameter_mm=5.0,
            depth_mm=12.0,
            face="top",
            description="Fixação corrediça 32mm",
        ),
        MachiningOperation(
            op_type="drill",
            x_offset_mm=37.0,
            y_offset_mm=64.0,
            diameter_mm=5.0,
            depth_mm=12.0,
            face="top",
            description="Fixação corrediça 64mm",
        ),
    ],
)


# Catálogo completo
MACHINING_PROFILES = [
    HINGE_35MM,
    HINGE_26MM,
    MINIFIX_15MM,
    DOWEL_8MM,
    DOWEL_6MM,
    CONFIRMAT,
    HANDLE_HOLE,
    SLIDE_HOLES,
]

MACHINING_CATEGORIES = [
    "Dobradiça",
    "Excêntrico",
    "Cavilha",
    "Puxador",
    "Corrediça",
]


def get_profile_by_id(profile_id):
    """Retorna perfil pelo ID."""
    for p in MACHINING_PROFILES:
        if p.profile_id == profile_id:
            return p
    return None


def get_profiles_by_category(category):
    """Retorna lista de perfis de uma categoria."""
    return [p for p in MACHINING_PROFILES if p.category == category]


def compute_absolute_operations(profile, part_length_mm, part_width_mm, part_thickness_mm,
                                 y_position_mm, mirror=False):
    """Calcula coordenadas absolutas das operações para uma peça.

    profile: MachiningProfile
    part_length_mm, part_width_mm, part_thickness_mm: dimensões da peça
    y_position_mm: posição Y do centro do perfil (ex: 100mm da borda inferior)
    mirror: se True, espelha para o lado oposto

    Retorna lista de dicts compatíveis com PanelPart.holes:
    [{x_mm, y_mm, diameter_mm, depth_mm, face, description}]
    """
    results = []
    for op in profile.operations:
        x = op.x_offset_mm
        y = y_position_mm + op.y_offset_mm

        if mirror:
            if profile.reference_edge == "left":
                x = part_length_mm - x
            elif profile.reference_edge == "right":
                x = part_length_mm - x
            elif profile.reference_edge == "bottom":
                y = part_width_mm - y
            elif profile.reference_edge == "top":
                y = part_width_mm - y

        depth = op.depth_mm
        if depth == 0:
            depth = part_thickness_mm  # Passante

        results.append({
            "x_mm": round(x, 2),
            "y_mm": round(y, 2),
            "diameter_mm": op.diameter_mm,
            "depth_mm": round(depth, 2),
            "face": op.face,
            "description": op.description,
        })

    return results
