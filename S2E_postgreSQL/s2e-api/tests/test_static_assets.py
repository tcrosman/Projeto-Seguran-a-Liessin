from pathlib import Path


def test_css_publico_existe(cliente):
    resposta = cliente.get('/static/css/style.css')

    assert resposta.status_code == 200
    assert resposta.mimetype == 'text/css'
    assert resposta.data


def test_favicon_publico_usa_logo_existente(cliente):
    resposta = cliente.get('/favicon.ico')

    assert resposta.status_code == 200
    assert resposta.mimetype == 'image/png'
    assert resposta.data.startswith(b'\x89PNG\r\n\x1a\n')


def test_logo_usado_pelo_favicon_existe():
    raiz_app = Path(__file__).resolve().parents[1] / 'app'

    assert (raiz_app / 'static' / 'images' / 'logo-dark.png').is_file()
