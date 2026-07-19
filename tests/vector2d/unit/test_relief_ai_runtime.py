"""Pure tests for the optional external AI-depth boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from woodcam_relief.ai_depth import (
    AIDepthRuntime,
    AIRuntimeError,
    CONFIG_ENV,
    discover_ai_runtime,
    load_ai_heightmap,
)
from woodcam_relief.heightmap import ReliefOptions


class ReliefAIRuntimeTests(unittest.TestCase):
    def _runtime_files(self, directory):
        root = Path(directory)
        python = root / "python"
        python.write_text("#!/bin/sh\n", encoding="utf-8")
        python.chmod(0o700)
        worker = root / "ai_worker.py"
        worker.write_text("# worker\n", encoding="utf-8")
        weights = root / "model.pth"
        weights.write_bytes(b"weights")
        source = root / "Depth-Anything-V2"
        source.mkdir()
        return python, worker, weights, source

    def test_runtime_is_explicit_validated_and_forces_cpu(self):
        with tempfile.TemporaryDirectory() as directory:
            python, worker, weights, source = self._runtime_files(directory)
            runtime = AIDepthRuntime(python, worker, weights, source)
            command = runtime.command(
                Path(directory) / "input.png",
                Path(directory) / "output.png",
                process_resolution=504,
                threads=6,
                detail_strength=0.14,
            )
            environment = runtime.process_environment({"PYTHONPATH": "old"})
        self.assertEqual(command[0], str(python))
        self.assertIn("--process-res", command)
        self.assertEqual(command[-2:], ("--refinement-profile", "balanced"))
        self.assertEqual(environment["WOODCAM_AI_DEVICE"], "CPU")
        self.assertIn(str(source), environment["PYTHONPATH"])
        self.assertFalse(runtime.has_surface_normals)

    def test_runtime_appends_optional_normals_model(self):
        with tempfile.TemporaryDirectory() as directory:
            python, worker, weights, source = self._runtime_files(directory)
            normals = Path(directory) / "metric3d.onnx"
            normals.write_bytes(b"onnx")
            runtime = AIDepthRuntime(
                python, worker, weights, source, normals_model_path=normals
            )
            command = runtime.command("in.png", "out.png")
        self.assertTrue(runtime.has_surface_normals)
        self.assertEqual(command[-2:], ("--normals-model", str(normals)))

    def test_runtime_config_is_optional_and_reports_invalid_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "runtime.json"
            self.assertIsNone(
                discover_ai_runtime(environ={CONFIG_ENV: str(config)})
            )
            config.write_text(
                json.dumps(
                    {
                        "python_executable": "/missing/python",
                        "worker_script": "/missing/worker",
                        "weights_path": "/missing/model",
                        "source_root": "/missing/source",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(AIRuntimeError):
                discover_ai_runtime(environ={CONFIG_ENV: str(config)})

    def test_ai_map_preserves_original_identity_and_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "fish.png"
            generated = Path(directory) / "depth.png"
            Image.new("RGB", (4, 3), (10, 20, 30)).save(original)
            depth = Image.new("L", (4, 3))
            depth.putdata((0, 20, 80, 0, 10, 120, 255, 0, 0, 30, 90, 0))
            depth.save(generated)
            data = load_ai_heightmap(
                generated,
                original,
                ReliefOptions(
                    width_mm=40,
                    height_mm=30,
                    auto_levels=True,
                    relief_mode="aspire_bitmap",
                    smoothing_radius_px=0,
                ),
            )
        self.assertEqual(data.source_name, "fish.png")
        self.assertEqual(len(data.source_sha256), 64)
        self.assertEqual(data.generator, "ai_depth")
        self.assertEqual(json.loads(data.generator_metadata_json)["device"], "CPU")
        self.assertEqual(
            json.loads(data.generator_metadata_json)["refinement_profile"],
            "balanced",
        )
        self.assertEqual(data.options.relief_mode, "heightmap")
        self.assertFalse(data.options.auto_levels)
        self.assertEqual(data.pixels[6], 255)

    def test_runtime_rejects_implicit_gpu_style_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = AIDepthRuntime(*self._runtime_files(directory))
            with self.assertRaises(AIRuntimeError):
                runtime.command("in.png", "out.png", process_resolution=50)
            with self.assertRaises(AIRuntimeError):
                runtime.command("in.png", "out.png", detail_strength=1.5)
            with self.assertRaises(AIRuntimeError):
                runtime.command("in.png", "out.png", refinement_profile="gpu")


if __name__ == "__main__":
    unittest.main()
