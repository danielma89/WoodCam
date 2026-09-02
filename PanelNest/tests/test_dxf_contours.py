"""Regressões da união de contornos DXF em polilinhas fechadas."""

from panelnest.reports import _join_closed_dxf_line_contours


def _line_entity(layer, start, end):
    return [
        "0", "LINE",
        "8", layer,
        "10", str(start[0]),
        "20", str(start[1]),
        "30", "0",
        "11", str(end[0]),
        "21", str(end[1]),
        "31", "0",
    ]


def _minimal_dxf(entity_lines):
    return [
        "0", "SECTION",
        "2", "HEADER",
        "0", "ENDSEC",
        "0", "SECTION",
        "2", "ENTITIES",
        *entity_lines,
        "0", "ENDSEC",
        "0", "EOF",
    ]


def _entity_types(lines):
    return [
        lines[index + 1].strip()
        for index in range(0, len(lines) - 1, 2)
        if lines[index].strip() == "0"
    ]


def test_closed_piece_lines_become_one_closed_polyline():
    entities = []
    entities.extend(_line_entity("PN_PECAS", (0, 0), (700, 0)))
    entities.extend(_line_entity("PN_PECAS", (700, 0), (700, 100)))
    entities.extend(_line_entity("PN_PECAS", (700, 100), (0, 260)))
    entities.extend(_line_entity("PN_PECAS", (0, 260), (0, 0)))

    result = _join_closed_dxf_line_contours(_minimal_dxf(entities))
    entity_types = _entity_types(result)

    assert entity_types.count("LWPOLYLINE") == 1
    assert "LINE" not in entity_types

    polyline_index = result.index("LWPOLYLINE")
    polyline_record = result[polyline_index:polyline_index + 26]
    assert "70" in polyline_record
    assert polyline_record[polyline_record.index("70") + 1] == "1"
    assert "90" in polyline_record
    assert polyline_record[polyline_record.index("90") + 1] == "4"


def test_open_cut_line_remains_line():
    entities = _line_entity("PN_CORTES", (0, 0), (700, 0))
    result = _join_closed_dxf_line_contours(_minimal_dxf(entities))
    entity_types = _entity_types(result)

    assert entity_types.count("LINE") == 1
    assert "LWPOLYLINE" not in entity_types
