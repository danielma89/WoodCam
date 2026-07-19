import unittest

from validators import validate_selected_contours, validate_settings


def _settings(tool_diameter=6.0, compensate_external=True, cut_side=None, **overrides):
    settings = {
        "tool_diameter": tool_diameter,
        "compensate_external": compensate_external,
    }
    if cut_side is not None:
        settings["cut_side"] = cut_side
    settings.update(overrides)
    return settings


def _full_settings(**overrides):
    settings = {
        "job_type": "single_sided",
        "z_zero_mode": "material_surface",
        "material_thickness": 15.0,
        "depth_extra": 0.0,
        "tool_diameter": 6.0,
        "stepdown": 3.0,
        "feed_xy": 1800.0,
        "feed_z": 500.0,
        "rapid_feed": 4000.0,
        "safe_height": 8.0,
        "retract_height": 15.0,
        "ramp_length": 30.0,
        "helix_pitch": 1.0,
        "helix_stepover_percent": 40.0,
        "rpm": 18000,
        "simulation_speed_multiplier": 60.0,
        "machine_x_size": 0.0,
        "machine_y_size": 0.0,
        "start_x": 0.0,
        "start_y": 0.0,
        "job_width": 0.0,
        "job_height": 0.0,
        "cut_side": "outside",
        "operation_mode": "cut",
        "start_depth": 0.0,
        "cut_depth": 15.5,
        "final_depth": 15.5,
    }
    settings.update(overrides)
    return settings


class ValidatorsTest(unittest.TestCase):
    def test_rapid_feed_has_no_arbitrary_upper_limit(self):
        validate_settings(_full_settings(rapid_feed=18000.0))

    def test_external_cut_blocks_touching_contours(self):
        first = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        second = [(0.0, 50.0), (100.0, 50.0), (100.0, 100.0), (0.0, 100.0)]

        with self.assertRaisesRegex(ValueError, "Espaçamento insuficiente"):
            validate_selected_contours([first, second], _settings(tool_diameter=6.0))

    def test_external_cut_blocks_gap_smaller_than_tool_diameter(self):
        first = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        second = [(0.0, 55.0), (100.0, 55.0), (100.0, 100.0), (0.0, 100.0)]

        with self.assertRaisesRegex(ValueError, "5.00 mm"):
            validate_selected_contours([first, second], _settings(tool_diameter=6.0))

    def test_external_cut_allows_gap_equal_to_tool_diameter(self):
        first = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        second = [(0.0, 56.0), (100.0, 56.0), (100.0, 100.0), (0.0, 100.0)]

        validate_selected_contours([first, second], _settings(tool_diameter=6.0))

    def test_external_cut_allows_wider_gap(self):
        first = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        second = [(0.0, 62.0), (100.0, 62.0), (100.0, 100.0), (0.0, 100.0)]

        validate_selected_contours([first, second], _settings(tool_diameter=6.0))

    def test_external_cut_blocks_nested_contours(self):
        first = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
        second = [(25.0, 25.0), (75.0, 25.0), (75.0, 75.0), (25.0, 75.0)]

        with self.assertRaisesRegex(ValueError, "0.00 mm"):
            validate_selected_contours([first, second], _settings(tool_diameter=6.0))

    def test_clearance_validation_is_disabled_without_external_compensation(self):
        first = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        second = [(0.0, 50.0), (100.0, 50.0), (100.0, 100.0), (0.0, 100.0)]

        validate_selected_contours([first, second], _settings(compensate_external=False))

    def test_clearance_validation_is_disabled_for_on_line_and_internal_cut(self):
        first = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        second = [(0.0, 50.0), (100.0, 50.0), (100.0, 100.0), (0.0, 100.0)]

        validate_selected_contours([first, second], _settings(cut_side="on_line"))
        validate_selected_contours([first, second], _settings(cut_side="inside"))

    def test_machine_area_uses_visible_settings_instead_of_hidden_limit(self):
        contour = [(0.0, 0.0), (2750.0, 0.0), (2750.0, 100.0), (0.0, 100.0)]

        validate_selected_contours(
            [contour],
            _settings(machine_x_size=2750.0, machine_y_size=1850.0),
        )

        with self.assertRaisesRegex(ValueError, "Área útil X"):
            validate_selected_contours(
                [contour],
                _settings(machine_x_size=1300.0, machine_y_size=1850.0),
            )

    def test_machine_area_has_no_hidden_limit_when_not_configured(self):
        contour = [(0.0, 0.0), (5000.0, 0.0), (5000.0, 100.0), (0.0, 100.0)]
        validate_selected_contours([contour], _settings())

    def test_machine_area_checks_the_complete_job_span(self):
        first = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        second = [(1400.0, 0.0), (1500.0, 0.0), (1500.0, 50.0), (1400.0, 50.0)]

        with self.assertRaisesRegex(ValueError, "conjunto selecionado"):
            validate_selected_contours(
                [first, second],
                _settings(
                    cut_side="on_line",
                    machine_x_size=1300.0,
                    machine_y_size=1850.0,
                ),
            )

    def test_machine_bed_z_zero_validates_machine_coordinates(self):
        validate_settings(_full_settings(z_zero_mode="machine_bed"))

        with self.assertRaisesRegex(ValueError, "maximo de Z"):
            validate_settings(
                _full_settings(
                    z_zero_mode="machine_bed",
                    material_thickness=70.0,
                    retract_height=15.0,
                )
            )

    def test_unsupported_job_types_are_blocked(self):
        with self.assertRaisesRegex(ValueError, "face única"):
            validate_settings(_full_settings(job_type="double_sided"))

    def test_3d_stepover_and_strategy_are_validated(self):
        validate_settings(
            _full_settings(
                operation_mode="rough3d",
                rough3d_strategy="z_level",
                rough3d_allowance=0.5,
                rough3d_stepover_percent=40.0,
            )
        )
        validate_settings(
            _full_settings(
                operation_mode="finish3d",
                finish3d_strategy="raster",
                finish3d_stepover_percent=10.0,
                finish3d_raster_angle=45.0,
            )
        )
        with self.assertRaisesRegex(ValueError, "passo lateral do acabamento"):
            validate_settings(
                _full_settings(
                    operation_mode="finish3d",
                    finish3d_strategy="raster",
                    finish3d_stepover_percent=0.0,
                )
            )


if __name__ == "__main__":
    unittest.main()
