"""Presentation-only tests for the global PanelNest language selector."""

from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6 import QtWidgets
    from PySide6.QtTest import QTest
except ImportError:  # pragma: no cover - FreeCAD/PySide2-only installations
    try:
        from PySide2 import QtWidgets
        from PySide2.QtTest import QTest
    except ImportError:  # pragma: no cover - CI without Qt
        QtWidgets = None
        QTest = None


@unittest.skipIf(QtWidgets is None, "PySide indisponível")
class PanelNestLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from panelnest.i18n import install_language_menu

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.install_language_menu = install_language_menu

    def setUp(self):
        from panelnest.i18n import set_language

        set_language("pt")
        self.window = QtWidgets.QMainWindow()
        self.menu_bar = self.window.menuBar()
        self.panel_menu = self.menu_bar.addMenu("PanelNest")
        self.panel_menu.addAction("Catálogo de Móveis")
        self.panel_menu.addAction("Importar Peças do CSV")
        self.panel_menu.addAction("Aplicar Dados")
        self.menu_bar.addMenu("Janelas")
        self.menu_bar.addMenu("Ajuda")
        self.language_menu = type(self).install_language_menu(self.window)

    def tearDown(self):
        from panelnest.i18n import set_language

        set_language("pt")
        self.window.close()

    def test_fresh_install_uses_english_and_keeps_saved_language(self):
        from panelnest.i18n import _stored_language

        preferences = {}

        class Parameters:
            def __init__(self, path):
                self.path = path

            def GetString(self, key, default=""):
                return preferences.get((self.path, key), default)

        fake_freecad = types.SimpleNamespace(ParamGet=Parameters)
        with patch.dict(sys.modules, {"FreeCAD": fake_freecad}):
            self.assertEqual(_stored_language(), "en")
            preferences[("User parameter:BaseApp/Preferences/WoodCAM2D", "language")] = "pt"
            self.assertEqual(_stored_language(), "pt")
            preferences[("User parameter:BaseApp/Preferences/PanelNest", "language")] = "en"
            self.assertEqual(_stored_language(), "en")

    def test_global_menu_translates_commands_and_restores_without_mutation(self):
        from panelnest.i18n import set_language

        set_language("en")
        self.assertEqual(
            [action.text() for action in self.panel_menu.actions()],
            ["Furniture Catalog", "Import Parts from CSV", "Apply Data"],
        )
        menu_titles = {menu.title() for menu in self.menu_bar.findChildren(QtWidgets.QMenu)}
        self.assertTrue({"Language", "Windows", "Help"}.issubset(menu_titles))

        set_language("pt")
        self.assertEqual(
            [action.text() for action in self.panel_menu.actions()],
            ["Catálogo de Móveis", "Importar Peças do CSV", "Aplicar Dados"],
        )

    def test_new_dialog_is_translated_when_shown_without_periodic_polling(self):
        from panelnest import i18n
        from panelnest.i18n import set_language

        set_language("en")
        dialog = QtWidgets.QDialog()
        button = QtWidgets.QPushButton("Aplicar Dados", dialog)
        button.show()
        dialog.show()
        QTest.qWait(50)
        self.assertEqual(button.text(), "Apply Data")
        self.assertIsNone(getattr(i18n, "_timer", None))

        button.setText("Aplicar Dados")
        QTest.qWait(850)
        self.assertEqual(button.text(), "Aplicar Dados")
        dialog.close()

    def test_same_language_refresh_does_not_emit_combo_text_change(self):
        from panelnest.i18n import set_language, translate_widget_tree

        combo = QtWidgets.QComboBox(self.window)
        combo.addItem("Aplicar Dados")
        changes = []
        combo.currentTextChanged.connect(changes.append)

        set_language("en")
        changes.clear()
        translate_widget_tree(self.window)

        self.assertEqual(combo.currentText(), "Apply Data")
        self.assertEqual(changes, [])

    def test_freecad_rich_command_tooltip_translates_wrapped_description(self):
        from panelnest.i18n import set_language

        action = self.panel_menu.addAction("Aplicar Fita por Face")
        action.setToolTip(
            "<p style='white-space:pre; margin-bottom:0.5em;'>"
            "<b>Aplicar Fita por Face</b></p>"
            "<p style='white-space:pre; margin:0;'>"
            "Resolve faces laterais selecionadas para os lados da peca e "
            "marca, remove</p>ou alterna a fita de borda."
            "<p style='white-space:pre; margin-top:0.5em;'>"
            "<i>PanelNest_ApplyEdgeBandByFace</i></p>"
        )

        set_language("en")

        tooltip = action.toolTip()
        self.assertIn("<b>Apply Edge Band by Face</b>", tooltip)
        self.assertIn(
            "Resolves selected side faces to the part sides and marks, "
            "removes or toggles edge banding.",
            tooltip,
        )
        self.assertIn("PanelNest_ApplyEdgeBandByFace", tooltip)
        self.assertNotIn("Resolve faces laterais", tooltip)

        multiline = self.panel_menu.addAction("Aplicar Material")
        multiline.setToolTip(
            "<p style='white-space:pre; margin-bottom:0.5em;'>"
            "<b>Aplicar Material</b></p>"
            "<p style='white-space:pre; margin:0;'>"
            "Aplica cor de material às peças selecionadas.</p>"
            "Compatível com a visualização de fita de borda."
            "<p style='white-space:pre; margin-top:0.5em;'>"
            "<i>PanelNest_ApplyMaterial</i></p>"
        )
        from panelnest.i18n import translate_widget_tree

        translate_widget_tree(self.window)
        tooltip = multiline.toolTip()
        self.assertIn("Applies a material color to selected parts.", tooltip)
        self.assertIn("Compatible with edge-band visualization.", tooltip)
        self.assertNotIn("Aplica cor de material", tooltip)

    def test_embedded_translator_owns_marked_subtree(self):
        from panelnest.i18n import set_language

        dialog = QtWidgets.QDialog()
        dialog.setProperty("woodcam_i18n_owned", True)
        button = QtWidgets.QPushButton("Aplicar Dados", dialog)
        button.show()
        dialog.show()
        set_language("en")
        QTest.qWait(850)
        self.assertEqual(button.text(), "Aplicar Dados")
        dialog.close()
