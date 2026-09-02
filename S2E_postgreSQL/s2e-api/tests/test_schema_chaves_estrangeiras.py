# -*- coding: utf-8 -*-
"""O schema não tinha nenhuma chave estrangeira. Foi assim que uma solicitação apontando para um
responsável inexistente pôde existir — contando no aviso do /inicio e sumindo da tela de revisão,
sem que o banco tivesse como impedir.

O que estes testes protegem não é a existência das chaves, é a **ação de cada uma**: ela decide o
que acontece com dado operacional quando uma pessoa é apagada. Trocar um SET NULL por CASCADE
aqui apagaria saídas de alunos em silêncio.
"""
import pytest

from app.core.database import _CHAVES_ESTRANGEIRAS


def _acao(tabela, coluna):
    for t, c, _ref, acao in _CHAVES_ESTRANGEIRAS:
        if (t, c) == (tabela, coluna):
            return acao
    raise AssertionError(f"{tabela}.{coluna} não está declarada como chave estrangeira")


# ---------------------------------------------------------------- o registro sobrevive à pessoa

def test_apagar_a_solicitacao_nao_apaga_a_saida():
    """A manutenção apaga solicitações revisadas depois de 30 dias. Com CASCADE ela levaria junto
    a saída do aluno; com RESTRICT ela pararia de rodar."""
    assert _acao('saidas', 'solicitacao_id') == 'SET NULL'


def test_apagar_o_funcionario_nao_apaga_a_saida_que_ele_autorizou():
    """A rota /deletar_usuario existe e é usada. A saída é registro operacional da escola, não
    propriedade de quem clicou o botão."""
    assert _acao('saidas', 'usuario_autorizou') == 'SET NULL'
    assert _acao('solicitacoes_saida', 'revisado_por') == 'SET NULL'


def test_apagar_o_responsavel_nao_apaga_as_solicitacoes_dele():
    """É também a semântica de exclusão de dado pessoal: apaga a pessoa, preserva o registro
    operacional anonimizado."""
    assert _acao('solicitacoes_saida', 'responsavel_id') == 'SET NULL'


# ---------------------------------------------------------------- o que pode ser levado junto

@pytest.mark.parametrize("tabela,coluna", [
    ('tokens_2fa', 'responsavel_id'),
    ('reset_tokens_pais', 'responsavel_id'),
    ('reset_tokens', 'user_id'),
])
def test_tokens_morrem_com_a_conta(tabela, coluna):
    """Material descartável e sensível do próprio dono: não faz sentido sobreviver à conta."""
    assert _acao(tabela, coluna) == 'CASCADE'


# ---------------------------------------------------------------- o que não pode ser apagado

@pytest.mark.parametrize("tabela,coluna", [
    ('saidas', 'aluno'),
    ('solicitacoes_saida', 'aluno_id'),
])
def test_aluno_com_historico_nao_pode_ser_apagado(tabela, coluna):
    """SET NULL aqui deixaria a saída sem dono nenhum — nem RA, nem nome. RESTRICT faz o banco
    recusar a exclusão em vez de produzir um registro impossível de ler."""
    assert _acao(tabela, coluna) == 'RESTRICT'


# ---------------------------------------------------------------- nada de CASCADE onde dói

def test_nenhuma_chave_apaga_saida_ou_solicitacao_em_cascata():
    """Varredura, e não um assert pontual: uma chave nova acrescentada com CASCADE sobre estas
    tabelas apagaria histórico de aluno sem aviso."""
    perigosas = [(t, c, a) for t, c, _ref, a in _CHAVES_ESTRANGEIRAS
                 if t in ('saidas', 'solicitacoes_saida') and a == 'CASCADE']

    assert perigosas == []


def test_toda_chave_declara_a_acao_explicitamente():
    """Sem ON DELETE, o Postgres assume NO ACTION — que é RESTRICT por acidente, não por escolha."""
    validas = {'CASCADE', 'SET NULL', 'RESTRICT'}
    assert all(acao in validas for _t, _c, _ref, acao in _CHAVES_ESTRANGEIRAS)


def test_as_colunas_declaradas_existem_no_codigo():
    """Uma chave sobre coluna que não existe some no log e ninguém percebe."""
    fonte = open('app/core/database.py', encoding='utf-8').read()
    for tabela, coluna, referida, _acao_ in _CHAVES_ESTRANGEIRAS:
        assert coluna in fonte, f"{tabela}.{coluna} não aparece no schema"
        assert referida in fonte, f"tabela referida {referida} não aparece no schema"


# ---------------------------------------------------------------- a migração é segura de repetir

def test_a_migracao_nao_apaga_solicitacao_para_criar_a_chave(banco):
    """O caminho fácil seria apagar as linhas órfãs. São 10 solicitações — o histórico inteiro de
    pedidos dos pais. A migração põe o vínculo em branco e preserva as linhas."""
    from app.core.database import aplicar_chaves_estrangeiras
    from unittest.mock import patch
    from app.core import database

    banco.responder("FROM information_schema.tables", [{'?column?': 1}])   # tabelas existem
    banco.responder("FROM information_schema.table_constraints", [])       # chaves ainda não

    with patch.object(database, 'get_db', banco.get_db):
        aplicar_chaves_estrangeiras()

    assert banco.sql_com("DELETE FROM solicitacoes_saida") == []
    assert banco.sql_com("UPDATE solicitacoes_saida SET responsavel_id = NULL") != []


def test_chave_ja_existente_nao_e_recriada(banco):
    from app.core.database import aplicar_chaves_estrangeiras
    from unittest.mock import patch
    from app.core import database

    banco.responder("FROM information_schema.table_constraints", [{'?column?': 1}])
    banco.responder("FROM information_schema.tables", [{'?column?': 1}])

    with patch.object(database, 'get_db', banco.get_db):
        aplicar_chaves_estrangeiras()

    assert banco.sql_com("ADD CONSTRAINT") == []


def test_uma_chave_recusada_nao_impede_as_outras(banco):
    """SAVEPOINT: no Postgres, um comando que falha aborta a transação inteira — sem ele, a
    primeira chave recusada deixaria todas as seguintes por criar."""
    from app.core.database import aplicar_chaves_estrangeiras
    from unittest.mock import patch
    from app.core import database

    banco.responder("FROM information_schema.tables", [{'?column?': 1}])
    banco.responder("FROM information_schema.table_constraints", [])
    banco.falhar_em("ADD CONSTRAINT fk_saidas_aluno ")

    with patch.object(database, 'get_db', banco.get_db):
        aplicar_chaves_estrangeiras()

    criadas = banco.sql_com("ADD CONSTRAINT")
    assert len(criadas) == len(_CHAVES_ESTRANGEIRAS)      # todas tentadas
    assert banco.sql_com("ROLLBACK TO SAVEPOINT criar_fk") != []
