# -*- coding: utf-8 -*-
"""A6 — TRUST_PROXY vinha ligado por padrão, e isso torna o IP forjável.

ProxyFix só corrige alguma coisa quando existe mesmo um proxy à frente. Ligado sem proxy — num
docker local, numa VM, num servidor da própria escola — ele passa a acreditar no
`X-Forwarded-For` que o próprio cliente enviou. Duas consequências:

  * todo limite por IP cai com um cabeçalho diferente a cada requisição. É a última defesa que
    restava contra a força bruta do 2FA (ver A5);
  * o `ip` gravado na tabela `auditoria` vira texto escolhido pelo atacante, contaminando a
    trilha de quem pediu a saída de uma criança.

O padrão passa a ser false; `true` fica só no render.yaml, onde o proxy é conhecido.
"""
import os
from unittest.mock import patch

import pytest

from tests.apoio import BancoFalso
from app.core import database, migrations

RAIZ_API = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _app_com(monkeypatch, trust_proxy):
    if trust_proxy is None:
        monkeypatch.delenv('TRUST_PROXY', raising=False)
    else:
        monkeypatch.setenv('TRUST_PROXY', trust_proxy)
    banco = BancoFalso()
    with patch.object(database, 'get_db', banco.get_db), \
         patch.object(migrations, 'get_db', banco.get_db), \
         patch.object(database, '_get_pool',
                      lambda: (_ for _ in ()).throw(AssertionError('banco real'))):
        from app import create_app
        aplicacao = create_app()
    aplicacao.config['WTF_CSRF_ENABLED'] = False
    aplicacao.config['TESTING'] = True
    return aplicacao, banco


def _ip_registrado(aplicacao, banco, cabecalho):
    """Faz um login que falha e devolve o IP que foi parar na trilha de auditoria."""
    with patch.object(database, 'get_db', banco.get_db), \
         patch('app.api.web.get_db', banco.get_db), \
         patch('app.core.audit_logger.get_db', banco.get_db), \
         patch('app.core.rate_limit.get_db', banco.get_db):
        cliente = aplicacao.test_client()
        cliente.post("/", data={'u': 'alguem', 's': 'errada'},
                     headers={'X-Forwarded-For': cabecalho})
    ips = [ip for _acao, _det, ip in banco.auditorias()]
    return ips[-1] if ips else None


def test_sem_a_variavel_o_cabecalho_do_cliente_e_ignorado(monkeypatch):
    """O caso do deploy fora do Render: quem manda o cabeçalho é o próprio atacante."""
    aplicacao, banco = _app_com(monkeypatch, None)

    assert _ip_registrado(aplicacao, banco, '203.0.113.9') != '203.0.113.9'


def test_com_a_variavel_ligada_o_cabecalho_volta_a_valer(monkeypatch):
    """Atrás de um proxy de verdade o IP real só existe no X-Forwarded-For — a correção não pode
    ter quebrado o caso do Render."""
    aplicacao, banco = _app_com(monkeypatch, 'true')

    assert _ip_registrado(aplicacao, banco, '203.0.113.9') == '203.0.113.9'


def test_o_padrao_do_codigo_e_desligado(monkeypatch):
    aplicacao, _banco = _app_com(monkeypatch, None)
    from werkzeug.middleware.proxy_fix import ProxyFix

    assert not isinstance(aplicacao.wsgi_app, ProxyFix)


def test_o_render_continua_declarando_o_proxy():
    """Lá o proxy existe, e sem TRUST_PROXY=true o rate limit veria o IP do proxy para todos."""
    with open(os.path.join(RAIZ_API, 'render.yaml'), encoding='utf-8') as f:
        render = f.read()

    assert 'TRUST_PROXY' in render
    trecho = render[render.index('TRUST_PROXY'):]
    assert 'value: "true"' in trecho.split('- key:')[0]


def test_o_exemplo_de_env_nao_induz_a_ligar_sem_proxy():
    with open(os.path.join(RAIZ_API, '.env.example'), encoding='utf-8') as f:
        exemplo = f.read()

    assert 'TRUST_PROXY=false' in exemplo
