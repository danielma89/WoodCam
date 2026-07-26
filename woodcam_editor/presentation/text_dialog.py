"""Small, explicit dialog for creating portable text outlines."""

from __future__ import annotations

from .compat import QtWidgets, qt_enum
from .text_vector import TextVectorOptions


class TextVectorDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, options=None):
        super(TextVectorDialog, self).__init__(parent)
        self.setWindowTitle("Criar texto vetorial")
        self.setModal(True)
        self.resize(460, 300)
        root = QtWidgets.QVBoxLayout(self)

        help_text = QtWidgets.QLabel(
            "O texto será convertido em contornos fechados no documento. "
            "Depois pode ser movido, combinado, usinado e exportado sem depender da fonte instalada.",
            self,
        )
        help_text.setWordWrap(True)
        root.addWidget(help_text)

        form = QtWidgets.QFormLayout()
        self.text = QtWidgets.QPlainTextEdit(self)
        self.text.setPlaceholderText("Digite o texto")
        self.text.setPlainText(str(getattr(options, "text", "Texto")))
        self.text.setFixedHeight(82)
        form.addRow("Texto", self.text)

        self.font = QtWidgets.QFontComboBox(self)
        self.font.setCurrentFont(self.font.currentFont())
        family = str(getattr(options, "family", "") or "")
        if family:
            from .compat import QtGui

            self.font.setCurrentFont(QtGui.QFont(family))
        form.addRow("Fonte", self.font)

        self.height = QtWidgets.QDoubleSpinBox(self)
        self.height.setRange(0.01, 100000.0)
        self.height.setDecimals(2)
        self.height.setValue(float(getattr(options, "height_mm", 20.0)))
        self.height.setSuffix(" mm")
        self.height.setToolTip("Altura visível final do bloco de texto.")
        form.addRow("Altura final", self.height)

        styles = QtWidgets.QHBoxLayout()
        self.bold = QtWidgets.QCheckBox("Negrito", self)
        self.italic = QtWidgets.QCheckBox("Itálico", self)
        self.bold.setChecked(bool(getattr(options, "bold", False)))
        self.italic.setChecked(bool(getattr(options, "italic", False)))
        styles.addWidget(self.bold)
        styles.addWidget(self.italic)
        styles.addStretch(1)
        form.addRow("Estilo", styles)
        root.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(
            qt_enum(QtWidgets.QDialogButtonBox, "Ok", "StandardButton")
            | qt_enum(QtWidgets.QDialogButtonBox, "Cancel", "StandardButton"),
            parent=self,
        )
        ok = buttons.button(qt_enum(QtWidgets.QDialogButtonBox, "Ok", "StandardButton"))
        if ok is not None:
            ok.setText("Criar curvas")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def options(self):
        font = self.font.currentFont()
        return TextVectorOptions(
            text=self.text.toPlainText(),
            family=str(font.family()),
            height_mm=float(self.height.value()),
            bold=bool(self.bold.isChecked()),
            italic=bool(self.italic.isChecked()),
        )


__all__ = ["TextVectorDialog"]
