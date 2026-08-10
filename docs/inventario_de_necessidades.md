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

### Decisão pendente: `linha_de_fomento` da FAPEMIG

`linha_de_fomento` continua com placeholder mesmo com a API informando o valor, porque a 
FAPEMIG marca **várias** linhas por chamada (ex.: a 013/2026 vem com "Auxílio à Inovação", 
"Auxílio à Pesquisa" e "Capacitação de Pessoas") e o nosso campo é de valor único. Resolver 
de verdade exige decidir se o campo vira lista — o que afeta formulário, filtros e migração. 
Os rótulos originais ficam em `dados_extra["linhas_fomento_fapemig"]` para a curadoria.

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

### Decisão pendente: qual data importa na listagem

Hoje o scraper grava `data_publicacao` com a data de publicação do edital (o "Publicado em" 
da página do CNPq) e joga o início das inscrições em `dados_extra.inscricao_inicio`.

Inclinação registrada (2026-08-10, a decidir depois): **a data relevante para o portal é a de 
submissão, não a de publicação** — o que o usuário final quer saber é a janela para se 
candidatar. O modelo já tem `data_prazo` (fim da submissão) mas não tem o início, então a 
forma provável é promover `inscricao_inicio` a coluna própria (ex.: `data_inicio_submissao`), 
formando o par início/fim, e deixar `data_publicacao` como metadado secundário. 
Não decidido ainda se `data_publicacao` continua sendo coletada ou some.
