from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from woodcam_editor.importers.bitmap_trace import (
    BitmapTraceOptions,
    threshold_mask,
    trace_bitmap,
)


class BitmapTraceTests(unittest.TestCase):
    def fixture(self, directory):
        path = Path(directory) / "fixture.png"
        image = Image.new("RGBA", (120, 80), (255, 255, 255, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 10, 55, 65), fill=(0, 0, 0, 255))
        draw.ellipse((70, 20, 105, 55), fill=(0, 0, 0, 255))
        image.save(path)
        return path

    def test_threshold_mask_selects_dark_or_light_regions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            dark = threshold_mask(path, BitmapTraceOptions(threshold=128))
            light = threshold_mask(path, BitmapTraceOptions(threshold=128, invert=True))
            self.assertEqual(dark.getpixel((20, 20)), 0)
            self.assertEqual(dark.getpixel((0, 0)), 255)
            self.assertEqual(light.getpixel((20, 20)), 255)
            self.assertEqual(light.getpixel((0, 0)), 0)

    @unittest.skipUnless(shutil.which("potrace"), "potrace indisponível")
    def test_potrace_returns_closed_curves_at_requested_width(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.fixture(directory)
            result = trace_bitmap(
                path,
                layer_id="layer",
                options=BitmapTraceOptions(
                    threshold=128,
                    noise_pixels=2,
                    target_width_mm=120.0,
                ),
            )
            self.assertGreaterEqual(len(result.entities), 2)
            self.assertTrue(all(entity.closed for entity in result.entities))
            min_x = min(entity.bounds().min_x for entity in result.entities)
            max_x = max(entity.bounds().max_x for entity in result.entities)
            self.assertAlmostEqual(max_x - min_x, 120.0, delta=0.5)
            self.assertTrue(
                all(entity.metadata["source_format"] == "bitmap_trace" for entity in result.entities)
            )


if __name__ == "__main__":
    unittest.main()
