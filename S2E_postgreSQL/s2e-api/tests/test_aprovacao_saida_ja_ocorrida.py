# -*- coding: utf-8 -*-
"""A checagem feita antes de criar a saída aprovada olhava só `status='pendente'`. Uma saída já
`concluida` — o aluno saiu — não bloqueava nada: aprovar uma solicitação esquecida na fila criava
uma nova saída pendente, e a portaria passava a exibir autorização de saída para um aluno que já
não estava na escola.
"""
import pytest


def _sol(**extra):
    linha = {
        'id': 7, 'aluno_id': None, 'ra': '2024001', 'data_solicitada': '2026-09-10',
        'horario_solicitado': '13:00', 'motivo': 'Consulta', 'tipo_saida': 'sozinho',
        'acompanhante': None, 'status': 'aguardando', 'turma': 'A',
        'responsavel_nome': 'Maria Souza', 'responsavel_email': 'mae@teste.com',
        'responsavel_status': 'aprovado',
        'nome_legado': None, 'turma_legado': None, 'serie_legado': None,
    }
    linha.update(extra)
    return linha


@pytest.fixture
def aprovando(sessao_admin, banco):
    banco.responder("FROM solicitacoes_saida ss", [_sol()])
    banco.responder("UPDATE solicitacoes_saida SET status = 'aprovado'", rowcount=1)
    return sessao_admin


# ---------------------------------------------------------------- o defeito em si

def test_aluno_ja_liberado_nao_ganha_nova_saida(aprovando, banco):
    banco.responder("SELECT status FROM saidas", [{'status': 'concluida'}])

    aprovando.post("/admin/solicitacoes/7/aprovar")

    assert banco.sql_com("INSERT INTO saidas") == []


def test_o_admin_e_avisado_de_que_o_aluno_ja_saiu(aprovando, banco):
    """Silenciar não serve: quem aprovou precisa saber que a saída não foi criada e por quê."""
    banco.responder("SELECT status FROM saidas", [{'status': 'concluida'}])

    r = aprovando.post("/admin/solicitacoes/7/aprovar", follow_redirects=True)
    corpo = r.get_data(as_text=True)

    assert "já foi liberado nesta data" in corpo
    assert "não deve liberá-lo de novo" in corpo


def test_a_consulta_passou_a_considerar_a_saida_concluida(aprovando, banco):
    banco.responder("SELECT status FROM saidas", [])

    aprovando.post("/admin/solicitacoes/7/aprovar")

    (sql, _p), = banco.sql_com("SELECT status FROM saidas")
    assert "'pendente', 'concluida'" in sql


def test_a_decisao_continua_registrada_mesmo_sem_criar_saida(aprovando, banco):
    """A aprovação aconteceu; o que não aconteceu foi a criação da saída."""
    banco.responder("SELECT status FROM saidas", [{'status': 'concluida'}])

    aprovando.post("/admin/solicitacoes/7/aprovar")

    assert banco.acoes_auditadas() == ["APROVOU SOLICITAÇÃO"]


# ---------------------------------------------------------------- sem regressão

def test_saida_pendente_continua_bloqueando_com_a_mensagem_antiga(aprovando, banco):
    banco.responder("SELECT status FROM saidas", [{'status': 'pendente'}])

    r = aprovando.post("/admin/solicitacoes/7/aprovar", follow_redirects=True)

    assert "já tinha uma saída pendente" in r.get_data(as_text=True)
    assert banco.sql_com("INSERT INTO saidas") == []


def test_sem_saida_anterior_a_aprovacao_cria_normalmente(aprovando, banco):
    banco.responder("SELECT status FROM saidas", [])

    r = aprovando.post("/admin/solicitacoes/7/aprovar")

    assert r.status_code == 302
    assert banco.sql_com("INSERT INTO saidas") != []


def test_saida_nao_realizada_nao_bloqueia(aprovando, banco):
    """'nao_realizada' só existe em data que já passou e não descreve um aluno que saiu — não
    pode ser confundida com liberação."""
    banco.responder("SELECT status FROM saidas WHERE ra", [], rowcount=0)

    aprovando.post("/admin/solicitacoes/7/aprovar")

    (sql, _p), = banco.sql_com("SELECT status FROM saidas")
    assert "nao_realizada" not in sql


def test_solicitacao_legada_sem_ra_usa_a_coluna_aluno(sessao_admin, banco):
    banco.responder("FROM solicitacoes_saida ss", [_sol(ra=None, aluno_id=42)])
    banco.responder("UPDATE solicitacoes_saida SET status = 'aprovado'", rowcount=1)
    banco.responder("SELECT status FROM saidas", [{'status': 'concluida'}])

    sessao_admin.post("/admin/solicitacoes/7/aprovar")

    (sql, params), = banco.sql_com("SELECT status FROM saidas")
    assert "aluno = %s" in sql
    assert params[0] == 42
    assert banco.sql_com("INSERT INTO saidas") == []


def test_aprovacao_bloqueada_nao_abre_conexao_aninhada(aprovando, banco):
    banco.responder("SELECT status FROM saidas", [{'status': 'concluida'}])

    aprovando.post("/admin/solicitacoes/7/aprovar")

    assert banco.profundidade_maxima == 1
