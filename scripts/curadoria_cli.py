#!/usr/bin/env python3
"""Curadoria de oportunidades pelo terminal, sem depender do app Flask.

Alternativa à tela web `/moderacao` para quando não dá para subir o Flask.
Usa a mesma `DATABASE_URL` do `.env` e lê o schema por reflection — assim não
duplica `app/models.py` e não quebra quando uma coluna muda.

    python scripts/curadoria_cli.py
    python scripts/curadoria_cli.py --status pendente --limite 10

Depende só de SQLAlchemy + psycopg2 + python-dotenv (já no requirements.txt).
"""

import argparse
import os
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from dotenv import load_dotenv
from sqlalchemy import MetaData, Table, create_engine, select, update

TABELA = "oportunidades"

# Vocabulários fechados, iguais aos do formulário web (templates/oportunidades/nova.html)
# e da tela de moderação. Manter em sincronia se o formulário mudar.
VOCAB_LINHA_DE_FOMENTO = [
    "auxilio_pesquisa",
    "auxilio_inovacao",
    "auxilio_divulgacao_cientifica",
    "apoio_formacao_capacitacao",
    "apoio_redes_grupos_pesquisa",
]
VOCAB_NATUREZA_RECURSO = ["custeio", "capital", "bolsa"]
VOCAB_PUBLICO_ALVO = [
    "pesquisadores", "empresas", "startups", "ict",
    "mestrandos", "doutorandos", "ies", "governo",
]
VOCAB_TIPO_INSTRUMENTO = ["chamada_publica_edital", "chamamento_publico", "premio"]
VOCAB_AREA_PRINCIPAL = [
    "Ciências Exatas e da Terra", "Ciências Biológicas", "Engenharias",
    "Ciências da Saúde", "Ciências Agrárias", "Ciências Sociais Aplicadas",
    "Ciências Humanas", "Linguística, Letras e Artes",
]
VOCAB_NIVEL_FORMACAO = [
    "mestrado", "doutorado", "pos_doutorado", "iniciacao_cientifica", "nao_aplicavel",
]
VOCAB_ABRANGENCIA = ["nacional", "estadual", "regional", "internacional"]
VOCAB_TIPO_PARCERIA = ["nacional", "regional", "internacional"]
VOCAB_MODALIDADE_PESSOA = ["atracao", "fixacao", "capacitacao_exterior"]
VOCAB_STATUS_OFICIAL = ["suspensa", "cancelada", "retificada", "resultado_divulgado"]

UFS = [
    "AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG","PA","PB",
    "PR","PE","PI","RJ","RN","RS","RO","RR","SC","SP","SE","TO",
]

# (coluna, rótulo, tipo, vocabulário)
# tipo: "texto" | "texto_longo" | "lista" | "lista_livre" | "escolha" | "data" | "decimal"
CAMPOS_EDITAVEIS = [
    ("titulo",                  "Título",                     "texto",       None),
    ("descricao",               "Descrição",                  "texto_longo", None),
    ("link",                    "Link do edital",             "texto",       None),
    ("linha_de_fomento",        "Linha de fomento",           "lista",       VOCAB_LINHA_DE_FOMENTO),
    ("tipo_instrumento",        "Tipo de instrumento",        "escolha",     VOCAB_TIPO_INSTRUMENTO),
    ("natureza_recurso",        "Natureza do recurso",        "lista",       VOCAB_NATUREZA_RECURSO),
    ("publico_alvo",            "Público-alvo",               "lista",       VOCAB_PUBLICO_ALVO),
    ("instituicao_financiadora","Instituição financiadora",   "lista_livre", None),
    ("instituicao_executora",   "Instituição executora",      "texto",       None),
    ("instituicao_beneficiaria","Instituição beneficiária",   "texto",       None),
    ("area_principal",          "Área principal",             "escolha",     VOCAB_AREA_PRINCIPAL),
    ("palavras_chave",          "Palavras-chave",             "lista_livre", None),
    ("nivel_formacao",          "Nível de formação",          "escolha",     VOCAB_NIVEL_FORMACAO),
    ("abrangencia",             "Abrangência",                "escolha",     VOCAB_ABRANGENCIA),
    ("uf",                      "UF",                         "lista",       UFS),
    ("cidade",                  "Cidade",                     "texto",       None),
    ("tipo_parceria",           "Tipo de parceria",           "escolha",     VOCAB_TIPO_PARCERIA),
    ("modalidade_pessoa",       "Modalidade de pessoa",       "escolha",     VOCAB_MODALIDADE_PESSOA),
    ("status_oficial",          "Status oficial do edital",   "escolha",     VOCAB_STATUS_OFICIAL),
    ("data_publicacao",         "Data de publicação",         "data",        None),
    ("data_prazo",              "Data final de submissão",    "data",        None),
    ("data_resultado_previsto", "Data prevista do resultado", "data",        None),
    ("orcamento_total_chamada", "Orçamento total",            "decimal",     None),
    ("valor_minimo_proposta",   "Valor mínimo da proposta",   "decimal",     None),
    ("valor_maximo_proposta",   "Valor máximo da proposta",   "decimal",     None),
]

# Campos NOT NULL de lista: aprovar com um deles vazio passa no banco (lista vazia não é
# NULL) mas deixa o registro sem classificação. Os dois primeiros a tela web bloqueia;
# os outros dois viram só aviso, para não travar a curadoria.
OBRIGATORIOS = ["linha_de_fomento", "instituicao_financiadora"]
RECOMENDADOS = ["natureza_recurso", "publico_alvo"]


# --------------------------------------------------------------------------- infra

def obter_engine():
    """Cria o engine a partir da DATABASE_URL do .env."""
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        sys.exit(
            "DATABASE_URL não encontrada. Rode a partir da raiz do projeto (onde está "
            "o .env) ou exporte a variável antes de chamar o script."
        )
    # SQLAlchemy 2.x não aceita o esquema legado 'postgres://' que alguns provedores usam.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    # pool_pre_ping é essencial aqui: o Neon derruba conexão ociosa, e numa curadoria
    # interativa a conexão fica parada enquanto o curador lê o registro e decide.
    return create_engine(url, pool_pre_ping=True, pool_recycle=300, future=True)


# ----------------------------------------------------------------- formatação/parse

def formatar_valor(valor):
    if valor is None:
        return "—"
    if isinstance(valor, list):
        return ", ".join(str(v) for v in valor) if valor else "(vazio)"
    if isinstance(valor, (date, datetime)):
        return valor.strftime("%d/%m/%Y")
    if isinstance(valor, Decimal):
        # Formato brasileiro: milhar com ponto, decimal com vírgula.
        return "R$ " + f"{valor:,.2f}".translate(str.maketrans({",": ".", ".": ","}))
    if isinstance(valor, bool):
        return "sim" if valor else "não"
    texto = str(valor)
    return texto if texto.strip() else "—"


def parse_data(texto):
    """Aceita DD/MM/AAAA ou AAAA-MM-DD. Devolve (ok, valor)."""
    texto = (texto or "").strip()
    if not texto:
        return True, None
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return True, datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return False, None


def parse_decimal(texto):
    """Aceita 1234.56 ou 1.234,56. Devolve (ok, valor)."""
    texto = (texto or "").strip()
    if not texto:
        return True, None
    normalizado = texto.replace("R$", "").strip()
    if "," in normalizado:  # formato brasileiro: 1.234,56
        normalizado = normalizado.replace(".", "").replace(",", ".")
    try:
        return True, Decimal(normalizado)
    except InvalidOperation:
        return False, None


def parse_lista_livre(texto):
    """Separa por vírgula, descartando vazios. String vazia devolve lista vazia."""
    return [p.strip() for p in (texto or "").split(",") if p.strip()]


def parse_selecao(texto, vocabulario):
    """Converte '1,3' em valores do vocabulário. Devolve (ok, lista)."""
    texto = (texto or "").strip()
    if not texto:
        return True, []
    escolhidos = []
    for parte in texto.split(","):
        parte = parte.strip()
        if not parte.isdigit():
            return False, []
        indice = int(parte)
        if not 1 <= indice <= len(vocabulario):
            return False, []
        valor = vocabulario[indice - 1]
        if valor not in escolhidos:
            escolhidos.append(valor)
    return True, escolhidos


# ------------------------------------------------------------------------- exibição

def mostrar_registro(registro, posicao, total):
    print("\n" + "=" * 78)
    print(f"[{posicao}/{total}]  id={registro['id']}  status={registro['status']}")
    if registro.get("revisao_pendente"):
        print("  ** re-scrape detectou mudança desde a última curadoria **")
    print("=" * 78)
    for coluna, rotulo, _tipo, _vocab in CAMPOS_EDITAVEIS:
        if coluna not in registro:
            continue
        print(f"  {rotulo:.<28} {formatar_valor(registro[coluna])}")

    extra = registro.get("dados_extra")
    if extra:
        print("\n  dados_extra (referência do scraper):")
        for chave, valor in extra.items():
            texto = formatar_valor(valor)
            if len(texto) > 100:
                texto = texto[:100] + "..."
            print(f"    {chave}: {texto}")


def escolher_campo():
    print("\n  Campos:")
    for i, (_col, rotulo, tipo, _v) in enumerate(CAMPOS_EDITAVEIS, 1):
        print(f"   {i:2}) {rotulo}  [{tipo}]")
    resposta = input("  Número do campo (Enter cancela): ").strip()
    if not resposta.isdigit():
        return None
    indice = int(resposta)
    if not 1 <= indice <= len(CAMPOS_EDITAVEIS):
        print("  Número fora da faixa.")
        return None
    return CAMPOS_EDITAVEIS[indice - 1]


def pedir_valor(campo, atual):
    """Lê o novo valor de um campo. Devolve (alterou, valor)."""
    coluna, rotulo, tipo, vocabulario = campo
    print(f"\n  {rotulo} — atual: {formatar_valor(atual)}")

    if tipo in ("lista", "escolha"):
        for i, opcao in enumerate(vocabulario, 1):
            marca = ""
            if tipo == "lista" and atual and opcao in atual:
                marca = " *"
            elif tipo == "escolha" and atual == opcao:
                marca = " *"
            print(f"     {i:2}) {opcao}{marca}")
        if tipo == "lista":
            entrada = input("  Números separados por vírgula (vazio = limpar, Enter só confirma): ")
            if entrada.strip() == "":
                return False, atual
            ok, valores = parse_selecao(entrada, vocabulario)
            if not ok:
                print("  Seleção inválida, nada alterado.")
                return False, atual
            return True, valores
        entrada = input("  Número (0 = limpar, Enter mantém): ").strip()
        if entrada == "":
            return False, atual
        if entrada == "0":
            return True, None
        if not entrada.isdigit() or not 1 <= int(entrada) <= len(vocabulario):
            print("  Seleção inválida, nada alterado.")
            return False, atual
        return True, vocabulario[int(entrada) - 1]

    if tipo == "lista_livre":
        entrada = input("  Valores separados por vírgula (Enter mantém, '-' limpa): ")
        if entrada.strip() == "":
            return False, atual
        if entrada.strip() == "-":
            return True, []
        return True, parse_lista_livre(entrada)

    if tipo == "data":
        entrada = input("  Data DD/MM/AAAA (Enter mantém, '-' limpa): ").strip()
        if entrada == "":
            return False, atual
        if entrada == "-":
            return True, None
        ok, valor = parse_data(entrada)
        if not ok:
            print("  Data inválida, nada alterado.")
            return False, atual
        return True, valor

    if tipo == "decimal":
        entrada = input("  Valor (Enter mantém, '-' limpa): ").strip()
        if entrada == "":
            return False, atual
        if entrada == "-":
            return True, None
        ok, valor = parse_decimal(entrada)
        if not ok:
            print("  Número inválido, nada alterado.")
            return False, atual
        return True, valor

    entrada = input("  Novo texto (Enter mantém, '-' limpa): ")
    if entrada.strip() == "":
        return False, atual
    if entrada.strip() == "-":
        return True, None
    return True, entrada.strip()


def validar_para_aprovar(registro):
    """Devolve (pode_aprovar, avisos)."""
    faltando = [c for c in OBRIGATORIOS if not registro.get(c)]
    avisos = [c for c in RECOMENDADOS if not registro.get(c)]
    return (not faltando), faltando, avisos


# ----------------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="Curadoria de oportunidades no terminal.")
    parser.add_argument("--status", default="pendente",
                        choices=["pendente", "aprovado", "rejeitado", "rascunho"],
                        help="status a revisar (padrão: pendente)")
    parser.add_argument("--limite", type=int, default=None, help="máximo de registros")
    args = parser.parse_args()

    engine = obter_engine()
    metadata = MetaData()
    try:
        tabela = Table(TABELA, metadata, autoload_with=engine)
    except Exception as erro:  # conexão, tabela ausente, credencial errada
        sys.exit(f"Não consegui ler a tabela '{TABELA}': {erro}")

    tem_revisao_pendente = "revisao_pendente" in tabela.c
    tem_atualizado_em = "atualizado_em" in tabela.c

    with engine.connect() as conn:
        consulta = (
            select(tabela.c.id)
            .where(tabela.c.status == args.status)
            .order_by(tabela.c.id)
        )
        if args.limite:
            consulta = consulta.limit(args.limite)
        ids = [linha[0] for linha in conn.execute(consulta)]

    if not ids:
        print(f"Nenhum registro com status '{args.status}'. Nada a fazer.")
        return

    print(f"{len(ids)} registro(s) com status '{args.status}'.")
    print("Ações: [a]provar  [e]ditar  [r]ejeitar  [p]ular  [q]sair")

    contagem = {"aprovados": 0, "rejeitados": 0, "pulados": 0, "editados": 0}
    editados_ids = set()
    posicao = 0

    for id_registro in ids:
        posicao += 1
        while True:
            # Relê a cada volta: reflete edições já feitas e evita trabalhar com
            # dado velho se algo mudou por fora durante a sessão.
            with engine.connect() as conn:
                linha = conn.execute(
                    select(tabela).where(tabela.c.id == id_registro)
                ).mappings().first()
            if linha is None:
                print(f"  Registro {id_registro} sumiu do banco; pulando.")
                contagem["pulados"] += 1
                break
            registro = dict(linha)

            mostrar_registro(registro, posicao, len(ids))
            escolha = input("\n  Ação [a/e/r/p/q]: ").strip().lower()

            if escolha == "q":
                print("\nSaindo a pedido.")
                resumo(contagem, len(editados_ids))
                return

            if escolha == "p":
                contagem["pulados"] += 1
                break

            if escolha == "e":
                campo = escolher_campo()
                if not campo:
                    continue
                coluna = campo[0]
                alterou, valor = pedir_valor(campo, registro.get(coluna))
                if not alterou:
                    continue
                valores = {coluna: valor}
                if tem_atualizado_em:
                    # `onupdate` do model é comportamento do ORM; via Core precisa ser explícito.
                    valores["atualizado_em"] = datetime.utcnow()
                with engine.begin() as conn:
                    conn.execute(
                        update(tabela).where(tabela.c.id == id_registro).values(**valores)
                    )
                editados_ids.add(id_registro)
                print(f"  ✓ {campo[1]} atualizado.")
                continue

            if escolha in ("a", "r"):
                novo_status = "aprovado" if escolha == "a" else "rejeitado"
                if escolha == "a":
                    pode, faltando, avisos = validar_para_aprovar(registro)
                    if not pode:
                        nomes = ", ".join(faltando)
                        print(f"  Não dá para aprovar: {nomes} está vazio (edite antes).")
                        continue
                    if avisos:
                        nomes = ", ".join(avisos)
                        confirma = input(
                            f"  {nomes} vazio(s). Aprovar mesmo assim? [s/N]: "
                        ).strip().lower()
                        if confirma != "s":
                            continue

                valores = {"status": novo_status}
                if tem_atualizado_em:
                    valores["atualizado_em"] = datetime.utcnow()
                if tem_revisao_pendente:
                    # O curador acabou de olhar o registro, então a sinalização de
                    # "mudou desde a última curadoria" deixa de fazer sentido.
                    valores["revisao_pendente"] = False
                with engine.begin() as conn:
                    conn.execute(
                        update(tabela).where(tabela.c.id == id_registro).values(**valores)
                    )
                contagem["aprovados" if escolha == "a" else "rejeitados"] += 1
                print(f"  ✓ {novo_status}.")
                break

            print("  Ação não reconhecida.")

    resumo(contagem, len(editados_ids))


def resumo(contagem, total_editados):
    print("\n" + "=" * 78)
    print("Resumo da sessão")
    print(f"  aprovados : {contagem['aprovados']}")
    print(f"  rejeitados: {contagem['rejeitados']}")
    print(f"  pulados   : {contagem['pulados']}")
    print(f"  registros com algum campo editado: {total_editados}")
    print("=" * 78)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrompido. As decisões já confirmadas foram gravadas.")
