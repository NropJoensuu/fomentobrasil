"""Executa todos os scrapers em sequência e devolve/registra um resumo consolidado.

Usado pelo botão manual em /admin/scrapers e pelo agendador diário (ver app/__init__.py).
Cada fonte roda isolada num try/except — uma falha (site fora do ar, mudança de HTML)
não derruba a execução das demais.
"""

from datetime import datetime

from scrapers import (araucaria, cnpq, facepe, fapeal, fapeg, fapema, fapemig, fapepi, fapergs,
                      fapero, fapes, fapesb, fapesc, fapesp, fapespa, fapesq, fapitec, fundect)

FONTES = [
    ("CNPq", cnpq.coletar_chamadas_cnpq, cnpq.salvar_no_banco),
    ("FAPESP", fapesp.coletar_chamadas_fapesp, fapesp.salvar_no_banco),
    ("FAPEMIG", fapemig.coletar_chamadas_fapemig, fapemig.salvar_no_banco),
    ("FAPES", fapes.coletar_todos_editais_fapes, fapes.salvar_no_banco),
    ("FAPESC", fapesc.coletar_chamadas_fapesc, fapesc.salvar_no_banco),
    ("Fundação Araucária", araucaria.coletar_chamadas_araucaria, araucaria.salvar_no_banco),
    ("FAPERGS", fapergs.coletar_chamadas_fapergs, fapergs.salvar_no_banco),
    ("FACEPE", facepe.coletar_editais_facepe, facepe.salvar_no_banco),
    ("FAPEG", fapeg.coletar_chamadas_fapeg, fapeg.salvar_no_banco),
    ("FUNDECT", fundect.coletar_chamadas_fundect, fundect.salvar_no_banco),
    ("FAPEAL", fapeal.coletar_chamadas_fapeal, fapeal.salvar_no_banco),
    ("FAPESB", fapesb.coletar_chamadas_fapesb, fapesb.salvar_no_banco),
    ("FAPEMA", fapema.coletar_chamadas_fapema, fapema.salvar_no_banco),
    ("FAPESQ", fapesq.coletar_chamadas_fapesq, fapesq.salvar_no_banco),
    ("FAPITEC", fapitec.coletar_chamadas_fapitec, fapitec.salvar_no_banco),
    ("FAPEPI", fapepi.coletar_chamadas_fapepi, fapepi.salvar_no_banco),
    ("FAPESPA", fapespa.coletar_chamadas_fapespa, fapespa.salvar_no_banco),
    ("FAPERO", fapero.coletar_chamadas_fapero, fapero.salvar_no_banco),
]


def rodar_todos():
    """Roda coletar+salvar de cada fonte e devolve uma lista de resumos por fonte.

    Cada item: {"fonte", "novos", "atualizados", "ja_existentes", "erro"}.
    `erro` é `None` em caso de sucesso, ou a mensagem da exceção em caso de falha —
    nesse caso os contadores ficam em 0 (nada foi coletado dessa fonte na execução).
    """
    resumo = []

    for nome, coletar, salvar in FONTES:
        try:
            registros = coletar()
            # coletar_chamadas_fapemig devolve (registros, total_informado); as demais
            # devolvem só a lista.
            if isinstance(registros, tuple):
                registros = registros[0]

            resultado = salvar(registros)
            resumo.append(
                {
                    "fonte": nome,
                    "novos": resultado["novos"],
                    "atualizados": resultado["atualizados"],
                    "ja_existentes": resultado["ja_existentes"],
                    "erro": None,
                }
            )
        except Exception as e:
            resumo.append(
                {
                    "fonte": nome,
                    "novos": 0,
                    "atualizados": 0,
                    "ja_existentes": 0,
                    "erro": str(e),
                }
            )

    return resumo


def rodar_e_registrar(disparado_por="manual"):
    """Roda todas as fontes e grava o resultado em ExecucaoScraper (histórico)."""
    from app import db
    from app.models import ExecucaoScraper

    resumo = rodar_todos()
    execucao = ExecucaoScraper(
        executado_em=datetime.utcnow(),
        disparado_por=disparado_por,
        resumo_json=resumo,
        sucesso=all(r["erro"] is None for r in resumo),
    )
    db.session.add(execucao)
    db.session.commit()
    return resumo


if __name__ == "__main__":
    from app import create_app

    app = create_app()
    with app.app_context():
        resumo = rodar_e_registrar(disparado_por="manual")
        for r in resumo:
            if r["erro"]:
                print(f"{r['fonte']}: ERRO — {r['erro']}")
            else:
                print(
                    f"{r['fonte']}: novos={r['novos']} "
                    f"atualizados={r['atualizados']} "
                    f"ja_existentes={r['ja_existentes']}"
                )
