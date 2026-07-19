#!/usr/bin/env python3
"""External CPU-only worker for Depth Anything V2 relief maps.

This file is launched by QProcess in an optional Python 3.11 environment.  It
never imports FreeCAD and never writes anywhere except the requested output.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--process-res", type=int, default=504)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--detail-strength", type=float, default=0.14)
    parser.add_argument("--normals-model", type=Path)
    parser.add_argument(
        "--refinement-profile",
        choices=("balanced", "sculptural", "detailed"),
        default="balanced",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if str(os.environ.get("WOODCAM_AI_DEVICE", "CPU")).upper() != "CPU":
        raise RuntimeError("O worker de relevo do WoodCAM aceita somente CPU.")
    # Heavy optional dependencies are imported only after the process boundary;
    # importing WoodCAM/FreeCAD itself never requires Torch, OpenCV or NumPy.
    import cv2
    import numpy as np
    from PIL import Image
    import torch

    from woodcam_relief.ai_worker_core import (
        add_photo_detail,
        infer_da2,
        infer_metric3d_normals,
        load_da2,
        prepare_relief,
        refine_ai_form,
        fuse_surface_normals,
    )

    torch.set_num_threads(int(args.threads))
    model = load_da2(args.weights)
    height, elapsed = infer_da2(model, args.image, int(args.process_res))
    with Image.open(args.image) as source:
        source_rgb = np.asarray(source.convert("RGB"))
        source_alpha = (
            np.asarray(source.getchannel("A"), dtype=np.float32) / 255.0
            if "A" in source.getbands()
            else None
        )
    form, subject_mask, stats = prepare_relief(height, source_rgb, source_alpha)
    form = refine_ai_form(form, subject_mask, args.refinement_profile)
    if args.normals_model is not None:
        normals, normal_confidence, normals_elapsed = infer_metric3d_normals(
            args.normals_model, args.image, form.shape
        )
        normal_strength = {
            "detailed": 0.20,
            "balanced": 0.16,
            "sculptural": 0.11,
        }[args.refinement_profile]
        form = fuse_surface_normals(
            form,
            normals,
            normal_confidence,
            subject_mask,
            strength=normal_strength,
        )
        stats.update(
            {
                "normals_model": "metric3d-v2-small-onnx-fp16",
                "normals_elapsed_seconds": float(normals_elapsed),
            }
        )
    detail_scale = {
        "detailed": 1.12,
        "balanced": 1.0,
        "sculptural": 0.10,
    }[args.refinement_profile]
    relief, _detail = add_photo_detail(
        form,
        source_rgb,
        strength=float(args.detail_strength) * detail_scale,
    )
    if args.refinement_profile == "sculptural":
        # Subpixel studio smoothing removes skin/photo grain after the geometry
        # cues have already been integrated. It preserves the other profiles,
        # where fine engraving is intentional.
        relief = cv2.GaussianBlur(relief, (0, 0), sigmaX=0.70, sigmaY=0.70)
        edge_mask = cv2.GaussianBlur(
            np.clip(subject_mask, 0.0, 1.0), (0, 0), sigmaX=0.75, sigmaY=0.75
        )
        relief *= np.clip(edge_mask, 0.0, 1.0)
    pixels = np.rint(np.clip(relief, 0.0, 1.0) * 255.0).astype(np.uint8)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels, mode="L").save(args.output, format="PNG", optimize=True)
    print(
        json.dumps(
            {
                "device": "CPU",
                "elapsed_seconds": float(elapsed),
                "height": int(pixels.shape[0]),
                "model": (
                    "depth-anything-v2-small+metric3d-v2-small-normals"
                    if args.normals_model is not None
                    else "depth-anything-v2-small"
                ),
                "refinement_profile": args.refinement_profile,
                "output": str(args.output),
                "width": int(pixels.shape[1]),
                **stats,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
