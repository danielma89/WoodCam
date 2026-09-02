"""Rastreio de produção para PanelNest.

Acompanha o status de cada peça no fluxo de manufatura:
    Pendente → Cortada → Fitada → Furada → Montada → Entregue

Dados persistidos em SQLite em ~/.local/share/PanelNest/production.db
Cada projeto tem suas ordens de produção, identificadas pelo nome do documento.
"""

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime


# ---------------------------------------------------------------------------
# Estados de produção
# ---------------------------------------------------------------------------

PRODUCTION_STATES = [
    "Pendente",
    "Cortada",
    "Fitada",
    "Furada",
    "Montada",
    "Entregue",
]

STATE_COLORS = {
    "Pendente": "#95a5a6",   # cinza
    "Cortada": "#e67e22",    # laranja
    "Fitada": "#f1c40f",     # amarelo
    "Furada": "#3498db",     # azul
    "Montada": "#2ecc71",    # verde
    "Entregue": "#27ae60",   # verde escuro
}


@dataclass
class ProductionOrder:
    """Ordem de produção de um projeto."""
    order_id: int
    project_name: str
    created_at: str       # ISO 8601
    notes: str = ""
    status: str = "Aberta"  # "Aberta", "Em andamento", "Concluida"


@dataclass
class ProductionPartStatus:
    """Status de produção de uma peça."""
    row_id: int
    order_id: int
    part_id: str
    label: str
    material: str
    thickness_mm: float
    length_mm: float
    width_mm: float
    quantity: int
    state: str              # um de PRODUCTION_STATES
    updated_at: str         # ISO 8601
    notes: str = ""
    operator: str = ""


# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------

_DB_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "PanelNest")


def _db_path():
    os.makedirs(_DB_DIR, exist_ok=True)
    return os.path.join(_DB_DIR, "production.db")


def open_production_db():
    """Abre (ou cria) o banco de produção. Retorna conexão sqlite3."""
    conn = sqlite3.connect(_db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS production_orders (
            order_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name  TEXT NOT NULL,
            created_at    TEXT NOT NULL,
            notes         TEXT DEFAULT '',
            status        TEXT DEFAULT 'Aberta'
        );

        CREATE TABLE IF NOT EXISTS production_parts (
            row_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id      INTEGER NOT NULL REFERENCES production_orders(order_id) ON DELETE CASCADE,
            part_id       TEXT NOT NULL,
            label         TEXT NOT NULL,
            material      TEXT DEFAULT '',
            thickness_mm  REAL DEFAULT 0,
            length_mm     REAL DEFAULT 0,
            width_mm      REAL DEFAULT 0,
            quantity      INTEGER DEFAULT 1,
            state         TEXT DEFAULT 'Pendente',
            updated_at    TEXT NOT NULL,
            notes         TEXT DEFAULT '',
            operator      TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS production_history (
            history_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            row_id        INTEGER NOT NULL REFERENCES production_parts(row_id) ON DELETE CASCADE,
            old_state     TEXT NOT NULL,
            new_state     TEXT NOT NULL,
            changed_at    TEXT NOT NULL,
            operator      TEXT DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_parts_order ON production_parts(order_id);
        CREATE INDEX IF NOT EXISTS idx_parts_state ON production_parts(state);
        CREATE INDEX IF NOT EXISTS idx_history_part ON production_history(row_id);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Ordens de produção
# ---------------------------------------------------------------------------

def create_order(conn, project_name, notes=""):
    """Cria uma nova ordem de produção. Retorna order_id."""
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO production_orders (project_name, created_at, notes) VALUES (?, ?, ?)",
        (project_name, now, notes),
    )
    conn.commit()
    return cur.lastrowid


def load_orders(conn, project_name=None):
    """Carrega ordens. Filtra por projeto se especificado."""
    if project_name:
        rows = conn.execute(
            "SELECT order_id, project_name, created_at, notes, status "
            "FROM production_orders WHERE project_name = ? ORDER BY created_at DESC",
            (project_name,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT order_id, project_name, created_at, notes, status "
            "FROM production_orders ORDER BY created_at DESC"
        ).fetchall()
    return [
        ProductionOrder(
            order_id=r[0], project_name=r[1], created_at=r[2],
            notes=r[3] or "", status=r[4] or "Aberta"
        )
        for r in rows
    ]


def update_order_status(conn, order_id, status):
    """Atualiza o status da ordem (Aberta, Em andamento, Concluida)."""
    conn.execute(
        "UPDATE production_orders SET status = ? WHERE order_id = ?",
        (status, order_id),
    )
    conn.commit()


def delete_order(conn, order_id):
    """Remove ordem e todas as peças associadas."""
    conn.execute("DELETE FROM production_orders WHERE order_id = ?", (order_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Peças da ordem
# ---------------------------------------------------------------------------

def add_parts_to_order(conn, order_id, parts):
    """Adiciona peças (lista de PanelPart) a uma ordem.

    parts: lista de PanelPart ou dicts com {part_id, label, material, thickness_mm, length_mm, width_mm, quantity}
    """
    now = datetime.now().isoformat(timespec="seconds")
    for p in parts:
        if hasattr(p, "part_id"):
            vals = (
                order_id, p.part_id, p.label, p.material,
                p.thickness_mm, p.length_mm, p.width_mm, p.quantity,
                "Pendente", now,
            )
        else:
            vals = (
                order_id, p["part_id"], p["label"], p.get("material", ""),
                p.get("thickness_mm", 0), p.get("length_mm", 0),
                p.get("width_mm", 0), p.get("quantity", 1),
                "Pendente", now,
            )
        conn.execute(
            "INSERT INTO production_parts "
            "(order_id, part_id, label, material, thickness_mm, length_mm, width_mm, "
            "quantity, state, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            vals,
        )
    conn.commit()


def load_order_parts(conn, order_id):
    """Carrega peças de uma ordem."""
    rows = conn.execute(
        "SELECT row_id, order_id, part_id, label, material, thickness_mm, "
        "length_mm, width_mm, quantity, state, updated_at, notes, operator "
        "FROM production_parts WHERE order_id = ? ORDER BY part_id",
        (order_id,),
    ).fetchall()
    return [
        ProductionPartStatus(
            row_id=r[0], order_id=r[1], part_id=r[2], label=r[3],
            material=r[4] or "", thickness_mm=r[5], length_mm=r[6],
            width_mm=r[7], quantity=r[8], state=r[9],
            updated_at=r[10], notes=r[11] or "", operator=r[12] or "",
        )
        for r in rows
    ]


def update_part_state(conn, row_id, new_state, operator=""):
    """Atualiza o estado de uma peça e registra no histórico."""
    now = datetime.now().isoformat(timespec="seconds")

    # Buscar estado atual
    row = conn.execute(
        "SELECT state FROM production_parts WHERE row_id = ?", (row_id,)
    ).fetchone()
    if row is None:
        return
    old_state = row[0]
    if old_state == new_state:
        return

    # Atualizar peça
    conn.execute(
        "UPDATE production_parts SET state = ?, updated_at = ?, operator = ? WHERE row_id = ?",
        (new_state, now, operator, row_id),
    )

    # Registrar no histórico
    conn.execute(
        "INSERT INTO production_history (row_id, old_state, new_state, changed_at, operator) "
        "VALUES (?, ?, ?, ?, ?)",
        (row_id, old_state, new_state, now, operator),
    )
    conn.commit()


def batch_update_state(conn, row_ids, new_state, operator=""):
    """Atualiza múltiplas peças de uma vez."""
    for row_id in row_ids:
        update_part_state(conn, row_id, new_state, operator)


def load_part_history(conn, row_id):
    """Carrega o histórico de mudanças de estado de uma peça."""
    rows = conn.execute(
        "SELECT history_id, row_id, old_state, new_state, changed_at, operator "
        "FROM production_history WHERE row_id = ? ORDER BY changed_at",
        (row_id,),
    ).fetchall()
    return [
        {
            "history_id": r[0], "row_id": r[1],
            "old_state": r[2], "new_state": r[3],
            "changed_at": r[4], "operator": r[5] or "",
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Resumo / Dashboard
# ---------------------------------------------------------------------------

def order_summary(conn, order_id):
    """Retorna resumo de uma ordem: contagem por estado."""
    rows = conn.execute(
        "SELECT state, COUNT(*), SUM(quantity) FROM production_parts "
        "WHERE order_id = ? GROUP BY state",
        (order_id,),
    ).fetchall()
    summary = {state: {"parts": 0, "quantity": 0} for state in PRODUCTION_STATES}
    total_parts = 0
    total_qty = 0
    for state, count, qty in rows:
        if state in summary:
            summary[state] = {"parts": count, "quantity": int(qty or 0)}
            total_parts += count
            total_qty += int(qty or 0)
    return {
        "by_state": summary,
        "total_parts": total_parts,
        "total_quantity": total_qty,
    }


def project_dashboard(conn, project_name):
    """Retorna dados do painel para um projeto: ordens ativas, progresso global."""
    orders = load_orders(conn, project_name)
    active = [o for o in orders if o.status != "Concluida"]
    completed = [o for o in orders if o.status == "Concluida"]

    # Progresso global das ordens ativas
    total = 0
    done = 0
    for order in active:
        parts = load_order_parts(conn, order.order_id)
        for p in parts:
            total += p.quantity
            if p.state == "Entregue":
                done += p.quantity

    return {
        "active_orders": len(active),
        "completed_orders": len(completed),
        "total_pieces": total,
        "delivered_pieces": done,
        "progress_pct": round(done / total * 100, 1) if total > 0 else 0.0,
    }
