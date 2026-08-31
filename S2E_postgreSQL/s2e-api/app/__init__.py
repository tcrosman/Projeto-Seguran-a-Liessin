from flask import Flask, session, render_template
from flask_wtf.csrf import CSRFProtect
from flask_talisman import Talisman
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
from datetime import datetime, timedelta
import os
import psycopg2
from app.core.logging_config import obter

_log = obter()

# Carrega variáveis de ambiente
load_dotenv()

def create_app():
    """Factory do Flask - cria e configura a aplicação"""
    app = Flask(__name__)

    # Atrás do proxy do Render, request.remote_addr é o IP do proxy, igual para todos os
    # visitantes — o que tornava o rate limit por IP ou inócuo ou capaz de trancar todo mundo de
    # uma vez. Com ProxyFix, remote_addr passa a ser o IP real, lido de X-Forwarded-For.
    # x_for=1: confia em exatamente um proxy à frente (o do Render). Aumentar esse número sem ter
    # os proxies correspondentes deixaria o cliente forjar o próprio IP no cabeçalho.
    # x_proto=1 é necessário para o Talisman saber que a request chegou por HTTPS e não entrar em
    # loop de redirect. x_host fica de fora de propósito: nada aqui depende de request.host (os
    # links de e-mail vêm de BASE_URL), e confiar em X-Forwarded-Host permitiria envenenar o
    # destino do redirect de HTTPS.
    if os.getenv('TRUST_PROXY', 'true').lower() == 'true':
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    # Configurações
    # LGPD Art. 46 (💻 App obligation) — reject startup if secret key is insecure
    _secret = os.getenv('SECRET_KEY', 'troque-esta-chave-em-producao')
    _unsafe_defaults = {'troque-esta-chave-em-producao', 'dev', 'secret', 'changeme'}
    if _secret in _unsafe_defaults or len(_secret) < 32:
        raise RuntimeError(
            "SECRET_KEY inválida. Defina uma chave aleatória com no mínimo 32 caracteres "
            "no arquivo .env. Exemplo: SECRET_KEY=" + os.urandom(32).hex()
        )
    app.secret_key = _secret
    app.config['UPLOAD_FOLDER'] = os.getenv('UPLOAD_FOLDER', 'storage')
    app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('MAX_CONTENT_LENGTH', 16 * 1024 * 1024))

    # Criar pastas necessárias
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'photos'), exist_ok=True)
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'documents'), exist_ok=True)
    os.makedirs('logs', exist_ok=True)
    
    # LGPD Art. 46 (💻 App obligation) — CSRF protection on all state-changing forms
    csrf = CSRFProtect(app)
    app.extensions['csrf_protect'] = csrf  # stored so REST API can self-exempt

    # LGPD Art. 46 (💻 App obligation) — transport security and browser security headers
    # FORCE_HTTPS=true only when TLS certificate is configured on the server
    _csp = {
        'default-src': "'self'",
        'script-src':  ["'self'", "'unsafe-inline'"],
        'style-src':   ["'self'", "'unsafe-inline'"],
        'img-src':     ["'self'", "data:"],
        'font-src':    "'self'",
        'connect-src': "'self'",
        'frame-ancestors': "'none'",
    }
    _force_https = os.getenv('FORCE_HTTPS', 'false').lower() == 'true'
    # O cookie de sessão precisa de Secure sempre que o site é servido por HTTPS, mesmo quando
    # FORCE_HTTPS está desligado — essa flag controla o *redirect*, não o transporte. Um deploy
    # com TLS no proxy e FORCE_HTTPS=false mandaria o cookie de sessão em claro.
    _https = _force_https or os.getenv('BASE_URL', '').startswith('https://')
    Talisman(
        app,
        force_https=_force_https,
        session_cookie_secure=_https,
        strict_transport_security=True,
        strict_transport_security_max_age=31536000,
        strict_transport_security_include_subdomains=True,
        frame_options='DENY',
        content_security_policy=_csp,
    )
    # Lax barra o envio do cookie em POST cross-site (CSRF), sem quebrar a volta de links
    # externos — como o link de redefinição de senha que chega por e-mail.
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SECURE'] = _https

    # LGPD Art. 46 (💻 App obligation) — session lifetime and idle timeout
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)
    _SESSION_IDLE_MINUTES = 30

    @app.before_request
    def enforce_session_timeout():
        # Vale para as duas sessões: a de funcionário (user_id) e a de responsável (pai_id), que
        # antes ficava aberta indefinidamente. 'pai_temp_id' entra junto para a sessão parcial
        # entre a senha e o 2FA não sobreviver a uma máquina abandonada.
        if not any(k in session for k in ('user_id', 'pai_id', 'pai_temp_id')):
            return
        session.permanent = True
        last_seen = session.get('last_seen')
        now = datetime.utcnow()
        if last_seen:
            elapsed = now - datetime.fromisoformat(last_seen)
            if elapsed > timedelta(minutes=_SESSION_IDLE_MINUTES):
                session.clear()
                return
        session['last_seen'] = now.isoformat()

    # Migração automática — idempotente (usa CREATE TABLE/COLUMN IF NOT EXISTS)
    try:
        from app.core.database import migrate_database
        from app.core.migrations import run_migrations
        migrate_database()
        run_migrations()
    except Exception as _mig_err:
        _log.warning(f"[WARN] Migrações não puderam ser aplicadas automaticamente: {_mig_err}")

    # Limpeza/expiração periódica em thread de fundo — antes essas escritas rodavam dentro das
    # rotas GET de listagem. Desligue com MANUTENCAO_AUTOMATICA=false se preferir só o cron
    # externo (python manutencao.py).
    if os.getenv('MANUTENCAO_AUTOMATICA', 'true').lower() == 'true':
        from app.core.maintenance import iniciar_agendador
        iniciar_agendador()

    # Importar e registrar rotas
    from app.api import web
    web.register_routes(app)

    from app.api import pais
    pais.register_parent_routes(app)

    # Tratamento global de falha de conexão com o banco de dados
    @app.errorhandler(psycopg2.OperationalError)
    def handle_db_error(e):
        return render_template("errors/db_unavailable.html"), 503

    @app.errorhandler(psycopg2.InterfaceError)
    def handle_db_interface_error(e):
        return render_template("errors/db_unavailable.html"), 503

    return app