"""Offscreen interaction checks for the non-modal relief controls."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image

try:
    from PySide6 import QtCore, QtTest, QtWidgets
except ImportError:  # pragma: no cover
    QtCore = QtTest = QtWidgets = None


@unittest.skipIf(QtWidgets is None, "PySide6/QtTest indisponível")
class ReliefDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from woodcam_relief.dialog import ReliefImageDialog

        cls.ReliefImageDialog = ReliefImageDialog
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_live_preview_preserves_aspect_and_emits_heightmap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gradient.png"
            image = Image.new("L", (40, 20))
            image.putdata([int(index % 40 / 39.0 * 255) for index in range(800)])
            image.save(path)
            dialog = self.ReliefImageDialog(path, default_origin=(12.0, 34.0))
            received = []
            dialog.previewChanged.connect(received.append)
            dialog.show()
            QtTest.QTest.qWait(220)
            self.assertIsNotNone(dialog.current_data)
            self.assertTrue(received)
            self.assertAlmostEqual(dialog.width_mm.value(), 200.0)
            self.assertAlmostEqual(dialog.height_mm.value(), 100.0)
            self.assertAlmostEqual(dialog.origin_x.value(), 12.0)
            self.assertAlmostEqual(dialog.origin_y.value(), 34.0)
            dialog.width_mm.setValue(300.0)
            QtTest.QTest.qWait(220)
            self.assertAlmostEqual(dialog.height_mm.value(), 150.0)
            before = dialog.current_data.pixels
            dialog.invert.setChecked(True)
            QtTest.QTest.qWait(220)
            self.assertNotEqual(dialog.current_data.pixels, before)
            dialog.reject()

    def test_light_background_is_automatically_placed_at_base_and_free_area_drags(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "subject.png"
            image = Image.new("L", (60, 40), 255)
            for y in range(12, 29):
                for x in range(15, 46):
                    image.putpixel((x, y), 45)
            image.save(path)
            host = QtWidgets.QWidget()
            host.resize(1300, 900)
            host.show()
            dialog = self.ReliefImageDialog(path, host)
            dialog.show()
            QtTest.QTest.qWait(220)
            self.assertTrue(dialog.auto_orientation.isChecked())
            self.assertTrue(dialog.invert.isChecked())
            self.assertTrue(dialog.invert.isEnabled())
            self.assertEqual(dialog.relief_mode.currentData(), "aspire_bitmap")
            self.assertIn("Recomendado", dialog.mode_hint.text())
            data = dialog.current_data
            self.assertLess(data.value(0, 0), data.value(data.width_px // 2, data.height_px // 2))
            dialog.invert.setChecked(False)
            QtTest.QTest.qWait(220)
            self.assertGreater(
                dialog.current_data.value(0, 0),
                dialog.current_data.value(
                    dialog.current_data.width_px // 2,
                    dialog.current_data.height_px // 2,
                ),
            )

            before = dialog.pos()
            start = QtCore.QPoint(8, 8)
            end = start + QtCore.QPoint(35, 24)
            QtTest.QTest.mousePress(dialog, QtCore.Qt.LeftButton, pos=start)
            QtTest.QTest.mouseMove(dialog, end, delay=20)
            QtTest.QTest.mouseRelease(dialog, QtCore.Qt.LeftButton, pos=end)
            self.app.processEvents()
            self.assertNotEqual(dialog.pos(), before)

            after_drag = dialog.pos()
            QtTest.QTest.mouseClick(
                dialog.width_mm,
                QtCore.Qt.LeftButton,
                pos=dialog.width_mm.rect().center(),
            )
            self.app.processEvents()
            self.assertEqual(dialog.pos(), after_drag)

            dialog.relief_mode.setCurrentIndex(1)
            self.app.processEvents()
            self.assertIn("Experimental", dialog.mode_hint.text())
            dialog.relief_mode.setCurrentIndex(2)
            self.app.processEvents()
            self.assertIn("mapa de profundidade", dialog.mode_hint.text())
            dialog.reject()
            host.close()

    def test_transparent_white_rgb_is_detected_as_base_not_light_background(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transparent-white.png"
            image = Image.new("RGBA", (40, 30), (255, 255, 255, 0))
            for y in range(8, 23):
                for x in range(10, 31):
                    image.putpixel((x, y), (100, 100, 100, 255))
            image.save(path)
            dialog = self.ReliefImageDialog(path)
            dialog.show()
            QtTest.QTest.qWait(220)
            self.assertFalse(dialog.invert.isChecked())
            self.assertEqual(dialog.current_data.value(0, 0), 0)
            self.assertGreater(max(dialog.current_data.pixels), 0)
            dialog.reject()

    def test_quality_presets_apply_coherent_values_and_manual_edit_marks_custom(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "object.png"
            Image.new("L", (40, 30), 100).save(path)
            dialog = self.ReliefImageDialog(path)
            dialog.show()
            self.assertEqual(dialog.quality_profile.currentData(), "aspire_detailed")
            self.assertAlmostEqual(dialog.relief_height.value(), 4.0)
            self.assertEqual(dialog.detail_strength.value(), 65)
            self.assertEqual(dialog.mesh_resolution.value(), 320)
            self.assertEqual(dialog.mesh_resolution.maximum(), 384)

            dialog.quality_profile.setCurrentIndex(1)
            self.app.processEvents()
            self.assertEqual(dialog.quality_profile.currentData(), "portrait_soft")
            self.assertAlmostEqual(dialog.relief_height.value(), 4.0)
            self.assertAlmostEqual(dialog.smoothing.value(), 1.5)
            self.assertEqual(dialog.detail_strength.value(), 32)
            self.assertEqual(dialog.mesh_resolution.value(), 288)
            self.assertIn("leões, rostos e pelos", dialog.profile_hint.text())

            dialog.detail_strength.setValue(52)
            self.app.processEvents()
            self.assertEqual(dialog.quality_profile.currentData(), "custom")
            self.assertIn("Personalizado", dialog.profile_hint.text())
            dialog.reject()

    def test_numeric_controls_combine_drag_slider_and_exact_value(self):
        from woodcam_relief.dialog import SliderSpinControl

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "object.png"
            Image.new("L", (40, 30), 100).save(path)
            dialog = self.ReliefImageDialog(path)
            dialog.show()
            controls = (
                dialog.width_mm,
                dialog.height_mm,
                dialog.relief_height,
                dialog.base_thickness,
                dialog.origin_x,
                dialog.origin_y,
                dialog.smoothing,
                dialog.detail_strength,
                dialog.form_smoothing,
                dialog.structure_strength,
                dialog.sculptural_volume,
                dialog.map_resolution,
                dialog.mesh_resolution,
            )
            self.assertTrue(all(isinstance(item, SliderSpinControl) for item in controls))
            before = dialog.detail_strength.value()
            dialog.detail_strength.slider.setValue(250)
            self.app.processEvents()
            self.assertNotEqual(dialog.detail_strength.value(), before)
            self.assertEqual(dialog.detail_strength.value(), 25)
            dialog.detail_strength.setValue(73)
            self.app.processEvents()
            self.assertEqual(dialog.detail_strength.spin.value(), 73)
            self.assertEqual(dialog.detail_strength.slider.value(), 730)
            dialog.reject()

    def test_photo_scenery_is_reported_before_it_becomes_relief(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scene.png"
            image = Image.new("RGB", (80, 50), (185, 220, 245))
            for y in range(25, 50):
                for x in range(80):
                    image.putpixel((x, y), (45, 105, 40))
            image.save(path)
            dialog = self.ReliefImageDialog(path)
            dialog.show()

            self.assertEqual(dialog._source_background_kind, "scene")
            self.assertIn("Foto com cenário", dialog.source_hint.text())
            self.assertIn("PNG transparente", dialog.source_hint.text())
            dialog.reject()

    def test_compact_layout_separates_essential_and_advanced_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "object.png"
            Image.new("L", (80, 50), 100).save(path)
            dialog = self.ReliefImageDialog(path)
            dialog.resize(1050, 690)
            dialog.show()
            self.app.processEvents()

            self.assertTrue(dialog.smoothing.isVisible())
            self.assertTrue(dialog.invert.isVisible())
            self.assertFalse(dialog.relief_mode.isVisible())
            self.assertLess(dialog.status.geometry().bottom(), dialog.height())
            essential_controls = (
                dialog.smoothing,
                dialog.detail_strength,
                dialog.form_smoothing,
                dialog.structure_strength,
                dialog.sculptural_volume,
                dialog.invert,
                dialog.compensate_height,
            )
            tops = [
                control.mapTo(dialog, QtCore.QPoint(0, 0)).y()
                for control in essential_controls
            ]
            self.assertEqual(tops, sorted(tops))
            self.assertEqual(len(tops), len(set(tops)))

            tab_bar = dialog.processing_tabs.tabBar()
            QtTest.QTest.mouseClick(
                tab_bar,
                QtCore.Qt.LeftButton,
                QtCore.Qt.NoModifier,
                tab_bar.tabRect(1).center(),
            )
            self.app.processEvents()
            self.assertEqual(dialog.processing_tabs.currentIndex(), 1)
            self.assertTrue(dialog.relief_mode.isVisible())
            self.assertFalse(dialog.smoothing.isVisible())

            QtTest.QTest.mouseClick(
                tab_bar,
                QtCore.Qt.LeftButton,
                QtCore.Qt.NoModifier,
                tab_bar.tabRect(0).center(),
            )
            self.app.processEvents()
            self.assertEqual(dialog.processing_tabs.currentIndex(), 0)
            self.assertTrue(dialog.invert.isVisible())
            dialog.reject()

    def test_external_ai_worker_is_cpu_only_preview_with_provenance(self):
        from woodcam_relief.ai_depth import AIDepthRuntime

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            Image.new("RGB", (40, 20), (80, 120, 160)).save(source)
            weights = root / "weights.pth"
            weights.write_bytes(b"fake weights")
            model_source = root / "model-source"
            model_source.mkdir()
            worker = root / "fake_worker.py"
            worker.write_text(
                "import argparse, os\n"
                "from PIL import Image\n"
                "p=argparse.ArgumentParser()\n"
                "p.add_argument('--output', required=True)\n"
                "p.add_argument('--image'); p.add_argument('--weights')\n"
                "p.add_argument('--process-res'); p.add_argument('--threads')\n"
                "p.add_argument('--detail-strength')\n"
                "p.add_argument('--refinement-profile')\n"
                "a=p.parse_args()\n"
                "assert os.environ['WOODCAM_AI_DEVICE'] == 'CPU'\n"
                "Image.linear_gradient('L').resize((40, 20)).save(a.output)\n",
                encoding="utf-8",
            )
            runtime = AIDepthRuntime(
                python_executable=Path(sys.executable),
                worker_script=worker,
                weights_path=weights,
                source_root=model_source,
            )
            dialog = self.ReliefImageDialog(source, ai_runtime=runtime)
            dialog.show()
            QtTest.QTest.qWait(220)
            self.assertTrue(dialog.ai_generate_button.isEnabled())
            dialog.ai_refinement_profile.setCurrentIndex(
                dialog.ai_refinement_profile.findData("sculptural")
            )

            QtTest.QTest.mouseClick(dialog.ai_generate_button, QtCore.Qt.LeftButton)
            for _attempt in range(30):
                QtTest.QTest.qWait(100)
                if dialog._ai_process is None:
                    break

            self.assertIsNone(dialog._ai_process)
            self.assertIsNotNone(dialog._ai_map_path)
            self.assertEqual(dialog.current_data.generator, "ai_depth")
            self.assertEqual(dialog.current_data.source_name, source.name)
            self.assertIn('"device":"CPU"', dialog.current_data.generator_metadata_json)
            self.assertIn(
                '"refinement_profile":"sculptural"',
                dialog.current_data.generator_metadata_json,
            )
            self.assertIn("escultórica", dialog.status.text())
            self.assertEqual(dialog.relief_mode.currentData(), "heightmap")

            dialog.relief_mode.setCurrentIndex(
                dialog.relief_mode.findData("aspire_bitmap")
            )
            self.app.processEvents()
            self.assertIsNone(dialog._ai_map_path)
            self.assertIn("voltou", dialog.ai_runtime_hint.text())
            dialog.reject()


if __name__ == "__main__":
    unittest.main()
