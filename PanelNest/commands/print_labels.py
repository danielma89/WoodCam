# -*- coding: utf-8 -*-
"""
Comando: Exportar Etiquetas
Dialog para exportar etiquetas em HTML (navegador), SVG individual ou ZPL (Zebra).
"""

import json
import os
import shutil
import subprocess
import tempfile
import webbrowser
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
except ImportError:
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide6 import QtCore, QtGui, QtWidgets

import panelnest

COMMAND_NAME = "PanelNest_PrintLabels"


def _main_window():
    if Gui is not None and hasattr(Gui, "getMainWindow"):
        try:
            return Gui.getMainWindow()
        except Exception:
            return None
    return None


def _default_dir():
    start = os.path.expanduser("~")
    if App is not None and App.ActiveDocument is not None:
        fname = getattr(App.ActiveDocument, "FileName", "") or ""
        if fname:
            start = os.path.dirname(fname) or start
    return start


def _show_error(message):
    if Gui is None:
        return
    QtWidgets.QMessageBox.critical(_main_window(), "PanelNest: Etiquetas", message)


def _show_info(message):
    if Gui is None:
        return
    QtWidgets.QMessageBox.information(_main_window(), "PanelNest: Etiquetas", message)


def _build_labels_html(document, records):
    """Gera HTML de etiquetas com QR code via processo externo, com fallback interno."""
    repo_root = Path(__file__).resolve().parent.parent
    doc_label = str(getattr(document, "Label", "") or "") if document else ""
    doc_name = str(getattr(document, "Name", "") or "") if document else ""

    script = (
        "import json, sys; from pathlib import Path; "
        "repo = Path(sys.argv[3]).resolve(); "
        "sys.path.insert(0, str(repo)); "
        "import panelnest; "
        "class D: pass; "
        "d = D(); d.Label = sys.argv[1]; d.Name = sys.argv[2]; "
        "recs = json.load(sys.stdin); "
        "sys.stdout.write(panelnest.build_panelnest_labels_html(document=d, records=recs))"
    )
    # Limpar variáveis do AppImage para não contaminar o Python do sistema
    clean_env = {
        k: v for k, v in os.environ.items()
        if k not in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP",
                     "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__")
    }

    payload = json.dumps(records, ensure_ascii=False)
    seen = set()
    for exe in filter(None, [shutil.which("python3"), "/usr/bin/python3"]):
        if exe in seen:
            continue
        seen.add(exe)
        try:
            result = subprocess.run(
                [exe, "-c", script, doc_label, doc_name, str(repo_root)],
                input=payload, text=True, capture_output=True, timeout=30,
                env=clean_env,
            )
            html = (result.stdout or "").strip()
            if html and "label-qr" in html:
                return html
            if result.stderr and App is not None:
                App.Console.PrintWarning(
                    f"PanelNest etiquetas externo ({exe}): {result.stderr[:200]}\n"
                )
        except Exception as exc:
            if App is not None:
                App.Console.PrintWarning(f"PanelNest etiquetas externo falhou: {exc}\n")
    return panelnest.build_panelnest_labels_html(document=document, records=records)


def _collect_parts_and_refs():
    doc = App.ActiveDocument if App is not None else None
    objects = doc.Objects if doc is not None else []
    parts = panelnest.collect_parts(objects)
    sheet_ref_map = {}
    try:
        records = panelnest.collect_part_label_records(parts=parts, document=doc)
        for rec in records or []:
            pid = str((rec or {}).get("part_id", "") or "").strip()
            sheet_label = str((rec or {}).get("sheet_label", "") or "").strip()
            if pid and sheet_label:
                sheet_ref_map[pid] = sheet_label
    except Exception:
        pass
    return parts, sheet_ref_map


class _PrintLabelsDialog(QtWidgets.QDialog):
    def __init__(self, parts, sheet_ref_map, parent=None):
        super().__init__(parent)
        self._parts = parts
        self._sheet_ref_map = sheet_ref_map
        self.setWindowTitle("PanelNest: Exportar Etiquetas")
        self.setMinimumWidth(420)
        self._build_ui()
        self._update_count()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)

        self._count_label = QtWidgets.QLabel()
        self._count_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(self._count_label)

        options_group = QtWidgets.QGroupBox("Opções")
        options_layout = QtWidgets.QVBoxLayout(options_group)

        self._chk_sheet_ref = QtWidgets.QCheckBox("Incluir referência de chapa")
        self._chk_sheet_ref.setChecked(bool(self._sheet_ref_map))
        self._chk_sheet_ref.setEnabled(bool(self._sheet_ref_map))
        if not self._sheet_ref_map:
            self._chk_sheet_ref.setToolTip(
                "Nenhum layout gerado; referências de chapa não disponíveis."
            )
        options_layout.addWidget(self._chk_sheet_ref)

        self._chk_project_name = QtWidgets.QCheckBox("Incluir nome do projeto")
        self._chk_project_name.setChecked(True)
        options_layout.addWidget(self._chk_project_name)

        layout.addWidget(options_group)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setFrameShadow(QtWidgets.QFrame.Sunken)
        layout.addWidget(sep)

        export_group = QtWidgets.QGroupBox("Exportar")
        export_layout = QtWidgets.QVBoxLayout(export_group)

        btn_html = QtWidgets.QPushButton("Abrir no Navegador (imprimir / salvar PDF)")
        btn_html.setToolTip(
            "Gera etiquetas com QR code e abre no navegador padrão.\n"
            "No navegador: Ctrl+P → Salvar como PDF."
        )
        btn_html.clicked.connect(self._export_html)
        export_layout.addWidget(btn_html)

        btn_svg = QtWidgets.QPushButton("Exportar SVG (arquivos individuais)")
        btn_svg.setToolTip("Salva um arquivo SVG por peça em uma pasta escolhida.")
        btn_svg.clicked.connect(self._export_svg)
        export_layout.addWidget(btn_svg)

        btn_zpl = QtWidgets.QPushButton("Exportar ZPL (Zebra)")
        btn_zpl.setToolTip("Salva um arquivo .zpl com todas as etiquetas para impressora Zebra.")
        btn_zpl.clicked.connect(self._export_zpl)
        export_layout.addWidget(btn_zpl)

        layout.addWidget(export_group)

        btn_close = QtWidgets.QPushButton("Fechar")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)

    def _update_count(self):
        total = sum(int(getattr(p, "quantity", 1) or 1) for p in self._parts)
        unique = len(self._parts)
        self._count_label.setText(f"{total} etiqueta(s) para {unique} tipo(s) de peça")

    def _get_project_name(self):
        if not self._chk_project_name.isChecked():
            return ""
        if App is not None and App.ActiveDocument is not None:
            return str(App.ActiveDocument.Name or "")
        return ""

    def _build_parts_with_refs(self):
        use_refs = self._chk_sheet_ref.isChecked()
        result = []
        for part in self._parts:
            ref = ""
            if use_refs:
                ref = self._sheet_ref_map.get(str(getattr(part, "part_id", "") or ""), "")
            result.append((part, ref))
        return result

    def _export_html(self):
        try:
            doc = App.ActiveDocument if App is not None else None
            records = panelnest.collect_part_label_records(
                parts=self._parts, document=doc
            ) or []
            use_refs = self._chk_sheet_ref.isChecked()
            for rec in records:
                pid = str((rec or {}).get("part_id", "") or "").strip()
                if use_refs and pid in self._sheet_ref_map:
                    rec["sheet_label"] = self._sheet_ref_map[pid]

            html_text = _build_labels_html(doc, records)

            with tempfile.NamedTemporaryFile(
                "w", suffix="_etiquetas.html", encoding="utf-8", delete=False
            ) as fh:
                fh.write(html_text)
                html_path = fh.name

            webbrowser.open(Path(html_path).resolve().as_uri())
            _show_info(
                "Etiquetas abertas no navegador.\n\n"
                "Use Ctrl+P para imprimir ou salvar como PDF."
            )
        except Exception as exc:
            _show_error(f"Erro ao gerar etiquetas:\n{exc}")

    def _export_svg(self):
        from panelnest.label_generator import generate_label_svg_batch

        parts_with_refs = self._build_parts_with_refs()
        project_name = self._get_project_name()

        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "PanelNest: Escolher Pasta para SVGs", _default_dir()
        )
        if not folder:
            return

        try:
            svg_list = generate_label_svg_batch(parts_with_refs, project_name=project_name)
            idx_per_label = {}
            flat_parts = []
            for part, _ref in parts_with_refs:
                qty = int(getattr(part, "quantity", 1) or 1)
                for _ in range(qty):
                    flat_parts.append(part)

            saved = 0
            for i, (svg_str, part) in enumerate(zip(svg_list, flat_parts)):
                raw_label = str(getattr(part, "label", "") or f"peca_{i}")
                safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in raw_label)
                count = idx_per_label.get(safe, 0)
                idx_per_label[safe] = count + 1
                suffix = f"_{count + 1}" if count > 0 else ""
                file_path = os.path.join(folder, f"{safe}{suffix}.svg")
                with open(file_path, "w", encoding="utf-8") as fh:
                    fh.write(svg_str)
                saved += 1

            _show_info(f"{saved} arquivo(s) SVG exportados em:\n{folder}")
        except Exception as exc:
            _show_error(f"Erro ao exportar SVGs:\n{exc}")

    def _export_zpl(self):
        from panelnest.label_generator import generate_label_zpl

        parts_with_refs = self._build_parts_with_refs()

        save_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "PanelNest: Salvar ZPL de Etiquetas",
            os.path.join(_default_dir(), "etiquetas.zpl"),
            "Arquivos ZPL (*.zpl);;Todos os arquivos (*)",
        )
        if not save_path:
            return

        try:
            zpl_blocks = []
            for part, ref in parts_with_refs:
                qty = int(getattr(part, "quantity", 1) or 1)
                for _ in range(qty):
                    zpl_blocks.append(generate_label_zpl(part, sheet_ref=ref))

            with open(save_path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(zpl_blocks))

            _show_info(f"{len(zpl_blocks)} etiqueta(s) ZPL exportadas em:\n{save_path}")
        except Exception as exc:
            _show_error(f"Erro ao exportar ZPL:\n{exc}")


class PrintLabelsCommand:
    def GetResources(self):
        import panelnest as _pn
        return {
            "Pixmap": _pn.get_icon_path("panelnest_label.svg"),
            "MenuText": "Exportar Etiquetas",
            "ToolTip": (
                "Exporta etiquetas das peças: abre no navegador (com QR code), "
                "SVG individual ou ZPL para impressora Zebra."
            ),
        }

    def IsActive(self):
        return App is not None and App.ActiveDocument is not None

    def Activated(self):
        try:
            parts, sheet_ref_map = _collect_parts_and_refs()
        except Exception as exc:
            _show_error(f"Erro ao coletar peças:\n{exc}")
            return

        if not parts:
            _show_info("Nenhuma peça encontrada.\nUse 'Etiquetar Peças' primeiro.")
            return

        dialog = _PrintLabelsDialog(parts, sheet_ref_map, parent=_main_window())
        dialog.exec_()


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, PrintLabelsCommand())
