"""Non-modal controls for a live image height-map relief preview."""

from __future__ import annotations

from pathlib import Path
import tempfile

from woodcam_editor.presentation.compat import Signal, QtCore, QtGui, QtWidgets, qt_enum

from .heightmap import (
    ReliefOptions,
    heightmap_png,
    load_heightmap,
    shaded_preview_png,
)
from .ai_depth import AIRuntimeError, discover_ai_runtime, load_ai_heightmap


class SliderSpinControl(QtWidgets.QWidget):
    """Compact professional slider with an exact editable numeric value."""

    valueChanged = Signal(float)

    def __init__(
        self,
        minimum,
        maximum,
        value,
        *,
        suffix="",
        decimals=0,
        step=1.0,
        integer=False,
        slider_range=None,
        parent=None,
    ):
        super(SliderSpinControl, self).__init__(parent)
        self._integer = bool(integer)
        self._slider_minimum, self._slider_maximum = slider_range or (
            float(minimum),
            float(maximum),
        )
        self._syncing = False
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.slider = QtWidgets.QSlider(
            qt_enum(QtCore.Qt, "Horizontal", "Orientation"), self
        )
        self.slider.setRange(0, 1000)
        self.slider.setMinimumWidth(105)
        self.spin = (
            QtWidgets.QSpinBox(self)
            if self._integer
            else QtWidgets.QDoubleSpinBox(self)
        )
        self.spin.setRange(minimum, maximum)
        if not self._integer:
            self.spin.setDecimals(int(decimals))
            self.spin.setSingleStep(float(step))
        else:
            self.spin.setSingleStep(max(1, int(round(step))))
        self.spin.setSuffix(str(suffix))
        self.spin.setMinimumWidth(104)
        try:
            self.spin.setButtonSymbols(
                qt_enum(QtWidgets.QAbstractSpinBox, "NoButtons", "ButtonSymbols")
            )
        except Exception:
            pass
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.spin, 0)
        self.slider.valueChanged.connect(self._slider_changed)
        self.spin.valueChanged.connect(self._spin_changed)
        self.setFocusProxy(self.spin)
        self.setValue(value)

    def _position_for_value(self, value):
        span = self._slider_maximum - self._slider_minimum
        if span <= 0.0:
            return 0
        ratio = (float(value) - self._slider_minimum) / span
        return int(round(max(0.0, min(1.0, ratio)) * 1000.0))

    def _value_for_position(self, position):
        ratio = float(position) / 1000.0
        value = self._slider_minimum + ratio * (
            self._slider_maximum - self._slider_minimum
        )
        return int(round(value)) if self._integer else value

    def _slider_changed(self, position):
        if self._syncing:
            return
        self._syncing = True
        try:
            self.spin.setValue(self._value_for_position(position))
            value = self.spin.value()
        finally:
            self._syncing = False
        self.valueChanged.emit(float(value))

    def _spin_changed(self, value):
        if self._syncing:
            return
        self._syncing = True
        try:
            self.slider.setValue(self._position_for_value(value))
        finally:
            self._syncing = False
        self.valueChanged.emit(float(value))

    def value(self):
        return self.spin.value()

    def setValue(self, value):
        self.spin.setValue(value)
        self.slider.setValue(self._position_for_value(self.spin.value()))

    def minimum(self):
        return self.spin.minimum()

    def maximum(self):
        return self.spin.maximum()

    def setToolTip(self, text):
        super(SliderSpinControl, self).setToolTip(text)
        self.slider.setToolTip(text)
        self.spin.setToolTip(text)


class ReliefImageDialog(QtWidgets.QDialog):
    previewChanged = Signal(object)

    def __init__(
        self,
        image_path,
        parent=None,
        *,
        default_origin=(0.0, 0.0),
        ai_runtime=None,
    ):
        super(ReliefImageDialog, self).__init__(parent)
        self.image_path = str(image_path)
        self.current_data = None
        self._syncing_size = False
        self._drag_start_global = None
        self._drag_start_pos = None
        self._dialog_dragging = False
        self._applying_quality_profile = False
        self._ai_process = None
        self._ai_map_path = None
        self._ai_profile_used = "sculptural"
        self._ai_pending_profile = "sculptural"
        self._ai_tempdir = tempfile.TemporaryDirectory(prefix="woodcam-relief-ai-")
        try:
            self._ai_runtime = ai_runtime or discover_ai_runtime()
            self._ai_runtime_error = ""
        except AIRuntimeError as error:
            self._ai_runtime = None
            self._ai_runtime_error = str(error)
        self._source_aspect = self._read_source_aspect()
        self._light_background = self._detect_light_background()
        self._source_background_kind = self._inspect_source_background()
        self.setWindowTitle("Criar relevo 3D por imagem")
        if parent is not None:
            # Same strategy as WoodCAM2DDialog on Wayland/Niri: a child widget
            # can be repositioned by Qt, while a top-level window cannot.
            self.setWindowFlags(qt_enum(QtCore.Qt, "Widget", "WindowType"))
        self.setModal(False)
        self.setAttribute(qt_enum(QtCore.Qt, "WA_DeleteOnClose", "WidgetAttribute"), True)
        self.resize(1050, 690)
        root = QtWidgets.QVBoxLayout(self)

        title = QtWidgets.QLabel(
            "Fluxo recomendado: 1) use imagem sem cenário; 2) escolha o resultado; 3) ajuste a altura; 4) confira o acabamento. Arraste por qualquer fundo livre para mover.",
            self,
        )
        title.setWordWrap(True)
        root.addWidget(title)

        previews = QtWidgets.QHBoxLayout()
        self.original_label = self._preview_label("Imagem original")
        self.map_label = self._preview_label("Mapa de altura")
        self.shaded_label = self._preview_label("Relevo sombreado")
        previews.addWidget(self.original_label, 1)
        previews.addWidget(self.map_label, 1)
        previews.addWidget(self.shaded_label, 1)
        root.addLayout(previews, 1)

        columns = QtWidgets.QHBoxLayout()
        dimensions = QtWidgets.QGroupBox("1. Tamanho e altura", self)
        dimension_form = QtWidgets.QFormLayout(dimensions)
        self.width_mm = self._double(
            0.1, 100000.0, 200.0, " mm", 2, slider_range=(1.0, 2000.0)
        )
        default_height = 200.0 / self._source_aspect
        self.height_mm = self._double(
            0.1, 100000.0, default_height, " mm", 2, slider_range=(1.0, 2000.0)
        )
        self.lock_aspect = QtWidgets.QCheckBox("Manter proporção da imagem", self)
        self.lock_aspect.setChecked(True)
        self.relief_height = self._double(
            0.01, 10000.0, 4.0, " mm", 2, slider_range=(0.01, 20.0)
        )
        self.base_thickness = self._double(
            0.0, 10000.0, 3.0, " mm", 2, slider_range=(0.0, 50.0)
        )
        self.origin_x = self._double(
            -100000.0,
            100000.0,
            float(default_origin[0]),
            " mm",
            2,
            slider_range=(-1000.0, 1000.0),
        )
        self.origin_y = self._double(
            -100000.0,
            100000.0,
            float(default_origin[1]),
            " mm",
            2,
            slider_range=(-1000.0, 1000.0),
        )
        dimension_form.addRow("Largura", self.width_mm)
        dimension_form.addRow("Altura", self.height_mm)
        dimension_form.addRow("Proporção", self.lock_aspect)
        dimension_form.addRow("Altura máxima", self.relief_height)
        dimension_form.addRow("Espessura da base", self.base_thickness)
        dimension_form.addRow("Posição X", self.origin_x)
        dimension_form.addRow("Posição Y", self.origin_y)
        columns.addWidget(dimensions, 1)

        processing = QtWidgets.QGroupBox("2. Forma e acabamento", self)
        self.processing_group = processing
        processing_layout = QtWidgets.QVBoxLayout(processing)
        self.quality_profile = QtWidgets.QComboBox(self)
        self.quality_profile.addItem(
            "Detalhado — escamas, penas e objetos", "aspire_detailed"
        )
        self.quality_profile.addItem(
            "Retrato com lissage — rostos e pelos", "portrait_soft"
        )
        self.quality_profile.addItem("Personalizado", "custom")
        self.quality_profile.setToolTip(
            "Aplica um conjunto coerente de altura, estrutura, detalhe, borda e qualidade."
        )
        self.profile_hint = QtWidgets.QLabel(self)
        self.profile_hint.setWordWrap(True)
        self.relief_mode = QtWidgets.QComboBox(self)
        self.relief_mode.addItem(
            "Componente multiescala (recomendado)", "aspire_bitmap"
        )
        self.relief_mode.addItem(
            "Volume por silhueta (experimental)", "photo_volume"
        )
        self.relief_mode.addItem(
            "Mapa de profundidade (altura literal)", "heightmap"
        )
        self.mode_hint = QtWidgets.QLabel(self)
        self.mode_hint.setWordWrap(True)
        self.source_hint = QtWidgets.QLabel(self)
        self.source_hint.setWordWrap(True)
        self._update_source_hint()
        self.auto_orientation = QtWidgets.QCheckBox(
            "Colocar fundo detectado na base", self
        )
        self.auto_orientation.setChecked(True)
        self.invert = QtWidgets.QCheckBox(
            "Inverter relevo (alto ↔ baixo)", self
        )
        self.invert.setChecked(bool(self._light_background))
        self.invert.setToolTip(
            "Alterne a direção do relevo: permite trocar livremente entre forma elevada e cavada."
        )
        self.auto_levels = QtWidgets.QCheckBox("Expandir preto ↔ branco", self)
        self.auto_levels.setChecked(True)
        self.contrast = self._double(0.1, 5.0, 1.0, "×", 2, 0.1)
        self.gamma = self._double(0.1, 5.0, 1.0, "", 2, 0.1)
        self.smoothing = self._double(0.0, 20.0, 0.25, " px", 2, 0.25)
        self.smoothing.setToolTip(
            "Acabamento pós-componente, equivalente ao Lisser components do Aspire. "
            "Suaviza picos sem puxar a transparência/fundo para dentro do objeto."
        )
        self.detail_strength = self._integer(0, 100, 65, " %")
        self.detail_strength.setToolTip(
            "Amplitude de pelos, escamas e textura sobre o volume geral. Menor evita picos."
        )
        self.form_smoothing = self._double(0.0, 40.0, 3.0, " px", 1, 1.0)
        self.form_smoothing.setToolTip(
            "Separa o volume geral da microtextura. Aumente para um corpo mais contínuo."
        )
        self.structure_strength = self._integer(0, 100, 95, " %")
        self.structure_strength.setToolTip(
            "Preserva olhos, focinho, barbatanas e outras formas médias durante a compressão."
        )
        self.edge_feather = self._double(0.0, 20.0, 1.5, " px", 1, 0.5)
        self.edge_feather.setToolTip(
            "Cria uma queda progressiva dentro do contorno, evitando uma parede serrilhada."
        )
        self.sculptural_volume = self._integer(0, 100, 50, " %")
        self.sculptural_volume.setToolTip(
            "Levanta o corpo/rosto pela silhueta antes de recolocar estrutura e textura."
        )
        self.compensate_height = QtWidgets.QCheckBox(
            "Estabilizar detalhes ao aumentar altura", self
        )
        self.compensate_height.setChecked(True)
        self.compensate_height.setToolTip(
            "Concentra a altura extra no volume geral e evita transformar textura em montanhas."
        )
        self.background_threshold = self._integer(0, 254, 20)
        self.background_threshold.setToolTip(
            "Aumente se ainda houver uma névoa elevada ao redor do objeto."
        )
        self.profile_power = self._double(0.2, 3.0, 0.75, "", 2, 0.05)
        self.profile_power.setToolTip(
            "Menor cria corpo mais cheio; maior concentra a altura no centro."
        )
        processing_layout.addWidget(self.source_hint)
        profile_form = QtWidgets.QFormLayout()
        profile_form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.AllNonFixedFieldsGrow
        )
        profile_form.addRow("Resultado", self.quality_profile)
        profile_form.addRow(self.profile_hint)
        processing_layout.addLayout(profile_form)

        self.processing_tabs = QtWidgets.QTabWidget(self)
        self.essential_tab = QtWidgets.QWidget(self.processing_tabs)
        essential_form = QtWidgets.QFormLayout(self.essential_tab)
        essential_form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.AllNonFixedFieldsGrow
        )
        essential_form.addRow("Lissage final", self.smoothing)
        essential_form.addRow("Textura fina", self.detail_strength)
        essential_form.addRow("Continuidade do corpo", self.form_smoothing)
        essential_form.addRow("Olhos e contornos", self.structure_strength)
        essential_form.addRow("Volume do corpo", self.sculptural_volume)
        essential_form.addRow("Direção", self.invert)
        essential_form.addRow("Altura inteligente", self.compensate_height)
        self.processing_tabs.addTab(self.essential_tab, "Essencial")

        self.advanced_tab = QtWidgets.QScrollArea(self.processing_tabs)
        self.advanced_tab.setWidgetResizable(True)
        self.advanced_tab.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.advanced_tab.setHorizontalScrollBarPolicy(
            qt_enum(QtCore.Qt, "ScrollBarAlwaysOff", "ScrollBarPolicy")
        )
        advanced_content = QtWidgets.QWidget(self.advanced_tab)
        advanced_form = QtWidgets.QFormLayout(advanced_content)
        advanced_form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.AllNonFixedFieldsGrow
        )
        advanced_form.addRow("Método", self.relief_mode)
        advanced_form.addRow(self.mode_hint)
        advanced_form.addRow("Orientação automática", self.auto_orientation)
        advanced_form.addRow("Níveis automáticos", self.auto_levels)
        advanced_form.addRow("Contraste", self.contrast)
        advanced_form.addRow("Gamma", self.gamma)
        advanced_form.addRow("Entrada do contorno", self.edge_feather)
        advanced_form.addRow("Tolerância do fundo", self.background_threshold)
        advanced_form.addRow("Perfil do volume", self.profile_power)
        self.ai_refinement_profile = QtWidgets.QComboBox(self)
        self.ai_refinement_profile.addItem(
            "Escultórico HD — animais, rostos e medalhões", "sculptural"
        )
        self.ai_refinement_profile.addItem(
            "Equilibrado — corpo contínuo + detalhes", "balanced"
        )
        self.ai_refinement_profile.addItem(
            "Gravação detalhada — exige ferramenta fina", "detailed"
        )
        self.ai_refinement_profile.setEnabled(self._ai_runtime is not None)
        self.ai_refinement_profile.setToolTip(
            "Escolha o acabamento e gere novamente para comparar. Cada resultado continua sendo somente prévia."
        )
        self.ai_generate_button = QtWidgets.QPushButton(
            "Gerar forma com IA — CPU", self
        )
        self.ai_generate_button.setEnabled(self._ai_runtime is not None)
        self.ai_generate_button.setToolTip(
            "Gera um mapa temporário em CPU. Quando o módulo Pro está instalado, combina profundidade DA2 com normais Metric3D. Nada é confirmado no FCStd até clicar Criar relevo."
        )
        self.ai_runtime_hint = QtWidgets.QLabel(self)
        self.ai_runtime_hint.setWordWrap(True)
        if self._ai_runtime is not None:
            if getattr(self._ai_runtime, "has_surface_normals", False):
                self.ai_runtime_hint.setText(
                    "IA Pro pronta: DA2 (volume) + Metric3D (inclinações) em CPU. A forma gerada continua sendo apenas prévia."
                )
            else:
                self.ai_runtime_hint.setText(
                    "IA opcional pronta: DA2 Small em CPU (pesos 99,2 MB). A forma gerada continua sendo apenas prévia."
                )
            self.ai_runtime_hint.setStyleSheet("QLabel { color: #166534; }")
        else:
            detail = (" " + self._ai_runtime_error) if self._ai_runtime_error else ""
            self.ai_runtime_hint.setText(
                "IA opcional não instalada/configurada; o relevo local atual continua disponível.%s"
                % detail
            )
            self.ai_runtime_hint.setStyleSheet("QLabel { color: #6b7280; }")
        advanced_form.addRow("Refino da IA", self.ai_refinement_profile)
        advanced_form.addRow("Forma por IA", self.ai_generate_button)
        advanced_form.addRow(self.ai_runtime_hint)
        self.advanced_tab.setWidget(advanced_content)
        self.processing_tabs.addTab(self.advanced_tab, "Avançado")
        processing_layout.addWidget(self.processing_tabs, 1)
        columns.addWidget(processing, 3)

        quality = QtWidgets.QGroupBox("3. Qualidade da malha", self)
        quality_form = QtWidgets.QFormLayout(quality)
        self.map_resolution = self._integer(64, 2048, 1024, " px máx.")
        self.mesh_resolution = self._integer(24, 384, 320, " pontos/lado")
        self.mesh_resolution.setToolTip(
            "Controla somente a aparência no FreeCAD. 320 é o padrão de alta qualidade; 384 melhora contornos finos com maior uso de memória. O CAM usa o mapa preservado independentemente desta malha."
        )
        quality_form.addRow("Limite do mapa", self.map_resolution)
        quality_form.addRow("Malha visual", self.mesh_resolution)
        quality_note = QtWidgets.QLabel(
            "O tamanho real do mapa aparece no rodapé. O CAM usa esse mapa; a malha visual controla apenas a apresentação no FreeCAD.",
            self,
        )
        quality_note.setWordWrap(True)
        quality_form.addRow(quality_note)
        columns.addWidget(quality, 1)
        columns.setStretch(0, 2)
        columns.setStretch(1, 3)
        columns.setStretch(2, 2)
        root.addLayout(columns)

        self.status = QtWidgets.QLabel("Preparando prévia…", self)
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        buttons = QtWidgets.QDialogButtonBox(
            qt_enum(QtWidgets.QDialogButtonBox, "Ok", "StandardButton")
            | qt_enum(QtWidgets.QDialogButtonBox, "Cancel", "StandardButton"),
            parent=self,
        )
        ok = buttons.button(qt_enum(QtWidgets.QDialogButtonBox, "Ok", "StandardButton"))
        if ok is not None:
            ok.setText("Criar relevo")
        buttons.accepted.connect(self._accept_if_ready)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(140)
        self._timer.timeout.connect(self._update_preview)
        self.ai_generate_button.clicked.connect(self._start_ai_generation)
        self._connect_controls()
        self._load_original()
        self._enable_background_dragging()
        self._timer.start(0)

    def _double(
        self,
        minimum,
        maximum,
        value,
        suffix,
        decimals,
        step=1.0,
        slider_range=None,
    ):
        return SliderSpinControl(
            minimum,
            maximum,
            value,
            suffix=suffix,
            decimals=decimals,
            step=step,
            slider_range=slider_range,
            parent=self,
        )

    def _integer(self, minimum, maximum, value, suffix=""):
        return SliderSpinControl(
            minimum,
            maximum,
            value,
            suffix=suffix,
            integer=True,
            parent=self,
        )

    def _read_source_aspect(self):
        reader = QtGui.QImageReader(self.image_path)
        size = reader.size()
        if size.isValid() and size.height() > 0:
            return max(1e-9, size.width() / float(size.height()))
        return 1.0

    def _detect_light_background(self):
        image = QtGui.QImage(self.image_path)
        if image.isNull() or image.width() < 2 or image.height() < 2:
            return False
        patch_x = max(1, min(16, image.width() // 12))
        patch_y = max(1, min(16, image.height() // 12))
        samples = []
        for start_x, start_y in (
            (0, 0),
            (image.width() - patch_x, 0),
            (0, image.height() - patch_y),
            (image.width() - patch_x, image.height() - patch_y),
        ):
            for y in range(start_y, start_y + patch_y):
                for x in range(start_x, start_x + patch_x):
                    color = image.pixelColor(x, y)
                    # Transparent RGB is undefined and many cut-outs retain
                    # white pixels beneath alpha zero.  The relief processor
                    # always maps transparency to the base, so orientation
                    # detection must see the same effective black background.
                    opacity = color.alpha() / 255.0
                    samples.append(
                        ((color.red() + color.green() + color.blue()) / 3.0)
                        * opacity
                    )
        return bool(samples) and sum(samples) / len(samples) >= 150.0

    def _inspect_source_background(self):
        """Classify whether component isolation is reliable without AI."""

        image = QtGui.QImage(self.image_path)
        if image.isNull() or image.width() < 2 or image.height() < 2:
            return "unknown"
        step_x = max(1, image.width() // 48)
        step_y = max(1, image.height() // 48)
        for y in range(0, image.height(), step_y):
            for x in range(0, image.width(), step_x):
                if image.pixelColor(x, y).alpha() < 250:
                    return "transparent"
        patch_x = max(1, min(24, image.width() // 10))
        patch_y = max(1, min(24, image.height() // 10))
        means = []
        for start_x, start_y in (
            (0, 0),
            (image.width() - patch_x, 0),
            (0, image.height() - patch_y),
            (image.width() - patch_x, image.height() - patch_y),
        ):
            red = green = blue = count = 0
            for y in range(start_y, start_y + patch_y):
                for x in range(start_x, start_x + patch_x):
                    color = image.pixelColor(x, y)
                    red += color.red()
                    green += color.green()
                    blue += color.blue()
                    count += 1
            means.append((red / count, green / count, blue / count))
        maximum_distance = 0.0
        for left in means:
            for right in means:
                maximum_distance = max(
                    maximum_distance,
                    sum((left[index] - right[index]) ** 2 for index in range(3))
                    ** 0.5,
                )
        return "uniform" if maximum_distance <= 55.0 else "scene"

    def _update_source_hint(self):
        if self._source_background_kind == "transparent":
            text = "✓ Fonte ideal: PNG com transparência; somente o objeto vira relevo."
            color = "#166534"
        elif self._source_background_kind == "uniform":
            text = "✓ Fundo uniforme: a separação automática deve preservar o objeto."
            color = "#166534"
        elif self._source_background_kind == "scene":
            text = (
                "⚠ Foto com cenário detectada: céu, parede ou paisagem também viram relevo. "
                "Para resultado tipo Aspire, recorte o objeto e use PNG transparente ou fundo uniforme."
            )
            color = "#b45309"
        else:
            text = "Não foi possível avaliar o fundo da imagem."
            color = "#b45309"
        self.source_hint.setText(text)
        self.source_hint.setStyleSheet(
            "QLabel { color: %s; font-weight: bold; }" % color
        )

    def _preview_label(self, text):
        label = QtWidgets.QLabel(text, self)
        label.setAlignment(qt_enum(QtCore.Qt, "AlignCenter", "AlignmentFlag"))
        label.setMinimumSize(240, 180)
        label.setFrameShape(QtWidgets.QFrame.StyledPanel)
        label.setStyleSheet("QLabel { background: #18212b; color: #dbe5ef; }")
        return label

    def _scaled(self, pixmap, label):
        size = label.size() - QtCore.QSize(14, 14)
        return pixmap.scaled(
            size,
            qt_enum(QtCore.Qt, "KeepAspectRatio", "AspectRatioMode"),
            qt_enum(QtCore.Qt, "SmoothTransformation", "TransformationMode"),
        )

    def _load_original(self):
        pixmap = QtGui.QPixmap(self.image_path)
        if pixmap.isNull():
            self.original_label.setText("Não foi possível abrir %s" % Path(self.image_path).name)
        else:
            self.original_label.setPixmap(self._scaled(pixmap, self.original_label))

    def _connect_controls(self):
        self.width_mm.valueChanged.connect(self._width_changed)
        self.height_mm.valueChanged.connect(self._height_changed)
        for control in (
            self.relief_height,
            self.base_thickness,
            self.origin_x,
            self.origin_y,
            self.contrast,
            self.gamma,
            self.smoothing,
            self.detail_strength,
            self.form_smoothing,
            self.structure_strength,
            self.edge_feather,
            self.sculptural_volume,
            self.background_threshold,
            self.profile_power,
            self.map_resolution,
            self.mesh_resolution,
        ):
            control.valueChanged.connect(self._schedule_preview)
        for control in (
            self.invert,
            self.auto_levels,
            self.lock_aspect,
            self.compensate_height,
        ):
            control.toggled.connect(self._schedule_preview)
        self.auto_orientation.toggled.connect(self._auto_orientation_changed)
        self.quality_profile.currentIndexChanged.connect(
            self._quality_profile_changed
        )
        for control in (
            self.relief_height,
            self.smoothing,
            self.detail_strength,
            self.form_smoothing,
            self.structure_strength,
            self.edge_feather,
            self.sculptural_volume,
            self.mesh_resolution,
            self.compensate_height,
        ):
            signal = getattr(control, "valueChanged", None) or getattr(
                control, "toggled"
            )
            signal.connect(self._mark_quality_profile_custom)
        self.relief_mode.currentIndexChanged.connect(self._relief_mode_changed)
        self._quality_profile_changed()
        self._relief_mode_changed()

    def _mark_quality_profile_custom(self, *_args):
        if self._applying_quality_profile:
            return
        custom_index = self.quality_profile.findData("custom")
        if custom_index >= 0 and self.quality_profile.currentIndex() != custom_index:
            blocked = self.quality_profile.blockSignals(True)
            self.quality_profile.setCurrentIndex(custom_index)
            self.quality_profile.blockSignals(blocked)
            self._quality_profile_changed()

    def _quality_profile_changed(self, *_args):
        profile = self.quality_profile.currentData()
        if profile == "custom":
            self.profile_hint.setText(
                "Personalizado: algum valor foi alterado manualmente."
            )
            self.profile_hint.setStyleSheet("QLabel { color: #1d4ed8; }")
            return
        values = (
            {
                "height": 4.0,
                "smoothing": 0.25,
                "detail": 65,
                "form": 3.0,
                "structure": 95,
                "edge": 1.5,
                "volume": 50,
                "mesh": 320,
            }
            if profile == "aspire_detailed"
            else {
                "height": 4.0,
                "smoothing": 1.5,
                "detail": 32,
                "form": 6.0,
                "structure": 82,
                "edge": 3.0,
                "volume": 45,
                "mesh": 288,
            }
        )
        self._applying_quality_profile = True
        try:
            self.relief_height.setValue(values["height"])
            self.smoothing.setValue(values["smoothing"])
            self.detail_strength.setValue(values["detail"])
            self.form_smoothing.setValue(values["form"])
            self.structure_strength.setValue(values["structure"])
            self.edge_feather.setValue(values["edge"])
            self.sculptural_volume.setValue(values["volume"])
            self.mesh_resolution.setValue(values["mesh"])
            self.compensate_height.setChecked(True)
        finally:
            self._applying_quality_profile = False
        if profile == "aspire_detailed":
            self.profile_hint.setText(
                "Use para peixes, penas, moedas e objetos: prioriza contornos e textura fina."
            )
        else:
            self.profile_hint.setText(
                "Use para leões, rostos e pelos: aplica lissage de 1,5 px preservando o contorno."
            )
        self.profile_hint.setStyleSheet("QLabel { color: #166534; }")
        self._schedule_preview()

    def _auto_orientation_changed(self, enabled):
        if enabled:
            self.invert.setChecked(bool(self._light_background))
        self._schedule_preview()

    def _relief_mode_changed(self, *_args):
        mode = self.relief_mode.currentData()
        if self._ai_map_path and mode != "heightmap":
            self._ai_map_path = None
            self.ai_runtime_hint.setText(
                "Forma da IA descartada; a prévia voltou a usar a imagem original."
            )
        photo_mode = mode == "photo_volume"
        component_mode = mode == "aspire_bitmap"
        self.detail_strength.setEnabled(photo_mode or component_mode)
        self.form_smoothing.setEnabled(component_mode)
        self.structure_strength.setEnabled(component_mode)
        self.edge_feather.setEnabled(component_mode)
        self.sculptural_volume.setEnabled(component_mode)
        self.compensate_height.setEnabled(component_mode)
        self.background_threshold.setEnabled(photo_mode or component_mode)
        self.profile_power.setEnabled(photo_mode)
        if mode == "aspire_bitmap":
            self.mode_hint.setText(
                "Recomendado: separa o corpo da microtextura para evitar pelos e escamas em forma de picos."
            )
            self.mode_hint.setStyleSheet("QLabel { color: #166534; font-weight: bold; }")
        elif photo_mode:
            self.mode_hint.setText(
                "Experimental: arredonda a silhueta e mistura parte da textura; não é a conversão direta do Aspire."
            )
            self.mode_hint.setStyleSheet("QLabel { color: #b45309; font-weight: bold; }")
        else:
            self.mode_hint.setText(
                "Use quando o arquivo já for um mapa de profundidade: preto é base e branco é altura máxima."
            )
            self.mode_hint.setStyleSheet("QLabel { color: #1d4ed8; font-weight: bold; }")
        self._schedule_preview()

    @staticmethod
    def _event_global_pos(event):
        position = getattr(event, "globalPosition", None)
        if callable(position):
            try:
                return position().toPoint()
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
            QtWidgets.QTabBar,
            QtWidgets.QSlider,
            QtWidgets.QScrollBar,
            QtWidgets.QAbstractItemView,
        )
        while widget is not None and widget is not self:
            if isinstance(widget, blocked_types):
                return True
            widget = widget.parentWidget()
        return False

    def _enable_background_dragging(self):
        self.installEventFilter(self)
        for widget in self.findChildren(QtWidgets.QWidget):
            widget.installEventFilter(self)
        self.setToolTip("Arraste por uma área livre para mover a janela.")

    def eventFilter(self, source, event):
        event_type = event.type()
        press = qt_enum(QtCore.QEvent, "MouseButtonPress", "Type")
        move = qt_enum(QtCore.QEvent, "MouseMove", "Type")
        release = qt_enum(QtCore.QEvent, "MouseButtonRelease", "Type")
        left = qt_enum(QtCore.Qt, "LeftButton", "MouseButton")
        if event_type == press:
            if event.button() == left and not self._is_drag_blocked_widget(source):
                self._drag_start_global = self._event_global_pos(event)
                self._drag_start_pos = self.pos()
                self._dialog_dragging = True
                event.accept()
                return True
        elif event_type == move:
            if self._dialog_dragging and event.buttons() & left:
                current = self._event_global_pos(event)
                self.move(self._drag_start_pos + (current - self._drag_start_global))
                event.accept()
                return True
        elif event_type == release:
            if self._dialog_dragging and event.button() == left:
                self._drag_start_global = None
                self._drag_start_pos = None
                self._dialog_dragging = False
                event.accept()
                return True
        return super(ReliefImageDialog, self).eventFilter(source, event)

    def _width_changed(self, value):
        if self.lock_aspect.isChecked() and not self._syncing_size:
            self._syncing_size = True
            self.height_mm.setValue(float(value) / self._source_aspect)
            self._syncing_size = False
        self._schedule_preview()

    def _height_changed(self, value):
        if self.lock_aspect.isChecked() and not self._syncing_size:
            self._syncing_size = True
            self.width_mm.setValue(float(value) * self._source_aspect)
            self._syncing_size = False
        self._schedule_preview()

    def _schedule_preview(self, *_args):
        self._timer.start()

    def _start_ai_generation(self):
        if self._ai_runtime is None or self._ai_process is not None:
            return
        output = Path(self._ai_tempdir.name) / "depth_anything_v2_relief.png"
        try:
            self._ai_pending_profile = str(
                self.ai_refinement_profile.currentData() or "balanced"
            )
            command = self._ai_runtime.command(
                self.image_path,
                output,
                # 504 px is the quality/performance point validated with the
                # bundled DA2 Small experiment (~1 s on the reference CPU).
                process_resolution=min(504, max(196, int(self.map_resolution.value()))),
                threads=6,
                detail_strength=float(self.detail_strength.value()) / 100.0 * 0.22,
                refinement_profile=self._ai_pending_profile,
            )
        except Exception as error:
            self.status.setText("IA indisponível: %s" % error)
            return
        process = QtCore.QProcess(self)
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        for name, value in self._ai_runtime.process_environment().items():
            environment.insert(str(name), str(value))
        process.setProcessEnvironment(environment)
        process.setWorkingDirectory(str(Path(self._ai_runtime.worker_script).resolve().parents[1]))
        process.finished.connect(self._ai_generation_finished)
        process.errorOccurred.connect(self._ai_generation_error)
        self._ai_process = process
        self.ai_generate_button.setEnabled(False)
        self.status.setText(
            "IA trabalhando no CPU… a imagem e o documento permanecem inalterados."
        )
        process.start(command[0], list(command[1:]))

    def _ai_generation_finished(self, exit_code, _exit_status):
        process = self._ai_process
        self._ai_process = None
        self.ai_generate_button.setEnabled(self._ai_runtime is not None)
        output = Path(self._ai_tempdir.name) / "depth_anything_v2_relief.png"
        stderr = ""
        if process is not None:
            stderr = bytes(process.readAllStandardError()).decode("utf-8", "replace").strip()
            process.deleteLater()
        if int(exit_code) != 0 or not output.is_file():
            self.status.setText(
                "A IA não gerou o mapa: %s" % (stderr or "processo encerrado com erro")
            )
            return
        probe = QtGui.QImage(str(output))
        if probe.isNull() or probe.width() < 2 or probe.height() < 2:
            self.status.setText("A IA retornou um mapa inválido; a prévia anterior foi mantida.")
            return
        self._ai_map_path = str(output)
        self._ai_profile_used = self._ai_pending_profile
        self.auto_orientation.setChecked(False)
        self.invert.setChecked(False)
        heightmap_index = self.relief_mode.findData("heightmap")
        if heightmap_index >= 0:
            self.relief_mode.setCurrentIndex(heightmap_index)
        engine = (
            "DA2 + normais Metric3D"
            if getattr(self._ai_runtime, "has_surface_normals", False)
            else "DA2"
        )
        self.status.setText(
            "Forma %s refinada (%s) no CPU; compare a prévia antes de confirmar."
            % (
                engine,
                {
                    "balanced": "equilibrada",
                    "sculptural": "escultórica",
                    "detailed": "detalhada",
                }.get(self._ai_profile_used, self._ai_profile_used),
            )
        )
        self._update_preview()

    def _ai_generation_error(self, error):
        process = self._ai_process
        self._ai_process = None
        self.ai_generate_button.setEnabled(self._ai_runtime is not None)
        if process is not None:
            process.deleteLater()
        self.status.setText("Não foi possível iniciar a IA: erro %s" % error)

    def options(self):
        return ReliefOptions(
            width_mm=float(self.width_mm.value()),
            height_mm=float(self.height_mm.value()),
            relief_height_mm=float(self.relief_height.value()),
            base_thickness_mm=float(self.base_thickness.value()),
            origin_x_mm=float(self.origin_x.value()),
            origin_y_mm=float(self.origin_y.value()),
            invert=bool(self.invert.isChecked()),
            auto_levels=bool(self.auto_levels.isChecked()),
            contrast=float(self.contrast.value()),
            gamma=float(self.gamma.value()),
            smoothing_radius_px=float(self.smoothing.value()),
            relief_mode=str(self.relief_mode.currentData()),
            detail_strength=float(self.detail_strength.value()) / 100.0,
            form_smoothing_radius_px=float(self.form_smoothing.value()),
            structure_strength=float(self.structure_strength.value()) / 100.0,
            edge_feather_radius_px=float(self.edge_feather.value()),
            sculptural_volume_strength=float(self.sculptural_volume.value()) / 100.0,
            compensate_height=bool(self.compensate_height.isChecked()),
            background_threshold=int(self.background_threshold.value()),
            profile_power=float(self.profile_power.value()),
            max_source_pixels=int(self.map_resolution.value()),
            mesh_resolution=int(self.mesh_resolution.value()),
        )

    def _set_png(self, label, payload):
        pixmap = QtGui.QPixmap()
        pixmap.loadFromData(payload, "PNG")
        label.setPixmap(self._scaled(pixmap, label))

    def _update_preview(self):
        try:
            data = (
                load_ai_heightmap(
                    self._ai_map_path,
                    self.image_path,
                    self.options(),
                    model_id=getattr(self._ai_runtime, "model_id", "depth-anything-v2-small"),
                    refinement_profile=self._ai_profile_used,
                )
                if self._ai_map_path
                else load_heightmap(self.image_path, self.options())
            )
            self.current_data = data
            self._set_png(self.map_label, heightmap_png(data))
            self._set_png(self.shaded_label, shaded_preview_png(data))
            source_summary = {
                "transparent": "PNG transparente",
                "uniform": "fundo uniforme",
                "scene": "foto com cenário",
            }.get(self._source_background_kind, "fundo não classificado")
            generation_summary = "Prévia temporária"
            if data.generator == "ai_depth":
                profile_label = {
                    "balanced": "equilibrada",
                    "sculptural": "escultórica",
                    "detailed": "detalhada",
                }.get(self._ai_profile_used, self._ai_profile_used)
                engine = (
                    "DA2 + Metric3D"
                    if getattr(self._ai_runtime, "has_surface_normals", False)
                    else "DA2"
                )
                generation_summary = "Prévia IA %s %s — CPU" % (engine, profile_label)
            self.status.setText(
                "%s · %s · mapa %d × %d px · relevo %.2f mm · base %.2f mm"
                % (
                    generation_summary,
                    source_summary,
                    data.width_px,
                    data.height_px,
                    data.options.relief_height_mm,
                    data.options.base_thickness_mm,
                )
            )
            self.previewChanged.emit(data)
        except Exception as error:
            self.current_data = None
            self.status.setText("Prévia indisponível: %s" % error)

    def _accept_if_ready(self):
        if self.current_data is None:
            self._update_preview()
        if self.current_data is not None:
            self.accept()

    def _cleanup_ai_tempdir(self):
        tempdir = self._ai_tempdir
        self._ai_tempdir = None
        if tempdir is not None:
            try:
                tempdir.cleanup()
            except Exception:
                pass

    def accept(self):
        result = super(ReliefImageDialog, self).accept()
        self._cleanup_ai_tempdir()
        return result

    def reject(self):
        process = self._ai_process
        if process is not None:
            try:
                process.kill()
                process.waitForFinished(1000)
            except Exception:
                pass
            self._ai_process = None
        result = super(ReliefImageDialog, self).reject()
        self._cleanup_ai_tempdir()
        return result


__all__ = ["ReliefImageDialog"]
