# -*- coding: utf-8 -*-
"""A tela de alunos vinha como uma lista corrida em ordem alfabética. A escola organiza os alunos
por série (Maternal 1 até 3º ano EM) e, dentro dela, por turma (A, B, C...) — e é assim que a
secretaria procura alguém.

Dois cuidados que os testes fixam: a ordem é a pedagógica, não a alfabética (que colocaria
"1º ano EF" antes de "Maternal 1"), e a série vem como texto livre do banco da instituição, então
"1 ano" e "1º ano EF" precisam cair no mesmo grupo em vez de virarem duas seções.
"""
import re

from unittest.mock import patch

import pytest

from tests.apoio import DiretorioFalso


def _aluno(ra, nome, serie, turma):
    return {"ra": ra, "nome": nome, "serie": serie, "turma": turma, "foto_url": None}


ESCOLA = {a["ra"]: a for a in [
    _aluno("300", "Bruno Lima", "3º ano EM", "C"),
    _aluno("100", "Ana Souza", "Maternal 1", "A"),
    _aluno("200", "Carla Dias", "1º ano EF", "B"),
    _aluno("201", "Diego Alves", "1 ano", "A"),          # mesma série, escrita de outro jeito
    _aluno("202", "Elisa Moreira", "1º ano EF", "A"),
    _aluno("150", "Felipe Rocha", "Pré 2", "A"),
]}


@pytest.fixture
def escola():
    return DiretorioFalso(ESCOLA)


def _abrir(cliente, escola, caminho="/alunos"):
    with patch("app.api.web.get_school_sql_directory", return_value=escola):
        return cliente.get(caminho).get_data(as_text=True)


def _series_na_ordem(corpo):
    return re.findall(r'class="group-title">([^<]+)</span>', corpo)


def _alunos_na_ordem(corpo):
    return re.findall(r'<strong>([^<]+)</strong>', corpo)


# ---------------------------------------------------------------- agrupamento

def test_alunos_saem_agrupados_por_serie(sessao_admin, escola):
    corpo = _abrir(sessao_admin, escola)

    assert _series_na_ordem(corpo) == ['Maternal 1', 'Pré 2', '1º ano EF', '3º ano EM']


def test_a_ordem_e_pedagogica_e_nao_alfabetica(sessao_admin, escola):
    """Em ordem alfabética, "1º ano EF" viria antes de "Maternal 1" e "Pré 2" depois do 9º ano."""
    series = _series_na_ordem(_abrir(sessao_admin, escola))

    assert series.index('Maternal 1') < series.index('Pré 2') < series.index('1º ano EF')
    assert series != sorted(series)


def test_series_escritas_de_formas_diferentes_caem_no_mesmo_grupo(sessao_admin, escola):
    """"1 ano" e "1º ano EF" são a mesma turma de alunos; duas seções seriam um erro de leitura."""
    corpo = _abrir(sessao_admin, escola)

    assert _series_na_ordem(corpo).count('1º ano EF') == 1
    assert "Diego Alves" in corpo and "Carla Dias" in corpo


def test_dentro_da_serie_ordena_por_turma_e_depois_por_nome(sessao_admin, escola):
    corpo = _abrir(sessao_admin, escola, "/alunos?serie=1º ano EF")

    # turma A (Diego, Elisa) antes da turma B (Carla)
    assert _alunos_na_ordem(corpo) == ["Diego Alves", "Elisa Moreira", "Carla Dias"]


def test_serie_desconhecida_nao_faz_o_aluno_sumir(sessao_admin):
    """O banco da escola é de outra equipe: um nome que ainda não mapeamos não pode custar o
    aluno na tela — ele vai para um grupo próprio, no fim."""
    escola = DiretorioFalso({**ESCOLA, "999": _aluno("999", "Zeca Turma Nova", "Curso Livre", "A")})

    corpo = _abrir(sessao_admin, escola)

    assert "Zeca Turma Nova" in corpo
    assert _series_na_ordem(corpo)[-1] == 'Curso Livre'


def test_aluno_sem_turma_nao_quebra_a_tela(sessao_admin):
    escola = DiretorioFalso({"1": _aluno("1", "Sem Turma", "Pré 1", None)})

    corpo = _abrir(sessao_admin, escola)

    assert "Sem Turma" in corpo


def test_cada_grupo_mostra_quantos_alunos_e_quais_turmas(sessao_admin, escola):
    corpo = _abrir(sessao_admin, escola)
    # a partir do cabeçalho do grupo — a primeira ocorrência do nome está no seletor de filtro
    bloco = corpo.split('class="group-title">1º ano EF</span>')[1].split('</table>')[0]

    assert 'class="group-badge">3<' in bloco          # três alunos no 1º ano EF
    assert 'turma-chip">A<' in bloco and 'turma-chip">B<' in bloco


# ---------------------------------------------------------------- filtros

def test_filtro_por_serie(sessao_admin, escola):
    corpo = _abrir(sessao_admin, escola, "/alunos?serie=Maternal 1")

    assert _series_na_ordem(corpo) == ['Maternal 1']
    assert "Ana Souza" in corpo and "Bruno Lima" not in corpo


def test_filtro_por_turma_atravessa_as_series(sessao_admin, escola):
    corpo = _abrir(sessao_admin, escola, "/alunos?turma=A")

    assert _alunos_na_ordem(corpo) == ["Ana Souza", "Felipe Rocha", "Diego Alves", "Elisa Moreira"]


def test_filtros_de_serie_e_turma_se_combinam(sessao_admin, escola):
    corpo = _abrir(sessao_admin, escola, "/alunos?serie=1º ano EF&turma=B")

    assert _alunos_na_ordem(corpo) == ["Carla Dias"]


def test_filtro_se_combina_com_a_busca(sessao_admin, escola):
    corpo = _abrir(sessao_admin, escola, "/alunos?busca=Elisa&serie=1º ano EF")

    assert _alunos_na_ordem(corpo) == ["Elisa Moreira"]


def test_as_opcoes_do_filtro_saem_do_conjunto_inteiro(sessao_admin, escola):
    """Se as opções viessem do resultado já filtrado, escolher uma série apagaria as outras do
    seletor e não haveria como voltar sem limpar tudo."""
    corpo = _abrir(sessao_admin, escola, "/alunos?serie=Maternal 1")

    for serie in ['Maternal 1', 'Pré 2', '1º ano EF', '3º ano EM']:
        assert f'<option value="{serie}"' in corpo


def test_filtro_sem_resultado_explica(sessao_admin, escola):
    corpo = _abrir(sessao_admin, escola, "/alunos?serie=Maternal 1&turma=C")

    assert "Nenhum aluno encontrado com esses filtros" in corpo


def test_a_serie_selecionada_fica_marcada_no_seletor(sessao_admin, escola):
    corpo = _abrir(sessao_admin, escola, "/alunos?serie=Pré 2")

    assert '<option value="Pré 2" selected>' in corpo


# ---------------------------------------------------------------- a lista oficial da escola

def test_as_dezesseis_series_da_escola_estao_declaradas():
    from app.config import Config

    assert Config.SERIES == [
        'Maternal 1', 'Maternal 2', 'Pré 1', 'Pré 2',
        '1º ano EF', '2º ano EF', '3º ano EF', '4º ano EF', '5º ano EF',
        '6º ano EF', '7º ano EF', '8º ano EF', '9º ano EF',
        '1º ano EM', '2º ano EM', '3º ano EM',
    ]


@pytest.mark.parametrize("escrita,oficial", [
    ("Maternal 1", "Maternal 1"), ("maternal ii", "Maternal 2"),
    ("Berçário 1", "Maternal 1"),          # nome anterior da etapa, ainda em dados antigos
    ("Pré 1", "Pré 1"), ("pre ii", "Pré 2"),
    ("1 ano", "1º ano EF"), ("2 ano", "2º ano EF"), ("3 ano", "3º ano EF"),
    ("9 ano", "9º ano EF"), ("9º ano EF", "9º ano EF"),
    ("1 ano EM", "1º ano EM"), ("3 ano em", "3º ano EM"),
])
def test_variacoes_de_escrita_resolvem_para_a_serie_oficial(escrita, oficial):
    from app.core.validators import normalizar_serie

    assert normalizar_serie(escrita) == oficial


def test_ano_sem_sufixo_e_do_fundamental_nao_do_medio():
    """Antes "1 ano" caía em "1º ano EM": uma criança de 6 anos apareceria no grupo do ensino
    médio. A escola nomeia 1 a 9 no fundamental e sempre põe "EM" no médio."""
    from app.core.validators import normalizar_serie

    assert normalizar_serie("1 ano") == "1º ano EF"
    assert normalizar_serie("1 ano EM") == "1º ano EM"


def test_o_mapa_de_variacoes_nao_tem_chave_repetida():
    """Chave repetida num dicionário literal não é erro: a última vence, em silêncio. Foi assim
    que "3 ano" apontava para o fundamental numa linha e para o médio noutra."""
    import collections
    from app.config import Config

    fonte = open(Config.__module__.replace('.', '/') + '.py', encoding='utf-8').read()
    bloco = fonte.split('NORMALIZE_SERIE = {')[1].split('\n    }')[0]
    chaves = re.findall(r"'([^']+)':\s*'[^']+'", bloco)

    repetidas = [k for k, n in collections.Counter(chaves).items() if n > 1]
    assert repetidas == []


def test_toda_serie_oficial_tem_lugar_na_ordem():
    from app.config import Config
    from app.core.validators import ordem_da_serie

    posicoes = [ordem_da_serie(s) for s in Config.SERIES]
    assert posicoes == list(range(len(Config.SERIES)))
    # desconhecida vai para o fim, não para o começo
    assert ordem_da_serie('Curso Livre') == len(Config.SERIES)
