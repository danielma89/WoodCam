"""
Banco de retalhos persistente (SQLite) para PanelNest.
Os retalhos são salvos em ~/.local/share/PanelNest/remnants.db e sobrevivem
entre sessões e documentos do FreeCAD.
"""
import os
import sqlite3
from datetime import datetime, timedelta, timezone


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


_DB_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "PanelNest")
_DB_PATH = os.path.join(_DB_DIR, "remnants.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS remnants (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT NOT NULL,
    length_mm   REAL NOT NULL,
    width_mm    REAL NOT NULL,
    thickness_mm REAL NOT NULL DEFAULT 0.0,
    material    TEXT NOT NULL DEFAULT '',
    source_project TEXT NOT NULL DEFAULT '',
    source_sheet   TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    used_at     TEXT,
    notes       TEXT NOT NULL DEFAULT ''
);
"""


def open_remnant_db() -> sqlite3.Connection:
    """Abre (ou cria) o banco em ~/.local/share/PanelNest/remnants.db.
    Retorna uma conexão SQLite. O chamador é responsável por fechar."""
    try:
        os.makedirs(_DB_DIR, exist_ok=True)
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        conn.commit()
        return conn
    except Exception as exc:
        # Fallback: banco em memória para não travar o FreeCAD
        import warnings
        warnings.warn(f"PanelNest: não foi possível abrir banco de retalhos em {_DB_PATH}: {exc}. Usando banco em memória.")
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.commit()
        return conn


def save_remnant(conn: sqlite3.Connection, remnant, project_name: str = "") -> int:
    """Salva um GeneratedRemnant no banco. Retorna o rowid inserido ou atualizado.

    Se já existir um retalho não-usado com mesmas dimensões, material e projeto,
    atualiza o registro em vez de duplicar (evita acúmulo ao regerar o layout).
    """
    now = _now_iso()
    label = getattr(remnant, "label", "")
    length_mm = float(getattr(remnant, "length_mm", 0))
    width_mm = float(getattr(remnant, "width_mm", 0))
    thickness_mm = float(getattr(remnant, "thickness_mm", 0))
    material = getattr(remnant, "material", "")
    source_sheet = getattr(remnant, "source_label", "")

    # Verificar se já existe entrada idêntica não-usada do mesmo projeto
    existing = conn.execute(
        """
        SELECT id FROM remnants
        WHERE used_at IS NULL
          AND source_project = ?
          AND ABS(length_mm - ?) < 0.5
          AND ABS(width_mm - ?) < 0.5
          AND ABS(thickness_mm - ?) < 0.1
          AND LOWER(material) = LOWER(?)
        LIMIT 1
        """,
        (project_name, length_mm, width_mm, thickness_mm, material),
    ).fetchone()

    if existing:
        # Atualiza label e data (pode ter mudado o nome da chapa de origem)
        conn.execute(
            "UPDATE remnants SET label = ?, source_sheet = ?, created_at = ? WHERE id = ?",
            (label, source_sheet, now, existing[0]),
        )
        conn.commit()
        return existing[0]

    cur = conn.execute(
        """
        INSERT INTO remnants
            (label, length_mm, width_mm, thickness_mm, material,
             source_project, source_sheet, created_at, used_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, '')
        """,
        (label, length_mm, width_mm, thickness_mm, material, project_name, source_sheet, now),
    )
    conn.commit()
    return cur.lastrowid


def load_remnants(
    conn: sqlite3.Connection,
    material: str = None,
    min_area_mm2: float = 0.0,
    thickness_mm: float = None,
    thickness_tolerance_mm: float = 0.75,
) -> list:
    """Retorna retalhos disponíveis (não usados) como lista de dicts.

    Filtros opcionais:
    - material: filtrar por material (case-insensitive, substring)
    - min_area_mm2: área mínima do retalho
    - thickness_mm: espessura exata (com tolerância)
    - thickness_tolerance_mm: tolerância para espessura
    """
    query = """
        SELECT id, label, length_mm, width_mm, thickness_mm, material,
               source_project, source_sheet, created_at, notes
        FROM remnants
        WHERE used_at IS NULL
          AND (length_mm * width_mm) >= ?
    """
    params = [max(0.0, min_area_mm2)]

    if material:
        query += " AND LOWER(material) LIKE ?"
        params.append(f"%{material.lower()}%")

    if thickness_mm is not None:
        query += " AND ABS(thickness_mm - ?) <= ?"
        params.extend([float(thickness_mm), float(thickness_tolerance_mm)])

    query += " ORDER BY (length_mm * width_mm) DESC"

    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def mark_remnant_used(conn: sqlite3.Connection, rowid: int) -> None:
    """Marca um retalho como utilizado (soft-delete)."""
    now = _now_iso()
    conn.execute("UPDATE remnants SET used_at = ? WHERE id = ?", (now, rowid))
    conn.commit()


def delete_remnant(conn: sqlite3.Connection, rowid: int) -> None:
    """Remove permanentemente um retalho do banco."""
    conn.execute("DELETE FROM remnants WHERE id = ?", (rowid,))
    conn.commit()


def purge_old_remnants(conn: sqlite3.Connection, days: int = 180) -> int:
    """Remove permanentemente retalhos com used_at mais antigo que `days` dias.
    Retorna o número de registros removidos."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cur = conn.execute(
        "DELETE FROM remnants WHERE used_at IS NOT NULL AND used_at < ?", (cutoff,)
    )
    conn.commit()
    return cur.rowcount


def count_available_remnants(conn: sqlite3.Connection) -> int:
    """Retorna quantos retalhos disponíveis (não usados) existem no banco."""
    row = conn.execute("SELECT COUNT(*) FROM remnants WHERE used_at IS NULL").fetchone()
    return row[0] if row else 0


def remnant_db_path() -> str:
    """Retorna o caminho do arquivo do banco de dados."""
    return _DB_PATH
