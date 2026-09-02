"""Infraestrutura compartilhada dos testes de rota.

Os testes antigos exercitam funções auxiliares puras e não precisam de nada disto. Os testes de
rota precisam de um Flask app de verdade sem encostar no Postgres de produção — daí o `BancoFalso`
abaixo, que substitui `get_db()` em todos os módulos que o importaram.

Os dublês (BancoFalso, DiretorioFalso, CorreioFalso) moram em tests/apoio.py — importá-los daqui
faria o conftest ser executado duas vezes e reescrever as variáveis de ambiente no meio da suíte.
"""
import os
import sys
from unittest.mock import patch

import pytest

from tests.apoio import BancoFalso, CorreioFalso

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Antes de qualquer import de app: sem thread de manutenção e sem banco da escola real.
os.environ['MANUTENCAO_AUTOMATICA'] = 'false'
os.environ['SCHOOL_SQL_MOCK'] = 'true'
os.environ.setdefault('SECRET_KEY', 'chave-de-teste-com-mais-de-32-caracteres-aqui')


def _sem_banco_real():
    """Qualquer caminho que escape do BancoFalso deve estourar, nunca abrir o Postgres real."""
    raise AssertionError("teste tentou abrir conexão real com o banco")


@pytest.fixture(scope="session")
def app_teste():
    from app.core import database, migrations
    vazio = BancoFalso()
    # As migrações rodam dentro de create_app(); com o banco falso elas não tocam em produção.
    with patch.object(database, 'get_db', vazio.get_db), \
         patch.object(migrations, 'get_db', vazio.get_db), \
         patch.object(database, '_get_pool', _sem_banco_real):
        from app import create_app
        aplicacao = create_app()
    aplicacao.config['WTF_CSRF_ENABLED'] = False
    aplicacao.config['TESTING'] = True
    return aplicacao


@pytest.fixture(autouse=True)
def _cache_do_diretorio_limpo():
    """O resolvedor de RA usa um TTLCache de módulo (5 min). Sem zerar, um teste enxerga o aluno
    que outro cadastrou e a suíte passa a depender da ordem de execução."""
    from app.api import web
    web._cache_diretorio = web.TTLCache(ttl_seconds=300)
    yield


@pytest.fixture
def banco():
    return BancoFalso()


@pytest.fixture
def correio():
    return CorreioFalso()


@pytest.fixture
def cliente(app_teste, banco, correio):
    """Client Flask com todos os get_db() apontando para o BancoFalso e o envio de e-mail preso."""
    from app.api import web, pais
    from app.core import rate_limit, audit_logger, database, mailer
    from app.repositories import user_repo, base_repositories
    # base_repositories importou get_db por conta própria — get_by_id/delete passam por lá.
    alvos = [web, pais, rate_limit, audit_logger, user_repo, base_repositories]
    ctx = [patch.object(m, 'get_db', banco.get_db) for m in alvos]
    ctx.append(patch.object(database, '_get_pool', _sem_banco_real))
    # web.py importa enviar_email_async dentro das funções (resolve em mailer na hora da chamada);
    # pais.py importou no topo do módulo. Os dois precisam ser trocados.
    ctx.append(patch.object(mailer, 'enviar_email_async', correio))
    ctx.append(patch.object(mailer, 'enviar_email', lambda *a, **k: True))
    ctx.append(patch.object(pais, 'enviar_email_async', correio))
    for c in ctx:
        c.start()
    try:
        with app_teste.test_client() as c:
            yield c
    finally:
        for c in ctx:
            c.stop()


@pytest.fixture
def sessao_admin(cliente):
    with cliente.session_transaction() as s:
        s['user_id'] = 1
        s['role'] = 'admin'
        s['username'] = 'admin_teste'
    return cliente
