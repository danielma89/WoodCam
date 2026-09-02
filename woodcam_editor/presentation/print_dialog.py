"""Compact TechDraw paper/scale chooser for Editor 2D printing."""

from __future__ import annotations

from dataclasses import dataclass

from woodcam_editor.application.print_layout import (
    ISO_PAPERS,
    recommend_paper,
    resolve_print_layouts,
)

from .compat import QtWidgets, qt_enum
from .i18n import translate_text


@dataclass(frozen=True)
class PrintSetupOptions:
    all_sheets: bool
    paper_name: str
    scale_mode: str
    custom_scale_percent: float
    include_sheet_fill: bool
    include_toolpath: bool


class PrintSetupDialog(QtWidgets.QDialog):
    def __init__(
        self,
        sheet_bounds,
        *,
        active_sheet_index=0,
        toolpath_available=False,
        parent=None,
    ):
        super(PrintSetupDialog, self).__init__(parent)
        self._sheet_bounds = tuple(tuple(map(float, value)) for value in sheet_bounds)
        if not self._sheet_bounds:
            raise ValueError("Nenhuma chapa configurada para impressão.")
        self._active_sheet_index = max(
            0, min(int(active_sheet_index), len(self._sheet_bounds) - 1)
        )
        self._recommended = recommend_paper(
            (self._sheet_bounds[self._active_sheet_index],)
        )
        self.setWindowTitle(translate_text("Enviar para impressão — TechDraw"))
        self.setModal(True)
        self.resize(560, 390)
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(10)

        introduction = QtWidgets.QLabel(
            translate_text(
                "O TechDraw receberá uma cópia vetorial da chapa, com seu limite e todo o conteúdo visível. O desenho e o CAM não serão alterados."
            ),
            self,
        )
        introduction.setWordWrap(True)
        root.addWidget(introduction)

        form = QtWidgets.QFormLayout()
        self.scope_combo = QtWidgets.QComboBox(self)
        self.scope_combo.addItem(translate_text("Somente a chapa ativa"), False)
        self.scope_combo.addItem(translate_text("Todas as chapas — uma página por chapa"), True)
        self.scope_combo.setEnabled(len(self._sheet_bounds) > 1)
        form.addRow(translate_text("Conteúdo"), self.scope_combo)

        self.paper_combo = QtWidgets.QComboBox(self)
        self._refresh_paper_choices(select_recommended=True)
        form.addRow(translate_text("Papel"), self.paper_combo)

        self.scale_mode_combo = QtWidgets.QComboBox(self)
        self.scale_mode_combo.addItem(
            translate_text("Ajustar a chapa inteira em uma página"), "fit"
        )
        self.scale_mode_combo.addItem(translate_text("Tamanho real — 1:1"), "actual")
        self.scale_mode_combo.addItem(translate_text("Escala personalizada"), "custom")
        form.addRow(translate_text("Escala"), self.scale_mode_combo)

        self.custom_scale = QtWidgets.QDoubleSpinBox(self)
        self.custom_scale.setRange(0.01, 1000.0)
        self.custom_scale.setDecimals(2)
        self.custom_scale.setSingleStep(5.0)
        self.custom_scale.setValue(100.0)
        self.custom_scale.setSuffix(" %")
        self.custom_scale.setEnabled(False)
        form.addRow(translate_text("Percentual"), self.custom_scale)
        root.addLayout(form)

        options_box = QtWidgets.QGroupBox(translate_text("Aparência"), self)
        options_layout = QtWidgets.QVBoxLayout(options_box)
        self.sheet_fill = QtWidgets.QCheckBox(
            translate_text("Manter o fundo azul-claro da chapa"), options_box
        )
        self.sheet_fill.setChecked(True)
        options_layout.addWidget(self.sheet_fill)
        self.toolpath = QtWidgets.QCheckBox(
            translate_text("Incluir o percurso 2D que está visível"), options_box
        )
        self.toolpath.setChecked(bool(toolpath_available))
        self.toolpath.setEnabled(bool(toolpath_available))
        options_layout.addWidget(self.toolpath)
        root.addWidget(options_box)

        self.summary = QtWidgets.QLabel(self)
        self.summary.setObjectName("printLayoutSummary")
        self.summary.setWordWrap(True)
        self.summary.setMinimumHeight(58)
        self.summary.setStyleSheet(
            "QLabel { background: #f8fafc; border: 1px solid #cbd5e1; "
            "border-radius: 4px; padding: 7px; color: #334155; }"
        )
        root.addWidget(self.summary)

        self.buttons = QtWidgets.QDialogButtonBox(
            qt_enum(QtWidgets.QDialogButtonBox, "Ok", "StandardButton")
            | qt_enum(QtWidgets.QDialogButtonBox, "Cancel", "StandardButton"),
            parent=self,
        )
        self.ok_button = self.buttons.button(
            qt_enum(QtWidgets.QDialogButtonBox, "Ok", "StandardButton")
        )
        if self.ok_button is not None:
            self.ok_button.setText(translate_text("Criar no TechDraw"))
        cancel = self.buttons.button(
            qt_enum(QtWidgets.QDialogButtonBox, "Cancel", "StandardButton")
        )
        if cancel is not None:
            cancel.setText(translate_text("Cancelar"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self.scope_combo.currentIndexChanged.connect(self._scope_changed)
        self.paper_combo.currentIndexChanged.connect(self._update_summary)
        self.scale_mode_combo.currentIndexChanged.connect(self._scale_mode_changed)
        self.custom_scale.valueChanged.connect(self._update_summary)
        self._update_summary()

    def _selected_bounds(self):
        if bool(self.scope_combo.currentData()):
            return self._sheet_bounds
        return (self._sheet_bounds[self._active_sheet_index],)

    def _refresh_paper_choices(self, *, select_recommended=False):
        current_name = str(self.paper_combo.currentData() or "")
        self.paper_combo.blockSignals(True)
        self.paper_combo.clear()
        recommended_index = 0
        for index, paper in enumerate(ISO_PAPERS):
            label = "%s — %.0f × %.0f mm" % (
                paper.name,
                paper.width_mm,
                paper.height_mm,
            )
            if paper.name == self._recommended.name:
                label += "  " + translate_text("(sugerido)")
                recommended_index = index
            self.paper_combo.addItem(label, paper.name)
        if select_recommended or not current_name:
            self.paper_combo.setCurrentIndex(recommended_index)
        else:
            matching = next(
                (
                    index
                    for index in range(self.paper_combo.count())
                    if str(self.paper_combo.itemData(index)) == current_name
                ),
                recommended_index,
            )
            self.paper_combo.setCurrentIndex(matching)
        self.paper_combo.blockSignals(False)

    def _scope_changed(self):
        self._recommended = recommend_paper(self._selected_bounds())
        # Scope is a new printing decision.  Start it on the paper that makes
        # sense for that scope; the operator can still choose any other page.
        self._refresh_paper_choices(select_recommended=True)
        self._update_summary()

    def _scale_mode_changed(self):
        self.custom_scale.setEnabled(self.scale_mode_combo.currentData() == "custom")
        self._update_summary()

    def _update_summary(self):
        layouts = resolve_print_layouts(
            self._selected_bounds(),
            str(self.paper_combo.currentData()),
            scale_mode=str(self.scale_mode_combo.currentData()),
            custom_scale_percent=float(self.custom_scale.value()),
        )
        fits = all(layout.fits for layout in layouts)
        minimum_percent = min(layout.scale_percent for layout in layouts)
        rotated_count = sum(layout.rotation_degrees == 90 for layout in layouts)
        paper_name = str(self.paper_combo.currentData())
        if fits:
            text = translate_text(
                "%d página(s) %s; escala mínima %.1f%%. A chapa será girada em %d página(s) para aproveitar melhor o papel."
            ) % (len(layouts), paper_name, minimum_percent, rotated_count)
        else:
            maximum = min(layout.fit_scale * 100.0 for layout in layouts)
            text = translate_text(
                "Essa escala não cabe no papel %s. Use Ajustar à página ou no máximo %.1f%%."
            ) % (paper_name, maximum)
        self.summary.setText(text)
        if fits:
            self.summary.setStyleSheet(
                "QLabel { background: #f0fdf4; border: 1px solid #86efac; "
                "border-radius: 4px; padding: 7px; color: #166534; }"
            )
        else:
            self.summary.setStyleSheet(
                "QLabel { background: #fef2f2; border: 1px solid #fca5a5; "
                "border-radius: 4px; padding: 7px; color: #991b1b; }"
            )
        if self.ok_button is not None:
            self.ok_button.setEnabled(fits)

    def options(self):
        return PrintSetupOptions(
            all_sheets=bool(self.scope_combo.currentData()),
            paper_name=str(self.paper_combo.currentData()),
            scale_mode=str(self.scale_mode_combo.currentData()),
            custom_scale_percent=float(self.custom_scale.value()),
            include_sheet_fill=bool(self.sheet_fill.isChecked()),
            include_toolpath=bool(self.toolpath.isChecked()),
        )


__all__ = ["PrintSetupDialog", "PrintSetupOptions"]
