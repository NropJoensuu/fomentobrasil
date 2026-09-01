"""Mede a qualidade das sugestões da IA contra os registros curados à mão.

    ./.venv/bin/python scripts/calibrar_ia.py [--limite N]

Roda `app.curadoria_ia.sugerir_campos` nos registros com status "aprovado" — os que já
passaram pela curadoria humana — e compara campo a campo. **Não grava nada no banco**: é só
medição.

O que interessa é a taxa POR CAMPO, não a geral. Uma média alta pode esconder que datas
erram sistematicamente enquanto campos categóricos acertam sempre — e é a taxa por campo que
diz onde a sugestão pode ser confiada e onde ela precisa ser conferida linha a linha.

Quatro classificações por campo:
  acerto      a IA sugeriu exatamente o que o curador pôs
  divergencia a IA sugeriu algo diferente
  omissao     o curador preencheu, a IA deixou null
  excesso     a IA preencheu, o curador deixou vazio

"Excesso" NÃO é necessariamente erro: o curador pode ter deixado o campo em branco por não
ter achado a informação, e a IA pode tê-la encontrado. Por isso o relatório guarda o valor e
a evidência de cada excesso — vale conferir os primeiros à mão antes de tirar conclusão.

O relatório completo vai para calibracao_ia.json (fora do versionamento).
"""

import argparse
import json
import pathlib
import sys
import time
import warnings
from datetime import date
from decimal import Decimal

warnings.filterwarnings("ignore")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from app.curadoria_ia import CAMPOS_ESPERADOS, MODELO, sugerir_campos  # noqa: E402
from app.models import Oportunidade  # noqa: E402

SAIDA = pathlib.Path(__file__).resolve().parent.parent / "calibracao_ia.json"

# Preço do claude-sonnet-5 por milhão de tokens, para estimar custo por edital.
PRECO_ENTRADA = 3.00
PRECO_SAIDA = 15.00

CAMPOS_LISTA = {
    "linha_de_fomento", "natureza_recurso", "proponente_elegivel", "nivel_formacao",
    "uf", "instituicao_financiadora", "palavras_chave",
}


def _normalizar(campo, valor):
    """Põe os dois lados na mesma forma antes de comparar."""
    if valor is None or valor == [] or valor == "":
        return None
    if campo in CAMPOS_LISTA:
        return frozenset(str(v).strip() for v in valor)
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, (Decimal, float, int)):
        return float(valor)
    return str(valor).strip()


def _classificar(curado, sugerido):
    if curado is None and sugerido is None:
        return None  # nada a medir
    if curado is None:
        return "excesso"
    if sugerido is None:
        return "omissao"
    return "acerto" if curado == sugerido else "divergencia"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=None, help="roda só os N primeiros")
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        registros = (
            Oportunidade.query.filter_by(status="aprovado")
            .order_by(Oportunidade.id).all()
        )
        if args.limite:
            registros = registros[: args.limite]

        # `palavras_chave` é texto livre: comparar conjunto de strings livres marcaria
        # divergência sempre e não diria nada. Fica no relatório, fora da pontuação.
        campos = [c for c in CAMPOS_ESPERADOS if c != "palavras_chave"]
        placar = {c: {"acerto": 0, "divergencia": 0, "omissao": 0, "excesso": 0} for c in campos}
        detalhes, falhas = [], []
        tokens_entrada = tokens_saida = 0

        for i, o in enumerate(registros, 1):
            print(f"[{i}/{len(registros)}] #{o.id} {o.titulo[:58]}", flush=True)
            inicio = time.time()
            try:
                sugestao = sugerir_campos(o)
            except Exception as e:
                print(f"    FALHOU: {type(e).__name__}: {e}", flush=True)
                falhas.append({"id": o.id, "titulo": o.titulo, "erro": f"{type(e).__name__}: {e}"})
                continue

            meta = sugestao.get("_meta", {})
            tokens_entrada += meta.get("tokens_entrada", 0)
            tokens_saida += meta.get("tokens_saida", 0)

            registro = {
                "id": o.id, "titulo": o.titulo, "link": o.link,
                "e_fomento": sugestao.get("e_fomento"),
                "observacao": sugestao.get("observacao"),
                "segundos": round(time.time() - inicio, 1),
                "_meta": meta, "campos": {},
            }
            for campo in campos:
                dado = sugestao["campos"].get(campo) or {}
                curado = _normalizar(campo, getattr(o, campo, None))
                sugerido = _normalizar(campo, dado.get("valor"))
                classe = _classificar(curado, sugerido)
                if classe is None:
                    continue
                placar[campo][classe] += 1
                registro["campos"][campo] = {
                    "classe": classe,
                    "curado": getattr(o, campo, None),
                    "sugerido": dado.get("valor"),
                    "evidencia": dado.get("evidencia"),
                }
            detalhes.append(registro)
            print(f"    {registro['segundos']}s | "
                  + " ".join(f"{k}={sum(1 for c in registro['campos'].values() if c['classe'] == k)}"
                             for k in ("acerto", "divergencia", "omissao", "excesso")), flush=True)

    n = len(detalhes)
    custo = (tokens_entrada / 1e6) * PRECO_ENTRADA + (tokens_saida / 1e6) * PRECO_SAIDA
    resumo = {
        "modelo": MODELO,
        "registros_medidos": n,
        "falhas": falhas,
        "tokens_entrada": tokens_entrada,
        "tokens_saida": tokens_saida,
        "custo_estimado_usd": round(custo, 4),
        "custo_por_edital_usd": round(custo / n, 4) if n else None,
        "placar_por_campo": placar,
    }
    SAIDA.write_text(json.dumps({"resumo": resumo, "detalhes": detalhes},
                                ensure_ascii=False, indent=1, default=str))

    print(f"\n{'=' * 74}")
    print(f"{'campo':<26}{'acerto':>8}{'diverg':>8}{'omiss':>8}{'excesso':>9}{'taxa':>9}")
    print("-" * 74)
    for campo in campos:
        p = placar[campo]
        avaliados = p["acerto"] + p["divergencia"] + p["omissao"]
        taxa = f"{100 * p['acerto'] / avaliados:.0f}%" if avaliados else "—"
        print(f"{campo:<26}{p['acerto']:>8}{p['divergencia']:>8}{p['omissao']:>8}"
              f"{p['excesso']:>9}{taxa:>9}")
    print("-" * 74)
    print(f"{n} editais medidos | {len(falhas)} falha(s) | "
          f"{tokens_entrada} tokens de entrada, {tokens_saida} de saída")
    if n:
        print(f"custo estimado US$ {custo:.4f} no total, "
              f"US$ {custo / n:.4f} por edital")
    print(f"relatório completo em {SAIDA.name}")


if __name__ == "__main__":
    main()
