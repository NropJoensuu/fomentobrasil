import re
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlparse

REGIAO_POR_UF = {
    "AC": "Norte", "AP": "Norte", "AM": "Norte", "PA": "Norte", "RO": "Norte", "RR": "Norte", "TO": "Norte",
    "AL": "Nordeste", "BA": "Nordeste", "CE": "Nordeste", "MA": "Nordeste", "PB": "Nordeste",
    "PE": "Nordeste", "PI": "Nordeste", "RN": "Nordeste", "SE": "Nordeste",
    "DF": "Centro-Oeste", "GO": "Centro-Oeste", "MT": "Centro-Oeste", "MS": "Centro-Oeste",
    "ES": "Sudeste", "MG": "Sudeste", "RJ": "Sudeste", "SP": "Sudeste",
    "PR": "Sul", "RS": "Sul", "SC": "Sul",
}


def get_regioes(ufs):
    """Recebe lista de UFs, retorna lista de regiões únicas (ordenada)."""
    if not ufs:
        return []
    return sorted({REGIAO_POR_UF[uf] for uf in ufs if uf in REGIAO_POR_UF})


def get_ufs_por_regiao(regiao):
    return [uf for uf, r in REGIAO_POR_UF.items() if r == regiao]


REGIOES = sorted(set(REGIAO_POR_UF.values()))


# Vocabulário de `proponente_elegivel`, dividido só para a interface — no banco é um campo
# único. A divisão existe porque a pergunta que o curador se faz é diferente dos dois lados:
# "o edital aceita a pessoa submetendo em nome próprio?" versus "aceita a instituição?".
PROPONENTE_PESSOA_FISICA = [
    ("pesquisadores", "Pesquisadores"),
    ("especialistas", "Especialistas"),
    ("mestrandos", "Mestrandos"),
    ("mestres", "Mestres"),
    ("doutorandos", "Doutorandos"),
    ("doutores", "Doutores"),
]

# Rótulos expandidos de propósito: a distinção entre IES e ICT é sutil e o curador precisa
# lembrar dela na hora. Uma universidade federal é as duas; a Fiocruz é ICT e não é IES; uma
# faculdade só de ensino é IES e não é ICT.
PROPONENTE_PESSOA_JURIDICA = [
    ("ies", "IES — Instituição de Ensino Superior"),
    ("ict", "ICT — Instituição Científica, Tecnológica e de Inovação"),
    ("empresas", "Empresas"),
    ("startups", "Startups"),
    ("governo", "Governo"),
]

VOCABULARIO_PROPONENTE = [v for v, _ in PROPONENTE_PESSOA_FISICA + PROPONENTE_PESSOA_JURIDICA]


def parse_valor_brl(texto):
    """Converte o texto de um campo com máscara de moeda em Decimal.

    Espelha a regra da máscara `.mascara-moeda` (base.html): só os dígitos importam e os
    dois últimos são os centavos. Como a máscara roda tanto ao digitar quanto ao carregar
    a página, o formulário sempre envia um valor já formatado — "R$ 12.345,67" — ou o
    valor cru do banco ("12345.67"), e ambos caem na mesma regra.

    Sem separador decimal nenhum o número é lido como inteiro em reais, para que um valor
    colado à mão ("12345") não vire R$ 123,45.
    """
    texto = (texto or "").strip()
    if not texto:
        return None
    tem_separador_decimal = "," in texto or "." in texto
    digitos = re.sub(r"\D", "", texto)
    if not digitos:
        return None
    if tem_separador_decimal:
        return Decimal(digitos) / 100
    return Decimal(digitos)


def parse_faixas(form):
    """Lê os campos indexados `faixa-<i>-<campo>` do formulário e devolve a lista de faixas.

    Os índices não são contínuos — o JS que remove uma linha deixa buracos de propósito
    (ver comentário em templates/_faixas.html) — então a varredura parte das chaves
    presentes no formulário, não de um `range()`.

    Faixas sem nome e sem valor nenhum são descartadas: é o que sobra quando o curador
    clica em "Adicionar faixa" e desiste sem preencher.

    Os valores viram string decimal com DUAS casas ("50000.00") porque o destino é
    `dados_extra`, que é JSONB — `Decimal` não é serializável em JSON, e `float` perderia
    precisão em dinheiro. As duas casas não são cosméticas: a máscara do formulário lê o
    valor como centavos, então gravar "50000" faria a faixa reaparecer como R$ 500,00 na
    próxima edição. É a mesma forma que a coluna `Numeric(14, 2)` já produz.
    """
    def em_centavos(campo):
        valor = parse_valor_brl(form.get(campo))
        return str(valor.quantize(Decimal("0.01"))) if valor is not None else None

    indices = sorted(
        {int(m.group(1)) for m in (re.match(r"faixa-(\d+)-", chave) for chave in form) if m}
    )

    faixas = []
    for i in indices:
        faixa = {
            "nome": (form.get(f"faixa-{i}-nome") or "").strip() or None,
            "descricao": (form.get(f"faixa-{i}-descricao") or "").strip() or None,
            "valor_minimo": em_centavos(f"faixa-{i}-valor_minimo"),
            "valor_maximo": em_centavos(f"faixa-{i}-valor_maximo"),
            "proponente_elegivel": form.getlist(f"faixa-{i}-proponente_elegivel") or None,
            "area_principal": form.get(f"faixa-{i}-area_principal") or None,
        }
        if any(faixa.values()):
            faixas.append(faixa)

    return faixas


def aplicar_faixas(dados_extra, faixas):
    """Grava `faixas` em `dados_extra` sem perder o que o scraper escreveu ali.

    `dados_extra` é compartilhado com os scrapers (numero_edital, documentos,
    fonte_baixa_estruturacao...). Devolve um dicionário novo — reatribuir a coluna é o que
    faz o SQLAlchemy detectar a mudança num JSONB.
    """
    novo = dict(dados_extra or {})
    if faixas:
        novo["faixas"] = faixas
    else:
        novo.pop("faixas", None)
    return novo or None


def formatar_moeda(valor):
    """Formata um valor monetário em pt-BR: 236000 -> "R$ 236.000,00".

    Aceita `Decimal` (as colunas `Numeric`) e `str` (as faixas, que vivem em JSONB).
    Registrado como filtro Jinja `moeda` em `create_app`.
    """
    if valor in (None, ""):
        return ""
    try:
        numero = Decimal(str(valor))
    except InvalidOperation:
        return str(valor)
    # Formata em en-US (1,234.56) e troca os separadores: evita depender de locale
    # instalado no sistema, que é o modo clássico de isso quebrar em produção.
    texto = f"{numero:,.2f}"
    return "R$ " + texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


# Os 19 scrapers gravam esta linha de fomento quando não conseguem inferi-la do título
# (que é quase sempre). Ver o comentário "Placeholder" em cada `salvar_no_banco`. Um
# registro que ainda esteja exatamente assim provavelmente não passou pela curadoria.
PLACEHOLDER_LINHA_DE_FOMENTO = ["apoio_formacao_capacitacao"]

# Campos que, vazios, não impedem aprovar mas quase sempre significam curadoria
# incompleta — a busca pública fica pior sem eles. Ver `avisos_de_aprovacao`.
# (campo, mensagem, é_lista) — `é_lista` diz como ler o campo no formulário: `getlist`
# para os ARRAY, `get` para os escalares.
AVISOS_POR_CAMPO = [
    ("natureza_recurso", "Natureza do Recurso não foi marcada", True),
    ("proponente_elegivel", "Proponente Elegível não foi marcado", True),
    ("data_prazo", "Data Final de Submissão em branco", False),
]

# Campos opcionais cuja ausência vale mostrar no painel de conferência, sem alarde.
OPCIONAIS_POR_CAMPO = [
    ("descricao", "Descrição"),
    ("area_principal", "Área Principal"),
    ("palavras_chave", "Palavras-chave"),
    ("data_publicacao", "Data de Publicação"),
    ("data_resultado_previsto", "Data Prevista do Resultado"),
    ("orcamento_total_chamada", "Orçamento Total da Chamada"),
    ("valor_minimo_proposta", "Valor Mínimo da Proposta"),
    ("valor_maximo_proposta", "Valor Máximo da Proposta"),
    ("abrangencia", "Abrangência"),
]


def _vazio(valor):
    return valor is None or valor == "" or valor == []


def resumo_preenchimento(oportunidade):
    """Panorama do que falta num registro, para o painel no topo do formulário.

    Devolve `(importantes, opcionais, placeholder)`. É só leitura do estado atual —
    diferente de `avisos_de_aprovacao`, que decide se a aprovação pede confirmação.
    """
    importantes = [
        rotulo
        for campo, rotulo, _ in AVISOS_POR_CAMPO
        if _vazio(getattr(oportunidade, campo, None))
    ]
    opcionais = [
        rotulo
        for campo, rotulo in OPCIONAIS_POR_CAMPO
        if _vazio(getattr(oportunidade, campo, None))
    ]
    placeholder = (
        list(oportunidade.linha_de_fomento or []) == PLACEHOLDER_LINHA_DE_FOMENTO
    )
    return importantes, opcionais, placeholder


def avisos_de_aprovacao(form):
    """Avisos que fazem a aprovação pedir uma confirmação explícita.

    Recebe o formulário submetido (e não o registro no banco) de propósito: o que vale é
    o que está sendo gravado agora, não o que estava lá antes.

    Foi #34 e #215 aprovados com `natureza_recurso` vazio que motivaram isto. A aprovação
    não é bloqueada — há editais em que a informação realmente não existe —, mas passa a
    exigir um "aprovar mesmo assim" consciente.
    """
    avisos = []
    for campo, mensagem, e_lista in AVISOS_POR_CAMPO:
        if not (form.getlist(campo) if e_lista else form.get(campo)):
            avisos.append(mensagem)

    if form.getlist("linha_de_fomento") == PLACEHOLDER_LINHA_DE_FOMENTO:
        avisos.append(
            "Linha de Fomento continua em “Apoio à Formação/Capacitação”, que é o valor "
            "que os scrapers gravam quando não conseguem inferir — confirme se é mesmo essa"
        )

    return avisos


def url_real_do_pdf(url):
    """Desembrulha URLs de download que carregam o arquivo real num parâmetro `url=`.

    A FAPEMIG serve os PDFs por um intermediário: o link visível é
    `fapemig.br/files/Chamada-16%2F2026?title=...&url=https://api.site.fapemig.br/...pdf`,
    e é o parâmetro `url` que aponta para o arquivo — o endereço de fora devolve HTML.
    Colar o link da página, que é o gesto natural, dava "a URL não devolveu um PDF".
    """
    if not url:
        return url
    embutida = parse_qs(urlparse(url).query).get("url", [None])[0]
    if embutida and embutida.lower().split("?")[0].endswith(".pdf"):
        return embutida
    return url
