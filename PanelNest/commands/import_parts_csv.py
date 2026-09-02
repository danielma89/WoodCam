"""Importar peças de um arquivo CSV e criar objetos Part::Box no FreeCAD.

Colunas suportadas (case-insensitive, aceita variações em pt/en):
  Obrigatórias: nome (ou label/rotulo), comprimento (ou length/comp), largura (ou width/larg), espessura (ou thickness/esp)
  Opcionais:    material, quantidade (ou qty/qtd), metodo_corte (ou cut_method),
                fita_sup, fita_inf, fita_esq, fita_dir (ou edge_top/bottom/left/right)
                veio (ou grain_direction), pode_girar (ou allow_rotation)

Formato aceito: CSV com separador vírgula ou ponto-e-vírgula, encoding UTF-8 ou latin-1.
"""
import csv
import io
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
    from PySide import QtCore, QtGui

    QtWidgets = QtGui
except ImportError:
    try:
        from PySide2 import QtCore, QtWidgets
    except ImportError:
        from PySide6 import QtCore, QtWidgets


COMMAND_NAME = "PanelNest_ImportPartsCSV"

# ── Mapeamento de variações de cabeçalho ──────────────────────────────────────

_COLUMN_ALIASES = {
    # Cabeçalhos normais + cabeçalhos exatos do CSV exportado pelo PanelNest
    "nome":         ["nome", "label", "name", "descricao", "descrição",
                     "peça", "peca", "id_da_peca", "id da peca"],
    # "rotulo" separado — quando presente no CSV do PanelNest, tem prioridade sobre "nome"
    "rotulo":       ["rotulo", "rótulo", "part_label", "rotulo_referencia", "rotulo/referencia"],
    "comprimento":  ["comprimento", "comp", "length", "len", "c", "compr",
                     "comprimento_(mm)", "comprimento (mm)"],
    "largura":      ["largura", "larg", "width", "w",
                     "largura_(mm)", "largura (mm)"],
    "espessura":    ["espessura", "esp", "thickness", "thick", "e",
                     "espessura_(mm)", "espessura (mm)"],
    "material":     ["material", "mat"],
    "quantidade":   ["quantidade", "qtd", "qty", "quant", "q"],
    "metodo_corte": ["metodo_corte", "metodo", "cut_method", "corte", "processo",
                     "metodo_de_corte", "metodo de corte"],
    # Fita de borda: o CSV do PanelNest exporta coluna única "Fita de borda"
    # com valor tipo "Superior, Inferior" — tratado em _parse_rows
    "fita_borda":   ["fita_de_borda", "fita de borda", "fita_borda", "edge_band"],
    "fita_sup":     ["fita_sup", "fita_superior", "edge_top", "borda_sup", "bs"],
    "fita_inf":     ["fita_inf", "fita_inferior", "edge_bottom", "borda_inf", "bi"],
    "fita_esq":     ["fita_esq", "fita_esquerda", "edge_left", "borda_esq", "be"],
    "fita_dir":     ["fita_dir", "fita_direita", "edge_right", "borda_dir", "bd"],
    "veio":         ["veio", "grain", "grain_direction", "fibra", "direcao_fibra",
                     "veio/fibra", "veio_fibra"],
    "pode_girar":   ["pode_girar", "girar", "allow_rotation", "rotacao",
                     "rotacao_permitida", "rotacao permitida"],
}

_BOOL_TRUE  = {"1", "s", "sim", "yes", "true", "x", "✓", "v"}
_BOOL_FALSE = {"0", "n", "nao", "não", "no", "false", "", "-"}


def _normalize_header(h):
    # Remove parênteses e conteúdo entre eles, normaliza espaços/hífens
    import re
    s = h.strip().lower()
    s = re.sub(r"\s*\(.*?\)", "", s)   # remove "(mm)", "(m2)", etc.
    s = s.replace("/", "_").replace("-", "_").replace(" ", "_")
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def _map_headers(fieldnames):
    """Retorna dict {canonical_key: index_in_row} para as colunas encontradas."""
    mapping = {}
    for idx, raw in enumerate(fieldnames):
        norm = _normalize_header(raw)
        for canonical, aliases in _COLUMN_ALIASES.items():
            if norm in aliases and canonical not in mapping:
                mapping[canonical] = idx
    return mapping


def _parse_float(value, default=0.0):
    try:
        return float(str(value).strip().replace(",", "."))
    except (ValueError, AttributeError):
        return default


def _parse_int(value, default=1):
    try:
        return max(1, int(float(str(value).strip().replace(",", "."))))
    except (ValueError, AttributeError):
        return default


def _parse_bool(value, default=False):
    norm = str(value).strip().lower()
    if norm in _BOOL_TRUE:
        return True
    if norm in _BOOL_FALSE:
        return False
    return default


def _read_csv_rows(filepath):
    """Lê o CSV tentando encodings e separadores comuns. Retorna (fieldnames, rows, error)."""
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        for delimiter in (",", ";", "\t"):
            try:
                with open(filepath, newline="", encoding=encoding) as fh:
                    sample = fh.read(4096)
                    fh.seek(0)
                    # Conta delimitadores na primeira linha
                    first_line = sample.split("\n")[0]
                    if first_line.count(delimiter) == 0:
                        continue
                    reader = csv.DictReader(fh, delimiter=delimiter)
                    rows = list(reader)
                    if rows and reader.fieldnames:
                        return reader.fieldnames, rows, None
            except Exception:
                continue
    return None, None, "Não foi possível ler o arquivo. Verifique se é um CSV válido (UTF-8 ou latin-1)."


def _parse_fita_borda_text(text):
    """Interpreta coluna 'Fita de borda' do PanelNest: 'Superior, Inferior' → (T,T,F,F)."""
    norm = text.strip().lower()
    if not norm or norm in ("sem fita", "-", "nenhuma", ""):
        return False, False, False, False
    sup = any(k in norm for k in ("sup", "top", "cima"))
    inf = any(k in norm for k in ("inf", "bottom", "baixo"))
    esq = any(k in norm for k in ("esq", "left", "esquerda"))
    dir_ = any(k in norm for k in ("dir", "right", "direita"))
    # "Todas" ou "4 lados"
    if any(k in norm for k in ("todas", "all", "4 lado", "quatro")):
        return True, True, True, True
    return sup, inf, esq, dir_


def _parse_rows(fieldnames, rows):
    """Converte linhas do CSV em lista de dicts normalizados. Retorna (parts, errors)."""
    mapping = _map_headers(fieldnames)

    # O CSV do PanelNest tem "ID da peca" e "Rotulo" separados.
    # "nome" mapeia para "id_da_peca"; se também tiver "rotulo", usamos rotulo.
    has_rotulo = "rotulo" in mapping

    required = ["comprimento", "largura", "espessura"]
    # Para nome: aceita "nome" OU "rotulo"
    if "nome" not in mapping and "rotulo" not in mapping:
        required.insert(0, "nome")
    missing = [k for k in required if k not in mapping]
    if missing:
        return None, (
            f"Colunas obrigatórias não encontradas: {', '.join(missing)}.\n\n"
            f"Colunas presentes: {', '.join(fieldnames)}\n\n"
            f"Dica: use o arquivo 'pecas_panelnest.csv' exportado pelo PanelNest, "
            f"ou crie um CSV com colunas: nome, comprimento, largura, espessura."
        )

    def _get(row, key, default=""):
        idx = mapping.get(key)
        if idx is None:
            return default
        vals = list(row.values())
        return vals[idx] if idx < len(vals) else default

    parts = []
    errors = []
    for line_num, row in enumerate(rows, start=2):
        # Nome: prefere "rotulo" (mais legível) sobre "nome"/"id_da_peca"
        if has_rotulo:
            nome = str(_get(row, "rotulo", "")).strip()
        else:
            nome = str(_get(row, "nome", "")).strip()
        if not nome:
            nome = f"Peça {line_num - 1:03d}"

        comp = _parse_float(_get(row, "comprimento", "0"))
        larg = _parse_float(_get(row, "largura", "0"))
        esp  = _parse_float(_get(row, "espessura", "0"))

        if comp <= 0 or larg <= 0:
            errors.append(f"Linha {line_num}: '{nome}' ignorada — comprimento/largura inválidos ({comp}×{larg})")
            continue

        # Fita de borda: coluna única "Fita de borda" ou colunas individuais
        fita_borda_text = str(_get(row, "fita_borda", "")).strip()
        if fita_borda_text:
            fita_sup, fita_inf, fita_esq, fita_dir = _parse_fita_borda_text(fita_borda_text)
        else:
            fita_sup = _parse_bool(_get(row, "fita_sup", "0"))
            fita_inf = _parse_bool(_get(row, "fita_inf", "0"))
            fita_esq = _parse_bool(_get(row, "fita_esq", "0"))
            fita_dir = _parse_bool(_get(row, "fita_dir", "0"))

        parts.append({
            "nome": nome,
            "comprimento": comp,
            "largura": larg,
            "espessura": esp,
            "material": str(_get(row, "material", "")).strip(),
            "quantidade": _parse_int(_get(row, "quantidade", "1")),
            "metodo_corte": str(_get(row, "metodo_corte", "Auto")).strip() or "Auto",
            "fita_sup": fita_sup,
            "fita_inf": fita_inf,
            "fita_esq": fita_esq,
            "fita_dir": fita_dir,
            "veio": str(_get(row, "veio", "Livre")).strip() or "Livre",
            "pode_girar": _parse_bool(_get(row, "pode_girar", "1"), default=True),
        })

    return parts, errors


# ── Dialog de preview ────────────────────────────────────────────────────────

def _exec_dialog(dialog):
    if hasattr(dialog, "exec_"):
        return dialog.exec_()
    return dialog.exec()


def _stretch_mode():
    if hasattr(QtWidgets.QHeaderView, "Stretch"):
        return QtWidgets.QHeaderView.Stretch
    return QtWidgets.QHeaderView.ResizeMode.Stretch


def _item_is_editable():
    if hasattr(QtCore.Qt, "ItemIsEditable"):
        return QtCore.Qt.ItemIsEditable
    return QtCore.Qt.ItemFlag.ItemIsEditable


def _read_only_item(text):
    item = QtWidgets.QTableWidgetItem(str(text))
    item.setFlags(item.flags() & ~_item_is_editable())
    return item


# ── Perfis de mapeamento ─────────────────────────────────────────────────────

_PROFILES_PATH = os.path.join(
    os.path.expanduser("~"), ".local", "share", "PanelNest", "csv_profiles.json"
)

# Campos canônicos: (chave, label_pt, obrigatório)
_CANONICAL_FIELDS = [
    ("nome",         "Nome / Rótulo",      True),
    ("comprimento",  "Comprimento (mm)",   True),
    ("largura",      "Largura (mm)",        True),
    ("espessura",    "Espessura (mm)",      True),
    ("material",     "Material",            False),
    ("quantidade",   "Quantidade",          False),
    ("metodo_corte", "Método de corte",     False),
    ("fita_borda",   "Fita de borda (texto)", False),
    ("fita_sup",     "Fita Superior",       False),
    ("fita_inf",     "Fita Inferior",       False),
    ("fita_esq",     "Fita Esquerda",       False),
    ("fita_dir",     "Fita Direita",        False),
    ("veio",         "Veio / Fibra",        False),
    ("pode_girar",   "Pode girar",          False),
]
_IGNORE_OPTION = "(ignorar)"


def _load_profiles():
    try:
        import json
        with open(_PROFILES_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_profiles(profiles):
    try:
        import json
        os.makedirs(os.path.dirname(_PROFILES_PATH), exist_ok=True)
        with open(_PROFILES_PATH, "w", encoding="utf-8") as fh:
            json.dump(profiles, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


class ColumnMappingDialog(QtWidgets.QDialog):
    """Dialog de mapeamento manual de colunas do CSV para campos do PanelNest."""

    def __init__(self, fieldnames, rows, filepath, parent=None):
        super().__init__(parent)
        self._fieldnames = list(fieldnames)
        self._rows = rows
        self._filepath = filepath
        self._profiles = _load_profiles()
        self._combos = {}  # canonical -> QComboBox
        self.setWindowTitle("PanelNest: Mapear Colunas do CSV")
        self.setModal(True)
        self.resize(640, 520)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        info = QtWidgets.QLabel(
            "O PanelNest não reconheceu automaticamente as colunas deste arquivo.\n"
            "Associe cada coluna do seu CSV ao campo correto."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Seletor de perfil
        profile_row = QtWidgets.QHBoxLayout()
        profile_row.addWidget(QtWidgets.QLabel("Perfil salvo:"))
        self._profile_combo = QtWidgets.QComboBox()
        self._profile_combo.addItem("(nenhum)")
        for name in sorted(self._profiles.keys()):
            self._profile_combo.addItem(name)
        self._profile_combo.currentTextChanged.connect(self._load_profile)
        profile_row.addWidget(self._profile_combo)
        save_btn = QtWidgets.QPushButton("Salvar como...")
        save_btn.clicked.connect(self._save_profile)
        del_btn = QtWidgets.QPushButton("Apagar")
        del_btn.clicked.connect(self._delete_profile)
        profile_row.addWidget(save_btn)
        profile_row.addWidget(del_btn)
        profile_row.addStretch(1)
        layout.addLayout(profile_row)

        # Tabela de mapeamento
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        container = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(container)
        form.setLabelAlignment(QtCore.Qt.AlignRight)

        csv_options = [_IGNORE_OPTION] + self._fieldnames
        # Primeira linha da CSV como amostra de valores
        sample = self._rows[0] if self._rows else {}
        sample_vals = list(sample.values()) if sample else []

        for canonical, label_pt, required in _CANONICAL_FIELDS:
            combo = QtWidgets.QComboBox()
            combo.addItems(csv_options)
            # Tenta auto-selecionar pela detecção existente
            auto_idx = self._auto_guess(canonical, csv_options)
            combo.setCurrentIndex(auto_idx)
            # Mostra amostra do valor quando seleciona
            combo.currentIndexChanged.connect(
                lambda idx, c=combo, sv=sample_vals: self._update_sample(c, sv)
            )
            req_mark = " *" if required else ""
            row_widget = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(combo)
            sample_lbl = QtWidgets.QLabel()
            sample_lbl.setStyleSheet("color: gray; font-size: 11px;")
            sample_lbl.setMinimumWidth(120)
            row_layout.addWidget(sample_lbl)
            combo._sample_label = sample_lbl
            self._update_sample(combo, sample_vals)
            form.addRow(f"{label_pt}{req_mark}:", row_widget)
            self._combos[canonical] = combo

        scroll.setWidget(container)
        layout.addWidget(scroll)

        legend = QtWidgets.QLabel("* Campo obrigatório")
        legend.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(legend)

        btn = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btn.accepted.connect(self._on_accept)
        btn.rejected.connect(self.reject)
        layout.addWidget(btn)

    def _auto_guess(self, canonical, options):
        """Tenta adivinhar a coluna certa pelos aliases conhecidos."""
        aliases = _COLUMN_ALIASES.get(canonical, [])
        for col_name in self._fieldnames:
            if _normalize_header(col_name) in aliases:
                try:
                    return options.index(col_name)
                except ValueError:
                    pass
        return 0  # "(ignorar)"

    def _update_sample(self, combo, sample_vals):
        idx = combo.currentIndex() - 1  # -1 porque primeiro item é "(ignorar)"
        lbl = getattr(combo, "_sample_label", None)
        if lbl is None:
            return
        if idx < 0 or idx >= len(sample_vals):
            lbl.setText("")
        else:
            lbl.setText(f"ex: {str(sample_vals[idx])[:30]}")

    def _load_profile(self, name):
        if name == "(nenhum)" or name not in self._profiles:
            return
        mapping = self._profiles[name]
        for canonical, combo in self._combos.items():
            col_name = mapping.get(canonical, _IGNORE_OPTION)
            idx = combo.findText(col_name)
            if idx >= 0:
                combo.setCurrentIndex(idx)

    def _save_profile(self):
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Salvar perfil", "Nome do perfil (ex: CorteCloud, Excel padrão):"
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        mapping = {
            canonical: combo.currentText()
            for canonical, combo in self._combos.items()
            if combo.currentText() != _IGNORE_OPTION
        }
        self._profiles[name] = mapping
        _save_profiles(self._profiles)
        if self._profile_combo.findText(name) < 0:
            self._profile_combo.addItem(name)
        self._profile_combo.setCurrentText(name)

    def _delete_profile(self):
        name = self._profile_combo.currentText()
        if name == "(nenhum)" or name not in self._profiles:
            return
        del self._profiles[name]
        _save_profiles(self._profiles)
        idx = self._profile_combo.findText(name)
        if idx >= 0:
            self._profile_combo.removeItem(idx)

    def _on_accept(self):
        # Valida campos obrigatórios
        missing = []
        for canonical, label_pt, required in _CANONICAL_FIELDS:
            if not required:
                continue
            combo = self._combos.get(canonical)
            if combo is None or combo.currentText() == _IGNORE_OPTION:
                # "nome" pode ser ignorado se "rotulo" estiver mapeado
                if canonical == "nome" and self._combos.get("rotulo") and \
                        self._combos["rotulo"].currentText() != _IGNORE_OPTION:
                    continue
                missing.append(label_pt)
        if missing:
            QtWidgets.QMessageBox.warning(
                self, "Campos obrigatórios",
                f"Mapeie os campos obrigatórios antes de continuar:\n\n" +
                "\n".join(f"• {m}" for m in missing)
            )
            return
        self.accept()

    def remapped_fieldnames_and_rows(self):
        """Retorna (fieldnames, rows) com colunas renomeadas conforme o mapeamento."""
        # col_name_original -> canonical (ou None para ignorar)
        col_to_canonical = {}
        for canonical, combo in self._combos.items():
            col_name = combo.currentText()
            if col_name != _IGNORE_OPTION:
                col_to_canonical[col_name] = canonical

        new_fieldnames = []
        col_map = {}  # índice_original -> novo_nome
        for i, fn in enumerate(self._fieldnames):
            mapped = col_to_canonical.get(fn)
            if mapped:
                new_fieldnames.append(mapped)
                col_map[i] = mapped
            else:
                new_fieldnames.append(fn)

        new_rows = []
        for row in self._rows:
            vals = list(row.values())
            new_row = {}
            for i, fn in enumerate(new_fieldnames):
                new_row[fn] = vals[i] if i < len(vals) else ""
            new_rows.append(new_row)

        return new_fieldnames, new_rows


class CSVPreviewDialog(QtWidgets.QDialog):
    def __init__(self, parts, parse_errors, filepath, parent=None):
        super().__init__(parent)
        self._parts = parts
        self.setWindowTitle("PanelNest: Importar Peças do CSV")
        self.setModal(True)
        self.resize(960, 560)
        self._build_ui(parts, parse_errors, filepath)

    def _build_ui(self, parts, errors, filepath):
        layout = QtWidgets.QVBoxLayout(self)

        # Cabeçalho
        header = QtWidgets.QLabel(
            f"<b>{len(parts)} peça(s)</b> encontrada(s) em <code>{os.path.basename(filepath)}</code>. "
            "Revise abaixo e clique em OK para criar os objetos no documento."
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        if errors:
            err_box = QtWidgets.QGroupBox(f"Avisos ({len(errors)})")
            err_layout = QtWidgets.QVBoxLayout(err_box)
            err_text = QtWidgets.QLabel("\n".join(errors[:10]))
            err_text.setStyleSheet("color: #a00; font-size: 11px;")
            err_text.setWordWrap(True)
            err_layout.addWidget(err_text)
            layout.addWidget(err_box)

        # Tabela de preview
        cols = ["Nome", "Comp.", "Larg.", "Esp.", "Material", "Qtd",
                "Método", "Fita S", "Fita I", "Fita E", "Fita D", "Veio", "Girar"]
        self._table = QtWidgets.QTableWidget(len(parts), len(cols))
        self._table.setHorizontalHeaderLabels(cols)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        hdr = self._table.horizontalHeader()
        if hasattr(hdr, "setSectionResizeMode"):
            hdr.setSectionResizeMode(0, _stretch_mode())

        def _bool_icon(v):
            return "✓" if v else ""

        for i, p in enumerate(parts):
            self._table.setItem(i, 0,  _read_only_item(p["nome"]))
            self._table.setItem(i, 1,  _read_only_item(f"{p['comprimento']:.0f}"))
            self._table.setItem(i, 2,  _read_only_item(f"{p['largura']:.0f}"))
            self._table.setItem(i, 3,  _read_only_item(f"{p['espessura']:.0f}"))
            self._table.setItem(i, 4,  _read_only_item(p["material"]))
            self._table.setItem(i, 5,  _read_only_item(str(p["quantidade"])))
            self._table.setItem(i, 6,  _read_only_item(p["metodo_corte"]))
            self._table.setItem(i, 7,  _read_only_item(_bool_icon(p["fita_sup"])))
            self._table.setItem(i, 8,  _read_only_item(_bool_icon(p["fita_inf"])))
            self._table.setItem(i, 9,  _read_only_item(_bool_icon(p["fita_esq"])))
            self._table.setItem(i, 10, _read_only_item(_bool_icon(p["fita_dir"])))
            self._table.setItem(i, 11, _read_only_item(p["veio"]))
            self._table.setItem(i, 12, _read_only_item(_bool_icon(p["pode_girar"])))

        self._table.resizeColumnsToContents()
        layout.addWidget(self._table)

        # Opções de importação
        opts_group = QtWidgets.QGroupBox("Opções")
        opts_layout = QtWidgets.QHBoxLayout(opts_group)
        self._group_checkbox = QtWidgets.QCheckBox("Criar grupo 'CSV Import' para organizar as peças")
        self._group_checkbox.setChecked(True)
        self._clear_checkbox = QtWidgets.QCheckBox("Remover peças CSV já existentes antes de importar")
        self._clear_checkbox.setChecked(False)
        opts_layout.addWidget(self._group_checkbox)
        opts_layout.addWidget(self._clear_checkbox)
        opts_layout.addStretch(1)
        layout.addWidget(opts_group)

        btn = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btn.accepted.connect(self.accept)
        btn.rejected.connect(self.reject)
        layout.addWidget(btn)

    def create_group(self):
        return self._group_checkbox.isChecked()

    def clear_existing(self):
        return self._clear_checkbox.isChecked()


# ── Criação dos objetos FreeCAD ───────────────────────────────────────────────

_CSV_IMPORT_TAG = "PanelNestCSVImport"


def _create_part_box(doc, part_data, group_obj):
    """Cria um Part::Box com propriedades PanelNest setadas."""
    import panelnest

    length = max(1.0, part_data["comprimento"])
    width  = max(1.0, part_data["largura"])
    thick  = max(1.0, part_data["espessura"]) if part_data["espessura"] > 0 else 18.0
    qty    = max(1, part_data["quantidade"])

    created = []
    for i in range(qty):
        suffix = f"_{i+1:02d}" if qty > 1 else ""
        obj_label = f"{part_data['nome']}{suffix}"

        box = doc.addObject("Part::Box", "PanelNestPart")
        box.Label  = obj_label
        box.Length = length
        box.Width  = width
        box.Height = thick

        # Garante propriedades PanelNest
        panelnest.ensure_part_properties(box)

        # Seta propriedades
        box.PanelNestMaterial = part_data["material"] or ""
        if hasattr(box, "PanelNestCutMethod"):
            method = part_data["metodo_corte"] or "Auto"
            try:
                box.PanelNestCutMethod = method
            except Exception:
                pass
        if hasattr(box, "PanelNestAllowRotation"):
            box.PanelNestAllowRotation = part_data["pode_girar"]
        if hasattr(box, "PanelNestGrainDirection"):
            try:
                box.PanelNestGrainDirection = part_data["veio"] or "Livre"
            except Exception:
                pass
        if hasattr(box, "PanelNestEdgeBandTop"):
            box.PanelNestEdgeBandTop    = part_data["fita_sup"]
            box.PanelNestEdgeBandBottom = part_data["fita_inf"]
            box.PanelNestEdgeBandLeft   = part_data["fita_esq"]
            box.PanelNestEdgeBandRight  = part_data["fita_dir"]

        # Marca como importado via CSV (propriedade string simples)
        try:
            box.addProperty("App::PropertyString", _CSV_IMPORT_TAG, "PanelNest", "Peca importada via CSV pelo PanelNest")
        except Exception:
            pass  # já existe
        try:
            setattr(box, _CSV_IMPORT_TAG, "1")
        except Exception:
            pass

        if group_obj is not None:
            group_obj.addObject(box)

        created.append(box)

    return created


def _arrange_parts_grid(objects, gap=50.0, cols=5):
    """Distribui objetos em grade para não ficarem sobrepostos."""
    if not objects:
        return
    try:
        import FreeCAD as _App
        x, y = 0.0, 0.0
        col = 0
        row_height = 0.0
        for obj in objects:
            try:
                bb = obj.Shape.BoundBox
                w = bb.XLength
                h = bb.YLength
            except Exception:
                w = getattr(obj, "Length", 100.0)
                h = getattr(obj, "Width", 100.0)
            obj.Placement = _App.Placement(
                _App.Vector(x, y, 0),
                _App.Rotation(0, 0, 0, 1),
            )
            row_height = max(row_height, h)
            x += w + gap
            col += 1
            if col >= cols:
                col = 0
                x = 0.0
                y += row_height + gap
                row_height = 0.0
    except Exception:
        pass


def _remove_existing_csv_parts(doc):
    """Remove objetos marcados como importados via CSV."""
    to_remove = []
    for obj in doc.Objects:
        try:
            if getattr(obj, _CSV_IMPORT_TAG, "") == "1":
                to_remove.append(obj.Name)
        except Exception:
            pass
    for name in to_remove:
        try:
            doc.removeObject(name)
        except Exception:
            pass


def import_parts_from_csv(filepath, create_group=True, clear_existing=False):
    """Lê o CSV e cria os objetos no documento ativo. Retorna (count, errors)."""
    fieldnames, rows, read_error = _read_csv_rows(filepath)
    if read_error:
        raise ValueError(read_error)

    parts, parse_errors = _parse_rows(fieldnames, rows)
    if parts is None:
        raise ValueError(parse_errors)

    doc = App.ActiveDocument if App is not None else None
    if doc is None:
        raise ValueError("Nenhum documento FreeCAD aberto.")

    if clear_existing:
        _remove_existing_csv_parts(doc)

    group_obj = None
    if create_group:
        group_name = f"CSV_Import_{os.path.splitext(os.path.basename(filepath))[0]}"
        group_obj = doc.addObject("App::Part", "CSVImportGroup")
        group_obj.Label = group_name

    total = 0
    all_created = []
    for part_data in parts:
        created = _create_part_box(doc, part_data, group_obj)
        all_created.extend(created)
        total += len(created)

    doc.recompute()
    _arrange_parts_grid(all_created)
    doc.recompute()
    return total, parse_errors


# ── Comando FreeCAD ───────────────────────────────────────────────────────────

class ImportPartsCSVCommand:
    def Activated(self):
        if App is None or App.ActiveDocument is None:
            QtWidgets.QMessageBox.warning(
                None, "PanelNest",
                "Abra ou crie um documento FreeCAD antes de importar peças."
            )
            return

        # Seleciona arquivo
        parent = Gui.getMainWindow() if Gui is not None and hasattr(Gui, "getMainWindow") else None
        filepath, _ = QtWidgets.QFileDialog.getOpenFileName(
            parent,
            "Importar Peças — Selecione o CSV",
            os.path.expanduser("~"),
            "CSV (*.csv *.txt);;Todos os arquivos (*)",
        )
        if not filepath:
            return

        # Lê e parseia
        fieldnames, rows, read_error = _read_csv_rows(filepath)
        if read_error:
            QtWidgets.QMessageBox.critical(parent, "PanelNest: Erro ao ler CSV", read_error)
            return

        parts, parse_errors = _parse_rows(fieldnames, rows)
        if parts is None:
            # Detecção automática falhou — abre dialog de mapeamento manual
            map_dlg = ColumnMappingDialog(fieldnames, rows, filepath, parent=parent)
            if _exec_dialog(map_dlg) != QtWidgets.QDialog.Accepted:
                return
            fieldnames, rows = map_dlg.remapped_fieldnames_and_rows()
            parts, parse_errors = _parse_rows(fieldnames, rows)
            if parts is None:
                QtWidgets.QMessageBox.critical(parent, "PanelNest: CSV inválido", parse_errors)
                return

        if not parts:
            QtWidgets.QMessageBox.warning(
                parent, "PanelNest: CSV vazio",
                "Nenhuma peça válida encontrada no arquivo.\n\n"
                + ("\n".join(parse_errors) if parse_errors else "Verifique os cabeçalhos do CSV.")
            )
            return

        # Dialog de preview
        dlg = CSVPreviewDialog(parts, parse_errors, filepath, parent=parent)
        if _exec_dialog(dlg) != QtWidgets.QDialog.Accepted:
            return

        create_group = dlg.create_group()
        clear_existing = dlg.clear_existing()

        # Cria objetos
        doc = App.ActiveDocument
        if clear_existing:
            _remove_existing_csv_parts(doc)

        group_obj = None
        if create_group:
            group_label = f"CSV — {os.path.splitext(os.path.basename(filepath))[0]}"
            group_obj = doc.addObject("App::Part", "CSVImportGroup")
            group_obj.Label = group_label

        import panelnest
        total = 0
        all_created = []
        for part_data in parts:
            created = _create_part_box(doc, part_data, group_obj)
            all_created.extend(created)
            total += len(created)

        doc.recompute()
        _arrange_parts_grid(all_created)
        doc.recompute()

        msg = f"PanelNest: {total} peça(s) importada(s) do CSV '{os.path.basename(filepath)}'."
        if parse_errors:
            msg += f" {len(parse_errors)} linha(s) ignorada(s)."
        if App is not None:
            App.Console.PrintMessage(msg + "\n")

        QtWidgets.QMessageBox.information(
            parent, "PanelNest: Importação concluída",
            f"{total} peça(s) criada(s) com sucesso.\n\n"
            "Próximos passos:\n"
            "1. Etiquetar Peças (atribui IDs)\n"
            "2. Aplicar Dados (confirma materiais)\n"
            "3. Organizar Peças\n"
            "4. Configurar Chapa → Gerar Layout"
            + (f"\n\n{len(parse_errors)} linha(s) ignorada(s) — verifique o log." if parse_errors else "")
        )

    def IsActive(self):
        if App is None:
            return False
        return App.ActiveDocument is not None

    def GetResources(self):
        return {
            "Pixmap": os.path.join(
                os.path.dirname(__file__), "..", "resources", "icons", "panelnest_import_csv.svg"
            ),
            "Accel": "",
            "MenuText": "Importar Peças do CSV",
            "ToolTip": (
                "Importa uma lista de peças de um arquivo CSV e cria os objetos no documento.\n"
                "Colunas: nome, comprimento, largura, espessura, material, quantidade, ..."
            ),
        }


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, ImportPartsCSVCommand())
