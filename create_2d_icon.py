#!/usr/bin/env python3
"""Cria um novo ícone 2D.png consistente com os demais ícones de abas."""
from PIL import Image, ImageDraw, ImageFont
import os

# Configurações baseadas no padrão dos demais ícones
OUTPUT_PATH = "/home/danielma/Projetos/CNC Marcenaria/resources/diagrams/2D.png"
SIZE = (128, 128)
BG_COLOR = (60, 60, 65)  # Azul escuro similar ao tema
ACCENT_COLOR = (99, 102, 241)  # Azul vibrante
LINE_COLOR = (147, 197, 253)  # Azul claro
HIGHLIGHT_COLOR = (255, 255, 255)  # Branco

def create_2d_icon():
    # Criar imagem com fundo transparente
    img = Image.new('RGBA', SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Centralizar o desenho
    cx, cy = SIZE[0] // 2, SIZE[1] // 2
    radius = 50
    
    # Desenhar um retângulo (representando área de trabalho)
    rect_x1, rect_y1 = cx - 35, cy - 25
    rect_x2, rect_y2 = cx + 35, cy + 25
    
    # Fundo do retângulo (área de trabalho)
    draw.rounded_rectangle(
        [rect_x1 - 2, rect_y1 - 2, rect_x2 + 2, rect_y2 + 2],
        radius=8,
        fill=(80, 80, 85, 220)
    )
    
    # Borda do retângulo
    draw.rounded_rectangle(
        [rect_x1 - 2, rect_y1 - 2, rect_x2 + 2, rect_y2 + 2],
        radius=8,
        outline=(100, 100, 110, 255),
        width=2
    )
    
    # Desenhar linhas (vetores) dentro do retângulo
    # Linha horizontal
    draw.line(
        [(cx - 25, cy), (cx + 25, cy)],
        fill=LINE_COLOR,
        width=3
    )
    
    # Linha diagonal
    draw.line(
        [(cx - 20, cy - 15), (cx + 20, cy + 15)],
        fill=LINE_COLOR,
        width=3
    )
    
    # Linha vertical
    draw.line(
        [(cx, cy - 20), (cx, cy + 20)],
        fill=LINE_COLOR,
        width=3
    )
    
    # Círculo (representando formas vetoriais)
    draw.ellipse(
        [cx - 12, cy - 12, cx + 12, cy + 12],
        fill=(0, 0, 0, 0),
        outline=ACCENT_COLOR,
        width=3
    )
    
    # Triângulo (representando formas vetoriais)
    points = [
        (cx - 8, cy + 10),
        (cx + 8, cy + 10),
        (cx, cy - 6)
    ]
    draw.polygon(points, fill=(0, 0, 0, 0), outline=ACCENT_COLOR, width=2)
    
    # Adicionar um pequeno destaque no canto (indicando "ativo")
    draw.rounded_rectangle(
        [rect_x1 + 28, rect_y1 + 2, rect_x1 + 38, rect_y1 + 12],
        radius=4,
        fill=(255, 255, 255, 200)
    )
    
    # Salvar como PNG com transparência
    img.save(OUTPUT_PATH, 'PNG')
    
    print(f"Ícone criado com sucesso: {OUTPUT_PATH}")
    print(f"Tamanho: {SIZE[0]}x{SIZE[1]}")
    print(f"Modo: RGBA com transparência")

if __name__ == "__main__":
    create_2d_icon()
