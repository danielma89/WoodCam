"""Offscreen contract tests for the Editor 2D TechDraw print assistant."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6 import QtWidgets
except ImportError:  # pragma: no cover - depends on the FreeCAD Python runtime
    QtWidgets = None


@unittest.skipIf(QtWidgets is None, "PySide6 indisponível")
class PrintSetupDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from woodcam_editor.presentation.print_dialog import PrintSetupDialog

        cls.PrintSetupDialog = PrintSetupDialog
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.dialog = self.PrintSetupDialog(
            ((0, 0, 200, 100), (300, 0, 2080, 2725)),
            active_sheet_index=0,
            toolpath_available=True,
        )

    def tearDown(self):
        self.dialog.close()
        self.app.processEvents()

    def test_active_sheet_starts_with_its_smallest_suggested_paper(self):
        self.assertEqual(self.dialog.paper_combo.currentData(), "A4")
        self.assertIn("sugerido", self.dialog.paper_combo.currentText())
        self.assertTrue(self.dialog.ok_button.isEnabled())

    def test_all_sheets_updates_suggestion_and_creates_one_page_each(self):
        self.dialog.scope_combo.setCurrentIndex(1)
        self.app.processEvents()
        self.assertEqual(self.dialog.paper_combo.currentData(), "A0")
        self.assertIn("sugerido", self.dialog.paper_combo.currentText())
        options = self.dialog.options()
        self.assertTrue(options.all_sheets)
        self.assertEqual(options.paper_name, "A0")

    def test_one_to_one_disables_creation_when_sheet_does_not_fit(self):
        self.dialog.scope_combo.setCurrentIndex(1)
        self.dialog.scale_mode_combo.setCurrentIndex(1)
        self.app.processEvents()
        self.assertFalse(self.dialog.ok_button.isEnabled())
        self.assertIn("não cabe", self.dialog.summary.text())

    def test_visible_toolpath_is_an_explicit_option(self):
        self.assertTrue(self.dialog.toolpath.isEnabled())
        self.assertTrue(self.dialog.options().include_toolpath)
        self.dialog.toolpath.setChecked(False)
        self.assertFalse(self.dialog.options().include_toolpath)

    def test_editor_file_menu_exposes_the_print_assistant_signal(self):
        from woodcam_editor.domain import VectorDocument, WorkArea
        from woodcam_editor.presentation.widget import Editor2DWidget

        widget = Editor2DWidget(
            VectorDocument.create_default(WorkArea(0, 0, 300, 200))
        )
        emitted = []
        widget.printTechDrawRequested.connect(lambda: emitted.append(True))
        action = next(
            action
            for action in widget.file_menu_button.menu().actions()
            if "TechDraw" in action.text()
        )
        action.trigger()
        self.app.processEvents()
        self.assertEqual(emitted, [True])
        widget.close()

    def test_print_assistant_is_fully_english_when_language_is_english(self):
        from woodcam_editor.presentation import i18n

        with patch.object(i18n, "_language", "en"):
            dialog = self.PrintSetupDialog(((0, 0, 200, 100),))
            self.assertEqual(dialog.windowTitle(), "Send to print — TechDraw")
            self.assertEqual(dialog.scope_combo.currentText(), "Active sheet only")
            self.assertIn("recommended", dialog.paper_combo.currentText())
            self.assertEqual(dialog.ok_button.text(), "Create in TechDraw")
            self.assertNotIn("chapa", dialog.summary.text().lower())
            dialog.close()


if __name__ == "__main__":
    unittest.main()
