from flask import Flask, session, render_template, request
from flask_wtf.csrf import CSRFProtect
from flask_talisman import Talisman
from dotenv import load_dotenv
from datetime import datetime, timedelta
import os
import psycopg2

# Carrega variáveis de ambiente
load_dotenv()

def create_app():
    """Factory do Flask - cria e configura a aplicação"""
    app = Flask(__name__)

    # Configurações
    # LGPD Art. 46 (💻 App obligation) — reject startup if secret key is insecure
    _secret = os.getenv('SECRET_KEY', 'troque-esta-chave-em-producao')
    _unsafe_defaults = {'troque-esta-chave-em-producao', 'dev', 'secret', 'changeme'}
    if (_secret in _unsafe_defaults or len(_secret) < 32
            or any(marker in _secret.lower() for marker in ('substitua', 'troque', 'changeme', 'example'))):
        raise RuntimeError("SECRET_KEY inválida. Defina uma chave aleatória com no mínimo 32 caracteres.")
    app.secret_key = _secret
    app.config['UPLOAD_FOLDER'] = os.getenv('UPLOAD_FOLDER', 'storage')
    app.config['BASE_URL'] = os.getenv('BASE_URL', 'https://portalsecureedu.com').rstrip('/')
    app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('MAX_CONTENT_LENGTH', 16 * 1024 * 1024))
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE', 'true').lower() == 'true',
        SESSION_COOKIE_SAMESITE='Lax',
    )

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
    Talisman(
        app,
        force_https=os.getenv('FORCE_HTTPS', 'false').lower() == 'true',
        strict_transport_security=True,
        strict_transport_security_max_age=31536000,
        strict_transport_security_include_subdomains=True,
        frame_options='DENY',
        content_security_policy=_csp,
    )

    # LGPD Art. 46 (💻 App obligation) — session lifetime and idle timeout
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)
    _SESSION_IDLE_MINUTES = 30

    @app.before_request
    def enforce_session_timeout():
        if not any(key in session for key in ('user_id', 'pai_id', 'pai_temp_id')):
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

    @app.after_request
    def prevent_authenticated_page_caching(response):
        if ('user_id' in session or 'pai_id' in session or 'pai_temp_id' in session
                or request.path.startswith(('/resetar_senha/', '/pais/resetar_senha/'))):
            response.headers['Cache-Control'] = 'no-store, private'
            response.headers['Pragma'] = 'no-cache'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    # Migração automática — idempotente (usa CREATE TABLE IF NOT EXISTS)
    try:
        from app.core.database import migrate_database
        from app.core.migrations import run_migrations
        migrate_database()
        run_migrations()
    except Exception as _mig_err:
        raise RuntimeError("A estrutura do banco não pôde ser verificada; inicialização interrompida.") from _mig_err

    # Importar e registrar rotas
    from app.api import web
    web.register_routes(app)

    from app.api import pais
    pais.register_parent_routes(app)

    @app.get('/healthz')
    def healthz():
        from app.core.database import get_db
        try:
            with get_db() as conn:
                conn.execute('SELECT 1')
        except Exception:
            return '', 503
        return '', 204

    # Tratamento global de falha de conexão com o banco de dados
    @app.errorhandler(psycopg2.OperationalError)
    def handle_db_error(e):
        return render_template("errors/db_unavailable.html"), 503

    @app.errorhandler(psycopg2.InterfaceError)
    def handle_db_interface_error(e):
        return render_template("errors/db_unavailable.html"), 503

    return app
