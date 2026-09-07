# -*- coding: utf-8 -*-
"""A8 — duas saídas pendentes para o mesmo aluno no mesmo dia.

/registrar_saida fazia SELECT COUNT(*) e depois INSERT, em READ COMMITTED e sem nenhuma
restrição no banco. Entre as duas instruções cabe outra transação inteira: as duas leem
"pendente = 0" e as duas inserem. Basta um duplo clique em "Registrar" ou dois funcionários
registrando ao mesmo tempo.

O resultado é a portaria vendo duas autorizações idênticas. A primeira é liberada; a segunda
continua pendente e serve para liberar a MESMA criança uma segunda vez, para outro
acompanhante, no mesmo dia.

A garantia tem que estar no banco: índice único parcial sobre (ra, data_saida) onde
status = 'pendente'.
"""
import pytest
from unittest.mock import patch

from psycopg2.errors import UniqueViolation

from tests.apoio import BancoFalso, DiretorioFalso
from app.core import database, migrations
from app.core.tempo import hoje

ANA = {"ra": "2024001", "nome": "Ana Beatriz Souza", "turma": "A", "serie": "6º ano EF",
       "foto_url": None, "responsaveis_email": ["mae@teste.com"]}

FORMULARIO = {
    'ra': '2024001', 'data_saida': hoje(), 'horario': '15:00', 'motivo': 'Consulta',
    'responsavel_escola': 'Secretaria', 'tipo_saida': 'sozinho',
}


@pytest.fixture
def secretaria(cliente):
    with cliente.session_transaction() as s:
        s['user_id'] = 2
        s['role'] = 'basico'
        s['username'] = 'secretaria_teste'
    return cliente


def _registrar(sessao, **extra):
    with patch("app.api.web.get_school_sql_directory",
               return_value=DiretorioFalso({'2024001': ANA})):
        return sessao.post("/registrar_saida", data=dict(FORMULARIO, **extra),
                           follow_redirects=True)


# ---------------------------------------------------------------- a restrição existe no banco

def test_a_migracao_cria_o_indice_unico_parcial():
    banco = BancoFalso()
    with patch.object(database, 'get_db', banco.get_db):
        database.aplicar_migracoes()

    (sql, _p), = banco.sql_com("uq_saidas_pendente_por_dia")
    assert "CREATE UNIQUE INDEX" in sql
    assert "ON saidas (ra, data_saida)" in sql
    assert "WHERE status = 'pendente'" in sql


def test_o_indice_e_parcial_para_nao_travar_o_historico():
    """Um aluno pode ter várias saídas concluídas na mesma data, e as 'nao_realizada' também
    precisam coexistir — só as pendentes se excluem."""
    banco = BancoFalso()
    with patch.object(database, 'get_db', banco.get_db):
        database.aplicar_migracoes()

    (sql, _p), = banco.sql_com("uq_saidas_pendente_por_dia")
    assert "WHERE status = 'pendente'" in sql


def test_base_com_duplicatas_antigas_nao_derruba_o_boot():
    """A única migração que pode falhar por dado já existente. Sem SAVEPOINT, ela abortaria a
    transação inteira e levaria junto o schema todo."""
    banco = BancoFalso()
    banco.falhar_em("uq_saidas_pendente_por_dia",
                    UniqueViolation("could not create unique index"))

    with patch.object(database, 'get_db', banco.get_db):
        database.aplicar_migracoes()   # não levanta

    assert banco.sql_com("ROLLBACK TO SAVEPOINT idx_saida_unica") != []
    # e o resto do schema continua sendo aplicado
    assert banco.sql_com("CREATE INDEX IF NOT EXISTS idx_saidas_data") != []


# ---------------------------------------------------------------- a rota trata a colisão

def test_insercao_concorrente_vira_mensagem_e_nao_erro_500(secretaria, banco):
    """A outra transação venceu a corrida entre o COUNT e o INSERT."""
    banco.responder("SELECT COUNT(*) as total FROM saidas", [{'total': 0}])
    banco.falhar_em("INSERT INTO saidas (ra, turma",
                    UniqueViolation('duplicate key value violates unique constraint '
                                    '"uq_saidas_pendente_por_dia"'))

    r = _registrar(secretaria)

    assert r.status_code == 200
    assert "já tem uma saída pendente" in r.get_data(as_text=True)


def test_a_mensagem_de_duplicata_nao_vaza_o_erro_do_banco(secretaria, banco):
    banco.responder("SELECT COUNT(*) as total FROM saidas", [{'total': 0}])
    banco.falhar_em("INSERT INTO saidas (ra, turma",
                    UniqueViolation('duplicate key value violates unique constraint '
                                    '"uq_saidas_pendente_por_dia"'))

    corpo = _registrar(secretaria).get_data(as_text=True)

    assert "uq_saidas_pendente_por_dia" not in corpo
    assert "duplicate key" not in corpo


def test_a_saida_duplicada_nao_e_confirmada_na_tela(secretaria, banco):
    """O pior desfecho seria dizer "Saída registrada!" para algo que não foi gravado."""
    banco.responder("SELECT COUNT(*) as total FROM saidas", [{'total': 0}])
    banco.falhar_em("INSERT INTO saidas (ra, turma", UniqueViolation("duplicate key"))

    assert "Saída registrada!" not in _registrar(secretaria).get_data(as_text=True)


def test_o_caminho_normal_continua_registrando(secretaria, banco):
    banco.responder("SELECT COUNT(*) as total FROM saidas", [{'total': 0}])

    r = _registrar(secretaria)

    assert "Saída registrada!" in r.get_data(as_text=True)
    assert banco.sql_com("INSERT INTO saidas (ra, turma") != []


def test_o_count_continua_dando_a_mensagem_no_caso_comum(secretaria, banco):
    """O índice é a garantia; o COUNT é a mensagem boa para quem está no balcão."""
    banco.responder("SELECT COUNT(*) as total FROM saidas", [{'total': 1}])

    r = _registrar(secretaria)

    assert "já tem uma saída pendente" in r.get_data(as_text=True)
    assert banco.sql_com("INSERT INTO saidas (ra, turma") == []


def test_a_mensagem_cita_a_data_e_nao_diz_hoje_sempre(secretaria, banco):
    """A saída pode ser registrada para outro dia; dizer "para hoje" era simplesmente falso."""
    banco.responder("SELECT COUNT(*) as total FROM saidas", [{'total': 1}])

    corpo = _registrar(secretaria, data_saida='2026-12-15').get_data(as_text=True)

    assert "2026-12-15" in corpo
