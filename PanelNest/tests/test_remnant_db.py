"""Testes do banco de retalhos SQLite (in-memory)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

for mod in ["FreeCAD", "FreeCADGui", "Part"]:
    if mod not in sys.modules:
        sys.modules[mod] = None  # type: ignore

import sqlite3
from panelnest.remnant_db import (
    open_remnant_db, save_remnant, load_remnants,
    mark_remnant_used, delete_remnant, purge_old_remnants,
    count_available_remnants,
)
from panelnest.models import GeneratedRemnant


def _in_memory_conn():
    """Cria uma conexão in-memory para testes."""
    import panelnest.remnant_db as rdb
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(rdb._SCHEMA)
    conn.commit()
    return conn


def _make_remnant(**kwargs):
    defaults = dict(
        group_id="g1", sheet_index=0,
        source_label="Chapa 1", source_kind="Chapa inteira",
        label="[Gerado] 500×300",
        x_mm=0, y_mm=0, length_mm=500, width_mm=300,
        area_mm2=150000, material="MDF", thickness_mm=18.0,
        cut_method="CNC", layout_strategy="MaxRects - BSSF",
    )
    defaults.update(kwargs)
    return GeneratedRemnant(**defaults)


def test_save_and_load():
    conn = _in_memory_conn()
    r = _make_remnant()
    rowid = save_remnant(conn, r, project_name="TestProject")
    assert rowid > 0

    rows = load_remnants(conn)
    assert len(rows) == 1
    assert rows[0]["label"] == "[Gerado] 500×300"
    assert rows[0]["material"] == "MDF"
    assert rows[0]["source_project"] == "TestProject"


def test_load_filters_by_material():
    conn = _in_memory_conn()
    save_remnant(conn, _make_remnant(material="MDF", label="r1"))
    save_remnant(conn, _make_remnant(material="MDP", label="r2"))

    mdf_rows = load_remnants(conn, material="MDF")
    assert len(mdf_rows) == 1
    assert mdf_rows[0]["material"] == "MDF"


def test_load_filters_by_min_area():
    conn = _in_memory_conn()
    save_remnant(conn, _make_remnant(length_mm=100, width_mm=100, area_mm2=10000))
    save_remnant(conn, _make_remnant(length_mm=500, width_mm=500, area_mm2=250000))

    rows = load_remnants(conn, min_area_mm2=50000)
    assert len(rows) == 1
    assert rows[0]["length_mm"] == 500


def test_load_filters_by_thickness():
    conn = _in_memory_conn()
    save_remnant(conn, _make_remnant(thickness_mm=18.0))
    save_remnant(conn, _make_remnant(thickness_mm=15.0))

    rows = load_remnants(conn, thickness_mm=18.0, thickness_tolerance_mm=0.5)
    assert len(rows) == 1
    assert rows[0]["thickness_mm"] == 18.0


def test_mark_used_hides_from_query():
    conn = _in_memory_conn()
    rowid = save_remnant(conn, _make_remnant())
    assert count_available_remnants(conn) == 1

    mark_remnant_used(conn, rowid)
    rows = load_remnants(conn)
    assert len(rows) == 0
    assert count_available_remnants(conn) == 0


def test_delete_removes_permanently():
    conn = _in_memory_conn()
    rowid = save_remnant(conn, _make_remnant())
    delete_remnant(conn, rowid)
    rows = conn.execute("SELECT * FROM remnants WHERE id = ?", (rowid,)).fetchall()
    assert len(rows) == 0


def test_purge_old_used_remnants():
    conn = _in_memory_conn()
    from datetime import datetime, timedelta, timezone

    # Inserir um retalho "usado há 200 dias"
    old_date = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    conn.execute(
        "INSERT INTO remnants (label, length_mm, width_mm, thickness_mm, material, "
        "source_project, source_sheet, created_at, used_at, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("[Gerado] old", 400, 200, 18, "MDF", "", "", old_date, old_date, ""),
    )
    conn.commit()

    # Inserir um retalho disponível
    save_remnant(conn, _make_remnant())

    removed = purge_old_remnants(conn, days=180)
    assert removed == 1
    assert count_available_remnants(conn) == 1


def test_multiple_saves_ordered_by_area():
    conn = _in_memory_conn()
    save_remnant(conn, _make_remnant(length_mm=200, width_mm=100, area_mm2=20000))
    save_remnant(conn, _make_remnant(length_mm=600, width_mm=400, area_mm2=240000))
    save_remnant(conn, _make_remnant(length_mm=400, width_mm=300, area_mm2=120000))

    rows = load_remnants(conn)
    areas = [r["length_mm"] * r["width_mm"] for r in rows]
    assert areas == sorted(areas, reverse=True)
