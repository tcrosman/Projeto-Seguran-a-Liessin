# -*- coding: utf-8 -*-
"""Ao editar a própria solicitação aprovada, o responsável executava
`DELETE FROM saidas WHERE ra=%s AND data_saida=%s AND status='pendente'` — sem nenhuma amarração
com a solicitação. Isso apagava toda saída pendente do aluno naquele dia, inclusive uma que a
portaria tivesse registrado por conta própria, com outro motivo e documento anexado. E o portal
dos pais não tinha uma única chamada de auditoria, então o registro da escola sumia sem rastro.

O conserto amarra a saída à solicitação que a originou (`saidas.solicitacao_id`) e leva a trilha
de auditoria para o lado do responsável.
"""
import pytest

SOL_APROVADA = {
    'id': 7, 'responsavel_id': 3, 'aluno_id': None, 'ra': '2024001',
    'data_solicitada': '2026-09-10', 'horario_solicitado': '13:00', 'motivo': 'Consulta',
    'tipo_saida': 'sozinho', 'acompanhante': None, 'status': 'aprovado',
    'saida_status': 'pendente', 'nome_legado': None, 'turma_legado': None, 'serie_legado': None,
}

SOL_ADMIN = {
    'id': 7, 'aluno_id': None, 'ra': '2024001', 'data_solicitada': '2026-09-10',
    'horario_solicitado': '13:00', 'motivo': 'Consulta', 'tipo_saida': 'sozinho',
    'acompanhante': None, 'status': 'aguardando', 'turma': 'A',
    'responsavel_nome': 'Maria Souza', 'responsavel_email': 'mae@teste.com',
    'nome_legado': None, 'turma_legado': None, 'serie_legado': None,
}

FORM_EDICAO = {
    'data_solicitada': '2026-09-10', 'horario': '15:00', 'motivo': 'Consulta',
    'tipo_saida': 'sozinho',
}


@pytest.fixture
def responsavel(cliente):
    with cliente.session_transaction() as s:
        s['pai_id'] = 3
        s['pai_email'] = 'mae@teste.com'
        s['pai_nome'] = 'Maria Souza'
    return cliente


# ---------------------------------------------------------------- o escopo do DELETE

def test_edicao_apaga_apenas_a_saida_daquela_solicitacao(responsavel, banco):
    banco.responder("FROM solicitacoes_saida ss", [SOL_APROVADA])

    responsavel.post("/pais/editar_solicitacao/7", data=FORM_EDICAO)

    (sql, params), = banco.sql_com("DELETE FROM saidas")
    assert "solicitacao_id" in sql
    assert params == (7,)


def test_o_delete_nao_alcanca_mais_saidas_por_aluno_e_data(responsavel, banco):
    """O filtro antigo pegava qualquer pendente do aluno naquele dia — inclusive a da portaria."""
    banco.responder("FROM solicitacoes_saida ss", [SOL_APROVADA])

    responsavel.post("/pais/editar_solicitacao/7", data=FORM_EDICAO)

    (sql, params), = banco.sql_com("DELETE FROM saidas")
    assert "data_saida" not in sql
    assert '2024001' not in params and '2026-09-10' not in params


def test_aprovacao_grava_o_vinculo_que_o_delete_usa(sessao_admin, banco):
    """As duas pontas precisam casar: sem o solicitacao_id gravado aqui, o DELETE lá não acha
    nada e a saída antiga fica de pé depois da reaprovação."""
    banco.responder("FROM solicitacoes_saida ss", [SOL_ADMIN])
    banco.responder("UPDATE solicitacoes_saida SET status = 'aprovado'", rowcount=1)
    banco.responder("SELECT 1 FROM saidas", [])

    sessao_admin.post("/admin/solicitacoes/7/aprovar")

    (sql, params), = banco.sql_com("INSERT INTO saidas")
    assert "solicitacao_id" in sql
    assert params[-1] == 7


def test_edicao_sem_reaprovacao_nao_apaga_saida_nenhuma(responsavel, banco):
    """Só mudar o motivo não desfaz a aprovação — e portanto não pode remover a saída."""
    banco.responder("FROM solicitacoes_saida ss", [SOL_APROVADA])

    responsavel.post("/pais/editar_solicitacao/7",
                     data=dict(FORM_EDICAO, horario='13:00', motivo='Outro motivo'))

    assert banco.sql_com("DELETE FROM saidas") == []


def test_solicitacao_de_outro_responsavel_continua_inacessivel(responsavel, banco):
    """A consulta filtra por responsavel_id da sessão; sem linha, nada acontece."""
    banco.responder("FROM solicitacoes_saida ss", [])

    r = responsavel.post("/pais/editar_solicitacao/999", data=FORM_EDICAO)

    assert r.status_code == 302
    assert banco.sql_com("DELETE FROM saidas") == []
    assert banco.sql_com("UPDATE solicitacoes_saida") == []


# ---------------------------------------------------------------- a trilha do lado do responsável

def test_edicao_com_reaprovacao_registra_quantas_saidas_removeu(responsavel, banco):
    banco.responder("FROM solicitacoes_saida ss", [SOL_APROVADA])
    banco.responder("DELETE FROM saidas", rowcount=1)

    responsavel.post("/pais/editar_solicitacao/7", data=FORM_EDICAO)

    acao, detalhes, _ip = banco.auditorias()[0]
    assert acao == "EDITOU SOLICITAÇÃO (reaprovação)"
    assert "saídas removidas: 1" in detalhes
    assert "15:00" in detalhes


def test_edicao_simples_tambem_fica_registrada(responsavel, banco):
    banco.responder("FROM solicitacoes_saida ss", [SOL_APROVADA])

    responsavel.post("/pais/editar_solicitacao/7",
                     data=dict(FORM_EDICAO, horario='13:00', motivo='Outro motivo'))

    assert banco.acoes_auditadas() == ["EDITOU SOLICITAÇÃO"]


def test_a_trilha_distingue_responsavel_de_funcionario(responsavel, banco):
    """`auditoria.usuario` recebe username de funcionário e e-mail de responsável na mesma
    coluna; sem prefixo, uma apuração não sabe de qual lado veio a ação."""
    banco.responder("FROM solicitacoes_saida ss", [SOL_APROVADA])

    responsavel.post("/pais/editar_solicitacao/7", data=FORM_EDICAO)

    (_sql, params), = banco.sql_com("INSERT INTO auditoria")
    assert params[0] == "responsavel:mae@teste.com"


def test_nova_solicitacao_fica_registrada(responsavel, banco):
    from unittest.mock import patch
    from tests.apoio import DiretorioFalso

    escola = DiretorioFalso({"2024001": {"ra": "2024001", "nome": "Ana", "turma": "A",
                                         "serie": "6º ano EF", "foto_url": None,
                                         "responsaveis_email": ["mae@teste.com"]}})
    banco.responder("SELECT id FROM solicitacoes_saida", [])

    with patch("app.api.pais.get_school_sql_directory", return_value=escola):
        responsavel.post("/pais/solicitar/2024001", data={
            'data_solicitada': '2026-09-10', 'horario': '13:00', 'motivo': 'Consulta',
            'tipo_saida': 'sozinho',
        })

    acao, detalhes, _ip = banco.auditorias()[0]
    assert acao == "SOLICITOU SAÍDA"
    assert "2024001" in detalhes and "2026-09-10" in detalhes


def test_login_do_responsavel_fica_registrado(cliente, banco):
    from datetime import datetime, timedelta
    with cliente.session_transaction() as s:
        s['pai_temp_id'] = 3
        s['pai_temp_email'] = 'mae@teste.com'
        s['pai_temp_nome'] = 'Maria Souza'

    banco.responder("SELECT id, expires_at FROM tokens_2fa",
                    [{'id': 1, 'expires_at': datetime.utcnow() + timedelta(minutes=5)}])
    banco.responder("UPDATE tokens_2fa SET usado = TRUE", rowcount=1)

    r = cliente.post("/pais/verificar", data={'codigo': '123456'})

    assert r.status_code == 302
    assert banco.acoes_auditadas() == ["LOGIN RESPONSÁVEL"]


def test_autocadastro_fica_registrado(cliente, banco):
    from unittest.mock import patch
    from tests.apoio import DiretorioFalso

    banco.responder("SELECT id FROM responsaveis WHERE email", [])
    banco.responder("SELECT email FROM usuarios", [])
    escola = DiretorioFalso({"2024001": {"ra": "2024001", "nome": "Ana", "turma": "A",
                                         "serie": "6º ano EF", "foto_url": None,
                                         "responsaveis_email": ["nova@teste.com"]}})

    with patch("app.api.pais.get_school_sql_directory", return_value=escola):
        cliente.post("/pais/cadastro", data={
            'nome': 'Maria Souza', 'email': 'nova@teste.com',
            'senha': 'Senha@Forte1', 'confirmar': 'Senha@Forte1',
        })

    acao, detalhes, _ip = banco.auditorias()[0]
    assert acao == "AUTOCADASTRO DE RESPONSÁVEL"
    assert "Maria Souza" in detalhes


def test_redefinicao_de_senha_do_responsavel_fica_registrada(cliente, banco):
    from datetime import datetime, timedelta
    banco.responder("SELECT responsavel_id, expires_at FROM reset_tokens_pais",
                    [{'responsavel_id': 3, 'expires_at': datetime.utcnow() + timedelta(hours=1)}])

    cliente.post("/pais/resetar_senha/tok", data={
        'senha': 'Senha@Forte1', 'confirma': 'Senha@Forte1',
    })

    assert banco.acoes_auditadas() == ["REDEFINIU SENHA"]


def test_logout_do_responsavel_fica_registrado(responsavel, banco):
    responsavel.post("/pais/logout")

    assert banco.acoes_auditadas() == ["LOGOUT RESPONSÁVEL"]


def test_logout_sem_sessao_nao_gera_registro(cliente, banco):
    cliente.post("/pais/logout")

    assert banco.acoes_auditadas() == []


# ---------------------------------------------------------------- o conserto não pode estragar nada

def test_rotas_do_portal_nao_abrem_conexao_aninhada(responsavel, banco):
    banco.responder("FROM solicitacoes_saida ss", [SOL_APROVADA])

    responsavel.post("/pais/editar_solicitacao/7", data=FORM_EDICAO)

    assert banco.profundidade_maxima == 1


def test_falha_na_auditoria_nao_impede_a_edicao(responsavel, banco):
    banco.responder("FROM solicitacoes_saida ss", [SOL_APROVADA])
    banco.falhar_em("INSERT INTO auditoria")

    r = responsavel.post("/pais/editar_solicitacao/7", data=FORM_EDICAO)

    assert r.status_code == 302
    assert banco.sql_com("UPDATE solicitacoes_saida") != []
