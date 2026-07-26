try:
    from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:
    try:
        from PySide2 import QtWidgets, QtCore, QtGui
    except ImportError:
        from PySide import QtGui, QtCore
        QtWidgets = QtGui

import FreeCAD
import FreeCADGui
import Part
import bisect
import json
import math
import os
import time
from array import array
from pathlib import Path

from presets import DEFAULT_PRESETS, MATERIAL_PRESETS, TOOL_PRESETS
from geometry_reader import get_selected_geometry, _extract_geometry_from_object
from operations import (
    CUT_SIDE_INSIDE,
    CUT_SIDE_ON_LINE,
    CUT_SIDE_OUTSIDE,
    build_contour_cut_stage,
    build_machining_stages,
    build_pocket_stage,
    split_nested_contours,
    _with_configured_start,
)
from gcode_writer import HEADER_TEMPLATE, build_gcode, save_gcode_file
from validators import validate_selected_contours, validate_settings
from cam_advisor import analyze_cam_settings
from woodcam_3d import (
    FinishingOptions,
    RoughingOptions,
    build_3d_finishing_moves,
    build_3d_roughing_moves,
    expand_height_field_support,
    extend_height_field,
    height_field_from_mesh,
)
from woodcam_3d.freecad_adapter import (
    height_field_from_relief_object,
    mesh_data_from_object,
    relief_source_fingerprint,
    selected_surface_object,
)
from woodcam_3d.coin_toolpath import CoinToolpathOverlay, create_coin_toolpath_feature
from woodcam_3d.preview import moves_for_preview, preview_material_top_z
from woodcam_3d.storage import decode_moves, encode_moves
from woodcam_tree import ensure_woodcam_tree
try:
    from woodcam_editor.domain import (
        AddEntitiesCommand,
        ApplyModifierPreviewCommand,
        Affine2D,
        ClosePathCommand,
        CompositeCommand,
        DeleteEntitiesCommand,
        CircleEntity,
        EllipseEntity,
        GroupEntity,
        InMemoryCommandHistory,
        JoinPathsCommand,
        JoinOpenPathsWithinToleranceCommand,
        MoveEntitiesCommand,
        PathEntity,
        Piece2D,
        ReplaceEntitiesCommand,
        ReplacePiecesCommand,
        SetDocumentMetadataCommand,
        SetWorkAreaCommand,
        Vec2,
        VectorDocument,
        WorkArea,
        TransformEntitiesCommand,
        serialize_document,
        validate_document,
        exact_duplicate_entity_groups,
        redundant_open_overline_entity_ids,
    )
    from woodcam_editor.presentation.widget import Editor2DWidget
    from woodcam_editor.presentation.workflows import ValidationReportDialog
    from woodcam_editor.application import CommandExecutionCancelled
    from woodcam_editor.application import prepare_import_batch
    from woodcam_editor.adapters.freecad_store import FreeCADDocumentStore
    from woodcam_editor.adapters.freecad_commands import FreeCADCommandSession
    from woodcam_editor.adapters.woodcam_geometry import (
        GeometryAdapterError,
        document_to_woodcam_geometry,
    )
    from woodcam_editor.application.document_store import StoredDocumentCorruptError
    from woodcam_editor.application.piece_organizer import (
        classify_document_pieces,
        organize_pieces,
    )
    from woodcam_editor.importers.sketch import import_sketch

    NEW_VECTOR_EDITOR_AVAILABLE = True
except Exception:
    # A bancada continua abrindo mesmo se uma instalação antiga estiver com o
    # pacote novo incompleto. O erro detalhado aparecerá dentro da aba.
    NEW_VECTOR_EDITOR_AVAILABLE = False
    Editor2DWidget = None
    from vector_editor import VectorCanvas


WOODCAM_PARAMETER_PATH = "User parameter:BaseApp/Preferences/WoodCAM2D"
_USE_ACTIVE_FREECAD_DOCUMENT = object()


def _trim_transparent_pixmap(pixmap, padding=0):
    """Remove margem transparente de PNGs para manter desenhos centralizados."""
    if pixmap is None or pixmap.isNull():
        return pixmap
    try:
        mask = pixmap.mask()
        rect = mask.boundingRect()
        if not rect.isValid() or rect.isNull() or (
            rect.width() >= pixmap.width() and rect.height() >= pixmap.height()
        ):
            alpha_mask = QtGui.QBitmap.fromImage(
                pixmap.toImage().createAlphaMask()
            )
            alpha_rect = alpha_mask.boundingRect()
            if alpha_rect.isValid() and not alpha_rect.isNull():
                rect = alpha_rect
        if not rect.isValid() or rect.isNull():
            return pixmap
        if rect.width() >= pixmap.width() and rect.height() >= pixmap.height():
            return pixmap
        if padding:
            rect = rect.adjusted(-padding, -padding, padding, padding).intersected(
                QtCore.QRect(0, 0, pixmap.width(), pixmap.height())
            )
        return pixmap.copy(rect)
    except Exception:
        return pixmap


def _make_white_background_transparent(pixmap):
    """Repair user-supplied RGB tab art that visually has a white backdrop.

    The current 2D artwork was saved as RGB (no alpha channel), despite the
    white area being intended as transparent.  Exact white is safe to remove
    for this small tab icon and preserves the drawing colours.
    """

    if pixmap is None or pixmap.isNull():
        return pixmap
    try:
        source_image = pixmap.toImage()
        # A arte final do 2D passou a ser um PNG RGBA de verdade.  Não rode a
        # heurística RGB sobre ela: além de ser desnecessário, ela poderia
        # tornar transparentes reflexos claros legítimos da peça de madeira.
        if source_image.hasAlphaChannel():
            return pixmap
        # The tab is at most a few dozen pixels.  Work on a compact copy both
        # to keep startup instant and to avoid white interpolation halos.
        pixmap = pixmap.scaled(128, 128)
        image = pixmap.toImage()
        format_argb32 = getattr(QtGui.QImage, "Format_ARGB32", None)
        if format_argb32 is None:
            format_argb32 = QtGui.QImage.Format.Format_ARGB32
        image = image.convertToFormat(format_argb32)
        # Do this explicitly: QImage.createMaskFromColor differs between Qt5
        # and Qt6 and was leaving the RGB white square opaque in FreeCAD.
        for y_value in range(image.height()):
            for x_value in range(image.width()):
                color = image.pixelColor(x_value, y_value)
                channels = (color.red(), color.green(), color.blue())
                # The supplied artwork contains a *drawn* transparency
                # checkerboard (RGB, without an alpha channel), not a real
                # transparent background.  Remove both white and the pale
                # neutral-grey squares.  The beige 2D tile is chromatic, so
                # this cannot erase its highlights.
                if min(channels) >= 225 and max(channels) - min(channels) <= 8:
                    color.setAlpha(0)
                    image.setPixelColor(x_value, y_value, color)
        return QtGui.QPixmap.fromImage(image)
    except Exception:
        return pixmap
STAGE_FILE_SUFFIXES = {
    "holes": "furos",
    "pocket": "rebaixo",
    "cut": "corte",
    "rough3d": "desbaste_3d",
    "finish3d": "acabamento_3d",
}
STAGE_LABELS = {
    "holes": "Furos",
    "pocket": "Preenchimento",
    "cut": "Corte",
    "rough3d": "Desbaste 3D",
    "finish3d": "Acabamento 3D",
}
OPERATION_TREE_LABELS = {
    "holes": "Furo",
    "pocket": "Preenchimento",
    "cut": "Corte",
    "rough3d": "Desbaste 3D",
    "finish3d": "Acabamento 3D",
}
OPERATION_MODE_BY_TAB_TITLE = {
    "Corte": "cut",
    "Furo": "holes",
    "Rebaixo": "pocket",
    "Preenchimento": "pocket",
    "Preenchimento/Rebaixo": "pocket",
    "Desbaste 3D": "rough3d",
    "Acabamento 3D": "finish3d",
}
ACTION_TAB_TITLE = "Simulação e Salvar"
JOB_TYPE_LABELS = {
    "single_sided": "Face única",
    "double_sided": "Dupla face",
}
ORIGIN_ANCHORS = [
    ("top_left", 0, 0),
    ("top_center", 0, 1),
    ("top_right", 0, 2),
    ("middle_left", 1, 0),
    ("center", 1, 1),
    ("middle_right", 1, 2),
    ("bottom_left", 2, 0),
    ("bottom_center", 2, 1),
    ("bottom_right", 2, 2),
]
ORIGIN_ANCHOR_LABELS = {
    "top_left": "superior esquerdo",
    "top_center": "superior centro",
    "top_right": "superior direito",
    "middle_left": "meio esquerdo",
    "center": "centro",
    "middle_right": "meio direito",
    "bottom_left": "inferior esquerdo",
    "bottom_center": "inferior centro",
    "bottom_right": "inferior direito",
}
DIAGRAM_ASSET_FILENAMES = {
    "work": ("material_tab.png", "material.png", "trabalho.png", "work.png", "job_type.png", "tipo_trabalho.png"),
    "job_type": ("job_type.png", "tipo_trabalho.png", "face_unica.png"),
    "job_size": ("job_size.png", "tamanho_trabalho.png", "dimensoes.png"),
    "z_zero": ("z_zero.png", "z_zero_chapa.png", "z_na_chapa.png"),
    "origin": ("origin.png", "origem_xy.png"),
    "home": ("fresa2.png", "fresa_2.png", "home.png", "origem_inicial.png"),
    "start_point": ("ponto_inicial.png", "home.png", "origem_inicial.png", "origin.png", "origem_xy.png"),
    "material": ("material_tab.png", "material.png", "definicao_material.png", "espessura.png"),
    "material_z_zero": ("material_z_surface.png", "m1.png", "material.png"),
    "model_position": ("model_position.png", "modelo.png"),
    "rapid_z": ("rapid_z.png", "z_rapido.png", "z rapido.png", "safety.png"),
    "safety": ("safety.png", "z_seguro.png", "seguranca.png"),
    "cut": ("tab_cut_profile.png", "cut.png", "corte.png", "perfil.png"),
    "editor2d": ("2d.png", "2D.png", "cut.png", "corte.png", "perfil.png"),
    "cut_external": ("corte externo.png", "cut_external.png", "fora.png"),
    "cut_inside": ("corte interno.png", "cut_inside.png", "dentro.png"),
    "cut_on_line": ("corte sobre.png", "cut_on_line.png", "sobre.png"),
    "depth": (
        "profundidade_corte.png",
        "ChatGPT Image 10 de jul. de 2026, 11_18_20.png",
        "profundidade de corte.png",
        "profundidade.png",
        "profundidades.png",
        "depth.png",
    ),
    "tool": ("tool_icon_end_mill.png", "i1.png", "tool_end_mill.png", "f1.png", "fresa.png", "tool.png", "ferramenta.png", "broca.png"),
    "ramp": ("ramp.png", "rampa.png"),
    "entry_smooth": ("entrada_suave.png",),
    "entry_zigzag": ("entrada_zigzag.png",),
    "entry_spiral": ("entrada_espiral.png",),
    "corner": ("corner.png", "canto.png", "cantos.png"),
    "tabs": ("tabs.png", "aba.png", "abas.png"),
    "tolerance": ("tolerância.png", "tolerancia.png", "last_pass.png"),
    "holes": ("tab_holes.png", "holes.png", "furo.png", "furacao.png"),
    "helix": ("helix.png", "helicoidal.png"),
    "peck": ("peck.png", "furacao_faseada.png", "faseada.png"),
    "dwell": ("dwell.png", "permanencia.png", "espera.png"),
    "order": ("order.png", "ordem.png", "ordem_vetores.png"),
    "pocket": ("tab_pocket.png", "pocket.png", "preenchimento.png", "rebaixo.png", "bolso.png"),
    "rough3d": ("tab_rough3d.png", "rough3d.png", "desbaste_3d.png"),
    "finish3d": ("tab_finish3d.png", "finish3d.png", "acabamento_3d.png"),
    "simulation": ("simulation.png", "simulacao.png"),
    "export": ("export.png", "salvar.png", "exportar.png"),
    "flow": ("flow.png", "fluxo.png"),
}
ORIGIN_ANCHOR_ASSET_FILENAMES = {
    "top_left": ("origin_top_left.png",),
    "top_center": ("origin_top_center.png",),
    "top_right": ("origin_top_right.png",),
    "middle_left": ("origin_middle_left.png",),
    "center": ("origin_center.png",),
    "middle_right": ("origin_middle_right.png",),
    "bottom_left": ("origin_bottom_left.png", "origin.png", "origem_xy.png"),
    "bottom_center": ("origin_bottom_center.png",),
    "bottom_right": ("origin_bottom_right.png",),
}
MATERIAL_Z_ZERO_ASSET_FILENAMES = {
    "material_surface": ("material_z_surface.png", "m1.png"),
    "machine_bed": ("material_z_bed.png", "m2.png"),
}
CUT_SIDE_ASSET_FILENAMES = {
    CUT_SIDE_OUTSIDE: DIAGRAM_ASSET_FILENAMES["cut_external"],
    CUT_SIDE_ON_LINE: DIAGRAM_ASSET_FILENAMES["cut_on_line"],
    CUT_SIDE_INSIDE: DIAGRAM_ASSET_FILENAMES["cut_inside"],
}
TOOL_TYPE_ORDER = (
    "end_mill",
    "ball_nose",
    "v_bit",
    "drill",
    "compression",
    "surfacing",
)
TOOL_TYPE_DEFINITIONS = {
    "end_mill": {
        "label": "Topo reto (End Mill)",
        "short_label": "Topo reto",
        "concept": "Corte lateral, perfil 2D e rebaixo com fundo plano. É a fresa padrão para cortar MDF e chapas.",
        "geometry": "Diâmetro D define o raio mínimo interno: cantos internos ficam arredondados por D/2.",
    },
    "ball_nose": {
        "label": "Topo esférico (Ball Nose)",
        "short_label": "Esférica",
        "concept": "Acabamento 3D e relevos. A ponta arredondada suaviza superfícies, mas não deixa fundo plano perfeito.",
        "geometry": "O diâmetro define a esfera; o passo lateral costuma ser pequeno para reduzir marcas.",
    },
    "v_bit": {
        "label": "Fresa V / Chanfro",
        "short_label": "V-Bit",
        "concept": "Gravação, chanfros, letras e futuros percursos V-carve. A largura do corte muda com a profundidade.",
        "geometry": "Além do diâmetro, o ângulo incluído é essencial: 60°, 90° etc.",
    },
    "drill": {
        "label": "Broca",
        "short_label": "Broca",
        "concept": "Furação vertical. Boa para descer no centro do furo; não é indicada para perfilar lateralmente.",
        "geometry": "O diâmetro é o tamanho nominal do furo quando a broca é igual ao furo.",
    },
    "compression": {
        "label": "Compressão",
        "short_label": "Compressão",
        "concept": "Corte de chapas laminadas ou melamínicas. As hélices opostas ajudam a reduzir lascas em cima e embaixo.",
        "geometry": "Precisa entrar o suficiente para a zona de compressão trabalhar corretamente.",
    },
    "surfacing": {
        "label": "Faceadora / Spoilboard",
        "short_label": "Faceadora",
        "concept": "Plainar mesa, nivelar spoilboard ou regularizar superfícies. Usa diâmetro maior e cortes rasos.",
        "geometry": "Passo lateral costuma ser percentual alto, mas a profundidade por passe é pequena.",
    },
}
TOOL_TYPE_IMAGE_FILENAMES = {
    "end_mill": ("tool_end_mill.png", "f1.png"),
    "ball_nose": ("tool_ball_nose.png", "f2.png"),
    "v_bit": ("tool_v_bit.png", "f3.png", "f6.png"),
    "drill": ("tool_drill.png", "f7.png"),
    "compression": ("tool_compression.png", "f9.png"),
    "surfacing": ("tool_surfacing.png", "f8.png"),
}
TOOL_TYPE_ICON_FILENAMES = {
    "end_mill": ("tool_icon_end_mill.png", "i1.png"),
    "ball_nose": ("tool_icon_ball_nose.png", "i2.png"),
    "v_bit": ("tool_icon_v_bit.png", "i4.png"),
    "drill": ("tool_icon_drill.png", "i3.png"),
    "compression": ("tool_icon_compression.png", "i3.png"),
    "surfacing": ("tool_icon_surfacing.png", "i5.png"),
}
TOOL_EDITOR_NUMERIC_FIELDS = (
    "tool_diameter",
    "included_angle",
    "stepdown",
    "stepover",
    "stepover_percent",
    "feed_xy",
    "feed_z",
    "rapid_feed",
    "ramp_length",
    "rpm",
    "tool_number",
)


def _tool_type_tooltip(tool_type):
    definition = TOOL_TYPE_DEFINITIONS.get(tool_type, TOOL_TYPE_DEFINITIONS["end_mill"])
    return f"{definition['concept']}\n{definition['geometry']}"


class OperationDiagram(QtWidgets.QWidget):
    """Pequeno esquema vetorial para identificação visual da operação."""

    def __init__(self, kind, state_provider=None, parent=None):
        super(OperationDiagram, self).__init__(parent)
        self.kind = kind
        self._has_dynamic_state = state_provider is not None
        self.state_provider = state_provider or (lambda: {})
        self.setMinimumSize(170, 105)
        self.setMaximumHeight(115)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Preferred,
            QtWidgets.QSizePolicy.Fixed,
        )

    def _state(self):
        try:
            return dict(self.state_provider() or {})
        except Exception:
            return {}

    def _pen(self, color, width=1.5, dashed=False):
        pen = QtGui.QPen(QtGui.QColor(color))
        pen.setWidthF(float(width))
        if dashed:
            pen.setStyle(QtCore.Qt.DashLine)
        return pen

    def _arrow(self, painter, start, end, color="#2563eb"):
        painter.setPen(self._pen(color, 1.6))
        painter.drawLine(start, end)
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        size = 6.0
        for offset in (2.55, -2.55):
            tip = QtCore.QPointF(
                end.x() + math.cos(angle + offset) * size,
                end.y() + math.sin(angle + offset) * size,
            )
            painter.drawLine(end, tip)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        except Exception:
            pass
        painter.fillRect(self.rect(), QtGui.QColor("#f7f9fc"))
        painter.setPen(self._pen("#cbd5e1", 1.0))
        painter.drawRoundedRect(
            QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
            8,
            8,
        )

        if self.kind == "job":
            self._paint_job(painter)
        elif self.kind == "material":
            self._paint_material(painter)
        elif self.kind == "cut":
            self._paint_cut(painter)
        elif self.kind == "holes":
            self._paint_holes(painter)
        elif self.kind == "pocket":
            self._paint_pocket(painter)
        elif self.kind == "simulation":
            self._paint_simulation(painter)
        else:
            self._paint_general(painter)
        painter.end()

    def _paint_board_3d(self, painter, base_rect, top_color="#f6c06b"):
        top = QtGui.QPolygonF(
            [
                QtCore.QPointF(base_rect.left() + 17, base_rect.top() - 16),
                QtCore.QPointF(base_rect.right() + 17, base_rect.top() - 16),
                QtCore.QPointF(base_rect.right(), base_rect.top()),
                QtCore.QPointF(base_rect.left(), base_rect.top()),
            ]
        )
        side = QtGui.QPolygonF(
            [
                QtCore.QPointF(base_rect.right(), base_rect.top()),
                QtCore.QPointF(base_rect.right() + 17, base_rect.top() - 16),
                QtCore.QPointF(base_rect.right() + 17, base_rect.bottom() - 16),
                QtCore.QPointF(base_rect.right(), base_rect.bottom()),
            ]
        )
        painter.setBrush(QtGui.QColor(top_color))
        painter.setPen(self._pen("#92400e", 1.1))
        painter.drawPolygon(top)
        painter.setBrush(QtGui.QColor("#c48642"))
        painter.drawPolygon(side)
        painter.setBrush(QtGui.QColor("#d99a50"))
        painter.drawRect(base_rect)

    def _paint_job(self, painter):
        board = QtCore.QRectF(38, 38, 92, 48)
        self._paint_board_3d(painter, board)
        painter.setPen(self._pen("#2563eb", 1.6))
        self._arrow(
            painter,
            QtCore.QPointF(board.left(), board.bottom() + 8),
            QtCore.QPointF(board.right(), board.bottom() + 8),
            "#2563eb",
        )
        self._arrow(
            painter,
            QtCore.QPointF(board.left() - 9, board.bottom()),
            QtCore.QPointF(board.left() - 9, board.top()),
            "#16a34a",
        )
        painter.setPen(self._pen("#1e3a8a", 1.0))
        painter.drawText(12, 21, "TRABALHO E ORIGEM")

    def _paint_material(self, painter):
        board = QtCore.QRectF(38, 45, 82, 31)
        self._paint_board_3d(painter, board, "#f7c879")
        painter.setPen(self._pen("#334155", 1.2))
        painter.drawLine(QtCore.QPointF(134, 29), QtCore.QPointF(134, 75))
        painter.drawLine(QtCore.QPointF(128, 29), QtCore.QPointF(140, 29))
        painter.drawLine(QtCore.QPointF(128, 75), QtCore.QPointF(140, 75))
        painter.setPen(self._pen("#92400e", 1.2))
        painter.setBrush(QtGui.QColor("#ef4444"))
        painter.drawEllipse(QtCore.QPointF(39, 58), 4, 4)
        painter.setPen(self._pen("#16a34a", 1.6, dashed=True))
        painter.drawLine(QtCore.QPointF(28, 38), QtCore.QPointF(151, 38))
        painter.setPen(self._pen("#166534", 1.0))
        painter.drawText(102, 34, "Z ZERO")
        painter.setPen(self._pen("#1e3a8a", 1.0))
        painter.drawText(12, 101, "MATERIAL E SEGURANÇA")

    def _paint_simulation(self, painter):
        painter.setBrush(QtGui.QColor("#dbeafe"))
        painter.setPen(self._pen("#334155", 1.4))
        painter.drawRoundedRect(QtCore.QRectF(20, 24, 64, 50), 5, 5)
        play = QtGui.QPolygonF(
            [
                QtCore.QPointF(43, 36),
                QtCore.QPointF(43, 62),
                QtCore.QPointF(64, 49),
            ]
        )
        painter.setBrush(QtGui.QColor("#2563eb"))
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawPolygon(play)
        painter.setPen(self._pen("#64748b", 1.2))
        painter.setBrush(QtGui.QColor("#ffffff"))
        painter.drawRect(QtCore.QRectF(104, 20, 42, 62))
        painter.drawLine(QtCore.QPointF(112, 35), QtCore.QPointF(138, 35))
        painter.drawLine(QtCore.QPointF(112, 47), QtCore.QPointF(138, 47))
        painter.drawLine(QtCore.QPointF(112, 59), QtCore.QPointF(132, 59))
        painter.setPen(self._pen("#f59e0b", 2.0))
        self._arrow(
            painter,
            QtCore.QPointF(86, 49),
            QtCore.QPointF(102, 49),
            "#f59e0b",
        )
        painter.setPen(self._pen("#1e3a8a", 1.0))
        painter.drawText(12, 101, "SIMULAR E SALVAR")

    def _paint_cut(self, painter):
        state = self._state()
        side = state.get("side", CUT_SIDE_OUTSIDE)
        climb = state.get("climb", True)
        part = QtCore.QRectF(24, 16, 122, 70)
        painter.setBrush(QtGui.QColor("#dbeafe"))
        painter.setPen(self._pen("#334155", 1.8))
        painter.drawRect(part)
        offset = {
            CUT_SIDE_OUTSIDE: -8.0,
            CUT_SIDE_ON_LINE: 0.0,
            CUT_SIDE_INSIDE: 8.0,
        }.get(side, -8.0)
        path = part.adjusted(offset, offset, -offset, -offset)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(self._pen("#2563eb", 2.0, dashed=True))
        painter.drawRect(path)

        # O corte climb é horário no contorno externo/sobre a linha e
        # anti-horário no contorno interno, como no gerador de percurso.
        clockwise = bool(climb) if side != CUT_SIDE_INSIDE else not bool(climb)
        arrow_color = "#2563eb"
        if clockwise:
            self._arrow(
                painter,
                QtCore.QPointF(path.left() + 12, path.top()),
                QtCore.QPointF(path.right() - 15, path.top()),
                arrow_color,
            )
            self._arrow(
                painter,
                QtCore.QPointF(path.right(), path.top() + 12),
                QtCore.QPointF(path.right(), path.bottom() - 12),
                arrow_color,
            )
        else:
            self._arrow(
                painter,
                QtCore.QPointF(path.right() - 12, path.top()),
                QtCore.QPointF(path.left() + 15, path.top()),
                arrow_color,
            )
            self._arrow(
                painter,
                QtCore.QPointF(path.right(), path.bottom() - 12),
                QtCore.QPointF(path.right(), path.top() + 12),
                arrow_color,
            )
        tool_x = path.right()
        tool_y = path.center().y()
        painter.setBrush(QtGui.QColor("#f59e0b"))
        painter.setPen(self._pen("#92400e", 1.2))
        painter.drawEllipse(QtCore.QPointF(tool_x, tool_y), 6, 6)
        label = {
            CUT_SIDE_OUTSIDE: "POR FORA",
            CUT_SIDE_ON_LINE: "SOBRE A LINHA",
            CUT_SIDE_INSIDE: "POR DENTRO",
        }.get(side, "CORTE")
        painter.setPen(self._pen("#1e3a8a", 1.0))
        direction = "SUBIDA" if climb else "CONVENCIONAL"
        painter.drawText(12, 101, f"{label} • {direction}")

    def _paint_holes(self, painter):
        state = self._state()
        painter.setPen(self._pen("#64748b", 1.2))
        painter.setBrush(QtGui.QColor("#e2e8f0"))
        painter.drawRect(QtCore.QRectF(20, 48, 130, 38))
        painter.setBrush(QtGui.QColor("#f7f9fc"))
        painter.drawRect(QtCore.QRectF(76, 48, 18, 38))
        painter.setBrush(QtGui.QColor("#f59e0b"))
        painter.setPen(self._pen("#92400e", 1.2))
        painter.drawRect(QtCore.QRectF(80, 12, 10, 43))
        painter.setPen(self._pen("#2563eb", 1.8))
        if state.get("helical", True):
            points = []
            for index in range(31):
                ratio = index / 30.0
                points.append(
                    QtCore.QPointF(
                        85 + math.sin(ratio * math.pi * 6.0) * 10.0,
                        28 + ratio * 58.0,
                    )
                )
            painter.drawPolyline(QtGui.QPolygonF(points))
            label = "ENTRADA HELICOIDAL"
        elif state.get("peck", False):
            for top, bottom in ((29, 44), (38, 61), (53, 78)):
                self._arrow(
                    painter,
                    QtCore.QPointF(107, top),
                    QtCore.QPointF(107, bottom),
                )
            label = "FURAÇÃO FASEADA"
        else:
            self._arrow(
                painter,
                QtCore.QPointF(108, 22),
                QtCore.QPointF(108, 81),
            )
            label = "FURAÇÃO DIRETA"
        painter.setPen(self._pen("#1e3a8a", 1.0))
        painter.drawText(12, 101, label)

    def _paint_pocket(self, painter):
        state = self._state()
        strategy = state.get("strategy", "offset")
        climb = state.get("climb", True)
        part = QtCore.QRectF(34, 20, 110, 66)
        painter.setBrush(QtGui.QColor("#fef3c7"))
        painter.setPen(self._pen("#334155", 1.6))
        painter.drawRect(part)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(self._pen("#2563eb", 1.8))
        if strategy == "raster":
            for row, y_value in enumerate(range(29, 82, 9)):
                if row % 2:
                    self._arrow(
                        painter,
                        QtCore.QPointF(135, y_value),
                        QtCore.QPointF(43, y_value),
                    )
                else:
                    self._arrow(
                        painter,
                        QtCore.QPointF(43, y_value),
                        QtCore.QPointF(135, y_value),
                    )
            label = "RASTER • ZIGUE-ZAGUE"
        else:
            for inset in (8, 16, 24):
                painter.drawRect(part.adjusted(inset, inset, -inset, -inset))
            if climb:
                self._arrow(
                    painter,
                    QtCore.QPointF(122, 28),
                    QtCore.QPointF(57, 28),
                )
                self._arrow(
                    painter,
                    QtCore.QPointF(136, 73),
                    QtCore.QPointF(136, 37),
                )
                label = "OFFSET • CONCORDANTE"
            else:
                self._arrow(
                    painter,
                    QtCore.QPointF(57, 28),
                    QtCore.QPointF(122, 28),
                )
                self._arrow(
                    painter,
                    QtCore.QPointF(136, 37),
                    QtCore.QPointF(136, 73),
                )
                label = "OFFSET • CONVENCIONAL"
        painter.setPen(self._pen("#1e3a8a", 1.0))
        painter.drawText(12, 101, label)

    def _paint_general(self, painter):
        painter.setBrush(QtGui.QColor("#e2e8f0"))
        painter.setPen(self._pen("#64748b", 1.2))
        painter.drawRect(QtCore.QRectF(28, 64, 112, 25))
        painter.setPen(self._pen("#16a34a", 1.8, dashed=True))
        painter.drawLine(QtCore.QPointF(20, 39), QtCore.QPointF(150, 39))
        painter.setBrush(QtGui.QColor("#f59e0b"))
        painter.setPen(self._pen("#92400e", 1.2))
        painter.drawRect(QtCore.QRectF(80, 15, 10, 34))
        self._arrow(
            painter,
            QtCore.QPointF(145, 89),
            QtCore.QPointF(145, 43),
            "#16a34a",
        )
        painter.setPen(self._pen("#166534", 1.0))
        painter.drawText(96, 34, "Z SEGURO")
        painter.setPen(self._pen("#1e3a8a", 1.0))
        painter.drawText(12, 101, "ORIGEM E SEGURANÇA")


class MiniSetupDiagram(QtWidgets.QWidget):
    """Desenho pequeno para blocos de configuração, no espírito do Aspire."""

    SIZE_BY_KIND = {
        "job_type": (106, 72),
        "job_size": (106, 72),
        "origin": (106, 78),
        "start_point": (106, 78),
        "material_z_zero": (108, 76),
        "model_position": (118, 100),
        "rapid_z": (118, 86),
        "home": (108, 86),
        "cut_external": (78, 62),
        "cut_inside": (78, 62),
        "cut_on_line": (78, 62),
        "entry_smooth": (86, 64),
        "entry_zigzag": (86, 64),
        "entry_spiral": (86, 64),
        "tabs": (132, 150),
        "tolerance": (132, 150),
        "depth": (112, 78),
        "cut": (106, 82),
    }

    def __init__(self, kind, state_provider=None, parent=None):
        super(MiniSetupDiagram, self).__init__(parent)
        self.kind = kind
        self._has_dynamic_state = state_provider is not None
        self.state_provider = state_provider or (lambda: {})
        width, height = self.SIZE_BY_KIND.get(kind, (92, 64))
        self.setFixedSize(width, height)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent; border: none;")
        try:
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
            self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        except Exception:
            pass
        self._asset_pixmap = (
            None if self._has_dynamic_state else self._load_asset_pixmap()
        )

    def _asset_directories(self):
        directories = []
        configured = os.environ.get("WOODCAM2D_DIAGRAM_PATH", "").strip()
        if configured:
            directories.extend(
                Path(part).expanduser()
                for part in configured.split(os.pathsep)
                if part.strip()
            )
        base = Path(__file__).resolve().parent
        directories.extend(
            [
                base / "resources" / "diagrams",
                base / "resources" / "icons",
            ]
        )
        return directories

    def _load_asset_pixmap(self, filenames=None):
        if not hasattr(QtGui, "QPixmap"):
            return None
        filenames = filenames or DIAGRAM_ASSET_FILENAMES.get(self.kind, ())
        generic_names = (
            f"{self.kind}.png",
            f"{self.kind}.jpg",
            f"{self.kind}.jpeg",
            f"{self.kind}.webp",
        )
        for directory in self._asset_directories():
            for filename in tuple(filenames) + generic_names:
                path = directory / filename
                if not path.is_file():
                    continue
                pixmap = QtGui.QPixmap(str(path))
                if not pixmap.isNull():
                    return _trim_transparent_pixmap(pixmap, padding=2)
        return None

    def _paint_asset(self, painter):
        pixmap = self._asset_pixmap
        if pixmap is None and self.kind == "origin":
            anchor = self._state().get("anchor", "bottom_left")
            filenames = ORIGIN_ANCHOR_ASSET_FILENAMES.get(
                anchor,
                ORIGIN_ANCHOR_ASSET_FILENAMES["bottom_left"],
            )
            pixmap = self._load_asset_pixmap(filenames)
        if pixmap is None and self.kind == "material_z_zero":
            mode = self._state().get("z_zero_mode", "material_surface")
            filenames = MATERIAL_Z_ZERO_ASSET_FILENAMES.get(
                mode,
                MATERIAL_Z_ZERO_ASSET_FILENAMES["material_surface"],
            )
            pixmap = self._load_asset_pixmap(filenames)
        if pixmap is None and self.kind == "cut":
            side = self._state().get("side", CUT_SIDE_OUTSIDE)
            filenames = CUT_SIDE_ASSET_FILENAMES.get(
                side,
                CUT_SIDE_ASSET_FILENAMES[CUT_SIDE_OUTSIDE],
            )
            pixmap = self._load_asset_pixmap(filenames)
        if pixmap is None or pixmap.isNull():
            return False
        max_width = max(1, self.width() - 2)
        max_height = max(1, self.height() - 2)
        scaled = pixmap.scaled(
            max_width,
            max_height,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        left = int((self.width() - scaled.width()) / 2)
        top = int((self.height() - scaled.height()) / 2)
        painter.drawPixmap(left, top, scaled)
        if self.kind == "cut":
            self._paint_cut_direction_overlay(
                painter,
                QtCore.QRectF(left, top, scaled.width(), scaled.height()),
            )
        return True

    def _paint_cut_direction_overlay(self, painter, rect):
        state = self._state()
        side = state.get("side", CUT_SIDE_OUTSIDE)
        climb = state.get("climb", True)
        clockwise = bool(climb) if side != CUT_SIDE_INSIDE else not bool(climb)
        path = rect.adjusted(
            rect.width() * 0.14,
            rect.height() * 0.18,
            -rect.width() * 0.14,
            -rect.height() * 0.18,
        )
        if clockwise:
            self._arrow(
                painter,
                QtCore.QPointF(path.left() + 9, path.top()),
                QtCore.QPointF(path.right() - 10, path.top()),
            )
            self._arrow(
                painter,
                QtCore.QPointF(path.right() - 9, path.bottom()),
                QtCore.QPointF(path.left() + 10, path.bottom()),
            )
        else:
            self._arrow(
                painter,
                QtCore.QPointF(path.right() - 9, path.top()),
                QtCore.QPointF(path.left() + 10, path.top()),
            )
            self._arrow(
                painter,
                QtCore.QPointF(path.left() + 9, path.bottom()),
                QtCore.QPointF(path.right() - 10, path.bottom()),
            )

    def _state(self):
        try:
            return dict(self.state_provider() or {})
        except Exception:
            return {}

    def _pen(self, color, width=1.3, dashed=False):
        pen = QtGui.QPen(QtGui.QColor(color))
        pen.setWidthF(float(width))
        if dashed:
            pen.setStyle(QtCore.Qt.DashLine)
        return pen

    def _arrow(self, painter, start, end, color="#2563eb"):
        painter.setPen(self._pen(color, 1.4))
        painter.drawLine(start, end)
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        size = 5.0
        for offset in (2.55, -2.55):
            painter.drawLine(
                end,
                QtCore.QPointF(
                    end.x() + math.cos(angle + offset) * size,
                    end.y() + math.sin(angle + offset) * size,
                ),
            )

    def _draw_board(self, painter, rect):
        top = QtGui.QPolygonF(
            [
                QtCore.QPointF(rect.left() + 12, rect.top() - 10),
                QtCore.QPointF(rect.right() + 12, rect.top() - 10),
                QtCore.QPointF(rect.right(), rect.top()),
                QtCore.QPointF(rect.left(), rect.top()),
            ]
        )
        side = QtGui.QPolygonF(
            [
                QtCore.QPointF(rect.right(), rect.top()),
                QtCore.QPointF(rect.right() + 12, rect.top() - 10),
                QtCore.QPointF(rect.right() + 12, rect.bottom() - 10),
                QtCore.QPointF(rect.right(), rect.bottom()),
            ]
        )
        painter.setBrush(QtGui.QColor("#f6c06b"))
        painter.setPen(self._pen("#92400e", 1.0))
        painter.drawPolygon(top)
        painter.setBrush(QtGui.QColor("#c48642"))
        painter.drawPolygon(side)
        painter.setBrush(QtGui.QColor("#d99a50"))
        painter.drawRect(rect)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        except Exception:
            pass
        if self._paint_asset(painter):
            painter.end()
            return
        if self.kind == "job_type":
            self._paint_job_type(painter)
        elif self.kind == "job_size":
            self._paint_job_size(painter)
        elif self.kind == "z_zero":
            self._paint_z_zero(painter)
        elif self.kind == "origin":
            self._paint_origin(painter)
        elif self.kind == "home":
            self._paint_home(painter)
        elif self.kind == "material":
            self._paint_material(painter)
        elif self.kind == "safety":
            self._paint_safety(painter)
        elif self.kind == "cut":
            self._paint_cut(painter)
        elif self.kind == "depth":
            self._paint_depth(painter)
        elif self.kind == "tool":
            self._paint_tool(painter)
        elif self.kind == "ramp":
            self._paint_ramp(painter)
        elif self.kind == "corner":
            self._paint_corner(painter)
        elif self.kind == "holes":
            self._paint_holes(painter)
        elif self.kind == "helix":
            self._paint_helix(painter)
        elif self.kind == "peck":
            self._paint_peck(painter)
        elif self.kind == "dwell":
            self._paint_dwell(painter)
        elif self.kind == "order":
            self._paint_order(painter)
        elif self.kind == "pocket":
            self._paint_pocket(painter)
        elif self.kind == "simulation":
            self._paint_simulation(painter)
        elif self.kind == "export":
            self._paint_export(painter)
        elif self.kind == "flow":
            self._paint_flow(painter)
        else:
            self._paint_home(painter)
        painter.end()

    def _paint_job_type(self, painter):
        self._draw_board(painter, QtCore.QRectF(19, 27, 48, 24))
        self._arrow(
            painter,
            QtCore.QPointF(27, 23),
            QtCore.QPointF(52, 15),
            "#334155",
        )

    def _paint_job_size(self, painter):
        rect = QtCore.QRectF(18, 27, 52, 24)
        self._draw_board(painter, rect)
        self._arrow(
            painter,
            QtCore.QPointF(rect.left(), rect.bottom() + 8),
            QtCore.QPointF(rect.right(), rect.bottom() + 8),
            "#2563eb",
        )
        self._arrow(
            painter,
            QtCore.QPointF(rect.left() - 8, rect.bottom()),
            QtCore.QPointF(rect.left() - 8, rect.top()),
            "#16a34a",
        )

    def _paint_z_zero(self, painter):
        mode = self._state().get("mode", "material_surface")
        rect = QtCore.QRectF(28, 18, 38, 34)
        self._draw_board(painter, rect)
        dot_y = rect.top() if mode == "material_surface" else rect.bottom()
        painter.setBrush(QtGui.QColor("#ef4444"))
        painter.setPen(self._pen("#991b1b", 1.0))
        painter.drawEllipse(QtCore.QPointF(rect.left(), dot_y), 4, 4)
        self._arrow(
            painter,
            QtCore.QPointF(13, dot_y),
            QtCore.QPointF(rect.left() - 3, dot_y),
            "#ef4444",
        )

    def _paint_origin(self, painter):
        anchor = self._state().get("anchor", "bottom_left")
        selected_row = selected_col = 0
        for key, row, col in ORIGIN_ANCHORS:
            if key == anchor:
                selected_row, selected_col = row, col
                break
        board = QtCore.QRectF(18, 18, 70, 40)
        top = QtGui.QPolygonF(
            [
                QtCore.QPointF(board.left() + 8, board.top() - 7),
                QtCore.QPointF(board.right() + 8, board.top() - 7),
                QtCore.QPointF(board.right(), board.top()),
                QtCore.QPointF(board.left(), board.top()),
            ]
        )
        painter.setBrush(QtGui.QColor("#f2c982"))
        painter.setPen(self._pen("#b7791f", 1.0))
        painter.drawPolygon(top)
        painter.setBrush(QtGui.QColor("#e5ad61"))
        painter.drawRect(board)
        painter.setPen(self._pen("#8b5e34", 0.9, dashed=True))
        painter.drawLine(
            QtCore.QPointF(board.center().x(), board.top() + 4),
            QtCore.QPointF(board.center().x(), board.bottom() - 4),
        )
        painter.drawLine(
            QtCore.QPointF(board.left() + 7, board.center().y()),
            QtCore.QPointF(board.right() - 7, board.center().y()),
        )
        anchor_x = board.left() + (board.width() * selected_col / 2.0)
        anchor_y = board.top() + (board.height() * selected_row / 2.0)
        painter.setBrush(QtGui.QColor("#ef4444"))
        painter.setPen(self._pen("#991b1b", 1.0))
        painter.drawEllipse(QtCore.QPointF(anchor_x, anchor_y), 4.2, 4.2)

    def _paint_home(self, painter):
        self._draw_board(painter, QtCore.QRectF(28, 29, 38, 18))
        painter.setPen(self._pen("#334155", 1.2))
        painter.setBrush(QtGui.QColor("#f59e0b"))
        painter.drawRect(QtCore.QRectF(43, 5, 6, 25))
        self._arrow(
            painter,
            QtCore.QPointF(13, 54),
            QtCore.QPointF(33, 54),
            "#2563eb",
        )
        self._arrow(
            painter,
            QtCore.QPointF(13, 54),
            QtCore.QPointF(13, 34),
            "#16a34a",
        )

    def _paint_material(self, painter):
        rect = QtCore.QRectF(24, 25, 42, 24)
        self._draw_board(painter, rect)
        painter.setPen(self._pen("#334155", 1.0))
        painter.drawLine(QtCore.QPointF(75, 16), QtCore.QPointF(75, 50))
        painter.drawLine(QtCore.QPointF(70, 16), QtCore.QPointF(80, 16))
        painter.drawLine(QtCore.QPointF(70, 50), QtCore.QPointF(80, 50))

    def _paint_safety(self, painter):
        rect = QtCore.QRectF(22, 41, 48, 12)
        self._draw_board(painter, rect)
        painter.setBrush(QtGui.QColor("#f59e0b"))
        painter.setPen(self._pen("#92400e", 1.0))
        painter.drawRect(QtCore.QRectF(45, 7, 6, 31))
        self._arrow(
            painter,
            QtCore.QPointF(67, 41),
            QtCore.QPointF(67, 19),
            "#16a34a",
        )

    def _paint_cut(self, painter):
        state = self._state()
        side = state.get("side", CUT_SIDE_OUTSIDE)
        climb = state.get("climb", True)
        part = QtCore.QRectF(15, 12, 76, 56)
        painter.setBrush(QtGui.QColor("#dbeafe"))
        painter.setPen(self._pen("#334155", 1.4))
        painter.drawRect(part)
        offset = {
            CUT_SIDE_OUTSIDE: -5.0,
            CUT_SIDE_ON_LINE: 0.0,
            CUT_SIDE_INSIDE: 5.0,
        }.get(side, -5.0)
        path = part.adjusted(offset, offset, -offset, -offset)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(self._pen("#2563eb", 1.7, dashed=True))
        painter.drawRect(path)
        clockwise = bool(climb) if side != CUT_SIDE_INSIDE else not bool(climb)
        if clockwise:
            self._arrow(
                painter,
                QtCore.QPointF(path.left() + 10, path.top()),
                QtCore.QPointF(path.right() - 11, path.top()),
            )
            self._arrow(
                painter,
                QtCore.QPointF(path.right(), path.top() + 10),
                QtCore.QPointF(path.right(), path.bottom() - 10),
            )
        else:
            self._arrow(
                painter,
                QtCore.QPointF(path.right() - 10, path.top()),
                QtCore.QPointF(path.left() + 11, path.top()),
            )
            self._arrow(
                painter,
                QtCore.QPointF(path.right(), path.bottom() - 10),
                QtCore.QPointF(path.right(), path.top() + 10),
            )
        painter.setBrush(QtGui.QColor("#f59e0b"))
        painter.setPen(self._pen("#92400e", 1.0))
        painter.drawEllipse(QtCore.QPointF(path.right(), path.center().y()), 5, 5)

    def _paint_depth(self, painter):
        rect = QtCore.QRectF(25, 35, 42, 16)
        self._draw_board(painter, rect)
        painter.setBrush(QtGui.QColor("#f59e0b"))
        painter.setPen(self._pen("#92400e", 1.0))
        painter.drawRect(QtCore.QRectF(43, 7, 7, 30))
        painter.setPen(self._pen("#ef4444", 2.0))
        painter.drawLine(QtCore.QPointF(17, 51), QtCore.QPointF(76, 51))

    def _paint_tool(self, painter):
        painter.setPen(self._pen("#64748b", 1.1))
        painter.setBrush(QtGui.QColor("#e5e7eb"))
        painter.drawRoundedRect(QtCore.QRectF(39, 5, 15, 38), 3, 3)
        tip = QtGui.QPolygonF(
            [
                QtCore.QPointF(39, 43),
                QtCore.QPointF(54, 43),
                QtCore.QPointF(46.5, 56),
            ]
        )
        painter.drawPolygon(tip)
        painter.setPen(self._pen("#334155", 1.0))
        painter.drawLine(QtCore.QPointF(30, 16), QtCore.QPointF(63, 16))
        painter.drawLine(QtCore.QPointF(30, 11), QtCore.QPointF(30, 21))
        painter.drawLine(QtCore.QPointF(63, 11), QtCore.QPointF(63, 21))

    def _paint_ramp(self, painter):
        rect = QtCore.QRectF(20, 41, 52, 12)
        self._draw_board(painter, rect)
        painter.setPen(self._pen("#2563eb", 2.0))
        self._arrow(
            painter,
            QtCore.QPointF(19, 52),
            QtCore.QPointF(70, 24),
            "#2563eb",
        )
        painter.setBrush(QtGui.QColor("#f59e0b"))
        painter.setPen(self._pen("#92400e", 1.0))
        painter.drawEllipse(QtCore.QPointF(70, 24), 4, 4)

    def _paint_corner(self, painter):
        painter.setPen(self._pen("#2563eb", 2.0))
        painter.drawLine(QtCore.QPointF(20, 45), QtCore.QPointF(50, 45))
        painter.setPen(self._pen("#a855f7", 3.0))
        painter.drawLine(QtCore.QPointF(50, 45), QtCore.QPointF(50, 18))
        painter.setPen(self._pen("#2563eb", 2.0))
        self._arrow(
            painter,
            QtCore.QPointF(50, 18),
            QtCore.QPointF(72, 18),
            "#2563eb",
        )

    def _paint_holes(self, painter):
        state = self._state()
        rect = QtCore.QRectF(21, 39, 50, 14)
        self._draw_board(painter, rect)
        painter.setBrush(QtGui.QColor("#f59e0b"))
        painter.setPen(self._pen("#92400e", 1.0))
        painter.drawRect(QtCore.QRectF(43, 6, 7, 36))
        painter.setPen(self._pen("#2563eb", 1.5))
        if state.get("helical", True):
            points = [
                QtCore.QPointF(46.5 + math.sin(i / 24.0 * math.pi * 5) * 9, 12 + i * 1.6)
                for i in range(25)
            ]
            painter.drawPolyline(QtGui.QPolygonF(points))
        else:
            self._arrow(painter, QtCore.QPointF(60, 10), QtCore.QPointF(60, 50))

    def _paint_helix(self, painter):
        painter.setBrush(QtGui.QColor("#f59e0b"))
        painter.setPen(self._pen("#92400e", 1.0))
        painter.drawRect(QtCore.QRectF(43, 5, 7, 50))
        painter.setPen(self._pen("#2563eb", 1.6))
        points = [
            QtCore.QPointF(46.5 + math.sin(i / 32.0 * math.pi * 7) * 13, 8 + i * 1.4)
            for i in range(33)
        ]
        painter.drawPolyline(QtGui.QPolygonF(points))

    def _paint_peck(self, painter):
        painter.setBrush(QtGui.QColor("#f59e0b"))
        painter.setPen(self._pen("#92400e", 1.0))
        painter.drawRect(QtCore.QRectF(31, 6, 7, 45))
        for top, bottom in ((11, 25), (22, 39), (34, 55)):
            self._arrow(
                painter,
                QtCore.QPointF(58, top),
                QtCore.QPointF(58, bottom),
                "#2563eb",
            )

    def _paint_dwell(self, painter):
        self._draw_board(painter, QtCore.QRectF(24, 38, 44, 14))
        painter.setBrush(QtGui.QColor("#f59e0b"))
        painter.setPen(self._pen("#92400e", 1.0))
        painter.drawRect(QtCore.QRectF(43, 8, 7, 34))
        painter.setPen(self._pen("#ef4444", 2.0))
        painter.drawArc(QtCore.QRectF(57, 22, 18, 18), 30 * 16, 290 * 16)

    def _paint_order(self, painter):
        points = [
            QtCore.QPointF(20, 45),
            QtCore.QPointF(35, 24),
            QtCore.QPointF(56, 33),
            QtCore.QPointF(73, 15),
        ]
        painter.setPen(self._pen("#2563eb", 1.3, dashed=True))
        for start, end in zip(points, points[1:]):
            self._arrow(painter, start, end, "#2563eb")
        painter.setBrush(QtGui.QColor("#ffffff"))
        painter.setPen(self._pen("#64748b", 1.0))
        for point in points:
            painter.drawEllipse(point, 4, 4)

    def _paint_pocket(self, painter):
        state = self._state()
        strategy = state.get("strategy", "offset")
        part = QtCore.QRectF(18, 13, 56, 38)
        painter.setBrush(QtGui.QColor("#fef3c7"))
        painter.setPen(self._pen("#334155", 1.2))
        painter.drawRect(part)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(self._pen("#2563eb", 1.4))
        if strategy == "raster":
            for row, y_value in enumerate(range(21, 49, 8)):
                if row % 2:
                    self._arrow(painter, QtCore.QPointF(66, y_value), QtCore.QPointF(26, y_value))
                else:
                    self._arrow(painter, QtCore.QPointF(26, y_value), QtCore.QPointF(66, y_value))
        else:
            for inset in (6, 12, 18):
                painter.drawRect(part.adjusted(inset, inset, -inset, -inset))
            if state.get("climb", True):
                self._arrow(painter, QtCore.QPointF(65, 20), QtCore.QPointF(30, 20))
            else:
                self._arrow(painter, QtCore.QPointF(30, 20), QtCore.QPointF(65, 20))

    def _paint_simulation(self, painter):
        painter.setBrush(QtGui.QColor("#dbeafe"))
        painter.setPen(self._pen("#334155", 1.2))
        painter.drawRoundedRect(QtCore.QRectF(18, 17, 36, 30), 4, 4)
        painter.setBrush(QtGui.QColor("#2563eb"))
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawPolygon(
            QtGui.QPolygonF(
                [
                    QtCore.QPointF(31, 24),
                    QtCore.QPointF(31, 40),
                    QtCore.QPointF(44, 32),
                ]
            )
        )
        self._arrow(painter, QtCore.QPointF(56, 32), QtCore.QPointF(75, 32), "#f59e0b")

    def _paint_export(self, painter):
        painter.setBrush(QtGui.QColor("#ffffff"))
        painter.setPen(self._pen("#64748b", 1.1))
        painter.drawRect(QtCore.QRectF(26, 9, 38, 47))
        for y_value in (21, 31, 41):
            painter.drawLine(QtCore.QPointF(34, y_value), QtCore.QPointF(56, y_value))
        self._arrow(painter, QtCore.QPointF(67, 43), QtCore.QPointF(78, 54), "#16a34a")

    def _paint_flow(self, painter):
        for x_value, color in ((15, "#f6c06b"), (39, "#dbeafe"), (63, "#dcfce7")):
            painter.setBrush(QtGui.QColor(color))
            painter.setPen(self._pen("#64748b", 1.0))
            painter.drawRoundedRect(QtCore.QRectF(x_value, 22, 16, 16), 3, 3)
        self._arrow(painter, QtCore.QPointF(31, 30), QtCore.QPointF(39, 30), "#64748b")
        self._arrow(painter, QtCore.QPointF(55, 30), QtCore.QPointF(63, 30), "#64748b")


class ToolTypeDiagram(QtWidgets.QWidget):
    """Imagem didática do tipo de fresa selecionado no cadastro."""

    def __init__(self, parent=None):
        super(ToolTypeDiagram, self).__init__(parent)
        self.tool_type = "end_mill"
        self.setFixedSize(150, 190)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent; border: none;")

    def set_tool_type(self, tool_type):
        self.tool_type = tool_type if tool_type in TOOL_TYPE_DEFINITIONS else "end_mill"
        self.update()

    @classmethod
    def _asset_directories(cls):
        directories = []
        configured = os.environ.get("WOODCAM2D_DIAGRAM_PATH", "").strip()
        if configured:
            directories.extend(
                Path(part).expanduser()
                for part in configured.split(os.pathsep)
                if part.strip()
            )
        base = Path(__file__).resolve().parent
        directories.extend(
            [
                base / "resources" / "diagrams",
                base / "resources" / "diagrams" / "source",
                base / "resources" / "icons",
            ]
        )
        return directories

    @classmethod
    def _pixmap_from_filenames(cls, filenames):
        for directory in cls._asset_directories():
            for filename in filenames:
                path = directory / filename
                if not path.is_file():
                    continue
                pixmap = QtGui.QPixmap(str(path))
                if not pixmap.isNull():
                    return _trim_transparent_pixmap(pixmap, padding=2)
        return None

    @classmethod
    def tool_pixmap(cls, tool_type):
        tool_type = tool_type if tool_type in TOOL_TYPE_DEFINITIONS else "end_mill"
        generic_names = (
            f"tool_{tool_type}.png",
            f"{tool_type}.png",
            f"{tool_type}.jpg",
            f"{tool_type}.jpeg",
            f"{tool_type}.webp",
        )
        return cls._pixmap_from_filenames(
            tuple(TOOL_TYPE_IMAGE_FILENAMES.get(tool_type, ())) + generic_names
        )

    @classmethod
    def tool_icon_pixmap(cls, tool_type):
        tool_type = tool_type if tool_type in TOOL_TYPE_DEFINITIONS else "end_mill"
        generic_names = (
            f"tool_icon_{tool_type}.png",
            f"icon_{tool_type}.png",
            f"{tool_type}_icon.png",
        )
        return cls._pixmap_from_filenames(
            tuple(TOOL_TYPE_ICON_FILENAMES.get(tool_type, ())) + generic_names
        ) or cls.tool_pixmap(tool_type)

    @classmethod
    def tool_icon(cls, tool_type, size=28):
        pixmap = cls.tool_icon_pixmap(tool_type)
        if pixmap is None or pixmap.isNull():
            return QtGui.QIcon()
        scaled = pixmap.scaled(
            QtCore.QSize(size, size),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        return QtGui.QIcon(scaled)

    def _pen(self, color, width=1.2, dashed=False):
        pen = QtGui.QPen(QtGui.QColor(color))
        pen.setWidthF(float(width))
        if dashed:
            pen.setStyle(QtCore.Qt.DashLine)
        return pen

    def _arrow(self, painter, start, end, color="#2563eb"):
        painter.setPen(self._pen(color, 1.4))
        painter.drawLine(start, end)
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        for offset in (2.55, -2.55):
            painter.drawLine(
                end,
                QtCore.QPointF(
                    end.x() + math.cos(angle + offset) * 5.0,
                    end.y() + math.sin(angle + offset) * 5.0,
                ),
            )

    def _draw_dimension(self, painter, x_left, x_right, y_value, label):
        painter.setPen(self._pen("#334155", 1.0))
        painter.drawLine(QtCore.QPointF(x_left, y_value), QtCore.QPointF(x_right, y_value))
        painter.drawLine(QtCore.QPointF(x_left, y_value - 5), QtCore.QPointF(x_left, y_value + 5))
        painter.drawLine(QtCore.QPointF(x_right, y_value - 5), QtCore.QPointF(x_right, y_value + 5))
        painter.drawText(QtCore.QRectF(x_left, y_value + 3, x_right - x_left, 20), QtCore.Qt.AlignCenter, label)

    def _draw_board(self, painter):
        painter.setBrush(QtGui.QColor("#d99a50"))
        painter.setPen(self._pen("#92400e", 1.0))
        painter.drawRoundedRect(QtCore.QRectF(28, 139, 94, 24), 3, 3)
        painter.setPen(self._pen("#b45309", 0.8))
        for y_value in (147, 155):
            painter.drawLine(QtCore.QPointF(32, y_value), QtCore.QPointF(118, y_value))

    def _draw_spiral(self, painter, x_center, top, bottom, color="#64748b", reverse=False):
        painter.setPen(self._pen(color, 1.2))
        points = []
        total = 34
        for index in range(total):
            ratio = index / float(total - 1)
            phase = ratio * math.pi * 5.0
            if reverse:
                phase = -phase
            points.append(
                QtCore.QPointF(
                    x_center + math.sin(phase) * 10.0,
                    top + (bottom - top) * ratio,
                )
            )
        painter.drawPolyline(QtGui.QPolygonF(points))

    def _draw_shank(self, painter, x_center=75, top=20, bottom=71, width=24):
        painter.setBrush(QtGui.QColor("#cbd5e1"))
        painter.setPen(self._pen("#64748b", 1.0))
        painter.drawRoundedRect(
            QtCore.QRectF(x_center - width / 2.0, top, width, bottom - top),
            4,
            4,
        )

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        except Exception:
            pass
        definition = TOOL_TYPE_DEFINITIONS.get(self.tool_type, TOOL_TYPE_DEFINITIONS["end_mill"])
        painter.setPen(self._pen("#1e3a8a", 1.0))
        painter.drawText(QtCore.QRectF(4, 2, 142, 20), QtCore.Qt.AlignCenter, definition["short_label"])
        pixmap = self.tool_pixmap(self.tool_type)
        if pixmap is not None and not pixmap.isNull():
            target_size = QtCore.QSize(130, 140)
            scaled = pixmap.scaled(
                target_size,
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
            x_value = int((self.width() - scaled.width()) / 2)
            y_value = int(24 + (140 - scaled.height()) / 2)
            painter.drawPixmap(x_value, y_value, scaled)
            if self.tool_type in {"end_mill", "ball_nose", "drill", "compression"}:
                self._draw_dimension(painter, 58, 92, 172, "D")
            elif self.tool_type == "v_bit":
                painter.setPen(self._pen("#334155", 1.0))
                painter.drawText(QtCore.QRectF(46, 168, 58, 18), QtCore.Qt.AlignCenter, "Ângulo")
            else:
                self._draw_dimension(painter, 44, 106, 172, "D")
            painter.end()
            return
        self._draw_shank(painter)
        self._draw_board(painter)

        if self.tool_type == "v_bit":
            painter.setBrush(QtGui.QColor("#d6a335"))
            painter.setPen(self._pen("#8a5a12", 1.1))
            painter.drawRect(QtCore.QRectF(66, 64, 18, 27))
            painter.drawPolygon(
                QtGui.QPolygonF(
                    [
                        QtCore.QPointF(57, 91),
                        QtCore.QPointF(93, 91),
                        QtCore.QPointF(75, 133),
                    ]
                )
            )
            painter.setPen(self._pen("#ef4444", 1.3))
            painter.drawArc(QtCore.QRectF(56, 93, 38, 38), 35 * 16, 110 * 16)
            painter.drawText(QtCore.QRectF(98, 103, 45, 20), QtCore.Qt.AlignLeft, "90°")
        elif self.tool_type == "drill":
            painter.setBrush(QtGui.QColor("#d6a335"))
            painter.setPen(self._pen("#8a5a12", 1.1))
            painter.drawRect(QtCore.QRectF(65, 65, 20, 56))
            self._draw_spiral(painter, 75, 68, 120, "#8a5a12")
            painter.drawPolygon(
                QtGui.QPolygonF(
                    [
                        QtCore.QPointF(65, 121),
                        QtCore.QPointF(85, 121),
                        QtCore.QPointF(75, 137),
                    ]
                )
            )
            self._arrow(painter, QtCore.QPointF(105, 69), QtCore.QPointF(105, 132), "#ef4444")
        elif self.tool_type == "ball_nose":
            painter.setBrush(QtGui.QColor("#d1d5db"))
            painter.setPen(self._pen("#64748b", 1.1))
            painter.drawRect(QtCore.QRectF(63, 64, 24, 58))
            self._draw_spiral(painter, 75, 67, 120)
            painter.drawEllipse(QtCore.QRectF(63, 111, 24, 24))
            painter.setBrush(QtGui.QColor("#ffffff"))
            painter.setPen(QtCore.Qt.NoPen)
            painter.drawRect(QtCore.QRectF(62, 110, 26, 12))
        elif self.tool_type == "compression":
            painter.setBrush(QtGui.QColor("#d1d5db"))
            painter.setPen(self._pen("#64748b", 1.1))
            painter.drawRect(QtCore.QRectF(63, 64, 24, 70))
            self._draw_spiral(painter, 75, 66, 99, "#2563eb")
            self._draw_spiral(painter, 75, 99, 132, "#16a34a", reverse=True)
            self._arrow(painter, QtCore.QPointF(101, 82), QtCore.QPointF(101, 64), "#2563eb")
            self._arrow(painter, QtCore.QPointF(101, 115), QtCore.QPointF(101, 136), "#16a34a")
        elif self.tool_type == "surfacing":
            painter.setBrush(QtGui.QColor("#cbd5e1"))
            painter.setPen(self._pen("#64748b", 1.1))
            painter.drawRoundedRect(QtCore.QRectF(48, 76, 54, 30), 4, 4)
            painter.drawRect(QtCore.QRectF(56, 104, 38, 22))
            for x_value in (59, 75, 91):
                painter.drawLine(QtCore.QPointF(x_value, 126), QtCore.QPointF(x_value - 9, 138))
            self._arrow(painter, QtCore.QPointF(43, 132), QtCore.QPointF(116, 132), "#2563eb")
        else:
            painter.setBrush(QtGui.QColor("#d1d5db"))
            painter.setPen(self._pen("#64748b", 1.1))
            painter.drawRect(QtCore.QRectF(63, 64, 24, 70))
            self._draw_spiral(painter, 75, 66, 132)
            painter.drawLine(QtCore.QPointF(63, 134), QtCore.QPointF(87, 134))

        self._draw_dimension(painter, 62, 88, 172, "D")
        painter.end()


class ToolPreviewWidget(QtWidgets.QWidget):
    """Prévia compacta com a foto real da fresa selecionada."""

    def __init__(self, parent=None):
        super(ToolPreviewWidget, self).__init__(parent)
        self.tool_type = "end_mill"
        self.setFixedSize(42, 72)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent; border: none;")

    def set_tool_type(self, tool_type):
        self.tool_type = tool_type if tool_type in TOOL_TYPE_DEFINITIONS else "end_mill"
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        except Exception:
            pass
        pixmap = ToolTypeDiagram.tool_pixmap(self.tool_type)
        if pixmap is None or pixmap.isNull():
            pixmap = ToolTypeDiagram.tool_icon_pixmap(self.tool_type)
        if pixmap is not None and not pixmap.isNull():
            scaled = pixmap.scaled(
                QtCore.QSize(self.width() - 10, self.height() - 10),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
            painter.drawPixmap(
                int((self.width() - scaled.width()) / 2),
                int((self.height() - scaled.height()) / 2),
                scaled,
            )
        painter.end()


class DialogDragHandle(QtWidgets.QFrame):
    """Alça interna para mover a janela em ambientes sem barra nativa."""

    def __init__(self, dialog, parent=None):
        super(DialogDragHandle, self).__init__(parent)
        self.dialog = dialog
        self._drag_start_global = None
        self._drag_start_pos = None
        self.setFixedHeight(8)
        self.setCursor(QtCore.Qt.SizeAllCursor)
        self.setObjectName("WoodCAM2DDragHandle")
        self.setStyleSheet(
            "#WoodCAM2DDragHandle {"
            "background: transparent;"
            "border: none;"
            "}"
        )
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

    def _event_global_pos(self, event):
        if hasattr(event, "globalPosition"):
            try:
                return event.globalPosition().toPoint()
            except Exception:
                pass
        return event.globalPos()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_start_global = self._event_global_pos(event)
            self._drag_start_pos = self.dialog.pos()
            try:
                self.grabMouse()
            except Exception:
                pass
            event.accept()
            return
        super(DialogDragHandle, self).mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_start_global is not None
            and event.buttons() & QtCore.Qt.LeftButton
        ):
            current = self._event_global_pos(event)
            self.dialog.move(
                self._drag_start_pos + (current - self._drag_start_global)
            )
            try:
                self.dialog._user_moved_dialog = True
            except Exception:
                pass
            event.accept()
            return
        super(DialogDragHandle, self).mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_start_global = None
            self._drag_start_pos = None
            try:
                self.releaseMouse()
            except Exception:
                pass
            event.accept()
            return
        super(DialogDragHandle, self).mouseReleaseEvent(event)


class DialogResizeGrip(QtWidgets.QWidget):
    """Alça de redimensionamento para o painel-filho do FreeCAD."""

    def __init__(self, dialog):
        super(DialogResizeGrip, self).__init__(dialog)
        self.dialog = dialog
        self._start_global = None
        self._start_size = None
        self.setFixedSize(18, 18)
        self.setCursor(QtCore.Qt.SizeFDiagCursor)
        self.setToolTip("Arraste para redimensionar a janela")

    @staticmethod
    def _global_pos(event):
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._start_global = self._global_pos(event)
            self._start_size = self.dialog.size()
            event.accept()
            return
        super(DialogResizeGrip, self).mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._start_global is not None and event.buttons() & QtCore.Qt.LeftButton:
            delta = self._global_pos(event) - self._start_global
            self.dialog.resize(
                max(self.dialog.minimumWidth(), self._start_size.width() + delta.x()),
                max(self.dialog.minimumHeight(), self._start_size.height() + delta.y()),
            )
            event.accept()
            return
        super(DialogResizeGrip, self).mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._start_global = None
            self._start_size = None
            event.accept()
            return
        super(DialogResizeGrip, self).mouseReleaseEvent(event)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setPen(QtGui.QPen(QtGui.QColor("#64748b"), 1.2))
        for offset in (5, 9, 13):
            painter.drawPoint(offset, 15)
            painter.drawPoint(15, offset)
        painter.end()


class PassDepthWidget(QtWidgets.QWidget):
    """Barra visual de passagens em Z, inspirada no editor de passadas do Aspire."""

    def __init__(self, parent=None):
        super(PassDepthWidget, self).__init__(parent)
        self.total_depth = 0.0
        self.pass_count = 1
        self.pass_depths = []
        self.setFixedSize(68, 190)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent; border: none;")

    def set_passes(self, total_depth, pass_count, pass_depths=None):
        self.total_depth = max(0.0, float(total_depth or 0.0))
        self.pass_count = max(1, int(pass_count or 1))
        parsed_depths = []
        for value in pass_depths or []:
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0.0:
                parsed_depths.append(parsed)
        self.pass_depths = parsed_depths
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        except Exception:
            pass

        bar = QtCore.QRectF(22, 10, 24, 160)
        painter.setPen(QtGui.QPen(QtGui.QColor("#9a641d"), 1.0))
        painter.setBrush(QtGui.QColor("#e6aa5b"))
        painter.drawRoundedRect(bar, 3, 3)

        if self.total_depth > 0.0:
            painter.setPen(QtGui.QPen(QtGui.QColor("#dc2626"), 2.0))
            cumulative_depths = []
            if self.pass_depths:
                current = 0.0
                for pass_depth in self.pass_depths:
                    current = min(self.total_depth, current + pass_depth)
                    cumulative_depths.append(current)
            if not cumulative_depths:
                cumulative_depths = [
                    self.total_depth * index / float(self.pass_count)
                    for index in range(1, self.pass_count + 1)
                ]
            for pass_depth in cumulative_depths:
                ratio = pass_depth / self.total_depth
                y_value = bar.top() + bar.height() * ratio
                painter.drawLine(
                    QtCore.QPointF(bar.left() - 8, y_value),
                    QtCore.QPointF(bar.right() + 8, y_value),
                )

        painter.setPen(QtGui.QPen(QtGui.QColor("#334155"), 1.0))
        painter.drawText(
            QtCore.QRectF(0, 172, self.width(), 18),
            QtCore.Qt.AlignCenter,
            f"{len(self.pass_depths) or self.pass_count}x",
        )
        painter.end()


class TabPlacementWidget(QtWidgets.QWidget):
    """Editor visual de posições normalizadas de tabs no perímetro."""

    def __init__(self, parent=None):
        super(TabPlacementWidget, self).__init__(parent)
        self.positions = []
        self._drag_index = None
        self.on_change = None
        self.setMinimumSize(280, 185)
        self.setMouseTracking(True)

    def set_positions(self, positions):
        self.positions = sorted(
            set(round(float(position) % 1.0, 6) for position in positions or [])
        )
        self.update()

    def _path_rect(self):
        return QtCore.QRectF(30, 24, self.width() - 60, self.height() - 48)

    def _point_at(self, position):
        rect = self._path_rect()
        perimeter = 2.0 * (rect.width() + rect.height())
        distance_on_path = (float(position) % 1.0) * perimeter
        if distance_on_path <= rect.width():
            return QtCore.QPointF(rect.left() + distance_on_path, rect.top())
        distance_on_path -= rect.width()
        if distance_on_path <= rect.height():
            return QtCore.QPointF(rect.right(), rect.top() + distance_on_path)
        distance_on_path -= rect.height()
        if distance_on_path <= rect.width():
            return QtCore.QPointF(rect.right() - distance_on_path, rect.bottom())
        distance_on_path -= rect.width()
        return QtCore.QPointF(rect.left(), rect.bottom() - distance_on_path)

    def _position_at(self, point):
        rect = self._path_rect()
        candidates = (
            (abs(point.y() - rect.top()), max(rect.left(), min(rect.right(), point.x())), "top"),
            (abs(point.x() - rect.right()), max(rect.top(), min(rect.bottom(), point.y())), "right"),
            (abs(point.y() - rect.bottom()), max(rect.left(), min(rect.right(), point.x())), "bottom"),
            (abs(point.x() - rect.left()), max(rect.top(), min(rect.bottom(), point.y())), "left"),
        )
        _distance, value, edge = min(candidates, key=lambda item: item[0])
        perimeter = 2.0 * (rect.width() + rect.height())
        if edge == "top":
            path_distance = value - rect.left()
        elif edge == "right":
            path_distance = rect.width() + value - rect.top()
        elif edge == "bottom":
            path_distance = rect.width() + rect.height() + rect.right() - value
        else:
            path_distance = 2.0 * rect.width() + rect.height() + rect.bottom() - value
        return path_distance / perimeter

    def _nearest_index(self, point):
        if not self.positions:
            return None
        distances = [
            self._point_at(position).distanceToPoint(point)
            for position in self.positions
        ]
        nearest = min(range(len(distances)), key=lambda index: distances[index])
        return nearest if distances[nearest] <= 12.0 else None

    def _notify(self):
        self.positions = sorted(set(round(position % 1.0, 6) for position in self.positions))
        self.update()
        if self.on_change is not None:
            self.on_change(list(self.positions))

    def mousePressEvent(self, event):
        point = event.position() if hasattr(event, "position") else event.posF()
        index = self._nearest_index(point)
        if event.button() == QtCore.Qt.RightButton and index is not None:
            self.positions.pop(index)
            self._notify()
            event.accept()
            return
        if event.button() == QtCore.Qt.LeftButton:
            if index is None:
                self.positions.append(self._position_at(point))
                self._notify()
                index = self._nearest_index(point)
            self._drag_index = index
            event.accept()
            return
        super(TabPlacementWidget, self).mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_index is not None and event.buttons() & QtCore.Qt.LeftButton:
            point = event.position() if hasattr(event, "position") else event.posF()
            self.positions[self._drag_index] = self._position_at(point)
            self._notify()
            self._drag_index = self._nearest_index(point)
            event.accept()
            return
        super(TabPlacementWidget, self).mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_index = None
        super(TabPlacementWidget, self).mouseReleaseEvent(event)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = self._path_rect()
        painter.setBrush(QtGui.QColor("#f6c06b"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#92400e"), 1.3))
        painter.drawRect(rect)
        painter.setBrush(QtCore.Qt.NoBrush)
        pen = QtGui.QPen(QtGui.QColor("#2563eb"), 1.5)
        pen.setStyle(QtCore.Qt.DashLine)
        painter.setPen(pen)
        painter.drawRect(rect.adjusted(-4, -4, 4, 4))
        painter.setPen(QtGui.QPen(QtGui.QColor("#dc2626"), 2.2))
        for position in self.positions:
            point = self._point_at(position)
            painter.drawLine(
                QtCore.QPointF(point.x() - 8, point.y() - 8),
                QtCore.QPointF(point.x() + 8, point.y() + 8),
            )
            painter.drawLine(
                QtCore.QPointF(point.x() - 8, point.y() + 8),
                QtCore.QPointF(point.x() + 8, point.y() - 8),
            )
        painter.end()


class SimulationControlDialog(QtWidgets.QDialog):
    def __init__(self, owner, parent=None):
        super(SimulationControlDialog, self).__init__(parent)
        self.owner = owner
        self.setWindowTitle("WoodCAM 2D - Simulação")
        self.setMinimumWidth(260)
        self.setModal(False)

        layout = QtWidgets.QHBoxLayout(self)
        stop_button = QtWidgets.QPushButton("Parar simulação")
        stop_button.clicked.connect(self.owner.stop_simulation)
        layout.addWidget(stop_button)

        config_button = QtWidgets.QPushButton("Configurações")
        config_button.clicked.connect(self.owner.show_config_dialog)
        layout.addWidget(config_button)

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.owner.show_config_dialog()


class CompactOperationTabBar(QtWidgets.QTabBar):
    """Keep icon-only tabs as narrow as their artwork."""

    def _icon_paint_rect(self, index):
        """Return a rect centered in the whole tab, not in Qt's text area."""
        tab_rect = self.tabRect(index)
        icon_size = self.iconSize()
        return QtCore.QRect(
            tab_rect.center().x() - icon_size.width() // 2,
            tab_rect.center().y() - icon_size.height() // 2,
            icon_size.width(),
            icon_size.height(),
        )

    def tabSizeHint(self, index):
        size = super(CompactOperationTabBar, self).tabSizeHint(index)
        if self.tabText(index):
            return size
        icon_size = self.iconSize()
        return QtCore.QSize(
            # O estilo Fusion reserva área para foco, borda e estado ativo.
            # Menos de 12 px por lado faz o desenho invadir a aba vizinha.
            icon_size.width() + 24,
            max(size.height(), icon_size.height() + 10),
        )

    def paintEvent(self, event):
        """Paint icon-only tabs without Qt's invisible text reservation."""
        painter = QtWidgets.QStylePainter(self)
        for index in range(self.count()):
            option = QtWidgets.QStyleOptionTab()
            self.initStyleOption(option, index)
            if self.tabText(index):
                painter.drawControl(QtWidgets.QStyle.CE_TabBarTab, option)
                continue

            # Em algumas combinações FreeCAD/PySide, ``initStyleOption`` não
            # conserva o QIcon Python de uma aba com texto vazio. A própria
            # QTabBar continua sendo a fonte confiável para o desenho.
            icon = self.tabIcon(index)
            option.icon = QtGui.QIcon()
            option.text = ""
            painter.drawControl(QtWidgets.QStyle.CE_TabBarTabShape, option)
            mode = (
                QtGui.QIcon.Normal
                if self.isTabEnabled(index)
                else QtGui.QIcon.Disabled
            )
            state = (
                QtGui.QIcon.On
                if index == self.currentIndex()
                else QtGui.QIcon.Off
            )
            icon.paint(
                painter,
                self._icon_paint_rect(index),
                QtCore.Qt.AlignCenter,
                mode,
                state,
            )


class WoodCAM2DDialog(QtWidgets.QDialog):
    CUT_SIDE_BY_INDEX = (
        CUT_SIDE_OUTSIDE,
        CUT_SIDE_ON_LINE,
        CUT_SIDE_INSIDE,
    )

    def __init__(self, parent=None):
        super(WoodCAM2DDialog, self).__init__(parent)
        self.setObjectName("WoodCAM2DDialog")
        self.setWindowTitle("WoodCAM 2D - CNC Marcenaria")
        if parent is not None:
            # No Wayland/Niri, mover uma janela top-level pelo aplicativo é
            # bloqueado pelo compositor. Como painel-filho do FreeCAD, o Qt
            # controla a posição e a alça de arraste funciona de verdade.
            flags = QtCore.Qt.Widget
        else:
            flags = (
                QtCore.Qt.Dialog
                | QtCore.Qt.WindowTitleHint
                | QtCore.Qt.WindowSystemMenuHint
                | QtCore.Qt.WindowMinimizeButtonHint
                | QtCore.Qt.WindowCloseButtonHint
            )
        self.setWindowFlags(flags)
        try:
            self.setWindowModality(QtCore.Qt.NonModal)
        except Exception:
            pass
        self.setSizeGripEnabled(False)
        self.setAutoFillBackground(False)
        try:
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
            self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        except Exception:
            pass
        self.setMinimumWidth(680)
        self.setModal(False)
        self.fields = {}
        self.field_labels = {}
        self.operation_fields = {}
        self.operation_field_labels = {}
        self.operation_names = {}
        self.operation_tool_combos = {}
        self.operation_tool_previews = {}
        self.operation_pass_labels = {}
        self.operation_ramp_checks = {}
        self.operation_diagrams = {}
        self.setup_diagrams = []
        self.cut_tab_positions = []
        self._tab_marker_callback = None
        self._tab_marker_geometry = None
        self.tool_database = self._load_tool_database()
        self.tool_order = self._load_tool_order()
        self._populating_tool_list = False
        self.tool_editor_fields = {}
        self.last_operation_mode = "cut"
        self.sim_timer = None
        self.simulation_state = None
        self.sim_control = None
        self.last_preview_settings = None
        self.last_preview_moves = None
        self.last_applied_settings = None
        self.last_applied_moves = None
        self._toolpath_view_valid = False
        self._invalidating_toolpath_view = False
        self._editing_operation = None
        self._current_document_key = None
        self._document_tab_indices = {}
        self._drag_start_global = None
        self._drag_start_pos = None
        self._dialog_dragging = False
        self._user_moved_dialog = False
        self._positioned_once = False
        self._restoring_operation_preferences = False
        self._loading_job_preferences = False
        self._pass_schedules = {}
        self._preview_hidden_sources = []
        self._vector_editor_document = None
        self._vector_editor_store = None
        self._vector_editor_session = None
        self._vector_editor_load_error = None
        self._vector_editor_bound_freecad_document = None
        self._vector_editor_memory_cache = {}
        self._vector_editor_switching = False
        self._vector_editor_binding_token = None
        self._vector_editor_area_edit_pending = False
        self._vector_editor_area_edit_binding_token = None
        self._vector_editor_area_edit_document = None
        self._vector_validation_dialog = None
        self._use_vector_editor_for_cam = False
        self._vector_editor_feature_fingerprint = None
        self._detached_editor_window = None
        self._reattaching_vector_editor = False
        self._setup_preview_refresh_timer = QtCore.QTimer(self)
        self._setup_preview_refresh_timer.setSingleShot(True)
        self._setup_preview_refresh_timer.timeout.connect(
            self._refresh_setup_preview_if_possible
        )
        self._build_ui()
        self._vector_editor_sync_timer = QtCore.QTimer(self)
        self._vector_editor_sync_timer.setInterval(350)
        self._vector_editor_sync_timer.timeout.connect(
            self._sync_vector_editor_from_freecad_history
        )
        self._vector_editor_sync_timer.start()
        self._enable_dialog_dragging()
        self.resize(860, 840)

    def _event_global_pos(self, event):
        if hasattr(event, "globalPosition"):
            try:
                return event.globalPosition().toPoint()
            except Exception:
                pass
        return event.globalPos()

    def _is_drag_blocked_widget(self, widget):
        blocked_types = (
            QtWidgets.QLineEdit,
            QtWidgets.QTextEdit,
            QtWidgets.QPlainTextEdit,
            QtWidgets.QComboBox,
            QtWidgets.QAbstractButton,
            QtWidgets.QAbstractSpinBox,
            QtWidgets.QSlider,
            QtWidgets.QScrollBar,
            QtWidgets.QAbstractItemView,
            QtWidgets.QTabBar,
            QtWidgets.QGraphicsView,
        )
        while widget is not None and widget is not self:
            if isinstance(widget, blocked_types):
                return True
            widget = widget.parentWidget()
        return False

    def _enable_dialog_dragging(self):
        try:
            self.installEventFilter(self)
            for widget in self.findChildren(QtWidgets.QWidget):
                widget.installEventFilter(self)
            self.setToolTip("Arraste em uma área vazia da janela para mover.")
        except Exception:
            pass

    def eventFilter(self, source, event):
        drag_handle = getattr(self, "drag_handle", None)
        if drag_handle is not None and (
            source is drag_handle or drag_handle.isAncestorOf(source)
        ):
            return super(WoodCAM2DDialog, self).eventFilter(source, event)
        resize_grip = getattr(self, "resize_grip", None)
        if resize_grip is not None and (
            source is resize_grip or resize_grip.isAncestorOf(source)
        ):
            return super(WoodCAM2DDialog, self).eventFilter(source, event)
        event_type = event.type()
        if event_type == QtCore.QEvent.MouseButtonPress:
            if (
                event.button() == QtCore.Qt.LeftButton
                and not self._is_drag_blocked_widget(source)
            ):
                self._drag_start_global = self._event_global_pos(event)
                self._drag_start_pos = self.pos()
                self._dialog_dragging = True
                event.accept()
                return True
        elif event_type == QtCore.QEvent.MouseMove:
            if self._dialog_dragging and event.buttons() & QtCore.Qt.LeftButton:
                current = self._event_global_pos(event)
                self.move(self._drag_start_pos + (current - self._drag_start_global))
                event.accept()
                return True
        elif event_type == QtCore.QEvent.MouseButtonRelease:
            if self._dialog_dragging and event.button() == QtCore.Qt.LeftButton:
                self._drag_start_global = None
                self._drag_start_pos = None
                self._dialog_dragging = False
                event.accept()
                return True
        return super(WoodCAM2DDialog, self).eventFilter(source, event)

    def _default_tool_database(self):
        return {
            name: self._normalize_tool_values(values)
            for name, values in TOOL_PRESETS.items()
        }

    def _normalize_tool_values(self, values):
        source = dict(values or {})
        diameter = float(source.get("tool_diameter", DEFAULT_PRESETS["tool_diameter"]))
        tool_type = str(source.get("tool_type", "end_mill"))
        if tool_type not in TOOL_TYPE_DEFINITIONS:
            tool_type = "end_mill"
        stepover = source.get("stepover")
        stepover_percent = source.get("stepover_percent")
        if stepover is None and stepover_percent is None:
            stepover_percent = 35.0 if tool_type not in {"ball_nose", "v_bit"} else 10.0
            stepover = diameter * float(stepover_percent) / 100.0
        elif stepover is None:
            stepover = diameter * float(stepover_percent) / 100.0
        elif stepover_percent is None:
            stepover_percent = 0.0 if diameter <= 0.0 else float(stepover) / diameter * 100.0
        normalized = {
            "tool_type": tool_type,
            "notes": str(
                source.get(
                    "notes",
                    TOOL_TYPE_DEFINITIONS[tool_type]["concept"],
                )
            ),
            "tool_diameter": diameter,
            "included_angle": float(
                source.get(
                    "included_angle",
                    90.0 if tool_type == "v_bit" else 118.0 if tool_type == "drill" else 0.0,
                )
            ),
            "stepdown": float(source.get("stepdown", DEFAULT_PRESETS["stepdown"])),
            "stepover": float(stepover),
            "stepover_percent": float(stepover_percent),
            "feed_xy": float(source.get("feed_xy", DEFAULT_PRESETS["feed_xy"])),
            "feed_z": float(source.get("feed_z", DEFAULT_PRESETS["feed_z"])),
            "rapid_feed": float(source.get("rapid_feed", DEFAULT_PRESETS["rapid_feed"])),
            "ramp_length": float(source.get("ramp_length", DEFAULT_PRESETS["ramp_length"])),
            "rpm": int(round(float(source.get("rpm", DEFAULT_PRESETS["rpm"])))),
            "tool_number": int(round(float(source.get("tool_number", 1)))),
        }
        return normalized

    def _load_tool_database(self):
        try:
            raw = FreeCAD.ParamGet(WOODCAM_PARAMETER_PATH).GetString(
                "tool_database_json",
                "",
            )
            if raw:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    # Quando já existe um cadastro salvo, ele é a fonte de
                    # verdade. Mesclar os presets aqui fazia uma fresa padrão
                    # removida reaparecer na próxima abertura.
                    database = {}
                    for name, values in loaded.items():
                        if isinstance(values, dict):
                            database[str(name)] = self._normalize_tool_values(values)
                    if database:
                        return database
        except Exception:
            pass
        return self._default_tool_database()

    def _save_tool_database(self):
        try:
            FreeCAD.ParamGet(WOODCAM_PARAMETER_PATH).SetString(
                "tool_database_json",
                json.dumps(self.tool_database, ensure_ascii=False, sort_keys=True),
            )
            self._flush_preferences()
        except Exception:
            FreeCAD.Console.PrintWarning(
                "WoodCAM 2D: não foi possível salvar o cadastro de fresas.\n"
            )

    def _load_tool_order(self):
        try:
            raw = FreeCAD.ParamGet(WOODCAM_PARAMETER_PATH).GetString(
                "tool_order_json",
                "",
            )
            if raw:
                loaded = json.loads(raw)
                if isinstance(loaded, list):
                    return [
                        str(name)
                        for name in loaded
                        if str(name) in self.tool_database
                    ]
        except Exception:
            pass
        return []

    def _save_tool_order(self):
        try:
            FreeCAD.ParamGet(WOODCAM_PARAMETER_PATH).SetString(
                "tool_order_json",
                json.dumps(self.tool_order, ensure_ascii=False),
            )
            self._flush_preferences()
        except Exception:
            FreeCAD.Console.PrintWarning(
                "WoodCAM 2D: não foi possível salvar a ordem das fresas.\n"
            )

    @staticmethod
    def _flush_preferences():
        """Grava imediatamente as preferências, inclusive antes de fechar o FreeCAD."""
        save_parameters = getattr(FreeCAD, "saveParameter", None)
        if callable(save_parameters):
            save_parameters()

    def _fallback_tool_order(self):
        preset_index = {name: index for index, name in enumerate(TOOL_PRESETS)}
        return sorted(
            self.tool_database,
            key=lambda name: (
                int(self._normalize_tool_values(self.tool_database[name]).get("tool_number", 9999)),
                preset_index.get(name, len(preset_index)),
                str(name).lower(),
            ),
        )

    def _normalize_tool_order(self):
        base_order = list(self.tool_order or self._fallback_tool_order())
        ordered = []
        seen = set()
        for name in base_order:
            if name in self.tool_database and name not in seen:
                ordered.append(name)
                seen.add(name)
        for name in self._fallback_tool_order():
            if name not in seen:
                ordered.append(name)
                seen.add(name)
        self.tool_order = ordered
        return ordered

    def _renumber_tools_from_order(self):
        for index, name in enumerate(self._normalize_tool_order(), start=1):
            values = self._normalize_tool_values(self.tool_database.get(name))
            values["tool_number"] = index
            self.tool_database[name] = values

    def _place_tool_by_number(self, name, number):
        order = [item for item in self._normalize_tool_order() if item != name]
        try:
            index = int(round(float(number))) - 1
        except (TypeError, ValueError):
            index = len(order)
        index = max(0, min(index, len(order)))
        order.insert(index, name)
        self.tool_order = order
        self._renumber_tools_from_order()
        self._save_tool_order()

    def _tool_names(self):
        return list(self._normalize_tool_order())

    def _tool_values_for_name(self, name):
        if name in self.tool_database:
            return self._normalize_tool_values(self.tool_database.get(name))
        return self._normalize_tool_values(DEFAULT_PRESETS)

    def _tool_type_for_name(self, name):
        return self._tool_values_for_name(name).get("tool_type", "end_mill")

    def _add_tool_combo_item(self, combo, name):
        if name == "Personalizada":
            combo.addItem(name)
            return
        values = self._tool_values_for_name(name)
        icon = ToolTypeDiagram.tool_icon(values.get("tool_type", "end_mill"), 24)
        label = str(name)
        if icon.isNull():
            combo.addItem(label)
        else:
            combo.addItem(icon, label)
        index = combo.count() - 1
        combo.setItemData(
            index,
            (
                f"T{values.get('tool_number', index + 1)} | "
                f"Ø {values.get('tool_diameter', 0):g} mm | "
                f"{TOOL_TYPE_DEFINITIONS.get(values.get('tool_type', 'end_mill'), TOOL_TYPE_DEFINITIONS['end_mill'])['short_label']}"
            ),
            QtCore.Qt.ToolTipRole,
        )

    def _populate_tool_combo(self, combo, current=None):
        current = current if current is not None else combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.setIconSize(QtCore.QSize(24, 24))
        for name in self._tool_names():
            self._add_tool_combo_item(combo, name)
        self._add_tool_combo_item(combo, "Personalizada")
        if current in self._tool_names() or current == "Personalizada":
            combo.setCurrentText(current)
        combo.blockSignals(False)

    def _refresh_tool_combos(self):
        for mode, combo in self.operation_tool_combos.items():
            self._populate_tool_combo(combo)
            self._apply_operation_tool_preset(mode, combo.currentText())

    def _show_tool_database_tab(self):
        index = self._tab_index("Fresas")
        if index is not None:
            self.operation_tabs.setCurrentIndex(index)

    def _tab_index(self, title):
        for index in range(self.operation_tabs.count()):
            if self._tab_title(index) == title:
                return index
        return None

    def _operation_tab_index(self, operation_mode):
        """Resolve an operation tab by its stable mode, not its UI label.

        Display names are deliberately free to use singular/plural wording
        (the production tab is ``Furo`` while menus commonly say ``Furos``).
        Routing CAM actions through those translated labels made the Editor
        preview work only when the desired tab happened to be active.
        """

        operation_mode = str(operation_mode or "")
        for index in range(self.operation_tabs.count()):
            if self._operation_mode_for_index(index) == operation_mode:
                return index
        return None

    def _populate_tool_list(self, selected_name=None):
        if not hasattr(self, "tool_list"):
            return
        self._populating_tool_list = True
        self.tool_list.blockSignals(True)
        self.tool_list.clear()
        for name in self._tool_names():
            values = self._normalize_tool_values(self.tool_database.get(name))
            item = QtWidgets.QListWidgetItem(name)
            item.setFlags(
                (item.flags() | QtCore.Qt.ItemIsDragEnabled)
                & ~QtCore.Qt.ItemIsDropEnabled
            )
            icon = ToolTypeDiagram.tool_icon(values.get("tool_type", "end_mill"), 30)
            if not icon.isNull():
                item.setIcon(icon)
            self.tool_list.addItem(item)
        self.tool_list.blockSignals(False)
        self._populating_tool_list = False
        if selected_name and selected_name in self.tool_database:
            matches = self.tool_list.findItems(selected_name, QtCore.Qt.MatchExactly)
            if matches:
                self.tool_list.setCurrentItem(matches[0])
                return
        if self.tool_list.count() > 0 and self.tool_list.currentRow() < 0:
            self.tool_list.setCurrentRow(0)

    def _tool_list_rows_moved(self, *_args):
        if getattr(self, "_populating_tool_list", False):
            return
        if not hasattr(self, "tool_list"):
            return
        selected_item = self.tool_list.currentItem()
        selected_name = selected_item.text() if selected_item is not None else None
        order = [
            self.tool_list.item(index).text()
            for index in range(self.tool_list.count())
        ]
        self.tool_order = [
            name
            for name in order
            if name in self.tool_database
        ]
        self._renumber_tools_from_order()
        self._save_tool_order()
        self._save_tool_database()
        self._refresh_tool_combos()
        if selected_name in self.tool_database:
            self._load_selected_tool_in_editor(selected_name)

    def _load_selected_tool_in_editor(self, name):
        if not name:
            return
        values = self._normalize_tool_values(self.tool_database.get(str(name)))
        if not values:
            return
        self.tool_editor_fields["name"].setText(str(name))
        self._set_tool_type_combo(values.get("tool_type", "end_mill"))
        self.tool_notes_edit.setPlainText(values.get("notes", ""))
        for key in TOOL_EDITOR_NUMERIC_FIELDS:
            self.tool_editor_fields[key].setText(
                self._format_value(values.get(key, DEFAULT_PRESETS.get(key, 0.0)))
            )
        self._update_tool_type_controls()

    def _new_tool_in_editor(self):
        base = "Nova fresa"
        index = 1
        candidate = base
        while candidate in self.tool_database:
            index += 1
            candidate = f"{base} {index}"
        self.tool_editor_fields["name"].setText(candidate)
        defaults = self._normalize_tool_values(TOOL_PRESETS.get("Fresa 6 mm MDF", DEFAULT_PRESETS))
        self._set_tool_type_combo(defaults["tool_type"])
        self.tool_notes_edit.setPlainText(defaults.get("notes", ""))
        for key in TOOL_EDITOR_NUMERIC_FIELDS:
            self.tool_editor_fields[key].setText(
                self._format_value(defaults.get(key, DEFAULT_PRESETS.get(key, 0.0)))
            )
        self.tool_editor_fields["tool_number"].setText(
            self._format_value(len(self.tool_database) + 1)
        )
        self.tool_list.clearSelection()
        self._update_tool_type_controls()

    def _tool_type_from_combo(self):
        if not hasattr(self, "tool_type_combo"):
            return "end_mill"
        value = self.tool_type_combo.currentData()
        if value in TOOL_TYPE_DEFINITIONS:
            return value
        return "end_mill"

    def _set_tool_type_combo(self, tool_type):
        if not hasattr(self, "tool_type_combo"):
            return
        index = self.tool_type_combo.findData(tool_type)
        self.tool_type_combo.setCurrentIndex(index if index >= 0 else 0)

    def _update_tool_type_controls(self, _index=None):
        tool_type = self._tool_type_from_combo()
        definition = TOOL_TYPE_DEFINITIONS[tool_type]
        if hasattr(self, "tool_type_diagram"):
            self.tool_type_diagram.set_tool_type(tool_type)
        if hasattr(self, "tool_type_combo"):
            self.tool_type_combo.setToolTip(_tool_type_tooltip(tool_type))
        angle_field = self.tool_editor_fields.get("included_angle")
        if angle_field is not None:
            angle_field.setEnabled(tool_type in {"v_bit", "drill"})
        stepover_fields_enabled = tool_type != "drill"
        for key in ("stepover", "stepover_percent"):
            field = self.tool_editor_fields.get(key)
            if field is not None:
                field.setEnabled(stepover_fields_enabled)

    def _sync_stepover_percent_from_mm(self):
        try:
            diameter = float(self.tool_editor_fields["tool_diameter"].text().strip().replace(",", "."))
            stepover = float(self.tool_editor_fields["stepover"].text().strip().replace(",", "."))
        except (KeyError, ValueError):
            return
        if diameter <= 0.0:
            return
        self.tool_editor_fields["stepover_percent"].setText(
            self._format_value(stepover / diameter * 100.0)
        )

    def _sync_stepover_mm_from_percent(self):
        try:
            diameter = float(self.tool_editor_fields["tool_diameter"].text().strip().replace(",", "."))
            percent = float(self.tool_editor_fields["stepover_percent"].text().strip().replace(",", "."))
        except (KeyError, ValueError):
            return
        self.tool_editor_fields["stepover"].setText(
            self._format_value(diameter * percent / 100.0)
        )

    def _parse_tool_editor_value(self, key):
        text = self.tool_editor_fields[key].text().strip().replace(",", ".")
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"O campo da fresa '{key}' precisa ser numérico.")
        if key in {"rpm", "tool_number"}:
            return int(round(value))
        return value

    def _save_tool_from_editor(self):
        try:
            name = self.tool_editor_fields["name"].text().strip()
            if not name:
                raise ValueError("Informe um nome para a fresa.")
            values = {
                key: self._parse_tool_editor_value(key)
                for key in TOOL_EDITOR_NUMERIC_FIELDS
            }
            values["tool_type"] = self._tool_type_from_combo()
            values["notes"] = self.tool_notes_edit.toPlainText().strip()
            if values["tool_diameter"] <= 0.0:
                raise ValueError("O diâmetro da fresa deve ser maior que zero.")
            if values["stepdown"] <= 0.0:
                raise ValueError("O stepdown deve ser maior que zero.")
            if values["feed_xy"] <= 0.0 or values["feed_z"] <= 0.0:
                raise ValueError("Os avanços XY e Z devem ser maiores que zero.")
            if values["rapid_feed"] <= 0.0 or values["rpm"] <= 0:
                raise ValueError("Avanço rápido e RPM devem ser maiores que zero.")
            if values["stepover"] < 0.0 or values["stepover_percent"] < 0.0:
                raise ValueError("O passo lateral não pode ser negativo.")
            if values["tool_type"] in {"v_bit", "drill"} and values["included_angle"] <= 0.0:
                raise ValueError("Informe um ângulo válido para esta ferramenta.")
            requested_tool_number = values["tool_number"]
            self.tool_database[name] = values
            self._place_tool_by_number(name, requested_tool_number)
            self._save_tool_database()
            self._populate_tool_list(name)
            self._refresh_tool_combos()
            QtWidgets.QMessageBox.information(
                self,
                "Fresa salva",
                f"'{name}' foi salva no cadastro de fresas.",
            )
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "Erro", str(error))

    def _remove_tool_from_editor(self):
        name = self.tool_editor_fields["name"].text().strip()
        if not name or name not in self.tool_database:
            return
        if len(self.tool_database) <= 1:
            QtWidgets.QMessageBox.warning(
                self,
                "Cadastro de fresas",
                "Mantenha pelo menos uma fresa cadastrada.",
            )
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Remover fresa",
            f"Remover '{name}' do cadastro?",
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self.tool_database.pop(name, None)
        self.tool_order = [
            item
            for item in self._normalize_tool_order()
            if item != name
        ]
        self._renumber_tools_from_order()
        self._save_tool_order()
        self._save_tool_database()
        self._populate_tool_list()
        self._refresh_tool_combos()

    def _select_tool_for_mode(self, mode):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Selecionar fresa")
        dialog.setModal(True)
        layout = QtWidgets.QVBoxLayout(dialog)
        list_widget = QtWidgets.QListWidget(dialog)
        list_widget.setIconSize(QtCore.QSize(30, 30))
        for name in self._tool_names():
            values = self._normalize_tool_values(self.tool_database[name])
            item = QtWidgets.QListWidgetItem(name)
            tool_type = values.get("tool_type", "end_mill")
            icon = ToolTypeDiagram.tool_icon(tool_type, 30)
            if not icon.isNull():
                item.setIcon(icon)
            tool_type_label = TOOL_TYPE_DEFINITIONS.get(
                tool_type,
                TOOL_TYPE_DEFINITIONS["end_mill"],
            )["short_label"]
            item.setToolTip(
                f"{tool_type_label} | T{values.get('tool_number', 1)} | "
                f"Ø {values.get('tool_diameter', 0):g} mm | "
                f"Stepdown {values.get('stepdown', 0):g} mm | "
                f"Avanço {values.get('feed_xy', 0):g} mm/min"
            )
            list_widget.addItem(item)
        current = self.operation_tool_combos[mode].currentText()
        matches = list_widget.findItems(current, QtCore.Qt.MatchExactly)
        if matches:
            list_widget.setCurrentItem(matches[0])
        elif list_widget.count() > 0:
            list_widget.setCurrentRow(0)
        layout.addWidget(list_widget)
        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)
        exec_dialog = getattr(dialog, "exec", None) or getattr(dialog, "exec_")
        if exec_dialog() != QtWidgets.QDialog.Accepted:
            return
        selected = list_widget.currentItem()
        if selected is None:
            return
        self.operation_tool_combos[mode].setCurrentText(selected.text())

    def _asset_directories(self):
        directories = []
        configured = os.environ.get("WOODCAM2D_DIAGRAM_PATH", "").strip()
        if configured:
            directories.extend(
                Path(part).expanduser()
                for part in configured.split(os.pathsep)
                if part.strip()
            )
        base = Path(__file__).resolve().parent
        directories.extend(
            [
                base / "resources" / "diagrams",
                base / "resources" / "icons",
            ]
        )
        return directories

    def _load_diagram_icon(self, kind):
        if not kind or not hasattr(QtGui, "QIcon"):
            return None
        filenames = DIAGRAM_ASSET_FILENAMES.get(kind, ())
        generic_names = (
            f"{kind}.png",
            f"{kind}.jpg",
            f"{kind}.jpeg",
            f"{kind}.webp",
        )
        for directory in self._asset_directories():
            for filename in tuple(filenames) + generic_names:
                path = directory / filename
                if not path.is_file():
                    continue
                # As ilustrações fornecidas para as abas têm margens
                # transparentes. QIcon puro as mantém e o desenho vira um
                # pontinho em uma aba de 20 px; recortar aqui usa a arte real.
                pixmap = QtGui.QPixmap(str(path))
                if pixmap.isNull():
                    continue
                if kind == "editor2d":
                    pixmap = _make_white_background_transparent(pixmap)
                icon = QtGui.QIcon(_trim_transparent_pixmap(pixmap, padding=1))
                if not icon.isNull():
                    return icon
        return None

    def _build_ui(self):
        outer_layout = QtWidgets.QVBoxLayout(self)
        outer_layout.setSpacing(6)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        self.setStyleSheet(
            "QDialog#WoodCAM2DDialog { background: transparent; }"
            "QTabWidget::pane { background: rgba(245, 245, 245, 232); }"
        )
        self.drag_handle = DialogDragHandle(self, self)
        outer_layout.addWidget(self.drag_handle)
        self.operation_tabs = QtWidgets.QTabWidget()
        self.operation_tabs.setTabBar(
            CompactOperationTabBar(self.operation_tabs)
        )
        self.operation_tabs.setIconSize(QtCore.QSize(20, 20))
        self.detach_editor_button = QtWidgets.QToolButton(self.operation_tabs)
        self.detach_editor_button.setObjectName("detachEditor2DButton")
        self.detach_editor_button.setIcon(
            self.style().standardIcon(QtWidgets.QStyle.SP_TitleBarMaxButton)
        )
        self.detach_editor_button.setAccessibleName("Destacar Editor 2D")
        self.detach_editor_button.setToolTip(
            "Abrir o mesmo Editor 2D em uma janela própria"
        )
        self.detach_editor_button.clicked.connect(self._detach_vector_editor)
        self.operation_tabs.setCornerWidget(
            self.detach_editor_button,
            QtCore.Qt.TopRightCorner,
        )
        outer_layout.addWidget(self.operation_tabs)

        cut_tab = QtWidgets.QWidget()
        cut_layout = QtWidgets.QVBoxLayout(cut_tab)
        cut_layout.setSpacing(8)
        self.operation_combo = QtWidgets.QComboBox()
        self.operation_combo.addItems(
            [
                "Corte externo",
                "Corte sobre a linha",
                "Corte interno",
            ]
        )
        self.operation_combo.setToolTip(
            "Externo preserva a medida da peça; sobre a linha usa o centro da fresa; "
            "interno preserva a medida do vão."
        )
        self.operation_combo.setVisible(False)
        cut_layout.addWidget(self._make_depth_group("cut"))
        cut_layout.addWidget(self._make_tool_group("cut"))
        cut_layout.addWidget(self._make_cut_vector_group())
        cut_layout.addWidget(self._make_cut_tolerance_group())
        cut_layout.addWidget(self._make_cut_tabs_group())
        cut_layout.addWidget(self._make_cut_entry_group())
        cut_layout.addWidget(self._make_cut_name_group())
        cut_layout.addStretch(1)
        self._add_scroll_tab(cut_tab, "Corte", "cut")

        hole_tab = QtWidgets.QWidget()
        hole_layout = QtWidgets.QVBoxLayout(hole_tab)
        hole_layout.setSpacing(8)
        hole_operation_group, hole_operation_layout = self._make_icon_group(
            "Percurso de Furação",
            "holes",
            lambda: {
                "helical": getattr(self, "use_helical_drilling", None) is None
                or self.use_helical_drilling.isChecked(),
                "peck": getattr(self, "peck_enabled", None) is not None
                and self.peck_enabled.isChecked(),
            },
            diagram_key="holes",
        )
        hole_name_layout = QtWidgets.QHBoxLayout()
        hole_name_layout.addWidget(QtWidgets.QLabel("Nome"))
        self.operation_names["holes"] = QtWidgets.QLineEdit("Furo 1")
        hole_name_layout.addWidget(self.operation_names["holes"])
        hole_operation_layout.addLayout(hole_name_layout, 0, 0, 1, 2)
        hole_help = QtWidgets.QLabel(
            "Executa somente os furos circulares. O corte dos contornos internos "
            "continua disponível separadamente na aba Corte."
        )
        hole_help.setWordWrap(True)
        hole_operation_layout.addWidget(hole_help, 1, 0, 1, 2)
        hole_layout.addWidget(self._compact_group(hole_operation_group))
        hole_depth_group = self._make_depth_group("holes")
        hole_depth_layout = getattr(
            hole_depth_group,
            "_woodcam_grid",
            hole_depth_group.layout(),
        )
        self.use_model_hole_depths = QtWidgets.QCheckBox(
            "Usar profundidade de cada furo do modelo"
        )
        self.use_model_hole_depths.setChecked(True)
        hole_depth_layout.addWidget(self.use_model_hole_depths, 2, 1, 1, 2)
        hole_layout.addWidget(hole_depth_group)
        hole_layout.addWidget(self._make_tool_group("holes"))

        helix_group, helix_layout = self._make_icon_group(
            "Interpolação helicoidal",
            "helix",
        )
        self.use_helical_drilling = QtWidgets.QCheckBox(
            "Entrada helicoidal quando a ferramenta for menor"
        )
        self.use_helical_drilling.setChecked(DEFAULT_PRESETS["use_helical_drilling"])
        self.use_helical_drilling.setToolTip(
            "Quando houver espaço, desce em hélice e abre o diâmetro por passadas "
            "circulares. Com ferramenta igual ou maior, usa descida vertical por etapas."
        )
        helix_layout.addWidget(self.use_helical_drilling, 0, 0, 1, 3)
        self._add_operation_field(
            helix_layout,
            1,
            "Descida por volta (mm)",
            "holes",
            "helix_pitch",
        )
        self._add_operation_field(
            helix_layout,
            2,
            "Passo lateral (%)",
            "holes",
            "helix_stepover_percent",
        )
        hole_layout.addWidget(self._compact_group(helix_group))

        peck_group, peck_layout = self._make_icon_group(
            "Furação faseada",
            "peck",
        )
        self.peck_enabled = QtWidgets.QCheckBox("Ativar furação faseada")
        peck_layout.addWidget(self.peck_enabled, 0, 0, 1, 3)
        self.peck_retract_mode = QtWidgets.QComboBox()
        self.peck_retract_mode.addItems(
            [
                "Retrair acima da cota inicial",
                "Retrair acima do passo anterior",
            ]
        )
        peck_layout.addWidget(QtWidgets.QLabel("Retração"), 1, 0)
        peck_layout.addWidget(self.peck_retract_mode, 1, 1, 1, 2)
        self._add_operation_field(
            peck_layout,
            2,
            "Passo de furação (mm)",
            "holes",
            "peck_step",
        )
        self._add_operation_field(
            peck_layout,
            3,
            "Folga de retração (mm)",
            "holes",
            "peck_retract_clearance",
        )
        hole_layout.addWidget(self._compact_group(peck_group))

        dwell_group, dwell_layout = self._make_icon_group(
            "Permanência no fundo",
            "dwell",
        )
        self.dwell_enabled = QtWidgets.QCheckBox("Permanecer no fundo")
        dwell_layout.addWidget(self.dwell_enabled, 0, 0, 1, 3)
        self._add_operation_field(
            dwell_layout,
            1,
            "Tempo de permanência (s)",
            "holes",
            "dwell_seconds",
        )
        hole_layout.addWidget(self._compact_group(dwell_group))

        order_group, order_layout = self._make_icon_group(
            "Ordem dos furos",
            "order",
        )
        self.hole_order_combo = QtWidgets.QComboBox()
        self.hole_order_combo.addItems(
            [
                "Otimizar menor deslocamento",
                "Usar ordem da seleção",
            ]
        )
        order_layout.addWidget(QtWidgets.QLabel("Sequência"), 0, 0)
        order_layout.addWidget(self.hole_order_combo, 0, 1)
        hole_layout.addWidget(self._compact_group(order_group))
        hole_layout.addStretch(1)
        self._add_scroll_tab(hole_tab, "Furo", "holes")

        pocket_tab = QtWidgets.QWidget()
        pocket_layout = QtWidgets.QVBoxLayout(pocket_tab)
        pocket_layout.setSpacing(8)
        self.pocket_strategy_combo = QtWidgets.QComboBox()
        self.pocket_strategy_combo.addItems(
            [
                "Offset concêntrico",
                "Raster (zigue-zague)",
            ]
        )
        pocket_operation_group, pocket_operation_layout = self._make_icon_group(
            "Percurso de Preenchimento",
            "pocket",
            lambda: {
                "strategy": (
                    "raster"
                    if self.pocket_strategy_combo.currentIndex() == 1
                    else "offset"
                ),
                "climb": getattr(self, "pocket_direction_combo", None) is None
                or self.pocket_direction_combo.currentIndex() == 0,
            },
            diagram_key="pocket",
        )
        self.operation_names["pocket"] = QtWidgets.QLineEdit("Preenchimento 1")
        pocket_operation_layout.addWidget(QtWidgets.QLabel("Nome"), 0, 0)
        pocket_operation_layout.addWidget(self.operation_names["pocket"], 0, 1, 1, 2)
        pocket_operation_layout.addWidget(QtWidgets.QLabel("Preenchimento"), 1, 0)
        pocket_operation_layout.addWidget(self.pocket_strategy_combo, 1, 1, 1, 2)
        self.pocket_direction_combo = QtWidgets.QComboBox()
        self.pocket_direction_combo.addItems(["Concordante", "Convencional"])
        pocket_operation_layout.addWidget(QtWidgets.QLabel("Sentido"), 2, 0)
        pocket_operation_layout.addWidget(self.pocket_direction_combo, 2, 1, 1, 2)
        self._add_operation_field(
            pocket_operation_layout,
            3,
            "Passo lateral / stepover (%)",
            "pocket",
            "pocket_stepover_percent",
        )
        self._add_operation_field(
            pocket_operation_layout,
            4,
            "Sobremetal lateral (mm)",
            "pocket",
            "pocket_allowance",
        )
        self._add_operation_field(
            pocket_operation_layout,
            5,
            "Ângulo do raster (°)",
            "pocket",
            "pocket_raster_angle",
        )
        self.pocket_profile_combo = QtWidgets.QComboBox()
        self.pocket_profile_combo.addItems(
            [
                "Sem passe de perfil",
                "Passe final de perfil",
            ]
        )
        self.pocket_profile_combo.setCurrentIndex(1)
        pocket_operation_layout.addWidget(QtWidgets.QLabel("Acabamento"), 6, 0)
        pocket_operation_layout.addWidget(self.pocket_profile_combo, 6, 1, 1, 2)
        pocket_note = QtWidgets.QLabel(
            "Áreas com ilhas internas usam raster automaticamente para preservar os contornos."
        )
        pocket_note.setWordWrap(True)
        pocket_operation_layout.addWidget(pocket_note, 7, 0, 1, 3)
        pocket_layout.addWidget(self._compact_group(pocket_operation_group))
        pocket_layout.addWidget(
            self._make_depth_group("pocket", "pocket_cut_depth")
        )
        pocket_layout.addWidget(self._make_tool_group("pocket"))
        pocket_layout.addWidget(self._make_ramp_group("pocket"))
        pocket_layout.addStretch(1)
        self._add_scroll_tab(pocket_tab, "Preenchimento", "pocket")

        # Cada aba WoodCAM corresponde a um botão de percurso do Aspire. As
        # duas operações 3D usam o mesmo rodapé Aplicar/Pré-visualizar/Simular/
        # G-code e persistem no mesmo grupo das operações 2D.
        rough3d_tab = QtWidgets.QWidget()
        rough3d_layout = QtWidgets.QVBoxLayout(rough3d_tab)
        rough3d_layout.setSpacing(8)
        rough_source_group, rough_source_grid = self._make_icon_group(
            "Modelo 3D para desbaste", "rough3d"
        )
        rough_source_note = QtWidgets.QLabel(
            "Selecione um relevo, STL, malha ou sólido 3D na árvore ou na vista. "
            "A fonte nunca é modificada."
        )
        rough_source_note.setWordWrap(True)
        rough_source_grid.addWidget(rough_source_note, 0, 0, 1, 3)
        self.rough3d_source_label = QtWidgets.QLabel("Fonte: usa a seleção atual")
        self.rough3d_source_label.setStyleSheet("color: #475569; font-weight: bold;")
        rough_source_grid.addWidget(self.rough3d_source_label, 1, 0, 1, 3)
        rough3d_layout.addWidget(self._compact_group(rough_source_group))

        rough_boundary_group, rough_boundary_grid = self._make_icon_group(
            "Fronteira de usinagem", "rough3d"
        )
        self.rough3d_boundary_combo = QtWidgets.QComboBox()
        self.rough3d_boundary_combo.addItems(
            [
                "Limite do modelo",
                "Limite do material",
                "Vetores selecionados",
                "Nível/grupo selecionado",
            ]
        )
        rough_boundary_grid.addWidget(QtWidgets.QLabel("Fronteira"), 0, 0)
        rough_boundary_grid.addWidget(self.rough3d_boundary_combo, 0, 1, 1, 2)
        self._add_operation_field(
            rough_boundary_grid, 1, "Distância da fronteira (mm)",
            "rough3d", "rough3d_boundary_offset"
        )
        rough3d_layout.addWidget(self._compact_group(rough_boundary_group))

        rough_strategy_group, rough_strategy_grid = self._make_icon_group(
            "Estratégia de desbaste", "rough3d"
        )
        self.rough3d_strategy_combo = QtWidgets.QComboBox()
        self.rough3d_strategy_combo.addItems(["Níveis Z", "Varredura 3D"])
        rough_strategy_grid.addWidget(QtWidgets.QLabel("Estratégia"), 0, 0)
        rough_strategy_grid.addWidget(self.rough3d_strategy_combo, 0, 1, 1, 2)
        self.rough3d_profile_combo = QtWidgets.QComboBox()
        self.rough3d_profile_combo.addItems(
            ["Perfil no último passe", "Perfil no primeiro passe", "Sem perfil"]
        )
        rough_strategy_grid.addWidget(QtWidgets.QLabel("Perfil"), 1, 0)
        rough_strategy_grid.addWidget(self.rough3d_profile_combo, 1, 1, 1, 2)
        self.rough3d_order_combo = QtWidgets.QComboBox()
        self.rough3d_order_combo.addItems(["Nível por nível", "Profundidade por região"])
        rough_strategy_grid.addWidget(QtWidgets.QLabel("Ordem"), 2, 0)
        rough_strategy_grid.addWidget(self.rough3d_order_combo, 2, 1, 1, 2)
        self.rough3d_axis_combo = QtWidgets.QComboBox()
        self.rough3d_axis_combo.addItems(["Ao longo de X", "Ao longo de Y"])
        rough_strategy_grid.addWidget(QtWidgets.QLabel("Direção do raster"), 3, 0)
        rough_strategy_grid.addWidget(self.rough3d_axis_combo, 3, 1, 1, 2)
        self.rough3d_reverse_check = QtWidgets.QCheckBox("Inverter o sentido dos passes")
        rough_strategy_grid.addWidget(self.rough3d_reverse_check, 4, 1, 1, 2)
        self._add_operation_field(
            rough_strategy_grid, 5, "Passo lateral / stepover (%)",
            "rough3d", "rough3d_stepover_percent"
        )
        self._add_operation_field(
            rough_strategy_grid, 6, "Folga no modelo / sobremetal (mm)",
            "rough3d", "rough3d_allowance"
        )
        rough3d_layout.addWidget(self._compact_group(rough_strategy_group))
        self.operation_fields.setdefault("rough3d", {})["start_depth"] = QtWidgets.QLineEdit("0")
        self.operation_field_labels.setdefault("rough3d", {})["start_depth"] = "Cota inicial"
        self.operation_fields["rough3d"]["cut_depth"] = QtWidgets.QLineEdit(
            self._format_value(DEFAULT_PRESETS["material_thickness"])
        )
        self.operation_field_labels["rough3d"]["cut_depth"] = "Profundidade do modelo"
        rough3d_layout.addWidget(self._make_tool_group("rough3d"))
        rough3d_layout.addWidget(self._make_ramp_group("rough3d"))
        self.operation_names["rough3d"] = QtWidgets.QLineEdit("Desbaste 3D 1")
        rough_name_group, rough_name_grid = self._make_icon_group("Nome", "rough3d")
        rough_name_grid.addWidget(QtWidgets.QLabel("Nome do percurso"), 0, 0)
        rough_name_grid.addWidget(self.operation_names["rough3d"], 0, 1, 1, 2)
        rough3d_layout.addWidget(self._compact_group(rough_name_group))
        rough3d_layout.addStretch(1)
        self._add_scroll_tab(rough3d_tab, "Desbaste 3D", "rough3d")

        finish3d_tab = QtWidgets.QWidget()
        finish3d_layout = QtWidgets.QVBoxLayout(finish3d_tab)
        finish3d_layout.setSpacing(8)
        finish_source_group, finish_source_grid = self._make_icon_group(
            "Modelo 3D para acabamento", "finish3d"
        )
        finish_source_note = QtWidgets.QLabel(
            "Selecione o mesmo relevo, STL, malha ou sólido usado no desbaste. "
            "Fresa de topo esférico é recomendada."
        )
        finish_source_note.setWordWrap(True)
        finish_source_grid.addWidget(finish_source_note, 0, 0, 1, 3)
        self.finish3d_source_label = QtWidgets.QLabel("Fonte: usa a seleção atual")
        self.finish3d_source_label.setStyleSheet("color: #475569; font-weight: bold;")
        finish_source_grid.addWidget(self.finish3d_source_label, 1, 0, 1, 3)
        finish3d_layout.addWidget(self._compact_group(finish_source_group))

        finish_boundary_group, finish_boundary_grid = self._make_icon_group(
            "Fronteira de usinagem", "finish3d"
        )
        self.finish3d_boundary_combo = QtWidgets.QComboBox()
        self.finish3d_boundary_combo.addItems(
            [
                "Limite do modelo",
                "Limite do material",
                "Vetores selecionados",
                "Nível/grupo selecionado",
            ]
        )
        finish_boundary_grid.addWidget(QtWidgets.QLabel("Fronteira"), 0, 0)
        finish_boundary_grid.addWidget(self.finish3d_boundary_combo, 0, 1, 1, 2)
        self._add_operation_field(
            finish_boundary_grid, 1, "Distância da fronteira (mm)",
            "finish3d", "finish3d_boundary_offset"
        )
        finish3d_layout.addWidget(self._compact_group(finish_boundary_group))

        finish_strategy_group, finish_strategy_grid = self._make_icon_group(
            "Estratégia de acabamento", "finish3d"
        )
        self.finish3d_strategy_combo = QtWidgets.QComboBox()
        self.finish3d_strategy_combo.addItems(["Raster", "Offset concêntrico"])
        finish_strategy_grid.addWidget(QtWidgets.QLabel("Estratégia"), 0, 0)
        finish_strategy_grid.addWidget(self.finish3d_strategy_combo, 0, 1, 1, 2)
        self._add_operation_field(
            finish_strategy_grid, 1, "Passo lateral / stepover (%)",
            "finish3d", "finish3d_stepover_percent"
        )
        self._add_operation_field(
            finish_strategy_grid, 2, "Ângulo do raster (°)",
            "finish3d", "finish3d_raster_angle"
        )
        self.finish3d_reverse_check = QtWidgets.QCheckBox("Inverter o sentido dos passes")
        finish_strategy_grid.addWidget(self.finish3d_reverse_check, 3, 1, 1, 2)
        finish_tip = QtWidgets.QLabel(
            "Sugestão: fresa esférica com stepover de 8% a 12% para acabamento fino."
        )
        finish_tip.setWordWrap(True)
        finish_tip.setStyleSheet("color: #166534;")
        finish_strategy_grid.addWidget(finish_tip, 4, 0, 1, 3)
        finish3d_layout.addWidget(self._compact_group(finish_strategy_group))
        # Campos consumidos pelo contrato CAM comum; o acabamento calcula sua
        # profundidade diretamente da superfície e não mostra passes Z.
        self.operation_fields.setdefault("finish3d", {})["start_depth"] = QtWidgets.QLineEdit("0")
        self.operation_field_labels.setdefault("finish3d", {})["start_depth"] = "Cota inicial"
        self.operation_fields["finish3d"]["cut_depth"] = QtWidgets.QLineEdit(
            self._format_value(DEFAULT_PRESETS["material_thickness"])
        )
        self.operation_field_labels["finish3d"]["cut_depth"] = "Profundidade do modelo"
        self.operation_fields["finish3d"]["ramp_length"] = QtWidgets.QLineEdit("0")
        self.operation_field_labels["finish3d"]["ramp_length"] = "Comprimento da rampa"
        finish3d_layout.addWidget(self._make_tool_group("finish3d", show_passes=False))
        if self.operation_tool_combos["finish3d"].findText("Topo esférico 6 mm") >= 0:
            self.operation_tool_combos["finish3d"].setCurrentText("Topo esférico 6 mm")
        self.operation_names["finish3d"] = QtWidgets.QLineEdit("Acabamento 3D 1")
        finish_name_group, finish_name_grid = self._make_icon_group("Nome", "finish3d")
        finish_name_grid.addWidget(QtWidgets.QLabel("Nome do percurso"), 0, 0)
        finish_name_grid.addWidget(self.operation_names["finish3d"], 0, 1, 1, 2)
        finish3d_layout.addWidget(self._compact_group(finish_name_group))
        finish3d_layout.addStretch(1)
        self._add_scroll_tab(finish3d_tab, "Acabamento 3D", "finish3d")

        job_tab = QtWidgets.QWidget()
        job_layout = QtWidgets.QVBoxLayout(job_tab)
        job_layout.setSpacing(8)

        job_type_group, job_type_grid = self._make_icon_group(
            "Tipo de trabalho",
            "job_type",
        )
        self.job_type_button_group = QtWidgets.QButtonGroup(job_type_group)
        self.job_type_buttons = {}
        for row, (key, label) in enumerate(JOB_TYPE_LABELS.items()):
            radio = QtWidgets.QRadioButton(label)
            radio.setProperty("woodcam_job_type", key)
            if key != "single_sided":
                radio.setEnabled(False)
                radio.setToolTip("Planejado para uma próxima etapa.")
            if key == DEFAULT_PRESETS["job_type"]:
                radio.setChecked(True)
            self.job_type_buttons[key] = radio
            self.job_type_button_group.addButton(radio)
            job_type_grid.addWidget(radio, row, 0, 1, 2)
        job_layout.addWidget(self._compact_group(job_type_group))

        job_size_group, job_size_grid = self._make_icon_group(
            "Tamanho da área de trabalho",
            "job_size",
        )
        self._add_field(
            job_size_grid,
            0,
            "Largura X (mm; 0 = usar seleção)",
            "job_width",
        )
        self._add_field(
            job_size_grid,
            1,
            "Altura Y (mm; 0 = usar seleção)",
            "job_height",
        )
        self._add_field(
            job_size_grid,
            2,
            "Altura Z (mm; 0 = usar Material)",
            "job_depth",
        )
        job_layout.addWidget(self._compact_group(job_size_group))

        job_z_zero_group, job_z_zero_grid = self._make_icon_group(
            "Z-zero do trabalho",
            "material_z_zero",
            lambda: {"z_zero_mode": self._job_z_zero_mode()},
        )
        self.job_z_zero_surface = QtWidgets.QRadioButton("Superfície do material")
        self.job_z_zero_bed = QtWidgets.QRadioButton("Mesa da máquina")
        self.job_z_zero_surface.setChecked(
            DEFAULT_PRESETS["job_z_zero_mode"] == "material_surface"
        )
        self.job_z_zero_bed.setChecked(
            DEFAULT_PRESETS["job_z_zero_mode"] == "machine_bed"
        )
        self.job_z_zero_button_group = QtWidgets.QButtonGroup(job_z_zero_group)
        self.job_z_zero_button_group.addButton(self.job_z_zero_surface)
        self.job_z_zero_button_group.addButton(self.job_z_zero_bed)
        job_z_zero_grid.addWidget(self.job_z_zero_surface, 0, 0, 1, 2)
        job_z_zero_grid.addWidget(self.job_z_zero_bed, 1, 0, 1, 2)
        job_layout.addWidget(self._compact_group(job_z_zero_group))

        job_origin_group, job_origin_grid = self._make_icon_group(
            "Posição da origem XY",
            "origin",
            lambda: {"anchor": self._job_origin_anchor()},
        )
        self.job_use_selection_bounds_origin = QtWidgets.QCheckBox(
            "Usar contorno selecionado como referência"
        )
        self.job_use_selection_bounds_origin.setChecked(
            DEFAULT_PRESETS["job_use_selection_bounds_origin"]
        )
        job_origin_grid.addWidget(self.job_use_selection_bounds_origin, 0, 0, 1, 4)
        origin_picker = QtWidgets.QWidget()
        origin_picker.setFixedSize(74, 72)
        origin_picker.setSizePolicy(
            QtWidgets.QSizePolicy.Fixed,
            QtWidgets.QSizePolicy.Fixed,
        )
        origin_picker_layout = QtWidgets.QGridLayout(origin_picker)
        origin_picker_layout.setContentsMargins(0, 0, 0, 0)
        origin_picker_layout.setSpacing(0)
        self.job_origin_button_group = QtWidgets.QButtonGroup(job_origin_group)
        self.job_origin_buttons = {}
        for key, row, column in ORIGIN_ANCHORS:
            radio = QtWidgets.QRadioButton()
            radio.setToolTip(f"Origem no {ORIGIN_ANCHOR_LABELS[key]}")
            radio.setProperty("woodcam_job_origin_anchor", key)
            if key == DEFAULT_PRESETS["job_origin_anchor"]:
                radio.setChecked(True)
            self.job_origin_buttons[key] = radio
            self.job_origin_button_group.addButton(radio)
            origin_picker_layout.addWidget(radio, row, column)
        job_origin_grid.addWidget(QtWidgets.QLabel("Ponto da área"), 1, 0)
        job_origin_grid.addWidget(origin_picker, 1, 1, 3, 1)
        self._add_field_at(
            job_origin_grid,
            1,
            "X do trabalho (mm)",
            "job_origin_x",
            2,
            3,
            1,
        )
        self._add_field_at(
            job_origin_grid,
            2,
            "Y do trabalho (mm)",
            "job_origin_y",
            2,
            3,
            1,
        )
        job_layout.addWidget(self._compact_group(job_origin_group))

        job_layout.addStretch(1)
        self._add_scroll_tab(job_tab, "Trabalho", "material")

        material_tab = QtWidgets.QWidget()
        material_layout = QtWidgets.QVBoxLayout(material_tab)
        material_layout.setSpacing(8)

        material_group, material_grid = self._make_icon_group(
            "Configuração do material",
            "material_z_zero",
            lambda: {"z_zero_mode": self._z_zero_mode()},
        )
        self._add_field(material_grid, 0, "Espessura (mm)", "material_thickness")
        self.z_zero_surface = QtWidgets.QRadioButton("Superfície do material")
        self.z_zero_bed = QtWidgets.QRadioButton("Mesa da máquina")
        self.z_zero_surface.setChecked(
            DEFAULT_PRESETS["z_zero_mode"] == "material_surface"
        )
        self.z_zero_bed.setChecked(DEFAULT_PRESETS["z_zero_mode"] == "machine_bed")
        self.z_zero_button_group = QtWidgets.QButtonGroup(material_group)
        self.z_zero_button_group.addButton(self.z_zero_surface)
        self.z_zero_button_group.addButton(self.z_zero_bed)
        material_grid.addWidget(QtWidgets.QLabel("Z-zero"), 1, 0)
        material_grid.addWidget(self.z_zero_surface, 1, 1, 1, 2)
        material_grid.addWidget(self.z_zero_bed, 2, 1, 1, 2)
        material_layout.addWidget(self._compact_group(material_group))

        datum_group, datum_grid = self._make_icon_group(
            "Datum XY do material",
            "origin",
            lambda: {"anchor": self._origin_anchor()},
        )
        self.use_selection_bounds_origin = QtWidgets.QCheckBox(
            "Usar contorno selecionado como referência"
        )
        self.use_selection_bounds_origin.setChecked(
            DEFAULT_PRESETS["use_selection_bounds_origin"]
        )
        datum_grid.addWidget(self.use_selection_bounds_origin, 0, 0, 1, 4)
        material_origin_picker = QtWidgets.QWidget()
        material_origin_picker.setFixedSize(74, 72)
        material_origin_picker.setSizePolicy(
            QtWidgets.QSizePolicy.Fixed,
            QtWidgets.QSizePolicy.Fixed,
        )
        material_origin_picker_layout = QtWidgets.QGridLayout(material_origin_picker)
        material_origin_picker_layout.setContentsMargins(0, 0, 0, 0)
        material_origin_picker_layout.setSpacing(0)
        self.origin_button_group = QtWidgets.QButtonGroup(datum_group)
        self.origin_buttons = {}
        for key, row, column in ORIGIN_ANCHORS:
            radio = QtWidgets.QRadioButton()
            radio.setToolTip(f"Datum no {ORIGIN_ANCHOR_LABELS[key]}")
            radio.setProperty("woodcam_origin_anchor", key)
            if key == DEFAULT_PRESETS["origin_anchor"]:
                radio.setChecked(True)
            self.origin_buttons[key] = radio
            self.origin_button_group.addButton(radio)
            material_origin_picker_layout.addWidget(radio, row, column)
        datum_grid.addWidget(QtWidgets.QLabel("Ponto do material"), 1, 0)
        datum_grid.addWidget(material_origin_picker, 1, 1, 3, 1)
        self._add_field_at(
            datum_grid,
            1,
            "X do datum (mm)",
            "origin_x",
            label_column=2,
            field_column=3,
            field_column_span=1,
        )
        self._add_field_at(
            datum_grid,
            2,
            "Y do datum (mm)",
            "origin_y",
            label_column=2,
            field_column=3,
            field_column_span=1,
        )
        datum_grid.setColumnStretch(3, 1)
        material_layout.addWidget(self._compact_group(datum_group))

        model_group, model_grid = self._make_icon_group(
            "Posição do modelo no material",
            "model_position",
        )
        self.model_position_slider = QtWidgets.QSlider(QtCore.Qt.Vertical)
        self.model_position_slider.setRange(0, 1000)
        self.model_position_slider.setSingleStep(10)
        self.model_position_slider.setPageStep(100)
        self.model_position_slider.setValue(0)
        self.model_position_slider.setFixedHeight(96)
        self.model_position_slider.setToolTip(
            "Arraste para posicionar o modelo entre 0 e a espessura do material."
        )
        self.model_position_slider.setStyleSheet(
            "QSlider::groove:vertical { background: #d1d5db; width: 5px; "
            "border-radius: 2px; }"
            "QSlider::handle:vertical { background: #1d74d8; height: 14px; "
            "margin: -4px -6px; border-radius: 4px; }"
            "QSlider::sub-page:vertical { background: #93c5fd; "
            "border-radius: 2px; }"
        )
        model_grid.addWidget(self.model_position_slider, 0, 0, 4, 1)
        self.model_gap_above_radio = QtWidgets.QRadioButton("Folga acima do modelo")
        self.model_gap_below_radio = QtWidgets.QRadioButton("Folga abaixo do modelo")
        self.model_gap_above_radio.setChecked(
            DEFAULT_PRESETS["model_position_mode"] == "gap_above"
        )
        self.model_gap_below_radio.setChecked(
            DEFAULT_PRESETS["model_position_mode"] == "gap_below"
        )
        self.model_position_button_group = QtWidgets.QButtonGroup(model_group)
        self.model_position_button_group.addButton(self.model_gap_above_radio)
        self.model_position_button_group.addButton(self.model_gap_below_radio)
        model_grid.addWidget(self.model_gap_above_radio, 0, 1)
        for field_row, field_name in (
            (0, "model_gap_above"),
            (1, "model_gap_below"),
        ):
            default_value = DEFAULT_PRESETS[field_name]
            try:
                default_value = FreeCAD.ParamGet(WOODCAM_PARAMETER_PATH).GetFloat(
                    field_name,
                    float(default_value),
                )
            except Exception:
                pass
            edit = QtWidgets.QLineEdit(self._format_value(default_value))
            edit.setToolTip("Folga do modelo no material em milímetros.")
            model_grid.addWidget(edit, field_row, 2)
            model_grid.addWidget(QtWidgets.QLabel("mm"), field_row, 3)
            self.fields[field_name] = edit
            self.field_labels[field_name] = "Folga do modelo"
        model_grid.addWidget(self.model_gap_below_radio, 1, 1)
        model_summary = QtWidgets.QLabel(
            "A barra vai de 0 até a espessura do material e calcula a folga oposta."
        )
        model_summary.setStyleSheet("color: #64748b;")
        model_grid.addWidget(model_summary, 2, 1, 1, 3)
        material_layout.addWidget(self._compact_group(model_group))

        safety_group, safety_grid = self._make_icon_group(
            "Folgas de Z rápido acima do material",
            "rapid_z",
        )
        self._add_field(
            safety_grid,
            0,
            "Folga Z1 / retração (mm)",
            "retract_height",
        )
        self._add_field(
            safety_grid,
            1,
            "Mergulho Z2 / segurança (mm)",
            "safe_height",
        )
        safety_note = QtWidgets.QLabel(
            "Z1 deve ficar acima de Z2. O G-code usa Z1 para retrações longas "
            "e Z2 como plano seguro próximo do material."
        )
        safety_note.setWordWrap(True)
        safety_grid.addWidget(safety_note, 2, 0, 1, 3)
        material_layout.addWidget(self._compact_group(safety_group))

        home_group, home_grid = self._make_icon_group("Home", "home")
        self._add_field(home_grid, 0, "X inicial (mm)", "start_x")
        self._add_field(home_grid, 1, "Y inicial (mm)", "start_y")
        self.home_z_display = QtWidgets.QLineEdit()
        self.home_z_display.setReadOnly(True)
        self.home_z_display.setToolTip("Usa a Folga Z1 / retração configurada acima.")
        home_grid.addWidget(QtWidgets.QLabel("Z inicial (usa Z1)"), 2, 0)
        home_grid.addWidget(self.home_z_display, 2, 1, 1, 2)
        self.return_to_start = QtWidgets.QCheckBox("Retornar ao ponto inicial no fim")
        self.return_to_start.setChecked(DEFAULT_PRESETS["return_to_start"])
        home_grid.addWidget(self.return_to_start, 3, 1, 1, 2)
        material_layout.addWidget(self._compact_group(home_group))

        material_layout.addStretch(1)
        self._add_scroll_tab(material_tab, "Material", "material")

        tools_tab = QtWidgets.QWidget()
        tools_layout = QtWidgets.QVBoxLayout(tools_tab)
        tools_layout.setSpacing(8)
        tool_db_group = QtWidgets.QGroupBox("Cadastro de fresas")
        tool_db_grid = QtWidgets.QGridLayout(tool_db_group)
        tool_db_grid.setContentsMargins(10, 8, 10, 8)
        tool_db_grid.setHorizontalSpacing(7)
        tool_db_grid.setVerticalSpacing(5)
        tool_db_grid.setColumnStretch(2, 1)
        tool_db_grid.setColumnStretch(3, 1)
        self.tool_list = QtWidgets.QListWidget()
        self.tool_list.setMinimumWidth(190)
        self.tool_list.setMinimumHeight(260)
        self.tool_list.setIconSize(QtCore.QSize(30, 30))
        self.tool_list.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.tool_list.setDefaultDropAction(QtCore.Qt.MoveAction)
        self.tool_list.setDragEnabled(True)
        self.tool_list.setAcceptDrops(True)
        self.tool_list.setDropIndicatorShown(True)
        self.tool_list.setDragDropOverwriteMode(False)
        try:
            self.tool_list.model().rowsMoved.connect(self._tool_list_rows_moved)
        except Exception:
            pass
        self.tool_list.currentTextChanged.connect(
            self._load_selected_tool_in_editor
        )
        tool_db_grid.addWidget(QtWidgets.QLabel("Fresas cadastradas"), 0, 0)
        tool_db_grid.addWidget(self.tool_list, 1, 0, 18, 1)

        self.tool_type_diagram = ToolTypeDiagram()
        tool_db_grid.addWidget(self.tool_type_diagram, 0, 4, 8, 1)

        info_label = QtWidgets.QLabel("Informações da ferramenta")
        info_label.setStyleSheet("font-weight: bold; color: #1e3a5f;")
        tool_db_grid.addWidget(info_label, 0, 1, 1, 3)
        self.tool_editor_fields["name"] = QtWidgets.QLineEdit()
        tool_db_grid.addWidget(QtWidgets.QLabel("Nome"), 1, 1)
        tool_db_grid.addWidget(self.tool_editor_fields["name"], 1, 2, 1, 2)

        self.tool_type_combo = QtWidgets.QComboBox()
        self.tool_type_combo.setIconSize(QtCore.QSize(24, 24))
        for key in TOOL_TYPE_ORDER:
            item_index = self.tool_type_combo.count()
            icon = ToolTypeDiagram.tool_icon(key, 24)
            if icon.isNull():
                self.tool_type_combo.addItem(TOOL_TYPE_DEFINITIONS[key]["label"], key)
            else:
                self.tool_type_combo.addItem(icon, TOOL_TYPE_DEFINITIONS[key]["label"], key)
            self.tool_type_combo.setItemData(
                item_index,
                _tool_type_tooltip(key),
                QtCore.Qt.ToolTipRole,
            )
        self.tool_type_combo.currentIndexChanged.connect(self._update_tool_type_controls)
        tool_db_grid.addWidget(QtWidgets.QLabel("Tipo"), 2, 1)
        tool_db_grid.addWidget(self.tool_type_combo, 2, 2, 1, 2)

        self.tool_notes_edit = QtWidgets.QTextEdit()
        self.tool_notes_edit.setAcceptRichText(False)
        self.tool_notes_edit.setFixedHeight(58)
        self.tool_notes_edit.setPlaceholderText("Observações de uso, material, acabamento...")
        tool_db_grid.addWidget(QtWidgets.QLabel("Notas"), 3, 1)
        tool_db_grid.addWidget(self.tool_notes_edit, 3, 2, 1, 2)

        geometry_label = QtWidgets.QLabel("Geometria")
        geometry_label.setStyleSheet("font-weight: bold; color: #1e3a5f;")
        tool_db_grid.addWidget(geometry_label, 4, 1, 1, 3)
        row = 5
        for label, key in (
            ("Diâmetro D (mm)", "tool_diameter"),
            ("Ângulo incluído (°)", "included_angle"),
        ):
            self.tool_editor_fields[key] = QtWidgets.QLineEdit()
            tool_db_grid.addWidget(QtWidgets.QLabel(label), row, 1)
            tool_db_grid.addWidget(self.tool_editor_fields[key], row, 2, 1, 2)
            row += 1

        cut_label = QtWidgets.QLabel("Parâmetros de corte")
        cut_label.setStyleSheet("font-weight: bold; color: #1e3a5f;")
        tool_db_grid.addWidget(cut_label, row, 1, 1, 3)
        row += 1
        for label, key in (
            ("Profundidade por passe (mm)", "stepdown"),
            ("Passo lateral (mm)", "stepover"),
        ):
            self.tool_editor_fields[key] = QtWidgets.QLineEdit()
            tool_db_grid.addWidget(QtWidgets.QLabel(label), row, 1)
            tool_db_grid.addWidget(self.tool_editor_fields[key], row, 2, 1, 2)
            row += 1
        self.tool_editor_fields["stepover"].editingFinished.connect(
            self._sync_stepover_percent_from_mm
        )
        self.tool_editor_fields["stepover_percent"] = QtWidgets.QLineEdit()
        tool_db_grid.addWidget(QtWidgets.QLabel("Passo lateral (%)"), row, 1)
        tool_db_grid.addWidget(self.tool_editor_fields["stepover_percent"], row, 2, 1, 2)
        self.tool_editor_fields["stepover_percent"].editingFinished.connect(
            self._sync_stepover_mm_from_percent
        )
        row += 1

        feeds_label = QtWidgets.QLabel("Avanços e rotação")
        feeds_label.setStyleSheet("font-weight: bold; color: #1e3a5f;")
        tool_db_grid.addWidget(feeds_label, row, 1, 1, 3)
        row += 1
        for label, key in (
            ("RPM", "rpm"),
            ("Avanço XY (mm/min)", "feed_xy"),
            ("Avanço Z / mergulho (mm/min)", "feed_z"),
            ("Avanço rápido (mm/min)", "rapid_feed"),
            ("Rampa padrão (mm)", "ramp_length"),
            ("Número da ferramenta", "tool_number"),
        ):
            self.tool_editor_fields[key] = QtWidgets.QLineEdit()
            tool_db_grid.addWidget(QtWidgets.QLabel(label), row, 1)
            tool_db_grid.addWidget(self.tool_editor_fields[key], row, 2, 1, 2)
            row += 1
        button_row = QtWidgets.QHBoxLayout()
        new_tool_button = QtWidgets.QPushButton("Nova")
        new_tool_button.clicked.connect(self._new_tool_in_editor)
        button_row.addWidget(new_tool_button)
        save_tool_button = QtWidgets.QPushButton("Salvar")
        save_tool_button.clicked.connect(self._save_tool_from_editor)
        button_row.addWidget(save_tool_button)
        remove_tool_button = QtWidgets.QPushButton("Remover")
        remove_tool_button.clicked.connect(self._remove_tool_from_editor)
        button_row.addWidget(remove_tool_button)
        button_row.addStretch(1)
        tool_db_grid.addLayout(button_row, row, 1, 1, 3)
        tools_layout.addWidget(self._compact_group(tool_db_group))
        tools_layout.addStretch(1)
        self._add_scroll_tab(tools_tab, "Fresas", "tool")

        editor_tab = QtWidgets.QWidget()
        editor_layout = QtWidgets.QVBoxLayout(editor_tab)
        editor_layout.setContentsMargins(4, 4, 4, 4)
        editor_layout.setSpacing(4)
        if NEW_VECTOR_EDITOR_AVAILABLE:
            self.vector_editor_widget = self._create_vector_editor_widget(editor_tab)
            self.vector_canvas = None
            editor_layout.addWidget(self.vector_editor_widget, 1)
        else:
            warning = QtWidgets.QLabel(
                "O novo Editor 2D não pôde ser carregado. Verifique a pasta "
                "woodcam_editor e reinicie o FreeCAD."
            )
            warning.setWordWrap(True)
            warning.setStyleSheet("color: #b91c1c; font-weight: bold;")
            editor_layout.addWidget(warning)
            self.vector_editor_widget = None
            self.vector_canvas = VectorCanvas()
            editor_layout.addWidget(self.vector_canvas, 1)
        self._vector_editor_tab = editor_tab
        self._vector_editor_layout = editor_layout
        self._vector_editor_detached_placeholder = QtWidgets.QLabel(
            "O Editor 2D está aberto em uma janela própria."
        )
        self._vector_editor_detached_placeholder.setAlignment(QtCore.Qt.AlignCenter)
        self._vector_editor_detached_placeholder.setWordWrap(True)
        self._vector_editor_detached_placeholder.hide()
        editor_layout.addWidget(self._vector_editor_detached_placeholder, 1)
        self._add_scroll_tab(editor_tab, "Editor 2D", "editor2d")

        vector_tab = QtWidgets.QWidget()
        vector_layout = QtWidgets.QVBoxLayout(vector_tab)
        vector_layout.setSpacing(8)
        vector_group, vector_grid = self._make_icon_group(
            "Preparar vetores 2D", "cut"
        )
        vector_note = QtWidgets.QLabel(
            "Legado somente para diagnóstico. Para editar, importar e organizar, use Editor 2D."
        )
        vector_note.setWordWrap(True)
        vector_grid.addWidget(vector_note, 0, 0, 1, 3)
        self.vector_diagnose_button = QtWidgets.QPushButton("Diagnosticar Sketch selecionado")
        self.vector_diagnose_button.clicked.connect(self._diagnose_selected_sketch)
        vector_grid.addWidget(self.vector_diagnose_button, 1, 0, 1, 3)
        self.vector_select_open_button = QtWidgets.QPushButton("Selecionar vetores abertos")
        self.vector_select_open_button.clicked.connect(self._select_open_vectors)
        vector_grid.addWidget(self.vector_select_open_button, 2, 0, 1, 3)
        self.vector_close_tolerance = QtWidgets.QLineEdit("0,2")
        vector_grid.addWidget(QtWidgets.QLabel("Tolerância para fechar (mm)"), 3, 0)
        vector_grid.addWidget(self.vector_close_tolerance, 3, 1)
        self.vector_close_button = QtWidgets.QPushButton("Fechar lacunas pequenas")
        self.vector_close_button.clicked.connect(self._close_small_vector_gaps)
        self.vector_close_button.setEnabled(False)
        self.vector_close_button.setToolTip(
            "Desativado: correções não devem alterar o Sketcher automaticamente. Importe no Editor 2D."
        )
        vector_grid.addWidget(self.vector_close_button, 3, 2)
        self.vector_connect_button = QtWidgets.QPushButton("Pré-visualizar conexões sugeridas")
        self.vector_connect_button.clicked.connect(self._connect_open_endpoints)
        vector_grid.addWidget(self.vector_connect_button, 4, 0, 1, 3)
        self.vector_apply_connections_button = QtWidgets.QPushButton("Aplicar conexões pré-visualizadas")
        self.vector_apply_connections_button.setEnabled(False)
        self.vector_apply_connections_button.setVisible(False)
        self.vector_apply_connections_button.setToolTip(
            "Em revisão: a aplicação direta de restrições será liberada após validar o mapeamento do Sketch."
        )
        self.vector_apply_connections_button.clicked.connect(self._apply_previewed_connections)
        vector_grid.addWidget(self.vector_apply_connections_button, 5, 0, 1, 3)
        self.vector_diagnostic_report = QtWidgets.QPlainTextEdit()
        self.vector_diagnostic_report.setReadOnly(True)
        self.vector_diagnostic_report.setPlaceholderText(
            "O relatório de vetores aparecerá aqui."
        )
        self.vector_diagnostic_report.setMinimumHeight(190)
        vector_grid.addWidget(self.vector_diagnostic_report, 6, 0, 1, 3)
        vector_layout.addWidget(self._compact_group(vector_group))
        vector_layout.addStretch(1)
        # O diagnóstico legado permanece preservado no código, mas não ocupa
        # mais uma aba na interface de produção. O Editor 2D é o único editor
        # vetorial visível e nunca modifica o Sketch de origem.
        self._legacy_vector_diagnostic_widget = vector_tab
        self._populate_tool_list()

        sim_save_tab = QtWidgets.QWidget()
        sim_save_layout = QtWidgets.QVBoxLayout(sim_save_tab)
        sim_save_layout.setSpacing(8)

        simulation_group, simulation_grid = self._make_icon_group(
            "Simulação",
            "simulation",
        )
        self._add_field(simulation_grid, 0, "Velocidade da simulação (x)", "simulation_speed_multiplier")
        simulation_note = QtWidgets.QLabel(
            "Selecione percursos já aplicados abaixo para simular mesmo sem manter os vetores originais selecionados."
        )
        simulation_note.setWordWrap(True)
        simulation_grid.addWidget(simulation_note, 1, 0, 1, 3)
        self.applied_toolpath_list = QtWidgets.QListWidget()
        self.applied_toolpath_list.setSelectionMode(
            QtWidgets.QAbstractItemView.ExtendedSelection
        )
        self.applied_toolpath_list.setMinimumHeight(96)
        simulation_grid.addWidget(QtWidgets.QLabel("Percursos aplicados"), 2, 0)
        simulation_grid.addWidget(self.applied_toolpath_list, 2, 1, 1, 2)
        self.simulation_time_label = QtWidgets.QLabel("Tempo estimado: —")
        self.simulation_time_label.setStyleSheet("color: #475569; font-weight: bold;")
        simulation_grid.addWidget(self.simulation_time_label, 3, 1, 1, 2)
        refresh_applied_button = QtWidgets.QPushButton("Atualizar lista")
        refresh_applied_button.clicked.connect(self._refresh_applied_operation_list)
        simulation_grid.addWidget(refresh_applied_button, 4, 1, 1, 2)
        sim_save_layout.addWidget(self._compact_group(simulation_group))

        export_group, export_grid = self._make_icon_group(
            "Exportação",
            "export",
        )
        self.output_path = QtWidgets.QLineEdit(DEFAULT_PRESETS["output_path"])
        export_grid.addWidget(QtWidgets.QLabel("Arquivo G-code"), 0, 0)
        export_grid.addWidget(self.output_path, 0, 1)
        browse_button = QtWidgets.QPushButton("Escolher...")
        browse_button.clicked.connect(self._select_output_path)
        export_grid.addWidget(browse_button, 0, 2)
        self.export_mode_combo = QtWidgets.QComboBox()
        self.export_mode_combo.addItems(
            [
                "Arquivos separados",
                "Arquivo único",
            ]
        )
        export_grid.addWidget(QtWidgets.QLabel("Quando houver vários percursos"), 1, 0)
        export_grid.addWidget(self.export_mode_combo, 1, 1, 1, 2)
        export_note = QtWidgets.QLabel(
            "Arquivos separados recebem sufixo automaticamente; arquivo único concatena os percursos selecionados."
        )
        export_note.setWordWrap(True)
        export_grid.addWidget(export_note, 2, 1, 1, 2)
        sim_save_layout.addWidget(self._compact_group(export_group))

        action_note_group, action_note_grid = self._make_icon_group(
            "Fluxo recomendado",
            "flow",
        )
        action_note = QtWidgets.QLabel(
            "1. Configure Trabalho e Material.  2. Abra Corte, Furo, Preenchimento, "
            "Desbaste 3D ou Acabamento 3D.  "
            "3. Use Aplicar para guardar a operação na árvore, ou Pré-visualizar/Simular/"
            "Gerar G-code pelos botões abaixo."
        )
        action_note.setWordWrap(True)
        action_note_grid.addWidget(action_note, 0, 0, 1, 3)
        sim_save_layout.addWidget(self._compact_group(action_note_group))
        sim_save_layout.addStretch(1)
        self._add_scroll_tab(sim_save_tab, ACTION_TAB_TITLE, "simulation")

        for target_index, title in enumerate(("Trabalho", "Material", "Fresas")):
            source_index = self._tab_index(title)
            if source_index is not None:
                self.operation_tabs.tabBar().moveTab(source_index, target_index)

        button_layout = QtWidgets.QHBoxLayout()
        self.operation_hint = QtWidgets.QLabel("")
        self.operation_hint.setStyleSheet("color: #64748b;")
        button_layout.addWidget(self.operation_hint)
        button_layout.addStretch(1)
        self.cam_advisor_button = QtWidgets.QPushButton("Assistente CAM")
        self.cam_advisor_button.setToolTip(
            "Analisa ferramenta, passes, alturas e acabamento. Não altera parâmetros nem G-code."
        )
        self.cam_advisor_button.clicked.connect(self.show_cam_advisor)
        button_layout.addWidget(self.cam_advisor_button)
        self.apply_button = QtWidgets.QPushButton("Aplicar")
        self.apply_button.setToolTip(
            "Cria uma operação persistente e numerada na árvore do documento."
        )
        self.apply_button.clicked.connect(self.apply_current_operation)
        button_layout.addWidget(self.apply_button)
        self.preview_button = QtWidgets.QPushButton("Pré-visualizar")
        self.preview_button.clicked.connect(self.preview_toolpath)
        button_layout.addWidget(self.preview_button)
        self.simulate_button = QtWidgets.QPushButton("Simular")
        self.simulate_button.clicked.connect(self.simulate_toolpath)
        button_layout.addWidget(self.simulate_button)
        self.cut_2d_button = QtWidgets.QPushButton("Percursos 2D")
        self.cut_2d_button.setToolTip(
            "Configura ou mostra no plano os percursos exatos de Corte, Furos e Rebaixo."
        )
        toolpath_menu = QtWidgets.QMenu(self.cut_2d_button)
        for operation_mode, operation_label in (
            ("cut", "Corte"),
            ("holes", "Furos"),
            ("pocket", "Rebaixo"),
        ):
            show_action = toolpath_menu.addAction(
                "Ver percurso de %s no 2D" % operation_label
            )
            show_action.triggered.connect(
                lambda _checked=False, mode=operation_mode: self.show_2d_toolpath_preview(mode)
            )
        toolpath_menu.addSeparator()
        for operation_mode, operation_label in (
            ("cut", "Corte"),
            ("holes", "Furos"),
            ("pocket", "Rebaixo"),
        ):
            configure_action = toolpath_menu.addAction(
                "Configurar/criar %s…" % operation_label
            )
            configure_action.triggered.connect(
                lambda _checked=False, mode=operation_mode: self._vector_editor_configure_toolpath(mode)
            )
        toolpath_menu.addSeparator()
        toolpath_menu.addAction(
            "Ocultar percurso 2D",
            self._vector_editor_clear_cut_toolpath,
        )
        self.cut_2d_button.setMenu(toolpath_menu)
        button_layout.addWidget(self.cut_2d_button)
        self.stop_sim_button = QtWidgets.QPushButton("Parar simulação")
        self.stop_sim_button.setEnabled(False)
        self.stop_sim_button.setVisible(False)
        self.stop_sim_button.clicked.connect(self.stop_simulation)
        button_layout.addWidget(self.stop_sim_button)
        self.generate_button = QtWidgets.QPushButton("Gerar G-code")
        self.generate_button.clicked.connect(self.generate_gcode)
        button_layout.addWidget(self.generate_button)
        close_button = QtWidgets.QPushButton("Ocultar")
        close_button.clicked.connect(self.reject)
        button_layout.addWidget(close_button)
        self.resize_grip = DialogResizeGrip(self)
        button_layout.addWidget(
            self.resize_grip,
            0,
            QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom,
        )
        self.toolpath_truth_label = QtWidgets.QLabel(
            "Vista de percurso: nenhuma trajetória calculada."
        )
        self.toolpath_truth_label.setWordWrap(True)
        self.toolpath_truth_label.setStyleSheet(
            "color: #475569; background: #f8fafc; border: 1px solid #cbd5e1; "
            "border-radius: 4px; padding: 5px 8px;"
        )
        outer_layout.addWidget(self.toolpath_truth_label)
        outer_layout.addLayout(button_layout)

        self.operation_combo.currentIndexChanged.connect(self._sync_cut_side_buttons)
        self.pocket_strategy_combo.currentIndexChanged.connect(
            self._update_pocket_controls
        )
        self.rough3d_strategy_combo.currentIndexChanged.connect(
            self._update_operation_controls
        )
        self.finish3d_strategy_combo.currentIndexChanged.connect(
            self._update_operation_controls
        )
        self.pocket_direction_combo.currentIndexChanged.connect(
            self.operation_diagrams["pocket"].update
        )
        self.use_model_hole_depths.toggled.connect(self._update_operation_controls)
        self.use_helical_drilling.toggled.connect(self._update_operation_controls)
        self.peck_enabled.toggled.connect(self._update_operation_controls)
        self.dwell_enabled.toggled.connect(self._update_operation_controls)
        self.z_zero_surface.toggled.connect(self._update_setup_diagrams)
        self.z_zero_bed.toggled.connect(self._update_setup_diagrams)
        self.job_z_zero_surface.toggled.connect(self._update_setup_diagrams)
        self.job_z_zero_bed.toggled.connect(self._update_setup_diagrams)
        for origin_button in self.origin_buttons.values():
            origin_button.toggled.connect(self._update_setup_diagrams)
        for origin_button in self.job_origin_buttons.values():
            origin_button.toggled.connect(self._update_setup_diagrams)
            origin_button.toggled.connect(
                lambda checked: (
                    self._commit_vector_editor_area_discrete()
                    if checked else None
                )
            )
        self.use_selection_bounds_origin.toggled.connect(self._update_setup_diagrams)
        self.job_use_selection_bounds_origin.toggled.connect(self._update_setup_diagrams)
        self.job_use_selection_bounds_origin.toggled.connect(
            self._commit_vector_editor_area_discrete
        )
        for key in (
            "job_width",
            "job_height",
            "job_depth",
            "job_origin_x",
            "job_origin_y",
            "origin_x",
            "origin_y",
            "material_thickness",
        ):
            if key in self.fields:
                self.fields[key].textChanged.connect(self._queue_setup_preview_refresh)
                if key in {
                    "job_width",
                    "job_height",
                    "job_origin_x",
                    "job_origin_y",
                }:
                    self.fields[key].textChanged.connect(
                        self._preview_vector_editor_area_from_setup
                    )
                    self.fields[key].editingFinished.connect(
                        self._commit_vector_editor_area_from_setup
                    )
        self.model_gap_above_radio.toggled.connect(self._update_model_position_controls)
        self.model_gap_below_radio.toggled.connect(self._update_model_position_controls)
        self.model_position_slider.valueChanged.connect(self._update_model_gaps_from_slider)
        self.fields["material_thickness"].textChanged.connect(
            self._sync_model_slider_from_fields
        )
        self.fields["model_gap_above"].editingFinished.connect(
            self._sync_model_slider_from_fields
        )
        self.fields["model_gap_below"].editingFinished.connect(
            self._sync_model_slider_from_fields
        )
        self.fields["retract_height"].textChanged.connect(self._sync_home_z_display)
        self.applied_toolpath_list.itemSelectionChanged.connect(
            self._on_applied_toolpath_selection_changed
        )
        self.operation_tabs.currentChanged.connect(self._update_tab_actions)
        self._load_job_preferences()
        self._load_operation_preferences()
        self._connect_exact_toolpath_invalidation()
        self._connect_operation_preference_savers()
        self._update_operation_controls()
        self._update_pocket_controls()
        self._sync_model_slider_from_fields()
        self._update_model_position_controls()
        self._sync_home_z_display()
        self._update_setup_diagrams()
        self._update_tab_actions(self.operation_tabs.currentIndex())
        self._update_vector_editor_area()

    def _compact_group(self, group):
        group.setSizePolicy(
            QtWidgets.QSizePolicy.Preferred,
            QtWidgets.QSizePolicy.Maximum,
        )
        group_layout = group.layout()
        if group_layout is not None:
            group_layout.setContentsMargins(10, 8, 10, 8)
            group_layout.setSpacing(5)
            if isinstance(group_layout, QtWidgets.QGridLayout):
                group_layout.setColumnStretch(1, 1)
        return group

    @staticmethod
    def _vector_editor_fingerprint(info):
        if info is None:
            return None
        return (
            str(info.document_uuid),
            int(info.revision),
            str(info.checksum),
        )

    def _vector_editor_store_fingerprint(self, store=None):
        target = store if store is not None else getattr(self, "_vector_editor_store", None)
        return self._vector_editor_fingerprint(
            target.stored_info() if target is not None else None
        )

    @staticmethod
    def _vector_editor_memory_fingerprint(vector_document):
        serialized = serialize_document(vector_document)
        return (
            str(serialized.document_uuid),
            int(serialized.revision),
            str(serialized.checksum),
        )

    @staticmethod
    def _vector_editor_cache_key(freecad_document):
        return id(freecad_document) if freecad_document is not None else None

    def _cache_vector_editor_document(
        self,
        freecad_document,
        vector_document,
        host_fingerprint=None,
        prefer_memory=True,
    ):
        if vector_document is None:
            return
        self._vector_editor_memory_cache[
            self._vector_editor_cache_key(freecad_document)
        ] = {
            "freecad_document": freecad_document,
            "vector_document": vector_document.clone(),
            "host_fingerprint": host_fingerprint,
            "prefer_memory": bool(prefer_memory),
        }

    def _cached_vector_editor_document(
        self,
        freecad_document,
        host_fingerprint=None,
        allow_over_host=False,
    ):
        entry = self._vector_editor_memory_cache.get(
            self._vector_editor_cache_key(freecad_document)
        )
        if not entry or entry.get("freecad_document") is not freecad_document:
            return None
        use_cached = bool(allow_over_host or host_fingerprint is None)
        if entry.get("prefer_memory") and entry.get("host_fingerprint") == host_fingerprint:
            use_cached = True
        if not use_cached:
            return None
        cached = entry.get("vector_document")
        return cached.clone() if cached is not None else None

    def _guard_vector_editor_binding(self, expected_document, binding_token):
        """Bloqueia comandos na janela de corrida entre troca A/B e o timer."""
        active_document = FreeCAD.ActiveDocument
        current_token = getattr(self, "_vector_editor_binding_token", None)
        if (
            active_document is expected_document
            and current_token is binding_token
        ):
            return True
        self._reload_vector_editor_for_active_document(active_document)
        self._set_vector_editor_status(
            "O documento ativo mudou; a ação foi cancelada. Repita-a no Editor carregado.",
            error=True,
        )
        return False

    def _build_vector_editor_binding(self, parent, freecad_document):
        """Build a complete binding locally so A/B cannot be half-installed."""
        vector_document = None
        store = None
        session = None
        load_error = None
        host_fingerprint = None
        binding_token = object()
        if freecad_document is not None:
            try:
                store = FreeCADDocumentStore(freecad_document)
                vector_document = store.load()
                host_fingerprint = self._vector_editor_store_fingerprint(store)
                cached = self._cached_vector_editor_document(
                    freecad_document,
                    host_fingerprint,
                )
                if cached is not None:
                    vector_document = cached
            except StoredDocumentCorruptError as error:
                load_error = str(error)
                store = None
                vector_document = self._cached_vector_editor_document(
                    freecad_document,
                    allow_over_host=True,
                )
            except Exception as error:
                load_error = (
                    "Não foi possível abrir o desenho vetorial persistente: %s" % error
                )
                store = None
                vector_document = self._cached_vector_editor_document(
                    freecad_document,
                    allow_over_host=True,
                )
        if vector_document is None:
            vector_document = (
                self._cached_vector_editor_document(
                    freecad_document,
                    host_fingerprint,
                    allow_over_host=freecad_document is None,
                )
                or VectorDocument.create_default()
            )
        if store is not None:
            session = FreeCADCommandSession(vector_document, store)
            execute_target = session.execute
            undo_target = session.undo
            redo_target = session.redo
            history = None
        else:
            history = InMemoryCommandHistory(vector_document)
            execute_target = history.execute
            undo_target = history.undo
            redo_target = history.redo

        def guarded_execute(command):
            if not self._guard_vector_editor_binding(
                freecad_document, binding_token
            ):
                raise CommandExecutionCancelled(
                    "Documento ativo alterado; repita a ação no Editor carregado."
                )
            return execute_target(command)

        def guarded_undo():
            if not self._guard_vector_editor_binding(
                freecad_document, binding_token
            ):
                return None
            return undo_target()

        def guarded_redo():
            if not self._guard_vector_editor_binding(
                freecad_document, binding_token
            ):
                return None
            return redo_target()

        widget = Editor2DWidget(
            vector_document,
            parent,
            history=history,
            execute_command=guarded_execute,
            undo=guarded_undo,
            redo=guarded_redo,
        )
        widget.importSketchRequested.connect(self._vector_editor_import_selected_sketch)
        widget.importPanelNestPartsRequested.connect(
            self._vector_editor_import_panelnest_parts
        )
        widget.diagnoseRequested.connect(self._vector_editor_diagnose)
        widget.cleanupDuplicatesRequested.connect(
            self._vector_editor_cleanup_duplicates
        )
        widget.booleanUnionRequested.connect(
            lambda: self._vector_editor_boolean_selection("union")
        )
        widget.booleanDifferenceRequested.connect(
            lambda: self._vector_editor_boolean_selection("difference")
        )
        widget.booleanIntersectionRequested.connect(
            lambda: self._vector_editor_boolean_selection("intersection")
        )
        widget.booleanOverlapRequested.connect(
            lambda: self._vector_editor_boolean_selection("overlap")
        )
        widget.reverseDirectionRequested.connect(self._vector_editor_reverse_directions)
        widget.createTextRequested.connect(self._vector_editor_create_text)
        widget.editTextRequested.connect(self._vector_editor_edit_text)
        widget.groupRequested.connect(self._vector_editor_group_selection)
        widget.ungroupRequested.connect(self._vector_editor_ungroup_selection)
        widget.closePathRequested.connect(self._vector_editor_close_selected)
        widget.joinOpenPathsRequested.connect(self._vector_editor_join_open_paths)
        widget.fitCurvesRequested.connect(self._vector_editor_fit_curves)
        widget.createContourRequested.connect(self._vector_editor_create_offset_contours)
        widget.repairRequested.connect(self._vector_editor_repair_selection)
        widget.createPiecesRequested.connect(self._vector_editor_create_pieces)
        widget.organizePiecesRequested.connect(
            lambda: self._vector_editor_request_organize("balanced")
        )
        widget.organizePiecesFastRequested.connect(
            lambda: self._vector_editor_request_organize("fast")
        )
        widget.organizePiecesThoroughRequested.connect(
            lambda: self._vector_editor_request_organize("thorough")
        )
        widget.sendPanelNestRequested.connect(self._vector_editor_send_panelnest)
        widget.useInCamRequested.connect(self._toggle_vector_editor_cam_source)
        widget.showCutToolpathRequested.connect(
            self._vector_editor_show_cut_toolpath
        )
        widget.showToolpathRequested.connect(
            self._vector_editor_show_toolpath
        )
        widget.configureToolpathRequested.connect(
            self._vector_editor_configure_toolpath
        )
        widget.clearCutToolpathRequested.connect(
            self._vector_editor_clear_cut_toolpath
        )
        widget.importRequested.connect(self._vector_editor_import_file)
        widget.traceBitmapRequested.connect(self._vector_editor_trace_bitmap)
        widget.createReliefRequested.connect(self._vector_editor_create_image_relief)
        widget.exportRequested.connect(self._vector_editor_export_file)
        widget.documentChanged.connect(self._vector_editor_document_changed)
        self._configure_vector_editor_preferences(widget)
        return {
            "freecad_document": freecad_document,
            "vector_document": vector_document,
            "store": store,
            "session": session,
            "widget": widget,
            "load_error": load_error,
            "fingerprint": host_fingerprint,
            "binding_token": binding_token,
        }

    def _install_vector_editor_binding(self, binding):
        widget = binding["widget"]
        self._vector_editor_bound_freecad_document = binding["freecad_document"]
        self._vector_editor_document = binding["vector_document"]
        self._vector_editor_store = binding["store"]
        self._vector_editor_session = binding["session"]
        self._vector_editor_load_error = binding["load_error"]
        self._vector_editor_feature_fingerprint = binding["fingerprint"]
        self._vector_editor_binding_token = binding["binding_token"]
        # Um editingFinished atrasado da aba Trabalho antiga não pode aplicar
        # seus valores no documento recém-carregado.
        self._vector_editor_area_edit_pending = False
        self._vector_editor_area_edit_binding_token = None
        self._vector_editor_area_edit_document = None
        if self._vector_editor_load_error:
            message = (
                self._vector_editor_load_error
                + " O original foi preservado; esta sessão está somente em memória."
            )
            QtCore.QTimer.singleShot(
                0,
                lambda target=widget, text=message: (
                    self._set_vector_editor_status(text, error=True)
                    if getattr(self, "vector_editor_widget", None) is target
                    else None
                ),
            )

    def _create_vector_editor_widget(
        self,
        parent=None,
        freecad_document=_USE_ACTIVE_FREECAD_DOCUMENT,
    ):
        """Cria a sessão persistente sem deixar JSON inválido ser sobrescrito."""
        if freecad_document is _USE_ACTIVE_FREECAD_DOCUMENT:
            freecad_document = FreeCAD.ActiveDocument
        binding = self._build_vector_editor_binding(parent, freecad_document)
        self._install_vector_editor_binding(binding)
        widget = binding["widget"]
        return widget

    def _configure_vector_editor_preferences(self, widget):
        """Carrega preferências visuais; geometria continua pertencendo ao FCStd."""
        try:
            preferences = FreeCAD.ParamGet(WOODCAM_PARAMETER_PATH + "/Editor2D")
            widget.snap_checkbox.setChecked(
                preferences.GetBool("SnapEnabled", True)
            )
            widget.smart_snap_checkbox.setChecked(
                preferences.GetBool("SmartSnapEnabled", True)
            )
            widget.grid_spacing.setValue(
                max(0.01, preferences.GetFloat("GridSpacingMM", 10.0))
            )
            widget.nesting_spacing.setValue(
                max(0.0, preferences.GetFloat("NestingSpacingMM", 10.0))
            )
            widget.join_tolerance.setValue(
                max(0.001, preferences.GetFloat("JoinToleranceMM", 0.2))
            )

            def save_bool(name, value):
                preferences.SetBool(name, bool(value))
                FreeCAD.saveParameter()

            def save_float(name, value):
                preferences.SetFloat(name, float(value))
                FreeCAD.saveParameter()

            widget.snap_checkbox.toggled.connect(
                lambda value: save_bool("SnapEnabled", value)
            )
            widget.smart_snap_checkbox.toggled.connect(
                lambda value: save_bool("SmartSnapEnabled", value)
            )
            widget.grid_spacing.valueChanged.connect(
                lambda value: save_float("GridSpacingMM", value)
            )
            widget.nesting_spacing.valueChanged.connect(
                lambda value: save_float("NestingSpacingMM", value)
            )
            widget.join_tolerance.valueChanged.connect(
                lambda value: save_float("JoinToleranceMM", value)
            )
        except Exception as error:
            FreeCAD.Console.PrintWarning(
                "WoodCAM Editor 2D: preferências visuais não carregadas: %s\n"
                % error
            )

    def _vector_editor_document_changed(self, _change_set=None):
        """Atualiza integrações derivadas depois de um comando já confirmado."""
        sender = self.sender()
        current_widget = getattr(self, "vector_editor_widget", None)
        if sender is not None and current_widget is not None and sender is not current_widget:
            # A widget removed with deleteLater may still have queued signals.
            # Never let an old A session update the active B integration.
            return
        validation_dialog = getattr(self, "_vector_validation_dialog", None)
        if validation_dialog is not None and validation_dialog.isVisible():
            validation_dialog.close()
            self._vector_validation_dialog = None
        self._remember_vector_editor_feature_fingerprint()
        if hasattr(self, "applied_toolpath_list"):
            self._refresh_applied_operation_list()

    def _remember_vector_editor_feature_fingerprint(self):
        try:
            self._vector_editor_feature_fingerprint = (
                self._vector_editor_store_fingerprint()
            )
        except Exception:
            self._vector_editor_feature_fingerprint = None

    def _persist_vector_editor_before_switch(self):
        """Persist a safe local delta or retain it in the per-document cache.

        If FreeCAD's host snapshot changed first (global Undo/Redo), the host
        wins and is reloaded.  Only when the host fingerprint is still the one
        previously observed may a local in-memory delta be saved.  This keeps
        direct UI state such as the work area without ever overwriting an
        external history change or a corrupt feature.
        """
        vector_document = getattr(self, "_vector_editor_document", None)
        freecad_document = getattr(
            self, "_vector_editor_bound_freecad_document", None
        )
        store = getattr(self, "_vector_editor_store", None)
        session = getattr(self, "_vector_editor_session", None)
        if vector_document is None:
            return None

        host_fingerprint = None
        prefer_memory = True
        warning = None
        try:
            if store is not None and getattr(store, "document", None) is freecad_document:
                host_fingerprint = self._vector_editor_store_fingerprint(store)
                if host_fingerprint != self._vector_editor_feature_fingerprint:
                    # Undo/Redo happened before the document switch.  Never
                    # write the stale canvas back over that host snapshot.
                    if session is None:
                        raise RuntimeError("sessão persistente indisponível")
                    session.reload()
                    vector_document = self._vector_editor_document
                memory_fingerprint = self._vector_editor_memory_fingerprint(
                    vector_document
                )
                if memory_fingerprint != host_fingerprint:
                    info = store.save(
                        vector_document,
                        transaction_label="WoodCAM 2D — Sincronizar antes de trocar documento",
                        use_transaction=True,
                    )
                    host_fingerprint = self._vector_editor_fingerprint(info)
                self._vector_editor_feature_fingerprint = host_fingerprint
                prefer_memory = False
        except Exception as error:
            warning = (
                "O desenho anterior não pôde ser persistido; a cópia em memória "
                "foi preservada nesta janela: %s" % error
            )
            prefer_memory = True

        self._cache_vector_editor_document(
            freecad_document,
            vector_document,
            host_fingerprint=host_fingerprint,
            prefer_memory=prefer_memory,
        )
        return warning

    def _sync_vector_editor_from_freecad_history(self):
        """Follow ActiveDocument and then reload host Undo/Redo snapshots."""
        if not NEW_VECTOR_EDITOR_AVAILABLE:
            return
        active_document = FreeCAD.ActiveDocument
        bound_document = getattr(
            self, "_vector_editor_bound_freecad_document", None
        )
        if active_document is not bound_document:
            if self._reload_vector_editor_for_active_document(active_document):
                self._current_document_key = self._active_document_key()
            return
        store = getattr(self, "_vector_editor_store", None)
        session = getattr(self, "_vector_editor_session", None)
        widget = getattr(self, "vector_editor_widget", None)
        if store is None or session is None or widget is None:
            return
        try:
            fingerprint = self._vector_editor_store_fingerprint(store)
            if fingerprint == self._vector_editor_feature_fingerprint:
                return
            session.reload()
            widget.refresh_from_document()
            self._vector_editor_feature_fingerprint = fingerprint
            self._show_vector_editor_document_area()
            self._set_vector_editor_status(
                "Editor 2D sincronizado com o Undo/Redo do FreeCAD."
            )
        except Exception as error:
            self._set_vector_editor_status(
                "Não foi possível sincronizar o histórico do FreeCAD: %s" % error,
                error=True,
            )

    def _reload_vector_editor_for_active_document(
        self,
        active_document=_USE_ACTIVE_FREECAD_DOCUMENT,
    ):
        """Troca a sessão vetorial junto com o documento ativo do FreeCAD."""
        if not NEW_VECTOR_EDITOR_AVAILABLE or self._vector_editor_switching:
            return False
        if active_document is _USE_ACTIVE_FREECAD_DOCUMENT:
            active_document = FreeCAD.ActiveDocument
        if active_document is getattr(
            self, "_vector_editor_bound_freecad_document", None
        ):
            return False
        old_widget = getattr(self, "vector_editor_widget", None)
        if old_widget is None:
            return False
        parent = old_widget.parentWidget()
        layout = parent.layout() if parent is not None else None
        if layout is None:
            return False

        self._vector_editor_switching = True
        warning = None
        try:
            validation_dialog = getattr(self, "_vector_validation_dialog", None)
            if validation_dialog is not None:
                validation_dialog.close()
                self._vector_validation_dialog = None
            warning = self._persist_vector_editor_before_switch()
            # Build completely before touching the installed widget/session.
            binding = self._build_vector_editor_binding(parent, active_document)
            new_widget = binding["widget"]
        except Exception as error:
            self._set_vector_editor_status(
                "Não foi possível trocar o documento ativo: %s" % error,
                error=True,
            )
            self._vector_editor_switching = False
            return False

        try:
            old_widget.blockSignals(True)
            old_widget.setEnabled(False)
            index = layout.indexOf(old_widget)
            layout.removeWidget(old_widget)
            insert = getattr(layout, "insertWidget", None)
            if callable(insert) and index >= 0:
                insert(index, new_widget, 1)
            else:
                layout.addWidget(new_widget, 1)
            self._install_vector_editor_binding(binding)
            self.vector_editor_widget = new_widget
            self._use_vector_editor_for_cam = False
            old_widget.setParent(None)
            old_widget.deleteLater()
            self._show_vector_editor_document_area()
            if warning:
                self._set_vector_editor_status(warning, error=True)
            elif active_document is None:
                self._set_vector_editor_status(
                    "Editor 2D em memória: nenhum documento FreeCAD ativo."
                )
            else:
                self._set_vector_editor_status(
                    "Documento vetorial carregado para %s."
                    % str(
                        getattr(
                            active_document,
                            "Label",
                            getattr(active_document, "Name", "documento"),
                        )
                    )
                )
            return True
        finally:
            self._vector_editor_switching = False

    def _set_vector_editor_status(self, message, error=False):
        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        widget.mode_label.setText(str(message))
        widget.mode_label.setStyleSheet(
            "color: %s; font-weight: %s;" % (
                "#b91c1c" if error else "#334155",
                "bold" if error else "normal",
            )
        )

    def _vector_editor_busy_dialog(self, label):
        """Show an honest indeterminate progress indicator for heavy 2D work.

        Geometry analysis is deliberately kept in the GUI process because it
        reads the immutable VectorDocument only.  Its duration varies greatly
        with the source drawing, so a busy bar is less misleading than a fake
        percentage.  Processing events once makes the feedback visible before
        the synchronous calculation starts.
        """

        dialog = QtWidgets.QProgressDialog(str(label), None, 0, 0, self)
        dialog.setObjectName("vectorEditorBusyDialog")
        dialog.setWindowTitle("WoodCAM Editor 2D")
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumDuration(0)
        dialog.setCancelButton(None)
        window_modal = getattr(QtCore.Qt, "WindowModal", None)
        if window_modal is None:
            window_modal = getattr(
                getattr(QtCore.Qt, "WindowModality", object), "WindowModal", None
            )
        if window_modal is not None:
            dialog.setWindowModality(window_modal)
        dialog.show()
        application = QtWidgets.QApplication.instance()
        if application is not None:
            application.processEvents()
        return dialog

    @staticmethod
    def _close_vector_editor_busy_dialog(dialog):
        if dialog is None:
            return
        dialog.close()
        dialog.deleteLater()

    def _vector_editor_import_selected_sketch(self):
        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        try:
            selected = list(FreeCADGui.Selection.getSelection() or [])
            if not selected:
                raise ValueError(
                    "Selecione um ou mais Sketches, faces ou objetos com Shape no FreeCAD."
                )
            from geometry_reader import resolve_selection_objects

            resolved = resolve_selection_objects(selected)
            self._vector_editor_import_freecad_sources(widget, resolved)
        except Exception as error:
            self._set_vector_editor_status("Importação não executada: %s" % error, error=True)

    def _vector_editor_import_panelnest_parts(self):
        """Use PanelNest only to read flat parts; never request its layout."""

        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        try:
            selected = list(FreeCADGui.Selection.getSelection() or [])
            if not selected:
                raise ValueError(
                    "Selecione o móvel, grupo ou peças que deseja ler pelo PanelNest."
                )
            from woodcam_editor.adapters.panelnest import _load_panelnest_module
            from woodcam_editor.importers.panelnest_parts import import_panelnest_parts

            panelnest = _load_panelnest_module()
            collect_parts = getattr(panelnest, "collect_parts", None) if panelnest else None
            if not callable(collect_parts):
                raise ValueError(
                    "O PanelNest não está disponível para ler as peças selecionadas."
                )
            try:
                parts = collect_parts(selected, include_hidden=True)
            except TypeError:
                parts = collect_parts(selected)
            source_profiles = self._panelnest_source_profiles_from_source(parts)
            exact_source_entities = self._panelnest_exact_source_entities(
                parts,
                layer_id=widget.document.active_layer_id,
            )
            result = import_panelnest_parts(
                parts,
                layer_id=widget.document.active_layer_id,
                source_profiles_by_source=source_profiles,
                exact_entities_by_source=exact_source_entities,
            )
            if not result.entities:
                details = "; ".join(issue.message for issue in result.issues[:3])
                raise ValueError(details or "O PanelNest não identificou peças planas na seleção.")
            entities, _batch_id, import_delta = prepare_import_batch(
                widget.document,
                result.entities,
            )
            widget.controller.execute(AddEntitiesCommand(entities))
            widget.controller.selection.clear()
            widget.fit_entities(entity.id for entity in entities)
            self._set_vector_editor_status(
                "PanelNest leu %d vetor(es) planos em grade compacta, sem executar nesting%s. "
                "Agora use Peças → Reconhecer peças e furos → Organizar."
                % (
                    len(entities),
                    "; lote posicionado ao lado" if import_delta.length() > 0.0 else "",
                )
            )
            for issue in result.issues:
                if issue.severity != "info":
                    FreeCAD.Console.PrintWarning("WoodCAM Editor 2D: %s\n" % issue.message)
        except Exception as error:
            self._set_vector_editor_status(
                "Importação PanelNest não executada: %s" % error,
                error=True,
            )

    @staticmethod
    def _panelnest_inner_profiles_from_source(parts):
        """Read non-circular through-cuts from the original broad face.

        PanelNest's current ``PanelPart`` contract transports the outer
        profile and circular holes, but not inner wires.  This reads only the
        source Shape (never changes it) in PanelNest's own length × width
        coordinate convention so rectangular window cuts travel with the
        part into the independent Editor 2D.
        """

        document = getattr(FreeCAD, "ActiveDocument", None)
        if document is None:
            return {}
        result = {}
        for part in parts or ():
            object_name = str(getattr(part, "object_name", "") or "")
            source = document.getObject(object_name) if object_name else None
            shape = getattr(source, "Shape", None)
            if shape is None:
                continue
            try:
                bbox = shape.BoundBox
                dimensions = sorted(
                    (
                        (abs(float(bbox.XLength)), 0, float(bbox.XMin)),
                        (abs(float(bbox.YLength)), 1, float(bbox.YMin)),
                        (abs(float(bbox.ZLength)), 2, float(bbox.ZMin)),
                    )
                )
                if dimensions[0][0] <= 1.0e-9:
                    continue
                width_axis, width_origin = dimensions[1][1], dimensions[1][2]
                length_axis, length_origin = dimensions[2][1], dimensions[2][2]
                faces = list(getattr(shape, "Faces", []) or [])
                broad_faces = sorted(
                    faces,
                    key=lambda face: (
                        len(list(getattr(face, "Wires", []) or [])),
                        float(getattr(face, "Area", 0.0)),
                    ),
                    reverse=True,
                )
                face = next(
                    (candidate for candidate in broad_faces if len(list(getattr(candidate, "Wires", []) or [])) > 1),
                    None,
                )
                if face is None:
                    continue
                outer_wire = getattr(face, "OuterWire", None)
                loops = []
                for wire in list(getattr(face, "Wires", []) or []):
                    # OCC does not promise that the outer wire is item zero.
                    # Skipping by identity avoids accidentally importing the
                    # whole panel again as an internal recut.
                    try:
                        if outer_wire is not None and wire.isSame(outer_wire):
                            continue
                    except Exception:
                        if outer_wire is wire:
                            continue
                    edges = list(
                        getattr(wire, "OrderedEdges", [])
                        or getattr(wire, "Edges", [])
                        or []
                    )
                    # Circular wires already travel in PanelNest's dedicated
                    # holes payload.  Importing them again as a discretized
                    # path produced coincident circle pairs, thousands of
                    # false CONTOUR_INTERSECTION issues and the apparent
                    # "osso" made of duplicated rings.
                    if len(edges) == 1:
                        curve = getattr(edges[0], "Curve", None)
                        curve_name = "%s %s" % (
                            type(curve).__name__,
                            getattr(curve, "TypeId", ""),
                        )
                        if "circle" in curve_name.lower():
                            continue
                    points = list(wire.discretize(Deflection=0.25) or [])
                    if len(points) > 1:
                        first, last = points[0], points[-1]
                        if first.distanceToPoint(last) <= 0.01:
                            points.pop()
                    loop = []
                    for point in points:
                        values = (float(point.x), float(point.y), float(point.z))
                        xy = (values[length_axis] - length_origin, values[width_axis] - width_origin)
                        if not loop or abs(loop[-1][0] - xy[0]) > 0.01 or abs(loop[-1][1] - xy[1]) > 0.01:
                            loop.append(xy)
                    if len(loop) >= 3:
                        loops.append(tuple(loop))
                if loops:
                    result[object_name] = tuple(loops)
            except Exception:
                # A source part without a readable solid simply retains the
                # public PanelNest payload; importing must stay non-destructive.
                continue
        return result

    @staticmethod
    def _panelnest_source_profiles_from_source(parts):
        """Read PanelNest's missing inner wires and correctly oriented holes.

        PanelNest provides the public outer profile plus a convenient list of
        round holes.  On equal-sided parts its public hole convention can be
        transposed relative to the profile convention.  More importantly, a
        dogbone is a *single non-circular inner wire* whose round arcs are
        also reported as four independent holes.  Reading a copy of the
        original broad face keeps the actual cut contour and prevents those
        arcs from being duplicated as loose circles.
        """

        inner_profiles = WoodCAM2DDialog._panelnest_inner_profiles_from_source(parts)
        document = getattr(FreeCAD, "ActiveDocument", None)
        if document is None:
            return {
                key: {"inner_loops": loops}
                for key, loops in inner_profiles.items()
            }

        def _point_segment_distance(point, start, end):
            px, py = point
            ax, ay = start
            bx, by = end
            dx, dy = bx - ax, by - ay
            denominator = dx * dx + dy * dy
            if denominator <= 1.0e-12:
                return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            ratio = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denominator))
            return ((px - (ax + ratio * dx)) ** 2 + (py - (ay + ratio * dy)) ** 2) ** 0.5

        def _is_arc_of_inner_loop(center, radius, loops):
            for loop in loops:
                if len(loop) < 3:
                    continue
                nearest = min(
                    _point_segment_distance(center, loop[index], loop[(index + 1) % len(loop)])
                    for index in range(len(loop))
                )
                # A circular cut represented by an arc in a dogbone has its
                # centre exactly one radius away from the joined inner wire.
                # A real drill hole inside a window is much farther away.
                if abs(nearest - radius) <= max(0.35, radius * 0.18):
                    return True
            return False

        result = {
            key: {"inner_loops": tuple(loops)}
            for key, loops in inner_profiles.items()
        }
        for part in parts or ():
            object_name = str(getattr(part, "object_name", "") or "")
            source = document.getObject(object_name) if object_name else None
            shape = getattr(source, "Shape", None)
            if shape is None:
                continue
            try:
                bbox = shape.BoundBox
                dimensions = sorted(
                    (
                        (abs(float(bbox.XLength)), 0, float(bbox.XMin)),
                        (abs(float(bbox.YLength)), 1, float(bbox.YMin)),
                        (abs(float(bbox.ZLength)), 2, float(bbox.ZMin)),
                    )
                )
                if dimensions[0][0] <= 1.0e-9:
                    continue
                thickness_axis = dimensions[0][1]
                width_axis, width_origin = dimensions[1][1], dimensions[1][2]
                length_axis, length_origin = dimensions[2][1], dimensions[2][2]
                loops = tuple(result.get(object_name, {}).get("inner_loops", ()) or ())
                holes = []
                seen = set()
                for face in list(getattr(shape, "Faces", []) or []):
                    surface = getattr(face, "Surface", None)
                    axis = getattr(surface, "Axis", None)
                    radius = float(getattr(surface, "Radius", 0.0) or 0.0)
                    center = getattr(surface, "Center", None)
                    if axis is None or center is None or radius <= 0.5:
                        continue
                    components = (
                        abs(float(getattr(axis, "x", 0.0))),
                        abs(float(getattr(axis, "y", 0.0))),
                        abs(float(getattr(axis, "z", 0.0))),
                    )
                    dominant_axis = max(range(3), key=lambda index: components[index])
                    if components[dominant_axis] < 0.85 or dominant_axis != thickness_axis:
                        continue
                    values = (float(center.x), float(center.y), float(center.z))
                    x_value = values[length_axis] - length_origin
                    y_value = values[width_axis] - width_origin
                    key = (round(x_value, 3), round(y_value, 3), round(radius, 3))
                    if key in seen:
                        continue
                    seen.add(key)
                    if _is_arc_of_inner_loop((x_value, y_value), radius, loops):
                        continue
                    holes.append(
                        {
                            "x_mm": x_value,
                            "y_mm": y_value,
                            "diameter_mm": radius * 2.0,
                        }
                    )
                payload = result.setdefault(object_name, {"inner_loops": loops})
                # Only replace PanelNest's public holes when the source
                # actually exposed cylindrical through-cuts.  Metadata-only
                # holes must retain the public payload rather than vanish.
                if seen:
                    payload["holes"] = tuple(holes)
            except Exception:
                # Keep PanelNest's public values when a source cannot be
                # inspected. The import remains an independent, safe copy.
                continue
        return result

    @staticmethod
    def _panelnest_exact_source_entities(parts, *, layer_id):
        """Copy the original broad face for PanelNest records when available.

        PanelNest is used solely to discover the manufacturing parts.  Its
        lightweight profile payload is a fallback, not the geometric truth:
        it can reduce a joined dogbone/window to independent circles.  This
        helper follows the same broad-face strategy used by the DXF macro and
        imports the source face's OCC wires exactly (line/arc), after flattening
        an upright panel into its own XY plane.  The source FCStd is read only.
        """

        document = getattr(FreeCAD, "ActiveDocument", None)
        if document is None:
            return {}
        from woodcam_editor.importers.part_shape import import_part_shape

        result = {}
        for part in parts or ():
            object_name = str(getattr(part, "object_name", "") or "")
            source = document.getObject(object_name) if object_name else None
            if getattr(source, "Shape", None) is None:
                continue
            try:
                imported = import_part_shape(
                    source,
                    layer_id=layer_id,
                    # The selected panel can be vertical in the furniture
                    # assembly.  The importer projects its broad face; it
                    # never imports the 15 mm thickness side as a profile.
                    flatten_solids=True,
                )
            except Exception:
                continue
            if imported.entities:
                result[object_name] = tuple(imported.entities)
        return result

    def _vector_editor_import_freecad_sources(
        self,
        widget,
        resolved,
        *,
        source_label="FreeCAD",
    ):
        """Copy resolved host geometry into the independent VectorDocument."""

        from woodcam_editor.importers.part_shape import ImportResult, import_freecad_tree

        entities = []
        issues = []
        imported_layers = {}
        shape_sources = []
        for source in resolved:
            if "Sketch" in str(getattr(source, "TypeId", "")):
                result = import_sketch(
                    source,
                    layer_id=widget.document.active_layer_id,
                    include_construction=False,
                )
            elif getattr(source, "Shape", None) is not None:
                shape_sources.append(source)
                continue
            else:
                raise ValueError(
                    "%s não é Sketch nem possui Shape importável."
                    % str(getattr(source, "Label", getattr(source, "Name", "Objeto")))
                )
            entities.extend(result.entities)
            issues.extend(result.issues)
            imported_layers.update(getattr(result, "layers", {}) or {})
        if shape_sources:
            # Traverse the actual Assembly tree before reading any broad
            # face.  A generated furniture object can expose one consolidated
            # Shape while its tree still contains every manufacturing board;
            # importing that consolidated Shape loses the board identity and
            # can mix internal wires from neighbouring parts.
            result = import_freecad_tree(
                shape_sources,
                layer_id=widget.document.active_layer_id,
                flatten_solids=True,
                compound_groups=True,
            )
            entities.extend(result.entities)
            issues.extend(result.issues)
            imported_layers.update(getattr(result, "layers", {}) or {})
        if not entities:
            details = "; ".join(issue.message for issue in issues[:3])
            raise ValueError(details or "A seleção não produziu vetores compatíveis.")
        # Convert the importer role metadata into document-local layers in
        # one atomic command.  External contours, internal cutouts and drill
        # circles therefore arrive already separated; the source FCStd and
        # its Shapes remain untouched.
        from woodcam_editor.importers.layers import remap_import_layers
        from woodcam_editor.application.layers import AddLayerCommand

        combined = ImportResult(
            tuple(entities),
            tuple(issues),
            {
                "source_kind": "freecad_import",
                "source_fingerprint": source_label,
            },
            imported_layers,
        )
        combined = self._annotate_freecad_import_roles(combined)
        remapped = remap_import_layers(combined, widget.document)
        staged_entities, _batch_id, import_delta = prepare_import_batch(
            widget.document,
            remapped.entities,
        )
        commands = [
            AddLayerCommand(layer, make_active=False)
            for layer in remapped.layers
        ]
        commands.append(AddEntitiesCommand(staged_entities))
        widget.controller.execute(
            commands[0] if len(commands) == 1 else CompositeCommand(
                commands,
                label="Importar geometria e camadas",
            )
        )
        # Importar centenas de vetores como selecionados obriga a cena a
        # desenhar todos os nós/handles azuis. Além de poluir a leitura, isso
        # era a principal causa da travada imediata em lotes grandes.
        widget.controller.selection.clear()
        widget.fit_entities(entity.id for entity in staged_entities)
        warning_count = len(issues)
        self._set_vector_editor_status(
            "Importados %d vetor(es) de %d objeto(s) de %s%s%s."
            % (
                len(staged_entities),
                len(resolved),
                source_label,
                "; %d aviso(s) no Console" % warning_count if warning_count else "",
                "; lote posicionado ao lado para não sobrepor o desenho"
                if import_delta.length() > 0.0 else "",
            )
        )
        for issue in issues:
            FreeCAD.Console.PrintWarning("WoodCAM Editor 2D: %s\n" % issue.message)

    @staticmethod
    def _annotate_freecad_import_roles(result):
        """Split an imported snapshot into CAM-role layers.

        Sketches and simple FreeCAD wires do not carry manufacturing layers.
        Classify their closed topology on a temporary read-only facade, then
        add only metadata and source-layer descriptors.  No entity is moved,
        duplicated or rewritten in the source document.
        """

        from dataclasses import replace
        from types import SimpleNamespace
        from woodcam_editor.application.piece_organizer import (
            circle_is_drill,
            classify_document_pieces,
        )
        from woodcam_editor.importers.part_shape import ImportLayerDescriptor

        entities = tuple(result.entities or ())
        if not entities:
            return result
        ids = {str(entity.id): entity for entity in entities if getattr(entity, "id", None)}
        layer_ids = {
            str(getattr(entity, "layer_id", "")): SimpleNamespace(
                purpose="design", visible=True, locked=False
            )
            for entity in entities
            if getattr(entity, "layer_id", None)
        }
        role_by_id = {}
        for entity_id, entity in ids.items():
            role = str((getattr(entity, "metadata", {}) or {}).get("import_role", "") or "")
            if role in {"cut_external", "cut_internal", "drill"}:
                if role == "drill" and not circle_is_drill(entity):
                    role = "cut_internal"
                role_by_id[entity_id] = role
        try:
            classification = classify_document_pieces(
                SimpleNamespace(entities_by_id=ids, layers_by_id=layer_ids)
            )
            for piece in classification.pieces:
                role_by_id.setdefault(str(piece.outer_id), "cut_external")
                for entity_id in piece.descendant_ids:
                    # Only small circular descendants are drills. A larger
                    # circle is a real internal cutout, not a 12 mm hole.
                    entity = ids.get(str(entity_id))
                    if entity is not None and type(entity).__name__ == "CircleEntity":
                        role_by_id.setdefault(
                            str(entity_id),
                            "drill" if circle_is_drill(entity) else "cut_internal",
                        )
                    else:
                        role_by_id.setdefault(str(entity_id), "cut_internal")
        except Exception:
            # Import remains useful even when a malformed source cannot be
            # classified; explicit role metadata from the Shape importer is
            # still preserved below.
            pass
        for entity_id, entity in ids.items():
            if type(entity).__name__ == "CircleEntity":
                role_by_id.setdefault(
                    entity_id,
                    "drill" if circle_is_drill(entity) else "cut_external",
                )

        role_specs = {
            "cut_external": ("Corte externo", "cut", "#f97316"),
            "cut_internal": ("Corte interno", "pocket", "#f59e0b"),
            "drill": ("Furos", "drill", "#2563eb"),
        }
        layers = dict(getattr(result, "layers", {}) or {})
        remapped = []
        for entity in entities:
            entity_id = str(getattr(entity, "id", ""))
            metadata = dict(getattr(entity, "metadata", {}) or {})
            role = role_by_id.get(entity_id)
            if role not in role_specs:
                remapped.append(entity)
                continue
            base_key = str(metadata.get("source_layer_key", "") or "imported")
            suffix = ":" + role
            if base_key.endswith(suffix):
                base_key = base_key[: -len(suffix)]
            # Manufacturing roles are document-wide concepts, not one layer
            # per source board.  The old ``<source>:cut_external`` keys made a
            # single import produce several visually identical rows and made
            # selecting all external contours unnecessarily confusing.  Keep
            # the original source only as provenance metadata.
            role_key = "freecad:" + role
            metadata.update(
                {
                    "import_role": role,
                    "source_layer_key": role_key,
                    "source_layer_base": base_key,
                }
            )
            remapped.append(replace(entity, metadata=metadata))
            name, purpose, color = role_specs[role]
            # Reassign even when the importer already supplied a descriptor;
            # this guarantees the canonical name/purpose/color in the UI.
            layers[role_key] = ImportLayerDescriptor(
                role_key,
                name,
                color=color,
                purpose=purpose,
            )
        # Do not expose source bookkeeping layers when every imported entity
        # has already been classified.  An unclassified entity still keeps
        # its source layer, which is useful for inspection and repair.
        used_layer_keys = {
            str((getattr(entity, "metadata", {}) or {}).get("source_layer_key", "") or "")
            for entity in remapped
        }
        layers = {
            key: descriptor
            for key, descriptor in layers.items()
            if key in used_layer_keys
        }
        return replace(result, entities=tuple(remapped), layers=layers)

    def _vector_editor_cleanup_duplicates(self):
        """Preview exact copies and open redraws covered by closed contours."""

        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        try:
            def is_locked(entity):
                layer = widget.document.layers_by_id.get(
                    getattr(entity, "layer_id", "")
                )
                return bool(layer.locked) if layer is not None else False

            candidate_ids = tuple(
                entity.id
                for entity in widget.document.entities_by_id.values()
                if not is_locked(entity)
            )
            unlocked_ids = []
            for entity_id in candidate_ids:
                entity = widget.document.entities_by_id.get(entity_id)
                if entity is not None and not is_locked(entity):
                    unlocked_ids.append(entity_id)
            unlocked_ids = tuple(unlocked_ids)
            groups = exact_duplicate_entity_groups(
                widget.document,
                entity_ids=unlocked_ids,
            )
            exact_duplicate_ids = tuple(
                entity_id
                for group in groups
                for entity_id in group[1:]
            )
            covered_open_ids = redundant_open_overline_entity_ids(
                widget.document,
                entity_ids=unlocked_ids,
            )
            duplicate_ids = tuple(
                dict.fromkeys(covered_open_ids + exact_duplicate_ids)
            )
            if not duplicate_ids:
                self._set_vector_editor_status(
                    "Nenhuma sobrelinha exata no desenho desbloqueado. "
                    "Sobreposições parciais continuam no Diagnóstico para revisão/Trim."
                )
                return
            preview_entities = tuple(
                widget.document.get_entity(entity_id)
                for entity_id in duplicate_ids
                if widget.document.get_entity(entity_id) is not None
            )

            def apply_cleanup(ids=duplicate_ids):
                widget.controller.execute(DeleteEntitiesCommand(ids))
                widget.controller.selection.clear()
                self._set_vector_editor_status(
                    "%d sobrelinha(s) exata(s) removida(s); Ctrl+Z desfaz."
                    % len(ids)
                )

            widget.begin_workflow_preview(
                preview_entities,
                "Limpeza segura: %d sobrelinha(s) em magenta serão removidas "
                "(%d linha(s) aberta(s) já coberta(s) por contorno fechado; "
                "%d cópia(s) exata(s)). Sobreposições parciais não serão alteradas."
                % (
                    len(duplicate_ids),
                    len(covered_open_ids),
                    len(set(exact_duplicate_ids) - set(covered_open_ids)),
                ),
                apply_cleanup,
                apply_label="Remover duplicados",
            )
        except Exception as error:
            self._set_vector_editor_status(
                "Limpeza de duplicados não executada: %s" % error,
                error=True,
            )

    def _vector_editor_import_file(self):
        """Importa DXF/SVG como um snapshot independente e um único Undo."""
        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        file_path, selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Importar vetores no Editor 2D",
            "",
            "Vetores (*.dxf *.svg);;DXF (*.dxf);;SVG (*.svg)",
        )
        if not file_path:
            return
        try:
            suffix = Path(file_path).suffix.lower()
            if suffix == ".dxf":
                from woodcam_editor.importers.dxf import import_dxf
                try:
                    result = import_dxf(
                        file_path,
                        layer_id=widget.document.active_layer_id,
                        backend="native",
                    )
                except Exception as native_error:
                    FreeCAD.Console.PrintWarning(
                        "WoodCAM Editor 2D: backend DXF nativo falhou (%s); "
                        "tentando o importador isolado do FreeCAD.\n" % native_error
                    )
                    result = import_dxf(
                        file_path,
                        layer_id=widget.document.active_layer_id,
                        backend="freecad",
                    )
            elif suffix == ".svg":
                from woodcam_editor.importers.svg import import_svg
                result = import_svg(
                    file_path,
                    layer_id=widget.document.active_layer_id,
                )
            else:
                raise ValueError("Formato não reconhecido; escolha DXF ou SVG.")
            if not result.entities:
                details = "; ".join(issue.message for issue in result.issues[:3])
                raise ValueError(details or "O arquivo não contém vetores compatíveis.")
            from woodcam_editor.importers.layers import remap_import_layers
            from woodcam_editor.application.layers import AddLayerCommand

            remapped = remap_import_layers(result, widget.document)
            staged_entities, _batch_id, import_delta = prepare_import_batch(
                widget.document,
                remapped.entities,
            )
            import_commands = [
                AddLayerCommand(layer, make_active=False)
                for layer in remapped.layers
            ]
            import_commands.append(AddEntitiesCommand(staged_entities))
            if len(import_commands) == 1:
                widget.controller.execute(import_commands[0])
            else:
                widget.controller.execute(
                    CompositeCommand(import_commands, label="Importar vetores e camadas")
                )
            widget.controller.selection.replace(
                entity.id for entity in staged_entities
            )
            widget.fit_selection()
            for issue in result.issues:
                printer = (
                    FreeCAD.Console.PrintWarning
                    if str(getattr(issue, "severity", "warning")) != "info"
                    else FreeCAD.Console.PrintMessage
                )
                printer("WoodCAM Editor 2D: %s\n" % issue.message)
            self._set_vector_editor_status(
                "Importados %d vetor(es) de %s%s."
                % (
                    len(staged_entities),
                    Path(file_path).name,
                    "; %d aviso(s) no Console" % len(result.issues)
                    if result.issues else "",
                )
                + (
                    " Lote posicionado ao lado para não sobrepor o desenho."
                    if import_delta.length() > 0.0 else ""
                )
            )
        except Exception as error:
            self._set_vector_editor_status(
                "Arquivo não importado: %s" % error,
                error=True,
            )

    def _vector_editor_trace_bitmap(self):
        """Vetoriza bitmap em uma prévia revisável antes de criar entidades."""
        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        file_path, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Vetorizar imagem no Editor 2D",
            "",
            "Imagens (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp);;Todos (*.*)",
        )
        if not file_path:
            return
        try:
            from woodcam_editor.presentation.bitmap_trace_dialog import BitmapTraceDialog
            from woodcam_editor.importers.bitmap_trace import trace_bitmap

            dialog = BitmapTraceDialog(file_path, self)
            execute = getattr(dialog, "exec", None) or getattr(dialog, "exec_")
            if execute() != QtWidgets.QDialog.Accepted:
                return
            result = trace_bitmap(
                file_path,
                layer_id=widget.document.active_layer_id,
                options=dialog.options(),
            )
            if not result.entities:
                details = "; ".join(issue.message for issue in result.issues[:3])
                raise ValueError(details or "Nenhum contorno foi encontrado na imagem.")

            bounds = None
            for entity in result.entities:
                current = entity.bounds()
                bounds = current if bounds is None else bounds.union(current)
            work_area = getattr(widget.document, "work_area", None)
            entities = tuple(result.entities)
            if bounds is not None and work_area is not None:
                margin = min(10.0, work_area.width * 0.02, work_area.height * 0.02)
                transform = Affine2D.translation(
                    work_area.min_x + margin - bounds.min_x,
                    work_area.min_y + margin - bounds.min_y,
                )
                entities = tuple(entity.transformed(transform) for entity in entities)

            def apply_trace(traced=entities):
                widget.controller.execute(AddEntitiesCommand(traced))
                widget.controller.selection.replace(entity.id for entity in traced)
                widget.fit_selection()
                self._set_vector_editor_status(
                    "Vetorização aplicada: %d contorno(s); Ctrl+Z desfaz."
                    % len(traced)
                )

            issue_suffix = (
                "; %d aviso(s) no Console" % len(result.issues)
                if result.issues else ""
            )
            widget.begin_workflow_preview(
                entities,
                "Prévia da vetorização: %d contorno(s)%s. Magenta ainda não altera o desenho."
                % (len(entities), issue_suffix),
                apply_trace,
                apply_label="Aplicar vetorização",
            )
            for issue in result.issues:
                FreeCAD.Console.PrintWarning(
                    "WoodCAM Vetorização: %s\n" % issue.message
                )
        except Exception as error:
            self._set_vector_editor_status(
                "Imagem não vetorizada: %s" % error,
                error=True,
            )

    def _vector_editor_create_text(self):
        """Create portable font outlines as one grouped, undoable vector item."""

        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        try:
            from woodcam_editor.presentation.text_dialog import TextVectorDialog
            from woodcam_editor.presentation.text_vector import create_text_outlines

            dialog = TextVectorDialog(self)
            execute = getattr(dialog, "exec", None) or getattr(dialog, "exec_")
            if execute() != QtWidgets.QDialog.Accepted:
                return
            bounds = widget.controller.selection_bounds()
            if bounds is not None:
                origin = Vec2(bounds.max_x + 10.0, bounds.min_y)
            else:
                work_area = getattr(widget.document, "work_area", None)
                origin = (
                    Vec2(work_area.min_x + 10.0, work_area.min_y + 10.0)
                    if work_area is not None
                    else Vec2(0.0, 0.0)
                )
            result = create_text_outlines(
                dialog.options(),
                layer_id=widget.document.active_layer_id,
                origin=origin,
            )
            widget.controller.execute(AddEntitiesCommand(result.entities))
            widget.controller.selection.select_only(result.group_id)
            self._set_vector_editor_status(
                "Texto convertido em %d contorno(s) vetorial(is); clique/arraste move o conjunto. Ctrl+Z desfaz."
                % (len(result.entities) - 1)
            )
        except Exception as error:
            self._set_vector_editor_status(
                "Texto vetorial não criado: %s" % error,
                error=True,
            )

    def _vector_editor_edit_text(self):
        """Edit an existing text-outline group without losing its placement."""

        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        try:
            from woodcam_editor.domain import GroupEntity, ReplaceTextOutlinesCommand
            from woodcam_editor.presentation.text_dialog import TextVectorDialog
            from woodcam_editor.presentation.text_vector import (
                create_text_outlines,
                text_options_from_group,
            )

            root_ids = widget.controller.canonical_group_selection(
                widget.selected_entity_ids
            )
            text_groups = tuple(
                widget.document.get_entity(entity_id)
                for entity_id in root_ids
                if isinstance(widget.document.get_entity(entity_id), GroupEntity)
                and dict(
                    getattr(widget.document.get_entity(entity_id), "metadata", {}) or {}
                ).get("source_kind") == "text_outline_group"
            )
            if len(text_groups) != 1:
                raise ValueError("Selecione um único texto vetorial para editar.")
            group = text_groups[0]
            widget.controller._editable_entity_ids((group.id,) + tuple(group.child_ids))
            options = text_options_from_group(group)
            bounds = None
            for child_id in group.child_ids:
                child = widget.document.get_entity(child_id)
                current = child.bounds() if child is not None else None
                if current is not None:
                    bounds = current if bounds is None else bounds.union(current)
            if bounds is None:
                raise ValueError("O texto selecionado não possui contornos editáveis.")
            dialog = TextVectorDialog(self, options=options)
            execute = getattr(dialog, "exec", None) or getattr(dialog, "exec_")
            if execute() != QtWidgets.QDialog.Accepted:
                return
            replacement = create_text_outlines(
                dialog.options(),
                layer_id=group.layer_id,
                origin=Vec2(bounds.min_x, bounds.min_y),
                group_id=group.id,
            )
            widget.controller.execute(
                ReplaceTextOutlinesCommand(
                    group.id,
                    group.child_ids,
                    replacement.entities,
                )
            )
            widget.controller.selection.select_only(group.id)
            self._set_vector_editor_status(
                "Texto vetorial atualizado; posição e grupo foram preservados. Ctrl+Z desfaz."
            )
        except Exception as error:
            self._set_vector_editor_status(
                "Texto vetorial não editado: %s" % error,
                error=True,
            )

    def _vector_editor_create_image_relief(self):
        """Abre o fluxo não modal imagem → heightmap → relevo persistente."""
        document = FreeCAD.ActiveDocument
        if document is None:
            self._set_vector_editor_status(
                "Abra ou crie um documento FreeCAD antes de criar o relevo.",
                error=True,
            )
            return
        file_path, _selected_filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Criar relevo 3D por imagem",
            "",
            "Imagens (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp);;Todos (*.*)",
        )
        if not file_path:
            return
        previous = getattr(self, "_image_relief_dialog", None)
        if previous is not None:
            try:
                previous.reject()
            except Exception:
                pass
        try:
            from woodcam_relief.dialog import ReliefImageDialog
            from woodcam_relief.freecad_adapter import (
                FreeCADReliefPreview,
                create_persistent_relief,
            )
            from woodcam_relief.heightmap import build_relief_mesh

            vector_document = getattr(self, "_vector_editor_document", None)
            work_area = getattr(vector_document, "work_area", None)
            default_origin = (
                float(getattr(work_area, "min_x", 0.0)),
                float(getattr(work_area, "min_y", 0.0)),
            )
            try:
                gui_document = FreeCADGui.getDocument(document.Name)
            except Exception:
                gui_document = None
            overlay = FreeCADReliefPreview(gui_document)
            relief_parent = self.parentWidget() or self
            dialog = ReliefImageDialog(
                file_path,
                relief_parent,
                default_origin=default_origin,
            )
            self._image_relief_dialog = dialog
            self._image_relief_overlay = overlay
            preview_fitted = [False]

            def update_preview(data):
                if FreeCAD.ActiveDocument is not document:
                    dialog.status.setText(
                        "O documento ativo mudou; feche esta janela e inicie o relevo novamente."
                    )
                    return
                mesh = build_relief_mesh(
                    data,
                    max_grid=min(384, int(data.options.mesh_resolution)),
                )
                overlay.update(mesh)
                if not preview_fitted[0] and gui_document is not None:
                    try:
                        gui_document.ActiveView.viewAxonometric()
                        gui_document.ActiveView.fitAll()
                    except Exception:
                        pass
                    preview_fitted[0] = True

            def clear_preview():
                overlay.clear()
                if getattr(self, "_image_relief_overlay", None) is overlay:
                    self._image_relief_overlay = None

            def apply_relief():
                data = dialog.current_data
                clear_preview()
                if data is None:
                    self._set_vector_editor_status(
                        "Relevo não criado: a prévia do mapa está inválida.",
                        error=True,
                    )
                    return
                if FreeCAD.ActiveDocument is not document:
                    self._set_vector_editor_status(
                        "Relevo não criado porque o documento ativo mudou.",
                        error=True,
                    )
                    return
                try:
                    mesh = build_relief_mesh(
                        data,
                        max_grid=min(384, int(data.options.mesh_resolution)),
                    )
                    obj = create_persistent_relief(document, data, mesh)
                    try:
                        FreeCADGui.Selection.clearSelection()
                        if gui_document is not None:
                            gui_document.ActiveView.fitAll()
                    except Exception:
                        pass
                    self._set_vector_editor_status(
                        "Relevo 3D criado com %d facetas visuais; o CAM usa o mapa preservado. Ctrl+Z desfaz."
                        % mesh.facet_count
                    )
                except Exception as error:
                    self._set_vector_editor_status(
                        "Relevo não criado: %s" % error,
                        error=True,
                    )

            def release_dialog(*_args):
                clear_preview()
                if getattr(self, "_image_relief_dialog", None) is dialog:
                    self._image_relief_dialog = None

            dialog.previewChanged.connect(update_preview)
            dialog.accepted.connect(apply_relief)
            dialog.rejected.connect(clear_preview)
            dialog.destroyed.connect(release_dialog)
            if relief_parent is not None:
                dialog.move(
                    max(0, int((relief_parent.width() - dialog.width()) / 2)),
                    max(0, int((relief_parent.height() - dialog.height()) / 2)),
                )
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            self._set_vector_editor_status(
                "Ajuste o mapa na janela e confira a malha dourada temporária na vista 3D."
            )
        except Exception as error:
            self._set_vector_editor_status(
                "Não foi possível iniciar o relevo: %s" % error,
                error=True,
            )

    def _vector_editor_export_file(self):
        """Exporta a seleção ou, sem seleção, todas as camadas visíveis."""
        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        file_path, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Exportar vetores do Editor 2D",
            "desenho_woodcam.svg",
            "SVG (*.svg);;DXF (*.dxf)",
        )
        if not file_path:
            return
        try:
            suffix = Path(file_path).suffix.lower()
            if not suffix:
                suffix = ".dxf" if "DXF" in selected_filter.upper() else ".svg"
                file_path += suffix
            entity_ids = tuple(widget.selected_entity_ids) or None
            if suffix == ".dxf":
                from woodcam_editor.exporters.dxf import export_dxf
                target = export_dxf(
                    widget.document,
                    file_path,
                    entity_ids=entity_ids,
                    visible_only=True,
                )
            elif suffix == ".svg":
                from woodcam_editor.exporters.svg import export_svg
                target = export_svg(
                    widget.document,
                    file_path,
                    entity_ids=entity_ids,
                    visible_only=True,
                )
            else:
                raise ValueError("Formato não reconhecido; use a extensão .dxf ou .svg.")
            self._set_vector_editor_status(
                "Vetores exportados para %s%s."
                % (
                    str(target),
                    " (somente a seleção)" if entity_ids else "",
                )
            )
        except Exception as error:
            self._set_vector_editor_status(
                "Arquivo não exportado: %s" % error,
                error=True,
            )

    def _vector_editor_diagnose(self):
        document = getattr(self, "_vector_editor_document", None)
        if document is None:
            return None
        busy = self._vector_editor_busy_dialog("Diagnosticando vetores… aguarde.")
        try:
            widget = getattr(self, "vector_editor_widget", None)
            if widget is not None:
                widget.cancel_workflow_preview()
            tolerance = self._vector_editor_join_tolerance()
            report = validate_document(document, join_tolerance=tolerance)
            counts = {"blocker": 0, "error": 0, "warning": 0, "info": 0}
            for issue in report.issues:
                counts[issue.severity.value] = counts.get(issue.severity.value, 0) + 1
            if report.issues:
                first = report.issues[0]
                self._set_vector_editor_status(
                    "Diagnóstico: %d bloqueio(s), %d erro(s), %d aviso(s). Primeiro: %s"
                    % (
                        counts.get("blocker", 0),
                        counts.get("error", 0),
                        counts.get("warning", 0),
                        first.message,
                    ),
                    error=bool(report.blockers),
                )
            else:
                self._set_vector_editor_status("Diagnóstico: desenho válido, sem ocorrências.")
            dialog = getattr(self, "_vector_validation_dialog", None)
            if dialog is None:
                dialog = ValidationReportDialog(report, self)
                dialog.issueSelected.connect(
                    self._focus_vector_editor_validation_issue
                )
                dialog.issueActivated.connect(
                    self._focus_vector_editor_validation_issue
                )
                self._vector_validation_dialog = dialog
            else:
                dialog.set_report(report)
            dialog.show()
            try:
                dialog.raise_()
            except Exception:
                pass
            if report.issues:
                dialog.select_issue(report.issues[0].id)
            return report
        except Exception as error:
            self._set_vector_editor_status("Diagnóstico não executado: %s" % error, error=True)
            return None
        finally:
            self._close_vector_editor_busy_dialog(busy)

    def _focus_vector_editor_validation_issue(self, issue):
        widget = getattr(self, "vector_editor_widget", None)
        if widget is not None:
            widget.focus_validation_issue(issue)

    def _vector_editor_join_tolerance(self):
        widget = getattr(self, "vector_editor_widget", None)
        field = getattr(widget, "join_tolerance", None) if widget is not None else None
        return float(field.value()) if field is not None else 0.2

    def _vector_editor_group_selection(self):
        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        try:
            entity_ids = widget.controller.canonical_group_selection(
                widget.selected_entity_ids
            )
            if len(entity_ids) < 2:
                raise ValueError("Selecione dois ou mais objetos para agrupar.")
            from woodcam_editor.domain import GroupEntitiesCommand

            command = GroupEntitiesCommand(entity_ids)
            widget.controller.execute(command)
            widget.controller.selection.select_only(command.group_id)
            self._set_vector_editor_status(
                "Objetos agrupados; clique em qualquer parte para mover o conjunto. Ctrl+Z desfaz."
            )
        except Exception as error:
            self._set_vector_editor_status("Agrupamento não executado: %s" % error, error=True)

    def _vector_editor_ungroup_selection(self):
        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        try:
            from woodcam_editor.domain import GroupEntity, UngroupEntitiesCommand

            group_ids = tuple(
                entity_id
                for entity_id in widget.controller.canonical_group_selection(
                    widget.selected_entity_ids
                )
                if isinstance(widget.document.get_entity(entity_id), GroupEntity)
            )
            if not group_ids:
                raise ValueError("Selecione um grupo; clique em qualquer elemento agrupado primeiro.")
            children = tuple(
                child_id
                for group_id in group_ids
                for child_id in widget.document.get_entity(group_id).child_ids
            )
            widget.controller.execute(UngroupEntitiesCommand(group_ids))
            widget.controller.selection.replace(children)
            self._set_vector_editor_status("Grupo desfeito; os vetores foram preservados. Ctrl+Z desfaz.")
        except Exception as error:
            self._set_vector_editor_status("Desagrupamento não executado: %s" % error, error=True)

    def _vector_editor_close_selected(self, mode="line"):
        """Preview one explicit Aspire-style close operation before mutation."""

        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        labels = {
            "line": "uma linha reta",
            "smooth": "uma curva suave",
            "midpoint": "aproximando as duas pontas",
        }
        mode = str(mode).lower()
        try:
            path_ids = tuple(widget.selected_entity_ids)
            if len(path_ids) != 1:
                raise ValueError("Selecione exatamente um caminho aberto.")
            path = widget.document.get_entity(path_ids[0])
            if not isinstance(path, PathEntity) or path.closed:
                raise ValueError("Selecione exatamente um caminho aberto.")
            layer = widget.document.layers_by_id.get(path.layer_id)
            if layer is not None and layer.locked:
                raise ValueError("Desbloqueie a camada antes de fechar o caminho.")
            if mode not in labels:
                raise ValueError("Modo de fechamento desconhecido.")
            preview_document = widget.document.clone()
            ClosePathCommand(path.id, mode=mode).apply(preview_document)
            preview_entity = preview_document.get_entity(path.id)

            def apply_close(path_id=path.id, close_mode=mode):
                widget.controller.execute(ClosePathCommand(path_id, mode=close_mode))
                widget.controller.selection.select_only(path_id)
                self._set_vector_editor_status(
                    "Caminho fechado com %s; Ctrl+Z desfaz." % labels[close_mode]
                )

            widget.begin_workflow_preview(
                (preview_entity,),
                "Prévia: fechar o caminho selecionado com %s." % labels[mode],
                apply_close,
                apply_label="Aplicar fechamento",
            )
        except Exception as error:
            self._set_vector_editor_status(
                "Fechamento não executado: %s" % error,
                error=True,
            )

    def _vector_editor_fit_curves(self):
        """Offer a conservative exact arc/circle fit for selected polylines."""

        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        try:
            tolerance, accepted = QtWidgets.QInputDialog.getDouble(
                self,
                "Ajustar arcos/círculos",
                "Desvio máximo permitido (mm):",
                0.20,
                0.001,
                1000.0,
                3,
            )
            if not accepted:
                return
            from woodcam_editor.geometry.curve_fit import fit_polyline_to_arc

            source_ids = widget.controller.expand_group_children(
                widget.selected_entity_ids
            )
            replacements = []
            skipped = []
            for entity_id in source_ids:
                entity = widget.document.get_entity(entity_id)
                if entity is None:
                    continue
                fitted = fit_polyline_to_arc(entity, tolerance)
                if fitted is None:
                    skipped.append(entity_id)
                else:
                    replacements.append(fitted)
            if not replacements:
                raise ValueError(
                    "Nenhum vetor selecionado ficou dentro da tolerância; nada foi alterado."
                )

            def apply_fit(values=tuple(replacements)):
                widget.controller.execute(ReplaceEntitiesCommand(values))
                widget.controller.selection.replace(entity.id for entity in values)
                self._set_vector_editor_status(
                    "%d vetor(es) ajustado(s) a arcos/círculos exatos%s; Ctrl+Z desfaz."
                    % (
                        len(values),
                        "; %d mantido(s) sem alteração" % len(skipped) if skipped else "",
                    )
                )

            widget.begin_workflow_preview(
                tuple(replacements),
                "Prévia: %d vetor(es) serão ajustados; %d não atendem à tolerância e serão preservados."
                % (len(replacements), len(skipped)),
                apply_fit,
                apply_label="Aplicar ajuste de curvas",
            )
        except Exception as error:
            self._set_vector_editor_status(
                "Ajuste de curvas não executado: %s" % error,
                error=True,
            )

    def _vector_editor_create_offset_contours(self):
        """Create Aspire-style separate boundaries without consuming artwork."""

        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        try:
            distance, accepted = QtWidgets.QInputDialog.getDouble(
                self,
                "Criar contorno (offset)",
                "Distância: positivo externo / negativo interno (mm):",
                2.0,
                -1000.0,
                1000.0,
                3,
            )
            if not accepted:
                return
            if abs(float(distance)) <= 1.0e-12:
                raise ValueError("Informe uma distância diferente de zero.")
            from woodcam_editor.geometry.modifiers import preview_create_offset_contour

            source_ids = widget.controller.expand_group_children(
                widget.selected_entity_ids
            )
            previews = []
            skipped = []
            for entity_id in source_ids:
                entity = widget.document.get_entity(entity_id)
                if entity is None:
                    continue
                try:
                    previews.append(preview_create_offset_contour(entity, distance))
                except Exception:
                    skipped.append(entity_id)
            if not previews:
                raise ValueError(
                    "Selecione círculos ou contornos lineares fechados que possam receber offset."
                )
            created = tuple(
                preview.result_entities[-1] for preview in previews
            )

            def apply_contours(values=tuple(previews), created_ids=tuple(entity.id for entity in created)):
                widget.controller.execute(
                    CompositeCommand(
                        tuple(ApplyModifierPreviewCommand(preview) for preview in values),
                        label="Criar contornos offset",
                    )
                )
                widget.controller.selection.replace(created_ids)
                self._set_vector_editor_status(
                    "%d contorno(s) criado(s) sem alterar os originais%s; Ctrl+Z desfaz."
                    % (
                        len(created_ids),
                        "; %d vetor(es) não compatível(is) preservado(s)" % len(skipped)
                        if skipped else "",
                    )
                )

            widget.begin_workflow_preview(
                created,
                "Prévia: criar %d contorno(s) separado(s) a %+.3f mm; os vetores originais serão preservados."
                % (len(created), float(distance)),
                apply_contours,
                apply_label="Criar contornos",
            )
        except Exception as error:
            self._set_vector_editor_status(
                "Criação de contorno não executada: %s" % error,
                error=True,
            )

    def _vector_editor_reverse_directions(self):
        """Reverse selected path directions as one undoable editor command."""

        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        try:
            path_ids = widget.controller.reverse_path_directions(
                widget.selected_entity_ids
            )
            if not path_ids:
                raise ValueError(
                    "Selecione ao menos um contorno ou caminho; círculos não possuem sentido independente."
                )
            self._set_vector_editor_status(
                "Direção invertida em %d caminho(s); geometria, furos e grupo foram preservados. Ctrl+Z desfaz."
                % len(path_ids)
            )
        except Exception as error:
            self._set_vector_editor_status(
                "Inversão de direção não executada: %s" % error,
                error=True,
            )

    def _vector_editor_boolean_selection(self, operation):
        """Preview an exact OCC boolean; the document changes only on Apply."""

        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        labels = {
            "union": "soldar",
            "difference": "subtrair",
            "intersection": "interseção",
            "overlap": "sobrepor",
        }
        label = labels.get(str(operation), str(operation))
        try:
            selected_ids = tuple(widget.selected_entity_ids)
            # A imported panel is normally represented as one GroupEntity so
            # its outer profile and every internal hole move together.  OCC
            # booleans operate on drawable closed vectors, therefore expand a
            # selected group here instead of forcing the operator to ungroup a
            # valid furniture part just to create/subtract an internal cutout.
            entity_ids = widget.controller.expand_group_children(selected_ids)
            entities = tuple(
                widget.document.get_entity(entity_id)
                for entity_id in entity_ids
            )
            entities = tuple(
                entity
                for entity in entities
                if isinstance(entity, (CircleEntity, EllipseEntity))
                or (isinstance(entity, PathEntity) and entity.closed)
            )
            if len(entities) < 2:
                raise ValueError("Selecione ao menos dois vetores fechados.")
            locked = [
                entity.id
                for entity in entities
                if bool(
                    getattr(widget.document.layers_by_id.get(entity.layer_id), "locked", False)
                )
            ]
            if locked:
                raise ValueError(
                    "Desbloqueie a camada antes de %s: %s"
                    % (label, ", ".join(locked))
                )
            from woodcam_editor.adapters.freecad_boolean import preview_boolean

            preview = preview_boolean(operation, entities)
            result_ids = tuple(entity.id for entity in preview.result_entities)

            def apply_boolean(current_preview=preview, current_label=label):
                widget.controller.apply_modifier_preview(current_preview)
                detail = (
                    " O furo/recorte interno agora pertence à peça, mesmo sem tocar a borda."
                    if current_label == "subtrair"
                    else ""
                )
                self._set_vector_editor_status(
                    "Booleano '%s' aplicado;%s Ctrl+Z desfaz."
                    % (current_label, detail)
                )

            widget.begin_workflow_preview(
                preview.result_entities,
                "Prévia: %s %d vetor(es) fechado(s) → %d resultado(s)."
                % (label.capitalize(), len(entities), len(result_ids)),
                apply_boolean,
                apply_label="Aplicar %s" % label,
            )
        except Exception as error:
            self._set_vector_editor_status(
                "Booleano '%s' não executado: %s" % (label, error),
                error=True,
            )

    def _vector_editor_join_open_paths(self):
        """Preview Aspire-style joining for every selected compatible path."""

        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        try:
            source_ids = widget.controller.expand_group_children(
                widget.selected_entity_ids
            )
            path_ids = tuple(
                entity_id
                for entity_id in source_ids
                if isinstance(widget.document.get_entity(entity_id), PathEntity)
                and not widget.document.get_entity(entity_id).closed
            )
            if len(path_ids) < 2:
                raise ValueError("Selecione ao menos dois vetores abertos.")
            locked = [
                entity_id for entity_id in path_ids
                if bool(
                    getattr(
                        widget.document.layers_by_id.get(
                            widget.document.get_entity(entity_id).layer_id
                        ),
                        "locked", False,
                    )
                )
            ]
            if locked:
                raise ValueError(
                    "Desbloqueie a camada antes de unir: %s" % ", ".join(locked)
                )
            tolerance = self._vector_editor_join_tolerance()
            preview_document = widget.document.clone()
            preview_command = JoinOpenPathsWithinToleranceCommand(path_ids, tolerance)
            preview_command.apply(preview_document)
            preview_entities = tuple(
                preview_document.get_entity(entity_id)
                for entity_id, _removed_id, _gap in preview_command.joined_pairs
            )
            preview_entities = tuple(entity for entity in preview_entities if entity is not None)
            gaps = tuple(gap for _first_id, _second_id, gap in preview_command.joined_pairs)

            def apply_join_all(ids=path_ids, join_tolerance=tolerance):
                command = JoinOpenPathsWithinToleranceCommand(ids, join_tolerance)
                widget.controller.execute(command)
                surviving = tuple(first_id for first_id, _second_id, _gap in command.joined_pairs)
                widget.controller.selection.replace(surviving)
                self._set_vector_editor_status(
                    "%d união(ões) aplicada(s), maior lacuna %.4g mm; Ctrl+Z desfaz."
                    % (len(command.joined_pairs), max(gap for _first, _second, gap in command.joined_pairs))
                )

            widget.begin_workflow_preview(
                preview_entities,
                "Prévia: %d união(ões) dentro de %.4g mm; maior lacuna %.4g mm. "
                "Vetores fora da tolerância foram preservados."
                % (len(gaps), tolerance, max(gaps)),
                apply_join_all,
                apply_label="Aplicar união de vetores",
            )
        except Exception as error:
            self._set_vector_editor_status(
                "União de vetores não executada: %s" % error,
                error=True,
            )

    def _vector_editor_repair_selection(self):
        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        try:
            ids = tuple(widget.selected_entity_ids)
            paths = [widget.controller.get_entity(entity_id) for entity_id in ids]
            locked_paths = [
                path.id
                for path in paths
                if path is not None
                and bool(
                    widget.document.layers_by_id.get(path.layer_id).locked
                    if widget.document.layers_by_id.get(path.layer_id) is not None
                    else False
                )
            ]
            if locked_paths:
                raise ValueError(
                    "Desbloqueie a camada antes de reparar: %s"
                    % ", ".join(locked_paths)
                )
            if len(paths) == 1:
                path = paths[0]
                if not isinstance(path, PathEntity) or path.closed:
                    raise ValueError("Selecione um único caminho aberto para fechá-lo.")
                preview_document = widget.document.clone()
                ClosePathCommand(path.id, mode="line").apply(preview_document)
                preview_entity = preview_document.get_entity(path.id)

                def apply_close(path_id=path.id):
                    widget.controller.execute(
                        ClosePathCommand(path_id, mode="line")
                    )
                    widget.controller.selection.select_only(path_id)
                    self._set_vector_editor_status(
                        "Caminho fechado por uma linha; Ctrl+Z desfaz."
                    )

                widget.begin_workflow_preview(
                    (preview_entity,),
                    "Prévia: fechar o caminho selecionado com uma linha reta.",
                    apply_close,
                    apply_label="Aplicar fechamento",
                )
                return
            if len(paths) != 2 or any(
                not isinstance(path, PathEntity) or path.closed for path in paths
            ):
                raise ValueError(
                    "Selecione um caminho aberto para fechar ou dois caminhos abertos para unir."
                )
            first, second = paths
            candidates = []
            for first_name, first_point in (("start", first.start), ("end", first.end)):
                for second_name, second_point in (("start", second.start), ("end", second.end)):
                    candidates.append(
                        (first_point.distance_to(second_point), first_name, second_name)
                    )
            distance, first_name, second_name = min(candidates)
            tolerance = self._vector_editor_join_tolerance()
            if distance > tolerance:
                raise ValueError(
                    "As pontas mais próximas estão a %.4g mm; aumente a tolerância "
                    "conscientemente ou aproxime-as com edição de nós." % distance
                )
            preview_document = widget.document.clone()
            JoinPathsCommand(
                first.id,
                second.id,
                first_name,
                second_name,
                tolerance,
            ).apply(preview_document)
            preview_entity = preview_document.get_entity(first.id)

            def apply_join(
                first_id=first.id,
                second_id=second.id,
                first_end=first_name,
                second_end=second_name,
                join_tolerance=tolerance,
                gap=distance,
            ):
                widget.controller.execute(
                    JoinPathsCommand(
                        first_id,
                        second_id,
                        first_end,
                        second_end,
                        join_tolerance,
                    )
                )
                widget.controller.selection.select_only(first_id)
                self._set_vector_editor_status(
                    "Caminhos unidos (lacuna %.4g mm); Ctrl+Z desfaz." % gap
                )

            widget.begin_workflow_preview(
                (preview_entity,),
                "Prévia: unir os dois caminhos; lacuna %.4g mm." % distance,
                apply_join,
                apply_label="Aplicar união",
            )
        except Exception as error:
            self._set_vector_editor_status("Fechamento/união não executado: %s" % error, error=True)

    def _vector_editor_create_pieces(self, *, raise_on_error=False):
        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return []
        busy = self._vector_editor_busy_dialog(
            "Reconhecendo peças, furos e recortes… aguarde."
        )
        try:
            report = validate_document(
                widget.document,
                join_tolerance=self._vector_editor_join_tolerance(),
            )
            blockers = [
                issue for issue in report.blockers
                if issue.code not in {
                    "OPEN_PATH",
                    "OUTSIDE_WORK_AREA",
                    "REDUNDANT_OPEN_OVERLINES",
                    "TOUCHING_CONTOURS",
                }
            ]
            if blockers:
                raise ValueError(
                    "%d bloqueio(s); resolva primeiro: %s"
                    % (len(blockers), blockers[0].message)
                )
            classification = classify_document_pieces(widget.document)
            if not classification.pieces:
                raise ValueError("Nenhum contorno fechado válido foi encontrado.")
            from dataclasses import replace

            previous_by_outer = {
                piece.outer_path_id: piece
                for piece in widget.document.pieces_by_id.values()
            }
            default_thickness = self._material_thickness_for_model_position()
            pieces = []
            for index, classified in enumerate(classification.pieces, start=1):
                previous = previous_by_outer.get(classified.outer_id)
                if previous is None:
                    piece = Piece2D(
                        id=classified.piece_id,
                        name="Peça %02d" % index,
                        outer_path_id=classified.outer_id,
                        inner_path_ids=classified.descendant_ids,
                        thickness=default_thickness,
                    )
                else:
                    piece = replace(
                        previous,
                        inner_path_ids=classified.descendant_ids,
                        thickness=(
                            previous.thickness
                            if previous.thickness > 0.0
                            else default_thickness
                        ),
                        stale=False,
                    )
                pieces.append(piece)
            # Recognition creates only Piece2D relationships. The original
            # vectors/layers remain untouched; CAM derives external, internal
            # and drill roles from containment and the 12 mm circle rule.
            widget.controller.execute(ReplacePiecesCommand(pieces))
            self._set_vector_editor_status(
                "Reconhecidas %d peça(s) sem criar novos vetores; contornos "
                "internos ficaram vinculados como furos/recortes%s."
                % (
                    len(pieces),
                    "; %d caminho(s) aberto(s) não entraram nas peças"
                    % len(classification.open_entity_ids)
                    if classification.open_entity_ids else "",
                )
            )
            return classification.pieces
        except Exception as error:
            self._set_vector_editor_status(
                "Peças/furos não reconhecidos: %s" % error, error=True
            )
            if raise_on_error:
                raise ValueError(str(error)) from error
            return []
        finally:
            self._close_vector_editor_busy_dialog(busy)

    def _vector_editor_request_organize(self, search_mode="balanced"):
        """Ask for job-specific clearance, then calculate a preview."""
        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        spacing, accepted = QtWidgets.QInputDialog.getDouble(
            self,
            "Espaçamento entre peças",
            "Folga mínima entre peças para o nesting (mm):",
            float(widget.nesting_spacing.value()),
            0.0,
            1000.0,
            2,
        )
        if not accepted:
            return
        self._vector_editor_organize_pieces(search_mode, spacing=float(spacing))

    def _vector_editor_organize_pieces(self, search_mode="balanced", spacing=None):
        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        if spacing is None:
            spacing = float(widget.nesting_spacing.value())
        widget.nesting_spacing.setValue(float(spacing))
        busy = self._vector_editor_busy_dialog("Calculando organização das peças… aguarde.")
        try:
            stored_area = widget.document.work_area
            bounds = (
                (
                    stored_area.min_x,
                    stored_area.min_y,
                    stored_area.max_x,
                    stored_area.max_y,
                )
                if stored_area is not None
                else self._work_area_bounds_for_preview(
                    self._collect_work_setup_settings()
                )
            )
            if bounds is None:
                raise ValueError("Defina largura e altura na aba Trabalho.")
            classification = classify_document_pieces(widget.document)
            if classification.open_entity_ids:
                raise ValueError(
                    "Há %d caminho(s) aberto(s); organize somente após corrigi-los."
                    % len(classification.open_entity_ids)
                )
            if not classification.pieces:
                raise ValueError("Nenhuma peça fechada foi encontrada.")
            locked_pieces = []
            for classified_piece in classification.pieces:
                entity_ids = (
                    classified_piece.outer_id,
                ) + tuple(classified_piece.descendant_ids)
                if any(
                    bool(widget.document.layers_by_id[widget.document.get_entity(entity_id).layer_id].locked)
                    for entity_id in entity_ids
                ):
                    locked_pieces.append(classified_piece.piece_id)
            if locked_pieces:
                raise ValueError(
                    "Desbloqueie as camadas das peças antes de organizar: %s"
                    % ", ".join(locked_pieces)
                )
            metadata_by_outer = {
                piece.outer_path_id: piece
                for piece in widget.document.pieces_by_id.values()
            }
            rotations = {
                piece.piece_id: tuple(
                    metadata_by_outer.get(piece.outer_id).rotations_allowed
                    if piece.outer_id in metadata_by_outer
                    else (0.0, 90.0)
                )
                for piece in classification.pieces
            }
            result = organize_pieces(
                classification.pieces,
                bounds,
                spacing=float(spacing),
                rotations=rotations,
                search_mode=search_mode,
            )
            replacements = []
            preview_entities = []
            classified_by_id = {
                piece.piece_id: piece for piece in classification.pieces
            }
            for placement in result.placements:
                classified = classified_by_id[placement.piece_id]
                angle = float(placement.rotation_degrees or 0.0)
                if abs(angle) <= 1e-9:
                    transform = Affine2D.translation(
                        Vec2(placement.dx, placement.dy)
                    )
                else:
                    min_x, min_y, max_x, max_y = classified.bounds
                    center = Vec2((min_x + max_x) * 0.5, (min_y + max_y) * 0.5)
                    rotation = Affine2D.rotation(math.radians(angle), center)
                    rotated_corners = tuple(
                        rotation.apply_to_point(Vec2(x_value, y_value))
                        for x_value, y_value in (
                            (min_x, min_y),
                            (max_x, min_y),
                            (max_x, max_y),
                            (min_x, max_y),
                        )
                    )
                    rotated_min_x = min(point.x for point in rotated_corners)
                    rotated_min_y = min(point.y for point in rotated_corners)
                    translation = Affine2D.translation(
                        placement.placed_bounds[0] - rotated_min_x,
                        placement.placed_bounds[1] - rotated_min_y,
                    )
                    transform = translation @ rotation
                for entity_id in placement.entity_ids:
                    source_entity = widget.document.get_entity(entity_id)
                    transformed_entity = source_entity.transformed(transform)
                    preview_entities.append(transformed_entity)
                    if not transform.is_identity(1e-9):
                        replacements.append(transformed_entity)
            if not replacements:
                raise ValueError(
                    "As peças que cabem já estão nas posições calculadas."
                )
            # All vectors move in one replacement command.  The prior version
            # built one TransformEntitiesCommand per piece; applying a cabinet
            # layout then repeatedly rebuilt the scene and appeared frozen.
            commands = [ReplaceEntitiesCommand(replacements)]
            commands.append(
                SetDocumentMetadataCommand(
                    "organization_sheet_bounds",
                    [list(value) for value in result.sheet_bounds],
                )
            )
            if widget.document.pieces_by_id:
                from dataclasses import replace
                commands.append(
                    ReplacePiecesCommand(
                        replace(piece, stale=False)
                        for piece in widget.document.pieces_by_id.values()
                    )
                )
            organize_command = CompositeCommand(
                commands,
                label="Organizar peças 2D",
            )
            extra_quantity = sum(
                max(0, piece.quantity - 1)
                for piece in widget.document.pieces_by_id.values()
            )
            summary = (
                "Prévia do nesting inteligente: magenta = destino, azul = posição atual. "
                "%d peça(s) em %d chapa(s); %d não couberam. Eficiência %.1f%%; "
                "%s venceu entre %d layouts%s."
                % (
                    len(result.placements),
                    len(result.sheet_bounds),
                    len(result.unplaced_piece_ids),
                    result.utilization_percent,
                    result.strategy or "encaixe",
                    result.evaluated_layouts,
                    "; %d ocorrência(s) extra serão replicadas pelo PanelNest"
                    % extra_quantity if extra_quantity else "",
                )
            )

            def apply_organization():
                widget.controller.execute(organize_command)
                if result.unplaced_piece_ids:
                    self._set_vector_editor_status(
                        "Organizadas %d em %d chapa(s); %d peça(s) não cabem nem em uma chapa vazia."
                        % (
                            len(result.placements),
                            len(result.sheet_bounds),
                            len(result.unplaced_piece_ids),
                        ),
                        error=True,
                    )
                else:
                    self._set_vector_editor_status(
                        "Organizadas %d peça(s) em %d chapa(s), sempre com seus furos/recortes%s."
                        % (
                            len(result.placements),
                            len(result.sheet_bounds),
                            "; quantidades extras seguem para o PanelNest"
                            if extra_quantity else "",
                        )
                    )

            widget.begin_workflow_preview(
                preview_entities,
                summary,
                apply_organization,
                apply_label="Aplicar organização",
                sheet_bounds=result.sheet_bounds,
            )
        except Exception as error:
            self._set_vector_editor_status("Organização não executada: %s" % error, error=True)
        finally:
            self._close_vector_editor_busy_dialog(busy)

    def _vector_editor_send_panelnest(self):
        """Materializa peças completas, incluindo todos os recortes, para PanelNest."""
        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        try:
            # Piece2D is a relationship snapshot, not a second geometry
            # source.  It may predate a later import, copy or newly drawn
            # hole.  Rebuild that relationship from the current immutable
            # vectors immediately before every exchange; otherwise the
            # bridge can validly materialize an obsolete subset and make
            # recent panels/holes appear to vanish in PanelNest.
            self._vector_editor_create_pieces(raise_on_error=True)
            if not widget.document.pieces_by_id:
                raise ValueError("Crie ao menos uma Peça 2D válida antes de enviar.")
            from woodcam_editor.adapters.panelnest import send_document_to_panelnest

            result = send_document_to_panelnest(
                widget.document,
                freecad_document=FreeCAD.ActiveDocument,
            )
            group = FreeCAD.ActiveDocument.getObject(result.group_name)
            if group is not None:
                FreeCADGui.Selection.clearSelection()
                FreeCADGui.Selection.addSelection(group)
            for warning in result.warnings:
                FreeCAD.Console.PrintWarning(
                    "WoodCAM Editor 2D → PanelNest: %s\n" % warning
                )
            occurrence_count = len(result.object_names)
            hole_count = sum(
                item.circular_hole_count * item.quantity
                for item in result.items
            )
            inner_count = sum(
                item.inner_profile_count * item.quantity
                for item in result.items
            )
            self._set_vector_editor_status(
                "Enviadas %d peça(s), %d ocorrência(s), %d furo(s) e %d recorte(s) "
                "ao PanelNest, mantendo XY/rotação do Editor; nenhum nesting foi "
                "executado%s."
                % (
                    len(result.items),
                    occurrence_count,
                    hole_count,
                    inner_count,
                    "; confira %d aviso(s) no Console" % len(result.warnings)
                    if result.warnings else "",
                )
            )
        except Exception as error:
            self._set_vector_editor_status(
                "Envio ao PanelNest não executado: %s" % error,
                error=True,
            )

    def _toggle_vector_editor_cam_source(self):
        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        if not self._use_vector_editor_for_cam:
            try:
                self._vector_editor_geometry_for_cam()
            except Exception as error:
                widget.set_cam_source_active(False)
                self._set_vector_editor_status(
                    "Editor não pode ser usado no CAM: %s" % error,
                    error=True,
                )
                return
        self._use_vector_editor_for_cam = not self._use_vector_editor_for_cam
        widget.set_cam_source_active(self._use_vector_editor_for_cam)
        self._set_vector_editor_status(
            "Fonte do CAM: %s"
            % ("Editor 2D" if self._use_vector_editor_for_cam else "seleção do FreeCAD")
        )

    def _vector_editor_geometry_for_cam(self):
        widget = getattr(self, "vector_editor_widget", None)
        if widget is None or not widget.document.entities_by_id:
            raise ValueError("O Editor 2D ainda não possui vetores.")
        report = validate_document(
            widget.document,
            join_tolerance=self._vector_editor_join_tolerance(),
        )
        if report.blockers:
            raise GeometryAdapterError(report.blockers[0].message)
        selected = tuple(widget.selected_entity_ids)
        if selected:
            # Clicking an imported board selects its persistent GroupEntity.
            # CAM consumes leaf contours, never the relationship object itself;
            # expanding here also keeps outer contours and their holes
            # together for the normal nesting/containment pass.
            selected = widget.controller.expand_group_children(selected)
            if not selected:
                raise GeometryAdapterError(
                    "A seleção não contém vetores CAM; escolha uma peça ou contorno fechado."
                )
            return document_to_woodcam_geometry(
                widget.document,
                entity_ids=selected,
            )
        if widget.document.pieces_by_id:
            return document_to_woodcam_geometry(
                widget.document,
                piece_ids=tuple(widget.document.pieces_by_id),
            )
        return document_to_woodcam_geometry(widget.document)

    def _active_geometry(self):
        if self._use_vector_editor_for_cam:
            return self._vector_editor_geometry_for_cam()
        return get_selected_geometry()

    def _selected_freecad_geometry_is_absolute(self):
        """Informa se a seleção 2D já está no XY final de usinagem.

        As peças da ponte Editor -> PanelNest preservam o placement do Editor,
        e uma ``CAM Chapa`` contém o layout real da chapa. Aplicar nelas uma
        segunda ancoragem pelo datum desloca o corte para a origem global.
        """
        try:
            from geometry_reader import resolve_selection_objects

            selected = list(FreeCADGui.Selection.getSelection() or [])
            resolved = resolve_selection_objects(selected)
        except Exception:
            return False
        if not resolved:
            return False

        absolute_count = 0
        for obj in resolved:
            exchange_layout = str(
                getattr(obj, "WoodCAMExchangeLayoutMode", "") or ""
            )
            managed_type = str(
                getattr(obj, "PanelNestManagedType", "") or ""
            )
            if (
                exchange_layout == "preserve_editor_xy"
                or managed_type == "layout_cam_compound"
            ):
                absolute_count += 1
        return absolute_count == len(resolved)

    def _clear_vector_editor(self):
        canvas = getattr(self, "vector_canvas", None)
        if canvas is not None:
            canvas.clear_vectors()

    def _delete_vector_selection(self):
        canvas = getattr(self, "vector_canvas", None)
        if canvas is not None:
            canvas.delete_selected()

    def _snap_vector_endpoints(self):
        canvas = getattr(self, "vector_canvas", None)
        if canvas is None:
            return
        try:
            tolerance = float(self.vector_editor_tolerance.text().strip().replace(",", "."))
            if tolerance <= 0.0:
                raise ValueError("A tolerância precisa ser maior que zero.")
            changed = canvas.snap_nearby_endpoints(tolerance)
            self.vector_editor_mode_label.setText(
                f"{changed} ponta(s) ajustada(s)" if changed else "Nenhuma ponta dentro da tolerância"
            )
        except Exception as error:
            self.vector_editor_mode_label.setText(str(error))

    def _start_vector_select(self):
        canvas = getattr(self, "vector_canvas", None)
        if canvas is not None:
            canvas.start_select()
            self.vector_editor_mode_label.setText("Modo: selecionar")

    def _start_vector_line(self):
        canvas = getattr(self, "vector_canvas", None)
        if canvas is not None:
            canvas.start_line()
            self.vector_editor_mode_label.setText("Modo: linha — arraste no canvas")

    def _start_vector_circle(self):
        canvas = getattr(self, "vector_canvas", None)
        if canvas is not None:
            canvas.start_circle()
            self.vector_editor_mode_label.setText("Modo: círculo — arraste do centro")

    def _vector_editor_work_area_from_setup(self):
        settings = self._collect_work_setup_settings()
        bounds = self._work_area_bounds_for_preview(settings)
        return WorkArea(*bounds) if bounds is not None else None

    def _preview_vector_editor_area_from_setup(self, *_args):
        """Atualiza somente a vista enquanto o usuário ainda digita os campos."""
        active_document = FreeCAD.ActiveDocument
        if active_document is not getattr(
            self, "_vector_editor_bound_freecad_document", None
        ):
            self._reload_vector_editor_for_active_document(active_document)
        widget = getattr(self, "vector_editor_widget", None)
        try:
            work_area = self._vector_editor_work_area_from_setup()
            if widget is not None:
                self._vector_editor_area_edit_pending = True
                self._vector_editor_area_edit_binding_token = getattr(
                    self, "_vector_editor_binding_token", None
                )
                self._vector_editor_area_edit_document = getattr(
                    self, "_vector_editor_bound_freecad_document", None
                )
                widget.adapter.set_work_area(work_area)
                widget.view.viewport().update()
                return
            canvas = getattr(self, "vector_canvas", None)
            if canvas is not None:
                settings = self._collect_work_setup_settings()
                canvas.set_work_area(
                    float(settings.get("job_width", 0.0) or 0.0),
                    float(settings.get("job_height", 0.0) or 0.0),
                )
        except Exception:
            # Um valor parcial como "-" ou "12," durante a digitação não
            # deve apagar nem persistir a última área válida.
            return

    def _commit_vector_editor_area_from_setup(self, *_args):
        """Confirma Trabalho no VectorDocument por comando/FCStd/Undo."""
        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            self._preview_vector_editor_area_from_setup()
            return
        try:
            if not getattr(self, "_vector_editor_area_edit_pending", False):
                self._show_vector_editor_document_area()
                return
            expected_token = self._vector_editor_area_edit_binding_token
            expected_document = self._vector_editor_area_edit_document
            if (
                expected_token is not getattr(
                    self, "_vector_editor_binding_token", None
                )
                or expected_document is not getattr(
                    self, "_vector_editor_bound_freecad_document", None
                )
                or FreeCAD.ActiveDocument is not expected_document
            ):
                self._vector_editor_area_edit_pending = False
                self._reload_vector_editor_for_active_document(
                    FreeCAD.ActiveDocument
                )
                self._set_vector_editor_status(
                    "A área digitada pertencia ao documento anterior e não foi aplicada.",
                    error=True,
                )
                return
            work_area = self._vector_editor_work_area_from_setup()
            self._vector_editor_area_edit_pending = False
            if widget.document.work_area != work_area:
                widget.controller.execute(SetWorkAreaCommand(work_area))
            widget.set_work_area(work_area)
            self._set_vector_editor_status(
                "Área de Trabalho atualizada e salva no documento vetorial."
            )
        except Exception as error:
            self._set_vector_editor_status(
                "Área de Trabalho não atualizada: %s" % error,
                error=True,
            )

    def _commit_vector_editor_area_discrete(self, *_args):
        """Confirma rádio/checkbox de origem como uma mudança intencional."""
        if getattr(self, "_loading_job_preferences", False):
            return
        self._vector_editor_area_edit_pending = True
        self._vector_editor_area_edit_binding_token = getattr(
            self, "_vector_editor_binding_token", None
        )
        self._vector_editor_area_edit_document = getattr(
            self, "_vector_editor_bound_freecad_document", None
        )
        self._commit_vector_editor_area_from_setup()

    def _show_vector_editor_document_area(self):
        """Mostra o snapshot do FCStd após reload/troca sem regravá-lo."""
        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        work_area = widget.document.work_area
        if work_area is None:
            try:
                work_area = self._vector_editor_work_area_from_setup()
            except Exception:
                work_area = None
        widget.set_work_area(work_area)

    def _update_vector_editor_area(self, *_args):
        """Compatibilidade interna: preview visual, nunca mutação direta."""
        self._preview_vector_editor_area_from_setup(*_args)

    def _diagnose_selected_sketch(self):
        try:
            self._clear_open_vector_markers()
            self._preview_vector_connections = None
            if hasattr(self, "vector_apply_connections_button"):
                self.vector_apply_connections_button.setEnabled(False)
            selected = list(FreeCADGui.Selection.getSelection() or [])
            if len(selected) != 1:
                raise ValueError("Selecione exatamente um Sketch para diagnosticar.")
            sketch = selected[0]
            if "Sketch" not in str(getattr(sketch, "TypeId", "")):
                raise ValueError("O objeto selecionado não é um Sketch.")
            from vector_diagnostics import diagnose_sketch

            bounds = None
            try:
                bounds = self._work_area_bounds_for_preview(
                    self._collect_work_setup_settings()
                )
            except Exception:
                pass
            report = diagnose_sketch(sketch, bounds)
            self._last_vector_diagnosis = (sketch, report)
            lines = [
                f"Sketch: {sketch.Label}",
                f"Contornos fechados: {report['closed_count']}",
                f"Vetores abertos: {report['open_count']}",
                f"Contornos duplicados: {report['duplicates']}",
            ]
            if report["open_count"]:
                lines.extend(
                    [
                        "",
                        "ORGANIZAÇÃO BLOQUEADA: feche os vetores abertos antes de separar peças.",
                        "Os contornos fechados abaixo são apenas candidatos; furos não serão separados enquanto houver abertura.",
                    ]
                )
                for candidate in report.get("nearest_connections", []):
                    lines.append(
                        "Ponta aberta {endpoint}: segmento mais próximo Edge{edge} "
                        "({distance:.4g} mm).".format(**candidate)
                    )
            lines.append(
                f"Peças 2D {'candidatas' if report['open_count'] else 'identificadas'}: {len(report['pieces'])}"
            )
            if report["pieces"]:
                lines.append("")
                for index, piece in enumerate(report["pieces"], start=1):
                    lines.append(f"Peça {index}: {len(piece['holes'])} furo(s)/recorte(s)")
            if bounds is not None:
                if report["outside"]:
                    numbers = ", ".join(str(item) for item in report["outside"])
                    lines.append(f"\nFORA DA ÁREA DE TRABALHO: peça(s) {numbers}.")
                else:
                    lines.append("\nTodas as peças cabem na área de Trabalho.")
            self.vector_diagnostic_report.setPlainText("\n".join(lines))
        except Exception as error:
            self.vector_diagnostic_report.setPlainText(f"Diagnóstico não executado:\n{error}")

    def _select_open_vectors(self):
        diagnosis = getattr(self, "_last_vector_diagnosis", None)
        if diagnosis is None:
            self._diagnose_selected_sketch()
            diagnosis = getattr(self, "_last_vector_diagnosis", None)
        if diagnosis is None:
            return
        sketch, report = diagnosis
        indices = report.get("open_edge_indices", [])
        if not indices:
            self.vector_diagnostic_report.appendPlainText("\nNão há vetores abertos para selecionar.")
            return
        FreeCADGui.Selection.clearSelection()
        for index in indices:
            FreeCADGui.Selection.addSelection(sketch, f"Edge{index}")
        self._show_open_vector_endpoints(report.get("open_endpoints", []))
        self.vector_diagnostic_report.appendPlainText(
            "\nSelecionadas as arestas abertas: " + ", ".join(f"Edge{item}" for item in indices)
        )

    def _close_small_vector_gaps(self):
        diagnosis = getattr(self, "_last_vector_diagnosis", None)
        if diagnosis is None:
            self._diagnose_selected_sketch()
            diagnosis = getattr(self, "_last_vector_diagnosis", None)
        if diagnosis is None:
            return
        try:
            tolerance = float(self.vector_close_tolerance.text().strip().replace(",", "."))
            if tolerance <= 0.0:
                raise ValueError("A tolerância precisa ser maior que zero.")
            sketch, report = diagnosis
            closed_count = 0
            skipped = []
            for start, end in report.get("open_gaps", []):
                distance = math.sqrt(sum((end[index] - start[index]) ** 2 for index in range(3)))
                if distance > tolerance:
                    skipped.append(distance)
                    continue
                sketch.addGeometry(
                    Part.LineSegment(FreeCAD.Vector(*start), FreeCAD.Vector(*end)),
                    False,
                )
                closed_count += 1
            if closed_count:
                sketch.Document.recompute()
                self._last_vector_diagnosis = None
                self.vector_diagnostic_report.appendPlainText(
                    f"\nFechadas {closed_count} lacuna(s) dentro de {tolerance:g} mm. Diagnostique novamente."
                )
            if skipped:
                self.vector_diagnostic_report.appendPlainText(
                    "\nNão fechadas automaticamente (lacuna maior que a tolerância): "
                    + ", ".join(f"{value:g} mm" for value in skipped)
                )
            if not closed_count and not skipped:
                self.vector_diagnostic_report.appendPlainText("\nNão há lacunas abertas para fechar.")
        except Exception as error:
            self.vector_diagnostic_report.appendPlainText(f"\nFechamento não executado: {error}")

    def _connect_open_endpoints(self):
        try:
            from vector_diagnostics import nearest_structure_connections

            tolerance = float(self.vector_close_tolerance.text().strip().replace(",", "."))
            if tolerance <= 0.0:
                raise ValueError("A tolerância precisa ser maior que zero.")
            selected = list(FreeCADGui.Selection.getSelection() or [])
            if len(selected) != 1 or "Sketch" not in str(getattr(selected[0], "TypeId", "")):
                raise ValueError("Selecione exatamente um Sketch para conectar as pontas.")
            sketch = selected[0]
            previewed = 0
            skipped = []
            candidates = nearest_structure_connections(sketch)
            if not candidates:
                self.vector_diagnostic_report.appendPlainText(
                    "\nNenhuma ponta isolada foi encontrada para conectar."
                )
                return
            FreeCADGui.Selection.clearSelection()
            for candidate in candidates:
                if candidate["distance"] > tolerance:
                    skipped.append(candidate["distance"])
                    continue
                FreeCADGui.Selection.addSelection(
                    sketch, f"Edge{candidate['target_index'] + 1}"
                )
                previewed += 1
            if previewed:
                self._preview_vector_connections = (
                    sketch,
                    [item for item in candidates if item["distance"] <= tolerance],
                )
                # Legado somente leitura: a prévia pode ajudar a localizar o
                # problema, mas nunca volta a aplicar restrições no Sketcher.
                self.vector_apply_connections_button.setEnabled(False)
                self.vector_diagnostic_report.appendPlainText(
                    "\nPrévia: segmentos candidatos destacados. Nenhuma geometria foi alterada."
                )
            if skipped:
                self.vector_diagnostic_report.appendPlainText(
                    "\nNão conectadas por estarem além da tolerância: "
                    + ", ".join(f"{value:g} mm" for value in skipped)
                )
            if not previewed and not skipped:
                self.vector_diagnostic_report.appendPlainText(
                    "\nNenhuma conexão sugerida pôde ser pré-visualizada."
                )
        except Exception as error:
            self.vector_diagnostic_report.appendPlainText(f"\nConexão não executada: {error}")

    def _apply_previewed_connections(self):
        preview = getattr(self, "_preview_vector_connections", None)
        if not preview:
            return
        sketch, candidates = preview
        answer = QtWidgets.QMessageBox.question(
            self,
            "Aplicar conexões 2D",
            f"Aplicar {len(candidates)} conexão(ões) pré-visualizada(s) ao Sketch?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        document = sketch.Document
        transaction_open = False
        try:
            import Sketcher
            from vector_diagnostics import diagnose_sketch

            before_open = sum(
                1
                for wire in getattr(getattr(sketch, "Shape", None), "Wires", []) or []
                if not wire.isClosed()
            )
            before_box = sketch.Shape.BoundBox
            before_size = (before_box.XLength, before_box.YLength, before_box.ZLength)
            document.openTransaction("WoodCAM 2D — Conectar vetores")
            transaction_open = True
            applied = 0
            for candidate in candidates:
                source_index = candidate["source_index"]
                source_position = candidate["source_position"]
                target_index = candidate.get("target_geometry", candidate["target_index"])
                target_position = candidate.get("target_position")
                constraint = (
                    Sketcher.Constraint(
                        "Coincident", source_index, source_position,
                        target_index, target_position,
                    )
                    if target_position
                    else Sketcher.Constraint(
                        "PointOnObject", source_index, source_position, target_index
                    )
                )
                sketch.addConstraint(constraint)
                applied += 1
            document.recompute()
            after_open = sum(
                1
                for wire in getattr(getattr(sketch, "Shape", None), "Wires", []) or []
                if not wire.isClosed()
            )
            after_box = sketch.Shape.BoundBox
            after_size = (after_box.XLength, after_box.YLength, after_box.ZLength)
            if after_open >= before_open or any(
                abs(after - before) > 1e-3
                for before, after in zip(before_size, after_size)
            ):
                document.abortTransaction()
                raise RuntimeError(
                    "A conexão não fechou o contorno sem alterar a geometria; "
                    "nenhuma alteração foi mantida."
                )
            document.commitTransaction()
            transaction_open = False
            self._preview_vector_connections = None
            self.vector_apply_connections_button.setEnabled(False)
            self._clear_open_vector_markers()
            self.vector_diagnostic_report.appendPlainText(
                f"\nAplicadas {applied} conexão(ões). Diagnostique novamente para validar."
            )
        except Exception as error:
            if transaction_open:
                try:
                    document.abortTransaction()
                except Exception:
                    pass
            self.vector_diagnostic_report.appendPlainText(
                f"\nNão foi possível aplicar as conexões: {error}"
            )

    def _show_open_vector_endpoints(self, endpoints):
        doc = FreeCAD.ActiveDocument
        if doc is None:
            return
        self._clear_open_vector_markers()
        if not endpoints:
            return
        group = doc.addObject("App::DocumentObjectGroup", "WoodCAM2D_OpenVectorMarkers")
        group.Label = "WoodCAM 2D — Pontas de vetores abertos"
        marker_size = 4.0
        for index, (x_value, y_value, z_value) in enumerate(endpoints, start=1):
            center = FreeCAD.Vector(x_value, y_value, z_value)
            shape = Part.makeCompound([
                Part.makeLine(
                    center + FreeCAD.Vector(-marker_size, 0, 0),
                    center + FreeCAD.Vector(marker_size, 0, 0),
                ),
                Part.makeLine(
                    center + FreeCAD.Vector(0, -marker_size, 0),
                    center + FreeCAD.Vector(0, marker_size, 0),
                ),
            ])
            marker = doc.addObject("Part::Feature", f"OpenVectorEndpoint{index:02d}")
            marker.Label = f"Ponta aberta {index}"
            marker.Shape = shape
            self._set_view_style(marker, (0.9, 0.05, 0.05), line_width=4, transparency=0)
            group.addObject(marker)
        doc.recompute()

    def _clear_open_vector_markers(self):
        doc = FreeCAD.ActiveDocument
        if doc is None:
            return
        group = doc.getObject("WoodCAM2D_OpenVectorMarkers")
        if group is not None:
            for child in list(group.Group):
                doc.removeObject(child.Name)
            doc.removeObject(group.Name)

    def _make_icon_group(self, title, diagram_kind, state_provider=None, diagram_key=None):
        group = QtWidgets.QGroupBox(title)
        layout = QtWidgets.QHBoxLayout(group)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)
        diagram = MiniSetupDiagram(diagram_kind, state_provider, group)
        self.setup_diagrams.append(diagram)
        if diagram_key:
            self.operation_diagrams[diagram_key] = diagram
        layout.addWidget(diagram, 0)
        grid = QtWidgets.QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(7)
        grid.setVerticalSpacing(5)
        group._woodcam_grid = grid
        layout.addLayout(grid, 1)
        return group, grid

    def _add_scroll_tab(self, content, title, icon_kind=None):
        # The production Editor 2D owns an interactive splitter, canvas
        # scrollbars and a side-panel scroll area.  Wrapping it in the generic
        # operation-tab QScrollArea makes the outer viewport steal horizontal
        # drag/wheel events (the divider then appears to move the whole task
        # page instead of resizing the editor).  Keep the editor as a direct
        # tab page; its own panels remain scrollable where needed.
        if title == "Editor 2D":
            content.setProperty("woodcam_tab_title", title)
            icon = self._load_diagram_icon(icon_kind)
            if icon is None or icon.isNull():
                icon = self._temporary_tab_icon(icon_kind)
            display_title = title if title in {"Trabalho", "Material"} else ""
            index = self.operation_tabs.addTab(content, icon, display_title)
            self.operation_tabs.setTabToolTip(index, title)
            try:
                self.operation_tabs.tabBar().setTabData(index, title)
            except Exception:
                pass
            return content
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(content)
        scroll.setProperty("woodcam_tab_title", title)
        icon = self._load_diagram_icon(icon_kind)
        if icon is None or icon.isNull():
            icon = self._temporary_tab_icon(icon_kind)
        display_title = title if title in {"Trabalho", "Material"} else ""
        index = self.operation_tabs.addTab(scroll, icon, display_title)
        self.operation_tabs.setTabToolTip(index, title)
        try:
            self.operation_tabs.tabBar().setTabData(index, title)
        except Exception:
            pass
        return scroll

    def _temporary_tab_icon(self, icon_kind=None):
        """Return a native placeholder until a final artwork is supplied."""

        style = self.style()
        standard_pixmap = {
            "rough3d": QtWidgets.QStyle.SP_ArrowDown,
            "finish3d": QtWidgets.QStyle.SP_DialogApplyButton,
            "simulation": QtWidgets.QStyle.SP_MediaPlay,
        }.get(icon_kind, QtWidgets.QStyle.SP_FileIcon)
        return style.standardIcon(standard_pixmap)

    def _tab_title(self, index):
        if not 0 <= int(index) < self.operation_tabs.count():
            return ""
        page = self.operation_tabs.widget(int(index))
        if page is not None:
            title = page.property("woodcam_tab_title")
            if title:
                return str(title)
        try:
            title = self.operation_tabs.tabBar().tabData(int(index))
            if title:
                return str(title)
        except Exception:
            pass
        return self.operation_tabs.tabText(int(index))

    def _make_visual_card(self, kind, title, description, state_provider=None):
        frame = QtWidgets.QFrame()
        frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        frame.setSizePolicy(
            QtWidgets.QSizePolicy.Preferred,
            QtWidgets.QSizePolicy.Maximum,
        )
        frame.setStyleSheet(
            "QFrame { background: #ffffff; border: 1px solid #d9e2ec; "
            "border-radius: 7px; }"
            "QLabel { border: none; background: transparent; }"
        )
        card_layout = QtWidgets.QHBoxLayout(frame)
        diagram = OperationDiagram(kind, state_provider, frame)
        self.operation_diagrams[kind] = diagram
        card_layout.addWidget(diagram, 0)
        text_layout = QtWidgets.QVBoxLayout()
        title_label = QtWidgets.QLabel(title)
        title_label.setStyleSheet(
            "font-weight: bold; font-size: 13px; color: #1e3a5f;"
        )
        description_label = QtWidgets.QLabel(description)
        description_label.setWordWrap(True)
        description_label.setStyleSheet("color: #475569;")
        text_layout.addWidget(title_label)
        text_layout.addWidget(description_label)
        text_layout.addStretch(1)
        card_layout.addLayout(text_layout, 1)
        return frame

    def _operation_mode_for_index(self, index):
        if not 0 <= int(index) < self.operation_tabs.count():
            return None
        return OPERATION_MODE_BY_TAB_TITLE.get(self._tab_title(int(index)))

    def _selected_job_type(self):
        for key, button in getattr(self, "job_type_buttons", {}).items():
            if button.isChecked():
                return key
        return DEFAULT_PRESETS["job_type"]

    def _z_zero_mode(self):
        if getattr(self, "z_zero_bed", None) is not None and self.z_zero_bed.isChecked():
            return "machine_bed"
        return "material_surface"

    def _job_z_zero_mode(self):
        if (
            getattr(self, "job_z_zero_bed", None) is not None
            and self.job_z_zero_bed.isChecked()
        ):
            return "machine_bed"
        return "material_surface"

    def _origin_anchor(self):
        for key, button in getattr(self, "origin_buttons", {}).items():
            if button.isChecked():
                return key
        return DEFAULT_PRESETS["origin_anchor"]

    def _job_origin_anchor(self):
        for key, button in getattr(self, "job_origin_buttons", {}).items():
            if button.isChecked():
                return key
        return DEFAULT_PRESETS["job_origin_anchor"]

    def _model_position_mode(self):
        if (
            getattr(self, "model_gap_below_radio", None) is not None
            and self.model_gap_below_radio.isChecked()
        ):
            return "gap_below"
        return "gap_above"

    def _update_setup_diagrams(self, _checked=None):
        for diagram in getattr(self, "setup_diagrams", []):
            diagram.update()
        self._queue_setup_preview_refresh()

    def _preview_group_exists(self):
        doc = FreeCAD.ActiveDocument
        return bool(doc is not None and doc.getObject("WoodCAM2D_Preview") is not None)

    def _queue_setup_preview_refresh(self, *_args):
        timer = getattr(self, "_setup_preview_refresh_timer", None)
        if timer is None or not self._preview_group_exists():
            return
        if not self._is_work_setup_tab_active():
            return
        timer.start(180)

    def _refresh_setup_preview_if_possible(self):
        if not self._preview_group_exists() or not self._is_work_setup_tab_active():
            return
        try:
            settings = self._collect_work_setup_settings()
            self._validate_work_setup_settings(settings)
            self._show_work_area_preview(settings)
        except Exception:
            # Enquanto a pessoa digita um valor parcial, não vale abrir alerta.
            pass

    def _sync_home_z_display(self, _text=None):
        if getattr(self, "home_z_display", None) is not None:
            self.home_z_display.setText(self.fields["retract_height"].text())

    def _field_float_or_default(self, key, default=0.0):
        try:
            if key in self.fields:
                return float(self.fields[key].text().strip().replace(",", "."))
        except (TypeError, ValueError):
            pass
        return float(default)

    def _material_thickness_for_model_position(self):
        return max(
            0.0,
            self._field_float_or_default(
                "material_thickness",
                DEFAULT_PRESETS["material_thickness"],
            ),
        )

    def _set_model_gap_field(self, key, value):
        edit = self.fields.get(key)
        if edit is None:
            return
        old_state = edit.blockSignals(True)
        try:
            edit.setText(self._format_value(value))
        finally:
            edit.blockSignals(old_state)

    def _set_model_slider_value(self, value):
        if getattr(self, "model_position_slider", None) is None:
            return
        old_state = self.model_position_slider.blockSignals(True)
        try:
            self.model_position_slider.setValue(int(round(value)))
        finally:
            self.model_position_slider.blockSignals(old_state)

    def _update_model_gaps_from_slider(self, value):
        if getattr(self, "_syncing_model_position", False):
            return
        thickness = self._material_thickness_for_model_position()
        ratio = max(0.0, min(1.0, float(value) / 1000.0))
        gap_above = thickness * ratio
        gap_below = max(0.0, thickness - gap_above)
        self._syncing_model_position = True
        try:
            self._set_model_gap_field("model_gap_above", gap_above)
            self._set_model_gap_field("model_gap_below", gap_below)
        finally:
            self._syncing_model_position = False

    def _sync_model_slider_from_fields(self, _value=None):
        if getattr(self, "_syncing_model_position", False):
            return
        thickness = self._material_thickness_for_model_position()
        if thickness <= 0.0:
            self._set_model_slider_value(0)
            return
        if self._model_position_mode() == "gap_below":
            gap_below = max(
                0.0,
                min(
                    thickness,
                    self._field_float_or_default("model_gap_below", 0.0),
                ),
            )
            gap_above = max(0.0, thickness - gap_below)
        else:
            gap_above = max(
                0.0,
                min(
                    thickness,
                    self._field_float_or_default("model_gap_above", 0.0),
                ),
            )
            gap_below = max(0.0, thickness - gap_above)
        slider_value = 0.0 if thickness <= 0.0 else (gap_above / thickness) * 1000.0
        self._syncing_model_position = True
        try:
            self._set_model_gap_field("model_gap_above", gap_above)
            self._set_model_gap_field("model_gap_below", gap_below)
            self._set_model_slider_value(slider_value)
        finally:
            self._syncing_model_position = False

    def _update_model_position_controls(self, _checked=None):
        below = self._model_position_mode() == "gap_below"
        if "model_gap_above" in self.fields:
            self.fields["model_gap_above"].setEnabled(not below)
        if "model_gap_below" in self.fields:
            self.fields["model_gap_below"].setEnabled(below)
        self._sync_model_slider_from_fields()

    def _update_tab_actions(self, index):
        operation_mode = self._operation_mode_for_index(index)
        is_operation = operation_mode is not None
        if is_operation:
            self.last_operation_mode = operation_mode
        tab_title = self._tab_title(int(index)) if index >= 0 else ""
        is_action_tab = tab_title == ACTION_TAB_TITLE
        is_setup_tab = tab_title in {"Trabalho", "Material"}
        can_run_operation = is_operation or is_action_tab
        can_apply_setup = is_setup_tab and self.sim_timer is None
        self.apply_button.setEnabled((can_run_operation or can_apply_setup) and self.sim_timer is None)
        self.preview_button.setEnabled((can_run_operation or can_apply_setup) and self.sim_timer is None)
        self.simulate_button.setEnabled(can_run_operation and self.sim_timer is None)
        self.generate_button.setEnabled(can_run_operation and self.sim_timer is None)
        self.stop_sim_button.setVisible(self.sim_timer is not None)
        self.stop_sim_button.setEnabled(self.sim_timer is not None)
        if is_operation:
            self.operation_hint.setText("")
        elif is_action_tab:
            self._refresh_applied_operation_list()
            label = OPERATION_TREE_LABELS.get(self.last_operation_mode, "operação")
            self.operation_hint.setText(f"Última: {label}")
        elif is_setup_tab:
            self.operation_hint.setText("Configuração")
        else:
            self.operation_hint.setText("Configuração")
        key = self._active_document_key()
        if key is not None and key == self._current_document_key and index >= 0:
            self._document_tab_indices[key] = int(index)

    def _detach_vector_editor(self):
        """Move o Editor 2D real para uma janela nativa, sem copiar estado."""

        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        window = getattr(self, "_detached_editor_window", None)
        if window is not None:
            window.show()
            window.raise_()
            window.activateWindow()
            return

        window = QtWidgets.QDialog(None)
        window.setObjectName("WoodCAMDetachedEditor2D")
        window.setWindowTitle("WoodCAM — Editor 2D")
        window.setModal(False)
        window.setWindowFlags(
            QtCore.Qt.Window
            | QtCore.Qt.WindowTitleHint
            | QtCore.Qt.WindowSystemMenuHint
            | QtCore.Qt.WindowMinimizeButtonHint
            | QtCore.Qt.WindowMaximizeButtonHint
            | QtCore.Qt.WindowCloseButtonHint
        )
        layout = QtWidgets.QVBoxLayout(window)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)
        self._vector_editor_layout.removeWidget(widget)
        self._vector_editor_detached_placeholder.show()
        widget.setParent(window)
        layout.addWidget(widget, 1)
        widget.show()
        window.finished.connect(self._reattach_vector_editor)
        self._detached_editor_window = window
        self.detach_editor_button.setIcon(
            self.style().standardIcon(QtWidgets.QStyle.SP_TitleBarNormalButton)
        )
        self.detach_editor_button.setToolTip(
            "O Editor 2D já está destacado; clique para trazê-lo à frente"
        )
        window.resize(max(980, self.width()), max(720, self.height()))
        window.show()
        window.raise_()
        window.activateWindow()

    def _reattach_vector_editor(self, _result=0):
        """Devolve a mesma instância do Editor à aba ao fechar sua janela."""

        if self._reattaching_vector_editor:
            return
        window = getattr(self, "_detached_editor_window", None)
        widget = getattr(self, "vector_editor_widget", None)
        if window is None or widget is None:
            return
        self._reattaching_vector_editor = True
        try:
            window.layout().removeWidget(widget)
            widget.setParent(self._vector_editor_tab)
            self._vector_editor_layout.insertWidget(0, widget, 1)
            self._vector_editor_detached_placeholder.hide()
            widget.show()
            self._detached_editor_window = None
            self.detach_editor_button.setIcon(
                self.style().standardIcon(QtWidgets.QStyle.SP_TitleBarMaxButton)
            )
            self.detach_editor_button.setToolTip(
                "Abrir o mesmo Editor 2D em uma janela própria"
            )
            window.deleteLater()
        finally:
            self._reattaching_vector_editor = False

    def reject(self):
        self.hide()

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def show_config_dialog(self):
        key = self._active_document_key()
        if key != self._current_document_key:
            self._reload_vector_editor_for_active_document()
            self._current_document_key = key
            remembered_index = self._document_tab_indices.get(key)
            if remembered_index is None:
                remembered_index = self._tab_index("Trabalho")
            if remembered_index is not None:
                self.operation_tabs.setCurrentIndex(int(remembered_index))
                self._document_tab_indices[key] = int(remembered_index)
        parent = self.parentWidget()
        if parent is not None and not self._user_moved_dialog and not self._positioned_once:
            x_value = max(0, int((parent.width() - self.width()) / 2))
            y_value = max(0, int((parent.height() - self.height()) / 2))
            self.move(x_value, y_value)
            self._positioned_once = True
        self.show()
        self._load_selected_operation_for_editing()
        try:
            self.raise_()
        except Exception:
            pass

    def _active_document_key(self):
        doc = FreeCAD.ActiveDocument
        if doc is None:
            return None
        return "|".join(
            [
                str(getattr(doc, "Name", "") or ""),
                str(getattr(doc, "FileName", "") or ""),
            ]
        )

    def _update_operation_controls(self, _checked=None):
        hole_fields = self.operation_fields["holes"]
        hole_fields["cut_depth"].setEnabled(not self.use_model_hole_depths.isChecked())
        hole_fields["helix_pitch"].setEnabled(self.use_helical_drilling.isChecked())
        hole_fields["helix_stepover_percent"].setEnabled(
            self.use_helical_drilling.isChecked()
        )
        peck_enabled = self.peck_enabled.isChecked()
        self.peck_retract_mode.setEnabled(peck_enabled)
        hole_fields["peck_step"].setEnabled(peck_enabled)
        hole_fields["peck_retract_clearance"].setEnabled(peck_enabled)
        hole_fields["dwell_seconds"].setEnabled(self.dwell_enabled.isChecked())
        hole_diagram = self.operation_diagrams.get("holes")
        if hole_diagram is not None:
            hole_diagram.update()
        is_z_level = self.rough3d_strategy_combo.currentIndex() == 0
        self.rough3d_profile_combo.setEnabled(is_z_level)
        self.rough3d_order_combo.setEnabled(is_z_level)
        is_finish_raster = self.finish3d_strategy_combo.currentIndex() == 0
        self.operation_fields["finish3d"]["finish3d_raster_angle"].setEnabled(
            is_finish_raster
        )

    def _update_pocket_controls(self, _index=None):
        is_raster = self.pocket_strategy_combo.currentIndex() == 1
        self.operation_fields["pocket"]["pocket_raster_angle"].setEnabled(
            is_raster
        )
        self.pocket_direction_combo.setEnabled(not is_raster)
        pocket_diagram = self.operation_diagrams.get("pocket")
        if pocket_diagram is not None:
            pocket_diagram.update()

    def _default_field_value(self, name):
        default_value = DEFAULT_PRESETS[name]
        if name in {
            "machine_x_size",
            "machine_y_size",
            "start_x",
            "start_y",
            "job_width",
            "job_height",
            "job_depth",
            "job_origin_x",
            "job_origin_y",
            "origin_x",
            "origin_y",
            "material_thickness",
            "safe_height",
            "retract_height",
        }:
            try:
                default_value = FreeCAD.ParamGet(WOODCAM_PARAMETER_PATH).GetFloat(
                    name,
                    float(default_value),
                )
            except Exception:
                    pass
        return default_value

    def _add_field_at(
        self,
        grid,
        row,
        label_text,
        name,
        label_column=0,
        field_column=1,
        field_column_span=2,
    ):
        default_value = self._default_field_value(name)
        edit = QtWidgets.QLineEdit(self._format_value(default_value))
        edit.setToolTip(label_text)
        grid.addWidget(QtWidgets.QLabel(label_text), row, label_column)
        grid.addWidget(edit, row, field_column, 1, field_column_span)
        self.fields[name] = edit
        self.field_labels[name] = label_text
        return edit

    def _add_field(self, grid, row, label_text, name):
        return self._add_field_at(grid, row, label_text, name)

    def _add_operation_field(self, grid, row, label_text, mode, name, default_key=None):
        default_value = DEFAULT_PRESETS[default_key or name]
        edit = QtWidgets.QLineEdit(self._format_value(default_value))
        edit.setToolTip(label_text)
        grid.addWidget(QtWidgets.QLabel(label_text), row, 0)
        grid.addWidget(edit, row, 1, 1, 2)
        self.operation_fields.setdefault(mode, {})[name] = edit
        self.operation_field_labels.setdefault(mode, {})[name] = label_text
        return edit

    def _make_depth_group(self, mode, cut_depth_default_key=None):
        group, grid = self._make_icon_group("Profundidades de corte", "depth")
        self._add_operation_field(
            grid,
            0,
            "Cota inicial (mm)",
            mode,
            "start_depth",
            "operation_start_depth",
        )
        self._add_operation_field(
            grid,
            1,
            "Profundidade de corte (mm)",
            mode,
            "cut_depth",
            cut_depth_default_key or "operation_cut_depth",
        )
        return self._compact_group(group)

    def _make_tool_group(self, mode, show_passes=True):
        group = QtWidgets.QGroupBox("Ferramenta")
        group.setSizePolicy(
            QtWidgets.QSizePolicy.Preferred,
            QtWidgets.QSizePolicy.Maximum,
        )
        grid = QtWidgets.QGridLayout(group)
        grid.setContentsMargins(10, 6, 10, 6)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 0)
        grid.setColumnStretch(2, 0)
        grid.setColumnStretch(3, 0)
        grid.setColumnStretch(4, 1)
        preview = ToolPreviewWidget()
        grid.addWidget(preview, 0, 0, 2, 1, QtCore.Qt.AlignCenter)
        combo = QtWidgets.QComboBox()
        self._populate_tool_combo(combo)
        combo.setFixedWidth(220)
        grid.addWidget(QtWidgets.QLabel("Fresa"), 0, 1)
        grid.addWidget(combo, 0, 2)
        manage_button = QtWidgets.QPushButton("Editar...")
        manage_button.setFixedWidth(84)
        manage_button.clicked.connect(
            lambda _checked=False, operation_mode=mode: self._edit_tool_for_mode(
                operation_mode
            )
        )
        grid.addWidget(manage_button, 0, 3)
        self.operation_tool_combos[mode] = combo
        self.operation_tool_previews[mode] = preview
        self.operation_pass_labels[mode] = QtWidgets.QLabel("—")
        passes_label = QtWidgets.QLabel("Passagens")
        grid.addWidget(passes_label, 1, 1)
        grid.addWidget(self.operation_pass_labels[mode], 1, 2)
        passes_button = QtWidgets.QPushButton("Editar passagens...")
        passes_button.setFixedWidth(150)
        passes_button.clicked.connect(
            lambda _checked=False, operation_mode=mode: self._edit_passes_for_mode(
                operation_mode
            )
        )
        grid.addWidget(passes_button, 1, 3)
        passes_label.setVisible(show_passes)
        self.operation_pass_labels[mode].setVisible(show_passes)
        passes_button.setVisible(show_passes)
        for label, field_name in (
            ("Diâmetro (mm)", "tool_diameter"),
            ("Stepdown (mm)", "stepdown"),
            ("Avanço XY (mm/min)", "feed_xy"),
            ("Avanço Z (mm/min)", "feed_z"),
            ("Avanço rápido (mm/min)", "rapid_feed"),
            ("RPM", "rpm"),
        ):
            default_value = DEFAULT_PRESETS[field_name]
            edit = QtWidgets.QLineEdit(self._format_value(default_value))
            edit.setToolTip(label)
            edit.setVisible(False)
            self.operation_fields.setdefault(mode, {})[field_name] = edit
            self.operation_field_labels.setdefault(mode, {})[field_name] = label
        if "cut_depth" in self.operation_fields.get(mode, {}):
            self.operation_fields[mode]["cut_depth"].textChanged.connect(
                lambda _text, operation_mode=mode: self._clear_pass_schedule(
                    operation_mode
                )
            )
        self.operation_fields[mode]["stepdown"].textChanged.connect(
            lambda _text, operation_mode=mode: self._update_passes_label(
                operation_mode
            )
        )
        combo.currentTextChanged.connect(
            lambda name, operation_mode=mode: self._apply_operation_tool_preset(
                operation_mode,
                name,
            )
        )
        self._apply_operation_tool_preset(mode, combo.currentText())
        return group

    def _edit_tool_for_mode(self, mode):
        combo = self.operation_tool_combos.get(mode)
        selected_name = combo.currentText() if combo is not None else ""
        if selected_name in self.tool_database:
            self._populate_tool_list(selected_name)
        self._show_tool_database_tab()

    def _operation_depth_and_stepdown(self, mode):
        fields = self.operation_fields.get(mode, {})
        try:
            depth = float(fields["cut_depth"].text().strip().replace(",", "."))
            stepdown = float(fields["stepdown"].text().strip().replace(",", "."))
        except (KeyError, ValueError):
            return 0.0, 0.0
        return max(0.0, depth), max(0.0, stepdown)

    def _update_passes_label(self, mode):
        label = self.operation_pass_labels.get(mode)
        if label is None:
            return
        depth, stepdown = self._operation_depth_and_stepdown(mode)
        if depth <= 0.0 or stepdown <= 0.0:
            label.setText("—")
            return
        schedule = self._pass_schedules.get(mode)
        passes = len(schedule) if schedule else max(1, int(math.ceil(depth / stepdown)))
        label.setText(f"{passes} passagem" + ("" if passes == 1 else "s"))

    def _clear_pass_schedule(self, mode):
        self._pass_schedules.pop(mode, None)
        self._update_passes_label(mode)

    def _edit_passes_for_mode(self, mode):
        fields = self.operation_fields.get(mode, {})
        if "cut_depth" not in fields or "stepdown" not in fields:
            return
        depth, stepdown = self._operation_depth_and_stepdown(mode)
        if depth <= 0.0:
            QtWidgets.QMessageBox.warning(
                self,
                "Editar passagens",
                "Informe a profundidade de corte antes de editar as passagens.",
            )
            return
        current_passes = max(1, int(math.ceil(depth / stepdown))) if stepdown > 0 else 1
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Editar passagens")
        layout = QtWidgets.QGridLayout(dialog)
        info = QtWidgets.QLabel(f"Profundidade total do corte: {depth:g} mm")
        layout.addWidget(info, 0, 0, 1, 3)
        visual_row = QtWidgets.QHBoxLayout()
        visual_row.setContentsMargins(0, 0, 0, 0)
        visual_row.setSpacing(8)
        pass_diagram = PassDepthWidget()
        visual_row.addWidget(pass_diagram, 0, QtCore.Qt.AlignTop | QtCore.Qt.AlignCenter)
        visual_row.addStretch(1)
        layout.addLayout(visual_row, 1, 0, 3, 1)
        passes_spin = QtWidgets.QSpinBox()
        passes_spin.setRange(1, 999)
        passes_spin.setValue(current_passes)
        amounts = list(self._pass_schedules.get(mode, []))
        if len(amounts) != current_passes or abs(sum(amounts) - depth) > 1e-6:
            amounts = [depth / current_passes] * current_passes
        selected = {"index": 0}
        step_field = QtWidgets.QLineEdit()
        list_widget = QtWidgets.QListWidget()
        list_widget.setMinimumHeight(110)
        layout.addWidget(QtWidgets.QLabel("Número de passagens"), 1, 1)
        layout.addWidget(passes_spin, 1, 2)
        layout.addWidget(QtWidgets.QLabel("Passada selecionada (mm)"), 2, 1)
        layout.addWidget(step_field, 2, 2)
        layout.addWidget(list_widget, 3, 1, 1, 2)

        def refresh_list(reset=False):
            count = max(1, int(passes_spin.value()))
            nonlocal amounts
            if reset or len(amounts) != count:
                amounts = [depth / count] * count
            if count > 1:
                amounts[-1] = max(0.001, depth - sum(amounts[:-1]))
            list_widget.clear()
            cumulative = 0.0
            for index, pass_amount in enumerate(amounts):
                cumulative += pass_amount
                suffix = " (ajustada automaticamente)" if index == count - 1 else ""
                list_widget.addItem(f"{index + 1}: {pass_amount:g} mm  →  {cumulative:g} mm{suffix}")
            pass_diagram.set_passes(depth, count, amounts)
            selected["index"] = min(selected["index"], count - 1)
            list_widget.setCurrentRow(selected["index"])

        def load_selected(row):
            if row < 0:
                return
            selected["index"] = row
            step_field.blockSignals(True)
            step_field.setText(self._format_value(amounts[row]))
            step_field.blockSignals(False)
            step_field.setEnabled(row < len(amounts) - 1)

        def update_selected_amount():
            index = selected["index"]
            if index >= len(amounts) - 1:
                return
            try:
                value = float(step_field.text().strip().replace(",", "."))
            except ValueError:
                return
            remaining_before_last = sum(amounts[:index]) + sum(amounts[index + 1:-1])
            amounts[index] = max(0.001, min(value, depth - remaining_before_last - 0.001))
            refresh_list()

        passes_spin.valueChanged.connect(lambda _value: refresh_list(reset=True))
        list_widget.currentRowChanged.connect(load_selected)
        step_field.editingFinished.connect(update_selected_amount)
        refresh_list()
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons, 4, 1, 1, 2)
        exec_dialog = getattr(dialog, "exec", None) or getattr(dialog, "exec_")
        if exec_dialog() != QtWidgets.QDialog.Accepted:
            return
        self._pass_schedules[mode] = list(amounts)
        fields["stepdown"].setText(self._format_value(max(amounts)))
        self._save_operation_preferences()
        self._update_passes_label(mode)

    def _make_ramp_group(self, mode):
        group, grid = self._make_icon_group("Entrada", "ramp")
        checkbox = QtWidgets.QCheckBox("Entrada em rampa")
        checkbox.setChecked(DEFAULT_PRESETS["use_ramp"])
        grid.addWidget(checkbox, 0, 1, 1, 2)
        self.operation_ramp_checks[mode] = checkbox
        ramp_field = self._add_operation_field(
            grid,
            1,
            "Comprimento da rampa (mm)",
            mode,
            "ramp_length",
        )
        checkbox.toggled.connect(ramp_field.setEnabled)
        ramp_field.setEnabled(checkbox.isChecked())
        return self._compact_group(group)

    def _asset_widget(self, kind):
        widget = MiniSetupDiagram(kind)
        self.setup_diagrams.append(widget)
        return widget

    def _make_cut_vector_group(self):
        group, grid = self._make_icon_group(
            "Usinar vetores",
            "cut",
            lambda: {
                "side": self.CUT_SIDE_BY_INDEX[
                    self.operation_combo.currentIndex()
                ],
                "climb": getattr(self, "cut_direction_climb", None) is None
                or self.cut_direction_climb.isChecked(),
            },
            "cut",
        )
        self.operation_combo.setVisible(True)
        grid.addWidget(QtWidgets.QLabel("Lado dos contornos externos"), 0, 0)
        grid.addWidget(self.operation_combo, 0, 1, 1, 3)
        direction_label = QtWidgets.QLabel("Direção")
        direction_label.setStyleSheet("font-weight: bold;")
        grid.addWidget(direction_label, 1, 0)
        self.cut_direction_climb = QtWidgets.QRadioButton("Subida")
        self.cut_direction_conventional = QtWidgets.QRadioButton("Convencional")
        self.cut_direction_climb.setChecked(True)
        self.cut_direction_climb.setToolTip("Sentido de usinagem climb/subida.")
        self.cut_direction_conventional.setToolTip("Sentido de usinagem convencional.")
        self.cut_direction_climb.toggled.connect(self._sync_cut_side_buttons)
        self.cut_direction_conventional.toggled.connect(self._sync_cut_side_buttons)
        direction_row = QtWidgets.QHBoxLayout()
        direction_row.addWidget(self.cut_direction_climb)
        direction_row.addWidget(self.cut_direction_conventional)
        direction_row.addStretch(1)
        grid.addLayout(direction_row, 1, 1, 1, 3)
        tolerance_field = self._add_operation_field(
            grid,
            2,
            "Compensação dimensional (mm)",
            "cut",
            "cut_allowance_offset",
        )
        tolerance_field.setToolTip(
            "Ajuste intencional da medida final. Valor positivo deixa "
            "material (corte menor); negativo remove material extra. "
            "Use 0 para seguir o desenho."
        )
        info = QtWidgets.QLabel(
            "Contornos internos e furos selecionados são cortados por dentro e antes do contorno externo."
        )
        info.setWordWrap(True)
        grid.addWidget(info, 3, 0, 1, 4)
        self._sync_cut_side_buttons()
        return self._compact_group(group)

    def _sync_cut_side_buttons(self, *_args):
        diagram = self.operation_diagrams.get("cut")
        if diagram is not None:
            diagram.update()

    def _make_cut_tolerance_group(self):
        group, grid = self._make_icon_group(
            "Última passada e cantos",
            "tolerance",
        )
        self.cut_separate_last_pass = QtWidgets.QCheckBox("Fazer última passada separada")
        self.cut_separate_last_pass.setToolTip(
            "Executa primeiro um corte com sobre-metal e depois uma passada final na medida."
        )
        grid.addWidget(self.cut_separate_last_pass, 0, 0, 1, 3)
        allowance = self._add_operation_field(
            grid,
            1,
            "Sobre-metal para última passada (mm)",
            "cut",
            "cut_last_pass_allowance",
        )
        allowance.setToolTip(
            "Material deixado pelo desbaste e removido somente na passada "
            "final de acabamento. Use um valor pequeno."
        )
        self.corner_slowdown_enabled = QtWidgets.QCheckBox(
            "Reduzir avanço próximo aos cantos"
        )
        self.corner_slowdown_enabled.setChecked(
            DEFAULT_PRESETS["corner_slowdown_enabled"]
        )
        self.corner_slowdown_enabled.setToolTip(
            "Divide o percurso antes e depois do vértice para a máquina chegar "
            "ao canto com menor velocidade."
        )
        grid.addWidget(self.corner_slowdown_enabled, 2, 0, 1, 3)
        corner_fields = []
        for row, label, name in (
            (3, "Mudança mínima de direção (°)", "corner_angle_threshold"),
            (4, "Avanço no canto (%)", "corner_feed_percent"),
            (5, "Distância antes/depois (mm)", "corner_slowdown_distance"),
        ):
            corner_fields.append(
                self._add_operation_field(grid, row, label, "cut", name)
            )

        def update_corner_fields(checked):
            for field in corner_fields:
                field.setEnabled(bool(checked))

        self.corner_slowdown_enabled.toggled.connect(update_corner_fields)
        update_corner_fields(self.corner_slowdown_enabled.isChecked())
        self.cut_separate_last_pass.toggled.connect(allowance.setEnabled)
        allowance.setEnabled(self.cut_separate_last_pass.isChecked())
        return self._compact_group(group)

    def _make_cut_tabs_group(self):
        group, grid = self._make_icon_group("Tabs / pontes de fixação", "tabs")
        self.cut_tabs_enabled = QtWidgets.QCheckBox("Adicionar tabs ao percurso")
        self.cut_tabs_enabled.setToolTip(
            "Cria pontes automáticas ao redor dos contornos externos para segurar a peça."
        )
        grid.addWidget(self.cut_tabs_enabled, 0, 0, 1, 3)
        self.cut_tabs_auto_enabled = QtWidgets.QCheckBox("Distribuir automaticamente")
        self.cut_tabs_auto_enabled.setChecked(True)
        grid.addWidget(self.cut_tabs_auto_enabled, 1, 0, 1, 3)
        tab_fields = []
        for row, (label, field_name) in enumerate(
            (
                ("Comprimento (mm)", "tab_length"),
                ("Espessura (mm)", "tab_thickness"),
                ("Quantidade automática", "tab_count"),
            ),
            start=2,
        ):
            field = self._add_operation_field(grid, row, label, "cut", field_name)
            tab_fields.append(field)
        self.cut_tabs_3d = QtWidgets.QCheckBox("Tabs 3D")
        self.cut_tabs_3d.setToolTip("Sobe e desce em rampa ao longo da tab, sem movimentos verticais bruscos.")
        grid.addWidget(self.cut_tabs_3d, 5, 0, 1, 3)
        self.cut_tabs_manual_enabled = QtWidgets.QCheckBox("Posicionar manualmente")
        self.cut_tabs_manual_enabled.setToolTip(
            "Ativa a marcação de tabs clicando diretamente no contorno da vista 2D."
        )
        grid.addWidget(self.cut_tabs_manual_enabled, 6, 0, 1, 3)
        edit_button = QtWidgets.QPushButton("Posicionar tabs...")
        self.cut_tabs_edit_button = edit_button
        edit_button.clicked.connect(self._edit_tabs_settings)
        grid.addWidget(edit_button, 7, 1, 1, 2)

        def update_tab_fields(checked):
            for field in tab_fields:
                field.setEnabled(bool(checked))
            self.cut_tabs_auto_enabled.setEnabled(bool(checked))
            self.cut_tabs_manual_enabled.setEnabled(bool(checked))
            self.cut_tabs_3d.setEnabled(bool(checked))
            edit_button.setEnabled(
                bool(checked) and self.cut_tabs_manual_enabled.isChecked()
            )

        self.cut_tabs_enabled.toggled.connect(update_tab_fields)
        self.cut_tabs_manual_enabled.toggled.connect(update_tab_fields)
        update_tab_fields(self.cut_tabs_enabled.isChecked())
        return self._compact_group(group)

    def _edit_tabs_settings(self):
        if self._tab_marker_callback is not None:
            self._finish_tab_marker_mode()
            return
        self._start_tab_marker_mode()

    def _start_tab_marker_mode(self):
        try:
            geometry = get_selected_geometry()
        except Exception as error:
            QtWidgets.QMessageBox.warning(self, "Posicionar tabs", str(error))
            return
        if not geometry.get("contours"):
            QtWidgets.QMessageBox.warning(
                self, "Posicionar tabs", "Selecione um contorno fechado antes de posicionar tabs."
            )
            return
        active_view = FreeCADGui.activeDocument().activeView() if FreeCADGui.activeDocument() else None
        if active_view is None:
            return
        if not any(isinstance(position, dict) for position in self.cut_tab_positions):
            self.cut_tab_positions = []
        self._tab_marker_geometry = geometry
        self._tab_marker_callback = active_view.addEventCallback(
            "SoMouseButtonEvent", self._on_tab_marker_event
        )
        self.cut_tabs_edit_button.setText("Concluir posicionamento")

    def _finish_tab_marker_mode(self):
        active_view = FreeCADGui.activeDocument().activeView() if FreeCADGui.activeDocument() else None
        if active_view is not None and self._tab_marker_callback is not None:
            active_view.removeEventCallback("SoMouseButtonEvent", self._tab_marker_callback)
        self._tab_marker_callback = None
        self._tab_marker_geometry = None
        self.cut_tabs_edit_button.setText("Posicionar tabs...")

    def _nearest_tab_point(self, point, geometry):
        best = None
        for contour in geometry.get("contours", []):
            points = list(contour or [])
            if len(points) < 2:
                continue
            for start, end in zip(points, points[1:] + points[:1]):
                dx = float(end[0]) - float(start[0])
                dy = float(end[1]) - float(start[1])
                length_squared = dx * dx + dy * dy
                if length_squared <= 1e-9:
                    continue
                ratio = ((point.x - float(start[0])) * dx + (point.y - float(start[1])) * dy) / length_squared
                ratio = max(0.0, min(1.0, ratio))
                candidate = (float(start[0]) + dx * ratio, float(start[1]) + dy * ratio)
                distance_squared = (point.x - candidate[0]) ** 2 + (point.y - candidate[1]) ** 2
                if best is None or distance_squared < best[0]:
                    best = (distance_squared, candidate)
        return best[1] if best is not None else None

    def _event_world_point(self, event):
        try:
            position = event["Position"]
            active_view = FreeCADGui.activeDocument().activeView()
            info = active_view.getObjectInfo(position)
            if info and "x" in info and "y" in info:
                return FreeCAD.Vector(float(info["x"]), float(info["y"]), float(info.get("z", 0.0)))
            return active_view.getPoint(position[0], position[1])
        except Exception:
            return None

    def _on_tab_marker_event(self, event):
        if event.get("State") != "DOWN" or event.get("Button") != "BUTTON1":
            return
        point = self._event_world_point(event)
        if point is None or self._tab_marker_geometry is None:
            return
        nearest = self._nearest_tab_point(point, self._tab_marker_geometry)
        if nearest is None:
            return
        self.cut_tab_positions.append({"x": nearest[0], "y": nearest[1]})
        self._render_tab_markers()

    def _render_tab_markers(self):
        doc = FreeCAD.ActiveDocument
        if doc is None:
            return
        group = doc.getObject("WoodCAM2D_TabMarkers")
        if group is None:
            group = doc.addObject("App::DocumentObjectGroup", "WoodCAM2D_TabMarkers")
            group.Label = "WoodCAM 2D — Tabs"
        for obj in list(getattr(group, "Group", []) or []):
            doc.removeObject(obj.Name)
        for index, position in enumerate(self.cut_tab_positions, start=1):
            if not isinstance(position, dict):
                continue
            center = FreeCAD.Vector(float(position["x"]), float(position["y"]), 0.3)
            marker = doc.addObject("Part::Feature", "WoodCAMTabMarker%03d" % index)
            marker.Label = "Tab %02d" % index
            marker.Shape = Part.makeCircle(4.0, center, FreeCAD.Vector(0, 0, 1))
            self._set_view_style(marker, (0.9, 0.1, 0.1), line_width=3, transparency=0)
            group.addObject(marker)
        doc.recompute()

    def _edit_tabs_settings_legacy(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Posicionar tabs")
        layout = QtWidgets.QVBoxLayout(dialog)
        placement_mode = QtWidgets.QComboBox()
        placement_mode.addItems(["Automático", "Manual"])
        placement_mode.setCurrentIndex(1 if self.cut_tab_positions else 0)
        automatic_row = QtWidgets.QHBoxLayout()
        automatic_row.addWidget(QtWidgets.QLabel("Quantidade automática"))
        count = QtWidgets.QSpinBox()
        count.setRange(1, 999)
        try:
            count.setValue(int(float(self.operation_fields["cut"]["tab_count"].text())))
        except (KeyError, ValueError):
            count.setValue(4)
        automatic_row.addWidget(count)
        distribute_button = QtWidgets.QPushButton("Distribuir")
        automatic_row.addWidget(distribute_button)
        automatic_row.addStretch(1)
        layout.addWidget(placement_mode)
        layout.addLayout(automatic_row)
        placement = TabPlacementWidget()
        placement.setToolTip(
            "Clique no perímetro para criar uma tab, arraste para movê-la ou use o botão direito para removê-la."
        )
        placement.set_positions(self.cut_tab_positions)
        layout.addWidget(placement)

        def distribute():
            tab_count = max(1, count.value())
            placement.set_positions(
                [(index + 0.5) / tab_count for index in range(tab_count)]
            )

        def update_mode(index):
            manual = index == 1
            placement.setEnabled(manual)
            if not manual:
                distribute()

        distribute_button.clicked.connect(distribute)
        placement_mode.currentIndexChanged.connect(update_mode)
        update_mode(placement_mode.currentIndex())
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        exec_dialog = getattr(dialog, "exec", None) or getattr(dialog, "exec_")
        if exec_dialog() != QtWidgets.QDialog.Accepted:
            return
        self.operation_fields["cut"]["tab_count"].setText(str(count.value()))
        self.cut_tab_positions = (
            list(placement.positions)
            if placement_mode.currentIndex() == 1
            else []
        )

    def _make_cut_entry_group(self):
        group = QtWidgets.QGroupBox("Entradas, ordem e ponto inicial")
        outer = QtWidgets.QVBoxLayout(group)
        outer.setContentsMargins(10, 8, 10, 8)
        tabs = QtWidgets.QTabWidget()
        tabs.setIconSize(QtCore.QSize(20, 20))
        outer.addWidget(tabs)

        ramp_tab = QtWidgets.QWidget()
        ramp_grid = QtWidgets.QGridLayout(ramp_tab)
        ramp_grid.setHorizontalSpacing(8)
        ramp_grid.setVerticalSpacing(6)
        checkbox = QtWidgets.QCheckBox("Adicionar rampas ao percurso")
        checkbox.setChecked(DEFAULT_PRESETS["use_ramp"])
        ramp_grid.addWidget(checkbox, 0, 0, 1, 3)
        self.operation_ramp_checks["cut"] = checkbox
        self.cut_ramp_type_buttons = []
        self.cut_ramp_type_group = QtWidgets.QButtonGroup(ramp_tab)
        self.cut_ramp_type_group.setExclusive(True)
        for column, (label, icon_kind, enabled) in enumerate(
            (
                ("Suave", "entry_smooth", True),
                ("Zigue-zague", "entry_zigzag", True),
                ("Espiral", "entry_spiral", True),
            )
        ):
            card = QtWidgets.QFrame()
            card_layout = QtWidgets.QVBoxLayout(card)
            card_layout.setContentsMargins(2, 2, 2, 2)
            card_layout.addWidget(self._asset_widget(icon_kind), 0, QtCore.Qt.AlignCenter)
            radio = QtWidgets.QRadioButton(label)
            radio.setChecked(column == 0)
            radio.setEnabled(enabled)
            self.cut_ramp_type_group.addButton(radio, column)
            card_layout.addWidget(radio, 0, QtCore.Qt.AlignCenter)
            self.cut_ramp_type_buttons.append(radio)
            ramp_grid.addWidget(card, 1, column)
        ramp_field = self._add_operation_field(
            ramp_grid,
            2,
            "Distância da rampa (mm)",
            "cut",
            "ramp_length",
        )
        checkbox.toggled.connect(ramp_field.setEnabled)
        checkbox.toggled.connect(
            lambda checked: [
                button.setEnabled(bool(checked))
                for index, button in enumerate(self.cut_ramp_type_buttons)
            ]
        )
        ramp_field.setEnabled(checkbox.isChecked())
        tabs.addTab(ramp_tab, "Rampa")

        entry_tab = QtWidgets.QWidget()
        entry_grid = QtWidgets.QGridLayout(entry_tab)
        entry_grid.addWidget(self._asset_widget("entry_smooth"), 0, 0, 2, 1)
        self.cut_smart_entry_check = QtWidgets.QCheckBox(
            "Otimizar entrada para evitar cantos e reduzir deslocamento"
        )
        self.cut_smart_entry_check.setChecked(True)
        entry_grid.addWidget(self.cut_smart_entry_check, 0, 1)
        entry_note = QtWidgets.QLabel(
            "Equivale ao ponto inicial inteligente atual: evita começar exatamente no vértice."
        )
        entry_note.setWordWrap(True)
        entry_grid.addWidget(entry_note, 1, 1)
        tabs.addTab(entry_tab, "Entradas")

        order_tab = QtWidgets.QWidget()
        order_grid = QtWidgets.QGridLayout(order_tab)
        order_grid.addWidget(self._asset_widget("order"), 0, 0, 2, 1)
        self.cut_order_combo = QtWidgets.QComboBox()
        self.cut_order_combo.addItems(
            [
                "Otimizar menor deslocamento",
                "Usar ordem de seleção dos vetores",
            ]
        )
        self.cut_order_combo.setEnabled(False)
        self.cut_order_combo.setToolTip("Preparado para etapa futura; o corte atual já usa entrada inteligente por percurso.")
        order_grid.addWidget(QtWidgets.QLabel("Sequência"), 0, 1)
        order_grid.addWidget(self.cut_order_combo, 0, 2)
        tabs.addTab(order_tab, "Ordem")

        start_tab = QtWidgets.QWidget()
        start_grid = QtWidgets.QGridLayout(start_tab)
        start_grid.addWidget(self._asset_widget("start_point"), 0, 0, 4, 1)
        self.cut_start_keep_radio = QtWidgets.QRadioButton("Manter pontos atuais")
        self.cut_start_optimize_radio = QtWidgets.QRadioButton("Otimizar pontos iniciais")
        self.cut_start_near_radio = QtWidgets.QRadioButton(
            "Mover pontos para perto do ponto escolhido"
        )
        self.cut_start_optimize_radio.setChecked(True)
        self.cut_start_near_radio.setEnabled(False)
        self.cut_start_near_radio.setToolTip("Preparado para seleção manual futura.")
        for row, button in enumerate(
            (
                self.cut_start_keep_radio,
                self.cut_start_optimize_radio,
                self.cut_start_near_radio,
            )
        ):
            start_grid.addWidget(button, row, 1, 1, 2)
        tabs.addTab(start_tab, "Ponto inicial")
        return self._compact_group(group)

    def _make_cut_name_group(self):
        group = QtWidgets.QGroupBox("Nome")
        grid = QtWidgets.QGridLayout(group)
        grid.setContentsMargins(10, 8, 10, 8)
        self.operation_names["cut"] = QtWidgets.QLineEdit("Corte 1")
        grid.addWidget(QtWidgets.QLabel("Nome do percurso"), 0, 0)
        grid.addWidget(self.operation_names["cut"], 0, 1)
        selection_label = QtWidgets.QLabel("Seleção de vetores: Manual")
        selection_label.setStyleSheet("color: #475569;")
        grid.addWidget(selection_label, 1, 0, 1, 2)
        return self._compact_group(group)

    def _make_corner_group(self):
        group, grid = self._make_icon_group(
            "Cantos e mudanças de direção",
            "corner",
        )
        self.corner_slowdown_enabled = QtWidgets.QCheckBox(
            "Reduzir avanço próximo aos cantos"
        )
        self.corner_slowdown_enabled.setChecked(
            DEFAULT_PRESETS["corner_slowdown_enabled"]
        )
        self.corner_slowdown_enabled.setToolTip(
            "Divide o percurso antes e depois do vértice para a máquina chegar "
            "ao canto com menor velocidade."
        )
        grid.addWidget(self.corner_slowdown_enabled, 0, 0, 1, 3)
        corner_fields = []
        for row, label, name in (
            (1, "Mudança mínima de direção (°)", "corner_angle_threshold"),
            (2, "Avanço no canto (%)", "corner_feed_percent"),
            (3, "Distância antes/depois (mm)", "corner_slowdown_distance"),
        ):
            corner_fields.append(
                self._add_operation_field(grid, row, label, "cut", name)
            )

        def update_corner_fields(checked):
            for field in corner_fields:
                field.setEnabled(bool(checked))

        self.corner_slowdown_enabled.toggled.connect(update_corner_fields)
        update_corner_fields(self.corner_slowdown_enabled.isChecked())
        note = QtWidgets.QLabel(
            "Ex.: avanço 2700 e 40% gera 1080 mm/min somente na região do canto."
        )
        note.setWordWrap(True)
        grid.addWidget(note, 4, 0, 1, 3)
        return self._compact_group(group)

    def _apply_operation_tool_preset(self, mode, name):
        preset = self.tool_database.get(name)
        if not preset:
            return
        preset = self._normalize_tool_values(preset)
        preview = self.operation_tool_previews.get(mode)
        if preview is not None:
            preview.set_tool_type(preset.get("tool_type", "end_mill"))
        fields = self.operation_fields.get(mode, {})
        for key, value in preset.items():
            if key in fields:
                fields[key].setText(self._format_value(value))
        self._update_passes_label(mode)

    def _parse_operation_float(self, mode, key):
        label = self.operation_field_labels[mode][key]
        text = self.operation_fields[mode][key].text().strip().replace(",", ".")
        try:
            return float(text)
        except ValueError:
            raise ValueError(f"O campo '{label}' da aba ativa precisa ser um número válido.")

    def _format_value(self, value):
        if isinstance(value, int):
            return str(value)
        return f"{float(value):g}"

    def _set_field(self, key, value):
        self.fields[key].setText(self._format_value(value))

    def _apply_material_preset(self, name):
        preset = MATERIAL_PRESETS.get(name)
        if not preset:
            return
        for key, value in preset.items():
            if key in self.fields:
                self._set_field(key, value)
        default_cut_depth = (
            float(preset.get("material_thickness", 0.0))
            + float(preset.get("depth_extra", 0.0))
        )
        if default_cut_depth > 0.0:
            for mode, fields in self.operation_fields.items():
                if mode == "pocket":
                    continue
                if "cut_depth" in fields:
                    fields["cut_depth"].setText(self._format_value(default_cut_depth))

    def _select_output_path(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Salvar G-code",
            self.output_path.text(),
            "G-code (*.nc *.gcode *.tap)",
        )
        if path:
            self.output_path.setText(path)

    def _parse_float(self, key):
        label = self.field_labels[key]
        text = self.fields[key].text().strip().replace(",", ".")
        if text == "" and key in {
            "job_origin_x",
            "job_origin_y",
            "origin_x",
            "origin_y",
        }:
            return 0.0
        try:
            return float(text)
        except ValueError:
            raise ValueError(f"O campo '{label}' precisa ser um número válido.")

    def _collect_settings(self):
        settings = {}
        for key in self.fields:
            settings[key] = self._parse_float(key)
        self._add_hidden_machine_limits(settings)

        settings["output_path"] = self.output_path.text().strip()
        settings["depth_extra"] = 0.0
        settings["job_type"] = self._selected_job_type()
        settings["job_z_zero_mode"] = self._job_z_zero_mode()
        settings["job_origin_anchor"] = self._job_origin_anchor()
        settings["job_use_selection_bounds_origin"] = (
            self.job_use_selection_bounds_origin.isChecked()
        )
        settings["z_zero_mode"] = self._z_zero_mode()
        settings["origin_anchor"] = self._origin_anchor()
        settings["use_selection_bounds_origin"] = (
            self.use_selection_bounds_origin.isChecked()
        )
        settings["model_position_mode"] = self._model_position_mode()
        operation_mode = self._operation_mode_for_index(
            self.operation_tabs.currentIndex()
        )
        if operation_mode is None:
            operation_mode = self.last_operation_mode
        settings["operation_mode"] = operation_mode
        schedule = self._pass_schedules.get(operation_mode)
        if schedule:
            settings["pass_depths"] = list(schedule)
        for key in self.operation_fields[operation_mode]:
            value = self._parse_operation_float(operation_mode, key)
            settings[key] = int(round(value)) if key == "rpm" else value

        settings["operation_name"] = (
            self.operation_names[operation_mode].text().strip()
            or STAGE_LABELS[operation_mode]
        )
        if operation_mode in {"rough3d", "finish3d"}:
            # A cota real virá da superfície. Este valor mantém o contrato de
            # validação comum e representa o envelope máximo disponível.
            settings["start_depth"] = 0.0
            settings["cut_depth"] = float(settings["material_thickness"])
        settings["final_depth"] = settings["start_depth"] + settings["cut_depth"]
        settings["outer_cut_side"] = self.CUT_SIDE_BY_INDEX[
            self.operation_combo.currentIndex()
        ]
        settings["cut_side"] = settings["outer_cut_side"]
        settings["pocket_strategy"] = (
            "raster"
            if self.pocket_strategy_combo.currentIndex() == 1
            else "offset"
        )
        settings["pocket_climb"] = self.pocket_direction_combo.currentIndex() == 0
        settings["pocket_profile_pass"] = self.pocket_profile_combo.currentIndex() == 1
        tool_name = self.operation_tool_combos[operation_mode].currentText()
        tool_values = self._tool_values_for_name(tool_name)
        settings["tool_name"] = tool_name
        settings["tool_type"] = tool_values.get("tool_type", "end_mill")
        settings["tool_number"] = int(tool_values.get("tool_number", 0) or 0)
        if operation_mode == "rough3d":
            settings["boundary_mode"] = (
                "model", "material", "selected_vectors", "selected_level"
            )[self.rough3d_boundary_combo.currentIndex()]
            settings["rough3d_strategy"] = (
                "z_level" if self.rough3d_strategy_combo.currentIndex() == 0 else "raster_3d"
            )
            settings["rough3d_profile"] = ("last", "first", "none")[
                self.rough3d_profile_combo.currentIndex()
            ]
            settings["rough3d_order"] = (
                "level" if self.rough3d_order_combo.currentIndex() == 0 else "depth"
            )
            settings["rough3d_axis"] = "x" if self.rough3d_axis_combo.currentIndex() == 0 else "y"
            settings["rough3d_reverse"] = self.rough3d_reverse_check.isChecked()
        elif operation_mode == "finish3d":
            settings["boundary_mode"] = (
                "model", "material", "selected_vectors", "selected_level"
            )[self.finish3d_boundary_combo.currentIndex()]
            settings["finish3d_strategy"] = (
                "raster" if self.finish3d_strategy_combo.currentIndex() == 0 else "offset"
            )
            settings["finish3d_reverse"] = self.finish3d_reverse_check.isChecked()
        settings["use_helical_drilling"] = self.use_helical_drilling.isChecked()
        settings["cut_climb"] = bool(
            getattr(self, "cut_direction_climb", None) is None
            or self.cut_direction_climb.isChecked()
        )
        settings["cut_separate_last_pass"] = bool(
            getattr(self, "cut_separate_last_pass", None) is not None
            and self.cut_separate_last_pass.isChecked()
        )
        settings["cut_tabs_enabled"] = bool(
            getattr(self, "cut_tabs_enabled", None) is not None
            and self.cut_tabs_enabled.isChecked()
        )
        settings["tab_positions"] = list(self.cut_tab_positions)
        if hasattr(self, "cut_tabs_manual_enabled") and not self.cut_tabs_manual_enabled.isChecked():
            settings["tab_positions"] = []
        if hasattr(self, "cut_tabs_auto_enabled") and not self.cut_tabs_auto_enabled.isChecked():
            settings["tab_count"] = 0
        settings["tabs_3d"] = bool(
            getattr(self, "cut_tabs_3d", None) is not None
            and self.cut_tabs_3d.isChecked()
        )
        settings["corner_slowdown_enabled"] = (
            self.corner_slowdown_enabled.isChecked()
        )
        settings["smart_entry"] = bool(
            getattr(self, "cut_smart_entry_check", None) is None
            or self.cut_smart_entry_check.isChecked()
        )
        if hasattr(self, "cut_start_keep_radio") and self.cut_start_keep_radio.isChecked():
            settings["smart_entry"] = False
        if hasattr(self, "cut_start_optimize_radio") and self.cut_start_optimize_radio.isChecked():
            settings["smart_entry"] = True
        settings["return_to_start"] = self.return_to_start.isChecked()
        settings["use_model_hole_depths"] = self.use_model_hole_depths.isChecked()
        settings["peck_enabled"] = self.peck_enabled.isChecked()
        settings["peck_retract_mode"] = (
            "previous_step"
            if self.peck_retract_mode.currentIndex() == 1
            else "surface"
        )
        settings["dwell_enabled"] = self.dwell_enabled.isChecked()
        settings["preserve_hole_order"] = self.hole_order_combo.currentIndex() == 1
        ramp_check = self.operation_ramp_checks.get(operation_mode)
        settings["use_ramp"] = bool(ramp_check and ramp_check.isChecked())
        settings.setdefault("ramp_length", 0.0)
        if not settings["use_ramp"]:
            settings["ramp_length"] = 0.0
        settings["ramp_feed"] = min(settings["feed_xy"] * 0.6, settings["feed_xy"])
        settings["ramp_type"] = (
            ("smooth", "zigzag", "spiral")[
                next(
                    (index for index, button in enumerate(self.cut_ramp_type_buttons) if button.isChecked()),
                    0,
                )
            ]
            if operation_mode == "cut"
            else "smooth"
        )
        return settings

    def _confirm_material_pierce(self, settings):
        material_thickness = float(settings.get("material_thickness", 0.0) or 0.0)
        final_depth = float(settings.get("final_depth", 0.0) or 0.0)
        if material_thickness <= 0.0 or final_depth <= material_thickness + 1e-6:
            return True

        message = (
            "A profundidade da ferramenta atual vai exceder a espessura do material.\n\n"
            f"Espessura do material = {material_thickness:g} mm\n"
            f"Profundidade máxima da ferramenta = {final_depth:g} mm\n\n"
            "Pressione OK para continuar com o cálculo do percurso.\n\n"
            "No entanto, o percurso resultante VAI FURAR a base do material, "
            "possivelmente danificando a mesa da máquina.\n\n"
            "Se não tiver uma base de sacrifício abaixo do material a cortar, "
            "cancele esta operação."
        )
        answer = QtWidgets.QMessageBox.warning(
            self,
            "AVISO - A ferramenta vai FURAR o material",
            message,
            QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        )
        return answer == QtWidgets.QMessageBox.Ok

    def _collect_work_setup_settings(self):
        settings = {}
        for key in self.fields:
            settings[key] = self._parse_float(key)
        self._add_hidden_machine_limits(settings)
        settings["output_path"] = self.output_path.text().strip()
        settings["depth_extra"] = 0.0
        settings["job_type"] = self._selected_job_type()
        settings["job_z_zero_mode"] = self._job_z_zero_mode()
        settings["job_origin_anchor"] = self._job_origin_anchor()
        settings["job_use_selection_bounds_origin"] = (
            self.job_use_selection_bounds_origin.isChecked()
        )
        settings["z_zero_mode"] = self._z_zero_mode()
        settings["origin_anchor"] = self._origin_anchor()
        settings["use_selection_bounds_origin"] = (
            self.use_selection_bounds_origin.isChecked()
        )
        settings["model_position_mode"] = self._model_position_mode()
        settings["operation_mode"] = "setup"
        return settings

    def _validate_work_setup_settings(self, settings):
        for key, label in (
            ("job_width", "Largura X"),
            ("job_height", "Altura Y"),
            ("job_depth", "Altura Z"),
            ("model_gap_above", "Folga acima do modelo"),
            ("model_gap_below", "Folga abaixo do modelo"),
            ("machine_x_size", "Área útil X"),
            ("machine_y_size", "Área útil Y"),
        ):
            if float(settings.get(key, 0.0) or 0.0) < 0.0:
                raise ValueError(f"{label} não pode ser negativa.")
        for key, label in (
            ("job_origin_x", "X do trabalho"),
            ("job_origin_y", "Y do trabalho"),
            ("origin_x", "X do datum"),
            ("origin_y", "Y do datum"),
        ):
            if not math.isfinite(float(settings.get(key, 0.0) or 0.0)):
                raise ValueError(f"{label} precisa ser um número finito.")
        if float(settings.get("material_thickness", 0.0) or 0.0) <= 0.0:
            raise ValueError("A espessura/altura Z do material deve ser maior que zero.")
        material_thickness = float(settings.get("material_thickness", 0.0) or 0.0)
        if float(settings.get("model_gap_above", 0.0) or 0.0) > material_thickness:
            raise ValueError("A folga acima do modelo não pode passar da espessura do material.")
        if float(settings.get("model_gap_below", 0.0) or 0.0) > material_thickness:
            raise ValueError("A folga abaixo do modelo não pode passar da espessura do material.")
        width, height = self._work_area_dimensions(settings)
        if width <= 0.0 or height <= 0.0:
            raise ValueError(
                "Informe Largura X e Altura Y da área de trabalho, "
                "ou selecione um contorno para usar como referência."
            )

    def _is_work_setup_tab_active(self):
        index = self.operation_tabs.currentIndex()
        if index < 0:
            return False
        return self._tab_title(index) in {"Trabalho", "Material"}

    def _show_work_area_preview(self, settings):
        doc = FreeCAD.ActiveDocument
        if doc is None:
            raise RuntimeError("Nenhum documento ativo do FreeCAD encontrado.")

        self._clear_existing_preview(doc)
        preview_group = doc.addObject("App::DocumentObjectGroup", "WoodCAM2D_Preview")
        preview_group.Label = "Prévia da configuração"
        ensure_woodcam_tree(doc).work_area.addObject(preview_group)
        selected_geometry = self._selected_geometry_for_preview()
        # A configuração da chapa não depende de uma seleção 3D. Toda
        # geometria que chega pelos adapters já está no XY do documento; o
        # datum serve apenas como início/retorno da máquina.
        settings["_xy_origin_offset"] = (0.0, 0.0)
        area_object = self._add_work_area_preview(doc, preview_group, settings)
        if area_object is None:
            doc.removeObject(preview_group.Name)
            raise ValueError(
                "Não foi possível desenhar a área de trabalho. "
                "Informe Largura X e Altura Y maiores que zero."
            )
        self._add_material_datum_preview(
            doc,
            preview_group,
            settings,
            selected_geometry if settings.get("use_selection_bounds_origin", False) else {},
        )
        doc.recompute()
        gui_document = getattr(FreeCADGui, "ActiveDocument", None)
        if gui_document:
            gui_document.ActiveView.fitAll()
        return area_object

    def _add_hidden_machine_limits(self, settings):
        parameters = None
        try:
            parameters = FreeCAD.ParamGet(WOODCAM_PARAMETER_PATH)
        except Exception:
            pass
        for key in ("machine_x_size", "machine_y_size"):
            if key in settings:
                continue
            default_value = float(DEFAULT_PRESETS[key])
            if parameters is not None:
                try:
                    default_value = parameters.GetFloat(key, default_value)
                except Exception:
                    pass
            settings[key] = default_value

    def _persist_machine_settings(self, settings):
        try:
            parameters = FreeCAD.ParamGet(WOODCAM_PARAMETER_PATH)
            parameters.SetFloat("machine_x_size", settings["machine_x_size"])
            parameters.SetFloat("machine_y_size", settings["machine_y_size"])
            parameters.SetFloat("start_x", settings["start_x"])
            parameters.SetFloat("start_y", settings["start_y"])
            parameters.SetFloat("job_width", settings["job_width"])
            parameters.SetFloat("job_height", settings["job_height"])
            parameters.SetFloat("job_depth", settings.get("job_depth", 0.0))
            parameters.SetFloat("job_origin_x", settings.get("job_origin_x", 0.0))
            parameters.SetFloat("job_origin_y", settings.get("job_origin_y", 0.0))
            parameters.SetFloat("origin_x", settings.get("origin_x", 0.0))
            parameters.SetFloat("origin_y", settings.get("origin_y", 0.0))
            parameters.SetFloat("material_thickness", settings["material_thickness"])
            parameters.SetFloat("safe_height", settings["safe_height"])
            parameters.SetFloat("retract_height", settings["retract_height"])
            parameters.SetBool(
                "return_to_start",
                settings.get("return_to_start", DEFAULT_PRESETS["return_to_start"]),
            )
            parameters.SetString("job_type", settings["job_type"])
            parameters.SetString(
                "job_z_zero_mode",
                settings.get("job_z_zero_mode", DEFAULT_PRESETS["job_z_zero_mode"]),
            )
            parameters.SetString(
                "job_origin_anchor",
                settings.get("job_origin_anchor", DEFAULT_PRESETS["job_origin_anchor"]),
            )
            parameters.SetString("z_zero_mode", settings["z_zero_mode"])
            parameters.SetString("origin_anchor", settings["origin_anchor"])
            parameters.SetString(
                "model_position_mode",
                settings.get("model_position_mode", DEFAULT_PRESETS["model_position_mode"]),
            )
            parameters.SetFloat(
                "model_gap_above",
                settings.get("model_gap_above", DEFAULT_PRESETS["model_gap_above"]),
            )
            parameters.SetFloat(
                "model_gap_below",
                settings.get("model_gap_below", DEFAULT_PRESETS["model_gap_below"]),
            )
            parameters.SetBool(
                "use_selection_bounds_origin",
                settings["use_selection_bounds_origin"],
            )
            parameters.SetBool(
                "job_use_selection_bounds_origin",
                settings.get(
                    "job_use_selection_bounds_origin",
                    DEFAULT_PRESETS["job_use_selection_bounds_origin"],
                ),
            )
            self._flush_preferences()
        except Exception as error:
            FreeCAD.Console.PrintWarning(
                "WoodCAM 2D: não foi possível salvar as configurações: "
                f"{error}\n"
            )

    def _load_job_preferences(self):
        self._loading_job_preferences = True
        try:
            parameters = FreeCAD.ParamGet(WOODCAM_PARAMETER_PATH)
            job_type = parameters.GetString(
                "job_type",
                DEFAULT_PRESETS["job_type"],
            )
            if (
                job_type in getattr(self, "job_type_buttons", {})
                and self.job_type_buttons[job_type].isEnabled()
            ):
                self.job_type_buttons[job_type].setChecked(True)

            job_z_zero_mode = parameters.GetString(
                "job_z_zero_mode",
                DEFAULT_PRESETS["job_z_zero_mode"],
            )
            if job_z_zero_mode == "machine_bed":
                self.job_z_zero_bed.setChecked(True)
            else:
                self.job_z_zero_surface.setChecked(True)

            job_origin_anchor = parameters.GetString(
                "job_origin_anchor",
                DEFAULT_PRESETS["job_origin_anchor"],
            )
            if job_origin_anchor in getattr(self, "job_origin_buttons", {}):
                self.job_origin_buttons[job_origin_anchor].setChecked(True)

            self.job_use_selection_bounds_origin.setChecked(
                parameters.GetBool(
                    "job_use_selection_bounds_origin",
                    DEFAULT_PRESETS["job_use_selection_bounds_origin"],
                )
            )

            z_zero_mode = parameters.GetString(
                "z_zero_mode",
                DEFAULT_PRESETS["z_zero_mode"],
            )
            if z_zero_mode == "machine_bed":
                self.z_zero_bed.setChecked(True)
            else:
                self.z_zero_surface.setChecked(True)

            origin_anchor = parameters.GetString(
                "origin_anchor",
                DEFAULT_PRESETS["origin_anchor"],
            )
            if origin_anchor in getattr(self, "origin_buttons", {}):
                self.origin_buttons[origin_anchor].setChecked(True)

            model_position_mode = parameters.GetString(
                "model_position_mode",
                DEFAULT_PRESETS["model_position_mode"],
            )
            if model_position_mode == "gap_below":
                self.model_gap_below_radio.setChecked(True)
            else:
                self.model_gap_above_radio.setChecked(True)

            self.use_selection_bounds_origin.setChecked(
                parameters.GetBool(
                    "use_selection_bounds_origin",
                    DEFAULT_PRESETS["use_selection_bounds_origin"],
                )
            )
            self.return_to_start.setChecked(
                parameters.GetBool(
                    "return_to_start",
                    DEFAULT_PRESETS["return_to_start"],
                )
            )
        except Exception:
            pass
        finally:
            self._loading_job_preferences = False

    def _operation_preferences_snapshot(self):
        """Estado editável das abas de usinagem, independente do documento."""
        return {
            "fields": {
                mode: {name: field.text() for name, field in fields.items()}
                for mode, fields in self.operation_fields.items()
            },
            "names": {
                mode: field.text() for mode, field in self.operation_names.items()
            },
            "tools": {
                mode: combo.currentText()
                for mode, combo in self.operation_tool_combos.items()
            },
            "combos": {
                name: getattr(self, name).currentIndex()
                for name in (
                    "operation_combo",
                    "pocket_strategy_combo",
                    "pocket_direction_combo",
                    "pocket_profile_combo",
                    "peck_retract_mode",
                    "hole_order_combo",
                    "cut_order_combo",
                    "rough3d_boundary_combo",
                    "rough3d_strategy_combo",
                    "rough3d_profile_combo",
                    "rough3d_order_combo",
                    "rough3d_axis_combo",
                    "finish3d_boundary_combo",
                    "finish3d_strategy_combo",
                )
                if hasattr(self, name)
            },
            "checks": {
                name: getattr(self, name).isChecked()
                for name in (
                    "return_to_start",
                    "use_model_hole_depths",
                    "use_helical_drilling",
                    "peck_enabled",
                    "dwell_enabled",
                    "cut_separate_last_pass",
                    "corner_slowdown_enabled",
                    "cut_tabs_enabled",
                    "cut_tabs_auto_enabled",
                    "cut_tabs_3d",
                    "cut_tabs_manual_enabled",
                    "cut_smart_entry_check",
                    "cut_direction_climb",
                    "cut_direction_conventional",
                    "cut_start_keep_radio",
                    "cut_start_optimize_radio",
                    "cut_start_near_radio",
                    "rough3d_reverse_check",
                    "finish3d_reverse_check",
                )
                if hasattr(self, name)
            },
            "ramps": {
                mode: checkbox.isChecked()
                for mode, checkbox in self.operation_ramp_checks.items()
            },
            "cut_ramp_type": next(
                (
                    index
                    for index, button in enumerate(
                        getattr(self, "cut_ramp_type_buttons", [])
                    )
                    if button.isChecked()
                ),
                0,
            ),
            "tab_positions": list(self.cut_tab_positions),
            "pass_schedules": self._pass_schedules,
        }

    def _save_operation_preferences(self, *_args):
        if self._restoring_operation_preferences:
            return
        try:
            FreeCAD.ParamGet(WOODCAM_PARAMETER_PATH).SetString(
                "operation_preferences_json",
                json.dumps(self._operation_preferences_snapshot(), ensure_ascii=False),
            )
            self._flush_preferences()
        except Exception as error:
            FreeCAD.Console.PrintWarning(
                "WoodCAM 2D: não foi possível salvar as preferências de usinagem: "
                f"{error}\n"
            )

    def _load_operation_preferences(self):
        try:
            raw = FreeCAD.ParamGet(WOODCAM_PARAMETER_PATH).GetString(
                "operation_preferences_json", ""
            )
            saved = json.loads(raw) if raw else {}
            if not isinstance(saved, dict):
                return
        except Exception:
            return

        self._restoring_operation_preferences = True
        try:
            for mode, name in saved.get("tools", {}).items():
                combo = self.operation_tool_combos.get(mode)
                if combo is not None and combo.findText(str(name)) >= 0:
                    combo.setCurrentText(str(name))
            for name, index in saved.get("combos", {}).items():
                widget = getattr(self, name, None)
                if widget is not None and 0 <= int(index) < widget.count():
                    widget.setCurrentIndex(int(index))
            for name, checked in saved.get("checks", {}).items():
                widget = getattr(self, name, None)
                if widget is not None:
                    widget.setChecked(bool(checked))
            for mode, checked in saved.get("ramps", {}).items():
                checkbox = self.operation_ramp_checks.get(mode)
                if checkbox is not None:
                    checkbox.setChecked(bool(checked))
            ramp_buttons = getattr(self, "cut_ramp_type_buttons", [])
            ramp_index = int(saved.get("cut_ramp_type", 0))
            if 0 <= ramp_index < len(ramp_buttons):
                ramp_buttons[ramp_index].setChecked(True)
            for mode, values in saved.get("fields", {}).items():
                for name, value in values.items():
                    field = self.operation_fields.get(mode, {}).get(name)
                    if field is not None:
                        field.setText(str(value))
            for mode, value in saved.get("names", {}).items():
                field = self.operation_names.get(mode)
                if field is not None:
                    field.setText(str(value))
            positions = saved.get("tab_positions")
            if isinstance(positions, list):
                self.cut_tab_positions = positions
            schedules = saved.get("pass_schedules")
            if isinstance(schedules, dict):
                self._pass_schedules = {
                    str(mode): [float(value) for value in values]
                    for mode, values in schedules.items()
                    if isinstance(values, list)
                }
        finally:
            self._restoring_operation_preferences = False

    def _connect_operation_preference_savers(self):
        for fields in self.operation_fields.values():
            for field in fields.values():
                field.textChanged.connect(self._save_operation_preferences)
        for field in self.operation_names.values():
            field.textChanged.connect(self._save_operation_preferences)
        for combo in self.operation_tool_combos.values():
            combo.currentTextChanged.connect(self._save_operation_preferences)
        for name in (
            "operation_combo", "pocket_strategy_combo", "pocket_direction_combo",
            "pocket_profile_combo", "peck_retract_mode", "hole_order_combo",
            "cut_order_combo", "rough3d_boundary_combo", "rough3d_strategy_combo",
            "rough3d_profile_combo", "rough3d_order_combo", "rough3d_axis_combo",
            "finish3d_boundary_combo", "finish3d_strategy_combo",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.currentIndexChanged.connect(self._save_operation_preferences)
        for name in (
            "return_to_start", "use_model_hole_depths", "use_helical_drilling",
            "peck_enabled", "dwell_enabled", "cut_separate_last_pass",
            "corner_slowdown_enabled", "cut_tabs_enabled", "cut_tabs_auto_enabled",
            "cut_tabs_3d", "cut_tabs_manual_enabled", "cut_smart_entry_check",
            "cut_direction_climb", "cut_direction_conventional", "cut_start_keep_radio",
            "cut_start_optimize_radio", "cut_start_near_radio",
            "rough3d_reverse_check", "finish3d_reverse_check",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.toggled.connect(self._save_operation_preferences)
        for checkbox in self.operation_ramp_checks.values():
            checkbox.toggled.connect(self._save_operation_preferences)
        for button in getattr(self, "cut_ramp_type_buttons", []):
            button.toggled.connect(self._save_operation_preferences)

    def _geometry_bounds(self, geometry):
        xs = []
        ys = []
        for contour in geometry.get("contours", []) or []:
            for point in contour:
                xs.append(float(point[0]))
                ys.append(float(point[1]))
        for hole in geometry.get("holes", []) or []:
            radius = abs(float(hole.get("diameter_mm", 0.0) or 0.0)) * 0.5
            center_x = float(hole.get("x", 0.0))
            center_y = float(hole.get("y", 0.0))
            xs.extend([center_x - radius, center_x + radius])
            ys.extend([center_y - radius, center_y + radius])
        if not xs or not ys:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    def _anchor_point_from_bounds(self, bounds, anchor):
        min_x, min_y, max_x, max_y = bounds
        if anchor.endswith("_right"):
            offset_x = max_x
        elif anchor.endswith("_center") or anchor == "center":
            offset_x = (min_x + max_x) * 0.5
        else:
            offset_x = min_x

        if anchor.startswith("top_"):
            offset_y = max_y
        elif anchor.startswith("middle_") or anchor == "center":
            offset_y = (min_y + max_y) * 0.5
        else:
            offset_y = min_y

        return float(offset_x), float(offset_y)

    def _datum_reference_bounds(self, settings, geometry=None):
        return self._work_area_bounds_for_preview(settings)

    def _xy_origin_offset(self, settings, geometry):
        """Placement XY da peça dentro do retângulo definido em Trabalho.

        O grid do Datum XY escolhe um ponto do retângulo de trabalho/material.
        X/Y do datum são deslocamentos a partir desse ponto. A geometria
        selecionada usa o ponto equivalente do seu próprio retângulo: topo
        com topo, centro com centro e assim por diante. Assim, o contorno
        permanece dentro da chapa quando o datum estiver em uma borda.
        """
        offset_x = float(settings.get("origin_x", 0.0) or 0.0)
        offset_y = float(settings.get("origin_y", 0.0) or 0.0)

        work_bounds = self._work_area_bounds_for_preview(settings)
        if work_bounds is None:
            work_anchor_x, work_anchor_y = 0.0, 0.0
        else:
            work_anchor_x, work_anchor_y = self._anchor_point_from_bounds(
                work_bounds,
                settings.get("origin_anchor", "bottom_left"),
            )

        geometry_bounds = self._geometry_bounds(geometry or {})
        if geometry_bounds is None:
            geometry_anchor_x, geometry_anchor_y = 0.0, 0.0
        else:
            geometry_anchor_x, geometry_anchor_y = self._anchor_point_from_bounds(
                geometry_bounds,
                settings.get("origin_anchor", "bottom_left"),
            )

        target_x = work_anchor_x + offset_x
        target_y = work_anchor_y + offset_y
        return target_x - geometry_anchor_x, target_y - geometry_anchor_y

    def _z_origin_offset(self, settings):
        if settings.get("z_zero_mode") == "machine_bed":
            return float(settings.get("material_thickness", 0.0))
        return 0.0

    def _machine_z_value(self, settings, top_relative_z):
        return float(top_relative_z) + self._z_origin_offset(settings)

    def _machine_safe_height(self, settings):
        return self._machine_z_value(settings, settings["safe_height"])

    def _machine_retract_height(self, settings):
        return self._machine_z_value(settings, settings["retract_height"])

    def _transform_moves_to_machine(self, settings, moves):
        placement_x, placement_y = settings.get("_xy_origin_offset", (0.0, 0.0))
        z_offset = self._z_origin_offset(settings)
        transformed = []
        for move in moves:
            converted = dict(move)
            if converted.get("x") is not None:
                converted["x"] = float(converted["x"]) + float(placement_x)
            if converted.get("y") is not None:
                converted["y"] = float(converted["y"]) + float(placement_y)
            if converted.get("z") is not None:
                converted["z"] = float(converted["z"]) + z_offset
            transformed.append(converted)
        return transformed

    def _transform_stages_to_machine(self, settings, stages):
        return {
            stage_name: self._transform_moves_to_machine(settings, moves)
            for stage_name, moves in stages.items()
        }

    def _configured_machine_start_xy(self, settings):
        """Retorna o início/retorno no zero da área de Trabalho.

        ``start_x/start_y`` são deslocamentos relativos ao job. O ponto base
        vem do anchor e dos offsets da área de Trabalho; não é o (0, 0) global
        da cena nem o placement temporário da geometria/material.
        """
        bounds = self._work_area_bounds_for_preview(settings)
        anchor = settings.get(
            "job_origin_anchor",
            settings.get("origin_anchor", "bottom_left"),
        )
        if bounds is None:
            base_x = float(settings.get("job_origin_x", 0.0) or 0.0)
            base_y = float(settings.get("job_origin_y", 0.0) or 0.0)
        else:
            base_x, base_y = self._anchor_point_from_bounds(bounds, anchor)
        x_direction = -1.0 if anchor.endswith("_right") else 1.0
        y_direction = -1.0 if anchor.startswith("top_") else 1.0
        return (
            base_x + float(settings.get("start_x", 0.0) or 0.0) * x_direction,
            base_y + float(settings.get("start_y", 0.0) or 0.0) * y_direction,
        )

    def _selected_3d_source(self):
        source = selected_surface_object(FreeCADGui.Selection.getSelectionEx())
        if source is not None:
            return source
        # A prévia remove o realce visual da seleção para não obrigar o
        # FreeCAD a destacar centenas de milhares de facetas. O nome guardado
        # mantém o mesmo modelo como fonte ao clicar Aplicar em seguida.
        doc = FreeCAD.ActiveDocument
        source_name = str(getattr(self, "_preview_3d_source_name", "") or "")
        return doc.getObject(source_name) if doc is not None and source_name else None

    def _boundary_polygon_for_3d(self, settings, source_object, placement):
        mode = settings.get("boundary_mode", "model")
        if mode == "model":
            return None
        if mode == "selected_level":
            groups = [
                parent
                for parent in list(getattr(source_object, "InList", []) or [])
                if list(getattr(parent, "Group", []) or [])
            ]
            if not groups:
                raise ValueError(
                    "O modelo selecionado não pertence a um grupo/nível. "
                    "Escolha Limite do modelo ou agrupe os componentes na árvore."
                )
            group = groups[0]
            boxes = []
            for child in list(getattr(group, "Group", []) or []):
                box = getattr(child, "BoundBox", None)
                if box is None:
                    shape = getattr(child, "Shape", None)
                    mesh = getattr(child, "Mesh", None)
                    box = getattr(shape, "BoundBox", None) or getattr(mesh, "BoundBox", None)
                if box is not None and float(getattr(box, "XLength", 0.0) or 0.0) > 1e-9:
                    boxes.append(box)
            if not boxes:
                raise ValueError("O grupo/nível selecionado não possui componentes 3D.")
            shift_x, shift_y = placement
            min_x = min(float(box.XMin) for box in boxes) - shift_x
            min_y = min(float(box.YMin) for box in boxes) - shift_y
            max_x = max(float(box.XMax) for box in boxes) - shift_x
            max_y = max(float(box.YMax) for box in boxes) - shift_y
            settings["_boundary_level_object"] = str(getattr(group, "Name", ""))
            return (
                (min_x, min_y), (max_x, min_y),
                (max_x, max_y), (min_x, max_y),
            )
        if mode == "material":
            bounds = self._work_area_bounds_for_preview(settings)
            if bounds is None:
                raise ValueError(
                    "Configure a área em Trabalho antes de usar o limite do material."
                )
            min_x, min_y, max_x, max_y = bounds
            shift_x, shift_y = placement
            return (
                (min_x - shift_x, min_y - shift_y),
                (max_x - shift_x, min_y - shift_y),
                (max_x - shift_x, max_y - shift_y),
                (min_x - shift_x, max_y - shift_y),
            )
        contours = []
        if self._use_vector_editor_for_cam:
            contours = list(self._vector_editor_geometry_for_cam().get("contours", []))
            if self._vector_editor_document is not None:
                settings["_boundary_document_uuid"] = str(
                    self._vector_editor_document.document_uuid
                )
                settings["_boundary_revision"] = int(
                    self._vector_editor_document.revision
                )
        else:
            for record in FreeCADGui.Selection.getSelectionEx():
                obj = getattr(record, "Object", None)
                if obj is None or obj is source_object:
                    continue
                try:
                    contours.extend(_extract_geometry_from_object(obj).get("contours", []))
                except Exception:
                    continue
        if not contours:
            raise ValueError(
                "Para 'Vetores selecionados', selecione também um contorno fechado "
                "ou ative a geometria do Editor 2D para CAM."
            )
        return tuple(max(contours, key=lambda contour: abs(self._polygon_area(contour))))

    def _polygon_area(self, points):
        return 0.5 * sum(
            points[index][0] * points[(index + 1) % len(points)][1]
            - points[(index + 1) % len(points)][0] * points[index][1]
            for index in range(len(points))
        )

    def _build_3d_stage_from_selection(self, settings):
        source = self._selected_3d_source()
        sampling = max(
            0.05,
            min(
                float(DEFAULT_PRESETS.get("surface_sampling", 0.5)),
                float(settings["tool_diameter"])
                * float(
                    settings.get(
                        "rough3d_stepover_percent",
                        settings.get("finish3d_stepover_percent", 10.0),
                    )
                )
                / 200.0,
            ),
        )
        field = height_field_from_relief_object(source, sampling_mm=sampling)
        if field is None:
            mesh_data = mesh_data_from_object(source, linear_deflection=sampling * 0.5)
            field = height_field_from_mesh(mesh_data, sampling_mm=sampling)
        model_height = float(field.source_max_z - field.source_min_z)
        material_thickness = float(settings["material_thickness"])
        if settings.get("model_position_mode") == "gap_below":
            gap_above = material_thickness - model_height - float(settings.get("model_gap_below", 0.0))
        else:
            gap_above = float(settings.get("model_gap_above", 0.0))
        if gap_above < -1e-6 or gap_above + model_height > material_thickness + 1e-6:
            raise ValueError(
                "O modelo 3D (%.2f mm) não cabe no material de %.2f mm com as "
                "folgas configuradas. Ajuste a posição na aba Material."
                % (model_height, material_thickness)
            )
        gap_above = max(0.0, gap_above)
        geometry = {
            "contours": [[
                (field.origin_x, field.origin_y),
                (field.max_x, field.origin_y),
                (field.max_x, field.max_y),
                (field.origin_x, field.max_y),
            ]],
            "holes": [],
        }
        # O height-field ja esta em coordenadas absolutas do documento. Assim
        # como no Editor 2D, o datum da area define o inicio/retorno da maquina,
        # nao uma segunda translacao da peca ou do percurso.
        settings["_xy_origin_offset"] = (0.0, 0.0)
        settings["_3d_coordinates_absolute"] = True
        settings["_geometry_source"] = "mesh_3d"
        settings["_mesh_source_object"] = str(getattr(source, "Name", ""))
        settings["_mesh_source_hash"] = str(field.source_hash)
        settings["_mesh_model_height"] = model_height
        settings["_mesh_source_min_z"] = float(field.source_min_z)
        settings["_mesh_source_max_z"] = float(field.source_max_z)
        settings["_mesh_sampling"] = sampling
        settings["_mesh_deflection"] = sampling * 0.5
        boundary = self._boundary_polygon_for_3d(
            settings, source, settings["_xy_origin_offset"]
        )
        offset_key = (
            "rough3d_boundary_offset"
            if settings["operation_mode"] == "rough3d"
            else "finish3d_boundary_offset"
        )
        model_boundary_padding = 0.0
        if boundary is None:
            model_boundary_padding = max(
                0.0, float(settings.get(offset_key, 0.0) or 0.0)
            )
        if boundary and settings.get("boundary_mode") in {
            "model", "material", "selected_vectors", "selected_level"
        }:
            xs = [point[0] for point in boundary]
            ys = [point[1] for point in boundary]
            padding = max(0.0, float(settings.get(offset_key, 0.0) or 0.0))
            if padding > 0.0 or settings.get("boundary_mode") in {
                "material", "selected_vectors", "selected_level"
            }:
                field = extend_height_field(
                    field,
                    (
                        min(xs) - padding,
                        min(ys) - padding,
                        max(xs) + padding,
                        max(ys) + padding,
                    ),
                    outside_height=field.source_min_z,
                )
        elif model_boundary_padding > 0.0:
            field = expand_height_field_support(
                field,
                model_boundary_padding,
                outside_height=field.source_min_z,
            )
        mode = settings["operation_mode"]
        if hasattr(self, mode + "_source_label"):
            getattr(self, mode + "_source_label").setText(
                "Fonte: %s · %.1f × %.1f × %.1f mm · amostra %.2f mm"
                % (
                    getattr(source, "Label", getattr(source, "Name", "Modelo 3D")),
                    field.max_x - field.origin_x,
                    field.max_y - field.origin_y,
                    model_height,
                    sampling,
                )
            )
        if mode == "rough3d":
            moves = build_3d_roughing_moves(
                field,
                RoughingOptions(
                    tool_diameter=settings["tool_diameter"],
                    stepdown=settings["stepdown"],
                    stepover_percent=settings["rough3d_stepover_percent"],
                    allowance=settings["rough3d_allowance"],
                    safe_height=settings["safe_height"],
                    gap_above=gap_above,
                    strategy=settings["rough3d_strategy"],
                    raster_axis=settings["rough3d_axis"],
                    reverse=settings["rough3d_reverse"],
                    ramp=settings.get("use_ramp", False),
                    ramp_length=settings.get("ramp_length", 0.0),
                    boundary_offset=settings["rough3d_boundary_offset"],
                    boundary=boundary,
                    profile=settings["rough3d_profile"],
                    order=settings["rough3d_order"],
                    pass_depths=(
                        tuple(settings.get("pass_depths", ())) or None
                    ),
                ),
            )
        else:
            if settings.get("tool_type") != "ball_nose":
                FreeCAD.Console.PrintWarning(
                    "WoodCAM 3D: acabamento sem fresa esférica; a geometria da "
                    "ferramenta selecionada será respeitada.\n"
                )
            moves = build_3d_finishing_moves(
                field,
                FinishingOptions(
                    tool_diameter=settings["tool_diameter"],
                    stepover_percent=settings["finish3d_stepover_percent"],
                    safe_height=settings["safe_height"],
                    gap_above=gap_above,
                    strategy=settings["finish3d_strategy"],
                    raster_angle=settings["finish3d_raster_angle"],
                    reverse=settings["finish3d_reverse"],
                    boundary_offset=settings["finish3d_boundary_offset"],
                    boundary=boundary,
                    tool_type=settings.get("tool_type", "ball_nose"),
                ),
            )
        # Os geradores de relevo trabalham no sistema local do height-field e
        # historicamente começavam no primeiro ponto da malha, ignorando o
        # ponto inicial configurado na aba Trabalho. Envolvemos a trajetória
        # aqui, antes da transformação para coordenadas de máquina, exatamente
        # como já é feito nas operações 2D.
        machine_start_x, machine_start_y = self._configured_machine_start_xy(settings)
        placement_x, placement_y = settings.get("_xy_origin_offset", (0.0, 0.0))
        start_xy = (
            machine_start_x - float(placement_x or 0.0),
            machine_start_y - float(placement_y or 0.0),
        )
        moves = _with_configured_start(
            moves,
            start_xy,
            float(settings.get("safe_height", 0.0) or 0.0),
            bool(settings.get("return_to_start", False)),
        )
        return {mode: moves}

    def _build_stage_moves_from_selection(self, settings):
        operation_mode = settings["operation_mode"]
        if operation_mode in {"rough3d", "finish3d"}:
            return self._build_3d_stage_from_selection(settings)
        geometry = self._active_geometry()
        settings["_geometry_source"] = (
            "editor_2d" if self._use_vector_editor_for_cam else "freecad_selection"
        )
        # geometry_reader e os adapters do Editor/PanelNest entregam XY de
        # documento. Reancorar apenas um Sketch/Part comum pelo datum movia o
        # percurso para o zero global. A regra agora é única para toda fonte:
        # geometria absoluta; datum somente para início e retorno.
        settings["_geometry_coordinates_absolute"] = True
        settings["_editor_coordinates_absolute"] = True
        if self._use_vector_editor_for_cam:
            if self._vector_editor_document is not None:
                settings["_geometry_revision"] = int(self._vector_editor_document.revision)
                settings["_geometry_document_uuid"] = str(
                    self._vector_editor_document.document_uuid
                )
            # O VectorDocument ja armazena seus vetores no mesmo sistema
            # cartesiano da area tracejada. Reancorar os bounds da geometria
            # no datum da aba Material aplicava uma segunda translacao: o
            # contorno CAM aparecia longe do vetor que lhe deu origem.
            #
            # Persistimos o snapshot da area junto da operacao para que
            # percurso, retorno e G-code usem exatamente a mesma referencia,
            # mesmo se o usuario editar a area de Trabalho depois.
            work_area = getattr(self._vector_editor_document, "work_area", None)
            if work_area is not None:
                settings["_work_area_bounds"] = [
                    float(work_area.min_x),
                    float(work_area.min_y),
                    float(work_area.max_x),
                    float(work_area.max_y),
                ]
        selected_contours = geometry["contours"]
        holes = geometry["holes"]
        outer_contours, nested_inner_contours = split_nested_contours(
            selected_contours
        )
        known_hole_contours = [
            hole["points"]
            for hole in holes
            if hole.get("points")
        ]
        inner_contours = nested_inner_contours
        all_inner_contours = nested_inner_contours + known_hole_contours
        settings["_xy_origin_offset"] = (0.0, 0.0)
        placement_x, placement_y = settings["_xy_origin_offset"]
        machine_start_x, machine_start_y = self._configured_machine_start_xy(settings)
        start_xy = (
            machine_start_x - float(placement_x or 0.0),
            machine_start_y - float(placement_y or 0.0),
        )

        if operation_mode == "holes":
            if not holes:
                raise ValueError("Nenhum furo circular foi encontrado na seleção.")
            stages = build_machining_stages(
                [],
                holes,
                settings["final_depth"],
                settings["stepdown"],
                settings["ramp_length"],
                settings["safe_height"],
                tool_diameter=settings["tool_diameter"],
                material_thickness=settings["material_thickness"],
                drill_holes=True,
                use_helical_drilling=settings["use_helical_drilling"],
                helix_pitch=settings["helix_pitch"],
                helix_stepover_ratio=settings["helix_stepover_percent"] / 100.0,
                use_model_depths=settings["use_model_hole_depths"],
                hole_depth_override=settings["cut_depth"],
                peck_enabled=settings["peck_enabled"],
                peck_step=settings["peck_step"],
                retract_mode=settings["peck_retract_mode"],
                retract_clearance=settings["peck_retract_clearance"],
                dwell_seconds=(
                    settings["dwell_seconds"]
                    if settings["dwell_enabled"]
                    else 0.0
                ),
                preserve_hole_order=settings["preserve_hole_order"],
                roughing_enabled=False,
                cut_enabled=False,
                start_xy=start_xy,
                return_to_start=settings["return_to_start"],
                start_depth=settings["start_depth"],
            )
            return stages

        if operation_mode == "cut":
            explicit_hole_contour_cut = not outer_contours and known_hole_contours
            if explicit_hole_contour_cut:
                # Selecionar o desenho inteiro: círculos entram como internos/furos.
                # Selecionar somente um círculo e escolher Corte: respeita a opção do
                # usuário (fora/dentro/sobre) e trata o círculo como um contorno comum.
                outer_contours = list(known_hole_contours)
                inner_contours = []
                all_inner_contours = []
                holes = []
            if not outer_contours and not all_inner_contours:
                raise ValueError("Nenhum contorno fechado foi encontrado para corte.")
            if outer_contours:
                outer_settings = dict(settings)
                outer_settings["cut_side"] = settings["outer_cut_side"]
                validate_selected_contours(outer_contours, outer_settings)
            if all_inner_contours:
                inner_settings = dict(settings)
                inner_settings["cut_side"] = CUT_SIDE_INSIDE
                validate_selected_contours(all_inner_contours, inner_settings)

            cut_moves = []
            dimensional_allowance = float(
                settings.get("cut_allowance_offset", 0.0) or 0.0
            )
            effective_tool_diameter = (
                float(settings["tool_diameter"]) + dimensional_allowance * 2.0
            )
            if effective_tool_diameter <= 1e-6:
                raise ValueError(
                    "A compensação dimensional deixa o diâmetro efetivo da "
                    "fresa inválido. Reduza o valor negativo."
                )
            last_pass_allowance = max(
                0.0,
                float(settings.get("cut_last_pass_allowance", 0.0) or 0.0),
            )
            if settings.get("cut_separate_last_pass") and last_pass_allowance > 1e-6:
                rough_moves = build_contour_cut_stage(
                    outer_contours,
                    inner_contours,
                    settings["final_depth"],
                    settings["stepdown"],
                    settings["ramp_length"],
                    settings["safe_height"],
                    tool_diameter=effective_tool_diameter + last_pass_allowance * 2.0,
                    outer_cut_side=settings["outer_cut_side"],
                    material_thickness=settings["material_thickness"],
                    smart_entry=settings["smart_entry"],
                    start_xy=start_xy,
                    return_to_start=False,
                    inner_holes=[],
                    start_depth=settings["start_depth"],
                    corner_slowdown_enabled=settings["corner_slowdown_enabled"],
                    corner_angle_threshold=settings["corner_angle_threshold"],
                    corner_feed_percent=settings["corner_feed_percent"],
                    corner_slowdown_distance=settings["corner_slowdown_distance"],
                    climb=settings["cut_climb"],
                    tabs_enabled=False,
                    pass_depths=settings.get("pass_depths"),
                )
                cut_moves.extend(rough_moves)

            final_cut_moves = build_contour_cut_stage(
                outer_contours,
                inner_contours,
                settings["final_depth"],
                settings["stepdown"],
                settings["ramp_length"],
                settings["safe_height"],
                tool_diameter=effective_tool_diameter,
                outer_cut_side=settings["outer_cut_side"],
                material_thickness=settings["material_thickness"],
                smart_entry=settings["smart_entry"],
                start_xy=start_xy,
                return_to_start=settings["return_to_start"],
                inner_holes=holes,
                start_depth=settings["start_depth"],
                corner_slowdown_enabled=settings["corner_slowdown_enabled"],
                corner_angle_threshold=settings["corner_angle_threshold"],
                corner_feed_percent=settings["corner_feed_percent"],
                corner_slowdown_distance=settings["corner_slowdown_distance"],
                climb=settings["cut_climb"],
                tabs_enabled=settings.get("cut_tabs_enabled", False),
                tab_length=settings.get("tab_length", 12.0),
                tab_thickness=settings.get("tab_thickness", 3.0),
                tab_count=settings.get("tab_count", 4),
                tab_positions=settings.get("tab_positions", ()),
                tabs_3d=settings.get("tabs_3d", False),
                ramp_type=settings.get("ramp_type", "smooth"),
                pass_depths=settings.get("pass_depths"),
            )
            cut_moves.extend(final_cut_moves)
            return {"cut": cut_moves}

        if operation_mode != "pocket":
            raise ValueError("A operação selecionada é inválida.")
        if not outer_contours:
            raise ValueError("Nenhuma área fechada foi encontrada para o preenchimento.")
        pocket_validation = dict(settings)
        pocket_validation["cut_side"] = CUT_SIDE_ON_LINE
        validate_selected_contours(outer_contours, pocket_validation)
        pocket_moves = build_pocket_stage(
            outer_contours,
            all_inner_contours,
            settings["final_depth"],
            settings["stepdown"],
            settings["ramp_length"],
            settings["safe_height"],
            tool_diameter=settings["tool_diameter"],
            stepover_percent=settings["pocket_stepover_percent"],
            strategy=settings["pocket_strategy"],
            allowance=settings["pocket_allowance"],
            raster_angle=settings["pocket_raster_angle"],
            climb=settings["pocket_climb"],
            profile_pass=settings["pocket_profile_pass"],
            material_thickness=settings["material_thickness"],
            start_xy=start_xy,
            return_to_start=settings["return_to_start"],
            start_depth=settings["start_depth"],
        )
        if not pocket_moves:
            raise ValueError("O preenchimento não produziu nenhuma trajetória.")
        return {"pocket": pocket_moves}

    def _build_moves_from_selection(self, settings):
        stages = self._build_stage_moves_from_selection(settings)
        machine_stages = self._transform_stages_to_machine(settings, stages)
        return [
            move
            for stage_moves in machine_stages.values()
            for move in stage_moves
        ]

    def _available_output_path(self, path):
        path = Path(path)
        if not path.exists():
            return path
        for index in range(2, 10000):
            candidate = path.with_name(f"{path.stem}_{index:02d}{path.suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError("Não foi possível escolher um nome livre para o G-code.")

    def _stage_output_path(self, base_path, stage_name):
        base_path = Path(base_path)
        suffix = base_path.suffix or ".nc"
        return base_path.with_name(
            f"{base_path.stem}_{STAGE_FILE_SUFFIXES[stage_name]}{suffix}"
        )

    def _safe_file_slug(self, text):
        cleaned = []
        for character in str(text or "").strip().lower():
            if character.isalnum():
                cleaned.append(character)
            elif character in {" ", "-", "_"}:
                cleaned.append("_")
        slug = "".join(cleaned).strip("_")
        while "__" in slug:
            slug = slug.replace("__", "_")
        return slug or "percurso"

    def _gcode_lines_for_settings_moves(self, settings, moves, job_name=None):
        home_x, home_y = self._configured_machine_start_xy(settings)
        return build_gcode(
            settings["rpm"],
            settings["feed_z"],
            settings["feed_xy"],
            settings["rapid_feed"],
            self._machine_safe_height(settings),
            self._machine_retract_height(settings),
            moves,
            ramp_feed=settings.get(
                "ramp_feed",
                min(settings["feed_xy"] * 0.6, settings["feed_xy"]),
            ),
            job_name=job_name or settings.get("operation_name", "WoodCAM 2D"),
            home_x=home_x,
            home_y=home_y,
            z_zero_mode=settings.get("z_zero_mode"),
            material_thickness=settings.get("material_thickness"),
            tool_name=settings.get("tool_name"),
            tool_number=settings.get("tool_number"),
        )

    def _combine_gcode_sections(self, sections, tool_changes=None):
        combined = []
        header_count = len(HEADER_TEMPLATE)
        for index, lines in enumerate(sections):
            part = list(lines)
            if index > 0 and len(part) >= header_count:
                part = part[header_count:]
            if index < len(sections) - 1 and part and part[-1] == "M30":
                part = part[:-1]
            if combined:
                combined.append("")
                combined.append("(--- Proximo percurso ---)")
                if tool_changes and index < len(tool_changes) and tool_changes[index]:
                    combined.append("(Troque a ferramenta e confirme antes de continuar)")
                    combined.append("M0")
            combined.extend(part)
        return combined

    def _export_mode_is_single_file(self):
        return (
            hasattr(self, "export_mode_combo")
            and self.export_mode_combo.currentIndex() == 1
        )

    def _save_applied_gcode_entries(self, entries, base_output_path):
        if not entries:
            return []
        stale_labels = [
            label
            for settings, _moves, label in entries
            if settings.get("_geometry_stale")
        ]
        if stale_labels:
            raise ValueError(
                "A geometria do Editor 2D mudou depois destas operações: %s. "
                "Atualize/aplique novamente antes de gerar G-code."
                % ", ".join(stale_labels)
            )
        base_path = Path(base_output_path)
        if self._export_mode_is_single_file():
            output_path = self._available_output_path(base_path)
            sections = [
                self._gcode_lines_for_settings_moves(settings, moves, label)
                for settings, moves, label in entries
            ]
            tool_keys = [
                (
                    int(settings.get("tool_number", 0) or 0),
                    str(settings.get("tool_name", "") or ""),
                )
                for settings, _moves, _label in entries
            ]
            tool_changes = [
                False if index == 0 else tool_keys[index] != tool_keys[index - 1]
                for index in range(len(tool_keys))
            ]
            save_gcode_file(
                str(output_path),
                self._combine_gcode_sections(sections, tool_changes),
            )
            return [str(output_path)]

        saved_paths = []
        suffix = base_path.suffix or ".nc"
        for settings, moves, label in entries:
            slug = self._safe_file_slug(label)
            output_path = self._available_output_path(
                base_path.with_name(f"{base_path.stem}_{slug}{suffix}")
            )
            lines = self._gcode_lines_for_settings_moves(settings, moves, label)
            save_gcode_file(str(output_path), lines)
            saved_paths.append(str(output_path))
        return saved_paths

    def generate_gcode(self):
        try:
            applied_entries = self._selected_applied_entries()
            if applied_entries:
                output_path = self.output_path.text().strip()
                if not output_path:
                    raise ValueError("Escolha um arquivo de saída válido para o G-code.")
                saved_paths = self._save_applied_gcode_entries(
                    applied_entries,
                    output_path,
                )
                if len(applied_entries) == 1:
                    applied_settings, applied_moves, _label = applied_entries[0]
                    self._show_exact_simulation_toolpath(
                        applied_settings,
                        applied_moves,
                        "G-CODE GERADO = VISTA",
                    )
                QtWidgets.QMessageBox.information(
                    self,
                    "Sucesso",
                    "G-code salvo sem sobrescrever:\n" + "\n".join(saved_paths),
                )
                return

            self._restore_editing_source_if_needed()
            settings = self._collect_settings()
            if not settings["output_path"]:
                raise ValueError("Escolha um arquivo de saída válido para o G-code.")
            validate_settings(settings)
            if not self._confirm_material_pierce(settings):
                return
            self._persist_machine_settings(settings)
            raw_stages = self._build_stage_moves_from_selection(settings)
            stages = self._transform_stages_to_machine(settings, raw_stages)
            if self._export_mode_is_single_file():
                output_path = self._available_output_path(settings["output_path"])
                combined_moves = [
                    move
                    for moves in stages.values()
                    for move in moves
                ]
                lines = self._gcode_lines_for_settings_moves(
                    settings,
                    combined_moves,
                    settings["operation_name"],
                )
                save_gcode_file(str(output_path), lines)
                self._show_exact_simulation_toolpath(
                    settings,
                    combined_moves,
                    "G-CODE GERADO = VISTA",
                )
                QtWidgets.QMessageBox.information(
                    self,
                    "Sucesso",
                    f"G-code salvo sem sobrescrever:\n{output_path}",
                )
                return

            files_to_save = [
                (
                    stage_name,
                    moves,
                    self._available_output_path(
                        self._stage_output_path(
                            settings["output_path"],
                            stage_name,
                        )
                    ),
                )
                for stage_name, moves in stages.items()
            ]

            saved_paths = []
            for stage_name, moves, output_path in files_to_save:
                lines = self._gcode_lines_for_settings_moves(
                    settings,
                    moves,
                    settings["operation_name"],
                )
                save_gcode_file(str(output_path), lines)
                saved_paths.append(str(output_path))

            exported_moves = [
                move
                for _stage_name, moves, _output_path in files_to_save
                for move in moves
            ]
            self._show_exact_simulation_toolpath(
                settings,
                exported_moves,
                "G-CODE GERADO = VISTA",
            )

            QtWidgets.QMessageBox.information(
                self,
                "Sucesso",
                "G-code salvo sem sobrescrever:\n" + "\n".join(saved_paths),
            )
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "Erro", str(error))

    def _update_cam_advisor_summary(self, settings, report=None):
        report = report or analyze_cam_settings(settings)
        if not hasattr(self, "cam_advisor_button"):
            return report
        count = len(report.items)
        self.cam_advisor_button.setText(
            "Assistente CAM" if not count else "Assistente CAM (%d)" % count
        )
        if report.warning_count:
            self.cam_advisor_button.setStyleSheet(
                "color: #92400e; background: #fffbeb; border: 1px solid #f59e0b;"
            )
        elif count:
            self.cam_advisor_button.setStyleSheet(
                "color: #1e3a8a; background: #eff6ff; border: 1px solid #93c5fd;"
            )
        else:
            self.cam_advisor_button.setStyleSheet("")
        self.cam_advisor_button.setToolTip(
            (
                "%d observação(ões), sendo %d alerta(s). Clique para entender. "
                "Nenhum parâmetro é alterado automaticamente."
                % (count, report.warning_count)
            )
            if count
            else (
                "Nenhum alerta conservador nos parâmetros atuais. Clique para conferir; "
                "isso não substitui prévia, simulação e teste na máquina."
            )
        )
        return report

    def show_cam_advisor(self):
        try:
            operation_mode = self._operation_mode_for_index(
                self.operation_tabs.currentIndex()
            )
            if operation_mode is None:
                raise ValueError(
                    "O Assistente CAM analisa a aba visível, não a última operação usada. "
                    "Abra Corte, Furo, Preenchimento, Desbaste 3D ou Acabamento 3D e clique novamente."
                )
            settings = self._collect_settings()
            if settings.get("operation_mode") != operation_mode:
                raise RuntimeError(
                    "A operação visível não corresponde aos parâmetros coletados."
                )
            validate_settings(settings)
            contours = []
            if settings.get("operation_mode") not in {"rough3d", "finish3d"}:
                try:
                    geometry = self._active_geometry()
                    contours.extend(geometry.get("contours", []) or [])
                    contours.extend(
                        hole.get("points")
                        for hole in (geometry.get("holes", []) or [])
                        if hole.get("points")
                    )
                except Exception:
                    # A análise dos parâmetros continua útil mesmo antes de o
                    # operador selecionar a geometria que alimentará o CAM.
                    contours = []
            report = analyze_cam_settings(settings, contours=contours)
            self._update_cam_advisor_summary(settings, report)
            title = "Assistente CAM — análise explicável"
            operation_label = OPERATION_TREE_LABELS.get(
                operation_mode,
                STAGE_LABELS.get(operation_mode, operation_mode),
            )
            tool_name = str(settings.get("tool_name", "") or "Ferramenta não nomeada")
            tool_type = str(settings.get("tool_type", "") or "tipo não informado")
            diameter = float(settings.get("tool_diameter", 0.0) or 0.0)
            report_text = (
                "ANALISANDO AGORA\n"
                "%s | %s | %s | Ø %g mm\n\n%s"
                % (
                    operation_label,
                    tool_name,
                    tool_type,
                    diameter,
                    report.to_plain_text(),
                )
            )
            if report.warning_count:
                QtWidgets.QMessageBox.warning(self, title, report_text)
            else:
                QtWidgets.QMessageBox.information(self, title, report_text)
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "Assistente CAM", str(error))

    def preview_toolpath(self):
        try:
            if self._is_work_setup_tab_active():
                settings = self._collect_work_setup_settings()
                self._validate_work_setup_settings(settings)
                self._persist_machine_settings(settings)
                self._show_work_area_preview(settings)
                return
            self._restore_editing_source_if_needed()
            settings = self._collect_settings()
            validate_settings(settings)
            self._update_cam_advisor_summary(settings)
            if not self._confirm_material_pierce(settings):
                return
            self._persist_machine_settings(settings)
            moves = self._build_moves_from_selection(settings)
            self._show_preview(settings, moves)
            self.last_preview_settings = dict(settings)
            self.last_preview_moves = [dict(move) for move in moves]
            self._set_toolpath_truth(settings, moves, "PRÉVIA EXATA")
            self._update_simulation_time_label()
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "Erro", str(error))

    def apply_current_operation(self):
        try:
            if self._is_work_setup_tab_active():
                settings = self._collect_work_setup_settings()
                self._validate_work_setup_settings(settings)
                self._persist_machine_settings(settings)
                self._clear_existing_preview(FreeCAD.ActiveDocument)
                self._ensure_persistent_work_area(FreeCAD.ActiveDocument, settings)
                return
            settings = self._collect_settings()
            validate_settings(settings)
            self._update_cam_advisor_summary(settings)
            if not self._confirm_material_pierce(settings):
                return
            self._persist_machine_settings(settings)
            editing_operation = self._selected_operation_from_tree()
            if editing_operation is None:
                editing_operation = self._editing_operation
            if editing_operation is not None:
                self._restore_operation_source_selection(editing_operation)
            moves = self._build_moves_from_selection(settings)
            if editing_operation is not None:
                operation = self._update_applied_operation(settings, moves, editing_operation)
            else:
                operation = self._create_applied_operation(settings, moves)
            self.last_applied_settings = dict(settings)
            self.last_applied_moves = [dict(move) for move in moves]
            self.last_preview_settings = None
            self.last_preview_moves = None
            self._clear_existing_preview(FreeCAD.ActiveDocument)
            self._ensure_persistent_work_area(FreeCAD.ActiveDocument, settings)
            if self._uses_lightweight_toolpath_overlay(settings):
                # Corte e percursos 3D gravam somente os movimentos; a vista
                # azul completa é recriada pelo overlay Coin, sem projeção
                # vermelha persistente no documento.
                self._show_exact_simulation_toolpath(
                    settings,
                    moves,
                    "APLICADO = G-CODE",
                )
            else:
                self._set_toolpath_truth(settings, moves, "APLICADO = G-CODE")
            self._refresh_applied_operation_list()
            if editing_operation is not None:
                # A edição é explícita: para editar novamente, selecione o
                # percurso na árvore. Assim uma nova seleção de geometria
                # volta a criar uma operação independente.
                self._editing_operation = None
                self.apply_button.setText("Aplicar")
                self.apply_button.setToolTip(
                    "Cria uma operação persistente e numerada na árvore do documento."
                )
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "Erro", str(error))

    def simulate_toolpath(self):
        try:
            applied = self._selected_applied_simulation()
            if applied is not None:
                settings, moves = applied
                self._show_simulation(moves, settings)
                return
            if self.last_preview_settings is not None and self.last_preview_moves:
                self._show_simulation(
                    self.last_preview_moves,
                    self.last_preview_settings,
                )
                return
            if self.last_applied_settings is not None and self.last_applied_moves:
                self._show_simulation(
                    self.last_applied_moves,
                    self.last_applied_settings,
                )
                return
            self._restore_editing_source_if_needed()
            settings = self._collect_settings()
            validate_settings(settings)
            self._persist_machine_settings(settings)
            moves = self._build_moves_from_selection(settings)
            self._show_simulation(moves, settings)
        except Exception as error:
            QtWidgets.QMessageBox.critical(self, "Erro", str(error))

    def stop_simulation(self):
        self._clear_existing_simulation()

    def _iter_applied_operation_objects(self):
        doc = FreeCAD.ActiveDocument
        if doc is None:
            return []
        root = doc.getObject("WoodCAM2D_Operations")
        if root is None:
            return []
        items = []
        for obj in list(getattr(root, "Group", []) or []):
            if not hasattr(obj, "SettingsJSON") or not (
                hasattr(obj, "MovesCompressedBase64") or hasattr(obj, "MovesJSON")
            ):
                continue
            sequence = int(getattr(obj, "WoodCAMSequence", 0) or 0)
            mode = str(getattr(obj, "WoodCAMOperationType", ""))
            items.append((mode, sequence, obj))
        mode_order = {"rough3d": 0, "finish3d": 1, "holes": 2, "cut": 3, "pocket": 4}
        items.sort(key=lambda item: (mode_order.get(item[0], 99), item[1], item[2].Label))
        return [obj for _mode, _sequence, obj in items]

    def _operation_geometry_is_stale(self, operation, settings=None):
        """Compara a revisão gravada sem regenerar uma operação silenciosamente."""
        if settings is None:
            try:
                settings = json.loads(str(operation.SettingsJSON))
            except Exception:
                return False
        if settings.get("_geometry_source") == "mesh_3d":
            doc = FreeCAD.ActiveDocument
            source = doc.getObject(str(settings.get("_mesh_source_object", ""))) if doc else None
            if source is None:
                return True
            try:
                current = relief_source_fingerprint(source)
                if current is None:
                    current = mesh_data_from_object(
                        source,
                        linear_deflection=float(settings.get("_mesh_deflection", 0.08)),
                    ).fingerprint()
                if current != str(settings.get("_mesh_source_hash", "")):
                    return True
                boundary_uuid = str(settings.get("_boundary_document_uuid", "") or "")
                if boundary_uuid:
                    document = getattr(self, "_vector_editor_document", None)
                    if document is None or boundary_uuid != str(document.document_uuid):
                        return True
                    return int(settings.get("_boundary_revision", -1)) != int(document.revision)
                return False
            except Exception:
                return True
        if settings.get("_geometry_source") != "editor_2d":
            return False
        document = getattr(self, "_vector_editor_document", None)
        if document is None:
            return True
        source_uuid = str(settings.get("_geometry_document_uuid", "") or "")
        if source_uuid and source_uuid != str(document.document_uuid):
            return True
        try:
            return int(settings.get("_geometry_revision", -1)) != int(document.revision)
        except Exception:
            return True

    def _refresh_applied_operation_list(self):
        if not hasattr(self, "applied_toolpath_list"):
            return
        self._hide_legacy_persisted_lightweight_paths(FreeCAD.ActiveDocument)
        selected_names = {
            item.data(QtCore.Qt.UserRole)
            for item in self.applied_toolpath_list.selectedItems()
        }
        self.applied_toolpath_list.blockSignals(True)
        self.applied_toolpath_list.clear()
        for obj in self._iter_applied_operation_objects():
            mode = str(getattr(obj, "WoodCAMOperationType", ""))
            label = getattr(obj, "Label", obj.Name)
            move_count = int(getattr(obj, "MoveCount", 0) or 0)
            operation_name = str(getattr(obj, "OperationName", "") or "")
            text = label if not operation_name or operation_name == label else f"{label} — {operation_name}"
            if move_count:
                text = f"{text} ({move_count} movimentos)"
            if self._operation_geometry_is_stale(obj):
                text = "⚠ DESATUALIZADA — " + text
            item = QtWidgets.QListWidgetItem(text)
            icon = self._load_diagram_icon(
                {
                    "holes": "holes", "cut": "cut", "pocket": "pocket",
                    "rough3d": "rough3d", "finish3d": "finish3d",
                }.get(mode, "simulation")
            )
            if icon is not None and not icon.isNull():
                item.setIcon(icon)
            item.setData(QtCore.Qt.UserRole, obj.Name)
            self.applied_toolpath_list.addItem(item)
            if obj.Name in selected_names:
                item.setSelected(True)
        if self.applied_toolpath_list.count() == 0:
            item = QtWidgets.QListWidgetItem("Nenhum percurso aplicado ainda")
            item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEnabled)
            self.applied_toolpath_list.addItem(item)
        self.applied_toolpath_list.blockSignals(False)
        self._update_simulation_time_label()

    def _entry_from_applied_object(self, obj):
        if obj is None:
            return None
        try:
            settings = json.loads(str(obj.SettingsJSON))
            moves = decode_moves(
                getattr(obj, "MovesCompressedBase64", ""),
                getattr(obj, "MovesJSON", ""),
            )
        except Exception:
            return None
        if not moves:
            return None
        settings["_geometry_stale"] = self._operation_geometry_is_stale(
            obj, settings
        )
        return (settings, moves, getattr(obj, "Label", obj.Name))

    def _all_applied_entries(self):
        entries = []
        for obj in self._iter_applied_operation_objects():
            entry = self._entry_from_applied_object(obj)
            if entry is not None:
                entries.append(entry)
        return entries

    def _selected_applied_entries(self):
        if not hasattr(self, "applied_toolpath_list"):
            return []
        current_index = self.operation_tabs.currentIndex()
        tab_title = self._tab_title(current_index) if current_index >= 0 else ""
        if tab_title != ACTION_TAB_TITLE:
            return []
        selected_items = [
            item
            for item in self.applied_toolpath_list.selectedItems()
            if item.data(QtCore.Qt.UserRole)
        ]
        if not selected_items:
            return []
        doc = FreeCAD.ActiveDocument
        if doc is None:
            return []
        entries = []
        for item in selected_items:
            obj = doc.getObject(item.data(QtCore.Qt.UserRole))
            entry = self._entry_from_applied_object(obj)
            if entry is not None:
                entries.append(entry)
        return entries

    def _selected_applied_simulation(self):
        entries = self._selected_applied_entries()
        if not entries:
            return None
        combined_moves = []
        first_settings = None
        names = []
        for settings, moves, label in entries:
            if first_settings is None:
                first_settings = settings
            combined_moves.extend(moves)
            names.append(label)
        if first_settings is None or not combined_moves:
            return None
        first_settings = dict(first_settings)
        if len(names) > 1:
            first_settings["operation_name"] = " + ".join(names)
        return first_settings, combined_moves

    def _toolpath_truth_text(self, settings, moves, prefix):
        mode = str(settings.get("operation_mode", "") or "")
        label = OPERATION_TREE_LABELS.get(mode, STAGE_LABELS.get(mode, mode or "Percurso"))
        tool_name = str(settings.get("tool_name", "") or "Ferramenta")
        diameter = float(settings.get("tool_diameter", 0.0) or 0.0)
        parts = [
            str(prefix),
            str(label),
            f"{tool_name} · Ø {diameter:g} mm",
        ]
        if mode == "finish3d":
            percent = float(settings.get("finish3d_stepover_percent", 0.0) or 0.0)
            parts.append(
                f"stepover {percent:g}% = {diameter * percent / 100.0:.3f} mm"
            )
        elif mode == "rough3d":
            percent = float(settings.get("rough3d_stepover_percent", 0.0) or 0.0)
            parts.append(
                f"stepover {percent:g}% = {diameter * percent / 100.0:.3f} mm"
            )
        parts.append(f"{len(moves):,} movimentos".replace(",", "."))
        return " | ".join(parts)

    def _set_toolpath_truth(self, settings, moves, prefix):
        if not hasattr(self, "toolpath_truth_label"):
            return
        self.toolpath_truth_label.setText(
            self._toolpath_truth_text(settings, moves, prefix)
        )
        self.toolpath_truth_label.setStyleSheet(
            "color: #14532d; background: #f0fdf4; border: 1px solid #86efac; "
            "border-radius: 4px; padding: 5px 8px; font-weight: bold;"
        )
        self._toolpath_view_valid = True

    def _operation_settings_and_moves_for_editor_preview(self, operation_mode):
        """Return exact 2D settings/moves without changing persistent CAM state.

        The returned list is deliberately the same list that drives the
        existing 3D/Coin plan preview and the G-code writer.  When the Editor
        2D is populated, it is temporarily selected as CAM source only while
        building the list; the user's source toggle is restored immediately.
        """
        operation_mode = str(operation_mode or "")
        if operation_mode not in {"cut", "holes", "pocket"}:
            raise ValueError("Escolha Corte, Furos ou Rebaixo para a vista 2D.")
        selected_entries = [
            entry
            for entry in self._selected_applied_entries()
            if str(entry[0].get("operation_mode", "")) == operation_mode
        ]
        if len(selected_entries) == 1:
            settings, moves, _label = selected_entries[0]
        elif len(selected_entries) > 1:
            raise ValueError(
                "Selecione somente uma operação de %s."
                % OPERATION_TREE_LABELS[operation_mode]
            )
        else:
            self._restore_editing_source_if_needed()
            moves = None
            settings = self._collect_settings()
            if str(settings.get("operation_mode", "")) != operation_mode:
                # The button is available from Editor 2D/Simulação as well as
                # from every operation tab.  Prefer the latest persisted
                # operation, exactly as Aspire does, instead of showing a
                # modal merely because another tab happens to be active.
                mode_entries = [
                    entry
                    for entry in self._all_applied_entries()
                    if str(entry[0].get("operation_mode", "")) == operation_mode
                ]
                if mode_entries:
                    settings, moves, _label = mode_entries[-1]
                else:
                    previous_index = self.operation_tabs.currentIndex()
                    operation_index = self._operation_tab_index(operation_mode)
                    if operation_index is None:
                        raise ValueError(
                            "Abra a configuração de %s ou aplique uma operação antes de visualizá-la."
                            % OPERATION_TREE_LABELS[operation_mode]
                        )
                    self.operation_tabs.setCurrentIndex(operation_index)
                    try:
                        settings = self._collect_settings()
                    finally:
                        if previous_index >= 0 and previous_index != operation_index:
                            self.operation_tabs.setCurrentIndex(previous_index)
                if moves is None:
                    validate_settings(settings)
                    editor_widget = getattr(self, "vector_editor_widget", None)
                    use_editor = bool(
                        editor_widget is not None
                        and getattr(editor_widget.document, "entities_by_id", {})
                    )
                    previous_editor_source = self._use_vector_editor_for_cam
                    if use_editor:
                        self._use_vector_editor_for_cam = True
                    try:
                        moves = self._build_moves_from_selection(settings)
                    finally:
                        self._use_vector_editor_for_cam = previous_editor_source
            else:
                validate_settings(settings)
                editor_widget = getattr(self, "vector_editor_widget", None)
                use_editor = bool(
                    editor_widget is not None
                    and getattr(editor_widget.document, "entities_by_id", {})
                )
                previous_editor_source = self._use_vector_editor_for_cam
                if use_editor:
                    self._use_vector_editor_for_cam = True
                try:
                    moves = self._build_moves_from_selection(settings)
                finally:
                    self._use_vector_editor_for_cam = previous_editor_source
        if str(settings.get("operation_mode", "")) != operation_mode:
            raise ValueError(
                "A operação obtida não corresponde a %s."
                % OPERATION_TREE_LABELS[operation_mode]
            )
        if not moves:
            raise ValueError(
                "%s não produziu movimentos para visualizar."
                % OPERATION_TREE_LABELS[operation_mode]
            )
        return settings, moves

    def _cut_settings_and_moves_for_preview(self):
        """Compatibility wrapper for the former Cut-only plan view."""
        return self._operation_settings_and_moves_for_editor_preview("cut")

    def _vector_editor_configure_toolpath(self, operation_mode):
        """Open the existing production settings for a 2D CAM operation."""
        operation_mode = str(operation_mode or "")
        if operation_mode not in {"cut", "holes", "pocket"}:
            return
        index = self._operation_tab_index(operation_mode)
        if index is None:
            self._set_vector_editor_status(
                "Configuração de %s não encontrada."
                % OPERATION_TREE_LABELS[operation_mode],
                error=True,
            )
            return
        widget = getattr(self, "vector_editor_widget", None)
        if widget is not None and getattr(widget.document, "entities_by_id", {}):
            self._use_vector_editor_for_cam = True
            widget.use_cam_action.setChecked(True)
        self.operation_tabs.setCurrentIndex(index)
        self._set_vector_editor_status(
            "%s aberto para configurar/criar; a geometria do Editor 2D é a fonte CAM."
            % OPERATION_TREE_LABELS[operation_mode]
        )

    def _vector_editor_show_toolpath(self, operation_mode):
        """Project the real Corte/Furo/Rebaixo moves over the Editor 2D."""
        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        operation_mode = str(operation_mode or "")
        operation_label = OPERATION_TREE_LABELS.get(operation_mode, operation_mode)
        try:
            settings, moves = self._operation_settings_and_moves_for_editor_preview(
                operation_mode
            )
            components = self._toolpath_components(moves, max_display_segments=None)
            if not widget.show_toolpath_preview(components, operation_label):
                raise ValueError("Não há deslocamentos XY neste percurso.")
            self._set_toolpath_truth(
                settings,
                moves,
                "%s 2D NO EDITOR = G-CODE" % str(operation_label).upper(),
            )
            self._set_vector_editor_status(
                "Percurso de %s alinhado ao desenho; cinza = usinagem, "
                "magenta = rápido e setas = sentido. Nada foi alterado."
                % operation_label
            )
        except Exception as error:
            widget.clear_cut_toolpath_preview()
            self._set_vector_editor_status(
                "Não foi possível mostrar o percurso de %s: %s"
                % (operation_label, error),
                error=True,
            )

    def _vector_editor_show_cut_toolpath(self):
        """Compatibility entry point for the former Cut-only signal."""
        self._vector_editor_show_toolpath("cut")

    def _vector_editor_clear_cut_toolpath(self):
        """Hide only the Editor 2D plan overlay, preserving the CAM job."""
        widget = getattr(self, "vector_editor_widget", None)
        if widget is None:
            return
        widget.clear_cut_toolpath_preview()
        self._set_vector_editor_status(
            "Percurso de Corte ocultado no Editor 2D; vetores e operação CAM foram preservados."
        )

    def show_2d_toolpath_preview(self, operation_mode="cut"):
        """Open an exact Corte/Furo/Rebaixo operation in plan view.

        This is only a view of the persisted/current move list: it does not
        recalculate geometry, change the document or generate G-code.
        """

        # When the production Editor 2D is on screen, keep the preview there
        # instead of sending the user back to a separate 3D camera.  The same
        # button is still useful from the other WoodCAM tabs, where the
        # lightweight top-view overlay below remains the fallback.
        editor_widget = getattr(self, "vector_editor_widget", None)
        if editor_widget is not None and editor_widget.isVisible():
            self._vector_editor_show_toolpath(operation_mode)
            return

        try:
            settings, moves = self._operation_settings_and_moves_for_editor_preview(
                operation_mode
            )
            operation_label = OPERATION_TREE_LABELS[operation_mode]
            if not self._show_exact_simulation_toolpath(
                settings,
                moves,
                "%s 2D = G-CODE" % operation_label.upper(),
            ):
                raise ValueError(
                    "Não foi possível montar o percurso de %s." % operation_label
                )
            active_view = FreeCADGui.activeDocument().activeView()
            active_view.viewTop()
            active_view.fitAll()
        except Exception as error:
            QtWidgets.QMessageBox.warning(self, "Percursos 2D", str(error))

    def show_cut_2d_preview(self):
        """Compatibility wrapper for callers of the former Cut-only button."""
        self.show_2d_toolpath_preview("cut")

    def _show_exact_3d_toolpath(self, settings, moves, prefix):
        """Mostra exatamente a lista usada/persistida pelo G-code 3D.

        Não recalcula, não simplifica e não reduz passadas. Os pontos são
        arredondados somente aos mesmos quatro decimais emitidos no arquivo
        ``.nc`` pelo ``gcode_writer``.
        """
        if settings.get("operation_mode") not in {"rough3d", "finish3d"}:
            return False
        return self._show_exact_simulation_toolpath(settings, moves, prefix)

    def _show_exact_simulation_toolpath(self, settings, moves, prefix):
        """Desenha a lista real de movimentos no overlay Coin, sem rastros OCC.

        O acabamento/desbaste 3D já usava esse caminho porque uma animação que
        recria centenas de segmentos por quadro fica cara em percursos densos.
        Corte 2D agora usa a mesma representação leve: o overlay recebe uma
        única vez a lista que alimenta o G-code e somente a fresa é animada.
        Furo conserva o rastro progressivo. Preenchimento usa a mesma prévia
        estática leve do Corte para não reconstruir milhares de segmentos.
        """
        mode = str(settings.get("operation_mode", "") or "")
        if mode not in {"cut", "pocket", "rough3d", "finish3d"}:
            return False
        is_3d = mode in {"rough3d", "finish3d"}
        components = self._toolpath_components(
            moves_for_preview(settings, moves) if is_3d else moves,
            max_display_segments=None,
        )
        if is_3d:
            placement_x, placement_y = settings.get(
                "_xy_origin_offset", (0.0, 0.0)
            )
            components = self._shift_toolpath_components_xy(
                components,
                -float(placement_x or 0.0),
                -float(placement_y or 0.0),
            )
            self._set_3d_toolpath_overlay(settings, components)
        else:
            self._set_lightweight_toolpath_overlay(components)
        self._set_toolpath_truth(settings, moves, prefix)
        return True

    def _clear_toolpath_overlay_only(self):
        overlay = getattr(self, "_toolpath_overlay", None)
        if overlay is not None:
            overlay.clear()
            self._toolpath_overlay = None

    def _invalidate_exact_toolpath_view(self, *_args):
        if self._invalidating_toolpath_view or not self._toolpath_view_valid:
            return
        self._invalidating_toolpath_view = True
        try:
            self._clear_toolpath_overlay_only()
            self.last_preview_settings = None
            self.last_preview_moves = None
            self._toolpath_view_valid = False
            if hasattr(self, "toolpath_truth_label"):
                self.toolpath_truth_label.setText(
                    "Percurso desatualizado: a configuração mudou. "
                    "Clique em Pré-visualizar ou Aplicar antes de confiar na vista."
                )
                self.toolpath_truth_label.setStyleSheet(
                    "color: #92400e; background: #fffbeb; border: 1px solid #f59e0b; "
                    "border-radius: 4px; padding: 5px 8px; font-weight: bold;"
                )
        finally:
            self._invalidating_toolpath_view = False

    def _connect_exact_toolpath_invalidation(self):
        """Uma configuração nova nunca pode permanecer sob uma vista antiga."""
        for mode in ("rough3d", "finish3d"):
            for field in self.operation_fields.get(mode, {}).values():
                field.textChanged.connect(self._invalidate_exact_toolpath_view)
            combo = self.operation_tool_combos.get(mode)
            if combo is not None:
                combo.currentTextChanged.connect(self._invalidate_exact_toolpath_view)
        for widget in (
            self.rough3d_boundary_combo,
            self.rough3d_strategy_combo,
            self.rough3d_profile_combo,
            self.rough3d_order_combo,
            self.rough3d_axis_combo,
            self.finish3d_boundary_combo,
            self.finish3d_strategy_combo,
        ):
            widget.currentIndexChanged.connect(self._invalidate_exact_toolpath_view)
        for widget in (
            self.rough3d_reverse_check,
            self.finish3d_reverse_check,
        ):
            widget.toggled.connect(self._invalidate_exact_toolpath_view)
        for key in (
            "material_thickness",
            "model_gap_above",
            "model_gap_below",
            "safe_height",
            "retract_height",
            "origin_x",
            "origin_y",
        ):
            field = self.fields.get(key)
            if field is not None:
                field.textChanged.connect(self._invalidate_exact_toolpath_view)

    def _on_applied_toolpath_selection_changed(self):
        self._update_simulation_time_label()
        entries = self._selected_applied_entries()
        if not entries:
            return
        if len(entries) != 1:
            self._clear_toolpath_overlay_only()
            self._toolpath_view_valid = False
            self.toolpath_truth_label.setText(
                "Selecione somente uma operação para conferir seu percurso exato. "
                "A exportação múltipla continua usando cada lista persistida separadamente."
            )
            self.toolpath_truth_label.setStyleSheet(
                "color: #92400e; background: #fffbeb; border: 1px solid #f59e0b; "
                "border-radius: 4px; padding: 5px 8px; font-weight: bold;"
            )
            return
        if not all(
            self._uses_lightweight_toolpath_overlay(settings)
            for settings, _moves, _label in entries
        ):
            return
        settings = dict(entries[0][0])
        moves = [
            dict(move)
            for _entry_settings, entry_moves, _label in entries
            for move in entry_moves
        ]
        self._show_exact_simulation_toolpath(settings, moves, "APLICADO = G-CODE")

    def _update_simulation_time_label(self):
        if not hasattr(self, "simulation_time_label"):
            return
        entries = self._selected_applied_entries()
        if entries:
            total = sum(
                self._estimate_machining_seconds(moves, settings)
                for settings, moves, _label in entries
            )
            self.simulation_time_label.setText(
                f"Tempo estimado selecionado: {self._format_duration(total)}"
            )
            return
        applied_entries = self._all_applied_entries()
        if applied_entries:
            total = sum(
                self._estimate_machining_seconds(moves, settings)
                for settings, moves, _label in applied_entries
            )
            self.simulation_time_label.setText(
                f"Tempo estimado total aplicado: {self._format_duration(total)}"
            )
            return
        if self.last_preview_settings is not None and self.last_preview_moves:
            total = self._estimate_machining_seconds(
                self.last_preview_moves,
                self.last_preview_settings,
            )
            self.simulation_time_label.setText(
                f"Tempo estimado da prévia: {self._format_duration(total)}"
            )
            return
        if self.last_applied_settings is not None and self.last_applied_moves:
            total = self._estimate_machining_seconds(
                self.last_applied_moves,
                self.last_applied_settings,
            )
            self.simulation_time_label.setText(
                f"Tempo estimado da última operação: {self._format_duration(total)}"
            )
            return
        self.simulation_time_label.setText("Tempo estimado: —")

    def _clear_existing_preview(self, doc):
        overlay = getattr(self, "_toolpath_overlay", None)
        if overlay is not None:
            overlay.clear()
            self._toolpath_overlay = None
        self._restore_preview_source_visibility(doc)
        group_name = "WoodCAM2D_Preview"
        existing = doc.getObject(group_name)
        if existing:
            # A prévia pode conter grupos aninhados (por exemplo, o datum XY).
            # Remover apenas os filhos diretos deixava os objetos internos na
            # árvore e fazia a próxima prévia acumular referências antigas.
            def remove_preview_tree(obj):
                for child in list(getattr(obj, "Group", ()) or ()):
                    remove_preview_tree(child)
                name = getattr(obj, "Name", None)
                if name and doc.getObject(name) is not None:
                    doc.removeObject(name)

            for child in list(getattr(existing, "Group", ()) or ()):
                remove_preview_tree(child)
            doc.removeObject(existing.Name)

    def _ensure_persistent_work_area(self, doc, settings):
        """Mantém a mesa visível fora do grupo temporário de pré-visualização."""
        if doc is None:
            return
        group = ensure_woodcam_tree(doc).work_area

        def remove_tree(obj):
            for nested in list(getattr(obj, "Group", ()) or ()):
                remove_tree(nested)
            name = getattr(obj, "Name", None)
            if name and doc.getObject(name) is not None:
                doc.removeObject(name)

        for child in list(group.Group):
            remove_tree(child)
        self._add_work_area_preview(doc, group, settings)
        self._add_material_datum_preview(doc, group, settings, {})
        doc.recompute()

    def _hide_selected_sources_for_preview(self, doc, keep_visible=()):
        self._restore_preview_source_visibility(doc)
        try:
            selected_objects = list(FreeCADGui.Selection.getSelection() or [])
        except Exception:
            selected_objects = []
        self._preview_hidden_sources = self._preview_source_visibility_changes(
            doc,
            selected_objects,
            keep_visible=keep_visible,
        )

    def _preview_source_visibility_changes(
        self,
        doc,
        selected_objects,
        keep_visible=(),
    ):
        """Aplica a visibilidade da prévia e retorna estados para restauração."""
        states = {}
        keep_names = {str(name) for name in (keep_visible or ()) if name}
        def set_visibility(obj, visible):
            view_object = getattr(obj, "ViewObject", None)
            if view_object is None or not hasattr(view_object, "Visibility"):
                return
            name = str(getattr(obj, "Name", "") or "")
            if not name:
                return
            was_visible = bool(view_object.Visibility)
            states.setdefault(name, was_visible)
            if was_visible != bool(visible):
                view_object.Visibility = bool(visible)

        handled = set()
        for obj in selected_objects:
            if obj is None or getattr(obj, "Document", None) is not doc:
                continue
            if obj.Name in handled:
                continue
            handled.add(obj.Name)
            try:
                if obj.Name in keep_names:
                    set_visibility(obj, True)
                else:
                    set_visibility(obj, False)
            except Exception:
                pass
        # Grupos pais ocultos também escondem o relevo, mesmo quando a
        # visibilidade do filho está ligada.
        for name in keep_names:
            source = doc.getObject(name) if hasattr(doc, "getObject") else None
            if source is None:
                source = next(
                    (obj for obj in selected_objects if getattr(obj, "Name", "") == name),
                    None,
                )
            if source is None:
                continue
            try:
                # A fonte pode estar dentro de mais de um grupo. No FreeCAD,
                # basta um ancestral oculto para o filho continuar invisível.
                # Percorra toda a cadeia sem assumir uma profundidade fixa.
                pending = [source]
                visited = set()
                while pending:
                    current = pending.pop()
                    current_name = str(getattr(current, "Name", "") or "")
                    if not current_name or current_name in visited:
                        continue
                    visited.add(current_name)
                    set_visibility(current, True)
                    pending.extend(list(getattr(current, "InList", []) or []))
            except Exception:
                pass
        return list(states.items())

    def _restore_preview_source_visibility(self, doc=None):
        hidden = list(getattr(self, "_preview_hidden_sources", []) or [])
        self._preview_hidden_sources = []
        if not hidden:
            return
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc is None:
            return
        for name, was_visible in hidden:
            obj = doc.getObject(name)
            view_object = getattr(obj, "ViewObject", None) if obj is not None else None
            if view_object is None or not hasattr(view_object, "Visibility"):
                continue
            try:
                view_object.Visibility = bool(was_visible)
            except Exception:
                pass

    def _set_simulation_running(self, running):
        current_index = self.operation_tabs.currentIndex()
        tab_title = self._tab_title(current_index) if current_index >= 0 else ""
        operation_tab_active = (
            self._operation_mode_for_index(current_index) is not None
            or tab_title == ACTION_TAB_TITLE
        )
        setup_tab_active = tab_title in {"Trabalho", "Material"}
        if hasattr(self, "apply_button"):
            self.apply_button.setEnabled(
                not running and (operation_tab_active or setup_tab_active)
            )
        if hasattr(self, "preview_button"):
            self.preview_button.setEnabled(
                not running and (operation_tab_active or setup_tab_active)
            )
        if hasattr(self, "simulate_button"):
            self.simulate_button.setEnabled(not running and operation_tab_active)
        if hasattr(self, "stop_sim_button"):
            self.stop_sim_button.setEnabled(running)
            self.stop_sim_button.setVisible(running)
        if self.sim_control is not None and not running:
            self.sim_control.hide()

    def _stop_simulation_timer(self):
        if self.sim_timer is not None:
            self.sim_timer.stop()
            self.sim_timer.deleteLater()
            self.sim_timer = None

    def _clear_existing_simulation(self, doc=None):
        self._stop_simulation_timer()

        if doc is None and self.simulation_state:
            doc = self.simulation_state.get("doc")
        if doc is None:
            doc = FreeCAD.ActiveDocument

        self._restore_simulation_path_visibility(doc)
        self.simulation_state = None
        self._set_simulation_running(False)

        if doc is not None:
            group_name = "WoodCAM2D_Simulation"
            existing = doc.getObject(group_name)
            if existing:
                for child in list(existing.Group):
                    doc.removeObject(child.Name)
                doc.removeObject(existing.Name)
                doc.recompute()

    def _hide_static_toolpaths_for_simulation(self, doc):
        """Oculta apenas a representação pesada; movimentos continuam intactos."""
        self._restore_simulation_path_visibility(doc)
        hidden = []
        for obj in list(getattr(doc, "Objects", []) or []):
            if not bool(getattr(obj, "WoodCAMAppliedPath", False)):
                continue
            view_object = getattr(obj, "ViewObject", None)
            if view_object is None or not hasattr(view_object, "Visibility"):
                continue
            was_visible = bool(view_object.Visibility)
            if was_visible:
                view_object.Visibility = False
            hidden.append((obj.Name, was_visible))
        self._simulation_hidden_paths = hidden

    def _restore_simulation_path_visibility(self, doc=None):
        hidden = list(getattr(self, "_simulation_hidden_paths", []) or [])
        self._simulation_hidden_paths = []
        if not hidden or doc is None:
            return
        for name, was_visible in hidden:
            obj = doc.getObject(name)
            view_object = getattr(obj, "ViewObject", None) if obj is not None else None
            if view_object is not None and hasattr(view_object, "Visibility"):
                view_object.Visibility = bool(was_visible)

    def _show_simulation_control(self, settings=None):
        if self.sim_control is not None:
            self.sim_control.hide()

    def _make_cutter_shape(
        self,
        tool_diameter,
        tool_type="end_mill",
        included_angle=None,
    ):
        """Cria uma fresa visual leve com a ponta exatamente em Z=0.

        As helices sao arestas de apresentacao sobre o solido, nao geometria
        usada no calculo do percurso. Elas tornam visivel a rotacao sem exigir
        uma subtracao booleana pesada ou recompute a cada quadro.
        """
        radius = max(float(tool_diameter) * 0.5, 0.5)
        cutting_height = max(8.0, min(20.0, radius * 5.0))
        body_height = max(cutting_height + 18.0, 30.0)
        normalized = str(tool_type or "end_mill")
        if normalized == "ball_nose":
            tip = Part.makeSphere(radius, FreeCAD.Vector(0, 0, radius))
            body_start = radius
        elif normalized in {"v_bit", "drill"}:
            # A ponta está em Z=0, exatamente na coordenada do movimento. A
            # altura do cone respeita o angulo incluido cadastrado da fresa.
            default_angle = 90.0 if normalized == "v_bit" else 118.0
            angle = max(1.0, min(179.0, float(included_angle or default_angle)))
            tip_height = max(
                radius / math.tan(math.radians(angle * 0.5)),
                0.5,
            )
            tip = Part.makeCone(
                0.0, radius, tip_height, FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1)
            )
            body_start = tip_height
        elif normalized == "surfacing":
            head_height = max(2.0, min(5.0, radius * 0.35))
            tip = Part.makeCylinder(
                radius,
                head_height,
                FreeCAD.Vector(0, 0, 0),
                FreeCAD.Vector(0, 0, 1),
            )
            body_start = head_height
        else:
            tip = Part.makeCylinder(
                radius, max(radius, 1.0), FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1)
            )
            body_start = max(radius, 1.0)

        cutting_body_height = max(1.0, min(cutting_height, body_height))
        cutting_body = Part.makeCylinder(
            radius,
            cutting_body_height,
            FreeCAD.Vector(0, 0, body_start),
            FreeCAD.Vector(0, 0, 1),
        )
        shank_start = body_start + cutting_body_height
        body = Part.makeCylinder(
            radius,
            max(1.0, body_height - cutting_body_height),
            FreeCAD.Vector(0, 0, shank_start),
            FreeCAD.Vector(0, 0, 1),
        )

        components = [tip, cutting_body, body]
        if normalized != "surfacing":
            flute_count = 2 if normalized in {"drill", "v_bit"} else 3
            pitch = max(8.0, cutting_body_height * 0.9)
            left_handed = normalized == "compression"
            for flute_index in range(flute_count):
                helix = Part.makeHelix(
                    pitch,
                    cutting_body_height,
                    radius * 1.002,
                    0.0,
                    left_handed,
                )
                helix.Placement = FreeCAD.Placement(
                    FreeCAD.Vector(0, 0, body_start),
                    FreeCAD.Rotation(
                        FreeCAD.Vector(0, 0, 1),
                        360.0 * flute_index / flute_count,
                    ),
                )
                components.append(helix)
        return Part.makeCompound(components)

    def _install_cutter_coin_transform(self, cutter_obj):
        """Move a fresa na GPU sem alterar Placement/documento a cada frame."""
        try:
            from pivy import coin

            root = cutter_obj.ViewObject.RootNode
            transform = coin.SoTransform()
            root.insertChild(transform, 0)
            return transform
        except Exception:
            return None

    @staticmethod
    def _visual_cutter_angle(settings, target_time_ms):
        """Rotacao legivel na tela, proporcional ao RPM sem alias de 18k RPM."""
        rpm = max(0.0, float(settings.get("rpm", DEFAULT_PRESETS["rpm"])))
        visible_turns_per_second = max(0.75, min(5.0, rpm / 6000.0))
        return (
            float(target_time_ms)
            * 0.001
            * visible_turns_per_second
            * 2.0
            * math.pi
        ) % (2.0 * math.pi)

    def _set_simulation_cutter_pose(self, state, position, target_time_ms):
        transform = state.get("cutter_transform")
        if transform is None:
            state["cutter_obj"].Placement.Base = FreeCAD.Vector(*position)
            return
        transform.translation = position
        try:
            from pivy import coin

            transform.rotation.setValue(
                coin.SbVec3f(0.0, 0.0, 1.0),
                self._visual_cutter_angle(state["settings"], target_time_ms),
            )
        except Exception:
            pass

    def _move_to_frame(self, move, current_position):
        current_x, current_y, current_z = current_position
        x = move.get("x") if move.get("x") is not None else current_x
        y = move.get("y") if move.get("y") is not None else current_y
        z = move.get("z") if move.get("z") is not None else current_z

        if x is None or y is None or z is None:
            return None, current_position

        frame = {"type": move["type"], "x": float(x), "y": float(y), "z": float(z)}
        if "feed_scale" in move:
            frame["feed_scale"] = float(move["feed_scale"])
        if move.get("corner_slowdown"):
            frame["corner_slowdown"] = True
        if move["type"] == "dwell":
            frame["seconds"] = max(0.0, float(move.get("seconds", 0.0)))
        return frame, (float(x), float(y), float(z))

    def _simulation_feed_for_move(self, move, settings):
        move_type = move["type"]
        if move_type == "rapid":
            return settings.get("rapid_feed", DEFAULT_PRESETS["rapid_feed"])
        if move_type in ("feed_plunge", "feed_drill"):
            return settings.get("feed_z", DEFAULT_PRESETS["feed_z"])
        if move_type in ("feed_ramp", "feed_helix"):
            return settings.get(
                "ramp_feed",
                min(
                    settings.get("feed_xy", DEFAULT_PRESETS["feed_xy"]) * 0.6,
                    settings.get("feed_xy", DEFAULT_PRESETS["feed_xy"]),
                ),
            )
        return settings.get("feed_xy", DEFAULT_PRESETS["feed_xy"]) * float(
            move.get("feed_scale", 1.0)
        )

    def _estimate_machining_seconds(self, moves, settings):
        current_position = (None, None, None)
        previous_frame = None
        total_seconds = 0.0

        for move in moves:
            frame, current_position = self._move_to_frame(move, current_position)
            if frame is None:
                continue
            if frame["type"] == "dwell":
                total_seconds += max(0.0, float(frame.get("seconds", 0.0)))
                continue
            if previous_frame is None:
                previous_frame = frame
                continue
            dx = frame["x"] - previous_frame["x"]
            dy = frame["y"] - previous_frame["y"]
            dz = frame["z"] - previous_frame["z"]
            length = math.sqrt(dx * dx + dy * dy + dz * dz)
            feed = max(self._simulation_feed_for_move(frame, settings), 1.0)
            total_seconds += length / feed * 60.0
            previous_frame = frame

        return total_seconds

    def _format_duration(self, seconds):
        seconds = max(0, int(round(float(seconds or 0.0))))
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours:d}h {minutes:02d}min {secs:02d}s"
        if minutes:
            return f"{minutes:d}min {secs:02d}s"
        return f"{secs:d}s"

    def _interpolate_simulation_frames(self, moves, settings):
        frames = []
        current_position = (None, None, None)
        tool_diameter = settings["tool_diameter"]
        cut_spacing = max(1.5, min(6.0, float(tool_diameter) * 0.75))
        rapid_spacing = cut_spacing * 3.0
        timeline_ms = 0.0
        speed_multiplier = max(float(settings["simulation_speed_multiplier"]), 0.001)

        for move_index, move in enumerate(moves):
            frame, current_position = self._move_to_frame(move, current_position)
            if frame is None:
                continue
            frame["move_index"] = move_index

            if not frames:
                frame["time_ms"] = 0.0
                frames.append(frame)
                continue

            previous = frames[-1]
            if frame["type"] == "dwell":
                timeline_ms += (
                    frame.get("seconds", 0.0) * 1000.0 / speed_multiplier
                )
                frame["time_ms"] = timeline_ms
                frames.append(frame)
                continue
            dx = frame["x"] - previous["x"]
            dy = frame["y"] - previous["y"]
            dz = frame["z"] - previous["z"]
            length = math.sqrt(dx * dx + dy * dy + dz * dz)
            spacing = rapid_spacing if frame["type"] == "rapid" else cut_spacing
            step_count = max(1, int(math.ceil(length / spacing)))
            feed = max(self._simulation_feed_for_move(frame, settings), 1.0)
            move_time_ms = (length / feed) * 60000.0 / speed_multiplier

            for step in range(1, step_count + 1):
                ratio = step / step_count
                frames.append(
                    {
                        "type": frame["type"],
                        "x": previous["x"] + dx * ratio,
                        "y": previous["y"] + dy * ratio,
                        "z": previous["z"] + dz * ratio,
                        "time_ms": timeline_ms + move_time_ms * ratio,
                        "move_index": move_index,
                    }
                )

            timeline_ms += move_time_ms

        max_frames = 8000
        if len(frames) > max_frames:
            frames = self._downsample_frames_preserving_moves(frames, max_frames)

        return frames

    def _exact_3d_simulation_timeline(self, moves, settings):
        """Linha do tempo compacta com cada ponto real do percurso 3D."""
        timeline = {
            "times": array("d"),
            "x": array("d"),
            "y": array("d"),
            "z": array("d"),
        }
        current_position = (None, None, None)
        previous_frame = None
        timeline_ms = 0.0
        speed_multiplier = max(
            float(settings["simulation_speed_multiplier"]),
            0.001,
        )
        for move in moves:
            frame, current_position = self._move_to_frame(move, current_position)
            if frame is None:
                continue
            if previous_frame is not None:
                if frame["type"] == "dwell":
                    timeline_ms += (
                        float(frame.get("seconds", 0.0))
                        * 1000.0
                        / speed_multiplier
                    )
                else:
                    dx = frame["x"] - previous_frame["x"]
                    dy = frame["y"] - previous_frame["y"]
                    dz = frame["z"] - previous_frame["z"]
                    length = math.sqrt(dx * dx + dy * dy + dz * dz)
                    feed = max(self._simulation_feed_for_move(frame, settings), 1.0)
                    timeline_ms += (
                        length / feed * 60000.0 / speed_multiplier
                    )
            timeline["times"].append(timeline_ms)
            timeline["x"].append(frame["x"])
            timeline["y"].append(frame["y"])
            timeline["z"].append(frame["z"])
            previous_frame = frame
        return timeline

    def _downsample_frames_preserving_moves(self, frames, max_frames):
        if len(frames) <= max_frames:
            return frames

        # Um percurso 3D normalmente possui um move_index diferente por ponto;
        # preservar toda troca de índice anulava completamente este limite.
        # Para a animação bastam extremos, mudanças de tipo e amostragem temporal.
        required = {0, len(frames) - 1}
        for index in range(1, len(frames)):
            if frames[index].get("type") != frames[index - 1].get("type"):
                required.add(index - 1)
                required.add(index)
        if len(required) >= max_frames:
            ordered = sorted(required)
            stride = int(math.ceil(len(ordered) / float(max_frames)))
            kept = ordered[::stride]
            if kept[-1] != len(frames) - 1:
                kept[-1] = len(frames) - 1
            return [frames[index] for index in kept[:max_frames]]

        remaining = max_frames - len(required)
        stride = max(1, int(math.ceil(len(frames) / float(remaining))))
        indexes = required | set(range(0, len(frames), stride))
        if len(indexes) > max_frames:
            optional = sorted(indexes - required)
            allowance = max_frames - len(required)
            optional_stride = max(1, int(math.ceil(len(optional) / float(allowance))))
            indexes = required | set(optional[::optional_stride][:allowance])
        return [frames[index] for index in sorted(indexes)]

    def _set_view_style(self, obj, color, line_width=None, transparency=None):
        if not hasattr(obj, "ViewObject") or obj.ViewObject is None:
            return

        obj.ViewObject.LineColor = color
        obj.ViewObject.ShapeColor = color
        if line_width is not None:
            obj.ViewObject.LineWidth = line_width
        if transparency is not None:
            obj.ViewObject.Transparency = transparency

    def _toolpath_components(self, moves, max_display_segments=4000):
        components = {
            "rapid": [],
            "ramp": [],
            "cut": [],
            "corner": [],
            # Primeiro deslocamento XY entre o datum configurado da area de
            # Trabalho e o inicio real da usinagem. Ele tambem pertence a
            # ``rapid``; esta copia permite que a vista 3D o destaque sem
            # transformar todos os rapidos de um relevo numa gaiola opaca.
            "origin": [],
            "entry_points": [],
        }
        def category(frame):
            if frame["type"] == "rapid":
                return "rapid"
            if frame["type"] in (
                "feed_plunge", "feed_drill", "feed_ramp", "feed_helix"
            ):
                return "ramp"
            if frame.get("corner_slowdown"):
                return "corner"
            return "cut"

        # Conta antes para limitar apenas a geometria Coin/OCC mostrada. A lista
        # original de movimentos permanece completa para estimativa e G-code.
        segment_count = 0
        previous_point = None
        current_position = (None, None, None)
        for move in moves:
            frame, current_position = self._move_to_frame(
                move,
                current_position,
            )
            if frame is None:
                continue
            current_point = (
                round(frame["x"], 4),
                round(frame["y"], 4),
                round(frame["z"], 4),
            )
            if previous_point is not None and previous_point != current_point:
                segment_count += 1
            previous_point = current_point

        if max_display_segments is None:
            stride = 1
        else:
            stride = max(
                1,
                int(math.ceil(
                    segment_count / float(max(1, max_display_segments))
                )),
            )
        previous_point = None
        anchor_point = None
        active_category = None
        accumulated = 0
        current_position = (None, None, None)
        before_first_feed = True
        for move in moves:
            frame, current_position = self._move_to_frame(move, current_position)
            if frame is None:
                continue
            current_point = (
                round(frame["x"], 4),
                round(frame["y"], 4),
                round(frame["z"], 4),
            )
            if (
                frame["type"] == "rapid"
                and move.get("x") is not None
                and move.get("y") is not None
            ):
                components["entry_points"].append(
                    (frame["x"], frame["y"])
                )
            if previous_point is not None and previous_point != current_point:
                if (
                    before_first_feed
                    and frame["type"] == "rapid"
                    and not components["origin"]
                    and (
                        previous_point[0] != current_point[0]
                        or previous_point[1] != current_point[1]
                    )
                ):
                    components["origin"].append((previous_point, current_point))
                current_category = category(frame)
                if current_category != active_category:
                    if (
                        active_category is not None
                        and anchor_point is not None
                        and anchor_point != previous_point
                    ):
                        components[active_category].append(
                            (anchor_point, previous_point)
                        )
                    active_category = current_category
                    anchor_point = previous_point
                    accumulated = 0
                accumulated += 1
                if accumulated >= stride:
                    components[active_category].append(
                        (anchor_point, current_point)
                    )
                    anchor_point = current_point
                    accumulated = 0
            if frame["type"] != "rapid":
                before_first_feed = False
            previous_point = current_point

        if (
            active_category is not None
            and anchor_point is not None
            and previous_point is not None
            and anchor_point != previous_point
        ):
            components[active_category].append((anchor_point, previous_point))
        entry_points = components["entry_points"]
        if len(entry_points) > 500:
            entry_stride = int(math.ceil(len(entry_points) / 500.0))
            components["entry_points"] = entry_points[::entry_stride][:500]

        return components

    @staticmethod
    def _shift_toolpath_components_xy(components, dx, dy):
        """Move only a visual toolpath projection in XY.

        The persisted moves/G-code are in machine coordinates.  A 3D source
        object, however, remains at its original document placement.  This
        helper is deliberately limited to the temporary Coin projection so
        the source and its real G-code are never modified just to make the
        preview line up on screen.
        """
        def shifted_point(point):
            # Coin3D so desenha linhas com pontos XYZ. A versao anterior
            # recriava cada extremidade somente como (X, Y); mesmo quando o
            # deslocamento era zero, isso fazia o overlay 3D descartar toda a
            # trajetoria. Preserve Z (e qualquer componente futura) enquanto
            # altera exclusivamente X/Y.
            values = [float(value) for value in point]
            values[0] += float(dx)
            values[1] += float(dy)
            return tuple(values)

        shifted = {}
        for key, values in dict(components or {}).items():
            if key == "entry_points":
                shifted[key] = [
                    (float(point[0]) + float(dx), float(point[1]) + float(dy))
                    for point in tuple(values or ())
                    if isinstance(point, (tuple, list)) and len(point) >= 2
                ]
                continue
            shifted[key] = [
                (
                    shifted_point(segment[0]),
                    shifted_point(segment[1]),
                )
                for segment in tuple(values or ())
                if (
                    isinstance(segment, (tuple, list))
                    and len(segment) == 2
                    and isinstance(segment[0], (tuple, list))
                    and isinstance(segment[1], (tuple, list))
                    and len(segment[0]) >= 2
                    and len(segment[1]) >= 2
                )
            ]
        return shifted

    def _add_toolpath_objects(
        self,
        doc,
        parent_group,
        components,
        name_prefix,
        tool_diameter,
        applied=False,
        operation_mode=None,
    ):
        created = []
        is_3d = operation_mode in {"rough3d", "finish3d"}
        object_specs = (
            (
                "Rapid",
                "Deslocamentos rápidos",
                components["rapid"],
                (0.5, 0.5, 1.0),
                1,
                0.55 if is_3d else 0.0,
            ),
            (
                "Ramp",
                "Entradas e descidas",
                components["ramp"],
                (1.0, 0.75, 0.0),
                2,
                0.35 if is_3d else 0.0,
            ),
            (
                "Cut",
                "Trajetória de usinagem",
                components["cut"],
                (0.08, 0.30, 0.62) if is_3d else (1.0, 0.0, 0.0),
                1 if is_3d else 2,
                0.55 if is_3d else 0.0,
            ),
            (
                "CornerSlowdown",
                "Cantos desacelerados",
                components["corner"],
                (0.65, 0.15, 0.95),
                4,
                0.35 if is_3d else 0.0,
            ),
        )
        for suffix, label, edges, color, width, transparency in object_specs:
            if not edges:
                continue
            path_object = create_coin_toolpath_feature(
                doc,
                f"{name_prefix}_{suffix}",
                label,
                edges,
                color=color,
                line_width=width,
                transparency=transparency,
            )
            parent_group.addObject(path_object)
            created.append(path_object)

        entry_points = components["entry_points"]
        if entry_points and not is_3d:
            marker_radius = max(
                1.5,
                min(5.0, float(tool_diameter) * 0.45),
            )
            marker_object = doc.addObject(
                "Part::Feature",
                f"{name_prefix}_EntryPoints",
            )
            marker_object.Label = "Pontos de entrada"
            marker_object.Shape = Part.makeCompound(
                [
                    Part.makeSphere(
                        marker_radius,
                        FreeCAD.Vector(point[0], point[1], 0.0),
                    )
                    for point in entry_points
                ]
            )
            self._set_view_style(
                marker_object,
                (0.0, 0.75, 0.2),
                transparency=5,
            )
            parent_group.addObject(marker_object)
            created.append(marker_object)

        if applied:
            for path_object in created:
                path_object.addProperty(
                    "App::PropertyBool",
                    "WoodCAMAppliedPath",
                    "WoodCAM 2D",
                )
                path_object.WoodCAMAppliedPath = True

        return created

    def _hide_legacy_persisted_lightweight_paths(self, doc):
        """Oculta projeções antigas de Corte/3D sem alterar movimentos ou G-code."""
        if doc is None:
            return
        for obj in list(getattr(doc, "Objects", []) or []):
            if not bool(getattr(obj, "WoodCAMAppliedPath", False)):
                continue
            parents = list(getattr(obj, "InList", []) or [])
            uses_lightweight_overlay = any(
                str(getattr(parent, "WoodCAMOperationType", ""))
                in {"cut", "rough3d", "finish3d"}
                for parent in parents
            )
            if not uses_lightweight_overlay:
                continue
            view_object = getattr(obj, "ViewObject", None)
            if view_object is not None and hasattr(view_object, "Visibility"):
                view_object.Visibility = False

    def _add_applied_toolpath_projection(
        self,
        doc,
        operation,
        moves,
        name_prefix,
        settings,
    ):
        """Persiste projeção somente para operações 2D de baixa cardinalidade.

        Em Corte e 3D, ``MovesCompressedBase64`` é a operação. A visualização
        completa azul é recriada sob demanda pelo overlay Coin; duplicá-la em
        vermelho no FCStd tornava o documento pesado e poluía a prévia.
        """
        if self._uses_lightweight_toolpath_overlay(settings):
            return []
        return self._add_toolpath_objects(
            doc,
            operation,
            self._toolpath_components(moves),
            name_prefix,
            settings["tool_diameter"],
            applied=True,
            operation_mode=settings.get("operation_mode"),
        )

    def _work_area_dimensions(self, settings):
        width = float(settings.get("job_width", 0.0) or 0.0)
        height = float(settings.get("job_height", 0.0) or 0.0)
        if width > 0.0 and height > 0.0:
            return width, height

        try:
            bounds = self._geometry_bounds(get_selected_geometry())
        except Exception:
            bounds = None
        if bounds is None:
            return width, height

        min_x, min_y, max_x, max_y = bounds
        if width <= 0.0:
            width = max_x - min_x
        if height <= 0.0:
            height = max_y - min_y
        return width, height

    def _work_area_bounds_for_preview(self, settings):
        explicit_bounds = settings.get("_work_area_bounds")
        if isinstance(explicit_bounds, (tuple, list)) and len(explicit_bounds) == 4:
            try:
                min_x, min_y, max_x, max_y = map(float, explicit_bounds)
            except (TypeError, ValueError):
                pass
            else:
                if max_x > min_x and max_y > min_y:
                    return min_x, min_y, max_x, max_y

        width, height = self._work_area_dimensions(settings)
        if width <= 0.0 or height <= 0.0:
            return None

        anchor = settings.get(
            "job_origin_anchor",
            settings.get("origin_anchor", "bottom_left"),
        )
        if anchor.endswith("_right"):
            min_x, max_x = -width, 0.0
        elif anchor.endswith("_center") or anchor == "center":
            min_x, max_x = -width * 0.5, width * 0.5
        else:
            min_x, max_x = 0.0, width

        if anchor.startswith("top_"):
            min_y, max_y = -height, 0.0
        elif anchor.startswith("middle_") or anchor == "center":
            min_y, max_y = -height * 0.5, height * 0.5
        else:
            min_y, max_y = 0.0, height

        offset_x = float(settings.get("job_origin_x", 0.0) or 0.0)
        offset_y = float(settings.get("job_origin_y", 0.0) or 0.0)
        min_x += offset_x
        max_x += offset_x
        min_y += offset_y
        max_y += offset_y
        return min_x, min_y, max_x, max_y

    def _add_work_area_preview(self, doc, parent_group, settings):
        bounds = self._work_area_bounds_for_preview(settings)
        if bounds is None:
            return None

        min_x, min_y, max_x, max_y = bounds
        z_value = preview_material_top_z(settings)
        points = [
            FreeCAD.Vector(min_x, min_y, z_value),
            FreeCAD.Vector(max_x, min_y, z_value),
            FreeCAD.Vector(max_x, max_y, z_value),
            FreeCAD.Vector(min_x, max_y, z_value),
            FreeCAD.Vector(min_x, min_y, z_value),
        ]
        edges = [
            Part.makeLine(points[index], points[index + 1])
            for index in range(len(points) - 1)
        ]
        area_object = doc.addObject("Part::Feature", "Preview_WorkArea")
        area_object.Label = "Limite da área de trabalho"
        area_object.Shape = Part.makeCompound(edges)
        self._set_view_style(
            area_object,
            (0.15, 0.55, 1.0),
            line_width=1,
            transparency=65,
        )
        if hasattr(area_object, "ViewObject") and area_object.ViewObject is not None:
            try:
                area_object.ViewObject.LinePattern = "Dashed"
            except Exception:
                pass
        parent_group.addObject(area_object)
        return area_object

    def _geometry_for_datum_preview(self, settings):
        if not settings.get("use_selection_bounds_origin", False):
            return {}
        try:
            return self._active_geometry()
        except Exception:
            return {}

    def _selected_geometry_for_preview(self):
        try:
            return self._active_geometry()
        except Exception:
            return {"contours": [], "holes": []}

    def _shift_preview_point(self, point, placement_x, placement_y, z_value):
        return FreeCAD.Vector(
            float(point[0]) + float(placement_x),
            float(point[1]) + float(placement_y),
            z_value,
        )

    def _add_shifted_geometry_preview(self, doc, parent_group, settings, geometry):
        """Desenha o esboço/contorno de referência já no placement do datum."""
        contours = list(geometry.get("contours", []) or [])
        holes = list(geometry.get("holes", []) or [])
        if not contours and not holes:
            return None

        placement_x, placement_y = settings.get("_xy_origin_offset", (None, None))
        if placement_x is None or placement_y is None:
            placement_x, placement_y = self._xy_origin_offset(settings, geometry)

        z_value = preview_material_top_z(settings) + 0.12
        sketch_geometry = []
        for contour in contours:
            points = list(contour or [])
            if len(points) < 2:
                continue
            shifted_points = [
                self._shift_preview_point(point, placement_x, placement_y, z_value)
                for point in points
            ]
            if shifted_points[0].distanceToPoint(shifted_points[-1]) > 1e-6:
                shifted_points.append(shifted_points[0])
            for index in range(len(shifted_points) - 1):
                sketch_geometry.append(
                    Part.LineSegment(shifted_points[index], shifted_points[index + 1])
                )

        for hole in holes:
            points = list(hole.get("points", []) or [])
            if len(points) >= 2:
                shifted_points = [
                    self._shift_preview_point(point, placement_x, placement_y, z_value)
                    for point in points
                ]
                if shifted_points[0].distanceToPoint(shifted_points[-1]) > 1e-6:
                    shifted_points.append(shifted_points[0])
                for index in range(len(shifted_points) - 1):
                    sketch_geometry.append(
                        Part.LineSegment(shifted_points[index], shifted_points[index + 1])
                    )
                continue

            diameter = float(hole.get("diameter_mm", 0.0) or 0.0)
            if diameter <= 0.0:
                continue
            center = FreeCAD.Vector(
                float(hole.get("x", 0.0)) + float(placement_x),
                float(hole.get("y", 0.0)) + float(placement_y),
                z_value,
            )
            sketch_geometry.append(
                Part.Circle(center, FreeCAD.Vector(0, 0, 1), diameter * 0.5)
            )

        if not sketch_geometry:
            return None

        reference_object = doc.addObject(
            "Sketcher::SketchObject",
            "Preview_ShiftedSketch",
        )
        reference_object.Label = "Esboço posicionado para corte"
        for item in sketch_geometry:
            reference_object.addGeometry(item, False)
        self._set_view_style(
            reference_object,
            (1.0, 0.45, 0.0),
            line_width=3,
            transparency=0,
        )
        if hasattr(reference_object, "ViewObject") and reference_object.ViewObject is not None:
            try:
                reference_object.ViewObject.LinePattern = "Dashed"
            except Exception:
                pass
        parent_group.addObject(reference_object)
        return reference_object

    def _add_material_datum_preview(self, doc, parent_group, settings, geometry=None):
        bounds = self._datum_reference_bounds(settings, geometry or {})
        if bounds is None:
            return []

        min_x, min_y, max_x, max_y = bounds
        anchor = settings.get("origin_anchor", "bottom_left")
        anchor_x, anchor_y = self._anchor_point_from_bounds(
            bounds,
            anchor,
        )
        span = max(max_x - min_x, max_y - min_y, 1.0)
        axis_length = max(35.0, min(span * 0.08, 140.0))
        radius = max(4.0, min(axis_length * 0.08, 9.0))
        geometry_bounds = self._geometry_bounds(geometry or {})
        if settings.get("use_selection_bounds_origin", False) and geometry_bounds:
            # Com o contorno como referência, o ponto inicial é sempre o
            # canto inferior esquerdo da peça já posicionada na chapa.
            placement_x, placement_y = settings.get("_xy_origin_offset", (None, None))
            if placement_x is None or placement_y is None:
                placement_x, placement_y = self._xy_origin_offset(settings, geometry)
            datum_x = geometry_bounds[0] + float(placement_x)
            datum_y = geometry_bounds[1] + float(placement_y)
            x_direction = 1.0
            y_direction = 1.0
        else:
            # Sem uma referência de contorno, o indicador representa exatamente
            # o datum configurado. Antes ele era deslocado artificialmente para
            # que os eixos coubessem dentro do retângulo, mas isso fazia o 3D
            # parecer começar em outro ponto que não o XY real da máquina.
            datum_x = anchor_x + float(settings.get("origin_x", 0.0) or 0.0)
            datum_y = anchor_y + float(settings.get("origin_y", 0.0) or 0.0)
            x_direction = -1.0 if anchor.endswith("_right") else 1.0
            y_direction = -1.0 if anchor.startswith("top_") else 1.0
        z_value = preview_material_top_z(settings) + 0.2
        created = []

        # Um único nó deixa a árvore legível. Os eixos continuam sendo
        # objetos independentes para manter cores/seleção, mas ficam agrupados
        # sob o mesmo Datum XY do material.
        datum_group = doc.addObject(
            "App::DocumentObjectGroup",
            "Preview_MaterialDatum",
        )
        datum_group.Label = "Datum XY do material"
        parent_group.addObject(datum_group)

        def add_feature(name, label, shape, color, line_width=3):
            obj = doc.addObject("Part::Feature", name)
            obj.Label = label
            obj.Shape = shape
            self._set_view_style(obj, color, line_width=line_width, transparency=0)
            datum_group.addObject(obj)
            created.append(obj)

        origin = FreeCAD.Vector(datum_x, datum_y, z_value)
        add_feature(
            "Preview_MaterialDatumX",
            "Eixo X",
            Part.makeLine(
                origin,
                FreeCAD.Vector(
                    datum_x + (axis_length * x_direction), datum_y, z_value
                ),
            ),
            (1.0, 0.12, 0.12),
        )
        add_feature(
            "Preview_MaterialDatumY",
            "Eixo Y",
            Part.makeLine(
                origin,
                FreeCAD.Vector(
                    datum_x, datum_y + (axis_length * y_direction), z_value
                ),
            ),
            (0.1, 0.78, 0.22),
        )
        add_feature(
            "Preview_MaterialDatumDot",
            "Origem XY",
            Part.makeCircle(radius, origin, FreeCAD.Vector(0, 0, 1)),
            (1.0, 0.0, 0.0),
            line_width=4,
        )
        return created

    def _selection_snapshot(self):
        current_mode = self._operation_mode_for_index(self.operation_tabs.currentIndex())
        if (
            current_mode not in {"rough3d", "finish3d"}
            and self._use_vector_editor_for_cam
            and self._vector_editor_document is not None
        ):
            widget = getattr(self, "vector_editor_widget", None)
            return [
                {
                    "source": "editor_2d",
                    "document_uuid": self._vector_editor_document.document_uuid,
                    "revision": self._vector_editor_document.revision,
                    "entity_ids": list(
                        getattr(widget, "selected_entity_ids", ()) or
                        self._vector_editor_document.entities_by_id.keys()
                    ),
                }
            ]
        snapshot = []
        for selection in FreeCADGui.Selection.getSelectionEx():
            obj = getattr(selection, "Object", None)
            if obj is None:
                continue
            snapshot.append(
                {
                    "document": getattr(
                        getattr(obj, "Document", None),
                        "Name",
                        "",
                    ),
                    "object": obj.Name,
                    "subelements": list(
                        getattr(selection, "SubElementNames", ())
                    ),
                }
            )
        if (
            current_mode in {"rough3d", "finish3d"}
            and self._use_vector_editor_for_cam
            and self._vector_editor_document is not None
        ):
            boundary_mode = (
                self.rough3d_boundary_combo.currentIndex()
                if current_mode == "rough3d"
                else self.finish3d_boundary_combo.currentIndex()
            )
            if boundary_mode == 2:
                widget = getattr(self, "vector_editor_widget", None)
                snapshot.append(
                    {
                        "source": "editor_2d",
                        "document_uuid": self._vector_editor_document.document_uuid,
                        "revision": self._vector_editor_document.revision,
                        "entity_ids": list(
                            getattr(widget, "selected_entity_ids", ())
                            or self._vector_editor_document.entities_by_id.keys()
                        ),
                    }
                )
        return snapshot

    def _selected_operation_from_tree(self):
        """Retorna a operação WoodCAM selecionada na árvore, inclusive um filho."""
        doc = FreeCAD.ActiveDocument
        if doc is None:
            return None
        try:
            selected = [item.Object for item in FreeCADGui.Selection.getSelectionEx()]
        except Exception:
            selected = []
        operations = self._iter_applied_operation_objects()
        for obj in selected:
            if obj in operations:
                return obj
            for operation in operations:
                if obj in list(getattr(operation, "Group", []) or []):
                    return operation
        return None

    def _restore_operation_source_selection(self, operation):
        """Restaura os vetores guardados quando a operação foi criada."""
        try:
            snapshot = json.loads(str(operation.SelectionJSON))
        except Exception as error:
            raise ValueError("A operação selecionada não possui a geometria original salva.") from error
        doc = FreeCAD.ActiveDocument
        if doc is None:
            raise ValueError("Nenhum documento ativo do FreeCAD encontrado.")
        FreeCADGui.Selection.clearSelection()
        restored = 0
        for item in snapshot:
            if item.get("source") == "editor_2d":
                widget = getattr(self, "vector_editor_widget", None)
                if widget is None:
                    continue
                if item.get("document_uuid") != widget.document.document_uuid:
                    continue
                ids = [
                    entity_id
                    for entity_id in item.get("entity_ids", [])
                    if entity_id in widget.document.entities_by_id
                ]
                widget.controller.selection.replace(ids)
                self._use_vector_editor_for_cam = True
                widget.set_cam_source_active(True)
                restored += len(ids) or 1
                continue
            if item.get("document") and item["document"] != doc.Name:
                continue
            source = doc.getObject(item.get("object", ""))
            if source is None:
                continue
            subelements = list(item.get("subelements", []) or [])
            if subelements:
                for subelement in subelements:
                    FreeCADGui.Selection.addSelection(source, subelement)
                    restored += 1
            else:
                FreeCADGui.Selection.addSelection(source)
                restored += 1
        if not restored:
            raise ValueError(
                "Não foi possível encontrar a geometria original desta operação. "
                "Selecione os vetores novamente e crie uma nova operação."
            )

    def _restore_editing_source_if_needed(self):
        """Restaura a fonte persistida antes de recalcular uma edição."""
        operation = self._selected_operation_from_tree()
        if operation is None:
            operation = self._editing_operation
        if operation is not None:
            self._restore_operation_source_selection(operation)

    def _load_selected_operation_for_editing(self):
        operation = self._selected_operation_from_tree()
        if operation is None or operation is self._editing_operation:
            return
        try:
            settings = json.loads(str(operation.SettingsJSON))
        except Exception:
            return
        mode = str(settings.get("operation_mode", ""))
        if mode not in self.operation_fields:
            return
        for key, field in self.fields.items():
            if key in settings:
                field.setText(self._format_value(settings[key]))
        for key, field in self.operation_fields[mode].items():
            if key in settings:
                field.setText(self._format_value(settings[key]))
        tool_combo = self.operation_tool_combos.get(mode)
        tool_name = str(settings.get("tool_name", "") or "")
        if tool_combo is not None and tool_name:
            tool_index = tool_combo.findText(tool_name)
            if tool_index >= 0:
                tool_combo.setCurrentIndex(tool_index)
        self.operation_names[mode].setText(str(settings.get("operation_name", operation.Label)))
        if mode == "cut":
            self.operation_combo.setCurrentIndex(
                self.CUT_SIDE_BY_INDEX.index(settings.get("outer_cut_side", CUT_SIDE_OUTSIDE))
                if settings.get("outer_cut_side", CUT_SIDE_OUTSIDE) in self.CUT_SIDE_BY_INDEX else 0
            )
            self.cut_smart_entry_check.setChecked(bool(settings.get("smart_entry", True)))
            self.cut_tabs_enabled.setChecked(bool(settings.get("cut_tabs_enabled", False)))
            self.cut_tabs_3d.setChecked(bool(settings.get("tabs_3d", False)))
            self.cut_tabs_auto_enabled.setChecked(bool(settings.get("tab_count", 0)))
            self.cut_tabs_manual_enabled.setChecked(bool(settings.get("tab_positions", [])))
            ramp_types = ("smooth", "zigzag", "spiral")
            ramp_type = settings.get("ramp_type", "smooth")
            self.cut_ramp_type_buttons[ramp_types.index(ramp_type) if ramp_type in ramp_types else 0].setChecked(True)
        elif mode == "rough3d":
            self.rough3d_boundary_combo.setCurrentIndex(
                {"model": 0, "material": 1, "selected_vectors": 2, "selected_level": 3}.get(
                    settings.get("boundary_mode"), 0
                )
            )
            self.rough3d_strategy_combo.setCurrentIndex(
                0 if settings.get("rough3d_strategy") == "z_level" else 1
            )
            self.rough3d_profile_combo.setCurrentIndex(
                {"last": 0, "first": 1, "none": 2}.get(settings.get("rough3d_profile"), 0)
            )
            self.rough3d_order_combo.setCurrentIndex(
                0 if settings.get("rough3d_order") == "level" else 1
            )
            self.rough3d_axis_combo.setCurrentIndex(
                0 if settings.get("rough3d_axis") == "x" else 1
            )
            self.rough3d_reverse_check.setChecked(bool(settings.get("rough3d_reverse", False)))
        elif mode == "finish3d":
            self.finish3d_boundary_combo.setCurrentIndex(
                {"model": 0, "material": 1, "selected_vectors": 2, "selected_level": 3}.get(
                    settings.get("boundary_mode"), 0
                )
            )
            self.finish3d_strategy_combo.setCurrentIndex(
                0 if settings.get("finish3d_strategy") == "raster" else 1
            )
            self.finish3d_reverse_check.setChecked(bool(settings.get("finish3d_reverse", False)))
        ramp_check = self.operation_ramp_checks.get(mode)
        if ramp_check is not None:
            ramp_check.setChecked(bool(settings.get("use_ramp", False)))
        index = self._operation_tab_index(mode)
        if index is not None:
            self.operation_tabs.setCurrentIndex(index)
        self.last_operation_mode = mode
        self._editing_operation = operation
        self.apply_button.setText("Atualizar operação")
        self.apply_button.setToolTip("Atualiza a operação selecionada e mantém a mesma entrada na árvore.")
        self.operation_hint.setText(f"Editando: {operation.Label}")
        self._update_operation_controls()

    def _update_applied_operation(self, settings, moves, operation):
        doc = FreeCAD.ActiveDocument
        for child in list(getattr(operation, "Group", []) or []):
            doc.removeObject(child.Name)
        operation_name = str(settings.get("operation_name", "") or "").strip()
        if operation_name:
            operation.Label = operation_name
        operation.OperationName = operation.Label
        operation.SettingsJSON = json.dumps(settings, ensure_ascii=False, sort_keys=True)
        if not hasattr(operation, "MovesCompressedBase64"):
            operation.addProperty(
                "App::PropertyString", "MovesCompressedBase64", "WoodCAM 2D"
            )
        operation.MovesCompressedBase64 = encode_moves(moves)
        operation.MovesJSON = ""
        operation.SelectionJSON = json.dumps(self._selection_snapshot(), ensure_ascii=False, separators=(",", ":"))
        operation.MoveCount = len(moves)
        self._add_applied_toolpath_projection(
            doc,
            operation,
            moves,
            operation.Name,
            settings,
        )
        doc.recompute()
        return operation

    def _next_operation_sequence(self, doc, operation_mode):
        sequences = [
            int(obj.WoodCAMSequence)
            for obj in doc.Objects
            if hasattr(obj, "WoodCAMOperationType")
            and obj.WoodCAMOperationType == operation_mode
            and hasattr(obj, "WoodCAMSequence")
        ]
        return max(sequences, default=0) + 1

    def _create_applied_operation(
        self,
        settings,
        moves,
        selection_snapshot=None,
    ):
        doc = FreeCAD.ActiveDocument
        if doc is None:
            raise RuntimeError(
                "Nenhum documento ativo do FreeCAD encontrado para aplicar "
                "a operação."
            )

        operation_mode = settings["operation_mode"]
        if operation_mode not in OPERATION_TREE_LABELS:
            raise ValueError("A operação selecionada não pode ser aplicada.")
        if not moves:
            raise ValueError("A operação não produziu nenhuma trajetória.")

        operations_root = ensure_woodcam_tree(doc).operations

        sequence = self._next_operation_sequence(doc, operation_mode)
        operation_label = OPERATION_TREE_LABELS[operation_mode]
        operation_slug = {
            "holes": "Furo",
            "cut": "Corte",
            "pocket": "Preenchimento",
            "rough3d": "Desbaste3D",
            "finish3d": "Acabamento3D",
        }[operation_mode]
        internal_name = f"WoodCAMOperation{operation_slug}{sequence:03d}"
        while doc.getObject(internal_name) is not None:
            sequence += 1
            internal_name = (
                f"WoodCAMOperation{operation_slug}{sequence:03d}"
            )

        operation_group = doc.addObject(
            "App::DocumentObjectGroup",
            internal_name,
        )
        operation_name = str(settings.get("operation_name", "") or "").strip()
        operation_group.Label = operation_name or f"{operation_label} {sequence:02d}"
        operation_group.addProperty(
            "App::PropertyString",
            "WoodCAMOperationType",
            "WoodCAM 2D",
        )
        operation_group.addProperty(
            "App::PropertyInteger",
            "WoodCAMSequence",
            "WoodCAM 2D",
        )
        operation_group.addProperty(
            "App::PropertyString",
            "OperationName",
            "WoodCAM 2D",
        )
        operation_group.addProperty(
            "App::PropertyString",
            "SettingsJSON",
            "WoodCAM 2D",
        )
        operation_group.addProperty(
            "App::PropertyString",
            "MovesJSON",
            "WoodCAM 2D",
        )
        operation_group.addProperty(
            "App::PropertyString",
            "MovesCompressedBase64",
            "WoodCAM 2D",
        )
        operation_group.addProperty(
            "App::PropertyString",
            "SelectionJSON",
            "WoodCAM 2D",
        )
        operation_group.addProperty(
            "App::PropertyInteger",
            "MoveCount",
            "WoodCAM 2D",
        )
        operation_group.WoodCAMOperationType = operation_mode
        operation_group.WoodCAMSequence = sequence
        operation_group.OperationName = operation_group.Label
        operation_group.SettingsJSON = json.dumps(
            settings,
            ensure_ascii=False,
            sort_keys=True,
        )
        operation_group.MovesJSON = ""
        operation_group.MovesCompressedBase64 = encode_moves(moves)
        operation_group.SelectionJSON = json.dumps(
            (
                selection_snapshot
                if selection_snapshot is not None
                else self._selection_snapshot()
            ),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        operation_group.MoveCount = len(moves)
        operations_root.addObject(operation_group)

        self._add_applied_toolpath_projection(
            doc,
            operation_group,
            moves,
            internal_name,
            settings,
        )
        doc.recompute()
        return operation_group

    def _show_simulation(self, moves, settings):
        doc = FreeCAD.ActiveDocument
        if doc is None:
            raise RuntimeError("Nenhum documento ativo do FreeCAD encontrado para simular a trajetória.")

        # A prévia 2D convencional usa linhas vermelhas persistidas num grupo
        # temporário. Sem removê-la, ela ficava sobreposta ao novo overlay Coin
        # azul do Corte e dava a impressão de que a simulação pesada ainda
        # estava ativa. Simular nunca deve acumular as duas representações.
        self._clear_existing_preview(doc)

        exact_static_path = self._simulation_uses_exact_static_path(settings)
        if exact_static_path:
            # O rastro visível e a linha do tempo compacta usam a mesma lista
            # integral de movimentos do G-code. A fresa interpola somente
            # dentro do segmento real atual; nenhum ponto de Z e descartado.
            self._show_exact_simulation_toolpath(
                settings,
                moves,
                "SIMULAÇÃO EXATA = G-CODE",
            )
        display_moves = (
            moves_for_preview(settings, moves)
            if exact_static_path
            else moves
        )
        exact_timeline = (
            self._exact_3d_simulation_timeline(display_moves, settings)
            if exact_static_path
            else None
        )
        frames = (
            []
            if exact_static_path
            else self._interpolate_simulation_frames(moves, settings)
        )
        simulation_point_count = (
            len(exact_timeline["times"])
            if exact_timeline is not None
            else len(frames)
        )
        if simulation_point_count < 2:
            raise RuntimeError("A trajetória não tem movimentos suficientes para simulação.")

        # O realce de seleção de uma malha com centenas de milhares de facetas
        # é mais caro que a própria animação e não é necessário durante o play.
        try:
            FreeCADGui.Selection.clearSelection()
        except Exception:
            pass
        self._clear_existing_simulation(doc)
        self._hide_static_toolpaths_for_simulation(doc)

        simulation_group = doc.addObject("App::DocumentObjectGroup", "WoodCAM2D_Simulation")
        simulation_group.Label = "Simulação"
        ensure_woodcam_tree(doc).operations.addObject(simulation_group)

        cutter_obj = doc.addObject("Part::Feature", "Simulation_Cutter")
        cutter_obj.Shape = self._make_cutter_shape(
            settings["tool_diameter"],
            settings.get("tool_type", "end_mill"),
            settings.get("included_angle"),
        )
        self._set_view_style(cutter_obj, (0.82, 0.84, 0.86), transparency=5)
        simulation_group.addObject(cutter_obj)

        rapid_obj = None
        ramp_obj = None
        cut_obj = None
        if not exact_static_path:
            rapid_obj = create_coin_toolpath_feature(
                doc, "Simulation_RapidTrail", "Deslocamentos rápidos", [],
                color=(0.35, 0.55, 1.0), line_width=1, transparency=0.45,
            )
            simulation_group.addObject(rapid_obj)

            ramp_obj = create_coin_toolpath_feature(
                doc, "Simulation_RampTrail", "Entradas e descidas", [],
                color=(1.0, 0.72, 0.0),
                line_width=max(2, min(8, int(settings["tool_diameter"]))),
                transparency=0.10,
            )
            simulation_group.addObject(ramp_obj)

            cut_obj = create_coin_toolpath_feature(
                doc, "Simulation_CutTrail", "Rastro de corte", [],
                color=(0.10, 0.70, 1.0),
                line_width=max(1, min(4, int(settings["tool_diameter"] * 0.5))),
                transparency=0.02,
            )
            simulation_group.addObject(cut_obj)

        self.simulation_state = {
            "doc": doc,
            "group": simulation_group,
            "frames": frames,
            "frame_times": [float(frame["time_ms"]) for frame in frames],
            "exact_timeline": exact_timeline,
            "index": 0,
            "cutter_obj": cutter_obj,
            "cutter_transform": self._install_cutter_coin_transform(cutter_obj),
            "rapid_obj": rapid_obj,
            "ramp_obj": ramp_obj,
            "cut_obj": cut_obj,
            "rapid_edges": [],
            "ramp_edges": [],
            "cut_edges": [],
            "exact_static_path": exact_static_path,
            "settings": dict(settings),
            "start_time": time.monotonic(),
        }

        if exact_timeline is not None:
            first_position = (
                exact_timeline["x"][0],
                exact_timeline["y"][0],
                exact_timeline["z"][0],
            )
        else:
            first_frame = frames[0]
            first_position = (
                first_frame["x"],
                first_frame["y"],
                first_frame["z"],
            )
        self._set_simulation_cutter_pose(
            self.simulation_state,
            first_position,
            0.0,
        )
        doc.recompute()
        if FreeCADGui.ActiveDocument:
            FreeCADGui.ActiveDocument.ActiveView.fitAll()

        self.sim_timer = QtCore.QTimer(self)
        try:
            self.sim_timer.setTimerType(QtCore.Qt.PreciseTimer)
        except (AttributeError, TypeError):
            try:
                self.sim_timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
            except (AttributeError, TypeError):
                pass
        self.sim_timer.timeout.connect(self._advance_simulation)
        self.sim_timer.start(25)
        self._set_simulation_running(True)
        self._show_simulation_control(settings)

    def _update_trail_shape(self, obj, edges):
        view_object = getattr(obj, "ViewObject", None)
        proxy = getattr(view_object, "Proxy", None) if view_object is not None else None
        if proxy is not None and hasattr(proxy, "set_segment_pairs"):
            proxy.set_segment_pairs(edges)

    @staticmethod
    def _uses_lightweight_toolpath_overlay(settings):
        return settings.get("operation_mode") in {"cut", "rough3d", "finish3d"}

    @staticmethod
    def _simulation_uses_exact_static_path(settings):
        # Corte e preenchimento podem gerar muitos segmentos em rampas,
        # curvas e múltiplas passadas. Um único overlay Coin evita recriar o
        # rastro a cada tick; somente a fresa é animada.
        return settings.get("operation_mode") in {
            "cut", "pocket", "rough3d", "finish3d"
        }

    @staticmethod
    def _interpolated_simulation_position(frames, frame_times, target_time_ms):
        """Interpola a fresa continuamente sem inventar geometria de corte."""
        if not frames:
            return 0, (0.0, 0.0, 0.0), True
        target = float(target_time_ms)
        if target <= frame_times[0]:
            frame = frames[0]
            return 0, (frame["x"], frame["y"], frame["z"]), False
        if target >= frame_times[-1]:
            frame = frames[-1]
            return (
                len(frames) - 1,
                (frame["x"], frame["y"], frame["z"]),
                True,
            )
        lower_index = max(
            0,
            min(
                len(frames) - 2,
                bisect.bisect_right(frame_times, target) - 1,
            ),
        )
        lower = frames[lower_index]
        upper = frames[lower_index + 1]
        duration = frame_times[lower_index + 1] - frame_times[lower_index]
        ratio = 1.0 if duration <= 1e-9 else (
            (target - frame_times[lower_index]) / duration
        )
        position = tuple(
            float(lower[axis]) + (float(upper[axis]) - float(lower[axis])) * ratio
            for axis in ("x", "y", "z")
        )
        return lower_index, position, False

    @staticmethod
    def _exact_timeline_position(timeline, target_time_ms):
        times = timeline["times"]
        target = float(target_time_ms)
        if target <= times[0]:
            return 0, tuple(timeline[axis][0] for axis in ("x", "y", "z")), False
        if target >= times[-1]:
            last = len(times) - 1
            return (
                last,
                tuple(timeline[axis][last] for axis in ("x", "y", "z")),
                True,
            )
        lower_index = max(
            0,
            min(len(times) - 2, bisect.bisect_right(times, target) - 1),
        )
        upper_index = lower_index + 1
        duration = times[upper_index] - times[lower_index]
        ratio = 1.0 if duration <= 1e-9 else (
            (target - times[lower_index]) / duration
        )
        position = tuple(
            timeline[axis][lower_index]
            + (timeline[axis][upper_index] - timeline[axis][lower_index]) * ratio
            for axis in ("x", "y", "z")
        )
        return lower_index, position, False

    def _advance_simulation(self):
        state = self.simulation_state
        if not state:
            return

        group_view = getattr(state.get("group"), "ViewObject", None)
        if group_view is not None and hasattr(group_view, "Visibility"):
            if not bool(group_view.Visibility):
                self._stop_simulation_timer()
                self._set_simulation_running(False)
                self.simulation_state = None
                return

        target_time_ms = (time.monotonic() - state["start_time"]) * 1000.0
        index = state["index"]

        if state.get("exact_static_path", False):
            index, position, finished = self._exact_timeline_position(
                state["exact_timeline"],
                target_time_ms,
            )
            self._set_simulation_cutter_pose(
                state,
                position,
                target_time_ms,
            )
            state["index"] = index
            if finished:
                self._stop_simulation_timer()
                self._set_simulation_running(False)
                self.simulation_state = None
                self._restore_simulation_path_visibility(state["doc"])
                state["doc"].recompute()
            return

        frames = state["frames"]

        if index >= len(frames) - 1:
            self._stop_simulation_timer()
            self._set_simulation_running(False)
            self.simulation_state = None
            self._restore_simulation_path_visibility(state["doc"])
            state["doc"].recompute()
            return

        advanced = 0
        changed_trails = set()
        while index < len(frames) - 1 and frames[index + 1]["time_ms"] <= target_time_ms and advanced < 100:
            previous = frames[index]
            current = frames[index + 1]
            index += 1
            advanced += 1

            self._set_simulation_cutter_pose(
                state,
                (current["x"], current["y"], current["z"]),
                target_time_ms,
            )

            start = (previous["x"], previous["y"], previous["z"])
            end = (current["x"], current["y"], current["z"])
            if (
                not state.get("exact_static_path", False)
                and math.dist(start, end) > 1e-6
            ):
                segment = (start, end)
                if current["type"] == "rapid":
                    state["rapid_edges"].append(segment)
                    changed_trails.add("rapid")
                elif current["type"] in (
                    "feed_plunge",
                    "feed_drill",
                    "feed_ramp",
                    "feed_helix",
                ):
                    state["ramp_edges"].append(segment)
                    changed_trails.add("ramp")
                else:
                    state["cut_edges"].append(segment)
                    changed_trails.add("cut")

        for trail in changed_trails:
            self._update_trail_shape(
                state[trail + "_obj"],
                state[trail + "_edges"],
            )

        state["index"] = index

        if index >= len(frames) - 1:
            self._stop_simulation_timer()
            self._set_simulation_running(False)
            self.simulation_state = None
            self._restore_simulation_path_visibility(state["doc"])
            state["doc"].recompute()
            return

        # Coin3D recebe os novos buffers diretamente; o ciclo normal do Qt
        # repinta a vista. Não force recompute nem updateGui em cada tick.

    def _show_preview(self, settings, moves):
        doc = FreeCAD.ActiveDocument
        if doc is None:
            raise RuntimeError("Nenhum documento ativo do FreeCAD encontrado para mostrar a pré-visualização.")

        self._clear_existing_preview(doc)
        is_3d = settings.get("operation_mode") in {"rough3d", "finish3d"}
        uses_lightweight_overlay = self._uses_lightweight_toolpath_overlay(settings)
        # Em Corte e 3D, o percurso completo vai direto ao scene graph Coin/GPU.
        # Assim a forma continua legível sem reduzir a precisão do percurso ou
        # do G-code, nem criar a projeção vermelha antiga no documento.
        display_moves = moves_for_preview(settings, moves) if is_3d else moves
        components = self._toolpath_components(
            display_moves,
            max_display_segments=None if uses_lightweight_overlay else 4000,
        )
        preview_group = doc.addObject("App::DocumentObjectGroup", "WoodCAM2D_Preview")
        preview_group.Label = "Prévia CAM"
        ensure_woodcam_tree(doc).work_area.addObject(preview_group)
        geometry = self._selected_geometry_for_preview()
        self._add_work_area_preview(doc, preview_group, settings)
        # Fonte e percurso permanecem no XY do documento. O datum não cria
        # cópia deslocada da geometria; ele aparece somente no movimento
        # inicial/retorno e no indicador da área de trabalho.
        shifted_reference = None
        if is_3d:
            keep_visible = ()
            if settings.get("_geometry_source") == "mesh_3d":
                keep_visible = (settings.get("_mesh_source_object", ""),)
            self._hide_selected_sources_for_preview(
                doc,
                keep_visible=keep_visible,
            )
        self._add_material_datum_preview(
            doc,
            preview_group,
            settings,
            geometry if settings.get("use_selection_bounds_origin", False) else {},
        )
        if is_3d:
            # O modelo-fonte 3D permanece nas coordenadas originais do
            # documento. Os movimentos, por outro lado, já foram convertidos
            # para o XY da máquina pelo datum. Deslocar apenas esta projeção
            # Coin pelo inverso do placement mantém ferramenta e modelo
            # coincidentes sem alterar a fonte, a operação persistida ou o
            # G-code.
            placement_x, placement_y = settings.get(
                "_xy_origin_offset", (0.0, 0.0)
            )
            components = self._shift_toolpath_components_xy(
                components,
                -float(placement_x or 0.0),
                -float(placement_y or 0.0),
            )
            self._set_3d_toolpath_overlay(settings, components)
        elif uses_lightweight_overlay:
            self._set_lightweight_toolpath_overlay(components)
        else:
            self._add_toolpath_objects(
                doc,
                preview_group,
                components,
                "Preview",
                settings["tool_diameter"],
                operation_mode=settings.get("operation_mode"),
            )

        doc.recompute()
        if FreeCADGui.ActiveDocument:
            # Em 3D, fitAll inclui mesa, datums e deslocamentos seguros; isso
            # afastava a câmera até o relevo virar um pequeno retângulo. A
            # câmera do usuário é preservada. Operações 2D mantêm o fit atual.
            if not is_3d:
                FreeCADGui.ActiveDocument.ActiveView.fitAll()

    def _set_3d_toolpath_overlay(self, settings, components):
        doc = FreeCAD.ActiveDocument
        keep_visible = (str(settings.get("_mesh_source_object", "") or ""),)
        self._hide_selected_sources_for_preview(doc, keep_visible=keep_visible)
        # Evita o realce de seleção sobre centenas de milhares de facetas; a
        # fonte continua visível e sombreada, apenas deixa de ser repintada
        # como seleção pelo FreeCAD.
        try:
            FreeCADGui.Selection.clearSelection()
        except Exception:
            pass
        self._preview_3d_source_name = keep_visible[0]
        overlay = getattr(self, "_toolpath_overlay", None)
        if overlay is None:
            overlay = CoinToolpathOverlay(FreeCADGui.ActiveDocument)
        overlay.update(components)
        self._toolpath_overlay = overlay

    def _set_lightweight_toolpath_overlay(self, components):
        """Atualiza somente o desenho Coin do percurso, sem tocar na fonte 2D."""
        overlay = getattr(self, "_toolpath_overlay", None)
        if overlay is None:
            overlay = CoinToolpathOverlay(FreeCADGui.ActiveDocument)
        overlay.update(components)
        self._toolpath_overlay = overlay


_DIALOG_INSTANCE = None


def show_woodcam2d_dialog(parent=None):
    global _DIALOG_INSTANCE

    if parent is None:
        try:
            parent = FreeCADGui.getMainWindow()
        except Exception:
            parent = None
    if parent is not None and hasattr(parent, "centralWidget"):
        try:
            central_widget = parent.centralWidget()
            if central_widget is not None:
                parent = central_widget
        except Exception:
            pass

    if _DIALOG_INSTANCE is None:
        _DIALOG_INSTANCE = WoodCAM2DDialog(parent)

    _DIALOG_INSTANCE.show_config_dialog()
    return _DIALOG_INSTANCE
