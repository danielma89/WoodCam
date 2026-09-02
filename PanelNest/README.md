# PanelNest (incluído na distribuição unificada)

Workbench de marcenaria para FreeCAD. Transforma um modelo 3D de móvel em planos de corte prontos para produção: etiquetagem de peças, nesting otimizado de chapas, fita de borda, exportação DXF/G-code e guias de montagem.

---

## Funcionalidades

### Fluxo principal
- **Etiquetar Peças** — gera IDs sequenciais (`PN-001`, `PN-002`…) para todas as peças
- **Aplicar Dados** — aplica material, método de corte, fita de borda e veio em lote
- **Aplicar Fita por Face** — marca fita de borda selecionando faces diretamente no modelo 3D
- **Editar Peças** — tabela para ajuste fino por peça
- **Organizar Peças** — agrupa por material, espessura e método de corte
- **Configurar Chapa** — define dimensões, margem, espaçamento, perfis CNC/seccionadora e estoque de retalhos
- **Validar Projeto** — detecta problemas antes de gerar o layout
- **Gerar Layout** — modo rápido por retângulos ou otimizado pelos contornos reais; gera modelo 3D + planilhas
- **Exportar** — CSV, HTML, DXF (por chapa e por peça), G-code NBM para CNC
- **WoodCAM 2D** — fluxo inspirado no Aspire com abas `Trabalho`, `Material`, `Fresas`, `Corte`, `Furo`, `Preenchimento` e `Simulação e Salvar`; inclui cadastro/seleção de fresas, tipo de trabalho, tamanho X/Y/Z da área, Z-zero na chapa/mesa, origem XY, posição inicial, prévia tracejada da área de trabalho, operações numeradas na árvore, preenchimento por offset/raster, furação helicoidal/faseada e desaceleração nos cantos
- **Imprimir Etiquetas** — PDF A4 ou ZPL para impressoras Zebra

### Planilhas geradas automaticamente
| Planilha | Conteúdo |
|---|---|
| `PanelNestParts` | Lista de peças com dimensões e propriedades |
| `PanelNestOrganizacao` | Agrupamento por material/espessura |
| `PanelNestAcabamento` | Metragem linear de fita por tipo |
| `PanelNestEtiquetas` | Dados para impressão de etiquetas |
| `PanelNestResumoChapas` | Aproveitamento % por chapa |
| `PanelNestPlanoCorte` | Sequência de cortes |
| `PanelNestCustos` | Custo por chapa, custo de desperdício |
| `PanelNestAlertasLayout` | Avisos e incompatibilidades |
| `PanelNestRetalhosGerados` | Retalhos gerados para reutilização |
| `PanelNestValidacao` | Problemas encontrados na validação |

### Destaques técnicos
- Algoritmos: MaxRects (BSSF/BAF/BLSF), consolidação regressiva entre chapas, otimização raster por contornos reais com rotações 0°/90°/180°/270° e fallback seguro, Guilhotina com GRASP e Faixas
- Banco de retalhos persistente entre sessões (SQLite em `~/.local/share/PanelNest/`)
- Compensação automática de espessura de fita de borda no corte
- Exportação G-code NBM (formato Homag) para CNC
- Exportação DXF por peça individual (ZIP) com layers de fita
- Etiquetas SVG/PDF com diagrama de fita, seta de veio e QR code
- Guias de montagem HTML com QR codes, publicáveis via Cloudflare Pages

---

## Instalação

### Pré-requisitos
- FreeCAD 1.0 ou superior
- Python 3.8+

### Opção 1 — Git clone (recomendado)

```bash
git clone https://github.com/danielma89/WoodCam.git
cd WoodCam
chmod +x install_workbench.sh
./install_workbench.sh
```

O script cria um link simbólico no diretório `Mod` do FreeCAD e detecta automaticamente:
- `~/.var/app/org.freecad.FreeCAD/data/FreeCAD/Mod` (Flatpak)
- `~/.local/share/FreeCAD/v*/Mod` (nativo recente)
- `~/.local/share/FreeCAD/Mod` (nativo legado)

### Opção 2 — Addon Manager do FreeCAD

> **Ferramentas → Gerenciador de Addons → buscar "PanelNest" → Instalar**

### Windows e pacote portátil

O WoodCAM já faz parte da bancada PanelNest. No Windows, copie apenas a pasta
unificada para:

```text
%APPDATA%\FreeCAD\Mod\PanelNest
```

Para gerar um ZIP contendo a bancada completa:

```bash
python PanelNest/scripts/build_portable_package.py
```

Extraia o ZIP dentro de `%APPDATA%\FreeCAD\Mod` e reinicie o FreeCAD. O comando
fica em `PanelNest → CAM → WoodCAM 2D`; não existe uma segunda instalação.

---

## Uso rápido

1. Abra o FreeCAD e troque para o workbench **PanelNest**
2. Crie ou abra um modelo com peças de chapas (Part::Box ou corpos sólidos)
3. **Etiquetar Peças** → **Aplicar Dados** → **Organizar Peças**
4. **Configurar Chapa** — defina dimensão, margens e estoque disponível
5. **Validar Projeto** — corrija eventuais alertas
6. **Gerar Layout** — o nesting é executado e o modelo 3D + planilhas são criados
7. **Exportar** — escolha CSV, DXF, G-code ou HTML

### Idioma da bancada

PanelNest é uma bancada única, incluindo o fluxo WoodCAM. O idioma é alterado
na barra principal do FreeCAD, em **Idioma → English** ou **Language →
Português**. A escolha traduz menus, ferramentas, dicas e diálogos abertos e
fica salva nas preferências do PanelNest; não altera comandos, documentos ou
dados de produção. A tradução global ocorre na troca do idioma e quando uma
janela ou menu é exibido, sem varredura periódica da janela principal do
FreeCAD. Os cartões de ajuda dos comandos também são traduzidos por inteiro,
inclusive quando o FreeCAD divide a descrição em HTML para ajustar sua largura.

---

## Estrutura do projeto

```
panelnest/          Lógica principal (pacote Python)
  constants.py      Constantes e configurações padrão
  models.py         Dataclasses: PanelPart, SheetSettings, LayoutSheet…
  nesting.py        Algoritmos de nesting (MaxRects, Guilhotina, GRASP)
  shape_optimizer.py Segundo estágio CNC por contornos reais
  spreadsheets.py   Geração de planilhas FreeCAD
  reports.py        Relatórios HTML, DXF, DXF por peça
  layout_model.py   Construção do modelo 3D do layout
  label_generator.py  Etiquetas SVG/PDF/ZPL
  gcode_export.py   Exportação G-code NBM
  remnant_db.py     Banco SQLite de retalhos
  edge_compensation.py  Compensação de espessura de fita
  ... (demais módulos)
commands/           Um arquivo por comando da toolbar
resources/icons/    Ícones SVG
tests/              Testes unitários (pytest, sem FreeCAD)
scripts/            Scripts de instalação
docs/               Documentação adicional
```

---

## Testes

```bash
pip install pytest
python -m pytest tests/ -v
```

Os testes rodam sem FreeCAD instalado.

---

## Licença

MIT — veja [LICENSE](LICENSE)
