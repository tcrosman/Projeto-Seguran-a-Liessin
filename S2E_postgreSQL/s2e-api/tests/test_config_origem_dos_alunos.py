# -*- coding: utf-8 -*-
"""`SCHOOL_SQL_MOCK` tinha padrão `true`: faltando a variável, o sistema subia sem erro nenhum
servindo três alunos de teste como se fossem o cadastro da escola. E o `render.yaml` não
declarava uma única variável SCHOOL_SQL_*, então era exatamente isso que um deploy produzia.

É o pior formato de falha: nada quebra, e o dado falso passa por real. A escolha da origem
agora é obrigatória — sem ela o app se recusa a subir, como já fazia com uma SECRET_KEY fraca.
"""
import os
import re

import pytest

from app.services.school_sql_directory import (
    SchoolSqlDirectoryClient, SchoolSqlDirectoryMock, get_school_sql_directory,
)

RENDER_YAML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'render.yaml')


@pytest.fixture
def sem_variavel(monkeypatch):
    monkeypatch.delenv('SCHOOL_SQL_MOCK', raising=False)


@pytest.fixture(autouse=True)
def _restaura(monkeypatch):
    """Os demais testes da suíte contam com o mock ativo."""
    yield
    monkeypatch.setenv('SCHOOL_SQL_MOCK', 'true')


# ---------------------------------------------------------------- o defeito em si

def test_sem_a_variavel_o_sistema_se_recusa_a_escolher_sozinho(sem_variavel):
    with pytest.raises(RuntimeError) as erro:
        get_school_sql_directory()

    mensagem = str(erro.value)
    # A mensagem tem que dizer o que fazer, não só que algo falta.
    assert 'SCHOOL_SQL_MOCK=true' in mensagem
    assert 'SCHOOL_SQL_MOCK=false' in mensagem


def test_variavel_vazia_conta_como_ausente(monkeypatch):
    """Variável declarada sem valor no painel da hospedagem chega como string vazia."""
    monkeypatch.setenv('SCHOOL_SQL_MOCK', '')

    with pytest.raises(RuntimeError):
        get_school_sql_directory()


def test_o_app_nao_sobe_sem_a_escolha(sem_variavel, monkeypatch):
    """A checagem tem que ser no boot: descobrir isso na primeira busca da portaria, no meio do
    expediente, aparece como "nenhum aluno encontrado" — não como erro de configuração."""
    from app.core import database, migrations
    from tests.apoio import BancoFalso
    from unittest.mock import patch

    vazio = BancoFalso()
    with patch.object(database, 'get_db', vazio.get_db), \
         patch.object(migrations, 'get_db', vazio.get_db):
        from app import create_app
        with pytest.raises(RuntimeError, match='SCHOOL_SQL_MOCK'):
            create_app()


# ---------------------------------------------------------------- a escolha explícita funciona

@pytest.mark.parametrize("valor,esperado", [
    ('true', SchoolSqlDirectoryMock),
    ('TRUE', SchoolSqlDirectoryMock),
    (' true ', SchoolSqlDirectoryMock),
    ('false', SchoolSqlDirectoryClient),
    ('False', SchoolSqlDirectoryClient),
])
def test_a_escolha_declarada_e_respeitada(monkeypatch, valor, esperado):
    monkeypatch.setenv('SCHOOL_SQL_MOCK', valor)

    assert type(get_school_sql_directory()) is esperado


def test_qualquer_valor_que_nao_seja_true_cai_no_banco_real(monkeypatch):
    """Melhor errar para o lado do banco real: um valor estranho não pode virar dado de teste."""
    monkeypatch.setenv('SCHOOL_SQL_MOCK', 'talvez')

    assert type(get_school_sql_directory()) is SchoolSqlDirectoryClient


# ---------------------------------------------------------------- o deploy declara o que precisa

def test_render_yaml_declara_a_origem_dos_alunos():
    conteudo = open(RENDER_YAML, encoding='utf-8').read()
    declaradas = set(re.findall(r'- key:\s*(SCHOOL_SQL_\w+)', conteudo))

    # Sem estas, o deploy no Render nem sobe — que é o comportamento desejado, mas o arquivo
    # de infraestrutura tem que trazê-las prontas.
    assert {'SCHOOL_SQL_MOCK', 'SCHOOL_SQL_ENGINE', 'SCHOOL_SQL_DATABASE'} <= declaradas


def test_render_yaml_nao_sobe_producao_em_modo_mock():
    conteudo = open(RENDER_YAML, encoding='utf-8').read()
    bloco = re.search(r'- key:\s*SCHOOL_SQL_MOCK\s*\n\s*value:\s*"?(\w+)"?', conteudo)

    assert bloco is not None
    assert bloco.group(1).lower() == 'false'


def test_credenciais_do_banco_da_escola_nao_estao_versionadas():
    conteudo = open(RENDER_YAML, encoding='utf-8').read()
    for chave in ('SCHOOL_SQL_PASSWORD', 'SCHOOL_SQL_USER', 'SCHOOL_SQL_HOST'):
        bloco = re.search(rf'- key:\s*{chave}\s*\n\s*(\w+):', conteudo)
        assert bloco and bloco.group(1) == 'sync', f"{chave} deveria ser sync: false"
