# -*- coding: utf-8 -*-
"""O /histórico partia de `alunos LEFT JOIN saidas ON a.id = s.aluno`. Toda saída do fluxo atual
grava `ra` e deixa `aluno` NULL, então nenhuma delas aparecia na tela — sem erro nenhum, só
resultado vazio. Enquanto o diretório roda em mock o defeito fica escondido, porque as saídas
antigas do banco ainda têm `aluno` preenchido; com o banco da instituição plugado, a tela some.

Estes testes fixam as duas origens ao mesmo tempo: o histórico precisa achar tanto a saída nova
(por RA, nome resolvido ao vivo) quanto a antiga (por nome na tabela local).
"""
from unittest.mock import patch

import pytest

from tests.apoio import DiretorioFalso

ALUNOS = {
    "2024001": {"ra": "2024001", "nome": "Ana Beatriz Souza", "turma": "A",
                "serie": "6º ano EF", "foto_url": "https://escola/foto/ana.jpg"},
}


def _saida_por_ra(**extra):
    linha = {
        'id': 1, 'ra': '2024001', 'turma': 'A', 'data_saida': '2026-08-20', 'horario': '13:00',
        'motivo': 'Consulta médica', 'responsavel_escola': 'Secretaria', 'tipo_saida': 'sozinho',
        'acompanhante': None, 'status': 'concluida', 'aluno_legado': None, 'serie_legado': None,
        'turma_legado': None, 'foto_path_legado': None,
    }
    linha.update(extra)
    return linha


def _saida_legada(**extra):
    linha = {
        'id': 2, 'ra': None, 'turma': None, 'data_saida': '2026-08-12', 'horario': '11:30',
        'motivo': 'Dentista', 'responsavel_escola': 'Coordenação', 'tipo_saida': 'acompanhado',
        'acompanhante': 'Pai', 'status': 'concluida', 'aluno_legado': 'Levi Cohen',
        'serie_legado': 'Pré 1', 'turma_legado': 'A', 'foto_path_legado': 'photos/levi.jpg',
    }
    linha.update(extra)
    return linha


@pytest.fixture
def escola():
    return DiretorioFalso(ALUNOS)


def _buscar(cliente, escola, nome):
    with patch("app.api.web.get_school_sql_directory", return_value=escola):
        return cliente.get(f"/historico?nome={nome}")


# ---------------------------------------------------------------- o defeito em si

def test_saida_registrada_por_ra_aparece_no_historico(sessao_admin, banco, escola):
    """É o caso que a consulta antiga perdia por completo."""
    banco.responder("FROM saidas s", [_saida_por_ra()])

    r = _buscar(sessao_admin, escola, "Ana")
    corpo = r.get_data(as_text=True)

    assert r.status_code == 200
    assert "Ana Beatriz Souza" in corpo
    assert "Consulta médica" in corpo


def test_o_nome_vira_lista_de_ras_no_banco_da_escola(sessao_admin, banco, escola):
    """O nome não existe mais no Postgres local; quem sabe casar nome→RA é o banco da escola."""
    banco.responder("FROM saidas s", [_saida_por_ra()])

    _buscar(sessao_admin, escola, "Ana")

    assert escola.buscas == ["Ana"]
    _sql, params = banco.sql_com("FROM saidas s")[0]
    assert params[0] == ["2024001"]


def test_historico_por_ra_traz_foto_e_serie_do_banco_da_escola(sessao_admin, banco, escola):
    """A consulta antiga só conseguia foto e série para aluno legado."""
    banco.responder("FROM saidas s", [_saida_por_ra()])

    corpo = _buscar(sessao_admin, escola, "Ana").get_data(as_text=True)

    assert "https://escola/foto/ana.jpg" in corpo
    assert "6º ano EF" in corpo


# ---------------------------------------------------------------- sem regressão no legado

def test_saida_legada_por_nome_continua_aparecendo(sessao_admin, banco, escola):
    banco.responder("FROM saidas s", [_saida_legada()])

    corpo = _buscar(sessao_admin, escola, "Levi").get_data(as_text=True)

    assert "Levi Cohen" in corpo
    assert "Dentista" in corpo
    # Foto legada continua servida pela rota de uploads.
    assert "/uploads/photos/levi.jpg" in corpo


def test_as_duas_origens_aparecem_na_mesma_busca(sessao_admin, banco, escola):
    banco.responder("FROM saidas s", [_saida_por_ra(), _saida_legada()])

    corpo = _buscar(sessao_admin, escola, "a").get_data(as_text=True)

    assert "Ana Beatriz Souza" in corpo and "Levi Cohen" in corpo


# ---------------------------------------------------------------- caminhos de erro

def test_banco_da_escola_fora_do_ar_ainda_devolve_o_historico_legado(sessao_admin, banco):
    """Fail-safe: a consulta externa é só o passo nome→RA. Se ela cair, a busca por nome na
    tabela local ainda tem que responder, em vez de a tela inteira ficar vazia."""
    banco.responder("FROM saidas s", [_saida_legada()])

    r = _buscar(sessao_admin, DiretorioFalso(quebrado=True), "Levi")

    assert r.status_code == 200
    assert "Levi Cohen" in r.get_data(as_text=True)


def test_ra_desconhecido_no_diretorio_nao_derruba_a_linha(sessao_admin, banco):
    """Aluno que saiu da escola: a saída dele continua no histórico, identificada pelo RA."""
    banco.responder("FROM saidas s", [_saida_por_ra(ra='9999999')])

    corpo = _buscar(sessao_admin, DiretorioFalso(ALUNOS), "9999999").get_data(as_text=True)

    assert "RA 9999999" in corpo


def test_busca_vazia_nao_consulta_banco_nenhum(sessao_admin, banco, escola):
    r = _buscar(sessao_admin, escola, "")

    assert r.status_code == 200
    assert banco.sql_com("FROM saidas s") == []
    assert escola.buscas == []


def test_curinga_digitado_nao_lista_a_base_inteira(sessao_admin, banco, escola):
    """escapar_like continua no lugar: '%' tem que ser buscado como texto, não como curinga."""
    banco.responder("FROM saidas s", [])

    _buscar(sessao_admin, escola, "%25")   # '%' urlencoded

    _sql, params = banco.sql_com("FROM saidas s")[0]
    assert params[1] == '%\\%%'


def test_historico_continua_negado_para_o_porteiro(cliente, banco, escola):
    with cliente.session_transaction() as s:
        s['user_id'] = 6
        s['role'] = 'vigia'
        s['username'] = 'porteiro'

    r = _buscar(cliente, escola, "Ana")

    assert r.status_code == 403


def test_historico_nao_abre_conexao_aninhada(sessao_admin, banco, escola):
    banco.responder("FROM saidas s", [_saida_por_ra()])

    _buscar(sessao_admin, escola, "Ana")

    assert banco.profundidade_maxima == 1
