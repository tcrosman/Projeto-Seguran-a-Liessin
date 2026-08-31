import psycopg2
import psycopg2.extras
import psycopg2.pool
from contextlib import contextmanager
from threading import BoundedSemaphore, Lock
from urllib.parse import urlparse
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / '.env')

# Pool de conexões. Antes, cada `with get_db()` abria TCP + TLS + autenticação contra o pooler do
# Supabase, e várias rotas abrem 2-4 blocos por request — o handshake dominava a latência.
#
# Criado sob demanda (nunca no import): o gunicorn faz fork dos workers, e um pool criado antes do
# fork teria seus sockets compartilhados entre processos. Como a primeira chamada acontece dentro
# do worker, cada um monta o seu.
_pool = None
_vagas = None       # semáforo: quantas conexões ainda podem ser retiradas do pool
_pool_lock = Lock()

# Quanto uma request espera por uma conexão livre antes de desistir. Menor que o timeout do
# gunicorn (120s) para o usuário receber a página de indisponibilidade em vez de um socket morto.
ESPERA_CONEXAO_SEG = int(os.getenv('DB_POOL_TIMEOUT_SEG', 10))


def _get_pool():
    """Devolve (pool, semáforo), criando-os na primeira chamada."""
    global _pool, _vagas
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                url = os.getenv('DATABASE_URL', '')
                if not url:
                    raise RuntimeError("DATABASE_URL não configurada no .env")
                p = urlparse(url)
                maximo = int(os.getenv('DB_POOL_MAX', 5))
                novo = psycopg2.pool.ThreadedConnectionPool(
                    minconn=1,
                    maxconn=maximo,
                    host=p.hostname,
                    port=p.port or 5432,
                    dbname=p.path.lstrip('/'),
                    user=p.username,
                    password=p.password,
                    sslmode='require',
                    # Sem keepalives, o Supabase derruba a conexão ociosa e o pool só descobre
                    # na próxima query, já dentro de uma request.
                    keepalives=1,
                    keepalives_idle=30,
                    keepalives_interval=10,
                    keepalives_count=5,
                )
                # getconn() do psycopg2 NÃO espera: com o pool cheio ele levanta
                # PoolError na hora, e a request viraria erro 500. O semáforo faz a request
                # excedente aguardar uma conexão ser devolvida, que é o comportamento esperado
                # sob pico (antes, sem pool, ela simplesmente abria mais uma conexão).
                _vagas = BoundedSemaphore(maximo)
                _pool = novo
    return _pool, _vagas


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
    """Context manager para conexão com banco de dados PostgreSQL, tirada do pool."""
    pool, vagas = _get_pool()

    if not vagas.acquire(timeout=ESPERA_CONEXAO_SEG):
        # OperationalError para cair no errorhandler que já mostra a página de banco
        # indisponível, em vez de um 500 cru.
        raise psycopg2.OperationalError(
            f"Sem conexão livre no pool após {ESPERA_CONEXAO_SEG}s. "
            "Aumente DB_POOL_MAX se isso acontecer sob carga normal."
        )

    try:
        raw = pool.getconn()

        # O pool pode devolver uma conexão que o servidor fechou enquanto estava ociosa; nesse
        # caso ela é descartada e outra é pedida, em vez de estourar no meio da rota.
        if raw.closed:
            pool.putconn(raw, close=True)
            raw = pool.getconn()

        descartar = False
        try:
            yield _PgConn(raw)
            raw.commit()
        except Exception:
            try:
                raw.rollback()
            except psycopg2.Error:
                # Conexão quebrada: nem o rollback passa. Não pode voltar para o pool.
                descartar = True
            raise
        finally:
            pool.putconn(raw, close=descartar or raw.closed)
    finally:
        # Só depois de devolver a conexão — senão outra thread acordaria sem nada disponível.
        vagas.release()


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
        # Colunas extras na tabela alunos
        for col in ['telefone', 'email_responsavel', 'data_nascimento', 'alergias', 'observacoes']:
            if not _col_exists(conn, 'alunos', col):
                conn.execute(f"ALTER TABLE alunos ADD COLUMN {col} TEXT")

        # Coluna usuario_autorizou na tabela saidas
        if not _col_exists(conn, 'saidas', 'usuario_autorizou'):
            conn.execute("ALTER TABLE saidas ADD COLUMN usuario_autorizou INTEGER")

        # RA do aluno (identidade passa a vir do RA, não mais de alunos.id), snapshot da turma
        # no momento do registro (turma antes só existia via JOIN com alunos) e hora real da liberação
        if not _col_exists(conn, 'saidas', 'ra'):
            conn.execute("ALTER TABLE saidas ADD COLUMN ra TEXT")
        if not _col_exists(conn, 'saidas', 'turma'):
            conn.execute("ALTER TABLE saidas ADD COLUMN turma TEXT")
        if not _col_exists(conn, 'saidas', 'liberado_em'):
            conn.execute("ALTER TABLE saidas ADD COLUMN liberado_em TIMESTAMP")
        # Saídas novas são identificadas por ra, não por aluno (FK local) — DROP NOT NULL é idempotente
        conn.execute("ALTER TABLE saidas ALTER COLUMN aluno DROP NOT NULL")

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

        # Tentativas de verificação por token: são só 6 dígitos (10^6 combinações), então o
        # limite de tentativas é o que impede força bruta do 2FA. Fica no banco, e não em
        # memória do processo, para valer entre workers e sobreviver a restart.
        if not _col_exists(conn, 'tokens_2fa', 'tentativas'):
            conn.execute("ALTER TABLE tokens_2fa ADD COLUMN tentativas INTEGER NOT NULL DEFAULT 0")

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
        for col in ['tipo_saida', 'acompanhante', 'responsavel_escola', 'ra']:
            if not _col_exists(conn, 'solicitacoes_saida', col):
                conn.execute(f"ALTER TABLE solicitacoes_saida ADD COLUMN {col} TEXT")
        # Solicitações novas são identificadas por ra, não por aluno_id (FK local)
        conn.execute("ALTER TABLE solicitacoes_saida ALTER COLUMN aluno_id DROP NOT NULL")

        # Vínculos pai→aluno legados (substituiu email_responsavel em alunos); o fluxo atual
        # resolve o vínculo ao vivo no banco SQL da escola, sem gravar aqui
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

        # Tentativas de autenticação falhas (ver app/core/rate_limit.py). No banco, e não em
        # memória do processo, para valer entre os workers do gunicorn e sobreviver a restart.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rate_limit_falhas (
                id        SERIAL PRIMARY KEY,
                escopo    TEXT NOT NULL,
                chave     TEXT NOT NULL,
                criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rate_limit_busca ON rate_limit_falhas(escopo, chave, criado_em)"
        )


def expirar_saidas_nao_liberadas(conn):
    """Marca como 'nao_realizada' as saídas aprovadas cuja data já passou sem terem sido liberadas
    pela segurança. Devolve quantas linhas mudaram.

    A data de corte vem do fuso da escola, não do CURRENT_DATE do banco: `data_saida` guarda a
    data que o usuário escolheu no relógio de Brasília, e o Postgres do Supabase está em UTC —
    entre 21h e meia-noite as duas divergem em um dia.

    Chamada pela manutenção periódica (app/core/maintenance.py). Não deve voltar para as rotas de
    listagem: é uma escrita em tabela inteira, e rodava a cada carregamento de página.
    """
    from app.core.tempo import hoje
    return conn.execute(
        "UPDATE saidas SET status = 'nao_realizada' WHERE status = 'pendente' AND data_saida < %s",
        (hoje(),),
    ).rowcount
