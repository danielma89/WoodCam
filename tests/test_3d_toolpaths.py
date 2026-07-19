import unittest

from woodcam_3d import (
    FinishingOptions,
    HeightField,
    MeshData,
    RoughingOptions,
    build_3d_finishing_moves,
    build_3d_roughing_moves,
    compensated_height_field,
    expand_height_field_support,
    extend_height_field,
    height_field_from_grayscale,
    height_field_from_mesh,
)


def ramp_mesh():
    return MeshData.create(
        [(0, 0, 0), (10, 0, 0), (0, 10, 5), (10, 10, 5)],
        [(0, 1, 2), (1, 3, 2)],
        "ramp",
    )


class Toolpath3DTests(unittest.TestCase):
    def test_preserved_grayscale_builds_cam_field_without_triangles(self):
        # Linha zero da imagem é o topo; no campo CAM ela deve virar Y máximo.
        field = height_field_from_grayscale(
            bytes((0, 64, 192, 255)),
            2,
            2,
            origin_x=10.0,
            origin_y=20.0,
            width_mm=4.0,
            height_mm=6.0,
            base_z=1.0,
            base_thickness=2.0,
            relief_height=4.0,
            sampling_mm=1.0,
            source_hash="bitmap",
        )
        self.assertEqual(field.columns, 2)
        self.assertEqual(field.rows, 2)
        self.assertAlmostEqual(field.sample(10.0, 20.0), 3.0 + 4.0 * 192.0 / 255.0)
        self.assertAlmostEqual(field.sample(14.0, 26.0), 3.0 + 4.0 * 64.0 / 255.0)
        self.assertEqual(field.source_hash, "bitmap")

    def test_mesh_projection_keeps_top_skin(self):
        mesh = MeshData.create(
            [(0, 0, 0), (10, 0, 0), (0, 10, 0), (0, 0, 3), (10, 0, 3), (0, 10, 3)],
            [(0, 1, 2), (3, 4, 5)],
        )
        field = height_field_from_mesh(mesh, sampling_mm=2.0)
        self.assertAlmostEqual(field.sample(2, 2), 3.0, places=6)

    def test_ballnose_compensation_never_goes_below_uncompensated_peak(self):
        field = height_field_from_mesh(ramp_mesh(), sampling_mm=1.0)
        compensated = compensated_height_field(field, 4.0, "ball_nose")
        self.assertGreaterEqual(compensated.sample(5, 5), field.sample(5, 5))

    def test_roughing_respects_stepdown_and_allowance(self):
        field = height_field_from_mesh(ramp_mesh(), sampling_mm=1.0)
        moves = build_3d_roughing_moves(
            field,
            RoughingOptions(2.0, stepdown=1.0, allowance=0.5, safe_height=4.0),
        )
        cut_z = [move["z"] for move in moves if move["type"] in {"feed_cut", "feed_ramp", "feed_plunge"}]
        self.assertTrue(cut_z)
        self.assertGreaterEqual(min(cut_z), -4.5 - 1e-6)
        self.assertTrue(all(move["z"] == 4.0 for move in moves if move["type"] == "rapid"))

    def test_finishing_supports_raster_and_offset(self):
        field = height_field_from_mesh(ramp_mesh(), sampling_mm=1.0)
        raster = build_3d_finishing_moves(field, FinishingOptions(2.0, 20.0, strategy="raster"))
        offset = build_3d_finishing_moves(field, FinishingOptions(2.0, 20.0, strategy="offset"))
        self.assertTrue(any(move["type"] == "feed_cut" for move in raster))
        self.assertTrue(any(move["type"] == "feed_cut" for move in offset))

    def test_raster_finishing_links_adjacent_rows_without_retracting_each_row(self):
        field = height_field_from_mesh(ramp_mesh(), sampling_mm=1.0)
        moves = build_3d_finishing_moves(
            field,
            FinishingOptions(2.0, 50.0, safe_height=5.0, strategy="raster"),
        )
        rapids = [move for move in moves if move["type"] == "rapid"]
        plunges = [move for move in moves if move["type"] == "feed_plunge"]
        self.assertEqual(len(rapids), 2)
        self.assertEqual(len(plunges), 1)
        self.assertIsNone(rapids[-1]["x"])

    def test_raster_finishing_retracts_across_disconnected_surface(self):
        field = HeightField(
            0.0,
            0.0,
            1.0,
            1.0,
            5,
            3,
            (
                1.0, 1.0, None, 1.0, 1.0,
                1.0, 1.0, None, 1.0, 1.0,
                1.0, 1.0, None, 1.0, 1.0,
            ),
            0.0,
            1.0,
            "disconnected",
        )
        moves = build_3d_finishing_moves(
            field,
            FinishingOptions(0.5, 100.0, safe_height=5.0, strategy="raster"),
        )
        rapids = [move for move in moves if move["type"] == "rapid"]
        plunges = [move for move in moves if move["type"] == "feed_plunge"]
        self.assertGreater(len(rapids), 2)
        self.assertGreater(len(plunges), 1)

    def test_polygon_boundary_clips_finishing(self):
        field = height_field_from_mesh(ramp_mesh(), sampling_mm=1.0)
        boundary = ((0, 0), (5, 0), (5, 10), (0, 10))
        moves = build_3d_finishing_moves(field, FinishingOptions(2.0, 25.0, boundary=boundary))
        cutting = [move for move in moves if move["type"].startswith("feed")]
        self.assertTrue(cutting)
        self.assertLessEqual(max(move["x"] for move in cutting), 5.0 + 1e-7)

    def test_material_boundary_extends_surface_with_flat_floor(self):
        field = height_field_from_mesh(ramp_mesh(), sampling_mm=1.0)
        extended = extend_height_field(field, (-5, -5, 15, 15))
        self.assertAlmostEqual(extended.sample(-4, -4), field.source_min_z)
        self.assertAlmostEqual(extended.sample(5, 5), field.sample(5, 5), places=5)

    def test_model_boundary_expands_silhouette_without_filling_bounding_box(self):
        field = height_field_from_mesh(
            MeshData.create(
                [(0, 0, 1), (10, 0, 1), (0, 10, 1)],
                [(0, 1, 2)],
            ),
            sampling_mm=1.0,
        )
        expanded = expand_height_field_support(field, 1.0)
        self.assertIsNotNone(expanded.sample(-0.5, 0.5))
        self.assertIsNone(expanded.sample(9.5, 9.5))


if __name__ == "__main__":
    unittest.main()
