"""Pure tests for bitmap height maps and closed relief meshes."""

from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from PIL import Image

from woodcam_relief.heightmap import (
    ReliefOptions,
    build_relief_mesh,
    heightmap_png,
    load_heightmap,
    shaded_preview_png,
)
from woodcam_relief.freecad_adapter import _preview_normals


class ImageReliefTests(unittest.TestCase):
    def _image(self, directory):
        path = Path(directory) / "height.png"
        image = Image.new("L", (3, 2))
        image.putdata((0, 64, 255, 255, 128, 0))
        image.save(path)
        return path

    def test_heightmap_preserves_levels_and_can_invert(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._image(directory)
            options = ReliefOptions(
                width_mm=30,
                height_mm=20,
                relief_height_mm=10,
                base_thickness_mm=2,
                auto_levels=False,
                smoothing_radius_px=0,
                relief_mode="heightmap",
            )
            normal = load_heightmap(path, options)
            inverted = load_heightmap(path, ReliefOptions(**{**options.__dict__, "invert": True}))
        self.assertEqual(normal.pixels, bytes((0, 64, 255, 255, 128, 0)))
        self.assertEqual(inverted.pixels, bytes(255 - value for value in normal.pixels))
        self.assertEqual(len(normal.source_sha256), 64)
        self.assertTrue(heightmap_png(normal).startswith(b"\x89PNG"))
        self.assertTrue(shaded_preview_png(normal).startswith(b"\x89PNG"))

    def test_closed_mesh_uses_mm_origin_base_and_relief_height(self):
        with tempfile.TemporaryDirectory() as directory:
            data = load_heightmap(
                self._image(directory),
                ReliefOptions(
                    width_mm=30,
                    height_mm=20,
                    relief_height_mm=10,
                    base_thickness_mm=2,
                    origin_x_mm=5,
                    origin_y_mm=-4,
                    auto_levels=False,
                    smoothing_radius_px=0,
                    relief_mode="heightmap",
                    mesh_resolution=24,
                ),
            )
        mesh = build_relief_mesh(data)
        xs = [vertex[0] for vertex in mesh.vertices]
        ys = [vertex[1] for vertex in mesh.vertices]
        zs = [vertex[2] for vertex in mesh.vertices]
        self.assertEqual((mesh.columns, mesh.rows), (3, 2))
        self.assertEqual((min(xs), max(xs)), (5.0, 35.0))
        self.assertEqual((min(ys), max(ys)), (-4.0, 16.0))
        self.assertEqual(min(zs), 0.0)
        self.assertEqual(max(zs), 12.0)
        self.assertEqual(len(mesh.vertices), 12)
        self.assertEqual(mesh.facet_count, 20)

        normals, indices = _preview_normals(mesh)
        self.assertEqual(len(indices), mesh.facet_count * 4)
        for normal in normals[: mesh.columns * mesh.rows]:
            length = sum(value * value for value in normal) ** 0.5
            self.assertAlmostEqual(length, 1.0)
            self.assertGreater(normal[2], 0.0)

    def test_invalid_dimensions_are_rejected(self):
        with self.assertRaises(ValueError):
            ReliefOptions(width_mm=0)
        with self.assertRaises(ValueError):
            ReliefOptions(base_thickness_mm=-1)

    def test_photo_volume_rounds_subject_instead_of_extruding_texture_only(self):
        from PIL import ImageDraw

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "subject.png"
            image = Image.new("L", (61, 41), 255)
            ImageDraw.Draw(image).ellipse((5, 5, 55, 35), fill=45)
            image.save(path)
            data = load_heightmap(
                path,
                ReliefOptions(
                    width_mm=61,
                    height_mm=41,
                    relief_height_mm=6,
                    invert=True,
                    auto_levels=False,
                    smoothing_radius_px=0,
                    relief_mode="photo_volume",
                    detail_strength=0,
                    background_threshold=8,
                ),
            )
        background = data.value(0, 0)
        boundary = data.value(5, 20)
        center = data.value(30, 20)
        self.assertEqual(background, 0)
        self.assertGreater(boundary, 0)
        self.assertGreater(center, boundary * 4)
        self.assertGreaterEqual(center, 250)

    def test_transparent_pixels_always_remain_on_base(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cutout.png"
            image = Image.new("RGBA", (21, 15), (240, 240, 240, 0))
            for y in range(4, 11):
                for x in range(5, 16):
                    image.putpixel((x, y), (80 + x * 5, 80 + x * 5, 80 + x * 5, 255))
            image.save(path)
            common = dict(
                width_mm=21,
                height_mm=15,
                relief_height_mm=6,
                auto_levels=True,
                smoothing_radius_px=1,
                relief_mode="aspire_bitmap",
            )
            normal = load_heightmap(path, ReliefOptions(**common))
            inverted = load_heightmap(path, ReliefOptions(**{**common, "invert": True}))

        for data in (normal, inverted):
            self.assertEqual(data.value(0, 0), 0)
            self.assertEqual(data.value(20, 14), 0)
            self.assertGreater(max(data.pixels), 0)

    def test_component_mode_reduces_texture_amplitude_but_keeps_body(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "textured-subject.png"
            image = Image.new("L", (81, 51), 0)
            for y in range(8, 43):
                for x in range(10, 71):
                    image.putpixel((x, y), 220 if (x + y) % 2 else 80)
            image.save(path)
            common = dict(
                width_mm=81,
                height_mm=51,
                relief_height_mm=3,
                auto_levels=False,
                smoothing_radius_px=0,
                background_threshold=8,
            )
            literal = load_heightmap(
                path, ReliefOptions(**{**common, "relief_mode": "heightmap"})
            )
            component = load_heightmap(
                path,
                ReliefOptions(
                    **{
                        **common,
                        "relief_mode": "aspire_bitmap",
                        "detail_strength": 0.25,
                        "form_smoothing_radius_px": 6,
                    }
                ),
            )

        literal_delta = abs(literal.value(40, 25) - literal.value(41, 25))
        component_delta = abs(component.value(40, 25) - component.value(41, 25))
        self.assertLess(component_delta, literal_delta * 0.5)
        self.assertGreater(component.value(40, 25), 0)
        self.assertEqual(component.value(0, 0), 0)

    def test_component_background_flood_does_not_punch_dark_internal_holes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "eye.png"
            image = Image.new("L", (41, 31), 0)
            for y in range(5, 26):
                for x in range(6, 35):
                    image.putpixel((x, y), 180)
            image.putpixel((20, 15), 0)
            image.save(path)
            data = load_heightmap(
                path,
                ReliefOptions(
                    width_mm=41,
                    height_mm=31,
                    relief_height_mm=3,
                    auto_levels=False,
                    smoothing_radius_px=0,
                    relief_mode="aspire_bitmap",
                    detail_strength=0.45,
                    form_smoothing_radius_px=5,
                    background_threshold=8,
                ),
            )

        self.assertEqual(data.value(0, 0), 0)
        self.assertGreater(data.value(20, 15), 0)

    def test_component_edge_feather_rises_inside_without_background_halo(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plateau.png"
            image = Image.new("L", (31, 21), 0)
            for y in range(4, 17):
                for x in range(5, 26):
                    image.putpixel((x, y), 180)
            image.save(path)
            data = load_heightmap(
                path,
                ReliefOptions(
                    width_mm=31,
                    height_mm=21,
                    relief_height_mm=3,
                    auto_levels=False,
                    smoothing_radius_px=0,
                    relief_mode="aspire_bitmap",
                    form_smoothing_radius_px=0,
                    edge_feather_radius_px=3,
                    background_threshold=20,
                ),
            )

        self.assertEqual(data.value(4, 10), 0)
        self.assertGreater(data.value(5, 10), 0)
        self.assertLess(data.value(5, 10), data.value(6, 10))
        self.assertLess(data.value(6, 10), data.value(8, 10))

    def test_component_lissage_reduces_spikes_without_bleeding_outside_mask(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lissage.png"
            image = Image.new("L", (51, 35), 0)
            for y in range(6, 29):
                for x in range(8, 43):
                    image.putpixel((x, y), 205 if (x + y) % 2 else 55)
            image.save(path)
            common = dict(
                width_mm=51,
                height_mm=35,
                relief_height_mm=3,
                auto_levels=False,
                relief_mode="aspire_bitmap",
                form_smoothing_radius_px=3,
                detail_strength=0.8,
                structure_strength=1.0,
                edge_feather_radius_px=2,
                sculptural_volume_strength=0,
                background_threshold=10,
            )
            raw = load_heightmap(
                path,
                ReliefOptions(**{**common, "smoothing_radius_px": 0}),
            )
            smoothed = load_heightmap(
                path,
                ReliefOptions(**{**common, "smoothing_radius_px": 1.5}),
            )

        raw_delta = abs(raw.value(25, 17) - raw.value(26, 17))
        smooth_delta = abs(smoothed.value(25, 17) - smoothed.value(26, 17))
        self.assertLess(smooth_delta, raw_delta * 0.25)
        self.assertEqual(smoothed.value(0, 0), 0)
        self.assertEqual(smoothed.value(7, 17), 0)
        self.assertGreater(smoothed.value(8, 17), 0)

    def test_sculptural_volume_lifts_subject_center_without_touching_background(self):
        from PIL import ImageDraw

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "silhouette.png"
            image = Image.new("L", (61, 41), 0)
            ImageDraw.Draw(image).ellipse((5, 5, 55, 35), fill=120)
            image.save(path)
            common = dict(
                width_mm=61,
                height_mm=41,
                relief_height_mm=3,
                auto_levels=False,
                smoothing_radius_px=0,
                relief_mode="aspire_bitmap",
                form_smoothing_radius_px=5,
                detail_strength=0,
                structure_strength=0,
                edge_feather_radius_px=3,
            )
            flat = load_heightmap(
                path,
                ReliefOptions(**{**common, "sculptural_volume_strength": 0}),
            )
            sculpted = load_heightmap(
                path,
                ReliefOptions(**{**common, "sculptural_volume_strength": 0.6}),
            )

        self.assertEqual(sculpted.value(0, 0), 0)
        self.assertGreater(sculpted.value(30, 20), flat.value(30, 20))
        self.assertGreater(sculpted.value(30, 20), sculpted.value(6, 20) * 3)

    def test_height_compensation_keeps_texture_physical_amplitude_under_control(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fur.png"
            image = Image.new("L", (81, 51), 0)
            for y in range(7, 44):
                for x in range(9, 72):
                    image.putpixel((x, y), 210 if (x + y) % 2 else 70)
            image.save(path)
            common = dict(
                width_mm=81,
                height_mm=51,
                auto_levels=False,
                smoothing_radius_px=0,
                relief_mode="aspire_bitmap",
                form_smoothing_radius_px=5,
                detail_strength=0.45,
                structure_strength=0.85,
                sculptural_volume_strength=0.35,
                compensate_height=True,
            )
            low = load_heightmap(path, ReliefOptions(**{**common, "relief_height_mm": 3}))
            high = load_heightmap(path, ReliefOptions(**{**common, "relief_height_mm": 10}))

        low_delta_mm = 3.0 * abs(low.value(40, 25) - low.value(41, 25)) / 255.0
        high_delta_mm = 10.0 * abs(high.value(40, 25) - high.value(41, 25)) / 255.0
        self.assertLessEqual(high_delta_mm, low_delta_mm * 1.35)
        self.assertGreater(
            10.0 * high.value(40, 25) / 255.0,
            3.0 * low.value(40, 25) / 255.0,
        )


if __name__ == "__main__":
    unittest.main()
