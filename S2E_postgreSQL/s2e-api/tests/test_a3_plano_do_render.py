# -*- coding: utf-8 -*-
"""A3 — o plano free do Render hiberna, e a primeira requisição do dia é a portaria.

Depois de 15 min sem tráfego o serviço dorme e leva ~50s para acordar. A primeira requisição do
dia é a portaria abrindo /saidas às 14h55, com os pais já a caminho do portão: ela trava por
quase um minuto. Some-se a RAM apertada, que aperta o cache do diretório e o pool de conexões.

Não é código, e por isso mesmo some se não estiver escrito em algum lugar que alguém leia — daí
o teste sobre o render.yaml e sobre o checklist de implantação do README.
"""
import os

RAIZ_API = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAIZ_PROJETO = os.path.dirname(RAIZ_API)


def _ler(caminho):
    with open(caminho, encoding='utf-8') as f:
        return f.read()


def test_o_servico_nao_esta_declarado_no_plano_free():
    render = _ler(os.path.join(RAIZ_API, 'render.yaml'))
    declarados = [l.split(':', 1)[1].strip()
                  for l in render.splitlines()
                  if l.strip().startswith('plan:')]

    assert declarados, "render.yaml deixou de declarar o plano"
    assert 'free' not in declarados


def test_o_plano_pago_e_o_que_viabiliza_o_passo_de_release():
    """preDeployCommand só existe em plano pago; declarar um sem o outro é meia correção."""
    render = _ler(os.path.join(RAIZ_API, 'render.yaml'))
    if 'preDeployCommand' in render:
        assert 'plan: free' not in render


def test_o_checklist_de_implantacao_existe_e_cobre_o_plano():
    """A escolha do plano não aparece em teste funcional nenhum e não dá erro quando está
    errada — só lentidão no pior momento. Precisa estar no checklist que alguém lê."""
    readme = _ler(os.path.join(RAIZ_PROJETO, 'README.md'))

    assert 'Checklist de implantação' in readme
    assert 'free' in readme
    for variavel in ('SCHOOL_SQL_MOCK', 'TRUST_PROXY', 'SECRET_KEY', 'MAILER_FALLBACK_LOG'):
        assert variavel in readme, f"{variavel} ficou fora do checklist"
