import os
import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui

_WB_DIR = os.path.dirname(os.path.realpath(__file__))
if _WB_DIR not in sys.path:
    sys.path.insert(0, _WB_DIR)


def _log_startup(message):
    try:
        if App is not None:
            App.Console.PrintMessage(message)
    except Exception:
        pass


_log_startup("PanelNest: InitGui.py inicio\n")


def _restore_textures_for_document(doc):
    """Restaura texturas Coin3D para todos os objetos do documento que têm material com textura."""
    if doc is None:
        return
    try:
        from panelnest.textures import apply_texture_to_object, texture_path
        from panelnest.materials import get_by_name
        from panelnest.edge_band import _edge_band_flags_dict_from_object
        for obj in doc.Objects:
            mat_name = str(getattr(obj, "PanelNestMaterial", "") or "").strip()
            if not mat_name:
                continue
            mat = get_by_name(mat_name)
            if mat is None or not mat.texture_id:
                continue
            png = texture_path(mat.texture_id)
            if png is None:
                continue
            # Não reaaplica se tem fita de borda (DiffuseColor cuida das cores por face)
            try:
                flags = _edge_band_flags_dict_from_object(obj)
                if any(flags.values()):
                    continue
            except Exception:
                pass
            try:
                apply_texture_to_object(obj, png)
            except Exception:
                pass
    except Exception:
        pass


_panelnest_doc_observer = None


class _PanelNestDocObserver:
    """Observer que restaura texturas quando um documento é aberto ou ativado."""

    def slotActivateDocument(self, doc):
        _restore_textures_for_document(doc)

    def slotFinishOpenDocument(self, doc):
        _restore_textures_for_document(doc)


def _ensure_document_observer():
    global _panelnest_doc_observer
    if App is None or Gui is None:
        return
    if _panelnest_doc_observer is None:
        _panelnest_doc_observer = _PanelNestDocObserver()
        try:
            App.addDocumentObserver(_panelnest_doc_observer)
        except Exception:
            pass


class PanelNestWorkbench(Gui.Workbench):
    """Workbench scaffold for panel nesting workflows."""

    MenuText = "PanelNest"
    ToolTip = "Etiquetagem, layout de corte e exportacao de chapas para marcenaria"
    Icon = ""

    def __init__(self):
        # Não importe o pacote panelnest inteiro aqui: o FreeCAD chama isto
        # durante o registro da bancada. Se uma dependência de comando falhar,
        # a bancada desaparece do seletor. O caminho direto mantém o registro
        # leve e resiliente.
        return

    def Initialize(self):
        import importlib
        from panelnest.i18n import install_language_menu

        def load_command_module(name):
            try:
                return importlib.import_module(f"commands.{name}")
            except Exception as exc:
                if App is not None:
                    App.Console.PrintError(
                        f"PanelNest: falha ao carregar comando {name}: {exc}\n"
                    )
                return None

        apply_edge_band_by_face = load_command_module("apply_edge_band_by_face")
        apply_material = load_command_module("apply_material")
        collect_parts_to_sheet = load_command_module("collect_parts_to_sheet")
        configure_sheet = load_command_module("configure_sheet")
        edit_parts = load_command_module("edit_parts")
        edit_hardware = load_command_module("edit_hardware")
        export_reports = load_command_module("export_reports")
        furniture_catalog = load_command_module("furniture_catalog")
        generate_layout = load_command_module("generate_layout")
        generate_quotation = load_command_module("generate_quotation")
        import_generated_remnants = load_command_module("import_generated_remnants")
        import_parts_csv = load_command_module("import_parts_csv")
        label_parts = load_command_module("label_parts")
        organize_parts = load_command_module("organize_parts")
        open_woodcam2d = load_command_module("open_woodcam2d")
        prepare_part_metadata = load_command_module("prepare_part_metadata")
        production_tracking = load_command_module("production_tracking")
        generate_shape_layout = load_command_module("generate_shape_layout")
        remnant_db_browser = load_command_module("remnant_db_browser")
        validate_project = load_command_module("validate_project")

        def command_name(module, attr="COMMAND_NAME"):
            return getattr(module, attr, None) if module is not None else None

        def command_list(items):
            return [item for item in items if item]

        self.toolbar_commands = command_list(
            [
                command_name(furniture_catalog),
                command_name(import_parts_csv),
                command_name(label_parts),
                command_name(prepare_part_metadata),
                command_name(apply_edge_band_by_face),
                command_name(apply_material),
                command_name(apply_material, "ROTATE_GRAIN_COMMAND_NAME"),
                command_name(organize_parts),
                command_name(configure_sheet),
                command_name(validate_project),
                command_name(generate_layout),
                command_name(generate_shape_layout),
                command_name(export_reports),
            ]
        )
        self.auxiliary_commands = command_list(
            [
                command_name(edit_parts),
                command_name(edit_hardware),
                command_name(collect_parts_to_sheet),
                command_name(import_generated_remnants),
                command_name(remnant_db_browser),
                command_name(generate_quotation),
                command_name(production_tracking),
            ]
        )
        self.cam_commands = command_list([command_name(open_woodcam2d)])
        self.commands = self.toolbar_commands + self.auxiliary_commands + self.cam_commands
        if self.toolbar_commands:
            self.appendToolbar(self.MenuText, self.toolbar_commands)
            self.appendMenu(self.MenuText, self.toolbar_commands)
        if self.cam_commands:
            self.appendToolbar("PanelNest CAM", self.cam_commands)
            self.appendMenu([self.MenuText, "CAM"], self.cam_commands)
        if self.auxiliary_commands:
            self.appendMenu(
                [self.MenuText, "Ferramentas Auxiliares"],
                self.auxiliary_commands,
            )
        # PanelNest is the single production bench.  The language selector is
        # installed in FreeCAD's main menu bar so it also covers CAM/WoodCAM,
        # command dialogs and any future PanelNest tools.
        install_language_menu()

    def Activated(self):
        try:
            from panelnest.i18n import install_language_menu

            install_language_menu()
        except Exception:
            pass
        if App is None or App.ActiveDocument is None:
            return
        try:
            import panelnest
            panelnest.refresh_part_edge_band_visuals(App.ActiveDocument.Objects)
        except Exception:
            pass
        try:
            _restore_textures_for_document(App.ActiveDocument)
        except Exception:
            pass
        try:
            _ensure_document_observer()
        except Exception:
            pass

    def Deactivated(self):
        return

    def ContextMenu(self, recipient):
        self.appendContextMenu(self.MenuText, self.toolbar_commands)
        self.appendContextMenu([self.MenuText, "CAM"], self.cam_commands)
        self.appendContextMenu([self.MenuText, "Ferramentas Auxiliares"], self.auxiliary_commands)

    def GetClassName(self):
        return "Gui::PythonWorkbench"


PanelNestWorkbench.Icon = os.path.join(
    _WB_DIR,
    "resources",
    "icons",
    "panelnest_workbench.svg",
)


if Gui is not None:
    try:
        Gui.addWorkbench(PanelNestWorkbench())
        if App is not None:
            App.Console.PrintMessage("PanelNest: addWorkbench OK\n")
    except Exception:
        if App is not None:
            App.Console.PrintError(
                "PanelNest: erro em addWorkbench:\n" + traceback.format_exc()
            )
