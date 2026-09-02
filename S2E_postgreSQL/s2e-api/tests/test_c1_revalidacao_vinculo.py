# -*- coding: utf-8 -*-
"""C1 — editar solicitação e aprovar solicitação não reconferiam o vínculo pai↔aluno.

As duas rotas se contentavam com o `responsavel_id` gravado quando a solicitação foi criada.
Isso responde "quem pediu", não "quem ainda pode pedir". Um responsável que perde o vínculo com
um filho e mantém o de outro continua entrando no portal — `responsavel_reconhecido()`, que é o
que o login exige, se satisfaz com UM vínculo ativo qualquer. A solicitação antiga do primeiro
filho continua na base (a manutenção só poda as já revisadas), e por ela dava para trocar data,
horário, tipo de saída e acompanhante: quem busca a criança no portão.

O conserto põe dois portões, ambos fail-closed: um na edição feita pelo responsável e outro na
aprovação feita pelo admin — porque entre criar e aprovar podem passar dias.
"""
import pytest
from unittest.mock import patch

from tests.apoio import DiretorioFalso

ANA = {"ra": "2024001", "nome": "Ana Beatriz Souza", "turma": "A", "serie": "6º ano EF",
       "foto_url": None, "responsaveis_email": ["mae@teste.com"]}

SOL_DO_PORTAL = {
    'id': 7, 'responsavel_id': 3, 'aluno_id': None, 'ra': '2024001',
    'data_solicitada': '2026-09-10', 'horario_solicitado': '13:00', 'motivo': 'Consulta',
    'tipo_saida': 'sozinho', 'acompanhante': None, 'status': 'aprovado',
    'responsavel_status': 'aprovado', 'saida_status': 'pendente',
    'nome_legado': None, 'turma_legado': None, 'serie_legado': None,
}

SOL_NA_MESA_DO_ADMIN = {
    'id': 7, 'aluno_id': None, 'ra': '2024001', 'data_solicitada': '2026-09-10',
    'horario_solicitado': '13:00', 'motivo': 'Consulta', 'tipo_saida': 'sozinho',
    'acompanhante': None, 'status': 'aguardando', 'turma': 'A',
    'responsavel_nome': 'Maria Souza', 'responsavel_email': 'mae@teste.com',
    'responsavel_status': 'aprovado',
    'nome_legado': None, 'turma_legado': None, 'serie_legado': None,
}

FORM = {'data_solicitada': '2026-09-10', 'horario': '15:00', 'motivo': 'Consulta',
        'tipo_saida': 'sozinho'}


@pytest.fixture
def responsavel(sessao_responsavel):
    """Sessão do portal dos pais — montada no conftest, que também declara a conta como ativa
    para o pai_required (ver C3)."""
    return sessao_responsavel


def _com_diretorio(modulo, diretorio):
    return patch("app.api." + modulo + ".get_school_sql_directory", return_value=diretorio)


# ------------------------------------------------- edição pelo responsável (/pais)

def test_edicao_recusada_quando_o_vinculo_com_o_aluno_acabou(responsavel, banco):
    """O cenário do diagnóstico: perdeu o vínculo com este filho, mantém o do outro, segue logado."""
    banco.responder("FROM solicitacoes_saida ss", [SOL_DO_PORTAL])

    with _com_diretorio('pais', DiretorioFalso({})):
        r = responsavel.post("/pais/editar_solicitacao/7", data=FORM)

    assert r.status_code == 403
    assert banco.sql_com("UPDATE solicitacoes_saida") == []
    assert banco.sql_com("DELETE FROM saidas") == []


def test_edicao_recusada_quando_o_banco_da_escola_nao_responde(responsavel, banco):
    """Fail-closed: sem confirmar o vínculo não se edita, nem se assume que continua valendo."""
    banco.responder("FROM solicitacoes_saida ss", [SOL_DO_PORTAL])

    with _com_diretorio('pais', DiretorioFalso({'2024001': ANA}, quebrado=True)):
        r = responsavel.post("/pais/editar_solicitacao/7", data=FORM)

    assert r.status_code == 403
    assert "indisponível" in r.get_data(as_text=True)
    assert banco.sql_com("UPDATE solicitacoes_saida") == []


def test_edicao_recusada_quando_a_conta_do_responsavel_nao_esta_ativa(responsavel, banco):
    banco.responder("FROM solicitacoes_saida ss",
                    [dict(SOL_DO_PORTAL, responsavel_status='bloqueado')])

    with _com_diretorio('pais', DiretorioFalso({'2024001': ANA})):
        r = responsavel.post("/pais/editar_solicitacao/7", data=FORM)

    assert r.status_code == 403
    assert banco.sql_com("UPDATE solicitacoes_saida") == []


def test_edicao_segue_normal_com_o_vinculo_de_pe(responsavel, banco):
    """A checagem não pode atrapalhar quem continua sendo responsável pelo aluno."""
    banco.responder("FROM solicitacoes_saida ss", [SOL_DO_PORTAL])

    with _com_diretorio('pais', DiretorioFalso({'2024001': ANA})):
        r = responsavel.post("/pais/editar_solicitacao/7", data=FORM)

    assert r.status_code == 302
    assert banco.sql_com("UPDATE solicitacoes_saida") != []


def test_recusa_da_edicao_fica_registrada_na_auditoria(responsavel, banco):
    banco.responder("FROM solicitacoes_saida ss", [SOL_DO_PORTAL])

    with _com_diretorio('pais', DiretorioFalso({})):
        responsavel.post("/pais/editar_solicitacao/7", data=FORM)

    acao, detalhes, _ip = banco.auditorias()[0]
    assert acao == "ACESSO NEGADO A SOLICITAÇÃO"
    assert "sem_vinculo" in detalhes


def test_abrir_a_tela_de_edicao_tambem_e_recusado(responsavel, banco):
    """O GET monta o formulário com os dados da saída de um aluno que não é mais dele."""
    banco.responder("FROM solicitacoes_saida ss", [SOL_DO_PORTAL])

    with _com_diretorio('pais', DiretorioFalso({})):
        r = responsavel.get("/pais/editar_solicitacao/7")

    assert r.status_code == 403


# ------------------------------------------------- aprovação pelo admin (/admin)

def test_aprovacao_recusada_quando_o_vinculo_com_o_aluno_acabou(sessao_admin, banco):
    """Entre criar e aprovar passam dias; o vínculo conferido na criação não vale para sempre."""
    banco.responder("FROM solicitacoes_saida ss", [SOL_NA_MESA_DO_ADMIN])
    banco.responder("UPDATE solicitacoes_saida SET status = 'aprovado'", rowcount=1)

    with _com_diretorio('web', DiretorioFalso({})):
        sessao_admin.post("/admin/solicitacoes/7/aprovar")

    assert banco.sql_com("UPDATE solicitacoes_saida", "aprovado") == []
    assert banco.sql_com("INSERT INTO saidas") == []


def test_aprovacao_recusada_quando_o_banco_da_escola_nao_responde(sessao_admin, banco):
    banco.responder("FROM solicitacoes_saida ss", [SOL_NA_MESA_DO_ADMIN])
    banco.responder("UPDATE solicitacoes_saida SET status = 'aprovado'", rowcount=1)

    with _com_diretorio('web', DiretorioFalso({'2024001': ANA}, quebrado=True)):
        sessao_admin.post("/admin/solicitacoes/7/aprovar")

    assert banco.sql_com("UPDATE solicitacoes_saida", "aprovado") == []
    assert banco.sql_com("INSERT INTO saidas") == []


def test_aprovacao_recusada_quando_a_conta_do_responsavel_esta_bloqueada(sessao_admin, banco):
    banco.responder("FROM solicitacoes_saida ss",
                    [dict(SOL_NA_MESA_DO_ADMIN, responsavel_status='bloqueado')])
    banco.responder("UPDATE solicitacoes_saida SET status = 'aprovado'", rowcount=1)

    with _com_diretorio('web', DiretorioFalso({'2024001': ANA})):
        sessao_admin.post("/admin/solicitacoes/7/aprovar")

    assert banco.sql_com("INSERT INTO saidas") == []


def test_recusa_da_aprovacao_fica_registrada_na_auditoria(sessao_admin, banco):
    banco.responder("FROM solicitacoes_saida ss", [SOL_NA_MESA_DO_ADMIN])

    with _com_diretorio('web', DiretorioFalso({})):
        sessao_admin.post("/admin/solicitacoes/7/aprovar")

    assert "APROVAÇÃO RECUSADA" in banco.acoes_auditadas()


def test_aprovacao_segue_normal_com_o_vinculo_de_pe(sessao_admin, banco):
    banco.responder("FROM solicitacoes_saida ss", [SOL_NA_MESA_DO_ADMIN])
    banco.responder("UPDATE solicitacoes_saida SET status = 'aprovado'", rowcount=1)
    banco.responder("SELECT status FROM saidas", [])

    with _com_diretorio('web', DiretorioFalso({'2024001': ANA})):
        sessao_admin.post("/admin/solicitacoes/7/aprovar")

    assert banco.sql_com("INSERT INTO saidas") != []
