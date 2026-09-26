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

# ANTES do `from tests.apoio ...` abaixo, e não depois: é esta linha que coloca s2e-api no
# caminho de importação, e sem ela o pacote `tests` não é encontrado.
#
# Com a ordem invertida, a suíte só rodava se o diretório atual já fosse s2e-api — aí o próprio
# pytest o acrescentava ao caminho e o import passava por acaso. De qualquer outro lugar,
# quebrava com "No module named 'tests'" antes mesmo de coletar um teste. Foi o que aconteceu no
# deploy/secureedu-deploy, que chama o pytest pelo caminho completo sem entrar na pasta: a etapa
# de testes falhava sempre, e a publicação era cancelada sem chegar a instalar nada.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.apoio import BancoFalso, CorreioFalso

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
    """Zera os caches de módulo entre testes. Sem isso um teste enxerga o aluno que outro
    cadastrou — ou o status de responsável que outro declarou — e a suíte passa a depender da
    ordem de execução."""
    from app.api import web, middleware
    web._cache_diretorio = web.TTLCache(ttl_seconds=300)
    middleware._cache_status_responsavel = middleware.TTLCache(
        ttl_seconds=middleware._TTL_STATUS_RESPONSAVEL)
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
    from app.api import web, pais, middleware
    from app.core import rate_limit, audit_logger, database, mailer
    from app.repositories import user_repo, base_repositories
    # base_repositories importou get_db por conta própria — get_by_id/delete passam por lá.
    # middleware entrou na lista quando pai_required passou a reler responsaveis.status.
    alvos = [web, pais, middleware, rate_limit, audit_logger, user_repo, base_repositories]
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
def sessao_responsavel(cliente, banco):
    """Sessão de responsável já autenticada no portal dos pais.

    Declara a conta como ativa porque `pai_required` passou a reler `responsaveis.status` a cada
    request (com cache curto de 30s): sem essa resposta, todo teste do portal cairia no redirect
    de conta bloqueada antes de chegar à rota que está sendo exercitada.
    """
    banco.responder("SELECT status FROM responsaveis WHERE id", [{'status': 'aprovado'}])
    with cliente.session_transaction() as s:
        s['pai_id'] = 3
        s['pai_email'] = 'mae@teste.com'
        s['pai_nome'] = 'Maria Souza'
    return cliente


@pytest.fixture
def sessao_admin(cliente):
    with cliente.session_transaction() as s:
        s['user_id'] = 1
        s['role'] = 'admin'
        s['username'] = 'admin_teste'
    return cliente
