"""Pure numerical tests for preview-first depth + normal fusion."""

from __future__ import annotations

import unittest

import numpy as np

try:
    import cv2  # noqa: F401
except ImportError:  # The production FreeCAD interpreter intentionally has no OpenCV.
    cv2 = None


@unittest.skipIf(cv2 is None, "OpenCV existe somente no runtime opcional da IA")
class ReliefAINormalsTests(unittest.TestCase):
    def test_flat_normals_preserve_flat_form(self):
        from woodcam_relief.ai_worker_core import fuse_surface_normals

        form = np.full((48, 64), 0.5, dtype=np.float32)
        normals = np.zeros((48, 64, 3), dtype=np.float32)
        normals[:, :, 2] = -1.0
        confidence = np.ones((48, 64), dtype=np.float32)
        mask = np.ones((48, 64), dtype=np.float32)
        fused = fuse_surface_normals(form, normals, confidence, mask)
        self.assertLess(float(np.max(np.abs(fused - form))), 1e-5)

    def test_normal_cue_changes_only_masked_midscale_shape(self):
        from woodcam_relief.ai_worker_core import fuse_surface_normals

        rows, columns = 72, 80
        form = np.full((rows, columns), 0.45, dtype=np.float32)
        normals = np.zeros((rows, columns, 3), dtype=np.float32)
        normals[:, :, 2] = -1.0
        normals[24:48, 28:52, 0] = np.linspace(-0.45, 0.45, 24)[None, :]
        normals /= np.maximum(np.linalg.norm(normals, axis=2, keepdims=True), 1e-6)
        mask = np.zeros((rows, columns), dtype=np.float32)
        mask[12:60, 14:66] = 1.0
        fused = fuse_surface_normals(
            form, normals, np.ones_like(form), mask, strength=0.16
        )
        self.assertGreater(float(np.ptp(fused[24:48, 28:52])), 0.02)
        self.assertEqual(float(fused[0, 0]), 0.0)


if __name__ == "__main__":
    unittest.main()
