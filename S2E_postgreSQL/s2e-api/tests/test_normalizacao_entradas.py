"""Espaços acidentais nas pontas não devem invalidar identificadores.

Senhas são a exceção deliberada: cada caractere faz parte da credencial e nunca é alterado.
"""
from unittest.mock import patch

from werkzeug.security import generate_password_hash


def test_login_de_funcionario_ignora_espacos_no_usuario(cliente, banco):
    banco.responder(
        "SELECT id, role, username, password FROM usuarios",
        [{
            'id': 7,
            'role': 'admin',
            'username': 'homolog_admin',
            'password': generate_password_hash('Teste@1234', method='pbkdf2:sha256'),
        }],
    )

    resposta = cliente.post(
        "/",
        data={'u': '  homolog_admin  ', 's': 'Teste@1234'},
        follow_redirects=False,
    )

    assert resposta.status_code == 302
    assert resposta.headers['Location'].endswith('/inicio')
    consulta = banco.sql_com("SELECT id, role, username, password FROM usuarios")[0]
    assert consulta[1] == ('homolog_admin',)


def test_login_de_funcionario_nao_altera_espacos_da_senha(cliente, banco):
    banco.responder(
        "SELECT id, role, username, password FROM usuarios",
        [{
            'id': 7,
            'role': 'admin',
            'username': 'homolog_admin',
            'password': generate_password_hash(' Teste@1234 ', method='pbkdf2:sha256'),
        }],
    )

    resposta = cliente.post(
        "/",
        data={'u': 'homolog_admin', 's': ' Teste@1234 '},
        follow_redirects=False,
    )

    assert resposta.status_code == 302


def test_login_de_responsavel_normaliza_email(cliente, banco, correio):
    banco.responder(
        "SELECT id, nome, email, password_hash, status FROM responsaveis",
        [{
            'id': 11,
            'nome': 'Responsável Teste',
            'email': 'responsavel@teste.com',
            'password_hash': generate_password_hash('Teste@1234', method='pbkdf2:sha256'),
            'status': 'ativo',
        }],
    )

    class _Escola:
        def responsavel_reconhecido(self, email):
            return email == 'responsavel@teste.com'

    with patch("app.api.pais.get_school_sql_directory", return_value=_Escola()):
        resposta = cliente.post(
            "/pais/login",
            data={'email': '  RESPONSAVEL@TESTE.COM  ', 'senha': 'Teste@1234'},
            follow_redirects=False,
        )

    assert resposta.status_code == 302
    assert resposta.headers['Location'].endswith('/pais/verificar')
    assert correio.destinatarios() == ['responsavel@teste.com']
