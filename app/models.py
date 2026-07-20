from datetime import datetime

from sqlalchemy.dialects.postgresql import JSONB

from app import db


class Oportunidade(db.Model):
    __tablename__ = "oportunidades"

    id = db.Column(db.Integer, primary_key=True)

    # Campos comuns a todos os tipos
    titulo = db.Column(db.String(300), nullable=False)
    descricao = db.Column(db.Text, nullable=True)
    instituicao = db.Column(db.String(150), nullable=False)
    tipo = db.Column(db.String(50), nullable=False)  # bolsa, auxilio, chamada_publica, projeto, cooperacao_internacional, premio
    area_conhecimento = db.Column(db.String(150), nullable=True)
    valor = db.Column(db.Numeric(12, 2), nullable=True)
    link = db.Column(db.String(500), nullable=False)
    fonte = db.Column(db.String(100), nullable=False)  # ex: "CNPq", "FAPESP"

    # Datas
    data_publicacao = db.Column(db.Date, nullable=True)
    data_prazo = db.Column(db.Date, nullable=True)

    # Status
    ativo = db.Column(db.Boolean, default=True, nullable=False)

    # Particularidades por tipo (público-alvo, requisitos, documentos, etc.)
    dados_extra = db.Column(JSONB, nullable=True)

    # Auditoria
    criado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    atualizado_em = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def __repr__(self):
        return f"<Oportunidade {self.titulo} ({self.tipo})>"