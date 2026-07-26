# Relatório de implementação — Editor 2D vetorial WoodCAM

**Data:** 12 de julho de 2026  
**Código ativo:** `/home/danielma/Projetos/CNC Marcenaria`  
**Instalação ativa:** `~/.local/share/FreeCAD/Mod/WoodCAM2D` aponta para o
código acima  
**FreeCAD validado:** 1.1.1, Python 3.14, PySide6

Este documento registra o resultado entregue. O plano mestre continua sendo o
contrato de produto e preserva o histórico que levou à reconstrução.

## 1. Resultado

O protótipo baseado em itens soltos da cena foi substituído por um editor
vetorial com documento próprio. A geometria não vive no `QGraphicsScene`; ela
vive no `VectorDocument`, é alterada somente por comandos e é salva no FCStd.

O fluxo disponível é:

```text
desenhar/importar
       ↓
selecionar, medir, editar nós e transformar
       ↓
diagnosticar e reparar com prévia
       ↓
classificar externo + furos/recortes
       ↓
organizar na área de Trabalho
       ↓
usar em Corte/Furo/Preenchimento ou enviar ao PanelNest
```

O menu `CAM` do Editor também oferece `Ver percurso de Corte aqui`. Ele projeta
na própria área 2D os mesmos movimentos XY que serão emitidos no G-code
(vermelho = corte, laranja = entrada/descida, cinza tracejado = rápido), sem
criar entidades, alterar vetores, mudar a seleção ou recalcular a operação.
Uma mutação geométrica ou de camada remove a sobreposição imediatamente, para
que um percurso antigo nunca pareça válido sobre um desenho novo.

## 2. Contrato de interação implementado

- um clique seleciona o vetor inteiro;
- arrastar o corpo move a seleção inteira;
- duplo clique ou `N` entra no modo de nós;
- clicar num nó apenas seleciona;
- o nó só muda depois de pressionar, ultrapassar o limiar de arraste e mover;
- clique solto nunca teletransporta ponto;
- alvos de vetor/nó e snap são medidos em pixels e continuam utilizáveis em
  diferentes níveis de zoom;
- `Esc` cancela prévia/gesto e depois sai do modo;
- uma interação confirmada corresponde a um comando e um Undo.

Atalhos: `S`, `N`, `L`, `P`, `R`, `C`, `E`, `A`, `G`, `Delete`, `Ctrl+Z`,
`Ctrl+Shift+Z`/`Ctrl+Y` e `F` para enquadrar.

## 3. Arquitetura entregue

```text
woodcam_editor/
├── domain/       # documento, entidades, spans, comandos, topologia e validação
├── geometry/     # matemática 2D e previews puros de modificadores
├── application/  # controlador, seleção, snap, camadas, peças e organização
├── presentation/ # widget, view, cena, overlays, painéis e ferramentas modais
├── adapters/     # FCStd, transações, geometria WoodCAM e ponte PanelNest
├── importers/    # Sketch, Part Shape, DXF, SVG e remapeamento de camadas
└── exporters/    # DXF e SVG
```

Regras estruturais já aplicadas:

1. `VectorDocument` é a única fonte de verdade.
2. A cena não grava coordenadas; `SceneAdapter` projeta o documento.
3. Preview é puro e magenta; só a confirmação executa comando.
4. Comandos guardam estado suficiente para `apply/revert` atômico.
5. Importadores criam cópia e relatório, sem modificar Sketch/Shape/arquivo.
6. Adapters convertem contratos; CAM e PanelNest não conhecem itens Qt.

## 4. Modelo e persistência

O domínio preserva linhas, arcos, círculos, elipses e Bézier cúbicas sem usar
polilinha como armazenamento universal. `PathEntity` mantém spans ordenados e
conectados; círculos e elipses permanecem entidades exatas.

O objeto `WoodCAM2D_VectorDocument` no FCStd conserva:

- UUID, versão de esquema e revisão;
- entidades e IDs estáveis;
- camadas, cor, visibilidade, bloqueio e finalidade;
- peças e seus metadados;
- área de Trabalho e metadados do editor;
- JSON determinístico e checksum;
- `Shape` auxiliar apenas para visualização/interoperabilidade.

`FreeCADCommandSession` abre uma transação para comando + persistência. A área
da aba Trabalho também usa `SetWorkAreaCommand`: digitar apenas atualiza a
prévia, e `editingFinished` confirma/persiste uma etapa de Undo. O
`UndoMode` do documento é habilitado explicitamente. Um timer da janela percebe
Undo/Redo feito na interface global e recarrega a projeção. Ao trocar o FCStd
ativo com a janela ainda aberta, a sessão/widget troca atomicamente; deltas
seguros são salvos ou mantidos no cache por documento, sem misturar A e B. Um
guard síncrono por documento + token também cancela qualquer clique/comando que
chegue antes do próximo tick do timer, eliminando a janela de corrida A→B.

Preferências visuais como grade, snap e tolerância de união usam `ParamGet` e
`FreeCAD.saveParameter()`. A biblioteca de fresas e os valores das abas CAM
continuam usando a persistência já existente do WoodCAM.

## 5. Desenho, seleção e edição

Ferramentas disponíveis:

- linha e polilinha aberta/fechada;
- texto por fonte instalada, convertido explicitamente em contornos fechados
  e agrupados para que letras/vazados possam mover e usinar como uma unidade;
  o grupo preserva as configurações para permitir edição posterior de texto,
  fonte, altura e estilo em um único Undo;
- retângulo/quadrado;
- círculo e elipse;
- arco por três pontos;
- triângulo e polígono de 3 a 64 lados;
- estrela paramétrica de 3 a 64 pontas, com profundidade interna ajustável;
- balão com comprimento, delta X/Y, ângulo, raio/diâmetro ou largura/altura;
- painel numérico de X, Y, largura e altura independentes; proporção opcional
  conduzida pela última dimensão editada; raio/diâmetro de círculo e raios X/Y
  e rotação de elipse;
- movimento, giro, escala, espelho, alinhamento e distribuição;
- seleção simples, aditiva, `Ctrl+A` e janela direcional;
- edição de nós separada do modo selecionar.

Camadas ocultas/bloqueadas não entram em seleção global, snap, transformação,
exclusão nem CAM.

A apresentação usa uma barra vertical estreita com ícones CAD para ferramentas
de desenho. A barra superior ficou reservada aos fluxos `Arquivo`, `Reparar`,
`Filetes`, `Peças` e `CAM`, além de Undo/Redo, grade, Snap e tolerância. O botão
direito cancela qualquer gesto/prévia, limpa a seleção e retorna a Selecionar.

## 6. Snap, topologia, diagnóstico e reparos

Snap disponível em extremidade, meio, centro, quadrante, interseção local,
geometria e grade. O raio visual e a tolerância geométrica de união são valores
separados.

O diagnóstico, sem mutar, abre uma lista detalhada e clicável. Selecionar uma
ocorrência destaca em magenta, seleciona os vetores envolvidos e enquadra o
local. Ele informa:

- caminhos abertos;
- duplicados;
- spans nulos/curtos;
- auto-interseções e cruzamentos entre contornos;
- ramificações;
- entidades fora da área de Trabalho;
- peças desatualizadas/incompletas.

Importações diretas de Assembly e PanelNest carregam um escopo de instância
física por chapa. Diagnóstico e validação não comparam vetores de instâncias
diferentes como se pertencessem à mesma peça: cópias idênticas, contatos e
interseções entre chapas distintas deixam de gerar bloqueios falsos, enquanto
o mesmo conjunto de verificações continua integral dentro de cada chapa.

`Limpar sobrelinhas/duplicados…` transforma as sugestões conservadoras numa
operação preview-first sobre todo o desenho desbloqueado: remove cópias exatas
e caminhos abertos cujos spans já estão integralmente cobertos por um mesmo
contorno fechado. Mostra em magenta somente o que será apagado e confirma um
único comando com Undo. Sobreposição colinear parcial continua sendo
diagnóstico/Trim, pois escolher automaticamente o trecho sobrevivente poderia
mudar a peça. O diagnóstico agrega essas sobrelinhas cobertas numa ocorrência
clara em vez de produzir centenas de ramificações/interseções derivadas.

Contornos fechados de peças diferentes que apenas compartilham borda ou vértice
são distinguidos de cruzamentos/sobreposição de área. Eles podem ser
classificados para que o organizador aplique espaçamento, mas permanecem
bloqueados como fonte CAM enquanto ainda se tocam.

Reparos e modificadores:

- fechar pontas dentro da tolerância;
- unir em lote vetores abertos selecionados dentro da tolerância, preservando
  lacunas maiores e mostrando uma prévia antes de um único Undo;
- `Subtrair vetores (criar furo/recorte interno)` diferencia com clareza a
  união lógica de uma chapa com um furo distante do simples fechamento de
  vetores: o contorno interno permanece exato e separado geometricamente, mas
  é agrupado com o externo para seleção, movimentação, organização e CAM;
  `Soldar vetores` continua reservado a perfis fechados que realmente se
  sobrepõem;
- unir duas pontas explicitamente escolhidas em caminhos diferentes; pontas
  coincidentes são fundidas e lacunas maiores recebem uma reta sem deformação;
- projetar uma ponta numa reta, arco ou círculo alvo, com aviso de ramificação;
- splice de um caminho aberto em contorno, escolhendo lado curto/longo e sem
  deixar ramificação T;
- trim interativo;
- extend;
- offset conservador;
- filete normal;
- dogbone e T-bone manual;
- dogbone e T-bone automático.

O snap inteligente acompanha a criação a partir do último ponto confirmado:
horizontal, vertical, ângulo configurável, perpendicular em vetores finitos e
arcos, e tangência em círculos/arcos. São somente alvos transitórios na prévia
do cursor — não são restrições ocultas nem alteram o documento até o clique
que confirma a ferramenta.

O modo automático usa peças/árvore de contenção para distinguir externo e
interno. Só aceita cantos internos lineares de 90 graus; funciona em orientação
CW/CCW, mostra todos os candidatos/rejeições e aplica tudo em um único Undo.

## 7. Importação e exportação

Importações são snapshots independentes:

- um ou mais Sketches selecionados;
- Part Shape/faces/objetos selecionados;
- itens/grupos de layout PanelNest selecionados na árvore, resolvidos para a
  respectiva `CAM Chapa` sem importar a chapa-base;
- DXF ASCII comum e backend temporário do FreeCAD quando disponível;
- SVG com paths e formas básicas, transforms, viewBox, unidades e conversão
  para o eixo Y positivo.

`Arquivo → Vetorizar imagem…` aceita formatos raster comuns, mostra original e
máscara, oferece limiar, inversão, filtro de ruído, ajuste de cantos,
suavização e largura final. Potrace gera SVG temporário, o importador converte
as curvas ao domínio e o usuário ainda confirma uma prévia magenta no topo.

DXF preserva LINE, ARC, CIRCLE, LWPOLYLINE/bulge, POLYLINE, SPLINE cúbica e
ELLIPSE suportada, além de unidades e tabela de camadas. SVG preserva
M/L/H/V/C/Q/A/Z, formas básicas, grupos/camadas, cor e finalidade.

`remap_import_layers()` cria IDs determinísticos sem colidir com as camadas do
documento atual. Uma importação multicamada inteira é um comando composto e um
Undo. Exportadores DXF/SVG preservam as primitivas comuns e camadas.

O importador Shape reorienta arestas OCC já coincidentes antes de construir o
caminho, sem deslocar pontos. Em compounds 3D, importa a maior face horizontal
de cada sólido, e não apenas a maior face de todo o compound; assim todas as
formas de uma `CAM Chapa` chegam ao Editor.

## 8. Peças e organização

`Criar peças` monta árvore de contenção e associa ao externo todos os furos,
ilhas e recortes descendentes. Vetores abertos não viram peças.

Metadados disponíveis:

- nome;
- quantidade;
- material e espessura;
- sentido do veio;
- rotações permitidas: 0°, 0°/90° ou livre.

O organizador usa a área X/Y confirmada na aba `Trabalho`, margem/espaçamento e
rotações permitidas. O estágio principal rasteriza o contorno externo real em
bitmasks, permitindo intercalar concavidades, triângulos e curvas. MaxRects
continua sendo executado como comparador/fallback e vence se produzir resultado
melhor ou se o raster ultrapassar o custo conservador. Ambos respeitam origem,
bordas e spacing, movem externo e internos como unidade e mostram o destino em
magenta sobre a posição azul atual. `Aplicar organização` confirma todo o plano
em um comando; Cancelar/Esc não altera nada.

Quando a organização precisar de mais de uma chapa, cada área virtual recebe
um rótulo visual `Chapa 01`, `Chapa 02` etc. (ou `— prévia` em magenta antes da
confirmação). Esses rótulos são apenas apresentação: não são vetores, não
entram em seleção, CAM ou G-code e continuam sendo reconstruídos somente a
partir dos limites de chapa persistidos no documento.

Quantidades adicionais não são duplicadas no canvas. Elas são replicadas na
ponte PanelNest, evitando transformar o documento de desenho em um layout de
produção.

## 9. Integração CAM e PanelNest

O botão `Usar no CAM` escolhe explicitamente a fonte:

- seleção/geometria atual do FreeCAD; ou
- documento validado do Editor 2D.

O provider é usado por prévia, aplicação, simulação e G-code. O adapter entrega
o contrato existente `contours` + `holes`, ignorando camadas ocultas/de
referência. Se a geometria usada por uma operação aplicada muda, ela recebe
`stale` e aparece como desatualizada; o G-code fica bloqueado até reaplicar.

A instalação real do PanelNest não oferece uma API pública capaz de receber
arbitrários recortes internos. A ponte implementada cria em `WoodCAM → Peças`
uma `Part::Feature` por ocorrência, `Shape` física já subtraída pelos internos
e manifesto JSON completo. Documentos antigos que possuam
`WoodCAM2DPanelNestExchange` são migrados sem recriar as Shapes. O PanelNest
pode reconhecer essas ocorrências sem perder furos/recortes. A integração é
opcional, lazy e transacional.

O intercâmbio preserva a pose do Editor: cada shape local recebe `Placement`
em X/Y, rotação já incorporada ao perfil e Z=0. O manifesto usa
`layout_mode=preserve_editor_xy` e `automatic_nesting=false`; um novo nesting só
acontece se o usuário mandar o PanelNest gerar outro layout.

Antes de substituir o intercâmbio anterior, a ponte prepara e valida todos os
`Wire`, `Face`, sólidos, volumes e recortes. Contatos/retraçados inequívocos são
normalizados; autocruzamentos ambíguos são rejeitados com peça e causa. Furos e
recortes são subtraídos em conjunto com fallback controlado. Assim, um Shape
inválido nunca apaga silenciosamente um intercâmbio PanelNest que já funcionava.

## 10. Interface legada

- `Editor 2D` é a aba de produção.
- `2D (Sketch)` foi renomeada para `Sketch legado (diagnóstico)`.
- correções mutantes do Sketch legado permanecem desabilitadas.
- `vector_editor.py` não deve ser reutilizado como fonte de verdade.

## 11. Testes executados na entrega

### Testes puros do editor

```bash
python3 -B -m unittest discover -s tests/vector2d/unit -v
```

Resultado atual: **168 aprovados**.

### Interação Qt offscreen

```bash
env -u DISPLAY -u QT_QPA_PLATFORMTHEME QT_QPA_PLATFORM=offscreen \
  QT_STYLE_OVERRIDE=Fusion PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest discover -s tests/vector2d/qt -v
```

Resultado atual: **34 aprovados**.

### Regressão do WoodCAM existente

```bash
python3 -B -m unittest discover -s tests -p 'test_*.py' -v
```

Resultado: **63 aprovados**.

### Smokes FreeCADCmd

Executados e aprovados:

- `run_import_smoke.py`;
- `run_sketch_import_smoke.py`;
- `run_store_smoke.py`;
- `run_dxf_smoke.py`;
- `run_panelnest_bridge_smoke.py`;
- `run_bitmap_trace_smoke.py`;
- `run_ui_smoke.py`.

O smoke da UI valida criação/salvamento, WorkArea sem vetor, Undo/Redo global,
refresh de painéis, troca A/B inclusive antes do tick do timer, edição de
Trabalho pendente entre documentos, locks, diagnóstico clicável e
prévia/aplicação/cancelamento de reparo e organização. O do PanelNest valida
manifesto, ocorrências, internos e reabertura.

### Compilação/imports

- `compileall` com cache em `/tmp`: aprovado;
- varredura de importação dos 50 módulos do pacote: aprovada.

## 12. Roteiro manual final recomendado

1. Reinicie o FreeCAD para descarregar módulos Python antigos.
2. Crie um FCStd e informe uma área pequena na aba `Trabalho`.
3. Desenhe retângulo com dois círculos internos e uma polilinha separada.
4. Verifique um clique/move, duplo clique/nós e clique solto sem movimento.
5. Feche a janela, reabra, salve o FCStd, feche o FreeCAD e reabra o arquivo.
6. Teste Undo/Redo pela barra global do FreeCAD.
7. Importe um Sketch e um DXF/SVG multicamada; desfaça uma vez.
8. Diagnostique, aplique um reparo com prévia e desfaça.
9. Crie peças e organize; confirme que os círculos acompanham o externo.
10. Ative `Usar no CAM`, gere prévia de Corte e altere o vetor para conferir
    marcação desatualizada.
11. Envie ao PanelNest e confira as ocorrências no grupo de intercâmbio.
12. Antes de usinar, simule e valide o G-code com ferramenta afastada.

## 13. Limites conservadores conhecidos

- trim/extend priorizam geometria linear segura;
- offset fechado está limitado ao caso linear/miter validado;
- splice aceita reta, arco, círculo e elipse circular; elipse geral é rejeitada;
- dogbone/T-bone automático aceita somente cantos internos lineares de 90°;
- Bézier cúbica é modelada/importada e pode ser criada pela ferramenta própria
  de quatro pontos (início, controle 1, controle 2 e fim). No modo Nós, os
  dois controles aparecem em azul com guias tracejadas e podem ser arrastados
  com prévia/Snap e um único Undo, preservando a curva exata;
- vetorização colorida por paleta/união de cores e recorte de uma subárea da
  imagem ainda não têm a paridade completa do Aspire; a entrega atual cobre
  máscara preto/branco com ajustes e curvas Potrace;
- cada importação recebe identidade de lote e, quando colide com o desenho,
  é deslocada rigidamente para o lado; lotes distintos não são classificados
  um dentro do outro como relação peça/furo;
- o organizador usa contorno real raster/bitmask e MaxRects como comparador e
  continua automaticamente em chapas virtuais lado a lado, persistidas com o
  mesmo Undo da organização; ainda não oferece espelhamento, nesting dentro de
  furos ou todos os controles especializados do Aspire/PanelNest;
- nenhum software conhece sozinho grampos, folgas reais, pós-processador ou
  cinemática da máquina: a conferência humana do percurso continua obrigatória.

## 14. Regra para continuidade

Qualquer recurso futuro deve manter esta direção:

> a geometria pertence ao documento; a ferramenta propõe; o comando confirma;
> a cena mostra; CAM e PanelNest consomem adapters validados.

Não reintroduzir mutação direta de Sketch, `QGraphicsItem` como banco de dados,
correção automática sem prévia ou fallback silencioso para outra fonte CAM.

## 15. Registro de continuidade — 15/07/2026

### Caso real acompanhado

O fluxo foi validado visualmente com peças provenientes do PanelNest e com o
DXF real `suporte-CAM Chapa 02.01 - Sem material 15 mm [2750x1850].dxf`.
Foram observados quatro problemas de uso:

1. a importação pela árvore não deixava claro que aceitava objetos Shape/CAM;
2. o DXF do PanelNest trazia 96 linhas abertas redesenhadas sobre oito
   contornos fechados, provocando centenas de diagnósticos derivados;
3. importações sucessivas preservavam a mesma origem e ficavam sobrepostas;
4. o organizador trabalhava em uma única chapa, deixando o excedente fora e
   podendo interpretar a contenção acidental entre lotes como peça/furo.

### Soluções entregues

- `Arquivo → Importar itens da árvore…` aceita a seleção resolvida de Sketch,
  face, sólido, compound e objetos com Shape sem modificar a origem;
- a conversão OCC respeita a orientação real das arestas e percorre sólidos de
  compounds, preservando as formas organizadas geradas pelo PanelNest;
- o diagnóstico agrega as 96 sobrelinhas no bloqueio
  `REDUNDANT_OPEN_OVERLINES` e `Reparar → Limpar sobrelinhas/duplicados…`
  apresenta prévia magenta antes de remover somente as cópias comprovadamente
  cobertas por contornos fechados;
- cada nova importação recebe `import_batch_id`; quando colide com geometria
  existente, o lote inteiro é deslocado rigidamente para a direita, sem alterar
  posições relativas nem o arquivo/objeto de origem;
- contornos pertencentes a lotes diferentes não formam relação falsa de
  peça/furo mesmo que um esteja visualmente dentro do outro;
- o organizador repete o empacotamento por contorno real em chapas virtuais
  lado a lado até posicionar tudo que cabe em uma chapa vazia;
- chapas adicionais aparecem em magenta na prévia e em azul após confirmação;
  limites das chapas e transformações são persistidos no mesmo comando atômico
  e saem juntos em Undo/Redo;
- o diagnóstico reconhece os limites persistidos das chapas adicionais.

### Resultado visual confirmado

O usuário confirmou que a organização atual ficou melhor que a obtida pelo
PanelNest no caso testado. A limpeza de sobrelinhas também produziu os oito
contornos fechados esperados sem deformar as peças.

### Verificação automatizada

- 168 testes puros do Editor 2D aprovados;
- 34 testes Qt offscreen aprovados;
- 63 testes legados do WoodCAM aprovados;
- sete smokes FreeCADCmd aprovados;
- compilação integral e importação dos 50 módulos aprovadas.

### Ponto de retomada manual

1. Reiniciar o FreeCAD para descarregar os módulos Python anteriores.
2. Apagar somente as cópias antigas do Editor e reimportar os lotes, pois
   vetores importados antes desta entrega não possuem `import_batch_id`.
3. Importar duas ou mais seleções da árvore e confirmar que cada lote aparece
   ao lado do anterior, sem sobreposição.
4. Executar `Reconhecer peças e furos` e depois `Organizar peças`, conferindo
   prévia, chapas adicionais, furos unidos ao externo e Undo/Redo único.
5. Salvar, fechar e reabrir o FCStd para conferir a persistência das páginas.

Foi levantada informalmente a possibilidade de uma exclusão no Editor também
apagar algo da árvore. O comportamento não foi confirmado e nenhuma sincronização
destrutiva foi implementada. A regra permanece: excluir uma cópia importada no
Editor pode remover sua relação `Piece2D`, mas nunca deve apagar automaticamente
o Sketch, Shape, objeto PanelNest ou outro objeto de origem na árvore do FreeCAD.

## 16. Registro de continuidade — relevo 3D por imagem

Foi entregue o primeiro fluxo vertical de relevo por mapa de altura, separado
do documento vetorial:

- `Arquivo → Criar relevo 3D por imagem…` abre controles não modais para
  largura/altura, posição X/Y, altura máxima, base, inversão, níveis, contraste,
  gamma, suavização e resolução;
- a janela mostra imagem original, mapa de altura e relevo sombreado;
- a vista 3D recebe uma malha Coin temporária, sem criar objeto FreeCAD durante
  os ajustes e sem tocar no `VectorDocument`;
- confirmar cria uma malha fechada `Mesh::Feature` no grupo
  `WoodCAM 3D — Relevos`, dentro de uma única transação com Undo/Redo;
- o FCStd preserva o mapa processado em PNG/base64, SHA-256 da fonte,
  parâmetros determinísticos, dimensões e resolução da malha;
- a imagem de origem nunca é modificada e nenhuma conexão com IA/internet é
  necessária para o método local; mapas de profundidade externos entram pelo
  mesmo fluxo;
- percursos próprios de desbaste e acabamento 3D não fazem parte deste marco e
  não foram misturados prematuramente com Corte/Preenchimento 2D.

Testes acrescentados: processamento/meshing puro, diálogo Qt offscreen e smoke
FreeCADCmd com criação, save/reopen e Undo/Redo.

Resultado automatizado após o ajuste visual: **176 testes puros**, **37 testes Qt**,
**63 testes legados** e **oito smokes FreeCADCmd** aprovados. O smoke novo também
insere/remove o overlay Coin transitório antes de validar persistência e
Undo/Redo.

### Ajuste após o primeiro aceite visual

O caso real do peixe revelou que o mapeamento literal de luminosidade fazia o
fundo branco virar o topo e o peixe escuro parecer uma cavidade. O diálogo agora
amostra os quatro cantos e, por padrão, coloca automaticamente o fundo na base:
fundo claro ativa inversão; fundo escuro mantém a orientação. O usuário pode
alterar `Inverter relevo — trocar cima/baixo` a qualquer momento; a detecção
automática apenas define seu estado inicial. Após confirmar, a malha usa modo sombreado e a
seleção é limpa para não esconder o relevo sob a triangulação azul.

### Ajuste após comparação direta com o componente do Aspire

O segundo aceite mostrou que inverter a foto corrigia o sentido, mas ainda
usava cada escama/sombra como altura absoluta e produzia uma superfície
serrilhada, sem o volume geral do peixe. O fluxo passou a oferecer dois modos:

- `Volume suave (foto)`, padrão: separa o fundo, calcula um campo de distância
  até a silhueta, cria um perfil arredondado e mistura de volta uma porcentagem
  configurável dos detalhes da foto;
- `Mapa de altura por luminosidade`: preserva o contrato literal anterior para
  mapas de profundidade já preparados externamente.

O perfil fotográfico expõe detalhe, separação de fundo e arredondamento. Os
padrões foram recalibrados para 6 mm de forma, 28% de detalhe, suavização 1,5 px
e malha visual de 192 pontos. A prévia Coin usa crease angle para iluminação
suave e a resolução viva subiu para 144 pontos.

No Wayland/Niri, tentar mover uma janela top-level pelo aplicativo é bloqueado
pelo compositor. O diálogo de relevo passou a usar exatamente a estratégia da
janela principal do WoodCAM: `Qt.Widget` filho direto da janela do FreeCAD e
arraste por delta global iniciado em qualquer área livre. Campos, botões,
combos e listas bloqueiam o gesto e mantêm sua interação normal; a faixa azul
experimental foi removida.

### Correção após comparação com a conversão direta documentada do Aspire

A comparação com um PNG transparente de leão revelou dois problemas distintos.
Primeiro, o canal alfa era achatado sobre branco; a área transparente virava
altura máxima e produzia a grande rampa retangular ao redor do animal. O
processamento agora mantém RGB e alfa separados, usa a opacidade como máscara
dos níveis e força todo pixel totalmente transparente para a base, inclusive
depois de inverter ou suavizar.

Segundo, o perfil arredondado por silhueta havia sido apresentado incorretamente
como equivalente ao Aspire. A documentação do Aspire descreve a criação direta
do componente a partir dos tons do bitmap. O diálogo agora usa por padrão
`Aspire/Emboss — componente multiescala`; `Volume suave experimental` permanece
como ferramenta opcional do WoodCAM e `Mapa de profundidade pronto` mantém o
contrato literal para arquivos externos. A malha padrão passou de 192 para 320
pontos no maior eixo e a prévia viva de 144 para até 256 pontos, reduzindo a
perda visual de pelos, olhos e escamas.

Validação após a correção: **178 testes puros**, **38 testes Qt**, **63 testes
legados** e **oito smokes FreeCADCmd** aprovados. O teste de regressão novo cobre
PNG transparente normal e invertido, com suavização, exigindo cantos em altura
zero.

### Refinamento de baixo-relevo após o segundo aceite visual

Fotos reais ainda aplicavam toda a faixa de 6 mm a pelos e escamas, gerando
picos triangulados apesar de o mapa estar corretamente orientado. O componente
padrão agora decompõe a imagem em forma de baixa frequência e microtextura:
`Suavização do corpo` controla o volume amplo e `Altura dos detalhes` devolve
somente uma fração da textura. Os padrões passaram para 3 mm de altura, 5 px de
forma e 45% de detalhe.

A separação de fundo também deixou de zerar todo pixel escuro. Um flood-fill
parte das bordas e remove somente o fundo conectado ao contorno da imagem;
olhos, escamas e pelos escuros fechados permanecem no componente. O limiar
padrão subiu para 20 para absorver ruído de compressão JPEG. Dois testes puros
novos validam compressão da microtextura e preservação de detalhes internos.

O passe seguinte substituiu a mistura de apenas duas escalas por três bandas:
corpo, estrutura média e microdetalhe. `Definição da forma` preserva olhos,
focinho e barbatanas independentemente da altura de pelos/escamas. A máscara
de contorno ganhou distância interna de 3 px para o componente chegar à base
progressivamente, sem halo externo nem parede serrilhada. A malha padrão e a
prévia subiram para 384 pontos no maior eixo; crease angle passou para 80°.

As capturas seguintes mostraram que o mapa sombreado do diálogo estava suave,
mas a vista Coin/FreeCAD permanecia metálica e estourada. A prévia 3D passou a
usar normais explícitas por vértice somente no topo, com fundo/laterais planos,
material de madeira fosca, baixa componente especular e transparência zero. O
objeto persistente recebe o mesmo material, iluminação em duas faces e, quando
suportado pelo ViewProvider instalado, realce de seleção por caixa em vez de
pintar toda a superfície de branco.

Com a apresentação estabilizada, o aceite seguinte evidenciou a limitação
geométrica restante: o retrato estava legível, porém parecia gravação plana. O
componente multiescala ganhou `Volume escultórico` (35% por padrão), calculado
pela distância interna à silhueta. Esse domo fornece massa macro ao rosto/corpo;
as bandas de estrutura e detalhe são recolocadas depois, independentemente. O
fundo continua em zero e imagens sem fundo separável não recebem domo forçado.
Um teste puro novo exige centro elevado, borda progressiva e fundo intacto.

Ao elevar manualmente o componente de 3 para 10 mm, a textura voltou a dominar:
45% passava a representar até 4,5 mm somente em pelos. O padrão agora habilita
`Altura inteligente`. Acima de 3 mm, microdetalhe é reduzido inversamente à
altura, estrutura pela raiz da razão, suavização/corpo/borda crescem pela mesma
raiz e o volume escultórico recebe a maior parte da altura adicional. Assim, o
relevo pode ficar mais alto sem converter textura em montanhas. O teste novo
compara diretamente a amplitude física em milímetros para 3 e 10 mm.

### Calibração com o bitmap da demonstração do Aspire

O mesmo peixe de 812 × 436 px usado na comparação visual expôs que o perfil
suave de retrato conservava a forma, mas apagava definição de escamas e
nadadeiras. A janela agora abre no perfil `Detalhado — escamas, penas e
objetos`: 4 mm de relevo, 0,25 px de suavização final, 65% de microdetalhe,
3 px de corpo, 95% de definição, borda interna de 1,5 px, 50% de volume
escultórico e malha de 512 pontos. O perfil `Retrato com lissage — rostos e
pelos` usa 1,5 px de acabamento, 32% de textura, 6 px de continuidade do corpo,
82% de estrutura e 45% de volume; editar qualquer campo marca o conjunto como
`Personalizado`.

A prévia transitória fica limitada a 448 pontos para manter resposta interativa,
mas `Criar relevo` grava a malha de 512 pontos escolhida. O novo teste Qt cobre
os dois perfis e a transição automática para o estado personalizado.

### Lissage preservando componente e diagnóstico da fonte

A comparação quadro a quadro revelou a operação posterior `Lisser components`
do Aspire. O antigo blur final misturava a base zero com a forma e podia
afundar a borda. O acabamento agora usa convolução normalizada pela máscara:
suaviza somente as alturas pertencentes ao componente e reaplica uma única vez
o contorno/transparência original.

O leão horizontal usado no WoodCAM mede 815 × 451 px e contém céu, vegetação e
parte do corpo; o retrato mostrado no Aspire é outro enquadramento, com fundo
isolado. Uma foto com cenário permanece quase toda diferente de zero (96,98%
nesse caso), portanto o cenário necessariamente vira geometria. A janela agora
classifica a fonte como PNG transparente, fundo uniforme ou foto com cenário e
mostra a recomendação antes da confirmação. Os controles foram renomeados por
resultado (`Textura fina`, `Continuidade do corpo`, `Olhos/focinho/nadadeiras`,
`Entrada do contorno`, `Volume do corpo` e `Lissage final`).

Validação desta rodada: **179 testes puros**, **40 testes Qt**, **63 testes
legados** e **oito smokes FreeCADCmd**.

### Reorganização visual do diálogo de relevo

O formulário único ultrapassava a altura disponível e o Qt sobrepunha rótulos,
campos e mensagens. A área de prévias foi compactada e o formulário central foi
dividido em `Essencial` e `Avançado`. O primeiro mantém somente lissage, textura,
continuidade, olhos/contornos, volume e compensação de altura; o segundo recebe
método, orientação, inversão, níveis, contraste, gamma, borda e fundo, dentro de
uma área com rolagem vertical própria. O rodapé agora usa uma única linha e as
três colunas têm larguras equilibradas. Um teste Qt em 1050 × 690 garante que os
controles essenciais não se sobreponham, o rodapé permaneça dentro da janela e
as abas exponham somente seu respectivo conjunto de controles.

Na primeira revisão, o filtro global de arraste ainda capturava cliques no
`QTabBar`, impedindo abrir `Avançado`. A barra de abas passou a integrar a lista
de controles que nunca iniciam arraste, e o teste Qt agora troca as abas com um
clique real. `Inverter relevo (alto ↔ baixo)` retornou ao painel `Essencial` e
permanece sempre visível; não depende da abertura das opções avançadas.

### Representação leve de relevo e percursos 3D

O mapa de altura preservado e a lista integral de movimentos passaram a ser as
fontes de precisão do CAM; a vista 3D é somente uma projeção desacoplada. O
relevo persistente é um `App::FeaturePython` leve, reconstruído do PNG salvo,
sem gravar uma segunda `Mesh::Feature` densa no FCStd. A resolução visual fica
limitada a 192 pontos no maior eixo e não altera a amostragem usada pelo CAM.

Na projeção Coin3D, a base plana deixou de repetir um vértice para cada ponto do
topo: ela usa apenas o perímetro e dois triângulos inferiores. Em uma malha
visual 192 × 103, isso reduz 39.552 para 20.362 vértices e 79.100 para 40.138
faces. O picking do relevo usa somente a caixa envolvente e overlays
temporários não participam do hit-test.

Percursos permanecem completos em `MovesCompressedBase64` para estimativa,
simulação e G-code. Há duas representações deliberadamente distintas: durante
`Pré-visualizar`, todos os movimentos 3D são enviados diretamente a um overlay
Coin3D transitório e segmentos consecutivos são coalescidos em polilinhas,
sem criar objetos nem propriedades no documento. Ao aplicar uma operação 3D,
nenhuma segunda projeção do percurso é gravada: a árvore conserva configurações,
seleção, contagem e movimentos comprimidos, e o botão `Pré-visualizar` reconstrói
a visualização completa sob demanda. Operações 2D continuam com sua projeção
persistente de baixa cardinalidade. Projeções 3D criadas por versões anteriores
são ocultadas automaticamente, sem apagar seus dados. Imediatamente após
`Aplicar`, o mesmo percurso completo permanece visível no overlay transitório;
ele dá retorno visual sem passar a fazer parte do FCStd.

A prévia 3D mantém explicitamente o relevo/STL selecionado e toda a cadeia de
grupos pais visíveis sob o percurso. Antes, a rotina genérica que escondia
vetores 2D substituídos também ocultava por engano a superfície 3D, deixando
apenas uma grade azul sem leitura da imagem. O realce caro da seleção é removido
sem esconder a fonte; rápidos e rampas ficam quase transparentes, enquanto as
linhas de desbaste/acabamento usam azul escuro translúcido para conservar o
relevo sombreado como referência principal.

O enquadramento automático foi desativado somente para prévias 3D. `fitAll`
incluía toda a mesa, planos de datum e movimentos seguros, podendo reduzir um
relevo de 200 mm a um pequeno retângulo quando a área de trabalho media metros.
A câmera escolhida pelo usuário agora é preservada em `Pré-visualizar` e
`Aplicar` 3D.

Validação após essa separação: **179 testes puros**, **40 testes Qt**, **75
testes legados** e **nove smokes FreeCADCmd** aprovados.

### Ligação contínua das passadas de acabamento 3D

O acabamento raster recolhia ao Z seguro e mergulhava novamente em cada linha.
Além do tempo perdido, visualizadores de G-code exibiam duas paredes de
movimentos verticais nas bordas do relevo. O raster agora alterna o sentido e
liga passadas vizinhas acompanhando amostras da superfície já compensada pela
ferramenta. A ligação só é aceita quando todo o trecho permanece dentro da
fronteira e possui altura válida; vazios, furos, recortes e regiões distantes
continuam obrigando recolhimento seguro.

Os testes cobrem os dois contratos: uma superfície retangular contínua possui
apenas posicionamento inicial e recolhimento final, enquanto uma superfície
separada por uma faixa sem dados mantém múltiplos recolhimentos. Validação:
**179 testes puros do Editor**, **40 testes Qt**, **77 testes legados** e o
smoke FreeCADCmd de malha → desbaste → acabamento → G-code aprovados.

### Contrato visual exato entre percurso aplicado e G-code

A trajetória Coin3D recebe todos os movimentos calculados, sem reduzir o
stepover nem trocar a ferramenta para fins visuais. Ao aplicar, a mesma lista é
compactada sem perda em `MovesCompressedBase64`; a aba `Simulação e Salvar`
decodifica essa lista tanto para reconstruir a vista quanto para escrever o
G-code. O teste de integração compara agora a lista inteira antes e depois da
persistência, e não apenas sua quantidade.

Para impedir uma interpretação perigosa, qualquer mudança de fresa, diâmetro,
stepover, estratégia, fronteira, material ou posição do modelo remove
imediatamente o overlay anterior e marca a vista como `Percurso desatualizado`.
Depois de `Pré-visualizar` ou `Aplicar`, uma faixa verde informa operação,
ferramenta, diâmetro, stepover percentual, distância física entre linhas e
quantidade exata de movimentos. Ao selecionar uma única operação 3D aplicada,
a faixa mostra `APLICADO = G-CODE` e a vista é reconstruída diretamente dos
movimentos persistidos que serão exportados. Seleções múltiplas não são
sobrepostas como se formassem um percurso único.

Na exportação direta ou de uma operação aplicada, o overlay também é atualizado
com o próprio objeto `moves` entregue a `build_gcode`, sem um segundo cálculo.
A faixa então mostra `G-CODE GERADO = VISTA`; os pontos visuais e o arquivo NC
usam o mesmo arredondamento de quatro casas decimais.

A animação 3D antes desenhava um rastro ciano ligando quadros reduzidos para
manter o limite de FPS. Em trajetórias grandes, essa ligação criava diagonais
visuais que não pertenciam aos movimentos nem ao G-code. Agora o percurso
exibido durante a simulação é o overlay integral reconstruído da lista real,
marcado como `SIMULAÇÃO EXATA = G-CODE`.

Para trajetórias longas, quatro vetores numéricos compactos guardam o tempo e
as coordenadas X/Y/Z de cada destino real do G-code. O relógio localiza por
busca binária o segmento real que envolve o instante atual e interpola somente
dentro dele a cada tick de um temporizador preciso. Isso preserva inclusive
pequenas subidas e descidas de Z sem criar centenas de milhares de objetos no
documento. O tipo geométrico mostrado continua vindo da fresa selecionada
(topo reto, esférica, V-bit ou broca pontiaguda).

A ferramenta de simulação passou a separar haste, corpo de corte e ponta,
mantendo a extremidade de contato exatamente em Z=0 local. Arestas helicoidais
leves deixam o giro visível, enquanto um `SoTransform` do Coin3D atualiza
translação e rotação diretamente na cena, sem recomputar o FCStd por quadro.
A rotação apresentada é desacelerada e proporcional ao RPM para permanecer
legível na taxa de quadros da interface; ela não altera tempo nem G-code.

### Integração opcional de profundidade por IA — CPU

Após a prova isolada com peixe e leão, Depth Anything V2 Small foi integrado
como gerador opcional de prévia. O modelo preservou melhor a anatomia que o DA3
Small nas imagens avaliadas. O contrato de segurança é explícito:

- PyTorch e o código do modelo vivem em runtime Python externo; o processo do
  FreeCAD não os importa;
- `WOODCAM_AI_DEVICE=CPU` é imposto pela fronteira e o worker rejeita outro
  dispositivo. A GPU Intel Arc B570 não é usada;
- o botão apenas gera PNG temporário e atualiza as três prévias; cancelar ou
  trocar de método descarta esse mapa sem mutar documento ou imagem;
- o refinamento possui perfis equilibrado, escultórico e detalhado. A etapa
  combina profundidade DA2, suavização bilateral que preserva bordas, volume
  de baixa frequência derivado da silhueta e microtextura limitada;
- quando existe alfa real, ele delimita o objeto antes da regularização. Nas
  demais imagens permanece o fallback de fundo uniforme ou componente de
  profundidade dominante;
- fundos de “transparência” gravados como xadrez cinza/branco em JPEG são
  reconhecidos por dois agrupamentos neutros conectados à borda. Isso removeu
  o piso quadriculado sob as patas no caso real do porquinho;
- ao confirmar, o mapa entra pelo adaptador já existente e registra
  `GenerationMethod=ai_depth` e metadados do gerador no FCStd;
- os pesos oficiais têm SHA-256
  `715fade13be8f229f8a70cc02066f656f2423a59effd0579197bbf57860e1378` e
  99.218.434 bytes. O instalador verifica o hash e não sobrescreve runtime já
  existente;
- a resolução de inferência inicial fica limitada a 504 px, ponto validado em
  aproximadamente 1,0 s no CPU desta máquina; a resolução preservada do mapa e
  a malha visual continuam sendo controles separados.

O runtime pode ser instalado por `bash install_relief_ai.sh`. Nesta máquina ele
ocupa aproximadamente 1,4 GB incluindo Python, Torch CPU, OpenCV e pesos; isso
não deve ser confundido com os 99,2 MB do modelo.

Validação desta integração: **183 testes puros do Editor**, **42 testes Qt**,
**77 testes legados**, **nove smokes FreeCADCmd**, compilação integral e
importação de **67 módulos leves** aprovados. O worker persistente também foi
executado nas imagens reais do peixe e do leão exclusivamente em CPU; em 504 px
a inferência ficou em aproximadamente 1,0–1,1 s. Os três perfis também foram
comparados no porquinho-da-índia real, e o caminho de alfa foi exercitado em
PNG transparente.

A auditoria visual posterior mostrou que `Mapa preservado: 1024 px` era apenas
o limite de entrada, enquanto o mapa real do caso tinha 554 × 554 e a malha
Coin estava limitada a 192 pontos/lado. A interface agora chama o primeiro
controle de `Limite do mapa`, mostra a dimensão efetiva no rodapé e usa 320
pontos/lado por padrão, com máximo explícito de 384. Em 320 a construção pura
da malha levou aproximadamente 0,10 s nesta máquina. O material Coin ganhou
reflexo semibrilho e luz lateral, tanto na prévia quanto no objeto reaberto,
para recuperar a leitura “plastificada” de inclinações suaves sem mudar mapa,
geometria CAM ou G-code.

Na comparação seguinte com uma demonstração em Blender, a captura do FreeCAD
estava simultaneamente no perfil `Detalhado` e com o objeto persistente
selecionado. O primeiro devolvia pelos como ranhuras, adequado a gravação com
ferramenta fina; o realce laranja da seleção substituía o material semibrilho.
`Escultórico HD` passou a ser o primeiro perfil para animais, rostos e
medalhões, com mais volume de baixa frequência. A captura seguinte
demonstrou que o laranja ainda visível era o próprio material dourado, não a
seleção; ele foi substituído por clay cinza neutro semibrilho e luz quase
branca para separar claramente avaliação de forma e estado de seleção.

Todos os controles numéricos aplicáveis agora combinam slider horizontal com
valor exato editável sem setas. Dimensões e posições mantêm a faixa numérica
ampla, enquanto o slider cobre a faixa prática; altura, base, forma, detalhe,
estrutura, borda, volume, níveis e resoluções são ajustáveis por arraste. O
`mouseMove` desses controles continua alterando somente a prévia temporária.

### Pacto profissional do relevo — segunda IA de superfície

A segunda etapa foi aceita somente após prova isolada nas imagens reais da
mulher, porquinho-da-índia, peixe e leão. O modelo escolhido foi Metric3D v2
Small ONNX FP16: 75.778.144 bytes, saídas distintas de profundidade, normal XYZ
e confiança, e inferência OpenVINO exclusivamente em CPU. Nesta máquina, a
inferência de normais levou aproximadamente 0,19–0,57 s após a compilação do
modelo. DSINE não entrou porque a licença do repositório limita o uso a
pesquisa não comercial; StableNormal não entrou no caminho principal porque a
arquitetura de difusão é desproporcional para a meta local em CPU.

O WoodCAM não usa a normal como simples textura de visualização. O worker
converte as normais em gradientes, pondera a confiança e resolve uma integração
Poisson com âncora na forma DA2. Somente a correção de média frequência entra:
a DA2 preserva silhueta e volume baixo, enquanto Metric3D acrescenta inclinação
geométrica de nariz, boca, bochecha, orelha, pata e cabelo. O perfil
`Escultórico HD` limita essa contribuição a 11%; `Equilibrado`, 16%; e
`Gravação detalhada`, 20%. A máscara alfa/xadrez continua delimitando o objeto.

A inspeção do primeiro retrato Pro ainda revelou pele e cabelo com aparência de
gravação. O `Escultórico HD` foi então recalibrado para 44% de volume anatômico,
24% de residual DA2 e somente 10% da microtextura fotográfica; o perfil
`Gravação detalhada` permanece disponível quando esses riscos forem desejados.
Uma suavização final de 0,70 px exclusiva desse perfil remove granulação de pele
depois da integração geométrica, preservando a máscara e sem afetar os perfis
Equilibrado/Detalhado.
O botão de limpar seleção foi removido porque o relevo leve não é selecionável
pela malha e o controle não tinha efeito útil para o operador.

O recurso permanece preview-first, Undo/Redo e processo externo. Ausência do
modelo ONNX produz fallback transparente para DA2; FreeCAD nunca importa Torch,
OpenVINO, OpenCV ou NumPy. O instalador agora verifica SHA-256 dos dois pesos e
grava a configuração híbrida. O runtime completo ocupa aproximadamente 1,7 GB.
O código oficial declara BSD-2-Clause e o artefato ONNX declara CC0-1.0; como o
repositório oficial também pede contato para consultas comerciais, os pesos não
devem ser redistribuídos em produto pago sem confirmação escrita dos autores.

Validação desta etapa: **184 testes puros aprovados e dois testes numéricos
ignorados no Python do FreeCAD por ausência intencional de OpenCV**; os dois
testes de fusão passaram no runtime isolado da IA; **42 testes Qt**, **77 testes
legados**, **nove smokes FreeCADCmd**, compilação integral e importação de **67
módulos leves** aprovados. O smoke do relevo também impede regressão para
material saturado laranja.

### Coordenadas de exibição do CAM 3D — 17 de julho de 2026

A prévia 3D passou a separar explicitamente coordenadas de máquina e de
exibição. Os movimentos persistidos e o G-code continuam usando o `Z-zero`
escolhido pelo operador; uma cópia transitória recebe somente na vista a
translação necessária para coincidir com a malha fonte. Isso elimina o caso em
que, com zero na superfície do material, o percurso aparecia por baixo do
relevo apesar de o G-code estar numericamente correto.

O alinhamento visual considera a espessura do material, a altura e o Z real da
fonte, `Folga acima`/`Folga abaixo` e os modos `Superfície do material`/`Mesa da
máquina`. A mesma conversão alimenta a prévia integral, a operação aplicada e a
posição animada da fresa, sem alterar a fonte, o FCStd ou a lista entregue ao
escritor de G-code. Operações 2D preservam o contrato anterior.

Validação: **186 testes puros do Editor** (dois ignorados pela ausência
intencional de OpenCV no Python do FreeCAD), **42 testes Qt**, **83 testes
legados**, **nove smokes FreeCADCmd** e compilação integral aprovados. Seis
regressões puras cobrem ambos os zeros Z, folga inferior, fonte deslocada,
fonte cujo topo é Z=0 e a preservação das operações 2D.

### Nesting multiestratégia e assistente CAM — 17 de julho de 2026

O organizador preserva a arquitetura preview-first e passou a expor três
orçamentos determinísticos de busca: `rápido`, `equilibrado` e `profundo`. As
quatro heurísticas históricas permanecem como primeiros candidatos; os perfis
maiores acrescentam permutações estáveis, independentes do processo e da ordem
de entrada. Cada chapa ainda compara MaxRects com a máscara raster do contorno
real, mantém contorno externo e descendentes como unidade e confirma toda a
organização em um único `CompositeCommand`. O resultado agora informa
estratégia vencedora, layouts avaliados, área das peças, área de chapas e
eficiência percentual. Nenhum perfil usa relógio ou aleatoriedade, portanto a
mesma entrada produz a mesma prévia em computadores diferentes.

O botão `Assistente CAM` executa uma auditoria pura e explicável dos parâmetros
da operação. Ele observa compatibilidade entre ferramenta e operação,
stepdown, stepover 3D/preenchimento, margens de Z, profundidade de corte e,
quando disponível, regiões menores que a fresa. Cada item apresenta causa e
valor conservador para revisão. A análise não escreve campos, não altera
geometria, não autoriza G-code e não substitui `validate_settings`, prévia,
simulação ou conferência física; os bloqueios duros continuam pertencendo aos
validadores existentes. Pré-visualizar e Aplicar atualizam apenas o indicador
do assistente.

O relatório detalhado não reutiliza silenciosamente `last_operation_mode`: ele
somente abre quando Corte, Furo, Preenchimento, Desbaste 3D ou Acabamento 3D é
a aba realmente visível. O cabeçalho identifica operação, nome, tipo e diâmetro
da fresa analisada. Nas demais abas, o operador recebe orientação para abrir a
operação desejada, eliminando ambiguidade entre corte, furo e acabamento.

Validação desta entrega: **188 testes puros do Editor** (dois ignorados pelo
runtime opcional sem OpenCV), **42 testes Qt**, **87 testes gerais** e **nove
smokes FreeCADCmd** aprovados. A cobertura nova verifica determinismo,
rejeição de perfil inválido, métricas do nesting, ausência de mutação pelo
assistente, recomendações CAM, os três comandos visíveis no menu Peças e o
botão do assistente no smoke real do FreeCAD.

### Barra de abas compacta — 17 de julho de 2026

Somente `Trabalho` e `Material` conservam texto na barra principal. Corte,
Furo, Preenchimento, Desbaste 3D, Acabamento 3D, Fresas, Editor 2D,
e Simulação/Salvar exibem somente ícone, com o nome integral no tooltip. Uma
`QTabBar` compacta calcula a largura dos botões sem texto pelo tamanho real do
ícone e pela margem mínima de foco/borda exigida pelo estilo Qt, eliminando a
reserva excessiva sem permitir que a arte invada a aba vizinha. Como o Qt ainda
reservava internamente uma área de rótulo invisível, as abas sem texto agora
desenham a moldura pelo estilo e o ícone separadamente no centro geométrico do
botão; assim as margens esquerda e direita ficam visualmente iguais. O smoke Qt
confere esse centro com tolerância de um pixel e também inspeciona o framebuffer
para garantir que cada desenho foi realmente pintado. O ícone é obtido
diretamente da `QTabBar`, contornando a perda observada no `QStyleOptionTab` do
FreeCAD/PySide quando o texto visível está vazio. O título lógico fica
armazenado na própria página e continua
alimentando seleção de operação, persistência da aba, Assistente CAM e edição
de percursos; nenhuma regra depende do texto visual vazio. Quando o acervo não
possui uma imagem final, o Qt fornece um ícone nativo temporário.

A aba `Sketch legado (diagnóstico)` foi retirada da interface de produção. O
código de diagnóstico permanece preservado e isolado, sem ganhar mutações ou
alterar Sketches/Shapes/FCStd de origem; o Editor 2D continua sendo a única
superfície vetorial visível.

Os ícones definitivos de Corte, Preenchimento, Furo, Desbaste 3D e Acabamento
3D foram extraídos da folha `resources/diagrams/abas.png` fornecida para o
produto. Cada recorte foi preservado, recebeu fundo alfa transparente, margem
e canvas uniforme de 128 × 128 px. A associação segue a linguagem de operações
do Aspire: perfil, cavidade, matriz de furos, desbaste em níveis e superfície
lisa. Trabalho e Material não foram modificados; Fresas, Editor 2D e
Simulação/Salvar mantêm seus ícones próprios por não terem equivalentes diretos
na folha.

### XY absoluto do CAM 3D e árvore única — 26 de julho de 2026

O datum da área de Trabalho passou a ter um contrato único para 2D e 3D: ele
define o ponto inicial/retorno da máquina, mas não reposiciona o modelo nem o
percurso de corte. A superfície 3D e seus movimentos permanecem no XY absoluto
do documento. Foi removida a cópia vetorial deslocada que confundia área,
fonte e trajetória na prévia.

O adaptador FreeCAD distinguia incorretamente a tesselação de `Part::Feature`
da topologia de `Mesh::Feature`. `TopoShape.tessellate()` já inclui o
`Placement`; aplicá-lo novamente duplicava o deslocamento XY — uma peça em
X=240 produzia percurso em X=480. Shapes agora usam diretamente os pontos
tesselados, enquanto malhas continuam recebendo seu `Placement` uma única vez.
O smoke real cobre tanto malha com coordenadas absolutas quanto Shape local
deslocada por `Placement`, além do rápido inicial partindo do datum configurado.

O cálculo desse rápido já estava correto, porém a etapa final da prévia 3D
reconstruía cada ponto somente como `(X, Y)` e descartava `Z`. Como o overlay
Coin3D exige pontos `(X, Y, Z)`, ele ignorava silenciosamente toda a trajetória:
o Editor 2D exibia a ligação magenta e a vista 3D não mostrava nada. A projeção
agora preserva todas as coordenadas e destaca somente a ligação inicial
datum → primeira peça com uma linha magenta tracejada. O smoke de XY passou a
inspecionar também o buffer efetivamente entregue ao scene graph, evitando que
um cálculo correto esconda novamente uma falha de renderização.

O fluxo completo `Editor 2D → PanelNest → Corte` revelou ainda uma segunda
transformação independente: a peça materializada pela ponte já preservava o
XY final do Editor, mas, ao ser lida novamente como uma seleção genérica do
FreeCAD, recebia outra ancoragem no datum e o corte era trazido para o zero.
Objetos `panel_part`, a pasta de intercâmbio e `layout_cam_compound` agora são
reconhecidos como fontes em coordenadas finais. A pasta resolve somente seus
filhos CAM, e prévia, aplicação e G-code recebem deslocamento XY nulo. O smoke
da interface reproduz esse caminho com a peça em X/Y deslocados e compara o
envelope integral dos movimentos de corte.

A árvore do FCStd também foi consolidada em uma única raiz:

```text
WoodCAM
├── Peças
├── Operações
└── Área de trabalho
```

Os grupos antigos de desenho e intercâmbio PanelNest são migrados preservando
filhos, Shapes, IDs e manifesto. O objeto `VectorDocument` continua sendo a
fonte de verdade, porém fica oculto na árvore como infraestrutura interna. A
migração muda somente a organização visual; não altera Sketch, Shape nem o
arquivo de origem importado.

Validação desta entrega: **228 testes puros do Editor** (dois ignorados pelo
runtime opcional sem OpenCV), **51 testes Qt**, **87 testes gerais**, compilação
dos módulos alterados e smokes FreeCADCmd específicos de XY 3D, árvore,
PanelNest e CAM 3D aprovados.

### Datum sem translação, janela destacável e simulação leve — 26 de julho de 2026

A exceção que ainda reancorava Sketches, faces e Parts comuns foi removida. O
contrato agora não depende da origem da seleção: todos os adapters entregam
coordenadas de documento; `_xy_origin_offset` permanece nulo; e o datum da área
de Trabalho entra apenas no primeiro movimento e no retorno opcional. O smoke
da interface cobre separadamente Editor 2D, peça materializada pelo PanelNest,
seleção FreeCAD comum e Preenchimento/Rebaixo deslocado, conferindo o envelope
XY integral da usinagem.

Aplicar Trabalho não consulta mais o leitor de superfícies 3D. `Aplicar` grava
diretamente um único limite e um único datum na pasta canônica `Área de
trabalho`; `Pré-visualizar` usa o grupo temporário claramente chamado `Prévia
da configuração`. A simulação passou para `WoodCAM → Operações → Simulação`, e
documentos antigos com o grupo solto são reparentados sem alterar seus objetos.

O Editor 2D ganhou um botão no canto direito da barra de abas que move a mesma
instância para uma janela nativa não modal. Ao fechar, o widget é recolocado na
aba; não há cópia de `VectorDocument`, controller, seleção nem histórico. A
régua vertical aproxima os números da borda esquerda na mesma proporção visual
da régua superior.

Preenchimento/Rebaixo agora usa a linha do tempo compacta e o overlay Coin
integral já empregado pelo Corte. A trajetória é enviada uma vez ao scene
graph e somente a fresa é interpolada; a lista persistida/G-code não é
amostrada nem simplificada.

Validação após estas correções: **228 testes puros** (dois ignorados por OpenCV
opcional), **51 testes Qt**, **87 testes gerais** e **12 smokes FreeCADCmd**
aprovados, incluindo os casos XY 3D, árvore, Sketch/Part comum, PanelNest,
rebaixo e janela destacável.

### Régua compacta, furos tessellados e percursos 2D — 26 de julho de 2026

A régua vertical passou a ter os mesmos 22 px de espessura da régua horizontal.
Número e traço permanecem associados dentro dessa faixa; o canto das duas
réguas também é 22 × 22 px, eliminando o antigo corredor vazio de 38 px.

A ponte Editor → PanelNest agora recupera como furo um círculo pequeno que uma
importação tenha convertido em polilinha. A promoção exige caminho fechado,
pelo menos doze vértices, diâmetro de até 12 mm, caixa quase quadrada e raio
quase constante depois do `placement`. Formas pequenas não circulares continuam
como recortes internos. O smoke real do PanelNest cobre simultaneamente um
`CircleEntity` e um círculo tessellado, ambos vinculados à mesma peça.

O antigo comando exclusivo `Ver corte 2D` virou uma única entrada
`Percursos 2D`. Corte, Furos e Rebaixo reutilizam as configurações e listas de
movimentos de produção já existentes; o Editor recebe somente um overlay
descartável. O menu `CAM` também permite abrir diretamente a configuração de
cada operação, sem acrescentar novos botões à faixa inferior.

Validação desta correção: **229 testes puros** (dois ignorados por OpenCV
opcional), **53 testes Qt**, **87 testes gerais** e **12 smokes FreeCADCmd**
aprovados.

### Roteamento de Furo e reconhecimento parcial seguro — 26 de julho de 2026

O comando de configuração do percurso deixou de procurar a aba pelo texto
visível. Corte, Furo e Rebaixo agora são encaminhados pelo identificador estável
de operação; com isso, `Furos` no menu encontra corretamente a aba `Furo` e a
operação pode ser configurada, aplicada e persistida.

O reconhecimento de `Piece2D` também deixou de tratar um caminho aberto
independente como falha global. Contornos fechados válidos são reconhecidos e
podem seguir ao PanelNest, enquanto o caminho aberto permanece intacto no
`VectorDocument`, visível no diagnóstico e fora de qualquer peça. Interseções,
auto-interseções, ramificações e demais bloqueios geométricos reais continuam
impedindo o envio. Quando o reconhecimento falha, a ponte preserva a causa
detalhada em vez de substituí-la pela mensagem genérica de ausência de peça.

Regressão coberta pelo smoke da interface com a diferença singular/plural da
aba de Furo e com um documento que contém simultaneamente uma peça fechada
válida e um caminho aberto independente. Validação: **229 testes puros** (dois
ignorados por OpenCV opcional), **53 testes Qt**, **87 testes gerais** e **12
smokes FreeCADCmd** aprovados.

### Intercâmbio PanelNest sempre atual e integral — 26 de julho de 2026

O desaparecimento de painéis e furos no caminho `Editor 2D → PanelNest` tinha
duas causas independentes. `Piece2D` é uma fotografia das relações topológicas:
se um painel, cópia ou furo fosse importado/desenhado depois do último
reconhecimento, o envio reutilizava a fotografia antiga. Além disso, qualquer
seleção residual no canvas restringia silenciosamente o intercâmbio somente às
peças relacionadas à seleção, embora o comando se chamasse `Enviar PanelNest`
e não `Enviar seleção`.

Antes de cada envio, o WoodCAM agora reclassifica os vetores atuais por um
comando atômico e então materializa o layout inteiro. A seleção continua sendo
apenas estado de interface: ela nunca elimina peças do intercâmbio. Furos e
recortes internos permanecem descendentes da peça externa, quantidades geram
as ocorrências esperadas e o XY/rotação do Editor continua preservado sem
executar nesting. O status final expõe quantas peças, ocorrências, perfurações e
recortes foram efetivamente produzidos.

O smoke real da interface cria uma peça e um círculo interno depois de já
existir uma classificação, mantém somente esses dois vetores selecionados e
confere no manifesto do FreeCAD que todas as peças do documento foram enviadas,
que o círculo tardio chegou como furo e que nenhum objeto ficou fora da pasta
de intercâmbio. Validação: **229 testes puros** (dois ignorados por OpenCV
opcional), **53 testes Qt**, **87 testes gerais** e **12 smokes FreeCADCmd**
aprovados.
