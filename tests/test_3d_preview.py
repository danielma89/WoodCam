import unittest

from woodcam_3d.preview import (
    moves_for_preview,
    preview_material_top_z,
    preview_z_offset,
)


def _settings(**overrides):
    settings = {
        "operation_mode": "rough3d",
        "material_thickness": 15.0,
        "_mesh_model_height": 15.0,
        "_mesh_source_min_z": 0.0,
        "_mesh_source_max_z": 15.0,
        "model_position_mode": "gap_above",
        "model_gap_above": 0.0,
        "model_gap_below": 0.0,
        "z_zero_mode": "material_surface",
    }
    settings.update(overrides)
    return settings


class PreviewCoordinatesTests(unittest.TestCase):
    def test_surface_zero_is_lifted_to_visible_model(self):
        settings = _settings(z_zero_mode="material_surface")
        self.assertEqual(preview_material_top_z(settings), 15.0)
        self.assertEqual(preview_z_offset(settings), 15.0)
        displayed = moves_for_preview(settings, [{"type": "feed_cut", "z": -4.0}])
        self.assertEqual(displayed[0]["z"], 11.0)

    def test_machine_bed_zero_already_matches_visible_model(self):
        settings = _settings(z_zero_mode="machine_bed")
        self.assertEqual(preview_z_offset(settings), 0.0)
        displayed = moves_for_preview(settings, [{"type": "feed_cut", "z": 11.0}])
        self.assertEqual(displayed[0]["z"], 11.0)

    def test_gap_below_aligns_both_zero_modes_to_original_source(self):
        common = {
            "material_thickness": 20.0,
            "_mesh_model_height": 15.0,
            "model_position_mode": "gap_below",
            "model_gap_below": 2.0,
        }
        surface = _settings(z_zero_mode="material_surface", **common)
        bed = _settings(z_zero_mode="machine_bed", **common)
        self.assertEqual(preview_material_top_z(surface), 18.0)
        self.assertEqual(preview_z_offset(surface), 18.0)
        self.assertEqual(preview_z_offset(bed), -2.0)
        self.assertEqual(
            moves_for_preview(surface, [{"z": -3.0}])[0]["z"],
            moves_for_preview(bed, [{"z": 17.0}])[0]["z"],
        )

    def test_source_absolute_z_is_respected_without_moving_source(self):
        settings = _settings(
            _mesh_source_min_z=40.0,
            _mesh_source_max_z=55.0,
        )
        self.assertEqual(preview_material_top_z(settings), 55.0)
        self.assertEqual(moves_for_preview(settings, [{"z": -4.0}])[0]["z"], 51.0)

    def test_source_whose_top_is_zero_keeps_that_real_coordinate(self):
        settings = _settings(
            _mesh_source_min_z=-15.0,
            _mesh_source_max_z=0.0,
        )
        self.assertEqual(preview_material_top_z(settings), 0.0)
        self.assertEqual(moves_for_preview(settings, [{"z": -4.0}])[0]["z"], -4.0)

    def test_non_3d_preview_keeps_existing_coordinate_contract(self):
        settings = _settings(operation_mode="cut", z_zero_mode="material_surface")
        self.assertEqual(preview_z_offset(settings), 0.0)
        self.assertEqual(moves_for_preview(settings, [{"z": -4.0}])[0]["z"], -4.0)


if __name__ == "__main__":
    unittest.main()
