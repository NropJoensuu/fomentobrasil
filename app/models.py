from datetime import datetime

from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from app import db


class Oportunidade(db.Model):
    __tablename__ = "oportunidades"

    id = db.Column(db.Integer, primary_key=True)

    # Campos comuns a todos os tipos
    titulo = db.Column(db.String(300), nullable=False)
    descricao = db.Column(db.Text, nullable=True)

    # Linha de fomento: o que a chamada oferece em termos de finalidade. Lista porque uma
    # chamada pode ter mais de uma simultaneamente (ex: FAPEMIG-SEDE 013/2026 tem três).
    # `premiacao` (2026-09-01) é a exceção retrospectiva às outras cinco, que são todas
    # apoio prospectivo a atividade futura: reconhece resultado já alcançado, não financia
    # o que vai acontecer. Migrou de `tipo_instrumento` — ver comentário abaixo.
    linha_de_fomento = db.Column(ARRAY(db.String(50)), nullable=False)  # auxilio_pesquisa, auxilio_inovacao, auxilio_divulgacao_cientifica, apoio_formacao_capacitacao, apoio_redes_grupos_pesquisa, premiacao

    # Instrumento administrativo/legal usado para veicular a linha de fomento. `premio` saiu
    # daqui (2026-09-01): não é procedimento, é o que está sendo oferecido — erro de
    # categoria. Um prêmio é concedido POR MEIO de um edital, então vira `linha_de_fomento`
    # ("premiacao") e o instrumento passa a ser `chamada_publica_edital` como qualquer outro.
    tipo_instrumento = db.Column(db.String(50), nullable=False)  # chamada_publica_edital, chamamento_publico

    # O que é efetivamente concedido na prática (pode ter mais de um valor)
    natureza_recurso = db.Column(ARRAY(db.String(50)), nullable=False)  # custeio, capital, bolsa

    # Quem pode APRESENTAR a proposta (elegibilidade do proponente), em nome próprio
    # (pessoa física) ou em nome de instituição (pessoa jurídica).
    # NÃO é "quem é beneficiado" — população beneficiada específica (mães, mulheres,
    # quilombolas) e instituição demandante vão em palavras_chave.
    # "ies" e "ict" são mantidos separados de propósito: a legislação os define em textos
    # distintos (LDB e Lei 10.973/2004). Editais que usam a sigla "IES/P" da FAPES
    # ("Instituições de Ensino Superior e/ou de Pesquisa") cobrem os dois — marcar ambos.
    proponente_elegivel = db.Column(ARRAY(db.String(50)), nullable=False)  # ver PROPONENTE_PESSOA_FISICA e PROPONENTE_PESSOA_JURIDICA em app/utils.py

    # Proveniência: institucional (edital top-down) vs vaga_projeto (oferta ligada a projeto já financiado)
    origem = db.Column(db.String(30), nullable=False, default="institucional")
    status = db.Column(db.String(30), nullable=False, default="aprovado")  # rascunho, pendente, aprovado, rejeitado (moderação/curadoria interna)

    # Status oficial declarado pela instituição sobre o edital em si (distinto de `status`, que é moderação).
    # Quando None (caso normal), aberta/encerrada é calculado a partir de data_prazo. Quando preenchido, tem prioridade sobre esse cálculo.
    status_oficial = db.Column(db.String(30), nullable=True)  # suspensa, cancelada, retificada, resultado_divulgado

    # Quem financia vs onde a pessoa atua. Lista porque um edital pode ter mais de uma
    # financiadora simultaneamente (ex: "INICIATIVA AMAZÔNIA+10: CONFAP-BNDES", financiado
    # por várias FAPs + BNDES juntos).
    instituicao_financiadora = db.Column(ARRAY(db.String(200)), nullable=False)  # quem origina o recurso: ex "CNPq", "FAPESP"

    # Publica a chamada e recebe as propostas. Distinta das financiadoras: em chamadas
    # conjuntas, várias instituições aportam recurso mas uma opera o certame. O edital
    # Amazônia +10 tem aporte de várias FAPs, CONFAP e BNDES, e foi publicado pela FAPESP
    # numa edição e pelo CNPq em outra — o proponente precisa saber ONDE submete, e isso
    # não é derivável da lista de financiadoras.
    instituicao_promotora = db.Column(db.String(200), nullable=True)

    # Lista porque uma mesma chamada costuma conceder bolsa para mais de um nível
    # (ex: mestrado e doutorado na mesma chamada). Só relevante quando
    # natureza_recurso inclui bolsa.
    nivel_formacao = db.Column(ARRAY(db.String(50)), nullable=True)  # mestrado, doutorado, pos_doutorado, iniciacao_cientifica, nao_aplicavel

    area_principal = db.Column(db.String(100), nullable=True)  # Grande Área da Tabela CNPq/CAPES (lista fechada, ver formulário)
    palavras_chave = db.Column(ARRAY(db.String(150)), nullable=True)  # livre, suporta múltiplos valores

    # Escopo da parceria entre instituições, quando houver
    tipo_parceria = db.Column(db.String(30), nullable=True)  # nacional, regional, internacional

    # Só relevante quando linha_de_fomento = apoio_formacao_capacitacao
    modalidade_pessoa = db.Column(db.String(30), nullable=True)  # atracao, fixacao, capacitacao, capacitacao_exterior

    # Abrangência geográfica. `uf` é lista porque uma chamada regional/multi-institucional
    # pode valer para várias UFs ao mesmo tempo (ex: chamada CONFAP com FAPs de vários estados).
    abrangencia = db.Column(db.String(30), nullable=True)  # nacional, estadual, regional, internacional
    uf = db.Column(ARRAY(db.String(2)), nullable=True)  # preenchido só quando abrangencia="estadual"/"regional"

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

    # Marca registros já aprovados em que um re-scrape detectou mudança num campo
    # monitorado (ver app/scraper_utils.CAMPOS_MONITORADOS) desde a última curadoria.
    # Não afeta `status`/visibilidade pública — só sinaliza que precisa de nova revisão.
    revisao_pendente = db.Column(db.Boolean, default=False, nullable=False)

    # Auditoria
    criado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    atualizado_em = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def __repr__(self):
        linhas = ", ".join(self.linha_de_fomento or [])
        return f"<Oportunidade {self.titulo} ({linhas})>"


class ExecucaoScraper(db.Model):
    __tablename__ = "execucoes_scraper"

    id = db.Column(db.Integer, primary_key=True)
    executado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    disparado_por = db.Column(db.String(30), nullable=False)  # "manual" ou "agendado"
    resumo_json = db.Column(JSONB, nullable=False)
    sucesso = db.Column(db.Boolean, nullable=False)

    def __repr__(self):
        return f"<ExecucaoScraper {self.executado_em} ({self.disparado_por})>"