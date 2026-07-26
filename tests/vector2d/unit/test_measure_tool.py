import unittest

from woodcam_editor.domain import Vec2
from woodcam_editor.presentation.tools.measure import measurement_text


class MeasureTextTests(unittest.TestCase):
    def test_reports_cartesian_distance_and_angle(self):
        text = measurement_text(Vec2(1, 2), Vec2(4, 6))
        self.assertIn("L 5.000 mm", text)
        self.assertIn("ΔX 3.000", text)
        self.assertIn("ΔY 4.000", text)
        self.assertIn("∠ 53.13°", text)

    def test_keeps_signed_deltas_and_angle(self):
        text = measurement_text(Vec2(0, 0), Vec2(-2, -2))
        self.assertIn("ΔX -2.000", text)
        self.assertIn("ΔY -2.000", text)
        self.assertIn("∠ -135.00°", text)


if __name__ == "__main__":
    unittest.main()
