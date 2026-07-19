#!/usr/bin/env python3
"""Isolated CPU proof for Metric3D depth + surface normals.

This experiment never imports FreeCAD or production WoodCAM modules.  It
preserves the source image and writes only inspection PNGs and a JSON report.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np
import openvino as ov


def _unit(values: np.ndarray, low=2.0, high=98.0) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    lo, hi = np.percentile(values[np.isfinite(values)], (low, high))
    return np.clip((values - lo) / max(float(hi - lo), 1e-7), 0.0, 1.0)


def _write_gray(path: Path, values: np.ndarray) -> None:
    cv2.imwrite(str(path), np.rint(np.clip(values, 0.0, 1.0) * 255).astype(np.uint8))


def _write_normal(path: Path, normal: np.ndarray) -> None:
    rgb = np.rint(np.clip(normal * 0.5 + 0.5, 0.0, 1.0) * 255).astype(np.uint8)
    cv2.imwrite(str(path), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))


def _write_normal_shading(path: Path, normal: np.ndarray, confidence: np.ndarray) -> None:
    light = np.asarray((-0.42, -0.50, 0.76), dtype=np.float32)
    light /= np.linalg.norm(light)
    shade = np.clip(np.sum(normal * light[None, None, :], axis=2), 0.0, 1.0)
    shade = 0.27 + 0.73 * shade
    # Neutral studio clay: this preview judges form, not source colour.
    clay_rgb = np.asarray((0.72, 0.75, 0.79), dtype=np.float32)
    rgb = shade[..., None] * clay_rgb[None, None, :]
    uncertain = np.clip(_unit(confidence, 5.0, 95.0), 0.25, 1.0)
    rgb *= uncertain[..., None]
    cv2.imwrite(
        str(path),
        cv2.cvtColor(np.rint(np.clip(rgb, 0.0, 1.0) * 255).astype(np.uint8), cv2.COLOR_RGB2BGR),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("images", type=Path, nargs="+")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    core = ov.Core()
    model = core.read_model(args.model)
    compiled = core.compile_model(model, "CPU", {"PERFORMANCE_HINT": "LATENCY"})
    report = {
        "purpose": "isolated_metric3d_normals_quality_proof",
        "device": "CPU",
        "model_bytes": args.model.stat().st_size,
        "images": {},
    }
    for image_path in args.images:
        source = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
        if source is None:
            raise ValueError(f"Não foi possível abrir {image_path}")
        if source.ndim == 2:
            source = cv2.cvtColor(source, cv2.COLOR_GRAY2BGR)
        bgr = source[:, :, :3]
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        height, width = rgb.shape[:2]
        scale = 518.0 / max(height, width)
        out_h = max(14, int(round(height * scale / 14.0)) * 14)
        out_w = max(14, int(round(width * scale / 14.0)) * 14)
        resized = cv2.resize(rgb, (out_w, out_h), interpolation=cv2.INTER_CUBIC)
        tensor = resized.transpose(2, 0, 1)[None].astype(np.float16)

        started = time.perf_counter()
        result = compiled({"pixel_values": tensor})
        elapsed = time.perf_counter() - started
        depth = np.asarray(result[compiled.output("predicted_depth")][0], dtype=np.float32)
        normal = np.asarray(result[compiled.output("predicted_normal")][0], dtype=np.float32).transpose(1, 2, 0)
        confidence = np.asarray(result[compiled.output("normal_confidence")][0], dtype=np.float32)
        norm = np.linalg.norm(normal, axis=2, keepdims=True)
        normal /= np.maximum(norm, 1e-7)

        stem = image_path.stem.replace(" ", "_")
        # Metric depth is farther=larger; for a relief, nearer must be higher.
        _write_gray(args.output / f"{stem}_near_height.png", 1.0 - _unit(depth))
        _write_normal(args.output / f"{stem}_normal.png", normal)
        _write_gray(args.output / f"{stem}_normal_confidence.png", _unit(confidence))
        _write_normal_shading(args.output / f"{stem}_normal_shaded.png", normal, confidence)
        report["images"][image_path.name] = {
            "seconds": round(elapsed, 6),
            "input_shape": list(tensor.shape),
            "depth_shape": list(depth.shape),
            "normal_shape": list(normal.shape),
            "depth_min": float(np.min(depth)),
            "depth_max": float(np.max(depth)),
            "confidence_p50": float(np.percentile(confidence, 50.0)),
        }

    (args.output / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
