# Resultado DA2 × DA3 no CPU

Execução preservada em 16 de julho de 2026, sem OpenVINO e sem acesso à GPU.

- máquina: Ryzen 5 5600, 6 threads do PyTorch;
- entrada: `peixei.png` 812 × 436 e `leao.png` 815 × 451;
- resolução de processamento solicitada: 504;
- DA2 Small: peixe 1,032 s; leão 0,958 s;
- DA3 Small: peixe 0,207 s; leão 0,203 s.

Conclusão visual: DA2 Small é o candidato de produção. Ele preserva melhor o
volume do peixe, as nadadeiras e a estrutura da face/juba do leão. DA3 Small é
mais rápido neste CPU, porém suaviza demais a forma e cria massas menos naturais
no leão. Os arquivos `*_comparison.png` mostram original, profundidade bruta,
forma isolada, microtextura, altura híbrida e prévia sombreada.

Esta pasta é evidência de uma prova isolada. Nenhum mapa daqui alimenta CAM ou
altera o relevo de produção automaticamente.

## OpenVINO no CPU

O relatório `openvino_cpu_report.json` concluiu a etapa que foi interrompida:

- DA2 converteu em 2,399 s e inferiu em mediana de 0,095 s na resolução 280;
- concordância PyTorch × OpenVINO: correlação 0,9999987 e erro absoluto médio
  0,002373 no tensor bruto;
- DA3 não converte diretamente: o frontend não implementa
  `aten::cartesian_prod`;
- nenhuma execução OpenVINO em GPU foi realizada nesta retomada.

Assim, o caminho técnico aprovado é DA2 Small + OpenVINO CPU, como recurso
opcional. Ele é mais simples, mais fiel visualmente e não depende do driver de
compute da placa que mantém o vídeo.
