"""Testes para panelnest.production_tracking."""

import sqlite3

from panelnest.production_tracking import (
    PRODUCTION_STATES,
    STATE_COLORS,
    open_production_db,
    _ensure_schema,
    create_order,
    load_orders,
    update_order_status,
    delete_order,
    add_parts_to_order,
    load_order_parts,
    update_part_state,
    batch_update_state,
    load_part_history,
    order_summary,
    project_dashboard,
)


def _in_memory_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_schema(conn)
    return conn


def test_create_and_load_order():
    conn = _in_memory_db()
    oid = create_order(conn, "TestProject", "notes here")
    assert oid > 0
    orders = load_orders(conn, "TestProject")
    assert len(orders) == 1
    assert orders[0].project_name == "TestProject"
    assert orders[0].notes == "notes here"
    assert orders[0].status == "Aberta"
    conn.close()


def test_load_orders_filter():
    conn = _in_memory_db()
    create_order(conn, "ProjectA")
    create_order(conn, "ProjectB")
    create_order(conn, "ProjectA")
    assert len(load_orders(conn, "ProjectA")) == 2
    assert len(load_orders(conn, "ProjectB")) == 1
    assert len(load_orders(conn)) == 3
    conn.close()


def test_update_order_status():
    conn = _in_memory_db()
    oid = create_order(conn, "P")
    update_order_status(conn, oid, "Concluida")
    orders = load_orders(conn, "P")
    assert orders[0].status == "Concluida"
    conn.close()


def test_delete_order_cascades():
    conn = _in_memory_db()
    oid = create_order(conn, "P")
    add_parts_to_order(conn, oid, [
        {"part_id": "PN-001", "label": "Lateral", "quantity": 1}
    ])
    assert len(load_order_parts(conn, oid)) == 1
    delete_order(conn, oid)
    assert len(load_orders(conn)) == 0
    assert len(load_order_parts(conn, oid)) == 0
    conn.close()


def test_add_parts_dict():
    conn = _in_memory_db()
    oid = create_order(conn, "P")
    add_parts_to_order(conn, oid, [
        {"part_id": "PN-001", "label": "Lateral", "material": "MDF",
         "thickness_mm": 18, "length_mm": 600, "width_mm": 400, "quantity": 2},
        {"part_id": "PN-002", "label": "Tampo", "quantity": 1},
    ])
    parts = load_order_parts(conn, oid)
    assert len(parts) == 2
    assert parts[0].state == "Pendente"
    assert parts[0].quantity == 2
    conn.close()


def test_update_part_state_records_history():
    conn = _in_memory_db()
    oid = create_order(conn, "P")
    add_parts_to_order(conn, oid, [
        {"part_id": "PN-001", "label": "Lateral", "quantity": 1}
    ])
    parts = load_order_parts(conn, oid)
    rid = parts[0].row_id

    update_part_state(conn, rid, "Cortada", "Joao")
    update_part_state(conn, rid, "Fitada", "Maria")

    parts = load_order_parts(conn, oid)
    assert parts[0].state == "Fitada"
    assert parts[0].operator == "Maria"

    history = load_part_history(conn, rid)
    assert len(history) == 2
    assert history[0]["old_state"] == "Pendente"
    assert history[0]["new_state"] == "Cortada"
    assert history[1]["old_state"] == "Cortada"
    assert history[1]["new_state"] == "Fitada"
    conn.close()


def test_same_state_no_history():
    conn = _in_memory_db()
    oid = create_order(conn, "P")
    add_parts_to_order(conn, oid, [
        {"part_id": "PN-001", "label": "L", "quantity": 1}
    ])
    parts = load_order_parts(conn, oid)
    update_part_state(conn, parts[0].row_id, "Pendente")  # same state
    assert len(load_part_history(conn, parts[0].row_id)) == 0
    conn.close()


def test_batch_update():
    conn = _in_memory_db()
    oid = create_order(conn, "P")
    add_parts_to_order(conn, oid, [
        {"part_id": f"PN-{i:03d}", "label": f"Part {i}", "quantity": 1}
        for i in range(5)
    ])
    parts = load_order_parts(conn, oid)
    rids = [p.row_id for p in parts]
    batch_update_state(conn, rids[:3], "Cortada", "Op1")
    parts = load_order_parts(conn, oid)
    states = [p.state for p in parts]
    assert states.count("Cortada") == 3
    assert states.count("Pendente") == 2
    conn.close()


def test_order_summary():
    conn = _in_memory_db()
    oid = create_order(conn, "P")
    add_parts_to_order(conn, oid, [
        {"part_id": "PN-001", "label": "A", "quantity": 3},
        {"part_id": "PN-002", "label": "B", "quantity": 2},
    ])
    parts = load_order_parts(conn, oid)
    update_part_state(conn, parts[0].row_id, "Cortada")

    summ = order_summary(conn, oid)
    assert summ["total_parts"] == 2
    assert summ["total_quantity"] == 5
    assert summ["by_state"]["Cortada"]["quantity"] == 3
    assert summ["by_state"]["Pendente"]["quantity"] == 2
    conn.close()


def test_project_dashboard():
    conn = _in_memory_db()
    oid1 = create_order(conn, "P")
    oid2 = create_order(conn, "P")
    add_parts_to_order(conn, oid1, [
        {"part_id": "PN-001", "label": "A", "quantity": 10},
    ])
    add_parts_to_order(conn, oid2, [
        {"part_id": "PN-002", "label": "B", "quantity": 5},
    ])
    # Deliver all of order 1
    parts1 = load_order_parts(conn, oid1)
    update_part_state(conn, parts1[0].row_id, "Entregue")
    update_order_status(conn, oid1, "Concluida")

    dash = project_dashboard(conn, "P")
    assert dash["active_orders"] == 1  # only oid2
    assert dash["completed_orders"] == 1
    assert dash["total_pieces"] == 5  # only active orders
    assert dash["delivered_pieces"] == 0
    conn.close()


def test_all_states_have_colors():
    for state in PRODUCTION_STATES:
        assert state in STATE_COLORS
