from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for

from app import db
from app.models import Oportunidade
from app.utils import get_regiao

main = Blueprint("main", __name__)


@main.route("/")
def index():
    return render_template("index.html")


@main.route("/oportunidades")
def listar_oportunidades():
    oportunidades = Oportunidade.query.order_by(Oportunidade.criado_em.desc()).all()
    return render_template("oportunidades/listar.html", oportunidades=oportunidades)


@main.route("/oportunidades/<int:id>")
def detalhe_oportunidade(id):
    oportunidade = Oportunidade.query.get_or_404(id)
    hoje = datetime.utcnow().date()
    aberta = oportunidade.data_prazo is None or oportunidade.data_prazo >= hoje
    regiao = get_regiao(oportunidade.uf)
    return render_template("oportunidades/detalhe.html", o=oportunidade, aberta=aberta, regiao=regiao)


@main.route("/oportunidades/nova", methods=["GET", "POST"])
def nova_oportunidade():
    if request.method == "POST":
        palavras_chave_raw = request.form.get("palavras_chave") or ""
        palavras_chave = [p.strip() for p in palavras_chave_raw.split(",") if p.strip()]

        def parse_decimal(campo):
            valor_raw = request.form.get(campo) or ""
            return float(valor_raw) if valor_raw else None

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
            instituicao_financiadora=request.form["instituicao_financiadora"],
            instituicao_executora=request.form.get("instituicao_executora") or None,
            instituicao_beneficiaria=request.form.get("instituicao_beneficiaria") or None,
            linha_de_fomento=request.form["linha_de_fomento"],
            tipo_instrumento=request.form["tipo_instrumento"],
            natureza_recurso=request.form.getlist("natureza_recurso"),
            publico_alvo=request.form.getlist("publico_alvo"),
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
            nivel_formacao=request.form.get("nivel_formacao") or None,
            abrangencia=request.form.get("abrangencia") or None,
            uf=request.form.get("uf") or None,
            cidade=request.form.get("cidade") or None,
            data_publicacao=(
                datetime.strptime(request.form["data_publicacao"], "%Y-%m-%d").date()
                if request.form.get("data_publicacao") else None
            ),
        )
        db.session.add(oportunidade)
        db.session.commit()
        return redirect(url_for("main.listar_oportunidades"))

    return render_template("oportunidades/nova.html")