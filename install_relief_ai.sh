#!/usr/bin/env bash
set -euo pipefail

# Optional, isolated CPU runtime for "Criar relevo 3D por imagem".
# It never installs packages in FreeCAD's Python and never enables a GPU.

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${WOODCAM_RELIEF_AI_DIR:-$HOME/.local/share/WoodCAM2D/relief-ai}"
CONFIG_FILE="${WOODCAM_RELIEF_AI_CONFIG:-$HOME/.config/WoodCAM2D/relief_ai.json}"
DA2_MODEL_SHA256="715fade13be8f229f8a70cc02066f656f2423a59effd0579197bbf57860e1378"
DA2_MODEL_URL="https://huggingface.co/depth-anything/Depth-Anything-V2-Small/resolve/main/depth_anything_v2_vits.pth"
NORMALS_MODEL_SHA256="4afcc0893dbb3c0c63e270f8bb24bfa63ccf2dc68ab9c0c3601fdae4f0aafd9b"
NORMALS_MODEL_URL="https://huggingface.co/onnx-community/metric3d-vit-small/resolve/main/onnx/model_fp16.onnx"
SOURCE_COMMIT="a561b849ebae10a6f5ef49e26c83cbbcd36c71bf"
SOURCE_URL="https://github.com/DepthAnything/Depth-Anything-V2/archive/${SOURCE_COMMIT}.tar.gz"

if [[ -e "$INSTALL_DIR" ]]; then
    echo "A instalação já existe: $INSTALL_DIR" >&2
    echo "Nada foi sobrescrito." >&2
    exit 2
fi

BASE_PYTHON="${WOODCAM_RELIEF_AI_PYTHON:-}"
if [[ -z "$BASE_PYTHON" ]]; then
    BLENDER_PYTHON="$HOME/Aplicativos/blender-cam/blender-5.0.1-linux-x64/5.0/python/bin/python3.11"
    if [[ -x "$BLENDER_PYTHON" ]]; then
        BASE_PYTHON="$BLENDER_PYTHON"
    elif command -v python3.11 >/dev/null 2>&1; then
        BASE_PYTHON="$(command -v python3.11)"
    else
        echo "Python 3.11 não encontrado. Defina WOODCAM_RELIEF_AI_PYTHON." >&2
        exit 2
    fi
fi

PARENT_DIR="$(dirname "$INSTALL_DIR")"
mkdir -p "$PARENT_DIR"
STAGING_DIR="$(mktemp -d "$PARENT_DIR/.relief-ai-install.XXXXXX")"
cleanup() {
    if [[ -d "$STAGING_DIR" ]]; then
        rm -rf -- "$STAGING_DIR"
    fi
}
trap cleanup EXIT

"$BASE_PYTHON" -m venv --copies "$STAGING_DIR/venv"
"$STAGING_DIR/venv/bin/python" -m pip install --upgrade pip
"$STAGING_DIR/venv/bin/python" -m pip install \
    --index-url https://download.pytorch.org/whl/cpu \
    'torch==2.13.0+cpu' 'torchvision==0.28.0+cpu'
"$STAGING_DIR/venv/bin/python" -m pip install \
    'numpy==2.4.4' 'opencv-python==5.0.0.93' 'pillow==12.2.0' \
    'openvino==2026.2.1'

mkdir -p "$STAGING_DIR/model-source" "$STAGING_DIR/models"
curl --fail --location --output "$STAGING_DIR/source.tar.gz" "$SOURCE_URL"
tar -xzf "$STAGING_DIR/source.tar.gz" -C "$STAGING_DIR/model-source" --strip-components=1
rm "$STAGING_DIR/source.tar.gz"
curl --fail --location --output "$STAGING_DIR/models/depth_anything_v2_vits.pth" "$DA2_MODEL_URL"
echo "$DA2_MODEL_SHA256  $STAGING_DIR/models/depth_anything_v2_vits.pth" | sha256sum --check --status
curl --fail --location --output "$STAGING_DIR/models/metric3d_v2_vit_small_fp16.onnx" "$NORMALS_MODEL_URL"
echo "$NORMALS_MODEL_SHA256  $STAGING_DIR/models/metric3d_v2_vit_small_fp16.onnx" | sha256sum --check --status

mv "$STAGING_DIR" "$INSTALL_DIR"
STAGING_DIR=""
mkdir -p "$(dirname "$CONFIG_FILE")"
"$INSTALL_DIR/venv/bin/python" - "$CONFIG_FILE" "$INSTALL_DIR" "$PROJECT_DIR" <<'PY'
import json
from pathlib import Path
import sys

config = Path(sys.argv[1])
install = Path(sys.argv[2])
project = Path(sys.argv[3])
payload = {
    "model_id": "depth-anything-v2-small+metric3d-v2-small-normals",
    "normals_model_path": str(install / "models/metric3d_v2_vit_small_fp16.onnx"),
    "python_executable": str(install / "venv/bin/python"),
    "source_root": str(install / "model-source"),
    "weights_path": str(install / "models/depth_anything_v2_vits.pth"),
    "worker_script": str(project / "woodcam_relief/ai_worker.py"),
}
config.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

echo "IA Pro de relevo (DA2 + Metric3D) instalada somente para CPU."
echo "Runtime: $INSTALL_DIR"
echo "Configuração: $CONFIG_FILE"
