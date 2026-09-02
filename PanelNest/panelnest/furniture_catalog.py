"""Catálogo pessoal de móveis do PanelNest.

Cada modelo é uma pasta com:
  <categoria>/<nome>/
      model.FCStd      — cópia do arquivo FreeCAD original
      thumbnail.png    — foto representativa (opcional, escolhida pelo usuário)
      meta.json        — nome, descrição, data de adição

Raiz do catálogo: ~/.local/share/PanelNest/catalog/
"""

from dataclasses import dataclass, field
import os as _os
import json as _json
import re as _re
import shutil as _shutil
from datetime import datetime as _dt


# ---------------------------------------------------------------------------
# Estrutura de dados
# ---------------------------------------------------------------------------

@dataclass
class CatalogEntry:
    name: str
    category: str
    description: str
    folder: str           # caminho completo da pasta do modelo
    fcstd_path: str       # <folder>/model.FCStd
    thumbnail_path: str   # <folder>/thumbnail.* (vazio se não tiver)
    date_added: str       # ISO 8601


# ---------------------------------------------------------------------------
# Diretórios
# ---------------------------------------------------------------------------

def catalog_root() -> str:
    base = _os.path.join(_os.path.expanduser("~"), ".local", "share", "PanelNest", "catalog")
    _os.makedirs(base, exist_ok=True)
    return base


def _slug(name: str) -> str:
    return _re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "sem_nome"


def _find_thumbnail(folder: str) -> str:
    for ext in ("png", "jpg", "jpeg", "PNG", "JPG", "JPEG"):
        p = _os.path.join(folder, f"thumbnail.{ext}")
        if _os.path.exists(p):
            return p
    return ""


# ---------------------------------------------------------------------------
# Leitura do catálogo
# ---------------------------------------------------------------------------

def load_catalog() -> list:
    """Retorna lista de CatalogEntry lendo a estrutura de pastas."""
    entries = []
    root = catalog_root()

    for cat_name in sorted(_os.listdir(root)):
        cat_path = _os.path.join(root, cat_name)
        if not _os.path.isdir(cat_path):
            continue

        for model_name in sorted(_os.listdir(cat_path)):
            folder = _os.path.join(cat_path, model_name)
            if not _os.path.isdir(folder):
                continue

            fcstd = _os.path.join(folder, "model.FCStd")
            if not _os.path.exists(fcstd):
                continue

            meta_path = _os.path.join(folder, "meta.json")
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = _json.load(f)
            except Exception:
                meta = {}

            entries.append(CatalogEntry(
                name=meta.get("name", model_name),
                category=meta.get("category", cat_name),
                description=meta.get("description", ""),
                folder=folder,
                fcstd_path=fcstd,
                thumbnail_path=_find_thumbnail(folder),
                date_added=meta.get("date_added", ""),
            ))

    return entries


def list_categories() -> list:
    """Lista categorias existentes (nomes das subpastas)."""
    root = catalog_root()
    return sorted(
        d for d in _os.listdir(root)
        if _os.path.isdir(_os.path.join(root, d))
    )


# ---------------------------------------------------------------------------
# Adição ao catálogo
# ---------------------------------------------------------------------------

def add_entry(source_fcstd: str, name: str, category: str,
              description: str = "") -> CatalogEntry:
    """Copia source_fcstd para o catálogo e cria meta.json."""
    root = catalog_root()
    cat_slug = _slug(category)
    model_slug = _slug(name)

    # Garante nome de pasta único
    base_folder = _os.path.join(root, cat_slug, model_slug)
    folder = base_folder
    suffix = 1
    while _os.path.exists(folder):
        folder = f"{base_folder}_{suffix}"
        suffix += 1

    _os.makedirs(folder)

    dst_fcstd = _os.path.join(folder, "model.FCStd")
    _shutil.copy2(source_fcstd, dst_fcstd)

    meta = {
        "name": name,
        "category": category,
        "description": description,
        "date_added": _dt.now().isoformat(timespec="seconds"),
        "source_file": _os.path.basename(source_fcstd),
    }
    with open(_os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        _json.dump(meta, f, ensure_ascii=False, indent=2)

    return CatalogEntry(
        name=name,
        category=category,
        description=description,
        folder=folder,
        fcstd_path=dst_fcstd,
        thumbnail_path="",
        date_added=meta["date_added"],
    )


def add_entry_from_selection(selection, name: str, category: str,
                              description: str = "") -> CatalogEntry:
    """Cria um FCStd temporário com os objetos selecionados e salva no catálogo.

    Objetos soltos (fora de App::Part) são automaticamente agrupados.
    """
    import FreeCAD as App
    import tempfile, time

    tmp_name = f"_pn_cat_{int(time.time())}"
    tmp_doc = App.newDocument(tmp_name)

    try:
        # Copia objetos selecionados (com dependências)
        for obj in selection:
            type_id = str(getattr(obj, "TypeId", "") or "")
            if type_id in ("App::Origin", "App::Plane", "App::Line", "App::Point"):
                continue
            try:
                tmp_doc.copyObject(obj, True)
            except Exception:
                pass

        tmp_doc.recompute()

        # Verifica se há peças soltas (fora de qualquer App::Part)
        containers = {
            obj.Name
            for obj in tmp_doc.Objects
            if getattr(obj, "TypeId", "") == "App::Part"
        }
        in_container = set()
        for obj in tmp_doc.Objects:
            if getattr(obj, "TypeId", "") == "App::Part":
                for child in getattr(obj, "OutList", []):
                    in_container.add(child.Name)

        loose = [
            obj for obj in tmp_doc.Objects
            if obj.Name not in in_container
            and getattr(obj, "TypeId", "") not in (
                "App::Part", "App::Origin", "App::Plane",
                "App::Line", "App::Point",
            )
        ]

        if loose and not containers:
            # Agrupa tudo num App::Part
            part = tmp_doc.addObject("App::Part", _slug(name))
            part.Label = name
            for obj in loose:
                part.addObject(obj)
            tmp_doc.recompute()

        # Salva em arquivo temporário
        with tempfile.NamedTemporaryFile(suffix=".FCStd", delete=False) as f:
            tmp_path = f.name

        tmp_doc.saveAs(tmp_path)

    finally:
        try:
            App.closeDocument(tmp_name)
        except Exception:
            pass

    try:
        entry = add_entry(tmp_path, name, category, description)
    finally:
        try:
            _os.unlink(tmp_path)
        except Exception:
            pass

    return entry


# ---------------------------------------------------------------------------
# Remoção
# ---------------------------------------------------------------------------

def remove_entry(entry: CatalogEntry) -> None:
    """Remove a pasta do modelo do catálogo."""
    if _os.path.isdir(entry.folder):
        _shutil.rmtree(entry.folder)
    # Remove categoria se ficou vazia
    cat_folder = _os.path.dirname(entry.folder)
    try:
        if not _os.listdir(cat_folder):
            _os.rmdir(cat_folder)
    except Exception:
        pass


def create_category(name: str) -> str:
    """Cria pasta de categoria. Retorna o caminho."""
    path = _os.path.join(catalog_root(), _slug(name))
    _os.makedirs(path, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Thumbnail
# ---------------------------------------------------------------------------

def set_thumbnail(entry: CatalogEntry, image_path: str) -> str:
    """Copia image_path para <entry.folder>/thumbnail.<ext>. Retorna novo path."""
    ext = _os.path.splitext(image_path)[1].lower() or ".png"
    dst = _os.path.join(entry.folder, f"thumbnail{ext}")
    # Remove thumbnail anterior
    for old_ext in ("png", "jpg", "jpeg", "PNG", "JPG", "JPEG"):
        old = _os.path.join(entry.folder, f"thumbnail.{old_ext}")
        if _os.path.exists(old):
            _os.remove(old)
    _shutil.copy2(image_path, dst)
    return dst
