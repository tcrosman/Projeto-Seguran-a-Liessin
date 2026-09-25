def test_raiz_prioriza_portal_dos_responsaveis(cliente):
    resposta = cliente.get('/', follow_redirects=False)

    assert resposta.status_code == 302
    assert resposta.headers['Location'].endswith('/pais')


def test_portal_dos_colaboradores_tem_endereco_proprio(cliente):
    resposta = cliente.get('/colaboradores')

    assert resposta.status_code == 200
    assert 'Bem-vindo' in resposta.get_data(as_text=True)


def test_login_antigo_por_post_continua_compativel(cliente, banco):
    banco.responder("SELECT id, role, username, password FROM usuarios", [])

    resposta = cliente.post('/', data={'u': 'inexistente', 's': 'incorreta'})

    assert resposta.status_code == 200
    assert 'Usuário ou senha incorretos' in resposta.get_data(as_text=True)


def test_portal_dos_responsaveis_aponta_para_colaboradores(cliente):
    resposta = cliente.get('/pais/login')

    assert resposta.status_code == 200
    assert 'href="/colaboradores"' in resposta.get_data(as_text=True)
