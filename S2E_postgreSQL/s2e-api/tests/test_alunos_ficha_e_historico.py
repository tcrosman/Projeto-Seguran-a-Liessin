# -*- coding: utf-8 -*-
"""A tela de alunos saiu do sistema junto com o cadastro local, quando os alunos passaram a vir
do banco da instituição. Só que a necessidade continuou: a secretaria precisa olhar UM aluno —
`/historico` devolve as saídas de todos os alunos que casam com o nome, misturadas numa lista só.

Estes testes cobrem a volta da lista de alunos e da ficha individual, com a parte delicada: o
histórico do aluno precisa juntar as saídas do fluxo atual (por RA) e as anteriores à migração
(sem RA, reconhecíveis só pelo nome na tabela legada) sem trazer as de um homônimo.
"""
from unittest.mock import patch

import pytest

from tests.apoio import DiretorioFalso

ALUNOS = {
    "2024001": {"ra": "2024001", "nome": "Ana Beatriz Souza", "turma": "A",
                "serie": "6º ano EF", "foto_url": "https://escola/foto/ana.jpg"},
    "2024003": {"ra": "2024003", "nome": "Theo Crosman", "turma": "A",
                "serie": "3º ano EM", "foto_url": None},
}


@pytest.fixture
def escola():
    return DiretorioFalso(ALUNOS)


def _saida(**extra):
    linha = {
        'id': 5, 'ra': '2024001', 'turma': 'A', 'data_saida': '2026-08-20', 'horario': '13:00',
        'motivo': 'Consulta médica', 'responsavel_escola': 'Secretaria', 'tipo_saida': 'sozinho',
        'acompanhante': None, 'status': 'concluida', 'liberado_em': None, 'documento_path': None,
        'tem_documento': False, 'aluno_legado': None, 'serie_legado': None,
        'turma_legado': None, 'foto_path_legado': None,
    }
    linha.update(extra)
    return linha


def _abrir(cliente, escola, caminho):
    with patch("app.api.web.get_school_sql_directory", return_value=escola):
        return cliente.get(caminho)


# ---------------------------------------------------------------- a lista

def test_lista_abre_com_os_alunos_sem_precisar_buscar(sessao_admin, escola):
    """A busca antiga exigia um termo: sem ele a tela abria vazia, o que não é uma lista."""
    r = _abrir(sessao_admin, escola, "/alunos")
    corpo = r.get_data(as_text=True)

    assert r.status_code == 200
    assert "Ana Beatriz Souza" in corpo and "Theo Crosman" in corpo


def test_cada_aluno_leva_para_a_propria_ficha(sessao_admin, escola):
    corpo = _abrir(sessao_admin, escola, "/alunos").get_data(as_text=True)

    assert "/alunos/2024001" in corpo and "/alunos/2024003" in corpo


def test_busca_filtra_a_lista(sessao_admin, escola):
    corpo = _abrir(sessao_admin, escola, "/alunos?busca=Theo").get_data(as_text=True)

    assert "Theo Crosman" in corpo
    assert "Ana Beatriz Souza" not in corpo


def test_busca_sem_resultado_explica_em_vez_de_ficar_em_branco(sessao_admin, escola):
    corpo = _abrir(sessao_admin, escola, "/alunos?busca=Ninguem").get_data(as_text=True)

    assert "Nenhum aluno encontrado" in corpo


def test_banco_da_escola_fora_do_ar_avisa_em_vez_de_fingir_lista_vazia(sessao_admin):
    """"Nenhum aluno" e "não consegui perguntar" são coisas diferentes para quem está na tela."""
    corpo = _abrir(sessao_admin, DiretorioFalso(quebrado=True), "/alunos").get_data(as_text=True)

    assert "Não foi possível consultar" in corpo


def test_lista_avisa_quando_foi_cortada_no_limite(sessao_admin, banco):
    from app.api.web import ALUNOS_POR_PAGINA
    muitos = {str(i): {"ra": str(i), "nome": f"Aluno {i:03d}", "turma": "A",
                       "serie": "1º ano EF", "foto_url": None}
              for i in range(ALUNOS_POR_PAGINA + 10)}

    corpo = _abrir(sessao_admin, DiretorioFalso(muitos), "/alunos").get_data(as_text=True)

    assert "Use a busca" in corpo


# ---------------------------------------------------------------- a ficha do aluno

def test_ficha_mostra_os_dados_vindos_da_escola(sessao_admin, banco, escola):
    banco.responder("FROM saidas s", [])

    corpo = _abrir(sessao_admin, escola, "/alunos/2024001").get_data(as_text=True)

    assert "Ana Beatriz Souza" in corpo
    assert "6º ano EF" in corpo and "2024001" in corpo


def test_ficha_lista_as_saidas_daquele_aluno(sessao_admin, banco, escola):
    banco.responder("FROM saidas s", [_saida(), _saida(id=6, data_saida='2026-08-25',
                                                       motivo='Dentista', status='pendente')])

    corpo = _abrir(sessao_admin, escola, "/alunos/2024001").get_data(as_text=True)

    assert "Consulta médica" in corpo and "Dentista" in corpo
    assert "Concluída" in corpo and "Pendente" in corpo


def test_a_consulta_casa_por_ra_e_pelo_nome_do_legado(sessao_admin, banco, escola):
    """As saídas anteriores à migração não têm RA; sem casar pelo nome, a ficha apareceria vazia
    para tudo o que já está no banco."""
    banco.responder("FROM saidas s", [])

    _abrir(sessao_admin, escola, "/alunos/2024001")

    sql, params = banco.sql_com("FROM saidas s")[0]
    assert "s.ra = %s" in sql
    assert "a.nome = %s" in sql          # igualdade exata, não ILIKE
    assert params == ('2024001', 'Ana Beatriz Souza')


def test_saida_legada_do_aluno_aparece_na_ficha(sessao_admin, banco, escola):
    banco.responder("FROM saidas s", [_saida(ra=None, aluno_legado='Ana Beatriz Souza',
                                             serie_legado='6º ano EF', turma_legado='A')])

    corpo = _abrir(sessao_admin, escola, "/alunos/2024001").get_data(as_text=True)

    assert "Consulta médica" in corpo


def test_resumo_conta_cada_situacao(sessao_admin, banco, escola):
    banco.responder("FROM saidas s", [
        _saida(id=1, status='concluida'), _saida(id=2, status='concluida'),
        _saida(id=3, status='pendente'), _saida(id=4, status='nao_realizada'),
    ])

    corpo = _abrir(sessao_admin, escola, "/alunos/2024001").get_data(as_text=True)

    assert ">4<" in corpo.replace(" ", "").replace("\n", "")   # total
    assert "Não realizadas" in corpo


def test_aluno_sem_saidas_mostra_estado_vazio(sessao_admin, banco, escola):
    banco.responder("FROM saidas s", [])

    corpo = _abrir(sessao_admin, escola, "/alunos/2024003").get_data(as_text=True)

    assert "Nenhuma saída registrada para este aluno" in corpo


def test_ra_inexistente_volta_para_a_lista_com_aviso(sessao_admin, banco, escola):
    r = _abrir(sessao_admin, escola, "/alunos/0000")

    assert r.status_code == 302
    assert r.headers['Location'].endswith('/alunos')
    assert banco.sql_com("FROM saidas s") == []


def test_ficha_oferece_o_atalho_para_registrar_saida(sessao_admin, banco, escola):
    banco.responder("FROM saidas s", [])

    corpo = _abrir(sessao_admin, escola, "/alunos/2024001").get_data(as_text=True)

    assert "/registrar_saida?ra=2024001" in corpo


def test_anexo_da_saida_e_alcancavel_pela_ficha(sessao_admin, banco, escola):
    banco.responder("FROM saidas s", [_saida(tem_documento=True)])

    corpo = _abrir(sessao_admin, escola, "/alunos/2024001").get_data(as_text=True)

    assert "/saidas/5/documento" in corpo


# ---------------------------------------------------------------- acesso

@pytest.mark.parametrize("rota", ["/alunos", "/alunos/2024001"])
def test_porteiro_nao_navega_o_cadastro_de_alunos(cliente, banco, escola, rota):
    """Mesma razão do histórico geral: é o cadastro da escola inteira, e o porteiro só precisa
    das saídas do dia."""
    with cliente.session_transaction() as s:
        s['user_id'] = 6
        s['role'] = 'vigia'
        s['username'] = 'porteiro'

    assert _abrir(cliente, escola, rota).status_code == 403


@pytest.mark.parametrize("rota", ["/alunos", "/alunos/2024001"])
def test_sem_sessao_nao_ha_acesso(cliente, escola, rota):
    r = _abrir(cliente, escola, rota)

    assert r.status_code == 302
    assert r.headers['Location'].endswith('/')


def test_ficha_nao_abre_conexao_aninhada(sessao_admin, banco, escola):
    banco.responder("FROM saidas s", [_saida()])

    _abrir(sessao_admin, escola, "/alunos/2024001")

    assert banco.profundidade_maxima == 1
