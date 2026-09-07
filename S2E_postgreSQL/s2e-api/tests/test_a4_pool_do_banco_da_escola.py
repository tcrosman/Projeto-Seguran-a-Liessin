# -*- coding: utf-8 -*-
"""A4 — o banco da escola não tinha pool: uma conexão aberta e fechada por consulta.

E as consultas vêm em grupo, não isoladas: get_student são 2 conexões (por causa de
_emails_por_ra), _nome_e_emails_para_saida são 3, /alunos são 2, e o autocomplete da portaria
são 2 a cada tecla. Numa tarde de saídas o handshake dominava a latência da consulta mais lenta
do sistema.

Além do pool, o autocomplete ganhou debounce de 300 ms e mínimo de 3 caracteres: cada busca é um
LIKE '%...%' sobre nome normalizado, que nenhum índice B-tree comum atende.
"""
import os

import pytest

from app.services import school_sql_directory as diretorio
from app.services.school_sql_directory import (SchoolSqlDirectoryClient,
                                                SchoolSqlIndisponivel)


class _CursorFalso:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        self._conn.consultas.append(sql)

    def fetchall(self):
        return []


class _ConexaoFalsa:
    def __init__(self):
        self.closed = False
        self.consultas = []
        self.rollbacks = 0

    def cursor(self):
        return _CursorFalso(self)

    def rollback(self):
        self.rollbacks += 1


class _PoolFalso:
    """Registra o que o cliente faz com o pool. Uma instância por criação de pool."""
    criados = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.conexao = _ConexaoFalsa()
        self.emprestimos = 0
        self.devolucoes = 0
        self.fechadas = 0
        _PoolFalso.criados.append(self)

    def getconn(self):
        self.emprestimos += 1
        return self.conexao

    def putconn(self, conn, close=False):
        self.devolucoes += 1
        if close:
            self.fechadas += 1

    def closeall(self):
        pass


@pytest.fixture(autouse=True)
def _pool_limpo(monkeypatch):
    """O pool é global de módulo — sem zerar, um teste herda o dublê de outro."""
    _PoolFalso.criados = []
    diretorio._reiniciar_pool()
    monkeypatch.setattr('psycopg2.pool.ThreadedConnectionPool', _PoolFalso)
    yield
    diretorio._reiniciar_pool()


@pytest.fixture
def cliente_postgres(monkeypatch):
    monkeypatch.setenv('SCHOOL_SQL_ENGINE', 'postgres')
    monkeypatch.setenv('SCHOOL_SQL_HOST', 'banco.da.escola')
    monkeypatch.setenv('SCHOOL_SQL_DATABASE', 'academico')
    return SchoolSqlDirectoryClient()


# ---------------------------------------------------------------- o pool existe e é reusado

def test_consultas_seguidas_nao_abrem_um_pool_novo(cliente_postgres):
    cliente_postgres._consultar("SELECT 1")
    cliente_postgres._consultar("SELECT 2")
    cliente_postgres._consultar("SELECT 3")

    assert len(_PoolFalso.criados) == 1


def test_o_pool_e_compartilhado_entre_instancias_do_cliente(cliente_postgres):
    """get_school_sql_directory() devolve uma instância nova a cada chamada; um pool por
    instância não pouparia conexão nenhuma."""
    cliente_postgres._consultar("SELECT 1")
    SchoolSqlDirectoryClient()._consultar("SELECT 2")

    assert len(_PoolFalso.criados) == 1
    assert _PoolFalso.criados[0].emprestimos == 2


def test_toda_conexao_emprestada_volta_para_o_pool(cliente_postgres):
    cliente_postgres._consultar("SELECT 1")
    cliente_postgres._consultar("SELECT 2")

    pool = _PoolFalso.criados[0]
    assert pool.emprestimos == pool.devolucoes == 2


def test_a_transacao_implicita_e_encerrada_antes_de_devolver(cliente_postgres):
    """São só SELECTs, mas o psycopg2 abre transação no primeiro execute. Sem encerrar, a
    conexão volta ao pool "idle in transaction", segurando recursos do banco da escola."""
    cliente_postgres._consultar("SELECT 1")

    assert _PoolFalso.criados[0].conexao.rollbacks == 1


def test_consulta_que_falha_devolve_a_conexao_e_libera_a_vaga(cliente_postgres):
    """Senão o pool esvazia sozinho a cada erro e a portaria trava depois de algumas falhas."""
    class _CursorQueQuebra(_CursorFalso):
        def execute(self, sql, params=None):
            raise RuntimeError("banco da escola caiu no meio da query")

    pool_original = None
    cliente_postgres._consultar("SELECT 1")
    pool_original = _PoolFalso.criados[0]
    pool_original.conexao.cursor = lambda: _CursorQueQuebra(pool_original.conexao)

    for _ in range(10):
        with pytest.raises(RuntimeError):
            cliente_postgres._consultar("SELECT 2")

    assert pool_original.emprestimos == pool_original.devolucoes
    # A 11ª consulta ainda encontra vaga: o semáforo foi liberado em todas as falhas.
    pool_original.conexao.cursor = lambda: _CursorFalso(pool_original.conexao)
    cliente_postgres._consultar("SELECT 3")


def test_o_pool_carrega_os_limites_de_tempo_do_banco_da_escola(cliente_postgres):
    """connect_timeout e statement_timeout saíram de _conectar() e não podem ter ficado
    para trás: sem eles, o banco da escola fora do ar pendura a request até o timeout de TCP."""
    cliente_postgres._consultar("SELECT 1")

    kwargs = _PoolFalso.criados[0].kwargs
    assert kwargs['connect_timeout'] == cliente_postgres._timeout
    assert 'statement_timeout' in kwargs['options']
    assert kwargs['maxconn'] == diretorio.POOL_MAX


def test_engine_desconhecido_nao_cria_pool(monkeypatch):
    monkeypatch.setenv('SCHOOL_SQL_ENGINE', 'oracle')
    monkeypatch.setenv('SCHOOL_SQL_DATABASE', 'x')

    with pytest.raises(SchoolSqlIndisponivel):
        SchoolSqlDirectoryClient().responsavel_reconhecido("pai@teste.com")
    assert _PoolFalso.criados == []


def test_sqlite_nao_usa_pool(monkeypatch, tmp_path):
    """O banco provisório é local e de desenvolvimento: abrir o arquivo é barato, e conexão de
    sqlite compartilhada entre threads pede cuidado que não se paga aqui."""
    monkeypatch.setenv('SCHOOL_SQL_ENGINE', 'sqlite')
    monkeypatch.setenv('SCHOOL_SQL_DATABASE', str(tmp_path / 'escola.db'))
    cliente = SchoolSqlDirectoryClient()

    cliente._consultar("SELECT 1")

    assert _PoolFalso.criados == []


# ---------------------------------------------------------------- autocomplete da portaria

def _template_de_registro():
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    caminho = os.path.join(raiz, 'app', 'templates', 'departures', 'register.html')
    with open(caminho, encoding='utf-8') as f:
        return f.read()


def test_o_autocomplete_espera_pelo_menos_300ms():
    import re
    html = _template_de_registro()
    esperas = [int(m) for m in re.findall(r'\}, (\d+)\);', html)]

    assert esperas, "o debounce do autocomplete sumiu do template"
    assert max(esperas) >= 300


def test_o_autocomplete_exige_pelo_menos_3_caracteres():
    """Com 2 caracteres, digitar um nome dispara a varredura da tabela de alunos várias vezes."""
    assert "q.length < 3" in _template_de_registro()
