from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for

from app import db
from app.models import Oportunidade

main = Blueprint("main", __name__)


@main.route("/")
def index():
    return render_template("index.html")


@main.route("/oportunidades")
def listar_oportunidades():
    oportunidades = Oportunidade.query.order_by(Oportunidade.criado_em.desc()).all()
    return render_template("oportunidades/listar.html", oportunidades=oportunidades)


@main.route("/oportunidades/nova", methods=["GET", "POST"])
def nova_oportunidade():
    if request.method == "POST":
        areas_raw = request.form.get("area_conhecimento") or ""
        areas = [a.strip() for a in areas_raw.split(",") if a.strip()]

        oportunidade = Oportunidade(
            titulo=request.form["titulo"],
            instituicao_financiadora=request.form["instituicao_financiadora"],
            instituicao_executora=request.form.get("instituicao_executora") or None,
            instituicao_beneficiaria=request.form.get("instituicao_beneficiaria") or None,
            tipo=request.form["tipo"],
            link=request.form["link"],
            descricao=request.form.get("descricao") or None,
            area_conhecimento=areas or None,
        )
        db.session.add(oportunidade)
        db.session.commit()
        return redirect(url_for("main.listar_oportunidades"))

    return render_template("oportunidades/nova.html")