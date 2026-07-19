# WoodCAM 2D - CNC Marcenaria para FreeCAD

Workbench para FreeCAD focado em CNC router de marcenaria. Gera G-code GRBL/gSender para contornos fechados selecionados, com stepdown, compensação automática da fresa, entrada em rampa, prévia e simulação.

## Continuidade do Editor 2D

Antes de alterar a aba `Editor 2D`, vetores, importação de Sketch, organização
de peças ou a ponte com o CAM, leia [`AGENTS.md`](AGENTS.md) e o
[`Plano mestre do Editor 2D`](docs/PLANO_MESTRE_EDITOR_2D.md). A implementação
de produção fica em `woodcam_editor/`; `vector_editor.py` e a aba
`2D (Sketch)` são apenas referências legadas e não devem voltar a armazenar ou
alterar a geometria principal. A entrega e os testes estão detalhados no
[`Relatório de implementação`](docs/RELATORIO_IMPLEMENTACAO_EDITOR_2D.md).

## Estado atual

Implementado:

### Editor 2D vetorial

- documento vetorial próprio em milímetros, com IDs estáveis, camadas, peças e
  serialização determinística dentro do arquivo FCStd;
- histórico integrado ao Undo/Redo do FreeCAD, inclusive após fechar e reabrir
  a janela do WoodCAM;
- seleção do vetor inteiro com um clique, movimento por arraste e edição de nós
  somente por duplo clique ou tecla `N`, sem teletransporte de pontas;
- barra vertical compacta com ícones CAD para desenho e menus superiores por
  fluxo (`Arquivo`, `Reparar`, `Filetes`, `Peças` e `CAM`); botão direito
  cancela a ferramenta/prévia e volta ao modo Selecionar;
- zoom, pan, grade, eixos, área real da aba `Trabalho`, janela direcional de
  seleção e alvos de mouse independentes do zoom;
- desenho exato de linha, polilinha, retângulo/quadrado, círculo, elipse, arco e
  polígonos/triângulos, com balões de medida durante o gesto;
- painel numérico para X, Y, largura e altura sem escala implícita; círculo com
  raio/diâmetro e elipse com raio X, raio Y e rotação; mover, girar, escalar,
  espelhar, alinhar e distribuir com um Undo por operação;
- snap em extremidade, meio, centro, quadrante, interseção, geometria e grade;
  espaçamento da grade visual e do snap é o mesmo, mas ligar/desligar Snap não
  esconde a grade;
- camadas com nome, cor, finalidade, visibilidade, bloqueio e movimentação da
  seleção entre camadas;
- diagnóstico de vetores abertos, duplicados, spans nulos, cruzamentos,
  auto-interseções e ramificações;
- limpeza preview-first no desenho desbloqueado de vetores exatamente
  duplicados e linhas abertas já cobertas por contornos fechados, confirmada
  em um único Undo;
- diagnóstico distingue peças fechadas apenas encostadas de cruzamentos ou
  sobreposições reais; peças encostadas podem ser reconhecidas e organizadas
  com espaçamento, mas continuam bloqueadas para CAM antes da organização;
- fechamento por tolerância, união explícita de duas pontas escolhidas (com
  medida da abertura e reta de ligação sem deformar os vetores), projeção de
  ponta a reta/arco/círculo, emenda sem ramificação, trim, extend, offset e
  filete com prévia antes de alterar o documento;
- dogbone e T-bone manuais e automáticos como arcos integrados ao contorno —
  não círculos soltos —, limitados deliberadamente a cantos internos lineares
  de 90 graus;
- importação por cópia de Sketch/Shape, DXF e SVG, sem modificar a origem;
- vetorização de PNG/JPEG/BMP/TIFF/WebP com limiar, inversão, filtro de ruído,
  ajuste de cantos, suavização, tamanho final e prévia antes de aplicar;
- criação local de relevo 3D por PNG/JPEG/BMP/TIFF/WebP: o perfil padrão
  `Detalhado` separa volume, forma e microtextura e a transparência
  sempre fica na base; há perfil suave para retratos, inversão, níveis,
  contraste, gamma, suavização, dimensões/base em milímetros, prévia sombreada
  no diálogo e malha temporária na vista 3D; a
  confirmação cria um objeto leve, undoable e desenhado por Coin3D no FCStd,
  sem inserir pixels no `VectorDocument` nem manter uma malha topológica pesada;
- exportação DXF/SVG e preservação de camadas, nomes e cores nos formatos que
  suportam esses dados;
- classificação de contorno externo, furos e recortes internos; metadados de
  peça, material, espessura, veio, quantidade e rotações permitidas;
- organização por contorno real raster/bitmask, comparada com MaxRects, dentro
  da área de `Trabalho`; concavidades, triângulos e curvas podem intercalar sem
  separar furos e recortes;
- fonte de geometria explícita para Corte/Furo/Preenchimento e marcação de
  operação CAM desatualizada quando o desenho usado por ela muda;
- envio de peças completas ao PanelNest por uma ponte segura no documento
  FreeCAD, mantendo internos, X/Y e rotação do layout do Editor.

### CAM e bancada

- integração com a bancada `PanelNest` no FreeCAD;
- comando `WoodCAM 2D` no submenu e na barra `PanelNest CAM`;
- presets de MDF 15 mm, MDF 18 mm, fresa 6 mm e fresa 3 mm;
- leitura de Sketch fechado ou face plana selecionada;
- leitura de multiplos contornos quando objetos/faces 3D ou compounds estiverem selecionados;
- discretizacao de curvas simples para gerar trajetoria 2D;
- corte externo, sobre a linha e interno;
- furação vertical opcional, sempre executada antes dos contornos;
- a furação não é bloqueada quando a ferramenta é maior que o furo: o diâmetro final será o da ferramenta instalada;
- entrada helicoidal e limpeza circular quando a ferramenta for menor que o furo, com descida por volta e passo lateral configuráveis;
- furação faseada opcional, com passo, folga de retração, retorno à cota inicial ou ao passo anterior;
- permanência configurável no fundo do furo e opção de preservar a ordem da seleção;
- ponto inicial/home XY configurável, com retorno opcional e retorno final do G-code respeitando esse ponto;
- interface organizada no fluxo `Trabalho`, `Material`, `Fresas`, `Corte`, `Furo`, `Preenchimento` e `Simulação e Salvar`, inspirada no painel de percursos do Aspire;
- na aba `Trabalho`, tipo de trabalho, tamanho X/Y/Z da área de trabalho, Z-zero na superfície do material ou na mesa, origem XY por canto/centro e posição inicial X/Y;
- a pré-visualização mostra a área de trabalho como retângulo azul-claro/tracejado quando houver tamanho configurado ou seleção útil;
- Z-zero na mesa converte o percurso para coordenadas de máquina: o topo fica na espessura do material e cortes passantes chegam perto de Z0;
- a origem XY por canto/centro pode usar o contorno selecionado para deslocar as coordenadas de saída sem alterar projetos antigos por padrão;
- aba `Fresas` para cadastrar, editar e remover ferramentas; as abas de usinagem usam `Selecionar...` para escolher uma fresa cadastrada;
- abas independentes `Corte`, `Furo` e `Preenchimento`, com diagramas vetoriais próprios que identificam cada percurso;
- abas independentes `Desbaste 3D` e `Acabamento 3D`, aceitando o relevo por
  imagem, STL, `Mesh::Feature` e sólidos/Shapes tessellados sem modificar a
  fonte;
- desbaste 3D por níveis Z ou varredura 3D, com fresa, profundidade por passe,
  stepover, sobremetal, fronteira, perfil, ordem, direção, inversão e rampa;
- acabamento 3D raster em qualquer ângulo ou offset concêntrico, com stepover,
  fronteira e compensação da geometria real de fresa esférica ou topo reto;
- projeção superior 2.5D com hash da malha de origem: operações ficam marcadas
  como desatualizadas quando o relevo/STL muda; undercuts exigem outra fixação
  ou mais eixos;
- formulários compactos com rolagem própria por aba, mini-ilustrações transparentes ao lado das configurações e botões de percurso sempre acessíveis;
- mini-ilustrações podem ser substituídas por PNG/JPG/WebP em `resources/diagrams`, com fallback automático para os desenhos internos;
- botão `Aplicar` cria operações persistentes e numeradas na árvore: `Furo 01`, `Corte 01`, `Preenchimento 01` etc.;
- cada operação aplicada conserva suas configurações, seleção de origem, movimentos e trajetória visível sem apagar as anteriores;
- nome, profundidades e ferramenta próprios em cada aba;
- rampa própria nas abas `Corte` e `Preenchimento`;
- desaceleração configurável nos cantos: ângulo mínimo, percentual do avanço e distância antes/depois do vértice;
- trechos desacelerados destacados em violeta na prévia;
- na aba `Corte`, contornos internos são cortados por dentro e antes dos externos;
- se a ferramenta não couber num furo da aba `Corte`, o WoodCAM usa descida central como fallback;
- lado dos contornos externos configurável, com corte externo como padrão;
- preenchimento/rebaixo de área por offset concêntrico ou raster em zigue-zague;
- stepdown, stepover, sobremetal lateral, ângulo do raster, sentido e passe final configuráveis;
- preservação de ilhas internas; quando existem ilhas, o preenchimento usa raster automaticamente;
- ligação contínua entre passadas seguras, retraindo apenas ao cruzar ilhas, regiões desconectadas ou ao terminar a camada;
- áreas separadas são ordenadas por proximidade e cada área conclui todas as profundidades antes da próxima;
- setas no diagrama de preenchimento mostram o zigue-zague e invertem no Offset ao alternar entre concordante e convencional;
- seleção parcial de arestas respeitada: somente os `Edge...` escolhidos formam a área do percurso;
- seleção parcial aberta é bloqueada, sem fallback silencioso para o Sketch inteiro;
- círculos fechados de Sketch e círculos vindos de `ExternalGeometry` são reconhecidos como furos;
- círculos 2D sem profundidade própria usam a profundidade configurada na aba `Furo`;
- arquivo identificado pela aba ativa e numeração automática para nunca sobrescrever o G-code anterior;
- compensação automática pelo raio da fresa conforme o tipo de corte;
- área útil X/Y da máquina visível, editável e persistente (`0` deixa sem limite até o curso real ser informado);
- seleção de um layout PanelNest redirecionada à geometria `CAM Chapa`, sem incluir a chapa-base;
- ordenacao inicial de multiplas pecas por menor deslocamento;
- multiplas passadas por stepdown;
- entrada em rampa ao longo do contorno;
- G-code GRBL/gSender simples (`G21`, `G90`, `G17`, `G94`, `G54`);
- previa no documento com rapido, rampa/descida e corte em cores separadas;
- prévia de percursos 3D completos desenhada por um único overlay Coin3D
  transitório, mantendo o relevo sombreado visível e sem transformar dezenas
  de milhares de pontos de G-code em objetos ou arestas OCC pesadas;
- relevos WoodCAM alimentam o CAM diretamente pelo mapa de altura preservado;
  a resolução visual não limita a precisão da usinagem;
- movimentos 3D aplicados são compactados no FCStd sem duplicar o percurso em
  objetos gráficos persistentes; `Pré-visualizar` recompõe a trajetória
  completa na GPU sob demanda e `Aplicar` mantém esse overlay visível sem
  alterar o G-code exportado nem afastar a câmera para enquadrar toda a mesa;
- a vista 3D de segurança não reduz nem reamostra movimentos: o rodapé verde
  informa a fresa, o diâmetro, o stepover físico e a quantidade exata. Qualquer
  alteração de ferramenta ou parâmetro apaga imediatamente a trajetória antiga
  e marca a vista como desatualizada;
- ao selecionar uma única operação 3D aplicada, a trajetória é reconstruída da
  mesma lista compactada usada na exportação. Depois de `Gerar G-code`, o estado
  `G-CODE GERADO = VISTA` confirma que o overlay exibido veio da própria lista
  entregue ao escritor do arquivo, com os mesmos quatro decimais do `.nc`;
- na simulação 3D, o percurso desenhado continua integral e exato e a fresa
  usa uma linha do tempo compacta com cada destino real do G-code. A posição
  é interpolada somente dentro do segmento real atual, preservando subidas e
  descidas de Z sem criar um objeto FreeCAD por movimento;
- a ferramenta animada representa topo reto, topo esférico, V-bit, broca,
  compressão ou faceadora com a ponta de contato em seu XYZ real. Hélices
  leves e rotação Coin3D tornam o giro perceptível sem recomputar o documento;
- simulacao visual com fresa animada, deslocamento rapido, rastro progressivo de rampa/corte e controle pequeno de cancelamento;
- tempo de simulacao baseado nos avancos configurados, com multiplicador de velocidade;
- janela de configuracao nao-modal, podendo ser ocultada sem parar a simulacao;
- macro antiga ainda disponivel como fallback.

Ainda nao implementado:

- rasgos/canais;
- pontes/tabs;
- otimizacao entre varias operacoes;
- pos-processador Mach3;
- texto e modelagem 3D interativa; desbaste/acabamento 3D de três eixos já
  existem, mas reentrâncias (*undercuts*) exigem outra fixação ou CAM de
  quatro/cinco eixos;
- desenho interativo de Bezier e booleanos vetoriais gerais;
- smart-snap tangente/perpendicular. Bezier cubica importada continua
  preservada pelo modelo quando o formato de origem for suportado.

## Instalacao local

O WoodCAM é distribuído sem pesos de IA, sem telemetria e sem downloads
silenciosos. O instalador pergunta explicitamente se você quer instalar a IA
opcional de relevo. Responda `s` para aceitar ou `N` para manter somente a
bancada. Para automatizar a escolha:

```bash
./install_workbench.sh --with-ai     # bancada + IA opcional
./install_workbench.sh --without-ai  # somente a bancada
```

A IA opcional é instalada em um runtime isolado fora do Python do FreeCAD,
executa em CPU e baixa seus próprios modelos. Os pesos não são incluídos neste
repositório por tamanho e por terem licenças próprias. O recurso de relevo
tradicional continua disponível sem IA e sem internet. Para instalar a IA
posteriormente, use `bash install_relief_ai.sh`.

### Aviso de privacidade e licenças

O WoodCAM não envia imagens, projetos ou G-code para a nuvem. A instalação da
IA opcional acessa Hugging Face apenas para baixar o runtime e os pesos na
primeira instalação. Revise as licenças dos modelos antes de redistribuir os
pesos ou usar o pacote em um produto comercial.

No terminal, dentro desta pasta:

```bash
chmod +x install_workbench.sh
./install_workbench.sh
```

O script cria um symlink em:

```text
~/.local/share/FreeCAD/Mod/WoodCAM2D
```

Depois reinicie o FreeCAD. Com o PanelNest instalado, escolha a bancada
`PanelNest` e use `PanelNest → CAM → WoodCAM 2D`. Se tiver instalado apenas o
WoodCAM2D, selecione a bancada `WoodCAM 2D` diretamente.

No Windows, mantenha os dois módulos lado a lado:

```text
%APPDATA%\FreeCAD\Mod\PanelNest
%APPDATA%\FreeCAD\Mod\WoodCAM2D
```

O WoodCAM é exibido dentro da bancada PanelNest, mas sua pasta auxiliar ainda
precisa estar instalada. O pacote gerado por
`PanelNest/scripts/build_portable_package.py` já contém as duas pastas.

## Pacote para outro FreeCAD

Não é necessário criar ZIP. Copie a pasta deste projeto com o nome
`WoodCAM2D` diretamente para a pasta `Mod` do outro FreeCAD. Se também usar o
PanelNest, os módulos são separados e devem ficar lado a lado — nunca um dentro
da pasta do outro:

```text
Mod/
├── PanelNest/
└── WoodCAM2D/
```

Com somente `WoodCAM2D`, aparece a bancada WoodCAM 2D. Com as duas pastas,
aparece apenas a bancada PanelNest e o WoodCAM fica disponível como botão/menu
dentro dela. Reinicie o FreeCAD depois da cópia.

## Uso do Editor 2D

`Vetorizar imagem…` usa Pillow e o executável `potrace`. Eles já estão
disponíveis no ambiente de desenvolvimento atual. Em outro computador, o
restante do WoodCAM continua funcionando apenas com a pasta copiada, mas esse
comando mostra uma orientação de dependência enquanto `potrace` não estiver no
`PATH`.

1. Abra ou crie um documento e configure X, Y e Z da mesa na aba `Trabalho`.
2. Abra `Editor 2D`. Desenhe com a barra vertical, use
   `Arquivo → Importar itens da árvore…` para copiar Sketches/Shapes e layouts
   PanelNest selecionados (resolvidos para `CAM Chapa`, sem a chapa-base),
   reorientando arestas OCC na cópia e trazendo uma face por sólido do compound,
   `Importar arquivo` para DXF/SVG ou `Vetorizar imagem…` para transformar uma
   imagem em contornos.
3. Um clique seleciona o vetor inteiro; arraste o corpo para mover. Use duplo
   clique ou `N` para mostrar e mover nós. `Esc` ou botão direito cancela a
   ferramenta atual e volta a Selecionar.
4. Use os painéis laterais para medidas exatas, transformações, camadas e
   metadados. As prévias em magenta ainda não alteraram o documento.
5. Rode `Reparar → Diagnosticar`. Para cópias coincidentes ou linhas abertas
   redesenhadas sobre os lados de contornos fechados, use
   `Limpar sobrelinhas/duplicados…`, confira todas as sobrelinhas do desenho
   desbloqueado em magenta e confirme; sobreposições parciais continuam para
   revisão/Trim. Para escolher os dois pontos, use
   `Reparar → Unir 2 pontas (reta)`; para levar uma ponta até o corpo de uma
   reta/curva use `Projetar ponta na geometria`. Toda confirmação cria uma
   única etapa de Undo.
6. Clique `Reconhecer peças e furos`: isso não cria vetores, apenas vincula cada
   externo aos internos contidos. Depois use `Organizar inteligente` para
   comparar MaxRects e contorno real em várias ordens. A prévia informa
   eficiência, estratégia vencedora e quantidade de layouts avaliados. Os
   perfis `rápido` e `profundo` reduzem ou ampliam a busca sem usar aleatoriedade.
7. Ative `Usar Editor 2D como fonte` para alimentar Corte/Furo/Preenchimento, ou
   use `Enviar PanelNest` para criar o intercâmbio no documento.
8. Salve normalmente o FCStd. Vetores, camadas, peças e preferências persistem.

Atalhos principais: `S` selecionar; `N` nós; `L` linha; `P` polilinha; `R`
retângulo; `C` círculo; `E` elipse; `A` arco; `G` polígono; `Delete` excluir;
`Ctrl+Z` desfazer; `Ctrl+Shift+Z`/`Ctrl+Y` refazer; `F` enquadrar.

## Relevo 3D por imagem

1. Abra um FCStd e use `Editor 2D → Arquivo → Criar relevo 3D por imagem…`.
2. Escolha uma imagem raster. A orientação automática lê os cantos: fundo
   claro é invertido para ficar na base, enquanto fundo escuro permanece na
   base. `Inverter relevo — trocar cima/baixo` permanece sempre disponível;
   a orientação automática apenas escolhe seu valor inicial.
3. Comece pelo perfil `Detalhado — escamas, penas e objetos`, calibrado com o
   mesmo peixe usado na demonstração do Aspire. Ele seleciona 4 mm de relevo,
   prévia visual de 192 pontos, corpo de 3 px, 65% de microdetalhe, 95% de definição,
   borda de 1,5 px e 50% de volume. `Retrato com lissage — rostos e pelos`
   aplica acabamento pós-componente de 1,5 px, preservando o contorno e a
   transparência como a operação *Lisser components* do Aspire; qualquer
   alteração manual muda o perfil para `Personalizado`. Ajuste largura, altura,
   altura máxima, espessura da base,
   posição X/Y, níveis, contraste, gamma e suavização. O modo
   `Aspire/Emboss — componente
   multiescala` separa o volume geral da microtextura: `Continuidade do corpo`
   controla a forma ampla e `Textura fina` dosa pelos e escamas sem
   transformá-los em picos. `Definição da forma` preserva olhos/focinho e
   `Borda arredondada` faz o contorno chegar progressivamente à base. A prévia
   usa normais suaves e material fosco. `Volume escultórico` levanta a massa
   geral do objeto pela silhueta antes de recolocar forma e textura. `Altura
   inteligente` mantém a amplitude física dos detalhes ao aumentar a altura
   máxima, concentrando os milímetros extras no volume geral. `Volume suave
   experimental` cria um corpo sintético pela silhueta; ele é opcional e não
   corresponde à conversão direta do Aspire. Use `Mapa de
   profundidade pronto` quando o arquivo já representar alturas. Em PNGs com
   transparência, pixels totalmente transparentes permanecem sempre na base. A
   janela avisa quando a fonte contém cenário: sem transparência ou fundo
   uniforme, céu, parede e vegetação também viram altura. Essa limitação existe
   antes do relevo; para componente isolado use PNG transparente. A janela
   permanece não modal para que
   a malha dourada temporária possa ser girada na vista 3D do FreeCAD; arraste
   por qualquer fundo livre do painel para mover a janela das três prévias.
   O painel `Essencial` concentra forma, textura, volume, lissage e o comando
   sempre visível `Inverter relevo (alto ↔ baixo)`; orientação automática,
   níveis e separação de fundo ficam em `Avançado`, com rolagem própria.
4. `Criar relevo` grava mapa processado, parâmetros e malha dentro do FCStd em
   `WoodCAM 3D — Relevos`. `Cancelar` remove somente a prévia; `Ctrl+Z` desfaz
   a criação confirmada.

O processamento local tradicional continua disponível sem IA e sem internet.
Opcionalmente, em `Avançado → Gerar forma com IA — CPU`, o WoodCAM pode usar
Depth Anything V2 Small para gerar um mapa temporário. A imagem e o FCStd não
são alterados nessa etapa; somente `Criar relevo` confirma o resultado na mesma
transação com Undo/Redo. O FreeCAD não importa PyTorch: a inferência roda em um
Python externo, explicitamente travado em CPU. Antes de gerar, `Refino da IA`
oferece três resultados comparáveis: `Escultórico HD` prioriza volume contínuo
para animais e rostos, `Equilibrado` combina corpo e detalhes, e `Gravação
detalhada` preserva pelos e ranhuras para ferramenta fina. Os ajustes numéricos
usam sliders com valor exato editável. O relevo usa material clay cinza neutro
para não ser confundido com a seleção laranja do FreeCAD. PNGs com
transparência usam diretamente o canal
alfa para impedir que o fundo entre no relevo; imagens com o xadrez cinza/branco
gravado no próprio JPEG também são reconhecidas e limpas. A malha visual usa
320 pontos por lado por padrão, aceita até 384 e recebe material clay neutro
semibrilho com luz lateral para revelar inclinações rasas. Esse aumento afeta somente a vista:
o rodapé informa a resolução real do mapa consumido pelo CAM.

Na instalação Pro, uma segunda IA Metric3D v2 Small estima normal XYZ e
confiança de superfície. O worker integra essas inclinações por Poisson
ancorado à profundidade DA2: a DA2 continua controlando silhueta e volume do
corpo, enquanto as normais recuperam focinho, bochechas, olhos, orelhas e
cabelo sem converter toda a textura fotográfica em riscos. Tudo roda no mesmo
processo externo, somente em CPU e ainda como prévia. Se o modelo de normais
não estiver configurado, a DA2 continua funcionando como fallback. Os pesos
DA2 têm 99.218.434 bytes e o Metric3D FP16 tem 75.778.144 bytes, totalizando
174.996.578 bytes; o runtime Python Pro completo ocupa aproximadamente 1,7 GB
nesta instalação. Para instalar o recurso opcional:

```bash
bash install_relief_ai.sh
```

Nota de distribuição: o código Metric3D declara BSD-2-Clause e o artefato ONNX
usado declara CC0-1.0, mas o repositório oficial solicita contato para consultas
comerciais. O uso local pode permanecer habilitado; antes de redistribuir os
pesos dentro de um produto pago, confirme por escrito a licença com os autores.

O relevo também pode alimentar diretamente as abas de desbaste e acabamento 3D.

## Uso do CAM

A barra principal mantém texto somente em `Trabalho` e `Material`. As demais
abas usam botões estreitos somente com ícone e mostram o nome completo ao
repousar o ponteiro; ícones nativos temporários marcam operações cuja arte
definitiva ainda não foi fornecida. A antiga aba de diagnóstico de Sketch não
faz mais parte da interface de produção; seu código permanece preservado sem
permissão para modificar a fonte.

1. Abra ou crie um documento no FreeCAD.
2. Desenhe Sketches fechados, selecione uma face plana ou selecione objetos 3D/compounds com varias chapas.
3. Ative a bancada `PanelNest`.
4. Clique em `WoodCAM 2D` na barra `PanelNest CAM`.
5. Confira primeiro as abas `Trabalho` e `Material`: defina tipo de trabalho, tamanho X/Y/Z da área, Z-zero, origem XY, posição inicial, espessura e cotas de segurança.
6. Na aba `Fresas`, cadastre ou ajuste as ferramentas; nas abas de usinagem use `Selecionar...` para escolher uma delas.
7. Na aba `Corte`, escolha o lado dos contornos externos; furos/contornos internos são enviados automaticamente como corte interno.
8. Na aba `Furo`, escolha profundidade, ferramenta, interpolação helicoidal, furação faseada, retração, permanência e ordem dos furos.
9. Na aba `Preenchimento`, escolha Offset ou Raster, passo lateral, sobremetal e passe de perfil.
10. Para relevos ou STL, selecione um único modelo 3D e configure `Desbaste 3D`;
    depois selecione a mesma fonte e configure `Acabamento 3D`.
    No acabamento raster, passadas vizinhas são ligadas acompanhando a própria
    superfície compensada. O WoodCAM recolhe ao Z seguro somente ao encontrar
    vazio, furo, fronteira ou outra região desconectada. Para o acabamento de
    relevo, prefira fresa esférica; uma fresa de topo reto respeita sua própria
    geometria, mas deixa degraus e não produz o mesmo acabamento arredondado.
11. Use `Assistente CAM` para uma revisão explicável de ferramenta, stepdown,
    stepover, alturas e profundidade. Ele mostra motivos e valores para revisar,
    mas nunca altera parâmetros, geometria ou G-code automaticamente. O
    assistente analisa somente a aba CAM visível e identifica no relatório a
    operação e a fresa; nas abas Editor 2D, Trabalho, Material, Fresas,
    diagnóstico ou simulação ele pede que uma operação seja aberta.
12. Clique em `Pré-visualizar` para conferir a trajetória temporária da aba ativa.
13. Clique em `Aplicar` para guardar a operação na árvore `WoodCAM 2D — Operações`. Cada tipo recebe sua própria numeração e não substitui as operações já aplicadas.
14. Use a aba `Simulação e Salvar`, selecione **uma** operação aplicada e confira
    o rodapé `APLICADO = G-CODE`, inclusive o passo físico mostrado. Se trocar a
    fresa ou o stepover, a trajetória antiga desaparece e só volta após novo
    `Pré-visualizar` ou `Aplicar`.
15. Clique em `Gerar G-code`. Para uma operação 3D, o rodapé muda para
    `G-CODE GERADO = VISTA`: a tela e o arquivo foram alimentados pela mesma
    lista de movimentos. Os percursos 3D usam os sufixos `_desbaste_3d` e
    `_acabamento_3d`; se um arquivo já existir, o WoodCAM acrescenta `_02`,
    `_03` etc.

## Arquivos principais

- `Init.py` e `InitGui.py` - entrada da bancada FreeCAD;
- `woodcam2d_commands.py` - comando registrado no menu/barra;
- `ui.py` - interface PySide;
- `geometry_reader.py` - leitura de Sketch/faces;
- `operations.py` - trajetória, stepdown, rampa e compensação externa/interna;
- `woodcam_3d/` - projeção de malha, compensação da ferramenta e percursos de
  desbaste/acabamento 3D;
- `gcode_writer.py` - geracao de G-code;
- `validators.py` - validacoes basicas;
- `cam_advisor.py` - revisão CAM explicável e sem mutação automática;
- `presets.py` - presets de material, fresa e maquina;
- `woodcam_editor/` - domínio, comandos, canvas, ferramentas, persistência,
  importadores/exportadores e pontes do Editor 2D;
- `tests/vector2d/` - testes puros, Qt e smokes FreeCADCmd do editor;
- `woodcam2d_macro.py` - macro fallback para abrir a mesma janela.

## Observacoes de seguranca

Confira sempre a previa e rode o G-code primeiro sem material ou com a
ferramenta afastada. O WoodCAM preserva ilhas vetoriais, mas ainda não conhece
a posição física de grampos/fixações, a cinemática real da máquina ou troca
automática de ferramenta.

## Protocolo atual de percurso

- Entrada: escolhe candidatos dentro dos segmentos do contorno compensado, evitando vertices/cantos.
- Pontuacao de entrada: combina distancia ate a posicao atual, folga em relacao aos cantos e tamanho do segmento.
- Visualizacao da entrada: a previa mostra marcadores verdes nos pontos de entrada escolhidos.
- Descida: entra em rampa pelo proprio contorno quando `Entrada em rampa` esta ativa.
- Cantos: quando a redução está ativa, divide o segmento antes e depois de mudanças bruscas e aplica avanço menor apenas nessa região.
- Saida: termina a ultima volta da peca e retrai para a altura segura.
- Mudanca de peca: move em rapido na altura segura ate a proxima peca.
- Ordem das pecas: nearest-neighbor simples; escolhe a proxima peca mais perto da posicao real de saida da peca anterior.

Limite conhecido: a escolha de entrada ainda nao conhece lado visivel, grampos ou regioes estreitas informadas pelo usuario.
