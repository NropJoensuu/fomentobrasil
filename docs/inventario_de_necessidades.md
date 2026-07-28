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