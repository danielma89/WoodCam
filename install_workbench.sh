#!/usr/bin/env bash
set -euo pipefail

WORKBENCH_NAME="PanelNest"
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

Instala a bancada única PanelNest, já com Editor 2D, WoodCAM e CAM. O instalador
pergunta se a IA opcional de relevo deve ser instalada. --with-ai aceita a
instalação automaticamente; --without-ai instala a bancada sem essa IA.
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
    version_name="$(basename "$(dirname "$versioned_mod_dir")")"
    [[ "$version_name" =~ ^v[0-9]+-[0-9]+$ ]] || continue
    [[ -d "$versioned_mod_dir" ]] && TARGET_DIRS+=("$versioned_mod_dir")
done

for mod_dir in "${TARGET_DIRS[@]}"; do
    mkdir -p "$mod_dir"
    target="$mod_dir/$WORKBENCH_NAME"
    if [[ -e "$target" && ! -L "$target" ]]; then
        echo "Conflito: já existe uma pasta real em $target" >&2
        echo "Renomeie essa instalação antiga antes de continuar." >&2
        exit 1
    fi
    ln -sfn "$SOURCE_DIR" "$target"

    legacy_target="$mod_dir/WoodCAM2D"
    if [[ -L "$legacy_target" && "$(readlink -f "$legacy_target")" == "$SOURCE_DIR" ]]; then
        rm "$legacy_target"
        echo "Atalho antigo WoodCAM2D removido: agora há uma única bancada."
    fi
    echo "PanelNest/WoodCAM instalado em: $target"
done

if command -v freecadcmd >/dev/null 2>&1; then
    freecadcmd -c "
import FreeCAD

param = FreeCAD.ParamGet('User parameter:BaseApp/Preferences/Workbenches')
name = 'PanelNestWorkbench'
legacy_name = 'WoodCAM2DWorkbench'
ordered = [item for item in param.GetString('Ordered', '').split(',') if item]
ordered = [item for item in ordered if item != legacy_name]
disabled = [item for item in param.GetString('Disabled', '').split(',') if item and item not in (name, legacy_name)]

if name not in ordered:
    ordered.append(name)

param.SetString('Ordered', ','.join(ordered))
param.SetString('Disabled', ','.join(disabled))
FreeCAD.saveParameter()
print('PanelNest/WoodCAM registrado como bancada unica nas preferencias.')
"
else
    echo "Aviso: freecadcmd nao encontrado; a bancada foi instalada, mas nao foi adicionada a lista ordenada."
fi

echo "Reinicie o FreeCAD e selecione a bancada 'PanelNest'. O WoodCAM fica no menu CAM."

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
