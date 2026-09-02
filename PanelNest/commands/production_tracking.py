"""Comando de rastreio de produção do PanelNest.

Abre um painel que permite:
- Criar ordens de produção a partir das peças do projeto
- Acompanhar o status de cada peça (Pendente → Cortada → Fitada → Furada → Montada → Entregue)
- Ver progresso geral da ordem
- Atualizar status em lote (selecionar várias peças e mudar estado)
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
    from PySide import QtGui, QtCore
    QtWidgets = QtGui
except ImportError:
    try:
        from PySide2 import QtWidgets, QtCore, QtGui
    except ImportError:
        from PySide6 import QtWidgets, QtCore, QtGui

COMMAND_NAME = "PanelNest_ProductionTracking"


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


def _project_name():
    """Retorna o nome do projeto atual."""
    if App is not None and App.ActiveDocument is not None:
        return App.ActiveDocument.Name
    return "SemProjeto"


class ProductionTrackingDialog(QtWidgets.QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PanelNest — Rastreio de Producao")
        self.setMinimumSize(900, 600)
        self._conn = None
        self._current_order_id = None
        self._build_ui()
        self._open_db()
        self._reload_orders()

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)

        # Barra superior — dashboard
        self._dashboard_label = QtWidgets.QLabel("")
        self._dashboard_label.setStyleSheet("font-size: 13px; padding: 4px;")
        root.addWidget(self._dashboard_label)

        # Splitter: ordens à esquerda, peças à direita
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        # Painel esquerdo — ordens
        left = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 4, 0)

        lv.addWidget(QtWidgets.QLabel("<b>Ordens de Producao</b>"))

        self._order_list = QtWidgets.QListWidget()
        self._order_list.setMaximumWidth(250)
        self._order_list.currentRowChanged.connect(self._on_order_selected)
        lv.addWidget(self._order_list)

        btn_row = QtWidgets.QHBoxLayout()
        btn_new = QtWidgets.QPushButton("+ Nova Ordem")
        btn_new.clicked.connect(self._on_new_order)
        btn_row.addWidget(btn_new)

        btn_del = QtWidgets.QPushButton("Remover")
        btn_del.setStyleSheet("color: #c0392b;")
        btn_del.clicked.connect(self._on_delete_order)
        btn_row.addWidget(btn_del)
        lv.addLayout(btn_row)

        splitter.addWidget(left)

        # Painel direito — peças e status
        right = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right)
        rv.setContentsMargins(4, 0, 0, 0)

        # Barra de progresso
        self._progress_bar = QtWidgets.QProgressBar()
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%v/%m pecas entregues (%p%)")
        rv.addWidget(self._progress_bar)

        # Barra de ações em lote
        action_row = QtWidgets.QHBoxLayout()
        action_row.addWidget(QtWidgets.QLabel("Alterar selecionadas para:"))
        self._state_combo = QtWidgets.QComboBox()
        from panelnest.production_tracking import PRODUCTION_STATES
        for s in PRODUCTION_STATES:
            self._state_combo.addItem(s)
        action_row.addWidget(self._state_combo)

        btn_apply = QtWidgets.QPushButton("Aplicar")
        btn_apply.clicked.connect(self._on_batch_apply)
        action_row.addWidget(btn_apply)

        action_row.addStretch()

        # Operador
        action_row.addWidget(QtWidgets.QLabel("Operador:"))
        self._operator_edit = QtWidgets.QLineEdit()
        self._operator_edit.setPlaceholderText("Nome (opcional)")
        self._operator_edit.setMaximumWidth(150)
        action_row.addWidget(self._operator_edit)
        rv.addLayout(action_row)

        # Tabela de peças
        self._table = QtWidgets.QTableWidget()
        self._table.setColumnCount(9)
        self._table.setHorizontalHeaderLabels([
            "", "ID", "Pecas", "Material", "Esp.", "Comp.", "Larg.", "Qtd", "Status"
        ])
        self._table.setColumnWidth(0, 30)
        self._table.setColumnWidth(1, 70)
        self._table.setColumnWidth(2, 200)
        self._table.setColumnWidth(3, 100)
        self._table.setColumnWidth(4, 50)
        self._table.setColumnWidth(5, 60)
        self._table.setColumnWidth(6, 60)
        self._table.setColumnWidth(7, 40)
        self._table.setColumnWidth(8, 100)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setStretchLastSection(True)
        rv.addWidget(self._table)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        # Botões inferiores
        bottom = QtWidgets.QHBoxLayout()

        btn_mark_complete = QtWidgets.QPushButton("Concluir Ordem")
        btn_mark_complete.clicked.connect(self._on_complete_order)
        bottom.addWidget(btn_mark_complete)

        bottom.addStretch()

        btn_close = QtWidgets.QPushButton("Fechar")
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

    def _open_db(self):
        from panelnest.production_tracking import open_production_db
        self._conn = open_production_db()

    def _reload_orders(self):
        from panelnest.production_tracking import load_orders, project_dashboard

        project = _project_name()
        orders = load_orders(self._conn, project)

        self._order_list.blockSignals(True)
        self._order_list.clear()
        self._order_ids = []
        for o in orders:
            status_icon = {
                "Aberta": "",
                "Em andamento": "",
                "Concluida": "",
            }.get(o.status, "")
            self._order_list.addItem(
                f"{status_icon} #{o.order_id} — {o.created_at[:10]}  [{o.status}]"
            )
            self._order_ids.append(o.order_id)
        self._order_list.blockSignals(False)

        # Dashboard
        dash = project_dashboard(self._conn, project)
        self._dashboard_label.setText(
            f"<b>{project}</b> | "
            f"Ordens ativas: {dash['active_orders']} | "
            f"Pecas: {dash['delivered_pieces']}/{dash['total_pieces']} "
            f"({dash['progress_pct']:.0f}% entregues)"
        )

        if self._order_ids:
            self._order_list.setCurrentRow(0)
        else:
            self._current_order_id = None
            self._table.setRowCount(0)
            self._progress_bar.setMaximum(1)
            self._progress_bar.setValue(0)

    def _on_order_selected(self, index):
        if index < 0 or index >= len(self._order_ids):
            self._current_order_id = None
            self._table.setRowCount(0)
            return

        self._current_order_id = self._order_ids[index]
        self._reload_parts()

    def _reload_parts(self):
        from panelnest.production_tracking import (
            load_order_parts, order_summary, STATE_COLORS,
        )

        if self._current_order_id is None:
            return

        parts = load_order_parts(self._conn, self._current_order_id)
        summary = order_summary(self._conn, self._current_order_id)

        # Progresso
        total = summary["total_quantity"]
        done = summary["by_state"].get("Entregue", {}).get("quantity", 0)
        self._progress_bar.setMaximum(max(total, 1))
        self._progress_bar.setValue(done)

        # Tabela
        self._table.setRowCount(len(parts))
        self._part_rows = []
        for row, p in enumerate(parts):
            self._part_rows.append(p.row_id)

            # Checkbox
            cb = QtWidgets.QTableWidgetItem()
            cb.setFlags(
                cb.flags() | QtCore.Qt.ItemIsUserCheckable
            )
            cb.setCheckState(QtCore.Qt.Unchecked)
            self._table.setItem(row, 0, cb)

            self._table.setItem(row, 1, QtWidgets.QTableWidgetItem(p.part_id))
            self._table.setItem(row, 2, QtWidgets.QTableWidgetItem(p.label))
            self._table.setItem(row, 3, QtWidgets.QTableWidgetItem(p.material))
            self._table.setItem(row, 4, QtWidgets.QTableWidgetItem(f"{p.thickness_mm:.0f}"))
            self._table.setItem(row, 5, QtWidgets.QTableWidgetItem(f"{p.length_mm:.0f}"))
            self._table.setItem(row, 6, QtWidgets.QTableWidgetItem(f"{p.width_mm:.0f}"))
            self._table.setItem(row, 7, QtWidgets.QTableWidgetItem(str(p.quantity)))

            # Status com cor
            state_item = QtWidgets.QTableWidgetItem(p.state)
            color = STATE_COLORS.get(p.state, "#95a5a6")
            state_item.setForeground(QtGui.QColor(color))
            font = state_item.font()
            font.setBold(True)
            state_item.setFont(font)
            self._table.setItem(row, 8, state_item)

    def _on_batch_apply(self):
        from panelnest.production_tracking import batch_update_state

        if self._current_order_id is None:
            return

        new_state = self._state_combo.currentText()
        operator = self._operator_edit.text().strip()
        selected_ids = []

        for row in range(self._table.rowCount()):
            cb = self._table.item(row, 0)
            if cb is not None and cb.checkState() == QtCore.Qt.Checked:
                if row < len(self._part_rows):
                    selected_ids.append(self._part_rows[row])

        if not selected_ids:
            QtWidgets.QMessageBox.information(
                self, "PanelNest",
                "Selecione pelo menos uma peca (checkbox) para alterar o status."
            )
            return

        batch_update_state(self._conn, selected_ids, new_state, operator)
        self._reload_parts()
        self._reload_orders()

    def _on_new_order(self):
        from panelnest.production_tracking import create_order, add_parts_to_order
        import panelnest

        project = _project_name()

        # Tentar coletar peças do projeto atual
        parts = []
        try:
            doc = App.ActiveDocument if App is not None else None
            if doc is not None:
                collected = panelnest.collect_parts()
                parts = collected
        except Exception:
            pass

        if not parts:
            reply = QtWidgets.QMessageBox.question(
                self, "Criar ordem",
                "Nenhuma peca encontrada no projeto atual.\n"
                "Deseja criar uma ordem vazia?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            )
            if reply != QtWidgets.QMessageBox.Yes:
                return

        notes, ok = QtWidgets.QInputDialog.getText(
            self, "Nova Ordem", "Observacoes (opcional):"
        )
        if not ok:
            return

        order_id = create_order(self._conn, project, notes or "")
        if parts:
            add_parts_to_order(self._conn, order_id, parts)

        self._reload_orders()
        # Selecionar a ordem recém-criada
        if self._order_ids:
            self._order_list.setCurrentRow(0)

        QtWidgets.QMessageBox.information(
            self, "PanelNest",
            f"Ordem #{order_id} criada com {len(parts)} pecas."
        )

    def _on_delete_order(self):
        from panelnest.production_tracking import delete_order

        if self._current_order_id is None:
            return

        reply = QtWidgets.QMessageBox.question(
            self, "Remover ordem",
            f"Remover a ordem #{self._current_order_id} e todas as pecas?\n"
            "Esta acao nao pode ser desfeita.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return

        delete_order(self._conn, self._current_order_id)
        self._current_order_id = None
        self._reload_orders()

    def _on_complete_order(self):
        from panelnest.production_tracking import update_order_status

        if self._current_order_id is None:
            return

        reply = QtWidgets.QMessageBox.question(
            self, "Concluir ordem",
            f"Marcar a ordem #{self._current_order_id} como concluida?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return

        update_order_status(self._conn, self._current_order_id, "Concluida")
        self._reload_orders()

    def closeEvent(self, event):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Comando FreeCAD
# ---------------------------------------------------------------------------

class ProductionTrackingCommand:
    def Activated(self):
        dlg = ProductionTrackingDialog(_main_window())
        _exec_dialog(dlg)

    def IsActive(self):
        return App is not None

    def GetResources(self):
        return {
            "Pixmap": os.path.join(
                os.path.dirname(__file__), "..", "resources", "icons",
                "panelnest_validate.svg"
            ),
            "Accel": "",
            "MenuText": "Rastreio de Producao",
            "ToolTip": (
                "Acompanha o status de cada peca no fluxo de producao.\n"
                "Crie ordens, atualize status e acompanhe o progresso."
            ),
        }


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, ProductionTrackingCommand())
