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
        oportunidade = Oportunidade(
            titulo=request.form["titulo"],
            instituicao=request.form["instituicao"],
            tipo=request.form["tipo"],
            link=request.form["link"],
            fonte=request.form["fonte"],
            descricao=request.form.get("descricao") or None,
            area_conhecimento=request.form.get("area_conhecimento") or None,
        )
        db.session.add(oportunidade)
        db.session.commit()
        return redirect(url_for("main.listar_oportunidades"))

    return render_template("oportunidades/nova.html")