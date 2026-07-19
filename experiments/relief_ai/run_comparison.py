#!/usr/bin/env python3
"""Compare local monocular-depth models without changing production WoodCAM."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont


def _robust_unit(values: np.ndarray, low: float = 2.0, high: float = 98.0) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        raise ValueError("O modelo não retornou valores finitos.")
    lo, hi = np.percentile(finite, (low, high))
    if hi <= lo + 1e-8:
        return np.zeros_like(values, dtype=np.float32)
    return np.clip((values - lo) / (hi - lo), 0.0, 1.0)


def _uniform_corner_subject_mask(source_rgb: np.ndarray) -> np.ndarray | None:
    """Return a subject mask when all four corners describe one flat backdrop."""
    h, w = source_rgb.shape[:2]
    band = max(3, int(round(min(h, w) * 0.06)))
    corners = np.concatenate(
        (
            source_rgb[:band, :band].reshape(-1, 3),
            source_rgb[:band, -band:].reshape(-1, 3),
            source_rgb[-band:, :band].reshape(-1, 3),
            source_rgb[-band:, -band:].reshape(-1, 3),
        )
    ).astype(np.float32)
    color = np.median(corners, axis=0)
    spread = np.linalg.norm(corners - color, axis=1)
    if float(np.percentile(spread, 90.0)) > 24.0:
        return None
    distance = np.linalg.norm(source_rgb.astype(np.float32) - color, axis=2)
    candidate = (distance <= max(14.0, float(np.percentile(spread, 95.0)) * 2.5)).astype(
        np.uint8
    )
    count, labels = cv2.connectedComponents(candidate, connectivity=8)
    border_labels = set(labels[0, :]) | set(labels[-1, :]) | set(labels[:, 0]) | set(labels[:, -1])
    background = np.zeros((h, w), dtype=np.uint8)
    for label in border_labels:
        if label:
            background[labels == label] = 1
    subject = 1 - background
    if count <= 1 or int(subject.sum()) < int(h * w * 0.01):
        return None
    return subject.astype(np.float32)


def _depth_subject_mask(height: np.ndarray) -> np.ndarray:
    """Keep the dominant near object for a photo whose corners are scenery."""
    pixels = np.rint(_robust_unit(height) * 255.0).astype(np.uint8)
    _threshold, binary = cv2.threshold(pixels, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    radius = max(1, int(round(min(height.shape) * 0.008)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if count <= 1:
        return np.ones_like(height, dtype=np.float32)
    h, w = height.shape
    center = np.asarray((w * 0.5, h * 0.5), dtype=np.float32)
    best_label, best_score = 1, -1.0
    for label in range(1, count):
        area = float(stats[label, cv2.CC_STAT_AREA])
        distance = float(np.linalg.norm(centroids[label] - center)) / max(h, w)
        contains_center = labels[h // 2, w // 2] == label
        score = area * (2.0 if contains_center else 1.0) / (1.0 + distance)
        if score > best_score:
            best_label, best_score = label, score
    return (labels == best_label).astype(np.float32)


def prepare_relief(
    height: np.ndarray, source_rgb: np.ndarray
) -> tuple[np.ndarray, dict[str, float | str]]:
    """Put the detected background at the base while preserving the foreground."""
    height = _robust_unit(height)
    mask = _uniform_corner_subject_mask(source_rgb)
    mask_method = "uniform_corner_color"
    if mask is None:
        mask = _depth_subject_mask(height)
        mask_method = "dominant_depth_component"
    inside = height[mask > 0.5]
    low, top = np.percentile(inside, (2.0, 99.5)) if inside.size else (0.0, 1.0)
    scale = max(1e-6, float(top - low))
    relief = np.clip((height - low) / scale, 0.0, 1.0)
    feather = max(0.8, min(height.shape) * 0.0025)
    soft_mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=feather, sigmaY=feather)
    relief *= np.clip(soft_mask, 0.0, 1.0)
    # A light blur removes isolated depth noise but retains the model's main form.
    relief = cv2.GaussianBlur(relief, (0, 0), sigmaX=0.65, sigmaY=0.65)
    return np.clip(relief, 0.0, 1.0), {
        "mask_method": mask_method,
        "subject_fraction": float(np.mean(mask > 0.5)),
        "subject_low": float(low),
        "subject_top": float(top),
    }


def add_photo_detail(form: np.ndarray, source_rgb: np.ndarray, strength: float = 0.14):
    """Blend photo microtexture into the AI form without changing its silhouette."""
    gray = cv2.cvtColor(source_rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    smooth = cv2.GaussianBlur(gray, (0, 0), sigmaX=2.2, sigmaY=2.2)
    detail = gray - smooth
    subject = form > 0.003
    samples = np.abs(detail[subject])
    scale = float(np.percentile(samples, 97.0)) if samples.size else 1.0
    normalized = np.clip(detail / max(scale, 1e-6), -1.0, 1.0)
    soft_mask = cv2.GaussianBlur(subject.astype(np.float32), (0, 0), 1.0)
    visibility = 0.30 + 0.70 * np.sqrt(np.clip(form, 0.0, 1.0))
    hybrid = form + float(strength) * normalized * visibility
    hybrid = np.clip(hybrid, 0.0, 1.0) * np.clip(soft_mask, 0.0, 1.0)
    return hybrid, normalized * 0.5 + 0.5


def _gray_image(values: np.ndarray) -> Image.Image:
    pixels = np.rint(np.clip(values, 0.0, 1.0) * 255.0).astype(np.uint8)
    return Image.fromarray(pixels, mode="L")


def _shaded_image(height: np.ndarray) -> Image.Image:
    gy, gx = np.gradient(height.astype(np.float32))
    strength = 7.0
    nx, ny, nz = -gx * strength, -gy * strength, np.ones_like(height)
    norm = np.sqrt(nx * nx + ny * ny + nz * nz)
    nx, ny, nz = nx / norm, ny / norm, nz / norm
    light = np.asarray((-0.45, -0.55, 0.70), dtype=np.float32)
    light /= np.linalg.norm(light)
    shade = np.clip(nx * light[0] + ny * light[1] + nz * light[2], 0.0, 1.0)
    shade = 0.30 + 0.70 * shade
    base = np.asarray((184.0, 119.0, 52.0), dtype=np.float32)
    rgb = np.clip(shade[..., None] * base[None, None, :], 0, 255).astype(np.uint8)
    return Image.fromarray(rgb, mode="RGB")


def _fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    copy = image.copy()
    copy.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, (25, 35, 46))
    x = (size[0] - copy.width) // 2
    y = (size[1] - copy.height) // 2
    canvas.paste(copy.convert("RGB"), (x, y))
    return canvas


def save_comparison(
    source_path: Path,
    raw_height: np.ndarray,
    form: np.ndarray,
    detail: np.ndarray,
    relief: np.ndarray,
    model_name: str,
    output_path: Path,
) -> None:
    panel = (420, 260)
    images = [
        _fit(Image.open(source_path).convert("RGB"), panel),
        _fit(_gray_image(raw_height).convert("RGB"), panel),
        _fit(_gray_image(form).convert("RGB"), panel),
        _fit(_gray_image(detail).convert("RGB"), panel),
        _fit(_gray_image(relief).convert("RGB"), panel),
        _fit(_shaded_image(relief), panel),
    ]
    labels = (
        "Original",
        "Profundidade da IA",
        "Forma 3D isolada",
        "Microtextura da foto",
        "Altura híbrida",
        "Prévia híbrida sombreada",
    )
    title_h, label_h = 42, 28
    canvas = Image.new("RGB", (panel[0] * 2, title_h + (panel[1] + label_h) * 3), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    draw.text((12, 13), f"{source_path.name} — {model_name}", fill="black", font=font)
    for index, (image, label) in enumerate(zip(images, labels)):
        col, row = index % 2, index // 2
        x = col * panel[0]
        y = title_h + row * (panel[1] + label_h)
        canvas.paste(image, (x, y))
        draw.text((x + 10, y + panel[1] + 8), label, fill="black", font=font)
    canvas.save(output_path)


def load_da2(weights: Path):
    from depth_anything_v2.dpt import DepthAnythingV2

    model = DepthAnythingV2(
        encoder="vits", features=64, out_channels=[48, 96, 192, 384]
    )
    state = torch.load(weights, map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)
    return model.eval()


def infer_da2(model, image_path: Path, process_res: int) -> tuple[np.ndarray, float]:
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Não foi possível abrir {image_path}")
    started = time.perf_counter()
    with torch.inference_mode():
        inverse_depth = model.infer_image(image, input_size=process_res)
    elapsed = time.perf_counter() - started
    # DA2 produces relative inverse depth: larger values are nearer/higher.
    return _robust_unit(inverse_depth), elapsed


def load_da3(model_dir: Path):
    from omegaconf import OmegaConf
    from safetensors.torch import load_file

    from depth_anything_3.cfg import create_object

    config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    model = create_object(OmegaConf.create(config["config"]))
    state = {
        key.removeprefix("model."): value
        for key, value in load_file(model_dir / "model.safetensors").items()
    }
    incompatible = model.load_state_dict(state, strict=False)
    # Current official source has six newly initialized auxiliary layers unused by
    # the primary depth output. Any other mismatch invalidates this comparison.
    allowed = {
        f"head.scratch.output_conv2_aux.{branch}.2.{kind}"
        for branch in (1, 2, 3)
        for kind in ("weight", "bias")
    }
    if set(incompatible.missing_keys) != allowed or incompatible.unexpected_keys:
        raise RuntimeError(f"Pesos DA3 incompatíveis: {incompatible}")
    return model.eval()


def infer_da3(model, image_path: Path, process_res: int) -> tuple[np.ndarray, float]:
    from depth_anything_3.utils.io.input_processor import InputProcessor

    images, _, _ = InputProcessor()(
        [str(image_path)],
        process_res=process_res,
        process_res_method="upper_bound_resize",
        num_workers=1,
        sequential=True,
    )
    started = time.perf_counter()
    with torch.inference_mode():
        output = model(images[None], None, None, [], False, False, "saddle_balanced")
    elapsed = time.perf_counter() - started
    depth = output.depth.detach().cpu().numpy()[0, 0]
    # DA3 produces distance: smaller values are nearer/higher.
    height = 1.0 - _robust_unit(depth)
    original = Image.open(image_path)
    height = cv2.resize(height, original.size, interpolation=cv2.INTER_CUBIC)
    return np.clip(height, 0.0, 1.0), elapsed


def run(args: argparse.Namespace) -> dict:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    inputs = {"fish": args.fish.resolve(), "lion": args.lion.resolve()}
    report = {
        "purpose": "isolated_quality_proof_only",
        "process_resolution": args.process_res,
        "torch_threads": args.threads,
        "models": {},
    }
    torch.set_num_threads(args.threads)

    models = (
        ("da2_small", load_da2(args.da2_weights), infer_da2),
        ("da3_small", load_da3(args.da3_dir), infer_da3),
    )
    for model_name, model, infer in models:
        model_report = {}
        for subject, image_path in inputs.items():
            height, elapsed = infer(model, image_path, args.process_res)
            source_rgb = np.asarray(Image.open(image_path).convert("RGB"))
            form, stats = prepare_relief(height, source_rgb)
            relief, detail = add_photo_detail(form, source_rgb)
            prefix = output / f"{subject}_{model_name}"
            _gray_image(height).save(prefix.with_name(prefix.name + "_depth.png"))
            _gray_image(form).save(prefix.with_name(prefix.name + "_form.png"))
            _gray_image(detail).save(prefix.with_name(prefix.name + "_detail.png"))
            _gray_image(relief).save(prefix.with_name(prefix.name + "_relief.png"))
            _shaded_image(relief).save(prefix.with_name(prefix.name + "_shaded.png"))
            save_comparison(
                image_path,
                height,
                form,
                detail,
                relief,
                model_name,
                prefix.with_name(prefix.name + "_comparison.png"),
            )
            model_report[subject] = {
                "seconds": round(elapsed, 6),
                "shape": list(height.shape),
                **stats,
            }
        report["models"][model_name] = model_report
        del model

    (output / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fish", type=Path, required=True)
    parser.add_argument("--lion", type=Path, required=True)
    parser.add_argument("--da2-weights", type=Path, required=True)
    parser.add_argument("--da3-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--process-res", type=int, default=504)
    parser.add_argument("--threads", type=int, default=6)
    return parser.parse_args(argv)


if __name__ == "__main__":
    print(json.dumps(run(parse_args(sys.argv[1:])), indent=2, ensure_ascii=False))
