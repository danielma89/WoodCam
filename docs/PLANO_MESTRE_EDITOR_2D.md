# Plano mestre — Editor 2D vetorial do WoodCAM

> Documento de continuidade obrigatório para desenvolver uma área de edição 2D
> inspirada no fluxo do Vectric Aspire, integrada ao WoodCAM e ao PanelNest.

**Data da decisão:** 12 de julho de 2026  
**Código-fonte ativo:** `/home/danielma/Projetos/CNC Marcenaria`  
**Instalação ativa do FreeCAD:** symlink `~/.local/share/FreeCAD/Mod/WoodCAM2D`
apontando para o código-fonte acima  
**Estado:** implementação de produção concluída no pacote `woodcam_editor/`;
validação automatizada aprovada e pronta para rodada manual no FreeCAD GUI  
**Documento para a próxima IA:** leia tudo antes de editar qualquer arquivo.

> **Atualização de implementação — 12 de julho de 2026:** as seções de
> contexto e migração abaixo preservam o histórico que motivou a reconstrução.
> O estado efetivamente entregue está registrado na seção 26 e no
> [`RELATORIO_IMPLEMENTACAO_EDITOR_2D.md`](RELATORIO_IMPLEMENTACAO_EDITOR_2D.md).

---

## 0. Resumo executivo

O objetivo é construir dentro do WoodCAM um editor de desenho 2D em milímetros,
baseado em vetores, com experiência semelhante à parte 2D do Aspire. Ele deve
permitir desenhar, importar, selecionar, medir, editar nós, reparar, fechar,
validar, classificar e organizar peças; depois deve entregar essas peças ao CAM
sem depender de transformar tudo em um Sketch do FreeCAD.

O protótipo atual comprovou que um canvas próprio é viável, mas também comprovou
que a arquitetura atual não deve ser ampliada. Hoje `QGraphicsLineItem` e
`QGraphicsEllipseItem` são simultaneamente o desenho, o armazenamento e o
estado de interação. Isso gera os sintomas observados pelo usuário:

- às vezes a reta move, às vezes uma ponta é capturada;
- o clique simples disputa seleção com edição de nó;
- nós podem parecer “teletransportar” até o cursor;
- depois que um item é movido, coordenadas locais e visuais podem divergir;
- não há documento vetorial, topologia, peças, camadas, persistência ou Undo;
- fechar a janela perde o desenho;
- não há caminho seguro para polilinha, trim, dogbone ou organização.

A solução escolhida é uma reconstrução incremental com esta regra:

```text
VectorDocument (fonte de verdade)
        ↓
Controller + Commands (toda interação e mutação)
        ↓
Scene/View (somente desenho, hit-test e overlays)
        ↓
Adapters (Sketch, DXF/SVG, WoodCAM, PanelNest, FCStd)
```

O `QGraphicsScene` continua sendo uma boa viewport, mas nunca mais será o banco
de dados. O Sketcher deixa de ser a base do editor. Sketch passa a ser uma fonte
de importação por cópia e, no máximo, uma ferramenta de diagnóstico legado.

### Resultado mínimo palpável antes de acrescentar recursos

O primeiro marco só estará pronto quando for possível:

1. criar uma linha e um círculo;
2. clicar uma vez e mover o objeto inteiro;
3. dar dois cliques ou apertar `N` e entrar em edição de nós;
4. clicar num nó sem movê-lo;
5. mover o nó somente ao pressionar e arrastar a própria alça;
6. desfazer e refazer cada gesto com `Ctrl+Z`/`Ctrl+Shift+Z`;
7. salvar o FCStd, fechar o FreeCAD e reabrir com IDs e geometria idênticos;
8. usar a área real configurada na aba Trabalho;
9. fazer tudo isso em zoom de 25% e 800% com o mesmo conforto de clique.

Não implementar retângulo, triângulo, dogbone ou organizador antes desse marco.

---

## 1. Contexto e decisões já tomadas com o usuário

### 1.1 Problema original

O fluxo de desenho no FreeCAD é mais fácil pelo Sketcher, mas um Sketch único
não resolve bem o fluxo de marcenaria/CAM:

- formas não se comportam como vetores independentes como no Aspire;
- é difícil separar peças mantendo seus furos e recortes internos;
- organização automática precisa respeitar o limite da mesa da aba Trabalho;
- o solver pode deformar geometria ao tentar “corrigir” pontas;
- importar/exportar e reparar vetores exige operações próprias;
- ferramentas como trim, extend, offset, dogbone e T-bone não cabem naturalmente
  no fluxo paramétrico atual.

Foi tentada uma aba `2D (Sketch)` que diagnosticava contornos abertos e sugeria
conexões. A aplicação por restrições `Coincident`/`PointOnObject` deformou o
Sketch em casos reais. O Undo do FreeCAD ficou desabilitado porque algumas
alterações não estavam em transações. Essa abordagem deve ficar congelada.

Depois foi criada a aba `Editor 2D`, com canvas, grade, linha, círculo, seleção e
tentativa de fechar pontas. O desenho básico funcionou, porém a experiência
ficou inconsistente porque seleção e nós compartilham o mesmo clique e porque
os itens gráficos são o próprio modelo.

### 1.2 Decisões de produto fechadas

| ID | Decisão | Consequência |
|---|---|---|
| D-001 | O editor será vetorial e independente do Sketcher | Sketch é importado como cópia; não é a fonte viva do desenho |
| D-002 | `VectorDocument` será a única fonte de verdade | A cena nunca será lida para salvar, unir, diagnosticar ou organizar |
| D-003 | Um clique seleciona; dois cliques editam nós | Nós não existem para hit-test fora do modo de nós |
| D-004 | Clique solto nunca muda geometria | Toda edição exige arrasto acima de um limiar ou entrada numérica confirmada |
| D-005 | Toda alteração é um comando atômico | Um arrasto equivale a um Undo; previews não alteram o documento |
| D-006 | O domínio usa milímetros, X à direita e Y para cima | Inversão do Y pertence somente à View do Qt |
| D-007 | Reparos automáticos são conservadores e têm prévia | Nada se conecta à “coisa mais próxima” silenciosamente |
| D-008 | Furos pertencem à peça externa | Organizador move a peça inteira com todos os internos |
| D-009 | Geometria do projeto fica no FCStd | Preferências visuais ficam no ParamGet; entidades/camadas/peças ficam no documento |
| D-010 | Sem dependência Python externa obrigatória | O módulo continua copiável para a pasta `Mod` de outro FreeCAD |
| D-011 | Curvas permanecem exatas no editor | Discretização só ocorre na fronteira com CAM/PanelNest |
| D-012 | A aba Sketch antiga é legado | Pode ajudar a importar/diagnosticar; não recebe novas correções mutantes |

---

## 2. Auditoria do código atual

### 2.1 Arquivos e pontos relevantes

- `ui.py` tem aproximadamente 7.400 linhas e contém toda a janela do WoodCAM.
- A aba nova é criada em `ui.py` perto de `3187–3226`.
- A aba antiga `2D (Sketch)` é criada perto de `3228–3270`.
- O protótipo está em `vector_editor.py`, classe `VectorCanvas`, perto da linha
  27, com aproximadamente 300 linhas.
- O diagnóstico legado está em `vector_diagnostics.py`.
- A leitura atual de Sketch/face/objetos/PanelNest está em
  `geometry_reader.py`.
- O CAM chama `get_selected_geometry()` em `ui.py`, perto de `5651`; o Editor
  2D novo ainda não é fonte para Corte/Furo/Preenchimento.
- O cálculo correto dos limites da mesa já existe em
  `ui._work_area_bounds_for_preview()`, perto de `6742`.
- Operações aplicadas já persistem `SettingsJSON`, `MovesJSON` e
  `SelectionJSON`; esse padrão demonstra que propriedades JSON no FCStd são
  compatíveis com o WoodCAM atual.

### 2.2 O que existe no protótipo e pode ser reaproveitado como conceito

- `QGraphicsView/QGraphicsScene` como viewport;
- compatibilidade de importação PySide6, PySide2 e PySide;
- grade, eixos e limite visual da área de Trabalho;
- ideia de hit-area maior que o traço em `HitLineItem.shape()`;
- balão de medida simples;
- fórmulas básicas de ponto mais próximo numa reta/elipse;
- abas separadas para Editor novo e Sketch legado;
- contrato CAM atual:

```python
{
    "contours": [[(x, y), ...], ...],
    "holes": [{"x": ..., "y": ..., "diameter_mm": ..., "points": ...}, ...],
}
```

- integração PanelNest por `PanelNestManagedType == "layout_cam_compound"` em
  `geometry_reader.py`.

Esses itens são referências. Fórmulas precisam ser extraídas para funções
puras e testadas; não copiar o estado interno atual.

### 2.3 Defeitos estruturais que explicam os bugs

#### A. Coordenadas locais versus posição do item

O Qt move um item alterando `item.pos()`, mas o código continua lendo
`item.line()`/`item.rect()` em coordenadas locais para mostrar nós e calcular
snap. Depois de mover o objeto, visual, nós e cálculo podem apontar para lugares
diferentes. Esse é o principal motivo do comportamento intermitente.

#### B. Clique simples ativa nó

`mousePressEvent()` procura uma extremidade próxima e inicia `_active_node` no
mesmo gesto que deveria apenas selecionar/mover o objeto. Não existe
`mouseDoubleClickEvent()` nem modo de nós isolado.

#### C. Snap é apenas coincidência visual

`snap_nearby_endpoints()` muda coordenadas de pontas de linhas. Não existe
caminho ordenado, nó compartilhado, divisão de segmento, escolha de candidato,
prévia ou Undo. Duas linhas visualmente coincidentes continuam sem topologia.

#### D. Mesa desenhada incorretamente

O protótipo desenha `(0, 0, width, -height)` numa cena fixa de `1000 × 1000 mm`.
O usuário trabalha, por exemplo, com `1850 × 2750 mm`, origem configurável,
nove âncoras e offsets X/Y. A fonte correta são os bounds completos do Trabalho.

#### E. Estado efêmero

`_vector_items` vive somente na janela. Não há serialização, IDs, documento,
camadas, histórico ou ligação com o FCStd.

### 2.4 Riscos da aba Sketch legada

- `_close_small_vector_gaps()` adiciona geometria diretamente ao Sketch e não
  foi concebido como editor transacional completo.
- o mapeamento `Shape.Edge → Sketch.Geometry` é heurístico;
- a aresta mais próxima não necessariamente representa a intenção da peça;
- restrições podem mover círculos e segmentos já dimensionados;
- comparar somente quantidade de wires abertos e tamanho da bounding box não
  detecta toda deformação possível;
- o classificador de peça/furo legado não resolve todas as profundidades de
  contenção, ilhas e auto-interseções.

### 2.5 Ação imediata sobre o legado

Na Fase 0:

1. renomear visualmente para `Sketch legado — somente diagnóstico/importação`;
2. desabilitar ou remover da interface os botões que escrevem no Sketch;
3. manter o diagnóstico apenas como referência até o novo validador existir;
4. nunca apagar o código antes de existirem fixtures que preservem o caso
   problemático;
5. não ampliar `vector_diagnostics.py` como motor do novo editor.

---

## 3. Contrato de experiência do usuário

Este comportamento é requisito, não sugestão.

### 3.1 Seleção e movimento

```text
Clique esquerdo no corpo        seleciona somente aquele objeto
Shift + clique                  adiciona/remove da seleção
Clique no fundo                 limpa seleção
Esc                             cancela gesto; depois sai do modo; depois limpa
Clique + arraste no corpo       move toda a seleção
Clique sem arrastar             nunca move nada
Delete/Backspace                apaga seleção em um comando
Ctrl+A                          seleciona objetos visíveis/desbloqueados
Setas                           deslocam seleção pelo passo configurado
Ctrl+Shift+setas                deslocamento fixo fino configurável
```

Nós não são desenhados nem capturam mouse no modo Selecionar.

Seleção por janela deve seguir o padrão Aspire/CAD:

- arrasto da esquerda para a direita: somente objetos totalmente contidos;
- arrasto da direita para a esquerda: objetos contidos ou tocados;
- `Shift` adiciona/remove ao conjunto atual;
- camada invisível ou bloqueada não participa.

### 3.2 Entrada e saída do modo de nós

- duplo clique num caminho selecionado entra em `NODE_EDIT`;
- a tecla `N` alterna `SELECT ↔ NODE_EDIT` para um caminho compatível;
- `Esc` sai de `NODE_EDIT` sem apagar a seleção do objeto;
- clicar em outro objeto no modo de nós sai do modo atual e seleciona o outro,
  sem modificar o primeiro;
- o primeiro nó/direção do caminho tem símbolo e cor distintos.

### 3.3 Regra “sem teleporte” dos nós

O fluxo interno deve ser exatamente:

1. `mousePress` sobre a alça guarda `node_id`, posição original, posição do
   mouse e snapshot do caminho;
2. nenhum dado é alterado no press;
3. movimentos menores que `QApplication.startDragDistance()` são clique, não
   drag;
4. depois de cruzar o limiar, `mouseMove` calcula `original + delta` e atualiza
   somente uma prévia;
5. o snap pode ajustar o ponto da prévia;
6. `mouseRelease` cria um único `MoveNodeCommand`;
7. soltar sem ultrapassar o limiar apenas seleciona o nó;
8. `Esc` restaura o snapshot e não cria comando.

Nunca usar “posição atual do mouse” diretamente como nova posição no clique.

### 3.4 Zoom, pan e enquadramento

- roda do mouse: zoom em torno do cursor;
- botão do meio ou `Espaço + arrastar`: pan;
- `F`: enquadrar área de Trabalho;
- `Shift+F`: enquadrar seleção;
- zoom mínimo/máximo definidos, sem inverter a vista;
- escala e posição de câmera podem ser lembradas por documento, mas não fazem
  parte da geometria;
- textos, alças, marcadores e hit-area mantêm tamanho em pixels, não em mm.

### 3.5 Balões de medida e entrada exata

Durante qualquer criação/edição deve existir um overlay próximo ao cursor:

| Ferramenta/gesto | Conteúdo mínimo |
|---|---|
| Linha/polilinha | `L`, ângulo, `ΔX`, `ΔY`, X/Y final |
| Movimento | `ΔX`, `ΔY`, X/Y de referência |
| Nó | X, Y e deslocamento |
| Retângulo | largura × altura, X/Y da âncora |
| Círculo | raio e diâmetro |
| Arco | raio, ângulo e comprimento |
| Rotação | ângulo absoluto e delta |
| Escala | largura, altura e fatores X/Y |

Entrada numérica acontece durante a prévia e só confirma com `Enter`. `Esc`
cancela. Valores aceitam vírgula ou ponto decimal, mas o domínio armazena float.

---

## 4. Escopo e paridade com o Aspire

O objetivo não é copiar toda a interface do Aspire de uma vez. É reproduzir os
conceitos que tornam o fluxo vetorial previsível e útil para CNC.

### 4.1 Matriz de prioridade

| Área | Recursos | Marco |
|---|---|---|
| Fundação | documento, IDs, camadas, JSON, comandos, Undo/Redo, FCStd | R1 |
| Navegação | zoom, pan, fit, grade, eixos, mesa, coordenadas | R1 |
| Seleção | clique, Shift, janela direcional, move, delete | R1 |
| Nós | duplo clique/N, drag sem teleporte, multi-nó, XY exato | R1/R2 |
| Desenho | linha, polilinha, retângulo/quadrado, círculo | R2 |
| Desenho 2 | arco, elipse, triângulo/polígono, Bézier | R2/R3 |
| Snap | fim, meio, centro, quadrante, interseção, geometria, grade | R2 |
| Smart snap | horizontal, vertical, ângulo, tangente, perpendicular | R3 |
| Reparos | join, close reta/meio/suave, conectar à geometria, split | R3 |
| Edição | trim, extend, offset, converter span, inverter direção | R3/R4 |
| Validador | abertos, duplicados, zero, cruzamentos, ramificações | R3 |
| Peças | externo, furos, ilhas, metadados, fora da mesa | R3 |
| Organização | mover peças completas, margem, rotação, colisão | R4 |
| Marcenaria | filete, dogbone, T-bone manual/automático | R4 |
| Interoperabilidade | Sketch, DXF, SVG, exportação | R3/R4 |
| Camadas | visibilidade, bloqueio, cor, finalidade, mover/copiar | R3 |
| Transformações | mover, girar, escalar, espelhar, alinhar/distribuir | R3/R4 |
| Posterior | texto, vetorização de bitmap, 3D/modelagem | fora do núcleo inicial |

### 4.2 Ferramentas do Aspire estudadas e tradução para o WoodCAM

- O Aspire separa `Vector Selection`, `Node Editing` e transformação
  interativa. O WoodCAM deve manter a mesma separação modal.
- `Join Open Vectors` une extremidades dentro de tolerância; não projeta
  qualquer ponta no meio de uma forma silenciosamente.
- Fechar com linha reta, mover pontas ao meio e fechar com curva suave são
  operações diferentes e devem continuar diferentes.
- `Interactive Trim` remove o trecho entre as interseções vizinhas ao clique.
- `Extend` mostra a extensão antes de confirmar.
- Snap de geometria, smart snap e grade são opções distintas e persistentes.
- `Vector Validator` marca sobreposições, interseções e spans de comprimento
  zero; diagnóstico não deve editar por si só.
- Filetes normal, dogbone e T-bone são ferramentas interativas, com prévia.

---

## 5. Arquitetura alvo

### 5.1 Pacotes

Criar um pacote novo. Nome recomendado: `woodcam_editor`.

```text
woodcam_editor/
├── __init__.py
├── domain/
│   ├── primitives.py       # Vec2, BBox2D, Affine2D, epsilons
│   ├── spans.py            # LineSpan, ArcSpan, CubicBezierSpan
│   ├── entities.py         # PathEntity, CircleEntity, EllipseEntity, Group
│   ├── document.py         # VectorDocument, Layer, Piece2D, WorkArea
│   ├── commands.py         # comandos puros e reversíveis
│   ├── topology.py         # componentes, loops, grau, contenção
│   ├── validation.py       # problemas com IDs/localização
│   └── serialization.py    # JSON e migrações
├── geometry/
│   ├── math2d.py           # distância, projeção, interseção, área
│   ├── flatten.py          # discretização controlada na fronteira
│   └── occ_backend.py      # Part/OCC para boolean, offset e fillet
├── application/
│   ├── editor_controller.py
│   ├── command_manager.py
│   ├── selection.py
│   ├── snapping.py
│   ├── diagnostics_service.py
│   ├── piece_classifier.py
│   ├── piece_organizer.py
│   └── document_store.py
├── presentation/
│   ├── widget.py           # Editor2DWidget completo
│   ├── view.py             # zoom/pan/Y-up/eventos
│   ├── scene_adapter.py    # entity_id ↔ item visual
│   ├── items.py            # desenho sem estado de domínio
│   ├── overlays.py         # nós, medidas, snap, seleção, problemas
│   └── tools/
│       ├── base.py
│       ├── select.py
│       ├── node.py
│       ├── polyline.py
│       ├── rectangle.py
│       ├── circle.py
│       ├── arc.py
│       ├── join.py
│       ├── trim.py
│       └── fillet.py
├── importers/
│   ├── sketch.py
│   ├── part_shape.py
│   ├── dxf.py
│   └── svg.py
├── exporters/
│   ├── dxf.py
│   └── svg.py
└── adapters/
    ├── freecad_store.py
    ├── woodcam_geometry.py
    └── panelnest.py

tests/vector2d/
├── unit/
├── qt/
├── freecad/
└── fixtures/
```

`ui.py` deve somente construir/injetar o `Editor2DWidget` e conversar com ele
por uma API curta. Não colocar novas regras geométricas dentro de `ui.py`.

### 5.2 Dependências entre camadas

Permitido:

```text
presentation → application → domain
adapters/importers → domain
application → geometry/domain
ui.py → presentation/adapters
```

Proibido:

```text
domain → Qt
domain → FreeCADGui
domain → ui.py
commands → QGraphicsItem
serializer → qualquer classe Qt
CAM → ler QGraphicsScene
```

O domínio deve ser importável em Python comum para testes. `occ_backend.py`,
importadores FreeCAD e persistência FCStd podem depender de FreeCAD/Part.

---

## 6. Modelo vetorial

### 6.1 Primitivas básicas

```python
@dataclass(frozen=True)
class Vec2:
    x: float
    y: float

@dataclass(frozen=True)
class BBox2D:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

@dataclass(frozen=True)
class Affine2D:
    # matriz 2D: translação, rotação, escala e espelho
    ...
```

Requisitos:

- floats em milímetros;
- não arredondar durante edição;
- comparações geométricas sempre recebem tolerância explícita;
- igualdade de ID não significa igualdade geométrica;
- operações não modificam objetos in-place sem comando.

### 6.2 Spans exatos

Tipos mínimos:

```text
LineSpan(start, end)
ArcSpan(start, end, center, clockwise)
CubicBezierSpan(start, control1, control2, end)
```

Cada span fornece:

- `point_at(t)`;
- `tangent_at(t)`;
- `length()`;
- `bounds()`;
- `nearest_point(point)`;
- `split(t)`;
- `reversed()`;
- `transformed(matrix)`;
- `flatten(deflection)`.

IDs estáveis pertencem a span e nós lógicos. Não usar `Edge1`, índice da lista
ou posição no `QGraphicsScene` como identidade persistente.

### 6.3 Entidades

```text
PathEntity
  id, layer_id, spans ordenados, closed, metadata

CircleEntity
  id, layer_id, center, radius, metadata

EllipseEntity
  id, layer_id, center, radius_x, radius_y, rotation, metadata

GroupEntity
  id, child_ids, transform opcional, metadata
```

Retângulo, triângulo, polígono e polilinha criam `PathEntity`. Círculo e elipse
permanecem exatos. Um retângulo pode manter metadado paramétrico enquanto não
for editado arbitrariamente; ao editar nós fora das regras do retângulo, ele é
convertido explicitamente em caminho, sem perda visual.

### 6.4 Invariantes de `PathEntity`

- spans estão ordenados;
- fim do span N coincide exatamente com início de N+1 no modelo;
- caminho fechado também liga último ao primeiro;
- um caminho aberto tem exatamente duas extremidades;
- nenhuma operação cria span de comprimento zero silenciosamente;
- ramificação não cabe dentro de um único `PathEntity`; ela é detectada como
  topologia inválida entre entidades/segmentos;
- direção do caminho é preservada e pode ser invertida.

### 6.5 Documento, camada e peça

```text
VectorDocument
  schema_version
  document_uuid
  revision
  units = "mm"
  coordinate_system = "xy_cartesian_y_up"
  work_area
  layers_by_id
  entities_by_id
  pieces_by_id
  active_layer_id
  metadata

Layer
  id, name, color, visible, locked, order
  purpose: design | construction | reference | cut | pocket | drill

Piece2D
  id, name, outer_path_id, inner_path_ids
  quantity, material, thickness
  grain_direction, rotations_allowed
  placement, metadata, stale
```

Seleção, hover, câmera, overlays e pilha de preview não pertencem ao JSON
geométrico. Podem existir no `EditorSession` da aplicação.

---

## 7. Persistência e esquema JSON

### 7.1 Objeto no documento FreeCAD

Criar um `Part::FeaturePython` chamado `WoodCAM2D_VectorDocument`, dentro de um
grupo `WoodCAM 2D — Desenho`.

Propriedades mínimas:

```text
SchemaVersion          App::PropertyInteger
DocumentUUID           App::PropertyString
GeometryJSON           App::PropertyString
Revision               App::PropertyInteger
Checksum               App::PropertyString
LastMigration          App::PropertyString
SourceMetadataJSON     App::PropertyString
Shape                  cache visual derivado, nunca fonte da verdade
```

O cache `Shape` permite ver/selecionar a geometria fora da janela, mas sempre é
reconstruído do JSON.

### 7.2 Exemplo de formato

```json
{
  "schema_version": 1,
  "document_uuid": "uuid",
  "revision": 17,
  "units": "mm",
  "coordinate_system": "xy_cartesian_y_up",
  "work_area": {
    "min_x": 0.0,
    "min_y": 0.0,
    "max_x": 1850.0,
    "max_y": 2750.0,
    "source": "woodcam_trabalho"
  },
  "layers": [
    {
      "id": "layer-uuid",
      "name": "Desenho",
      "color": "#2563eb",
      "visible": true,
      "locked": false,
      "order": 0,
      "purpose": "design"
    }
  ],
  "entities": [
    {
      "id": "path-uuid",
      "type": "path",
      "layer_id": "layer-uuid",
      "closed": false,
      "spans": [
        {
          "id": "span-uuid",
          "type": "line",
          "start": [10.0, 20.0],
          "end": [110.0, 20.0]
        }
      ],
      "metadata": {}
    }
  ],
  "pieces": [],
  "metadata": {}
}
```

### 7.3 Regras de serialização

- JSON determinístico, chaves estáveis e sem pickle;
- `schema_version` obrigatório;
- checksum calculado sobre representação canônica;
- migrações explícitas `v1 → v2`; nunca alterar formato no lugar sem teste;
- preservar campos desconhecidos quando possível;
- carregar num objeto temporário, validar invariantes e só então substituir o
  documento atual;
- JSON inválido não é sobrescrito: exibir erro e oferecer exportação de cópia;
- persistir uma vez por comando, nunca a cada `mouseMove`;
- alterações marcam o FCStd como modificado;
- preferências de snap/grade/cores ficam em `FreeCAD.ParamGet` e chamam
  `FreeCAD.saveParameter()` quando necessário;
- desenho, camadas, peças e área do projeto ficam no FCStd.

### 7.4 Reabrir e recuperação

Ao abrir a janela:

1. localizar `WoodCAM2D_VectorDocument`;
2. validar versão e checksum;
3. migrar em memória, dentro de transação própria, se necessário;
4. criar `VectorDocument`;
5. reconstruir `Shape` se estiver ausente/desatualizada;
6. projetar entidades na cena;
7. ajustar a área de Trabalho e a câmera;
8. nunca restaurar uma seleção antiga como se fosse intenção atual.

O fechamento da janela não apaga o documento. Fechar o FreeCAD sem salvar o
FCStd segue o comportamento normal do FreeCAD, que deve perguntar se deseja
salvar. Uma recuperação automática em arquivo temporário pode ser R4, mas não
substitui o FCStd.

---

## 8. Comandos, transações e Undo/Redo

### 8.1 Regra geral

Toda mutação passa por um comando de domínio:

```text
AddEntitiesCommand
DeleteEntitiesCommand
MoveEntitiesCommand
TransformEntitiesCommand
MoveNodeCommand
MoveSpanCommand
ChangeSpanTypeCommand
JoinPathsCommand
ClosePathCommand
ConnectEndpointToSpanCommand
SplitSpanCommand
TrimCommand
ExtendCommand
OffsetCommand
FilletCommand
ImportBatchCommand
CreatePiecesCommand
OrganizePiecesCommand
EditPieceMetadataCommand
SetLayerCommand
CompositeCommand
```

O comando contém estado suficiente para `apply()` e `revert()`, ou before/after
imutáveis. IDs gerados na primeira aplicação são reaproveitados no Redo.

### 8.2 Autoridade do histórico

Quando houver FCStd ativo, o histórico autoritativo deve ser a transação do
FreeCAD, para que `Ctrl+Z` não fique apagado:

```python
doc.openTransaction(label)
try:
    command.apply(vector_document)
    validate_document_invariants(vector_document)
    store.save_to_feature(vector_document)  # uma escrita JSON
    store.refresh_derived_shape(vector_document)
    doc.recompute()
    doc.commitTransaction()
except Exception:
    command.revert(vector_document)
    doc.abortTransaction()
    raise
```

Um observer do documento recarrega o `VectorDocument` e a cena quando Undo/Redo
restaurar `GeometryJSON`. Não manter simultaneamente `QUndoStack` e transações
como duas verdades concorrentes.

Para testes puros ou um documento ainda não anexado, usar
`InMemoryCommandHistory` com a mesma interface. Ele é adaptador de teste, não um
segundo histórico em produção.

### 8.3 Granularidade

- um drag completo = um comando;
- uma polilinha completa = um comando, embora Backspace possa desfazer o último
  ponto da prévia antes da confirmação;
- uma importação inteira = um comando;
- organização de todas as peças = um comando composto;
- trim de cada clique = um comando, salvo modo lote confirmado de uma vez;
- limpar tudo pede confirmação e é um único comando reversível;
- alterações de seleção/câmera não entram no histórico geométrico.

### 8.4 Previews

`mouseMove` nunca altera `VectorDocument` nem `GeometryJSON`. O controlador
constrói uma `PreviewState` derivada do snapshot inicial. A cena desenha a
prévia magenta/tracejada. `mouseRelease` converte a prévia em comando ou descarta.

---

## 9. Controlador e máquina de estados

### 9.1 Estados globais

```text
SELECT
NODE_EDIT
DRAW_POLYLINE
DRAW_RECTANGLE
DRAW_CIRCLE
DRAW_ARC
TRANSFORM
JOIN
CONNECT_TO_GEOMETRY
TRIM
EXTEND
FILLET
ORGANIZE_PREVIEW
```

Cada ferramenta implementa:

```text
Idle → Armed → Pressed → Previewing → Commit
                               └──────→ Cancel
```

Ela recebe eventos normalizados do View, consulta seleção/snap/documento e
emite apenas preview ou comando. Não chama métodos do `QGraphicsItem` para
mudar geometria.

### 9.2 Estrutura do gesto

```python
PressContext(
    screen_pos,
    scene_pos,
    model_pos,
    target_entity_id,
    target_span_id,
    target_node_id,
    selection_before,
    document_revision,
    snapshot,
)
```

Se a revisão mudar durante um gesto, cancelar o gesto para não aplicar sobre
estado obsoleto.

### 9.3 Hit-test

- calcular em pixels de tela;
- corpo de vetor: alvo recomendado de 10 px;
- alça de nó: 8–10 px;
- hit-test geométrico pode converter px para mm usando a transformação da view;
- overlays usam `ItemIgnoresTransformations` ou pintura em coordenada de tela;
- itens de apresentação não têm `ItemIsMovable`;
- desempate: nó somente em `NODE_EDIT`; depois span, entidade, menor distância,
  camada ativa e ordem visual;
- lista de candidatos sob o cursor pode ser percorrida com `Tab` em sobreposição.

### 9.4 Cena incremental

`SceneAdapter` mantém `entity_id → GraphicsItem`. Após um comando, recebe
`DocumentChangeSet` com IDs adicionados, alterados e removidos. Atualiza somente
esses itens. Uma reconstrução completa fica disponível para load/migração e
teste de consistência.

---

## 10. Snapping e conectividade

### 10.1 Não misturar duas tolerâncias

```text
snap_radius_px       ajuda interativa do cursor, padrão 10 px
join_tolerance_mm    reparo/união em lote, padrão conservador 0,2 mm
```

O campo de 20 mm visto no protótipo é inadequado para captura do mouse. Zoom
deve mudar quantos milímetros cabem em 10 px, sem mudar o conforto visual.

### 10.2 Candidatos

```python
SnapCandidate(
    point: Vec2,
    kind: ENDPOINT | INTERSECTION | CENTER | MIDPOINT | QUADRANT |
          ON_GEOMETRY | GRID | HORIZONTAL | VERTICAL | ANGLE |
          TANGENT | PERPENDICULAR | WORK_AREA,
    entity_id,
    span_id,
    parameter,
    distance_px,
    priority,
    label,
)
```

Prioridade inicial:

1. extremidade;
2. interseção;
3. centro/quadrante/ponto médio;
4. alinhamento horizontal/vertical/ângulo;
5. ponto sobre a geometria;
6. grade.

Mostrar marcador e rótulo (`Fim`, `Meio`, `Centro`, `Interseção`, `Na curva`,
etc.). `Shift` desativa snap temporariamente. Configurações persistem.

### 10.3 Índice espacial

O SnapEngine consulta entidades dentro da bounding box correspondente ao raio
em pixels. Começar com índice simples por bounding boxes; medir antes de
otimizar. Para milhares de entidades, adotar grid/R-tree próprio ou aproveitar
o índice somente como busca de candidatos, nunca como fonte geométrica.

### 10.4 Diferença entre snap, join e conectar à geometria

Esses conceitos não podem ser fundidos:

- **Snap:** posiciona uma prévia com precisão durante desenho/movimento.
- **Join de pontas:** combina extremidades de dois caminhos abertos e cria um
  único `PathEntity` ordenado, invertendo direção quando necessário.
- **Fechar caminho:** conecta as duas extremidades do mesmo caminho por reta,
  ponto médio ou curva suave.
- **Conectar à geometria:** projeta uma extremidade sobre o meio de uma reta,
  arco ou círculo; divide explicitamente o span alvo; mostra a topologia final.
- **Weld/Trim:** resolve contornos sobrepostos/intersectantes.

Se uma linha encostar num círculo, “coincidir visualmente” não significa que
existe um contorno simples. O comando deve mostrar se o círculo será dividido e
se o resultado gerará ramificação. Ramificação de grau maior que 2 é marcada e
bloqueada para peça/CAM até ser resolvida.

### 10.5 Prévia obrigatória de reparo

Antes de aplicar:

- origem e destino destacados;
- linha/curva resultante em magenta;
- distância em mm;
- entidade/span que será dividido;
- indicação `resultado: fechado`, `resultado: aberto` ou
  `resultado: ramificação inválida`;
- `Enter/clique` aplica; `Esc/botão direito` cancela.

---

## 11. Ferramentas de desenho

### 11.1 Polilinha — primeira ferramenta de caminho

Fluxo:

1. primeiro clique define início;
2. cliques seguintes acrescentam spans de linha;
3. movimento mostra o próximo span e medidas;
4. `Backspace` remove o último ponto da prévia;
5. `Tab` ou clique no primeiro nó fecha;
6. `Espaço` termina o caminho e mantém a ferramenta ativa;
7. botão direito/`Esc` termina/cancela conforme já existam spans;
8. entrada `X,Y`, comprimento/ângulo ou `ΔX,ΔY` confirma ponto exato;
9. snap em extremidade pode continuar um caminho aberto existente;
10. clique-arraste para Bézier pode entrar somente depois da linha estável.

### 11.2 Linha isolada

Pode ser atalho da polilinha configurado para concluir após o segundo ponto.
Manter um único motor de criação para não duplicar bugs.

### 11.3 Retângulo e quadrado

- arrastar canto a canto;
- `Ctrl` restringe quadrado;
- `Alt` cria pelo centro;
- largura/altura exatas;
- cantos retos inicialmente; raio interno/externo depois;
- duplo clique num retângulo intacto abre propriedades paramétricas;
- edição arbitrária de nó converte em caminho.

### 11.4 Círculo

- centro + raio por arraste;
- entrada de raio ou diâmetro;
- snap de centro e quadrantes;
- mover centro e editar raio somente em modo de nós/propriedades;
- um clique no círculo nunca captura centro/raio no modo Selecionar.

### 11.5 Arcos

Dois modos explícitos:

- início → fim → terceiro ponto;
- centro → início → fim.

Mostrar raio, ângulo, sentido e comprimento. `ArcSpan` permanece exato.

### 11.6 Triângulo, polígono, elipse e Bézier

- triângulo é preset de polígono regular com 3 lados;
- polígono aceita lados, raio/diâmetro, centro e rotação;
- elipse aceita centro, raio X/Y e rotação;
- Bézier cúbica entra após nó/handles estarem testados;
- estrela e texto são posteriores.

### 11.7 Entrada numérica

Usar um pequeno editor flutuante/overlay comum a todas as ferramentas, com
campos contextuais. Não abrir diálogos modais a cada ponto. Validar antes de
confirmar e manter a prévia se houver erro.

---

## 12. Edição de nós e spans

### 12.1 Nós

- selecionar um ou vários com `Shift`/janela;
- mover por drag ou X/Y exato;
- excluir nó preservando caminho quando matematicamente possível;
- inserir nó no ponto mais próximo do span;
- definir nó inicial de caminho fechado;
- cortar caminho no nó;
- suavizar/quebrar tangência de Bézier;
- alças sempre em pixels e apenas no modo de nós.

### 12.2 Spans

- arrastar uma linha move o span inteiro mantendo forma;
- arrastar arco altera curvatura/raio com extremidades fixas;
- `Ctrl+arrastar` arco/Bézier move o span inteiro;
- converter linha ↔ arco ↔ Bézier quando possível;
- apagar span e resultar em caminhos abertos previsíveis;
- inverter direção do caminho;
- menu contextual recebe IDs e executa comandos, nunca modifica a cena.

### 12.3 Transformação do objeto inteiro

R2: movimento por mouse e X/Y.  
R3: handles para escala uniforme/não uniforme, rotação e espelho.

Durante transformação:

- preview deriva do snapshot original, não acumula erro a cada move;
- `Shift`/`Ctrl` têm modificadores documentados;
- centro de rotação padrão é centro da seleção;
- entrada exata mostra largura, altura, escala e ângulo;
- múltiplos objetos formam um comando composto.

---

## 13. Reparos e operações geométricas

### 13.1 Unir e fechar

Implementar como comandos separados:

1. `JoinOpenEndpoints`: une somente extremidades de caminhos abertos dentro da
   tolerância; mostra pares e resultado;
2. `CloseWithLine`: adiciona reta entre pontas do mesmo caminho;
3. `CloseByMidpoint`: move as duas pontas ao ponto médio;
4. `CloseWithSmoothCurve`: cria Bézier/curva compatível com tangentes;
5. `ConnectEndpointToSpan`: divide reta/arco/círculo no ponto projetado e
   conecta explicitamente;
6. `SplitAtPoint`: divide caminho/span sem unir.

Ao unir dois caminhos, considerar quatro orientações possíveis
`start-start`, `start-end`, `end-start`, `end-end`; inverter spans conforme
necessário; o resultado é um único caminho ordenado.

### 13.2 Trim interativo

- hover identifica span e interseções vizinhas;
- trecho que será removido fica vermelho/magenta;
- clique remove somente o intervalo previsto;
- opção de reunir trechos restantes ao encerrar;
- grupo bloqueado pisca/mostra mensagem, sem editar silenciosamente;
- cada clique é Undo atômico.

### 13.3 Extend

- selecionar/hover nos spans que podem ser prolongados;
- mostrar tracejado até a interseção;
- permitir extensão de linha e arco onde definida;
- confirmar destino antes de mudar modelo;
- não estender quando a interseção está atrás do sentido válido sem aviso.

### 13.4 Offset e booleanos

Usar `Part`/OCC no backend para robustez, mantendo o domínio independente:

- aberto: esquerda/direita;
- fechado: dentro/fora;
- cantos retos ou arredondados;
- distância exata;
- preview e alerta de auto-interseções;
- weld/união, subtração e interseção de áreas fechadas;
- resultado volta como spans exatos quando reconhecível ou curvas OCC
  convertidas com relatório.

Não adicionar Shapely/PyClipper obrigatório: o WoodCAM precisa funcionar ao
copiar sua pasta para outro FreeCAD.

### 13.5 Filete, dogbone e T-bone

Modos:

- filete normal;
- dogbone;
- T-bone.

Regras:

- raio digitado ou derivado da fresa selecionada;
- manual: hover num canto válido mostra prévia; clique aplica;
- automático: apenas cantos internos, inicialmente `90° ± 1°`;
- T-bone permite escolher o lado; clique no canto usa o lado mais longo como
  padrão, clique próximo a um lado força esse lado;
- não aplicar dogbone automático em canto externo;
- validar largura da ranhura versus diâmetro da fresa;
- guardar metadados/original do comando para remoção fiel por Undo;
- aplicação automática em várias peças é um `CompositeCommand` com lista de
  cantos e prévia total.

---

## 14. Diagnóstico e validador

### 14.1 Diagnóstico não altera

O validador produz `ValidationIssue`:

```python
ValidationIssue(
    id,
    severity,          # info | warning | error | blocker
    code,
    message,
    entity_ids,
    span_ids,
    node_ids,
    points,
    suggested_actions,
)
```

Ele cria overlays clicáveis. Clicar numa ocorrência enquadra e seleciona o
local. Corrigir exige botão/comando separado.

### 14.2 Verificações mínimas

- caminhos abertos;
- extremidades próximas, mas não unidas;
- duplicatas exatas;
- spans zero e microspans;
- auto-interseção;
- interseção/sobreposição entre contornos;
- ramificação com grau maior que 2;
- arco/círculo com raio inválido;
- caminho fechado com área zero;
- orientação de caminhos;
- peça/furo ambíguo;
- furo fora de qualquer externo;
- objeto fora da área de Trabalho;
- camada oculta/bloqueada conforme o escopo;
- entidade/importação não suportada;
- geometria não-manifold antes de CAM.

### 14.3 Autocorreções iniciais permitidas

Somente com lista e prévia:

- apagar spans zero;
- apagar duplicata exata;
- unir extremidades sob tolerância;
- remover microspans quando não deslocar vizinhos além da tolerância.

Nunca resolver interseções, escolher peça ou projetar ponta no meio de curva
automaticamente sem confirmação explícita.

---

## 15. Importação e exportação

### 15.1 Importar Sketch selecionado

Primeira versão é snapshot independente:

1. validar um ou mais Sketches selecionados;
2. ler `Sketch.Geometry` diretamente, não o resultado discretizado final;
3. aplicar `getGlobalPlacement()`;
4. converter linhas, círculos, arcos e Bézier/B-spline suportados;
5. geometria de construção entra em camada `Referência — <Sketch>` bloqueada ou
   é ignorada conforme opção;
6. restrições/dimensões não são copiadas como solver, mas valores geométricos
   resultantes são preservados;
7. objetos não suportados entram em relatório com índice/tipo;
8. original não é modificado, ocultado nem excluído;
9. importação inteira é `ImportBatchCommand` e um Undo;
10. salvar metadados de origem/fingerprint para possível “Atualizar da origem”
    futuro, mas não criar vínculo vivo agora.

Curvas B-spline podem ser preservadas por tipo futuro ou aproximadas por
Béziers dentro de tolerância, sempre reportando a conversão.

### 15.2 Importar objetos/faces e PanelNest

Reutilizar as ideias de `geometry_reader.py`, porém converter para entidades do
domínio. Não importar apenas pontos se a curva OCC exata estiver disponível.

PanelNest já resolve `layout_cam_compound`; o adaptador deve capturar contornos,
furos e metadados de peça/chapa sem incluir a chapa-base.

### 15.3 DXF

Prioridade após Sketch:

- usar importador disponível no FreeCAD/documento temporário;
- converter entidades para o domínio;
- preservar unidade, camadas, blocos quando possível;
- mostrar relatório de escala e tipos ignorados;
- fechar/remover o documento temporário em transação segura;
- golden files testam mm, polegadas, linha, arco, círculo e polilinha.

### 15.4 SVG

Suportar gradualmente:

```text
M L H V C Q A Z
line, rect, circle, ellipse, polyline, polygon
transform stack e unidades
```

Texto não deve virar geometria silenciosamente; informar que texto/vetorização
virá depois.

### 15.5 Exportação

R4:

- DXF preservando camadas;
- SVG preservando paths/círculos/elipses e unidades;
- exportar seleção ou todas as camadas visíveis;
- discretização só quando o formato não suportar o span;
- PDF/EPS/AI ficam posteriores.

---

## 16. Classificação e organização de peças

### 16.1 Pré-condições

Uma peça só nasce de caminhos:

- fechados;
- simples ou com interseções resolvidas;
- área maior que epsilon;
- sem ramificações;
- em camada visível e habilitada para organização.

Vetores abertos continuam no desenho, mas bloqueiam somente a classificação que
depende deles e aparecem no relatório.

### 16.2 Árvore de contenção

Construir com:

1. filtro por bounding box;
2. flatten controlado apenas para teste de contenção;
3. ponto interno robusto, nunca assumir sempre o primeiro vértice em borda;
4. profundidade/paridade;
5. detecção de interseções antes de classificar.

Uma raiz de nível 0 cria `Piece2D`. Todos os seus descendentes permanecem
ligados a ela:

```text
nível 0: contorno externo da peça
nível 1: furo/recorte
nível 2: ilha de material dentro do recorte
nível 3: recorte da ilha
...
```

O organizador nunca transforma nível 1 em peça solta. Casos de ilhas complexas
recebem relatório e mantêm a hierarquia completa.

### 16.3 Metadados

Cada peça pode ter:

- nome e código;
- quantidade;
- material e espessura;
- veio/grão;
- rotações permitidas (0/90, livre ou bloqueada);
- margem individual;
- origem local;
- lado/frente quando aplicável;
- estado `stale` quando o contorno muda.

### 16.4 Área da aba Trabalho

Não usar `0,0,width,-height` fixo. Injetar os bounds retornados por
`_work_area_bounds_for_preview(settings)`, considerando:

- largura/altura;
- âncora de origem;
- offsets X/Y;
- contorno de referência quando configurado;
- dimensão zero e fallback de seleção;
- margem visual ao definir `sceneRect`.

O domínio permanece Y-up; a View inverte somente a renderização.

### 16.5 Organizador inicial

Primeiro algoritmo confiável, não “ótimo”:

1. duplicar instâncias conforme quantidade;
2. manter contorno externo e todos os internos como unidade rígida;
3. aplicar margem/spacing;
4. ordenar por maior dimensão/área;
5. shelf/row packing dentro dos bounds;
6. testar rotações permitidas;
7. validar colisão de bounding box e depois de contorno real;
8. gerar prévia;
9. informar peças que não couberam e excesso em X/Y;
10. confirmar tudo num `OrganizePiecesCommand`.

Depois, integrar/compartilhar algoritmo mais avançado com PanelNest. Nunca
mover apenas furos; eles seguem a transformação da peça.

### 16.6 Critério visual

- mesa claramente delimitada;
- peças válidas, fora da mesa e em colisão com cores distintas;
- número/nome no centro da peça;
- internos visíveis como parte da mesma peça;
- preview não altera o documento até confirmar;
- botão `Reverter` é Undo normal, não função especial.

---

## 17. Integração com WoodCAM e PanelNest

### 17.1 Fonte de geometria explícita

Hoje o CAM depende da seleção visual do FreeCAD. Criar uma interface:

```python
class GeometryProvider:
    def get_geometry(self) -> dict: ...
    def describe_source(self) -> str: ...
    def revision_token(self) -> str: ...
```

Providers:

```text
FreeCADSelectionGeometryProvider  # comportamento atual
EditorDocumentGeometryProvider    # entidades/peças selecionadas no editor
PanelNestGeometryProvider         # layout/cam compound
```

Na UI, o usuário escolhe ou o sistema mostra claramente a fonte ativa. Não
trocar de fonte por seleção acidental.

### 17.2 Pontos que devem usar o provider

- construção de moves, hoje perto de `ui.py:5651`;
- prévia do datum;
- geometria selecionada para preview;
- fallback de dimensões/área;
- simulação e G-code;
- criação de operações persistentes.

### 17.3 Adapter WoodCAM

Converter documento/peças para o contrato atual:

- contornos externos e recortes internos arbitrários em `contours`;
- círculos exatos em `holes` quando fizer sentido para Furo;
- flatten com deflexão definida na fronteira, preservando o padrão atual de
  aproximadamente `0,01 mm` para CAM quando apropriado;
- internos antes de externos na operação de corte, mantendo a lógica atual;
- revision token salvo na operação aplicada;
- se o desenho mudar, operação derivada vira `desatualizada`, sem regenerar
  G-code silenciosamente.

### 17.4 Adapter PanelNest

Contrato inicial sugerido:

```text
PanelPart
  id
  profile_points
  inner_profile_loops
  circular_holes
  quantity/material/thickness/grain/rotations
```

Se a API atual aceitar apenas perfil externo, evoluí-la de forma compatível.
Não fingir que todo recorte interno é um furo circular.

Botões futuros:

- `Criar peças 2D`;
- `Organizar na área de Trabalho`;
- `Enviar ao PanelNest`;
- `Usinar no WoodCAM`.

---

## 18. Organização da interface

### 18.1 Evitar dezenas de botões soltos

Barra primária recomendada, com 9 grupos/ações visíveis:

1. **Selecionar**;
2. **Nós**;
3. **Polilinha**;
4. **Formas** (retângulo, círculo, arco, elipse, triângulo/polígono);
5. **Transformar** (mover exato, girar, escalar, espelhar, alinhar);
6. **Reparar** (join, fechar, conectar, split, trim, extend);
7. **Filetes** (normal, dogbone, T-bone);
8. **Validar**;
9. **Peças/Organizar**.

Importar/exportar, camadas, snap, grade e preferências ficam em barra secundária
ou painel lateral. Assim o editor ganha muitas funções sem uma fileira enorme.

### 18.2 Layout

```text
┌──────────────── barra de ferramentas ────────────────┐
│ painel de camadas/peças │ canvas expansível │ propriedades │
│                         │                  │ contexto      │
├───────────────────────── status/medidas/snap ────────────┤
```

Usar `QSplitter` para painéis recolhíveis. Em janela estreita, propriedades
viram painel inferior ou popover. O canvas sempre recebe a maior área.

### 18.3 Integração na janela atual

- manter `Editor 2D` como aba do WoodCAM;
- encapsular tudo num `Editor2DWidget` independente;
- considerar botão `Expandir editor` para uma janela/dock maior usando a mesma
  sessão/controlador, não uma cópia do documento;
- a janela continua redimensionável;
- toolbar usa overflow/flyouts, não impõe largura mínima exagerada;
- aba antiga fica escondida em `Avançado` ou renomeada como legado.

### 18.4 Status sempre visível

Mostrar:

- modo atual;
- X/Y do cursor;
- unidade mm;
- snap candidato;
- número de selecionados;
- quantidade de erros/bloqueios;
- documento salvo/modificado;
- fonte ativa do CAM.

Mensagens informativas comuns devem aparecer no status, não em `QMessageBox`.
Diálogo modal fica para perda de dados, confirmação destrutiva ou erro grave.

---

## 19. Plano de migração por fases

Cada fase termina com testes e critérios de aceite. Não avançar “porque parece
funcionar”.

### Fase 0 — Segurança, congelamento e baseline

**Objetivo:** impedir novas deformações e criar terreno de teste.

Tarefas:

- [ ] criar pacote `woodcam_editor` e diretórios de teste;
- [ ] preservar `vector_editor.py` como `vector_editor_legacy.py` ou mantê-lo
      claramente marcado como protótipo;
- [ ] renomear/desabilitar mutações da aba Sketch legada;
- [ ] criar fixture JSON/desenho para o caso real: contorno externo aberto,
      duas aberturas próximas de estrutura, dois círculos internos;
- [ ] teste que abrir/diagnosticar não altera Sketch/FCStd;
- [ ] registrar comportamento atual da área de Trabalho e provider CAM;
- [ ] adicionar feature flag `UseNewVectorEditor` se necessário para migração;
- [ ] nenhum código novo de geometria entra em `ui.py`.

Aceite:

- diagnóstico é somente leitura;
- abrir/fechar a janela não modifica Sketch;
- testes atuais de `operations` e `validators` continuam verdes;
- fixture problemática existe sem depender de screenshot.

### Fase 1 — Domínio, comandos e persistência

**Objetivo:** criar a fonte de verdade antes de trabalhar na UX.

Tarefas:

- [ ] `Vec2`, `BBox2D`, spans e entidades;
- [ ] invariantes de caminho;
- [ ] `VectorDocument`, `Layer`, `WorkArea`;
- [ ] IDs UUID estáveis;
- [ ] serializer JSON v1 + checksum + round-trip;
- [ ] objeto `WoodCAM2D_VectorDocument` no FCStd;
- [ ] `Add/Delete/Move/MoveNode` commands;
- [ ] transação FreeCAD e observer de Undo/Redo;
- [ ] cache Shape derivado;
- [ ] testes puros e FreeCADCmd.

Aceite:

- linha/círculo criados por API sobrevivem save/reopen;
- JSON antes/depois do round-trip é semanticamente idêntico;
- IDs não mudam;
- `Ctrl+Z` desfaz uma criação/movimento e Redo reaplica;
- erro no comando aborta transação e restaura JSON/Shape;
- 100 ciclos Undo/Redo sem divergência.

### Fase 2 — View e interação palpável

**Objetivo:** resolver definitivamente a experiência relatada pelo usuário.

Tarefas:

- [ ] `Editor2DWidget`, View Y-up, scene adapter;
- [ ] área de Trabalho por bounds reais e sceneRect dinâmico;
- [ ] zoom/pan/fit;
- [ ] hit-test em pixels;
- [ ] seleção simples, Shift e caixa direcional;
- [ ] drag de objeto por delta;
- [ ] duplo clique/N para nós;
- [ ] alças somente no modo de nós;
- [ ] drag threshold e nó sem teleporte;
- [ ] balões de movimento/nó;
- [ ] Delete/Esc/Ctrl+A/Ctrl+Z/Redo;
- [ ] testes Qt com eventos reais.

Aceite:

- um clique nunca pega ponta fora de `NODE_EDIT`;
- clique solto em nó não muda nem `1e-12` da coordenada;
- drag começa da posição original;
- mover objeto atualiza modelo, cena, nós e snap no mesmo lugar;
- hit-test equivalente em 25%, 100% e 800%;
- 100 repetições de selecionar/mover/nó sem comportamento intermitente;
- reabrir FreeCAD mantém resultado.

**Pausa obrigatória:** mostrar este marco ao usuário antes de adicionar a barra
completa. Se ainda houver seleção inconsistente, corrigir a base.

### Fase 3 — Desenho e snap

**Objetivo:** desenhar uma peça real com medidas exatas.

Tarefas:

- [ ] motor de polilinha/linha;
- [ ] retângulo/quadrado;
- [ ] círculo;
- [ ] arco 3 pontos e centro-início-fim;
- [ ] triângulo/polígono;
- [ ] input numérico contextual;
- [ ] snap endpoint/midpoint/center/quadrant/intersection/on-geometry/grid;
- [ ] `Shift` desativa snap;
- [ ] configurações persistentes;
- [ ] overlays e rótulos de candidato.

Aceite:

- desenhar placa retangular 500 × 300 com dois furos Ø35 em posições exatas;
- dimensões do domínio batem a `1e-6 mm`;
- `Tab` fecha polilinha num único caminho;
- snap não altera geometria antes da confirmação;
- entrada com vírgula e ponto funciona;
- cada forma completa é um Undo.

### Fase 4 — Topologia, diagnóstico e reparos

**Objetivo:** tornar importações/desenhos imperfeitos usináveis com segurança.

Tarefas:

- [ ] `TopologyGraph` e graus;
- [ ] abertos, duplicados, zero/microspans, interseções, ramificações;
- [ ] join endpoints;
- [ ] fechar por reta e ponto médio;
- [ ] conectar ponta ao span com divisão explícita;
- [ ] split/cut;
- [ ] trim interativo;
- [ ] extend;
- [ ] overlays clicáveis e lista de ocorrências;
- [ ] curva suave depois das operações lineares estáveis.

Aceite:

- fixture original sugere as estruturas corretas para cada ponta;
- prévia não muda documento/JSON;
- aplicar corrige somente o escolhido e é um Undo;
- círculo/linha alvo é dividido de forma topológica quando necessário;
- ramificação é detectada e bloqueada;
- nenhuma forma distante se move;
- diagnóstico repetido retorna o mesmo resultado.

### Fase 5 — Importação

**Objetivo:** trazer trabalho existente para o editor.

Ordem:

1. Sketch;
2. face/Shape;
3. DXF;
4. SVG.

Aceite:

- Placement global preservado;
- linha/arco/círculo continuam exatos;
- unidade e escala documentadas;
- original nunca é alterado;
- unsupported aparecem no relatório;
- importação inteira é um Undo;
- golden files passam dentro da tolerância.

### Fase 6 — Peças e área de Trabalho

**Objetivo:** classificar e organizar sem separar furos.

Tarefas:

- [ ] árvore de contenção completa;
- [ ] `Piece2D` e metadados;
- [ ] fora da mesa/colisão;
- [ ] quantidade e instâncias;
- [ ] algoritmo inicial de organização;
- [ ] preview e comando composto;
- [ ] camadas e bloqueio no escopo.

Aceite:

- duas peças, cada uma com dois furos, resultam em 2 peças e 4 internos;
- nenhum furo vira peça;
- mover/organizar peça move internos com o mesmo transform;
- limites respeitam origem real da aba Trabalho;
- peças que não cabem ficam inalteradas e recebem relatório em mm;
- Undo restaura todas as posições.

### Fase 7 — Ponte CAM e PanelNest

**Objetivo:** transformar desenho validado em usinagem real.

Tarefas:

- [x] GeometryProvider explícito;
- [x] adapter Editor → WoodCAM;
- [x] roteamento de preview/moves/simulação/G-code;
- [x] revision token/stale;
- [x] adapter Editor ↔ PanelNest;
- [x] perfis internos no contrato PanelNest;
- [x] testes contra resultados atuais.

Aceite:

- mesma peça produz contornos/furos esperados;
- internos são cortados antes do externo;
- edição posterior marca operação derivada como desatualizada;
- usuário vê claramente qual fonte está sendo usinada;
- PanelNest recebe peça inteira com furos/recortes, não itens soltos;
- G-code existente para seleção FreeCAD continua igual.

### Fase 8 — Recursos avançados do Aspire/marcenaria

Ordem sugerida:

1. camadas completas;
2. rotação/escala/espelho/alinhar/distribuir;
3. offset;
4. booleanos/weld;
5. filete normal;
6. dogbone manual;
7. T-bone manual;
8. dogbone/T-bone automático em cantos internos;
9. exportação DXF/SVG;
10. smart snapping avançado.

Cada operação exige preview, comando, Undo, save/reopen e teste geométrico.

---

## 20. Estratégia de testes

### 20.1 Testes puros (`pytest` normal)

Cobrir:

- `Vec2`, transformações e bounds;
- span `point_at`, nearest, split, reverse e length;
- invariantes de path;
- round-trip/migração/checksum;
- apply/revert de todo comando;
- join nas quatro orientações;
- split de linha/arco/círculo;
- snap e desempate;
- topologia/grau/componentes;
- containment com externos, furos e ilhas;
- duplicatas, zero, interseções e ramificações;
- organização preservando internos;
- adapter CAM e flatten por tolerância.

Não usar FreeCAD ou Qt nesses testes.

### 20.2 Testes Qt/QTest

Executar com plataforma offscreen quando possível. Casos obrigatórios:

- clique simples seleciona;
- clique simples perto da ponta em SELECT não entra em nós;
- duplo clique entra em nós;
- clique no nó sem drag não muda coordenada;
- drag abaixo do limiar não muda;
- drag acima do limiar usa delta correto;
- Esc durante drag restaura snapshot;
- um drag cria um único Undo;
- seleção Shift;
- janela esquerda→direita versus direita→esquerda;
- hit-test em múltiplos zooms;
- `Shift` desativa snap;
- pan/zoom não altera modelo;
- balão tem valor correto;
- itens de camada bloqueada não capturam mouse.

### 20.3 Testes FreeCADCmd/FreeCAD GUI

- criar documento e feature persistente;
- salvar FCStd e reabrir;
- comparar IDs/JSON/Shape;
- transação commit/abort;
- `doc.undo()/redo()` e observer;
- importação de Sketch com Placement;
- cache Shape reconstruído;
- ParamGet separado de geometria;
- provider atual versus provider Editor.

### 20.4 Fixtures/goldens

Criar arquivos pequenos e legíveis:

- `open_neck_two_circles.json`: caso que motivou a mudança;
- `two_parts_two_holes_each.json`;
- `nested_island.json`;
- `branch_t_junction.json`;
- `duplicates_and_zero_spans.json`;
- `work_area_origins.json`;
- Sketch com linha/arco/círculo e Placement;
- DXF mm/polegada;
- SVG com transform e arco.

Evitar depender somente de FCStd binário; manter JSON esperado ao lado.

### 20.5 Comandos de verificação esperados

A próxima IA deve registrar os comandos reais disponíveis no ambiente. Meta:

```bash
pytest -q tests/vector2d/unit
QT_QPA_PLATFORM=offscreen pytest -q tests/vector2d/qt
freecadcmd tests/vector2d/freecad/run_smoke.py
pytest -q tests/test_operations.py tests/test_validators.py
```

Se o executável tiver outro nome/caminho, documentá-lo sem esconder a ausência.

### 20.6 Roteiro manual de aceite R1/R2

1. abrir um FCStd novo;
2. configurar Trabalho 1850 × 2750;
3. abrir Editor 2D e enquadrar mesa;
4. criar linha de 100 mm e círculo Ø80;
5. clicar na metade da linha e mover 20 mm;
6. confirmar que a linha inteira moveu;
7. clicar perto da ponta e confirmar que continua selecionando o objeto;
8. dar dois cliques, clicar na ponta sem arrastar e confirmar nenhuma alteração;
9. arrastar a ponta 15 mm e conferir balão;
10. `Ctrl+Z` volta exatamente; Redo reaplica;
11. repetir em zoom 25% e 800%;
12. salvar, fechar o FreeCAD, reabrir e comparar;
13. fechar somente a janela do WoodCAM e reabrir sem perder desenho;
14. mover o círculo e confirmar que centro, nós, snap e persistência concordam;
15. cancelar um drag com Esc e confirmar JSON inalterado.

### 20.7 Roteiro manual de aceite de peças/CAM

1. desenhar/importar duas placas fechadas;
2. criar dois furos em cada uma;
3. validar: 2 peças, 4 internos, 0 abertos;
4. organizar dentro da mesa;
5. confirmar que furos acompanham as placas;
6. editar um furo e marcar peça/operação como stale;
7. selecionar fonte Editor 2D no CAM;
8. pré-visualizar Corte/Furo;
9. confirmar internos antes de externos;
10. simular e gerar G-code de teste;
11. Undo da organização restaura tudo;
12. salvar/reabrir e repetir diagnóstico.

---

## 21. Desempenho e precisão

Metas iniciais:

- interação fluida com 2.000 entidades;
- abrir documento com 10.000 entidades sem corrupção;
- pan/zoom não recalcula topologia inteira;
- preview atualiza somente entidades afetadas;
- validação pesada pode ser cancelada e mostrar progresso;
- tolerância visual em pixels;
- tolerância geométrica explícita por operação;
- curvas exatas no domínio;
- flatten CAM com deflexão apropriada, sem reutilizar a grade visual.

Não otimizar antes de medir. A correção do modelo vem antes. Se necessário,
usar índice espacial para candidatos e cache de bounds/revision, invalidado por
IDs alterados.

---

## 22. Segurança, compatibilidade e coisas proibidas

### 22.1 Proibido

- adicionar lógica de domínio nova dentro de `VectorCanvas` atual;
- usar `QGraphicsItem` como banco de dados;
- serializar itens Qt;
- deixar `ItemIsMovable` modificar geometria diretamente;
- mudar modelo no `mousePress` ou em todo `mouseMove`;
- criar restrições automáticas no Sketcher para “resolver” o editor;
- usar `EdgeN`/índice como identidade persistente;
- unir automaticamente a coisa mais próxima sem prévia/topologia;
- arredondar coordenadas em cada gesto;
- discretizar círculos/arcos no documento principal;
- adicionar dependência pip obrigatória que não acompanha a pasta do módulo;
- apagar o protótipo/legado antes de fixture e migração;
- declarar fase pronta sem save/reopen e Undo;
- gerar CAM de geometria inválida sem bloqueio claro;
- separar furos durante organização.

### 22.2 Compatibilidade

- manter fallback PySide6/PySide2/PySide enquanto as versões suportadas do
  FreeCAD não forem formalmente reduzidas;
- centralizar diferenças de enums/sinais Qt num módulo de compatibilidade;
- não assumir que o PanelNest está instalado;
- WoodCAM sozinho continua mostrando sua bancada;
- com PanelNest, WoodCAM continua como botão/integrado conforme comportamento
  existente;
- código e assets permanecem dentro da pasta copiável `WoodCAM2D`;
- nenhuma configuração pessoal deve ser embutida no pacote.

### 22.3 Dados do usuário

- importação é cópia;
- correção destrutiva exige preview/Undo;
- migração JSON trabalha em transação e preserva original em erro;
- operação CAM existente nunca é reescrita silenciosamente após edição;
- documentos desconhecidos não são “resetados para padrão”.

---

## 23. Primeiras tarefas exatas para a próxima IA

Executar nesta ordem; não pular diretamente para novas formas.

### Entrega A — Preparação

1. ler `AGENTS.md` e este plano inteiro;
2. abrir `vector_editor.py`, `ui.py:3187–3508`,
   `vector_diagnostics.py`, `geometry_reader.py` e persistência de operações;
3. rodar os testes atuais e registrar baseline;
4. criar branch/checkpoint se houver controle de versão; se não houver, evitar
   edições destrutivas e manter legado intacto;
5. criar `woodcam_editor/` e `tests/vector2d/`;
6. criar fixture do desenho problemático;
7. desabilitar somente as mutações do Sketch legado, sem quebrar diagnóstico.

### Entrega B — Modelo vertical mínimo

1. implementar `Vec2`, `LineSpan`, `PathEntity`, `CircleEntity`, `Layer` e
   `VectorDocument`;
2. implementar JSON v1/round-trip;
3. implementar `AddEntitiesCommand`, `MoveEntitiesCommand` e
   `MoveNodeCommand` puros;
4. testes apply/revert e invariantes;
5. implementar `FreeCADDocumentStore` e feature persistente;
6. testar save/reopen/undo/redo em FreeCADCmd;
7. somente então criar `SceneAdapter` para linha/círculo.

### Entrega C — Corrigir a UX principal

1. `SelectTool` com clique/Shift/drag do corpo;
2. remover qualquer `ItemIsMovable` da apresentação nova;
3. `mouseDoubleClickEvent`/tecla N para `NodeTool`;
4. handles apenas no modo Node;
5. press context e drag threshold;
6. preview por delta; commit no release;
7. balão de medidas;
8. testes Qt que reproduzem exatamente os relatos do usuário;
9. mostrar ao usuário para validação antes de seguir.

### Relatório que a IA deve deixar ao terminar cada entrega

```text
Fase/entrega:
Arquivos alterados:
Decisões tomadas:
Testes executados e resultados:
Critérios de aceite aprovados:
Critérios ainda pendentes:
Riscos conhecidos:
Próximo passo permitido:
```

Atualizar este documento somente para registrar decisão nova comprovada, não
para apagar histórico de problemas.

---

## 24. Definition of Done do projeto

O Editor 2D será considerado pronto para produção quando:

- [x] seleção, movimento e nós obedecem integralmente ao contrato;
- [x] nenhum clique solto modifica geometria;
- [x] todas as mutações têm Undo/Redo atômico;
- [x] desenho sobrevive janela/FreeCAD/save/reopen;
- [x] linha, polilinha, retângulo, círculo, arco e polígono aceitam medidas;
- [x] snap funciona independentemente de zoom e mostra candidato;
- [x] join/close/trim/extend têm prévia e topologia protegida;
- [x] validador localiza abertos, duplicados, zero, interseções e ramificações;
- [x] Sketch/DXF/SVG prioritários importam com relatório e sem alterar fonte;
- [x] peças externas absorvem todos os furos/recortes;
- [x] organizador respeita Trabalho, margem, rotação, colisão e Undo;
- [x] dogbone/T-bone manual e automático respeitam cantos internos/fresa;
- [x] Editor alimenta Corte/Furo/Preenchimento por provider explícito;
- [x] PanelNest recebe peça e internos juntos;
- [x] edição marca derivados CAM como stale;
- [x] testes puros, Qt e FreeCAD cobrem fluxos críticos;
- [x] funções antigas do WoodCAM continuam verdes;
- [x] instalação continua sendo apenas copiar pastas para `Mod`;
- [x] dados vetoriais, preferências e ferramentas persistem ao reiniciar.

---

## 25. Referências oficiais estudadas

Documentação Vectric/Aspire usada para amadurecer o fluxo:

- [Aspire V12 — documentação completa](https://docs.vectric.com/docs/V12.0/Aspire/ENU/Help/page/single-page/index.html)
- [Create Component from Bitmap](https://docs.vectric.com/docs/V12.0/Aspire/ENU/Help/form/create-component-from-bitmap/)
- [Object Selection Tools](https://docs.vectric.com/docs/V12.0/Aspire/ENU/Help/form/object-selection-tools/)
- [Vector Selection Mode](https://docs.vectric.com/docs/V12.5/Aspire/ENU/Help/form/vector-selection-mode/)
- [Node Editing Mode](https://docs.vectric.com/docs/V12.0/VCarveDesktop/ENU/Help/form/node-editing-mode/index.html)
- [Draw Line / Polyline](https://docs.vectric.com/docs/V12.5/Aspire/ENU/Help/form/Create%20Line%20-%20Polyline/index.html)
- [Draw Rectangle](https://docs.vectric.com/docs/V12.0/Aspire/ENU/Help/form/Draw%20Rectangle/index.html)
- [Draw Arc](https://docs.vectric.com/docs/V12.5/Aspire/ENU/Help/form/Create%20Arc/index.html)
- [Join Open Vectors](https://docs.vectric.com/docs/V12.0/Aspire/ENU/Help/form/Join%20Vectors/)
- [Join/Close with Straight Line](https://docs.vectric.com/docs/V12.0/Aspire/ENU/Help/form/join-close-vector-with-a-straight-line/)
- [Join/Close with Smooth Curve](https://docs.vectric.com/docs/V12.5/Aspire/ENU/Help/form/join-close-vector-with-a-smooth-curve/index.html)
- [Interactive Vector Trim](https://docs.vectric.com/docs/V12.5/Aspire/ENU/Help/form/Interactive%20Vector%20Trim/index.html)
- [Offset Vectors](https://docs.vectric.com/docs/V12.0/Aspire/ENU/Help/form/Offset%20Vectors/index.html)
- [Create Fillets — normal, dogbone e T-bone](https://docs.vectric.com/docs/V12.0/Aspire/ENU/Help/form/Create%20Fillets/index.html)
- [Trace Bitmap — limiar, cores, ruído, cantos e prévia](https://docs.vectric.com/docs/V12.5/Aspire/ENU/Help/form/Trace%20Bitmap/)
- [Nest Parts — peças, folgas, rotações e contorno](https://docs.vectric.com/docs/V12.5/Cut2DPro/ENU/Help/form/Nest%20Parts/index.html)
- [Vector Validator](https://docs.vectric.com/docs/V12.0/VCarveDesktop/ENU/Help/form/Vector%20Validator/index.html)
- [Import Bitmap / Vectors](https://docs.vectric.com/docs/V12.0/Aspire/ENU/Help/form/import-vectors/)
- [File Menu / Export](https://docs.vectric.com/docs/V12.0/Aspire/ENU/Help/form/file-menu/)
- [Edit Menu / Undo, seleção e Join](https://docs.vectric.com/docs/V12.0/Aspire/ENU/Help/form/edit-menu/index.html)

Princípios extraídos dessas referências:

1. seleção, nós e transformações são modos distintos;
2. snap usa raio em pixels e oferece fontes configuráveis;
3. join, close, trim e extend são comandos explícitos;
4. medidas e quick input fazem parte do desenho, não são correção posterior;
5. validação marca problemas antes do toolpath;
6. dogbone/T-bone são operações geométricas interativas, não apenas opção CAM.

---

## 26. Registro de status para continuidade

### Baseline histórico concluído antes deste plano

- canvas experimental abriu dentro da aba `Editor 2D`;
- grade/mesa, linha e círculo foram desenhados;
- hit-area da linha foi aumentada;
- movimento/seleção/nós foram experimentados;
- tentativa Sketch e tentativa de conexão foram identificadas como inseguras;
- comportamento desejado de um clique/dois cliques foi definido pelo usuário;
- recursos do Aspire foram pesquisados;
- código atual foi auditado.

### Pendências registradas quando o plano foi criado

- nenhum `VectorDocument` real;
- nenhuma persistência do Editor;
- nenhum histórico confiável;
- nenhuma máquina de estados correta;
- nenhuma peça/organizador;
- nenhuma ponte Editor → CAM;
- nenhuma importação Sketch no Editor;
- nenhum teste do editor novo.

### Autorização histórica que iniciou a reconstrução

Somente Fase 0 e Fase 1, seguidas da interação da Fase 2. O protótipo atual
deve continuar disponível como referência até o marco R1 ser aprovado.

### Implementação entregue em 12 de julho de 2026

- Fases 0 a 7 concluídas e Fase 8 concluída para filete, dogbone e T-bone;
- `VectorDocument` é a fonte de verdade; a cena apenas projeta;
- persistência JSON determinística no `WoodCAM2D_VectorDocument` do FCStd;
- comandos integrados a transações e Undo/Redo global do FreeCAD;
- contrato de mouse, desenho, snap, camadas, propriedades e transformações;
- diagnóstico e reparos com prévia, incluindo conexão e splice sem ramificação;
- importação Sketch/Shape/DXF/SVG e exportação DXF/SVG multicamada;
- classificação, metadados e organização raster por contorno real, com
  MaxRects como comparador/fallback, de peças completas;
- provider explícito para CAM, detecção de operação stale e ponte PanelNest;
- dogbone/T-bone manual e automático apenas em cantos internos de 90 graus;
- aba Sketch antiga congelada e identificada como diagnóstico legado;
- 157 testes unitários, 34 testes Qt, 63 testes legados e sete smokes
  FreeCADCmd aprovados na entrega.

### Polimento de interação entregue após o primeiro aceite visual

- barra vertical de desenho com ícones CAD e topo agrupado por fluxo;
- botão direito cancela operação/prévia e retorna a Selecionar;
- grade visual usa o espaçamento configurado sem depender de Snap estar ativo;
- `Unir 2 pontas (reta)` permite escolher pontos de caminhos diferentes,
  mostra a abertura em milímetros e não teletransporta a geometria;
- conexão antiga foi esclarecida como `Projetar ponta na geometria`;
- papel externo/interno de dogbone e T-bone é detectado pela peça/contenção,
  com escolha manual apenas como exceção;
- organizador shelf foi substituído por raster/bitmask de contorno real, com
  MaxRects/bottom-left determinístico como comparador/fallback;
- ponte PanelNest valida Face/solid/volume e preserva o intercâmbio anterior se
  alguma peça for ambígua ou inválida.
- PanelNest preserva X/Y/rotação do Editor e só executa novo nesting por comando
  explícito do usuário;
- vetorização preto/branco de bitmaps usa limiar, ruído, cantos, suavização,
  tamanho em milímetros e confirmação por prévia;
- propriedades exatas distinguem escala, largura, altura, raio/diâmetro e raios
  da elipse; a mudança da origem de Trabalho atualiza a referência sem mover a
  câmera automaticamente.
- importação de `CAM Chapa` pela árvore reorienta arestas OCC coincidentes e
  preserva uma face por sólido do compound; DXFs PanelNest podem limpar, com
  prévia/Undo, linhas abertas já cobertas por contornos fechados; contornos de
  peças apenas encostados podem ser classificados para o organizador aplicar
  espaçamento sem liberar o CAM enquanto ainda se tocam.

### Limites deliberados desta entrega

- trim e extend priorizam casos lineares seguros;
- offset fechado usa o caso linear/miter validado;
- elipse não circular não é alvo de splice;
- Bézier cúbica pertence ao modelo/importação, mas ainda não tem ferramenta de
  desenho dedicada;
- texto, vetorização de bitmap, booleanos gerais e modelagem 3D continuam fora
  do núcleo inicial;
- quantidades adicionais são replicadas na ponte PanelNest, não desenhadas como
  cópias no canvas do Editor;
- todo G-code ainda exige conferência visual e teste seguro na máquina real.

### Extensão entregue em 15 de julho de 2026 — relevo por imagem

Sem alterar o núcleo 2D, foi criado o pacote irmão `woodcam_relief/`. Ele
converte luminosidade em um heightmap local, mostra prévia sombreada no diálogo
e overlay 3D transitório no FreeCAD, e somente na confirmação cria uma malha
fechada persistente por transação/Undo. Pixels, parâmetros e malha não entram no
`VectorDocument`; o FCStd guarda o mapa processado e os parâmetros no objeto de
relevo. Modelagem 3D geral e toolpaths de relevo continuam fora do escopo deste
marco.

Após a comparação com a conversão documentada do Aspire, o modo padrão passou
a converter diretamente as tonalidades do bitmap em altura. PNG transparente
é processado sem achatamento sobre branco: alfa zero permanece sempre na base.
O volume arredondado por campo de distância continua disponível, mas está
identificado como experimento de silhueta do WoodCAM, não como equivalência ao
Aspire. O modo heightmap literal permanece disponível para mapas de profundidade
externos. A janela usa o mesmo contrato de movimento `Qt.Widget` da janela
principal em Wayland/Niri. O perfil de produção `Detalhado — escamas, penas e
objetos`, aferido com o mesmo bitmap de peixe da demonstração de referência,
usa malha de 512 pontos no maior eixo e parâmetros coerentes de volume, forma e
microtextura. `Retrato com lissage — rostos e pelos` aplica suavização
normalizada dentro da máscara, sem erodir transparência/contorno. A janela
classifica fontes transparentes, de fundo uniforme ou com cenário e avisa que
uma paisagem não pode ser isolada deterministicamente como um componente.
Para caber na área útil do FreeCAD sem sobreposição, os ajustes de uso frequente
ficam na aba `Essencial` e os parâmetros técnicos ficam em `Avançado`, com
rolagem independente.
O componente fotográfico separa corpo, estrutura média e microtextura para
evitar picos de pelos/escamas sem perder olhos ou focinho; o fundo é removido
somente quando conectado às bordas, sem perfurar detalhes escuros internos. A
borda usa queda progressiva interna até a base, sem halo externo.
O modo multiescala também pode misturar volume macro derivado da distância à
silhueta antes de restaurar estrutura e textura; imagens sem fundo separável
permanecem sem domo sintético.
Acima da altura de referência de 3 mm, a compensação padrão mantém microdetalhe
aproximadamente estável em milímetros e transfere a altura adicional para o
volume macro, aumentando também a suavização de forma/borda.

### Próximo passo após esta entrega

Executar o roteiro manual de aceite no FreeCAD GUI do computador de produção e
registrar apenas defeitos reproduzíveis. Novos recursos devem continuar usando
o domínio/comandos existentes; não reativar `VectorCanvas` nem mutações no
Sketcher.

---

## 27. Frase-guia para toda decisão futura

> A geometria pertence ao documento vetorial; a ferramenta propõe; o comando
> confirma; a cena apenas mostra; o CAM consome somente um resultado validado.

Se uma implementação violar essa frase, ela provavelmente recriará os mesmos
bugs que motivaram este plano.
