# -*- coding: utf-8 -*-
"""A1 — a capacidade real era de 2 requisições simultâneas em toda a escola.

`gunicorn --workers 2` com worker sync e sem `--threads` atende UMA requisição por processo. A
portaria carregando /saidas mais um pai abrindo o portal já enchiam a capacidade, e o terceiro
acesso ficava na fila. DB_POOL_MAX=5 era inútil nesse desenho: nunca haveria 5 requisições
concorrentes num worker que só faz uma por vez.

E `--timeout 120` significa que uma requisição travada só devolve o worker depois de dois
minutos — com metade da escola parada nesse intervalo, às 15h.

Estes testes olham os arquivos de deploy porque é lá que o defeito mora: nenhum teste funcional
enxerga o dimensionamento do servidor.
"""
import os
import re

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ler(*partes):
    with open(os.path.join(RAIZ, *partes), encoding='utf-8') as f:
        return f.read()


def _comando_do_gunicorn(texto):
    linha = next(l for l in texto.splitlines() if 'gunicorn' in l and '#' not in l.split('gunicorn')[0])
    return linha


@pytest.fixture(params=['Procfile', 'render.yaml'])
def comando(request):
    """Os dois arquivos declaram o mesmo servidor e precisam continuar de acordo — hoje a linha
    é duplicada, e um deploy que use só um deles não pode receber metade da correção."""
    return _comando_do_gunicorn(_ler(request.param))


def test_o_worker_atende_mais_de_uma_requisicao_por_vez(comando):
    assert '--threads' in comando
    threads = int(re.search(r'--threads\s+(\d+)', comando).group(1))
    assert threads >= 4


def test_requisicao_travada_devolve_o_worker_em_menos_de_um_minuto(comando):
    timeout = int(re.search(r'--timeout\s+(\d+)', comando).group(1))
    assert timeout <= 60


def test_procfile_e_render_declaram_o_mesmo_servidor():
    """Enquanto a linha for duplicada, divergir entre os dois é um erro silencioso."""
    assert _comando_do_gunicorn(_ler('Procfile')).split('gunicorn', 1)[1] == \
           _comando_do_gunicorn(_ler('render.yaml')).split('gunicorn', 1)[1]


def test_o_pool_cobre_as_threads_de_um_worker():
    """O pool é criado depois do fork, um por processo: DB_POOL_MAX menor que o número de
    threads faria as requisições excedentes esperarem por conexão sem necessidade."""
    render = _ler('render.yaml')
    threads = int(re.search(r'--threads\s+(\d+)', render).group(1))
    pool = int(re.search(r'key: DB_POOL_MAX\s*\n\s*value: "(\d+)"', render).group(1))
    assert pool >= threads


def test_a_espera_por_conexao_cabe_dentro_do_timeout_do_gunicorn():
    """Senão o gunicorn mata a requisição antes de ela receber a página de indisponibilidade."""
    from app.core import database
    timeout = int(re.search(r'--timeout\s+(\d+)', _ler('render.yaml')).group(1))
    assert database.ESPERA_CONEXAO_SEG < timeout
