import os

from dotenv import load_dotenv
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate



load_dotenv()

migrate = Migrate()

db = SQLAlchemy()



def create_app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///app.db")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev")

    db.init_app(app)
    migrate.init_app(app, db)

    from app import models  # <-- adicionar esta linha

    from app.routes import main

    app.register_blueprint(main)

    return app
