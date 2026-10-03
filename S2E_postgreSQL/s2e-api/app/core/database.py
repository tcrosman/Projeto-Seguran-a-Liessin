import psycopg2
import psycopg2.extras
from contextlib import contextmanager
from urllib.parse import urlparse, unquote
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / '.env')


def _get_connection():
    url = os.getenv('DATABASE_URL', '')
    if not url:
        raise RuntimeError("DATABASE_URL não configurada no .env")
    p = urlparse(url)
    sslmode = os.getenv('DATABASE_SSLMODE', 'require')
    if sslmode not in {'require', 'verify-ca', 'verify-full'} and not (
        os.getenv('APP_ENV', 'production').lower() == 'test'
        and p.hostname in {'localhost', '127.0.0.1', '::1'}
    ):
        raise RuntimeError('DATABASE_SSLMODE alternativo permitido somente em teste local')
    return psycopg2.connect(
        host=p.hostname,
        port=p.port or 5432,
        dbname=unquote(p.path.lstrip('/')),
        user=unquote(p.username) if p.username else None,
        password=unquote(p.password) if p.password else None,
        sslmode=sslmode
    )


class _PgConn:
    """Wrap psycopg2 para manter a mesma interface conn.execute() do sqlite3."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=None):
        cur = self._raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params or ())
        return cur

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()


@contextmanager
def get_db():
    """Context manager para conexão com banco de dados PostgreSQL."""
    raw = _get_connection()
    conn = _PgConn(raw)
    try:
        yield conn
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def _col_exists(conn, table, column):
    row = conn.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
        (table, column)
    ).fetchone()
    return row is not None


def init_db(app):
    """Inicializa o banco de dados e aplica migrações"""
    with app.app_context():
        migrate_database()


def migrate_database():
    """Migrações automáticas — verifica information_schema antes de ALTER TABLE."""
    with get_db() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(746392019)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alunos (
                id SERIAL PRIMARY KEY, nome TEXT NOT NULL, turma TEXT NOT NULL,
                serie TEXT NOT NULL, saida_seg TEXT, saida_ter TEXT,
                saida_qua TEXT, saida_qui TEXT, saida_sex TEXT,
                responsaveis TEXT, foto_path TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'basico',
                email TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS saidas (
                id SERIAL PRIMARY KEY, aluno INTEGER NOT NULL,
                data_saida TEXT NOT NULL, horario TEXT NOT NULL,
                motivo TEXT NOT NULL, status TEXT NOT NULL,
                responsavel_escola TEXT, tipo_saida TEXT, acompanhante TEXT,
                documento_path TEXT
            )
        """)
        if not _col_exists(conn, 'usuarios', 'auth_version'):
            conn.execute("ALTER TABLE usuarios ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 0")
        # Colunas extras na tabela alunos
        for col in ['telefone', 'email_responsavel', 'data_nascimento', 'alergias', 'observacoes']:
            if not _col_exists(conn, 'alunos', col):
                conn.execute(f"ALTER TABLE alunos ADD COLUMN {col} TEXT")
        if not _col_exists(conn, 'alunos', 'school_external_id'):
            conn.execute('ALTER TABLE alunos ADD COLUMN school_external_id TEXT')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS uq_alunos_school_external_id ON alunos(school_external_id)')

        # Coluna usuario_autorizou na tabela saidas
        if not _col_exists(conn, 'saidas', 'usuario_autorizou'):
            conn.execute("ALTER TABLE saidas ADD COLUMN usuario_autorizou INTEGER")

        # Tabela de logs de alunos (auditoria)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS logs_alunos (
                id SERIAL PRIMARY KEY,
                aluno_id INTEGER NOT NULL,
                usuario_id INTEGER NOT NULL,
                acao TEXT NOT NULL,
                dados_antigos TEXT,
                dados_novos TEXT,
                data_hora TEXT NOT NULL
            )
        """)

        # Tabela de consentimentos LGPD
        conn.execute("""
            CREATE TABLE IF NOT EXISTS consentimentos (
                id SERIAL PRIMARY KEY,
                aluno_id INTEGER NOT NULL,
                aceitou BOOLEAN NOT NULL,
                ip TEXT NOT NULL,
                data_hora TEXT NOT NULL
            )
        """)

        # Portal dos responsáveis: contas (só email + nome ficam localmente — LGPD)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS responsaveis (
                id          SERIAL PRIMARY KEY,
                email       TEXT UNIQUE NOT NULL,
                nome        TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pendente',
                criado_em   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        if not _col_exists(conn, 'responsaveis', 'auth_version'):
            conn.execute("ALTER TABLE responsaveis ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 0")
        if not _col_exists(conn, 'responsaveis', 'school_external_id'):
            conn.execute('ALTER TABLE responsaveis ADD COLUMN school_external_id TEXT')

        # Tokens de 2FA enviados por email (6 dígitos, expiram em 10 min)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tokens_2fa (
                id             SERIAL PRIMARY KEY,
                responsavel_id INTEGER NOT NULL,
                token          TEXT NOT NULL,
                expires_at     TIMESTAMP NOT NULL,
                usado          BOOLEAN DEFAULT FALSE,
                criado_em      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Solicitações de saída criadas pelos responsáveis (revisadas pelo admin antes de virar saida)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS solicitacoes_saida (
                id                 SERIAL PRIMARY KEY,
                responsavel_id     INTEGER NOT NULL,
                aluno_id           INTEGER NOT NULL,
                data_solicitada    TEXT NOT NULL,
                horario_solicitado TEXT,
                motivo             TEXT,
                status             TEXT NOT NULL DEFAULT 'aguardando',
                revisado_por       INTEGER,
                revisado_em        TIMESTAMP,
                criado_em          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Colunas adicionadas após criação inicial da tabela solicitacoes_saida
        for col in ['tipo_saida', 'acompanhante', 'responsavel_escola']:
            if not _col_exists(conn, 'solicitacoes_saida', col):
                conn.execute(f"ALTER TABLE solicitacoes_saida ADD COLUMN {col} TEXT")

        # Associação explícita para novas aprovações. Registros anteriores ficam
        # nulos até uma conciliação supervisionada, sem associação presumida.
        if not _col_exists(conn, 'saidas', 'solicitacao_id'):
            conn.execute("ALTER TABLE saidas ADD COLUMN solicitacao_id INTEGER REFERENCES solicitacoes_saida(id)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_saidas_solicitacao ON saidas(solicitacao_id) WHERE solicitacao_id IS NOT NULL")

        # Vínculos confirmados pela consulta escolar; dados legados ficam para conciliação.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vinculos_pais_alunos (
                id             SERIAL PRIMARY KEY,
                responsavel_id INTEGER NOT NULL,
                aluno_id       INTEGER NOT NULL,
                criado_em      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(responsavel_id, aluno_id)
            )
        """)

        # Tokens de redefinição de senha para responsáveis
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reset_tokens_pais (
                id             SERIAL PRIMARY KEY,
                responsavel_id INTEGER NOT NULL,
                token          TEXT NOT NULL UNIQUE,
                expires_at     TIMESTAMP NOT NULL,
                criado_em      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS auth_attempts (
                key TEXT PRIMARY KEY,
                failures INTEGER NOT NULL,
                window_start TIMESTAMP NOT NULL,
                blocked_until TIMESTAMP
            )
        """)


def expirar_saidas_nao_liberadas(conn):
    """Marca como 'nao_realizada' as saídas aprovadas cuja data já passou sem terem sido liberadas pela segurança."""
    conn.execute("""
        UPDATE saidas SET status = 'nao_realizada'
        WHERE status = 'pendente'
          AND data_saida < TO_CHAR((CURRENT_TIMESTAMP AT TIME ZONE 'America/Sao_Paulo')::date, 'YYYY-MM-DD')
    """)
