from flask import render_template_string


def test_identidade_publica_esta_disponivel_nos_templates(app_teste):
    with app_teste.test_request_context('/'):
        conteudo = render_template_string(
            '{{ app_name }}|{{ institution_id }}|{{ institution_name }}|'
            '{{ institution_short_name }}|{{ institution_support_email }}'
        )

    assert conteudo == 'SecureEdu|secureedu-demo|Escola|Escola|'


def test_contexto_publico_nao_expoe_credenciais(app_teste):
    """O que ESTE app põe nos templates é lista fechada; credenciais não entram.

    Filtrado por módulo, e não por posição na lista: o Flask e o flask_wtf registram os deles
    antes e depois dos nossos, e o que cada um injeta muda entre versões — o flask_wtf mais novo
    do servidor acrescenta `csrf_meta_tag` ao `csrf_token`, e fixar a lista inteira fazia a suíte
    passar na máquina de quem escreveu e quebrar no deploy.
    """
    processadores = app_teste.template_context_processors[None]

    with app_teste.test_request_context('/'):
        nosso = {}
        for processador in processadores:
            if processador.__module__ == 'app':
                nosso.update(processador())

        todo_o_contexto = {}
        for processador in processadores:
            todo_o_contexto.update(processador())

    assert set(nosso) == {
        'estatico',
        'app_name',
        'institution_id',
        'institution_name',
        'institution_short_name',
        'institution_support_email',
    }
    # Vale para o contexto inteiro, inclusive o que vier de biblioteca: nenhuma credencial
    # pode chegar ao template, não importa quem a tenha colocado lá.
    assert not any('password' in chave.lower() or 'secret' in chave.lower()
                   for chave in todo_o_contexto)
