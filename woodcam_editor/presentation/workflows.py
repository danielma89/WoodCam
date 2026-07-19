"""Reusable workflow widgets for validation and preview-first operations.

The widgets in this module intentionally know nothing about FreeCAD or vector
geometry.  Callers pass domain objects through Qt ``object`` signals and keep
authority over selection, camera movement and command execution.
"""

from __future__ import annotations

from collections import Counter

from .compat import Signal, QtCore, QtGui, QtWidgets, qt_enum


_USER_ROLE = qt_enum(QtCore.Qt, "UserRole", "ItemDataRole")


def _severity_value(issue):
    severity = getattr(issue, "severity", "info")
    return str(getattr(severity, "value", severity) or "info").lower()


def _severity_label(value):
    return {
        "blocker": "Bloqueio",
        "error": "Erro",
        "warning": "Aviso",
        "info": "Informação",
    }.get(str(value), str(value).capitalize())


def _severity_color(value):
    return {
        "blocker": "#991b1b",
        "error": "#dc2626",
        "warning": "#b45309",
        "info": "#1d4ed8",
    }.get(str(value), "#334155")


def _report_issues(report_or_issues):
    if report_or_issues is None:
        return ()
    value = getattr(report_or_issues, "issues", report_or_issues)
    return tuple(value or ())


class ValidationReportPanel(QtWidgets.QWidget):
    """Detailed, reusable list of validation issues.

    API:

    ``set_report(report_or_issues)``
        Accepts either a ``ValidationReport``-like object exposing ``issues``
        or any iterable of issue objects.
    ``clear()``
        Removes the current report.
    ``select_issue(issue_id)``
        Selects an issue by stable ID and returns whether it was found.
    ``current_issue()``
        Returns the selected issue object, or ``None``.

    ``issueSelected`` is emitted whenever list selection changes.
    ``issueActivated`` is emitted on double click or keyboard activation.
    """

    issueSelected = Signal(object)
    issueActivated = Signal(object)

    def __init__(self, report_or_issues=None, parent=None):
        super(ValidationReportPanel, self).__init__(parent)
        self._issues = ()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self.summary_label = QtWidgets.QLabel(self)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.issue_tree = QtWidgets.QTreeWidget(self)
        self.issue_tree.setObjectName("validationIssueTree")
        self.issue_tree.setHeaderLabels(("Severidade", "Código", "Mensagem"))
        self.issue_tree.setRootIsDecorated(False)
        self.issue_tree.setAlternatingRowColors(True)
        self.issue_tree.setUniformRowHeights(False)
        self.issue_tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        header = self.issue_tree.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        layout.addWidget(self.issue_tree, 1)

        self.empty_label = QtWidgets.QLabel("Nenhuma ocorrência encontrada.", self)
        self.empty_label.setAlignment(
            qt_enum(QtCore.Qt, "AlignCenter", "AlignmentFlag")
        )
        self.empty_label.setStyleSheet("color: #64748b; padding: 12px;")
        layout.addWidget(self.empty_label)

        self.issue_tree.itemSelectionChanged.connect(self._selection_changed)
        self.issue_tree.itemActivated.connect(self._item_activated)
        self.set_report(report_or_issues)

    @property
    def issues(self):
        return self._issues

    def set_report(self, report_or_issues):
        self._issues = _report_issues(report_or_issues)
        self.issue_tree.blockSignals(True)
        try:
            self.issue_tree.clear()
            for issue in self._issues:
                severity = _severity_value(issue)
                code = str(getattr(issue, "code", "") or "")
                message = str(getattr(issue, "message", "") or "")
                item = QtWidgets.QTreeWidgetItem(
                    self.issue_tree,
                    (_severity_label(severity), code, message),
                )
                item.setData(0, _USER_ROLE, issue)
                color = QtGui.QColor(_severity_color(severity))
                item.setForeground(0, QtGui.QBrush(color))
                item.setToolTip(0, _severity_label(severity))
                item.setToolTip(1, code)
                item.setToolTip(2, message)
        finally:
            self.issue_tree.blockSignals(False)
        self._refresh_summary()

    def clear(self):
        self.set_report(())

    def current_issue(self):
        item = self.issue_tree.currentItem()
        return item.data(0, _USER_ROLE) if item is not None else None

    def select_issue(self, issue_id):
        target = str(issue_id)
        for row in range(self.issue_tree.topLevelItemCount()):
            item = self.issue_tree.topLevelItem(row)
            issue = item.data(0, _USER_ROLE)
            if str(getattr(issue, "id", "")) == target:
                self.issue_tree.setCurrentItem(item)
                self.issue_tree.scrollToItem(item)
                return True
        return False

    def _selection_changed(self):
        issue = self.current_issue()
        if issue is not None:
            self.issueSelected.emit(issue)

    def _item_activated(self, item, _column=0):
        issue = item.data(0, _USER_ROLE) if item is not None else None
        if issue is not None:
            self.issueActivated.emit(issue)

    def _refresh_summary(self):
        counts = Counter(_severity_value(issue) for issue in self._issues)
        total = len(self._issues)
        if not total:
            text = "Diagnóstico concluído: nenhuma ocorrência."
        else:
            text = (
                "Diagnóstico: %d ocorrência(s) — %d bloqueio(s), %d erro(s), "
                "%d aviso(s), %d informação(ões)."
                % (
                    total,
                    counts.get("blocker", 0),
                    counts.get("error", 0),
                    counts.get("warning", 0),
                    counts.get("info", 0),
                )
            )
        self.summary_label.setText(text)
        self.issue_tree.setVisible(bool(total))
        self.empty_label.setVisible(not bool(total))


class ValidationReportDialog(QtWidgets.QDialog):
    """Thin dialog wrapper around :class:`ValidationReportPanel`."""

    issueSelected = Signal(object)
    issueActivated = Signal(object)

    def __init__(
        self,
        report_or_issues=None,
        parent=None,
        title="Diagnóstico do Editor 2D",
    ):
        super(ValidationReportDialog, self).__init__(parent)
        self.setWindowTitle(str(title))
        self.resize(760, 430)

        layout = QtWidgets.QVBoxLayout(self)
        self.panel = ValidationReportPanel(report_or_issues, self)
        layout.addWidget(self.panel, 1)

        buttons = QtWidgets.QDialogButtonBox(
            qt_enum(QtWidgets.QDialogButtonBox, "Close", "StandardButton"),
            parent=self,
        )
        self.close_button = buttons.button(
            qt_enum(QtWidgets.QDialogButtonBox, "Close", "StandardButton")
        )
        if self.close_button is not None:
            self.close_button.setText("Fechar")
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

        self.panel.issueSelected.connect(self.issueSelected.emit)
        self.panel.issueActivated.connect(self.issueActivated.emit)

    @property
    def issues(self):
        return self.panel.issues

    def set_report(self, report_or_issues):
        self.panel.set_report(report_or_issues)

    def clear(self):
        self.panel.clear()

    def current_issue(self):
        return self.panel.current_issue()

    def select_issue(self, issue_id):
        return self.panel.select_issue(issue_id)


class WorkflowPreviewBar(QtWidgets.QFrame):
    """Embeddable confirmation bar for preview-first workflows.

    The bar starts hidden. ``begin(summary, payload)`` makes it active and
    visible; ``clear()`` resets and hides it. Button presses emit the exact
    payload supplied to ``begin``. They deliberately do not clear the bar so a
    caller can keep a failed/stale preview visible and decide when it is safe
    to discard it.
    """

    applyRequested = Signal(object)
    cancelRequested = Signal(object)
    activeChanged = Signal(bool)

    def __init__(self, parent=None):
        super(WorkflowPreviewBar, self).__init__(parent)
        self._payload = None
        self._active = False
        self.setObjectName("workflowPreviewBar")
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame#workflowPreviewBar { background: #fdf4ff; "
            "border: 1px solid #d946ef; border-radius: 4px; }"
        )

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 6, 5)
        layout.setSpacing(6)
        self.summary_label = QtWidgets.QLabel(self)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label, 1)

        self.apply_button = QtWidgets.QPushButton("Aplicar", self)
        self.cancel_button = QtWidgets.QPushButton("Cancelar", self)
        self.apply_button.clicked.connect(self._request_apply)
        self.cancel_button.clicked.connect(self._request_cancel)
        layout.addWidget(self.apply_button)
        layout.addWidget(self.cancel_button)
        self.hide()

    @property
    def payload(self):
        return self._payload

    @property
    def is_active(self):
        return self._active

    def begin(
        self,
        summary,
        payload=None,
        apply_label="Aplicar",
        cancel_label="Cancelar",
    ):
        self._payload = payload
        self._active = True
        self.summary_label.setText(str(summary))
        self.apply_button.setText(str(apply_label))
        self.cancel_button.setText(str(cancel_label))
        self.apply_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.show()
        self.activeChanged.emit(True)

    def clear(self):
        was_active = self._active
        self._payload = None
        self._active = False
        self.summary_label.clear()
        self.hide()
        if was_active:
            self.activeChanged.emit(False)

    def set_apply_enabled(self, enabled):
        self.apply_button.setEnabled(bool(enabled) and self._active)

    def set_summary(self, summary):
        self.summary_label.setText(str(summary))

    def _request_apply(self):
        if self._active:
            self.applyRequested.emit(self._payload)

    def _request_cancel(self):
        if self._active:
            self.cancelRequested.emit(self._payload)


__all__ = [
    "ValidationReportDialog",
    "ValidationReportPanel",
    "WorkflowPreviewBar",
]
