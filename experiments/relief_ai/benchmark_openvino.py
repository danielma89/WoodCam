#!/usr/bin/env python3
"""Check whether the isolated depth models can run faithfully on OpenVINO."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np
import openvino as ov
import torch

from run_comparison import load_da2, load_da3


class DA3DepthOnly(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, image):
        return self.model(image, None, None, [], False, False, "saddle_balanced").depth


def _measure(compiled, value: np.ndarray, iterations: int):
    compiled([value])
    times = []
    output = None
    for _index in range(iterations):
        started = time.perf_counter()
        output = compiled([value])[0]
        times.append(time.perf_counter() - started)
    assert output is not None
    return np.asarray(output), {
        "median_seconds": float(np.median(times)),
        "minimum_seconds": float(np.min(times)),
        "iterations": iterations,
    }


def _agreement(reference: np.ndarray, candidate: np.ndarray):
    reference = np.asarray(reference, dtype=np.float32).ravel()
    candidate = np.asarray(candidate, dtype=np.float32).ravel()
    return {
        "mean_absolute_error": float(np.mean(np.abs(reference - candidate))),
        "correlation": float(np.corrcoef(reference, candidate)[0, 1]),
    }


def run(args):
    requested = str(args.device).strip().upper()
    if requested.startswith("GPU") and not args.allow_gpu:
        raise RuntimeError(
            "Execução OpenVINO na GPU bloqueada por segurança. Em 16/07/2026, "
            "a Arc B570 com o driver xe ficou wedged durante este benchmark e "
            "derrubou a sessão gráfica. Use CPU ou informe --allow-gpu somente "
            "em uma sessão de teste sem depender da GPU para o vídeo."
        )
    torch.set_num_threads(args.threads)
    core = ov.Core()
    report = {
        "openvino": ov.__version__,
        "requested_device": args.device,
        "available_devices": list(core.available_devices),
        "models": {},
    }

    da2 = load_da2(args.da2_weights)
    raw = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    da2_input, _size = da2.image2tensor(raw, args.process_res)
    with torch.inference_mode():
        da2_reference = da2(da2_input).detach().cpu().numpy()
    started = time.perf_counter()
    da2_ov = ov.convert_model(da2, example_input=da2_input)
    conversion = time.perf_counter() - started
    compiled = core.compile_model(da2_ov, args.device)
    da2_output, stats = _measure(compiled, da2_input.numpy(), args.iterations)
    report["models"]["da2_small"] = {
        "status": "ok",
        "input_shape": list(da2_input.shape),
        "conversion_seconds": conversion,
        **stats,
        **_agreement(da2_reference, da2_output),
    }
    del compiled, da2_ov, da2

    try:
        from depth_anything_3.utils.io.input_processor import InputProcessor

        da3 = load_da3(args.da3_dir)
        da3_input, _, _ = InputProcessor()(
            [str(args.image)],
            process_res=args.process_res,
            process_res_method="upper_bound_resize",
            num_workers=1,
            sequential=True,
        )
        da3_input = da3_input[None]
        wrapper = DA3DepthOnly(da3).eval()
        with torch.inference_mode():
            da3_reference = wrapper(da3_input).detach().cpu().numpy()
        started = time.perf_counter()
        da3_ov = ov.convert_model(wrapper, example_input=da3_input)
        conversion = time.perf_counter() - started
        compiled = core.compile_model(da3_ov, args.device)
        da3_output, stats = _measure(compiled, da3_input.numpy(), args.iterations)
        report["models"]["da3_small"] = {
            "status": "ok",
            "input_shape": list(da3_input.shape),
            "conversion_seconds": conversion,
            **stats,
            **_agreement(da3_reference, da3_output),
        }
    except Exception as exc:
        report["models"]["da3_small"] = {
            "status": "conversion_error",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    return report


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--da2-weights", type=Path, required=True)
    parser.add_argument("--da3-dir", type=Path, required=True)
    parser.add_argument("--device", default="CPU")
    parser.add_argument(
        "--allow-gpu",
        action="store_true",
        help="Confirma conscientemente um teste GPU de risco fora da sessão gráfica.",
    )
    parser.add_argument("--process-res", type=int, default=280)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--threads", type=int, default=6)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    result = run(arguments)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
