"""Small, presentation-only language layer for the WoodCAM UI.

The editor and the CAM dialog predate a translation catalogue and contain a
large amount of text created directly by Qt widgets.  This module deliberately
does not touch commands, document data, operation identifiers or callbacks:
it remembers the original Portuguese presentation strings on each widget and
changes only the visible text when the user selects English.
"""

from __future__ import annotations

import re
import weakref

try:  # The editor is also loaded by FreeCAD's PySide2 builds.
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:  # pragma: no cover - exercised by FreeCAD/PySide2
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
    except ImportError:  # pragma: no cover - legacy FreeCAD
        from PySide import QtCore, QtGui
        QtWidgets = QtGui


_PARAMETER_PATH = "User parameter:BaseApp/Preferences/WoodCAM2D"
_LANGUAGE_KEY = "language"
_ITEM_SOURCE_ROLE = int(QtCore.Qt.UserRole) + 1000


# Exact labels are kept here instead of replacing strings throughout the
# application.  This makes the selector safe for old saved operations and for
# integrations that still look up the Portuguese object names.
PT_EN = {
    "Arquivo": "File",
    "Editar": "Edit",
    "Reparar": "Repair",
    "Filetes": "Fillets",
    "Peças": "Parts",
    "Chapas": "Sheets",
    "Chapa": "Sheet",
    "Enquadrar chapa": "Fit sheet",
    "Origem local da chapa: X0  Y0": "Local sheet origin: X0 Y0",
    "Origem local: X0 Y0  (global %.1f, %.1f)": "Local origin: X0 Y0  (global %.1f, %.1f)",
    "Chapa %02d  —  %.1f × %.1f mm": "Sheet %02d  —  %.1f × %.1f mm",
    "Chapa %02d  ·  X0 Y0": "Sheet %02d  ·  X0 Y0",
    "Chapa %02d — prévia  ·  X0 Y0": "Sheet %02d — preview  ·  X0 Y0",
    "Nenhuma chapa configurada.": "No sheet configured.",
    "Selecione a chapa ativa; as coordenadas passam a usar o canto inferior esquerdo dela como X0 Y0.": "Select the active sheet; coordinates then use its lower-left corner as X0 Y0.",
    "Recolher esta seção": "Collapse this section",
    "Expandir esta seção": "Expand this section",
    "Idioma": "Language",
    "Português": "Portuguese",
    "Importar itens da árvore…": "Import items from tree…",
    "Importar peças planas pelo PanelNest…": "Import flat parts from PanelNest…",
    "Importar arquivo": "Import file",
    "Vetorizar imagem…": "Vectorize image…",
    "Criar relevo 3D por imagem…": "Create 3D relief from image…",
    "Exportar": "Export",
    "Enviar para impressão (TechDraw)…": "Send to print (TechDraw)…",
    "Enviar para impressão — TechDraw": "Send to print — TechDraw",
    "O TechDraw receberá uma cópia vetorial da chapa, com seu limite e todo o conteúdo visível. O desenho e o CAM não serão alterados.": "TechDraw will receive a vector copy of the sheet, including its boundary and all visible content. The drawing and CAM will not be changed.",
    "Somente a chapa ativa": "Active sheet only",
    "Todas as chapas — uma página por chapa": "All sheets — one page per sheet",
    "Conteúdo": "Content",
    "Papel": "Paper",
    "(sugerido)": "(recommended)",
    "Escala": "Scale",
    "Ajustar a chapa inteira em uma página": "Fit the whole sheet on one page",
    "Tamanho real — 1:1": "Actual size — 1:1",
    "Escala personalizada": "Custom scale",
    "Percentual": "Percentage",
    "Aparência": "Appearance",
    "Manter o fundo azul-claro da chapa": "Keep the sheet's light-blue background",
    "Incluir o percurso 2D que está visível": "Include the currently visible 2D toolpath",
    "Criar no TechDraw": "Create in TechDraw",
    "%d página(s) %s; escala mínima %.1f%%. A chapa será girada em %d página(s) para aproveitar melhor o papel.": "%d %s page(s); minimum scale %.1f%%. The sheet will be rotated on %d page(s) to use the paper more efficiently.",
    "Essa escala não cabe no papel %s. Use Ajustar à página ou no máximo %.1f%%.": "This scale does not fit on %s paper. Use Fit to page or at most %.1f%%.",
    "Configure a área de Trabalho antes de criar a impressão.": "Configure the Job area before creating the print.",
    "%d página(s) criada(s) no TechDraw; desenho e CAM preservados.": "%d page(s) created in TechDraw; drawing and CAM preserved.",
    "Não foi possível criar a impressão no TechDraw: %s": "Could not create the TechDraw print: %s",
    "Região menor que a fresa": "Region smaller than the cutter",
    "A fresa Ø %.2f mm não alcança todos os detalhes de %d peça(s). A compensação externa fechou uma região estreita e o percurso passou reto; o primeiro ponto está próximo de X %.2f / Y %.2f.\n\nCortar mesmo assim mantém o contorno externo compensado e acrescenta um percurso auxiliar somente na região estreita. Em uma fenda aberta, a fresa segue o eixo médio local, inclusive em afunilamentos e degraus, para retirar o mínimo possível de cada lateral. A abertura final nunca pode ser menor que o diâmetro da ferramenta. Confira a prévia antes de gerar o G-code.": "The Ø %.2f mm cutter cannot reach every detail of %d part(s). Outside compensation closed a narrow region and the toolpath went straight past it; the first point is near X %.2f / Y %.2f.\n\nProceeding keeps the outside contour compensated and adds a cleanup path only in the narrow region. In an open slot, the cutter follows the local medial axis, including through tapers and steps, to remove as little as possible from each side. The final opening can never be smaller than the tool diameter. Check the preview before generating G-code.",
    "Cortar mesmo assim e assumir conscientemente essa perda dimensional?": "Cut anyway and knowingly accept this dimensional loss?",
    "Corte cancelado: a região estreita não foi autorizada.": "Cut cancelled: the narrow region was not authorized.",
    "Agrupar objetos": "Group objects",
    "Desagrupar objetos": "Ungroup objects",
    "Medir / inspecionar": "Measure / inspect",
    "Medir entre dois pontos com Snap; não altera o desenho": "Measure between two points with Snap; does not change the drawing",
    "Soldar vetores sobrepostos": "Weld overlapping vectors",
    "Subtrair vetores (criar furo/recorte interno)": "Subtract vectors (create hole/internal cut)",
    "Interseção de vetores": "Vector intersection",
    "Sobrepor vetores (último recorta os anteriores)": "Overlap vectors (last cuts previous)",
    "Inverter direção dos vetores": "Reverse vector direction",
    "Editar texto vetorial…": "Edit vector text…",
    "Ajustar arcos/círculos aos vetores…": "Fit arcs/circles to vectors…",
    "Criar contorno (offset)…": "Create contour (offset)…",
    "Diagnosticar": "Diagnose",
    "Limpar sobrelinhas/duplicados…": "Clean overlines/duplicates…",
    "Fechar caminho / unir próximas": "Close path / join nearby",
    "Unir vetores abertos (por tolerância)": "Join open vectors (by tolerance)",
    "Fechar caminho com reta": "Close path with line",
    "Fechar caminho com curva suave": "Close path with smooth curve",
    "Fechar aproximando as pontas": "Close by bringing endpoints together",
    "Unir 2 pontas (reta)": "Join 2 endpoints (line)",
    "Unir 2 pontas (curva suave)": "Join 2 endpoints (smooth curve)",
    "Projetar ponta na geometria": "Project endpoint onto geometry",
    "Emendar em contorno (Splice)": "Splice into contour",
    "Trim interativo": "Interactive trim",
    "Estender": "Extend",
    "Offset": "Offset",
    "Filete normal": "Standard fillet",
    "Dogbone": "Dogbone",
    "T-bone": "T-bone",
    "Dogbone automático": "Automatic dogbone",
    "T-bone automático": "Automatic T-bone",
    "Reconhecer peças e furos": "Recognize parts and holes",
    "Reconhecer contornos externos como peças e manter furos e recortes vinculados": "Recognize outer contours as parts and keep holes and cutouts attached",
    "1 externo + %d interno(s) + %d marcação(ões)": "1 outer + %d inner + %d marking(s)",
    "Contorno externo + %d furo(s)/recorte(s) + %d marcação(ões).": "Outer contour + %d hole(s)/cutout(s) + %d marking(s).",
    "Organizar inteligente": "Smart nesting",
    "Organizar peças automaticamente usando o nesting inteligente": "Automatically arrange parts using smart nesting",
    "Organizar rápido": "Quick nesting",
    "Organizar profundo": "Thorough nesting",
    "Organização progressiva": "Progressive nesting",
    "O WoodCAM mostrará a primeira solução rapidamente e continuará testando encaixes melhores até o tempo-alvo.": "WoodCAM will show the first solution quickly and keep testing better layouts until the target time.",
    "Folga mínima entre peças": "Minimum clearance between parts",
    "Tempo-alvo de busca": "Search target time",
    "Preparando a primeira solução…": "Preparing the first solution…",
    "Preparando a primeira solução": "Preparing the first solution",
    "Parar e manter a melhor prévia": "Stop and keep the best preview",
    "Resposta rápida": "Quick result",
    "Busca equilibrada": "Balanced search",
    "Refino profundo": "Deep refinement",
    "Refino adicional": "Additional refinement",
    "Refino máximo": "Maximum refinement",
    "sem solução completa ainda": "no complete solution yet",
    "%d peça(s), %d chapa(s), %.1f%%": "%d part(s), %d sheet(s), %.1f%%",
    "%.1f s restantes": "%.1f s remaining",
    "tempo-alvo atingido; encerrando a tentativa atual": "target time reached; finishing the current attempt",
    "Tentativa %d/%d — %s\n%s • melhor: %s": "Attempt %d/%d — %s\n%s • best: %s",
    "Buscando encaixes: a primeira solução aparecerá assim que ficar pronta.": "Searching for layouts: the first solution will appear as soon as it is ready.",
    "Solução %d pronta em %.1f s; continuando a busca por encaixe melhor.": "Solution %d ready in %.1f s; continuing to search for a better layout.",
    "Busca de organização cancelada porque o documento mudou.": "Nesting search was cancelled because the document changed.",
    "Busca de organização interrompida; a prévia foi descartada.": "Nesting search stopped; the preview was discarded.",
    "Busca interrompida antes de produzir a primeira solução.": "Search stopped before producing the first solution.",
    "Nenhuma solução de organização foi produzida.": "No nesting solution was produced.",
    "tempo-alvo atingido": "target time reached",
    "busca interrompida pelo operador": "search stopped by the operator",
    "todas as tentativas planejadas concluídas": "all planned attempts completed",
    "busca concluída": "search completed",
    "%s em %.1f s e %d etapa(s); a melhor prévia permanece pronta para aplicar.": "%s in %.1f s and %d stage(s); the best preview remains ready to apply.",
    "Interrompendo a tentativa atual…": "Stopping the current attempt…",
    "A melhor solução atual coincide com as posições existentes%s.": "The current best solution matches the existing positions%s.",
    "; a busca continua": "; the search continues",
    "Prévia do nesting inteligente: magenta = destino, azul = posição atual. %d peça(s) em %d chapa(s); %d não couberam. Eficiência %.1f%%; %s venceu entre %d layouts%s%s.": "Smart nesting preview: magenta = destination, blue = current position. %d part(s) on %d sheet(s); %d did not fit. Efficiency %.1f%%; %s won among %d layouts%s%s.",
    "contorno real": "real contour",
    "MaxRects — folga vetorial segura": "MaxRects — safe vector clearance",
    "encaixe": "layout",
    "; %d ocorrência(s) extra serão replicadas pelo PanelNest": "; %d extra occurrence(s) will be replicated by PanelNest",
    "; busca continua em segundo plano": "; search continues in the background",
    "; busca concluída": "; search completed",
    "A prévia de organização ficou desatualizada; execute a busca novamente.": "The nesting preview became outdated; run the search again.",
    "Organizadas %d em %d chapa(s); %d peça(s) não cabem nem em uma chapa vazia.": "%d organized on %d sheet(s); %d part(s) do not fit even on an empty sheet.",
    "Organizadas %d peça(s) em %d chapa(s), sempre com seus furos, recortes e marcações%s.": "%d part(s) organized on %d sheet(s), always with their holes, cutouts, and markings%s.",
    "; quantidades extras seguem para o PanelNest": "; extra quantities will be sent to PanelNest",
    "Aplicar organização": "Apply nesting",
    "Linha comum preservando medidas: a folga não pode ser menor que o Ø efetivo de %.2f mm. O valor recomendado faz os percursos externos vizinhos coincidirem.": "Dimension-preserving common line: clearance cannot be smaller than the %.2f mm effective diameter. The recommended value makes neighboring outside toolpaths coincide.",
    "Linha comum sobre o vetor: use 0 mm para encostar as bordas. Uma folga intermediária entre 0 e %.2f mm não comporta a fresa.": "On-vector common line: use 0 mm to make the edges touch. An intermediate clearance between 0 and %.2f mm cannot accommodate the cutter.",
    "A folga foi ajustada de %.2f para %.2f mm para comportar o percurso externo da fresa.": "Clearance was adjusted from %.2f to %.2f mm to accommodate the cutter's outside toolpath.",
    "Usar Editor 2D como fonte": "Use 2D Editor as source",
    "Configurar/criar Corte…": "Configure/create Cut…",
    "Configurar/criar Furos…": "Configure/create Holes…",
    "Configurar/criar Rebaixo…": "Configure/create Pocket…",
    "Ver percurso de Corte aqui": "Show Cut toolpath here",
    "Ver percurso de Furos aqui": "Show Holes toolpath here",
    "Ver percurso de Rebaixo aqui": "Show Pocket toolpath here",
    "Ver percurso de Corte no 2D": "Show Cut toolpath in 2D",
    "Ver percurso de Furos no 2D": "Show Holes toolpath in 2D",
    "Ver percurso de Rebaixo no 2D": "Show Pocket toolpath in 2D",
    "Ocultar percurso": "Hide toolpath",
    "Enviar PanelNest": "Send to PanelNest",
    "Desfazer": "Undo",
    "Refazer": "Redo",
    "Excluir": "Delete",
    "Enquadrar": "Fit",
    "Imã (Snap)": "Snap",
    "Orto/ângulo": "Ortho/angle",
    "Desenho": "Drawing",
    "Trabalho": "Job",
    "Material": "Material",
    "Corte": "Cut",
    "StockTabs físicas": "Physical StockTabs",
    "SharedTabs físicas": "Physical SharedTabs",
    "WasteTabs físicas": "Physical WasteTabs",
    "Corte externo": "Outside cut",
    "Corte sobre a linha": "On-line cut",
    "Corte interno": "Inside cut",
    "Furo": "Hole",
    "Furos": "Holes",
    "Rebaixo": "Pocket",
    "Preenchimento": "Pocket",
    "Simulação": "Simulation",
    "Simular": "Simulate",
    "Aplicar": "Apply",
    "Atualizar operação": "Update operation",
    "Cancelar edição": "Cancel editing",
    "Editando": "Editing",
    "distribuição antiga de tabs; atualize a operação para recalcular": "legacy tab distribution; update the operation to recalculate it",
    "Pré-visualizar": "Preview",
    "Gerar G-code": "Generate G-code",
    "Cancelar": "Cancel",
    "Fechar": "Close",
    "OK": "OK",
    "Nome": "Name",
    "Quantidade": "Quantity",
    "Espessura": "Thickness",
    "Profundidade": "Depth",
    "Avanço": "Feed",
    "Fresa": "Tool",
    "Fresas": "Tools",
    "Ferramenta": "Tool",
    "Personalizada": "Custom",
    "Fresa 6 mm MDF": "6 mm MDF end mill",
    "Broca 6 mm": "6 mm drill bit",
    "Faceadora 20 mm": "20 mm surfacing bit",
    "Topo esférico 6 mm": "6 mm ball nose",
    "V-Bit 90° 6 mm": "90° 6 mm V-bit",
    "Diâmetro": "Diameter",
    "Rampa": "Ramp",
    "Entradas": "Entries",
    "Ordem": "Order",
    "Ponto inicial": "Start point",
    "Automático": "Automatic",
    "Manual": "Manual",
    "Concluir posicionamento": "Finish placement",
    "Posicionar tabs...": "Place tabs…",
    "Manter tabs": "Keep tabs",
    "Remover tabs ao final": "Remove tabs at end",
    "Remover tabs automaticamente ao final": "Remove tabs automatically at end",
    "Altura intacta no MDF (mm)": "Untouched MDF height (mm)",
    "Mínimo automático por peça": "Automatic minimum per part",
    "Depois de concluir todas as passadas, visita somente as tabs: mergulha, corta a ponte, sobe imediatamente e usa rápido até a próxima. Ao marcar, Linha comum é ativada automaticamente.": "After all passes are complete, visits only the tabs: plunges, cuts the bridge, retracts immediately, and rapids to the next one. Enabling this also enables Common-line cutting automatically.",
    "Escolha tabs ou parafusos para prender desperdícios que poderiam se soltar. O plano de Linha comum é ativado automaticamente.": "Choose tabs or screws to secure waste that could come loose. The Common-line plan is enabled automatically.",
    "Pausa única antes de todas": "One pause before all tabs",
    "Pausa antes de cada peça": "Pause before each part",
    "Pausa antes de cada tab": "Pause before each tab",
    "Restos soltos: desativado": "Loose waste: disabled",
    "Restos soltos: tabs": "Loose waste: tabs",
    "Restos soltos: parafusos": "Loose waste: screws",
    "Diâmetro piloto; 0 = fresa (mm)": "Pilot diameter; 0 = tool (mm)",
    "Profundidade piloto; 0 = automática (mm)": "Pilot depth; 0 = automatic (mm)",
    "Diâmetro da cabeça/arruela (mm)": "Head/washer diameter (mm)",
    "Margem ao redor do parafuso (mm)": "Screw safety margin (mm)",
    "Altura da cabeça acima do MDF (mm)": "Head height above MDF (mm)",
    "Espessura original de MDF que fica totalmente intacta na ponte. O sobrecorte no spoilboard não reduz este valor.": "Original MDF thickness left completely untouched in the bridge. Spoilboard overcut does not reduce this value.",
    "Define quando o programa retrai, para o spindle e executa M0 antes da liberação definitiva das peças.": "Defines when the program retracts, stops the spindle, and runs M0 before parts are finally released.",
    "Depois de concluir todas as passadas, visita somente as tabs: mergulha, corta a ponte, sobe imediatamente e usa rápido até a próxima.": "After all passes are complete, visits only the tabs: plunges, cuts the bridge, retracts immediately, and rapids to the next one.",
    "Concordante": "Climb",
    "Convencional": "Conventional",
    "Níveis Z": "Z levels",
    "Varredura 3D": "3D raster",
    "Ao longo de X": "Along X",
    "Ao longo de Y": "Along Y",
    "Raster": "Raster",
    "Offset concêntrico": "Concentric offset",
    "Selecionar fresa": "Select tool",
    "Fresa salva": "Tool saved",
    "Erro": "Error",
    "Aviso": "Warning",
    "Arquivo de saída": "Output file",
    "Tempo estimado": "Estimated time",
    "%s selecionado. Use Ver percurso ou Simular para carregar a geometria exata.": "%s selected. Use Show toolpath or Simulate to load the exact geometry.",
    "Selecione somente uma operação para conferir seu percurso exato. A exportação múltipla continua usando cada lista persistida separadamente.": "Select only one operation to inspect its exact toolpath. Multi-operation export continues to use each stored move list separately.",
    "Tempo estimado selecionado: %s": "Estimated selected time: %s",
    "Tempo da seleção: disponível ao abrir percurso ou simulação": "Selected time: available after opening the toolpath or simulation",
    "Tempo estimado total aplicado: %s": "Total applied estimated time: %s",
    "Tempo total: disponível ao abrir percurso ou simulação": "Total time: available after opening the toolpath or simulation",
    "Operação aplicada com as tabs preservadas: não há uma ordem automática segura para liberar todas as peças restantes.": "Operation applied with tabs preserved: there is no safe automatic order for releasing all remaining parts.",
    "Configuração": "Configuration",
    "Posicionar tabs": "Place tabs",
    "Modo: selecionar": "Mode: select",
    "Modo: linha — arraste no canvas": "Mode: line — drag on canvas",
    "Modo: círculo — arraste do centro": "Mode: circle — drag from center",
    "Destacar Editor 2D": "Detach 2D Editor",
    "Alternar painel ou janela do WoodCAM": "Toggle WoodCAM panel or window",
    "Soltar o WoodCAM em uma janela independente": "Detach WoodCAM into an independent window",
    "Prender o WoodCAM novamente ao FreeCAD": "Attach WoodCAM back to FreeCAD",
    "O WoodCAM já está em uma janela independente": "WoodCAM is already an independent window",
    "WoodCAM 2D - CNC Marcenaria": "WoodCAM 2D - CNC Woodworking",
    "WoodCAM 2D - Simulação": "WoodCAM 2D - Simulation",
    "WoodCAM Editor 2D": "WoodCAM 2D Editor",
    "WoodCAM — Editor 2D": "WoodCAM — 2D Editor",
    "Editor 2D": "2D Editor",
    "Desbaste 3D": "3D Roughing",
    "Acabamento 3D": "3D Finishing",
    "Editor 2D é a fonte do CAM": "2D Editor is the CAM source",
    "Mostrar painel lateral": "Show side panel",
    "Ocultar painel lateral": "Hide side panel",
    "Mostrar somente o painel lateral": "Show side panel only",
    "Ocultar somente o painel lateral": "Hide side panel only",
    "Abrir o mesmo Editor 2D em uma janela própria": "Open the same 2D Editor in its own window",
    "Selecionar": "Select",
    "Nós": "Nodes",
    "Linha": "Line",
    "Arco": "Arc",
    "Círculo": "Circle",
    "Elipse": "Ellipse",
    "Retângulo": "Rectangle",
    "Polilinha": "Polyline",
    "Polígono": "Polygon",
    "Bézier": "Bezier",
    "Estrela": "Star",
    "Texto vetorial": "Vector text",
    "Criar texto vetorial em curvas": "Create vector text as curves",
    "Aplicar prévia": "Apply preview",
    "Prévia desatualizada; cancele e gere novamente.": "Preview is stale; cancel and generate it again.",
    "Prévia desatualizada porque o documento mudou; cancele e gere novamente.": "Preview is stale because the document changed; cancel and generate it again.",
    "Número de lados do polígono": "Number of polygon sides",
    "Número de pontas da estrela": "Number of star points",
    "Profundidade das pontas internas da estrela": "Inner star-point depth",
    "Espaçamento da grade": "Grid spacing",
    "Espaçamento entre peças para nesting": "Spacing between parts for nesting",
    "Exportar vetores do Editor 2D": "Export 2D Editor vectors",
    "Importar geometria e camadas": "Import geometry and layers",
    "Importar vetores e camadas": "Import vectors and layers",
    "Importar vetores no Editor 2D": "Import vectors into 2D Editor",
    "Arquivo G-code": "G-code file",
    "Salvar": "Save",
    "Salvar G-code": "Save G-code",
    "Arquivo único": "Single file",
    "Arquivos separados": "Separate files",
    "Formato não reconhecido; escolha DXF ou SVG.": "Unrecognized format; choose DXF or SVG.",
    "TRABALHO E ORIGEM": "JOB AND ORIGIN",
    "MATERIAL E SEGURANÇA": "MATERIAL AND SAFETY",
    "Área de Trabalho": "Work area",
    "Limite da área de trabalho": "Work area bounds",
    "Limite do material": "Material bounds",
    "Configuração do material": "Material setup",
    "Tamanho da área de trabalho": "Work area size",
    "Largura X": "Width X",
    "Altura Y": "Height Y",
    "Altura Z": "Height Z",
    "Posição da origem XY": "XY origin position",
    "Ponto do material": "Material point",
    "Ponto da área": "Work-area point",
    "Z-zero": "Z-zero",
    "Z-zero do trabalho": "Job Z-zero",
    "Superfície do material": "Material surface",
    "Mesa da máquina": "Machine bed",
    "Folga abaixo do modelo": "Clearance below model",
    "Folga acima do modelo": "Clearance above model",
    "Folga do modelo": "Model clearance",
    "Modelo 3D": "3D model",
    "Modelo 3D para desbaste": "3D model for roughing",
    "Modelo 3D para acabamento": "3D model for finishing",
    "Corte 1": "Cut 1",
    "Furo 1": "Hole 1",
    "Preenchimento 1": "Pocket 1",
    "Preenchimento/Rebaixo": "Pocket",
    "Percurso de Furação": "Drilling toolpath",
    "Trajetória de usinagem": "Machining toolpath",
    "Configurações": "Settings",
    "Informações da ferramenta": "Tool information",
    "Cadastro de fresas": "Tool database",
    "Fresas cadastradas": "Registered tools",
    "Tipo": "Type",
    "Estratégia": "Strategy",
    "Direção": "Direction",
    "Subida": "Climb",
    "Sentido": "Direction",
    "Sequência": "Sequence",
    "Entrada": "Entry",
    "Entradas e descidas": "Entries and plunges",
    "Entrada em rampa": "Ramp entry",
    "Entrada helicoidal quando a ferramenta for menor": "Helical entry when the tool is smaller",
    "Interpolação helicoidal": "Helical interpolation",
    "Mergulho Z2 / segurança (mm)": "Plunge Z2 / safety (mm)",
    "Folga Z1 / retração (mm)": "Clearance Z1 / retract (mm)",
    "Retração": "Retract",
    "Retornar ao ponto inicial no fim": "Return to start point at end",
    "Retrair acima da cota inicial": "Retract above initial height",
    "Retrair acima do passo anterior": "Retract above previous step",
    "Profundidade por passe (mm)": "Depth per pass (mm)",
    "Profundidade de corte (mm)": "Cut depth (mm)",
    "Profundidade final Z (mm)": "Final Z depth (mm)",
    "Profundidade inicial Z (mm)": "Initial Z depth (mm)",
    "Primeira profundidade considerada pelo percurso. As tabs sempre sobem até a altura intacta medida a partir da face do MDF. Não é diâmetro e não cria rebaixo.": "First depth considered by the toolpath. Tabs always rise to the untouched height measured from the MDF top face. It is not a diameter and does not create a counterbore.",
    "Profundidade que já foi usinada, medida desde a face original do material. O percurso começa nessa cota. Não é diâmetro e não cria rebaixo.": "Depth already machined, measured from the material's original top face. The toolpath starts at this level. It is not a diameter and does not create a counterbore.",
    "Cota final absoluta medida desde a face original do material. Ex.: início 6 mm e final 7 mm usinam somente o 1 mm restante.": "Absolute final depth measured from the material's original top face. Example: start at 6 mm and finish at 7 mm machines only the remaining 1 mm.",
    "Ex.: início 6 mm → final 7 mm = usinar 1 mm restante.": "Example: start at 6 mm → finish at 7 mm = machine the remaining 1 mm.",
    "Final deve ser maior que o início": "Final must be greater than start",
    "O WoodCAM distribui as tabs ao redor de cada peça e aumenta a quantidade somente quando o tamanho exigir.": "WoodCAM distributes tabs around each part and increases the quantity only when its size requires it.",
    "Quantidade mínima desejada por peça. O WoodCAM preserva pelo menos 4 e acrescenta somente o necessário em peças maiores; tabs altas permitem um espaçamento maior.": "Minimum quantity requested per part. WoodCAM keeps at least 4 and adds only what larger parts require; tall tabs allow wider spacing.",
    "Profundidade total do corte:": "Total cut depth:",
    "Profundidades de corte": "Cut depths",
    "Sobre-metal para última passada (mm)": "Allowance for final pass (mm)",
    "Sobremetal lateral (mm)": "Side allowance (mm)",
    "Fazer última passada separada": "Make final pass separately",
    "Adicionar rampas ao percurso": "Add ramps to toolpath",
    "Adicionar tabs ao percurso": "Add tabs to toolpath",
    "Passagens": "Passes",
    "Editar passagens...": "Edit passes…",
    "Editar passagens": "Edit passes",
    "Número de passagens": "Number of passes",
    "Tabs / pontes de fixação": "Tabs / holding bridges",
    "Tabs 3D": "3D tabs",
    "Melhor fixação": "Best holding",
    "Posicionar manualmente": "Place manually",
    "Cantos e mudanças de direção": "Corners and direction changes",
    "Cantos desacelerados": "Slowed corners",
    "Reduzir avanço próximo aos cantos": "Reduce feed near corners",
    "Avanço XY (mm/min)": "XY feed (mm/min)",
    "Avanço Z (mm/min)": "Z feed (mm/min)",
    "Avanço Z / mergulho (mm/min)": "Z feed / plunge (mm/min)",
    "Avanço rápido (mm/min)": "Rapid feed (mm/min)",
    "RPM": "RPM",
    "Diâmetro (mm)": "Diameter (mm)",
    "Ângulo incluído (°)": "Included angle (°)",
    "Ângulo do raster (°)": "Raster angle (°)",
    "Distância da fronteira (mm)": "Boundary distance (mm)",
    "Distância antes/depois (mm)": "Distance before/after (mm)",
    "Tolerância da linha comum (mm)": "Common-line tolerance (mm)",
    "Usinar vetores": "Machine vectors",
    "Lado dos contornos externos": "Outer contour side",
    "Compensação dimensional (mm)": "Dimensional compensation (mm)",
    "Geometria compartilhada": "Shared geometry",
    "Ordem das profundidades": "Depth order",
    "Contornos internos e furos selecionados são cortados por dentro e antes do contorno externo.": "Selected internal contours and holes are cut from the inside before the outer contour.",
    "Última passada e cantos": "Final pass and corners",
    "Mudança mínima de direção (°)": "Minimum direction change (°)",
    "Avanço no canto (%)": "Corner feed (%)",
    "Tolerância para fechar (mm)": "Close tolerance (mm)",
    "Linha comum altera as medidas": "Common line changes dimensions",
    "Preservar medidas — folga = Ø efetivo": "Preserve dimensions — gap = effective Ø",
    "Aproveitar fronteiras por linha comum": "Use common-line boundaries",
    "Chapa inteira por profundidade": "Whole sheet by depth",
    "Todas as passadas direto por peça (ex.: 3 direto)": "All passes directly per part (e.g. 3 straight through)",
    "Híbrido — estabilidade": "Hybrid — stability",
    "Última passada no final (ex.: 2 + última geral)": "Final pass at the end (e.g. 2 + final sheet pass)",
    "Passada de acabamento com sobre-metal": "Finishing pass with allowance",
    "Estas estratégias pertencem exclusivamente ao corte por Linha comum. Com Linha comum desligada, o WoodCAM usa sempre o corte CAM padrão por peça. Chapa inteira conclui cada Z em todas as peças. Híbrido prioriza a retenção ao atingir a chapa. 'Última passada no final' faz N-1 passadas por peça e só depois a última em toda a chapa. 'Todas direto' conclui todas as profundidades da peça antes de seguir para a próxima.": "These strategies apply only to common-line cutting. With common line disabled, WoodCAM always uses the standard per-part CAM cut. Whole sheet completes each Z level across all parts. Hybrid prioritizes retention at through depth. 'Final pass at the end' completes N-1 passes per part and then the final pass across the sheet. 'All passes directly' completes every depth on a part before moving to the next one.",
    "Deixa material na lateral durante o desbaste e faz outra volta na medida. Esta opção não controla a ordem das profundidades da Linha comum.": "Leaves material on the side during roughing and makes another pass at final size. This option does not control common-line depth order.",
    "Rebaixo para cabeça do parafuso": "Screw-head counterbore",
    "Criar assento maior na entrada do furo": "Create a larger seat at the hole entrance",
    "Diâmetro do rebaixo (mm)": "Counterbore diameter (mm)",
    "Profundidade do rebaixo (mm)": "Counterbore depth (mm)",
    "Abre um rebaixo cilíndrico raso e de fundo plano para assentar a cabeça do parafuso. Use fresa de topo ou de compressão.": "Cuts a shallow, flat-bottom cylindrical counterbore to seat the screw head. Use an end mill or compression bit.",
    "A profundidade é medida a partir da Profundidade inicial Z. O furo principal continua até a profundidade configurada/modelada.": "Depth is measured from the Initial Z depth. The main hole continues to the configured or model depth.",
    "Simulação e Salvar": "Simulation and Save",
    "Percursos 2D": "2D Toolpaths",
    "Ocultar": "Hide",
    "SIMULAR E SALVAR": "SIMULATE AND SAVE",
    "Velocidade da simulação": "Simulation speed",
    "Tempo estimado da prévia:": "Estimated preview time:",
    "Tempo estimado da última operação:": "Estimated last-operation time:",
    "Tempo estimado selecionado:": "Estimated selected time:",
    "Tempo estimado total aplicado:": "Estimated total applied time:",
    "Prévia CAM": "CAM preview",
    "G-CODE GERADO = VISTA": "GENERATED G-CODE = VIEW",
    "SIMULAÇÃO EXATA = G-CODE": "EXACT SIMULATION = G-CODE",
    "APLICADO = G-CODE": "APPLIED = G-CODE",
    "Vista de percurso: nenhuma trajetória calculada.": "Toolpath view: no toolpath calculated.",
    "Ver percurso de %s no 2D": "Show %s toolpath in 2D",
    "Assistente CAM": "CAM assistant",
    "Assistente CAM — análise explicável": "CAM assistant — explainable analysis",
    "ANALISANDO AGORA": "ANALYZING NOW",
    "ATENÇÃO": "WARNING",
    "SUGESTÃO": "SUGGESTION",
    "INFORMAÇÃO": "INFORMATION",
    "Ferramenta não nomeada": "Unnamed tool",
    "Tipo não informado": "Unspecified type",
    "Topo reto": "End mill",
    "Esférica": "Ball nose",
    "Broca": "Drill bit",
    "Compressão": "Compression",
    "Faceadora": "Surfacing bit",
    "Analisar ferramenta, passes, alturas e acabamento. Não altera parâmetros nem G-code.": "Analyze tool, passes, heights and finish. Does not change parameters or G-code.",
    "Nenhum alerta conservador foi encontrado. Isso não substitui a pré-visualização, a simulação nem a conferência na máquina.": "No conservative warnings were found. This does not replace previewing, simulation, or verification at the machine.",
    "Valor sugerido para revisar: %g": "Suggested value to review: %g",
    "O assistente apenas analisa: nenhum parâmetro, geometria ou G-code foi alterado.": "The assistant only analyzes: no parameter, geometry, or G-code was changed.",
    "Broca escolhida para usinagem lateral": "Drill bit selected for side cutting",
    "Brocas são destinadas à furação axial. Revise a ferramenta antes de cortar contornos, bolsões ou superfícies.": "Drill bits are intended for axial drilling. Review the tool before cutting contours, pockets, or surfaces.",
    "Acabamento 3D sem fresa esférica": "3D finishing without a ball-nose tool",
    "Uma fresa esférica normalmente acompanha relevos com transições mais suaves e marcas menos agressivas.": "A ball-nose tool usually follows reliefs with smoother transitions and less aggressive marks.",
    "Ferramenta incomum para furação": "Unusual tool for drilling",
    "Confirme diâmetro e geometria da ferramenta; o percurso de furo pressupõe broca ou fresa de topo apta a mergulhar.": "Confirm the tool diameter and geometry; the hole toolpath assumes a drill bit or plunge-capable end mill.",
    "Profundidade por passe maior que o diâmetro": "Depth per pass is greater than the tool diameter",
    "Esse passe pode exigir esforço elevado. Confirme a recomendação do fabricante, a rigidez da máquina e o material.": "This pass may require high cutting force. Confirm the manufacturer's recommendation, machine rigidity, and material.",
    "Passo lateral alto para acabamento fino": "High stepover for fine finishing",
    "Marcas entre passadas tendem a ficar mais visíveis. Para relevo detalhado, revise algo entre 8% e 12%.": "Marks between passes tend to become more visible. For detailed reliefs, review a value between 8% and 12%.",
    "Acabamento muito denso": "Very dense finishing toolpath",
    "A qualidade pode aumentar, mas o tempo e a quantidade de movimentos crescem bastante.": "Quality may improve, but machining time and move count increase substantially.",
    "Passo lateral agressivo no desbaste 3D": "Aggressive stepover in 3D roughing",
    "Pode sobrar material entre passadas e aumentar a carga. Revise o limite recomendado para a fresa.": "Material may remain between passes and increase tool load. Review the recommended limit for the tool.",
    "Passo lateral agressivo no preenchimento": "Aggressive stepover in pocketing",
    "O engajamento lateral está alto; confirme carga, evacuação de cavaco e potência disponível.": "Radial engagement is high; confirm tool load, chip evacuation, and available power.",
    "Altura segura com pouca margem": "Safe height has little clearance",
    "Grampos, empeno da chapa e irregularidades podem superar essa folga. Confirme a montagem real.": "Clamps, sheet warping, and surface irregularities may exceed this clearance. Confirm the actual setup.",
    "Pouca diferença entre retração e altura segura": "Small difference between retract and safe height",
    "Uma margem maior torna os deslocamentos longos mais conservadores, especialmente com fixações altas.": "A larger margin makes long travels more conservative, especially with tall fixtures.",
    "Corte não atravessa a espessura informada": "Cut does not pass through the specified thickness",
    "Se a intenção for recortar a peça, ainda restará material no fundo. Para gravação ou sulco, ignore esta observação.": "If the intent is to cut out the part, material will remain at the bottom. Ignore this observation for engraving or grooves.",
    "Corte avança muito abaixo da chapa": "Cut extends too far below the sheet",
    "Revise a profundidade extra para não consumir desnecessariamente a mesa de sacrifício.": "Review the extra depth to avoid unnecessarily cutting into the spoilboard.",
    "Essas regiões podem desaparecer, ser alargadas ou não gerar percurso. Confira a prévia exata.": "These regions may disappear, be enlarged, or produce no toolpath. Check the exact preview.",
    "%d observação(ões), sendo %d alerta(s). Clique para entender. Nenhum parâmetro é alterado automaticamente.": "%d observation(s), including %d warning(s). Click for details. No parameter is changed automatically.",
    "Nenhum alerta conservador nos parâmetros atuais. Clique para conferir; isso não substitui prévia, simulação e teste na máquina.": "No conservative warnings in the current parameters. Click to review; this does not replace previewing, simulation, and a machine test.",
    "O Assistente CAM analisa a aba visível, não a última operação usada. Abra Corte, Furo, Preenchimento, Desbaste 3D ou Acabamento 3D e clique novamente.": "The CAM assistant analyzes the visible tab, not the last operation used. Open Cut, Hole, Pocket, 3D Roughing, or 3D Finishing and click again.",
    "A operação visível não corresponde aos parâmetros coletados.": "The visible operation does not match the collected parameters.",
    "Sugestão: fresa esférica com stepover de 8% a 12% para acabamento fino.": "Suggestion: use a ball-nose tool with 8% to 12% stepover for fine finishing.",
    "Aplicar organização": "Apply nesting",
    "Aplicar conexões 2D": "Apply 2D connections",
    "Aplicar vetorização": "Apply vectorization",
    "Aplicar fechamento": "Apply close",
    "Aplicar união": "Apply join",
    "Aplicar ajuste de curvas": "Apply curve fitting",
    "Prévia da vetorização": "Vectorization preview",
    "Diagnóstico": "Diagnostics",
    "Diagnóstico: desenho válido, sem ocorrências.": "Diagnostics: valid drawing, no issues.",
    "Fechadas": "Closed",
    "Ponta aberta": "Open endpoint",
    "Selecionar vetores abertos": "Select open vectors",
    "Vetores abertos:": "Open vectors:",
    "Contornos fechados:": "Closed contours:",
    "Contornos sobrepostos": "Overlapping contours",
    "Remover": "Remove",
    "Remover duplicados": "Remove duplicates",
    "Remover fresa": "Remove tool",
    "Atualizar lista": "Refresh list",
    "Conexão não executada:": "Connection not executed:",
    "Fechamento não executado:": "Close not executed:",
    "União de vetores não executada: %s": "Vector join not executed: %s",
}

# Complete production catalogue for the CAM tabs and Editor side panels.  Keep
# exact sentences here so the English UI never falls back to mixed, word-by-
# word substitutions. Values entered or named by the user are intentionally
# absent and therefore remain unchanged.
PT_EN.update({
    "+ Camada": "Add layer",
    "Ativa": "Active",
    "Camada": "Layer",
    "Camadas": "Layers",
    "Visível": "Visible",
    "Bloq.": "Locked",
    "Mover seleção": "Move selection",
    "0 selecionados": "0 selected",
    "Direção do veio": "Grain direction",
    "Definir": "Set",
    "Rotação": "Rotation",
    "Somente 0°": "0° only",
    "Livre": "Free",
    "Bloqueada": "Locked",
    "Aplicar à peça": "Apply to part",
    "Nenhuma peça classificada.": "No classified part.",
    "Posição e dimensões exatas": "Exact position and dimensions",
    "X mínimo (mm)": "Minimum X (mm)",
    "Y mínimo (mm)": "Minimum Y (mm)",
    "Largura (mm)": "Width (mm)",
    "Altura (mm)": "Height (mm)",
    "Manter proporção ao alterar dimensões": "Keep proportions when changing dimensions",
    "Quando marcado, a última dimensão editada (largura ou altura) define a outra. Desmarcado permite medidas independentes.": "When enabled, the last edited dimension (width or height) defines the other. Disable it for independent dimensions.",
    "Propriedades da forma": "Shape properties",
    "Raio (mm)": "Radius (mm)",
    "Raio X (mm)": "X radius (mm)",
    "Raio Y (mm)": "Y radius (mm)",
    "Rotação (°)": "Rotation (°)",
    "Aplicar medidas": "Apply dimensions",
    "Escala uniforme (%)": "Uniform scale (%)",
    "Aplicar escala": "Apply scale",
    "Escala toda a seleção de forma uniforme pelo centro. 100% mantém o tamanho atual; 50% reduz à metade; 200% dobra.": "Uniformly scales the entire selection around its centre. 100% keeps the current size; 50% halves it; 200% doubles it.",
    "Aplica a porcentagem como uma única alteração com Undo.": "Applies the percentage as one undoable change.",
    "Selecione um vetor.": "Select a vector.",
    "Cada linha é uma peça externa; furos e recortes internos seguem junto.": "Each row is an outer part; holes and internal cutouts stay with it.",
    "Selecione percursos já aplicados abaixo para simular mesmo sem manter os vetores originais selecionados.": "Select previously applied toolpaths below to simulate without keeping the original vectors selected.",
    "Nenhum percurso aplicado ainda": "No applied toolpath yet",
    "Percursos aplicados": "Applied toolpaths",
    "Renomear": "Rename",
    "Renomear operação": "Rename operation",
    "Novo nome": "New name",
    "Editar": "Edit",
    "Excluir operações": "Delete operations",
    "Informe um nome para a operação.": "Enter a name for the operation.",
    "Renomear a operação selecionada": "Rename the selected operation",
    "Abrir os parâmetros da operação selecionada sem recalcular o percurso": "Open the selected operation parameters without recalculating the toolpath",
    "Excluir as operações selecionadas; o Undo do FreeCAD pode restaurá-las": "Delete the selected operations; FreeCAD Undo can restore them",
    "Selecione os percursos que deseja simular ou exportar. Arraste para definir a ordem real do G-code.": "Select the toolpaths to simulate or export. Drag to define the actual G-code order.",
    "Tempo estimado: —": "Estimated time: —",
    "Parar simulação": "Stop simulation",
    "Arraste durante a simulação: a velocidade muda imediatamente, sem reiniciar nem saltar o percurso. Faixa de 0,1x a 200x.": "Drag during simulation: speed changes immediately without restarting or skipping the toolpath. Range: 0.1x to 200x.",
    "Exportação": "Export",
    "Arquivo G-code": "G-code file",
    "Escolher...": "Choose…",
    "Escolher a pasta e o nome do arquivo G-code": "Choose the folder and G-code filename",
    "Arquivos separados": "Separate files",
    "Arquivo único": "Single file",
    "Quando houver vários percursos": "When there are multiple toolpaths",
    "Arquivos separados recebem sufixo automaticamente; arquivo único concatena os percursos na ordem exibida. Sem seleção, todos são exportados.": "Separate files receive an automatic suffix; a single file concatenates toolpaths in the displayed order. With no selection, all are exported.",
    "Gerar o G-code dos percursos selecionados; sem seleção, gerar todos na ordem exibida": "Generate G-code for the selected toolpaths; with no selection, generate all in the displayed order",
    "Fluxo recomendado": "Recommended workflow",
    "1. Configure Trabalho e Material.  2. Abra Corte, Furo, Preenchimento, Desbaste 3D ou Acabamento 3D.  3. Use Aplicar para guardar a operação na árvore, Pré-visualizar/Simular pelos botões abaixo e exporte o G-code nesta página.": "1. Configure Job and Material.  2. Open Cut, Hole, Pocket, 3D Roughing, or 3D Finishing.  3. Use Apply to store the operation in the tree, use Preview/Simulate below, and export G-code on this page.",
    "Excluir %d operação(ões)? O Undo do FreeCAD pode restaurá-las.": "Delete %d operation(s)? FreeCAD Undo can restore them.",
    "Não foi possível salvar a nova ordem: %s": "Could not save the new order: %s",
    "Não foi possível renomear a operação: %s": "Could not rename the operation: %s",
    "Não foi possível excluir as operações: %s": "Could not delete the operations: %s",
    "Configura ou mostra no plano os percursos exatos de Corte, Furos e Rebaixo.": "Configure or display the exact Cut, Hole, and Pocket toolpaths in the plan.",
    "Ocultar percurso 2D": "Hide 2D toolpath",
    "Analisa ferramenta, passes, alturas e acabamento. Não altera parâmetros nem G-code.": "Analyzes the tool, passes, heights, and finish. Does not change parameters or G-code.",
    "A barra vai de 0 até a espessura do material e calcula a folga oposta.": "The bar runs from 0 to the material thickness and calculates the opposite clearance.",
    "Ajuste intencional da medida final. Valor positivo deixa material (corte menor); negativo remove material extra. Use 0 para seguir o desenho.": "Intentional final-size adjustment. A positive value leaves material (smaller cut); a negative value removes extra material. Use 0 to follow the drawing.",
    "Analisa cada peça, evita cantos e exige ao menos quatro regiões afastadas e mecanicamente equilibradas. Peças maiores recebem tabs adicionais para limitar o trecho de perímetro sem retenção. Na Linha comum estas regras de segurança são obrigatórias.": "Analyzes each part, avoids corners, and requires at least four separated, mechanically balanced regions. Larger parts receive additional tabs to limit unsupported perimeter span. These safety rules are mandatory for common-line cutting.",
    "Ativa a marcação de tabs clicando diretamente no contorno da vista 2D.": "Enables tab placement by clicking directly on the contour in the 2D view.",
    "Ativar furação faseada": "Enable peck drilling",
    "Furação faseada": "Peck drilling",
    "Folga de retração (mm)": "Retract clearance (mm)",
    "Tempo de permanência (s)": "Dwell time (s)",
    "Permanência no fundo": "Dwell at bottom",
    "Permanecer no fundo": "Stay at bottom",
    "Quando houver espaço, desce em hélice e abre o diâmetro por passadas circulares. Com ferramenta igual ou maior, usa descida vertical por etapas.": "When space allows, plunges helically and opens the diameter with circular passes. With an equal or larger tool, uses staged vertical plunges.",
    "Usar profundidade de cada furo do modelo": "Use each hole depth from the model",
    "Bordas coincidentes — vetor = centro da fresa": "Coincident edges — vector = tool center",
    "Corta uma fronteira reta compartilhada somente uma vez. O WoodCAM comprova a coincidência geométrica antes de gerar o percurso; cruzamentos e sobreposições de área continuam bloqueados.": "Cuts a shared straight boundary only once. WoodCAM verifies geometric coincidence before generating the toolpath; crossings and area overlaps remain blocked.",
    "Desvio máximo para considerar duas linhas-centro coincidentes. Use uma tolerância pequena; ela não autoriza cruzamentos.": "Maximum deviation for considering two centerlines coincident. Use a small tolerance; it does not permit crossings.",
    "Externo preserva a medida da peça; sobre a linha usa o centro da fresa; interno preserva a medida do vão.": "Outside preserves part size; on-line uses the tool center; inside preserves opening size.",
    "Preservar medidas compara os percursos já compensados e exige entre as peças uma folga igual ao diâmetro efetivo. Bordas coincidentes corta sobre os vetores em comum e, portanto, altera cada medida em meio diâmetro de fresa.": "Preserve dimensions compares compensated toolpaths and requires a gap equal to the effective diameter. Coincident edges cuts on the shared vectors and therefore changes each size by half the tool diameter.",
    "Executa primeiro um corte com sobre-metal e depois uma passada final na medida.": "Runs an allowance cut first, then a final pass at the target size.",
    "Material deixado pelo desbaste e removido somente na passada final de acabamento. Use um valor pequeno.": "Material left by roughing and removed only by the final finishing pass. Use a small value.",
    "Divide o percurso antes e depois do vértice para a máquina chegar ao canto com menor velocidade.": "Splits the toolpath before and after the vertex so the machine reaches the corner at a lower feed.",
    "Comprimento (mm)": "Length (mm)",
    "Comprimento da rampa (mm)": "Ramp length (mm)",
    "Distância da rampa (mm)": "Ramp distance (mm)",
    "Descida por volta (mm)": "Descent per turn (mm)",
    "Folga no modelo / sobremetal (mm)": "Model clearance / allowance (mm)",
    "Folgas de Z rápido acima do material": "Rapid-Z clearances above material",
    "Z inicial (usa Z1)": "Initial Z (uses Z1)",
    "Z1 deve ficar acima de Z2. O G-code usa Z1 para retrações longas e Z2 como plano seguro próximo do material.": "Z1 must be above Z2. G-code uses Z1 for long retracts and Z2 as the safe plane near the material.",
    "Z-zero": "Z zero",
    "Datum XY do material": "Material XY datum",
    "X do datum (mm)": "Datum X (mm)",
    "Y do datum (mm)": "Datum Y (mm)",
    "X do trabalho (mm)": "Job X (mm)",
    "Y do trabalho (mm)": "Job Y (mm)",
    "X inicial (mm)": "Start X (mm)",
    "Y inicial (mm)": "Start Y (mm)",
    "Face única": "Single-sided",
    "Dupla face": "Double-sided",
    "Centro": "Center",
    "Cima": "Top",
    "Baixo": "Bottom",
    "Meio": "Middle",
    "Origem no centro": "Origin at center",
    "Origem no inferior centro": "Origin at bottom center",
    "Origem no inferior direito": "Origin at bottom right",
    "Origem no inferior esquerdo": "Origin at bottom left",
    "Origem no meio direito": "Origin at middle right",
    "Origem no meio esquerdo": "Origin at middle left",
    "Origem no superior centro": "Origin at top center",
    "Origem no superior direito": "Origin at top right",
    "Origem no superior esquerdo": "Origin at top left",
    "Datum no centro": "Datum at center",
    "Datum no inferior centro": "Datum at bottom center",
    "Datum no inferior direito": "Datum at bottom right",
    "Datum no inferior esquerdo": "Datum at bottom left",
    "Datum no meio direito": "Datum at middle right",
    "Datum no meio esquerdo": "Datum at middle left",
    "Datum no superior centro": "Datum at top center",
    "Datum no superior direito": "Datum at top right",
    "Datum no superior esquerdo": "Datum at top left",
    "Posição do modelo no material": "Model position in material",
    "Limite do modelo": "Model boundary",
    "Fronteira de usinagem": "Machining boundary",
    "Fronteira": "Boundary",
    "Geometria": "Geometry",
    "Fonte: usa a seleção atual": "Source: uses the current selection",
    "Selecione um relevo, STL, malha ou sólido 3D na árvore ou na vista. A fonte nunca é modificada.": "Select a relief, STL, mesh, or 3D solid in the tree or view. The source is never modified.",
    "Arraste para posicionar o modelo entre 0 e a espessura do material.": "Drag to position the model between 0 and the material thickness.",
    "Áreas com ilhas internas usam raster automaticamente para preservar os contornos.": "Areas with internal islands automatically use raster to preserve contours.",
    "Raster (zigue-zague)": "Raster (zigzag)",
    "Zigue-zague": "Zigzag",
    "Espiral": "Spiral",
    "Suave": "Smooth",
    "Nível por nível": "Level by level",
    "Nível/grupo selecionado": "Selected level/group",
    "Inverter o sentido dos passes": "Reverse pass direction",
    "Passe final de perfil": "Final profile pass",
    "Perfil no primeiro passe": "Profile on first pass",
    "Perfil no último passe": "Profile on last pass",
    "Sem passe de perfil": "No profile pass",
    "Sem perfil": "No profile",
    "Parâmetros de corte": "Cutting parameters",
    "Número da ferramenta": "Tool number",
    "Interno (%)": "Inside (%)",
    "Positivo: externo; negativo: interno": "Positive: outside; negative: inside",
    "Otimizar entrada para evitar cantos e reduzir deslocamento": "Optimize entry to avoid corners and reduce travel",
    "Otimizar menor deslocamento": "Optimize shortest travel",
    "Otimizar pontos iniciais": "Optimize start points",
    "Usar ordem da seleção": "Use selection order",
    "Usar ordem de seleção dos vetores": "Use vector selection order",
    "Quando houver vários percursos": "When there are multiple toolpaths",
    "Perfil": "Profile",
    "Acabamento": "Finishing",
    "Fronteira de usinagem": "Machining boundary",
    "Seleção de vetores: Manual": "Vector selection: Manual",
    "Vetores selecionados": "Selected vectors",
    "Selecione um vetor ou grupo de vetores.": "Select a vector or vector group.",
    "Papel do contorno": "Contour role",
    "Detectar pela peça": "Detect from part",
    "Recorte / furo interno": "Internal cutout / hole",
    "Contorno externo / entalhe": "Outer contour / notch",
    "Lado T-bone": "T-bone side",
    "Lado de entrada": "Incoming side",
    "Lado de saída": "Outgoing side",
    "Rota da emenda": "Splice route",
    "Curto": "Short",
    "Longo": "Long",
    "Tolerância unir": "Join tolerance",
    "Tolerância para unir pontas; não é o raio de captura do mouse.": "Tolerance for joining endpoints; it is not the mouse capture radius.",
    "Passe o mouse na geometria; magenta é somente prévia. Clique aplica.": "Hover over the geometry; magenta is preview only. Click to apply.",
    "Transformar / alinhar": "Transform / align",
    "Ângulo": "Angle",
    "Girar": "Rotate",
    "Espelho H ↕": "Mirror H ↕",
    "Espelho V ↔": "Mirror V ↔",
    "Alinhar seleção": "Align selection",
    "Distribuir": "Distribute",
    "Copiar em matriz": "Array copy",
    "Copiar": "Copy",
    "Colar": "Paste",
    "Selecione uma peça ou vetor antes de copiar.": "Select a part or vector before copying.",
    "Nada copiado ainda. Selecione e use Ctrl+C primeiro.": "Nothing has been copied yet. Select an object and press Ctrl+C first.",
    "%d objeto(s) copiado(s). Ctrl+V cola a cópia com deslocamento visível.": "%d object(s) copied. Ctrl+V pastes with a visible offset.",
    "%d objeto(s) colado(s). A peça inteira foi preservada; Ctrl+Z desfaz.": "%d object(s) pasted. The whole part was preserved; Ctrl+Z undoes it.",
    "Copiar matriz": "Copy array",
    "Colunas": "Columns",
    "Reparos / filetes": "Repairs / fillets",
    "Usar para novos vetores": "Use for new vectors",
    "Manter pontos atuais": "Keep current points",
    "Mover pontos para perto do ponto escolhido": "Move points near the selected point",
    "Alinhar os vetores selecionados entre si": "Align selected vectors to each other",
    "Espelhar sobre a linha horizontal no centro da seleção": "Mirror across the horizontal line at the selection center",
    "Espelhar sobre a linha vertical no centro da seleção": "Mirror across the vertical line at the selection center",
    "Distribuir automaticamente": "Distribute automatically",
    "Cria cópias exatas da seleção; furos e recortes de uma peça agrupada acompanham a chapa.": "Creates exact copies of the selection; holes and cutouts in a grouped part follow the sheet.",
    "Cria pontes automáticas ao redor dos contornos externos para segurar a peça.": "Creates automatic tabs around outer contours to hold the part.",
    "Sobe e desce em rampa ao longo da tab, sem movimentos verticais bruscos.": "Ramps up and down along the tab without abrupt vertical moves.",
    "Equivale ao ponto inicial inteligente atual: evita começar exatamente no vértice.": "Equivalent to the current smart start point: avoids starting exactly at a vertex.",
    "Escolhe qual trecho do contorno fechado será mantido na emenda.": "Chooses which section of the closed contour is kept in the splice.",
    "Reconhece a topologia existente: cada contorno externo vira uma peça e os contornos contidos viram furos ou recortes. Não cria geometria.": "Recognizes existing topology: each outer contour becomes a part and contained contours become holes or cutouts. Does not create geometry.",
    "Folga mínima entre peças no nesting, em milímetros. Use a folga que precisa sobrar entre os cortes/fresa.": "Minimum nesting gap between parts in millimeters. Use the clearance required between cuts/toolpaths.",
    "Espaçamento visual da grade e do encaixe na grade, em milímetros.": "Visual grid and grid-snap spacing in millimeters.",
    "Atrai o cursor para pontas, centros, interseções e geometria. A Grade possui controle separado.": "Snaps the cursor to endpoints, centers, intersections, and geometry. Grid has a separate control.",
    "Grade": "Grid",
    "Mostrar os quadradinhos e encaixar na grade; desmarque para fundo branco e movimento livre.": "Show the grid squares and snap to grid; disable for a white background and free movement.",
    "Durante desenho e medição, prende ao horizontal, vertical e a múltiplos de 15° a partir do último ponto. Shift ignora todo snap.": "While drawing and measuring, constrains to horizontal, vertical, and 15° increments from the last point. Shift bypasses all snapping.",
    "Arraste para aumentar ou reduzir o painel lateral": "Drag to resize the side panel",
    "Arraste em uma área vazia da janela para mover.": "Drag an empty area of the window to move it.",
    "Arraste para redimensionar a janela": "Drag to resize the window",
    "Importar itens da árvore": "Import items from tree",
    "Importar peças planas pelo PanelNest": "Import flat parts through PanelNest",
    "Vetorizar imagem": "Vectorize image",
    "Criar relevo 3D por imagem": "Create 3D relief from image",
    "Ajustar arcos/círculos aos vetores": "Fit arcs/circles to vectors",
    "Criar contorno (offset)": "Create contour (offset)",
    "Limpar sobrelinhas/duplicados": "Clean overlines/duplicates",
    "Produz uma prévia com menos tentativas e tempo-alvo curto; mantém contorno real, furos vinculados e Undo.": "Produces a preview with fewer attempts and a short target time; preserves real contours, linked holes, and Undo.",
    "Mostra rapidamente a primeira prévia e continua comparando MaxRects e contorno real em segundo plano até o tempo-alvo.": "Shows the first preview quickly and keeps comparing MaxRects and real contours in the background until the target time.",
    "Explora mais ordens de encaixe e refinamentos enquanto mostra a melhor prévia encontrada; o tempo-alvo pode ser ajustado.": "Explores more nesting orders and refinements while showing the best preview found; the target time can be adjusted.",
    "Planejado para uma próxima etapa.": "Planned for a future stage.",
    "Preparado para etapa futura; o corte atual já usa entrada inteligente por percurso.": "Prepared for a future stage; the current cut already uses smart entry per toolpath.",
    "Preparado para seleção manual futura.": "Prepared for future manual selection.",
    "Notas": "Notes",
    "Nova": "New",
    "Cria uma operação persistente e numerada na árvore do documento.": "Creates a persistent numbered operation in the document tree.",
    "Sair da edição sem alterar o percurso aplicado.": "Leave editing without changing the applied toolpath.",
    "Edição cancelada; o percurso aplicado foi preservado.": "Editing canceled; the applied toolpath was preserved.",
    "Dir.": "Right",
    "Esq.": "Left",
    "Raio": "Radius",
    "Topo esférico (Ball Nose)": "Ball nose",
    "Usa a Folga Z1 / retração configurada acima.": "Uses the Z1 clearance / retract configured above.",
    "Usar contorno selecionado como referência": "Use selected contour as reference",
    "movimento": "move",
    "movimentos": "moves",
    "DESATUALIZADA": "OUTDATED",
    "Aplicar prévia automática": "Apply automatic preview",
    "Automático usa a peça classificada ou a contenção dos contornos. Escolha manualmente somente quando o desenho ainda não estiver classificado.": "Automatic uses the classified part or contour containment. Choose manually only when the drawing has not been classified yet.",
    "Corte lateral, perfil 2D e rebaixo com fundo plano. É a fresa padrão para cortar MDF e chapas.\nDiâmetro D define o raio mínimo interno: cantos internos ficam arredondados por D/2.": "Side cutting, 2D profiling, and flat-bottom pockets. This is the standard tool for cutting MDF and sheet goods.\nDiameter D defines the minimum inside radius: inside corners are rounded to D/2.",
    "Profundidade por região": "Depth by region",
    "Executa somente os furos circulares. O corte dos contornos internos continua disponível separadamente na aba Corte.": "Runs circular holes only. Internal contours remain available separately on the Cut tab.",
    "Avanços e rotação": "Feeds and spindle speed",
    "Enquadrar área de Trabalho (F)": "Fit Job area (F)",
    "Altura Y (mm; 0 = usar seleção)": "Y height (mm; 0 = use selection)",
    "Altura Z (mm; 0 = usar Material)": "Z height (mm; 0 = use Material)",
    "Linhas comuns: internos primeiro; fronteira compartilhada uma vez; StockTabs e SharedTabs são distribuídas juntas, em regiões opostas e longe das quinas; uma SharedTab pode segurar duas peças, mas a rede continua ancorada ao stock.": "Common lines: internal contours first; shared boundary once; StockTabs and SharedTabs are distributed together, in opposing regions and away from corners; one SharedTab can retain two parts, while the network remains anchored to the stock.",
    "Folga do modelo no material em milímetros.": "Model clearance within the material in millimeters.",
    "O Editor 2D está aberto em uma janela própria.": "The 2D Editor is open in its own window.",
    "Ocultar somente o painel lateral": "Hide the side panel only",
    "Ordem dos furos": "Hole order",
    "Passo de furação (mm)": "Drilling stepdown (mm)",
    "Quantidade automática": "Automatic quantity",
    "Rampa padrão (mm)": "Default ramp (mm)",
    "Selecione o mesmo relevo, STL, malha ou sólido usado no desbaste. Fresa de topo esférico é recomendada.": "Select the same relief, STL, mesh, or solid used for roughing. A ball-nose tool is recommended.",
    "Estratégia de acabamento": "Finishing strategy",
    "Estratégia de desbaste": "Roughing strategy",
    "Tipo de trabalho": "Job type",
    "Usa os vetores persistidos do Editor 2D como fonte das operações CAM; desmarcado, o WoodCAM continua usando a seleção do FreeCAD.": "Uses the persistent 2D Editor vectors as the source for CAM operations; when disabled, WoodCAM continues using the FreeCAD selection.",
    "Largura X (mm; 0 = usar seleção)": "X width (mm; 0 = use selection)",
    "Tamanhos salvos": "Saved sizes",
    "Selecionar tamanho salvo…": "Select a saved size…",
    "Salvar tamanho atual…": "Save current size…",
    "Salvar tamanho da área": "Save work area size",
    "Nome da predefinição": "Preset name",
    "Tamanho não salvo": "Size not saved",
    "Excluir tamanho salvo": "Delete saved size",
    "Escolha uma área usada com frequência. A predefinição preenche X, Y e Z sem alterar origem, material ou geometria das peças.": "Choose a frequently used area. The preset fills X, Y, and Z without changing the origin, material, or part geometry.",
    "Salvar os valores atuais de Largura X, Altura Y e Altura Z para reutilizar.": "Save the current X width, Y height, and Z height for reuse.",
    "Excluir a predefinição selecionada; não altera a área atual.": "Delete the selected preset; the current area is unchanged.",
    "Largura e altura precisam ser maiores que zero; Z não pode ser negativo.": "Width and height must be greater than zero; Z cannot be negative.",
    "Sugerir linha-guia para separar o maior retalho retangular": "Suggest a guide line to separate the largest rectangular remnant",
    "Criar linhas de corte para separar retalhos retangulares": "Create cut lines to separate rectangular remnants",
    "Selecione somente linhas de separação de retalho ou somente contornos de peças.": "Select only remnant separation lines or only part contours.",
    "Linhas de separação de retalho são usinadas somente pela operação Corte.": "Remnant separation lines are machined only by the Cut operation.",
    "A linha de separação selecionada não possui extensão válida.": "The selected separation line has no valid length.",
    "Selecione somente linhas de separação de retalho para criar este percurso.": "Select only remnant separation lines to create this toolpath.",
    "A linha aparece na prévia e fica salva com o layout, mas não entra no G-code automaticamente.": "The line appears in the preview and is saved with the layout, but it is not added to G-code automatically.",
    "As linhas aparecem na prévia e viram vetores selecionáveis. Para usinar, selecione-as e crie uma operação Corte; nada entra no G-code automaticamente.": "The lines appear in the preview and become selectable vectors. To machine them, select them and create a Cut operation; nothing is added to G-code automatically.",
    "Ignora sobras cujo menor lado seja inferior a este tamanho.": "Ignores remnants whose shortest side is smaller than this size.",
    "Menor lado do retalho guardável": "Shortest side of a storeable remnant",
    "; %d retalho(s) retangular(es) sugerido(s), %.2f m² no total": "; %d rectangular remnant(s) suggested, %.2f m² total",
    "Retalho %.0f × %.0f mm · %.2f m²": "Remnant %.0f × %.0f mm · %.2f m²",
    "⚠ DESATUALIZADA — ": "⚠ OUTDATED — ",
    "Selecionar — 1 clique seleciona; arraste move o corpo": "Select — one click selects; drag moves the object",
    "Transformar — alças redimensionam; arraste move a seleção": "Transform — handles resize; drag moves the selection",
    "Medir — dois pontos com Snap; não altera o desenho": "Measure — two points with Snap; does not alter the drawing",
    "Nós — clique seleciona; somente arrastar move": "Nodes — click selects; only dragging moves",
    "Linha — clique no início e no fim": "Line — click the start and end",
    "Polilinha — cliques; Enter termina; Tab fecha": "Polyline — click points; Enter finishes; Tab closes",
    "Retângulo — dois cantos": "Rectangle — two corners",
    "Círculo — centro e raio": "Circle — center and radius",
    "Elipse — centro, raio X e raio Y": "Ellipse — center, X radius, and Y radius",
    "Arco — início, ponto intermediário e fim": "Arc — start, intermediate point, and end",
    "Bézier — início, controle 1, controle 2 e fim": "Bézier — start, control 1, control 2, and end",
    "Polígono — centro e raio": "Polygon — center and radius",
    "Estrela — centro e ponta externa": "Star — center and outer point",
    "Trim — prévia no hover; clique aplica": "Trim — hover previews; click applies",
    "Extend — prévia na ponta; clique aplica": "Extend — endpoint previews; click applies",
    "Offset — prévia do contorno; clique aplica": "Offset — contour previews; click applies",
    "Filete — prévia no canto; clique aplica": "Fillet — corner previews; click applies",
    "Dogbone — somente canto interno de 90°": "Dogbone — 90° inside corners only",
    "T-bone — somente canto interno de 90°": "T-bone — 90° inside corners only",
    "Unir 2 pontas — escolha exatamente as duas pontas": "Join 2 endpoints — select exactly two endpoints",
    "Unir 2 pontas suave — escolha exatamente as duas pontas": "Smoothly join 2 endpoints — select exactly two endpoints",
    "Projetar ponta — escolha a ponta e depois uma reta/curva alvo": "Project endpoint — select the endpoint and then a target line/curve",
    "Emendar — escolha caminho aberto e contorno alvo": "Splice — select an open path and a target contour",
    "Dogbone automático — revisar prévia total": "Automatic dogbone — review the full preview",
    "T-bone automático — revisar prévia total": "Automatic T-bone — review the full preview",
    "Clique no desenho para posicionar.": "Click the drawing to place the point.",
})


# Phrases/prefixes cover status messages and labels assembled with values at
# runtime (for example ``Última: ...`` and ``Tempo estimado: ...``).
_PREFIX_EN = (
    ("Organização não executada: ", "Nesting was not run: "),
    ("O nesting foi bloqueado pela validação vetorial final: ", "Final vector validation blocked the layout: "),
    ("O nesting retangular produziu geometria inválida: ", "Rectangular nesting produced invalid geometry: "),
    ("Última: ", "Last: "),
    ("Editando: ", "Editing: "),
    ("Modo: ", "Mode: "),
    ("Tempo estimado: ", "Estimated time: "),
    ("Configuração: ", "Configuration: "),
    ("Última operação: ", "Last operation: "),
    ("Passo ", "Pass "),
    ("Profundidade ", "Depth "),
)

_PHRASE_EN = tuple(
    sorted(
        PT_EN.items(),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
)


def _stored_language():
    try:
        import FreeCAD

        value = FreeCAD.ParamGet(_PARAMETER_PATH).GetString(_LANGUAGE_KEY, "pt")
        return value if value in {"pt", "en"} else "pt"
    except Exception:
        return "pt"


_language = _stored_language()
_roots = []
_dynamic_timer = None


def language():
    return _language


def translate_text(value, code=None):
    """Translate a UI string, preserving values not in the catalogue."""

    text = "" if value is None else str(value)
    if (code or _language) != "en" or not text:
        return text
    blocked_nesting = re.fullmatch(
        r"Organização não executada: O nesting foi bloqueado pela validação "
        r"vetorial final: As peças (\S+) e (\S+) ficaram com folga de "
        r"([0-9.,]+) mm; a folga mínima é ([0-9.,]+) mm\.",
        text,
    )
    if blocked_nesting:
        first, second, clearance, minimum = blocked_nesting.groups()
        return (
            "Nesting was not run: final vector validation blocked the layout: "
            "parts %s and %s ended with %s mm clearance; the minimum clearance "
            "is %s mm."
            % (first, second, clearance, minimum)
        )
    insufficient_clearance = re.fullmatch(
        r"As peças (\S+) e (\S+) ficaram com folga de ([0-9.,]+) mm; "
        r"a folga mínima é ([0-9.,]+) mm\.",
        text,
    )
    if insufficient_clearance:
        first, second, clearance, minimum = insufficient_clearance.groups()
        return (
            "Parts %s and %s ended with %s mm clearance; the minimum clearance "
            "is %s mm."
            % (first, second, clearance, minimum)
        )
    common_line_blocked = re.fullmatch(
        r"Linha comum bloqueada: os contornos originais são válidos, mas os "
        r"percursos externos compensados se cruzam\. A folga entre algumas "
        r"peças é menor que o diâmetro efetivo de ([0-9.,]+) mm\. Execute "
        r"Organizar peças novamente com Linha comum ativa; o organizador "
        r"ajustará essa folga automaticamente\. Detalhes: (.*)",
        text,
        flags=re.DOTALL,
    )
    if common_line_blocked:
        diameter, details = common_line_blocked.groups()
        details = re.sub(
            r"Os contornos (\S+) e (\S+) possuem cruzamento real próximo de "
            r"X ([^ /;]+) / Y ([^.;]+)\.",
            r"Contours \1 and \2 have a real crossing near X \3 / Y \4.",
            details,
        )
        return (
            "Common-line cutting blocked: the original contours are valid, "
            "but the compensated outside toolpaths cross. The gap between "
            "some parts is smaller than the %s mm effective diameter. Run "
            "Smart nesting again with common-line cutting enabled; the "
            "organizer will adjust this gap automatically. Details: %s"
            % (diameter, details)
        )
    match = re.fullmatch(
        r"1 externo \+ (\d+) interno\(s\) \+ (\d+) marcação\(ões\)",
        text,
    )
    if match:
        return "1 outer + %s inner + %s marking(s)" % match.groups()
    match = re.fullmatch(
        r"Contorno externo \+ (\d+) furo\(s\)/recorte\(s\) \+ "
        r"(\d+) marcação\(ões\)\.",
        text,
    )
    if match:
        return "Outer contour + %s hole(s)/cutout(s) + %s marking(s)." % match.groups()
    narrow_prefix = "Fresa maior que "
    narrow_suffix = " região(ões) selecionada(s)"
    if text.startswith(narrow_prefix) and text.endswith(narrow_suffix):
        count = text[len(narrow_prefix):-len(narrow_suffix)]
        if count.isdigit():
            return "Tool larger than %s selected region(s)" % count
    pass_count = text.split(" ", 1)
    if (
        len(pass_count) == 2
        and pass_count[0].isdigit()
        and pass_count[1] in {"passagem", "passagems", "passagens"}
    ):
        return "%s %s" % (
            pass_count[0],
            "pass" if pass_count[0] == "1" else "passes",
        )
    selection_count = text.split(" ", 1)
    if (
        len(selection_count) == 2
        and selection_count[0].isdigit()
        and selection_count[1] in {"selecionado", "selecionados"}
    ):
        return "%s selected" % selection_count[0]
    exact = PT_EN.get(text)
    if exact is not None:
        return exact
    for source, target in _PREFIX_EN:
        if text.startswith(source):
            return target + translate_text(text[len(source) :], code="en")
    translated = text
    for source, target in _PHRASE_EN:
        if source in translated:
            translated = translated.replace(source, target)
    return translated


def _property(obj, name, default=None):
    try:
        value = obj.property(name)
        return default if value is None else value
    except Exception:
        return default


def _remember(obj, name, value):
    if _property(obj, name) is None:
        try:
            obj.setProperty(name, str(value))
        except Exception:
            pass


def _set_source(obj, name, value):
    try:
        obj.setProperty(name, "" if value is None else str(value))
    except Exception:
        pass


def _iter_objects(root):
    objects = [root]
    try:
        objects.extend(root.findChildren(QtCore.QObject))
    except Exception:
        pass
    return objects


def _visible_translation_objects(root, objects):
    """Limit periodic work to presentation objects the user can observe.

    A language change still performs one complete pass.  The timer exists
    only to catch legacy labels updated later at runtime; walking the nine
    hidden CAM pages every second adds GUI-thread stalls without changing any
    visible text.  Actions remain eligible because menu actions can be shown
    without their owning ``QMenu`` being visible at the instant of the pass.
    """

    action_types = tuple(
        action_type
        for action_type in (
            getattr(QtWidgets, "QAction", None),
            getattr(QtGui, "QAction", None),
        )
        if action_type is not None
    )
    visible = []
    for obj in objects:
        try:
            if action_types and isinstance(obj, action_types):
                if obj.isVisible():
                    visible.append(obj)
            elif isinstance(obj, QtWidgets.QWidget) and obj.isVisible():
                visible.append(obj)
        except (RuntimeError, AttributeError, TypeError):
            continue
    if root not in visible:
        visible.insert(0, root)
    return visible


def _mark_owned_subtree(root):
    """Make the PanelNest ownership check constant-time for this Qt tree.

    The host translator already skips a ``woodcam_i18n_owned`` subtree, but
    its compatibility implementation receives a flat ``findChildren`` list
    and otherwise has to walk the parent chain of every WoodCAM object every
    750 ms.  Marking the controls that already exist at registration keeps the
    same ownership boundary while avoiding that repeated ancestry walk.
    """

    for obj in _iter_objects(root):
        try:
            obj.setProperty("woodcam_i18n_owned", True)
        except (RuntimeError, AttributeError, TypeError):
            continue


def _sync_dynamic_sources(root, objects=None):
    """Remember text assigned by later CAM refreshes before translating it.

    A few legacy operation refreshes call ``setText`` long after construction.
    If the user is already in English those updates arrive in Portuguese.  A
    light timer notices that the value is neither the original source nor its
    current translation and adopts it as the new source string.  It never
    touches editable field values or geometry.
    """

    if objects is None:
        objects = _iter_objects(root)
    for obj in objects:
        try:
            action_types = tuple(
                action_type
                for action_type in (
                    getattr(QtWidgets, "QAction", None),
                    getattr(QtGui, "QAction", None),
                )
                if action_type is not None
            )
            if action_types and isinstance(obj, action_types):
                current = obj.text()
                source = _property(obj, "woodcam_source_text")
                if source is not None and current not in {
                    str(source),
                    translate_text(source, "en"),
                }:
                    _set_source(obj, "woodcam_source_text", current)
                continue
            if isinstance(obj, (QtWidgets.QAbstractButton, QtWidgets.QLabel)):
                current = obj.text()
                source = _property(obj, "woodcam_source_text")
                if source is not None and current not in {
                    str(source),
                    translate_text(source, "en"),
                }:
                    _set_source(obj, "woodcam_source_text", current)
            for attr in ("toolTip", "statusTip", "whatsThis", "accessibleName"):
                getter = getattr(obj, attr, None)
                if getter is None:
                    continue
                current = getter()
                source = _property(obj, "woodcam_source_" + attr)
                if source is not None and current not in {
                    str(source),
                    translate_text(source, "en"),
                }:
                    _set_source(obj, "woodcam_source_" + attr, current)
        except (RuntimeError, AttributeError, TypeError):
            continue


def _registered_refresh_roots():
    """Return live, non-overlapping roots for one presentation refresh.

    ``Editor2DWidget`` registers itself for standalone use and the complete
    WoodCAM dialog registers again after embedding it.  Refreshing both roots
    used to walk and rewrite the whole editor twice every second.  Keep all
    weak references so reparenting the editor to its detached window remains
    supported, but refresh only the outermost live root in each QObject tree.
    """

    alive = []
    candidates = []
    for reference in _roots:
        try:
            root = reference()
        except Exception:
            root = None
        if root is None:
            continue
        alive.append(reference)
        if all(root is not candidate for candidate in candidates):
            candidates.append(root)
    _roots[:] = alive

    result = []
    for root in candidates:
        current = root
        nested = False
        while current is not None:
            try:
                current = current.parent()
            except (RuntimeError, AttributeError, TypeError):
                current = None
            if any(current is candidate for candidate in candidates):
                nested = True
                break
        if not nested:
            result.append(root)
    return result


def _refresh_registered_widgets(force=False, sync_sources=True):
    # Dynamic refreshes matter only while the translated UI is active.  In the
    # default Portuguese mode the timer stays effectively free, preserving
    # the editor's existing interaction cost in large CAM dialogs.
    if _language != "en" and not force:
        return
    for root in _registered_refresh_roots():
        try:
            translate_widget_tree(
                root,
                sync_sources=sync_sources,
                visible_only=not force,
            )
        except Exception:
            continue


def _ensure_dynamic_timer():
    global _dynamic_timer
    if _dynamic_timer is not None:
        return
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    try:
        _dynamic_timer = QtCore.QTimer(app)
        _dynamic_timer.setInterval(1000)
        _dynamic_timer.timeout.connect(_refresh_registered_widgets)
        _dynamic_timer.start()
    except Exception:
        _dynamic_timer = None


def _translate_action(action):
    source = _property(action, "woodcam_source_text")
    if source is None:
        source = action.text()
        _remember(action, "woodcam_source_text", source)
    translated = translate_text(source)
    if action.text() != translated:
        action.setText(translated)
    for attr in ("toolTip", "statusTip", "whatsThis", "accessibleDescription"):
        getter = getattr(action, attr, None)
        setter = getattr(action, "set" + attr[0].upper() + attr[1:], None)
        if getter is None or setter is None:
            continue
        prop = "woodcam_source_" + attr
        source_value = _property(action, prop)
        if source_value is None:
            source_value = getter()
            _remember(action, prop, source_value)
        translated = translate_text(source_value)
        if getter() != translated:
            setter(translated)


def _translate_combo(combo):
    for index in range(combo.count()):
        source = combo.itemData(index, _ITEM_SOURCE_ROLE)
        if source is None:
            source = combo.itemText(index)
            combo.setItemData(index, source, _ITEM_SOURCE_ROLE)
        translated = translate_text(source)
        if combo.itemText(index) != translated:
            combo.setItemText(index, translated)


def _translate_item_view(view):
    """Translate static and dynamic list/tree/table cells without touching IDs."""

    model = view.model()
    if model is None:
        return
    display_role = int(QtCore.Qt.DisplayRole)

    # QListWidget uses Qt's private QListModel.  PySide exposes its no-argument
    # row/column count overloads but rejects the QModelIndex overload that the
    # public QAbstractItemModel API normally offers.  Trees/tables accept both.
    def model_count(method_name, parent=None):
        if isinstance(view, QtWidgets.QListView):
            if parent is not None and parent.isValid():
                return 0
            if method_name == "columnCount":
                return 1
        method = getattr(model, method_name)
        if parent is not None and parent.isValid():
            try:
                return method(parent)
            except TypeError:
                return 0
        try:
            return method(QtCore.QModelIndex())
        except TypeError:
            return method()

    def translate_index(parent=QtCore.QModelIndex()):
        for row in range(model_count("rowCount", parent)):
            first = None
            for column in range(model_count("columnCount", parent)):
                index = model.index(row, column, parent)
                if first is None:
                    first = index
                current = model.data(index, display_role)
                if not isinstance(current, str) or not current:
                    continue
                source = model.data(index, _ITEM_SOURCE_ROLE)
                if source is None or current not in {
                    str(source),
                    translate_text(source, "en"),
                }:
                    source = current
                    model.setData(index, source, _ITEM_SOURCE_ROLE)
                translated = translate_text(source)
                if current != translated:
                    model.setData(index, translated, display_role)
            if first is not None and model_count("rowCount", first):
                translate_index(first)

    translate_index()
    for orientation, count in (
        (QtCore.Qt.Horizontal, model_count("columnCount")),
        (QtCore.Qt.Vertical, model_count("rowCount")),
    ):
        for section in range(count):
            current = model.headerData(section, orientation, display_role)
            if not isinstance(current, str) or not current:
                continue
            source = model.headerData(section, orientation, _ITEM_SOURCE_ROLE)
            if source is None or current not in {
                str(source),
                translate_text(source, "en"),
            }:
                source = current
                model.setHeaderData(section, orientation, source, _ITEM_SOURCE_ROLE)
            translated = translate_text(source)
            if current != translated:
                model.setHeaderData(section, orientation, translated, display_role)


def _translate_tabs(tabs):
    for index in range(tabs.count()):
        # TabData is also inspected by the broad PanelNest translator. Keep a
        # private, immutable Portuguese source and expose the translated text
        # in both TabData and TabText. That makes every later host refresh
        # idempotent instead of alternating labels with icon-only tabs.
        source_prop = "woodcam_tab_source_%d" % index
        source = _property(tabs, source_prop)
        page = tabs.widget(index)
        if source is None and page is not None:
            source = _property(page, "woodcam_tab_title")
        if source is None:
            source = tabs.tabBar().tabData(index)
        if hasattr(source, "toString"):
            try:
                source = source.toString()
            except Exception:
                pass
        if source is None or source == "":
            source = tabs.tabText(index)
        if not isinstance(source, str):
            source = str(source)
        if source:
            _remember(tabs, source_prop, source)
        translated = translate_text(source)
        if tabs.tabBar().tabData(index) != translated:
            tabs.tabBar().setTabData(index, translated)
        if tabs.tabText(index) != translated:
            tabs.setTabText(index, translated)
        tooltip = tabs.tabToolTip(index)
        if tooltip:
            prop = "woodcam_tab_tooltip_%d" % index
            source_tip = _property(tabs, prop)
            if source_tip is None:
                _remember(tabs, prop, tooltip)
                source_tip = tooltip
            translated_tip = translate_text(source_tip)
            if tabs.tabToolTip(index) != translated_tip:
                tabs.setTabToolTip(index, translated_tip)


def translate_widget_tree(root, *, sync_sources=True, visible_only=False):
    """Apply the selected language to a widget tree without changing state."""

    if root is None:
        return
    objects = _iter_objects(root)
    if visible_only:
        objects = _visible_translation_objects(root, objects)
    if sync_sources:
        _sync_dynamic_sources(root, objects)
    for obj in objects:
        try:
            action_types = tuple(
                action_type
                for action_type in (
                    getattr(QtWidgets, "QAction", None),
                    getattr(QtGui, "QAction", None),
                )
                if action_type is not None
            )
            if action_types and isinstance(obj, action_types):
                _translate_action(obj)
                continue
            if isinstance(obj, QtWidgets.QTabWidget):
                _translate_tabs(obj)
            if isinstance(obj, QtWidgets.QComboBox):
                _translate_combo(obj)
            if isinstance(obj, QtWidgets.QAbstractItemView):
                _translate_item_view(obj)
            if isinstance(obj, QtWidgets.QGroupBox):
                source = _property(obj, "woodcam_source_title")
                if source is None:
                    source = obj.title()
                    _remember(obj, "woodcam_source_title", source)
                translated = translate_text(source)
                if obj.title() != translated:
                    obj.setTitle(translated)
            if isinstance(obj, QtWidgets.QAbstractButton):
                source = _property(obj, "woodcam_source_text")
                if source is None:
                    source = obj.text()
                    _remember(obj, "woodcam_source_text", source)
                translated = translate_text(source)
                if obj.text() != translated:
                    obj.setText(translated)
            if isinstance(obj, QtWidgets.QLabel):
                source = _property(obj, "woodcam_source_text")
                if source is None:
                    source = obj.text()
                    _remember(obj, "woodcam_source_text", source)
                translated = translate_text(source)
                if obj.text() != translated:
                    obj.setText(translated)
            if isinstance(obj, (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
                getter = getattr(obj, "placeholderText", None)
                setter = getattr(obj, "setPlaceholderText", None)
                if getter is not None and setter is not None:
                    source = _property(obj, "woodcam_source_placeholder")
                    if source is None:
                        source = getter()
                        _remember(obj, "woodcam_source_placeholder", source)
                    translated = translate_text(source)
                    if getter() != translated:
                        setter(translated)
            if isinstance(obj, QtWidgets.QToolBar):
                source = _property(obj, "woodcam_source_window_title")
                if source is None:
                    source = obj.windowTitle()
                    _remember(obj, "woodcam_source_window_title", source)
                translated = translate_text(source)
                if obj.windowTitle() != translated:
                    obj.setWindowTitle(translated)
            if isinstance(obj, QtWidgets.QWidget):
                source = _property(obj, "woodcam_source_window_title")
                if source is None:
                    source = obj.windowTitle()
                    if source:
                        _remember(obj, "woodcam_source_window_title", source)
                if source:
                    translated = translate_text(source)
                    if obj.windowTitle() != translated:
                        obj.setWindowTitle(translated)
            for attr in ("toolTip", "statusTip", "whatsThis", "accessibleName"):
                getter = getattr(obj, attr, None)
                setter = getattr(obj, "set" + attr[0].upper() + attr[1:], None)
                if getter is None or setter is None:
                    continue
                prop = "woodcam_source_" + attr
                source = _property(obj, prop)
                if source is None:
                    source = getter()
                    if source:
                        _remember(obj, prop, source)
                if source:
                    translated = translate_text(source)
                    if getter() != translated:
                        setter(translated)
        except (RuntimeError, AttributeError, TypeError):
            # A dialog can close while a language action is being dispatched.
            # Presentation translation must never interfere with its command.
            continue


def register_widget(root):
    if root is None:
        return
    # The global PanelNest translator owns the workbench chrome; this subtree
    # has a more complete WoodCAM catalogue and must be translated exactly
    # once by this module. The host recognizes this presentation-only marker.
    _mark_owned_subtree(root)
    for reference in list(_roots):
        try:
            if reference() is root:
                translate_widget_tree(root)
                return
        except Exception:
            _roots.remove(reference)
    try:
        _roots.append(weakref.ref(root))
    except TypeError:
        _roots.append(lambda root=root: root)
    translate_widget_tree(root)
    _ensure_dynamic_timer()


def set_language(code):
    """Select ``pt`` or ``en`` and refresh all open WoodCAM windows."""

    global _language
    code = "en" if str(code).lower().startswith("en") else "pt"
    _language = code
    try:
        import FreeCAD

        FreeCAD.ParamGet(_PARAMETER_PATH).SetString(_LANGUAGE_KEY, code)
    except Exception:
        pass
    # PanelNest is the host workbench for WoodCAM.  If its global selector is
    # available, keep both selectors synchronized without making PanelNest a
    # required dependency for standalone Editor 2D tests/usage.  Refresh the
    # WoodCAM tree afterwards: the broad host translator intentionally knows
    # only common PanelNest labels, while this catalogue owns the CAM tabs and
    # tooltips (for example Trabalho -> Job).
    try:
        import panelnest.i18n as panelnest_i18n

        if panelnest_i18n.language() != code:
            panelnest_i18n.set_language(code)
    except Exception:
        pass
    # PanelNest may have applied a broad/prefix translation (for example only
    # ``Remover`` -> ``Remove``).  Reuse the Portuguese sources already
    # captured by WoodCAM instead of mistaking that intermediate host text for
    # a newly-created dynamic label.
    _refresh_registered_widgets(force=True, sync_sources=False)


__all__ = ["language", "register_widget", "set_language", "translate_text", "translate_widget_tree"]
