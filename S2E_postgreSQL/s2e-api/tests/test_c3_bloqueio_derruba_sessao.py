# -*- coding: utf-8 -*-
"""C3 — bloquear um responsável não derrubava a sessão que já estava aberta.

`pai_required` só verificava `'pai_id' in session`, e nenhuma rota do portal relia
`responsaveis.status`. Bloquear gravava a coluna e mais nada: quem já estava logado quando a
escola descobriu que ele não pode mais retirar a criança mantinha dashboard, nova solicitação e
edição por até 8h de sessão ou 30 min de inatividade. O bloqueio só passava a valer no login
seguinte.

Agora o status é relido a cada request, com cache curto de processo — a janela de bloqueio
efetivo é de segundos, não de horas.
"""
import pytest

from app.api import middleware
from app.core.cache import TTLCache


def _sessao_de_responsavel(cliente):
    with cliente.session_transaction() as s:
        s['pai_id'] = 3
        s['pai_email'] = 'mae@teste.com'
        s['pai_nome'] = 'Maria Souza'


def _expirar_o_cache():
    """Simula a passagem da janela de 30s sem esperar por ela."""
    middleware._cache_status_responsavel = TTLCache(ttl_seconds=-1)


@pytest.fixture
def portal(cliente):
    _sessao_de_responsavel(cliente)
    return cliente


# ---------------------------------------------------------------- o status passa a ser relido

def test_o_portal_confere_o_status_no_banco_e_nao_so_a_sessao(portal, banco):
    banco.responder("SELECT status FROM responsaveis WHERE id", [{'status': 'aprovado'}])

    portal.get("/pais/dashboard")

    assert banco.sql_com("SELECT status FROM responsaveis") != []


def test_conta_bloqueada_recusa_a_requisicao_do_portal(portal, banco):
    banco.responder("SELECT status FROM responsaveis WHERE id", [{'status': 'bloqueado'}])

    r = portal.get("/pais/dashboard")

    assert r.status_code == 302
    assert "/pais/login" in r.headers['Location']


def test_conta_bloqueada_limpa_a_sessao(portal, banco):
    """Não basta recusar esta request: a sessão inteira tem que cair, senão a próxima tela
    tentaria de novo com a mesma credencial."""
    banco.responder("SELECT status FROM responsaveis WHERE id", [{'status': 'bloqueado'}])

    portal.get("/pais/dashboard")

    with portal.session_transaction() as s:
        assert 'pai_id' not in s
        assert 'pai_email' not in s


def test_conta_ainda_pendente_tambem_e_recusada(portal, banco):
    """Mesma regra do login: só 'aprovado' dá acesso."""
    banco.responder("SELECT status FROM responsaveis WHERE id", [{'status': 'pendente'}])

    assert portal.get("/pais/dashboard").status_code == 302


def test_conta_apagada_com_sessao_aberta_e_recusada(portal, banco):
    """Linha ausente conta como inativa — fail-closed."""
    banco.responder("SELECT status FROM responsaveis WHERE id", [])

    assert portal.get("/pais/dashboard").status_code == 302


def test_conta_ativa_continua_navegando(portal, banco):
    banco.responder("SELECT status FROM responsaveis WHERE id", [{'status': 'aprovado'}])

    assert portal.get("/pais/dashboard").status_code == 200


def test_encerramento_por_conta_inativa_fica_na_auditoria(portal, banco):
    banco.responder("SELECT status FROM responsaveis WHERE id", [{'status': 'bloqueado'}])

    portal.get("/pais/dashboard")

    acao, detalhes, _ip = banco.auditorias()[0]
    assert acao == "SESSÃO ENCERRADA"
    assert "não está mais ativa" in detalhes


# ---------------------------------------------------------------- o bloqueio alcança quem já entrou

def test_bloqueio_alcanca_a_sessao_aberta_assim_que_a_janela_do_cache_passa(portal, banco):
    """O cenário do diagnóstico, do começo ao fim: o responsável está navegando, a escola
    bloqueia a conta, e a requisição seguinte já é recusada."""
    banco.responder("SELECT status FROM responsaveis WHERE id", [{'status': 'aprovado'}])
    assert portal.get("/pais/dashboard").status_code == 200

    banco.responder("SELECT status FROM responsaveis WHERE id", [{'status': 'bloqueado'}])
    _expirar_o_cache()

    assert portal.get("/pais/dashboard").status_code == 302


def test_a_janela_de_bloqueio_e_de_segundos_e_nao_de_horas(portal):
    """O defeito era a janela ter o tamanho da sessão (8h) / da inatividade (30 min). Qualquer
    valor aqui na casa dos minutos volta a ser o mesmo problema, mais devagar."""
    assert middleware._TTL_STATUS_RESPONSAVEL <= 60


def test_dentro_da_janela_o_status_nao_e_reconsultado_a_cada_clique(portal, banco):
    """A checagem não pode custar uma consulta por página no pico das 15h."""
    banco.responder("SELECT status FROM responsaveis WHERE id", [{'status': 'aprovado'}])

    portal.get("/pais/dashboard")
    portal.get("/pais/dashboard")
    portal.get("/pais/dashboard")

    assert len(banco.sql_com("SELECT status FROM responsaveis")) == 1
