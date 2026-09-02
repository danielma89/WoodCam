"""Testes de utilitários de planilhas (sem FreeCAD)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

for mod in ["FreeCAD", "FreeCADGui", "Part"]:
    if mod not in sys.modules:
        sys.modules[mod] = None  # type: ignore

from panelnest.metadata import _format_mm, _format_m2, _format_m, _format_percent


def test_format_mm_integer():
    assert _format_mm(600) == "600"


def test_format_mm_decimal():
    result = _format_mm(600.5)
    assert "600" in result and "5" in result  # separador pode variar por locale


def test_format_mm_round():
    assert _format_mm(18.0) == "18"


def test_format_m2():
    result = _format_m2(1_000_000)
    assert "1" in result


def test_format_m2_small():
    result = _format_m2(250_000)
    assert "0,25" in result or "0.25" in result


def test_format_m_meters():
    result = _format_m(1000)
    assert "1" in result


def test_format_percent_half():
    result = _format_percent(0.5)
    assert "50" in result


def test_format_percent_full():
    result = _format_percent(1.0)
    assert "100" in result


def test_format_percent_zero():
    result = _format_percent(0.0)
    assert "0" in result
