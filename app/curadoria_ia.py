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

DECISÕES DE IMPLEMENTAÇÃO que divergem do briefing original, todas para reduzir modo de
falha (ver o cabeçalho de ESQUEMA_RESPOSTA e as notas em `sugerir_campos`):

- Saída estruturada (`output_config.format`) em vez de pedir JSON no prompt e limpar cercas
  de markdown na mão. O vocabulário controlado vira `enum` no esquema, então valor fora do
  vocabulário deixa de ser possível em vez de ser só desencorajado.
- Pensamento adaptativo ligado. O próprio briefing aponta o cronograma com muitas datas
  parecidas como o risco principal, que é exatamente o tipo de desambiguação que se beneficia
  disso.
"""

import io
import json
import os

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
    "apoio_formacao_capacitacao", "apoio_redes_grupos_pesquisa",
]
VOCAB_TIPO_INSTRUMENTO = ["chamada_publica_edital", "chamamento_publico", "premio"]
VOCAB_NATUREZA_RECURSO = ["custeio", "capital", "bolsa"]
VOCAB_PROPONENTE = [
    "pesquisadores", "especialistas", "mestrandos", "mestres", "doutorandos", "doutores",
    "ies", "ict", "empresas", "startups", "governo",
]
VOCAB_NIVEL_FORMACAO = ["mestrado", "doutorado", "pos_doutorado", "iniciacao_cientifica"]
VOCAB_MODALIDADE_PESSOA = ["atracao", "fixacao", "capacitacao_exterior"]
VOCAB_AREA_PRINCIPAL = [
    "Ciências Exatas e da Terra", "Ciências Biológicas", "Engenharias",
    "Ciências da Saúde", "Ciências Agrárias", "Ciências Sociais Aplicadas",
    "Ciências Humanas", "Linguística, Letras e Artes",
]
VOCAB_TIPO_PARCERIA = ["nacional", "regional", "internacional"]
VOCAB_ABRANGENCIA = ["nacional", "estadual", "regional", "internacional"]


def _campo(tipo_do_valor):
    """Um campo da resposta: o valor e o trecho literal que o justifica.

    `evidencia` é obrigatória no esquema (podendo ser null) para que a ausência de trecho seja
    uma escolha explícita do modelo, e não um campo que ele simplesmente esqueceu de emitir.
    """
    return {
        "type": "object",
        "properties": {
            "valor": tipo_do_valor,
            "evidencia": {"type": ["string", "null"]},
        },
        "required": ["valor", "evidencia"],
        "additionalProperties": False,
    }


def _lista(vocabulario):
    return {"type": ["array", "null"], "items": {"type": "string", "enum": vocabulario}}


def _unico(vocabulario):
    return {"type": ["string", "null"], "enum": vocabulario + [None]}


# O vocabulário controlado entra como `enum` no esquema: valor fora da lista deixa de ser
# possível, em vez de ficar só desencorajado pelo texto do prompt.
CAMPOS_ESPERADOS = {
    "linha_de_fomento": _lista(VOCAB_LINHA_DE_FOMENTO),
    "tipo_instrumento": _unico(VOCAB_TIPO_INSTRUMENTO),
    "natureza_recurso": _lista(VOCAB_NATUREZA_RECURSO),
    "proponente_elegivel": _lista(VOCAB_PROPONENTE),
    "nivel_formacao": _lista(VOCAB_NIVEL_FORMACAO),
    "modalidade_pessoa": _unico(VOCAB_MODALIDADE_PESSOA),
    "area_principal": _unico(VOCAB_AREA_PRINCIPAL),
    "tipo_parceria": _unico(VOCAB_TIPO_PARCERIA),
    "abrangencia": _unico(VOCAB_ABRANGENCIA),
    "instituicao_promotora": {"type": ["string", "null"]},
    "instituicao_financiadora": {"type": ["array", "null"], "items": {"type": "string"}},
    "uf": {"type": ["array", "null"], "items": {"type": "string"}},
    "data_publicacao": {"type": ["string", "null"]},
    "data_prazo": {"type": ["string", "null"]},
    "data_resultado_previsto": {"type": ["string", "null"]},
    "orcamento_total_chamada": {"type": ["number", "null"]},
    "valor_minimo_proposta": {"type": ["number", "null"]},
    "valor_maximo_proposta": {"type": ["number", "null"]},
    "palavras_chave": {"type": ["array", "null"], "items": {"type": "string"}},
}

ESQUEMA_RESPOSTA = {
    "type": "object",
    "properties": {
        "e_fomento": {"type": "boolean"},
        "observacao": {"type": ["string", "null"]},
        "campos": {
            "type": "object",
            "properties": {c: _campo(t) for c, t in CAMPOS_ESPERADOS.items()},
            "required": list(CAMPOS_ESPERADOS),
            "additionalProperties": False,
        },
    },
    "required": ["e_fomento", "observacao", "campos"],
    "additionalProperties": False,
}


def extrair_texto(link):
    """Baixa o link e extrai texto. Devolve (texto, tipo). Levanta exceção em falha."""
    link = url_real_do_pdf(link)
    resp = requests.get(link, timeout=60, headers={"User-Agent": "Mozilla/5.0"}, verify=False)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "").lower()

    # O magic number decide, não o Content-Type: vários sites das FAPs servem PDF como
    # application/octet-stream, e alguns servem HTML com .pdf na URL.
    if resp.content[:4] == b"%PDF" or "pdf" in content_type or link.lower().endswith(".pdf"):
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            texto = "\n".join((p.extract_text() or "") for p in pdf.pages[:MAX_PAGINAS_PDF])
        if not texto.strip():
            raise ValueError(
                "PDF sem camada de texto (digitalizado como imagem). Requer OCR — fora do escopo."
            )
        return texto[:MAX_CARACTERES], "pdf"

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    texto = soup.get_text("\n", strip=True)
    if not texto.strip():
        raise ValueError("Página sem conteúdo textual extraível.")
    return texto[:MAX_CARACTERES], "html"


PROMPT_SISTEMA = """Você extrai metadados estruturados de editais de fomento à pesquisa \
brasileiros para um portal público de descoberta de oportunidades.

Para CADA campo preenchido, inclua em "evidencia" o trecho LITERAL do edital que o \
justifica — copiado, não parafraseado. Se não encontrar a informação, use null em "valor" e \
em "evidencia". NUNCA invente nem infira além do que está escrito. Um campo em branco custa \
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

proponente_elegivel — QUEM PODE APRESENTAR A PROPOSTA.
Pessoa física: pesquisadores, especialistas, mestrandos, mestres, doutorandos, doutores.
Pessoa jurídica: ies, ict, empresas, startups, governo.
ATENÇÃO: marque quem SUBMETE, não quem é beneficiado. Se a proposta é submetida por uma \
instituição e a bolsa vai para estudantes, marque a instituição (ies ou ict), não os \
estudantes. IES e ICT se sobrepõem sem coincidir: uma universidade federal é as duas; a \
Fiocruz é ICT e não IES; uma faculdade só de ensino é IES e não ICT. Um edital que diga \
"IES/P" cobre as duas — marque ambas.

modalidade_pessoa — só quando linha_de_fomento incluir apoio_formacao_capacitacao. \
Editais de seleção ou concurso de PESQUISADOR são fomento, e a modalidade é fixacao.

palavras_chave — texto livre. Use também para:
- a população beneficiada específica, quando difere do proponente (mães, mulheres, \
quilombolas, pessoas com deficiência)
- a instituição demandante nomeada no edital (ex.: PROCON-SC)
- os eixos, temas ou linhas temáticas listados no edital

uf — siglas de duas letras, só quando a abrangência for estadual ou regional.

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
Seleção de pesquisador NÃO é esse caso — é fomento."""


def sugerir_campos(oportunidade):
    """Chama o modelo e devolve o dicionário de sugestões, já validado contra o esquema."""
    texto, tipo_fonte = extrair_texto(oportunidade.link)

    cliente = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    resposta = cliente.messages.create(
        model=MODELO,
        max_tokens=MAX_TOKENS,
        # O risco apontado na análise é o cronograma com várias datas parecidas — desambiguar
        # isso é exatamente o que o pensamento adaptativo faz bem.
        thinking={"type": "adaptive"},
        system=PROMPT_SISTEMA,
        output_config={"format": {"type": "json_schema", "schema": ESQUEMA_RESPOSTA}},
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

    # Com output_config.format a resposta é JSON válido; o bloco de texto é o primeiro
    # (blocos de pensamento vêm antes na lista, por isso o filtro por type).
    bruto = next(b.text for b in resposta.content if b.type == "text")
    sugestao = json.loads(bruto)
    sugestao["_meta"] = {
        "modelo": MODELO,
        "tipo_fonte": tipo_fonte,
        "caracteres_analisados": len(texto),
        "tokens_entrada": resposta.usage.input_tokens,
        "tokens_saida": resposta.usage.output_tokens,
    }
    return sugestao
