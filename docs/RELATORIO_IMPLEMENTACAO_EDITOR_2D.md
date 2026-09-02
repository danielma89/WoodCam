# Relatório de implementação — Editor 2D vetorial WoodCAM

**Data:** 12 de julho de 2026  
**Código ativo:** `/home/danielma/Projetos/CNC Marcenaria`  
**Instalação ativa:** `~/.local/share/FreeCAD/Mod/PanelNest` aponta para o
código unificado acima
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
- em `Fechar caminho / unir próximas`, expandir automaticamente a partir de um
  único fragmento para toda a cadeia aberta conectada na mesma camada. Quando
  a cadeia forma um contorno e sobra somente uma folga numérica, as duas pontas
  convergem ao ponto médio em vez de criar uma aresta microscópica. A tolerância
  decide quais pontas podem ser unidas, mas não apaga spans reais curtos;
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

### Linha comum, tabs e busca de nesting — 11 de agosto de 2026

O caminho de linha comum deixou de ser apenas uma opção persistida na interface
e passou a alimentar o CAM. O planejador puro normaliza linhas retas, divide
sobreposições parciais em intervalos atômicos e emite cada intervalo com um ou
dois proprietários. Uma fronteira de dois proprietários entra uma única vez no
trabalho; cruzamento real, área sobreposta, contorno duplicado/autocruzado ou
mais de dois proprietários bloqueiam todo o plano. No modo `Preservar medidas`,
a compensação pelo diâmetro efetivo acontece antes da detecção; no modo
`Vetor = centro da fresa`, a mudança dimensional de meio diâmetro permanece
explícita. Sobre-metal com passada final separada é rejeitado quando existe
linha comum, pois dois desbastes dimensionalmente distintos não cabem no mesmo
kerf.

Furos e recortes internos continuam fechados e são concluídos primeiro. A rede
externa usa polilinhas abertas e entrada vertical, evitando que a limpeza de
uma rampa refaça uma aresta compartilhada. Tabs automáticos são distribuídos
por peça somente nos intervalos de perímetro exclusivo; tabs manuais substituem
a distribuição automática e também são projetados para um intervalo exclusivo.
O limitador de Z agora atua desde o primeiro passe que alcança a altura da
ponte, inclusive em profundidades personalizadas, porque um último passe não
consegue restaurar material removido antes.

Uma inspeção visual posterior revelou que esses intervalos seguros ainda eram
enviados ao CAM como pequenos percursos independentes. Em duas peças retangulares
isso produzia 15 entradas, 15 retrações e 60 mergulhos, apesar de nenhum tab ser
removido em Z. O contrato foi corrigido: intervalos atômicos adjacentes são
reunidos em trilhas edge-disjoint contínuas e cada aresta carrega seu próprio
estado de tab. Para duas peças lado a lado restam três entradas físicas — a
fronteira comum e um perímetro exclusivo por peça — e a ferramenta apenas sobe
e desce Z ao atravessar cada ponte.

A distribuição automática ganhou `Melhor fixação`. Ela amostra regiões retas
afastadas dos vértices, procura três apoios não colineares que envolvam o centro
geométrico e acrescenta um quarto em peças alongadas. A quantidade informada
pelo operador continua sendo um mínimo. Se largura de tab e perímetro exclusivo
não permitirem o padrão geométrico, a prévia é bloqueada e pede ajuste manual;
o sistema não promete compensar fixação física inadequada, avanço excessivo ou
material defeituoso.

O organizador passou ainda a tratar raster/MaxRects somente como geradores de
candidatos. Antes de qualquer solução chegar à prévia, os contornos externos
transformados são reconstruídos em coordenadas exatas e passam pelo mesmo
validador vetorial de cruzamento, sobreposição e propriedade de linhas. Junções
T e encontros de quatro peças em um ponto continuam válidos; cruzamento real
ou trecho pertencente a três peças descarta o candidato e tenta o fallback. Se
ambos falharem, a organização é bloqueada em vez de persistir um layout que o
CAM corretamente recusaria depois.

A validação final também mede a distância vetorial real entre cada par, sem
confiar somente na dilatação raster. O diálogo de nesting passou a compartilhar
o contrato do corte externo ativo: em `Preservar medidas`, a folga mínima é o
diâmetro efetivo da fresa (diâmetro mais duas vezes o sobre-metal); em `Vetor =
centro da fresa`, a recomendação é 0 mm para coincidir as bordas, e qualquer
folga intermediária menor que a fresa é elevada para o diâmetro seguro. Assim,
um layout produzido pelo organizador não é aprovado nos vetores originais para
depois cruzar somente quando o CAM aplica a compensação. Se a fresa for trocada
após o nesting, o bloqueio distingue falta de folga de uma sobreposição real e
orienta executar novamente a organização.

O caso real com painel grande e ripas trapezoidais expôs uma segunda causa: o
offset histórico unia retas deslocadas por miter ilimitado. Em um bico agudo,
essa interseção ultrapassa o raio da fresa e inventa um cruzamento que não
corresponde ao movimento físico da ferramenta. A compensação externa comum e
a usada pelo planejador foram unificadas em junções convexas arredondadas, com
erro máximo de corda de 0,02 mm; cantos côncavos continuam usando a interseção
das retas deslocadas. O nesting valida exatamente essa mesma geometria final.
Durante o smoke do arranjo completo, a rede revelou ainda peças sem nenhuma
fronteira compartilhada no mesmo trabalho. Seus perímetros exclusivos são
naturalmente trilhas fechadas; o adapter CAM agora aceita essas trilhas junto
das linhas comuns abertas, preserva o estado de tab por aresta e não acrescenta
uma aresta de fechamento implícita.

O desenho segue a prática documentada pelo Autodesk Fabrication e Hypertherm
ProNest: margem/kerf coerente, fronteira comum usinada uma vez e redução de
distância e entradas. Para tabs, foram usadas as regras documentadas pelo
Vectric e Autodesk Fusion: material retido no perfil, distribuição longe de
cantos quando automática e rampa de subida/descida no tab 3D.

O organizador de contorno real deixou de manter apenas a primeira posição
viável de cada ordem. `Equilibrado` e `Profundo` usam busca em feixe
determinística com várias posições de fronteira e pontuação por quantidade
colocada, altura, área, largura e contato. A solução gulosa histórica continua
na competição, portanto uma busca maior não pode regredir. Rotações de 180° e
270° passaram a complementar 0°/90° sem violar os eixos de veio, permitindo
que peças trapezoidais assimétricas se enfrentem; rotação livre avalia passos de
15°. O espalhamento XY anterior das peças não participa da decisão.

A execução da busca passou para uma `QThread`, sem mutar o `VectorDocument`.
Uma primeira solução é projetada assim que fica pronta e soluções estritamente
melhores substituem essa mesma prévia enquanto o tempo-alvo configurável segue
contando. A janela exibe etapa, tempo restante, peças, chapas e eficiência
reais; `Parar` conserva a melhor prévia, e somente `Aplicar organização` cria
um `CompositeCommand`. Cancelamento, alteração do documento ou fechamento do
diálogo interrompem cooperativamente rasterização, varredura e busca em feixe.

No modo de linha comum sobre o vetor, a validação final agora recebe os pares
de contornos efetivamente comprovados pelo planejador. Uma folga menor que o
diâmetro efetivo que não seja uma fronteira coincidente é bloqueada, sem
converter silenciosamente corte externo em corte sobre a linha. Quando a
fronteira comum existe, o operador recebe confirmação explícita de que cada
peça perde aproximadamente meio diâmetro da ferramenta nessa borda. O modo de
preservação dimensional continua exigindo a folga correspondente ao kerf.

Foi executado também um ensaio de caixa-preta no serviço SuperNesting com doze
peças trapezoidais/faixas, chapa 300 × 200 mm e espaçamento de 3 mm. O serviço
colocou as doze em uma chapa após busca temporizada e confirmou a importância
de múltiplas posições e rotações. No benchmark determinístico local de cinco
trapézios, a busca equilibrada reduziu o envelope de 120 × 71 mm para
119 × 70 mm sem perder peça.

Validação desta entrega: **264 testes puros** (dois ignorados pela ausência
intencional de OpenCV no Python principal), **59 testes Qt**, **106 testes
gerais**, **12 smokes FreeCADCmd** e compilação integral aprovados. A cobertura
nova verifica aresta compartilhada única, topologias bloqueadas, tabs comuns
sem recorte posterior, profundidade intermediária segura, substituição manual
dos tabs, independência do espalhamento inicial e melhoria estrita do nesting
equilibrado num arranjo trapezoidal apertado. O smoke de interface também cobre
primeira prévia assíncrona, relógio real, interrupção, aplicação atômica e a
confirmação/bloqueio dimensional da linha comum, além da orientação com
coordenada aproximada quando o layout contém uma sobreposição real. Um fluxo
integrado organiza um painel com entalhe, quatro ripas trapezoidais longas,
três segmentos inclinados e três peças de topo com a folga de Ø4 mm; depois
compensa os onze contornos, encontra oito fronteiras comuns e comprova que o
plano não possui rejeição. Separadamente, o smoke da interface gera o percurso
compensado com linha comum e tabs e executa dois bicos agudos pelo fallback de
corte externo sem recriar o miter inválido.

### Velocidade de simulação ao vivo — 12 de agosto de 2026

O multiplicador deixou de fazer parte da construção da linha do tempo. Os
frames 2D e a timeline compacta 3D agora guardam o tempo físico do percurso em
1x; um relógio de reprodução acumula esse tempo de acordo com a velocidade
atual. Ao mudar o multiplicador durante o play, primeiro é contabilizado o
intervalo na velocidade anterior e só então o novo fator é aplicado. A fresa
não reinicia, não salta e não exige reconstrução do percurso.

O campo textual foi substituído por um slider horizontal de 0,1x a 200x com
leitura explícita. A preferência é global e persistida, não um parâmetro de
usinagem da operação. Por isso, simular uma operação aplicada não restaura mais
o multiplicador antigo salvo no `SettingsJSON`: a velocidade escolhida agora
na aba `Simulação e Salvar` sempre prevalece e continua ajustável durante a
execução.

O smoke FreeCADCmd verifica as duas timelines independentes da velocidade,
continuidade matemática na troca de 1x para 10x, precedência do valor atual
sobre uma operação salva e uma simulação real mudando de 1x para 50x enquanto
o timer está ativo.

### Plano global de corte, retenção e TabRelease — 12 de agosto de 2026

Antes desta etapa, `operations.py` gerava os movimentos diretamente por
contorno: `generate_depth_steps` calculava o stepdown, cada job concluía todas
as profundidades do perfil atual, `_tab_ranges` e a opção `Melhor fixação`
dividiam o perfil em mudanças de Z, e a compensação acontecia antes do corte.
O simulador e `gcode_writer.py` já consumiam a mesma lista neutra de movimentos.
O otimizador era nearest-neighbor entre contornos. A linha comum já possuía o
planejador vetorial puro com overlap parcial, split, owner e trilhas
edge-disjoint, mas ainda executava todas as profundidades por trilha e não
possuía identidade física global, dependências, retenção ou liberação.

Foi acrescentado `woodcam_editor/application/global_cut_plan.py`, sem Qt,
FreeCAD ou G-code. Ele recebe linhas-centro já compensadas, usa o detector de
linha comum existente, classifica intervalos físicos como `INTERNAL`,
`EXTERNAL` ou `SHARED`, atribui ID canônico e constrói `GlobalCutPlan`. Cada
`CutOperation` referencia os IDs antes de virar toolpath; a validação rejeita
estruturalmente uma segunda ocorrência de `segment_id + depth`, em vez de
tentar apagar movimentos parecidos depois. Contornos degenerados,
self-intersections, owners ambíguos, internos fora da peça e internos que se
cruzam/tocam também são bloqueados antes do adapter CAM.

As estratégias disponíveis são:

- `per_piece`: padrão compatível; sem linha comum ou TabRelease continua
  chamando integralmente o gerador histórico;
- `global_by_depth`: conclui internos, SharedEdges e externos da chapa em cada
  Z antes de descer;
- `hybrid_stability`: nas camadas que ainda não alcançaram a espessura da chapa
  usa `FAST ROUTE`; ao atingir material passante muda para `STABILITY ROUTE`,
  mantém as precedências e deixa perfis longos/estreitos para depois.

O `FAST ROUTE` não mantém uma barreira artificial entre `INTERNAL`, `SHARED` e
`EXTERNAL`. Todos os trails elegíveis da camada entram na mesma busca. A rota
gulosa parte do XY atual, compara as extremidades, pode inverter linhas
compartilhadas abertas e pode rotacionar o ponto inicial de um perfil fechado
sem inverter seu climb/conventional. Uma programação dinâmica escolhe as
orientações para a ordem candidata, seguida de 2-opt limitado a janelas de 12
trails e duas iterações. A posição de saída da camada passa a ser a origem da
busca da profundidade seguinte; não há retorno intermediário ao datum. Cada
operação termina com retração e todo G0 XY entre trails ocorre no Z seguro.

O `STABILITY ROUTE` preserva `INTERNAL → SHARED → EXTERNAL`, risco geométrico,
grafo de retenção e dependências. Apenas entrada/direção que não alterem essa
ordem podem reduzir distância. Depois de escolhido o plano, uma cadeia
explícita de dependências liga as operações híbridas; reordenar uma operação
crítica para antes de outra segura passa a invalidar o plano. Se profundidade
extra criar, por exemplo, Z 15,0 e Z 15,5 para chapa de 15 mm, ambas são
STABILITY: a troca ocorre na primeira profundidade que alcança o material, não
somente no último número da agenda. `per_piece`, `global_by_depth` e a sequência
de `TabRelease` não usam esse otimizador.

O `RetentionGraph` contém o nó `STOCK`, peças e arestas `StockTab` ou
`SharedTab`. Uma ligação entre peças não é promovida a ligação direta ao
stock. `RetentionRules` concentra os limiares de tamanho, aspect ratio,
quantidade mínima, espaçamento e trecho livre; peças pequenas exigem no mínimo
dois apoios, peças regulares três e alongadas quatro. A largura e a espessura
continuam sendo exatamente as informadas pelo operador. A camada global
aproveita primeiro os tabs exclusivos existentes e somente cria SharedTab para
suprir retenção quando há um intervalo compartilhado seguro; tab sobreposta,
concentrada ou sem caminho direto/indireto ao stock bloqueia o plano.

`keep_tabs` não acrescenta movimentos depois do corte principal.
`automatic_release` calcula primeiro uma ordem de peças cuja remoção não
desconecta as restantes do stock. Cada `TabReleaseOperation` vai em rápido ao
tab, mergulha verticalmente até a profundidade final, percorre somente
`max(0, tab_width - tool_diameter)` e retrai imediatamente. Se largura e
diâmetro forem iguais, ou a ferramenta for maior, não existe sweep. Na última
tab, o sweep termina afastando-se do centro da peça quando a geometria permite
decidir o sentido, e o movimento seguinte é obrigatoriamente a retração. A
primeira liberação depende de todas as operações principais; as seguintes
formam uma cadeia explícita.

`operations.build_global_cut_plan_moves` adapta o plano à lista histórica de
movimentos, de modo que simulador e writer não foram duplicados. A UI ganhou
`Ordem das profundidades` e `Depois do corte`; os valores são persistidos no
`SettingsJSON`. Linha comum, estratégia global ou TabRelease ativam o plano
novo. `per_piece + keep_tabs`, sem SharedEdge efetiva, preserva o caminho
legado, inclusive rampa, desaceleração de cantos e ponto inicial. Sobre-metal
de última passada separado permanece bloqueado com o plano global, porque ele
exigiria duas identidades físicas incompatíveis no mesmo kerf.

Cobertura acumulada após o ajuste de rota: **291 testes puros** (dois ignorados pela ausência
intencional de OpenCV), **59 testes Qt**, **113 testes gerais**, **12 smokes
FreeCADCmd** e compilação integral aprovados. Os 27 testes específicos do
planejador cobrem os casos A–R, linha comum opcional, FAST/STABILITY, continuidade
entre profundidades, reversão segura, ordem híbrida por risco, internos inválidos
e ferramenta sem plunge seguro. Sete testes do adapter confirmam G-code
existente, redução de rápidos sem mudança geométrica, G0 no Z seguro, plunge
único, sweep exato, TabRelease invariável e retração imediatamente após a última
tab. O smoke real da UI cobre seleção/persistência dos modos,
duas peças com SharedEdge e automatic release, além do arranjo relatado de 11
contornos: painel com entalhe, ripas trapezoidais e segmentos inclinados. Esse
arranjo entrou no `hybrid_stability`, reconheceu ao menos oito SharedEdges e
gerou o percurso sem a rejeição anterior. O benchmark disperso de três peças
com internos caiu de aproximadamente 14.049 mm para 4.030 mm de rápidos nas
duas camadas intermediárias, mantendo o mesmo conjunto `segment_id + depth`.
O mesmo comparativo foi incorporado ao smoke das onze peças e exige que o
híbrido permaneça abaixo da rota `global_by_depth` nas camadas não passantes.

Limites mantidos explicitamente: o CAM recebe polígonos/polilinhas, portanto
curvas já chegam discretizadas pelo adapter atual; o modelo não conhece
grampos, empeno real, vácuo ou rigidez do skeleton; e a base de ferramentas não
possui ainda um atributo específico de fresa center-cutting. Por segurança, o
TabRelease automático fica restrito aos tipos atuais `end_mill` e
`compression`; os demais são rejeitados. O operador ainda precisa confirmar
que a fresa cadastrada admite plunge e executar a prévia/teste seguro na
máquina.

### Auditoria de volta única em perfis com tabs — 12 de agosto de 2026

A lista de movimentos mostrou que a tab 2D já era uma modulação local de Z:
o perfil não era particionado em jobs e cada intervalo de tab continuava na
mesma sequência XY. Em um retângulo de perímetro 300 mm e profundidades 5, 10
e 15 mm, a passada final produziu 252 mm em Z -15 e 48 mm em Z -12, totalizando
uma única volta de 300 mm. `moves_for_preview` também apenas copiava essa lista
e o writer emitia uma linha para cada movimento, sem duplicação própria.

A auditoria encontrou, porém, uma reabertura real independente da modelagem da
tab. Com desaceleração de cantos ativa, `_append_corner_aware_loop` chegava ao
ponto final que já fechava o contorno e ainda emitia a saída lenta desse canto
sobre o começo da primeira aresta. Em um retângulo de 300 mm, isso acrescentava
8 mm depois do fechamento em cada passada atingida. O preview e o G-code
mostravam corretamente esse erro da lista-fonte. A saída lenta continua nos
cantos intermediários, mas agora é proibida no alvo final da volta.

Os movimentos de perfis fechados passaram a carregar `profile_id`,
`profile_loop_id`, `depth_pass`, início/fim de loop e transições de tab.
`audit_profile_cut_moves` mede voltas, fechamentos, comprimento XY, tabs,
retracts, reinícios e arestas repetidas por perfil/profundidade;
`validate_profile_cut_moves` bloqueia estruturalmente qualquer volta reaberta,
duplicada ou incompleta antes de o resultado chegar ao preview/G-code. Rampas
permanecem fora da medida da volta e múltiplas profundidades permanecem grupos
distintos. IDs de internos e externos também são separados no job composto.

Dez testes novos cobrem perfil sem tab, uma tab, múltiplas tabs, rampa,
`per_piece`, `global_by_depth`, `hybrid_stability`, injeção deliberada de uma
segunda volta, equivalência preview/G-code e o smoke de peças alongadas e
anguladas. O smoke real do diálogo compara ainda a quantidade de segmentos da
lista-fonte com os componentes desenhados pela UI. Resultado acumulado:
**291 testes puros** (dois ignorados), **59 testes Qt**, **123 testes gerais**,
**12 smokes FreeCADCmd** e compilação integral aprovados.

### Híbrido por peça com cobertura física compartilhada — 13 de agosto de 2026

O `GlobalCutPlan` continua sendo a fonte de verdade. A detecção, split parcial,
IDs e owners de `PhysicalCutSegment` não foram reconstruídos no gerador de
movimentos. A mudança ficou restrita às profundidades não passantes de
`hybrid_stability`: o antigo `FAST ROUTE` global permanece acessível pelo flag
interno `hybrid_intermediate_mode="fast"` para benchmark, enquanto o padrão
passou a `per_piece_common_line`.

Em cada profundidade intermediária, o scheduler cria um conjunto `DONE` novo e
escolhe uma peça. Os segmentos dela já presentes nesse conjunto são removidos;
os restantes são agrupados por conectividade diretamente a partir dos átomos
físicos. Isso produz loop quando todo o perímetro continua pendente ou um ou
mais trails abertos quando SharedEdges já foram executadas. Nenhuma ligação é
inventada entre as extremidades abertas. Ao concluir um trail, todos os seus
IDs entram em `SegmentDepthCoverage`; por ser uma cobertura física, um segmento
compartilhado executado por A completa também a mesma fronteira de B naquele Z.
Na profundidade seguinte, o conjunto começa vazio.

A pontuação da próxima peça ordena por: fragmentação externa, quantidade de
trails, distância até a primeira entrada, SharedEdges pendentes aproveitáveis,
distância rápida total e ID determinístico. Dentro da peça, internos continuam
antes do perfil; cada componente é contínuo. Trails abertos somente podem ser
invertidos quando marcados tecnologicamente reversíveis — hoje isso significa
uma trilha composta exclusivamente de SharedEdges. Loops preservam a direção e
podem rotacionar a entrada. Toda troca entre componentes mantém retração, G0 em
Z seguro e novo plunge.

`GlobalCutPlan.validate` agora cruza operações com `SegmentDepthCoverage` e
rejeita owner inválido, `segment_id + depth` repetido, cobertura incompleta de
qualquer peça ou gap físico. `CutRouteMetrics` registra, por profundidade,
distâncias de corte/rápido, retracts, plunges, trocas de peça, trails,
fragmentação, duplicações, perímetro independente, economia por linha comum,
SharedEdges reutilizadas e rápidos longos. A UI guarda essas métricas no resumo
transitório do plano, sem criar nova opção visível.

No arranjo real de onze contornos usado pelo smoke (painel entalhado, quatro
ripas altas, três segmentos inclinados, duas tampas e cunha), ambos os métodos
cortaram 18.024,32 mm físicos por camada, contra 21.440,82 mm de perímetros
independentes: economia de **3.416,50 mm** por oito SharedEdges. Nas duas
camadas intermediárias, o comparativo foi:

```text
                         FAST antigo        por peça + linha comum
trails                   26 + 26            13 + 13
retracts/plunges         52                 26
trocas de peça           40                 20
peças fragmentadas       12                 2
rápidos                  4.149,39 mm        9.749,22 mm
duplicações físicas      0                  0
```

O experimento troca distância rápida absoluta por metade das entradas e trocas,
forte redução de fragmentação e leitura visual peça → vizinha, como solicitado.
A camada passante manteve `STABILITY ROUTE`, `RetentionGraph` e precedências;
somente o ponto de chegada herdado da camada anterior pode alterar a rotação de
entrada entre operações igualmente seguras. `per_piece`, `global_by_depth`,
tabs, `TabRelease`, preview e writer 1:1 permanecem nos caminhos anteriores.

Foram acrescentados T01–T16 e um cenário de sete peças com retângulos, L,
perfil alongado, linha comum total/parcial, dois lados já cobertos e
fragmentação concorrente. O smoke do caso real executa os dois schedulers e
compara as métricas. A validação final aprovou **308 testes puros** (dois
ignorados por dependência opcional), **59 testes Qt offscreen**, **123 testes
gerais** e **12 smokes FreeCADCmd**. A compilação dos módulos alterados e a
verificação de whitespace do diff também foram aprovadas.

### Correção do fluxo literal Editor → organizar → Corte — 13 de agosto de 2026

A validação anterior do híbrido não reproduzia integralmente o gesto do
operador: parte dos testes chamava o planejador diretamente e o smoke da UI
chegava ao cenário com `Piece2D` previamente montado. O smoke atual começa com
quatro retângulos crus e dispersos, sem relações `Piece2D`, executa o
organizador real em thread, aplica sua primeira prévia e pede o Corte pelo
adapter do Editor enquanto o worker apenas termina de encerrar. A captura
`/tmp/woodcam_hybrid_editor_flow.png` é produzida da lista real de movimentos.

A reprodução revelou e corrigiu seis defeitos concretos:

- depois de organizar, a seleção continuava limitada ao último retângulo
  criado; a ponte Editor → CAM priorizava essa seleção e entregava uma única
  peça. Aplicar o nesting agora seleciona transitoriamente todos os IDs
  organizados;
- a tolerância persistida de linha comum (0,20 mm) também era usada para
  simplificar a geometria compensada. Ela apagava pequenos segmentos de cantos
  arredondados e podia inventar relações tangentes. Normalização e coincidência
  física agora têm limites próprios, sem deformar a linha-centro;
- cantos arredondados em grades ortogonais deixavam arcos isolados nos nós T/X,
  criando trails e retracts sem função. Apenas neste caso ortogonal comprovado
  a compensação usa junção contínua; peças oblíquas/agudas continuam com o
  offset arredondado seguro;
- cada trail começava com um plunge em Z 0 seguido por outro plunge no mesmo
  XY. A entrada agora vai diretamente à primeira profundidade (ou à cota da
  tab quando ela realmente começa naquele ponto);
- o plano global ignorava a opção Subida/Convencional. Contornos externos e
  internos agora são orientados antes da resolução dos segmentos físicos;
- o roteamento de um grupo seguro terminava no seu ótimo local sem considerar
  a entrada do próximo grupo igualmente seguro. O otimizador ganhou um alvo de
  saída antecipado, sem atravessar barreiras de fase ou retenção.

No caso literal, cada profundidade corta 792 mm físicos contra 1.056 mm de
perímetros independentes: **264 mm economizados por camada**, com quatro
SharedEdges executadas uma única vez. As camadas intermediárias ficaram com
quatro trails, zero peça fragmentada, zero segmento duplicado, zero rápido
longo e, respectivamente, 142,24 mm e 92,00 mm de rápidos. A camada passante
permanece intencionalmente no `STABILITY ROUTE`; distância continua subordinada
a SharedEdges, retenção e precedências.

Com `keep_tabs`, as 12 tabs do cenário são atravessadas exatamente uma vez na
camada em que ficam ativas: subida local em uma extremidade, avanço XY pela
largura configurada e descida na outra extremidade. O smoke rejeita plunge
consecutivo no mesmo XY, tab com avanço XY nulo, segmento físico repetido e
liberação automática indevidamente herdada. `automatic_release` continua em
seu teste separado.

Resultado acumulado depois desta reprodução: **310 testes puros** (dois
ignorados pela dependência opcional), **59 testes Qt**, **125 testes gerais**,
**12 smokes FreeCADCmd**, compilação integral e `git diff --check` aprovados.

### Estratégia experimental peça ida/volta — 13 de agosto de 2026

Foi acrescentada uma quarta estratégia explícita,
`hybrid_piece_bidirectional`, apresentada na UI como
`Teste — peça ida/volta + estabilidade`. Ela não substitui nem altera
`per_piece`, `global_by_depth` ou `hybrid_stability`.

Nas profundidades não passantes, `_schedule_piece_bidirectional_depths`
seleciona uma peça a partir dos mesmos `PhysicalCutSegment` do
`GlobalCutPlan`, remove tudo que já está `DONE` em cada profundidade e mantém a
peça durante todos os stepdowns intermediários. Uma trilha é percorrida para
fora em Z1, invertida a partir do seu ponto final em Z2 e alternada novamente
se houver mais camadas. O estado `DONE` continua separado por profundidade;
uma SharedEdge cortada pelo primeiro owner não reaparece no segundo owner, mas
volta a estar pendente no próximo Z.

Loops completos podem inverter sua travessia sem mudar o ponto físico de
entrada. Quando a remoção de SharedEdges produz um trail aberto, ele não é
fechado nem recebe retorno ao início: a camada seguinte começa no endpoint em
que a anterior acabou e percorre exatamente os mesmos segmentos no sentido
contrário. Entre as duas camadas da mesma visita não há retract. Entre peças
distintas há retract, G0 em Z seguro e novo plunge; em particular, o adapter
não usa uma aresta `DONE` como conector de baixa altura, pois isso pareceria
uma reusinagem da linha comum. Tabs não modulam Z nas camadas que ainda estão
acima da cota retida. Ao alcançar a espessura, o plano volta integralmente ao
`STABILITY ROUTE`, e `TabRelease` continua inalterado.

O smoke literal da UI cria e organiza quatro retângulos, seleciona a nova
opção, gera o Corte e renderiza a lista real de movimentos. As oito operações
intermediárias aparecem em quatro pares peça/Z: cada segundo passe tem direção
oposta, não há `cleared_path_link`, existem somente quatro entradas seguras
(uma por peça) e o maior salto entre peças vizinhas é 86 mm. Cada profundidade
continua cobrindo 792 mm físicos contra 1.056 mm de perímetros independentes,
economizando 264 mm por linha comum.

Foram acrescentados testes para seleção explícita sem regressão dos três modos,
sequência por peça, inversão de loop, ida/volta de trail aberto, cobertura
física por profundidade, ausência de tabs intermediárias, continuidade sem
retract dentro da visita, troca segura entre peças sem recortar aresta `DONE`,
STABILITY final e contrato de TabRelease. Resultado acumulado:
**318 testes puros** (dois ignorados pela dependência opcional), **59 testes
Qt offscreen**, **129 testes gerais**, **12 smokes FreeCADCmd**, compilação e
inspeção visual do preview aprovados.

Uma revisão do preview acrescentou dois ajustes. Em todas as escolhas não
passantes, a distância desde a posição atual passou à frente da fragmentação;
esta permanece apenas como desempate. Entradas de loops intermediários também
ficaram restritas a vértices geométricos, impedindo que um split colinear criado
por `Melhor fixação` faça a ferramenta começar no meio de uma lateral. O smoke
fixa origem inferior esquerda, offsets zero e comprova que a primeira entrada
fica a menos de 10 mm do datum.
A diagonal que ainda aparecia na captura era o movimento opcional `Retornar ao
ponto inicial`, persistido de outro ensaio, e desaparece quando essa opção está
desmarcada.

O adapter global também passou a receber `use_ramp`, `ramp_length` e
`ramp_type`. Como um SharedEdge aberto não possui área segura para um lead-in
fora do perfil, a rampa é confinada ao próprio trail: avança metade do
comprimento disponível enquanto desce, retorna ao ponto de entrada concluindo
a descida e só então inicia a travessia física agendada. Assim não existe
plunge no material, cunha acima da profundidade, fechamento artificial ou
geometria lateral nova. A rampa para antes de uma tab ativa e o writer continua
emitindo esses movimentos com o avanço específico de rampa.

Após os ajustes: **322 testes puros** (dois ignorados), **59 testes Qt**,
**129 testes gerais**, **12 smokes FreeCADCmd**, compilação integral e
`git diff --check` aprovados.

Uma auditoria posterior da ponte UI → CAM encontrou uma regressão de escopo:
`cut_depth_strategy != per_piece` e `automatic_release` conseguiam acionar o
`GlobalCutPlan` mesmo com `common_line_enabled = false`. Isso substituía o
corte padrão por uma estratégia criada exclusivamente para Linha comum. O
dispatcher agora só entra no plano global quando o operador ativa Linha comum;
com a opção desligada, os settings efetivos são sempre `per_piece` e
`keep_tabs`, e o percurso passa pelo `build_contour_cut_stage` legado. Os
combos globais ficam desabilitados e voltam visualmente aos valores padrão.

O smoke real compara o mesmo perfil com tabs em duas configurações: controle
legado e dados antigos contendo `hybrid_piece_bidirectional` mais
`automatic_release`. Com Linha comum desligada, as listas de movimentos são
iguais, não existe `_global_cut_plan_summary`, `global_cut` nem `tab_release`.
Permanecem aprovados **322 testes puros** (dois ignorados), **59 testes Qt**,
**129 testes gerais** e **12 smokes FreeCADCmd**.

### Rebaixos importados e duplicidade restrita à seleção — 18 de agosto de 2026

A importação de `Part::Shape` passou a reconhecer faces planas de fundo que
estão abaixo da face larga de referência. Essas faces entram na cópia do
`VectorDocument` como `pocket_region` e eventuais fios internos como
`pocket_island`, com profundidade, cotas e identidade do componente de origem.
O Shape/FCStd não é alterado. Regiões circulares pequenas continuam seguindo o
contrato de furo com profundidade, em vez de virarem preenchimentos.

No Editor, `pocket_region` é uma projeção hachurada: o clique dentro da área
seleciona o rebaixo, mesmo quando ele pertence ao grupo da chapa ou compartilha
uma lateral com o contorno externo. `Reconhecer peças e furos` mantém essa
região vinculada à peça como característica de fabricação, mas não a classifica
como recorte passante nem como peça solta. A organização move externo, internos
e rebaixos rigidamente; PanelNest e Corte de documento completo não transformam
a hachura em um segundo perfil.

Ao abrir `CAM → Preenchimento` com a hachura selecionada, a ponte envia apenas
a região e suas ilhas e pré-carrega a profundidade importada para confirmação.
Corte/Furo filtram essas características. Rebaixos abertos na borda podem
compartilhar seu segmento com a chapa porque ambos carregam a mesma identidade
física de origem; a exceção não se aplica a vetores comuns sem essa relação.

A validação CAM também passou a respeitar o escopo explícito: uma entre duas
cópias coincidentes pode receber Corte sem ser recusada por outra cópia não
selecionada. Se ambas forem selecionadas, `DUPLICATE_ENTITY` continua bloqueando
a operação e tem prioridade na mensagem exibida.

Foram aprovados **326 testes puros** (dois ignorados pela dependência opcional),
**60 testes Qt offscreen**, **129 testes gerais**, **12 smokes FreeCADCmd** e
compilação integral. Os smokes novos constroem rebaixo fechado e aberto na
borda com OCC, conferem profundidade e preservação do Shape, além de validar a
seleção única/dupla e o preenchimento da profundidade na UI real.

### Furos PanelNest soltos e arestas OCC degeneradas — 18 de agosto de 2026

Foram reproduzidos dois defeitos de importação. No caminho direto pela árvore,
uma aresta OCC linear com os dois extremos iguais — ou que colapsava somente
depois da projeção no plano da chapa — chegava ao construtor de `LineSpan` e
abortava o lote inteiro com `LineSpan cannot have zero length`. O conversor
agora descarta somente essa aresta sem área/comprimento, registra um
`ImportIssue` e continua formando o wire com as demais arestas. Nenhum ponto é
movido e o Shape de origem permanece intocado.

No caminho PanelNest, o importador agora reconcilia as convenções XY normal,
local e transposta, aceitando uma alternativa apenas quando o círculo completo
fica dentro da área útil do contorno externo e fora dos recortes internos. Um
furo que não cabe em nenhuma convenção é omitido com aviso, em vez de aparecer
como anel solto. Quando o Shape exato contém vários sólidos repetidos ou
cilindros auxiliares, a classificação escolhe somente o componente cujas
dimensões correspondem ao registro `PanelPart`; quantidade continua sendo
expandida pelo próprio registro, sem duplicar todos os componentes em cada
ocorrência.

As regressões cobrem XY transposto, perfil com origem diferente de zero, furo
fora do material, furo dentro de recorte interno, componente circular solto e
wire com aresta zero. Permanecem aprovados **331 testes puros** (dois ignorados
pela dependência opcional), **60 testes Qt**, **129 testes gerais** e os smokes
FreeCADCmd de importação, PanelNest e UI.

### Revalidação em móvel real: plano comum e silhueta de rebaixo — 18 de agosto de 2026

A validação sintética anterior não cobria uma chapa em pé com rebaixo cego. No
arquivo real de cozinha que motivou a correção, o contorno externo usava a
projeção local da face larga, mas o fundo do rebaixo era recursivamente lido em
XY global e depois estacionado sozinho. Rebaixos retangulares colapsavam para
uma linha sem área; rebaixos circulares conservavam o diâmetro, mas perdiam sua
posição relativa e apareciam como anéis fora da chapa. A filtragem PanelNest
acabava descartando 27 dessas regiões degeneradas.

O fundo cego agora herda exatamente o mesmo plano ortonormal da chapa e só é
estacionado junto com a peça completa. A silhueta de corte externo também vem
da maior face plana paralela, enquanto a face detalhada continua sendo usada
para descobrir fundos, profundidades e furos. Assim um rebaixo aberto na borda
preenche/hachura a área rasa sem criar um entalhe passante no contorno externo;
um recorte realmente passante continua ausente nas duas faces e permanece
`cut_internal`. A união temporária de componentes coplanares com uma única face
é desembrulhada para preservar `OuterWire`. Todas essas operações usam cópias
OCC e não modificam Shape, Placement ou FCStd.

Os dois caminhos foram executados no mesmo móvel real. A importação direta e a
importação lida pelo PanelNest convergiram para **54 cortes externos, 42 furos e
35 regiões de rebaixo**, sem aviso de vetor descartado. A classificação
resultou em **54 peças**, zero contorno aberto/rejeitado e todas as 131 entidades
geométricas vinculadas a uma peça. As 35 regiões produziram `QPainterPath` com
largura/altura positivas e brush de hachura ativo.

O smoke OCC permanente agora cobre chapa em pé com rebaixo fechado, rebaixo
aberto na borda, dois rebaixos circulares cegos, área externa íntegra e ausência
de `cut_internal` coincidente. Foram novamente aprovados **331 testes puros**
(dois ignorados), **60 testes Qt**, **129 testes gerais**, **12 smokes
FreeCADCmd**, compilação integral e inspeção de whitespace do diff.

### Tabs manuais no canvas do Editor 2D — 18 de agosto de 2026

O botão `Posicionar tabs...` ainda ignorava a fonte CAM ativa e chamava
diretamente `get_selected_geometry()`. Por isso uma operação iniciada no Editor
2D exigia seleção de Sketch, face ou objeto 3D. Mesmo se essa validação fosse
vencida, o callback de mouse era registrado na câmera 3D, não no canvas que a
interface prometia.

O modo manual agora lê a seleção validada do `VectorDocument`, abre a aba do
Editor (ou traz sua janela destacada à frente) e suspende temporariamente a
ferramenta corrente. Somente a liberação do botão esquerdo é entregue ao CAM;
seleção, arraste e ferramentas vetoriais não recebem o gesto. A projeção no
contorno aceita no máximo 12 pixels, independentemente do zoom. Os marcadores
são overlays não interativos, fora do documento; clicar numa marca a remove e
botão direito/Esc conclui e retorna à configuração de Corte.

Os modos automático e manual passaram a ser mutuamente exclusivos. Assim uma
marcação explícita serializa `tab_count = 0` e não soma silenciosamente a
distribuição automática. As coordenadas confirmadas continuam usando o
contrato existente de `tab_positions` e foram exercitadas até os movimentos de
corte com elevação de tab.

A regressão Qt confirma captura modal e revisão geométrica invariável. O smoke
real executa o caso sem API de seleção 3D, rejeita clique distante, adiciona,
remove e recoloca a marca, conclui por botão direito, retorna ao Corte e verifica a tab
nos movimentos finais.

Uma segunda reprodução cobriu a sequência usada pelo operador: visualizar o
percurso normal, marcar tabs sobre uma chapa importada como `GroupEntity`,
concluir pelo botão direito e aplicar. O cancelamento genérico limpava a
seleção do grupo; o Apply caía no documento inteiro e tentava converter o
próprio objeto relacional `group-...` como contorno sem pontos. A captura CAM
agora preserva sua seleção de origem ao ser concluída, enquanto ferramentas
normais mantêm o contrato anterior de limpar a seleção. Como defesa adicional,
o adapter de documento inteiro ignora `GroupEntity` e consome somente suas
geometrias-folha.

O smoke atualizado usa deliberadamente um grupo, conclui com botão direito,
confere que o ID selecionado sobrevive, expande exatamente um contorno e chega
a movimentos com tab. Foram aprovados **332 testes puros** (dois ignorados),
**61 testes Qt**, **129 testes gerais**, **12 smokes FreeCADCmd**, compilação
integral e `git diff --check`.

### Prévia integral de preenchimento — 19 de agosto de 2026

A diferença entre a prévia deformada e a simulação correta não estava nos
movimentos nem no G-code. A simulação de Preenchimento já enviava todos os
segmentos ao overlay Coin, enquanto a prévia comum ainda os reduzia ao limite
de 4.000. Essa redução pulava pontos de quina e religava os pontos mantidos por
cordas diagonais que não pertenciam ao percurso real.

Preenchimento/Rebaixo agora usa a mesma projeção Coin integral na prévia e na
simulação. Operações antigas com projeção densa persistida são ocultadas e
recriadas a partir de `MovesCompressedBase64`; o FCStd de origem, os movimentos
e o G-code não são alterados.

O smoke da UI reproduz uma trajetória ortogonal com **5.001 segmentos** e uma
quina em cada movimento. A prévia conserva os 5.001 segmentos e rejeita por
asserção qualquer diagonal. Permanecem aprovados **332 testes puros** (dois
ignorados), **61 testes Qt**, **129 testes gerais**, **12 smokes FreeCADCmd**,
compilação integral e `git diff --check`.

### Escopo das tabs manuais e limpeza da prévia — 19 de agosto de 2026

As coordenadas de tabs manuais eram gravadas junto das preferências globais de
usinagem. Ao abrir `Posicionar tabs...` numa chapa diferente, círculos de uma
configuração anterior já apareciam antes do primeiro clique. Ao mesmo tempo, o
overlay do último percurso permanecia no Editor, incluindo deslocamentos
rápidos magenta entre peças, e confundia a leitura da marcação.

Posições manuais agora pertencem à geometria/operação corrente. Um contorno
novo começa vazio; reabrir a marcação do mesmo contorno conserva somente as
tabs colocadas nele; editar uma operação aplicada restaura exclusivamente seu
próprio `tab_positions`. Preferências antigas ainda são lidas, mas a chave
geométrica legada é ignorada e desaparece na próxima gravação. Ao iniciar a
captura, o overlay CAM anterior é ocultado sem alterar vetores, operação,
movimentos ou G-code.

O smoke da UI injeta três tabs de outra chapa e um rápido magenta antes de
iniciar a captura. Ele exige zero marcadores e zero percurso antigo na abertura,
adiciona uma tab real, reabre a mesma configuração e confirma que apenas ela é
preservada.

Foram novamente aprovados **332 testes puros** (dois ignorados), **61 testes
Qt**, **129 testes gerais**, **12 smokes FreeCADCmd**, compilação integral e
`git diff --check`.

### Uma tab manual, um único contorno — 19 de agosto de 2026

A captura com três marcadores no Editor e várias pontes no percurso revelou
duas multiplicações posteriores à interface. No corte convencional, cada
coordenada XY manual era projetada novamente sobre todos os perfis da chapa;
assim, três cliques viravam três tabs em cada peça selecionada. No planejador
global/linha comum, a coordenada já era associada a um único trecho, mas a
camada de retenção ainda completava silenciosamente o mínimo mecânico das
demais peças.

Cada marcador XY agora é atribuído exatamente ao contorno mais próximo antes
da otimização da ordem de corte. A associação acompanha o índice estável do
perfil mesmo quando o nearest-neighbor muda a ordem de usinagem. No plano
global, uma lista manual não vazia é autoritativa: não recebe distribuição
automática, suplementação por peça nem relatório bloqueante de quantidade
mínima. O modo automático continua com todas as regras de retenção e segurança
anteriores; somente a escolha explícita do operador deixa de ser alterada.

As regressões usam duas peças afastadas e conferem tanto os `profile_id` dos
movimentos convencionais quanto os proprietários das tabs do plano global.
Três coordenadas sobre a primeira peça não podem produzir qualquer tab na
segunda, e uma única coordenada manual permanece uma única tab física.

O smoke FreeCADCmd reproduz ainda a chapa com duas peças e audita a passada
final: os três cliques resultam em `3` cruzamentos de tab no primeiro perfil e
`0` no segundo. Foram aprovados **333 testes puros** (dois ignorados), **61
testes Qt**, **130 testes gerais**, **12 smokes FreeCADCmd**, compilação
integral e `git diff --check`.

### Linha comum orientada a tempo real — 19 de agosto de 2026

A auditoria solicitada comparou os movimentos efetivamente consumidos pelo
simulador/G-code, não somente o comprimento das arestas compartilhadas. O modo
`Por peça` economizava corte XY, mas voltava a cada trilha em cada profundidade.
Num arranjo determinístico de 12 retângulos em grade, quatro profundidades,
tabs e rampa de 30 mm, isso produziu 26,1 m de movimentos rápidos contra 2,8 m
do corte convencional. Com a liberação final das tabs, a linha comum chegava a
**30min06s**, acima dos **28min51s** do corte normal.

`Otimizado — ida/volta + estabilidade` deixou de ser experimental e passou a
ser o padrão quando Linha comum é ativada. Cada peça conclui suas camadas não
passantes na mesma visita; trilhas abertas alternam o sentido para começar a
profundidade seguinte no ponto onde a fresa já terminou. Perfis fechados não
são invertidos: eles já retornam à entrada e conservam climb/conventional. A
camada passante continua sob `STABILITY ROUTE`, com SharedEdges primeiro,
perímetros exclusivos depois e tabs como modulação local de Z.

A fase opcional `Liberar automaticamente` permanece posterior ao corte
principal e mantém plunge, sweep mínimo e retração imediata. A ordem deixou de
ser alfabética/por risco heurístico: entre as peças cuja remoção o
`RetentionGraph` comprova ser segura, escolhe-se a rota espacial total mais
curta, e as tabs dessa peça usam nearest-neighbor determinístico. Dependências,
conectividade ao stock e última tab explícita permanecem obrigatórias.

No mesmo benchmark e com os avanços padrão do WoodCAM, o modo otimizado marcou
**21min02s mantendo tabs** (27% abaixo do normal) e **24min07s removendo tabs**
(16% abaixo do normal e 20% abaixo da linha comum legada equivalente). Os
testes de desempenho exigem margem mínima de 15%, inclusive com rampa ativa;
também verificam segmento físico único por profundidade, tabs intactas,
liberação topologicamente segura e início pela peça segura mais próxima.

Validação acumulada: **334 testes puros** (dois ignorados), **61 testes Qt**,
**133 testes gerais**, **12 smokes FreeCADCmd**, compilação integral e
`git diff --check`. O smoke real confirma a seleção automática da estratégia
otimizada e distingue trilhas abertas, que alternam, de perfis fechados, que
preservam o sentido.

### Seletor de idioma da bancada — 19 de agosto de 2026

Quando o WoodCAM é carregado pela bancada PanelNest, o menu global `Idioma` da
barra principal do FreeCAD (`Português`/`English`) é a escolha oficial e traduz
a bancada inteira, incluindo menus, ferramentas, dicas, diálogos e o Editor
2D. `Idioma` no Editor 2D permanece apenas como fallback para uso independente.
As duas camadas alteram somente propriedades de apresentação; comandos, IDs,
callbacks, documento vetorial, operações CAM e histórico Undo/Redo continuam os
mesmos. A preferência global fica em
`User parameter:BaseApp/Preferences/PanelNest/language`; a camada registra o
texto-fonte em propriedades Qt, portanto novas janelas e atualizações dinâmicas
continuam traduzíveis sem duplicar estado geométrico.

O teste Qt `test_language_menu_translates_presentation_and_keeps_actions`
confirma a troca nos menus e no Snap, o retorno ao português e revisão de
documento inalterada. O contrato visual das abas foi posteriormente substituído
pela barra estável descrita na correção de 20 de agosto.

### Última volta separada, liberação explícita de tabs e assento de parafuso — 19 de agosto de 2026

O modo otimizado de linha comum agora conclui, em ida/volta por peça, somente
as profundidades anteriores à última. Em seguida inicia uma fase
`final_sheet_pass` própria e percorre a profundidade final em todas as peças.
O plano continua rejeitando qualquer duplicação de `segment_id + depth`; no
benchmark de 12 retângulos, os 101 segmentos físicos da última volta produziram
101 ocorrências, sem usar uma aresta já cortada nessa mesma profundidade como
deslocamento.

O adapter pode permanecer baixo apenas sobre kerf de uma passada anterior e
somente quando o caminho completo até a próxima entrada é curto. A interface
define esse limite como o maior entre 1,5 diâmetro efetivo da fresa e duas
tolerâncias de linha comum. Acima dele há retração, G0 em Z seguro e novo
plunge. Assim, atravessar uma segunda linha de corte a avanço de usinagem deixou
de ser a rota para alcançar outra região; o teste também comprova que um enlace
local de 5 mm ainda pode ser aproveitado com fresa de 6 mm.

`Remover tabs automaticamente ao final` passou a ser uma caixa visível no
grupo de tabs. Marcada, preserva a fase posterior já validada pelo
`RetentionGraph`: rápido até a tab segura, plunge na profundidade final, sweep
somente de `largura - diâmetro` quando positivo, retração imediata e rápido para
a próxima. O controle legado persistido continua sendo lido, mas permanece
oculto e sincronizado para não quebrar trabalhos antigos.

Na aba Furo, a antiga `Cota inicial` foi renomeada para `Profundidade inicial
Z`: ela representa material já removido antes do início do percurso e nunca
foi um diâmetro. Para o caso real de assentar a cabeça do parafuso foi criado
`Criar assento maior na entrada do furo`, com diâmetro e profundidade próprios.
O WoodCAM abre primeiro o assento circular com fundo plano e depois continua o
furo principal. A operação exige fresa de topo ou de compressão, diâmetro do
assento pelo menos igual ao da fresa, maior que o furo e profundidade que não
ultrapasse o furo.

No benchmark determinístico com quatro profundidades e rampa de 30 mm, o corte
normal marcou **28min51,5s**. A linha comum nova marcou **20min06,5s mantendo
tabs** (30,3% menor) e **23min32,5s removendo tabs** (18,4% menor). Foram
aprovados **336 testes puros** (dois ignorados pela dependência opcional), **62
testes Qt offscreen**, **137 testes gerais**, os **166 testes direcionados** de
planejamento/operações/validação, compilação integral, o smoke FreeCADCmd
completo da interface e um smoke específico que usa os movimentos reais de
linha comum, tabs e assento de parafuso. Ambos também alternam o seletor global
para inglês; o tradutor específico do WoodCAM é reaplicado depois do tradutor
amplo do PanelNest para impedir traduções parciais nos controles CAM.

### Barra de operações estável entre idiomas — 20 de agosto de 2026

A captura real revelou uma oscilação periódica, não apenas uma diferença de
tradução. O temporizador global do PanelNest, a cada 750 ms, reescrevia o
`QTabWidget` do WoodCAM com seu catálogo amplo e parcial; o temporizador local,
a cada 1.000 ms, restaurava o catálogo CAM e escondia as abas originalmente
icon-only. A barra alternava entre dez abas nomeadas e duas nomeadas mais oito
ícones, chegando a misturar `Simulação e Save`.

O limite de propriedade agora é explícito. `register_widget` marca a raiz com
`woodcam_i18n_owned`; o tradutor global ignora essa raiz e seus descendentes,
enquanto continua traduzindo menus, barras e diálogos próprios do PanelNest.
Cada aba CAM conserva sua fonte portuguesa numa propriedade dedicada e publica
o texto traduzido tanto em `TabText` quanto em `TabData`, tornando atualizações
posteriores idempotentes.

O contrato visual definitivo é ícone + nome em todas as dez abas, nos dois
idiomas; se a largura acabar, a rolagem nativa da barra permanece disponível.
O mesmo passe de inspeção removeu misturas visíveis no formulário de Corte,
incluindo passagens, direção, compensação dimensional, geometria compartilhada,
ordem das profundidades, última passada, percursos 2D e nomes das ferramentas
padrão; nomes personalizados pelo usuário continuam dados e não são traduzidos.
O diagnóstico FreeCADCmd amostrou a barra a cada 250 ms por três segundos em
inglês e mais dois segundos em português, sem uma única mudança estrutural ou
texto parcial. O smoke completo repete três janelas de 850 ms, valida os dez
nomes traduzidos, retorna ao português e continua até o fluxo CAM integrado.
Também foram aprovados os três testes do tradutor PanelNest, incluindo a
regressão que impede o host de alterar uma subárvore pertencente ao WoodCAM,
além de **336 testes puros** (dois ignorados pela dependência opcional), **62
testes Qt offscreen**, **137 testes gerais**, compilação integral e o smoke
FreeCADCmd completo da interface.

### Sugestões dinâmicas do Assistente CAM — 20 de agosto de 2026

O catálogo de widgets não alcançava o conteúdo do Assistente CAM porque o
relatório era montado somente no instante em que o `QMessageBox` era aberto.
Assim a janela podia estar em inglês enquanto níveis, títulos, explicações e
valores sugeridos continuavam em português.

`CAMAdviceReport.to_plain_text` agora recebe opcionalmente um tradutor de
apresentação; sem ele, o domínio puro e os testes/consumidores anteriores
continuam recebendo exatamente o português original. A UI injeta o idioma
ativo e traduz o cabeçalho, operação, tipo e nome padrão da ferramenta, nível
`WARNING/SUGGESTION/INFORMATION`, título, explicação, valor recomendado e nota
final. O resumo e a dica do botão são formatados já no idioma correto, sem
esperar o temporizador de atualização.

O catálogo cobre os 13 diagnósticos atuais, inclusive contagem dinâmica de
regiões menores que a fresa. O teste Qt gera duas recomendações reais de
acabamento e exige texto integralmente inglês; o smoke FreeCADCmd abre o
Assistente na aba Acabamento 3D, confirma `ANALYZING NOW`, `3D Finishing` e
`[SUGGESTION]`, além de rejeitar qualquer ocorrência de `SUGESTÃO`.

### Cobertura integral da interface inglesa — 20 de agosto de 2026

A auditoria visual das abas Simulação e Editor 2D revelou dois limites que não
eram cobertos pelo primeiro tradutor. Itens de `QListWidget` e cabeçalhos de
`QTreeWidget` vivem no modelo Qt, fora dos textos de botões e labels; por isso
`Nenhum percurso aplicado ainda` e os cabeçalhos de camadas permaneciam em
português. Além disso, o fallback por substituição de palavras podia formar
frases híbridas como `Direction do veio`, `Apply à peça` e `Type de trabalho`.

O tradutor agora percorre células e cabeçalhos dos modelos, guarda o texto-fonte
em um papel Qt privado e altera somente `DisplayRole`. Identificadores em
`UserRole`, seleção, dados do documento e geometria não são tocados. As frases
de produção ganharam traduções exatas e completas para Simulação, propriedades
de peças, dimensões, Trabalho, Material, ferramentas, Corte, Furo,
Preenchimento, 3D, exportação, dicas e estados vazios. Contagens dinâmicas como
`1 selecionado`/`12 selecionados` também são formatadas em inglês. A dica do
painel lateral, atualizada durante o redimensionamento, passou a consultar o
idioma imediatamente em vez de aguardar o temporizador.

Um scanner FreeCADCmd abre uma janela real, seleciona inglês, força a criação
dos estados dinâmicos e inspeciona botões, labels, grupos, abas, combos,
tooltips, listas e tabelas; o resultado final foi **zero fragmentos portugueses**.
As regressões Qt confirmam ida e volta de listas/cabeçalhos e preservação de
`UserRole` e da revisão do `VectorDocument`. Foram aprovados **336 testes
puros** (dois ignorados pela dependência opcional), **62 testes Qt offscreen**,
**138 testes gerais**, o smoke específico de linha comum/assento e o smoke
FreeCADCmd completo da interface. O roteiro completo também foi tornado
determinístico: zera explicitamente a profundidade inicial e aceita o enlace
baixo curto já autorizado entre peças próximas, exigindo ainda cobertura de
todas as peças e zero duplicação física.

### Retenção física desde a primeira passada e restos soltos — 20 de agosto de 2026

A antiga espessura de tab era interpretada a partir da profundidade final e
permitia que uma peça perdesse a retenção antes da última volta. O contrato
agora é físico: `tab_thickness` continua sendo a chave persistida por
compatibilidade, mas representa a altura original de MDF intacta desde a face
superior. A profundidade permitida na ponte é
`espessura_material - altura_tab`, independente do sobrecorte. Para tab de
altura total, o alvo fica 0,2 mm acima da superfície. Nas passadas anteriores,
a modulação nunca corta a ponte para depois tentar restaurá-la: ela conserva o
material desde a primeira passagem.

As regras de retenção foram centralizadas em `RetentionRules`. Toda peça pede
no mínimo quatro tabs e também `ceil(perímetro / 300 mm)`. Como cantos não são
posições válidas e a tab possui largura, o seletor pode acrescentar um suporte
para manter o vão real abaixo de 300 mm. Uma tira de 1000 × 50 mm recebe oito
tabs alternadas nos dois lados longos e apresentou vão máximo de 267,3 mm. O
mesmo seletor é usado no corte normal e no plano de linha comum; redes
compartilhadas complementam a retenção com `SharedTab` sem duplicar arestas.

O grupo existente de tabs ganhou `Fixação de restos soltos` com Desativada,
Tabs e Parafusos. Recortes internos fechados são identificados exatamente. Para
restos criados entre diversas peças do nesting, a área da chapa alimenta um
flood-fill conservador: células são usadas somente para decidir conectividade e
deliberadamente superestimam passagens para não inventar um corpo solto. Pontos
de parafuso e tabs são depois confirmados contra segmentos vetoriais exatos. Se
não houver folga para cabeça/arruela, raio da fresa e margem, o fallback cria
duas `WasteTab` de altura total, separadas e fora da liberação automática.

Todos os pilotos de parafuso precedem qualquer operação de contorno. O adapter
retrai, retorna à origem segura e emite uma única sequência `M5`, `M0`, `M3` e
tempo de retomada. As posições persistem em coordenadas de modelo e máquina;
cabeça + margem, acrescida do raio da ferramenta posterior, é um keep-out para
todo percurso aplicado depois. A prévia mostra essas regiões e exige Z seguro
acima da cabeça. Diâmetro piloto explícito menor que a fresa é rejeitado, sem
alargamento silencioso.

A remoção ao final mantém a fase existente e agora oferece supervisão única,
por peça ou por tab; o padrão é por peça. Cada pausa ocorre já retraída, para o
spindle e só libera a ponte após `Cycle Start`.

Foram aprovados **339 testes puros** (dois ignorados pela dependência opcional),
**62 testes Qt offscreen**, **145 testes gerais**, **215 testes direcionados**,
os **13 smokes FreeCADCmd**, compilação dos módulos alterados e o fluxo real da
bancada em português e inglês. O smoke específico confirma tab integral de
15 mm com corte final de 15,5 mm e a ordem pilotos → pausa única → contornos; o
smoke completo confirma a linha comum sem duplicação física.

### Reabertura do percurso aplicado no Editor 2D — 20 de agosto de 2026

Foi reproduzido o fluxo em que um Corte era aplicado, o operador voltava ao
Editor 2D, selecionava outros vetores e pedia novamente `Percursos 2D`. Como a
aba não representa uma configuração CAM própria, `_collect_settings()` herdava
o último modo (`cut`) e reconstruía o percurso com a seleção nova, em vez de
ler os movimentos persistidos. Isso produzia a mensagem enganosa de seleção
incompatível apesar de o Corte aplicado continuar íntegro na árvore.

O resolvedor de prévia agora diferencia consulta de edição: em Editor 2D ou
Simulação, usa o último percurso persistido do tipo solicitado; numa aba CAM
explícita, continua calculando uma prévia nova com a seleção atual. O smoke
completo cria um Corte real, troca para Editor 2D, proíbe qualquer chamada ao
gerador e confirma que os movimentos desserializados são exatamente os
persistidos. A bateria permaneceu com **339 testes puros**, **62 testes Qt**,
**145 testes gerais** e **13 smokes FreeCADCmd** aprovados.

### Exportação de peças compostas com rebaixos — 20 de agosto de 2026

O caso real `Cozinha v3.FCStd` mostrou 50 contornos externos preservados no
`VectorDocument`, dos quais 19 pertenciam a `GroupEntity` por possuírem furos
ou rebaixos. Ao exportar uma seleção, DXF/SVG tentavam serializar o wrapper do
grupo como se fosse geometria e não percorriam seus `child_ids`. O arquivo
resultante continha somente os 31 contornos de peças simples, embora incluísse
parte das operações internas selecionadas.

Os exportadores agora expandem grupos recursivamente, eliminam IDs repetidos e
serializam apenas folhas geométricas. Seleção explícita representa o objeto
inteiro, mesmo que uma camada filha esteja temporariamente oculta; sem seleção,
continua valendo o contrato de exportar somente camadas visíveis. O round-trip
executado diretamente sobre o FCStd do caso produziu **50 contornos externos,
31 rebaixos e 51 furos**, totalizando 132 entidades sem duplicação. Testes
puros equivalentes cobrem DXF e SVG.

### Copiar e colar peças completas — 20 de agosto de 2026

O Editor 2D possuía cópia por `Ctrl+arrastar` e matriz, mas não oferecia o
contrato convencional `Ctrl+C`/`Ctrl+V`. Os atalhos agora são capturados pelo
canvas antes das ações globais do FreeCAD e também aparecem em `Editar`.
`Ctrl+C` guarda uma fotografia transitória do domínio selecionado; `Ctrl+V`
cria IDs novos para entidades, spans, nós, grupos e relações `Piece2D`, com
deslocamento horizontal acumulado de 10 mm para que cópias nunca fiquem
silenciosamente coincidentes. Cada colagem é um único comando com Undo/Redo.

Grupos são expandidos recursivamente, mantendo contorno, furos e rebaixos como
uma unidade. Em documentos reconhecidos sem grupo persistente, selecionar o
contorno externo inclui as relações internas e os rebaixos da `Piece2D`. A
fotografia do clipboard permite colar mesmo depois de mover ou apagar a fonte,
sem introduzir geometria persistente fora do `VectorDocument`.

O caso `Cozinha v3.FCStd` também foi auditado sem alterar o arquivo original.
O documento mantém 151 entidades e as posições das 50 peças; dois percursos de
Furo e dois de Preenchimento continuam persistidos. O FCStd e o único autosave
com o mesmo UUID não possuem uma operação de Corte persistida, portanto apenas
esse percurso precisa ser reaplicado sobre a geometria preservada. Foram
aprovados **344 testes puros** (dois opcionais ignorados), **63 testes Qt**,
**145 testes gerais** e os **13 smokes FreeCADCmd**.

### Distribuição automática sem bloqueio manual — 20 de agosto de 2026

O erro de retenção reproduzido nas duas chapas de `Cozinha v3.FCStd` não era
falta de configuração do operador. O complemento de tabs compartilhadas
considerava somente o centro de cada aresta comum, mesmo quando essa aresta
possuía centenas de milímetros disponíveis. Além disso, a busca desistia quando
uma primeira inserção ainda não reduzia sozinha o maior vão, embora duas
inserções sucessivas resolvessem a região.

Uma aresta comum longa agora oferece vários centros candidatos, respeita a
largura da tab, a margem dos cantos e o espaçamento mínimo centralizado em
`RetentionRules`. Os intervalos escolhidos são divididos uma única vez em
trechos de corte e retenção, mantendo a garantia de que cada aresta física seja
usinada uma vez por profundidade. A busca continua até satisfazer quantidade e
vão máximo; ela não exige que cada tab intermediária produza uma melhoria
isolada. Tabs exclusivas criadas pelo fallback também são normalizadas quando
ficam concentradas no mesmo canto, permitindo que a rede comum complete a
retenção de forma equilibrada.

Restos pequenos deixaram de herdar rigidamente a largura horizontal usada nas
peças úteis. Quando existem duas bordas vetoriais distintas, a largura é
reduzida somente até o que cabe nessas bordas; a altura continua sendo toda a
espessura original do material e essas retenções não entram na liberação
automática das peças. Portanto um recorte pequeno não bloqueia o trabalho nem
exige que o operador descubra manualmente uma largura especial.

O plano real foi gerado com as preferências já configuradas do usuário: linha
comum, quatro tabs mínimas, largura de 10 mm, altura de 14 mm, fresa de 4 mm,
fixação de restos por tabs e liberação automática por peça. A primeira chapa,
com 28 peças, ficou válida com vão máximo de 297,0 mm; a segunda, com 22 peças,
ficou válida com vão máximo de 299,31 mm. Ambas incluíram retenção dos restos e
operações de liberação sem a antiga mensagem bloqueante.

Foram aprovados **346 testes puros** (dois opcionais ignorados), **63 testes Qt
offscreen**, **145 testes gerais** e os **13 smokes FreeCADCmd**. As regressões
novas cobrem múltiplas tabs numa mesma aresta compartilhada de 1000 mm e um
resto interno de 5 × 5 mm com duas tabs adaptativas de altura integral.

### Altura física independente e densidade proporcional — 21 de agosto de 2026

A validação anterior interpretava `Profundidade inicial Z` como prova de que
toda a camada já havia sido removida, inclusive nas futuras pontes. Assim, uma
operação iniciada em 6 mm bloqueava uma tab de 14 mm numa chapa de 15 mm, apesar
de o percurso conseguir subir localmente até Z −1 mm e preservar a ponte desde
a primeira passagem. A altura física explícita agora prevalece nos intervalos
de tab: a profundidade inicial continua definindo o começo dos trechos normais,
mas não reduz nem invalida material que o próprio percurso deve deixar intacto.
Uma tab de 15 mm permanece 0,2 mm acima da face, inclusive com sobrecorte.

A quantidade mostrada como `Quantidade automática` era igualmente ambígua:
ela representa um mínimo solicitado, enquanto as regras mecânicas podem elevar
o total. O rótulo passou a ser `Mínimo automático por peça`, com explicação
direta de que nunca são usadas menos de quatro retenções. A densidade agora
considera a resistência física da ponte. Abaixo de 75% da espessura continuam
valendo no máximo 300 mm de perímetro entre centros; a partir de 75%, o limite
centralizado em `RetentionRules` passa a 500 mm. Isso reduz tabs desnecessárias
quando quase toda a espessura do MDF permanece intacta sem afrouxar a regra das
pontes baixas.

No ensaio de uma tira de 1000 × 50 mm, largura de tab de 10 mm e mínimo quatro,
a tab baixa continuou exigindo oito regiões sob a regra conservadora; com 14 mm
intactos em MDF de 15 mm, o plano usou cinco regiões equilibradas e manteve o
maior vão abaixo de 500 mm. O smoke da interface valida diretamente início em
6 mm com tab de 15 mm em material de 15 mm, em português e inglês.

Foram aprovados **347 testes puros** (dois opcionais ignorados), **63 testes Qt
offscreen**, **148 testes gerais**, compilação dos módulos alterados e os **13
smokes FreeCADCmd**.

### Ativação intuitiva da retenção global — 21 de agosto de 2026

As escolhas `Remover tabs automaticamente ao final` e `Restos soltos`
dependem tecnicamente do plano global de Linha comum, mas a interface exigia
que o operador descobrisse e ativasse primeiro essa opção em outro grupo. Com
Linha comum desligada, os controles apareciam cinza; preferências históricas
podiam ainda restaurar uma escolha marcada e inacessível.

Os dois controles principais permanecem agora acessíveis. Marcar a remoção ou
escolher Tabs/Parafusos para restos por uma ação do operador ativa Linha comum
automaticamente e libera os campos dependentes. Desligar Linha comum
explicitamente limpa a remoção e volta a fixação de restos para Desativada,
evitando que a tela mostre uma configuração que não será executada. Na
reabertura, combinações inválidas gravadas por versões anteriores também são
normalizadas; configurações válidas continuam preservadas.

O smoke completo da interface e o smoke específico de linha comum, última
passada, tabs, restos, parafusos e assento foram aprovados no FreeCADCmd. A
bateria permaneceu com **347 testes puros** (dois opcionais ignorados), **63
testes Qt offscreen** e **148 testes gerais** aprovados.

### Tabs físicas equilibradas na rede comum — 21 de agosto de 2026

Foi reproduzida a concentração mostrada na prévia 3D. O seletor anterior
preenchia primeiro os trechos exclusivos de cada peça e somente depois tentava
completar a estabilidade nas linhas compartilhadas. Numa rede sintética de
três peças de 100 × 1000 mm, tab de 7 mm e altura física de 6 mm, isso gerava
32 tabs físicas: 24 `StockTabs` e oito `SharedTabs`. A peça central recebia 16
retenções apesar de precisar de oito.

A seleção automática passou a tratar `StockTabs` e `SharedTabs` no mesmo
problema físico. Cada peça recebe alvos igualmente espaçados no seu perímetro;
uma candidata compartilhada satisfaz simultaneamente um alvo de cada vizinha,
mas não pode consumir um alvo do lado oposto. A margem de quina combina largura
da tab, diâmetro da ferramenta e um mínimo centralizado em `RetentionRules`.
Peças compridas recebem pares nos dois lados longos. Cada componente ligado
por SharedTabs mantém ainda duas ancoragens ao stock quando a geometria
comporta, impedindo que toda a rede se transforme numa ilha articulada.

No caso de duas peças de 453 × 35 mm, o total caiu de nove para seis tabs
físicas: duas compartilhadas e quatro exclusivas, exatamente quatro retenções
por peça, todas nos lados longos e a mais de 50 mm das quinas. Na rede de três
peças, a configuração conservadora de 6 mm caiu de 32 para 19 tabs, com vão
máximo de 292,1 mm. Com 14 mm intactos, foram usadas 13. Uma grade de 50 peças
foi resolvida com 115 tabs físicas, todas as peças válidas e sem duplicação de
segmento.

A prévia passou a persistir a geometria de cada tab no resumo do plano e a
desenhá-la uma única vez acima do material. `StockTabs` aparecem em verde,
`SharedTabs` em azul-claro e `WasteTabs` em laranja, agrupadas na árvore com a
contagem física. As linhas amarelas de entrada, subida e descida continuam
representando movimentos por profundidade, mas deixam de ser a única indicação
visual das pontes.

Foram aprovados **349 testes puros** (dois opcionais ignorados), **63 testes Qt
offscreen**, **148 testes gerais**, os **13 smokes FreeCADCmd**, compilação dos
módulos alterados e `git diff --check`. As regressões novas cobrem duas peças
estreitas adjacentes, a rede de três tiras, serialização do resumo físico e a
prévia traduzida em português/inglês.

### Auditoria das tabs no arquivo real e diferenciação visual — 21 de agosto de 2026

As capturas seguintes foram confrontadas diretamente com `Cozinha v3.FCStd`,
sem salvar nem modificar o arquivo de origem. O documento continha duas
operações de Corte anteriores ao seletor físico atual. Cada uma guardava 246
intervalos de tab, altura intacta de 1 mm e `Restos soltos: desativado`; a
segunda operação guardava ainda mínimo automático igual a um. Como operações
aplicadas são persistentes, o Editor reproduzia corretamente esses percursos
antigos e não os recalculava silenciosamente ao abrir o FCStd.

O seletor atual passou a impor também espaçamento circular relativo ao
perímetro da peça. Isso impede que alvos teoricamente opostos terminem
projetados no mesmo pequeno trecho; a tolerância relaxa em etapas somente
quando uma geometria curta não oferece outra solução. SharedTabs que ficam
redundantes depois de conectar toda a rede ao stock são removidas desde que
quantidade, vão máximo e conectividade permaneçam válidos. Para restos com
fixação por tabs, a validação final passou a rejeitar explicitamente qualquer
região com menos de duas WasteTabs.

O caso real foi então recalculado em memória com tab de 7 mm, 14 mm intactos,
mínimo quatro, fresa de 4 mm e restos por tabs. A chapa 1, com 28 peças, gerou
105 tabs físicas, incluindo 14 WasteTabs em sete restos. A chapa 2, com 22
peças, gerou 98, incluindo 12 WasteTabs em seis restos. Todas as 50 peças
passaram as regras de quantidade, vão livre e ligação ao stock; cada um dos 13
restos detectados recebeu exatamente duas pontes de altura integral.

As marcações físicas verde, azul-claro e laranja deixaram de existir somente
na prévia temporária. Uma operação nova ou atualizada grava três objetos leves
sob seu próprio grupo — um por tipo presente —, sempre com uma aresta por tab
física. Assim, linhas amarelas repetidas por passe não podem mais ser contadas
como tabs. Ao editar uma operação antiga cujo resumo não possui essa geometria,
a interface avisa que a distribuição é legada e que `Atualizar operação`
executará o recálculo atual, sem fazer migração destrutiva automática.

Foram aprovados **350 testes puros** (dois opcionais ignorados), **63 testes Qt
offscreen**, **148 testes gerais**, os **13 smokes FreeCADCmd**, compilação e
`git diff --check`. O smoke completo confirma também que as marcações físicas
persistidas possuem exatamente a mesma contagem do plano usado pelo G-code.

### Retenção de restos em layouts com várias chapas — 21 de agosto de 2026

O caso real `Cozinha v3.FCStd` revelou uma diferença entre a auditoria por
chapa e o fluxo normal da interface. O `VectorDocument` preservava corretamente
os dois limites produzidos pelo organizador em `organization_sheet_bounds`, mas
a ponte Editor → CAM convertia somente `work_area` em `stock_boundary`. Assim,
o detector raster de restos examinava a Chapa 01 e ignorava silenciosamente a
Chapa 02, mesmo com `Restos soltos: tabs` visivelmente selecionado.

O contrato puro de `build_global_cut_plan` passou a aceitar vários limites de
chapa, mantendo `stock_boundary` compatível para chamadas existentes. A ponte
agora copia o snapshot persistido pelo organizador para a configuração da
operação e processa cada chapa separadamente, sem inferir limites pela posição
dos vetores e sem alterar o `VectorDocument`. IDs de restos e parafusos
continuam globais e únicos no plano resultante.

A reprodução completa foi executada em memória com as 50 peças do arquivo,
linha comum, tab de 10 mm, altura intacta de 15 mm, mínimo quatro e fixação de
restos por tabs. O plano reconheceu as duas chapas, detectou 13 regiões soltas
— sete na primeira e seis na segunda — e criou 26 WasteTabs, exatamente duas
por região. As marcações alcançaram de X 489,5 até X 3465,5, comprovando que a
segunda chapa passou a participar do mesmo cálculo. O arquivo FCStd de origem
não foi salvo nem modificado.

Foram aprovados **351 testes puros** (dois opcionais ignorados), **63 testes Qt
offscreen**, **148 testes gerais** e os **13 smokes FreeCADCmd**. A regressão
nova cobre duas ilhas de desperdício em duas chapas distintas e o smoke da
interface verifica o encaminhamento dos dois limites até o plano físico.

### WasteTabs em lados mecanicamente úteis — 21 de agosto de 2026

As WasteTabs ainda eram escolhidas por ordem de segmento: o primeiro trecho
elegível recebia a primeira ponte e apenas a segunda tentava maximizar a
distância. Em encontros com várias peças, isso concentrava retenções nas
quinas e podia reduzir uma tab de 10 mm ao comprimento de um microsegmento,
mesmo quando o mesmo resto possuía duas bordas longas disponíveis.

O seletor passou a avaliar o par inteiro. A prioridade é agora: comportar a
largura completa com a folga de quina centralizada em `RetentionRules`, usar
duas bordas longas, preferir direções opostas/paralelas e maximizar a distância
entre os centros. Em uma aresta comprida, são avaliadas posições próximas aos
dois extremos úteis e o centro; isso evita alinhar duas retenções no mesmo
ponto longitudinal. Tabs adaptativas menores que o valor pedido continuam
existindo somente como último recurso para restos realmente pequenos.

O cenário sintético de um resto de 120 × 20 mm passou a usar duas tabs de 10
mm nos lados de 120 mm, com mais de 80 mm entre os centros, ignorando arestas
curtas apresentadas antes na lista. No recálculo completo de `Cozinha v3.FCStd`,
permaneceram 13 restos e 26 WasteTabs, porém todas as 26 conservaram os 10 mm
configurados; nenhuma mini tab foi necessária. O arquivo de origem permaneceu
inalterado.

Foram aprovados **352 testes puros** (dois opcionais ignorados), **63 testes Qt
offscreen**, **148 testes gerais**, os **13 smokes FreeCADCmd**, compilação e
`git diff --check`.

### Remoção suave de tabs em zig-zag — 21 de agosto de 2026

A liberação automática anterior fazia um mergulho vertical até a profundidade
final e, quando a tab era mais larga que a fresa, uma única varredura lateral.
Isso era geometricamente curto, mas aplicava esforço excessivo ao remover uma
tab de altura integral. A pausa supervisionada continuava protegendo o fluxo,
porém a trajetória não era suficientemente progressiva para uma peça já quase
solta.

Tabs mais largas que a ferramenta agora usam o trecho útil como rampa. A fresa
desce até o topo já aberto da ponte, atravessa para a outra extremidade enquanto
desce, retorna na travessia seguinte e repete até a profundidade final. O passo
vertical respeita tanto o stepdown da operação quanto um ângulo máximo de 12°;
o G-code usa `feed_ramp`, portanto recebe o avanço reduzido de rampa. A paridade
das travessias é escolhida para que o último esforço termine no endpoint que o
planejador já orienta para longe da peça. Em seguida há retração imediata para
Z seguro antes de qualquer deslocamento XY.

Quando a tab é igual/mais estreita que a fresa ou o curso disponível seria
curto demais para respeitar o passo mínimo operacional, não existe rampa
lateral útil. Somente nesses casos permanece o mergulho central, sem esforço
XY. Os três modos de supervisão e a regra de uma pausa por peça continuam
inalterados; WasteTabs de restos não entram na liberação automática.

No recálculo completo de `Cozinha v3.FCStd`, as 175 tabs liberáveis produziram
sequências válidas. Com tab de 10 mm e fresa de 4 mm, cada travessia percorreu
6 mm e desceu no máximo cerca de 1,275 mm até Z −15,5 mm. Foram mantidos 50
`M0`, um por peça, e nenhuma liberação terminou sem retração imediata. O FCStd
de origem não foi modificado.

Foram aprovados **352 testes puros** (dois opcionais ignorados), **63 testes Qt
offscreen**, **149 testes gerais**, os **13 smokes FreeCADCmd**, compilação e
`git diff --check`.

### Marcações abertas vinculadas às peças no nesting — 22 de agosto de 2026

Um caminho aberto inteiramente contido na área material de uma única peça
deixou de ser interpretado como contorno quebrado pelo organizador. Ele passa a
ser uma marcação pertencente à `Piece2D`, sem virar peça, furo ou limite de
nesting. A marcação acompanha translação, rotação, seleção e copiar/colar da
peça; a relação é persistida em `marking_path_ids`, sem duplicar nem redesenhar
o vetor original. Ao aplicar o nesting, uma relação `Piece2D` existente é
atualizada no mesmo comando/Undo, portanto arquivos reconhecidos antes desta
mudança não exigem uma segunda etapa manual de reconhecimento.

A associação é deliberadamente conservadora. Caminho fora das peças, dentro de
um recorte vazado, atravessando uma borda côncava, sobre uma fronteira
compartilhada ou contido por mais de uma peça continua bloqueando a organização.
Assim, o Editor não esconde um contorno realmente aberto nem escolhe um dono
arbitrário. A marcação permanece um vetor independente para uma operação
posterior de corte sobre a linha; o contrato CAM atual não a transforma
automaticamente em contorno fechado.

Durante o smoke completo foi corrigida também a validação de configurações
dormentes: com tabs desativadas, uma altura antiga gravada no campo não bloqueia
um corte comum nem uma rede sem pontes. Quando uma tab está ativa, a validação
física contra a espessura do material permanece obrigatória. O smoke específico
de linha comum passou ainda a definir MDF de 15 mm explicitamente, em vez de
herdar a última preferência do operador.

Foram aprovados **358 testes puros** (dois opcionais ignorados), **63 testes Qt
offscreen**, **151 testes gerais**, os **13 smokes FreeCADCmd**, compilação
integral e `git diff --check`.

### Escala percentual e contrato explícito de tabs/passadas — 23 de agosto de 2026

O painel `Posição e dimensões exatas` passou a oferecer `Escala uniforme (%)`.
O valor é aplicado ao conjunto inteiro em torno do centro visual e produz um
único comando/Undo; 100% conserva o tamanho, 50% reduz à metade e 200% dobra.
Essa transformação permanece no `EditorController`, portanto grupos e peças
classificadas continuam expandindo para seus vetores pelo fluxo de comandos,
sem estado geométrico paralelo na apresentação.

Na Linha comum, o modo manual de tabs agora é estritamente exclusivo. Contagem
automática e `Melhor fixação` não complementam cliques manuais. Um clique sobre
uma fronteira compartilhada é dividido no próprio segmento físico e produz uma
única `SharedTab` vinculada às duas peças, em vez de ser projetado para uma
borda externa distante. Com tabs desmarcadas, `tab_count` e `tab_positions` são
normalizados para zero/vazio no contrato persistido; com restos em
`desativado`, nenhum `WasteTab` é criado. A prévia de qualquer CAM 2D também é
invalidada e removida ao alterar esses controles, eliminando a aparência
enganosa de tabs antigas ainda ativas.

A ordem de profundidades ganhou nomes operacionais inequívocos:
`Última passada no final (ex.: 2 + última geral)` mantém o ciclo N-1 por peça e
uma camada final pela chapa; `Todas as passadas direto por peça (ex.: 3 direto)`
conclui todos os Z de uma peça antes da próxima. A escolha deixa de ser trocada
silenciosamente ao desligar/religar Linha comum. A antiga caixa de acabamento
lateral foi renomeada para `Passada de acabamento com sobre-metal`, deixando
claro que ela não controla a ordem vertical.

Os casos Dogbone e T-bone foram reproduzidos numa rede com fronteira comum. Em
ambos, a tesselação dos arcos permaneceu dentro do mesmo trail e não criou
entradas/fins adicionais: o plano teve apenas a SharedEdge e um restante físico
por peça em cada profundidade. Foram aprovados **365 testes puros** (dois
opcionais ignorados), **64 testes Qt**, **151 testes gerais**, os **13 smokes
FreeCADCmd**, compilação integral e `git diff --check`.

### Preservação integral de T-bone/Dogbone na Linha comum — 23 de agosto de 2026

O teste anterior de alívios usava tolerância de 0,02 mm e conferia somente a
quantidade de operações. A reprodução no arquivo de recuperação real revelou
o caso que faltava: com tolerância de Linha comum em 0,2 mm, fresa de 4 mm e
alívio de raio 3 mm, a compensação externa gera acordes de aproximadamente
0,156 mm na meia-volta interna. O planejador reutilizava a tolerância de
comparação entre peças como comprimento mínimo e grade topológica; esses
acordes eram descartados e o contorno acabava fragmentado. A simulação levava
a fresa até o alívio, mas a meia-volta ausente nunca poderia ser executada.

Foram separados três conceitos: tolerância configurável para reconhecer
fronteiras candidatas, coincidência física limitada a 0,001 mm e epsilon
numérico de preservação do vetor. Um segmento curto continua participando das
relações de cruzamento/contato, os parâmetros 0 e 1 de sua aresta nunca são
fundidos, e as chaves do grafo não usam mais uma grade de 0,2 mm. A checagem de
auto-interseção também usa coincidência física, evitando classificar acordes
vizinhos da mesma curva como um falso toque.

No caso real recuperado, o recálculo passou de 18 operações fragmentadas e 184
segmentos persistidos para 9 operações contínuas e 258 segmentos físicos. A
auditoria dos movimentos confirmou 956 movimentos, três perfis fechados nas
três profundidades e cobertura de exatamente uma volta por perfil, sem trecho
omitido ou repetido. O FCStd e o `VectorDocument` de origem permaneceram
intactos.

Foram aprovados **366 testes puros** (dois opcionais ignorados), **64 testes Qt
offscreen**, **151 testes gerais**, os **13 smokes FreeCADCmd** e a compilação
integral.

### Todas as passadas direto com a mesma rota ida/volta — 23 de agosto de 2026

A opção `Todas as passadas direto por peça (ex.: 3 direto)` estava apresentada
como equivalente ao ciclo otimizado sem volta final, mas ainda enviava o valor
legado `per_piece`. Esse scheduler reiniciava cada trail em cada profundidade e
contradizia o texto da interface. Foi criado o modo explícito
`piece_bidirectional`: ele reutiliza `_schedule_piece_bidirectional_depths` com
**todas** as profundidades, mantém cada peça numa única visita, alterna os
endpoints das trilhas abertas e não cria `final_sheet_pass`. O modo
`hybrid_piece_bidirectional` continua sendo a opção distinta `N-1 + última
geral`. Trabalhos antigos que gravaram `per_piece` a partir do mesmo controle
visual são migrados ao reabrir, sem alterar geometrias ou operações já
persistidas.

No caso real recuperado que originou a captura, com Z −6, −12 e −16 mm, o
scheduler legado produzia 9 entradas de corte. `piece_bidirectional` produziu
6 operações físicas e somente 2 entradas seguras, uma por peça; a sequência
foi `6 → 12 → 16` na primeira peça e depois `6 → 12 → 16` na segunda. A opção
com última geral permaneceu com a terceira entrada própria da fase final.

O adapter global também deixou de tratar `Suave`, `Zigue-zague` e `Espiral`
como meros rótulos. Em perfil fechado, a espiral solicitada
é construída como círculo tangente e só é aceita depois de validar que todos
os seus pontos ficam no lado sacrificial do contorno e mantêm distância das
outras linhas físicas considerando o diâmetro da ferramenta. Se essa prova
falhar, o movimento registra fallback e permanece no kerf; em situação segura,
a simulação e o G-code recebem efetivamente os segmentos helicoidais escolhidos.

Foram aprovados **369 testes puros** (dois opcionais ignorados), **64 testes Qt
offscreen**, **151 testes gerais**, o smoke completo da interface e a reprodução
FreeCADCmd do caso real.

### Navegação das operações na lateral esquerda — 23 de agosto de 2026

A barra de operações deixou de consumir uma faixa horizontal e de recorrer a
setas de overflow. O mesmo `QTabWidget` continua sendo a fonte única de
seleção, ícones, nomes traduzidos, atalhos e páginas; somente a posição e a
apresentação foram alteradas. As abas agora formam uma coluna fixa na
extremidade esquerda e um `QTabBar` especializado desenha os nomes na
horizontal, pois o comportamento `West` padrão do Qt os giraria em 90 graus.

A lateral termina visualmente depois de `Simulação e Salvar`; a área vazia até
o rodapé não prolonga a coluna de abas e usa somente o fundo neutro comum da
janela. Esse fundo é deliberadamente opaco: o modo independente não revela o
papel de parede, a vista 3D nem pixels sem pintura nas margens e no rodapé. O
painel de conteúdo recebeu borda e separação de 10 px. O botão que destaca
somente o Editor 2D foi colocado na
própria linha `Editor 2D`, com posicionamento calculado pela geometria
horizontal real — o layout nativo de botões em abas `West` pressupõe texto
girado e sobrepunha o rótulo.

Acima de `Trabalho` há agora uma ação distinta para alternar a mesma instância
do WoodCAM entre painel-filho do FreeCAD e janela `Qt.Window` independente. O
ciclo salva/restaura tamanho e posição do modo preso, preserva página ativa,
campos e a sessão real do Editor 2D e não tenta impor posição à janela nativa:
em Wayland/Niri essa decisão pertence ao compositor. Ao prender novamente, a
posição é limitada à área atual do FreeCAD. Nenhuma página, configuração CAM,
índice de operação ou funcionalidade foi recriada.

A regressão FreeCAD/Qt confirma a posição oeste, o texto horizontal, a ordem das
páginas, a tradução repetida Português/English, o ciclo completo
prender–soltar–prender do WoodCAM e o funcionamento de destacar e reatar o
Editor 2D. Foram aprovados **369 testes puros** (dois opcionais
ignorados), **64 testes Qt offscreen**, **151 testes gerais**, os **13 smokes
FreeCADCmd**, compilação e `git diff --check`.

### Rampa Suave separada do Zigue-zague — 23 de agosto de 2026

A reprodução confirmou que a interface enviava corretamente `smooth`, mas o
adapter da Linha comum convertia tanto `smooth` quanto `zigzag` no mesmo
movimento físico de ida e volta. Apenas o metadado era diferente; por isso a
simulação mostrava corretamente que a opção Suave se comportava como
Zigue-zague.

Os contratos agora são distintos. `Suave` posiciona a ferramenta adiante no
próprio trail e executa **uma única inclinação contínua** que termina no ponto
inicial do corte e na profundidade alvo. Na primeira camada, o posicionamento
XY ocorre em Z seguro e a fresa desce somente até a superfície antes da rampa.
Nas camadas seguintes, a ida até a origem da nova rampa permanece exatamente
no kerf aberto pela profundidade anterior; ela é identificada separadamente e
não pode ser confundida com corredor de corte da passada final. `Zigue-zague`
conserva explicitamente a descida de ida e volta. O G-code continua usando o
avanço reduzido de rampa nos dois casos.

O teste do domínio confere origem, fim, Z monotônico e geometria da inclinação,
além de manter uma regressão independente para o ida/volta do Zigue-zague. O
smoke FreeCAD/Qt seleciona literalmente o primeiro botão da interface, coleta
as configurações, monta o plano global e recusa qualquer movimento
`physical_trail_out_and_back` nessa rampa. Foram aprovados **369 testes puros**
(dois opcionais ignorados), **64 testes Qt offscreen**, **151 testes gerais**,
os **13 smokes FreeCADCmd**, compilação e `git diff --check`.

### Pan livre depois de enquadrar — 23 de agosto de 2026

O `sceneRect` justo da geometria limitava os scrollbars do `QGraphicsView`.
Assim, `Enquadrar` deixava de ser apenas uma posição inicial e se tornava uma
barreira: mesmo repetindo o pan, a chapa permanecia presa dentro do canvas.

Durante o pan, a vista agora amplia somente sua região navegável ao redor do
viewport atual. Nenhum vetor, `WorkArea`, comando ou histórico é alterado; a
grade continua sendo desenhada apenas na região visível. A ampliação prossegue
conforme o operador navega, permitindo retirar completamente a chapa da tela.
`F`/`Enquadrar` recalcula novamente o retângulo justo da geometria e restaura a
vista centralizada.

A regressão Qt executa três arrastes reais com o botão do meio, exige que toda
a área de trabalho saia do viewport, confirma documento e revisão invariáveis
e então verifica que `Enquadrar` a traz integralmente de volta.

Foram aprovados **369 testes puros** (dois opcionais ignorados), **65 testes Qt
offscreen**, **151 testes gerais**, os **13 smokes FreeCADCmd**, compilação e
`git diff --check`.

### Ações produtivas na barra do Editor e estado da exportação — 23 de agosto de 2026

As ações que iniciam o fluxo produtivo deixaram de depender exclusivamente dos
menus superiores. A barra vertical do Editor 2D agora oferece, depois das
ferramentas de criação, comandos diretos para `Medir / inspecionar`,
`Reconhecer peças e furos` e `Organizar inteligente`. Eles reutilizam os mesmos
sinais e casos de uso já existentes: não há uma segunda implementação de
medição, classificação ou nesting. Os perfis Rápido e Profundo continuam no
menu `Peças` como escolhas avançadas, enquanto a barra apresenta somente o
fluxo recomendado.

Os ícones programáticos permanecem vetoriais no Qt e independentes de arquivos
raster. O conjunto recebeu cores funcionais discretas — azul/ciano para desenho,
violeta para nós e curvas, verde para inspeção, âmbar para nesting e vermelho
para exclusão — preservando contraste, transparência e nitidez em HiDPI. Os
novos textos e dicas participam do mesmo catálogo Português/English.

Na página `Simulação e Salvar`, editar somente o caminho/nome do arquivo G-code
agora recalcula imediatamente a disponibilidade da linha de ações. O término da
simulação também restaura `Gerar G-code`; antes, o método atualizava Aplicar,
Pré-visualizar e Simular, mas esquecia esse botão, e uma troca de aba acabava
funcionando como correção acidental. Durante uma simulação real o comando de
exportação continua corretamente bloqueado.

A regressão Qt verifica os ícones e exige que cada botão lateral ative o mesmo
modo/sinal de produção. O smoke FreeCAD/Qt altera o nome de saída na página de
exportação, confere que o botão permanece ativo e cobre a transição
simulação ativa → concluída. Foram aprovados **369 testes puros** (dois
opcionais ignorados), **65 testes Qt offscreen**, **151 testes gerais**, os
**13 smokes FreeCADCmd**, compilação e `git diff --check`.

### Grade independente, réguas ativas e fluidez do canvas — 23 de agosto de 2026

A reprodução com 500 vetores confirmou duas causas independentes para a
sensação de salto/lentidão. A criação de vetores ainda tinha o snap de grade
ativo quando o Imã era desmarcado, e cada movimento consultava a geometria de
todos os vetores visíveis. Ao mesmo tempo, os dois indicadores de régua eram
redesenhados integralmente a cada posição do mouse e a grade não utilizava o
cache próprio do `QGraphicsView`.

`Grade` tornou-se um controle explícito ao lado de `Imã (Snap)`. Desmarcá-lo
oculta quadrículas e o preenchimento azul da área de trabalho, entrega fundo
branco e desliga somente o encaixe da grade. O Imã passa a controlar apenas
pontas, centros, interseções, meios e geometria; os dois estados podem ser
combinados livremente. Essa separação é somente de apresentação/snap e não
cria nem altera entidades no `VectorDocument`.

As réguas horizontal e vertical agora mostram uma seta azul exatamente no X e
Y cartesianos do cursor. Somente a pequena faixa ocupada pela posição antiga e
nova é invalidada; cada régua também responde apenas ao seu próprio eixo de
rolagem. A grade usa cache de fundo e traço cosmético sem antialiasing. A busca
de snap descarta conservadoramente entidades cujos limites estão fora do raio
de captura em pixels antes de consultar spans, sem mudar prioridade,
tolerância ou resultado geométrico.

No probe offscreen com 500 vetores e 180 movimentos de desenho, o tempo caiu de
**1,91 s para 0,43 s** (aproximadamente 4,4×). Em 120 movimentos de pan, caiu
de **0,73 s para 0,28 s** (aproximadamente 2,6×). Foram aprovados **370 testes
puros** (dois opcionais ignorados), **66 testes Qt offscreen**, **151 testes
gerais**, os **13 smokes FreeCADCmd**, compilação integral e
`git diff --check`.

### Chapas locais, seções recolhíveis e segundo perfil de fluidez — 23 de agosto de 2026

As folhas geradas pela organização já existiam em
`organization_sheet_bounds`, mas eram somente uma representação visual. O
painel lateral agora apresenta `Chapas`, permite selecionar e enquadrar cada
folha e usa o canto inferior esquerdo da folha ativa como X0/Y0 local nas
réguas, no cursor, na grade, no snap e nas propriedades exatas. Essa conversão
é estritamente de apresentação: o `VectorDocument` continua guardando as
coordenadas globais e o comando numérico soma a origem ativa somente no
momento de formar a mutação com Undo. Nenhum vetor, peça ou arquivo de origem
é deslocado ao trocar de chapa.

`Camadas`, `Peças`, propriedades exatas, transformação e parâmetros passaram
a ter uma seta de recolher/expandir. O recolhimento apenas recorta a área
visual do grupo, preservando valores, seleção e revisão do documento.

Um segundo perfil do movimento do mouse encontrou **82.500 construções de
caixa geométrica** em 165 eventos sobre 500 vetores. Como as entidades do
domínio são imutáveis e substituídas a cada comando, o mecanismo de snap agora
mantém um cache por ID e identidade da entidade, invalidando naturalmente uma
geometria substituída. O mesmo probe de 180 movimentos passou de **1,91 s na
primeira reprodução**, para **0,43 s após a filtragem espacial**, e finalmente
para aproximadamente **0,27 s com o cache**, sem limitar a taxa dos eventos ou
alterar precisão e prioridade do snap.

Foram aprovados **371 testes puros** (dois opcionais ignorados), **68 testes Qt
offscreen**, **151 testes gerais**, os **13 smokes FreeCADCmd**, compilação
integral e `git diff --check`.

### Latência do documento vazio e controles de seção visíveis — 23 de agosto de 2026

O perfil anterior cobria um desenho carregado e não representava o atraso
relatado antes de existir qualquer vetor. A reprodução sobre o widget completo
e vazio identificou **1.200 pinturas de régua em apenas 600 movimentos de
mouse**: cada pacote redesenhava imediatamente os dois indicadores, mesmo sem
desenho, seleção ou snap ativo. No probe vazio isso consumia **0,267 s** e a
grade ligada ou desligada produzia o mesmo custo.

O feedback visual de régua e texto X/Y passou a agrupar pacotes e atualizar no
máximo a cada **8 ms**. O campo X/Y também ganhou largura fixa, impedindo que a
variação dos dígitos renegocie o layout completo. No mesmo probe, 600
movimentos caíram para **0,031 s** fora do FreeCAD e **0,025 s dentro do
FreeCADCmd**, com apenas três atualizações visuais e nenhuma mutação no
documento. A política das prévias de construção é detalhada na medição
seguinte.

As setas nativas cinzas dos grupos foram substituídas por botões de 27 × 22 px,
com fundo azul, hover contrastante e chevrons `▼`/`▶`. Ao recolher, os widgets
do corpo são realmente ocultados e restaurados no expandir, preservando seus
valores e reduzindo trabalho de layout/pintura.

Foi acrescentado um smoke permanente de responsividade do documento vazio.
Foram aprovados **371 testes puros** (dois opcionais ignorados), **69 testes Qt
offscreen**, **151 testes gerais** e os **14 smokes FreeCADCmd**.

### Pan e prévias de construção em cadência de quadro — 23 de agosto de 2026

O pan com botão do meio e o dimensionamento de um retângulo foram perfilados
como gestos completos, e não inferidos a partir do hover. Em 600 pacotes, o pan
chamava `_ensure_free_pan_extent` **601 vezes** e escrevia separadamente nas
duas barras de rolagem em cada pacote. A expansão comparava retângulos que se
moviam juntos e, por isso, aumentava `sceneRect` e recentralizava a câmera quase
pixel por pixel. O probe consumia **0,176 s**.

O canvas agora acumula deslocamentos por até 8 ms e os aplica de uma vez. A
área navegável cresce em blocos de oito viewports e só quando resta menos de
uma viewport de margem. No FreeCADCmd, os mesmos 600 movimentos passaram para
**0,0095 s**.

A prévia do retângulo consumia **0,408 s** porque caminho, snap e balão eram
recalculados/pintados muito acima da cadência do monitor. Linha, polilinha,
retângulo, círculo, elipse, arco, Bézier, polígono e estrela agora apresentam
o primeiro movimento imediatamente e agrupam apenas os pacotes excedentes do
mesmo quadro. O clique de confirmação recalcula e grava a coordenada exata.
Seleção, reparos e modificadores topológicos continuam recebendo hover
imediato, pois o candidato sob o mouse pode ser consumido pelo clique seguinte.
O retângulo caiu para **0,0239 s** no smoke FreeCADCmd, que também compara os
limites gravados com as coordenadas exatas do primeiro e do segundo clique.

O balão de medida passou a guardar seus limites de texto, evitando quatro
medições de fonte para cada pintura. Foram aprovados **371 testes puros** (dois
opcionais ignorados), **70 testes Qt offscreen**, **151 testes gerais** e os
**14 smokes FreeCADCmd**.

### Organização limitada à seleção — 23 de agosto de 2026

Os comandos de organização rápida, inteligente e profunda classificavam o
documento inteiro e enviavam todas as peças ao worker, mesmo quando o operador
mantinha uma seleção explícita. O escopo agora é formado antes de iniciar a
busca. Selecionar o contorno externo, um descendente, rebaixo, marcação ou um
grupo inclui a peça rígida completa; vetores e peças não selecionados não
entram na busca, na prévia, no comando de substituição nem na atualização de
metadados. Caminhos abertos e camadas bloqueadas fora do escopo também não
bloqueiam a operação. Sem seleção, permanece o comportamento compatível de
organizar todas as peças reconhecíveis.

O smoke da interface cobre prévia, aplicação, Undo e Redo e verifica que uma
segunda peça não selecionada permanece idêntica. Foram aprovados **372 testes
puros** (dois opcionais ignorados), **70 testes Qt offscreen**, **151 testes
gerais**, os **14 smokes FreeCADCmd**, compilação integral e
`git diff --check`.

### Zero local visível e automático por chapa — 23 de agosto de 2026

O painel `Chapas` já convertia régua, cursor, snap e propriedades para a origem
local da folha ativa, mas uma folha vazia só se tornava ativa depois de escolhida
na lista ou de selecionar um vetor dentro dela. Ao entrar em outra folha para
começar um desenho, o cursor podia portanto continuar mostrando o datum da
folha anterior. Sem seleção ativa, o movimento do cursor agora identifica a
folha sob ele e ativa seu canto inferior esquerdo como `X0 Y0` antes do próximo
clique. Isso é somente estado de apresentação: não cria comando, não desloca
vetores e não altera a revisão do `VectorDocument`.

Cada limite de chapa e cada folha da prévia também exibe explicitamente
`X0 Y0` ao lado do nome, com tradução correspondente em inglês. Foram aprovados
**372 testes puros** (dois opcionais ignorados), **71 testes Qt offscreen**,
**151 testes gerais** e os **14 smokes FreeCADCmd**, incluindo o smoke de
responsividade do editor vazio.

### Gerenciador persistente de operações e exportação — 24 de agosto de 2026

A lista `Percursos aplicados` da página `Simulação e Salvar` deixou de ser
somente uma seleção para a simulação. Ela agora oferece `Renomear`, `Editar` e
`Excluir`, com as mutações persistentes protegidas por transações e Undo do
FreeCAD. `Editar` carrega os parâmetros já gravados e apenas troca a interface
para a aba CAM correspondente; o percurso só é recalculado quando o operador
confirma `Atualizar operação`.

A lista aceita arraste vertical. O resultado é salvo em
`WoodCAMGlobalOrder`, sem alterar `WoodCAMSequence` nem a numeração histórica
por tipo. A enumeração da tela, a simulação múltipla e a concatenação do arquivo
único passam pelo mesmo ordenamento. Arquivos antigos continuam usando a ordem
histórica até o primeiro arraste, quando todas as operações recebem a ordem
global numa única transação. Dentro da página de exportação, seleção vazia
significa exportar todas as operações na ordem mostrada, evitando recalcular
silenciosamente a última aba CAM.

O botão `Gerar G-code` foi retirado do rodapé global e colocado na seção
`Exportação`. O seletor de destino ganhou ícone de pasta e tooltip explícito.
Os novos textos e dicas participam do catálogo português/inglês.

O smoke completo da interface cria duas operações de tipos diferentes,
reordena pelo próprio modelo Qt, confere a ordem consumida pela exportação,
renomeia, abre para edição, exclui e restaura com Undo. Também valida a nova
posição do botão, o ícone de pasta e a tradução dos controles. Foram aprovados
**379 testes puros** (dois opcionais ignorados), **73 testes Qt offscreen**,
**152 testes gerais**, os **14 smokes FreeCADCmd**, compilação dos módulos
alterados e `git diff --check`.

### Ponte vetorial de impressão para o TechDraw — 26 de agosto de 2026

O menu `Arquivo` do Editor 2D ganhou `Enviar para impressão (TechDraw)…`. O
assistente pergunta se deve usar somente a chapa ativa ou todas as chapas,
sugere o menor papel ISO entre A4 e A0 que comporta o escopo em 1:1 quando isso
é fisicamente possível e oferece três decisões explícitas: chapa inteira em
uma página, tamanho real 1:1 ou escala percentual. A orientação do conteúdo é
escolhida automaticamente. Uma escala explícita que cortaria a chapa é
recusada com a indicação da maior porcentagem possível, sem redução silenciosa.

A ponte gera uma página `TechDraw::DrawPage` por chapa com modelo ISO vazio e
um `TechDraw::DrawViewSymbol` vetorial. O SVG tem exatamente os limites físicos
da folha, conserva furos, recortes, curvas, cores de camada e, se solicitado, o
percurso CAM que já estava visível. Grade, seleção, nós e outras ajudas
transitórias de edição não entram na impressão. O retângulo azul-claro da chapa
é apresentação dentro do símbolo: nenhum `Part::Feature`, Shape ou entidade é
adicionado ao `VectorDocument`, portanto ele não pode virar acidentalmente um
contorno de corte.

Cada página registra UUID, revisão, índice da chapa, papel, escala e rotação
de origem para auditoria, mas permanece uma fotografia vetorial independente.
O smoke FreeCADCmd cria duas páginas, verifica a separação do conteúdo por
chapa, salva/reabre o FCStd e confirma novamente a ausência de geometria CAM.
Foram aprovados **385 testes puros** (dois opcionais ignorados), **79 testes Qt
offscreen**, **152 testes gerais** e **15 smokes FreeCADCmd**.

### Detalhes externos menores que a fresa — 26 de agosto de 2026

A compensação externa podia fechar uma fenda profunda mais estreita que a
ferramenta e substituir silenciosamente esse trecho por uma passagem reta. O
CAM agora compara o contorno de origem com seu percurso compensado antes de
calcular o corte. Quando encontra material inacessível, interrompe a prévia e
mostra diâmetro, quantidade de peças afetadas e a primeira coordenada de risco.

O operador pode cancelar, que continua sendo a decisão padrão, ou autorizar
conscientemente a perda dimensional. A autorização conserva a compensação do
contorno externo e acrescenta um percurso auxiliar apenas na região colapsada.
Uma fenda aberta usa seu eixo médio local, inclusive quando afunilada ou com
degraus, produzindo a menor abertura fisicamente possível com aquela fresa sem
encolher toda a peça. Regiões que não podem ser interpretadas com segurança
como uma única fenda usam somente o trecho local inacessível. O mesmo contrato alimenta o planejador
global de Linha comum, sem desativar tabs ou ignorar suas validações topológicas.
A confirmação é reutilizada entre Pré-visualizar e Aplicar apenas enquanto
geometria, ferramenta e pontos de risco permanecerem idênticos.

A regressão final aprovou **385 testes puros** (dois opcionais ignorados), **79
testes Qt offscreen**, **156 testes gerais** e os **15 smokes FreeCADCmd**. A
cobertura geométrica verifica fendas retas, escalonadas, afuniladas, rotacionadas
e com orientação invertida. O smoke da interface cobre tanto o CAM normal
quanto o plano global de Linha comum com uma fenda escalonada que termina em
2 mm e fresa de 4 mm.

### Reparo de contorno DXF fragmentado sem perder detalhe curto — 27 de agosto de 2026

Foi reproduzido o contorno mostrado pelo operador diretamente no arquivo
`base lateral.dxf`. O importador o recebia como 13 caminhos abertos. Depois da
união, as extremidades finais ficavam separadas por apenas cerca de
0,000000329 mm; fechar com uma reta acrescentava uma aresta microscópica. Ao
mesmo tempo, o desenho possui um span legítimo de aproximadamente 0,198035 mm,
que não pode ser apagado só porque a tolerância de procura entre pontas está em
0,2 mm. Essa combinação explicava os diagnósticos simultâneos de ramificação,
caminho aberto e segmento nulo/curto.

`Fechar caminho / unir próximas` agora aceita um único fragmento como semente,
descobre transitivamente os demais caminhos abertos conectados por extremidades
na mesma camada e prepara a cadeia completa. Se o resultado forma uma área
fechada e resta somente a folga numérica, as extremidades convergem ao ponto
médio, preservando os IDs dos spans existentes e sem criar uma nova aresta
degenerada. Cadeias colineares ou sem área não são fechadas por engano. A
operação continua preview-first, altera o `VectorDocument` somente ao aplicar e
entra como uma única transação no Undo do FreeCAD.

A regressão reproduz quatro fragmentos com folga final de 0,0000003 mm e um
detalhe real de 0,198 mm. Confirma contorno fechado, ausência de
`BRANCH_NODE`, `OPEN_PATH` e `ZERO_LENGTH_SPAN`, preserva o detalhe e restaura
os quatro vetores com Undo. Foram aprovados **387 testes puros** (dois
opcionais ignorados), **79 testes Qt offscreen**, **156 testes gerais** e os
**15 smokes FreeCADCmd**, além de compilação e `git diff --check`.

### Profundidade inicial e final como cotas absolutas — 27 de agosto de 2026

Os grupos de profundidade de Corte, Furo e Preenchimento mostravam
`Profundidade inicial Z` ao lado de `Profundidade de corte`, mas tratavam o
segundo valor como incremento. Assim, início 6,3 mm e corte 7 mm geravam uma
cota final de 13,3 mm e o contador calculava todas as passagens dos 7 mm, apesar
de o operador querer continuar a usinagem somente até Z −7 mm.

O segundo campo agora se chama `Profundidade final Z` e representa uma cota
absoluta desde a face original do material. O intervalo efetivo é
`final - inicial`; com início 6,3 mm, final 7 mm e stepdown 0,4 mm, a interface
mostra duas passagens e o planejador gera 6,7 e 7 mm. Alterar qualquer uma das
duas cotas invalida uma programação manual anterior, evitando reutilizar
incrementos incompatíveis. O editor de passagens informa cota inicial, cota
final, material restante e exibe cada chegada em Z absoluto.

Novas operações persistem `depth_input_mode = absolute_final`. Ao editar uma
operação antiga sem esse marcador, a interface usa seu `final_depth` gravado
em vez do antigo incremento `cut_depth`; portanto o percurso histórico não
muda silenciosamente. A validação bloqueia final menor ou igual ao início, e o
rebaixo de cabeça na aba Furo passa a ser limitado pela profundidade realmente
restante.

A regressão cobre as três abas 2D, o intervalo 6,3 → 7 mm, as chegadas em
6,7/7 mm, a invalidação de final igual ao início, o limite do rebaixo e a
abertura compatível de uma operação antiga. Foram aprovados **387 testes
puros** (dois opcionais ignorados), **79 testes Qt offscreen**, **159 testes
gerais** e os **15 smokes FreeCADCmd**, além de compilação e
`git diff --check`.

### Círculo no Preenchimento e cancelamento de edição — 27 de agosto de 2026

Foi reproduzido um círculo válido no diagnóstico que, ao abrir
`Preenchimento`, resultava em `Nenhuma área fechada foi encontrada`. A ponte
Editor → CAM classificava automaticamente todo círculo pequeno como furo. Esse
contrato permanece correto para `Furo` e `Corte`, mas em `Preenchimento`
retirava o único vetor da lista de contornos antes do cálculo da área.

A conversão agora é consciente da operação. Em `Preenchimento`, círculos
selecionados são discretizados como contornos fechados e podem ser usados
diretamente como região; nas demais operações continuam sendo registrados como
furos, preservando o fluxo existente e o `VectorDocument` original. O mesmo
parâmetro é aplicado quando a fonte é uma seleção, uma `Piece2D`, a chapa ativa
ou o documento completo.

O gerenciador de percursos também ganhou `Cancelar edição`, visível somente
depois de `Editar`. O comando abandona o modo `Atualizar operação`, remove uma
prévia temporária e conserva `SettingsJSON`, movimentos e objeto persistente.
Além disso, uma operação apenas selecionada na árvore não é mais atualizada
implicitamente por `Aplicar`; a sobrescrita exige que o modo de edição tenha
sido aberto de forma explícita.

A regressão adiciona o contrato unitário círculo → contorno de rebaixo e, no
smoke completo da interface, confirma simultaneamente círculo → furo nas abas
existentes, círculo → área em `Preenchimento` e cancelamento sem alteração dos
dados persistidos. Foram aprovados **388 testes puros** (dois opcionais
ignorados), **79 testes Qt offscreen**, **159 testes gerais** e os **15 smokes
FreeCADCmd**.

### Ordem adaptativa e seleção vetorial de alternativas do nesting — 28 de agosto de 2026

O layout mostrado pelo operador evidenciou que contorno real, sozinho, não
garante boa organização. A busca em feixe ainda congelava uma ordem de peças
por tentativa; em conjuntos com perfis em L, trapézios, triângulos e grandes
concavidades, isso podia ocupar a fronteira com a peça errada e deixar vazios
que as peças seguintes já não conseguiam aproveitar.

Nos perfis com mais de uma alternativa, cada estado do feixe agora pode escolher
deterministicamente entre as próximas peças da ordem-base, além de comparar
posições e rotações. O estado registra explicitamente quais instâncias já foram
tratadas, preserva IDs, mantém a busca cancelável e continua sem tocar no
`VectorDocument`; somente `Aplicar organização` cria o comando atômico já
existente. A busca rápida de primeira resposta permanece com uma escolha e,
portanto, conserva sua latência e seu resultado histórico.

Foi corrigida também a fronteira raster → vetor exato. Um perfil mais profundo
podia gerar primeiro um layout raster muito compacto que falhava por uma
diferença de discretização. A implementação descartava então todo o conjunto de
alternativas raster e retornava ao MaxRects, mesmo quando uma solução válida e
quase tão compacta já havia sido avaliada. Os layouts são agora ordenados pela
mesma pontuação e testados, em sequência, pelo validador vetorial de colisão,
folga e compensação da fresa; vence a melhor alternativa realmente válida.
Pesquisas profundas mantêm ainda os feixes rasos na competição, impedindo
regressão em relação ao perfil equilibrado.

A nova regressão usa sete peças mistas, deslocadas na origem mas normalizadas
para o nesting. A busca rápida precisa de duas chapas; a equilibrada encaixa as
sete numa única chapa de 125 × 105 mm com 2 mm de folga, e a profunda conserva
o mesmo resultado válido. Nenhum furo, rebaixo, marcação ou relação `Piece2D`
é separado ou redesenhado.

Validação desta entrega: **389 testes puros** (dois opcionais ignorados), **79
testes Qt offscreen**, **159 testes gerais**, **15 smokes FreeCADCmd**,
compilação integral e `git diff --check` aprovados. O smoke principal exerceu a
prévia/aplicação real do organizador; avisos de inicialização pertencentes a
módulos FreeCAD externos não alteraram o resultado dos smokes.

### Rotação livre medida pelo contorno real — 28 de agosto de 2026

Um segundo layout real mostrou que a melhoria de ordem ainda não resolvia as
ripas diagonais: as peças superiores ficavam empilhadas, enquanto os perfis
longos inferiores formavam um leque com grandes triângulos vazios. A causa não
era apenas a pontuação. A orientação arbitrária rotacionava os quatro cantos do
retângulo envolvente original, em vez dos pontos do contorno. Uma ripa fina em
diagonal continuava, portanto, com a ocupação fictícia de um quadrado grande
mesmo quando a rotação a deixava horizontal.

Todas as opções de rotação passam agora a calcular mínimo, máximo, largura e
altura pelos pontos reais do contorno externo. Peças reconhecidas que não têm
decisão explícita de rotação nem direção de veio usam busca livre: além da
varredura de 15°, entram os alinhamentos das arestas reais mais relevantes.
Para conservar a latência da primeira prévia, o conjunto amplo é reduzido
deterministicamente às poses ortogonais e às menores pegadas reais. Se o
operador gravou 0°, 0°/90°, bloqueio ou veio, esse contrato permanece soberano.
Furos, recortes, features e marcações continuam recebendo a transformação
rígida da peça externa, sem duplicação nem estado geométrico paralelo.

A regressão equivalente ao layout denunciado usa quatro tábuas de 150 × 14 mm
e quatro ripas finas diagonais numa chapa de 360 × 200 mm, com folga de 3 mm.
Limitado às poses ortogonais, o envelope mede 28050 mm² e 100 mm de altura. Com
a correção, as ripas são alinhadas, o envelope cai para 15496 mm² e 51,14 mm de
altura: redução aproximada de 45% na área ocupada. O resultado passa pelo
validador vetorial exato de colisão e folga. Há cobertura adicional para uma
aresta de 36,87°, provando que o comportamento não depende de múltiplos de 15°.

Validação desta entrega: **391 testes puros** (dois opcionais ignorados), **79
testes Qt offscreen**, **159 testes gerais** e os **15 smokes FreeCADCmd**
aprovados. Os avisos de inicialização observados pertencem a módulos FreeCAD
externos e não alteraram os resultados.

### Mesma transformação no nesting e na prévia 2D — 28 de agosto de 2026

O caso foi finalmente reproduzido na guia 2D usando uma cópia somente de teste
do arquivo real `Base_Mesa_Pe_L_Parametrica_V23.FCStd`: 160 entidades, 18 peças
classificadas, nenhuma aberta e chapa de 1850 × 2750 mm. O cálculo puro
declarava as 18 peças dentro de X=3…1832 e Y=3…1053 mm, mas a captura do
operador mostrava duas ripas além da borda direita e grandes vazios entre os
grupos.

A auditoria encontrou uma segunda implementação da transformação dentro de
`ui.py`. O organizador já calculava `dx`/`dy` pelo contorno real, porém a prévia
ignorava esses valores para qualquer rotação e reconstruía a translação pelos
quatro cantos do retângulo envolvente antigo. Assim, o validador aprovava uma
pose e a interface desenhava/aplicava outra. No arquivo real, a projeção antiga
ocupava X=3…2270,07 e Y=3…1323,97 mm e deixava duas peças fora da chapa.

Foi criado um único contrato `organization_placement_transform`, consumido
tanto pelo validador vetorial quanto pela prévia/comando da interface. A guia
2D real foi aberta offscreen, o perfil equilibrado foi executado com 3 mm de
folga e a prévia foi aplicada numa cópia do FCStd. O resultado final ocupou
X=3…1832 e Y=3…1053 mm; as 18 peças ficaram dentro da chapa, sem colisões nem
violações de folga. O FCStd de origem não foi salvo nem alterado.

A regressão pura move uma ripa diagonal pela mesma transformação usada na UI e
exige que os limites dos pontos transformados coincidam, coordenada por
coordenada, com `placed_bounds`. Com ela, a suíte passa a **392 testes puros**
(dois opcionais ignorados).

### Ocultar/reexibir camadas sem reconstruir o arquivo — 29 de agosto de 2026

O travamento progressivo foi reproduzido em uma cópia do arquivo real
`Base_Mesa_Pe_L_Parametrica_V23.FCStd`, com cerca de 45 MB, 67 objetos e uma
projeção interna do Editor com 2.198 arestas. Um clique no checkbox de camada
era tratado como alteração geométrica: serializava o documento, reconstruía o
compound OCC, chamava `recompute()` no FCStd inteiro, recriava os
`QPainterPath` de todas as entidades da camada, recalculava os limites da cena
e ainda destruía/recriava a árvore de camadas.

Alterações exclusivas de camada, relações `Piece2D`, área de trabalho ou
metadados continuam persistentes e atômicas no Undo do FreeCAD, mas agora
preservam a projeção OCC existente. A cena altera somente `visible`/`locked`
dos itens já criados, sem tocar na geometria nem nos limites. Camadas CAM
consolidadas são atualizadas por um único comando e um único Undo; os controles
da árvore lateral também são reutilizados durante o clique.

Na reprodução completa, incluindo `FreeCADDocumentStore`,
`FreeCADCommandSession`, `Editor2DWidget` e processamento de eventos Qt, seis
ciclos consecutivos de ocultar/reexibir ficaram entre **0,154 s e 0,172 s**,
sem crescimento progressivo e sem recompute. O FCStd original não foi salvo
nem alterado. A auditoria separada da árvore 3D encontrou duas famílias
completas de componentes visíveis (nomes originais e sufixo `001`), além das
pré-visualizações CAM; portanto reexibir o conjunto pela árvore nativa ainda
obriga o FreeCAD a enviar milhares de arestas e dezenas de sólidos ao render,
mas isso já não é disparado pelo checkbox do Editor 2D.

Validação desta entrega: **394 testes puros** (dois opcionais ignorados),
**81 testes Qt offscreen**, **159 testes gerais** e os **15 smokes FreeCADCmd**
aprovados.

### Troca de idioma sem varredura repetida da interface — 31 de agosto de 2026

O travamento periódico foi reproduzido com
`Base_Mesa_Pe_L_Parametrica_V23.FCStd` aberto e o WoodCAM em inglês. O Editor
2D e a janela completa estavam registrados como duas raízes de tradução; a
cada segundo, o temporizador percorria ambas, reprocessava também as páginas
CAM ocultas e reatribuía textos de combos, abas, labels e ações que já estavam
corretos. Essas atribuições repetidas provocavam layout, pintura e sinais Qt no
thread da interface, sem qualquer mudança real de idioma.

A troca explícita de idioma continua fazendo um passe integral e imediato. Os
passes periódicos posteriores agora usam somente raízes externas não
sobrepostas, inspecionam apenas controles visíveis e ações observáveis e são
idempotentes: nenhum setter Qt é chamado quando o texto traduzido já coincide.
Os controles existentes recebem diretamente a marca de propriedade do
WoodCAM, permitindo que o tradutor amplo do PanelNest os descarte sem caminhar
novamente por toda a cadeia de pais de cada objeto.
Textos dinâmicos continuam sendo detectados e traduzidos; documento, revisão,
seleção, comandos, operações CAM e geometria não participam desse mecanismo.

Na reprodução completa com o FCStd real, um ciclo periódico do WoodCAM caiu de
aproximadamente **65 ms para 7 ms**; a inspeção da mesma subárvore pelo
tradutor do PanelNest caiu de aproximadamente **41 ms para 6 ms**, sem
crescimento progressivo. O arquivo de origem foi apenas aberto e fechado, sem
salvar nem modificar. Foram aprovados
**396 testes puros** (dois opcionais ignorados), **81 testes Qt offscreen**,
**159 testes gerais** e os **15 smokes FreeCADCmd**. A regressão Qt também
confirma que reaplicar o mesmo idioma não emite `currentTextChanged` no combo
já traduzido.

### Tradução global por evento, sem polling do PanelNest — 31 de agosto de 2026

A otimização anterior diminuiu o custo de cada passe, mas não eliminou a causa
do engasgo: o tradutor global do PanelNest ainda percorria a janela principal
inteira do FreeCAD a cada **750 ms**. A falha também ocorria sem documento
aberto e dependia do idioma interno do PanelNest, não da macro nem do núcleo do
FreeCAD. Em execuções gráficas controladas com os mesmos complementos, o
processo ocioso consumiu entre **22,865% e 32,471%** com o PanelNest em inglês,
contra **2,749%** com somente o PanelNest em português. Alterar apenas o idioma
do WoodCAM não reproduziu a carga.

O temporizador global foi removido. A troca explícita de idioma ainda traduz
imediatamente as raízes registradas; novas janelas de topo e menus são
traduzidos uma única vez ao receber `QEvent.Show`. A fila elimina eventos
duplicados, respeita a subárvore pertencente ao tradutor do WoodCAM e todos os
setters permanecem idempotentes. Assim, o idioma inglês continua cobrindo
diálogos criados depois da troca sem varrer continuamente toda a interface.

Depois da correção, o mesmo FreeCAD XCB com todos os complementos e o idioma
inglês ficou em **2,998%** de CPU ociosa. Três ensaios de câmera com 500 quadros
registraram máximos de **24,095 ms**, **24,605 ms** e **27,743 ms**, sem nenhum
intervalo acima de 50 ms ou 100 ms. No arquivo real
`Base_Mesa_Pe_L_Parametrica_V23.FCStd`, aberto somente para leitura, foram 499
amostras, máximo de **30,295 ms** e nenhum intervalo acima de 50 ms ou 100 ms;
tamanho e data de modificação permaneceram idênticos.

Validação final: **164 testes do PanelNest**, **396 testes puros do WoodCAM**
(dois opcionais ignorados), **81 testes Qt offscreen**, **159 testes gerais** e
os **15 smokes FreeCADCmd** aprovados. A regressão do PanelNest também comprova
que um diálogo novo é traduzido ao aparecer e que não existe mais atualização
periódica após 850 ms.

### Idioma dinâmico e contrato geométrico do nesting — 31 de agosto de 2026

Três regressões relatadas na interface real foram reproduzidas. O menu
`Percursos 2D` montava suas ações por interpolação e o catálogo traduzia apenas
`Corte`, `Furos` ou `Rebaixo`, produzindo textos mistos como `Ver percurso de
Cut no 2D`. Na árvore de camadas, a alteração de `Visível` reutilizava
corretamente os controles, mas reatribuía o nome de domínio em português; o
refresh de idioma o devolvia depois ao inglês e causava a alternância visível.
As frases concretas do menu passaram a ter traduções integrais, e toda
atualização da linha de camada agora escreve diretamente no idioma ativo. A
mensagem dinâmica de bloqueio da Linha comum e os erros de prévia, aplicação e
simulação também são traduzidos antes de abrir o `QMessageBox`.

O bloqueio do corte, apesar de uma organização com **4,00 mm**, tinha uma causa
geométrica no WoodCAM. O validador final do nesting expandia todos os cantos
pelo raio físico arredondado; o gerador CAM preservava junções em esquadria nos
contornos ortogonais para conectar redes T/cruzadas. Duas peças com exatamente
4 mm de folga diagonal podiam, portanto, passar pelo primeiro cálculo e ter os
percursos em esquadria cruzados em dois pontos. A reprodução pura confirmou
`nenhuma ocorrência` no organizador antigo e `CROSSING` no percurso executado.

A política de compensação da Linha comum foi centralizada na camada geométrica.
Organizador e CAM usam agora a mesma função: esquadria para contornos
ortogonais e raio físico para os demais. A validação vetorial descarta o
candidato incompatível durante a própria busca, antes da prévia e sem alterar
automaticamente o documento. Um layout já criado pela versão anterior precisa
ser organizado novamente para receber posições calculadas pelo contrato novo.

Validação: **396 testes puros** (dois opcionais ignorados), **81 testes Qt
offscreen**, **159 testes gerais** e os **15 smokes FreeCADCmd** aprovados. As
novas regressões cobrem o par diagonal de Ø4 mm, os textos completos do menu,
a camada que permanece em inglês imediatamente após sincronizar visibilidade e
a mensagem detalhada de bloqueio sem trechos em português.

### Tooltips HTML dos comandos do PanelNest — 31 de agosto de 2026

O cartão de ajuda de `Aplicar Fita por Face` ainda exibia a descrição em
português embora o título já estivesse em inglês. O texto existia integralmente
no catálogo, mas o FreeCAD o transformava em HTML e inseria tags de parágrafo
no meio da frase para quebrar a linha. A tradução exata encontrava o título e o
`statusTip`, porém já não encontrava a descrição como uma sequência contínua.

O tradutor do PanelNest agora reconhece o cartão rico do FreeCAD, recupera o
título e o texto visível ignorando as quebras HTML, normaliza somente os espaços
introduzidos pela apresentação, traduz pelo catálogo e recompõe o cartão sem
alterar o identificador interno do comando. A correção é genérica para todos os
comandos registrados, inclusive descrições originalmente compostas por várias
linhas.

Uma execução gráfica real auditou as **21 ações PanelNest** carregadas na barra:
títulos, `toolTip` e `statusTip` ficaram em inglês, sem os termos portugueses
pesquisados. A suíte completa do PanelNest aprovou **165 testes**; a regressão
Qt cobre tanto a quebra no meio da frase de fita de borda quanto uma descrição
originalmente multilinha.

### Progresso traduzido e recuperação segura do nesting — 31 de agosto de 2026

O fluxo progressivo ainda criava e atualizava diretamente em português o
título, a etapa, o tempo restante, o resumo da melhor solução, o botão de
interrupção e a mensagem da barra inferior. Esses textos agora entram no idioma
ativo no momento em que são atribuídos, inclusive o diagnóstico dinâmico com
IDs e valores de folga. A cobertura inclui a frase real de bloqueio com
`0,008 mm` para uma folga solicitada de `4,000 mm`.

O `QProgressDialog` nativo também foi substituído por uma janela não modal de
layout explícito e largura estável. A atualização de uma frase curta para duas
linhas já não recalcula uma área de janela sem conteúdo; fundo, label, barra e
botão pertencem ao mesmo layout. Cancelar continua apenas interrompendo o
worker e preservando a melhor prévia disponível.

No organizador, raster e MaxRects permanecem apenas geradores de candidatos e
a validação vetorial exata continua inegociável. Quando os dois melhores
candidatos de uma chapa contêm pares incompatíveis, o fluxo não publica a
geometria nem desativa a validação. Primeiro refaz o layout completo na mesma
chapa com folgas vetoriais progressivamente mais conservadoras e revalida os
percursos reais. Somente quando nenhuma dessas poses é segura remove
deterministicamente uma ocorrência conflitante da página e deixa o laço
externo tentá-la na chapa seguinte. Assim um falso encaixe de `0,008 mm` não
derruba toda a organização nem desperdiça uma segunda chapa quando o conjunto
cabe na primeira; nenhuma mutação ocorre antes de o operador aplicar a prévia.

Validação desta correção: **398 testes puros** (dois opcionais ignorados), **81
testes Qt offscreen**, **159 testes gerais** e os **15 smokes FreeCADCmd**
aprovados. O smoke da UI confirma que a janela nasce em inglês, permanece com
440 px ao trocar a mensagem e conserva o fundo Qt opaco.

### Fronteira global, commit localizado e flyouts de ferramentas — 31 de agosto de 2026

O DXF real `desenho_woodcam.dxf` foi reconstruído em memória e comparado com
`nestresult_202608312002.dxf`, sem alterar os arquivos. Ambos possuem 26 peças
e 3,614560 m² de material. O resultado anterior do WoodCAM ocupava envelope de
aproximadamente 4,114563 m²; o resultado normalizado do serviço ocupava
3,906442 m². A causa não era uma preferência fixa pela peça diagonal: o
MaxRects testava somente o canto de cada retângulo livre e não combinava X e Y
de fronteiras criadas por vizinhos diferentes.

Foi acrescentado um gerador puro por pontos de fronteira que concorre com
MaxRects e raster, preserva peça + descendentes como unidade e continua
subordinado à validação vetorial exata. No mesmo caso, com uma chapa de
1850 × 2750 mm e folga de 4 mm, a primeira busca colocou as 26 peças em uma
chapa, sem ocorrências de cruzamento ou folga, em cerca de 0,23 s. O envelope
foi 1820 × 2122 mm, ou aproximadamente 3,862040 m²: mesma altura útil do
resultado externo normalizado e cerca de 1,1% menos envelope. As permutações
exploratórias agora derivam da geometria normalizada, não de UUIDs de uma nova
importação DXF. A resposta rápida usa geradores de fronteira/MaxRects e publica
uma prévia imediatamente; raster e busca mais larga continuam nos estágios
seguintes e respeitam o cancelamento.

O atraso ao confirmar um vetor foi isolado da pintura Qt. A persistência
reconstruía corretamente a Shape derivada, mas chamava `Document.recompute()`
sem escopo e fazia o FreeCAD recalcular também planilhas, corpos paramétricos e
operações CAM do móvel inteiro. O adapter agora usa o overload localizado
`recompute([feature])`, com fallback para versões antigas. No FCStd real,
aberto sem salvar, o commit de uma nova linha mediu aproximadamente 0,19 s.

Os menus `Editar` e `Reparar` receberam ícones programáticos específicos e
separadores semânticos. Dois `QToolButton` expansíveis foram adicionados à barra
vertical; eles reutilizam os mesmos `QAction` canônicos dos menus superiores,
portanto atalhos, traduções, sinais e comandos permanecem únicos.

Validação final: **401 testes puros** (dois opcionais ignorados), **81 testes Qt
offscreen**, **159 testes gerais** e os **15 smokes FreeCADCmd** aprovados. A
compilação integral e `git diff --check` também passaram; a prévia do DXF real
foi exportada somente para `/tmp` e os dois arquivos de comparação e o FCStd de
origem permaneceram sem gravação.

### Ícones, áreas recorrentes, câmera estável e retalhos — 1º de setembro de 2026

Os ícones programáticos da barra e dos flyouts foram substituídos, na rota
principal, por uma seleção local e versionada do Tabler Icons. São SVGs
consistentes de 24 × 24, redistribuídos sob MIT com a licença junto aos assets;
o renderizador anterior permanece como fallback. Uma folha de contato
offscreen auditou todas as ações em 32 px, inclusive Selecionar, Nós, desenho,
Editar e Reparar.

`Tamanho da área de trabalho` agora aproveita o espaço à direita dos campos
para listar, salvar e excluir predefinições pessoais de X, Y e Z. Os valores
ficam nas preferências `WoodCAM2D`, dados antigos/malformados são normalizados
sem impedir a abertura da bancada e a escolha de um tamanho atualiza a área
por `SetWorkAreaCommand`. A geometria das peças, a origem e o material não são
alterados.

Foi reproduzido também o salto de câmera ao confirmar uma primitiva longe da
vista: a inclusão ampliava `sceneRect`, ativava barras de rolagem e o Qt
recentralizava o conteúdo. O adapter agora preserva centro e transformação da
viewport enquanto amplia apenas a região navegável. Alterar a cena por comando
não passou a criar estado geométrico paralelo, e `Fit` continua sendo a ação
explícita para reenquadrar.

O nesting passou a sugerir, por chapa, o maior retalho retangular de borda que
pode ser separado por uma linha de guilhotina fora dos `placed_bounds`, com
folga e menor lado configuráveis. A prévia mostra linha, dimensões e área; ao
aplicar, a instrução é persistida em `organization_remnant_cuts` dentro do
mesmo `CompositeCommand` da organização. Ela é metadado de layout, não
`PathEntity`: assim não vira peça aberta, não bloqueia uma organização futura
e não entra no contrato Editor → CAM sem uma confirmação de usinagem dedicada.

Validação: **403 testes puros** (dois opcionais ignorados), **83 testes Qt
offscreen**, **161 testes gerais** e os **15 smokes FreeCADCmd** aprovados. As
regressões cobrem normalização das predefinições, cálculo conservador do
retalho, projeção sem vetor paralelo, Undo da instrução e invariância da câmera
ao criar uma linha.

### Pausa ao confirmar vetor com percursos aplicados — 1º de setembro de 2026

O atraso restante foi separado da pintura e do `recompute` global. A medição
no `Base_Mesa_Pe_L_Parametrica_V23.FCStd` separou o commit do restante:
persistir uma nova linha no documento de 45 MB levou aproximadamente 0,17 s.
O arquivo, porém, continha sete operações aplicadas, 303.881 movimentos e cerca
de 65,5 MB de payload comprimido. Depois de todo comando vetorial,
`_vector_editor_document_changed` reconstruía a lista dessas operações e
`_update_simulation_time_label` descompactava novamente todos os movimentos
somente para reapresentar um tempo que não havia mudado. Essa leitura isolada
levou 4,09 s no arquivo real e bloqueava a thread da interface.

Uma alteração de vetor agora atualiza somente o marcador leve
`DESATUALIZADA` das operações que dependem do Editor. Nome, ordem, quantidade
de movimentos e tempo estimado permanecem válidos sem ler o payload. A lista
completa continua sendo reconstruída nos eventos que realmente a modificam;
Undo/Redo reavalia os pequenos `SettingsJSON`, sem descompactar percursos. O
mesmo fingerprint vetorial é calculado uma única vez por atualização completa,
em vez de uma vez para cada operação.

No mesmo FCStd, com o diálogo de produção e a projeção real carregados, o
commit completo caiu para **0,183 s** e o processamento Qt posterior para
**0,0007 s**, com zero atualização do tempo de simulação. O smoke da UI impede
explicitamente que confirmar uma linha volte a chamar a reconstrução pesada;
os **15 smokes FreeCADCmd** foram executados novamente e aprovados.

### Retalhos usináveis, tela de Furo e ícones 3D — 1º de setembro de 2026

A primeira implementação de retalho encerrava o cálculo assim que encontrava
uma única faixa de borda e persistia sua reta apenas em metadados de projeção.
Por isso uma sobra em L recebia somente a linha horizontal superior; a faixa
lateral continuava sem identificação e a reta laranja não podia ser
selecionada pelo operador.

O cálculo agora remove virtualmente o maior retângulo seguro e repete a busca
no envelope ocupado restante, limitado a oito separações por chapa. Cada
linha aceita vira um `PathEntity` aberto com papel `remnant_cut`, ID estável
quando a mesma separação reaparece, estilo laranja tracejado e vínculo com
chapa, limites e área. Criação, substituição, remoção, metadados e organização
das peças permanecem em um único `CompositeCommand`; portanto Undo/Redo restaura
todo o resultado. Reconhecimento de peças, validação comum, PanelNest e o
fallback CAM de documento inteiro ignoram explicitamente esse papel.

Quando o operador seleciona somente uma ou mais linhas de retalho e cria uma
operação `Corte`, a ponte entrega `contours` e `holes` vazios e acrescenta as
trilhas abertas ao fluxo especializado. `build_common_line_cut_job` gera as
passadas no centro do vetor, sem fechamento e sem compensação implícita. Uma
seleção mista é bloqueada e nenhuma linha entra no G-code sem essa escolha.

Na tela de Furo, o checkbox de profundidade do modelo foi movido da linha 2
para a linha 3 do grid; a linha 2 já pertencia à explicação da profundidade e
era a causa exata da sobreposição mostrada na interface. Nas telas Desbaste 3D
e Acabamento 3D, os quatro desenhos genéricos repetidos foram substituídos por
SVGs semânticos distintos para modelo, fronteira, estratégia e nome. Os assets
usam a coleção Tabler já versionada sob licença MIT; nenhum ícone proprietário
do Aspire foi copiado.

Validação desta correção: **407 testes puros** (dois opcionais ignorados),
**83 testes Qt offscreen**, **161 testes gerais** e os **15 smokes FreeCADCmd**
aprovados no FreeCAD 1.1.3. O smoke da UI verifica ainda os seis papéis de
ícone, a linha correta do grid de Furo, a seleção do retalho e o percurso
aberto passando exatamente pelos dois extremos. Compilação e
`git diff --check` também passaram.

### Seleção constante, retalhos internos e retenção segura — 1º de setembro de 2026

Foram reproduzidas cinco regressões independentes. A seleção de uma operação
aplicada lia o payload comprimido duas vezes — uma para o tempo e outra para a
projeção — e, no arquivo real, isso significava reconstruir 303.881 movimentos
e aproximadamente 65,5 MB somente por clicar na árvore. O evento de seleção
agora é constante: usa `EstimatedMachiningSeconds`, habilita as ações e limpa a
prévia antiga sem chamar `decode_moves`. A leitura exata continua restrita às
ações explícitas `Ver percurso`, `Simular`, editar e exportar. O smoke injeta
uma falha caso a seleção volte a decodificar movimentos.

No Editor, a resolução de pais de grupos e o retângulo da seleção ganharam
caches vinculados à revisão do `VectorDocument`. `Copiar` deixou de clonar o
documento completo: o snapshot contém somente raízes selecionadas, filhos de
grupo, relações `Piece2D` completas e as camadas usadas. A regressão cria mil
vetores alheios à seleção e prova que nenhum deles entra no clipboard. No FCStd
real de 45 MB, com 579 entidades, a desserialização mediu 0,053 s e o commit de
uma entidade no documento completo 0,173 s.

A reta laranja de retalho caía na seleção genérica de Sketch/face quando a
opção de fonte do Editor estava desligada. `_active_geometry` agora reconhece
uma seleção formada somente por `remnant_cut`, ativa a fonte correta e entrega
o percurso aberto especializado. Ela permanece selecionável e só entra no CAM
por escolha explícita do operador.

O detector anterior conhecia apenas o envelope ocupado e, por isso, não podia
enxergar o grande vazio interno à direita mostrado no caso real. O cálculo foi
substituído por uma grade de coordenadas derivada da chapa e dos limites das
peças. Um maior-retângulo-vazio ponderado encontra até oito regiões sem
sobreposição; cada região recebe todas as separações necessárias, reaproveita
limites de chapa e cortes anteriores e exibe a etiqueta uma única vez. A
regressão confirma a faixa superior e também o retângulo interno à direita,
com suas linhas inferior e esquerda.

Uma única ocorrência passa pela mesma validação vetorial das demais e depois
é transladada deterministicamente para o X0/Y0 da chapa. No plano global, a
falha `não existe ordem de TabRelease` deixou de invalidar um corte geométrico
válido: somente esse impasse de retenção troca para `keep_tabs`, preserva todas
as pontes e informa o operador. Qualquer outro erro continua bloqueando a
operação, e nenhuma ordem insegura é forçada.

Os novos textos entram pela camada de idioma ativa. Validação final: **410
testes puros** (dois opcionais ignorados), **83 testes Qt offscreen**, **161
testes gerais** e **15 smokes FreeCADCmd**, todos aprovados no FreeCAD 1.1.3.
O smoke de responsividade mediu 600 movimentos de hover em 0,045 s, pan em
0,009 s e prévia de retângulo em 0,021 s.

### Seleção total com linhas de retalho e ponte Editor → CAM — 1º de setembro de 2026

Foi reproduzido no FreeCADCmd o fluxo mostrado pelo operador. Depois que a
organização criava linhas laranjas `remnant_cut`, `Ctrl+A` selecionava essas
linhas junto dos contornos fechados. A ponte tratava qualquer seleção mista
como erro: o comando `Usar Editor 2D como fonte` permanecia desmarcado e Corte
mostrava `Selecione somente linhas de separação de retalho ou somente contornos
de peças`, impedindo usinar as peças normais.

O escopo agora é inequívoco e conservador. Quando a seleção contém contornos
normais e linhas de retalho, a ponte envia somente os contornos fechados ao
Corte comum e não acrescenta as linhas abertas ao percurso. Quando a seleção é
formada exclusivamente pelas linhas laranjas, permanece o percurso aberto
especializado, no centro do vetor e sem fechamento ou compensação implícita.
Assim `Ctrl+A`, seleção por janela e grupos completos voltam a ativar a fonte
do Editor sem transformar separações auxiliares em cortes acidentais.

Fase/entrega: manutenção de produção da ponte Editor → CAM.

Arquivos alterados: `ui.py`, `tests/vector2d/freecad/run_ui_smoke.py`,
`README.md` e este relatório.

Decisões tomadas: `remnant_cut` é opt-in quando constitui toda a seleção; em
seleção mista ele é excluído do Corte normal.

Testes executados e resultados: reprodução FreeCADCmd antes/depois; 410 testes
puros aprovados (dois opcionais ignorados), 83 testes Qt offscreen aprovados,
161 testes gerais aprovados e 15 smokes FreeCADCmd aprovados. O smoke da UI
executa literalmente `Ctrl+A`, ativa a fonte, exige quatro contornos normais e
zero linha de retalho; em seguida preserva o caso exclusivo de corte aberto.

Critérios de aceite aprovados: seleção única normal, seleção total mista,
ativação explícita da fonte Editor, Corte dos contornos normais e Corte
especializado somente das linhas laranjas.

Critérios ainda pendentes: roteiro visual curto no FreeCAD GUI após reiniciar a
bancada, para confirmar o estado do checkbox no estilo/tema de produção.

Riscos conhecidos: nenhum vetor é mutado; uma linha laranja selecionada junto
das peças não será usinada. Para usiná-la, o operador deve selecioná-la sem os
contornos de peça, conforme o contrato exibido no README.

Próximo passo permitido: reiniciar o FreeCAD e repetir `Ctrl+A → Usar Editor 2D
como fonte → Corte` no arquivo real.

### Distribuição unificada PanelNest/WoodCAM — 1º de setembro de 2026

A auditoria do clone público encontrou uma dependência de instalação que os
testes anteriores não revelavam: o repositório WoodCAM não continha o núcleo
PanelNest e o FreeCAD da máquina carregava esse núcleo por um segundo symlink.
Assim, um clone novo podia aprovar os testes locais e ainda não oferecer a
bancada completa em outro computador.

O núcleo PanelNest de produção, seus comandos, ícones, testes e relatórios
foram incorporados ao mesmo repositório. `InitGui.py` agora registra somente
`PanelNestWorkbench`; o comando `PanelNest → CAM → WoodCAM 2D` encontra `ui.py`
na raiz do próprio clone. O instalador cria apenas `Mod/PanelNest`, remove
somente o symlink legado `WoodCAM2D` quando ele aponta para esse mesmo checkout
e ignora diretórios de backup que apenas começam com `v`. `package.xml`, o ZIP
portátil e as instruções de instalação descrevem a mesma distribuição única.

Foi acrescentado `run_unified_workbench_smoke.py`. O teste copiou o repositório
para um diretório temporário, usou um HOME vazio e instalou somente um symlink
`PanelNest`. No FreeCAD GUI offscreen, a bancada foi ativada e o teste confirmou
que `panelnest/__init__.py` e `ui.py` vieram do mesmo clone temporário, que o
comando WoodCAM foi registrado e que não apareceu `WoodCAM2DWorkbench`.

Validação após a unificação: **165 testes PanelNest**, **410 testes puros do
Editor** (dois opcionais ignorados), **83 testes Qt offscreen**, **161 testes
gerais**, **15 smokes FreeCADCmd**, smoke isolado da bancada unificada,
compilação integral e verificação de whitespace aprovados no FreeCAD 1.1.3.
