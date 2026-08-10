"""Backfill retroativo de uf/abrangencia para registros coletados antes da correção do bug
nos scrapers (que não preenchiam esses campos).

Classifica pelo DOMÍNIO do link, não por `instituicao_financiadora`: chamadas do CNPq e da
FAPESP frequentemente têm financiadora composta (ex.: "CNPq/MCTI", "FAPESP e JSPS", "Finep/MCTI"),
então um match exato contra "CNPq"/"FAPESP" deixaria a maior parte dos registros de fora — na
prática, checado contra o banco real, cobriria só ~42 dos 81 registros (e nenhum do CNPq, cujo
`instituicao_financiadora` nunca é literalmente "CNPq"). O domínio do link é o identificador
confiável de qual scraper originou o registro.

Rodar uma vez (da raiz do projeto): `python -m scripts.backfill_uf`
(`python scripts/backfill_uf.py` direto falha com "No module named 'app'" — o script
fica fora de `sys.path` nesse modo; `-m` resolve os imports do pacote `app`).
"""
from urllib.parse import urlparse

from app import create_app, db
from app.models import Oportunidade

MAPEAMENTO_POR_DOMINIO = {
    "fapesp.br": {"uf": "SP", "abrangencia": "estadual"},
    "fapemig.br": {"uf": "MG", "abrangencia": "estadual"},
    "fapes.es.gov.br": {"uf": "ES", "abrangencia": "estadual"},
    "www.gov.br": {"uf": None, "abrangencia": "nacional"},  # CNPq
}

app = create_app()
with app.app_context():
    # uf.is_(None) E abrangencia.is_(None): evita sobrescrever qualquer registro que um
    # curador já tenha preenchido manualmente durante a moderação.
    candidatos = Oportunidade.query.filter(
        Oportunidade.uf.is_(None),
        Oportunidade.abrangencia.is_(None),
    ).all()

    total_atualizados = 0
    sem_mapeamento = []
    for r in candidatos:
        valores = MAPEAMENTO_POR_DOMINIO.get(urlparse(r.link).netloc)
        if valores is None:
            sem_mapeamento.append(r)
            continue
        r.uf = valores["uf"]
        r.abrangencia = valores["abrangencia"]
        total_atualizados += 1

    db.session.commit()
    print(f"Atualizados: {total_atualizados}")
    if sem_mapeamento:
        print(f"Sem mapeamento de domínio, não alterados ({len(sem_mapeamento)}):")
        for r in sem_mapeamento:
            print(f"  id={r.id} link={r.link}")
