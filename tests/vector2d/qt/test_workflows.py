"""Qt tests for reusable validation and preview workflow widgets."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6 import QtCore, QtTest, QtWidgets
except ImportError:  # pragma: no cover - depends on the FreeCAD runtime
    try:
        from PySide2 import QtCore, QtTest, QtWidgets
    except ImportError:  # pragma: no cover
        QtCore = QtTest = QtWidgets = None


@unittest.skipIf(QtWidgets is None, "PySide/QtTest indisponível")
class WorkflowWidgetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from woodcam_editor.domain import Severity, ValidationIssue, Vec2
        from woodcam_editor.presentation.workflows import (
            ValidationReportDialog,
            ValidationReportPanel,
            WorkflowPreviewBar,
        )

        cls.Severity = Severity
        cls.ValidationIssue = ValidationIssue
        cls.Vec2 = Vec2
        cls.ValidationReportDialog = ValidationReportDialog
        cls.ValidationReportPanel = ValidationReportPanel
        cls.WorkflowPreviewBar = WorkflowPreviewBar
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def issues(self):
        return (
            self.ValidationIssue(
                id="issue-open",
                severity=self.Severity.BLOCKER,
                code="OPEN_PATH",
                message="O caminho está aberto.",
                entity_ids=("path-1",),
                points=(self.Vec2(10.0, 20.0),),
            ),
            self.ValidationIssue(
                id="issue-outside",
                severity=self.Severity.WARNING,
                code="OUTSIDE_WORK_AREA",
                message="A entidade está fora da área de Trabalho.",
                entity_ids=("path-2",),
            ),
        )

    def test_validation_panel_lists_details_and_emits_exact_issue(self):
        issues = self.issues()
        panel = self.ValidationReportPanel(issues)
        panel.resize(720, 300)
        panel.show()
        self.app.processEvents()
        self.assertEqual(panel.issues, issues)
        self.assertEqual(panel.issue_tree.topLevelItemCount(), 2)
        first = panel.issue_tree.topLevelItem(0)
        self.assertEqual(first.text(0), "Bloqueio")
        self.assertEqual(first.text(1), "OPEN_PATH")
        self.assertEqual(first.text(2), "O caminho está aberto.")
        selected = []
        activated = []
        panel.issueSelected.connect(selected.append)
        panel.issueActivated.connect(activated.append)
        self.assertTrue(panel.select_issue("issue-open"))
        self.app.processEvents()
        self.assertIs(panel.current_issue(), issues[0])
        self.assertEqual(selected, [issues[0]])
        panel.issue_tree.itemActivated.emit(first, 0)
        self.assertEqual(activated, [issues[0]])
        self.assertFalse(panel.select_issue("missing"))
        panel.close()

    def test_validation_dialog_forwards_selection_and_close_button(self):
        issues = self.issues()
        dialog = self.ValidationReportDialog(issues)
        forwarded = []
        dialog.issueSelected.connect(forwarded.append)
        dialog.show()
        self.app.processEvents()
        self.assertTrue(dialog.select_issue("issue-outside"))
        self.app.processEvents()
        self.assertEqual(forwarded, [issues[1]])
        self.assertIs(dialog.current_issue(), issues[1])
        self.assertIsNotNone(dialog.close_button)
        QtTest.QTest.mouseClick(
            dialog.close_button,
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoModifier,
        )
        self.app.processEvents()
        self.assertFalse(dialog.isVisible())

    def test_validation_panel_empty_state_and_clear(self):
        panel = self.ValidationReportPanel(self.issues())
        panel.show()
        panel.clear()
        self.app.processEvents()
        self.assertEqual(panel.issues, ())
        self.assertFalse(panel.issue_tree.isVisible())
        self.assertTrue(panel.empty_label.isVisible())
        self.assertIn("nenhuma ocorrência", panel.summary_label.text().lower())
        panel.close()

    def test_preview_bar_begin_signals_payload_and_clear(self):
        host = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(host)
        bar = self.WorkflowPreviewBar(host)
        layout.addWidget(bar)
        host.show()
        self.app.processEvents()
        self.assertFalse(bar.is_active)
        self.assertTrue(bar.isHidden())

        payload = {"preview_id": "preview-1"}
        applied = []
        cancelled = []
        active_states = []
        bar.applyRequested.connect(applied.append)
        bar.cancelRequested.connect(cancelled.append)
        bar.activeChanged.connect(active_states.append)
        bar.begin(
            "3 conexões sugeridas",
            payload,
            apply_label="Aplicar 3",
            cancel_label="Descartar",
        )
        self.app.processEvents()
        self.assertTrue(bar.is_active)
        self.assertFalse(bar.isHidden())
        self.assertIs(bar.payload, payload)
        self.assertEqual(bar.apply_button.text(), "Aplicar 3")
        self.assertEqual(bar.cancel_button.text(), "Descartar")
        QtTest.QTest.mouseClick(
            bar.apply_button, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier
        )
        QtTest.QTest.mouseClick(
            bar.cancel_button, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier
        )
        self.assertEqual(applied, [payload])
        self.assertEqual(cancelled, [payload])
        self.assertTrue(bar.is_active)

        bar.clear()
        self.app.processEvents()
        self.assertFalse(bar.is_active)
        self.assertIsNone(bar.payload)
        self.assertTrue(bar.isHidden())
        self.assertEqual(active_states, [True, False])
        host.close()


if __name__ == "__main__":
    unittest.main()
