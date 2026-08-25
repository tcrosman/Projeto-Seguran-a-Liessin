# -*- coding: utf-8 -*-
"""As telas de admin de solicitações usavam INNER JOIN com a tabela local `alunos`. Como o portal
dos pais grava só `ra` (aluno_id NULL), nenhuma solicitação nova casava: o admin não via nem
conseguia aprovar. Estes testes cobrem a resolução por RA que substituiu aquele JOIN.
"""
from unittest.mock import patch

from app.api.web import _resolver_solicitacoes


def _sem_cache():
    """O helper usa um cache TTL de módulo; zera entre testes para não vazar estado."""
    from app.api import web
    web._cache_diretorio = web.TTLCache(ttl_seconds=300)


class _Diretorio:
    def __init__(self, alunos):
        self._alunos = alunos

    def get_students_by_ras(self, ras):
        return {ra: self._alunos[ra] for ra in ras if ra in self._alunos}


def test_solicitacao_por_ra_resolve_no_banco_da_escola():
    _sem_cache()
    diretorio = _Diretorio({
        "2024001": {"ra": "2024001", "nome": "Ana Beatriz Souza", "turma": "A", "serie": "6º ano EF"}
    })
    linhas = [{"id": 1, "ra": "2024001", "status": "aguardando",
               "nome_legado": None, "turma_legado": None, "serie_legado": None}]

    with patch("app.api.web.get_school_sql_directory", return_value=diretorio):
        r = _resolver_solicitacoes(linhas)[0]

    assert r["aluno_nome"] == "Ana Beatriz Souza"
    assert r["serie"] == "6º ano EF"
    assert r["turma"] == "A"


def test_solicitacao_legada_usa_os_campos_da_tabela_alunos():
    """Solicitação anterior à migração para RA continua aparecendo, com os dados do JOIN legado."""
    _sem_cache()
    linhas = [{"id": 2, "ra": None, "status": "aguardando",
               "nome_legado": "Aluno Antigo", "turma_legado": "C", "serie_legado": "5º ano EF"}]

    with patch("app.api.web.get_school_sql_directory", return_value=_Diretorio({})):
        r = _resolver_solicitacoes(linhas)[0]

    assert r["aluno_nome"] == "Aluno Antigo"
    assert r["serie"] == "5º ano EF"
    assert r["turma"] == "C"


def test_ra_desconhecido_no_diretorio_ainda_aparece():
    """Se o banco da escola não conhece o RA, a solicitação não pode sumir da tela do admin —
    ela ainda precisa ser aprovável/rejeitável."""
    _sem_cache()
    linhas = [{"id": 3, "ra": "9999999", "status": "aguardando",
               "nome_legado": None, "turma_legado": None, "serie_legado": None}]

    with patch("app.api.web.get_school_sql_directory", return_value=_Diretorio({})):
        r = _resolver_solicitacoes(linhas)[0]

    assert r["aluno_nome"] == "RA 9999999"
    assert r["serie"] is None


def test_diretorio_fora_do_ar_nao_derruba_a_tela():
    _sem_cache()

    class _Quebrado:
        def get_students_by_ras(self, ras):
            raise RuntimeError("banco da escola indisponível")

    linhas = [{"id": 4, "ra": "2024001", "status": "aguardando",
               "nome_legado": None, "turma_legado": None, "serie_legado": None}]

    with patch("app.api.web.get_school_sql_directory", return_value=_Quebrado()):
        r = _resolver_solicitacoes(linhas)[0]

    assert r["aluno_nome"] == "RA 2024001"


def test_mistura_de_linhas_novas_e_legadas():
    _sem_cache()
    diretorio = _Diretorio({
        "2024002": {"ra": "2024002", "nome": "Carlos Eduardo Lima", "turma": "B", "serie": "8º ano EF"}
    })
    linhas = [
        {"id": 5, "ra": "2024002", "status": "aguardando",
         "nome_legado": None, "turma_legado": None, "serie_legado": None},
        {"id": 6, "ra": None, "status": "aprovado",
         "nome_legado": "Aluno Antigo", "turma_legado": "C", "serie_legado": "5º ano EF"},
    ]

    with patch("app.api.web.get_school_sql_directory", return_value=diretorio):
        resultado = _resolver_solicitacoes(linhas)

    assert [r["aluno_nome"] for r in resultado] == ["Carlos Eduardo Lima", "Aluno Antigo"]
