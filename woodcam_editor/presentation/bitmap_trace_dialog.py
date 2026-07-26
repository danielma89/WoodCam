"""Interactive black/white bitmap tracing parameters and live mask preview."""

from __future__ import annotations

from pathlib import Path

from woodcam_editor.importers.bitmap_trace import (
    BitmapTraceOptions,
    threshold_preview_png,
)

from .compat import QtCore, QtGui, QtWidgets, qt_enum


class BitmapTraceDialog(QtWidgets.QDialog):
    def __init__(self, image_path, parent=None):
        super(BitmapTraceDialog, self).__init__(parent)
        self.image_path = str(image_path)
        self.setWindowTitle("Vetorizar imagem")
        self.setModal(True)
        self.resize(780, 520)
        root = QtWidgets.QVBoxLayout(self)

        title = QtWidgets.QLabel(
            "Ajuste o que ficará preto. Somente as regiões pretas serão convertidas em vetores fechados.",
            self,
        )
        title.setWordWrap(True)
        root.addWidget(title)

        previews = QtWidgets.QHBoxLayout()
        self.original_label = self._preview_label("Imagem original")
        self.mask_label = self._preview_label("Máscara a vetorizar")
        previews.addWidget(self.original_label, 1)
        previews.addWidget(self.mask_label, 1)
        root.addLayout(previews, 1)

        form = QtWidgets.QFormLayout()
        self.threshold = QtWidgets.QSlider(
            qt_enum(QtCore.Qt, "Horizontal", "Orientation"), self
        )
        self.threshold.setRange(0, 255)
        self.threshold.setValue(128)
        self.threshold_value = QtWidgets.QLabel("128", self)
        threshold_row = QtWidgets.QHBoxLayout()
        threshold_row.addWidget(self.threshold, 1)
        threshold_row.addWidget(self.threshold_value)
        form.addRow("Limiar claro/escuro", threshold_row)

        self.invert = QtWidgets.QCheckBox("Vetorizar regiões claras", self)
        self.invert.setToolTip("Desmarcado vetoriza pixels escuros; marcado vetoriza pixels claros.")
        form.addRow("Inverter", self.invert)

        self.noise = QtWidgets.QSpinBox(self)
        self.noise.setRange(0, 100000)
        self.noise.setValue(2)
        self.noise.setSuffix(" px")
        self.noise.setToolTip("Descarta pequenas manchas com área até este número de pixels.")
        form.addRow("Filtro de ruído", self.noise)

        self.corner_fit = QtWidgets.QDoubleSpinBox(self)
        self.corner_fit.setRange(0.0, 1.34)
        self.corner_fit.setDecimals(2)
        self.corner_fit.setSingleStep(0.05)
        self.corner_fit.setValue(1.0)
        self.corner_fit.setToolTip("Menor preserva mais cantos; maior suaviza a passagem.")
        form.addRow("Ajuste de cantos", self.corner_fit)

        self.curve_tolerance = QtWidgets.QDoubleSpinBox(self)
        self.curve_tolerance.setRange(0.0, 2.0)
        self.curve_tolerance.setDecimals(3)
        self.curve_tolerance.setSingleStep(0.05)
        self.curve_tolerance.setValue(0.2)
        form.addRow("Suavização de curvas", self.curve_tolerance)

        self.target_width = QtWidgets.QDoubleSpinBox(self)
        self.target_width.setRange(0.1, 100000.0)
        self.target_width.setDecimals(2)
        self.target_width.setValue(200.0)
        self.target_width.setSuffix(" mm")
        form.addRow("Largura final", self.target_width)
        root.addLayout(form)

        crop_box = QtWidgets.QGroupBox("Recortar imagem antes de vetorizar", self)
        crop_form = QtWidgets.QGridLayout(crop_box)
        self.crop_enabled = QtWidgets.QCheckBox("Usar somente esta área", crop_box)
        self.crop_enabled.setToolTip(
            "O recorte é feito nos pixels da imagem original; o arquivo nunca é alterado."
        )
        crop_form.addWidget(self.crop_enabled, 0, 0, 1, 4)
        self.crop_x = self._crop_field(crop_box)
        self.crop_y = self._crop_field(crop_box)
        self.crop_width = self._crop_field(crop_box, minimum=1)
        self.crop_height = self._crop_field(crop_box, minimum=1)
        for column, label in enumerate(("X", "Y", "Largura", "Altura")):
            crop_form.addWidget(QtWidgets.QLabel(label, crop_box), 1, column)
        for column, field in enumerate(
            (self.crop_x, self.crop_y, self.crop_width, self.crop_height)
        ):
            crop_form.addWidget(field, 2, column)
        root.addWidget(crop_box)

        self.status = QtWidgets.QLabel(
            "A confirmação seguinte aparecerá no topo do Editor como prévia magenta.", self
        )
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        buttons = QtWidgets.QDialogButtonBox(
            qt_enum(QtWidgets.QDialogButtonBox, "Ok", "StandardButton")
            | qt_enum(QtWidgets.QDialogButtonBox, "Cancel", "StandardButton"),
            parent=self,
        )
        ok = buttons.button(qt_enum(QtWidgets.QDialogButtonBox, "Ok", "StandardButton"))
        if ok is not None:
            ok.setText("Gerar prévia vetorial")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.threshold.valueChanged.connect(self._update_preview)
        self.invert.toggled.connect(self._update_preview)
        self.crop_enabled.toggled.connect(self._update_crop_enabled)
        for field in (self.crop_x, self.crop_y, self.crop_width, self.crop_height):
            field.valueChanged.connect(self._update_preview)
        self._load_original()
        self._update_crop_enabled(False)
        self._update_preview()

    def _preview_label(self, text):
        label = QtWidgets.QLabel(text, self)
        label.setAlignment(qt_enum(QtCore.Qt, "AlignCenter", "AlignmentFlag"))
        label.setMinimumSize(300, 220)
        label.setFrameShape(QtWidgets.QFrame.StyledPanel)
        label.setStyleSheet("QLabel { background: #f8fafc; color: #475569; }")
        return label

    @staticmethod
    def _crop_field(parent, minimum=0):
        field = QtWidgets.QSpinBox(parent)
        field.setRange(int(minimum), 1)
        field.setSuffix(" px")
        return field

    def _scaled(self, pixmap):
        return pixmap.scaled(
            self.original_label.size() - QtCore.QSize(12, 12),
            qt_enum(QtCore.Qt, "KeepAspectRatio", "AspectRatioMode"),
            qt_enum(QtCore.Qt, "SmoothTransformation", "TransformationMode"),
        )

    def _load_original(self):
        self._original_pixmap = QtGui.QPixmap(self.image_path)
        pixmap = self._original_pixmap
        if pixmap.isNull():
            self.original_label.setText("Não foi possível visualizar %s" % Path(self.image_path).name)
        else:
            self.crop_x.setRange(0, max(0, pixmap.width() - 1))
            self.crop_y.setRange(0, max(0, pixmap.height() - 1))
            self.crop_width.setRange(1, max(1, pixmap.width()))
            self.crop_height.setRange(1, max(1, pixmap.height()))
            self.crop_width.setValue(max(1, pixmap.width()))
            self.crop_height.setValue(max(1, pixmap.height()))
            self._update_original_preview()

    def _update_crop_enabled(self, enabled):
        for field in (self.crop_x, self.crop_y, self.crop_width, self.crop_height):
            field.setEnabled(bool(enabled))
        self._update_preview()

    def _crop_values(self):
        if not self.crop_enabled.isChecked():
            return 0, 0, 0, 0
        return (
            int(self.crop_x.value()),
            int(self.crop_y.value()),
            int(self.crop_width.value()),
            int(self.crop_height.value()),
        )

    def _refresh_crop_limits(self):
        pixmap = getattr(self, "_original_pixmap", None)
        if pixmap is None or pixmap.isNull():
            return
        max_width = max(1, pixmap.width() - self.crop_x.value())
        max_height = max(1, pixmap.height() - self.crop_y.value())
        self.crop_width.setMaximum(max_width)
        self.crop_height.setMaximum(max_height)

    def _update_original_preview(self):
        pixmap = getattr(self, "_original_pixmap", None)
        if pixmap is None or pixmap.isNull():
            return
        crop_x, crop_y, crop_width, crop_height = self._crop_values()
        if crop_width and crop_height:
            pixmap = pixmap.copy(crop_x, crop_y, crop_width, crop_height)
        self.original_label.setPixmap(self._scaled(pixmap))

    def options(self):
        return BitmapTraceOptions(
            threshold=int(self.threshold.value()),
            invert=bool(self.invert.isChecked()),
            noise_pixels=int(self.noise.value()),
            corner_fit=float(self.corner_fit.value()),
            curve_tolerance=float(self.curve_tolerance.value()),
            target_width_mm=float(self.target_width.value()),
            crop_x_px=self._crop_values()[0],
            crop_y_px=self._crop_values()[1],
            crop_width_px=self._crop_values()[2],
            crop_height_px=self._crop_values()[3],
        )

    def _update_preview(self, *_args):
        self.threshold_value.setText(str(int(self.threshold.value())))
        try:
            self._refresh_crop_limits()
            self._update_original_preview()
            data = threshold_preview_png(self.image_path, self.options())
            pixmap = QtGui.QPixmap()
            pixmap.loadFromData(data, "PNG")
            self.mask_label.setPixmap(self._scaled(pixmap))
            self.status.setText(
                "Preto = vetor. O recorte só afeta a prévia e a vetorização; a imagem original não muda."
            )
        except Exception as error:
            self.mask_label.setText("Prévia indisponível")
            self.status.setText(str(error))


__all__ = ["BitmapTraceDialog"]
