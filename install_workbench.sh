#!/usr/bin/env bash
set -euo pipefail

WORKBENCH_NAME="WoodCAM2D"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_MOD_DIR="$HOME/.local/share/FreeCAD/Mod"
TARGET_DIRS=("${FREECAD_MOD_DIR:-$DEFAULT_MOD_DIR}")
AI_MODE="ask"

for arg in "$@"; do
    case "$arg" in
        --with-ai) AI_MODE="with" ;;
        --without-ai) AI_MODE="without" ;;
        --help|-h)
            cat <<'EOF'
Uso: ./install_workbench.sh [--with-ai|--without-ai]

Instala somente o WoodCAM por padrão e pergunta se a IA opcional de relevo
deve ser instalada. --with-ai aceita a instalação automaticamente; --without-ai
instala apenas a bancada.
EOF
            exit 0
            ;;
        *)
            echo "Opção desconhecida: $arg" >&2
            exit 2
            ;;
    esac
done

for versioned_mod_dir in "$HOME"/.local/share/FreeCAD/v*/Mod; do
    if [[ -d "$versioned_mod_dir" ]]; then
        TARGET_DIRS+=("$versioned_mod_dir")
    fi
done

for mod_dir in "${TARGET_DIRS[@]}"; do
    mkdir -p "$mod_dir"
    ln -sfn "$SOURCE_DIR" "$mod_dir/$WORKBENCH_NAME"
    echo "WoodCAM 2D instalado em: $mod_dir/$WORKBENCH_NAME"
done

if command -v freecadcmd >/dev/null 2>&1; then
    freecadcmd -c "
import FreeCAD

param = FreeCAD.ParamGet('User parameter:BaseApp/Preferences/Workbenches')
name = 'WoodCAM2DWorkbench'
ordered = [item for item in param.GetString('Ordered', '').split(',') if item]
disabled = [item for item in param.GetString('Disabled', '').split(',') if item and item != name]

if name not in ordered:
    ordered.append(name)

param.SetString('Ordered', ','.join(ordered))
param.SetString('Disabled', ','.join(disabled))
FreeCAD.saveParameter()
print('WoodCAM 2D registrado nas preferencias de workbenches.')
"
else
    echo "Aviso: freecadcmd nao encontrado; a bancada foi instalada, mas nao foi adicionada a lista ordenada."
fi

echo "Reinicie o FreeCAD e selecione a bancada 'WoodCAM 2D'."

if [[ "$AI_MODE" == "ask" && -t 0 ]]; then
    echo ""
    echo "IA opcional de relevo (Depth Anything + OpenVINO/CPU)"
    echo "- baixa aproximadamente 1,7 GB de runtime e modelos;"
    echo "- roda fora do Python do FreeCAD e somente em CPU;"
    echo "- é opcional: o WoodCAM continua funcionando sem ela;"
    echo "- os pesos têm licenças próprias e não são incluídos no Git."
    read -r -p "Deseja instalar a IA opcional agora? [s/N] " answer
    case "$answer" in
        s|S|sim|SIM|Sim) AI_MODE="with" ;;
        *) AI_MODE="without" ;;
    esac
fi

if [[ "$AI_MODE" == "with" ]]; then
    if [[ -x "$SOURCE_DIR/install_relief_ai.sh" ]]; then
        echo "Instalando a IA opcional de relevo…"
        if ! bash "$SOURCE_DIR/install_relief_ai.sh"; then
            echo "Aviso: a bancada foi instalada, mas a IA opcional não terminou." >&2
            echo "Você pode tentar novamente com: bash install_relief_ai.sh" >&2
        fi
    else
        echo "Aviso: install_relief_ai.sh não foi encontrado; IA não instalada." >&2
    fi
else
    echo "IA opcional não instalada. Para instalar depois: bash install_relief_ai.sh"
fi
