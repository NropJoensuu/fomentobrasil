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
