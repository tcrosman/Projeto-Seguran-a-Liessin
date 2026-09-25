# -*- coding: utf-8 -*-
"""A2 — consultas ao banco da escola aconteciam dentro da transação do banco próprio.

Em /concluir_saida o UPDATE trava a linha da saída e, ainda dentro do `with get_db()`,
_nome_e_emails_para_saida abria TRÊS conexões ao banco da instituição (get_student já são duas,
por causa de _emails_por_ra, mais get_guardian_emails_for_ra). Com SCHOOL_SQL_TIMEOUT_SEG=5 o
pior caso é ~30s segurando ao mesmo tempo uma conexão do pool e uma thread do worker. E basta o
banco da escola estar LENTO, que é o caso comum — não fora do ar. Dois seguranças clicando
"liberar" juntos paravam o sistema inteiro, portaria e portal dos pais inclusive, sem chegar ao
timeout do gunicorn que dispararia o restart.

O mesmo padrão estava em /editar_saida, /admin/solicitacoes/<id>/aprovar e .../rejeitar.

O dublê aqui não olha o resultado da rota: ele mede, no instante da chamada ao diretório, se há
transação nossa aberta. É um defeito que nenhum teste funcional enxerga.
"""
import pytest
from unittest.mock import patch

from tests.apoio import DiretorioFalso
from app.core.tempo import hoje

ANA = {"ra": "2024001", "nome": "Ana Beatriz Souza", "turma": "A", "serie": "6º ano EF",
       "foto_url": None, "responsaveis_email": ["mae@teste.com"]}


class DiretorioQueEspiaOPool(DiretorioFalso):
    """Anota, a cada consulta, quantas conexões do banco próprio estavam retidas no momento."""

    def __init__(self, banco, alunos=None):
        super().__init__(alunos or {})
        self._banco = banco
        self.profundidades = []

    def _talvez_quebrar(self):
        self.profundidades.append(self._banco.profundidade)
        super()._talvez_quebrar()

    @property
    def segurou_conexao(self):
        return any(p > 0 for p in self.profundidades)


@pytest.fixture
def portaria(cliente):
    with cliente.session_transaction() as s:
        s['user_id'] = 9
        s['role'] = 'basico'
        s['username'] = 'portaria_teste'
    return cliente


SAIDA_PENDENTE = {
    'id': 5, 'ra': '2024001', 'turma': 'A', 'horario': '12:00', 'motivo': 'Consulta',
    'responsavel_escola': 'Secretaria', 'tipo_saida': 'sozinho', 'acompanhante': None,
    'documento_path': None, 'status': 'pendente', 'data_saida': hoje(),
    'aluno_legado': None, 'serie_legado': None, 'turma_legado': None, 'foto_path_legado': None,
    'aluno_id_legado': None, 'aluno_nome_legado': None,
}

SOL_ADMIN = {
    'id': 7, 'aluno_id': None, 'ra': '2024001', 'data_solicitada': '2026-09-10',
    'horario_solicitado': '13:00', 'motivo': 'Consulta', 'tipo_saida': 'sozinho',
    'acompanhante': None, 'status': 'aguardando', 'turma': 'A',
    'responsavel_nome': 'Maria Souza', 'responsavel_email': 'mae@teste.com',
    'responsavel_status': 'aprovado',
    'nome_legado': None, 'turma_legado': None, 'serie_legado': None,
}


def _espiao(banco):
    return DiretorioQueEspiaOPool(banco, {'2024001': ANA})


def test_concluir_saida_nao_consulta_a_escola_com_a_linha_travada(portaria, banco):
    """O pior caso: o UPDATE já travou a linha da saída."""
    diretorio = _espiao(banco)
    banco.responder("UPDATE saidas SET status = 'concluida'", rowcount=1)
    banco.responder("FROM saidas s LEFT JOIN alunos", [SAIDA_PENDENTE])

    with patch("app.api.web.get_school_sql_directory", return_value=diretorio):
        portaria.post("/concluir_saida/5")

    assert diretorio.profundidades, "a rota nem chegou a consultar o banco da escola"
    assert not diretorio.segurou_conexao


def test_editar_saida_nao_consulta_a_escola_dentro_da_transacao(portaria, banco):
    diretorio = _espiao(banco)
    banco.responder("FROM saidas s LEFT JOIN alunos a ON s.aluno = a.id", [SAIDA_PENDENTE])

    with patch("app.api.web.get_school_sql_directory", return_value=diretorio):
        portaria.get("/editar_saida/5")

    assert diretorio.profundidades
    assert not diretorio.segurou_conexao


def test_aprovar_solicitacao_nao_consulta_a_escola_dentro_da_transacao(sessao_admin, banco):
    diretorio = _espiao(banco)
    banco.responder("FROM solicitacoes_saida ss", [SOL_ADMIN])
    banco.responder("UPDATE solicitacoes_saida SET status = 'aprovado'", rowcount=1)
    banco.responder("SELECT status FROM saidas", [])

    with patch("app.api.web.get_school_sql_directory", return_value=diretorio):
        sessao_admin.post("/admin/solicitacoes/7/aprovar")

    assert diretorio.profundidades
    assert not diretorio.segurou_conexao


def test_rejeitar_solicitacao_nao_consulta_a_escola_dentro_da_transacao(sessao_admin, banco):
    diretorio = _espiao(banco)
    banco.responder("FROM solicitacoes_saida ss", [SOL_ADMIN])

    with patch("app.api.web.get_school_sql_directory", return_value=diretorio):
        sessao_admin.post("/admin/solicitacoes/7/rejeitar")

    assert diretorio.profundidades
    assert not diretorio.segurou_conexao


# ---------------------------------------------------------------- o comportamento não muda

def test_a_saida_continua_sendo_liberada_e_notificada(portaria, banco, correio):
    banco.responder("UPDATE saidas SET status = 'concluida'", rowcount=1)
    banco.responder("FROM saidas s LEFT JOIN alunos", [SAIDA_PENDENTE])

    with patch("app.api.web.get_school_sql_directory",
               return_value=DiretorioFalso({'2024001': ANA})):
        r = portaria.post("/concluir_saida/5", follow_redirects=True)

    assert "Saída autorizada!" in r.get_data(as_text=True)
    assert correio.destinatarios() == ["mae@teste.com"]


def test_edicao_da_saida_continua_gravando(portaria, banco):
    banco.responder("FROM saidas s LEFT JOIN alunos a ON s.aluno = a.id", [SAIDA_PENDENTE])

    with patch("app.api.web.get_school_sql_directory",
               return_value=DiretorioFalso({'2024001': ANA})):
        portaria.post("/editar_saida/5", data={
            'horario': '14:00', 'motivo': 'Consulta', 'responsavel_escola': 'Secretaria',
            'tipo_saida': 'sozinho'})

    (sql, params), = banco.sql_com("UPDATE saidas SET horario")
    assert "status='pendente'" in sql
    assert '14:00' in params


def test_nenhuma_rota_abre_conexao_aninhada(portaria, banco):
    """O pool tem DB_POOL_MAX pequeno: um `with get_db()` dentro de outro retém duas conexões."""
    banco.responder("UPDATE saidas SET status = 'concluida'", rowcount=1)
    banco.responder("FROM saidas s LEFT JOIN alunos", [SAIDA_PENDENTE])

    with patch("app.api.web.get_school_sql_directory",
               return_value=DiretorioFalso({'2024001': ANA})):
        portaria.post("/concluir_saida/5")

    assert banco.profundidade_maxima == 1
