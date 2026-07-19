"""Optional external AI-depth boundary for image reliefs.

The FreeCAD Python process never imports PyTorch or OpenVINO.  An explicitly
configured Python runtime owns inference and writes a temporary grayscale PNG;
this module validates that result and converts it to the existing HeightMapData
contract while preserving the original image identity.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Mapping, Optional, Tuple

from .heightmap import HeightMapData, ReliefOptions, load_heightmap


CONFIG_ENV = "WOODCAM_RELIEF_AI_CONFIG"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "WoodCAM2D" / "relief_ai.json"
MODEL_ID = "depth-anything-v2-small"
MODEL_BYTES = 99_218_434


class AIRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class AIDepthRuntime:
    python_executable: Path
    worker_script: Path
    weights_path: Path
    source_root: Path
    model_id: str = MODEL_ID
    normals_model_path: Optional[Path] = None

    def validate(self) -> "AIDepthRuntime":
        checks = (
            (self.python_executable, "Python da IA", True),
            (self.worker_script, "worker da IA", False),
            (self.weights_path, "pesos da IA", False),
            (self.source_root, "código-fonte do modelo", False),
        )
        for path, label, executable in checks:
            candidate = Path(path).expanduser()
            if not candidate.exists():
                raise AIRuntimeError("%s não encontrado: %s" % (label, candidate))
            if executable and not os.access(str(candidate), os.X_OK):
                raise AIRuntimeError("%s não é executável: %s" % (label, candidate))
        if self.normals_model_path is not None:
            normals = Path(self.normals_model_path).expanduser()
            if not normals.is_file():
                raise AIRuntimeError("pesos da IA de normais não encontrados: %s" % normals)
        return self

    @property
    def has_surface_normals(self) -> bool:
        return self.normals_model_path is not None

    def command(
        self,
        image_path: str | Path,
        output_path: str | Path,
        *,
        process_resolution: int = 504,
        threads: int = 6,
        detail_strength: float = 0.14,
        refinement_profile: str = "balanced",
    ) -> Tuple[str, ...]:
        self.validate()
        resolution = int(process_resolution)
        if resolution < 196 or resolution > 1024:
            raise AIRuntimeError("A resolução da IA precisa ficar entre 196 e 1024.")
        thread_count = int(threads)
        if thread_count < 1 or thread_count > 64:
            raise AIRuntimeError("A quantidade de threads da IA é inválida.")
        detail = float(detail_strength)
        if detail < 0.0 or detail > 1.0:
            raise AIRuntimeError("O detalhe híbrido da IA precisa ficar entre 0 e 1.")
        profile = str(refinement_profile).strip().lower()
        if profile not in {"balanced", "sculptural", "detailed"}:
            raise AIRuntimeError("O perfil de refinamento da IA é inválido.")
        command = (
            str(self.python_executable),
            str(self.worker_script),
            "--image",
            str(Path(image_path)),
            "--weights",
            str(self.weights_path),
            "--output",
            str(Path(output_path)),
            "--process-res",
            str(resolution),
            "--threads",
            str(thread_count),
            "--detail-strength",
            "%.6f" % detail,
            "--refinement-profile",
            profile,
        )
        if self.normals_model_path is not None:
            command += ("--normals-model", str(Path(self.normals_model_path)))
        return command

    def process_environment(self, base: Optional[Mapping[str, str]] = None):
        values = dict(os.environ if base is None else base)
        roots = (str(self.source_root), str(Path(self.worker_script).resolve().parents[1]))
        existing = values.get("PYTHONPATH", "")
        values["PYTHONPATH"] = os.pathsep.join(
            entry for entry in roots + ((existing,) if existing else ()) if entry
        )
        values["WOODCAM_AI_DEVICE"] = "CPU"
        return values


def _runtime_from_payload(payload: Mapping[str, object]) -> AIDepthRuntime:
    required = ("python_executable", "worker_script", "weights_path", "source_root")
    missing = [name for name in required if not str(payload.get(name, "")).strip()]
    if missing:
        raise AIRuntimeError("Configuração da IA incompleta: %s" % ", ".join(missing))
    return AIDepthRuntime(
        python_executable=Path(str(payload["python_executable"])).expanduser(),
        worker_script=Path(str(payload["worker_script"])).expanduser(),
        weights_path=Path(str(payload["weights_path"])).expanduser(),
        source_root=Path(str(payload["source_root"])).expanduser(),
        model_id=str(payload.get("model_id", MODEL_ID)),
        normals_model_path=(
            Path(str(payload["normals_model_path"])).expanduser()
            if str(payload.get("normals_model_path", "")).strip()
            else None
        ),
    ).validate()


def discover_ai_runtime(
    config_path: str | Path | None = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[AIDepthRuntime]:
    values = os.environ if environ is None else environ
    selected = config_path or values.get(CONFIG_ENV) or DEFAULT_CONFIG_PATH
    path = Path(selected).expanduser()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise AIRuntimeError("Não foi possível ler %s: %s" % (path, error)) from error
    if not isinstance(payload, dict):
        raise AIRuntimeError("A configuração da IA precisa ser um objeto JSON.")
    return _runtime_from_payload(payload)


def load_ai_heightmap(
    generated_map_path: str | Path,
    original_image_path: str | Path,
    options: ReliefOptions,
    *,
    model_id: str = MODEL_ID,
    refinement_profile: str = "balanced",
) -> HeightMapData:
    """Validate an AI map and retain the original source's identity/provenance."""

    original = Path(original_image_path)
    if not original.is_file():
        raise FileNotFoundError(str(original))
    map_options = replace(options, relief_mode="heightmap", auto_levels=False)
    generated = load_heightmap(generated_map_path, map_options)
    metadata = json.dumps(
        {
            "device": "CPU",
            "model": str(model_id),
            "provider": "external_worker",
            "refinement_profile": str(refinement_profile),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return replace(
        generated,
        source_name=original.name,
        source_sha256=sha256(original.read_bytes()).hexdigest(),
        generator="ai_depth",
        generator_metadata_json=metadata,
    )


__all__ = [
    "AIDepthRuntime",
    "AIRuntimeError",
    "CONFIG_ENV",
    "DEFAULT_CONFIG_PATH",
    "MODEL_BYTES",
    "MODEL_ID",
    "discover_ai_runtime",
    "load_ai_heightmap",
]
