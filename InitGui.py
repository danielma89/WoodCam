"""Inicialização da bancada única PanelNest, incluindo todo o WoodCAM."""

import importlib.util
import os
import sys

import FreeCAD


if FreeCAD.GuiUp:
    try:
        project_root = os.path.realpath(
            os.environ.get("WOODCAM_UNIFIED_ROOT", "").strip()
            or os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "PanelNest")
        )
        panelnest_root = os.path.join(project_root, "PanelNest")
        init_gui = os.path.join(panelnest_root, "InitGui.py")
        if not os.path.isfile(init_gui):
            raise RuntimeError(
                "A instalação unificada está incompleta: PanelNest/InitGui.py não foi encontrado."
            )
        if panelnest_root not in sys.path:
            sys.path.insert(0, panelnest_root)
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        loader_module = "_panelnest_unified_initgui"
        if loader_module not in sys.modules:
            spec = importlib.util.spec_from_file_location(loader_module, init_gui)
            if spec is None or spec.loader is None:
                raise RuntimeError("Não foi possível inicializar a bancada PanelNest.")
            module = importlib.util.module_from_spec(spec)
            sys.modules[loader_module] = module
            spec.loader.exec_module(module)
    except Exception as error:
        FreeCAD.Console.PrintError(
            "PanelNest/WoodCAM: falha ao carregar a bancada unificada: %s\n" % error
        )
