from flask import render_template_string


def test_identidade_publica_esta_disponivel_nos_templates(app_teste):
    with app_teste.test_request_context('/'):
        conteudo = render_template_string(
            '{{ app_name }}|{{ institution_id }}|{{ institution_name }}|'
            '{{ institution_short_name }}|{{ institution_support_email }}'
        )

    assert conteudo == 'SecureEdu|secureedu-demo|Escola|Escola|'


def test_contexto_publico_nao_expoe_credenciais(app_teste):
    with app_teste.test_request_context('/'):
        contexto = app_teste.template_context_processors[None][1]()

    assert set(contexto) == {
        'app_name',
        'institution_id',
        'institution_name',
        'institution_short_name',
        'institution_support_email',
    }
    assert not any('password' in chave.lower() or 'secret' in chave.lower() for chave in contexto)
