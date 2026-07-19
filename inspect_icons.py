#!/usr/bin/env python3
"""Script para inspecionar ícones PNG e verificar dimensões/transparência."""
from PIL import Image
import os

files_to_inspect = [
    "2D.png",
    "tab_cut_profile.png",
    "tab_holes.png",
    "tab_pocket.png",
    "tab_rough3d.png",
    "tab_finish3d.png",
    "trabalho.png",
    "material.png",
    "cut.png",
    "furo.png",
    "bolso.png",
    "rough3d.png",
    "finish3d.png",
]

base_path = "/home/danielma/Projetos/CNC Marcenaria/resources/diagrams"

print(f"{'Arquivo':<25} {'Dimensões':<15} {'Modo':<10} {'Transparência':<15}")
print("-" * 70)

for filename in files_to_inspect:
    filepath = os.path.join(base_path, filename)
    if os.path.exists(filepath):
        img = Image.open(filepath)
        has_transparency = img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info)
        size_str = f"{img.size[0]}x{img.size[1]}"
        print(f"{filename:<25} {size_str:<15} {img.mode:<10} {'Sim' if has_transparency else 'Não':<15}")
    else:
        print(f"{filename:<25} NAO ENCONTRADO{'':<30}")
