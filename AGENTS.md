# LEIA PRIMEIRO — continuidade do Editor 2D do WoodCAM

Este arquivo é obrigatório para qualquer IA ou pessoa que vá alterar o Editor
2D, vetores, organização de peças ou a ponte Editor → CAM.

## Documento obrigatório

Leia integralmente, na ordem:

1. [`docs/PLANO_MESTRE_EDITOR_2D.md`](docs/PLANO_MESTRE_EDITOR_2D.md)
2. [`docs/RELATORIO_IMPLEMENTACAO_EDITOR_2D.md`](docs/RELATORIO_IMPLEMENTACAO_EDITOR_2D.md)
3. [`README.md`](README.md), para conhecer as funções já estáveis do WoodCAM.

O plano mestre registra a decisão de produto, a auditoria do protótipo, o
contrato exato de interação, a arquitetura, o modelo de dados, os módulos, a
ordem de implementação, os testes e os critérios de aceite.

## Estado que deve ser respeitado

- O Editor 2D de produção está em `woodcam_editor/`. `VectorDocument` é a fonte
  de verdade, comandos são as únicas mutações e a cena é somente projeção. Não
  acrescente estado geométrico paralelo em `ui.py` ou em `QGraphicsItem`.
- `vector_editor.py` é a prova de conceito congelada. Ela permanece apenas como
  registro histórico e não deve receber ferramentas novas.
- A aba `2D (Sketch)` e `vector_diagnostics.py` são um experimento legado. A
  tentativa de corrigir Sketches por restrições do Sketcher deformou geometria
  e deixou o Undo indisponível. Preserve-a somente para diagnóstico/importação
  até a substituição e não faça mutações automáticas no Sketch.
- O WoodCAM de produção já possui Trabalho, Material, Fresas, Corte, Furo,
  Preenchimento, simulação, G-code, operações persistentes e integração com
  PanelNest. Não quebre nem reescreva essas áreas para construir o editor.

## Contrato inegociável de interação

```text
1 clique no vetor        = selecionar o objeto inteiro
arrastar o corpo         = mover o objeto/seleção inteira
2 cliques ou tecla N     = entrar na edição de nós
1 clique no nó           = apenas selecionar o nó
pressionar e arrastar nó = mover a partir da posição original
clique solto             = nunca teletransporta nem altera geometria
Esc                      = cancelar operação/sair do modo; depois limpar seleção
botão direito            = cancelar prévia/ferramenta e voltar a Selecionar
```

Nós não podem capturar o mouse no modo Selecionar. Tolerâncias de hit-test e
snap são em pixels de tela e independem do zoom. Toda mutação é um comando
atômico com Undo/Redo; `mouseMove` mostra apenas a prévia e `mouseRelease`
confirma um único comando.

Para reparar aberturas, não confundir os contratos:

- `Fechar caminho / unir próximas`: decisão automática limitada pela tolerância;
- `Unir 2 pontas (reta)`: operador escolhe duas pontas de caminhos diferentes;
  lacuna grande recebe uma reta sem mover/deformar as geometrias originais;
- `Projetar ponta na geometria`: leva uma ponta até o corpo de uma reta, arco
  ou círculo e pode gerar ramificação, sempre avisada na prévia.

## Regras de segurança

1. Antes de editar, leia o plano inteiro e identifique a fase ativa.
2. Não use `QGraphicsItem` como fonte de verdade. A cena é somente projeção do
   `VectorDocument`.
3. Não modifique Sketches, Shapes ou o arquivo FCStd de origem durante uma
   importação. A primeira versão importa uma cópia independente.
4. Não faça correção geométrica sem prévia, escopo explícito e Undo.
5. Não avance de fase com testes ou critérios de aceite pendentes.
6. Mantenha IDs estáveis, milímetros e o eixo cartesiano Y positivo no domínio.
7. Preserve a compatibilidade PySide6/PySide2 usada pelo FreeCAD.
8. Toda ponte com CAM deve produzir o contrato atual:
   `{"contours": [[(x, y), ...]], "holes": [...]}`.
9. Furos e recortes internos pertencem à peça externa e nunca podem ser
   organizados como peças soltas.
10. Antes de entregar, execute os testes puros, os testes Qt e o roteiro manual
    da fase descritos no plano mestre.
11. `Reconhecer peças e furos` somente cria relações `Piece2D`; não duplica nem
    redesenha vetores. O organizador usa contorno raster real e mantém externos
    e descendentes como unidade.
12. A vetorização de bitmap é preview-first. Pixels nunca pertencem ao
    `VectorDocument`; somente as curvas confirmadas entram por comando/Undo.
13. O intercâmbio PanelNest preserva o layout XY do Editor e não pode disparar
    nesting automaticamente.

## Primeira ação esperada

Antes de uma mudança, reproduza o caso e execute os testes da camada afetada.
Reaproveite o domínio, os comandos e adapters entregues; preserve IDs e dados
FCStd. Antes de concluir, rode os testes unitários, Qt, legados e os smokes
FreeCADCmd documentados no relatório. Uma mudança visual não pode mutar o
documento sem comando, e uma importação nunca pode alterar a fonte.
