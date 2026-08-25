"""OBSOLETO (2026-08-25) — já cumpriu sua função, mantido só como registro histórico.

Backfill retroativo de uf/abrangencia para os registros coletados antes da correção do bug
nos scrapers (que não preenchiam esses campos). Rodou uma vez contra os 81 registros que
existiam então; hoje os 7 scrapers já gravam `uf`/`abrangencia` desde a coleta, então o
filtro `uf IS NULL AND abrangencia IS NULL` abaixo normalmente não encontra mais nenhum
candidato. Ajustado para gravar `uf` como lista (`[valor]`/`None`), acompanhando a conversão
de `uf` para ARRAY — mas não há necessidade real de rodar de novo.

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
        r.uf = [valores["uf"]] if valores["uf"] else None
        r.abrangencia = valores["abrangencia"]
        total_atualizados += 1

    db.session.commit()
    print(f"Atualizados: {total_atualizados}")
    if sem_mapeamento:
        print(f"Sem mapeamento de domínio, não alterados ({len(sem_mapeamento)}):")
        for r in sem_mapeamento:
            print(f"  id={r.id} link={r.link}")
