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

    # Centraliza no Flask as configurações lidas do ambiente. As rotas de recuperação de
    # senha consultam app.config['BASE_URL']; sem carregar Config, elas ignoravam a BASE_URL do
    # ambiente e enviavam links apontando para http://localhost:8002.
    from app.config import Config
    app.config.from_object(Config)

    # Disponibiliza a identidade da instalação em todos os templates. O dicionário é
    # pequeno e contém somente configuração pública; credenciais nunca entram no contexto.
    @app.context_processor
    def inject_installation_identity():
        return {
            'app_name': app.config['APP_NAME'],
            'institution_id': app.config['INSTITUTION_ID'],
            'institution_name': app.config['INSTITUTION_NAME'],
            'institution_short_name': app.config['INSTITUTION_SHORT_NAME'],
            'institution_support_email': app.config['INSTITUTION_SUPPORT_EMAIL'],
        }

    # Atrás de um proxy reverso (hoje o nginx do VPS), request.remote_addr é o IP do proxy, igual
    # para todos os visitantes — o que tornava o rate limit por IP ou inócuo ou capaz de trancar
    # todo mundo de uma vez. Com ProxyFix, remote_addr passa a ser o IP real, lido de
    # X-Forwarded-For.
    # x_for=1: confia em exatamente UM proxy à frente. Aumentar esse número sem ter os proxies
    # correspondentes deixaria o cliente forjar o próprio IP no cabeçalho.
    # x_proto=1 é necessário para o Talisman saber que a request chegou por HTTPS e não entrar em
    # loop de redirect. x_host fica de fora de propósito: nada aqui depende de request.host (os
    # links de e-mail vêm de BASE_URL), e confiar em X-Forwarded-Host permitiria envenenar o
    # destino do redirect de HTTPS.
    #
    # O padrão é FALSE, e precisa continuar sendo. ProxyFix sem proxy à frente não corrige nada:
    # ele passa a acreditar no X-Forwarded-For que o próprio cliente enviou. Num deploy sem proxy
    # (docker local, VM, servidor da escola), isso significa (a) todo limite por IP caindo com um
    # cabeçalho diferente a cada requisição — inclusive o que segura a força bruta do 2FA — e
    # (b) o `ip` gravado na auditoria virando texto escolhido pelo atacante, contaminando a
    # trilha de quem pediu a saída de uma criança.
    #
    # Ligue só onde o proxy é conhecido E o gunicorn não é alcançável direto. No VPS as duas
    # condições valem: o nginx está à frente e a porta 8002 escuta apenas em 127.0.0.1, fechada
    # no firewall (ver deploy/DEPLOY.md). Por isso o .env de produção traz TRUST_PROXY=true.
    if os.getenv('TRUST_PROXY', 'false').strip().lower() == 'true':
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
    # Caminho ABSOLUTO. Com o valor relativo ('storage'), a gravação em /registrar_saida usava o
    # diretório de trabalho do processo enquanto o send_from_directory do Flask resolve caminho
    # relativo contra app.root_path (s2e-api/app) — arquivo salvo num lugar, procurado noutro, e
    # todo anexo dava 404. Ancorado na pasta do projeto (s2e-api/), que é onde os uploads já estão.
    _upload = os.getenv('UPLOAD_FOLDER', 'storage')
    if not os.path.isabs(_upload):
        _upload = os.path.join(os.path.dirname(app.root_path), _upload)
    app.config['UPLOAD_FOLDER'] = os.path.abspath(_upload)
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

    # Origem dos dados de aluno, conferida no boot e não na primeira busca da portaria: uma
    # configuração faltando tem que impedir o app de subir, não aparecer como "nenhum aluno
    # encontrado" no meio do expediente. Mesma postura da checagem de SECRET_KEY acima.
    #
    # A checagem antiga parava na escolha da classe, e isso não provava nada: o __init__ do
    # client não valida configuração, e o erro por SCHOOL_SQL_HOST/DATABASE ausentes só aparecia
    # dentro de _conectar(), no meio de uma requisição, capturado e transformado em lista vazia.
    # Como o render.yaml marca essas variáveis como `sync: false`, esquecer uma fazia o app subir
    # saudável, com health check verde e toda tela dizendo "nenhum aluno encontrado".
    #
    # Agora o boot conversa com o banco da escola de verdade e se recusa a subir se ele não
    # responder. É fail-fast deliberado: um erro de configuração precisa aparecer no deploy, e
    # não às 15h. O preço é que um restart durante uma indisponibilidade do banco da escola não
    # sobe — e é preferível a um processo que atende sem saber quem são os alunos.
    from app.services.school_sql_directory import get_school_sql_directory
    _diretorio = get_school_sql_directory()
    _saude = _diretorio.verificar_saude()
    if _saude['modo'] == 'mock':
        _log.warning("[SCHOOL_SQL] MODO MOCK ativo (SCHOOL_SQL_MOCK=true): os alunos são dados "
                     "de teste, não o cadastro da escola. Não use assim em produção.")
    elif not _saude['ok']:
        raise RuntimeError(
            "Banco SQL da escola não respondeu no boot: " + str(_saude['detalhe']) + "\n"
            "Confira SCHOOL_SQL_HOST, SCHOOL_SQL_DATABASE, SCHOOL_SQL_USER e "
            "SCHOOL_SQL_PASSWORD, e se o schema tem alunos(ra, nome, turma, serie, foto_url, "
            "ativo), responsaveis(email, nome, ativo) e vinculos(ra, email). Subir sem isso "
            "seria servir todas as telas de aluno vazias, sem erro nenhum."
        )
    elif _saude['unaccent'] is False:
        _log.warning("[SCHOOL_SQL] Extensão `unaccent` ausente: a busca de aluno funciona, mas "
                     "sem ignorar acentos. Peça à instituição: CREATE EXTENSION unaccent;")

    # Migração do schema. Idempotente (CREATE TABLE/COLUMN IF NOT EXISTS) e, desde a correção
    # C4, serializada por advisory lock — ver aplicar_migracoes() em app/core/database.py para o
    # porquê. Uma falha precisa abortar o boot: continuar serviria um processo aparentemente
    # saudável sobre um schema incompleto, que só revelaria o problema nas primeiras requisições.
    #
    # MIGRACOES_NO_BOOT=false desliga esta etapa para quem roda as migrações como passo de
    # release (`python run_migrations.py` antes do deploy). O padrão continua ligado de
    # propósito: um ambiente sem o passo de release configurado precisa subir com o schema
    # aplicado, não sem schema nenhum.
    if os.getenv('MIGRACOES_NO_BOOT', 'true').strip().lower() == 'true':
        from app.core.database import aplicar_migracoes
        aplicar_migracoes()
    else:
        _log.info("[SCHEMA] MIGRACOES_NO_BOOT=false: migrações não rodam no boot; devem vir do "
                  "passo de release (python run_migrations.py).")

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
