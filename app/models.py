from datetime import datetime

from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from app import db


class Oportunidade(db.Model):
    __tablename__ = "oportunidades"

    id = db.Column(db.Integer, primary_key=True)

    # Campos comuns a todos os tipos
    titulo = db.Column(db.String(300), nullable=False)
    descricao = db.Column(db.Text, nullable=True)

    # Linha de fomento: o que a chamada oferece em termos de finalidade
    linha_de_fomento = db.Column(db.String(50), nullable=False)  # auxilio_pesquisa, auxilio_inovacao, auxilio_divulgacao_cientifica, apoio_formacao_capacitacao, apoio_redes_grupos_pesquisa

    # Instrumento administrativo/legal usado para veicular a linha de fomento
    tipo_instrumento = db.Column(db.String(50), nullable=False)  # chamada_publica_edital, chamamento_publico, premio

    # O que é efetivamente concedido na prática (pode ter mais de um valor)
    natureza_recurso = db.Column(ARRAY(db.String(50)), nullable=False)  # custeio, capital, bolsa

    # Quem pode se candidatar (pode ter mais de um valor)
    publico_alvo = db.Column(ARRAY(db.String(50)), nullable=False)  # pesquisadores, empresas, startups, ict, mestrandos, doutorandos, ies, governo

    # Proveniência: institucional (edital top-down) vs vaga_projeto (oferta ligada a projeto já financiado)
    origem = db.Column(db.String(30), nullable=False, default="institucional")
    status = db.Column(db.String(30), nullable=False, default="aprovado")  # rascunho, pendente, aprovado, rejeitado (moderação/curadoria interna)

    # Status oficial declarado pela instituição sobre o edital em si (distinto de `status`, que é moderação).
    # Quando None (caso normal), aberta/encerrada é calculado a partir de data_prazo. Quando preenchido, tem prioridade sobre esse cálculo.
    status_oficial = db.Column(db.String(30), nullable=True)  # suspensa, cancelada, retificada, resultado_divulgado

    # Quem financia vs onde a pessoa atua
    instituicao_financiadora = db.Column(db.String(200), nullable=False)  # quem origina o recurso: ex "CNPq", "FAPESP"
    instituicao_executora = db.Column(db.String(200), nullable=True)  # quem executa o projeto: ex "Centro Universitário FEI"
    instituicao_beneficiaria = db.Column(db.String(200), nullable=True)  # intermediária que recebe e repassa o recurso (ex: fundação de apoio)

    nivel_formacao = db.Column(db.String(50), nullable=True)  # mestrado, doutorado, pos_doutorado, iniciacao_cientifica, nao_aplicavel; só relevante quando natureza_recurso inclui bolsa

    area_principal = db.Column(db.String(100), nullable=True)  # Grande Área da Tabela CNPq/CAPES (lista fechada, ver formulário)
    palavras_chave = db.Column(ARRAY(db.String(150)), nullable=True)  # livre, suporta múltiplos valores

    # Escopo da parceria entre instituições, quando houver
    tipo_parceria = db.Column(db.String(30), nullable=True)  # nacional, regional, internacional

    # Só relevante quando linha_de_fomento = apoio_formacao_capacitacao
    modalidade_pessoa = db.Column(db.String(30), nullable=True)  # atracao, fixacao, capacitacao_exterior

    # Abrangência geográfica
    abrangencia = db.Column(db.String(30), nullable=True)  # nacional, estadual, regional, internacional
    uf = db.Column(db.String(2), nullable=True)  # preenchido só quando abrangencia="estadual"
    cidade = db.Column(db.String(150), nullable=True)

    orcamento_total_chamada = db.Column(db.Numeric(14, 2), nullable=True)
    valor_minimo_proposta = db.Column(db.Numeric(14, 2), nullable=True)
    valor_maximo_proposta = db.Column(db.Numeric(14, 2), nullable=True)
    link = db.Column(db.String(500), nullable=False)

    # Datas
    data_publicacao = db.Column(db.Date, nullable=True)
    data_prazo = db.Column(db.Date, nullable=True)  # data-fim de submissão
    data_resultado_previsto = db.Column(db.Date, nullable=True)  # data prevista de divulgação do resultado

    ativo = db.Column(db.Boolean, default=True, nullable=False)

    # Particularidades por tipo (faixas de valor, modalidade, requisitos, público-alvo detalhado)
    dados_extra = db.Column(JSONB, nullable=True)

    # Auditoria
    criado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    atualizado_em = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def __repr__(self):
        return f"<Oportunidade {self.titulo} ({self.linha_de_fomento})>"