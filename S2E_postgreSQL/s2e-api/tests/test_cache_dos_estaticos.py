# -*- coding: utf-8 -*-
"""O CSS era pedido sempre pelo mesmo endereço, e o nginx manda guardá-lo por 7 dias.

O efeito: para quem já tinha aberto o site, o navegador não perguntava se havia mudado. Uma
correção de layout publicada hoje só apareceria quando o prazo vencesse — até uma semana depois —
e nesse meio-tempo a tela continuava a antiga, como se o deploy não tivesse funcionado. Foi
exatamente o que aconteceu ao publicar as correções de mobile.

A versão no endereço (`?v=<mtime>`) resolve: cada publicação gera um endereço diferente, o
navegador busca de novo na hora, e quem não mudou nada segue aproveitando o cache.
"""
from flask import render_template_string


def _render(app, template):
    with app.test_request_context('/'):
        return render_template_string(template)


def test_o_css_carrega_versao_no_endereco(app_teste):
    saida = _render(app_teste, "{{ estatico('css/style.css') }}")

    assert saida.startswith('/static/css/style.css?v=')
    assert saida.split('?v=')[1].isdigit()


def test_as_paginas_usam_o_endereco_versionado(app_teste):
    resposta = app_teste.test_client().get('/pais/login')

    corpo = resposta.get_data(as_text=True)
    assert '/static/css/style.css?v=' in corpo
    assert 'href="/static/css/style.css"' not in corpo, "endereço sem versão volta a cachear 7 dias"


def test_a_versao_e_a_data_do_arquivo_publicado(app_teste):
    """É isto que faz a correção chegar na hora: o deploy reescreve o arquivo, o mtime muda, e o
    endereço muda junto. Se a versão fosse fixa (um número no código, por exemplo), publicar CSS
    novo sem lembrar de alterá-la traria o problema de volta."""
    import os
    from pathlib import Path

    css = Path(__file__).resolve().parents[1] / 'app' / 'static' / 'css' / 'style.css'

    saida = _render(app_teste, "{{ estatico('css/style.css') }}")

    assert saida.endswith(f"?v={int(os.path.getmtime(css))}")


def test_arquivo_ausente_nao_derruba_a_pagina(app_teste):
    # Sem o arquivo não há versão a calcular; serve o endereço puro em vez de estourar no render.
    saida = _render(app_teste, "{{ estatico('css/nao-existe.css') }}")

    assert saida == '/static/css/nao-existe.css'
