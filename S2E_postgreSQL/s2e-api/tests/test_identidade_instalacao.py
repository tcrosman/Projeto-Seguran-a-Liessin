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
        # O índice 0 é o do próprio Flask (request, session, g); do 1 em diante são os nossos.
        # Somados, e não por posição: a checagem vale para qualquer processador que seja
        # registrado depois, que é justamente o que precisa continuar sob vigilância.
        contexto = {}
        for processador in app_teste.template_context_processors[None][1:]:
            contexto.update(processador())

    assert set(contexto) == {
        'csrf_token',   # do flask_wtf, não nosso — mas passa a ficar sob a mesma vigilância
        'estatico',
        'app_name',
        'institution_id',
        'institution_name',
        'institution_short_name',
        'institution_support_email',
    }
    assert not any('password' in chave.lower() or 'secret' in chave.lower() for chave in contexto)
