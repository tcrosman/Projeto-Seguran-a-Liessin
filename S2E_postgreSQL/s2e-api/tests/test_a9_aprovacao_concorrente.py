# -*- coding: utf-8 -*-
"""A9 — a mesma corrida do A8, agora na aprovação de solicitação.

O `UPDATE ... WHERE status='aguardando'` serializa corretamente duas aprovações DA MESMA
solicitação. O que ele não impede é outra saída para o mesmo aluno e a mesma data nascer no meio
do caminho: pai e mãe criam cada um a sua solicitação e dois admins aprovam ao mesmo tempo, ou
um admin aprova enquanto a portaria registra em /registrar_saida. Os dois leem "nenhuma
existente" no SELECT e os dois inserem — exatamente o que o comentário daquela checagem diz
querer evitar.

Quem impede é o índice único parcial criado em A8. Aqui é o tratamento do erro que falta: sem
SAVEPOINT a violação abortaria a transação inteira e levaria junto a aprovação e a auditoria já
gravadas — o admin clicaria em "aprovar", veria erro, e a decisão dele sumiria.
"""
import pytest
from unittest.mock import patch

from psycopg2.errors import UniqueViolation

from tests.apoio import DiretorioFalso

ANA = {"ra": "2024001", "nome": "Ana Beatriz Souza", "turma": "A", "serie": "6º ano EF",
       "foto_url": None, "responsaveis_email": ["mae@teste.com"]}

SOL = {
    'id': 7, 'aluno_id': None, 'ra': '2024001', 'data_solicitada': '2026-09-10',
    'horario_solicitado': '13:00', 'motivo': 'Consulta', 'tipo_saida': 'sozinho',
    'acompanhante': None, 'status': 'aguardando', 'turma': 'A',
    'responsavel_nome': 'Maria Souza', 'responsavel_email': 'mae@teste.com',
    'responsavel_status': 'aprovado',
    'nome_legado': None, 'turma_legado': None, 'serie_legado': None,
}


@pytest.fixture
def aprovando(sessao_admin, banco):
    banco.responder("FROM solicitacoes_saida ss", [SOL])
    banco.responder("UPDATE solicitacoes_saida SET status = 'aprovado'", rowcount=1)
    banco.responder("SELECT status FROM saidas", [])
    return sessao_admin


def _aprovar(sessao):
    with patch("app.api.web.get_school_sql_directory",
               return_value=DiretorioFalso({'2024001': ANA})):
        return sessao.post("/admin/solicitacoes/7/aprovar", follow_redirects=True)


def _colidir(banco):
    banco.falhar_em("INSERT INTO saidas (aluno, ra, turma",
                    UniqueViolation('duplicate key value violates unique constraint '
                                    '"uq_saidas_pendente_por_dia"'))


# ---------------------------------------------------------------- a colisão é tratada

def test_saida_concorrente_nao_vira_erro_500(aprovando, banco):
    _colidir(banco)

    r = _aprovar(aprovando)

    assert r.status_code == 200


def test_a_aprovacao_sobrevive_a_colisao(aprovando, banco):
    """Sem SAVEPOINT, a violação desfaria o UPDATE de status junto com o INSERT: o admin
    clicaria em aprovar, veria erro, e a decisão sumiria."""
    _colidir(banco)

    _aprovar(aprovando)

    assert banco.sql_com("ROLLBACK TO SAVEPOINT criar_saida") != []
    assert banco.sql_com("UPDATE solicitacoes_saida SET status = 'aprovado'") != []


def test_a_auditoria_da_aprovacao_tambem_sobrevive(aprovando, banco):
    _colidir(banco)

    _aprovar(aprovando)

    assert "APROVOU SOLICITAÇÃO" in banco.acoes_auditadas()


def test_a_tela_explica_que_nenhuma_saida_nova_foi_criada(aprovando, banco):
    """Mesma mensagem do caminho em que a saída já existia na conferência: para quem está na
    tela, a situação é idêntica."""
    _colidir(banco)

    corpo = _aprovar(aprovando).get_data(as_text=True)

    assert "nenhuma saída nova foi criada" in corpo
    assert "uq_saidas_pendente_por_dia" not in corpo


def test_a_tela_nao_afirma_que_a_saida_foi_registrada(aprovando, banco):
    _colidir(banco)

    assert "aprovada e registrada" not in _aprovar(aprovando).get_data(as_text=True)


# ---------------------------------------------------------------- o caminho normal

def test_sem_colisao_a_saida_e_criada_e_confirmada(aprovando, banco):
    r = _aprovar(aprovando)

    assert "aprovada e registrada" in r.get_data(as_text=True)
    assert banco.sql_com("INSERT INTO saidas (aluno, ra, turma") != []
    assert banco.sql_com("ROLLBACK TO SAVEPOINT criar_saida") == []


def test_a_checagem_previa_continua_valendo(aprovando, banco):
    """O índice é a garantia; o SELECT anterior é a mensagem boa no caso comum, e evita o
    INSERT que já se sabe que vai falhar."""
    banco.responder("SELECT status FROM saidas", [{'status': 'pendente'}])

    corpo = _aprovar(aprovando).get_data(as_text=True)

    assert "nenhuma saída nova foi criada" in corpo
    assert banco.sql_com("INSERT INTO saidas (aluno, ra, turma") == []
