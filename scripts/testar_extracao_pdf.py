"""Regressão de `app.extracao_pdf` contra os editais curados à mão.

Rodar sempre que mexer nas regras de extração:

    ./.venv/bin/python scripts/testar_extracao_pdf.py

Não precisa de banco nem de rede: o texto dos PDFs está congelado em
tests/fixtures/editais/. Congelado de propósito — o edital do CNPq 19/2026 já mudou de
conteúdo por retificação depois de ter sido baixado, e um teste que baixa de novo mede
outra coisa a cada dia.

Sai com código 1 se a pontuação cair abaixo da linha de base, que é o que impede uma regra
nova de consertar um edital quebrando três.
"""

import gzip
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.extracao_pdf import extrair_candidatos  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures"
SEPARADOR = "\n\n===PAGINA===\n\n"

# Linha de base medida em 2026-08-30. Subiu? Atualize junto com a mudança que a fez subir.
MINIMO_ENCONTRADOS = 29
MINIMO_NA_PRIMEIRA = 27


def _normalizar(campo, valor):
    """Datas comparam como string ISO; valores como float, para "50000" == "50000.00"."""
    return valor if campo.startswith("data") else float(valor)


def main():
    referencia = json.loads((FIXTURES / "curadoria_de_referencia.json").read_text())
    fora_da_conta = referencia["_fora_da_conta"]

    encontrados = na_primeira = total = 0
    observacoes = []

    for oid, registro in sorted(referencia["registros"].items(), key=lambda kv: int(kv[0])):
        with gzip.open(FIXTURES / "editais" / f"{oid}.txt.gz", "rt") as f:
            paginas = f.read().split(SEPARADOR)
        candidatos = extrair_candidatos(paginas)

        for campo, esperado in registro["esperado"].items():
            excecao = fora_da_conta.get(f"{oid}:*") or fora_da_conta.get(f"{oid}:{campo}")
            sugeridos = [c.como_dict()["valor"] for c in candidatos.get(campo, [])]

            if excecao:
                observacoes.append(f"  fora da conta  #{oid} {campo}: {excecao}")
                continue

            total += 1
            alvo = _normalizar(campo, esperado)
            lista = [_normalizar(campo, v) for v in sugeridos]

            if lista and lista[0] == alvo:
                encontrados += 1
                na_primeira += 1
            elif alvo in lista:
                encontrados += 1
                observacoes.append(
                    f"  2a opcao      #{oid} {campo}: curado={esperado} "
                    f"em {lista.index(alvo) + 1}o lugar de {sugeridos[:3]}"
                )
            else:
                observacoes.append(
                    f"  NAO ACHOU     #{oid} {campo}: curado={esperado} "
                    f"sugerido={sugeridos[:3] or 'nada'}"
                )

    print("\n".join(observacoes))
    print(f"\nEncontrados {encontrados}/{total} (minimo {MINIMO_ENCONTRADOS}) | "
          f"na 1a sugestao {na_primeira}/{total} (minimo {MINIMO_NA_PRIMEIRA})")

    if encontrados < MINIMO_ENCONTRADOS or na_primeira < MINIMO_NA_PRIMEIRA:
        print("\nREGRESSAO: a pontuacao caiu abaixo da linha de base.")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
