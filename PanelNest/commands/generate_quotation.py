"""Gerar orçamento/cotação do projeto para o cliente.

Abre dialog para configurar margem e custos adicionais,
depois exporta HTML profissional do orçamento.
"""
import os
import webbrowser

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

COMMAND_NAME = "PanelNest_GenerateQuotation"


def _main_window():
    if Gui is not None and hasattr(Gui, "getMainWindow"):
        try:
            return Gui.getMainWindow()
        except Exception:
            return None
    return None


class _QuotationDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PanelNest — Gerar Orçamento")
        self.setMinimumWidth(450)
        self._extra_rows = []
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(8)

        # Margem
        margin_group = QtWidgets.QGroupBox("Margem de Lucro")
        mg_layout = QtWidgets.QHBoxLayout(margin_group)
        mg_layout.addWidget(QtWidgets.QLabel("Margem (%):"))
        self._margin_spin = QtWidgets.QDoubleSpinBox()
        self._margin_spin.setRange(0, 200)
        self._margin_spin.setValue(0)
        self._margin_spin.setSuffix(" %")
        mg_layout.addWidget(self._margin_spin)
        layout.addWidget(margin_group)

        # Custos adicionais
        extras_group = QtWidgets.QGroupBox("Custos Adicionais (mão de obra, frete, etc.)")
        eg_layout = QtWidgets.QVBoxLayout(extras_group)

        self._extras_table = QtWidgets.QTableWidget(0, 2)
        self._extras_table.setHorizontalHeaderLabels(["Descrição", "Valor (R$)"])
        self._extras_table.horizontalHeader().setStretchLastSection(True)
        self._extras_table.setMinimumHeight(100)
        eg_layout.addWidget(self._extras_table)

        btn_row = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("+ Adicionar")
        add_btn.clicked.connect(self._add_extra_row)
        remove_btn = QtWidgets.QPushButton("- Remover")
        remove_btn.clicked.connect(self._remove_extra_row)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch()
        eg_layout.addLayout(btn_row)
        layout.addWidget(extras_group)

        # Botões
        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.button(QtWidgets.QDialogButtonBox.Ok).setText("Gerar Orçamento")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _add_extra_row(self):
        row = self._extras_table.rowCount()
        self._extras_table.insertRow(row)
        self._extras_table.setItem(row, 0, QtWidgets.QTableWidgetItem(""))
        val_item = QtWidgets.QTableWidgetItem("0")
        val_item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self._extras_table.setItem(row, 1, val_item)

    def _remove_extra_row(self):
        row = self._extras_table.currentRow()
        if row >= 0:
            self._extras_table.removeRow(row)

    def margin_pct(self):
        return self._margin_spin.value()

    def extra_costs(self):
        result = []
        for row in range(self._extras_table.rowCount()):
            desc_item = self._extras_table.item(row, 0)
            val_item = self._extras_table.item(row, 1)
            desc = desc_item.text().strip() if desc_item else ""
            try:
                val = float((val_item.text() if val_item else "0").replace(",", "."))
            except ValueError:
                val = 0.0
            if desc or val > 0:
                result.append({"descricao": desc or f"Item {row + 1}", "valor": val})
        return result


class GenerateQuotationCommand:
    def Activated(self):
        if Gui is None or App is None:
            return

        # Verificar se tem layout gerado
        try:
            import panelnest
            parts = panelnest.collect_parts()
            if not parts:
                QtWidgets.QMessageBox.warning(
                    _main_window(), "PanelNest",
                    "Nenhuma peça encontrada. Etiquete as peças primeiro."
                )
                return

            settings = panelnest.get_sheet_settings()
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                _main_window(), "PanelNest", f"Erro ao coletar dados: {e}"
            )
            return

        # Verificar se layout já foi gerado
        try:
            layout_sheets = panelnest.create_layout_sheets(parts, settings)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                _main_window(), "PanelNest",
                f"Erro ao gerar layout para orçamento:\n{e}\n\n"
                "Configure a chapa e gere o layout primeiro."
            )
            return

        # Dialog de configuração
        dlg = _QuotationDialog(_main_window())
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        margin_pct = dlg.margin_pct()
        extra_costs = dlg.extra_costs()

        # Gerar dados e HTML
        try:
            from panelnest.quotation import build_quotation_data, build_quotation_html
            data = build_quotation_data(parts, layout_sheets, settings)
            html = build_quotation_html(data, margin_pct=margin_pct, extra_costs=extra_costs)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                _main_window(), "PanelNest", f"Erro ao gerar orçamento: {e}"
            )
            return

        # Salvar
        file_dialog = QtWidgets.QFileDialog(_main_window())
        doc_label = getattr(App.ActiveDocument, "Label", "orcamento")
        default_name = f"{doc_label}_orcamento_panelnest.html"
        path, _ = file_dialog.getSaveFileName(
            _main_window(),
            "Salvar Orçamento",
            default_name,
            "HTML (*.html)",
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                _main_window(), "PanelNest", f"Erro ao salvar: {e}"
            )
            return

        App.Console.PrintMessage(f"PanelNest: orçamento salvo em {path}\n")

        # Abrir no navegador
        try:
            webbrowser.open(f"file://{os.path.abspath(path)}")
        except Exception:
            pass

    def IsActive(self):
        return (
            Gui is not None
            and App is not None
            and App.ActiveDocument is not None
        )

    def GetResources(self):
        import panelnest
        return {
            "Pixmap": panelnest.get_icon_path("panelnest_export.svg"),
            "MenuText": "Gerar Orçamento",
            "ToolTip": (
                "Gera orçamento/cotação do projeto em HTML.\n"
                "Inclui custo de material, fita de borda, ferragens e margem de lucro."
            ),
        }


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, GenerateQuotationCommand())
