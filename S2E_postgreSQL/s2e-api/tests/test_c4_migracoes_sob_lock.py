# -*- coding: utf-8 -*-
"""C4 — as migrações rodavam no import, nos dois workers, em paralelo, sem lock.

O gunicorn sobe sem `--preload`, então cada worker importa run.py DEPOIS do fork e os dois
executavam ~34 comandos DDL ao mesmo tempo. No PostgreSQL isso não é seguro nem com IF NOT
EXISTS: dois CREATE TABLE IF NOT EXISTS concorrentes dão "duplicate key value violates unique
constraint pg_type_typname_nsp_index", e dois ALTER TABLE na mesma tabela deadlockam. A exceção
matava o worker, o Render reiniciava, e o ciclo se repetia — no cold start do primeiro acesso do
dia, ou num deploy às 14h50. A ironia é que maintenance.py já usava advisory lock para um
problema muito menor.

O conserto tem duas pontas: um advisory lock que serializa o bloco inteiro, e o passo de release
(`python run_migrations.py`) que permite tirar as migrações do boot por completo.
"""
import os
import threading
import time
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from app.core import database, migrations
from tests.apoio import BancoFalso, CursorFalso


def _e_ddl(sql):
    return sql.lstrip().upper().startswith(('CREATE', 'ALTER', 'DROP'))


# ---------------------------------------------------------------- o lock vem antes do DDL

def test_o_lock_e_pedido_antes_de_qualquer_ddl():
    banco = BancoFalso()
    with patch.object(database, 'get_db', banco.get_db):
        database.aplicar_migracoes()

    comandos = [sql for sql, _p in banco.executados]
    assert 'pg_advisory_xact_lock' in comandos[0]
    primeiro_ddl = next(i for i, sql in enumerate(comandos) if _e_ddl(sql))
    assert primeiro_ddl > 0


def test_o_lock_e_de_transacao_e_nao_de_sessao():
    """pg_advisory_lock (sessão) preso numa conexão devolvida ao pool trava todas as migrações
    seguintes daquele worker — é o defeito que maintenance.py já documenta."""
    banco = BancoFalso()
    with patch.object(database, 'get_db', banco.get_db):
        database.aplicar_migracoes()

    (sql, params), = banco.sql_com('pg_advisory_xact_lock')
    assert 'pg_try_advisory' not in sql       # try desiste e serve schema pela metade
    assert params == (database._LOCK_MIGRACOES,)


def test_as_tres_etapas_rodam_na_mesma_transacao():
    """Se cada etapa abrisse a própria transação, o lock de transação da primeira seria liberado
    antes das outras duas — e elas voltariam a correr soltas."""
    banco = BancoFalso()
    with patch.object(database, 'get_db', banco.get_db):
        database.aplicar_migracoes()

    assert banco.profundidade_maxima == 1
    comandos = ' '.join(sql for sql, _p in banco.executados)
    assert 'CREATE TABLE IF NOT EXISTS responsaveis' in comandos   # migrate_database
    assert 'idx_saidas_data' in comandos                           # run_migrations
    assert 'information_schema.tables WHERE table_name = %s' in comandos  # chaves estrangeiras


def test_a_chave_do_lock_nao_colide_com_a_da_manutencao():
    """Chaves iguais fariam a manutenção esperar pela migração e vice-versa, sem necessidade."""
    from app.core import maintenance
    assert database._LOCK_MIGRACOES != maintenance._LOCK_KEY


# ---------------------------------------------------------------- duas execuções concorrentes

class _BancoQueSerializa:
    """get_db() em que `pg_advisory_xact_lock` se comporta como no Postgres: quem chega depois
    espera até a transação de quem chegou antes terminar."""

    def __init__(self):
        self.trava = threading.Lock()
        self.registro_lock = threading.Lock()
        self.eventos = []

    def _anotar(self, texto):
        with self.registro_lock:
            self.eventos.append(texto)

    @contextmanager
    def get_db(self):
        estado = {'travado': False}
        try:
            yield _ConexaoQueSerializa(self, estado)
        finally:
            if estado['travado']:
                self._anotar(f"fim:{threading.current_thread().name}")
                self.trava.release()


class _ConexaoQueSerializa:
    def __init__(self, banco, estado):
        self._banco = banco
        self._estado = estado

    def execute(self, sql, params=None):
        normal = ' '.join(sql.split())
        nome = threading.current_thread().name
        if 'pg_advisory_xact_lock' in normal:
            self._banco.trava.acquire()
            self._estado['travado'] = True
            self._banco._anotar(f"inicio:{nome}")
            return CursorFalso([{'pg_advisory_xact_lock': True}], 1)
        if _e_ddl(normal):
            self._banco._anotar(f"ddl:{nome}")
            time.sleep(0.001)   # dá chance real de intercalar, se o lock não estivesse lá
        return CursorFalso([], 1)

    def commit(self):
        pass

    def rollback(self):
        pass


def test_dois_workers_nao_intercalam_ddl():
    """O cenário do deploy: dois workers importando run.py ao mesmo tempo, depois do fork."""
    banco = _BancoQueSerializa()

    def migrar():
        with patch.object(database, 'get_db', banco.get_db):
            database.aplicar_migracoes()

    fios = [threading.Thread(target=migrar, name=f"worker{i}") for i in (1, 2)]
    for f in fios:
        f.start()
    for f in fios:
        f.join()

    # Cada worker abre com "inicio:", fecha com "fim:", e nenhum DDL do outro aparece no meio.
    dono = None
    for evento in banco.eventos:
        tipo, quem = evento.split(':')
        if tipo == 'inicio':
            assert dono is None, "segundo worker entrou antes de o primeiro sair"
            dono = quem
        elif tipo == 'fim':
            assert dono == quem
            dono = None
        else:
            assert dono == quem, "DDL de um worker rodou dentro do bloco do outro"
    assert banco.eventos.count('inicio:worker1') == 1
    assert banco.eventos.count('inicio:worker2') == 1


# ---------------------------------------------------------------- passo de release

def test_create_app_nao_migra_com_migracoes_no_boot_desligado(monkeypatch):
    """Com o passo de release configurado, o boot não pode repetir o trabalho de schema."""
    banco = BancoFalso()
    monkeypatch.setenv('MIGRACOES_NO_BOOT', 'false')
    with patch.object(database, 'get_db', banco.get_db), \
         patch.object(migrations, 'get_db', banco.get_db), \
         patch.object(database, '_get_pool', lambda: (_ for _ in ()).throw(AssertionError('banco real'))):
        from app import create_app
        create_app()

    assert banco.sql_com('pg_advisory_xact_lock') == []
    assert [sql for sql, _p in banco.executados if _e_ddl(sql)] == []


def test_create_app_migra_por_padrao(monkeypatch):
    """O padrão continua ligado: um ambiente sem passo de release precisa subir com o schema
    aplicado, não sem schema nenhum."""
    banco = BancoFalso()
    monkeypatch.delenv('MIGRACOES_NO_BOOT', raising=False)
    with patch.object(database, 'get_db', banco.get_db), \
         patch.object(migrations, 'get_db', banco.get_db), \
         patch.object(database, '_get_pool', lambda: (_ for _ in ()).throw(AssertionError('banco real'))):
        from app import create_app
        create_app()

    assert banco.sql_com('pg_advisory_xact_lock') != []


def test_o_passo_de_release_nao_constroi_o_app():
    """create_app() dentro do run_migrations.py refazia o mesmo trabalho pelo caminho do boot,
    subia a thread de manutenção e exigia as variáveis SCHOOL_SQL_* só para encerrar em seguida."""
    raiz = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(raiz, 'run_migrations.py'), encoding='utf-8') as f:
        codigo = f.read()

    assert 'aplicar_migracoes' in codigo
    assert 'from app import create_app' not in codigo


def test_render_roda_as_migracoes_como_passo_de_release():
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(raiz, 'render.yaml'), encoding='utf-8') as f:
        render = f.read()

    assert 'preDeployCommand: python run_migrations.py' in render
