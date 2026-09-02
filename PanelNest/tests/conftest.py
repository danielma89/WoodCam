"""Stubs centralizados para rodar testes sem FreeCAD instalado."""
import sys
import os

# Adiciona raiz do projeto ao path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Stub dos módulos FreeCAD antes de qualquer import de panelnest
for mod in ["FreeCAD", "FreeCADGui", "Part", "Draft", "Mesh"]:
    if mod not in sys.modules:
        sys.modules[mod] = None  # type: ignore
