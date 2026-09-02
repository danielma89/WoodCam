import unittest

from presets import normalize_work_area_presets


class WorkAreaPresetTests(unittest.TestCase):
    def test_normalizes_order_and_rejects_invalid_or_duplicate_names(self):
        values = normalize_work_area_presets(
            [
                {"name": "MDF inteiro", "width": 1850, "height": 2750, "depth": 15},
                {"name": "mdf INTEIRO", "width": 100, "height": 100, "depth": 0},
                {"name": "Meia chapa", "width": "1375", "height": "1850", "depth": 0},
                {"name": "Inválido", "width": 0, "height": 500, "depth": 0},
            ]
        )

        self.assertEqual([item["name"] for item in values], ["MDF inteiro", "Meia chapa"])
        self.assertEqual(values[0]["width"], 1850.0)
        self.assertEqual(values[1]["height"], 1850.0)

    def test_accepts_legacy_name_keyed_dictionary(self):
        values = normalize_work_area_presets(
            {
                "Mesa pequena": {"width": 600, "height": 900, "depth": 18},
                "Preferência quebrada": "não é um dicionário",
            }
        )

        self.assertEqual(
            values,
            [{"name": "Mesa pequena", "width": 600.0, "height": 900.0, "depth": 18.0}],
        )


if __name__ == "__main__":
    unittest.main()
