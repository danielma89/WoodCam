import unittest

from cam_advisor import analyze_cam_settings


def settings(**overrides):
    result = {
        "operation_mode": "cut",
        "tool_type": "end_mill",
        "tool_diameter": 6.0,
        "stepdown": 3.0,
        "safe_height": 8.0,
        "retract_height": 15.0,
        "material_thickness": 15.0,
        "cut_depth": 15.5,
    }
    result.update(overrides)
    return result


class CAMAdvisorTests(unittest.TestCase):
    def test_normal_cut_is_clear_and_input_is_not_mutated(self):
        source = settings()
        before = dict(source)
        report = analyze_cam_settings(source)
        self.assertTrue(report.is_clear)
        self.assertEqual(source, before)

    def test_reports_tool_depth_and_safety_reasons_with_suggestions(self):
        report = analyze_cam_settings(
            settings(
                tool_type="drill",
                stepdown=8.0,
                safe_height=1.0,
                retract_height=2.0,
                cut_depth=12.0,
            )
        )
        codes = {item.code for item in report.items}
        self.assertIn("drill-side-cut", codes)
        self.assertIn("safe-height-low", codes)
        self.assertIn("retract-margin-low", codes)
        self.assertIn("cut-not-through", codes)
        self.assertGreater(report.warning_count, 0)
        self.assertIn("nenhum parâmetro", report.to_plain_text())

    def test_finish3d_advises_ball_tool_and_finer_stepover(self):
        report = analyze_cam_settings(
            settings(
                operation_mode="finish3d",
                tool_type="end_mill",
                finish3d_stepover_percent=25.0,
            )
        )
        codes = {item.code for item in report.items}
        self.assertEqual(codes, {"finish-tool", "finish-stepover-high"})
        self.assertEqual(report.recommendation_count, 2)

    def test_report_formatter_accepts_presentation_translator(self):
        report = analyze_cam_settings(
            settings(
                operation_mode="finish3d",
                tool_type="end_mill",
                finish3d_stepover_percent=25.0,
            )
        )
        translated = report.to_plain_text(
            translator=lambda value: "EN<%s>" % value
        )
        self.assertIn("[EN<SUGESTÃO>]", translated)
        self.assertIn("EN<Acabamento 3D sem fresa esférica>", translated)
        self.assertIn("EN<Valor sugerido para revisar: 10>", translated)

    def test_geometry_warns_when_tool_is_larger_than_selected_region(self):
        report = analyze_cam_settings(
            settings(operation_mode="pocket", pocket_stepover_percent=40.0),
            contours=[[(0, 0), (4, 0), (4, 10), (0, 10)]],
        )
        self.assertIn(
            "tool-larger-than-feature", {item.code for item in report.items}
        )


if __name__ == "__main__":
    unittest.main()
