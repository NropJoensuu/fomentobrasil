import atexit
import os

from dotenv import load_dotenv
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate



load_dotenv()

migrate = Migrate()

db = SQLAlchemy()



def create_app(iniciar_agendador=False):
    app = Flask(__name__)
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///app.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev")
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}

    db.init_app(app)
    migrate.init_app(app, db)

    from app import models  # <-- adicionar esta linha

    from app.routes import main

    app.register_blueprint(main)

    # `iniciar_agendador` é opt-in (default False) de propósito: create_app() é chamado
    # por todo script/scraper avulso e por qualquer teste que precise de app_context
    # (inclusive os deste projeto). Se o agendador iniciasse incondicionalmente aqui
    # dentro, cada `python -m scrapers.x` ou `flask shell` levantaria uma thread de
    # background com um cron apontando pras 20h — só run.py deve passar True.
    if iniciar_agendador:
        _iniciar_agendador_diario(app)

    return app


def _iniciar_agendador_diario(app):
    # Só inicia no processo que efetivamente serve requisições, não no processo
    # "monitor" do reloader do Flask (--debug/app.run(debug=True) sobe dois
    # processos: o monitor, que reinicia o worker a cada mudança de arquivo, e o
    # worker de verdade). WERKZEUG_RUN_MAIN só existe (e vale "true") no worker;
    # checar isso é o que evita o job rodar em dobro — confirmado empiricamente
    # (o monitor não tem a variável definida; o worker tem == "true").
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")

    def job_scraper_diario():
        with app.app_context():
            from scripts.rodar_todos_scrapers import rodar_e_registrar

            rodar_e_registrar(disparado_por="agendado")

    scheduler.add_job(job_scraper_diario, "cron", hour=20, minute=0)
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())
