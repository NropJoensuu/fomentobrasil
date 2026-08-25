# Inventário de Necessidades — Fomento Brasil

Decisões e ideias já discutidas, mas que não entram no MVP imediato. Registradas aqui para não se perderem entre sessões.

## Autenticação e submissão pública de vagas

- Permitir que pesquisadores e empresas submetam vagas diretamente (modelo Euraxess/FAPESP), além de dado agregado por scraper.
- Login via **ORCID**, não via Lattes (Lattes não oferece OAuth/login para terceiros). ORCID já é usado como forma de autenticação em plataformas internacionais como o Euraxess, dá autoridade acadêmica real e reduz risco de golpe/spam.
- Vaga submetida por usuário deve passar por moderação (status pendente → aprovado) antes de aparecer na busca pública — mesmo princípio que a FAPESP usa (ela se isenta de responsabilidade pelo conteúdo, responsabilidade fica com quem submeteu).
- Schema já prevê os campos `origem` e `status` desde já (ver `app/models.py`), mas a funcionalidade completa (tabela de usuários, formulário público, fluxo de moderação) fica para uma fase posterior.

## Modelo de dados — refinamentos identificados via benchmark

- `nivel_formacao` (mestrado, doutorado, pós-doutorado, iniciação científica, não aplicável) — campo de filtro comum em bolsas ligadas a projeto (padrão FAPESP/Euraxess).
- `area_conhecimento` deveria suportar múltiplos valores (lista), não string única — oportunidades interdisciplinares são comuns.
- `cidade` como campo específico, complementar a `abrangencia`/`uf` — relevante para vagas ligadas a uma instituição física.
- Distinção entre `fonte` (quem financia) e `instituicao_executora` (onde a pessoa vai atuar de fato) — hoje confundidos num único campo `instituicao`.

## Camada de BI / analytics

- Modelo estrela (fato + dimensões) fica para uma fase futura, quando houver volume real de dados para analisar (ex: "quanto o CNPq financiou em BI vs FAPESP nos últimos 3 anos").
- Não vale forçar o schema operacional (usado pela busca do site) a virar estrela agora — otimizado para OLTP, não OLAP.

## Scrapers

- PDFs de editais (comuns em FAPs) vão exigir `pdfplumber` ou similar — não é parte do MVP inicial de scraper (que cobre HTML via `requests`+`BeautifulSoup`).

### Primeiro scraper: CNPq (chamadas abertas)

`scrapers/cnpq.py` coleta https://www.gov.br/cnpq/pt-br/chamadas/abertas-para-submissao 
(server-rendered, sem JavaScript). Roda manualmente:

```bash
python -m scrapers.cnpq     # ou: python scrapers/cnpq.py
```

Insere tudo como `status="pendente"`, para curadoria antes de aparecer publicamente. 
Deduplica por `link`, então rodar de novo não gera cópias — só reporta "já existentes". 
Ainda não há agendamento (cron/scheduler): a execução é manual por ora.

Detalhes de parsing que não são óbvios e já quebraram uma versão anterior:

- A descrição e as datas **não** são irmãs diretas do `<h2>` — ficam aninhadas em `<div>`s 
  irmãos (`#parent-fieldname-text`). Procurar um `<p>` irmão do `<h2>` não encontra nada.
- O rótulo antes das datas varia ("Inscrições:", "INSCRIÇÕES:", "Recebimento das propostas:", 
  "Inscrições 2ª Rodada:"), então o regex casa só o par de datas, ignorando o rótulo.
- Há datas com espaço no meio (`18/09 /2026`, resquício de `\xa0`) e anos de 2 dígitos 
  (`30/06/26`). O parser tolera ambos.
- `data_publicacao` vem do "Publicado em" da página, **não** do início das inscrições — são 
  conceitos distintos e divergem na prática (ex.: CNPq/ERC 21/2026, publicada 04/08 com 
  inscrições desde 03/08; CNPq/SETEC 13/2026, publicada 28/04 com 2ª rodada em 04/08).

### Segundo scraper: FAPESP (chamadas de propostas)

`scrapers/fapesp.py`, roda igual ao do CNPq (`python -m scrapers.fapesp`), também grava 
como `status="pendente"` e deduplica por `link`.

**Usar a listagem anual, não a página de categorias.** A URL correta é 
`https://fapesp.br/2185/chamadas-de-propostas-2026` — uma lista única e limpa (41 chamadas 
em 2026-08-10). A página `https://fapesp.br/chamadas/` repete a mesma chamada em várias 
categorias, gerando duplicatas.

Detalhes de parsing:

- O seletor é `ul.list > li` (há um único `<ul class="list">` na página). Ancorar assim é 
  obrigatório: o texto completo da listagem aparece **triplicado** no HTML, porque também é 
  embutido nas meta tags `og:description` e `twitter:description`. Uma busca solta por `<li>` 
  pegaria lixo de menu de navegação.
- Cada `<li>` tem as linhas separadas por `<br>`, nesta ordem: título (link), 
  "Chamada FAPESP NN/AAAA", linha(s) de prazo, descrição livre e "Apoio: ...".
- **Quando há mais de um prazo, vale o último.** Acontece em dois formatos: pré-proposta vs 
  proposta completa (ex.: T-AP, 08/07 e 28/10 → vale 28/10) e chamadas com dois ciclos 
  (ex.: FICA-SP, 30/09/2026 e 26/02/2027 → vale 26/02/2027). O texto cru fica em 
  `dados_extra.prazo_bruto` para conferência na curadoria.
- Classificar linha de prazo pelo **início** da linha (rótulo "Prazo(s)", "Data(s) limite", 
  "Inscrições"), não pela presença do termo em qualquer posição — há descrições que mencionam 
  "submissão" no meio do texto e seriam engolidas. Pelo mesmo motivo, a linha de instituições 
  exige os dois-pontos (`Apoio:`): existe descrição começando com "Apoio a projetos bilaterais...".
- Rótulos de prazo no plural existem ("Datas limite ..."), então o regex precisa aceitar `datas?`.
- Datas por extenso ("6 de abril de 2026") são entendidas. Datas **sem ano** ("Prazos: 21/05 
  ... e 17/08") ficam com `data_prazo=None` de propósito — inferir o ano seria chute; o 
  curador resolve pelo `prazo_bruto`. Hoje isso afeta 1 das 41 chamadas.
- `dados_extra.chamada_numero` guarda o "Chamada FAPESP NN/AAAA" para referência/citação.

### Terceiro scraper: FAPEMIG (API JSON, não scraping de HTML)

`scrapers/fapemig.py`, roda igual aos outros (`python -m scrapers.fapemig`).

**A FAPEMIG não precisa de scraping de HTML**: o site é Nuxt + WordPress headless e expõe 
uma API REST com namespace próprio, que devolve os dados já estruturados — mais robusto e 
imune a mudança visual do site.

```
GET https://api.site.fapemig.br/wp-json/fapemig-chamadas-e-editais/v1/chamadas
    ?publicacao_status=publish&page=N
```

Resposta paginada: `{"data": [...], "total": N, "per_page": 20, "pagination": {"total_pages": N}}`. 
O `link` público é montado como `https://fapemig.br/oportunidades/chamadas-e-editais/{slug}` 
(confirmado que resolve, HTTP 200).

**Caminho de descoberta, para tentar em outras FAPs antes de partir para HTML:** o site é 
Nuxt, então o `_payload.json` da página revela de onde os dados vêm → daí chega-se ao 
`/wp-json/` do WordPress → e finalmente ao namespace específico 
(`fapemig-chamadas-e-editais/v1`). Vale testar esse caminho sempre que a FAP tiver cara de 
SPA moderna (Nuxt/Next/React) — o custo de checar é baixo e o ganho é grande.

Pontos de atenção:

- **Só ~6% das chamadas estão abertas.** Em 2026-08-10: 189 publicadas, mas 12 `aberta`, 
  136 `encerrada`, 33 `resultados`, 8 `analise`. O scraper importa **só as abertas** por 
  padrão (`coletar_chamadas_fapemig(apenas_abertas=False)` traz todas). Motivo concreto: 
  113 das encerradas não têm `data_fim_submissao`, e o site calcula "aberta" quando 
  `data_prazo` é nulo — elas apareceriam como "Chamada aberta" sem estarem.
- Das 12 abertas, 4 legitimamente não têm prazo (Portarias de credenciamento em fluxo 
  contínuo) — nesse caso exibir "Aberta" está correto.
- `descricao_chamada` às vezes vem com HTML (`<ul><li>`); é limpo com BeautifulSoup.
- `data_divulgacao_resultado` vem como dict (`{"label":..., "data":...}`) ou `null`.
- `status_chamada` da FAPEMIG não é o nosso vocabulário: só `resultados` tem equivalente 
  (`status_oficial="resultado_divulgado"`). `encerrada` não tem — no nosso modelo isso 
  deriva de `data_prazo`.
- **A API já classifica linha de fomento, público-alvo e área de conhecimento** 
  (`linhas_fomento`, `publico_alvo`, `areas_conhecimento`, cada um com `selected`). Todos os 
  rótulos brutos vão para `dados_extra`; além disso, `publico_alvo` é convertido para a nossa 
  coluna (ver abaixo). Este é o único scraper que chega com público-alvo já preenchido — 
  CNPq e FAPESP não expõem essa informação na listagem.

#### Público-alvo: mapeado automaticamente

O vocabulário de público-alvo da FAPEMIG tem 5 valores, e 4 são idênticos aos nossos, então 
são convertidos direto (`MAPA_PUBLICO_ALVO` em `scrapers/fapemig.py`):

| FAPEMIG | nosso |
|---|---|
| `pesquisadores` | `pesquisadores` |
| `empresas` | `empresas` |
| `governo` | `governo` |
| `ict` | `ict` |
| `ambiente-de-inovacao` | *(sem equivalente — descartado)* |

`ambiente-de-inovacao` (incubadora, parque tecnológico, aceleradora) fica de fora de 
propósito: **não é sinônimo de `startups`**, que é o valor mais próximo que temos. Ele 
continua visível em `dados_extra["publico_alvo_fapemig"]` para o curador decidir caso a caso. 
Se aparecer com frequência, é candidato a virar valor novo do nosso vocabulário.

### Quarto scraper: FAPES (5 categorias, sem prazo de submissão)

`scrapers/fapes.py`, roda igual aos outros (`python -m scrapers.fapes`).

A FAPES publica editais abertos em **5 páginas separadas por categoria** (Carreira 
Científica, Pesquisa, Difusão do Conhecimento, Extensão, Inovação), todas no mesmo template 
HTML server-rendered (Orchard CMS). Confirmado em 2026-08-10 que **não há API JSON** 
(`/api/editais` e `/wp-json/` devolvem a página de erro padrão do CMS, não dados 
estruturados) — diferente da FAPEMIG, aqui é scraping de HTML mesmo.

Cada edital é uma `<table class="table-downloads">` (um "acordeão"), e cada linha dessa 
tabela é **um documento** (`<th class="coluna-1">` com link, título em `span.conteudo-value` 
e descrição em `div.caption span.caption-value`; data em `span.dataatualizacao-value` na 
coluna seguinte). Um mesmo edital frequentemente aparece com **múltiplas linhas** — versão 
original + 1ª alteração + 2ª alteração/retificação, cada uma com seu próprio link — e o 
scraper grava todas como registros separados (dedup só por `link`, que é único por PDF). A 
categoria "Extensão" pode legitimamente ter zero editais abertos no momento (bloco vazio, 
sem tabela).

**Limitação sem precedente nos scrapers anteriores: não há prazo de submissão na listagem.** 
A única data disponível é "Atualização", que é a data de última modificação do **arquivo 
PDF**, não o prazo da chamada (o prazo só existe dentro do PDF, que não é extraído nesta 
fase — ver "PDFs de editais" acima). Por isso `data_prazo` fica sempre `None` nos registros 
da FAPES; a data do PDF vai só para `dados_extra["documento_atualizado_em"]`, como 
referência. Um curador precisa abrir cada PDF pendente da FAPES para preencher `data_prazo` 
manualmente até que a extração de PDF seja implementada.

### RESOLVIDA (2026-08-25): `linha_de_fomento` virou lista (ARRAY)

Era `db.Column(db.String(50))` (valor único); virou `db.Column(ARRAY(db.String(50)))`, mesmo 
padrão de `natureza_recurso`/`publico_alvo`/`palavras_chave`. Motivo concreto que motivou a 
conversão: a chamada FAPEMIG-SEDE 013/2026 tem **três** linhas de fomento simultâneas na 
taxonomia da própria FAPEMIG ("Auxílio à Inovação", "Auxílio à Pesquisa" e "Capacitação de 
Pessoas") — um campo de valor único não tinha como representar isso; ficava só o placeholder, 
e os rótulos reais iam parar em `dados_extra["linhas_fomento_fapemig"]` sem afetar filtro nem 
formulário.

Migração (`52ffc42f67d1`) escrita à mão — autogenerate do Alembic não detecta mudança 
varchar→varchar[] (limitação conhecida, nem chegou a gerar um `alter_column` errado, 
simplesmente não percebeu a mudança nenhuma). Usa `postgresql_using` para empacotar cada 
valor existente numa lista de um elemento; confirmado que os 118 registros existentes 
(inclusive os 3 já curados manualmente) preservaram o valor exatamente, sem nenhum NULL ou 
lista vazia. Os 7 scrapers passam `["apoio_formacao_capacitacao"]` (lista de um elemento) — 
a FAPEMIG continua com o placeholder por ora; virar a lista de verdade com os múltiplos 
valores da própria API é um passo natural seguinte, mas não fazia parte deste escopo.

### Scraper FAPERGS: API "híbrida" JSON+HTML

`scrapers/fapergs.py` descobriu um padrão diferente de todos os anteriores: a FAPERGS expõe 
um endpoint interno (`_service/conteudo/pagedlistfilho`) que devolve **JSON** com 
`recordcount`/`pagecount`, mas o conteúdo em si vem como um **fragmento HTML bruto** dentro 
do campo `body` (um `<article class="conteudo-lista__item">` por edital) — nem API 
estruturada pura como a FAPEMIG/FAPESC (campos já tipados), nem HTML de página completa como 
CNPq/FAPESP/Araucária (sem envelope JSON nenhum). Precisa dos dois passos: `resp.json()` para 
tirar o `body`, depois `BeautifulSoup` no fragmento para tirar os itens.

Descoberto via inspeção manual do Network tab do navegador (filtro por domínio próprio) — 
mesmo processo usado para achar a API da FAPEMIG. Vale sempre esse caminho antes de partir 
para parsing de HTML de página completa, mesmo em sites que não parecem SPA moderna.

## Detecção de atualizações em registros já curados + painel admin de scrapers (2026-08-25)

### O que mudou

- `Oportunidade.revisao_pendente` (booleano): marca um registro já curado em que um 
  re-scrape detectou mudança num campo monitorado (`app/scraper_utils.CAMPOS_MONITORADOS`: 
  `data_prazo`, `data_resultado_previsto`, `orcamento_total_chamada`, 
  `valor_minimo_proposta`, `valor_maximo_proposta`, `status_oficial`) desde a última 
  revisão. Não afeta `status`/visibilidade — só sinaliza. Fica em `/moderacao/atualizacoes`, 
  com o histórico de mudanças em `dados_extra["mudancas_detectadas"]`.
- `app/scraper_utils.processar_registro()` substitui o padrão antigo de salvamento em todos 
  os 7 scrapers: insere se for novo, atualiza+flagga se algo monitorado mudou, ignora se 
  nada mudou.
- `ExecucaoScraper`: histórico de execuções dos scrapers (manual via `/admin/scrapers` ou 
  agendado), com resumo por fonte (`novos`/`atualizados`/`ja_existentes`/`erro`).
- `scripts/rodar_todos_scrapers.py`: roda as 7 fontes em sequência, isolando falha de uma 
  fonte das demais (try/except por fonte — ver caso real abaixo).
- Agendador diário (APScheduler, 20h `America/Sao_Paulo`), opt-in via 
  `create_app(iniciar_agendador=True)` — só `run.py` passa isso.

### ⚠️ Limitação real do agendador — documentar sempre que alguém perguntar por que não rodou

O agendador só dispara de verdade às 20h se o processo Flask estiver rodando NAQUELE 
MOMENTO. Durante o desenvolvimento (Codespace, ligado manualmente), isso só funciona se o 
`python run.py` ficar ativo às 20h. Quando o projeto for hospedado num servidor sempre 
ligado (Render/Railway), passa a funcionar de forma confiável todos os dias, sem depender de 
ninguém deixar nada aberto.

### Três bugs pegos na validação, antes de virar produção (nenhum estava no briefing original)

1. **Guard do agendador contra duplicação do reloader estava invertido.** A ideia (comum em 
   projetos Flask) é: `python run.py` com `debug=True` sobe DOIS processos — um "monitor" 
   que reinicia o worker a cada mudança de arquivo, e o worker de verdade, que serve as 
   requisições. Só o worker deve iniciar o agendador. A variável de ambiente 
   `WERKZEUG_RUN_MAIN` diferencia os dois: fica **ausente** no monitor e `"true"` no worker 
   — confirmado empiricamente com um probe script. A condição óbvia (`!= "false"`) é 
   verdadeira nos dois processos (`None != "false"` também é `True`), então o agendador 
   subiria duas vezes e o job das 20h disparava em dobro todo santo dia. A condição certa é 
   `== "true"`.
2. **`create_app()` sempre chamou o agendador incondicionalmente no briefing original** — 
   mas `create_app()` é chamado por todo scraper avulso (`python -m scrapers.x`), por 
   `scripts/backfill_uf.py`, por `flask shell`, por qualquer teste. Se o agendador subisse 
   ali dentro sem controle, cada um desses viraria um processo com uma thread de background 
   esperando dar 20h. Corrigido com um parâmetro opt-in (`iniciar_agendador=False` por 
   padrão) — só `run.py` passa `True`.
3. **`processar_registro` não estava persistindo `dados_extra["mudancas_detectadas"]`.** 
   Clássica pegadinha do SQLAlchemy com colunas JSON/JSONB: mutar o dict *in place* e depois 
   reatribuir o mesmo objeto (`existente.dados_extra = dados_extra_atual`, sendo 
   `dados_extra_atual` o mesmo objeto que já estava em `existente.dados_extra`) faz o 
   SQLAlchemy comparar "valor antigo" com "valor novo" e achar que são o mesmo objeto — 
   então a coluna simplesmente não entra no UPDATE. `data_prazo`/`revisao_pendente` 
   persistiam normalmente (são escalares), só `dados_extra` voltava ao valor de antes depois 
   do commit. Corrigido criando um dict novo (`dict(existente.dados_extra or {})`) em vez de 
   mutar o existente. Pego só porque a validação conferiu o valor DEPOIS de um commit de 
   verdade, não só em memória.

Também pego na validação (não é bug, mas documentado para não assustar quem revisar o 
histórico de execuções): rodando o painel admin de verdade, a FAPERGS falhou uma vez com 
`ConnectionResetError` — rede instável do lado do host (mesmo padrão intermitente já visto 
com a FAPESC), não erro de parsing. Isso é exatamente o cenário que o isolamento por fonte 
em `rodar_todos()` existe para tratar: as outras 6 fontes rodaram normalmente na mesma 
execução, e o histórico registrou `sucesso=False` com o erro específico da FAPERGS visível.

## Moderação — ainda não existe interface

Não há tela de moderação (aprovar/rejeitar/editar pendentes). Hoje o fluxo é manual via shell:

```bash
flask --app run.py shell
>>> from app.models import Oportunidade
>>> from app import db
>>> o = Oportunidade.query.filter_by(status="pendente").first()
>>> o.status = "aprovado"
>>> o.linha_de_fomento = "auxilio_pesquisa"   # corrigir conforme o caso real
>>> db.session.commit()
```

Uma tela de moderação é candidata natural para a próxima etapa, especialmente quando o 
sistema de usuários/papéis (admin/colaborador) for implementado — ver "Autenticação e 
submissão pública de vagas" acima.

### Decisão revisada (2026-08-10): visibilidade pública de pendentes

A política original era esconder todo conteúdo pendente até aprovação manual. Isso foi 
trocado por "mostrar tudo exceto rejeitado, com selo Não verificado" — motivo: curadoria 
100%-antes-de-publicar cria um gargalo que trava o crescimento de conteúdo visível, e 
conteúdo visível é o que atrai colaboradores para ajudar a curar (ciclo que precisa de um 
empurrão inicial). O selo mantém honestidade sobre a origem e confiabilidade do dado. 
Rejeitado continua sempre invisível — curadoria explícita de descarte não deve reaparecer. 
Este é o modelo que também prepara o terreno para quando a submissão pública (ORCID) for 
retomada: mostrar valor primeiro é o que dá motivo para alguém querer logar e colaborar.


## Vocabulário:
Instituições com papéis jurídicos e financeiros distintos:

- Instituição financiadora — quem origina o recurso (CNPq, FAPESP, FINEP)
- Instituição executora — quem usa o recurso na prática, executando o projeto (a universidade, o laboratório)
- Instituição beneficiária — o intermediário que recebe o recurso da financiadora e repassa pra executora — isso é exatamente o papel que, no Brasil, costuma ser preenchido por fundações de apoio (ex: FAPEU, FUNDEP, FUNCAMP), que existem justamente para gerir recursos de pesquisa em nome de universidades públicas, sob a Lei 8.958/1994.

Nem toda oportunidade tem os três — muitas vezes financiadora e executora se relacionam direto, sem intermediário

## Glossário — linha de fomento, instrumento, natureza do recurso e público-alvo

Termos frequentemente confundidos entre si. Vocabulário de referência para manter consistência em scrapers futuros e no formulário público de submissão.

- **Linha de fomento** (`linha_de_fomento`) — a finalidade do fomento: o que a chamada se propõe a apoiar. Ex: `auxilio_pesquisa`, `auxilio_inovacao`, `auxilio_divulgacao_cientifica`, `apoio_formacao_capacitacao`, `apoio_redes_grupos_pesquisa`. É o "para quê" da chamada.
- **Tipo de instrumento** (`tipo_instrumento`) — o instrumento administrativo/legal usado para veicular a chamada, independente da linha de fomento. Ex: `chamada_publica_edital`, `chamamento_publico`, `premio`. Uma mesma linha de fomento (ex: auxílio pesquisa) pode ser operacionalizada por instrumentos diferentes.
- **Natureza do recurso** (`natureza_recurso`) — o que é efetivamente concedido na prática, podendo ter mais de um valor simultâneo. Ex: `custeio`, `capital`, `bolsa`. Uma chamada de auxílio pesquisa pode conceder custeio e capital ao mesmo tempo, por exemplo.
- **Público-alvo** (`publico_alvo`) — quem pode se candidatar, podendo ter mais de um valor. Ex: `pesquisadores`, `empresas`, `startups`, `ict`, `mestrandos`, `doutorandos`, `ies`, `governo`. Importante: "pesquisador" não é sinônimo de "doutor" — nem todo pesquisador tem doutorado, e a categoria é mais ampla que os níveis de formação acadêmica.
- **Nível de formação** (`nivel_formacao`) — grau acadêmico do beneficiário (mestrado, doutorado, pós-doutorado, iniciação científica, não aplicável). Independente de `publico_alvo` — só é relevante quando `natureza_recurso` inclui `bolsa` (não faz sentido para custeio ou capital direcionado a uma empresa, por exemplo).
- **Modalidade de pessoa** (`modalidade_pessoa`) — só relevante quando `linha_de_fomento` é `apoio_formacao_capacitacao`. Descreve o tipo de movimentação de pessoal que o apoio financia: `atracao` (trazer pesquisador de fora), `fixacao` (reter pesquisador já vinculado), `capacitacao_exterior` (formação/estágio no exterior).
- **Tipo de parceria** (`tipo_parceria`) — escopo geográfico/institucional da parceria entre instituições, quando a chamada exigir ou incentivar cooperação. Ex: `nacional`, `regional`, `internacional`. Distinto de `abrangencia` (que descreve o alcance geográfico da própria chamada, não da parceria).

## `status` vs `status_oficial` — não confundir

Dois campos parecidos, com significados completamente diferentes:

- **`status`** — status de **moderação/curadoria interna** do registro no sistema: `rascunho`, `pendente`, `aprovado`, `rejeitado`. Controla se a oportunidade aparece na busca pública (ver seção "Autenticação e submissão pública de vagas" acima). Não tem relação com a situação do edital em si.
- **`status_oficial`** — status **oficial declarado pela instituição** sobre o edital: `suspensa`, `cancelada`, `retificada`, `resultado_divulgado`. Fica `None`/vazio na maioria das chamadas (caso normal), e nesse caso o sistema calcula "Aberta"/"Encerrada" automaticamente comparando `data_prazo` com a data atual. Quando preenchido, `status_oficial` tem prioridade sobre esse cálculo na exibição — por exemplo, uma chamada com `status_oficial="suspensa"` mostra "Suspensa" mesmo que `data_prazo` ainda esteja no futuro.

## `area_principal` — Tabela de Áreas do Conhecimento CNPq/CAPES

`area_principal` é validado contra as 8 Grandes Áreas oficiais da Tabela de Áreas do Conhecimento CNPq/CAPES (Ciências Exatas e da Terra, Ciências Biológicas, Engenharias, Ciências da Saúde, Ciências Agrárias, Ciências Sociais Aplicadas, Ciências Humanas, Linguística/Letras/Artes). Por ora só o nível de granularidade "Grande Área" foi implementado — descer até "Área" e "Subárea" da tabela oficial fica como melhoria futura, se houver demanda real por filtragem mais fina. `palavras_chave` continua livre (lista de strings, sem validação) para cobrir termos que a Grande Área sozinha não capta.

## Decisão de escopo do MVP (revisão)

MVP redefinido como um portal de **leitura pública**, no estilo de sites de concurso público 
(PCI Concursos, QConcursos, Estratégia Concursos) — conteúdo agregado, buscável e filtrável, 
sem exigir login para consulta. ORCID, papéis de usuário (admin/colaborador/usuário) e 
submissão pública ficam confirmados como fase pós-MVP (ver seção "Autenticação e submissão 
pública de vagas" acima) — infraestrutura de e-mail (dev@fomentobrasil.com.br via Cloudflare 
Email Routing) já está pronta para quando essa fase começar, mas o cadastro OAuth no ORCID 
Sandbox foi pausado por decisão consciente de foco.

Implicação prática: o valor do MVP agora depende de **volume de conteúdo real** (scrapers e/ou 
cadastro manual em escala), não de autenticação. Priorizar frentes que aumentam quantidade e 
qualidade de oportunidades cadastradas.

## Pendência de segurança — `?status=` sem controle de acesso

A listagem pública (`/oportunidades`) passou a filtrar por `status == "aprovado"` na base da 
query, para que registros coletados por scraper (gravados como `pendente`) não apareçam antes 
da curadoria. Para permitir a revisão manual desses pendentes existe o parâmetro 
`/oportunidades?status=pendente`, aceito também para `aprovado`, `rejeitado` e `rascunho`.

**Esse parâmetro não tem controle de acesso**: qualquer visitante pode usá-lo. É aceitável por 
ora porque não há dado sensível (tudo vem de editais públicos) e não existe sistema de 
usuários, mas precisa virar rota restrita a curador/admin quando os papéis forem implementados 
(ver "Autenticação e submissão pública de vagas" acima).

Na mesma linha: a página de detalhe (`/oportunidades/<id>`) responde 200 para registros 
pendentes. Isso é intencional por enquanto — o curador precisa abrir o registro para revisá-lo 
—, mas significa que um pendente é acessível por URL direta para quem souber o id. Deve ser 
fechado junto com o `?status=`.

## Curadoria de dados vindos de scraper

Registros coletados por scraper entram com `status="pendente"` e com campos que a página de 
origem não permite inferir com confiança:

- `linha_de_fomento` — recebe um **placeholder** (`apoio_formacao_capacitacao`), pois não é 
  dedutível do título/descrição. **Sempre revisar.**
- `natureza_recurso` e `publico_alvo` — são `NOT NULL` no schema, mas não são extraíveis da 
  página de listagem. Entram como **lista vazia** (`[]`), representando "ainda não determinado", 
  em vez de um chute que viraria dado errado no banco.
- `area_principal` — fica `None`, a preencher na curadoria.
- `dados_extra.inscricao_inicio` — data de início do período de inscrição, que não tem coluna 
  própria no modelo (`data_publicacao` é a data de publicação do edital, conceito distinto). 
  Guardada aí para não se perder; promover a coluna se virar necessidade recorrente de filtro.

### Bug corrigido (2026-08-10): `uf`/`abrangencia` não eram preenchidos pelos scrapers

Os 4 scrapers gravavam os registros sem `uf`/`abrangencia`, o que quebrava silenciosamente o 
filtro de região da listagem (`?regiao=`) — nenhum registro de agência estadual (FAPESP, 
FAPEMIG, FAPES) aparecia ao filtrar por região, porque a query depende de `uf`. Corrigido nos 
4 scrapers (cada um agora fixa `uf`/`abrangencia` de acordo com a agência que representa) e 
retroativamente nos 81 registros existentes via `scripts/backfill_uf.py`.

Nota sobre o backfill: a classificação foi feita pelo **domínio do link**, não por 
`instituicao_financiadora` — esse campo frequentemente vem composto (ex.: "CNPq/MCTI", 
"FAPESP e JSPS"), então um match exato contra "CNPq"/"FAPESP" cobriria só uma fração dos 
registros (checado: ~42 dos 81, e nenhum do CNPq). O domínio do link identifica a origem com 
confiabilidade.

### Assistente de importação por link (`/oportunidades/importar`)

Extração **genérica e best-effort**, bem diferente dos scrapers dedicados: funciona para 
qualquer link (HTML ou PDF), mas só aproxima título e descrição — nenhuma classificação 
estruturada (linha de fomento, público-alvo, área etc.). Serve para acelerar o preenchimento 
manual, nunca para pular a curadoria; o curador sempre revisa e completa antes de salvar. 
PDFs muito grandes ou com texto em imagem escaneada (sem camada de texto extraível) não serão 
lidos corretamente — ficaria para uma fase de OCR, mais adiante do que a extração de PDF já 
prevista.

**Pendência de segurança:** a rota faz `requests.get()` para a URL que o visitante informar, 
sem controle de acesso nem validação de host/IP — um SSRF em potencial (ver comentário em 
`app/routes.py`). O risco é parcialmente mitigado por só devolver título/trecho de texto, mas 
precisa ser revisitado junto com o sistema de usuários/papéis, e é candidato a bloquear 
IPs privados/loopback antes disso.

### Decisão pendente: qual data importa na listagem

Hoje o scraper grava `data_publicacao` com a data de publicação do edital (o "Publicado em" 
da página do CNPq) e joga o início das inscrições em `dados_extra.inscricao_inicio`.

Inclinação registrada (2026-08-10, a decidir depois): **a data relevante para o portal é a de 
submissão, não a de publicação** — o que o usuário final quer saber é a janela para se 
candidatar. O modelo já tem `data_prazo` (fim da submissão) mas não tem o início, então a 
forma provável é promover `inscricao_inicio` a coluna própria (ex.: `data_inicio_submissao`), 
formando o par início/fim, e deixar `data_publicacao` como metadado secundário. 
Não decidido ainda se `data_publicacao` continua sendo coletada ou some.
