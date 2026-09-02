"""Gera um ZIP portátil da bancada única PanelNest/WoodCAM."""

import argparse
from pathlib import Path
import zipfile


UNIFIED_ROOT = Path(__file__).resolve().parents[2]
IGNORED_PARTS = {
    ".git",
    ".github",
    ".pytest_cache",
    ".claude",
    ".vscode",
    "__pycache__",
    ".codex",
    ".agents",
    "backups",
    "dist",
    "local_ai",
    "output",
    "outputs",
    "test-results",
    "tests",
}


def _iter_package_files(root):
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts[:3] == ("resources", "diagrams", "source"):
            continue
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if path.is_file() and path.suffix not in {".pyc", ".pyo"}:
            yield path, relative


def build_package(output_path):
    output_path = Path(output_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    install_text = """PanelNest / WoodCAM — bancada única

1. Feche o FreeCAD.
2. Extraia a pasta PanelNest deste ZIP dentro da pasta Mod do FreeCAD.

Windows: %APPDATA%\\FreeCAD\\Mod\\PanelNest
Linux:   ~/.local/share/FreeCAD/Mod/PanelNest

3. Abra o FreeCAD e selecione a bancada PanelNest.
4. O Editor 2D e o CAM ficam em PanelNest > CAM > WoodCAM 2D.

Não é necessário instalar uma pasta WoodCAM2D separada.
"""
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source, relative in _iter_package_files(UNIFIED_ROOT):
            if source.resolve() == output_path:
                continue
            archive.write(source, Path("PanelNest") / relative)
        archive.writestr("LEIA-ME-INSTALACAO.txt", install_text)
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Gera um ZIP com a bancada única PanelNest/WoodCAM."
    )
    parser.add_argument(
        "--output",
        default=str(UNIFIED_ROOT / "dist" / "PanelNest-WoodCAM.zip"),
        help="Arquivo ZIP de saída.",
    )
    args = parser.parse_args()
    print(build_package(args.output))


if __name__ == "__main__":
    main()
