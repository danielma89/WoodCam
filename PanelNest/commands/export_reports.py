import csv
import json
import http.server
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from functools import partial
from pathlib import Path

try:
    import FreeCAD as App
except ImportError:
    App = None

try:
    import FreeCADGui as Gui
except ImportError:
    Gui = None

try:
    from PySide import QtCore, QtGui

    QtWidgets = QtGui
    QtPrintSupport = QtGui
except ImportError:
    try:
        from PySide2 import QtCore, QtGui, QtWidgets, QtPrintSupport
    except ImportError:
        from PySide6 import QtCore, QtGui, QtWidgets, QtPrintSupport


COMMAND_NAME = "PanelNest_ExportReports"
_ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
_GUIDE_SERVER_STATE = {
    "server": None,
    "thread": None,
    "directory": "",
    "base_url": "",
    "log_path": "",
}
_PREVIEW_SERVER_STATE = {
    "server": None,
    "thread": None,
    "directory": "",
    "base_url": "",
}
_GUIDE_SERVER_PORT_CANDIDATES = (8765, 8875, 8985, 0)


class _SilentGuideRequestHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        message = ""
        try:
            message = "%s - - [%s] %s" % (
                self.address_string(),
                self.log_date_time_string(),
                format % args,
            )
        except Exception:
            return

        log_path = str(_GUIDE_SERVER_STATE.get("log_path") or "").strip()
        if log_path:
            try:
                with open(log_path, "a", encoding="utf-8") as handle:
                    handle.write(message + "\n")
            except Exception:
                pass

        if App is not None:
            try:
                App.Console.PrintMessage("PanelNest: guia HTTP %s\n" % message)
            except Exception:
                pass


class _SilentPreviewRequestHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return


def _main_window():
    if Gui is not None and hasattr(Gui, "getMainWindow"):
        try:
            return Gui.getMainWindow()
        except Exception:
            return None
    return None


def _native_path(path):
    if hasattr(QtCore, "QDir") and hasattr(QtCore.QDir, "toNativeSeparators"):
        try:
            return QtCore.QDir.toNativeSeparators(path)
        except Exception:
            return path
    return path


def _show_error_dialog(message):
    if Gui is None:
        return
    QtWidgets.QMessageBox.critical(_main_window(), "PanelNest: Exportar Arquivos", message)


def _show_info_dialog(message):
    if Gui is None:
        return
    QtWidgets.QMessageBox.information(_main_window(), "PanelNest: Exportar Arquivos", message)


def _stop_preview_server():
    server = _PREVIEW_SERVER_STATE.get("server")
    thread = _PREVIEW_SERVER_STATE.get("thread")
    if server is not None:
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
    if thread is not None and thread.is_alive():
        try:
            thread.join(timeout=1.0)
        except Exception:
            pass
    _PREVIEW_SERVER_STATE.update(
        {
            "server": None,
            "thread": None,
            "directory": "",
            "base_url": "",
        }
    )


def _ensure_preview_server(directory):
    target_directory = os.path.realpath(directory)
    current_server = _PREVIEW_SERVER_STATE.get("server")
    current_thread = _PREVIEW_SERVER_STATE.get("thread")
    if (
        current_server is not None
        and current_thread is not None
        and current_thread.is_alive()
        and _PREVIEW_SERVER_STATE.get("directory") == target_directory
    ):
        return _PREVIEW_SERVER_STATE["base_url"]

    _stop_preview_server()
    handler_factory = partial(_SilentPreviewRequestHandler, directory=target_directory)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_factory)
    thread = threading.Thread(
        target=server.serve_forever,
        name="PanelNestPreviewServer",
        daemon=True,
    )
    thread.start()
    base_url = f"http://127.0.0.1:{int(server.server_address[1])}"
    _PREVIEW_SERVER_STATE.update(
        {
            "server": server,
            "thread": thread,
            "directory": target_directory,
            "base_url": base_url,
        }
    )
    return base_url


def _open_external_url(url):
    qurl = QtCore.QUrl(str(url))
    desktop_services = getattr(QtGui, "QDesktopServices", None)
    if desktop_services is not None:
        try:
            if desktop_services.openUrl(qurl):
                return True
        except Exception:
            pass

    opener = shutil.which("xdg-open")
    if opener:
        env = dict(os.environ)
        original_ld_path = env.pop("LD_LIBRARY_PATH_ORIG", "")
        if original_ld_path:
            env["LD_LIBRARY_PATH"] = original_ld_path
        else:
            env.pop("LD_LIBRARY_PATH", None)
        for variable_name in ("APPDIR", "APPIMAGE", "ARGV0", "PYTHONHOME"):
            env.pop(variable_name, None)
        try:
            subprocess.Popen(
                [opener, str(url)],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except Exception:
            pass
    return False


def _write_and_open_preview(html_text, suffix):
    preview_dir = os.path.join(tempfile.gettempdir(), "panelnest_previews")
    os.makedirs(preview_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=suffix,
        prefix="panelnest_",
        dir=preview_dir,
        encoding="utf-8",
        delete=False,
    ) as handle:
        handle.write(html_text)
        html_path = handle.name

    base_url = _ensure_preview_server(preview_dir)
    file_name = urllib.parse.quote(os.path.basename(html_path))
    preview_url = f"{base_url}/{file_name}"
    if App is not None:
        try:
            App.Console.PrintMessage(f"PanelNest: previa disponivel em {preview_url}\n")
        except Exception:
            pass
    if not _open_external_url(preview_url):
        raise RuntimeError(
            "Nao foi possivel abrir o navegador. "
            f"Copie este endereco manualmente: {preview_url}"
        )
    return preview_url


def _severity_counts(issues):
    counts = {"Erro": 0, "Alerta": 0, "Info": 0, "OK": 0}
    for issue in issues or []:
        severity = str((issue or {}).get("severity", "") or "").strip()
        if severity:
            counts[severity] = counts.get(severity, 0) + 1
    return counts


def _validation_summary_line(counts, sheet_label="Validacao PanelNest"):
    return (
        f"{counts.get('Erro', 0)} erro(s), "
        f"{counts.get('Alerta', 0)} alerta(s), "
        f"{counts.get('Info', 0)} info(s). "
        f"Planilha em '{sheet_label}'."
    )


def _validation_issue_reference(issue):
    issue = issue or {}
    part_id = str(issue.get("part_id", "") or "").strip()
    part_label = str(issue.get("part_label", "") or "").strip()
    group_id = str(issue.get("group_id", "") or "").strip()
    object_name = str(issue.get("object_name", "") or "").strip()

    if part_id and part_label and part_label.casefold() != part_id.casefold():
        return f"{part_id} - {part_label}"
    if part_id:
        return part_id
    if part_label:
        return part_label
    if group_id:
        return f"Grupo {group_id}"
    if object_name:
        return object_name
    return ""


def _validation_issue_brief(issue):
    issue = issue or {}
    reference = _validation_issue_reference(issue)
    problem = str(issue.get("problem", "") or "").strip()
    if problem and problem.casefold().startswith("grupo "):
        return problem
    if reference and problem:
        return f"{reference}: {problem}"
    return problem or reference or "Pendencia sem descricao."


def _validation_issue_preview_lines(issues, severities=None, limit=4):
    allowed = None
    if severities:
        allowed = {str(item or "").strip() for item in severities if str(item or "").strip()}

    filtered = []
    for issue in issues or []:
        severity = str((issue or {}).get("severity", "") or "").strip()
        if severity in {"", "OK"}:
            continue
        if allowed is not None and severity not in allowed:
            continue
        filtered.append(issue)

    lines = []
    preview_count = min(len(filtered), max(0, int(limit or 0)))
    for issue in filtered[:preview_count]:
        lines.append(f"- {_validation_issue_brief(issue)}")

    remaining = len(filtered) - preview_count
    if remaining > 0:
        lines.append(f"- e mais {remaining} pendencia(s).")
    return lines


def _write_csv_tables(output_dir, base_name, tables, delimiter=";"):
    os.makedirs(output_dir, exist_ok=True)
    exported_files = []
    for table in tables or []:
        file_name = f"{base_name}_{table['slug']}.csv"
        file_path = os.path.join(output_dir, file_name)
        with open(file_path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL)
            writer.writerows(table["rows"])
        exported_files.append(
            {
                "label": table["label"],
                "path": file_path,
                "row_count": table["row_count"],
            }
        )
    return exported_files


def _select_output_directory():
    """Abre dialog com opções de preview antes de escolher pasta de destino.
    Retorna a pasta escolhida, ou "" se cancelado.
    """
    start_dir = os.path.expanduser("~")
    if App is not None and App.ActiveDocument is not None and getattr(App.ActiveDocument, "FileName", ""):
        start_dir = os.path.dirname(App.ActiveDocument.FileName) or start_dir

    dialog = _ExportPreviewDialog(start_dir, _main_window())
    if dialog.exec_() != QtWidgets.QDialog.Accepted:
        return ""
    return dialog.chosen_dir


class _ExportPreviewDialog(QtWidgets.QDialog):
    """Dialog de confirmação de exportação com botões de prévia."""

    def __init__(self, start_dir, parent=None):
        super().__init__(parent)
        self.chosen_dir = ""
        self._start_dir = start_dir
        self.setWindowTitle("PanelNest: Exportar Arquivos")
        self.setMinimumWidth(400)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)

        lbl = QtWidgets.QLabel("Prévia antes de exportar:")
        lbl.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl)

        preview_group = QtWidgets.QGroupBox("Abrir no navegador")
        preview_layout = QtWidgets.QVBoxLayout(preview_group)

        btn_labels = QtWidgets.QPushButton("Prévia de Etiquetas")
        btn_labels.setToolTip("Abre as etiquetas com QR code no navegador para conferir antes de exportar.")
        btn_labels.clicked.connect(self._preview_labels)
        preview_layout.addWidget(btn_labels)

        btn_layout = QtWidgets.QPushButton("Prévia do Plano de Corte (SVG)")
        btn_layout.setToolTip("Abre o plano de corte visual com as chapas e peças posicionadas.")
        btn_layout.clicked.connect(self._preview_layout)
        preview_layout.addWidget(btn_layout)

        btn_cut_sheet = QtWidgets.QPushButton("Plano de Corte A4 (imprimir)")
        btn_cut_sheet.setToolTip("Abre o plano de corte A4 formatado para impressão, com sequência numerada de cortes.")
        btn_cut_sheet.clicked.connect(self._preview_cut_sheet)
        preview_layout.addWidget(btn_cut_sheet)

        btn_report = QtWidgets.QPushButton("Prévia do Relatório Completo")
        btn_report.setToolTip("Abre o relatório HTML completo com tabelas e estatísticas.")
        btn_report.clicked.connect(self._preview_report)
        preview_layout.addWidget(btn_report)

        layout.addWidget(preview_group)

        quick_export_group = QtWidgets.QGroupBox("Exportação rápida")
        quick_export_layout = QtWidgets.QVBoxLayout(quick_export_group)

        btn_dxf = QtWidgets.QPushButton("Exportar somente DXF…")
        btn_dxf.setToolTip(
            "Exporta o layout em DXF para CAD/CAM, mantendo os contornos reais das peças."
        )
        btn_dxf.clicked.connect(self._export_dxf_only)
        quick_export_layout.addWidget(btn_dxf)

        btn_svg = QtWidgets.QPushButton("Exportar somente SVG…")
        btn_svg.setToolTip(
            "Exporta um SVG vetorial por chapa, útil para Blender, Inkscape e corte 2D."
        )
        btn_svg.clicked.connect(self._export_svg_only)
        quick_export_layout.addWidget(btn_svg)

        layout.addWidget(quick_export_group)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setFrameShadow(QtWidgets.QFrame.Sunken)
        layout.addWidget(sep)

        remnants_group = QtWidgets.QGroupBox("Histórico de retalhos")
        remnants_layout = QtWidgets.QVBoxLayout(remnants_group)
        remnants_info = QtWidgets.QLabel(
            "Quando o corte estiver definido, salve os retalhos gerados no histórico.\n"
            "Eles ficarão disponíveis em projetos futuros via 'Configurar Chapa → Retalhos → Carregar do Histórico'."
        )
        remnants_info.setWordWrap(True)
        remnants_info.setStyleSheet("color: #555; font-size: 10px;")
        remnants_layout.addWidget(remnants_info)
        btn_save_remnants = QtWidgets.QPushButton("💾 Salvar retalhos no histórico")
        btn_save_remnants.setToolTip(
            "Salva os retalhos gerados pelo layout atual no banco persistente.\n"
            "Faça isso apenas quando o corte estiver definido — não a cada teste."
        )
        btn_save_remnants.clicked.connect(self._save_remnants_to_history)
        remnants_layout.addWidget(btn_save_remnants)
        layout.addWidget(remnants_group)

        sep2 = QtWidgets.QFrame()
        sep2.setFrameShape(QtWidgets.QFrame.HLine)
        sep2.setFrameShadow(QtWidgets.QFrame.Sunken)
        layout.addWidget(sep2)

        btn_export = QtWidgets.QPushButton("Escolher Pasta e Exportar…")
        btn_export.setStyleSheet("font-weight: bold; padding: 6px;")
        btn_export.clicked.connect(self._choose_and_accept)
        layout.addWidget(btn_export)

        btn_cancel = QtWidgets.QPushButton("Cancelar")
        btn_cancel.clicked.connect(self.reject)
        layout.addWidget(btn_cancel)

    def _build_labels_html(self):
        import json, shutil, subprocess, tempfile
        from pathlib import Path
        document = App.ActiveDocument if App is not None else None
        try:
            import panelnest
            parts = panelnest.collect_parts(include_hidden=True)
            records = panelnest.collect_part_label_records(parts=parts, document=document) or []
        except Exception as exc:
            raise RuntimeError(f"Erro ao coletar peças: {exc}")
        doc_label = str(getattr(document, "Label", "") or "") if document else ""
        doc_name = str(getattr(document, "Name", "") or "") if document else ""
        try:
            html = _build_labels_html_with_external_python(doc_label, doc_name, records)
            return html
        except Exception:
            import panelnest as _pn
            return _pn.build_panelnest_labels_html(document=document, records=records)

    def _preview_labels(self):
        try:
            html_text = self._build_labels_html()
            _write_and_open_preview(html_text, "_etiquetas.html")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "PanelNest", f"Erro ao gerar prévia:\n{exc}")

    def _preview_layout(self):
        try:
            import panelnest
            document = App.ActiveDocument if App is not None else None
            html_text = panelnest.build_panelnest_layout_preview_html(document=document)
            _write_and_open_preview(html_text, "_plano_corte.html")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "PanelNest", f"Erro ao gerar prévia:\n{exc}")

    def _preview_cut_sheet(self):
        try:
            import panelnest
            document = App.ActiveDocument if App is not None else None
            html_text = panelnest.build_panelnest_cut_sheet_html(document=document)
            _write_and_open_preview(html_text, "_plano_corte_a4.html")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "PanelNest", f"Erro ao gerar prévia:\n{exc}")

    def _preview_report(self):
        try:
            import panelnest
            document = App.ActiveDocument if App is not None else None
            html_text = panelnest.build_panelnest_report_html(document=document)
            _write_and_open_preview(html_text, "_relatorio.html")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "PanelNest", f"Erro ao gerar prévia:\n{exc}")

    def _save_remnants_to_history(self):
        import panelnest
        try:
            document = App.ActiveDocument if App is not None else None
            parts = panelnest.collect_parts(include_hidden=True)
            settings = panelnest.get_sheet_settings()
            layout_sheets = panelnest.create_layout_sheets(parts, settings=settings)
            panelnest.save_layout_remnants_to_db(layout_sheets, settings)
            count = panelnest.count_available_remnants(panelnest.open_remnant_db())
            QtWidgets.QMessageBox.information(
                self,
                "Retalhos salvos",
                f"Retalhos do layout atual salvos no histórico.\n"
                f"Total disponível no banco: {count} retalho(s).\n\n"
                "Acesse 'Configurar Chapa → Retalhos → Carregar do Histórico' em qualquer projeto futuro.",
            )
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Erro", f"Não foi possível salvar os retalhos:\n{exc}")

    def _choose_quick_export_directory(self, title):
        return QtWidgets.QFileDialog.getExistingDirectory(
            self,
            title,
            self._start_dir,
        )

    def _export_dxf_only(self):
        chosen = self._choose_quick_export_directory("PanelNest: Exportar DXF")
        if not chosen:
            return
        output_dir = os.path.join(str(chosen), "dxf")
        try:
            import panelnest

            document = App.ActiveDocument if App is not None else None
            result = panelnest.export_panelnest_dxf_package(
                output_dir,
                document=document,
                settings=panelnest.get_sheet_settings(),
            )
            file_count = len(result.get("files") or [])
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self,
                "PanelNest: Exportar DXF",
                f"Não foi possível exportar o DXF:\n{exc}",
            )
            return

        QtWidgets.QMessageBox.information(
            self,
            "PanelNest: DXF Exportado",
            f"{file_count} arquivo(s) DXF exportado(s).\n\nPasta:\n{_native_path(output_dir)}",
        )

    def _export_svg_only(self):
        chosen = self._choose_quick_export_directory("PanelNest: Exportar SVG")
        if not chosen:
            return
        output_dir = os.path.join(str(chosen), "svg")
        try:
            import panelnest

            document = App.ActiveDocument if App is not None else None
            result = panelnest.export_panelnest_svg_package(
                output_dir,
                document=document,
                settings=panelnest.get_sheet_settings(),
            )
            file_count = len(result.get("files") or [])
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self,
                "PanelNest: Exportar SVG",
                f"Não foi possível exportar o SVG:\n{exc}",
            )
            return

        QtWidgets.QMessageBox.information(
            self,
            "PanelNest: SVG Exportado",
            f"{file_count} arquivo(s) SVG exportado(s).\n\nPasta:\n{_native_path(output_dir)}",
        )

    def _choose_and_accept(self):
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "PanelNest: Escolher Pasta de Destino", self._start_dir
        )
        if chosen:
            self.chosen_dir = chosen
            self.accept()


def _set_printer_a4(printer, landscape=True):
    if hasattr(QtGui, "QPageLayout") and hasattr(QtGui, "QPageSize"):
        try:
            printer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A4))
        except Exception:
            pass
        try:
            orientation = (
                QtGui.QPageLayout.Landscape if landscape else QtGui.QPageLayout.Portrait
            )
            printer.setPageOrientation(orientation)
            return
        except Exception:
            pass

    if hasattr(printer, "setPageSize"):
        try:
            printer.setPageSize(QtPrintSupport.QPrinter.A4)
        except Exception:
            pass
    elif hasattr(printer, "setPaperSize"):
        try:
            printer.setPaperSize(QtPrintSupport.QPrinter.A4)
        except Exception:
            pass

    if hasattr(printer, "setOrientation"):
        try:
            orientation = (
                QtPrintSupport.QPrinter.Landscape
                if landscape
                else QtPrintSupport.QPrinter.Portrait
            )
            printer.setOrientation(orientation)
        except Exception:
            pass


def _write_pdf_document(html_text, output_path, landscape=True):
    document = QtGui.QTextDocument()
    if hasattr(document, "setDocumentMargin"):
        document.setDocumentMargin(18)
    document.setHtml(html_text)

    printer = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.HighResolution)
    if hasattr(printer, "setOutputFormat"):
        printer.setOutputFormat(QtPrintSupport.QPrinter.PdfFormat)
    printer.setOutputFileName(output_path)
    if hasattr(printer, "setFullPage"):
        try:
            printer.setFullPage(False)
        except Exception:
            pass
    _set_printer_a4(printer, landscape=landscape)

    if hasattr(document, "print_"):
        document.print_(printer)
    else:
        document.print(printer)


def _chrome_pdf_command(output_path, html_path, landscape=True, virtual_time_budget=2000):
    browser = (
        shutil.which("google-chrome")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
    )
    if not browser:
        return None
    command = [
        browser,
        "--headless",
        "--disable-gpu",
        "--no-sandbox",
        "--no-pdf-header-footer",
        "--run-all-compositor-stages-before-draw",
        f"--virtual-time-budget={max(0, int(virtual_time_budget or 0))}",
        f"--print-to-pdf={output_path}",
        Path(html_path).resolve().as_uri(),
    ]
    if landscape:
        command.insert(4, "--landscape")
    return command


def _write_pdf_document_file_with_chrome(
    html_path,
    output_path,
    landscape=True,
    virtual_time_budget=2000,
):
    command = _chrome_pdf_command(
        output_path,
        html_path,
        landscape=landscape,
        virtual_time_budget=virtual_time_budget,
    )
    if not command:
        raise RuntimeError("google-chrome nao esta disponivel para impressao headless")

    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        stderr_text = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(stderr_text or f"saida {completed.returncode} ao chamar o navegador")


def _write_pdf_document_with_chrome(html_text, output_path, landscape=True):
    command = None
    html_path = ""
    with tempfile.NamedTemporaryFile("w", suffix=".html", encoding="utf-8", delete=False) as handle:
        handle.write(html_text)
        html_path = handle.name

    try:
        _write_pdf_document_file_with_chrome(
            html_path,
            output_path,
            landscape=landscape,
        )
    finally:
        if html_path and os.path.exists(html_path):
            try:
                os.remove(html_path)
            except OSError:
                pass


def _merge_pdf_files(input_paths, output_path):
    input_paths = [str(path) for path in input_paths if str(path).strip()]
    if not input_paths:
        raise RuntimeError("nenhum PDF intermediario foi gerado para a uniao final")
    if len(input_paths) == 1:
        shutil.copyfile(input_paths[0], output_path)
        return

    pdfunite = shutil.which("pdfunite")
    if not pdfunite:
        raise RuntimeError("pdfunite nao esta disponivel para unir os PDFs do guia")

    completed = subprocess.run(
        [pdfunite] + input_paths + [output_path],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        stderr_text = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(stderr_text or f"saida {completed.returncode} ao unir os PDFs")


def _write_pdf_batch_from_html_paths_with_chrome(
    html_paths,
    output_path,
    landscape=True,
    virtual_time_budget=4000,
):
    html_paths = [str(path) for path in html_paths if str(path).strip()]
    if not html_paths:
        raise RuntimeError("nao ha paginas HTML individuais para montar o PDF")

    temp_pdf_paths = []
    try:
        for index, html_path in enumerate(html_paths, start=1):
            with tempfile.NamedTemporaryFile(
                suffix=f"_{index:03d}.pdf",
                delete=False,
            ) as handle:
                temp_pdf_path = handle.name
            temp_pdf_paths.append(temp_pdf_path)
            _write_pdf_document_file_with_chrome(
                html_path,
                temp_pdf_path,
                landscape=landscape,
                virtual_time_budget=virtual_time_budget,
            )
            if not (os.path.exists(temp_pdf_path) and os.path.getsize(temp_pdf_path) > 0):
                raise RuntimeError(
                    "o navegador nao gerou um PDF valido para '%s'"
                    % _native_path(html_path)
                )

        _merge_pdf_files(temp_pdf_paths, output_path)
    finally:
        for temp_pdf_path in temp_pdf_paths:
            if temp_pdf_path and os.path.exists(temp_pdf_path):
                try:
                    os.remove(temp_pdf_path)
                except OSError:
                    pass


def _write_pdf_with_fallback(html_text, pdf_path, html_path, landscape=True, prefer_chrome=False):
    pdf_created = False
    pdf_error = ""
    strategies = []
    if prefer_chrome:
        strategies = [
            ("Chrome", _write_pdf_document_with_chrome),
            ("Qt", _write_pdf_document),
        ]
    else:
        strategies = [
            ("Qt", _write_pdf_document),
            ("Chrome", _write_pdf_document_with_chrome),
        ]

    for strategy_name, strategy in strategies:
        try:
            strategy(html_text, pdf_path, landscape=landscape)
            pdf_created = os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0
            if pdf_created:
                pdf_error = ""
                break
        except Exception as exc:
            if pdf_error:
                pdf_error = f"{pdf_error}; fallback {strategy_name}: {exc}"
            else:
                pdf_error = f"fallback {strategy_name}: {exc}"

    if not pdf_created:
        with open(html_path, "w", encoding="utf-8") as handle:
            handle.write(html_text)

    return pdf_created, pdf_error


def _write_assembly_guide_pdf_with_fallback(
    html_text,
    pdf_path,
    html_path,
    page_html_paths,
    landscape=False,
):
    errors = []

    try:
        _write_pdf_batch_from_html_paths_with_chrome(
            page_html_paths,
            pdf_path,
            landscape=landscape,
            virtual_time_budget=4500,
        )
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
            return True, ""
    except Exception as exc:
        errors.append(f"fallback Chrome por pagina: {exc}")

    pdf_created, pdf_error = _write_pdf_with_fallback(
        html_text,
        pdf_path,
        html_path,
        landscape=landscape,
        prefer_chrome=True,
    )
    if pdf_created:
        return True, ""
    if pdf_error:
        errors.append(pdf_error)
    return False, "; ".join(errors)


def _external_python_env():
    env = dict(os.environ)
    for key in (
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONEXECUTABLE",
        "__PYVENV_LAUNCHER__",
    ):
        env.pop(key, None)
    return env


def _summarize_external_python_error(stderr_text):
    lines = [line.strip() for line in str(stderr_text or "").splitlines() if line.strip()]
    if not lines:
        return "python externo nao inicializou corretamente"
    for prefix in (
        "json.decoder.JSONDecodeError:",
        "ModuleNotFoundError:",
        "Fatal Python error:",
        "ValueError:",
        "RuntimeError:",
        "Traceback",
    ):
        for line in reversed(lines):
            if line.startswith(prefix):
                return line
    return lines[-1]


def _build_labels_html_with_external_python(document_label, document_name, records):
    repo_root = Path(__file__).resolve().parent.parent
    python_candidates = []
    for candidate in (
        shutil.which("python3"),
        "/usr/bin/python3",
        "python3",
    ):
        candidate = str(candidate or "").strip()
        if not candidate or candidate in python_candidates:
            continue
        if candidate == "python3" or os.path.exists(candidate):
            python_candidates.append(candidate)

    script = """
import json
import sys
from pathlib import Path

repo_root = Path(sys.argv[3]).resolve()
repo_root_str = str(repo_root)
if repo_root_str not in sys.path:
    sys.path.insert(0, repo_root_str)

import panelnest

class DummyDocument:
    pass

document = DummyDocument()
document.Label = sys.argv[1]
document.Name = sys.argv[2]
records = json.load(sys.stdin)
html = panelnest.build_panelnest_labels_html(document=document, records=records)
sys.stdout.write(html)
""".strip()

    payload = json.dumps(records, ensure_ascii=False)
    last_error = ""
    for python_executable in python_candidates:
        try:
            completed = subprocess.run(
                [
                    python_executable,
                    "-c",
                    script,
                    document_label or "",
                    document_name or "",
                    str(repo_root),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                input=payload,
                text=True,
                cwd=str(repo_root),
                env=_external_python_env(),
            )
            html_text = (completed.stdout or "").strip()
            if (
                completed.returncode == 0
                and html_text
                and "data:image/png;base64," in html_text
                and "label-qr" in html_text
            ):
                return html_text
            last_error = _summarize_external_python_error(
                completed.stderr or completed.stdout or ""
            )
        except Exception as exc:
            last_error = str(exc)
    raise RuntimeError(last_error or "nao foi possivel montar o HTML das etiquetas via python externo")


def _detect_local_host_for_qr():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            host = probe.getsockname()[0]
            if host and not host.startswith("127."):
                return host
    except Exception:
        pass

    try:
        for host in socket.gethostbyname_ex(socket.gethostname())[2]:
            if host and not host.startswith("127."):
                return host
    except Exception:
        pass

    return "127.0.0.1"


def _compose_guide_url(base_url, file_name):
    normalized_base = str(base_url or "").strip().rstrip("/")
    normalized_file_name = os.path.basename(str(file_name or "").strip())
    if not normalized_base or not normalized_file_name:
        return ""
    return f"{normalized_base}/{normalized_file_name}"


def _detect_wrangler_command():
    wrangler_binary = shutil.which("wrangler")
    if wrangler_binary:
        return [wrangler_binary]

    npx_binary = shutil.which("npx")
    if npx_binary:
        return [npx_binary, "--yes", "wrangler"]
    return []


def _strip_ansi_sequences(text):
    return _ANSI_ESCAPE_RE.sub("", str(text or ""))


def _summarize_cloudflare_publish_error(output_text):
    cleaned_output = _strip_ansi_sequences(output_text)
    lines = [line.strip() for line in cleaned_output.splitlines() if line.strip()]
    if not lines:
        return "nao foi possivel publicar no Cloudflare Pages"

    for pattern in (
        "not logged in",
        "authentication",
        "api token",
        "account id",
        "must login",
        "project",
        "not found",
        "unknown argument",
        "command not found",
    ):
        for line in reversed(lines):
            if pattern.lower() in line.lower():
                return line
    return lines[-1]


def _parse_cloudflare_api_error(status_code, response_text):
    cleaned_text = str(response_text or "").strip()
    try:
        payload = json.loads(cleaned_text or "{}")
    except Exception:
        payload = {}

    errors = payload.get("errors") or []
    if errors:
        first_error = errors[0]
        error_message = str(first_error.get("message", "") or "").strip()
        error_code = first_error.get("code")
        if error_message and error_code not in (None, ""):
            return f"{error_message} (status: {status_code}) [code: {error_code}]"
        if error_message:
            return f"{error_message} (status: {status_code})"
    if cleaned_text:
        return f"{cleaned_text} (status: {status_code})"
    return f"falha na API da Cloudflare (status: {status_code})"


def _cloudflare_api_request(account_id, api_token, method, path, payload=None):
    request_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}{path}"
    request_headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    request_data = None
    if payload is not None:
        request_data = json.dumps(payload, ensure_ascii=True).encode("utf-8")

    request = urllib.request.Request(
        request_url,
        data=request_data,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response_body = response.read().decode("utf-8", "replace")
            parsed_response = json.loads(response_body or "{}")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(_parse_cloudflare_api_error(exc.code, error_body))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"nao foi possivel conectar a API da Cloudflare ({exc.reason})")

    if not parsed_response.get("success", False):
        raise RuntimeError(
            _parse_cloudflare_api_error(200, json.dumps(parsed_response, ensure_ascii=False))
        )
    return parsed_response


def _ensure_cloudflare_pages_project(project_name, account_id, api_token):
    normalized_project_name = str(project_name or "").strip()
    encoded_project_name = urllib.parse.quote(normalized_project_name, safe="")
    try:
        response_payload = _cloudflare_api_request(
            account_id,
            api_token,
            "GET",
            f"/pages/projects/{encoded_project_name}",
        )
        result = response_payload.get("result") or {}
        return {
            "status": "exists",
            "subdomain": str(result.get("subdomain") or "").strip(),
        }
    except RuntimeError as exc:
        error_message = str(exc)
        if "status: 404" not in error_message and "[code: 8000007]" not in error_message:
            raise

    create_payload = _cloudflare_api_request(
        account_id,
        api_token,
        "POST",
        "/pages/projects",
        payload={
            "name": normalized_project_name,
            "production_branch": "production",
        },
    )
    result = create_payload.get("result") or {}
    return {
        "status": "created",
        "subdomain": str(result.get("subdomain") or "").strip(),
    }


def _get_cloudflare_pages_project_info(project_name, account_id="", api_token=""):
    normalized_project_name = str(project_name or "").strip()
    resolved_api_token = str(api_token or "").strip()
    resolved_account_id = str(account_id or "").strip()
    if not normalized_project_name or not resolved_account_id or not resolved_api_token:
        return {}

    encoded_project_name = urllib.parse.quote(normalized_project_name, safe="")
    try:
        response_payload = _cloudflare_api_request(
            resolved_account_id,
            resolved_api_token,
            "GET",
            f"/pages/projects/{encoded_project_name}",
        )
    except RuntimeError as exc:
        error_message = str(exc)
        if "status: 404" in error_message or "[code: 8000007]" in error_message:
            return {}
        raise

    result = response_payload.get("result") or {}
    return {
        "name": normalized_project_name,
        "subdomain": str(result.get("subdomain") or "").strip(),
    }


def _cloudflare_project_public_base_url(project_info):
    subdomain = str((project_info or {}).get("subdomain") or "").strip()
    if not subdomain:
        return ""
    if subdomain.startswith("http://") or subdomain.startswith("https://"):
        return subdomain.rstrip("/")
    return f"https://{subdomain}"


def _extract_html_title(html_text):
    match = re.search(r"<title[^>]*>(.*?)</title>", str(html_text or ""), re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _verify_public_guide_url(public_url):
    expected_marker = "Guia de Montagem PanelNest"
    last_error = ""
    for _attempt in range(6):
        try:
            request = urllib.request.Request(
                public_url,
                headers={"User-Agent": "PanelNest/1.0"},
                method="GET",
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                response_body = response.read().decode("utf-8", "replace")
            if expected_marker in response_body:
                return
            page_title = _extract_html_title(response_body)
            if page_title:
                last_error = (
                    "a URL publica respondeu com outro site ('%s') em vez do guia PanelNest"
                    % page_title
                )
            else:
                last_error = "a URL publica respondeu, mas nao entregou o HTML do guia PanelNest"
        except urllib.error.HTTPError as exc:
            last_error = f"a URL publica respondeu com HTTP {exc.code}"
        except urllib.error.URLError as exc:
            last_error = f"nao foi possivel validar a URL publica ({exc.reason})"
        time.sleep(1.0)

    raise RuntimeError(last_error or "a URL publica nao entregou o guia PanelNest apos a publicacao")


def _resolve_cloud_public_base_url(settings, cloudflare_publish_prefs, cloudflare_project_name):
    import panelnest

    explicit_public_base_url = panelnest.normalize_assembly_guide_public_base_url(
        getattr(settings, "assembly_guide_public_base_url", "")
    )
    explicit_host = ""
    if explicit_public_base_url:
        try:
            explicit_host = urllib.parse.urlparse(explicit_public_base_url).netloc.strip().lower()
        except Exception:
            explicit_host = ""

    if explicit_public_base_url and explicit_host and not explicit_host.endswith(".pages.dev"):
        return explicit_public_base_url

    project_info = _get_cloudflare_pages_project_info(
        cloudflare_project_name,
        account_id=cloudflare_publish_prefs.get("account_id", ""),
        api_token=cloudflare_publish_prefs.get("api_token", ""),
    )
    detected_public_base_url = _cloudflare_project_public_base_url(project_info)
    if detected_public_base_url:
        return detected_public_base_url

    if explicit_public_base_url:
        return explicit_public_base_url

    return panelnest.resolve_assembly_guide_public_base_url(settings)


def _publish_directory_to_cloudflare_pages(directory, project_name, account_id="", api_token=""):
    command_prefix = _detect_wrangler_command()
    if not command_prefix:
        raise RuntimeError(
            "Wrangler nao encontrado. Instale Node.js nesta maquina para habilitar a publicacao automatica."
        )

    normalized_directory = os.path.realpath(directory)
    normalized_project_name = str(project_name or "").strip()
    if not normalized_project_name:
        raise RuntimeError("O nome do projeto Cloudflare Pages nao foi configurado.")

    env = dict(os.environ)
    resolved_api_token = str(api_token or env.get("CLOUDFLARE_API_TOKEN", "")).strip()
    resolved_account_id = str(account_id or env.get("CLOUDFLARE_ACCOUNT_ID", "")).strip()
    if not resolved_api_token:
        raise RuntimeError(
            "Configure o Cloudflare API Token na aba Montagem ou na variavel CLOUDFLARE_API_TOKEN."
        )
    if not resolved_account_id:
        raise RuntimeError(
            "Configure o Cloudflare Account ID na aba Montagem ou na variavel CLOUDFLARE_ACCOUNT_ID."
        )
    env["CLOUDFLARE_API_TOKEN"] = resolved_api_token
    env["CLOUDFLARE_ACCOUNT_ID"] = resolved_account_id
    project_info = _ensure_cloudflare_pages_project(
        normalized_project_name,
        resolved_account_id,
        resolved_api_token,
    )

    command = list(command_prefix) + [
        "pages",
        "deploy",
        normalized_directory,
        "--project-name",
        normalized_project_name,
    ]
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=normalized_directory,
        env=env,
    )
    combined_output = "\n".join(
        [chunk for chunk in ((completed.stdout or "").strip(), (completed.stderr or "").strip()) if chunk]
    )
    if completed.returncode != 0:
        raise RuntimeError(_summarize_cloudflare_publish_error(combined_output))
    cleaned_output = _strip_ansi_sequences(combined_output)
    if project_info.get("status") == "created":
        cleaned_output = (
            "Projeto Cloudflare Pages criado automaticamente.\n" + cleaned_output
            if cleaned_output
            else "Projeto Cloudflare Pages criado automaticamente."
        )
    return {
        "log": cleaned_output,
        "project_info": project_info,
    }


def _stop_guide_server():
    server = _GUIDE_SERVER_STATE.get("server")
    thread = _GUIDE_SERVER_STATE.get("thread")
    if server is not None:
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
    if thread is not None and thread.is_alive():
        try:
            thread.join(timeout=1.5)
        except Exception:
            pass
    _GUIDE_SERVER_STATE.update(
        {
            "server": None,
            "thread": None,
            "directory": "",
            "base_url": "",
            "log_path": "",
        }
    )


def _ensure_guide_server(directory):
    target_directory = os.path.realpath(directory)
    current_server = _GUIDE_SERVER_STATE.get("server")
    current_thread = _GUIDE_SERVER_STATE.get("thread")
    if (
        current_server is not None
        and current_thread is not None
        and current_thread.is_alive()
        and _GUIDE_SERVER_STATE.get("directory") == target_directory
        and _GUIDE_SERVER_STATE.get("base_url")
    ):
        return _GUIDE_SERVER_STATE["base_url"]

    _stop_guide_server()

    handler_factory = partial(_SilentGuideRequestHandler, directory=target_directory)
    last_error = ""
    server = None
    for port in _GUIDE_SERVER_PORT_CANDIDATES:
        try:
            server = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler_factory)
            break
        except OSError as exc:
            last_error = str(exc)
    if server is None:
        raise RuntimeError(last_error or "nao foi possivel iniciar o servidor local do guia")

    thread = threading.Thread(
        target=server.serve_forever,
        name="PanelNestGuideServer",
        daemon=True,
    )
    thread.start()

    host = _detect_local_host_for_qr()
    port = int(server.server_address[1])
    base_url = f"http://{host}:{port}"
    log_path = os.path.join(target_directory, ".panelnest_guide_server.log")
    try:
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write("PanelNest guide server log\n")
    except Exception:
        log_path = ""
    _GUIDE_SERVER_STATE.update(
        {
            "server": server,
            "thread": thread,
            "directory": target_directory,
            "base_url": base_url,
            "log_path": log_path,
        }
    )
    return base_url


class ExportReportsCommand:
    def Activated(self):
        import panelnest

        output_dir = _select_output_directory()
        if not output_dir:
            return

        try:
            document = App.ActiveDocument if App is not None else None
            prepared = panelnest.prepare_panelnest_export_tables(document=document)
            settings = prepared.get("settings")
            parts = prepared.get("parts") or panelnest.collect_parts(include_hidden=True)
            validation_issues = prepared.get("validation_issues") or []
            validation_counts = _severity_counts(validation_issues)
            validation_summary = _validation_summary_line(validation_counts)
            if validation_counts.get("Erro", 0):
                error_preview_lines = _validation_issue_preview_lines(
                    validation_issues,
                    severities=("Erro",),
                    limit=4,
                )
                message = (
                    "PanelNest: exportacao interrompida. "
                    f"{validation_summary}"
                )
                if App is not None:
                    if hasattr(App.Console, "PrintError"):
                        App.Console.PrintError(message + "\n")
                    else:
                        App.Console.PrintMessage(message + "\n")
                    if error_preview_lines:
                        for line in error_preview_lines:
                            App.Console.PrintError(f"PanelNest: {line}\n")
                _show_error_dialog(
                    "Exportacao interrompida.\n\n"
                    f"{validation_summary}\n\n"
                    + (
                        "Erros principais:\n"
                        + "\n".join(error_preview_lines)
                        + "\n\n"
                        if error_preview_lines
                        else ""
                    )
                    +
                    "Corrija as pendencias na planilha 'Validacao PanelNest' antes de exportar."
                )
                return
            if App is not None and (
                validation_counts.get("Alerta", 0) or validation_counts.get("Info", 0)
            ):
                pending_preview_lines = _validation_issue_preview_lines(
                    validation_issues,
                    severities=("Alerta", "Info"),
                    limit=4,
                )
                for line in pending_preview_lines:
                    App.Console.PrintWarning("PanelNest: %s\n" % line)

            base_name = panelnest.get_panelnest_export_base_name(document=document)
            _dir_csv_early = os.path.join(output_dir, "csv")
            os.makedirs(_dir_csv_early, exist_ok=True)
            csv_files = _write_csv_tables(
                _dir_csv_early,
                base_name,
                prepared.get("tables") or [],
            )
            export_result = {
                "base_name": base_name,
                "files": csv_files,
                "parts": parts,
                "settings": settings,
                "layout_sheets": prepared.get("layout_sheets"),
                "validation_issues": validation_issues,
                "tables": prepared.get("tables") or [],
            }
            labels_records = panelnest.collect_part_label_records(parts=parts)
            document_label = getattr(document, "Label", "") if document is not None else ""
            document_name = getattr(document, "Name", "") if document is not None else ""
            html_text = panelnest.build_panelnest_report_html(tables=export_result["tables"])
        except ValueError as exc:
            if App is not None:
                App.Console.PrintError(f"PanelNest: {exc}\n")
            _show_error_dialog(str(exc))
            return

        base_name = export_result["base_name"]

        # Subpastas organizadas por tipo
        dir_relatorios = os.path.join(output_dir, "relatorios")
        dir_dxf = os.path.join(output_dir, "dxf")
        dir_cnc = os.path.join(output_dir, "cnc")
        dir_csv = os.path.join(output_dir, "csv")
        for _d in (dir_relatorios, dir_dxf, dir_cnc, dir_csv):
            os.makedirs(_d, exist_ok=True)

        csv_files = export_result["files"]
        pdf_path = os.path.join(dir_relatorios, f"{base_name}_relatorio_panelnest.pdf")
        html_path = os.path.join(dir_relatorios, f"{base_name}_relatorio_panelnest.html")
        labels_pdf_path = os.path.join(dir_relatorios, f"{base_name}_etiquetas_panelnest.pdf")
        labels_html_path = os.path.join(dir_relatorios, f"{base_name}_etiquetas_panelnest.html")
        guide_pdf_path = os.path.join(dir_relatorios, f"{base_name}_guia_montagem_panelnest.pdf")
        guide_html_path = os.path.join(dir_relatorios, f"{base_name}_guia_montagem_panelnest.html")
        cut_sheet_html_path = os.path.join(dir_relatorios, f"{base_name}_plano_corte_panelnest.html")
        dxf_files = []
        dxf_variants = []
        dxf_error = ""
        nbm_files = []
        nbm_error = ""
        guide_pdf_error = ""
        guide_server_error = ""
        guide_cloud_publish_error = ""
        guide_cloud_publish_log = ""
        guide_public_url = ""
        guide_scan_base_url = ""
        guide_delivery_mode = ""
        guide_page_files = {}
        guide_page_paths = []
        cloudflare_project_name = panelnest.normalize_cloudflare_pages_project_name(
            getattr(settings, "assembly_guide_cloudflare_project_name", "")
        )
        cloudflare_publish_prefs = panelnest.get_cloudflare_publish_preferences()
        cloudflare_auto_publish_enabled = bool(
            getattr(settings, "assembly_guide_cloudflare_auto_publish", False)
            and cloudflare_project_name
        )

        preview_map = panelnest.capture_panelnest_assembly_preview_map(
            document=document,
            records=labels_records,
            parts=parts,
        )
        guide_html_text = panelnest.build_panelnest_assembly_guide_html(
            document=document,
            records=labels_records,
            preview_map=preview_map,
            parts=parts,
        )
        with open(guide_html_path, "w", encoding="utf-8") as handle:
            handle.write(guide_html_text)

        for record in labels_records:
            anchor = panelnest._assembly_record_anchor(record)
            page_name = f"{base_name}_guia_montagem_{anchor}.html"
            page_path = os.path.join(dir_relatorios, page_name)
            page_html_text = panelnest.build_panelnest_assembly_guide_html(
                document=document,
                records=[record],
                preview_map=preview_map,
                parts=parts,
            )
            with open(page_path, "w", encoding="utf-8") as handle:
                handle.write(page_html_text)
            guide_page_files[panelnest._assembly_record_key(record)] = page_name
            guide_page_paths.append(page_path)

        resolved_public_base_url = _resolve_cloud_public_base_url(
            settings,
            cloudflare_publish_prefs,
            cloudflare_project_name,
        )
        prefer_public_delivery = bool(resolved_public_base_url or cloudflare_auto_publish_enabled)
        if prefer_public_delivery:
            guide_delivery_mode = "public"
            if resolved_public_base_url:
                guide_scan_base_url = resolved_public_base_url
                guide_public_url = _compose_guide_url(
                    resolved_public_base_url,
                    "relatorios/" + os.path.basename(guide_html_path),
                )
            if cloudflare_auto_publish_enabled:
                try:
                    publish_result = _publish_directory_to_cloudflare_pages(
                        output_dir,
                        cloudflare_project_name,
                        account_id=cloudflare_publish_prefs.get("account_id", ""),
                        api_token=cloudflare_publish_prefs.get("api_token", ""),
                    )
                    guide_cloud_publish_log = str(publish_result.get("log") or "").strip()
                    refreshed_project_info = _get_cloudflare_pages_project_info(
                        cloudflare_project_name,
                        account_id=cloudflare_publish_prefs.get("account_id", ""),
                        api_token=cloudflare_publish_prefs.get("api_token", ""),
                    )
                    published_public_base_url = _cloudflare_project_public_base_url(
                        refreshed_project_info or publish_result.get("project_info") or {}
                    )
                    if published_public_base_url:
                        guide_scan_base_url = published_public_base_url
                        guide_public_url = _compose_guide_url(
                            published_public_base_url,
                            "relatorios/" + os.path.basename(guide_html_path),
                        )
                    if not guide_public_url:
                        raise RuntimeError(
                            "nao foi possivel determinar a URL publica real do projeto Cloudflare apos a publicacao"
                        )
                    _verify_public_guide_url(guide_public_url)
                except Exception as exc:
                    guide_cloud_publish_error = str(exc)
                    guide_scan_base_url = ""
                    guide_public_url = ""
                    guide_delivery_mode = ""
            elif not guide_public_url:
                prefer_public_delivery = False

        if not prefer_public_delivery or not guide_public_url:
            try:
                guide_scan_base_url = _ensure_guide_server(output_dir)
                guide_public_url = _compose_guide_url(
                    guide_scan_base_url,
                    "relatorios/" + os.path.basename(guide_html_path),
                )
                guide_delivery_mode = "local"
            except Exception as exc:
                guide_server_error = str(exc)

        label_records_for_export = [dict(record) for record in labels_records]
        if guide_scan_base_url:
            for record in label_records_for_export:
                page_name = guide_page_files.get(panelnest._assembly_record_key(record), "")
                if page_name:
                    record["scan_payload"] = _compose_guide_url(
                        guide_scan_base_url, "relatorios/" + page_name
                    )
                else:
                    record["scan_payload"] = (
                        f"{guide_public_url}#{panelnest._assembly_record_anchor(record)}"
                    )

        try:
            labels_html_text = _build_labels_html_with_external_python(
                document_label,
                document_name,
                label_records_for_export,
            )
            if App is not None:
                App.Console.PrintMessage(
                    "PanelNest: etiquetas HTML montadas com python externo para preservar o QR.\n"
                )
        except Exception as exc:
            if App is not None:
                App.Console.PrintWarning(
                    "PanelNest: fallback para HTML interno das etiquetas (%s).\n" % str(exc)
                )
            labels_html_text = panelnest.build_panelnest_labels_html(
                document=document,
                records=label_records_for_export,
            )

        pdf_created, pdf_error = _write_pdf_with_fallback(
            html_text,
            pdf_path,
            html_path,
            landscape=True,
        )
        guide_pdf_created, guide_pdf_error = _write_assembly_guide_pdf_with_fallback(
            guide_html_text,
            guide_pdf_path,
            guide_html_path,
            guide_page_paths,
            landscape=False,
        )
        labels_pdf_created, labels_pdf_error = _write_pdf_with_fallback(
            labels_html_text,
            labels_pdf_path,
            labels_html_path,
            landscape=False,
            prefer_chrome=True,
        )

        try:
            cut_sheet_html_text = panelnest.build_panelnest_cut_sheet_html(document=document)
            with open(cut_sheet_html_path, "w", encoding="utf-8") as handle:
                handle.write(cut_sheet_html_text)
        except Exception:
            cut_sheet_html_path = ""

        dxf_per_part_result = None
        dxf_per_part_error = ""
        try:
            dxf_result = panelnest.export_panelnest_dxf_package(dir_dxf)
            dxf_files = dxf_result["files"]
            dxf_variants = dxf_result.get("variants", [])
        except Exception as exc:
            dxf_error = str(exc)

        all_layout_sheets_for_parts = export_result.get("layout_sheets") or []
        if all_layout_sheets_for_parts:
            try:
                dir_dxf_pecas = os.path.join(dir_dxf, "pecas")
                dxf_per_part_result = panelnest.export_panelnest_dxf_per_part(
                    dir_dxf_pecas, all_layout_sheets_for_parts, settings, as_zip=True
                )
            except Exception as exc:
                dxf_per_part_error = str(exc)

        # Desenhos técnicos SVG por peça (com cotas)
        tech_drawing_result = None
        tech_drawing_error = ""
        try:
            all_parts = export_result.get("parts") or []
            if all_parts:
                dir_desenhos = os.path.join(output_dir, "desenhos_tecnicos")
                tech_drawing_result = panelnest.export_part_technical_drawings(
                    all_parts, dir_desenhos, as_zip=True
                )
        except Exception as exc:
            tech_drawing_error = str(exc)

        layout_sheets = export_result.get("layout_sheets") or []
        cnc_sheets = [
            ls for ls in layout_sheets
            if str(getattr(ls, "cut_method", "") or "").strip() == "CNC"
        ]
        if cnc_sheets:
            try:
                nbm_files = panelnest.export_nbm_bundle(cnc_sheets, settings, dir_cnc)
            except Exception as exc:
                nbm_error = str(exc)

        if App is not None:
            App.Console.PrintMessage(
                "PanelNest: exportou %d CSV(s) para '%s'.\n"
                % (len(csv_files), _native_path(output_dir))
            )
            if guide_cloud_publish_error:
                App.Console.PrintWarning(
                    "PanelNest: nao conseguiu publicar automaticamente no Cloudflare Pages (%s).\n"
                    % guide_cloud_publish_error
                )
            if guide_public_url:
                if guide_delivery_mode == "public":
                    if cloudflare_auto_publish_enabled and not guide_cloud_publish_error:
                        App.Console.PrintMessage(
                            "PanelNest: guia publicado automaticamente no Cloudflare Pages em '%s'.\n"
                            % guide_public_url
                        )
                    else:
                        App.Console.PrintMessage(
                            "PanelNest: QR configurado para a URL publica '%s'.\n"
                            % guide_public_url
                        )
                else:
                    App.Console.PrintMessage(
                        "PanelNest: guia de montagem local disponivel em '%s'.\n"
                        % guide_public_url
                    )
            elif guide_server_error:
                App.Console.PrintWarning(
                    "PanelNest: nao conseguiu publicar o guia local (%s).\n"
                    % guide_server_error
                )
            if pdf_created:
                App.Console.PrintMessage(
                    "PanelNest: relatorio PDF criado em '%s'.\n" % _native_path(pdf_path)
                )
            else:
                App.Console.PrintWarning(
                    "PanelNest: nao conseguiu gerar o PDF%s. Relatorio HTML criado em '%s'.\n"
                    % (
                        f" ({pdf_error})" if pdf_error else "",
                        _native_path(html_path),
                    )
                )
            if guide_pdf_created:
                App.Console.PrintMessage(
                    "PanelNest: guia de montagem PDF criado em '%s'.\n"
                    % _native_path(guide_pdf_path)
                )
            else:
                App.Console.PrintWarning(
                    "PanelNest: nao conseguiu gerar o guia de montagem PDF%s. HTML criado em '%s'.\n"
                    % (
                        f" ({guide_pdf_error})" if guide_pdf_error else "",
                        _native_path(guide_html_path),
                    )
                )
            if labels_pdf_created:
                App.Console.PrintMessage(
                    "PanelNest: etiquetas PDF criadas em '%s'.\n"
                    % _native_path(labels_pdf_path)
                )
            else:
                App.Console.PrintWarning(
                    "PanelNest: nao conseguiu gerar as etiquetas PDF%s. HTML criado em '%s'.\n"
                    % (
                        f" ({labels_pdf_error})" if labels_pdf_error else "",
                        _native_path(labels_html_path),
                    )
                )
            if dxf_files:
                App.Console.PrintMessage(
                    "PanelNest: exportou %d arquivo(s) DXF em '%s'.\n"
                    % (len(dxf_files), _native_path(dir_dxf))
                )
            elif dxf_error:
                App.Console.PrintWarning(
                    "PanelNest: nao conseguiu gerar o DXF (%s).\n" % dxf_error
                )
            if dxf_per_part_result:
                App.Console.PrintMessage(
                    "PanelNest: DXF por peca salvo em '%s'.\n"
                    % _native_path(str(dxf_per_part_result))
                )
            elif dxf_per_part_error:
                App.Console.PrintWarning(
                    "PanelNest: nao conseguiu gerar DXF por peca (%s).\n" % dxf_per_part_error
                )
            if nbm_files:
                App.Console.PrintMessage(
                    "PanelNest: exportou %d arquivo(s) G-code NBM (.nbm) em '%s'.\n"
                    % (len(nbm_files), _native_path(dir_cnc))
                )
            elif nbm_error:
                App.Console.PrintWarning(
                    "PanelNest: nao conseguiu gerar os arquivos NBM (%s).\n" % nbm_error
                )

        message_lines = [
            "Arquivos exportados com sucesso.",
            "",
            f"Validacao: {validation_summary}",
        ]
        if validation_counts.get("Alerta", 0):
            alert_preview_lines = _validation_issue_preview_lines(
                export_result.get("validation_issues") or [],
                severities=("Alerta",),
                limit=4,
            )
            if alert_preview_lines:
                message_lines.extend(["", "Pendencias principais:"])
                message_lines.extend(alert_preview_lines)
        elif validation_counts.get("Info", 0):
            info_preview_lines = _validation_issue_preview_lines(
                export_result.get("validation_issues") or [],
                severities=("Info",),
                limit=3,
            )
            if info_preview_lines:
                message_lines.extend(["", "Observacoes principais:"])
                message_lines.extend(info_preview_lines)
        message_lines.extend(
            [
                "",
                f"CSV: {len(csv_files)} arquivo(s)",
                f"Pasta: {_native_path(output_dir)}",
            ]
        )
        message_lines.extend(["", f"Guia HTML: {_native_path(guide_html_path)}"])
        if guide_cloud_publish_error:
            message_lines.append(
                f"Publicacao automatica no Cloudflare falhou nesta execucao: {guide_cloud_publish_error}"
            )
        if guide_public_url:
            if guide_delivery_mode == "public":
                message_lines.append(f"Guia via QR publico: {guide_public_url}")
                if cloudflare_auto_publish_enabled and not guide_cloud_publish_error:
                    message_lines.append("Publicacao automatica no Cloudflare: concluida")
                elif not cloudflare_auto_publish_enabled:
                    message_lines.append(
                        "Modo cloud manual: publique os HTMLs desta pasta nessa URL base para atualizar o QR."
                    )
            else:
                message_lines.append(f"Guia via QR local: {guide_public_url}")
        elif guide_server_error:
            message_lines.append(f"Guia local indisponivel nesta execucao: {guide_server_error}")
        if pdf_created:
            message_lines.extend(["", f"PDF: {_native_path(pdf_path)}"])
        else:
            message_lines.extend(
                [
                    "",
                    f"HTML: {_native_path(html_path)}",
                ]
            )
            if pdf_error:
                message_lines.extend(["", f"PDF indisponivel nesta execucao: {pdf_error}"])

        if guide_pdf_created:
            message_lines.extend(["", f"Guia PDF: {_native_path(guide_pdf_path)}"])
        elif guide_pdf_error:
            message_lines.extend(["", f"Guia PDF indisponivel nesta execucao: {guide_pdf_error}"])

        if labels_pdf_created:
            message_lines.extend(["", f"Etiquetas PDF: {_native_path(labels_pdf_path)}"])
        else:
            message_lines.extend(["", f"Etiquetas HTML: {_native_path(labels_html_path)}"])
            if labels_pdf_error:
                message_lines.extend(
                    ["", f"Etiquetas PDF indisponiveis nesta execucao: {labels_pdf_error}"]
                )

        if cut_sheet_html_path:
            message_lines.extend(["", f"Plano de Corte A4: {_native_path(cut_sheet_html_path)}"])

        if dxf_files:
            message_lines.extend(["", f"DXF: {len(dxf_files)} arquivo(s)"])
            for variant in dxf_variants:
                message_lines.append(f"- modo {variant['name']}: {variant['file_count']} arquivo(s)")
        elif dxf_error:
            message_lines.extend(["", f"DXF indisponivel nesta execucao: {dxf_error}"])

        if dxf_per_part_result:
            message_lines.extend(["", f"DXF por peca (ZIP): {_native_path(str(dxf_per_part_result))}"])
        elif dxf_per_part_error:
            message_lines.extend(["", f"DXF por peca indisponivel nesta execucao: {dxf_per_part_error}"])

        if nbm_files:
            message_lines.extend(["", f"G-code NBM (CNC): {len(nbm_files)} arquivo(s) .nbm"])
        elif nbm_error:
            message_lines.extend(["", f"G-code NBM indisponivel nesta execucao: {nbm_error}"])

        _show_info_dialog("\n".join(message_lines))

    def IsActive(self):
        if App is None:
            return False
        return App.ActiveDocument is not None

    def GetResources(self):
        return {
            "Pixmap": os.path.join(
                os.path.dirname(__file__), "..", "resources", "icons", "panelnest_export.svg"
            ),
            "Accel": "",
            "MenuText": "Exportar Arquivos",
            "ToolTip": (
                "Exporta as planilhas do PanelNest em CSV, gera relatorio e etiquetas em PDF, salva DXF e publica o guia no Cloudflare Pages quando configurado."
            ),
        }


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, ExportReportsCommand())
