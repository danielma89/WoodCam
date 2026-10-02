"""Startup language must respect saved FreeCAD preferences."""

import sys
import types
import unittest
from unittest.mock import patch


class LanguageDefaultTests(unittest.TestCase):
    def test_new_woodcam_install_starts_in_english(self):
        from woodcam_editor.presentation.i18n import _stored_language

        preferences = {}

        class Parameters:
            def __init__(self, path):
                self.path = path

            def GetString(self, key, default=""):
                return preferences.get((self.path, key), default)

        with patch.dict(sys.modules, {"FreeCAD": types.SimpleNamespace(ParamGet=Parameters)}):
            self.assertEqual(_stored_language(), "en")
            preferences[("User parameter:BaseApp/Preferences/WoodCAM2D", "language")] = "pt"
            self.assertEqual(_stored_language(), "pt")
            preferences[("User parameter:BaseApp/Preferences/PanelNest", "language")] = "en"
            self.assertEqual(_stored_language(), "en")


if __name__ == "__main__":
    unittest.main()
