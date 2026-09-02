# -*- coding: utf-8 -*-
"""As duas telas de login sempre tiveram rate limit; as três rotas públicas restantes não tinham
nenhum, e são justamente as que custam caro sem autenticação: cada POST manda e-mail e, no
autocadastro, consulta o banco SQL da instituição.

O ponto que estes testes protegem, e que a versão antiga do rate_limit não cobria: aqui a
tentativa é contada SEMPRE, não só quando "falha". Contar apenas o erro deixaria livre exatamente
quem varre endereços desconhecidos — que é o abuso que se quer barrar.
"""
from unittest.mock import patch

import pytest


class _EscolaReconhece:
    def responsavel_reconhecido(self, email):
        return True

    def get_students_for_guardian_email(self, email):
        return []


# ------------------------------------------------- /esqueci_senha (funcionários)

def test_reset_de_funcionario_conta_a_tentativa_mesmo_com_email_inexistente(cliente, banco, correio):
    """O atacante testa endereços que não existem. Se só a "falha" fosse contada, ele nunca
    esbarraria em limite nenhum."""
    banco.responder("SELECT id FROM usuarios WHERE LOWER(email)", [])

    cliente.post("/esqueci_senha", data={'email': 'nao-existe@escola.br'})

    escopos = [e for e, _c in banco.tentativas_registradas()]
    assert 'reset_conta' in escopos and 'reset_ip' in escopos
    assert correio.enviados == []


def test_reset_de_funcionario_bloqueado_nao_manda_email_nem_cria_token(cliente, banco, correio):
    banco.bloquear('reset_conta')
    banco.responder("SELECT id FROM usuarios WHERE LOWER(email)", [{'id': 1}])

    r = cliente.post("/esqueci_senha", data={'email': 'admin@escola.br'})

    assert r.status_code == 200
    assert "Muitas solicita" in r.get_data(as_text=True)
    assert correio.enviados == []
    assert banco.sql_com("INSERT INTO reset_tokens") == []


def test_reset_de_funcionario_liberado_segue_funcionando(cliente, banco, correio):
    """O conserto não pode quebrar o fluxo legítimo."""
    banco.responder("SELECT id FROM usuarios WHERE LOWER(email)", [{'id': 1}])

    r = cliente.post("/esqueci_senha", data={'email': 'admin@escola.br'})

    assert banco.sql_com("INSERT INTO reset_tokens") != []
    assert correio.destinatarios() == ['admin@escola.br']
    # A resposta continua neutra: não confirma se o e-mail existe.
    assert "Se este email estiver cadastrado" in r.get_data(as_text=True)


def test_bloqueio_por_ip_vale_mesmo_com_email_diferente_a_cada_vez(cliente, banco, correio):
    """Trocar o e-mail a cada request contorna o limite por conta — o limite por IP é o que
    segura a varredura."""
    banco.bloquear('reset_ip')
    banco.responder("SELECT id FROM usuarios WHERE LOWER(email)", [{'id': 1}])

    cliente.post("/esqueci_senha", data={'email': 'outro-endereco@escola.br'})

    assert correio.enviados == []


# ------------------------------------------------- /pais/esqueci_senha

def test_reset_de_responsavel_bloqueado_nao_manda_email(cliente, banco, correio):
    banco.bloquear('pais_reset_conta')
    banco.responder("SELECT id FROM responsaveis", [{'id': 3}])

    r = cliente.post("/pais/esqueci_senha", data={'email': 'mae@teste.com'})

    assert "Muitas solicita" in r.get_data(as_text=True)
    assert correio.enviados == []
    assert banco.sql_com("INSERT INTO reset_tokens_pais") == []


def test_reset_de_responsavel_liberado_segue_funcionando(cliente, banco, correio):
    banco.responder("SELECT id FROM responsaveis", [{'id': 3}])

    cliente.post("/pais/esqueci_senha", data={'email': 'mae@teste.com'})

    assert banco.sql_com("INSERT INTO reset_tokens_pais") != []
    assert correio.destinatarios() == ['mae@teste.com']


# ------------------------------------------------- /pais/cadastro

def test_cadastro_bloqueado_nao_consulta_o_banco_da_escola(cliente, banco):
    """O limite precisa vir ANTES da consulta externa: é ela o recurso que se quer proteger, e é
    a resposta dela que revela se o e-mail é de um responsável da escola."""
    banco.bloquear('pais_cadastro_ip')
    escola = _EscolaReconhece()

    with patch("app.api.pais.get_school_sql_directory", return_value=escola) as consulta:
        r = cliente.post("/pais/cadastro", data={
            'nome': 'Fulano', 'email': 'alvo@teste.com',
            'senha': 'Senha@Forte1', 'confirmar': 'Senha@Forte1',
        })

    assert "Muitas tentativas" in r.get_data(as_text=True)
    assert consulta.call_count == 0
    assert banco.sql_com("INSERT INTO responsaveis") == []


def test_cadastro_conta_a_tentativa_antes_de_validar_o_formulario(cliente, banco):
    """Um formulário incompleto sai da rota mais cedo. Se a contagem viesse depois da validação,
    bastaria mandar lixo para varrer sem nunca ser contabilizado."""
    cliente.post("/pais/cadastro", data={'nome': '', 'email': '', 'senha': ''})

    assert [e for e, _c in banco.tentativas_registradas()] == ['pais_cadastro_ip']


def test_cadastro_legitimo_continua_passando(cliente, banco, correio):
    banco.responder("SELECT id FROM responsaveis WHERE email", [])
    banco.responder("SELECT email FROM usuarios WHERE role = 'admin'",
                    [{'email': 'admin@escola.br'}])

    with patch("app.api.pais.get_school_sql_directory", return_value=_EscolaReconhece()):
        r = cliente.post("/pais/cadastro", data={
            'nome': 'Maria Souza', 'email': 'mae@teste.com',
            'senha': 'Senha@Forte1', 'confirmar': 'Senha@Forte1',
        })

    assert r.status_code == 200
    assert banco.sql_com("INSERT INTO responsaveis") != []
    assert correio.destinatarios() == ['admin@escola.br']


# ------------------------------------------------- o conserto não pode estragar nada

@pytest.mark.parametrize("rota,dados", [
    ("/esqueci_senha", {'email': 'a@b.com'}),
    ("/pais/esqueci_senha", {'email': 'a@b.com'}),
    ("/pais/cadastro", {'nome': 'X', 'email': 'a@b.com', 'senha': 'Senha@Forte1',
                        'confirmar': 'Senha@Forte1'}),
])
def test_rate_limit_nao_abre_conexao_aninhada(cliente, banco, rota, dados):
    """rate_limit abre a própria conexão; chamá-lo de dentro de um `with get_db()` reteria duas
    ao mesmo tempo e travaria o pool sob concorrência."""
    with patch("app.api.pais.get_school_sql_directory", return_value=_EscolaReconhece()):
        cliente.post(rota, data=dados)

    assert banco.profundidade_maxima == 1


def test_bloqueio_de_reset_nao_bloqueia_o_login(cliente, banco):
    """Escopos separados: quem estourou o limite de redefinição não pode ficar impedido de
    simplesmente entrar com a senha correta."""
    banco.bloquear('reset_conta')
    banco.bloquear('reset_ip')
    banco.responder("SELECT id, role, username, password FROM usuarios", [])

    r = cliente.post("/", data={'u': 'admin', 's': 'errada'})

    assert "Muitas solicita" not in r.get_data(as_text=True)
    assert "Usuário ou senha incorretos" in r.get_data(as_text=True)


def test_get_nas_rotas_publicas_nao_consome_o_limite(cliente, banco):
    """Abrir a tela não é tentativa; contar o GET esgotaria o limite de quem só visitou a página."""
    cliente.get("/esqueci_senha")
    cliente.get("/pais/esqueci_senha")
    cliente.get("/pais/cadastro")

    assert banco.tentativas_registradas() == []
