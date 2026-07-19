# Prova isolada de profundidade por IA

Esta pasta registra a comparação que escolheu o modelo usando as imagens reais
do peixe e do leão. Ela **não é importada pelo WoodCAM de produção** e seus
scripts não alteram documento FreeCAD, `VectorDocument` nem G-code. A integração
opcional aprovada vive em `woodcam_relief/` e reaproveita o resultado técnico,
sem depender deste diretório experimental.

Modelos avaliados:

- Depth Anything V2 Small — Apache 2.0;
- Depth Anything 3 Small — Apache 2.0.

Os pesos e o ambiente Python ficam fora do projeto. O script preserva a imagem
original e grava três resultados por modelo:

- `*_depth.png`: profundidade normalizada para inspeção;
- `*_relief.png`: altura preparada para relevo, com o fundo dos cantos na base;
- `*_shaded.png`: prévia sombreada do mesmo mapa de altura;
- `*_comparison.png`: comparação lado a lado.

O mapa de relevo ainda é uma prova de qualidade. Ele não deve alimentar CAM até
que a orientação, o fundo e a escala sejam confirmados pelo usuário.

`benchmark_openvino.py` também confere a concordância numérica entre PyTorch e
OpenVINO antes de considerar a execução na GPU. Um resultado rápido não é
aceito se mudar o mapa de altura gerado.

## Segurança da GPU

Em 16 de julho de 2026, uma execução OpenVINO na Intel Arc B570, usando o
driver Linux `xe`, causou falha de memória do engine, repetidos resets e o
estado `device wedged`. A sessão Niri/Xorg e outros programas que usavam a GPU
foram encerrados. Por isso, o benchmark agora usa `CPU` por padrão e bloqueia
`GPU` sem a confirmação adicional `--allow-gpu`. Não execute esse opt-in na
mesma GPU que mantém o vídeo da sessão de trabalho.

## Resultado preservado — CPU

A comparação em resolução 504 com seis threads está em `results_cpu/`. Nos
arquivos reais avaliados, DA2 Small preservou melhor a forma anatômica do peixe,
as nadadeiras e a face/juba do leão. DA3 Small foi mais rápido, mas produziu
volume excessivamente liso e massas menos naturais no leão. Para a primeira
integração opcional, o candidato aprovado é **Depth Anything V2 Small em CPU**;
DA3 permanece apenas como experimento comparativo.

O benchmark OpenVINO CPU confirmou DA2 com correlação 0,9999987 em relação ao
PyTorch e mediana de 0,095 s na entrada reduzida. DA3 não converte diretamente
por conter `aten::cartesian_prod`, operação ainda não suportada pelo frontend
usado. O relatório completo está em `results_cpu/openvino_cpu_report.json`.

## Segunda IA — prova de normais

`metric3d_normals_proof.py` avalia isoladamente o Metric3D v2 Small ONNX. Esse
modelo fornece profundidade, normal de superfície XYZ e confiança por pixel; a
prova roda somente em CPU e grava mapas e sombreamento clay neutro para inspeção.
Nenhuma saída entra no WoodCAM até a fusão depth+normals superar a DA2 nos casos
reais e passar pelo mesmo contrato preview-first. DSINE foi descartada porque
sua licença limita o uso a pesquisa não comercial.

Exemplo do ambiente usado nesta máquina:

```bash
PYTHONPATH=/tmp/Depth-Anything-V2:/tmp/depth-anything-3/src \
  /tmp/woodcam-relief-ai-venv/bin/python \
  experiments/relief_ai/run_comparison.py \
  --fish /home/danielma/Downloads/peixei.png \
  --lion /home/danielma/Downloads/leao.png \
  --da2-weights /tmp/woodcam-ai-models/hub/models--depth-anything--Depth-Anything-V2-Small/snapshots/03876f8651c73a60fe4c2c48294e09fcb6838fcf/depth_anything_v2_vits.pth \
  --da3-dir /tmp/woodcam-ai-models/hub/models--depth-anything--DA3-SMALL/snapshots/e08cab65ca0ec38e7826075418411ab90cab4da3 \
  --output /tmp/woodcam-relief-ai-results
```
