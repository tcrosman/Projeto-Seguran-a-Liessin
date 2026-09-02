# -*- coding: utf-8 -*-
"""Testes do client do banco SQL da escola.

O client real roda contra um SQLite temporário criado com o mesmo schema/seed do banco provisório
(S2E_postgreSQL/escola_provisoria/), então os casos-limite testados aqui são os mesmos que existem
para teste manual: aluno sem responsável, responsável com dois filhos, responsável sem vínculo
ativo e aluno desligado.
"""
import importlib.util
import os
import sqlite3

import pytest

from app.services.school_sql_directory import (
    SchoolSqlDirectoryClient,
    SchoolSqlDirectoryMock,
    get_school_sql_directory,
)

ESCOLA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "escola_provisoria",
)


def _carregar_seed():
    spec = importlib.util.spec_from_file_location("seed_escola", os.path.join(ESCOLA_DIR, "seed.py"))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def banco(tmp_path_factory):
    """Banco SQLite temporário com o schema e os dados de exemplo reais."""
    seed = _carregar_seed()
    caminho = str(tmp_path_factory.mktemp("escola") / "escola.db")
    with open(os.path.join(ESCOLA_DIR, "schema.sql"), encoding="utf-8") as f:
        ddl = f.read()
    conn = sqlite3.connect(caminho)
    conn.executescript(ddl)
    conn.executemany("INSERT INTO alunos (ra, nome, turma, serie, foto_url, ativo) VALUES (?,?,?,?,?,?)", seed.ALUNOS)
    conn.executemany("INSERT INTO responsaveis (email, nome, ativo) VALUES (?,?,?)", seed.RESPONSAVEIS)
    conn.executemany("INSERT INTO vinculos (ra, email, parentesco) VALUES (?,?,?)", seed.VINCULOS)
    conn.commit()
    conn.close()
    return caminho


@pytest.fixture
def client(monkeypatch, banco):
    monkeypatch.setenv("SCHOOL_SQL_ENGINE", "sqlite")
    monkeypatch.setenv("SCHOOL_SQL_DATABASE", banco)
    return SchoolSqlDirectoryClient()


class TestDadosDeAluno:
    def test_aluno_encontrado_traz_todos_os_campos(self, client):
        aluno = client.get_student("2024001")
        assert aluno["nome"] == "Ana Beatriz Souza"
        assert aluno["turma"] == "A"
        assert aluno["serie"] == "6º ano EF"
        assert aluno["foto_url"] is None
        assert sorted(aluno["responsaveis_email"]) == ["mae@teste.com", "pai@teste.com"]

    def test_ra_inexistente_retorna_none(self, client):
        assert client.get_student("0000") is None

    def test_aluno_desligado_nao_e_retornado(self, client):
        assert client.get_student("2024013") is None

    def test_get_students_by_ras_ignora_desconhecidos_e_desligados(self, client):
        result = client.get_students_by_ras(["2024001", "2024002", "2024013", "0000"])
        assert sorted(result.keys()) == ["2024001", "2024002"]

    def test_get_students_by_ras_vazio(self, client):
        assert client.get_students_by_ras([]) == {}


class TestListagem:
    """A tela de alunos abre sem termo de busca — `search_students` devolveria vazio."""

    def test_lista_todos_os_ativos_em_ordem_alfabetica(self, client):
        nomes = [a["nome"] for a in client.list_students(limite=100)]
        assert nomes == sorted(nomes)
        assert len(nomes) == 12          # os 12 ativos do seed

    def test_aluno_desligado_fica_de_fora(self, client):
        assert "Gabriel" not in " ".join(a["nome"] for a in client.list_students(limite=100))

    def test_respeita_o_limite(self, client):
        assert len(client.list_students(limite=3)) == 3

    def test_traz_o_mesmo_formato_da_busca(self, client):
        """Quem consome as duas listas não pode precisar saber de qual veio."""
        assert set(client.list_students(limite=1)[0]) == set(client.search_students("Ana")[0])

    def test_banco_fora_do_ar_devolve_lista_vazia(self, monkeypatch):
        monkeypatch.setenv("SCHOOL_SQL_ENGINE", "sqlite")
        monkeypatch.delenv("SCHOOL_SQL_DATABASE", raising=False)
        assert SchoolSqlDirectoryClient().list_students() == []


class TestBusca:
    def test_busca_por_nome(self, client):
        assert [a["nome"] for a in client.search_students("Ana")] == ["Ana Beatriz Souza", "Mariana Gonçalves"]

    def test_busca_por_ra_parcial(self, client):
        assert len(client.search_students("20240")) == 12  # os 12 ativos

    @pytest.mark.parametrize("termo", ["júlia", "julia", "JULIA", "Júlia"])
    def test_busca_ignora_acento_e_caixa(self, client, termo):
        """Uma secretária buscando 'julia' precisa achar 'JÚLIA MENDES' — o LOWER() do SQLite
        sozinho não faz isso (só cobre ASCII)."""
        assert [a["nome"] for a in client.search_students(termo)] == ["JÚLIA MENDES"]

    def test_busca_vazia_nao_retorna_nada(self, client):
        assert client.search_students("") == []
        assert client.search_students("   ") == []

    def test_busca_nao_acha_aluno_desligado(self, client):
        assert client.search_students("Gabriel") == []


class TestResponsaveis:
    def test_emails_do_aluno(self, client):
        assert sorted(client.get_guardian_emails_for_ra("2024001")) == ["mae@teste.com", "pai@teste.com"]

    def test_aluno_sem_responsavel_retorna_lista_vazia(self, client):
        """Caso-limite de /concluir_saida: não há ninguém para notificar, e isso não pode quebrar."""
        assert client.get_guardian_emails_for_ra("2024012") == []

    def test_responsavel_com_dois_filhos(self, client):
        filhos = client.get_students_for_guardian_email("claudia.almeida@teste.com")
        assert [f["nome"] for f in filhos] == ["Miguel Almeida", "Sofia Almeida"]

    def test_responsavel_com_um_filho(self, client):
        assert len(client.get_students_for_guardian_email("responsavel@teste.com")) == 2

    def test_email_desconhecido_nao_tem_filhos(self, client):
        assert client.get_students_for_guardian_email("ninguem@teste.com") == []


class TestResponsavelReconhecido:
    def test_responsavel_com_vinculo_ativo(self, client):
        assert client.responsavel_reconhecido("pai@teste.com") is True

    def test_normaliza_caixa_e_espacos(self, client):
        assert client.responsavel_reconhecido("  PAI@TESTE.COM  ") is True

    def test_email_desconhecido(self, client):
        assert client.responsavel_reconhecido("estranho@teste.com") is False

    def test_responsavel_sem_nenhum_vinculo(self, client):
        assert client.responsavel_reconhecido("sem.vinculo@teste.com") is False

    def test_responsavel_inativo(self, client):
        assert client.responsavel_reconhecido("bloqueado@teste.com") is False

    def test_responsavel_so_de_aluno_desligado(self, client):
        """Filho saiu da escola -> perde o acesso ao portal."""
        assert client.responsavel_reconhecido("pai.desligado@teste.com") is False


class TestBancoIndisponivel:
    """Banco fora do ar / mal configurado não pode propagar exceção nem liberar acesso."""

    @pytest.fixture
    def quebrado(self, monkeypatch):
        monkeypatch.setenv("SCHOOL_SQL_ENGINE", "sqlite")
        monkeypatch.delenv("SCHOOL_SQL_DATABASE", raising=False)
        return SchoolSqlDirectoryClient()

    def test_retornos_seguros(self, quebrado):
        assert quebrado.get_student("2024001") is None
        assert quebrado.get_students_by_ras(["2024001"]) == {}
        assert quebrado.get_students_for_guardian_email("pai@teste.com") == []
        assert quebrado.search_students("Ana") == []
        assert quebrado.get_guardian_emails_for_ra("2024001") == []

    def test_responsavel_reconhecido_falha_fechado(self, quebrado):
        assert quebrado.responsavel_reconhecido("pai@teste.com") is False

    def test_engine_desconhecido_nao_propaga(self, monkeypatch):
        monkeypatch.setenv("SCHOOL_SQL_ENGINE", "oracle")
        monkeypatch.setenv("SCHOOL_SQL_DATABASE", "x")
        assert SchoolSqlDirectoryClient().responsavel_reconhecido("pai@teste.com") is False


class TestMock:
    def test_mock_continua_funcionando(self):
        assert SchoolSqlDirectoryMock().get_student("2024001")["nome"] == "Ana Beatriz Souza"
        assert SchoolSqlDirectoryMock().responsavel_reconhecido("pai@teste.com") is True
        assert SchoolSqlDirectoryMock().responsavel_reconhecido("estranho@teste.com") is False


def test_factory_exige_escolha_explicita(monkeypatch):
    """Este teste garantia o padrão `mock`. O padrão foi removido: sem a variável, um deploy
    subia servindo três alunos de teste como se fossem o cadastro da escola, sem erro nenhum.
    Agora a origem do dado precisa ser declarada — ver tests/test_config_origem_dos_alunos.py."""
    import pytest
    monkeypatch.delenv("SCHOOL_SQL_MOCK", raising=False)
    with pytest.raises(RuntimeError, match="SCHOOL_SQL_MOCK"):
        get_school_sql_directory()


def test_factory_retorna_mock_quando_declarado(monkeypatch):
    monkeypatch.setenv("SCHOOL_SQL_MOCK", "true")
    assert isinstance(get_school_sql_directory(), SchoolSqlDirectoryMock)


def test_factory_retorna_client_real_quando_mock_false(monkeypatch):
    monkeypatch.setenv("SCHOOL_SQL_MOCK", "false")
    client = get_school_sql_directory()
    assert isinstance(client, SchoolSqlDirectoryClient)
    assert not isinstance(client, SchoolSqlDirectoryMock)
