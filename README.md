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
- no menu `CAM`, `Ver percurso de Corte aqui` sobrepõe no próprio Editor 2D a
  lista exata de movimentos que alimenta o G-code: vermelho para usinagem,
  laranja para entradas e cinza tracejado para rápidos. É uma vista transitória
  e não cria vetores nem altera a operação; qualquer edição do desenho a oculta;
- histórico integrado ao Undo/Redo do FreeCAD, inclusive após fechar e reabrir
  a janela do WoodCAM;
- seleção do vetor inteiro com um clique, movimento por arraste e edição de nós
  somente por duplo clique ou tecla `N`, sem teletransporte de pontas;
- barra vertical compacta com ícones CAD para desenho e menus superiores por
  fluxo (`Arquivo`, `Reparar`, `Filetes`, `Peças` e `CAM`); botão direito
  cancela a ferramenta/prévia e volta ao modo Selecionar;
- quando carregado pela bancada PanelNest, o idioma é escolhido no menu global
  `Idioma` da barra principal do FreeCAD (`Português`/`English`) e traduz a
  bancada inteira, incluindo o Editor 2D e o CAM; em uso independente do
  Editor 2D, o seletor local continua disponível como fallback. A barra de
  operações fica na lateral esquerda, com ícone e nome horizontal em todas as
  abas; trocar o idioma não muda sua estrutura, quantidade de abas nem área de
  clique. A coluna termina depois de `Simulação e Salvar`, sem prolongar um
  fundo vazio até o rodapé. O botão acima de `Trabalho` alterna a mesma
  instância do WoodCAM entre painel preso ao FreeCAD e janela independente,
  preservando aba, campos e Editor 2D. Sugestões e
  alertas gerados pelo Assistente CAM também são formatados diretamente no
  idioma ativo, incluindo explicação e valor sugerido. A tradução alcança
  também itens de listas, cabeçalhos de camadas, painéis de peça e dimensões,
  mensagens vazias, dicas e textos atualizados durante o uso; nomes e valores
  informados pelo usuário permanecem intactos. A tradução global do PanelNest
  ocorre somente na troca de idioma e quando janelas ou menus são exibidos,
  sem varredura periódica da janela principal; o fallback dinâmico local do
  WoodCAM limita-se a controles visíveis e não reatribui textos já corretos;
- zoom, pan livre sem limite artificial do enquadramento, grade opcional,
  eixos, área real da aba `Trabalho`, réguas com indicadores X/Y do cursor,
  janela direcional de seleção e alvos de mouse independentes do zoom;
- desenho exato de linha, polilinha, retângulo/quadrado, círculo, elipse, arco,
  polígonos/triângulos e estrelas paramétricas (pontas e profundidade interna),
  com balões de medida durante o gesto;
- painel numérico para X, Y, largura e altura, mais escala uniforme explícita
  em porcentagem aplicada pelo centro; círculo com
  raio/diâmetro e elipse com raio X, raio Y e rotação; mover, girar, escalar,
  espelhar, alinhar e distribuir com um Undo por operação;
- snap em extremidade, meio, centro, quadrante, interseção, geometria e grade;
  `Imã (Snap)` controla as referências geométricas e `Grade` controla
  separadamente a quadrícula e seu encaixe;
- smart-snap a partir do ponto de desenho ativo: horizontal, vertical, ângulos
  configuráveis, perpendicular em retas/arcos e tangente em círculos/arcos;
  ele é uma prévia sob o cursor e nunca cria restrições implícitas;
- camadas com nome, cor, finalidade, visibilidade, bloqueio e movimentação da
  seleção entre camadas;
- painel `Chapas` derivado das folhas reais do layout, com seleção,
  enquadramento e X0/Y0 local por chapa; a origem local é apenas uma vista de
  edição e os vetores continuam nas coordenadas globais estáveis do documento;
- `Arquivo → Enviar para impressão (TechDraw)…` cria uma página vetorial por
  chapa, preservando seu limite e todo o conteúdo visível. O assistente permite
  chapa ativa ou todas, sugere A4–A0 conforme o tamanho, oferece chapa inteira
  na página, 1:1 ou escala percentual e pode incluir o percurso 2D visível. A
  página é uma cópia de apresentação: não cria contornos CAM nem altera os
  vetores;
- seções laterais recolhíveis por seta, preservando seleção, valores e estado
  do documento ao abrir ou fechar cada bloco;
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
- `Unir vetores abertos (por tolerância)` para vários caminhos selecionados:
  percorre somente pontas compatíveis dentro da medida informada, sem criar
  pontes sobre lacunas grandes, sempre com prévia e um único Undo. Em
  `Fechar caminho / unir próximas`, basta selecionar um fragmento: o reparo
  descobre toda a cadeia aberta conectada na mesma camada, une os trechos e
  fecha a folga numérica final sem acrescentar um span microscópico. Detalhes
  geométricos reais, mesmo menores que a tolerância de união, são preservados;
- `Editar → Subtrair vetores (criar furo/recorte interno)` transforma uma
  chapa externa e qualquer contorno fechado dentro dela em uma peça composta,
  mesmo quando o furo não toca a borda; `Soldar vetores` fica reservado aos
  perfis fechados que se sobrepõem fisicamente;
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
- criação de texto vetorial por fonte instalada, convertido explicitamente em
  contornos fechados portáteis; letras, vazados e acentos ficam agrupados como
  um único objeto e podem seguir para CAM/DXF sem depender da fonte no outro PC;
  o mesmo grupo pode ser reeditado depois para trocar conteúdo, fonte, tamanho
  e estilo sem perder a posição ou a operação de Undo;
- exportação DXF/SVG e preservação de camadas, nomes e cores nos formatos que
  suportam esses dados;
- classificação de contorno externo, furos e recortes internos; metadados de
  peça, material, espessura, veio, quantidade e rotações permitidas;
- organização por contorno real raster/bitmask, comparada com MaxRects e com
  encaixe por pontos de fronteira, dentro da área de `Trabalho`; o terceiro
  gerador combina cantos formados por vizinhos diferentes que uma subdivisão
  MaxRects isolada não enxerga. Concavidades, triângulos e curvas podem intercalar sem
  separar furos e recortes; a busca roda fora da interface, mostra a primeira
  solução e continua refinando a prévia com tempo-alvo, progresso e interrupção;
  nos perfis equilibrado/profundo, o feixe também escolhe dinamicamente qual
  das próximas peças melhor ocupa a fronteira livre, e a seleção final conserva
  a melhor alternativa que passe pela validação vetorial exata em vez de cair
  no arranjo retangular por causa de um único candidato raster inválido. Peças
  reconhecidas sem restrição explícita de rotação ou veio recebem rotação livre,
  incluindo ângulos das arestas reais; cada pose é medida pelo contorno, não pelo
  retângulo envolvente antigo. Modos 0°, 0°/90°, bloqueado e direção do veio
  continuam sendo respeitados. A prévia e o comando aplicado reutilizam a mesma
  transformação rígida validada pelo nesting, portanto peças rotacionadas não
  mudam de posição entre o cálculo e a guia 2D;
- fonte de geometria explícita para Corte/Furo/Preenchimento e marcação de
  operação CAM desatualizada quando o desenho usado por ela muda;
- círculos pequenos selecionados continuam sendo furos em `Furo`/`Corte`, mas
  em `Preenchimento` o próprio círculo é preservado como área fechada a
  usinar, sem exigir conversão manual do vetor;
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
- ao abrir um percurso aplicado para edição, `Cancelar edição` sai do modo de
  atualização e descarta somente a prévia temporária, sem alterar nem excluir
  o percurso persistido; selecionar uma operação na árvore, sozinho, nunca a
  sobrescreve, descompacta seus movimentos nem redesenha a trajetória; a
  geometria exata é carregada somente por `Ver percurso`, `Simular`, editar ou
  exportar;
- nome, profundidades e ferramenta próprios em cada aba;
- rampa própria nas abas `Corte` e `Preenchimento`;
- desaceleração configurável nos cantos: ângulo mínimo, percentual do avanço e distância antes/depois do vértice;
- trechos desacelerados destacados em violeta na prévia;
- na aba `Corte`, contornos internos são cortados por dentro e antes dos externos;
- se a ferramenta não couber num furo da aba `Corte`, o WoodCAM usa descida central como fallback;
- lado dos contornos externos configurável, com corte externo como padrão;
- corte por linha comum com dois contratos explícitos: preservar medidas pela
  coincidência dos percursos compensados ou usar o vetor como centro da fresa;
  cruzamentos, sobreposição de área e propriedade ambígua bloqueiam a prévia;
  no segundo modo, somente fronteiras realmente comprovadas são aceitas e o
  operador confirma a perda aproximada de meio diâmetro em cada peça;
- ao organizar com linha comum e corte externo ativos, a folga acompanha a
  fresa: no modo de preservar medidas ela nunca fica abaixo do diâmetro efetivo;
  no modo sobre o vetor, 0 mm coincide as bordas e uma folga intermediária
  menor que a fresa é corrigida antes da busca. A validação final do nesting
  usa a mesma política do CAM — esquadria apenas nos nós ortogonais de linha
  comum e raio físico nos demais —, impedindo que uma aproximação diagonal
  aceita na prévia cruze somente ao gerar o percurso;
- a compensação externa contorna vértices convexos pelo raio físico da fresa;
  bicos agudos não usam miter ilimitado, que criaria uma ponta artificial e
  falsos cruzamentos entre ripas/trapézios próximos;
- quando a compensação externa fechar uma fenda ou detalhe menor que a fresa,
  o WoodCAM não passa reto silenciosamente: bloqueia a prévia e oferece uma
  confirmação explícita. Se o operador aceitar a perda dimensional, o contorno
  externo permanece compensado e somente a região colapsada recebe um percurso
  auxiliar. Fendas abertas usam seu eixo médio local, inclusive quando são
  afuniladas ou escalonadas, e retiram o mínimo fisicamente possível de cada
  lateral, inclusive no plano de Linha comum;
- a rede de linha comum corta cada fronteira compartilhada uma única vez,
  mantém furos/recortes internos primeiro e usa entrada vertical para não
  repassar uma aresta aberta já usinada; peças isoladas no mesmo trabalho
  conservam seu perímetro exclusivo fechado, inclusive com tabs;
- com **Linha comum ativa**, o padrão é
  `Otimizado — ida/volta + última volta separada`;
  `Por peça`, `Chapa inteira por profundidade` e `Híbrido — estabilidade`
  permanecem como alternativas de compatibilidade e diagnóstico. Essas estratégias
  criam antes do percurso um plano de segmentos físicos
  `INTERNAL`, `SHARED` e `EXTERNAL`, e rejeitam qualquer repetição de
  `segmento + profundidade`;
- com **Linha comum desligada**, o WoodCAM usa sempre o gerador CAM padrão por
  peça; preferências globais antigas são ignoradas e não alteram percurso,
  tabs, fechamento, rampa ou G-code do modo legado;
- no modo híbrido, cada camada ainda cobre fisicamente a chapa inteira, mas as
  camadas não passantes usam `PER_PIECE_COMMON_LINE_ROUTE`: escolhem uma peça,
  consultam os `PhysicalCutSegment` ainda pendentes naquele Z e percorrem
  continuamente apenas o restante; uma SharedEdge executada por qualquer owner
  fica `DONE` para todos os demais owners somente naquela profundidade. O
  restante pode ser um trail aberto e nunca é fechado artificialmente. A ordem
  penaliza fragmentação antes de favorecer peças próximas; o antigo
  `FAST ROUTE` global permanece como comparador interno de desenvolvimento;
- em `Última passada no final (ex.: 2 + última geral)`, todas as camadas anteriores
  à profundidade final de uma peça são concluídas na mesma visita: a primeira percorre os segmentos
  físicos ainda pendentes e a seguinte volta pelo mesmo trail na direção
  inversa. SharedEdges já `DONE` são removidas, portanto peças vizinhas podem
  produzir trails abertos sem fechamento artificial. A troca de peça retrai e
  usa G0 em Z seguro; somente a ida/volta contínua da mesma peça permanece
  engajada. Todas as visitas não passantes priorizam a entrada mais próxima da
  posição atual; fragmentação é apenas desempate. Pontos artificiais criados
  pelos splits de tabs não viram entradas intermediárias. Quando a entrada em
  rampa está ativa, `Suave` faz uma única inclinação contínua sobre o trail e
  termina no ponto inicial já na nova profundidade; `Zigue-zague` é a opção
  explícita de ida e volta. Na primeira camada, a aproximação da rampa suave
  ocorre em Z seguro; nas seguintes, usa somente o kerf comprovadamente aberto
  na profundidade anterior, sem plunge no material nem lead-in fora da
  geometria. Perfis fechados conservam o sentido configurado, pois já
  terminam no próprio ponto de entrada; somente trilhas abertas alternam o
  sentido. Depois dessas visitas, a profundidade final é executada uma única
  vez em uma volta separada pela chapa. Cada segmento físico aparece uma vez
  nessa volta. Para ligar duas entradas, a fresa só permanece baixa sobre um
  kerf de passada anterior quando o caminho inteiro é muito curto; o limite é
  o maior entre 1,5 diâmetro da ferramenta e duas tolerâncias de linha comum.
  Fora disso ela retrai, desloca em G0 e mergulha na próxima entrada. Um trecho
  já cortado na própria profundidade final nunca serve de atalho;
- em `Todas as passadas direto por peça (ex.: 3 direto)`, o mesmo scheduler
  ida/volta recebe todas as profundidades. Uma trilha aberta faz Z1 para fora,
  Z2 voltando e Z3 novamente para fora, sem a antiga reentrada por trail e sem
  criar a fase `final_sheet_pass`; somente depois a máquina troca de peça;
- o tipo de entrada escolhido chega sem substituição ao plano global: `Suave`
  não reutiliza o gerador de zigue-zague; uma espiral é
  usada em perfil fechado somente quando o círculo tangente fica integralmente
  no lado sacrificial e afastado das outras linhas físicas. Trilhas abertas,
  linhas compartilhadas ou regiões sem espaço usam o fallback confinado ao
  próprio kerf, mantendo a entrada segura e a cobertura física única;
- tabs automáticos, manuais, retos ou 3D preservam a peça desde a primeira
  passada. A altura é MDF fisicamente intacto medido a partir da superfície:
  numa chapa de 15 mm, tab de 15 mm mantém a fresa acima do material mesmo que
  a profundidade final seja 15,5 mm; `StockTab` liga uma peça ao skeleton e `SharedTab` liga
  duas peças sem fingir que ambas estão diretamente presas ao stock; tabs são
  mudanças de Z dentro de uma trilha contínua, nunca pequenos percursos
  independentes;
- ao escolher tabs manuais a distribuição automática é desligada e
  `Posicionar tabs...` abre o próprio canvas do Editor 2D: clique perto do
  contorno adiciona, clique na marca remove e botão direito/Esc conclui e volta
  ao Corte. Um clique sobre fronteira comum cria uma única `SharedTab` naquele
  local, em vez de projetá-lo para uma borda externa. A tolerância e o marcador
  são de tela; nenhum vetor é criado ou alterado;
- `Todas as passadas direto por peça (ex.: 3 direto)` conclui todas as
  profundidades de uma peça antes de seguir à próxima; a escolha fica
  preservada ao ligar/desligar Linha comum e é independente da passada lateral
  de acabamento com sobre-metal;
- a distribuição automática analisa cada peça, evita cantos, prefere retas e
  usa no mínimo quatro regiões mecanicamente equilibradas. A quantidade cresce
  por `ceil(perímetro / 300 mm)` e pode ganhar uma tab adicional para que o vão
  real entre centros não ultrapasse 300 mm; peças longas usam os dois lados
  compridos;
- `Fixação de restos soltos` pode ficar desativada, criar tabs integrais ou
  preparar parafusos. O plano reconhece recortes internos e também vazios
  fechados formados entre peças dentro da área da chapa. A conectividade pode
  ser detectada conservadoramente por raster, mas a posição final e a folga são
  sempre validadas contra os vetores exatos. Sem posição segura para a cabeça,
  fresa e margem, o resto recebe duas tabs integrais bem separadas;
- no modo Parafusos, todos os pilotos são executados antes de qualquer contorno;
  depois há retração, retorno à origem segura, `M5`, um único `M0`, retomada do
  spindle e somente então o nesting. Cabeça + margem vira keep-out persistente
  e qualquer operação posterior que invada a região é bloqueada;
- a caixa visível `Remover tabs automaticamente ao final` cria uma fase
  posterior ao corte principal; desmarcada, encerra o programa com todas as
  pontes. Cada tab liberada recebe
  plunge vertical, sweep mínimo de `largura - diâmetro` somente quando
  necessário e retração imediata; entre todas as peças cuja remoção é segura,
  a rota escolhe o conjunto e as tabs mais próximos da posição atual. O grafo
  de retenção continua impedindo que outra peça seja desconectada
  antecipadamente do stock. A supervisão oferece uma pausa antes de todas as
  tabs, antes de cada peça (padrão) ou antes de cada tab;
- preenchimento/rebaixo de área por offset concêntrico ou raster em zigue-zague;
- na aba `Furo`, `Criar assento maior na entrada do furo` gera um rebaixo
  circular de diâmetro e profundidade próprios para alojar a cabeça do
  parafuso, antes de continuar o furo principal. `Profundidade inicial Z` é
  somente a cota já removida onde a usinagem começa; não é um diâmetro;
- em Corte, Furo e Preenchimento, `Profundidade inicial Z` e `Profundidade
  final Z` são cotas absolutas medidas desde a face original: início 6 mm e
  final 7 mm usinam somente o 1 mm restante. O contador e o editor de passagens
  usam esse intervalo; operações antigas são convertidas ao abrir sem mudar a
  profundidade final que estava gravada;
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
- tempo de simulação baseado nos avanços configurados; um slider de 0,1x a
  200x controla a reprodução e responde em tempo real durante o movimento,
  sem reiniciar nem saltar a posição atual;
- janela de configuracao nao-modal, podendo ser ocultada sem parar a simulacao;
- macro antiga ainda disponivel como fallback.

Ainda nao implementado:

- rasgos/canais;
- otimizacao entre varias operacoes;
- pos-processador Mach3;
- edição tipográfica posterior à conversão e modelagem 3D interativa;
  desbaste/acabamento 3D de três eixos já existem, mas reentrâncias (*undercuts*) exigem outra fixação ou CAM de
  quatro/cinco eixos;
- booleanos gerais para caminhos abertos continuam fora de escopo; os fechados
  (soldar, subtrair e interseção) já usam OCC com prévia/Undo;
- Bézier cúbica pode ser desenhada com quatro cliques (início, controle 1,
  controle 2 e fim), preservando a curva exata; importações também a preservam
  quando o formato de origem suportar.

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
2. Abra `Editor 2D`. Desenhe com a barra vertical; os botões expansíveis
   `Editar` e `Reparar` abrem as mesmas ações dos menus superiores, com ícones
   vetoriais e sem duplicar comandos. Use
   `Arquivo → Importar itens da árvore…` para copiar Sketches/Shapes e layouts
   PanelNest selecionados (resolvidos para `CAM Chapa`, sem a chapa-base),
   reorientando arestas OCC na cópia e trazendo uma face por sólido do compound,
   inclusive regiões de rebaixo cego como hachuras selecionáveis com a
   profundidade detectada. Em chapas em pé, fundo e contorno compartilham o
   mesmo plano 2D; rebaixo aberto na borda não deforma a silhueta de corte
   externo. A importação ignora arestas degeneradas sem perder
   o restante da chapa e só aceita furos PanelNest contidos na área útil do
   perfil,
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
   perfis `rápido`, `equilibrado` e `profundo` reduzem ou ampliam a busca sem
   usar aleatoriedade. O perfil equilibrado também conserva alternativas de
   posição/rotação em paralelo e nunca aceita resultado pior que o rápido.
7. Ative `Usar Editor 2D como fonte` para alimentar Corte/Furo/Preenchimento, ou
   use `Enviar PanelNest` para criar o intercâmbio no documento. Para um
   rebaixo importado, clique dentro da hachura e abra `CAM → Preenchimento`;
   a profundidade do modelo é preenchida como sugestão e deve ser confirmada.
   Uma cópia coincidente selecionada sozinha pode receber Corte; selecionar as
   duas cópias juntas continua bloqueado para evitar duas passadas acidentais.
8. Para imprimir o plano, use
   `Arquivo → Enviar para impressão (TechDraw)…`. Escolha a chapa, o papel e
   `Ajustar a chapa inteira em uma página`, `Tamanho real — 1:1` ou uma escala
   percentual. Se 1:1 não couber, o assistente impede o recorte silencioso e
   informa a maior escala possível. Depois de criar, use os comandos normais
   de impressão/exportação da página no TechDraw.
9. Salve normalmente o FCStd. Vetores, camadas, peças e preferências persistem.

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
   Toda geometria 2D — Editor, PanelNest, Sketch, face ou Shape selecionado no
   FreeCAD — permanece no XY do documento. O datum da área define somente a
   saída e o retorno da ferramenta; nunca reposiciona a peça no zero global.
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
    Nas operações 3D, a ligação inicial entre o datum da área de trabalho e a
    primeira peça aparece em magenta tracejado; ela é apenas visual e não
    reposiciona o modelo nem altera os movimentos usados pelo G-code.
13. Clique em `Aplicar` para guardar a operação em `WoodCAM → Operações`. A
    árvore possui uma única raiz `WoodCAM`, com `Peças`, `Operações` e
    `Área de trabalho`; cada tipo de operação recebe sua própria numeração e
    não substitui as operações já aplicadas.
14. Use a aba `Simulação e Salvar` como gerenciador das operações aplicadas.
    É possível renomear, abrir os parâmetros para edição, excluir com Undo e
    arrastar as linhas para definir a ordem persistida de simulação/exportação.
    Enquanto uma edição estiver aberta, use `Cancelar edição` para voltar a
    criar uma operação nova sem modificar o percurso aplicado existente.
    Selecione **uma** operação para conferir o rodapé `APLICADO = G-CODE`,
    inclusive o passo físico mostrado. Se trocar a fresa ou o stepover, a
    trajetória antiga desaparece e só volta após novo `Pré-visualizar` ou
    `Aplicar`.
    Corte e Preenchimento/Rebaixo usam uma projeção Coin leve: o percurso é
    desenhado uma vez e somente a fresa é animada, preservando a lista integral
    do G-code sem reconstruir milhares de segmentos a cada quadro.
15. Na seção `Exportação` dessa mesma aba, escolha o arquivo pelo botão com
    ícone de pasta e clique em `Gerar G-code`. Os itens selecionados são
    exportados na ordem exibida; sem seleção, todos são exportados nessa ordem.
    Para uma operação 3D, o rodapé muda para
    `G-CODE GERADO = VISTA`: a tela e o arquivo foram alimentados pela mesma
    lista de movimentos. Os percursos 3D usam os sufixos `_desbaste_3d` e
    `_acabamento_3d`; se um arquivo já existir, o WoodCAM acrescenta `_02`,
    `_03` etc.

Na própria linha `Editor 2D`, o botão de maximizar destaca o mesmo Editor em
uma janela nativa. Fechar essa janela devolve a mesma sessão à aba, com
documento, seleção e histórico preservados. O botão isolado acima de
`Trabalho` é diferente: ele solta ou prende a janela inteira do WoodCAM.

Os campos de `Tamanho da área de trabalho` possuem uma lista de tamanhos
salvos. `Salvar tamanho atual…` grava X, Y e Z nas preferências pessoais do
FreeCAD; selecionar uma predefinição preenche os campos e atualiza a área do
Editor por um único comando com Undo, sem alterar origem, material ou vetores.
Excluir uma predefinição não modifica a área que já está em uso.

As ferramentas do Editor usam uma seleção local dos SVGs Tabler Icons, com a
licença MIT preservada em `resources/icons/tabler/LICENSE.txt`; o desenho
programático anterior continua como fallback caso um recurso não possa ser
carregado. Confirmar um vetor amplia somente a região navegável da cena e
preserva a câmera/zoom atuais — enquadramento só ocorre por ação explícita.
Essa confirmação também não reabre nem descompacta os percursos CAM já
aplicados: ela apenas marca como desatualizadas as operações dependentes da
geometria editada. A estimativa de tempo é recalculada quando a lista de
operações é realmente consultada ou modificada.

Seleção de grupo e limites da seleção usam índices associados à revisão do
`VectorDocument`. `Copiar` cria no clipboard somente o objeto selecionado, seus
filhos e relações `Piece2D`; vetores não relacionados do móvel não são mais
clonados. `Colar` continua sendo um único comando com Undo/Redo.

Na organização progressiva, a opção de retalho separa sucessivamente os
maiores retângulos vazios da chapa que não atravessam peças, inclusive regiões
internas abaixo ou ao lado do conjunto ocupado. Assim, uma sobra em L pode
produzir uma faixa superior, outra lateral e um bloco central guardável. Uma
região interna recebe todas as linhas de separação necessárias, sem repetir
uma borda já proposta. As linhas e dimensões aparecem primeiro na prévia; ao
aplicar, cada linha vira um
`PathEntity` aberto, tracejado, selecionável e persistido no mesmo
`CompositeCommand`/Undo da organização. Essas linhas não são peças, não
bloqueiam o diagnóstico comum e não entram silenciosamente no G-code. Uma
seleção total que contenha peças e essas linhas continua produzindo o Corte
normal das peças; as linhas laranjas são ignoradas nesse escopo misto. Para
usiná-las, selecione somente as linhas desejadas e crie uma operação `Corte`:
o WoodCAM ativa essa fonte automaticamente e segue o eixo aberto do vetor, sem
fechar nem compensar o caminho.

Uma organização contendo uma única ocorrência sempre ancora o menor X/Y de
sua orientação validada no X0/Y0 da chapa. Na linha comum, se a remoção
automática das tabs não possui nenhuma ordem que mantenha as peças restantes
presas, o WoodCAM aplica o corte válido mantendo as tabs e informa a decisão;
não descarta a operação nem força uma liberação insegura.

No Editor 2D, o menu `CAM` concentra o fluxo de percurso sem ocupar a faixa
inferior: ele abre a configuração existente de Corte, Furos ou Rebaixo e pode
projetar qualquer um desses percursos no próprio plano. A projeção é somente
visual e usa exatamente os movimentos do G-code. O botão inferior
`Percursos 2D` oferece as mesmas escolhas quando o usuário está em outra aba.
O encaminhamento usa o identificador interno da operação, portanto diferenças
de apresentação como `Furos` no menu e `Furo` na aba não impedem abrir a
configuração nem aplicar a operação.

Ao enviar peças ao PanelNest, círculos pequenos importados como polilinhas
fechadas são reconhecidos de forma conservadora como furos (até 12 mm, com
circularidade validada). Assim, o furo continua vinculado ao contorno externo
e chega ao sólido/PanelNest como cilindro, sem promover o recorte a peça solta.
Caminhos abertos independentes permanecem no desenho e no diagnóstico, mas não
impedem o reconhecimento e o envio das demais peças fechadas válidas. Erros
geométricos reais da própria peça continuam bloqueando o envio.

`Enviar PanelNest` sempre refaz as relações `Piece2D` a partir dos vetores
atuais e envia o layout completo. Uma seleção residual no Editor não limita o
intercâmbio silenciosamente; todas as peças válidas, suas quantidades, furos e
recortes internos seguem juntas, preservando XY e rotação. O rodapé informa as
contagens de peças, ocorrências, furos e recortes realmente materializados.

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
