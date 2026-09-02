"""Ponte para abrir o WoodCAM 2D dentro da bancada PanelNest."""

import importlib
import os
import sys
from pathlib import Path

try:
    import FreeCAD as App
except ImportError:
    App = None

try:
    import FreeCADGui as Gui
except ImportError:
    Gui = None


COMMAND_NAME = "PanelNest_OpenWoodCAM2D"


def _woodcam_root_candidates():
    candidates = []
    configured_path = os.environ.get("WOODCAM2D_PATH", "").strip()
    if configured_path:
        candidates.append(Path(configured_path).expanduser())

    panelnest_root = Path(__file__).resolve().parent.parent
    candidates.extend(
        [
            # Instalação unificada: PanelNest vive dentro da raiz do WoodCAM.
            panelnest_root.parent,
            panelnest_root / "WoodCAM2D",
            panelnest_root / "woodcam2d",
            panelnest_root.parent / "WoodCAM2D",
            panelnest_root.parent / "CNC Marcenaria",
            panelnest_root.parent.parent / "WoodCAM2D",
            panelnest_root.parent.parent / "CNC Marcenaria",
        ]
    )

    if App is not None and hasattr(App, "getUserAppDataDir"):
        try:
            freecad_user_dir = Path(App.getUserAppDataDir())
            candidates.extend(
                [
                    freecad_user_dir / "Mod" / "WoodCAM2D",
                    freecad_user_dir / "Mod" / "CNC Marcenaria",
                ]
            )
        except Exception:
            pass

    home = Path.home()
    candidates.extend(
        [
            home / ".local" / "share" / "FreeCAD" / "Mod" / "WoodCAM2D",
            home / "Projetos" / "CNC Marcenaria",
        ]
    )
    versioned_root = home / ".local" / "share" / "FreeCAD"
    if versioned_root.exists():
        candidates.extend(sorted(versioned_root.glob("v*/Mod/WoodCAM2D")))

    unique_candidates = []
    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        candidate_key = str(resolved)
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        unique_candidates.append(resolved)
    return unique_candidates


def find_woodcam_root():
    for candidate in _woodcam_root_candidates():
        if (
            (candidate / "ui.py").is_file()
            and (candidate / "operations.py").is_file()
            and (candidate / "gcode_writer.py").is_file()
        ):
            return candidate
    return None


def woodcam_icon_path():
    try:
        import panelnest

        fallback_icon = panelnest.get_icon_path("panelnest_export.svg")
    except Exception:
        fallback_icon = ""

    try:
        root = find_woodcam_root()
        if root is not None:
            icon = root / "resources" / "diagrams" / "botao2.png"
            if icon.is_file():
                return str(icon)
            icon = root / "resources" / "diagrams" / "botao.png"
            if icon.is_file():
                return str(icon)
            icon = root / "Resources" / "icons" / "woodcam2d.svg"
            if icon.is_file():
                return str(icon)
    except Exception:
        return fallback_icon
    return fallback_icon


def _load_woodcam_ui():
    root = find_woodcam_root()
    if root is None:
        raise RuntimeError(
            "WoodCAM 2D não foi encontrado. A bancada PanelNest foi instalada, "
            "mas o módulo WoodCAM2D não está junto dela. Extraia/copie também a "
            "pasta 'WoodCAM2D' para o diretório 'Mod' do FreeCAD ou defina "
            "WOODCAM2D_PATH. No Windows, o local normalmente é "
            "'%APPDATA%\\FreeCAD\\Mod\\WoodCAM2D'."
        )

    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)

    loaded_ui = sys.modules.get("ui")
    loaded_path = str(getattr(loaded_ui, "__file__", "") or "") if loaded_ui else ""
    if loaded_ui is not None and Path(loaded_path).resolve().parent != root:
        raise RuntimeError(
            "Outro módulo chamado 'ui' já está carregado no FreeCAD. "
            "Reinicie o FreeCAD e abra o WoodCAM pelo PanelNest primeiro."
        )
    return importlib.import_module("ui")


class OpenWoodCAM2DCommand:
    def GetResources(self):
        return {
            "Pixmap": woodcam_icon_path(),
            "MenuText": "WoodCAM 2D",
            "ToolTip": (
                "Abre o WoodCAM 2D na bancada PanelNest para pré-visualizar, "
                "simular e gerar G-code CNC."
            ),
        }

    def IsActive(self):
        return App is not None and App.ActiveDocument is not None

    def Activated(self):
        try:
            woodcam_ui = _load_woodcam_ui()
            woodcam_ui.show_woodcam2d_dialog(
                Gui.getMainWindow() if Gui is not None else None
            )
        except Exception as exc:
            if App is not None:
                App.Console.PrintError(f"PanelNest / WoodCAM 2D: {exc}\n")
            if Gui is not None:
                try:
                    from PySide import QtGui

                    QtGui.QMessageBox.critical(
                        Gui.getMainWindow(),
                        "PanelNest: WoodCAM 2D",
                        str(exc),
                    )
                except Exception:
                    pass


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, OpenWoodCAM2DCommand())
