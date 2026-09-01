> **RESOLVIDO em 2026-08-31.** As duas perguntas foram decididas: `publico_alvo` virou
> `proponente_elegivel` (quem pode apresentar a proposta); `instituicao_executora` e
> `instituicao_beneficiaria` foram removidas, por serem papéis pós-concessão; e foi criada
> `instituicao_promotora`. Ver "Glossário de papéis institucionais: pré-outorga vs
> pós-concessão" em `inventario_de_necessidades.md`. Este documento fica como registro do
> raciocínio e dos dados que levaram à decisão.

# Consulta: `publico_alvo` e os campos de instituição

> Documento escrito para ser lido por inteiro como contexto de uma conversa. Ele descreve um
> problema de modelagem real do projeto, com os dados que o originaram, e termina em duas
> perguntas. Não há resposta certa óbvia — as três alternativas de cada pergunta são
> defensáveis, e a escolha tem consequência para a busca pública.

## O projeto

`fomentobrasil` é um portal público que agrega editais e chamadas de fomento à pesquisa no
Brasil. Dezenove scrapers coletam de fontes federais (CNPq) e estaduais (as FAPs), e um
curador humano revisa cada registro antes de publicar. A busca pública filtra por região, UF,
linha de fomento, área do conhecimento e **público-alvo**.

Stack: Flask + SQLAlchemy + Postgres. O modelo é `app/models.py`, classe `Oportunidade`.

## Os campos em discussão

Quatro campos hoje, com as definições que estão nos comentários do modelo:

| campo | tipo | definição atual |
|---|---|---|
| `publico_alvo` | `ARRAY(String)`, obrigatório | "Quem pode se candidatar" — vocabulário fechado: `pesquisadores`, `especialistas`, `mestrandos`, `mestres`, `doutorandos`, `doutores`, `empresas`, `startups`, `ies`, `ict`, `governo` |
| `instituicao_financiadora` | `ARRAY(String)`, obrigatório | "Quem origina o recurso" — ex.: CNPq, FAPESP. É também quem publica o edital |
| `instituicao_executora` | `String`, opcional | "Quem executa o projeto" — ex.: "Centro Universitário FEI" |
| `instituicao_beneficiaria` | `String`, opcional | "Intermediária que recebe e repassa o recurso" — pensado para fundações de apoio (FAPEU, FUNDEP, FUNCAMP), sob a Lei 8.958/1994 |

## O que aconteceu na prática

Nove editais foram curados à mão. Os campos de instituição foram preenchidos com **tipos**, não
com nomes próprios:

| registro | `instituicao_executora` | `instituicao_beneficiaria` | `publico_alvo` |
|---|---|---|---|
| CNPq 19/2026 — Asas para o Futuro | `IF; IES` | — | `ies` |
| CNPq 15/2026 — PROAFRICA | `ICT` | — | `ict` |
| CNPq 13/2026 — Eventos | — | — | `empresas`, `ies`, `ict` |
| FAPESP/CONFAP — Desafios da Amazônia | — | `ICT-Amazônia Legal` | `pesquisadores`, `ict` |
| FAPEMIG 014/2026 — Cientista Empreendedor | — | — | `empresas`, `ict` |
| FAPES 21/2026 — Redes de PD&I | — | `ICT-ES; IES-ES` | `pesquisadores` |
| FAPESC 47/2026 — INOVA PROCON | — | `PROCON-SC` | `pesquisadores`, `especialistas`, `mestrandos`, `mestres`, `doutorandos` |
| FACEPE 27/2026 — Solano Trindade | `ICT-PE` | — | `pesquisadores`, `ict` |
| FUNDECT 09/2026 — PAE-MS | — | — | `ies`, `ict` |

De nove registros, **uma única** instituição de nome próprio: PROCON-SC. Todo o resto são
categorias, às vezes com o estado anexado (`ICT-ES`, `IES-ES`, `ICT-PE`).

## A confusão relatada pelo curador, nas palavras dele

> "Acredito que estou com um problema na questão do público-alvo e das instituições executoras
> e beneficiárias. Pelo que estou vendo:
> - Público-alvo são as pessoas que podem ser afetadas diretamente pelo edital.
> - Proponente é quem deve apresentar a proposta. E aqui é que fica esquisito, pois o
>   proponente pode ser empresas, ICT...
> - Instituição executora é quem está executando (publicando) o edital? Acredito que sim. Mas
>   é confuso.
> - Instituição beneficiária é a que irá receber os recursos, mas também é a instituição que
>   executará o Termo de Outorga ou o Projeto."

Duas observações sobre isso:

1. A leitura de que "executora é quem publica o edital" **diverge da prática brasileira**: quem
   publica e concede é a financiadora/concedente (CNPq, FAP). "Executora" é normalmente a
   instituição onde o projeto é realizado.
2. A leitura de `publico_alvo` como "quem é afetado" **diverge da definição atual** do campo
   ("quem pode se candidatar"). As duas leituras coincidem na maioria dos editais e divergem
   justamente nos mais sensíveis — ver os dois casos abaixo.

## Os casos que separam as duas leituras

- **FAPAC 003/2026** — edital de bolsas cujo objetivo declarado é apoiar **mães** estudantes.
  Quem propõe é a estudante; quem o edital existe para beneficiar é uma população específica.
- **CNPq 19/2026 — Asas para o Futuro** — quem submete a proposta é uma **IES ou IF**
  (instituição); o edital existe para formar **mulheres** em áreas de ciência e tecnologia.
- **FAPESC 47/2026 — INOVA PROCON** — quem se candidata são pessoas físicas com titulação
  específica; a beneficiária institucional do trabalho é o PROCON-SC.

## Evidência adicional, medida

Uma análise comparou os nove registros curados contra o texto dos PDFs dos editais. Sobre
`publico_alvo` especificamente:

- Nas chamadas CNPq 15/2026 e FUNDECT 09/2026, a palavra "pesquisador" aparece 17 e 18 vezes, e
  o curador **não** marcou `pesquisadores` — porque quem submete é a instituição.
- No edital FAPESC 47/2026, `mestrandos`, `mestres` e `doutorandos` foram marcados com **zero**
  ocorrências dessas palavras no PDF: o curador leu a tabela de modalidades de bolsa e deduziu
  a titulação exigida.

Ou seja: na prática o curador **já** estava preenchendo `publico_alvo` como elegibilidade de
quem propõe, mesmo descrevendo-o depois como "quem é afetado".

## Terminologia de referência

Na prática brasileira de fomento (Lei 10.973/2004 — Marco Legal de CT&I; Lei 8.958/1994 —
fundações de apoio; editais de CNPq e FAPs):

- **Concedente / financiadora** — quem aporta o recurso e publica o edital.
- **Proponente** — quem submete a proposta. Pode ser pessoa física (pesquisador) ou jurídica.
- **Instituição de execução** — onde o projeto é realizado.
- **Beneficiária / outorgada** — quem recebe os recursos e assina o Termo de Outorga. Pode
  coincidir com a executora, ou ser uma fundação de apoio que gere o recurso em nome dela.

Note que `IES` (Lei 9.394/1996, LDB) e `ICT` (Lei 10.973/2004, art. 2º, V) se sobrepõem sem
coincidir: uma universidade federal é as duas; a Fiocruz é ICT e não é IES; uma faculdade
privada só de ensino é IES e não é ICT. O projeto já decidiu manter as duas separadas.

---

## Pergunta 1 — o que `publico_alvo` deve responder?

- **(a) Quem pode APRESENTAR a proposta** (proponente elegível). Argumento: quem usa o portal é
  um pesquisador perguntando "posso me candidatar?", e é isso que os editais definem com
  precisão numa seção própria de elegibilidade. Implicaria um campo novo e opcional para
  registrar a população beneficiada quando ela difere.
- **(b) Quem é AFETADO/beneficiado** pelo edital. Implicaria que a elegibilidade vira outro
  campo — e é esse outro campo que passaria a ser o filtro principal da busca.
- **(c) Os dois, em campos irmãos** preenchidos sempre. Mais preciso e mais custoso por edital.

## Pergunta 2 — o que fazer com `instituicao_executora` e `instituicao_beneficiaria`?

- **(a) Só nome próprio.** Tipos como `ICT-ES` saem desses campos e viram elegibilidade. Os
  campos ficam vazios na maioria dos editais e se preenchem apenas quando existe instituição
  concreta (PROCON-SC). É o uso para o qual foram criados.
- **(b) Manter como estão**, apenas documentando no glossário como preencher de forma
  consistente. Nada muda no código.
- **(c) Remover os dois.** Estão vazios ou mal usados em quase tudo. Instituição concreta só
  passaria a existir quando o projeto tiver o cadastro de vaga ligada a projeto financiado
  (`origem = vaga_projeto`), que hoje é previsto no modelo mas não usado.

## O que se espera da resposta

Uma recomendação para cada pergunta, com o raciocínio. Interessa especialmente:

1. Se a distinção proponente / executora / beneficiária / outorgada deve aparecer no modelo de
   dados de um **portal de busca**, ou se é detalhe de execução contratual que só importa depois
   que a proposta é aprovada — e portanto fora do escopo.
2. Como tratar o caso em que o proponente é a instituição e o beneficiário final é uma pessoa
   com característica específica (mães, mulheres), sem inflar o vocabulário controlado.
3. Se o sufixo de UF que o curador vem usando (`ICT-ES`, `IES-PE`) indica falta de um campo, ou
   se é redundante com `abrangencia` + `uf`, que já existem.
