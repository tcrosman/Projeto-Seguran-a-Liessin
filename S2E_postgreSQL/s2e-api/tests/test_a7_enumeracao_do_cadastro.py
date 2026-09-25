# -*- coding: utf-8 -*-
"""A7 — o porteiro enumerava o cadastro inteiro por /portaria/buscar_aluno.

`_VIGIA_BLOCKED_ENDPOINTS` barra `lista_alunos` e `historico_do_aluno` com comentário explícito
de que o porteiro não deve ver o cadastro da escola. Mas `portaria_buscar_aluno` ficava de fora
da lista, tinha só `@login_required`, devolvia JSON com ra, nome, turma e série, e o parâmetro
`q` não tinha tamanho mínimo. `?q=a`, `?q=e`, `?q=202400`... e em poucas dezenas de requisições
sai a escola inteira — exatamente o dado que os dois bloqueios acima pretendiam negar.

O endpoint só existe para alimentar /registrar_saida, que o vigia nem pode acessar.
"""
import json

import pytest
from unittest.mock import patch

from tests.apoio import DiretorioFalso
from app.api.web import MIN_CARACTERES_BUSCA, RESULTADOS_AUTOCOMPLETE

TURMA_INTEIRA = {
    f"20240{i:02d}": {"ra": f"20240{i:02d}", "nome": f"Aluno Numero {i}", "turma": "A",
                      "serie": "6º ano EF", "foto_url": None, "responsaveis_email": []}
    for i in range(1, 41)
}


@pytest.fixture
def vigia(cliente):
    with cliente.session_transaction() as s:
        s['user_id'] = 9
        s['role'] = 'vigia'
        s['username'] = 'porteiro_teste'
    return cliente


@pytest.fixture
def secretaria(cliente):
    with cliente.session_transaction() as s:
        s['user_id'] = 2
        s['role'] = 'basico'
        s['username'] = 'secretaria_teste'
    return cliente


def _buscar(sessao, q):
    with patch("app.api.web.get_school_sql_directory",
               return_value=DiretorioFalso(TURMA_INTEIRA)):
        return sessao.get("/portaria/buscar_aluno?q=" + q)


# ---------------------------------------------------------------- o vigia perde o endpoint

def test_o_vigia_nao_alcanca_a_busca_de_alunos(vigia):
    r = _buscar(vigia, "Aluno")

    assert r.status_code == 403


def test_a_busca_do_vigia_e_barrada_pelo_mesmo_lugar_das_outras_telas(vigia):
    """Não é uma checagem solta na rota: entra na lista que já nega /alunos e a ficha do aluno,
    para as três não voltarem a divergir."""
    from app.api.middleware import _VIGIA_BLOCKED_ENDPOINTS

    assert 'portaria_buscar_aluno' in _VIGIA_BLOCKED_ENDPOINTS
    assert 'lista_alunos' in _VIGIA_BLOCKED_ENDPOINTS


def test_quem_registra_saida_continua_buscando(secretaria):
    """O endpoint existe para /registrar_saida; quem pode registrar precisa dele."""
    r = _buscar(secretaria, "Aluno")

    assert r.status_code == 200
    assert json.loads(r.get_data(as_text=True))


# ---------------------------------------------------------------- piso e teto

def test_termo_curto_nao_consulta_o_banco_da_escola(secretaria):
    """`?q=a` era uma fatia alfabética do cadastro. E cada busca é uma varredura da tabela."""
    escola = DiretorioFalso(TURMA_INTEIRA)
    with patch("app.api.web.get_school_sql_directory", return_value=escola):
        r = secretaria.get("/portaria/buscar_aluno?q=a")

    assert json.loads(r.get_data(as_text=True)) == []
    assert escola.buscas == [], "a consulta nem deveria ter saído"


def test_o_piso_e_de_pelo_menos_tres_caracteres():
    assert MIN_CARACTERES_BUSCA >= 3


def test_espaco_em_branco_nao_conta_como_caractere(secretaria):
    escola = DiretorioFalso(TURMA_INTEIRA)
    with patch("app.api.web.get_school_sql_directory", return_value=escola):
        secretaria.get("/portaria/buscar_aluno?q=%20%20a%20%20")

    assert escola.buscas == []


def test_a_resposta_tem_teto_de_resultados(secretaria):
    """Sem teto, um termo comum ('a', 'Silva') devolvia o cadastro em blocos grandes."""
    r = _buscar(secretaria, "Aluno")

    assert len(json.loads(r.get_data(as_text=True))) <= RESULTADOS_AUTOCOMPLETE


def test_o_teto_e_pedido_ao_diretorio_e_nao_so_cortado_na_resposta(secretaria):
    """Cortar depois ainda faria o banco da escola montar a lista inteira."""
    escola = DiretorioFalso(TURMA_INTEIRA)
    with patch("app.api.web.get_school_sql_directory", return_value=escola):
        secretaria.get("/portaria/buscar_aluno?q=Aluno")

    assert escola.limites == [RESULTADOS_AUTOCOMPLETE]
