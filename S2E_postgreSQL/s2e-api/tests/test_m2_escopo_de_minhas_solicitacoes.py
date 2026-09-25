# -*- coding: utf-8 -*-
"""M2 — /pais/minhas_solicitacoes mostrava dados de aluno sem vínculo atual.

A consulta filtrava só por `responsavel_id`, que responde "quem pediu" — não "sobre quem ainda
se pode pedir". `get_students_by_ras`, usada para resolver os nomes, não filtra por responsável.
Quem perdeu o vínculo com um filho e manteve o de outro continuava vendo nome, série e turma da
primeira criança nesta tela. E é daqui que sai o `sol_id` usado na edição, que é o C1: aquela
revalidação recusa a ação, mas o dado pessoal já teria sido exibido.
"""
import pytest
from unittest.mock import patch

from tests.apoio import DiretorioFalso

ANA = {"ra": "2024001", "nome": "Ana Beatriz Souza", "turma": "A", "serie": "6º ano EF",
       "foto_url": None, "responsaveis_email": ["mae@teste.com"]}
CARLOS = {"ra": "2024002", "nome": "Carlos Eduardo Lima", "turma": "B", "serie": "8º ano EF",
          "foto_url": None, "responsaveis_email": ["mae@teste.com"]}


def _linha(ra, sol_id=7, aluno_id=None):
    return {'id': sol_id, 'data_solicitada': '2026-09-10', 'horario_solicitado': '13:00',
            'motivo': 'Consulta', 'status': 'aguardando', 'criado_em': None, 'ra': ra,
            'aluno_id': aluno_id, 'nome_legado': None, 'turma_legado': None,
            'serie_legado': None, 'saida_status': None}


@pytest.fixture
def responsavel(sessao_responsavel):
    return sessao_responsavel


def _abrir(sessao, diretorio):
    with patch("app.api.pais.get_school_sql_directory", return_value=diretorio):
        return sessao.get("/pais/minhas_solicitacoes").get_data(as_text=True)


def test_solicitacao_de_filho_sem_vinculo_atual_nao_aparece(responsavel, banco):
    """O cenário do C1, do lado da exibição: perdeu o vínculo com Ana, mantém o de Carlos."""
    banco.responder("FROM solicitacoes_saida ss",
                    [_linha('2024001'), _linha('2024002', sol_id=8)])

    corpo = _abrir(responsavel, DiretorioFalso({'2024002': CARLOS}))

    assert "Ana Beatriz Souza" not in corpo
    assert "Carlos Eduardo Lima" in corpo


def test_o_sol_id_do_filho_sem_vinculo_nao_e_oferecido(responsavel, banco):
    """É por este link que se chegava à tela de edição."""
    banco.responder("FROM solicitacoes_saida ss", [_linha('2024001')])

    corpo = _abrir(responsavel, DiretorioFalso({'2024002': CARLOS}))

    assert "/pais/editar_solicitacao/7" not in corpo


def test_nem_o_ra_do_aluno_sem_vinculo_vaza(responsavel, banco):
    banco.responder("FROM solicitacoes_saida ss", [_linha('2024001')])

    assert "2024001" not in _abrir(responsavel, DiretorioFalso({}))


def test_banco_da_escola_fora_do_ar_nao_mostra_dado_nenhum(responsavel, banco):
    """Fail-closed: sem confirmar os vínculos não se exibe aluno. A tela diz que está
    indisponível em vez de fingir que o responsável não tem pedidos."""
    banco.responder("FROM solicitacoes_saida ss", [_linha('2024001')])

    corpo = _abrir(responsavel, DiretorioFalso({'2024001': ANA}, quebrado=True))

    assert "Ana Beatriz Souza" not in corpo
    assert "cadastro da escola está indisponível" in corpo


def test_quem_mantem_o_vinculo_continua_vendo_o_historico(responsavel, banco):
    banco.responder("FROM solicitacoes_saida ss", [_linha('2024001')])

    corpo = _abrir(responsavel, DiretorioFalso({'2024001': ANA}))

    assert "Ana Beatriz Souza" in corpo
    assert "/pais/editar_solicitacao/7" in corpo


def test_linha_legada_sem_ra_depende_do_vinculo_local(responsavel, banco):
    """Sem RA não há o que perguntar ao banco da escola; o vínculo dessas linhas mora na tabela
    local, e é ela que decide."""
    banco.responder("FROM solicitacoes_saida ss",
                    [dict(_linha(None, aluno_id=42), nome_legado='Aluno Antigo')])
    banco.responder("SELECT aluno_id FROM vinculos_pais_alunos", [])

    assert "Aluno Antigo" not in _abrir(responsavel, DiretorioFalso({}))


def test_linha_legada_com_vinculo_local_aparece(responsavel, banco):
    banco.responder("FROM solicitacoes_saida ss",
                    [dict(_linha(None, aluno_id=42), nome_legado='Aluno Antigo')])
    banco.responder("SELECT aluno_id FROM vinculos_pais_alunos", [{'aluno_id': 42}])

    assert "Aluno Antigo" in _abrir(responsavel, DiretorioFalso({}))


def test_sem_linhas_legadas_a_tabela_local_nem_e_consultada(responsavel, banco):
    """Uma consulta a mais por carregamento de tela, no pico, para nada."""
    banco.responder("FROM solicitacoes_saida ss", [_linha('2024001')])

    _abrir(responsavel, DiretorioFalso({'2024001': ANA}))

    assert banco.sql_com("SELECT aluno_id FROM vinculos_pais_alunos") == []
