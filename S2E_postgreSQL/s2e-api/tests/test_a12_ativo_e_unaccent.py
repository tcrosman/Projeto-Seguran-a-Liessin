# -*- coding: utf-8 -*-
"""A12 — `ativo = 1` quebra contra BOOLEAN, e a quebra é silenciosa.

As consultas comparavam `ativo = 1` direto, e o render.yaml já fixa SCHOOL_SQL_ENGINE=postgres.
Se a escola expuser `ativo` como BOOLEAN — o normal em PostgreSQL — toda consulta dá
"operator does not exist: boolean = integer". O erro era capturado pelo `except` de cada método e
virava resultado vazio: o app subia, nada além de um warning aparecia, e ninguém conseguia
registrar uma saída às 15h do dia da virada do mock para o banco real.

O mesmo vale para `unaccent`, exigida por _expr_norm() e não instalada por padrão no PostgreSQL:
sem ela toda busca de aluno falhava e a portaria via "nenhum aluno encontrado".
"""
import pytest

from app.services import school_sql_directory as diretorio
from app.services.school_sql_directory import SchoolSqlDirectoryClient


class _Consulta:
    """Captura o SQL montado sem tocar em banco nenhum.

    `unaccent` decide o que a checagem de extensão responde; qualquer outra consulta volta
    vazia, que é o bastante para o SQL montado ser observado.
    """

    def __init__(self, unaccent=False):
        self.sqls = []
        self.params = []
        self.unaccent = unaccent

    def __call__(self, sql, params=()):
        normal = " ".join(sql.split())
        self.sqls.append(normal)
        self.params.append(params)
        if 'pg_extension' in normal:
            return [{"ok": 1}] if self.unaccent else []
        return []


@pytest.fixture(autouse=True)
def _estado_limpo():
    diretorio._reiniciar_pool()
    yield
    diretorio._reiniciar_pool()


@pytest.fixture
def cliente_postgres(monkeypatch):
    monkeypatch.setenv('SCHOOL_SQL_ENGINE', 'postgres')
    monkeypatch.setenv('SCHOOL_SQL_HOST', 'banco.da.escola')
    monkeypatch.setenv('SCHOOL_SQL_DATABASE', 'academico')
    return SchoolSqlDirectoryClient()


@pytest.fixture
def cliente_sqlite(monkeypatch, tmp_path):
    monkeypatch.setenv('SCHOOL_SQL_ENGINE', 'sqlite')
    monkeypatch.setenv('SCHOOL_SQL_DATABASE', str(tmp_path / 'escola.db'))
    return SchoolSqlDirectoryClient()


# ---------------------------------------------------------------- ativo

def test_no_postgres_a_comparacao_de_ativo_nao_e_com_inteiro(cliente_postgres, monkeypatch):
    """`ativo = 1` contra BOOLEAN é erro de operador, não resultado vazio."""
    espiao = _Consulta()
    monkeypatch.setattr(cliente_postgres, '_consultar', espiao)

    cliente_postgres.get_student("2024001")

    sql = espiao.sqls[-1]
    assert "ativo = 1" not in sql
    assert "(ativo)::boolean IS TRUE" in sql


def test_o_cast_aceita_as_duas_formas_que_a_escola_pode_usar(cliente_postgres):
    """1::boolean é true e true::boolean é ele mesmo — um cast só cobre INTEGER e BOOLEAN."""
    assert cliente_postgres._expr_ativo('ativo') == "(ativo)::boolean IS TRUE"


def test_no_sqlite_continua_sendo_0_e_1(cliente_sqlite):
    """O banco provisório não tem tipo booleano; 0/1 é a única forma."""
    assert cliente_sqlite._expr_ativo('ativo') == "ativo = 1"


@pytest.mark.parametrize("chamada", [
    lambda c: c.get_student("2024001"),
    lambda c: c.get_students_by_ras(["2024001"]),
    lambda c: c.get_students_for_guardian_email("mae@teste.com"),
    lambda c: c.list_students(),
    lambda c: c.get_guardian_emails_for_ra("2024001"),
    lambda c: c.responsavel_reconhecido("mae@teste.com"),
])
def test_nenhuma_consulta_ficou_com_a_comparacao_antiga(cliente_postgres, monkeypatch, chamada):
    """Varredura, e não um assert pontual: eram oito lugares, e um esquecido derruba a tela."""
    espiao = _Consulta()
    monkeypatch.setattr(cliente_postgres, '_consultar', espiao)

    chamada(cliente_postgres)

    for sql in espiao.sqls:
        assert "ativo = 1" not in sql


# ---------------------------------------------------------------- unaccent

def test_com_a_extensao_presente_a_busca_ignora_acento(cliente_postgres, monkeypatch):
    espiao = _Consulta(unaccent=True)
    monkeypatch.setattr(cliente_postgres, '_consultar', espiao)

    cliente_postgres.search_students("Julia")

    assert "unaccent(lower(nome))" in espiao.sqls[-1]


def test_sem_a_extensao_a_busca_degrada_em_vez_de_falhar(cliente_postgres, monkeypatch):
    """Antes a consulta inteira estourava com "function unaccent(text) does not exist" e a tela
    dizia "nenhum aluno encontrado"."""
    espiao = _Consulta(unaccent=False)
    monkeypatch.setattr(cliente_postgres, '_consultar', espiao)

    cliente_postgres.search_students("Julia")

    ultimo = espiao.sqls[-1]
    assert "unaccent" not in ultimo
    assert "lower(nome)" in ultimo


def test_sem_a_extensao_o_termo_buscado_tambem_deixa_de_perder_o_acento(cliente_postgres, monkeypatch):
    """Os dois lados têm que normalizar igual: tirar o acento só do termo faz 'julia' procurar
    por 'júlia' e não achar nada."""
    espiao = _Consulta(unaccent=False)
    monkeypatch.setattr(cliente_postgres, '_consultar', espiao)

    cliente_postgres.search_students("JÚLIA")

    termo = espiao.params[-1][0]
    assert termo == "%júlia%"


def test_com_a_extensao_o_termo_perde_o_acento(cliente_postgres, monkeypatch):
    espiao = _Consulta(unaccent=True)
    monkeypatch.setattr(cliente_postgres, '_consultar', espiao)

    cliente_postgres.search_students("JÚLIA")

    assert espiao.params[-1][0] == "%julia%"


def test_a_extensao_e_verificada_uma_vez_so(cliente_postgres, monkeypatch):
    """É propriedade do banco, não da consulta — verificar a cada busca dobraria as idas."""
    espiao = _Consulta(unaccent=True)
    monkeypatch.setattr(cliente_postgres, '_consultar', espiao)

    cliente_postgres.search_students("Ana")
    cliente_postgres.search_students("Bia")
    cliente_postgres.search_students("Caio")

    checagens = [s for s in espiao.sqls if 'pg_extension' in s]
    assert len(checagens) == 1


def test_o_sqlite_nao_pergunta_por_unaccent(cliente_sqlite, monkeypatch):
    """Lá a normalização é a função Python registrada na conexão."""
    espiao = _Consulta()
    monkeypatch.setattr(cliente_sqlite, '_consultar', espiao)

    cliente_sqlite.search_students("Julia")

    assert not any('pg_extension' in s for s in espiao.sqls)
    assert "norm_texto(nome)" in espiao.sqls[-1]
