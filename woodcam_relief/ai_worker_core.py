"""Depth-model processing used only by the optional external AI worker."""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np


def robust_unit(values, low=2.0, high=98.0):
    values = np.asarray(values, dtype=np.float32)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        raise ValueError("O modelo não retornou valores finitos.")
    lower, upper = np.percentile(finite, (low, high))
    if upper <= lower + 1e-8:
        return np.zeros_like(values, dtype=np.float32)
    return np.clip((values - lower) / (upper - lower), 0.0, 1.0)


def _uniform_corner_subject_mask(source_rgb):
    height, width = source_rgb.shape[:2]
    band = max(3, int(round(min(height, width) * 0.06)))
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
    candidate = (
        distance <= max(14.0, float(np.percentile(spread, 95.0)) * 2.5)
    ).astype(np.uint8)
    count, labels = cv2.connectedComponents(candidate, connectivity=8)
    border_labels = (
        set(labels[0, :])
        | set(labels[-1, :])
        | set(labels[:, 0])
        | set(labels[:, -1])
    )
    background = np.zeros((height, width), dtype=np.uint8)
    for label in border_labels:
        if label:
            background[labels == label] = 1
    subject = 1 - background
    if count <= 1 or int(subject.sum()) < int(height * width * 0.01):
        return None
    return subject.astype(np.float32)


def _checkerboard_subject_mask(source_rgb):
    """Detect a baked gray/white transparency checker and remove it at edges."""

    height, width = source_rgb.shape[:2]
    band = max(6, int(round(min(height, width) * 0.10)))
    corners = np.concatenate(
        (
            source_rgb[:band, :band].reshape(-1, 3),
            source_rgb[:band, -band:].reshape(-1, 3),
            source_rgb[-band:, :band].reshape(-1, 3),
            source_rgb[-band:, -band:].reshape(-1, 3),
        )
    ).astype(np.float32)
    criteria = (
        cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
        30,
        0.25,
    )
    _compactness, labels, centers = cv2.kmeans(
        corners,
        2,
        None,
        criteria,
        5,
        cv2.KMEANS_PP_CENTERS,
    )
    centers = centers.astype(np.float32)
    if any(float(center.max() - center.min()) > 14.0 for center in centers):
        return None
    luminance = centers.mean(axis=1)
    if not 14.0 <= abs(float(luminance[0] - luminance[1])) <= 95.0:
        return None
    corner_distance = np.linalg.norm(
        corners - centers[labels.ravel()], axis=1
    )
    if float(np.percentile(corner_distance, 90.0)) > 13.0:
        return None
    pixels = source_rgb.astype(np.float32)
    distance = np.minimum(
        np.linalg.norm(pixels - centers[0], axis=2),
        np.linalg.norm(pixels - centers[1], axis=2),
    )
    candidate = (distance <= 16.0).astype(np.uint8)
    candidate = cv2.morphologyEx(
        candidate,
        cv2.MORPH_CLOSE,
        np.ones((3, 3), dtype=np.uint8),
    )
    _count, components = cv2.connectedComponents(candidate, connectivity=8)
    border_labels = (
        set(components[0, :])
        | set(components[-1, :])
        | set(components[:, 0])
        | set(components[:, -1])
    )
    background = np.zeros((height, width), dtype=np.uint8)
    for label in border_labels:
        if label:
            background[components == label] = 1
    subject = 1 - background
    fraction = float(np.mean(subject > 0))
    if fraction < 0.01 or fraction > 0.95:
        return None
    return subject.astype(np.float32)


def _depth_subject_mask(height):
    pixels = np.rint(robust_unit(height) * 255.0).astype(np.uint8)
    _threshold, binary = cv2.threshold(
        pixels, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    radius = max(1, int(round(min(height.shape) * 0.008)))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1)
    )
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(
        binary, connectivity=8
    )
    if count <= 1:
        return np.ones_like(height, dtype=np.float32)
    image_height, image_width = height.shape
    center = np.asarray((image_width * 0.5, image_height * 0.5), dtype=np.float32)
    best_label, best_score = 1, -1.0
    for label in range(1, count):
        area = float(stats[label, cv2.CC_STAT_AREA])
        distance = float(np.linalg.norm(centroids[label] - center)) / max(
            image_height, image_width
        )
        contains_center = labels[image_height // 2, image_width // 2] == label
        score = area * (2.0 if contains_center else 1.0) / (1.0 + distance)
        if score > best_score:
            best_label, best_score = label, score
    return (labels == best_label).astype(np.float32)


def prepare_relief(height, source_rgb, source_alpha=None):
    height = robust_unit(height)
    mask = None
    if source_alpha is not None:
        alpha = np.asarray(source_alpha, dtype=np.float32)
        if alpha.shape == height.shape:
            alpha = np.clip(alpha, 0.0, 1.0)
            fraction = float(np.mean(alpha > 0.02))
            if 0.01 < fraction < 0.995:
                mask = alpha
    mask_method = "alpha_channel"
    if mask is None:
        mask = _uniform_corner_subject_mask(source_rgb)
        mask_method = "uniform_corner_color"
    if mask is None:
        mask = _checkerboard_subject_mask(source_rgb)
        mask_method = "baked_checkerboard"
    if mask is None:
        mask = _depth_subject_mask(height)
        mask_method = "dominant_depth_component"
    inside = height[mask > 0.5]
    lower, upper = (
        np.percentile(inside, (2.0, 99.5)) if inside.size else (0.0, 1.0)
    )
    relief = np.clip((height - lower) / max(1e-6, float(upper - lower)), 0.0, 1.0)
    feather = max(0.8, min(height.shape) * 0.0025)
    soft_mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=feather, sigmaY=feather)
    relief *= np.clip(soft_mask, 0.0, 1.0)
    relief = cv2.GaussianBlur(relief, (0, 0), sigmaX=0.65, sigmaY=0.65)
    return np.clip(relief, 0.0, 1.0), mask, {
        "mask_method": mask_method,
        "subject_fraction": float(np.mean(mask > 0.5)),
        "subject_low": float(lower),
        "subject_top": float(upper),
    }


def refine_ai_form(form, mask, profile="balanced"):
    """Regularize AI depth into a machinable form without losing landmarks."""

    profiles = {
        "detailed": {"volume": 0.06, "residual": 0.88, "sigma": 0.004},
        "balanced": {"volume": 0.14, "residual": 0.68, "sigma": 0.007},
        # Portraits and animals need continuous anatomical mass. Photo grain,
        # makeup and individual hairs belong to the detailed engraving profile,
        # not to the default sculptural surface.
        "sculptural": {"volume": 0.44, "residual": 0.24, "sigma": 0.020},
    }
    if profile not in profiles:
        raise ValueError("Perfil de refinamento da IA desconhecido: %s" % profile)
    params = profiles[profile]
    form = np.asarray(form, dtype=np.float32)
    mask = np.clip(np.asarray(mask, dtype=np.float32), 0.0, 1.0)
    hard_mask = (mask > 0.08).astype(np.uint8)
    minimum = min(form.shape)
    radius = max(1, int(round(minimum * 0.003)))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1)
    )
    hard_mask = cv2.morphologyEx(hard_mask, cv2.MORPH_CLOSE, kernel)
    distance = cv2.distanceTransform(hard_mask, cv2.DIST_L2, 5)
    maximum_distance = float(distance.max())
    dome = (
        np.power(np.clip(distance / maximum_distance, 0.0, 1.0), 0.58)
        if maximum_distance > 1e-6
        else np.zeros_like(form)
    )

    bilateral = cv2.bilateralFilter(
        form,
        d=0,
        sigmaColor=0.075,
        sigmaSpace=max(2.0, minimum * 0.008),
    )
    low = cv2.GaussianBlur(
        bilateral,
        (0, 0),
        sigmaX=max(0.8, minimum * params["sigma"]),
        sigmaY=max(0.8, minimum * params["sigma"]),
    )
    residual = form - low
    base = (1.0 - params["volume"]) * low + params["volume"] * dome
    refined = base + params["residual"] * residual
    inside = refined[hard_mask > 0]
    if inside.size:
        lower, upper = np.percentile(inside, (0.5, 99.7))
        refined = np.clip(
            (refined - lower) / max(1e-6, float(upper - lower)), 0.0, 1.0
        )
    edge_sigma = max(0.8, minimum * 0.002)
    soft_mask = cv2.GaussianBlur(
        hard_mask.astype(np.float32), (0, 0), edge_sigma
    )
    return np.clip(refined * soft_mask, 0.0, 1.0)


def add_photo_detail(form, source_rgb, strength=0.14):
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


def load_da2(weights: Path):
    import torch
    from depth_anything_v2.dpt import DepthAnythingV2

    model = DepthAnythingV2(
        encoder="vits", features=64, out_channels=[48, 96, 192, 384]
    )
    state = torch.load(weights, map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)
    return model.eval()


def infer_da2(model, image_path: Path, process_res: int):
    import torch

    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Não foi possível abrir %s" % image_path)
    started = time.perf_counter()
    with torch.inference_mode():
        inverse_depth = model.infer_image(image, input_size=process_res)
    return robust_unit(inverse_depth), time.perf_counter() - started


def infer_metric3d_normals(model_path: Path, image_path: Path, output_shape):
    """Infer camera-space normals in an isolated OpenVINO CPU process."""

    import openvino as ov

    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Não foi possível abrir %s" % image_path)
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    height, width = rgb.shape[:2]
    scale = 518.0 / max(height, width)
    input_height = max(14, int(round(height * scale / 14.0)) * 14)
    input_width = max(14, int(round(width * scale / 14.0)) * 14)
    resized = cv2.resize(
        rgb, (input_width, input_height), interpolation=cv2.INTER_CUBIC
    )
    tensor = resized.transpose(2, 0, 1)[None].astype(np.float16)
    core = ov.Core()
    model = core.read_model(str(model_path))
    compiled = core.compile_model(model, "CPU", {"PERFORMANCE_HINT": "LATENCY"})
    started = time.perf_counter()
    result = compiled({"pixel_values": tensor})
    normal = np.asarray(
        result[compiled.output("predicted_normal")][0], dtype=np.float32
    ).transpose(1, 2, 0)
    confidence = np.asarray(
        result[compiled.output("normal_confidence")][0], dtype=np.float32
    )
    elapsed = time.perf_counter() - started
    out_height, out_width = int(output_shape[0]), int(output_shape[1])
    normal = cv2.resize(normal, (out_width, out_height), interpolation=cv2.INTER_CUBIC)
    confidence = cv2.resize(
        confidence, (out_width, out_height), interpolation=cv2.INTER_LINEAR
    )
    normal /= np.maximum(np.linalg.norm(normal, axis=2, keepdims=True), 1e-7)
    return normal, confidence, elapsed


def fuse_surface_normals(form, normals, confidence, mask, strength=0.16):
    """Add mid-scale geometric cues while anchoring volume to DA2 depth.

    A screened Poisson solve integrates Metric3D's normal gradients.  Only the
    mid-frequency correction is retained, so perspective/scene depth cannot
    replace the DA2 silhouette and low-frequency body volume.
    """

    form = np.asarray(form, dtype=np.float32)
    normals = np.asarray(normals, dtype=np.float32)
    confidence = np.asarray(confidence, dtype=np.float32)
    mask = np.clip(np.asarray(mask, dtype=np.float32), 0.0, 1.0)
    if normals.shape != form.shape + (3,) or confidence.shape != form.shape:
        raise ValueError("Normais e forma precisam ter a mesma resolução.")
    nz = normals[:, :, 2]
    safe_nz = np.where(np.abs(nz) >= 0.20, nz, np.where(nz < 0.0, -0.20, 0.20))
    # Metric3D normals face the camera (negative Z). For relief height toward
    # the camera, dh/dx=nx/nz and dh/dy=ny/nz.
    slope_x = np.clip(normals[:, :, 0] / safe_nz, -2.0, 2.0)
    slope_y = np.clip(normals[:, :, 1] / safe_nz, -2.0, 2.0)
    confidence_weight = 0.35 + 0.65 * robust_unit(confidence, 5.0, 95.0)
    soft_mask = cv2.GaussianBlur(mask, (0, 0), max(0.8, min(form.shape) * 0.002))
    weight = confidence_weight * np.clip(soft_mask, 0.0, 1.0)

    grad_y, grad_x = np.gradient(form)
    reference = np.hypot(grad_x, grad_y)[mask > 0.08]
    predicted = np.hypot(slope_x, slope_y)[mask > 0.08]
    reference_scale = float(np.percentile(reference, 82.0)) if reference.size else 0.01
    predicted_scale = float(np.percentile(predicted, 82.0)) if predicted.size else 1.0
    gradient_scale = np.clip(reference_scale / max(predicted_scale, 1e-6), 0.002, 0.12)
    slope_x *= gradient_scale * weight
    slope_y *= gradient_scale * weight

    rows, columns = form.shape
    ky = 2.0 * np.pi * np.fft.fftfreq(rows)[:, None]
    kx = 2.0 * np.pi * np.fft.fftfreq(columns)[None, :]
    screen = 1.0 / (14.0 * 14.0)
    form_hat = np.fft.fft2(form)
    divergence_hat = 1j * kx * np.fft.fft2(slope_x) + 1j * ky * np.fft.fft2(slope_y)
    integrated_hat = (screen * form_hat - divergence_hat) / (screen + kx * kx + ky * ky)
    integrated_hat[0, 0] = form_hat[0, 0]
    integrated = np.fft.ifft2(integrated_hat).real.astype(np.float32)

    correction = integrated - form
    correction -= cv2.GaussianBlur(
        correction, (0, 0), max(4.0, min(form.shape) * 0.032)
    )
    samples = np.abs(correction[mask > 0.08])
    limit = float(np.percentile(samples, 98.0)) if samples.size else 1.0
    correction = np.clip(correction / max(limit, 1e-6), -1.0, 1.0)
    visibility = 0.25 + 0.75 * np.sqrt(np.clip(form, 0.0, 1.0))
    fused = form + float(strength) * correction * visibility
    fused = np.clip(fused, 0.0, 1.0) * np.clip(soft_mask, 0.0, 1.0)
    return fused.astype(np.float32)


__all__ = [
    "add_photo_detail",
    "infer_da2",
    "infer_metric3d_normals",
    "fuse_surface_normals",
    "load_da2",
    "prepare_relief",
    "refine_ai_form",
]
