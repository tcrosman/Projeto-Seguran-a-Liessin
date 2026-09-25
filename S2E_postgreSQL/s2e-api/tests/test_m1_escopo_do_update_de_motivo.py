# -*- coding: utf-8 -*-
"""M1 — o UPDATE de motivo alcançava saídas que não eram daquela solicitação.

`UPDATE saidas SET motivo=... WHERE ra=... AND data_saida=... AND status='pendente'`, sem
`solicitacao_id`. É a mesma falha já corrigida no DELETE irmão, algumas linhas acima: o
responsável editava o motivo do próprio pedido e sobrescrevia o motivo de uma saída que a
portaria tinha registrado por conta própria, com outra justificativa e outro documento anexado.
"""
import pytest
from unittest.mock import patch

from tests.apoio import DiretorioFalso

ANA = {"ra": "2024001", "nome": "Ana Beatriz Souza", "turma": "A", "serie": "6º ano EF",
       "foto_url": None, "responsaveis_email": ["mae@teste.com"]}

SOL_APROVADA = {
    'id': 7, 'responsavel_id': 3, 'aluno_id': None, 'ra': '2024001',
    'data_solicitada': '2026-09-10', 'horario_solicitado': '13:00', 'motivo': 'Consulta',
    'tipo_saida': 'sozinho', 'acompanhante': None, 'status': 'aprovado',
    'responsavel_status': 'aprovado', 'saida_status': 'pendente',
    'nome_legado': None, 'turma_legado': None, 'serie_legado': None,
}

# Só o motivo muda: não dispara reaprovação, e é esse caminho que fazia o UPDATE largo.
FORM_SO_MOTIVO = {'data_solicitada': '2026-09-10', 'horario': '13:00',
                  'motivo': 'Outro motivo', 'tipo_saida': 'sozinho'}


@pytest.fixture
def responsavel(sessao_responsavel):
    return sessao_responsavel


def _editar(sessao, sol=SOL_APROVADA):
    with patch("app.api.pais.get_school_sql_directory",
               return_value=DiretorioFalso({'2024001': ANA})):
        return sessao.post("/pais/editar_solicitacao/7", data=FORM_SO_MOTIVO)


def test_o_update_de_motivo_e_amarrado_a_solicitacao(responsavel, banco):
    banco.responder("FROM solicitacoes_saida ss", [SOL_APROVADA])

    _editar(responsavel)

    (sql, params), = banco.sql_com("UPDATE saidas SET motivo")
    assert "solicitacao_id" in sql
    assert params == ('Outro motivo', 7)


def test_o_update_nao_alcanca_mais_saidas_por_aluno_e_data(responsavel, banco):
    """O filtro antigo pegava qualquer pendente do aluno naquele dia — inclusive a da portaria,
    com outro motivo e outro documento anexado."""
    banco.responder("FROM solicitacoes_saida ss", [SOL_APROVADA])

    _editar(responsavel)

    (sql, params), = banco.sql_com("UPDATE saidas SET motivo")
    assert "data_saida" not in sql
    assert "ra=" not in sql
    assert '2024001' not in params and '2026-09-10' not in params


def test_solicitacao_legada_usa_o_mesmo_vinculo(responsavel, banco):
    """As linhas sem RA passavam pela coluna `aluno` e tinham exatamente o mesmo problema."""
    legada = dict(SOL_APROVADA, ra=None, aluno_id=42)
    banco.responder("FROM solicitacoes_saida ss", [legada])
    banco.responder("SELECT 1 FROM vinculos_pais_alunos", [{'?column?': 1}])

    _editar(responsavel, legada)

    (sql, params), = banco.sql_com("UPDATE saidas SET motivo")
    assert "solicitacao_id" in sql
    assert 42 not in params


def test_a_solicitacao_em_si_continua_sendo_atualizada(responsavel, banco):
    banco.responder("FROM solicitacoes_saida ss", [SOL_APROVADA])

    _editar(responsavel)

    assert banco.sql_com("UPDATE solicitacoes_saida", "motivo=") != []
