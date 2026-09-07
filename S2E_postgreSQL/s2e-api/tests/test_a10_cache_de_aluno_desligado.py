# -*- coding: utf-8 -*-
"""A10 — o cache servia dado vencido para sempre, e aluno desligado nunca saía das telas.

`_cache_diretorio.get(ra) or _cache_diretorio.get_stale(ra)` aplicava o fallback no caminho de
SUCESSO também. Quando o TTL expirava e o diretório respondia sem aquele RA — aluno com
`ativo = 0`, transferido, vínculo revogado — `set()` nunca era chamado e `get_stale()` devolvia o
valor antigo indefinidamente: nome, turma e e-mails dos responsáveis de alguém que a escola já
desligou. O e-mail de "saída liberada" podia ir para um responsável descredenciado. É problema
de LGPD, não de cache.

A distinção só é possível porque o cliente do diretório passou a levantar `SchoolSqlIndisponivel`
em vez de devolver `{}`: resposta vazia agora quer dizer "perguntei e não existe".

E o `_store` não tinha teto de tamanho nem idade máxima — um item por RA consultado, guardado
para sempre, num plano onde a memória é o recurso mais apertado.
"""
import time

import pytest
from unittest.mock import patch

from app.api import web
from app.core.cache import TTLCache
from app.services.school_sql_directory import SchoolSqlIndisponivel

ANA = {"ra": "2024001", "nome": "Ana Beatriz Souza", "turma": "A", "serie": "6º ano EF",
       "foto_url": None, "responsaveis_email": ["mae@teste.com"]}


class _Diretorio:
    """Devolve o que estiver em `alunos`; com `fora_do_ar`, levanta como o cliente real."""

    def __init__(self, alunos, fora_do_ar=False):
        self.alunos = alunos
        self.fora_do_ar = fora_do_ar

    def get_students_by_ras(self, ras):
        if self.fora_do_ar:
            raise SchoolSqlIndisponivel("banco da escola fora do ar")
        return {ra: dict(self.alunos[ra]) for ra in ras if ra in self.alunos}


def _cache_expirado():
    """Cache normal com UMA entrada já vencida — o estado em que o defeito aparecia.

    Escreve em `_store` direto de propósito: não há como envelhecer uma entrada por fora sem
    esperar o TTL, e criar o cache com TTL negativo faria também a entrada NOVA nascer vencida,
    o que mascararia justamente o que se quer observar.
    """
    cache = TTLCache(ttl_seconds=300)
    agora = time.time()
    cache._store["2024001"] = (ANA, agora - 1, agora - 1)   # (valor, vence_em, gravado_em)
    web._cache_diretorio = cache
    return cache


def _buscar(diretorio):
    with patch("app.api.web.get_school_sql_directory", return_value=diretorio):
        return web._buscar_alunos_com_cache(["2024001"])


# ---------------------------------------------------------------- o defeito em si

def test_aluno_desligado_some_das_telas_quando_o_ttl_vence():
    """O diretório respondeu, e não trouxe mais este RA. Isso é uma resposta, não uma falha."""
    _cache_expirado()

    assert _buscar(_Diretorio({})) == {}


def test_o_valor_antigo_nao_e_reaproveitado_no_caminho_de_sucesso():
    """O `or get_stale(...)` disfarçava de dado atual o último nome conhecido."""
    _cache_expirado()

    resultado = _buscar(_Diretorio({}))

    assert "2024001" not in resultado


def test_aluno_com_dados_alterados_e_reescrito_e_nao_servido_do_stale():
    _cache_expirado()
    novo = dict(ANA, turma="B", responsaveis_email=["pai@teste.com"])

    resultado = _buscar(_Diretorio({"2024001": novo}))

    assert resultado["2024001"]["turma"] == "B"
    assert resultado["2024001"]["responsaveis_email"] == ["pai@teste.com"]


# ---------------------------------------------------------------- o fallback continua existindo

def test_com_o_banco_da_escola_fora_do_ar_o_stale_ainda_vale():
    """O fallback não pode ter virado letra morta: é ele que mantém a tela da portaria legível
    durante uma indisponibilidade."""
    _cache_expirado()

    resultado = _buscar(_Diretorio({}, fora_do_ar=True))

    assert resultado["2024001"]["nome"] == "Ana Beatriz Souza"


def test_dado_dentro_do_ttl_nem_consulta_o_diretorio():
    web._cache_diretorio = TTLCache(ttl_seconds=300)
    web._cache_diretorio.set("2024001", ANA)

    class _NuncaChamado:
        def get_students_by_ras(self, ras):
            raise AssertionError("não deveria ter consultado o banco da escola")

    assert _buscar(_NuncaChamado())["2024001"]["nome"] == "Ana Beatriz Souza"


# ---------------------------------------------------------------- limites do cache

def test_o_stale_tem_idade_maxima():
    """Um valor velho demais deixa de ser fallback e vira desinformação — além de ser dado
    pessoal de menor guardado sem necessidade."""
    cache = TTLCache(ttl_seconds=-1, stale_seconds=-1)
    cache.set("2024001", ANA)

    assert cache.get_stale("2024001") is None


def test_o_stale_vale_dentro_da_idade_maxima():
    cache = TTLCache(ttl_seconds=-1, stale_seconds=3600)
    cache.set("2024001", ANA)

    assert cache.get_stale("2024001") == ANA


def test_a_entrada_velha_demais_e_descartada_e_nao_so_escondida():
    cache = TTLCache(ttl_seconds=-1, stale_seconds=-1)
    cache.set("2024001", ANA)

    cache.get_stale("2024001")

    assert len(cache) == 0


def test_o_cache_tem_teto_de_tamanho():
    """Sem teto, `_store` crescia com um item por RA consultado e nunca devolvia memória."""
    cache = TTLCache(ttl_seconds=300, maximo=3)

    for i in range(10):
        cache.set(f"20240{i:02d}", dict(ANA, ra=f"20240{i:02d}"))

    assert len(cache) == 3


def test_o_descarte_e_por_menos_usado_recentemente():
    """Numa escola, quem sai primeiro é o aluno que ninguém procurou hoje."""
    cache = TTLCache(ttl_seconds=300, maximo=2)
    cache.set("a", {"ra": "a"})
    cache.set("b", {"ra": "b"})

    cache.get("a")            # 'a' volta a ser o mais recente
    cache.set("c", {"ra": "c"})

    assert cache.get("a") is not None
    assert cache.get("b") is None
    assert cache.get("c") is not None


def test_da_para_invalidar_uma_entrada_sem_esperar_o_ttl():
    cache = TTLCache(ttl_seconds=300)
    cache.set("2024001", ANA)

    cache.descartar("2024001")

    assert cache.get("2024001") is None
    assert cache.get_stale("2024001") is None
