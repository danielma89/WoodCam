# Manual de Publicacao no Cloudflare Pages

## Objetivo

Este manual descreve o fluxo recomendado para que cada marcenaria, bancada ou cliente use a sua propria conta Cloudflare Pages para publicar o guia de montagem do PanelNest.

O objetivo de produto e este:

- cada operacao usa a propria conta cloud
- a configuracao inicial e feita uma unica vez por um responsavel
- no uso diario, o operador so precisa exportar e publicar

## Estado atual do PanelNest

Hoje o PanelNest ja faz estas partes:

- exporta o pacote web do guia de montagem em HTML
- gera um HTML geral e um HTML por peca
- permite configurar uma URL publica base do guia em `Configurar Chapa > Montagem`
- coloca essa URL nos QRs das etiquetas quando ela esta configurada
- permite informar o nome do projeto Cloudflare Pages
- publica automaticamente no Cloudflare Pages via Wrangler durante a exportacao, quando essa opcao esta ligada

Hoje o que ainda depende do usuario:

- criar a conta Cloudflare
- criar o projeto Pages
- instalar o prerequisito local para publicacao automatica na maquina da bancada

## Recomendacao para a bancada

Para este projeto, a recomendacao e usar:

- `Cloudflare Pages`
- `Direct Upload`
- `Wrangler CLI` para a publicacao automatica da bancada

Motivo:

- o conteudo do PanelNest nasce localmente no FreeCAD, nao em um repositorio Git
- `Direct Upload` aceita arquivos HTML pre-prontos
- um projeto `Direct Upload` tambem pode receber novas implantacoes com `Wrangler CLI`
- `Wrangler` suporta mais arquivos do que o arraste-e-solte do navegador

Observacao importante:

- se o projeto for criado como `Direct Upload`, ele nao pode ser convertido depois para `Git integration`

## Conceitos rapidos

### Cloudflare Pages

Servico de hospedagem estatica da Cloudflare. O projeto fica disponivel em uma URL como:

```text
https://meu-projeto.pages.dev
```

### Direct Upload

Modo de projeto em que os arquivos HTML, CSS e JS ja prontos sao enviados para a Cloudflare sem build no GitHub ou GitLab.

### Wrangler CLI

Ferramenta oficial de linha de comando da Cloudflare. Ela permite publicar a pasta exportada pelo PanelNest usando comando em vez de abrir o navegador e fazer upload manual.

Comando principal:

```bash
npx wrangler pages deploy <PASTA_EXPORTADA>
```

## Fluxo de onboarding para usuario novo

### 1. Criar a conta Cloudflare

1. Acesse a Cloudflare e crie uma conta gratuita.
2. Entre no painel da conta.
3. Abra `Workers & Pages`.

### 2. Criar o projeto Pages da empresa

1. Escolha `Pages`.
2. Escolha `Arraste e solte seus arquivos`.
3. Informe um nome simples para o projeto.

Exemplos:

- `bancada-alfa`
- `painelnest-montagem`
- `marcenaria-silva-guia`

Resultado:

- a Cloudflare vai criar uma URL publica `https://<NOME_DO_PROJETO>.pages.dev`

### 3. Fazer a primeira implantacao manual

Esta primeira implantacao serve para validar que o projeto da empresa esta correto.

1. No FreeCAD, exporte os arquivos do PanelNest para uma pasta.
2. No projeto Cloudflare Pages, clique em `Criar implantacao`.
3. Envie a pasta exportada ou um arquivo ZIP com esse conteudo.
4. Aguarde o deploy terminar.
5. Teste uma URL de arquivo real, por exemplo:

```text
https://painelnest-montagem.pages.dev/gaveteiro_teste_guia_montagem_pn-006-01-01.html
```

Observacao:

- a raiz do site pode nao ter `index.html`
- por isso, o teste correto deve ser feito em um HTML real do guia ou por um QR ja configurado

### 4. Configurar a publicacao no PanelNest

1. No FreeCAD, abra `Configurar Chapa`.
2. Entre na aba `Montagem`.
3. Preencha `URL publica base do guia` com a URL do projeto.
4. Preencha `Projeto Cloudflare Pages` com o nome do projeto.
5. Ative `Publicar automaticamente no Cloudflare Pages via Wrangler`.

Exemplo:

```text
https://painelnest-montagem.pages.dev
```

Importante:

- nao coloque o nome de um arquivo HTML nesse campo
- coloque apenas a URL base do projeto
- o nome do projeto e normalmente o mesmo trecho usado antes de `.pages.dev`

### 5. Configurar as credenciais locais da maquina

Na primeira vez em cada maquina:

1. instale `Node.js`
2. crie um `API Token` da Cloudflare com permissao para `Pages`
3. copie o `Account ID` da conta Cloudflare
4. no FreeCAD, abra `Configurar Chapa > Montagem`
5. preencha `Cloudflare Account ID`
6. preencha `Cloudflare API Token`

Alternativa avancada:

- em vez de salvar na tela, a bancada tambem pode usar as variaveis de ambiente `CLOUDFLARE_ACCOUNT_ID` e `CLOUDFLARE_API_TOKEN`

### 6. Exportar e publicar com um clique

Depois da configuracao:

1. exporte novamente
2. o mesmo comando `Exportar Arquivos` vai:
3. exportar os arquivos locais
4. publicar no Cloudflare Pages
5. gerar as etiquetas com QR apontando para a URL publica
6. confirme no console do FreeCAD que o guia foi publicado automaticamente

Resultado esperado:

- os novos QRs passam a apontar para o `pages.dev`
- o operador nao precisa entrar no painel da Cloudflare no uso diario

## Operacao diaria da bancada

Depois da configuracao inicial da maquina, o uso diario fica assim:

1. o operador abre o projeto no FreeCAD
2. clica em `Exportar Arquivos`
3. o PanelNest exporta o lote e publica no Cloudflare Pages
4. o QR do lote passa a abrir a versao online mais recente

## Fluxo alvo para a bancada

O fluxo mais adequado para a bancada e este:

### Configuracao unica por maquina

Feita por um admin ou implantador:

1. instalar `Node.js`
2. instalar ou usar `Wrangler`
3. configurar `Account ID` e `API Token` locais da Cloudflare
4. vincular a maquina ao projeto Pages da empresa
5. configurar a URL publica base no PanelNest

### Uso diario por qualquer operador

1. abrir o projeto no FreeCAD
2. clicar em `Exportar Arquivos`
3. esperar o PanelNest exportar os HTMLs e executar o deploy
4. imprimir ou usar as etiquetas com QR

## O que o PanelNest deve automatizar

O fluxo atual de um clique ja assume estes passos:

1. exportar a pasta web
2. detectar a configuracao cloud do documento
3. chamar o deploy do Cloudflare com `Wrangler`
4. mostrar sucesso ou erro de publicacao ao usuario
5. gerar as etiquetas usando a URL publica

Melhorias futuras recomendadas:

1. botao dedicado `Testar conexao`
2. diagnostico guiado para falta de `Wrangler`, `Account ID` ou `API Token`
3. assistente visual para criar o projeto Cloudflare pela primeira vez

## Recomendacao de UX para o produto

Para um ambiente de bancada, a interface ideal e:

### Janela de configuracao cloud

Campos:

- `Fornecedor`: Cloudflare Pages
- `Nome do projeto`
- `URL publica base`
- `Metodo de publicacao`: manual ou automatica
- `Status da autenticacao`

### Botoes

- `Testar conexao`
- `Exportar e Publicar`
- `Abrir projeto no Cloudflare`

### Mensagens claras

- `Publicacao concluida em https://...`
- `Wrangler nao encontrado nesta maquina`
- `Login Cloudflare necessario`
- `Falha ao publicar. Veja o log abaixo.`

## Recomendacao de implantacao

Para uma empresa ou bancada, a recomendacao e:

- cada empresa usa a propria conta Cloudflare
- cada empresa tem um projeto Pages proprio
- um admin faz a configuracao inicial da maquina
- os operadores nao precisam saber usar o painel da Cloudflare

## Limites importantes

Pelos limites atuais documentados pela Cloudflare:

- `Wrangler`: ate 20.000 arquivos por deploy
- `Drag and drop`: ate 1.000 arquivos por deploy
- tamanho maximo por arquivo: `25 MiB`

Como o guia de montagem do PanelNest pode gerar muitos HTMLs por peca, a recomendacao para o fluxo definitivo da bancada e usar `Wrangler`, nao o upload manual do navegador.

## Fontes oficiais

- Getting started: https://developers.cloudflare.com/pages/get-started/
- Direct Upload: https://developers.cloudflare.com/pages/get-started/direct-upload/
- Git integration: https://developers.cloudflare.com/pages/get-started/git-integration/
- Continuous integration com Direct Upload: https://developers.cloudflare.com/pages/how-to/use-direct-upload-with-continuous-integration/
- Limites da plataforma: https://developers.cloudflare.com/pages/platform/limits/
