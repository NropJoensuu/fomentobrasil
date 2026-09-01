import io
from datetime import datetime

import pdfplumber
import requests
from bs4 import BeautifulSoup
from flask import Blueprint, jsonify, render_template, request, redirect, url_for
from sqlalchemy import or_, func

from app import db
from app.extracao_pdf import extrair_candidatos, extrair_paginas
from app.models import ExecucaoScraper, Oportunidade
from app.utils import (
    aplicar_faixas,
    avisos_de_aprovacao,
    get_regioes,
    get_ufs_por_regiao,
    parse_faixas,
    parse_valor_brl,
    REGIAO_POR_UF,
    REGIOES,
    url_real_do_pdf,
)

main = Blueprint("main", __name__)


@main.route("/")
def index():
    return render_template("index.html")


@main.route("/oportunidades")
def listar_oportunidades():
    # Filtro de moderação: a listagem pública mostra tudo, exceto rejeitado (descartado
    # por um curador e que não deve reaparecer). Pendentes aparecem com selo "Não
    # verificado" no template. `?status=` ainda permite isolar um status específico
    # (ex: revisar só os rejeitados) e é aplicado na base da query, antes dos filtros
    # opcionais do usuário.
    # PENDÊNCIA DE SEGURANÇA: sem controle de acesso — qualquer visitante pode usar
    # `?status=`. Aceitável por ora (não há dado sensível), mas precisa virar
    # rota restrita quando o sistema de usuários/papéis existir.
    status_filtro = request.args.get("status")
    if status_filtro in ("pendente", "aprovado", "rejeitado", "rascunho"):
        query = Oportunidade.query.filter(Oportunidade.status == status_filtro)
    else:
        status_filtro = ""
        query = Oportunidade.query.filter(Oportunidade.status != "rejeitado")

    busca = request.args.get("busca") or ""
    regiao = request.args.get("regiao") or ""
    uf_filtro = request.args.get("uf") or ""
    linha_de_fomento = request.args.get("linha_de_fomento") or ""
    area_principal = request.args.get("area_principal") or ""
    proponente_elegivel = request.args.get("proponente_elegivel") or ""
    apenas_abertas = request.args.get("apenas_abertas") == "1"

    if busca:
        termo = f"%{busca}%"
        query = query.filter(
            or_(
                Oportunidade.titulo.ilike(termo),
                Oportunidade.descricao.ilike(termo),
                func.array_to_string(Oportunidade.palavras_chave, ",").ilike(termo),
            )
        )
    if regiao:
        ufs = get_ufs_por_regiao(regiao)
        # overlap (&&) testa interseção entre dois arrays — uf agora é lista, então
        # .in_() (que compara um escalar contra vários valores) não serve mais aqui.
        query = query.filter(Oportunidade.uf.overlap(ufs))
    if uf_filtro:
        query = query.filter(Oportunidade.uf.any(uf_filtro))
    if linha_de_fomento:
        query = query.filter(Oportunidade.linha_de_fomento.any(linha_de_fomento))
    if area_principal:
        query = query.filter(Oportunidade.area_principal == area_principal)
    if proponente_elegivel:
        query = query.filter(Oportunidade.proponente_elegivel.any(proponente_elegivel))
    if apenas_abertas:
        hoje = datetime.utcnow().date()
        query = query.filter(
            or_(Oportunidade.data_prazo.is_(None), Oportunidade.data_prazo >= hoje)
        )

    oportunidades = query.order_by(Oportunidade.criado_em.desc()).all()

    filtros = {
        "status": status_filtro,
        "busca": busca,
        "regiao": regiao,
        "uf": uf_filtro,
        "linha_de_fomento": linha_de_fomento,
        "area_principal": area_principal,
        "proponente_elegivel": proponente_elegivel,
        "apenas_abertas": apenas_abertas,
    }

    return render_template(
        "oportunidades/listar.html",
        oportunidades=oportunidades,
        filtros=filtros,
        regioes=REGIOES,
        ufs_disponiveis=sorted(REGIAO_POR_UF.keys()),
    )


@main.route("/oportunidades/<int:id>")
def detalhe_oportunidade(id):
    oportunidade = Oportunidade.query.get_or_404(id)
    hoje = datetime.utcnow().date()
    aberta = oportunidade.data_prazo is None or oportunidade.data_prazo >= hoje
    regioes = get_regioes(oportunidade.uf)
    return render_template("oportunidades/detalhe.html", o=oportunidade, aberta=aberta, regioes=regioes)


@main.route("/oportunidades/nova", methods=["GET", "POST"])
def nova_oportunidade():
    if request.method == "POST":
        # Reforço de dedup: cobre o caso de alguém editar o link manualmente durante a
        # revisão (ex: vindo de /oportunidades/importar) para um que já existe.
        if Oportunidade.query.filter_by(link=request.form["link"]).first():
            return render_template(
                "oportunidades/nova.html",
                erro="Este link já está cadastrado.",
                prefill=request.form,
            )

        linha_de_fomento = request.form.getlist("linha_de_fomento")
        if not linha_de_fomento:
            # linha_de_fomento é NOT NULL; checkbox não garante "pelo menos um" no HTML,
            # então valida aqui para não estourar IntegrityError na cara do curador.
            return render_template(
                "oportunidades/nova.html",
                erro="Selecione ao menos uma Linha de Fomento.",
                prefill=request.form,
            )

        instituicao_financiadora = request.form.getlist("instituicao_financiadora")
        if not instituicao_financiadora:
            # Também NOT NULL, mesmo raciocínio: o campo de tags não garante "pelo
            # menos um" no HTML.
            return render_template(
                "oportunidades/nova.html",
                erro="Informe ao menos uma Instituição Financiadora.",
                prefill=request.form,
            )

        palavras_chave = request.form.getlist("palavras_chave")

        def parse_decimal(campo):
            return parse_valor_brl(request.form.get(campo))

        data_prazo_raw = request.form.get("data_prazo") or ""
        data_prazo = (
            datetime.strptime(data_prazo_raw, "%Y-%m-%d").date()
            if data_prazo_raw
            else None
        )

        data_resultado_previsto_raw = request.form.get("data_resultado_previsto") or ""
        data_resultado_previsto = (
            datetime.strptime(data_resultado_previsto_raw, "%Y-%m-%d").date()
            if data_resultado_previsto_raw
            else None
        )

        oportunidade = Oportunidade(
            titulo=request.form["titulo"],
            instituicao_financiadora=instituicao_financiadora,
            instituicao_promotora=request.form.get("instituicao_promotora") or None,
            linha_de_fomento=linha_de_fomento,
            tipo_instrumento=request.form["tipo_instrumento"],
            natureza_recurso=request.form.getlist("natureza_recurso"),
            proponente_elegivel=request.form.getlist("proponente_elegivel"),
            tipo_parceria=request.form.get("tipo_parceria") or None,
            modalidade_pessoa=request.form.get("modalidade_pessoa") or None,
            status_oficial=request.form.get("status_oficial") or None,
            orcamento_total_chamada=parse_decimal("orcamento_total_chamada"),
            valor_minimo_proposta=parse_decimal("valor_minimo_proposta"),
            valor_maximo_proposta=parse_decimal("valor_maximo_proposta"),
            data_prazo=data_prazo,
            data_resultado_previsto=data_resultado_previsto,
            link=request.form["link"],
            descricao=request.form.get("descricao") or None,
            area_principal=request.form.get("area_principal") or None,
            palavras_chave=palavras_chave or None,
            nivel_formacao=request.form.getlist("nivel_formacao") or None,
            abrangencia=request.form.get("abrangencia") or None,
            uf=request.form.getlist("uf") or None,
            data_publicacao=(
                datetime.strptime(request.form["data_publicacao"], "%Y-%m-%d").date()
                if request.form.get("data_publicacao") else None
            ),
            dados_extra=aplicar_faixas(None, parse_faixas(request.form)),
        )
        db.session.add(oportunidade)
        db.session.commit()
        return redirect(url_for("main.listar_oportunidades"))

    return render_template("oportunidades/nova.html")


# PENDÊNCIA DE SEGURANÇA: esta rota faz requests.get() para uma URL fornecida pelo
# visitante (SSRF) — sem controle de acesso, qualquer pessoa pode usar o servidor para
# sondar endereços internos/metadados de nuvem. Risco parcialmente mitigado por só
# devolver título e um trecho de texto (sem repassar a resposta bruta), mas não há
# validação de host/IP. Aceitável por ora (assistente interno, mesma pendência de
# controle de acesso já documentada para /moderacao), mas revisitar junto com o
# sistema de usuários/papéis — e considerar bloquear IPs privados/loopback antes disso.
@main.route("/oportunidades/importar", methods=["GET", "POST"])
def importar_oportunidade():
    if request.method == "POST":
        link = request.form.get("link", "").strip()
        if not link:
            return render_template("oportunidades/importar.html", erro="Informe um link.")

        existe = Oportunidade.query.filter_by(link=link).first()
        if existe:
            return render_template(
                "oportunidades/importar.html",
                erro="Este link já está cadastrado.",
                oportunidade_existente=existe,
            )

        try:
            extraido = extrair_dados_de_link(link)
        except Exception as e:
            return render_template(
                "oportunidades/importar.html",
                erro=f"Não foi possível extrair dados automaticamente deste link ({e}). "
                     f"Você pode cadastrar manualmente pela tela de Cadastrar.",
            )

        # Não salva ainda — mostra formulário de revisão pré-preenchido
        return render_template("oportunidades/nova.html", prefill=extraido, link_importado=link)

    return render_template("oportunidades/importar.html")


def extrair_dados_de_link(link):
    """Extração genérica best-effort: HTML (título/meta description) ou PDF (texto bruto).

    Bem mais fraca que os scrapers dedicados (CNPq, FAPESP, FAPEMIG, FAPES) — não
    classifica nada, só aproxima título/descrição para acelerar o preenchimento manual.
    """
    resp = requests.get(link, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "")

    if "pdf" in content_type.lower() or link.lower().endswith(".pdf"):
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            texto_completo = "\n".join(
                (pagina.extract_text() or "") for pagina in pdf.pages[:5]  # só as 5 primeiras páginas
            )
        linhas = [l.strip() for l in texto_completo.split("\n") if l.strip()]
        titulo = linhas[0] if linhas else "Título não identificado"
        descricao = " ".join(linhas[1:30])  # próximas ~30 linhas como aproximação de descrição
        return {"titulo": titulo[:300], "descricao": descricao[:2000], "link": link}

    soup = BeautifulSoup(resp.text, "html.parser")
    titulo_tag = soup.find("title")
    titulo = titulo_tag.get_text(strip=True) if titulo_tag else "Título não identificado"

    meta_desc = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    descricao = meta_desc.get("content", "").strip() if meta_desc else None

    return {"titulo": titulo[:300], "descricao": descricao, "link": link}


# PENDÊNCIA DE SEGURANÇA: rotas de moderação sem controle de acesso — qualquer
# pessoa com a URL pode editar/aprovar/rejeitar. Aceitável por ora (não há dado
# sensível), mas precisa exigir login de colaborador/admin quando esse sistema existir.
STATUS_MODERACAO = ("pendente", "aprovado", "rejeitado", "rascunho")


@main.route("/moderacao")
def listar_pendentes():
    # `status` na URL para poder voltar a um item JÁ CURADO: sem isso, um registro
    # aprovado só era alcançável digitando /moderacao/<id> na mão. Retificações do edital
    # e curadoria incompleta são os dois motivos concretos de revisitar.
    status = request.args.get("status")
    if status not in STATUS_MODERACAO:
        status = "pendente"

    busca = (request.args.get("busca") or "").strip()

    query = Oportunidade.query.filter_by(status=status)
    if busca:
        termo = f"%{busca}%"
        query = query.filter(
            or_(
                Oportunidade.titulo.ilike(termo),
                func.array_to_string(Oportunidade.instituicao_financiadora, ",").ilike(termo),
            )
        )

    # Aprovados/rejeitados ordenam pela última edição (o que você mexeu por último é o que
    # provavelmente quer rever); pendentes seguem pela entrada, como antes.
    ordem = (
        Oportunidade.criado_em.desc()
        if status == "pendente"
        else Oportunidade.atualizado_em.desc()
    )
    itens = query.order_by(ordem).all()

    contagens = dict(
        db.session.query(Oportunidade.status, func.count(Oportunidade.id))
        .group_by(Oportunidade.status)
        .all()
    )
    total_atualizacoes = Oportunidade.query.filter_by(revisao_pendente=True).count()
    return render_template(
        "moderacao/listar.html",
        itens=itens,
        status=status,
        busca=busca,
        contagens=contagens,
        total_atualizacoes=total_atualizacoes,
    )


@main.route("/moderacao/atualizacoes")
def listar_atualizacoes():
    itens = Oportunidade.query.filter_by(revisao_pendente=True).order_by(
        Oportunidade.atualizado_em.desc()
    ).all()
    return render_template("moderacao/atualizacoes.html", itens=itens)


@main.route("/moderacao/atualizacoes/<int:id>/revisar", methods=["POST"])
def marcar_atualizacao_revisada(id):
    # Só zera revisao_pendente — o item continua com o `status` que já tinha (aprovado
    # na prática, na maioria dos casos), sem regredir para pendente e sumir da listagem
    # pública por causa de uma correção de data/valor detectada automaticamente.
    oportunidade = Oportunidade.query.get_or_404(id)
    oportunidade.revisao_pendente = False
    db.session.commit()
    return redirect(url_for("main.listar_atualizacoes"))


# PENDÊNCIA DE SEGURANÇA: faz requests.get() para uma URL do formulário (SSRF), mesma
# dívida já documentada em /oportunidades/importar — sem controle de acesso e sem bloqueio
# de IP privado/loopback. Revisitar junto com o sistema de usuários/papéis.
@main.route("/moderacao/<int:id>/extrair", methods=["POST"])
def extrair_do_pdf(id):
    """Lê o PDF do edital e devolve candidatos para os campos de data e valor.

    Sugere, não preenche: a resposta traz o trecho e a página de cada candidato para o
    curador conferir. Ver a docstring de `app.extracao_pdf` para o que é e o que não é
    extraível — e por quê.
    """
    oportunidade = Oportunidade.query.get_or_404(id)
    url = url_real_do_pdf((request.form.get("url_pdf") or oportunidade.link or "").strip())
    if not url:
        return jsonify({"ok": False, "erro": "Sem URL para ler."}), 400

    try:
        resposta = requests.get(
            url, timeout=90, verify=False,
            headers={"User-Agent": "fomentobrasil-curadoria/1.0"},
        )
        resposta.raise_for_status()
    except requests.RequestException as e:
        return jsonify({"ok": False, "erro": f"Não consegui baixar: {type(e).__name__}"}), 502

    if resposta.content[:4] != b"%PDF":
        return jsonify({
            "ok": False,
            "erro": "A URL não devolveu um PDF. Se o link da oportunidade aponta para uma "
                    "página de listagem, cole aqui a URL do PDF do edital.",
        }), 415

    try:
        paginas = extrair_paginas(resposta.content)
    except Exception as e:
        return jsonify({"ok": False, "erro": f"Não consegui ler o PDF: {type(e).__name__}"}), 422

    if not any(p.strip() for p in paginas):
        return jsonify({
            "ok": False,
            "erro": "O PDF não tem camada de texto (provavelmente é digitalizado). "
                    "Preenchimento manual.",
        }), 422

    candidatos = extrair_candidatos(paginas)
    return jsonify({
        "ok": True,
        "paginas": len(paginas),
        "candidatos": {
            campo: [c.como_dict() for c in lista] for campo, lista in candidatos.items()
        },
    })


# PENDÊNCIA DE SEGURANÇA: mesma dívida das demais rotas de moderação — sem controle de
# acesso, qualquer pessoa com a URL dispara uma chamada paga à API. Precisa exigir login de
# curador antes de ir a público.
@main.route("/moderacao/<int:id>/sugerir-ia", methods=["POST"])
def sugerir_com_ia(id):
    """Pede sugestões ao modelo e guarda em dados_extra["sugestao_ia"].

    NÃO altera `status` nem grava em campo estruturado — a sugestão só aparece na tela para
    o curador aplicar campo a campo. Ver a docstring de `app.curadoria_ia`.
    """
    from app.curadoria_ia import sugerir_campos

    oportunidade = Oportunidade.query.get_or_404(id)

    # dict(...) + reatribuição: o SQLAlchemy não detecta mutação dentro de um dict de coluna
    # JSONB e descartaria a alteração no commit. Já mordeu este projeto antes.
    dados = dict(oportunidade.dados_extra or {})
    try:
        dados["sugestao_ia"] = sugerir_campos(oportunidade)
        dados.pop("sugestao_ia_erro", None)
    except Exception as e:
        dados["sugestao_ia_erro"] = f"{type(e).__name__}: {e}"
    oportunidade.dados_extra = dados
    db.session.commit()
    return redirect(url_for("main.moderar_oportunidade", id=id))


@main.route("/moderacao/<int:id>", methods=["GET", "POST"])
def moderar_oportunidade(id):
    oportunidade = Oportunidade.query.get_or_404(id)

    if request.method == "POST":
        acao = request.form.get("acao")  # "salvar", "aprovar" ou "rejeitar"

        linha_de_fomento = request.form.getlist("linha_de_fomento")
        if not linha_de_fomento:
            # linha_de_fomento é NOT NULL; checkbox não garante "pelo menos um" no HTML,
            # então valida ANTES de tocar em `oportunidade` — evita estourar
            # IntegrityError e evita deixar mudanças parciais penduradas na sessão.
            return render_template(
                "moderacao/editar.html", o=oportunidade, erro="Selecione ao menos uma Linha de Fomento."
            )

        instituicao_financiadora = request.form.getlist("instituicao_financiadora")
        if not instituicao_financiadora:
            return render_template(
                "moderacao/editar.html", o=oportunidade, erro="Informe ao menos uma Instituição Financiadora."
            )

        def parse_decimal(campo):
            return parse_valor_brl(request.form.get(campo))

        def parse_data(campo):
            valor_raw = request.form.get(campo) or ""
            return datetime.strptime(valor_raw, "%Y-%m-%d").date() if valor_raw else None

        oportunidade.titulo = request.form["titulo"]
        oportunidade.descricao = request.form.get("descricao") or None
        oportunidade.link = request.form["link"]
        oportunidade.instituicao_financiadora = instituicao_financiadora
        oportunidade.instituicao_promotora = request.form.get("instituicao_promotora") or None
        oportunidade.linha_de_fomento = linha_de_fomento
        oportunidade.tipo_instrumento = request.form["tipo_instrumento"]
        oportunidade.tipo_parceria = request.form.get("tipo_parceria") or None
        oportunidade.modalidade_pessoa = request.form.get("modalidade_pessoa") or None
        oportunidade.nivel_formacao = request.form.getlist("nivel_formacao") or None
        oportunidade.abrangencia = request.form.get("abrangencia") or None
        oportunidade.uf = request.form.getlist("uf") or None
        oportunidade.area_principal = request.form.get("area_principal") or None

        oportunidade.palavras_chave = request.form.getlist("palavras_chave") or None

        oportunidade.natureza_recurso = request.form.getlist("natureza_recurso")
        oportunidade.proponente_elegivel = request.form.getlist("proponente_elegivel")

        # Reatribuição (e não mutação in-place) para o SQLAlchemy detectar a mudança no
        # JSONB; `aplicar_faixas` preserva as chaves que o scraper gravou ali.
        oportunidade.dados_extra = aplicar_faixas(
            oportunidade.dados_extra, parse_faixas(request.form)
        )

        # Campos que o scraper nem sempre consegue extrair (ex: FAPES nunca traz prazo
        # na listagem) — sem isso no formulário, a curadoria desses casos fica bloqueada.
        oportunidade.data_publicacao = parse_data("data_publicacao")
        oportunidade.data_prazo = parse_data("data_prazo")
        oportunidade.data_resultado_previsto = parse_data("data_resultado_previsto")
        oportunidade.orcamento_total_chamada = parse_decimal("orcamento_total_chamada")
        oportunidade.valor_minimo_proposta = parse_decimal("valor_minimo_proposta")
        oportunidade.valor_maximo_proposta = parse_decimal("valor_maximo_proposta")

        avisos = avisos_de_aprovacao(request.form) if acao == "aprovar" else []
        if avisos and not request.form.get("confirmar_avisos"):
            # Devolve o formulário com os avisos SEM commit: `oportunidade` já recebeu as
            # edições acima, mas elas ficam só na sessão e o `db.session` é descartado no
            # fim da requisição. O curador vê os campos como acabou de preenchê-los.
            return render_template("moderacao/editar.html", o=oportunidade, avisos=avisos)

        if acao == "aprovar":
            oportunidade.status = "aprovado"
        elif acao == "rejeitar":
            oportunidade.status = "rejeitado"
        # "salvar" mantém o status atual (pendente), só grava as edições

        db.session.commit()

        if acao in ("aprovar", "rejeitar"):
            return redirect(url_for("main.listar_pendentes"))
        return redirect(url_for("main.moderar_oportunidade", id=id))

    return render_template("moderacao/editar.html", o=oportunidade)


# PENDÊNCIA DE SEGURANÇA: painel admin sem controle de acesso — qualquer pessoa com a
# URL pode disparar todos os scrapers a qualquer momento. Mesma dívida já documentada para
# /moderacao e /oportunidades/importar; precisa virar rota restrita a admin quando o
# sistema de usuários/papéis existir.
@main.route("/admin/scrapers", methods=["GET"])
def painel_scrapers():
    historico = ExecucaoScraper.query.order_by(
        ExecucaoScraper.executado_em.desc()
    ).limit(20).all()
    return render_template("admin/scrapers.html", historico=historico)


@main.route("/admin/scrapers/rodar", methods=["POST"])
def rodar_scrapers_agora():
    # Roda todos os scrapers de forma síncrona — a página fica "carregando" até terminar.
    # Aceitável agora (poucos segundos por fonte); evoluir para background/assíncrono
    # se um dia demorar muito (sites lentos, muitas fontes novas).
    from scripts.rodar_todos_scrapers import rodar_e_registrar

    rodar_e_registrar(disparado_por="manual")
    return redirect(url_for("main.painel_scrapers"))