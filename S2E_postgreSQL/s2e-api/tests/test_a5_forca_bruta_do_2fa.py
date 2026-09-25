# -*- coding: utf-8 -*-
"""A5 — o contador de tentativas do 2FA era zerável à vontade.

`tokens_2fa.tentativas` parecia o limite por conta, mas não era: ao queimar o código depois de 5
erros a LINHA é apagada, e /pais/login apaga os tokens abertos do responsável antes de inserir o
novo. Ou seja, não existia contador por conta que sobrevivesse à regeneração — login, 5
palpites, login de novo, mais 5, indefinidamente.

O único freio restante era PAIS_2FA_IP (30 falhas / 15 min), que é só por IP: cerca de 2.160
palpites por dia por IP, e com IPs rotativos — ou um único /64 de IPv6, onde "por IP" não
significa nada — o espaço de 10^6 cai em poucos dias. Quem já tem a senha (vazamento, reúso)
entra no portal de um menor.

O conserto é um contador por responsavel_id, persistido em rate_limit_falhas, independente do
ciclo de vida do token.
"""
import pytest

from app.core import rate_limit


@pytest.fixture
def meio_do_login(cliente):
    """Sessão parcial entre a senha e o 2FA — é onde /pais/verificar atua."""
    with cliente.session_transaction() as s:
        s['pai_temp_id'] = 3
        s['pai_temp_email'] = 'mae@teste.com'
        s['pai_temp_nome'] = 'Maria Souza'
    return cliente


def _erros_por_conta(banco):
    return banco.tentativas_registradas('pais_2fa_conta')


# ---------------------------------------------------------------- o contador existe e é por conta

def test_o_escopo_por_conta_existe_e_e_independente_do_token():
    assert hasattr(rate_limit, 'PAIS_2FA_CONTA')
    escopo, maximo, janela, bloqueio = rate_limit.PAIS_2FA_CONTA
    assert escopo != rate_limit.PAIS_2FA_IP[0]
    assert maximo <= 20, "um teto alto devolve o espaço de 10^6 para o atacante"


def test_codigo_errado_conta_falha_na_conta_e_nao_so_no_ip(meio_do_login, banco):
    banco.responder("SELECT id, expires_at FROM tokens_2fa", [])
    banco.responder("UPDATE tokens_2fa SET tentativas", [{'tentativas': 1}])

    meio_do_login.post("/pais/verificar", data={'codigo': '000000'})

    assert _erros_por_conta(banco) == [('pais_2fa_conta', '3')]


def test_o_contador_da_conta_sobrevive_ao_token_queimado(meio_do_login, banco):
    """Queimar o código apaga a linha de tokens_2fa; a falha precisa continuar contada em outro
    lugar, senão o atacante refaz o login e recomeça do zero."""
    banco.responder("SELECT id, expires_at FROM tokens_2fa", [])
    banco.responder("UPDATE tokens_2fa SET tentativas", [{'tentativas': 5}])

    meio_do_login.post("/pais/verificar", data={'codigo': '000000'})

    assert banco.sql_com("DELETE FROM tokens_2fa") != []   # o token foi mesmo queimado
    assert _erros_por_conta(banco) == [('pais_2fa_conta', '3')]


def test_conta_trancada_recusa_a_verificacao(meio_do_login, banco):
    banco.bloquear('pais_2fa_conta')

    r = meio_do_login.post("/pais/verificar", data={'codigo': '123456'})

    corpo = r.get_data(as_text=True)
    assert "temporariamente bloqueada" in corpo
    assert banco.sql_com("SELECT id, expires_at FROM tokens_2fa") == []


def test_conta_trancada_nao_recebe_codigo_novo(cliente, banco, correio):
    """Este é o buraco em si: era refazendo o login que o contador voltava a zero."""
    banco.responder("SELECT id, nome, email, password_hash, status FROM responsaveis",
                    [{'id': 3, 'nome': 'Maria', 'email': 'mae@teste.com',
                      'password_hash': _hash('segredo'), 'status': 'aprovado'}])
    banco.bloquear('pais_2fa_conta')

    r = cliente.post("/pais/login", data={'email': 'mae@teste.com', 'senha': 'segredo'})

    assert "Muitas tentativas de verificação" in r.get_data(as_text=True)
    assert banco.sql_com("INSERT INTO tokens_2fa") == []
    assert correio.enviados == []


def test_verificacao_correta_zera_o_contador_da_conta(meio_do_login, banco):
    """Quem erra e depois acerta não pode ficar com falhas acumuladas pendurando a próxima
    entrada — o limite existe contra o atacante, não contra o responsável."""
    from datetime import timedelta
    from app.core.tempo import agora_utc
    banco.responder("SELECT id, expires_at FROM tokens_2fa",
                    [{'id': 1, 'expires_at': agora_utc() + timedelta(minutes=5)}])
    banco.responder("UPDATE tokens_2fa SET usado = TRUE", rowcount=1)

    meio_do_login.post("/pais/verificar", data={'codigo': '123456'})

    apagados = [p for _s, p in banco.sql_com("DELETE FROM rate_limit_falhas WHERE escopo")]
    assert ('pais_2fa_conta', '3') in [(e, c) for e, c in apagados]


def test_o_limite_por_ip_continua_valendo(meio_do_login, banco):
    """O escopo novo não substitui o antigo: um deles é por conta, o outro é rede de segurança
    contra varredura distribuída."""
    banco.responder("SELECT id, expires_at FROM tokens_2fa", [])
    banco.responder("UPDATE tokens_2fa SET tentativas", [{'tentativas': 1}])

    meio_do_login.post("/pais/verificar", data={'codigo': '000000'})

    assert banco.tentativas_registradas('pais_2fa_ip') != []


def _hash(senha):
    from werkzeug.security import generate_password_hash
    return generate_password_hash(senha, method='pbkdf2:sha256')
