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