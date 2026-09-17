"""Sugestão de campos de curadoria a partir do texto do edital, com evidência literal.

**A IA sugere, nunca decide.** Nada aqui altera `status` nem grava em campo estruturado: a
saída vai para `dados_extra["sugestao_ia"]` e aparece pré-preenchida na tela de moderação
para o curador confirmar, corrigir ou descartar.

Cada campo vem acompanhado do TRECHO LITERAL do edital que o justifica. Isso muda a natureza
da conferência — de "reler o edital" para "bater o olho no trecho" — e é a proteção contra
alucinação, sobretudo em datas e valores, que a análise dos 15 registros curados apontou como
o ponto de maior risco.

**Complementar ao `app/extracao_pdf.py`, não substituto.** Aquele módulo extrai datas e
valores por regra, com 29/29 de acerto medido, e roda sem custo nem rede externa. Este cobre
o que regra não alcança: os campos de julgamento (`proponente_elegivel`, `linha_de_fomento`,
`abrangencia`), em que contar palavra é comprovadamente errado — a chamada CNPq 15/2026 diz
"pesquisador" 17 vezes e não tem `pesquisadores` marcado, porque quem submete é a instituição.

SAÍDA ESTRUTURADA NÃO COUBE. A primeira versão usava `output_config.format` com JSON Schema,
para que o vocabulário controlado virasse `enum` e valor fora da lista fosse impossível em vez
de apenas desencorajado. A API recusou em dois limites sucessivos: primeiro
"Enum value does not match declared type" (enum com tipo em união precisa de `anyOf`), e
depois, já corrigido isso, "Schema is too complex" — 19 campos, cada um com valor e evidência,
não cabem. A garantia foi reposta em Python: `_validar` descarta o que estiver fora do
vocabulário e registra o descarte em `_meta["valores_descartados"]`. Mesmo efeito prático
para o curador; a diferença é que o modelo pode errar e o erro é filtrado depois, em vez de
não poder errar.

Pensamento adaptativo fica ligado: o briefing aponta o cronograma com muitas datas parecidas
como o risco principal, que é exatamente o tipo de desambiguação que se beneficia disso.
"""

import io
import json
import os
import re
from datetime import datetime
from urllib.parse import urljoin

import pdfplumber
import requests
from anthropic import Anthropic
from bs4 import BeautifulSoup

from app.utils import url_real_do_pdf

MODELO = "claude-sonnet-5"
MAX_PAGINAS_PDF = 8
MAX_CARACTERES = 40000

# Folgado de propósito: são 18 campos, cada um com um trecho de evidência, e o pensamento
# adaptativo também consome saída. Estourar o limite trunca o JSON no meio e perde a chamada
# inteira — o custo de pedir folga é zero, porque só se paga o que for gerado.
MAX_TOKENS = 16000

VOCAB_LINHA_DE_FOMENTO = [
    "auxilio_pesquisa", "auxilio_inovacao", "auxilio_divulgacao_cientifica",
    "apoio_formacao_capacitacao", "apoio_redes_grupos_pesquisa", "premiacao",
]
VOCAB_TIPO_INSTRUMENTO = ["chamada_publica_edital", "chamamento_publico"]
VOCAB_NATUREZA_RECURSO = ["custeio", "capital", "bolsa"]
VOCAB_PROPONENTE = [
    "pesquisadores", "especialistas", "mestrandos", "mestres", "doutorandos", "doutores",
    "ies", "ict", "empresas", "startups", "governo", "outros",
]
VOCAB_NIVEL_FORMACAO = [
    "educacao_basica", "graduacao", "iniciacao_cientifica", "mestrado", "doutorado", "pos_doutorado",
]
VOCAB_MODALIDADE_PESSOA = ["atracao", "fixacao", "capacitacao_exterior"]
VOCAB_AREA_PRINCIPAL = [
    "Ciências Exatas e da Terra", "Ciências Biológicas", "Engenharias",
    "Ciências da Saúde", "Ciências Agrárias", "Ciências Sociais Aplicadas",
    "Ciências Humanas", "Linguística, Letras e Artes",
]
VOCAB_TIPO_PARCERIA = ["nacional", "regional", "internacional"]
VOCAB_ABRANGENCIA = ["nacional", "estadual", "regional", "internacional"]


# O vocabulário de cada campo de lista fechada. Não vai mais para a API como `enum` —
# ver "SAÍDA ESTRUTURADA NÃO COUBE" na docstring do módulo — mas é aplicado na volta por
# `_validar`, então valor fora do vocabulário nunca chega à tela do curador.
VOCABULARIO_POR_CAMPO = {
    "linha_de_fomento": VOCAB_LINHA_DE_FOMENTO,
    "tipo_instrumento": VOCAB_TIPO_INSTRUMENTO,
    "natureza_recurso": VOCAB_NATUREZA_RECURSO,
    "proponente_elegivel": VOCAB_PROPONENTE,
    "nivel_formacao": VOCAB_NIVEL_FORMACAO,
    "modalidade_pessoa": VOCAB_MODALIDADE_PESSOA,
    "area_principal": VOCAB_AREA_PRINCIPAL,
    "tipo_parceria": VOCAB_TIPO_PARCERIA,
    "abrangencia": VOCAB_ABRANGENCIA,
}

CAMPOS_ESPERADOS = [
    "linha_de_fomento", "tipo_instrumento", "natureza_recurso", "proponente_elegivel",
    "nivel_formacao", "modalidade_pessoa", "area_principal", "tipo_parceria",
    "abrangencia", "programa", "instituicao_promotora", "instituicao_financiadora", "uf",
    "data_publicacao", "data_prazo", "data_resultado_previsto",
    "orcamento_total_chamada", "valor_minimo_proposta", "valor_maximo_proposta",
    "palavras_chave",
]

CAMPOS_DE_LISTA = {
    "linha_de_fomento", "natureza_recurso", "proponente_elegivel", "nivel_formacao",
    "uf", "instituicao_promotora", "instituicao_financiadora", "palavras_chave",
}


def _validar(sugestao):
    """Descarta o que estiver fora do vocabulário e registra o descarte.

    O prompt pede os valores certos, mas pedir não é garantir. Um `"ies_p"` inventado que
    chegasse à tela viraria dado sujo no banco no primeiro clique em "aplicar". O que for
    descartado fica em `_meta["valores_descartados"]` — se essa lista crescer, é sinal de
    que o prompt precisa de ajuste, não de que o curador precisa de mais atenção.
    """
    campos = sugestao.get("campos") or {}
    descartados = []

    for campo in list(campos):
        if campo not in CAMPOS_ESPERADOS:
            descartados.append({"campo": campo, "motivo": "campo desconhecido"})
            campos.pop(campo)
            continue

        dado = campos[campo]
        if not isinstance(dado, dict):
            descartados.append({"campo": campo, "motivo": "formato inesperado"})
            campos.pop(campo)
            continue

        valor = dado.get("valor")
        if valor in (None, "", []):
            campos.pop(campo)
            continue

        # O prompt pede lista para instituicao_promotora (migrou de campo único em
        # 2026-09), mas o modelo às vezes devolve string solta mesmo assim — mesma
        # tolerância que o resto do módulo já tem para "pedir não é garantir".
        if campo in CAMPOS_DE_LISTA and isinstance(valor, str):
            valor = [valor]
            dado["valor"] = valor

        vocabulario = VOCABULARIO_POR_CAMPO.get(campo)
        if vocabulario:
            if campo in CAMPOS_DE_LISTA:
                validos = [v for v in valor if v in vocabulario]
                fora = [v for v in valor if v not in vocabulario]
                if fora:
                    descartados.append({"campo": campo, "motivo": "fora do vocabulário", "valores": fora})
                if not validos:
                    campos.pop(campo)
                    continue
                dado["valor"] = validos
            elif valor not in vocabulario:
                descartados.append({"campo": campo, "motivo": "fora do vocabulário", "valores": [valor]})
                campos.pop(campo)
                continue

    sugestao["campos"] = campos
    return descartados


# Nomes de documento que NÃO são o edital. Mesma lógica de filtragem já usada nos scrapers.
PADRAO_NAO_EDITAL = re.compile(
    r"resultado|anexo|retifica|errata|formul[áa]rio|declara|modelo|\bfaq\b"
    r"|carta[- ]de[- ]servi|manual|cartilha|guia",
    re.IGNORECASE,
)
PADRAO_EDITAL = re.compile(r"edital|chamada|chamamento", re.IGNORECASE)

# Abaixo disto a página quase certamente não é o edital, e sim uma casca de listagem. Veio
# da medição: as páginas do CNPq renderam 1300–2000 caracteres e a da FAPESP, OITO.
MINIMO_CARACTERES_UTEIS = 3000


# URL de PDF solta no HTML bruto, para páginas renderizadas por JavaScript: a da FAPEMIG tem
# 247 KB e apenas 157 caracteres de texto, sem nenhum <a href> de PDF — mas o endereço está
# no JSON embutido que o front-end consome.
PADRAO_PDF_NO_BRUTO = re.compile(r"https?://[^\s\"'<>\\]+?\.pdf", re.IGNORECASE)


def _pdf_do_edital(sopa, url_base, html_bruto):
    """Procura, numa página de listagem, o link para o PDF do edital em si."""
    candidatos = []

    for a in sopa.find_all("a", href=True):
        alvo = url_real_do_pdf(urljoin(url_base, a["href"]))
        if ".pdf" not in alvo.lower():
            continue
        rotulo = f"{a.get_text(' ', strip=True)} {alvo.rsplit('/', 1)[-1]}"
        if PADRAO_NAO_EDITAL.search(rotulo):
            continue
        # Rótulo que menciona edital/chamada vem primeiro; o resto entra como reserva.
        candidatos.append((0 if PADRAO_EDITAL.search(rotulo) else 1, alvo))

    if not candidatos:
        for alvo in dict.fromkeys(PADRAO_PDF_NO_BRUTO.findall(html_bruto)):
            nome = alvo.rsplit("/", 1)[-1]
            if PADRAO_NAO_EDITAL.search(nome):
                continue
            candidatos.append((2 if PADRAO_EDITAL.search(nome) else 3, alvo))

    candidatos.sort(key=lambda c: c[0])
    return candidatos[0][1] if candidatos else None


def _texto_do_pdf(conteudo):
    with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
        texto = "\n".join((p.extract_text() or "") for p in pdf.pages[:MAX_PAGINAS_PDF])
    if not texto.strip():
        raise ValueError(
            "PDF sem camada de texto (digitalizado como imagem). Requer OCR — fora do escopo."
        )
    return texto


def _baixar(url):
    resp = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"}, verify=False)
    resp.raise_for_status()
    return resp


def extrair_texto(link):
    """Baixa o link e extrai o texto do EDITAL. Devolve (texto, tipo, url_lida).

    O `link` do registro raramente aponta para o edital: na calibração, 8 dos 15 levavam a
    uma página de listagem, e o que sobrava depois de limpar navegação era um talo — 1300 a
    2000 caracteres no CNPq, OITO na FAPESP. A IA não tinha o que ler, e a omissão em massa
    de valores e datas veio daí, não de incapacidade do modelo.

    Por isso, quando o destino é HTML, a função procura na página o link do PDF do edital e
    segue para ele. Só volta a usar o texto da página quando não acha PDF nenhum — aí é o que
    existe.
    """
    url = url_real_do_pdf(link)
    resp = _baixar(url)
    content_type = resp.headers.get("Content-Type", "").lower()

    # O magic number decide, não o Content-Type: vários sites das FAPs servem PDF como
    # application/octet-stream, e alguns servem HTML com .pdf na URL.
    if resp.content[:4] == b"%PDF" or ("pdf" in content_type and resp.content[:4] == b"%PDF"):
        return _texto_do_pdf(resp.content)[:MAX_CARACTERES], "pdf", url

    sopa = BeautifulSoup(resp.text, "html.parser")

    # Redirecionamento por <meta refresh>: é o caso de fapesp.br/18249, que devolve 8 bytes
    # de casca e a página real noutro endereço.
    meta = sopa.find("meta", attrs={"http-equiv": lambda v: v and v.lower() == "refresh"})
    if meta and "url=" in (meta.get("content") or "").lower():
        destino = meta["content"].split("URL=", 1)[-1].split("url=", 1)[-1].strip("'\" ")
        url = urljoin(url, destino)
        resp = _baixar(url)
        sopa = BeautifulSoup(resp.text, "html.parser")

    alvo = _pdf_do_edital(sopa, url, resp.text)
    if alvo:
        try:
            resposta_pdf = _baixar(alvo)
            if resposta_pdf.content[:4] == b"%PDF":
                return _texto_do_pdf(resposta_pdf.content)[:MAX_CARACTERES], "pdf", alvo
        except (requests.RequestException, ValueError):
            pass  # cai para o texto da página

    for tag in sopa(["script", "style", "nav", "footer"]):
        tag.decompose()
    texto = sopa.get_text("\n", strip=True)
    if not texto.strip():
        raise ValueError("Página sem conteúdo textual extraível.")
    if len(texto) < MINIMO_CARACTERES_UTEIS:
        raise ValueError(
            f"Só encontrei {len(texto)} caracteres nesta página e nenhum PDF de edital "
            "para seguir. Cole a URL do PDF do edital no painel de leitura assistida."
        )
    return texto[:MAX_CARACTERES], "html", url


PROMPT_SISTEMA = """Você extrai metadados estruturados de editais de fomento à pesquisa \
brasileiros para um portal público de descoberta de oportunidades.

Para CADA campo preenchido, inclua em "evidencia" o trecho LITERAL do edital que o \
justifica — copiado, não parafraseado. Se não encontrar a informação no texto, OMITA o campo \
inteiro do objeto "campos" — não invente, não infira e não chute. Um campo ausente custa \
pouco ao curador; um campo errado com aparência de certo custa muito.

## Conceito central: este é um catálogo PRÉ-OUTORGA
O portal indexa editais abertos. Papéis de execução (executora, beneficiária, outorgada, \
interveniente) só existem depois da proposta aprovada e NÃO devem ser extraídos.
Os papéis que existem no edital são:
- promotora: quem publica a chamada e recebe as propostas
- financiadoras: quem aporta recurso financeiro (uma ou várias)
- proponente elegível: quem pode submeter a proposta
- demandante: pessoa ou instituição cujo problema a pesquisa atende (quando houver)

## Como preencher cada campo

linha_de_fomento é o PROPÓSITO da chamada — o que ela existe para apoiar. NÃO é o tipo de \
recurso que ela concede.

Uma chamada de apoio a projetos de pesquisa que concede bolsas continua sendo \
auxilio_pesquisa. A bolsa é um INSTRUMENTO e vai em natureza_recurso, não em \
linha_de_fomento.

Só marque apoio_formacao_capacitacao quando o objetivo declarado da chamada for formar, \
capacitar, atrair ou fixar pessoas — não quando bolsas aparecem como meio de executar outro \
objetivo.

Exemplos do contraste:

"Apoiar projetos de pesquisa em biotecnologia, com concessão de bolsas de mestrado para a \
equipe" -> auxilio_pesquisa (bolsa é meio). natureza_recurso inclui bolsa.
"Conceder bolsas de mestrado e doutorado para formação de recursos humanos qualificados" \
-> apoio_formacao_capacitacao (formar é o fim).

Marque MAIS DE UMA linha quando a chamada tiver objetivos distintos e explícitos. Não marque \
uma linha por dedução a partir dos instrumentos.

Classifique pelo OBJETO do apoio, não por palavras temáticas do texto. Quase todo edital de \
fomento menciona "inovação" em algum trecho; isso sozinho não o torna auxilio_inovacao. \
auxilio_inovacao é para apoio DIRETO ao desenvolvimento de produto, processo ou serviço \
inovador, geralmente com empresa envolvida. Apoio a infraestrutura laboratorial ou a rede de \
pesquisa, mesmo quando a motivação declarada é fomentar inovação no estado, é auxilio_pesquisa \
(e também apoio_redes_grupos_pesquisa se o objeto for justamente estruturar a rede).

premiacao: use quando a chamada reconhece resultado já alcançado (prêmio, concurso de \
trabalhos, menção honrosa) em vez de apoiar atividade futura.

proponente_elegivel — QUEM PODE APRESENTAR A PROPOSTA.
Pessoa física: pesquisadores, especialistas, mestrandos, mestres, doutorandos, doutores.
Pessoa jurídica: ies, ict, empresas, startups, governo.
outros: publicações fora do fomento (ver e_fomento) que ainda assim têm um "proponente" — \
candidato a vaga, consultor, avaliador.
ATENÇÃO: marque quem SUBMETE, não quem é beneficiado. Se a proposta é submetida por uma \
instituição e a bolsa vai para estudantes, marque a instituição (ies ou ict), não os \
estudantes. IES e ICT se sobrepõem sem coincidir: uma universidade federal é as duas; a \
Fiocruz é ICT e não IES; uma faculdade só de ensino é IES e não ICT. Um edital que diga \
"IES/P" cobre as duas — marque ambas.
Quando a evidência de elegibilidade falar em "instituições proponentes", "a proponente deverá \
ser uma ICT/IES" ou equivalente, marque APENAS a instituição. Não acrescente pesquisadores, \
doutores ou mestres porque outro trecho do edital descreve requisitos do COORDENADOR ou da \
EQUIPE do projeto — requisito de quem coordena não é elegibilidade de quem propõe. Só marque \
pessoa física quando o edital permitir submissão por pessoa física em nome próprio.

modalidade_pessoa — só quando linha_de_fomento incluir apoio_formacao_capacitacao. \
Editais de seleção ou concurso de PESQUISADOR são fomento, e a modalidade é fixacao.

palavras_chave — texto livre. Use também para:
- a população beneficiada específica, quando difere do proponente (mães, mulheres, \
quilombolas, pessoas com deficiência)
- a instituição demandante nomeada no edital (ex.: PROCON-SC)
- os eixos, temas ou linhas temáticas listados no edital

abrangencia — quando o edital admitir mais de uma abrangência, registre a MAIS AMPLA. \
"Eventos de abrangência nacional ou internacional" -> internacional, não nacional.

uf — siglas de duas letras, só quando a abrangência for estadual ou regional. Preencha \
APENAS com estados explicitamente listados no texto do edital. NÃO deduza a partir do nome \
do programa, da região mencionada no título ou do bioma (ex.: um programa com "Amazônia" no \
nome não implica os nove estados da Amazônia Legal — outras FAPs podem aderir ao mesmo \
programa sem que o edital as liste). Se o edital não listar os estados, deixe o campo de fora.

programa — identifique o programa guarda-chuva NACIONAL ao qual a chamada pertence, quando o \
próprio edital declarar pertencer a um (ex.: Centelha, Tecnova, PROFIX, PPSUS, Universal, \
Amazônia +10). Não invente: só preencha quando o nome do programa aparecer no texto. Sem a \
edição nem a UF — "Centelha", não "Centelha 3 – Rondônia".

Datas em AAAA-MM-DD. Se o cronograma der só o mês ("até dezembro de 2026"), use o dia 01 e \
diga isso na evidência. Valores como número decimal, sem símbolo nem separador de milhar.

## Cuidados críticos

data_prazo é a data-LIMITE de submissão — não a de publicação, não a de resultado. \
Cronogramas trazem muitas datas parecidas; escolha com atenção e cite o trecho exato. \
Quando o edital tiver FASES SEQUENCIAIS (fase 1, fase 2), o prazo que vale é o da primeira, \
porque quem não submete na fase 1 não chega à fase 2. Quando tiver RODADAS independentes \
(1ª rodada, 2ª rodada), vale a última, porque quem chega agora ainda pode entrar nela.

Se o edital for de contratação de pessoal administrativo, oficineiros, consultoria ad hoc \
ou credenciamento de avaliadores, marque "e_fomento": false e explique em "observacao". \
Seleção de pesquisador NÃO é esse caso — é fomento.

## Formato da resposta
Responda SOMENTE com JSON válido, sem markdown, sem preâmbulo.

Percorra a lista de campos abaixo INTEIRA, um por um, e para cada um procure a informação no \
texto. Inclua em "campos" todos os que encontrar; omita apenas os que realmente não estiverem \
no edital. Não pare nos primeiros — os valores financeiros e a data de resultado costumam \
ficar em seções mais adiante e são tão importantes quanto os demais.

Vocabulário fechado:
- linha_de_fomento (lista): auxilio_pesquisa, auxilio_inovacao, auxilio_divulgacao_cientifica, \
apoio_formacao_capacitacao, apoio_redes_grupos_pesquisa, premiacao
- tipo_instrumento: chamada_publica_edital, chamamento_publico
- natureza_recurso (lista): custeio, capital, bolsa
- proponente_elegivel (lista): pesquisadores, especialistas, mestrandos, mestres, doutorandos, \
doutores, ies, ict, empresas, startups, governo, outros
- nivel_formacao (lista): educacao_basica, graduacao, iniciacao_cientifica, mestrado, \
doutorado, pos_doutorado. graduacao é para bolsa que NÃO é iniciação à pesquisa (ex.: \
mobilidade/intercâmbio de graduação sanduíche) — bolsa de pesquisa para estudante de \
graduação continua iniciacao_cientifica
- modalidade_pessoa: atracao, fixacao, capacitacao_exterior
- area_principal: "Ciências Exatas e da Terra", "Ciências Biológicas", "Engenharias", \
"Ciências da Saúde", "Ciências Agrárias", "Ciências Sociais Aplicadas", "Ciências Humanas", \
"Linguística, Letras e Artes"
- tipo_parceria: nacional, regional, internacional
- abrangencia: nacional, estadual, regional, internacional

Campo livre:
- programa: texto — programa guarda-chuva nacional, só quando o edital declarar pertencer a um
- instituicao_promotora: lista de textos — quem publica a chamada e recebe as propostas. \
Normalmente uma só, mas chamadas multilaterais podem distribuir o ato de promover entre mais \
de uma instituição. Use a SIGLA sozinha ("FAPEMIG", "CNPq", "FAPES"), nunca o nome por \
extenso nem sigla mais nome
- instituicao_financiadora: lista de textos — quem aporta recurso. Siglas, mesma regra
- uf: lista de siglas de duas letras
- data_publicacao: "AAAA-MM-DD" — quando o edital foi publicado
- data_prazo: "AAAA-MM-DD" — data-LIMITE de submissão
- data_resultado_previsto: "AAAA-MM-DD" — divulgação do resultado FINAL (a última do \
cronograma, não a preliminar)
- orcamento_total_chamada: número — o montante total da chamada, o que o edital costuma \
chamar de "valor global"
- valor_minimo_proposta: número — mínimo que uma proposta pode solicitar
- valor_maximo_proposta: número — máximo por proposta. Quando o edital dá tetos separados \
que se SOMAM no mesmo projeto (ex.: até R$ 80.000 de subvenção mais até R$ 50.000 em bolsas), \
informe a soma
- palavras_chave: lista de textos

Exemplo do formato (os campos mostrados são ilustrativos, não a lista completa):

{
  "e_fomento": true,
  "observacao": null,
  "campos": {
    "linha_de_fomento": {"valor": ["auxilio_pesquisa"], "evidencia": "trecho literal"},
    "data_prazo": {"valor": "2026-09-30", "evidencia": "trecho literal"},
    "data_resultado_previsto": {"valor": "2026-11-27", "evidencia": "trecho literal"},
    "orcamento_total_chamada": {"valor": 2500000.00, "evidencia": "trecho literal"},
    "valor_maximo_proposta": {"valor": 180000.00, "evidencia": "trecho literal"}
  }
}
"""


def sugerir_campos(oportunidade):
    """Chama o modelo e devolve o dicionário de sugestões, já validado contra o esquema."""
    texto, tipo_fonte, url_lida = extrair_texto(oportunidade.link)

    cliente = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    resposta = cliente.messages.create(
        model=MODELO,
        max_tokens=MAX_TOKENS,
        # O risco apontado na análise é o cronograma com várias datas parecidas — desambiguar
        # isso é exatamente o que o pensamento adaptativo faz bem.
        thinking={"type": "adaptive"},
        system=PROMPT_SISTEMA,
        messages=[{
            "role": "user",
            "content": (
                f"Título: {oportunidade.titulo}\n"
                f"Financiadora conhecida: "
                f"{', '.join(oportunidade.instituicao_financiadora or []) or '(nenhuma)'}\n\n"
                f"Texto do edital:\n{texto}"
            ),
        }],
    )

    if resposta.stop_reason == "refusal":
        raise ValueError("O modelo recusou a solicitação para este documento.")
    if resposta.stop_reason == "max_tokens":
        raise ValueError("Resposta truncada no limite de tokens — JSON incompleto.")

    # Blocos de pensamento vêm antes na lista, por isso o filtro por type. A limpeza de
    # cerca de markdown é defensiva: o prompt pede JSON puro, mas pedir não é garantir.
    bruto = next(b.text for b in resposta.content if b.type == "text").strip()
    if bruto.startswith("```"):
        bruto = bruto.split("```")[1]
        bruto = bruto[4:] if bruto.startswith("json") else bruto
    sugestao = json.loads(bruto.strip())

    descartados = _validar(sugestao)
    sugestao["_meta"] = {
        "valores_descartados": descartados,
        "modelo": MODELO,
        "tipo_fonte": tipo_fonte,
        "url_lida": url_lida,
        "caracteres_analisados": len(texto),
        "tokens_entrada": resposta.usage.input_tokens,
        "tokens_saida": resposta.usage.output_tokens,
        # Usado por editar.html para avisar quando `mudancas_detectadas` (o scraper
        # notando que um campo mudou numa nova coleta) é mais recente que esta sugestão —
        # achado #82: a sugestão ficava presa lendo o PDF antigo mesmo depois do prazo
        # mudar de 24/08 para 31/08 numa retificação. Mesma convenção de
        # app/scraper_utils.py (UTC, isoformat, sem timezone).
        "gerado_em": datetime.utcnow().isoformat(),
    }
    return sugestao
