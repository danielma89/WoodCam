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
        self._load_original()
        self._update_preview()

    def _preview_label(self, text):
        label = QtWidgets.QLabel(text, self)
        label.setAlignment(qt_enum(QtCore.Qt, "AlignCenter", "AlignmentFlag"))
        label.setMinimumSize(300, 220)
        label.setFrameShape(QtWidgets.QFrame.StyledPanel)
        label.setStyleSheet("QLabel { background: #f8fafc; color: #475569; }")
        return label

    def _scaled(self, pixmap):
        return pixmap.scaled(
            self.original_label.size() - QtCore.QSize(12, 12),
            qt_enum(QtCore.Qt, "KeepAspectRatio", "AspectRatioMode"),
            qt_enum(QtCore.Qt, "SmoothTransformation", "TransformationMode"),
        )

    def _load_original(self):
        pixmap = QtGui.QPixmap(self.image_path)
        if pixmap.isNull():
            self.original_label.setText("Não foi possível visualizar %s" % Path(self.image_path).name)
        else:
            self.original_label.setPixmap(self._scaled(pixmap))

    def options(self):
        return BitmapTraceOptions(
            threshold=int(self.threshold.value()),
            invert=bool(self.invert.isChecked()),
            noise_pixels=int(self.noise.value()),
            corner_fit=float(self.corner_fit.value()),
            curve_tolerance=float(self.curve_tolerance.value()),
            target_width_mm=float(self.target_width.value()),
        )

    def _update_preview(self, *_args):
        self.threshold_value.setText(str(int(self.threshold.value())))
        try:
            data = threshold_preview_png(self.image_path, self.options())
            pixmap = QtGui.QPixmap()
            pixmap.loadFromData(data, "PNG")
            self.mask_label.setPixmap(self._scaled(pixmap))
            self.status.setText(
                "Preto = vetor. Ajuste ruído/cantos e gere a prévia magenta no Editor."
            )
        except Exception as error:
            self.mask_label.setText("Prévia indisponível")
            self.status.setText(str(error))


__all__ = ["BitmapTraceDialog"]
