import os

try:
    import FreeCAD as App
except ImportError:
    App = None

try:
    import FreeCADGui as Gui
except ImportError:
    Gui = None

try:
    from PySide import QtGui

    QtWidgets = QtGui
except ImportError:
    try:
        from PySide2 import QtWidgets
    except ImportError:
        from PySide6 import QtWidgets


COMMAND_NAME = "PanelNest_GenerateLayout"


def _main_window():
    if Gui is not None and hasattr(Gui, "getMainWindow"):
        try:
            return Gui.getMainWindow()
        except Exception:
            return None
    return None


def _show_error_dialog(message):
    if Gui is None:
        return
    QtWidgets.QMessageBox.critical(_main_window(), "PanelNest: Gerar Layout", message)


def _show_warning_dialog(
    summary_sheet,
    warnings,
    warnings_sheet,
    utilization_text="",
    sheet_utilization_lines=None,
    cut_plan_sheet=None,
    generated_remnants_sheet=None,
    validation_sheet=None,
    edge_band_sheet=None,
):
    if Gui is None or not warnings:
        return

    import panelnest

    lines = []
    for warning in warnings[:3]:
        stock = str(warning.get("stock", "") or "").strip()
        problem = panelnest._layout_warning_problem(warning)
        if stock and problem:
            lines.append(f"- {stock}: {problem}")
        elif problem:
            lines.append(f"- {problem}")
    extra_count = len(warnings) - len(lines)
    if extra_count > 0:
        lines.append(f"- mais {extra_count} alerta(s) na planilha '{warnings_sheet.Label}'")

    details = "\n".join(lines)
    utilization_line = f"Aproveitamento total: {utilization_text}\n\n" if utilization_text else ""
    sheet_utilization_text = ""
    if sheet_utilization_lines:
        preview_lines = [f"- {line}" for line in sheet_utilization_lines[:5]]
        extra_sheet_count = len(sheet_utilization_lines) - len(preview_lines)
        if extra_sheet_count > 0:
            preview_lines.append(f"- mais {extra_sheet_count} chapa(s) no resumo")
        sheet_utilization_text = "Aproveitamento por chapa:\n" + "\n".join(preview_lines) + "\n\n"
    warnings_sheet_line = f"Detalhes completos em: {warnings_sheet.Label}" if warnings_sheet is not None else ""
    message = (
        f"O layout foi gerado, mas ha alertas para revisar.\n\n"
        f"{utilization_line}"
        f"{sheet_utilization_text}"
        f"Pendencias principais:\n"
        f"{details}"
        f"\n\n{warnings_sheet_line}"
    )
    QtWidgets.QMessageBox.warning(_main_window(), "PanelNest: Layout com Alertas", message)


class GenerateLayoutCommand:
    def Activated(self):
        import panelnest

        # Capturar seleção antes de qualquer operação que possa limpá-la
        _raw_selection = Gui.Selection.getSelection() if Gui is not None else []
        _all_source = panelnest.get_part_source_objects()
        # Usar seleção parcial; senão (vazia ou igual ao total) usar todos
        if _raw_selection and len(_raw_selection) < len(_all_source):
            _use_selection = _raw_selection
        else:
            _use_selection = None  # collect_parts usará todos os visíveis

        try:
            parts = panelnest.collect_parts(_use_selection)
            settings = panelnest.get_sheet_settings()

            # Detecção automática de ferragens por diâmetro de furo
            try:
                from panelnest.parts import apply_detected_hardware
                hw_count, hw_parts = apply_detected_hardware(parts, settings.hardware_catalog)
                if hw_count > 0 and App is not None:
                    App.Console.PrintMessage(
                        f"PanelNest: detectou {hw_count} ferragem(ns) automaticamente em {hw_parts} peça(s).\n"
                    )
                    parts = panelnest.collect_parts(_use_selection)
            except Exception:
                pass

            try:
                layout_sheets, root = panelnest.create_layout_model(parts, settings=settings)
            except ValueError as exc:
                # Se retalhos insuficientes e chapas = 0, perguntar se quer completar com chapa nova
                if settings.full_sheet_count == 0 and not settings.allow_extra_full_sheets:
                    reply = QtWidgets.QMessageBox.question(
                        Gui.getMainWindow() if Gui is not None else None,
                        "Retalhos insuficientes",
                        f"Os retalhos configurados não são suficientes para todas as peças.\n\n"
                        f"Detalhe: {exc}\n\n"
                        "Deseja completar os cortes restantes usando chapas inteiras novas?",
                        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    )
                    if reply != QtWidgets.QMessageBox.Yes:
                        if App is not None:
                            App.Console.PrintMessage("PanelNest: layout cancelado pelo usuario.\n")
                        return
                    from dataclasses import replace as _dc_replace
                    settings = _dc_replace(settings, allow_extra_full_sheets=True)
                    layout_sheets, root = panelnest.create_layout_model(parts, settings=settings)
                else:
                    raise

            summary_sheet = panelnest.create_sheet_summary_spreadsheet(layout_sheets, settings)
            cut_plan_sheet = panelnest.create_cut_plan_spreadsheet(layout_sheets)
            generated_remnants_sheet = panelnest.create_generated_remnants_spreadsheet(
                layout_sheets,
                settings,
            )
            labels_sheet = panelnest.create_part_labels_spreadsheet(parts)
            validation_sheet = panelnest.create_project_validation_spreadsheet(
                parts=parts,
                settings=settings,
                layout_sheets=layout_sheets,
            )
            edge_band_sheet = panelnest.create_edge_band_spreadsheet(parts)
            cost_sheet = panelnest.create_cost_report_spreadsheet(layout_sheets, settings)
            fita_sheet = panelnest.create_edge_band_consumption_spreadsheet(parts, settings)
            hardware_sheet = panelnest.create_hardware_spreadsheet(parts, settings)
            warnings = panelnest.collect_layout_warnings(parts, layout_sheets, settings)
            warnings_sheet = panelnest.create_layout_warnings_spreadsheet(warnings)
        except ValueError as exc:
            if App is not None:
                App.Console.PrintError(f"PanelNest: {exc}\n")
            _show_error_dialog(str(exc))
            return

        total_parts = sum(len(layout_sheet.placements) for layout_sheet in layout_sheets)
        total_groups = len({layout_sheet.group_id for layout_sheet in layout_sheets})
        utilization_ratio = panelnest.layout_overall_utilization_ratio(layout_sheets, settings)
        utilization_text = f"{utilization_ratio * 100:.1f}%"
        sheet_utilization_lines = panelnest.layout_sheet_utilization_lines(layout_sheets, settings)
        extra_sheets = [ls for ls in layout_sheets if getattr(ls, "source_kind", "") == "Chapa extra"]


        if App is not None:
            message = (
                "PanelNest: gerou layout para "
                f"{total_parts} peca(s), {len(layout_sheets)} chapa(s) e {total_groups} grupo(s). "
                f"Aproveitamento total: {utilization_text}. "
                f"Resumo criado em '{summary_sheet.Label}'."
            )
            if cut_plan_sheet is not None:
                message += f" Plano de corte em '{cut_plan_sheet.Label}'."
            if generated_remnants_sheet is not None:
                message += f" Retalhos gerados em '{generated_remnants_sheet.Label}'."
            if labels_sheet is not None:
                message += f" Etiquetas em '{labels_sheet.Label}'."
            if cost_sheet is not None:
                message += f" Custos em '{cost_sheet.Label}'."
            if fita_sheet is not None:
                message += f" Fita de borda em '{fita_sheet.Label}'."
            if hardware_sheet is not None:
                message += f" Ferragens em '{hardware_sheet.Label}'."
            if validation_sheet is not None:
                message += f" Validacao em '{validation_sheet.Label}'."
            if edge_band_sheet is not None:
                message += f" Acabamento em '{edge_band_sheet.Label}'."
            if extra_sheets and settings.full_sheet_count == 0:
                message += (
                    f" AVISO: os retalhos nao foram suficientes — "
                    f"{len(extra_sheets)} chapa(s) extra(s) foram adicionadas automaticamente."
                )
            if warnings_sheet is not None:
                message += f" Alertas em '{warnings_sheet.Label}'."
                if hasattr(App.Console, "PrintWarning"):
                    App.Console.PrintWarning(f"{message}\n")
                else:
                    App.Console.PrintMessage(f"{message}\n")
            else:
                App.Console.PrintMessage(f"{message}\n")

        if Gui is not None:
            Gui.Selection.clearSelection()

        # Reaplicar texturas nas chapas base — os vários doc.recompute() feitos
        # pelas planilhas de resumo, corte, etc. descartam os nós Coin3D do
        # ViewObject. Reaplicamos a partir do registry para que todas as chapas
        # apareçam texturizadas no final.
        try:
            from panelnest.textures import reapply_registered_textures
            reapply_registered_textures()
        except Exception:
            pass

        if warnings_sheet is not None:
            _show_warning_dialog(
                summary_sheet,
                warnings,
                warnings_sheet,
                utilization_text,
                sheet_utilization_lines,
                cut_plan_sheet=cut_plan_sheet,
                generated_remnants_sheet=generated_remnants_sheet,
                validation_sheet=validation_sheet,
                edge_band_sheet=edge_band_sheet,
            )

    def IsActive(self):
        if App is None:
            return False
        return App.ActiveDocument is not None

    def GetResources(self):
        return {
            "Pixmap": os.path.join(
                os.path.dirname(__file__), "..", "resources", "icons", "panelnest_layout.svg"
            ),
            "Accel": "",
            "MenuText": "Gerar Layout",
            "ToolTip": "Gera o layout das pecas com a configuracao atual e cria resumo, plano de corte, alertas e retalhos.",
        }


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, GenerateLayoutCommand())
