# -*- coding: utf-8 -*-
"""A13 — a checagem de saúde do banco da escola era cosmética.

O boot só conferia se a classe era o mock. O `__init__` do client não valida nada, e o
RuntimeError por SCHOOL_SQL_HOST/DATABASE ausentes só surgia dentro de `_conectar()` — no meio de
uma requisição, capturado pelo `except` de cada método e transformado em []/{}/None/False. Como
o render.yaml marca essas variáveis como `sync: false` (preenchidas à mão no painel), esquecer
uma fazia o app subir saudável, com health check verde, e toda tela mostrando "nenhum aluno
encontrado". É a mesma classe de falha que a checagem de SCHOOL_SQL_MOCK quis eliminar, e que
passou pela conexão.
"""
import json

import pytest
from unittest.mock import patch

from tests.apoio import BancoFalso
from app.core import database, migrations
from app.services.school_sql_directory import (SchoolSqlDirectoryClient,
                                               SchoolSqlDirectoryMock,
                                               SchoolSqlIndisponivel)


# ---------------------------------------------------------------- verificar_saude

def _cliente_postgres(monkeypatch):
    monkeypatch.setenv('SCHOOL_SQL_ENGINE', 'postgres')
    monkeypatch.setenv('SCHOOL_SQL_HOST', 'banco.da.escola')
    monkeypatch.setenv('SCHOOL_SQL_DATABASE', 'academico')
    return SchoolSqlDirectoryClient()


def test_banco_inacessivel_e_reportado_e_nao_levanta(monkeypatch):
    cliente = _cliente_postgres(monkeypatch)
    monkeypatch.setattr(cliente, '_consultar',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("connection refused")))

    saude = cliente.verificar_saude()

    assert saude['ok'] is False
    assert "não foi possível conectar" in saude['detalhe']


def test_schema_errado_e_pego_alem_da_conexao(monkeypatch):
    """SELECT 1 passa com o schema errado. É a consulta real de alunos que prova que as tabelas
    existem, que os nomes de coluna batem e que a comparação de `ativo` funciona."""
    cliente = _cliente_postgres(monkeypatch)

    def _consultar(sql, params=()):
        if 'SELECT 1 AS ok' in sql:
            return [{'ok': 1}]
        raise RuntimeError('relation "alunos" does not exist')

    monkeypatch.setattr(cliente, '_consultar', _consultar)

    saude = cliente.verificar_saude()

    assert saude['ok'] is False
    assert "consulta de alunos falhou" in saude['detalhe']


def test_banco_saudavel_e_reportado_como_ok(monkeypatch):
    cliente = _cliente_postgres(monkeypatch)
    monkeypatch.setattr(cliente, '_consultar', lambda sql, params=(): [])

    saude = cliente.verificar_saude()

    assert saude['ok'] is True
    assert saude['modo'] == 'sql'


def test_o_mock_se_declara_como_mock():
    """Quem lê o health check tem de conseguir ver que os alunos são dados de teste."""
    saude = SchoolSqlDirectoryMock().verificar_saude()

    assert saude['ok'] is True
    assert saude['modo'] == 'mock'


# ---------------------------------------------------------------- boot

def _subir_app(monkeypatch, diretorio):
    banco = BancoFalso()
    with patch.object(database, 'get_db', banco.get_db), \
         patch.object(migrations, 'get_db', banco.get_db), \
         patch.object(database, '_get_pool',
                      lambda: (_ for _ in ()).throw(AssertionError('banco real'))), \
         patch('app.services.school_sql_directory.get_school_sql_directory',
               return_value=diretorio):
        from app import create_app
        return create_app()


class _DiretorioQuebrado(SchoolSqlDirectoryMock):
    def verificar_saude(self):
        return {'modo': 'sql', 'engine': 'postgres', 'ok': False,
                'detalhe': 'não foi possível conectar: connection refused', 'unaccent': None}


class _DiretorioSaudavel(SchoolSqlDirectoryMock):
    def verificar_saude(self):
        return {'modo': 'sql', 'engine': 'postgres', 'ok': True,
                'detalhe': None, 'unaccent': True}


def test_o_app_se_recusa_a_subir_com_o_banco_da_escola_fora(monkeypatch):
    """Fail-fast deliberado: um erro de configuração precisa aparecer no deploy, não às 15h."""
    with pytest.raises(RuntimeError) as erro:
        _subir_app(monkeypatch, _DiretorioQuebrado())

    assert "Banco SQL da escola não respondeu no boot" in str(erro.value)


def test_a_mensagem_do_boot_diz_o_que_conferir(monkeypatch):
    """Quem lê isso está num deploy quebrado e precisa saber onde olhar."""
    with pytest.raises(RuntimeError) as erro:
        _subir_app(monkeypatch, _DiretorioQuebrado())

    texto = str(erro.value)
    assert "SCHOOL_SQL_HOST" in texto
    assert "connection refused" in texto


def test_com_o_banco_saudavel_o_app_sobe(monkeypatch):
    assert _subir_app(monkeypatch, _DiretorioSaudavel()) is not None


def test_o_modo_mock_continua_subindo_com_aviso(monkeypatch):
    """Desenvolvimento não pode exigir banco da escola de verdade."""
    assert _subir_app(monkeypatch, SchoolSqlDirectoryMock()) is not None


# ---------------------------------------------------------------- /api/v1/health

def _health(cliente, saude_escola):
    from app.api import web
    web._cache_saude = web.TTLCache(ttl_seconds=15, maximo=4)

    class _Diretorio(SchoolSqlDirectoryMock):
        def verificar_saude(self):
            return saude_escola

    with patch("app.api.web.get_school_sql_directory", return_value=_Diretorio()):
        resposta = cliente.get("/api/v1/health")
    return resposta, json.loads(resposta.get_data(as_text=True))


OK_ESCOLA = {'modo': 'sql', 'engine': 'postgres', 'ok': True, 'detalhe': None, 'unaccent': True}
FORA_ESCOLA = {'modo': 'sql', 'engine': 'postgres', 'ok': False,
               'detalhe': 'connection refused para escola.interno:5432 usuario s2e',
               'unaccent': None}


def test_health_reporta_os_dois_bancos_separados(cliente):
    _resposta, corpo = _health(cliente, OK_ESCOLA)

    assert corpo['banco_proprio']['ok'] is True
    assert corpo['banco_da_escola']['ok'] is True
    assert corpo['status'] == 'ok'


def test_health_distingue_qual_dos_dois_caiu(cliente):
    """A reação é diferente: um é chamado para a hospedagem, o outro é telefone para a
    secretaria — e a portaria segue liberando as saídas que já estão na tela."""
    _resposta, corpo = _health(cliente, FORA_ESCOLA)

    assert corpo['banco_proprio']['ok'] is True
    assert corpo['banco_da_escola']['ok'] is False
    assert corpo['status'] == 'degradado'


def test_health_devolve_503_quando_algo_esta_fora(cliente):
    resposta, _corpo = _health(cliente, FORA_ESCOLA)

    assert resposta.status_code == 503


def test_health_nao_vaza_host_nem_usuario_do_banco(cliente):
    """A rota é pública: a mensagem crua do driver carrega host, usuário e nome de banco."""
    resposta, _corpo = _health(cliente, FORA_ESCOLA)

    texto = resposta.get_data(as_text=True)
    assert "escola.interno" not in texto
    assert "connection refused" not in texto


def test_health_mostra_quando_os_alunos_sao_dados_de_teste(cliente):
    _resposta, corpo = _health(cliente, {'modo': 'mock', 'engine': None, 'ok': True,
                                         'detalhe': None, 'unaccent': None})

    assert corpo['banco_da_escola']['modo'] == 'mock'


def test_health_nao_exige_login(cliente):
    """É o monitoramento externo que consome."""
    resposta, _corpo = _health(cliente, OK_ESCOLA)

    assert resposta.status_code != 302


def test_health_nao_martela_o_banco_da_escola_a_cada_chamada(cliente):
    """A rota é pública e cada checagem custa duas consultas a um sistema de terceiro."""
    from app.api import web
    web._cache_saude = web.TTLCache(ttl_seconds=15, maximo=4)
    chamadas = []

    class _Diretorio(SchoolSqlDirectoryMock):
        def verificar_saude(self):
            chamadas.append(1)
            return OK_ESCOLA

    with patch("app.api.web.get_school_sql_directory", return_value=_Diretorio()):
        cliente.get("/api/v1/health")
        cliente.get("/api/v1/health")
        cliente.get("/api/v1/health")

    assert len(chamadas) == 1
