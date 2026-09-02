"""Aplicar material visual às peças selecionadas.

Ao contrário do material nativo do FreeCAD (que só muda ShapeColor),
este comando atualiza também DiffuseColor e os campos internos do
PanelNest para que a fita de borda continue funcionando corretamente
após a mudança de material.
"""
import os
import json

try:
    import FreeCAD as App
except ImportError:
    App = None

try:
    import FreeCADGui as Gui
except ImportError:
    Gui = None

try:
    from PySide import QtGui, QtCore
    QtWidgets = QtGui
except ImportError:
    try:
        from PySide2 import QtWidgets, QtCore, QtGui
    except ImportError:
        from PySide6 import QtWidgets, QtCore, QtGui

COMMAND_NAME = "PanelNest_ApplyMaterial"


def _follow_link(obj):
    """Retorna o objeto real atrás de App::Link / App::LinkElement, ou obj se não for link."""
    type_id = getattr(obj, "TypeId", "") or ""
    if type_id in ("App::Link", "App::LinkElement"):
        linked = getattr(obj, "LinkedObject", None)
        if linked is not None and linked is not obj:
            return linked
    return obj


def _resolve_selection(objects):
    """Resolve a lista de objetos selecionados para pares (visual_obj, body).

    visual_obj: o Tip/Pad do Body — quem tem geometria no ViewObject.RootNode do Coin3D.
    body: PartDesign::Body — onde PanelNestMaterial/GrainRotated são armazenados.

    Por que não aplicar ao Body diretamente:
      Body.ViewObject.RootNode não contém os nós de geometria do Coin3D.
      A geometria está no Tip feature (último Pad/Pocket), que é o que o FreeCAD renderiza.
      SoTexture2 no RootNode do Body não tem geometria abaixo para texturizar.
    """
    result = []
    visited = set()
    for obj in objects:
        _collect_visual_body_pairs(obj, result, visited, 0)
    return result


def _collect_visual_body_pairs(obj, result, visited, depth):
    if depth > 8:
        return
    obj_id = id(obj)
    if obj_id in visited:
        return
    visited.add(obj_id)

    # Seguir App::Link → objeto real
    real = _follow_link(obj)
    if real is not obj:
        _collect_visual_body_pairs(real, result, visited, depth + 1)
        return

    type_id = getattr(obj, "TypeId", "") or ""

    # Body PartDesign: usar o Tip feature como visual_obj
    if type_id == "PartDesign::Body":
        tip = getattr(obj, "Tip", None)
        visual = tip if tip is not None else obj
        result.append((visual, obj))
        return

    # Pad/Feature filho de Body: usar diretamente como visual_obj
    if type_id.startswith("PartDesign::") and type_id not in ("PartDesign::Body",):
        body = None
        for parent in getattr(obj, "InList", []):
            if getattr(parent, "TypeId", "") == "PartDesign::Body":
                body = parent
                break
        result.append((obj, body if body is not None else obj))
        return

    # Sketcher: ignorar (não tem geometria 3D)
    if type_id.startswith("Sketcher::"):
        return

    # Part::Box / Part::Cut / Part::Feature etc: têm geometria 3D
    # diretamente — aplica como visual_obj=body=obj.
    if type_id.startswith("Part::") and hasattr(obj, "Shape"):
        result.append((obj, obj))
        return

    # Container (Assembly, App::Part, App::DocumentObjectGroup, etc.): descer
    for child in getattr(obj, "OutList", []):
        _collect_visual_body_pairs(child, result, visited, depth + 1)


def _main_window():
    if Gui is not None and hasattr(Gui, "getMainWindow"):
        try:
            return Gui.getMainWindow()
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# Lógica de aplicação (separada da UI para facilitar testes)
# ---------------------------------------------------------------------------

def apply_material_to_object(obj, color_rgb: tuple) -> bool:
    """Aplica cor material ao objeto respeitando o estado da fita de borda.

    Retorna True se aplicado com sucesso.

    O problema que resolve:
      Quando a fita de borda está ativa, o objeto tem DiffuseColor com cores
      por face (faces com fita = cor da fita, outras = cor base).
      O FreeCAD nativo só muda ShapeColor, que fica ignorado pelo DiffuseColor.
      Além disso, PanelNestVisualBaseShapeColorJson fica desatualizado e na
      próxima renderização da fita a cor antiga é restaurada.

    Esta função:
      1. Seta ShapeColor para a nova cor
      2. Se DiffuseColor está ativo (fita aplicada), atualiza as faces
         que não são de fita para a nova cor
      3. Atualiza PanelNestVisualBaseShapeColorJson e
         PanelNestVisualBaseDiffuseColorsJson para a nova cor base
    """
    view_obj = getattr(obj, "ViewObject", None)
    if view_obj is None:
        return False

    r, g, b = float(color_rgb[0]), float(color_rgb[1]), float(color_rgb[2])
    new_color = (r, g, b)

    # 1. ShapeColor
    try:
        if hasattr(view_obj, "ShapeColor"):
            view_obj.ShapeColor = new_color
    except Exception:
        pass

    # 2. DiffuseColor — setar uniformemente para a nova cor.
    # As cores de fita/corte serão reaaplicadas depois por
    # refresh_part_edge_band_visuals sobre esta base uniforme.
    try:
        if hasattr(view_obj, "DiffuseColor"):
            face_count = 1
            try:
                face_count = max(1, len(list(view_obj.DiffuseColor)))
            except Exception:
                pass
            view_obj.DiffuseColor = tuple([new_color] * face_count)
    except Exception:
        pass

    # 3. Atualiza a cor base salva (usada por refresh_part_edge_band_visuals
    # como "fundo" antes de pintar as faces com fita/corte).
    try:
        if hasattr(obj, "PanelNestVisualBaseShapeColorJson"):
            obj.PanelNestVisualBaseShapeColorJson = json.dumps([r, g, b])
    except Exception:
        pass

    try:
        if hasattr(obj, "PanelNestVisualBaseDiffuseColorsJson"):
            saved_json = getattr(obj, "PanelNestVisualBaseDiffuseColorsJson", "") or ""
            if saved_json.strip():
                try:
                    saved = json.loads(saved_json)
                    updated = [[r, g, b] for _ in saved]
                    obj.PanelNestVisualBaseDiffuseColorsJson = json.dumps(updated)
                except Exception:
                    pass
    except Exception:
        pass

    return True


def _colors_similar(a: tuple, b: tuple, tolerance: float = 0.05) -> bool:
    """Retorna True se duas cores RGB são próximas dentro da tolerância."""
    return all(abs(a[i] - b[i]) <= tolerance for i in range(min(len(a), len(b), 3)))


# ---------------------------------------------------------------------------
# Swatch de cor
# ---------------------------------------------------------------------------

class _ColorSwatch(QtWidgets.QFrame):
    clicked = QtCore.Signal(str)  # material.id

    def __init__(self, material, selected=False, parent=None):
        super().__init__(parent)
        self._mat_id = material.id
        self.setFixedSize(80, 70)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(material.name)
        self._update_style(selected, material.color)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # Retângulo de cor ou textura
        self._color_box = QtWidgets.QLabel()
        self._color_box.setFixedHeight(40)
        self._color_box.setScaledContents(True)
        self._apply_color_box(material)
        layout.addWidget(self._color_box)

        lbl = QtWidgets.QLabel(material.name)
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("font-size:8px;")
        layout.addWidget(lbl)

    def _update_style(self, selected, color):
        if selected:
            self.setStyleSheet(
                "QFrame{border:2px solid palette(highlight);border-radius:6px;}"
            )
        else:
            self.setStyleSheet(
                "QFrame{border:1px solid palette(mid);border-radius:6px;}"
                "QFrame:hover{border-color:palette(highlight);}"
            )

    def _apply_color_box(self, material):
        png_path = None
        if getattr(material, "texture_id", None):
            try:
                from panelnest.textures import texture_path
                png_path = texture_path(material.texture_id)
            except Exception:
                pass
        if png_path:
            pix = QtGui.QPixmap(str(png_path))
            if not pix.isNull():
                self._color_box.setPixmap(pix.scaled(72, 40, QtCore.Qt.KeepAspectRatioByExpanding, QtCore.Qt.SmoothTransformation))
                self._color_box.setStyleSheet("border-radius:4px; border:1px solid rgba(0,0,0,40);")
                return
        r, g, b = [int(c * 255) for c in material.color]
        self._color_box.setStyleSheet(
            f"background:rgb({r},{g},{b});"
            "border-radius:4px;"
            "border:1px solid rgba(0,0,0,40);"
        )

    def set_selected(self, sel):
        # Rebuild não vale a pena — só muda borda
        self._update_style(sel, None)

    def mousePressEvent(self, event):
        self.clicked.emit(self._mat_id)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Dialog de seleção de material
# ---------------------------------------------------------------------------

class ApplyMaterialDialog(QtWidgets.QDialog):

    def __init__(self, parent=None, title="PanelNest — Aplicar Material", ok_label="Aplicar"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._selected_id = None
        self._swatches = {}
        self._ok_label = ok_label
        self._build_ui()
        self.adjustSize()

    def _build_ui(self):
        from panelnest.materials import MATERIALS, CATEGORIES

        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(12, 12, 12, 12)

        mats_by_cat = {}
        for m in MATERIALS:
            mats_by_cat.setdefault(m.category, []).append(m)

        for cat in CATEGORIES:
            mats = mats_by_cat.get(cat, [])
            if not mats:
                continue

            cat_lbl = QtWidgets.QLabel(f"<b>{cat}</b>")
            root.addWidget(cat_lbl)

            row_widget = QtWidgets.QWidget()
            row = QtWidgets.QHBoxLayout(row_widget)
            row.setSpacing(6)
            row.setContentsMargins(0, 0, 0, 4)

            for m in mats:
                sw = _ColorSwatch(m)
                sw.clicked.connect(self._on_swatch_clicked)
                row.addWidget(sw)
                self._swatches[m.id] = sw

            row.addStretch()
            root.addWidget(row_widget)

        # Barra inferior: botão de texturas + botões OK/Cancel
        bottom_layout = QtWidgets.QHBoxLayout()

        self._texture_btn = QtWidgets.QPushButton("⬇ Baixar texturas de madeira")
        self._texture_btn.setToolTip(
            "Baixa as texturas de veio CC0 (ambientcg.com) para os materiais amadeirados.\n"
            "Necessário apenas uma vez (~100 MB total)."
        )
        self._texture_btn.clicked.connect(self._on_download_textures)
        self._update_texture_btn_label()
        bottom_layout.addWidget(self._texture_btn)
        bottom_layout.addStretch(1)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self._ok_btn = btns.button(QtWidgets.QDialogButtonBox.Ok)
        self._ok_btn.setText(self._ok_label)
        self._ok_btn.setEnabled(False)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        bottom_layout.addWidget(btns)
        root.addLayout(bottom_layout)

    def _update_texture_btn_label(self):
        try:
            from panelnest.textures import list_missing_textures
            missing = list_missing_textures()
            if missing:
                self._texture_btn.setText(f"⬇ Baixar texturas de madeira ({len(missing)} pendentes)")
                self._texture_btn.setEnabled(True)
            else:
                self._texture_btn.setText("✓ Texturas de madeira baixadas")
                self._texture_btn.setEnabled(False)
        except Exception:
            self._texture_btn.setVisible(False)

    def _on_download_textures(self):
        try:
            from panelnest.textures import list_missing_textures, ensure_texture
        except ImportError:
            return

        missing = list_missing_textures()
        if not missing:
            return

        progress = QtWidgets.QProgressDialog(
            "Baixando texturas...", "Cancelar", 0, len(missing), self
        )
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.setMinimumWidth(400)
        progress.show()

        for i, texture_id in enumerate(missing):
            if progress.wasCanceled():
                break
            progress.setValue(i)
            progress.setLabelText(f"Baixando {texture_id} ({i + 1}/{len(missing)})...")
            QtWidgets.QApplication.processEvents()

            def _cb(msg):
                progress.setLabelText(msg)
                QtWidgets.QApplication.processEvents()

            ensure_texture(texture_id, progress_callback=_cb)

        progress.setValue(len(missing))
        progress.close()
        self._update_texture_btn_label()

        # Atualizar swatches para mostrar indicador de textura disponível
        self._refresh_swatch_indicators()

    def _refresh_swatch_indicators(self):
        try:
            from panelnest.textures import is_texture_downloaded
            from panelnest.materials import get_by_id
            for mat_id, sw in self._swatches.items():
                mat = get_by_id(mat_id)
                if mat and mat.texture_id and is_texture_downloaded(mat.texture_id):
                    sw.setToolTip(f"{mat.name} ✓ textura disponível")
        except Exception:
            pass

    def _on_swatch_clicked(self, mat_id):
        for mid, sw in self._swatches.items():
            sw.set_selected(mid == mat_id)
        self._selected_id = mat_id
        self._ok_btn.setEnabled(True)

    def selected_material(self):
        from panelnest.materials import get_by_id
        return get_by_id(self._selected_id)


# ---------------------------------------------------------------------------
# Comando FreeCAD
# ---------------------------------------------------------------------------

class ApplyMaterialCommand:
    def Activated(self):
        if Gui is None:
            return

        selection = Gui.Selection.getSelection()
        if not selection:
            QtWidgets.QMessageBox.information(
                _main_window(), "PanelNest",
                "Selecione as peças antes de aplicar o material."
            )
            return

        dlg = ApplyMaterialDialog(_main_window())
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        mat = dlg.selected_material()
        if mat is None:
            return

        count = 0
        pairs = _resolve_selection(selection)
        if not pairs:
            # Fallback: _resolve_selection pode retornar vazio para tipos
            # de objeto não reconhecidos (Part::Feature, App::Link fora de
            # Body, etc). Nesses casos, aplicamos direto no próprio objeto.
            pairs = [(obj, obj) for obj in selection]

        # Para materiais texturados, a cor de base deve ser branca (neutra
        # em MODULATE) para que a textura PNG apareça com suas cores reais.
        # Para materiais sólidos, usamos a cor do material diretamente.
        base_color = (1.0, 1.0, 1.0) if mat.texture_id else mat.color

        for visual_obj, body in pairs:
            # Aplicar cor nos dois: no visual (Tip/Pad — onde o Coin3D renderiza)
            # e também no Body (fallback). A textura mora no visual, então a
            # cor sólida precisa ir para lá para ficar visível.
            applied_any = False
            if apply_material_to_object(visual_obj, base_color):
                applied_any = True
            if body is not None and body is not visual_obj:
                if apply_material_to_object(body, base_color):
                    applied_any = True
            if applied_any:
                try:
                    import panelnest
                    targets = []
                    if body is not None:
                        targets.append(body)
                    if visual_obj is not None and visual_obj is not body:
                        targets.append(visual_obj)
                    for tgt in targets:
                        try:
                            panelnest.ensure_part_properties(tgt)
                            tgt.PanelNestMaterial = mat.name
                        except Exception:
                            pass
                except Exception:
                    pass
                count += 1

        # Garantir que objetos diretamente selecionados também recebam a
        # propriedade — o candidato de collect_parts pode ser o objeto da
        # seleção, não o (visual, body) resolvido acima.
        for sel_obj in selection:
            try:
                import panelnest
                panelnest.ensure_part_properties(sel_obj)
                sel_obj.PanelNestMaterial = mat.name
            except Exception:
                pass

        # Textura Coin3D: aplicar se material tem texture_id, remover se não tem
        # (feito ANTES do recompute para evitar que o recompute destrua a textura)
        if mat.texture_id:
            self._apply_texture(mat, selection)
        else:
            self._remove_texture(selection, color_rgb=mat.color)

        if App is not None:
            App.ActiveDocument.recompute()

        if mat.texture_id:
            # Reaplicar textura após recompute (recompute invalida RootNode)
            self._apply_texture(mat, selection)
        else:
            # Remover NOVAMENTE após recompute — recompute reconstrói o
            # RootNode e pode restaurar a textura Coin3D cacheada.
            self._remove_texture(selection, color_rgb=mat.color)

        # Reaplicar texturas registradas em todo o documento. O recompute()
        # acima reconstrói o RootNode dos objetos texturizados anteriormente
        # e eles perdem o ShapeColor=(1,1,1), saindo com tom da cor antiga.
        # _texture_registry guarda os objetos certos (panéis com textura),
        # não os containers App::Part.
        try:
            from panelnest.textures import reapply_registered_textures
            reapply_registered_textures(App.ActiveDocument if App else None)
        except Exception:
            pass

        # Limpar o "base visual style" salvo de cada peça antes do refresh.
        # Sem isso, refresh_part_edge_band_visuals carrega a cor SALVA do
        # material anterior e sobrescreve a cor nova que acabamos de aplicar.
        try:
            from panelnest.edge_band import _clear_part_base_visual_style
            from panelnest.geometry import get_part_source_objects
            for _o in get_part_source_objects(selection):
                try:
                    _clear_part_base_visual_style(_o)
                except Exception:
                    pass
        except Exception:
            pass

        # Aplicar cor de corte por face (bordas sem fita = miolo exposto)
        try:
            import panelnest
            panelnest.refresh_part_edge_band_visuals(selection)
        except Exception:
            pass

        if App is not None:
            App.Console.PrintMessage(
                f"PanelNest: material \"{mat.name}\" aplicado a {count} peça(s).\n"
            )

    def _remove_texture(self, objects, color_rgb=None):
        """Remove qualquer SoTexture2/Transform/CoordinatePlane e força
        o scene graph a refletir o ShapeColor atual da peça. Para
        materiais sólidos a cor já foi setada por apply_material_to_object;
        este passo apenas garante que nenhuma textura residual sobreponha.
        """
        try:
            from panelnest.textures import remove_texture_from_object

            pairs = _resolve_selection(objects)
            if not pairs:
                pairs = [(obj, obj) for obj in objects]
            for visual, _body in pairs:
                remove_texture_from_object(visual)
                if _body is not None and _body is not visual:
                    remove_texture_from_object(_body)
                    for child in getattr(_body, "OutList", []):
                        remove_texture_from_object(child)
        except Exception:
            pass

    def _apply_texture(self, mat, objects):
        try:
            from panelnest.textures import ensure_texture
        except ImportError:
            return

        # Verificar se textura está baixada; se não, perguntar ao usuário
        from panelnest.textures import is_texture_downloaded
        if not is_texture_downloaded(mat.texture_id):
            reply = QtWidgets.QMessageBox.question(
                _main_window(),
                "PanelNest — Textura de madeira",
                f"A textura de veio para \"{mat.name}\" ainda não foi baixada (~3 MB).\n\n"
                "Deseja baixar agora? (requer conexão com a internet)",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            )
            if reply != QtWidgets.QMessageBox.Yes:
                return

            progress = QtWidgets.QProgressDialog(
                f"Baixando textura {mat.texture_id}...", "Cancelar", 0, 0, _main_window()
            )
            progress.setWindowModality(QtCore.Qt.WindowModal)
            progress.show()

            def _progress_cb(msg):
                progress.setLabelText(msg)
                QtWidgets.QApplication.processEvents()

            png_path = ensure_texture(mat.texture_id, progress_callback=_progress_cb)
            progress.close()
        else:
            from panelnest.textures import texture_path
            png_path = texture_path(mat.texture_id)

        if png_path is None:
            if App is not None:
                App.Console.PrintWarning(
                    f"PanelNest: textura {mat.texture_id} não disponível, usando cor sólida.\n"
                )
            return

        try:
            from panelnest.textures import apply_texture_to_object
        except ImportError:
            return

        # Resolver para (visual_obj=Tip/Pad, body). Aplicamos a textura em
        # AMBOS: no Tip/Pad (onde a geometria concreta mora) e no Body (que
        # é o ViewProvider que o FreeCAD efetivamente desenha na cena).
        # Sem o Body, a textura fica invisível apesar de aplicada na cena.
        pairs = _resolve_selection(objects)
        if not pairs:
            pairs = [(obj, obj) for obj in objects]

        applied = 0
        for visual_obj, body in pairs:
            try:
                ok_any = False
                if apply_texture_to_object(visual_obj, png_path):
                    ok_any = True
                if body is not None and body is not visual_obj:
                    if apply_texture_to_object(body, png_path):
                        ok_any = True
                if ok_any:
                    applied += 1
            except Exception as exc:
                if App is not None:
                    App.Console.PrintWarning(f"PanelNest textura: {exc}\n")

        if App is not None:
            App.Console.PrintMessage(
                f"PanelNest: textura aplicada a {applied}/{len(pairs)} objeto(s).\n"
            )

    def IsActive(self):
        return Gui is not None and (
            App is not None and App.ActiveDocument is not None
        )

    def GetResources(self):
        return {
            "Pixmap": os.path.join(
                os.path.dirname(__file__), "..", "resources", "icons",
                "panelnest_material.svg"
            ),
            "Accel": "",
            "MenuText": "Aplicar Material",
            "ToolTip": (
                "Aplica cor de material às peças selecionadas.\n"
                "Compatível com a visualização de fita de borda."
            ),
        }


ROTATE_GRAIN_COMMAND_NAME = "PanelNest_RotateGrain"


class RotateGrainCommand:
    """Gira o veio da textura 90° nas peças selecionadas."""

    def Activated(self):
        if Gui is None:
            return
        selection = Gui.Selection.getSelection()
        if not selection:
            QtWidgets.QMessageBox.information(
                _main_window(), "PanelNest",
                "Selecione as peças antes de girar o veio."
            )
            return

        try:
            from panelnest.textures import rotate_grain_on_object
        except ImportError:
            return

        pairs = _resolve_selection(selection)
        if not pairs:
            pairs = [(obj, obj) for obj in selection]

        count = 0
        for visual_obj, body in pairs:
            if rotate_grain_on_object(visual_obj, prop_target=body):
                count += 1

        if App is not None:
            App.Console.PrintMessage(
                f"PanelNest: veio girado em {count} peça(s).\n"
            )

    def IsActive(self):
        return Gui is not None and App is not None and App.ActiveDocument is not None

    def GetResources(self):
        import panelnest
        return {
            "Pixmap": panelnest.get_icon_path("panelnest_rotate_grain.svg"),
            "Accel": "",
            "MenuText": "Girar Veio",
            "ToolTip": (
                "Gira o sentido do veio/fibra 90° nas peças selecionadas.\n"
                "Afeta o layout de corte: peças com veio girado são posicionadas "
                "rotacionadas na chapa."
            ),
        }


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, ApplyMaterialCommand())
    Gui.addCommand(ROTATE_GRAIN_COMMAND_NAME, RotateGrainCommand())
