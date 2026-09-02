"""Comando 'Layout CNC Formas' do PanelNest.

Gera layout de corte CNC usando o contorno real das peças (não apenas bounding box).
Usa nesting por rasterização para aproveitar melhor o espaço com formas irregulares
(círculos, L-shapes, curvas, etc.).

Este comando é independente do 'Gerar Layout' padrão — não interfere com ele.
"""

import os

try:
    import FreeCAD as App
except ImportError:
    App = None

try:
    import FreeCADGui as Gui
except ImportError:
    Gui = None

try:
    import Part as PartModule
except ImportError:
    PartModule = None

try:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui
except ImportError:
    try:
        from PySide2 import QtCore, QtWidgets
    except ImportError:
        from PySide6 import QtCore, QtWidgets

COMMAND_NAME = "PanelNest_GenerateShapeLayout"
SHAPE_LAYOUT_ROOT_NAME = "PanelNestShapeLayout"


def _main_window():
    if Gui is not None and hasattr(Gui, "getMainWindow"):
        try:
            return Gui.getMainWindow()
        except Exception:
            return None
    return None


def _exec_dialog(dialog):
    if hasattr(dialog, "exec_"):
        return dialog.exec_()
    return dialog.exec()


class _ShapeLayoutDialog(QtWidgets.QDialog):
    """Dialog de configuração para o layout por formas."""

    def __init__(self, part_count, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PanelNest — Layout CNC por Formas")
        self.setMinimumWidth(420)
        self._build_ui(part_count)

    def _build_ui(self, part_count):
        layout = QtWidgets.QVBoxLayout(self)

        info = QtWidgets.QLabel(
            f"<b>{part_count}</b> peca(s) encontrada(s).<br><br>"
            "Este layout usa o contorno real de cada peca para encaixar "
            "formas irregulares com melhor aproveitamento.<br>"
            "Ideal para CNC router — nao altera o layout retangular existente."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QtWidgets.QFormLayout()

        self._length_spin = QtWidgets.QSpinBox()
        self._length_spin.setRange(100, 5000)
        self._length_spin.setValue(2750)
        self._length_spin.setSuffix(" mm")
        form.addRow("Comprimento da chapa:", self._length_spin)

        self._width_spin = QtWidgets.QSpinBox()
        self._width_spin.setRange(100, 3000)
        self._width_spin.setValue(1830)
        self._width_spin.setSuffix(" mm")
        form.addRow("Largura da chapa:", self._width_spin)

        self._thickness_spin = QtWidgets.QSpinBox()
        self._thickness_spin.setRange(1, 100)
        self._thickness_spin.setValue(18)
        self._thickness_spin.setSuffix(" mm")
        form.addRow("Espessura:", self._thickness_spin)

        self._margin_spin = QtWidgets.QSpinBox()
        self._margin_spin.setRange(0, 100)
        self._margin_spin.setValue(5)
        self._margin_spin.setSuffix(" mm")
        form.addRow("Margem da borda:", self._margin_spin)

        self._spacing_spin = QtWidgets.QSpinBox()
        self._spacing_spin.setRange(0, 50)
        self._spacing_spin.setValue(3)
        self._spacing_spin.setSuffix(" mm")
        form.addRow("Espacamento entre pecas:", self._spacing_spin)

        self._resolution_combo = QtWidgets.QComboBox()
        self._resolution_combo.addItem("2 mm (preciso, mais lento)", 2.0)
        self._resolution_combo.addItem("3 mm (rapido, boa precisao)", 3.0)
        self._resolution_combo.addItem("5 mm (muito rapido)", 5.0)
        self._resolution_combo.setCurrentIndex(1)
        form.addRow("Resolucao:", self._resolution_combo)

        self._rotation_check = QtWidgets.QCheckBox("Permitir rotacao (0, 90, 180, 270)")
        self._rotation_check.setChecked(True)
        form.addRow(self._rotation_check)

        self._material_edit = QtWidgets.QLineEdit()
        self._material_edit.setPlaceholderText("Ex: MDF 18mm")
        form.addRow("Material:", self._material_edit)

        layout.addLayout(form)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.button(QtWidgets.QDialogButtonBox.Ok).setText("Gerar Layout")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def payload(self):
        return {
            "length_mm": self._length_spin.value(),
            "width_mm": self._width_spin.value(),
            "thickness_mm": self._thickness_spin.value(),
            "margin_mm": self._margin_spin.value(),
            "spacing_mm": self._spacing_spin.value(),
            "resolution_mm": self._resolution_combo.currentData(),
            "allow_rotation": self._rotation_check.isChecked(),
            "material": self._material_edit.text().strip(),
        }


def _collect_shape_parts(doc):
    """Coleta peças com shapes válidas do documento.

    Retorna lista de dicts: {obj, part_id, label, shape, length_mm, width_mm, thickness_mm}
    """
    from panelnest.geometry import (
        _is_candidate_object,
        _object_dimensions,
    )
    from panelnest.freecad_utils import _is_internal_object

    parts = []
    seen = set()

    # TypeIds que são containers e NÃO peças
    _container_types = {
        "App::Part", "App::DocumentObjectGroup", "App::Link",
        "App::LinkGroup", "App::Origin", "App::Plane", "App::Line",
    }

    for obj in doc.Objects:
        name = getattr(obj, "Name", "")
        if name in seen:
            continue
        type_id = getattr(obj, "TypeId", "") or ""
        if type_id in _container_types or type_id.startswith("Assembly::"):
            continue
        if _is_internal_object(obj):
            continue
        if not _is_candidate_object(obj):
            continue
        seen.add(name)

        try:
            length_mm, width_mm, thickness_mm = _object_dimensions(obj)
        except Exception:
            continue

        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue

        label = str(getattr(obj, "PanelNestLabel", "") or getattr(obj, "Label", "") or name)
        part_id = name  # obj.Name é sempre único no FreeCAD

        qty = 1
        try:
            qty = int(getattr(obj, "PanelNestQuantity", 1) or 1)
        except Exception:
            pass

        parts.append({
            "obj": obj,
            "obj_name": name,
            "part_id": part_id,
            "label": label,
            "shape": shape,
            "length_mm": length_mm,
            "width_mm": width_mm,
            "thickness_mm": thickness_mm,
            "quantity": max(1, qty),
        })

    return parts


def _build_masks(shape_parts, resolution_mm=1.0, progress_fn=None):
    """Constrói máscaras raster para todas as peças.

    Retorna lista de (RasterMask, part_info) — expandida por quantidade.
    progress_fn: callback(current, total, label) — chamado por peça.
    """
    import math
    from panelnest.raster_nesting import RasterMask, extract_contour_from_shape

    masks = []
    total = len(shape_parts)

    for pi_idx, part_info in enumerate(shape_parts):
        if progress_fn is not None:
            result = progress_fn(pi_idx, total, part_info.get("label", ""))
            if result is False:
                break
        shape = part_info["shape"]

        # Calcular dimensões do plano a partir do BoundBox
        bbox = shape.BoundBox
        dims = {
            "x": bbox.XLength, "y": bbox.YLength, "z": bbox.ZLength,
        }
        t_axis = min(dims, key=dims.get)
        plane_axes = sorted([a for a in ("x", "y", "z") if a != t_axis])
        ax0, ax1 = plane_axes
        plane_len = dims[ax0]
        plane_wid = dims[ax1]

        # Peça retangular? Usar mask 100% preenchida (mais seguro e rápido)
        bbox_vol = dims["x"] * dims["y"] * dims["z"]
        fill_ratio = shape.Volume / bbox_vol if bbox_vol > 0 else 1.0
        is_rectangular = fill_ratio > 0.95

        if is_rectangular:
            result = None  # Forçar fallback retangular
        else:
            result = extract_contour_from_shape(shape, resolution_mm)

        if result is None:
            cols = max(1, int(math.ceil(plane_len / resolution_mm)))
            rows = max(1, int(math.ceil(plane_wid / resolution_mm)))
            grid = [[True] * cols for _ in range(rows)]
            area_pixels = rows * cols
        else:
            grid, rows, cols, area_pixels = result

        for q in range(part_info["quantity"]):
            suffix = f"#{q + 1}" if part_info["quantity"] > 1 else ""
            mask = RasterMask(
                part_id=f"{part_info['part_id']}{suffix}",
                grid=grid,
                rows=rows,
                cols=cols,
                area_pixels=area_pixels,
                resolution_mm=resolution_mm,
            )
            masks.append((mask, part_info))

    return masks


def _create_shape_layout_model(doc, raster_sheets, shape_parts, resolution_mm):
    """Cria o modelo 3D do layout no FreeCAD.

    Gera uma hierarquia separada (PanelNestShapeLayout) sem tocar no layout existente.
    """
    from panelnest.freecad_utils import _mark_internal_object

    # Remover layout anterior se existir
    existing = doc.getObject(SHAPE_LAYOUT_ROOT_NAME)
    if existing is not None:
        _remove_tree(doc, existing)

    root = doc.addObject("App::Part", SHAPE_LAYOUT_ROOT_NAME)
    root.Label = "Layout CNC Formas"
    _mark_internal_object(root, "shape_layout_root")

    # Mapa de objetos por part_id base
    obj_map = {}
    for pi in shape_parts:
        obj_map[pi["part_id"]] = pi

    sheet_offset_x = 0.0

    for rsheet in raster_sheets:
        sheet_container = doc.addObject("App::Part", f"PanelNestShapeChapa{rsheet.sheet_index:02d}")
        sheet_container.Label = (
            f"Chapa {rsheet.sheet_index} | "
            f"{rsheet.length_mm:.0f} x {rsheet.width_mm:.0f} | "
            f"{rsheet.utilization_pct:.1f}% aproveitamento"
        )
        sheet_container.Placement.Base = App.Vector(sheet_offset_x, 0, 0)
        _mark_internal_object(sheet_container, "shape_layout_sheet")
        root.addObject(sheet_container)

        # Base da chapa (fina, apenas visual)
        sheet_h = 1.0
        sheet_box = doc.addObject("Part::Box", f"PanelNestShapeBase{rsheet.sheet_index:02d}")
        sheet_box.Length = rsheet.length_mm
        sheet_box.Width = rsheet.width_mm
        sheet_box.Height = sheet_h
        sheet_box.Label = f"Base Chapa {rsheet.sheet_index}"
        _mark_internal_object(sheet_box, "shape_layout_sheet_base")
        sheet_container.addObject(sheet_box)

        # Estilo da base
        try:
            sv = sheet_box.ViewObject
            sv.ShapeColor = (0.82, 0.85, 0.88)
            sv.LineColor = (0.4, 0.4, 0.4)
            sv.Transparency = 0
        except Exception:
            pass

        # Peças ficam EM CIMA da chapa base (z = sheet_h)
        piece_h = 1.0
        z_base = sheet_h

        # Peças
        for pidx, placement in enumerate(rsheet.placements, start=1):
            base_pid = placement.part_id.split("#")[0]
            part_info = obj_map.get(base_pid)

            contour_obj = None
            if part_info is not None:
                # Tentar contorno real (só para peças não retangulares)
                contour_obj = _create_contour_piece(
                    doc, part_info["shape"], placement, rsheet, pidx, resolution_mm
                )

            if contour_obj is not None:
                _mark_internal_object(contour_obj, "shape_layout_part")
                sheet_container.addObject(contour_obj)
                try:
                    cv = contour_obj.ViewObject
                    cv.ShapeColor = _part_color(pidx)
                    cv.LineColor = _darken(cv.ShapeColor, 0.3)
                    cv.Transparency = 5
                except Exception:
                    pass
                label_text = part_info["label"] if part_info else placement.part_id
                contour_obj.Label = f"{placement.part_id} - {label_text}"
            else:
                # Part::Box — limpo e confiável para peças retangulares
                box = doc.addObject("Part::Box", f"PanelNestShapePeca{rsheet.sheet_index:02d}_{pidx:03d}")
                box.Length = placement.bbox_length_mm
                box.Width = placement.bbox_width_mm
                box.Height = piece_h
                box.Placement.Base = App.Vector(placement.x_mm, placement.y_mm, z_base)
                label_text = part_info["label"] if part_info else placement.part_id
                box.Label = f"{placement.part_id} - {label_text}"
                _mark_internal_object(box, "shape_layout_part")
                sheet_container.addObject(box)
                try:
                    bv = box.ViewObject
                    bv.ShapeColor = _part_color(pidx)
                    bv.LineColor = _darken(bv.ShapeColor, 0.3)
                    bv.Transparency = 5
                except Exception:
                    pass

        sheet_offset_x += rsheet.length_mm + 50.0  # Gap entre chapas

    doc.recompute()
    return root


def _create_contour_piece(doc, source_shape, placement, rsheet, pidx, resolution_mm):
    """Cria sólido a partir do contorno real para peças NÃO retangulares.

    Retorna None para peças retangulares (fill_ratio > 0.95) — caller usa Part::Box.
    """
    if PartModule is None:
        return None

    try:
        bbox = source_shape.BoundBox
        dims = {
            "x": bbox.XLength, "y": bbox.YLength, "z": bbox.ZLength,
        }

        # Peça retangular? Pula — Part::Box é mais limpo e rápido
        bbox_vol = dims["x"] * dims["y"] * dims["z"]
        if bbox_vol > 0:
            fill = source_shape.Volume / bbox_vol
            if fill > 0.95:
                return None

        t_axis = min(dims, key=dims.get)
        plane_axes = sorted([ax for ax in ("x", "y", "z") if ax != t_axis])
        ax0, ax1 = plane_axes

        origin = {
            "x": bbox.XMin, "y": bbox.YMin, "z": bbox.ZMin,
        }

        # Encontrar face superior
        from panelnest.raster_nesting import _find_top_face
        top_face = _find_top_face(source_shape, t_axis)
        if top_face is None:
            return None

        wires = list(getattr(top_face, "Wires", []) or [])
        if not wires:
            return None
        outer_wire = max(wires, key=lambda w: w.Length)

        # Discretizar o wire INTEIRO de uma vez (resolve o problema de edges
        # invertidas que causavam wireframe bagunçado)
        n_pts = max(24, int(outer_wire.Length / 2.0))
        try:
            raw_pts = outer_wire.discretize(Number=n_pts)
        except Exception:
            return None

        if len(raw_pts) < 3:
            return None

        z_base = 1.0  # Sobre a chapa base de 1mm
        w0 = dims[ax0]
        w1 = dims[ax1]

        # Transformar para coordenadas do layout
        transformed = []
        for pt in raw_pts:
            lx = float(getattr(pt, ax0, 0.0)) - origin[ax0]
            ly = float(getattr(pt, ax1, 0.0)) - origin[ax1]

            if placement.rotation_deg == 90:
                lx, ly = ly, w0 - lx
            elif placement.rotation_deg == 180:
                lx, ly = w0 - lx, w1 - ly
            elif placement.rotation_deg == 270:
                lx, ly = w1 - ly, lx

            transformed.append(App.Vector(
                placement.x_mm + lx,
                placement.y_mm + ly,
                z_base,
            ))

        # Construir wire fechado a partir dos pontos consecutivos
        edges = []
        n = len(transformed)
        for i in range(n):
            p1 = transformed[i]
            p2 = transformed[(i + 1) % n]
            if p1.distanceToPoint(p2) > 0.05:
                edges.append(PartModule.LineSegment(p1, p2).toShape())

        if len(edges) < 3:
            return None

        wire = PartModule.Wire(edges)
        face = PartModule.Face(wire)
        # Extrusão fina (1mm) para visualização limpa
        solid = face.extrude(App.Vector(0, 0, 1.0))

        obj_name = f"PanelNestShapePeca{rsheet.sheet_index:02d}_{pidx:03d}"
        feat = doc.addObject("Part::Feature", obj_name)
        feat.Shape = solid
        return feat

    except Exception:
        return None


def _hide_source_objects(doc):
    """Esconde todos os objetos do documento que não são do shape layout.

    Itera em DUAS passadas: primeiro esconde containers de topo (App::Part),
    depois todos os restantes. Chama Gui.updateGui() entre passadas.
    """
    if Gui is None or doc is None:
        return

    layout_names = set()
    for obj in doc.Objects:
        name = getattr(obj, "Name", "") or ""
        if name.startswith("PanelNestShape"):
            layout_names.add(name)

    # Passada 1: containers de topo (esconder estes esconde os filhos na viewport)
    for obj in doc.Objects:
        name = getattr(obj, "Name", "") or ""
        if name in layout_names:
            continue
        type_id = getattr(obj, "TypeId", "") or ""
        if type_id not in ("App::Part", "App::DocumentObjectGroup") and \
           not type_id.startswith("Assembly::"):
            continue
        try:
            obj.ViewObject.Visibility = False
        except Exception:
            pass

    # Passada 2: tudo o resto que não é layout
    for obj in doc.Objects:
        name = getattr(obj, "Name", "") or ""
        if name in layout_names:
            continue
        try:
            vo = getattr(obj, "ViewObject", None)
            if vo is not None and hasattr(vo, "Visibility"):
                vo.Visibility = False
        except Exception:
            pass

    # Forçar atualização da GUI
    try:
        Gui.updateGui()
    except Exception:
        pass


def _remove_tree(doc, obj):
    """Remove objeto e todos os filhos."""
    for child in list(getattr(obj, "OutList", []) or []):
        _remove_tree(doc, child)
    try:
        doc.removeObject(obj.Name)
    except Exception:
        pass


def _part_color(index):
    """Retorna cor para a peça baseada no índice."""
    palette = [
        (0.85, 0.55, 0.50), (0.50, 0.72, 0.55), (0.52, 0.60, 0.82),
        (0.82, 0.70, 0.45), (0.65, 0.50, 0.75), (0.48, 0.75, 0.75),
        (0.78, 0.55, 0.68), (0.60, 0.78, 0.45), (0.72, 0.62, 0.52),
        (0.55, 0.65, 0.72),
    ]
    return palette[index % len(palette)]


def _darken(color, amount):
    return tuple(max(0, c - amount) for c in color)


# ---------------------------------------------------------------------------
# Comando FreeCAD
# ---------------------------------------------------------------------------

class GenerateShapeLayoutCommand:
    def Activated(self):
        if App is None or App.ActiveDocument is None:
            return

        doc = App.ActiveDocument

        # Coletar peças
        shape_parts = _collect_shape_parts(doc)
        if not shape_parts:
            QtWidgets.QMessageBox.warning(
                _main_window(), "PanelNest",
                "Nenhuma peca com forma valida encontrada no documento."
            )
            return

        # Carregar defaults do settings se disponível
        try:
            import panelnest
            settings = panelnest.get_sheet_settings()
        except Exception:
            settings = None

        # Dialog
        dlg = _ShapeLayoutDialog(len(shape_parts), _main_window())

        # Preencher com settings se disponível
        if settings is not None:
            try:
                dlg._length_spin.setValue(int(settings.length_mm))
                dlg._width_spin.setValue(int(settings.width_mm))
                dlg._margin_spin.setValue(int(settings.margin_mm))
                dlg._spacing_spin.setValue(int(settings.spacing_mm))
                dlg._material_edit.setText(settings.full_sheet_material or "")
                if settings.full_sheet_thickness_mm > 0:
                    dlg._thickness_spin.setValue(int(settings.full_sheet_thickness_mm))
            except Exception:
                pass

        if _exec_dialog(dlg) != QtWidgets.QDialog.Accepted:
            return

        payload = dlg.payload()

        # Contar total de peças com quantidade
        total_pieces = sum(p["quantity"] for p in shape_parts)

        # Progress dialog
        progress = QtWidgets.QProgressDialog(
            "Rasterizando pecas...", "Cancelar", 0, total_pieces + 2, _main_window()
        )
        progress.setWindowTitle("PanelNest — Layout CNC Formas")
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setMinimumWidth(400)
        progress.setValue(0)

        _cancelled = [False]

        def _on_mask_progress(current, total, label):
            """Callback durante construção de masks."""
            if progress.wasCanceled():
                _cancelled[0] = True
                return False
            progress.setLabelText(f"Rasterizando peca {current}/{total}: {label}")
            progress.setValue(current)
            QtCore.QCoreApplication.processEvents()
            return None

        def _on_nest_progress(current, total, part_id):
            """Callback durante posicionamento."""
            if progress.wasCanceled():
                _cancelled[0] = True
                return False
            progress.setLabelText(f"Posicionando peca {current + 1}/{total}: {part_id}")
            progress.setValue(current + 1)
            QtCore.QCoreApplication.processEvents()
            return None

        try:
            # Construir máscaras com progresso
            masks_with_info = _build_masks(
                shape_parts, payload["resolution_mm"],
                progress_fn=_on_mask_progress,
            )
            masks = [m for m, _ in masks_with_info]

            if _cancelled[0]:
                progress.close()
                return

            if not masks:
                progress.close()
                QtWidgets.QMessageBox.warning(
                    _main_window(), "PanelNest",
                    "Nao foi possivel criar mascaras para as pecas."
                )
                return

            progress.setMaximum(len(masks) + 2)
            progress.setLabelText(f"Posicionando {len(masks)} peca(s)...")
            progress.setValue(0)
            QtCore.QCoreApplication.processEvents()

            # Nesting com progresso
            from panelnest.raster_nesting import raster_nest_multi_sheet

            raster_sheets = raster_nest_multi_sheet(
                masks,
                sheet_length_mm=payload["length_mm"],
                sheet_width_mm=payload["width_mm"],
                thickness_mm=payload["thickness_mm"],
                material=payload["material"],
                margin_mm=payload["margin_mm"],
                spacing_mm=payload["spacing_mm"],
                resolution_mm=payload["resolution_mm"],
                allow_rotation=payload["allow_rotation"],
                progress_fn=_on_nest_progress,
            )

            if _cancelled[0]:
                progress.close()
                return

            if not raster_sheets:
                progress.close()
                QtWidgets.QMessageBox.warning(
                    _main_window(), "PanelNest",
                    "Nao foi possivel encaixar nenhuma peca na chapa."
                )
                return

            progress.setLabelText("Criando modelo 3D...")
            progress.setValue(len(masks))
            QtCore.QCoreApplication.processEvents()

            # Criar modelo
            root = _create_shape_layout_model(
                doc, raster_sheets, shape_parts, payload["resolution_mm"]
            )

            progress.setLabelText("Ajustando vista...")
            progress.setValue(len(masks) + 1)

            # Esconder objetos originais
            _hide_source_objects(doc)

            # Garantir que o layout está visível
            try:
                root.ViewObject.Visibility = True
                for child in root.OutList:
                    try:
                        child.ViewObject.Visibility = True
                    except Exception:
                        pass
                    for grandchild in getattr(child, "OutList", []):
                        try:
                            grandchild.ViewObject.Visibility = True
                        except Exception:
                            pass
            except Exception:
                pass

            # Ajustar vista: ortogonal top-down ANTES do MessageBox
            if Gui is not None:
                try:
                    view = Gui.ActiveDocument.ActiveView
                    view.setCameraType("Orthographic")
                except Exception:
                    pass
                try:
                    view = Gui.ActiveDocument.ActiveView
                    view.viewTop()
                    view.fitAll()
                except Exception:
                    pass
                try:
                    Gui.updateGui()
                except Exception:
                    pass

            progress.close()

            total_placed = sum(len(s.placements) for s in raster_sheets)
            total_unplaced = len(masks) - total_placed
            avg_util = sum(s.utilization_pct for s in raster_sheets) / len(raster_sheets)

            msg = (
                f"Layout gerado: {total_placed} peca(s) em {len(raster_sheets)} chapa(s).\n"
                f"Aproveitamento medio: {avg_util:.1f}%"
            )
            if total_unplaced > 0:
                msg += f"\n{total_unplaced} peca(s) nao couberam."

            App.Console.PrintMessage(f"PanelNest: {msg}\n")
            QtWidgets.QMessageBox.information(_main_window(), "PanelNest", msg)

        except Exception as exc:
            progress.close()
            QtWidgets.QMessageBox.critical(
                _main_window(), "Erro no Layout CNC Formas", str(exc)
            )

    def IsActive(self):
        return App is not None and App.ActiveDocument is not None

    def GetResources(self):
        return {
            "Pixmap": os.path.join(
                os.path.dirname(__file__), "..", "resources", "icons",
                "panelnest_layout.svg"
            ),
            "Accel": "",
            "MenuText": "Layout CNC Formas",
            "ToolTip": (
                "Gera layout de corte CNC usando o contorno real das pecas.\n"
                "Ideal para formas irregulares (circulos, curvas, L-shapes).\n"
                "Nao altera o layout retangular existente."
            ),
        }


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, GenerateShapeLayoutCommand())
