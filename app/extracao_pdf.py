"""Extração de campos de curadoria a partir do PDF do edital.

Motivação medida, não suposta. Comparando os 9 registros curados à mão contra o texto dos
respectivos PDFs (2026-08-30), os campos se separam em três camadas bem distintas:

1. **Literais** — datas, valores e faixas aparecem no PDF exatamente como o curador os
   gravou, em 90%+ dos casos. São estes que este módulo extrai.
2. **Inferíveis com contagem** — `natureza_recurso`: a presença de "custeio"/"capital"/
   "bolsa" acerta, mas só com limiar. Uma menção isolada não significa que a chamada
   concede aquilo (a #23 cita "bolsa" uma vez e não concede nenhuma).
3. **Julgamento** — `publico_alvo`, `linha_de_fomento`, `abrangencia`. Aqui contar palavra
   é ativamente errado: a #22 e a #215 mencionam "pesquisador" 17 e 18 vezes e o curador
   NÃO marcou `pesquisadores`, porque quem submete é a instituição. E a #118 tem
   `mestrandos`/`mestres`/`doutorandos` marcados com ZERO ocorrências dessas palavras — o
   curador leu a tabela de modalidades de bolsa e deduziu a titulação exigida.

Por isso este módulo **sugere e não preenche**: devolve candidatos com o trecho de origem e
a página, para o curador conferir e aceitar campo a campo.

DUAS ARMADILHAS REAIS, ambas encontradas nos 9 registros:

- **O documento certo pode não ser o do link.** A #21 (CNPq 19/2026) tem prazo 31/08/2026,
  que NÃO está no PDF da chamada — está numa *retificação de cronograma* publicada depois
  (o PDF original diz 10/08/2026). E o link da #34 leva a um "Guia Informativo" que diz de
  si mesmo que não substitui o edital.
- **Data sem dia.** Cronogramas do CNPq usam "Outubro/2026" para o resultado. O curador
  converteu para 01/10/2026 — dia 1 como convenção. Está reproduzido aqui em
  `DIA_PADRAO_MES_SEM_DIA`, e o candidato sai marcado com `mes_sem_dia=True` para a
  interface poder avisar que a data é aproximada.
"""

import io
import re
from datetime import date

import pdfplumber

# Convenção do curador para cronogramas que só dão mês ("Outubro/2026" -> 01/10/2026).
DIA_PADRAO_MES_SEM_DIA = 1

MESES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5,
    "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}

# Aceita 14/09/2026, 14.09.2026 e 14-09-2026. O ponto NÃO é detalhe: o cronograma inteiro
# da FAPES usa "⎯ A partir de 27.11.2026 Divulgação do resultado final", e só com barra o
# extrator não via nenhuma etapa daquele edital. O ano com 4 dígitos e os limites de palavra
# impedem que valores monetários ("R$ 1.200.000,00") sejam lidos como data.
PADRAO_DATA = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b")
# Aceita "Outubro/2026" e "até dezembro de 2026" — o CNPq usa as duas formas no mesmo
# cronograma, e o segundo formato é justamente o da divulgação do resultado.
PADRAO_MES_ANO = re.compile(
    rf"\b({'|'.join(MESES)})\s*(?:/|\s+de\s+)\s*(\d{{4}})\b", re.IGNORECASE
)
PADRAO_VALOR = re.compile(r"R\$\s*([\d.]+,\d{2}|[\d.]+)")

# Quanto texto olhar em volta da data para descobrir a que etapa ela pertence.
#
# Olhar SÓ para trás não funciona, e olhar longe demais é pior que não olhar. Duas razões
# medidas nos PDFs reais:
#
# - Na FACEPE o rótulo vem DEPOIS da data, porque o extrator lê a coluna de datas antes da
#   coluna de fases: "21/09/2026 (até 23h59, Limite para submissão (Sistema AgilFAP)".
# - Uma janela fixa e larga atravessa linhas da tabela e captura o rótulo da etapa anterior.
#   Foi o que fez a FAPESC devolver 16/09 como data de publicação: o "Lançamento da Chamada
#   28/07/2026" ainda estava dentro dos 260 caracteres anteriores.
#
# A correção é limitar a janela pelas datas VIZINHAS — cada data enxerga só o texto que a
# separa da anterior e da seguinte, que é exatamente a linha dela na tabela.
JANELA_ANTES = 240
JANELA_DEPOIS = 130

# Palavras que podem aparecer ENTRE duas datas sem que sejam etapas diferentes: é um
# intervalo ("de 29/07/2026 a 31/08/2026", "De 31/07/2026 até às 17h do dia 08/09/2026").
# Um intervalo vira um grupo só, classificado pelo rótulo do grupo — e, para prazo, o valor
# é a ponta final.
CONECTORES = {
    "a", "ate", "até", "as", "às", "de", "do", "da", "dia", "e", "h", "hs", "horas",
    "horario", "horário", "entre", "no", "partir", "ao",
}
TAMANHO_MAXIMO_CONECTOR = 45

# Extração de tabela às vezes injeta o conteúdo de OUTRA COLUNA no meio de um intervalo. Na
# FUNDECT o par vira "De 31/07/2026 até às 17h do dia Proponente propostas no SIGFUNDECT
# 08/09/2026": ainda é um intervalo só, mas com três palavras estranhas no meio. Sem tolerar
# isso, o prazo extraído era 31/07 — a ponta errada.
TAMANHO_MAXIMO_CONECTOR_TOLERANTE = 95
MAXIMO_PALAVRAS_ESTRANHAS = 4
MARCADORES_DE_INTERVALO = {"a", "ate", "até"}

# (campo, prioridade, padrão positivo, padrão negativo). Prioridade maior ganha quando duas
# regras casam com a mesma data. Os padrões foram derivados dos cronogramas reais de CNPq,
# FUNDECT, FAPESC, FACEPE, FAPES e FAPEMIG — não de suposição sobre como "deveriam" ser.
REGRAS_DATA = [
    ("data_prazo", 30, r"data\s+limite\s+para\s+submiss|prazo\s+final\s+para\s+submiss", r"impugna[çc]|recurso administrativo"),
    ("data_prazo", 20, r"per[íi]odo\s+(?:para\s+)?(?:de\s+)?(?:submiss|inscri)", r"impugna[çc]|recurso administrativo"),
    ("data_prazo", 28, r"limite\s+para\s+submiss|limite\s+para\s+(?:o\s+)?envio", r"impugna[çc]|recurso administrativo"),
    ("data_prazo", 25, r"(?:enviar|submeter)\s+a\s+proposta", r"impugna[çc]|recurso administrativo"),
    ("data_prazo", 10, r"submiss[ãa]o\s+(?:eletr[ôo]nica\s+)?(?:das?\s+)?propostas?|inscri[çc][ãa]o",
     r"impugna|recurso|resultado|homologa"),

    ("data_resultado_previsto", 30, r"divulga[çc][ãa]o\s+d[oa]s?\s+resultados?\s+final|resultado\s+final\s+d[eo]\s+julgamento", None),
    ("data_resultado_previsto", 20, r"resultado\s+final", r"admissibilidade|enquadrad"),
    # "homologação" não tem versão preliminar — a negativa larga só fazia a regra
    # morrer por causa do "resultado preliminar" da linha anterior da tabela.
    ("data_resultado_previsto", 25, r"homologa[çc][ãa]o", None),
    ("data_resultado_previsto", 24, r"divulga[çc][ãa]o\s+d[ae]\s+decis[ãa]o", None),
    ("data_resultado_previsto", 10, r"divulga[çc][ãa]o\s+d[oa]s?\s+resultados?", r"preliminar|admissibilidade|enquadrad"),

    ("data_publicacao", 30, r"lan[çc]amento\s+d[ao]\s+(?:chamada|edital)",
     r"ap[óo]s\s+o\s+lan[çc]amento|impugna[çc]"),
    ("data_publicacao", 20, r"publica[çc][ãa]o\s+n[oa]\s+di[áa]rio|an[úu]ncio\s+d[ao]\s+chamada", None),
    # A FAPES não põe data de publicação no cronograma: o que existe é a assinatura
    # eletrônica do diretor, na última página. Prioridade baixa, por ser um proxy.
    ("data_publicacao", 5, r"assinado\s+em|assinatura\s+eletr", None),
]

REGRAS_VALOR = [
    ("orcamento_total_chamada", 30, r"valor\s+(?:global|total)|montante\s+(?:global|total)|recursos?\s+(?:financeiros?\s+)?(?:total|global)", None),
    ("orcamento_total_chamada", 20, r"dota[çc][ãa]o\s+or[çc]ament|total\s+d[eo]s?\s+recursos|totalizando", None),
    ("valor_maximo_proposta", 30, r"valor\s+m[áa]ximo\s+(?:por\s+)?(?:proposta|projeto)|limite\s+m[áa]ximo\s+por\s+(?:proposta|projeto)", None),
    ("valor_maximo_proposta", 28, r"teto\s+or[çc]ament", None),
    ("valor_maximo_proposta", 25, r"(?:projetos?|propostas?)\s+de\s+at[ée]\s*$|de\s+at[ée]\s*$", None),
    ("valor_maximo_proposta", 20, r"at[ée]\s+o?\s*valor\s+de|limitad[oa]s?\s+a", None),
    ("valor_minimo_proposta", 30, r"valor\s+m[íi]nimo\s+(?:por\s+)?(?:proposta|projeto)", None),
    # Só dispara em intervalo (dois valores ligados por "a"/"até"), onde a faixa por
    # proposta é o significado quase certo mesmo sem rótulo canônico.
    ("valor_maximo_proposta", 12, r"projetos?|propostas?", None),
]


class Candidato:
    """Um valor encontrado no PDF, com de onde veio — a procedência é o ponto."""

    def __init__(self, campo, valor, pagina, trecho, prioridade, mes_sem_dia=False):
        self.campo = campo
        self.valor = valor
        self.pagina = pagina
        self.trecho = trecho
        self.prioridade = prioridade
        self.mes_sem_dia = mes_sem_dia

    def como_dict(self):
        return {
            "campo": self.campo,
            "valor": self.valor.isoformat() if isinstance(self.valor, date) else self.valor,
            "pagina": self.pagina,
            "trecho": self.trecho,
            "mes_sem_dia": self.mes_sem_dia,
        }


def extrair_paginas(conteudo_pdf):
    """Texto de cada página. Devolve lista vazia se o PDF não tiver camada de texto."""
    with pdfplumber.open(io.BytesIO(conteudo_pdf)) as doc:
        return [p.extract_text() or "" for p in doc.pages]


def _normalizar(texto):
    return re.sub(r"\s+", " ", texto)


# O rótulo que vem DEPOIS da data vale menos que o que vem antes. Sem esse desempate, o
# "25/05/2026" do lançamento da chamada CNPq era classificado como prazo, porque a linha
# seguinte do cronograma ("Data limite para submissão das propostas") caía na janela de
# depois com a mesma prioridade.
PENALIDADE_ROTULO_POSTERIOR = 3


def _classificar(antes, depois, regras):
    """Melhor (campo, prioridade) para uma data/valor, ou (None, 0).

    A positiva é procurada primeiro no texto anterior e só depois no posterior, com
    desconto: o rótulo posterior existe (a FACEPE lê a coluna de datas antes da de fases),
    mas é o caso menos comum.

    A negativa vale APENAS do lado em que a positiva casou, e só quando estiver MAIS PERTO
    da data que a positiva. Duas lições dos cronogramas reais:

    - Avaliar a negativa nos dois lados derruba o alvo certo: o "impugnação" da linha de
      baixo matava o "Lançamento da Chamada" da linha de cima.
    - Nem toda etapa tem data ("Prazo para impugnação: 10 dias corridos após o lançamento"),
      então a janela entre duas datas às vezes contém duas etapas inteiras. Na chamada CNPq
      15/2026 ela traz "impugnação ... Data limite para submissão das propostas 31/08/2026":
      as duas casam, e quem vale é a etiqueta colada na data.
    """
    melhor = (None, 0)
    for campo, prioridade, positivo, negativo in regras:
        for texto, desconto, perto_e_maior in (
            (antes, 0, True),                              # em "antes", perto = índice maior
            (depois, PENALIDADE_ROTULO_POSTERIOR, False),  # em "depois", perto = índice menor
        ):
            achado_positivo = None
            for m in re.finditer(positivo, texto, re.IGNORECASE):
                achado_positivo = m.start() if perto_e_maior else (achado_positivo if achado_positivo is not None else m.start())
            if achado_positivo is None:
                continue
            if negativo:
                achado_negativo = None
                for m in re.finditer(negativo, texto, re.IGNORECASE):
                    achado_negativo = m.start() if perto_e_maior else (achado_negativo if achado_negativo is not None else m.start())
                if achado_negativo is not None:
                    mais_perto = (
                        achado_negativo > achado_positivo if perto_e_maior
                        else achado_negativo < achado_positivo
                    )
                    if mais_perto:
                        break  # a etiqueta colada na data é a negada
            if prioridade - desconto > melhor[1]:
                melhor = (campo, prioridade - desconto)
            break
    return melhor


def _ocorrencias(texto, padrao_completo=True):
    """Todas as datas de um texto, como (inicio, fim, date, mes_sem_dia)."""
    achados = []
    for m in PADRAO_DATA.finditer(texto):
        dia, mes, ano = (int(g) for g in m.groups())
        try:
            achados.append((m.start(), m.end(), date(ano, mes, dia), False))
        except ValueError:
            continue
    for m in PADRAO_MES_ANO.finditer(texto):
        valor = date(int(m.group(2)), MESES[m.group(1).lower()], DIA_PADRAO_MES_SEM_DIA)
        achados.append((m.start(), m.end(), valor, True))
    return sorted(achados)


def _e_conector(separador):
    """True se o texto entre duas datas for só ligação de intervalo, não outra etapa."""
    palavras = [p.lower() for p in re.findall(r"[^\W\d_]+", separador, re.UNICODE)]
    estranhas = [p for p in palavras if p not in CONECTORES]

    if len(separador) <= TAMANHO_MAXIMO_CONECTOR and not estranhas:
        return True

    # Tolerância para o lixo de coluna vizinha (ver comentário acima). Exige um marcador
    # explícito de intervalo para não colar duas etapas distintas num grupo só.
    return (
        len(separador) <= TAMANHO_MAXIMO_CONECTOR_TOLERANTE
        and len(estranhas) <= MAXIMO_PALAVRAS_ESTRANHAS
        and any(p in MARCADORES_DE_INTERVALO for p in palavras)
    )


def _agrupar(ocorrencias, texto):
    """Junta datas consecutivas que formam um intervalo. Devolve grupos de ocorrências."""
    grupos = []
    for oc in ocorrencias:
        if grupos and _e_conector(texto[grupos[-1][-1][1]:oc[0]]):
            grupos[-1].append(oc)
        else:
            grupos.append([oc])
    return grupos


def _candidatos_de_data(paginas):
    encontrados = []
    for n_pagina, bruto in enumerate(paginas, 1):
        texto = _normalizar(bruto)
        grupos = _agrupar(_ocorrencias(texto), texto)

        for i, grupo in enumerate(grupos):
            inicio, fim = grupo[0][0], grupo[-1][1]
            # Janela limitada pelas datas vizinhas: é o que mantém o rótulo de uma linha da
            # tabela fora da classificação da linha seguinte (ver comentário em JANELA_ANTES).
            limite_anterior = grupos[i - 1][-1][1] if i else 0
            limite_seguinte = grupos[i + 1][0][0] if i + 1 < len(grupos) else len(texto)
            antes = texto[max(limite_anterior, inicio - JANELA_ANTES):inicio]
            depois = texto[fim:min(limite_seguinte, fim + JANELA_DEPOIS)]

            campo, prioridade = _classificar(antes, depois, REGRAS_DATA)
            if not campo:
                continue

            # Num intervalo, o prazo é a ponta final; as demais etapas são pontuais e a
            # primeira data é a que vale.
            oc = max(grupo, key=lambda o: o[2]) if campo == "data_prazo" else grupo[0]
            encontrados.append(Candidato(
                campo, oc[2], n_pagina,
                texto[max(0, inicio - 120):fim + 60].strip(),
                prioridade - (5 if oc[3] else 0),
                mes_sem_dia=oc[3],
            ))
    return encontrados


def _candidatos_de_valor(paginas):
    encontrados = []
    for n_pagina, bruto in enumerate(paginas, 1):
        texto = _normalizar(bruto)
        marcas = [m for m in PADRAO_VALOR.finditer(texto) if "," in m.group(1) or "." in m.group(1)]

        # Mesma lógica de agrupamento das datas: "projetos que demandem de R$ 70.000,00 a
        # R$ 180.000,00" é UM intervalo, não dois valores soltos. O rótulo está antes do
        # par, e sem agrupar o segundo valor fica com um contexto de dois caracteres (" a ").
        grupos = []
        for m in marcas:
            if grupos and _e_conector(texto[grupos[-1][-1].end():m.start()]):
                grupos[-1].append(m)
            else:
                grupos.append([m])

        for i, grupo in enumerate(grupos):
            valores = []
            for m in grupo:
                cru = m.group(1).replace(".", "").replace(",", ".")
                try:
                    valores.append((float(cru), cru))
                except ValueError:
                    continue
            if not valores:
                continue

            inicio, fim = grupo[0].start(), grupo[-1].end()
            limite_anterior = grupos[i - 1][-1].end() if i else 0
            limite_seguinte = grupos[i + 1][0].start() if i + 1 < len(grupos) else len(texto)
            antes = texto[max(limite_anterior, inicio - JANELA_ANTES):inicio]
            depois = texto[fim:min(limite_seguinte, fim + JANELA_DEPOIS)]

            campo, prioridade = _classificar(antes, depois, REGRAS_VALOR)
            if not campo or (prioridade <= 12 and len(valores) < 2):
                continue

            trecho = texto[max(0, inicio - 120):fim + 60].strip()
            if len(valores) > 1:
                # Um intervalo preenche os dois campos de uma vez.
                encontrados.append(Candidato("valor_minimo_proposta", min(valores)[1], n_pagina, trecho, prioridade))
                encontrados.append(Candidato("valor_maximo_proposta", max(valores)[1], n_pagina, trecho, prioridade))
            else:
                encontrados.append(Candidato(campo, valores[0][1], n_pagina, trecho, prioridade))
    return encontrados


def extrair_candidatos(paginas):
    """Agrupa os candidatos por campo, do mais confiável para o menos.

    Um mesmo campo pode ter vários candidatos de propósito: o cronograma cita "resultado
    preliminar" e "resultado final", e quem decide qual vale é o curador. A ordenação é por
    prioridade da regra e, para `data_prazo`, pela data mais TARDIA em caso de empate — um
    período "de 29/07 a 31/08" tem as duas pontas casando com a mesma regra, e o prazo é a
    ponta final.
    """
    por_campo = {}
    for c in _candidatos_de_data(paginas) + _candidatos_de_valor(paginas):
        por_campo.setdefault(c.campo, []).append(c)

    for campo, lista in por_campo.items():
        lista.sort(key=lambda c: (c.prioridade, c.valor if campo.startswith("data") else 0), reverse=True)
        # Remove repetições do mesmo valor mantendo a ocorrência mais confiável.
        vistos, unicos = set(), []
        for c in lista:
            if c.valor in vistos:
                continue
            vistos.add(c.valor)
            unicos.append(c)
        por_campo[campo] = unicos[:5]

    return por_campo
