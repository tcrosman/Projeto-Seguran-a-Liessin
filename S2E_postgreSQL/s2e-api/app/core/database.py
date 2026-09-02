import psycopg2
import psycopg2.extras
import psycopg2.pool
from contextlib import contextmanager
from threading import BoundedSemaphore, Lock
from urllib.parse import urlparse
import os
from pathlib import Path
from dotenv import load_dotenv

from app.core.logging_config import obter

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / '.env')

_log = obter()

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
# gunicorn (30s) para o usuário receber a página de indisponibilidade em vez de um socket morto.
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


# Chave do advisory lock que serializa as migrações. Diferente da chave da manutenção
# (app/core/maintenance.py): são exclusões mútuas independentes, e compartilhar a chave faria uma
# esperar pela outra sem motivo.
_LOCK_MIGRACOES = 8021978


@contextmanager
def _conexao(conn):
    """Reaproveita a conexão recebida, ou abre uma própria quando não veio nenhuma.

    Cada etapa de migração passou a aceitar `conn` justamente para as três poderem rodar dentro
    de UMA transação — é o que permite proteger o conjunto inteiro com um advisory lock de
    transação. Sem o parâmetro, cada função continua funcionando isolada (setup_db.py, testes).
    """
    if conn is not None:
        yield conn
    else:
        with get_db() as propria:
            yield propria


def aplicar_migracoes():
    """Aplica o schema inteiro — tabelas, índices e chaves estrangeiras — sob advisory lock.

    Antes as três etapas eram chamadas soltas de create_app(). Como o gunicorn roda sem
    `--preload`, cada worker importa run.py DEPOIS do fork e os dois executavam ~34 comandos DDL
    ao mesmo tempo. No PostgreSQL isso não é seguro nem com IF NOT EXISTS: dois CREATE TABLE IF
    NOT EXISTS concorrentes dão "duplicate key value violates unique constraint
    pg_type_typname_nsp_index", e dois ALTER TABLE na mesma tabela deadlockam. A exceção matava o
    worker, o Render reiniciava, e o ciclo se repetia — no cold start do primeiro acesso do dia,
    ou num deploy às 14h50.

    Lock de TRANSAÇÃO, e não de sessão, pela mesma razão documentada em maintenance.py: o
    Postgres o solta sozinho no commit e no rollback, então um erro no meio não deixa o lock
    preso numa conexão que volta para o pool.

    Lock que ESPERA (pg_advisory_xact_lock), e não pg_try_advisory_xact_lock: quem chega depois
    precisa encontrar o schema pronto antes de começar a atender. Desistir na hora deixaria o
    segundo worker servindo requisições sobre um schema pela metade — que é justamente a falha
    que se quer evitar. O DDL é idempotente, então repetir depois de esperar não custa nada.
    """
    from app.core.migrations import run_migrations
    with get_db() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_MIGRACOES,))
        migrate_database(conn)
        run_migrations(conn)
        # Por último: as chaves estrangeiras precisam de todas as tabelas já criadas.
        aplicar_chaves_estrangeiras(conn)


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


def migrate_database(conn=None):
    """Migrações automáticas — verifica information_schema antes de ALTER TABLE.

    `conn` é passado por aplicar_migracoes(), para esta etapa entrar na mesma transação — e no
    mesmo advisory lock — das demais.
    """
    with _conexao(conn) as conn:
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
        # Vínculo com a solicitação que originou a saída. Sem ele, a edição feita pelo
        # responsável só sabia apagar "a saída pendente daquele aluno naquela data" — e levava
        # junto a que a portaria tivesse registrado por conta própria, com outro motivo e outro
        # documento anexado. Fica NULL nas saídas criadas direto pela portaria, que é o certo:
        # elas não pertencem a solicitação nenhuma e não podem ser apagadas por essa via.
        if not _col_exists(conn, 'saidas', 'solicitacao_id'):
            conn.execute("ALTER TABLE saidas ADD COLUMN solicitacao_id INTEGER")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_saidas_solicitacao ON saidas(solicitacao_id)")
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

        # Documentos anexados às saídas (atestados, autorizações). Ficam no banco, e não em
        # arquivo, porque o disco da hospedagem é efêmero: no plano free do Render tudo em
        # storage/ some a cada deploy, enquanto saidas.documento_path continuava apontando para
        # os arquivos — o link da portaria virava 404 justamente quando alguém ia conferir a
        # justificativa de uma saída antiga. Aqui o anexo dura exatamente o que dura o registro
        # que o referencia.
        #
        # Tabela separada de propósito: `SELECT s.*` em `saidas` é usado em várias rotas e
        # traria o binário inteiro para a memória sem necessidade.
        #
        # É a primeira chave estrangeira do schema — cabe aqui porque a tabela é nova e não há
        # linha antiga para violá-la. O CASCADE resolve a limpeza: apagar a saída (pela
        # manutenção, ou pela reaprovação vinda do portal dos pais) leva o documento junto.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS saidas_documentos (
                saida_id  INTEGER PRIMARY KEY REFERENCES saidas(id) ON DELETE CASCADE,
                nome      TEXT NOT NULL,
                tipo      TEXT NOT NULL,
                dados     BYTEA NOT NULL,
                criado_em TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)

        # Trilha de auditoria (ver app/core/audit_logger.py). Fica no banco, e não só no arquivo
        # rotacionado, porque em disco efêmero (Render) `logs/system.log` some a cada deploy — e o
        # registro de quem aprovou a saída de um aluno precisa sobreviver a isso e à remoção da
        # própria solicitação pela manutenção.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS auditoria (
                id        SERIAL PRIMARY KEY,
                usuario   TEXT,
                acao      TEXT NOT NULL,
                detalhes  TEXT,
                ip        TEXT,
                criado_em TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        # Índice pela data: é por ela que a manutenção poda e que uma apuração busca o período.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_auditoria_data ON auditoria(criado_em)")


# Chaves estrangeiras: (tabela, coluna, tabela referida, o que fazer quando o alvo é apagado).
#
# O schema nasceu sem nenhuma, e foi exatamente isso que produziu o primeiro defeito investigado
# aqui: uma solicitação apontando para um responsável que não existia mais contava no aviso do
# /inicio e sumia da tela de revisão. O banco não tinha como impedir.
#
# A ação de cada linha não é detalhe — ela decide o que acontece com dado operacional quando uma
# pessoa é apagada:
#
#   CASCADE   só para material descartável do próprio dono (tokens). Apagar a conta apaga junto.
#   SET NULL  onde o registro precisa sobreviver à pessoa: quem autorizou uma saída pode sair da
#             escola, e a saída continua existindo. É também a semântica certa para exclusão de
#             dado pessoal (LGPD): apaga a pessoa, preserva o registro operacional anonimizado.
#             Quem autorizou o quê continua respondido pela trilha de auditoria, que guarda o
#             nome como texto e não depende destas chaves.
#   RESTRICT  onde apagar o alvo destruiria histórico sem substituto: aluno com saídas no nome.
#             O banco recusa a exclusão em vez de deixar o histórico órfão.
_CHAVES_ESTRANGEIRAS = [
    ('tokens_2fa',           'responsavel_id',    'responsaveis',       'CASCADE'),
    ('reset_tokens_pais',    'responsavel_id',    'responsaveis',       'CASCADE'),
    ('reset_tokens',         'user_id',           'usuarios',           'CASCADE'),

    ('solicitacoes_saida',   'responsavel_id',    'responsaveis',       'SET NULL'),
    ('solicitacoes_saida',   'revisado_por',      'usuarios',           'SET NULL'),
    ('saidas',               'usuario_autorizou', 'usuarios',           'SET NULL'),
    # A manutenção apaga solicitações já revisadas depois de 30 dias. Com CASCADE ela levaria a
    # saída junto; com RESTRICT ela pararia de funcionar. SET NULL é a única opção correta aqui.
    ('saidas',               'solicitacao_id',    'solicitacoes_saida', 'SET NULL'),

    ('solicitacoes_saida',   'aluno_id',          'alunos',             'RESTRICT'),
    ('saidas',               'aluno',             'alunos',             'RESTRICT'),
    ('logs_alunos',          'aluno_id',          'alunos',             'RESTRICT'),
    ('logs_alunos',          'usuario_id',        'usuarios',           'RESTRICT'),
]


def _tabela_existe(conn, tabela):
    return conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s", (tabela,)
    ).fetchone() is not None


def _constraint_existe(conn, nome):
    return conn.execute(
        "SELECT 1 FROM information_schema.table_constraints WHERE constraint_name = %s", (nome,)
    ).fetchone() is not None


def aplicar_chaves_estrangeiras(conn=None):
    """Cria as chaves estrangeiras que faltavam no schema. Idempotente.

    Roda depois de migrate_database() e run_migrations(), quando todas as tabelas já existem.
    Cada chave é criada isoladamente: se uma falhar por dado inconsistente, as outras entram
    assim mesmo, e o motivo fica no log em vez de derrubar a subida do app.

    `conn` é passado por aplicar_migracoes() — ver migrate_database().
    """
    with _conexao(conn) as conn:
        # Antes das chaves, o dado precisa parar de contradizê-las. Solicitações apontando para
        # responsáveis que não existem mais são anteriores a esta migração — a coluna passa a
        # aceitar nulo e elas ficam com o vínculo em branco, que é o que a tela já mostra
        # ("Responsável removido"). Nenhuma solicitação é apagada.
        if _tabela_existe(conn, 'solicitacoes_saida'):
            conn.execute("ALTER TABLE solicitacoes_saida ALTER COLUMN responsavel_id DROP NOT NULL")
            orfas = conn.execute("""
                UPDATE solicitacoes_saida SET responsavel_id = NULL
                WHERE responsavel_id IS NOT NULL
                  AND responsavel_id NOT IN (SELECT id FROM responsaveis)
            """).rowcount
            if orfas:
                _log.warning("[SCHEMA] %d solicitação(ões) apontavam para responsável inexistente; "
                             "o vínculo ficou em branco para a chave estrangeira poder existir.", orfas)

        for tabela, coluna, referida, ao_apagar in _CHAVES_ESTRANGEIRAS:
            nome = f"fk_{tabela}_{coluna}"
            if not _tabela_existe(conn, tabela) or not _tabela_existe(conn, referida):
                continue
            if _constraint_existe(conn, nome):
                continue
            try:
                conn.execute("SAVEPOINT criar_fk")
                conn.execute(
                    f"ALTER TABLE {tabela} ADD CONSTRAINT {nome} "
                    f"FOREIGN KEY ({coluna}) REFERENCES {referida}(id) ON DELETE {ao_apagar}"
                )
                conn.execute("RELEASE SAVEPOINT criar_fk")
                _log.info("[SCHEMA] chave estrangeira %s criada (ON DELETE %s)", nome, ao_apagar)
            except Exception as e:
                # SAVEPOINT: sem ele, uma chave recusada aborta a transação inteira e as
                # seguintes nem chegam a ser tentadas.
                conn.execute("ROLLBACK TO SAVEPOINT criar_fk")
                _log.warning("[SCHEMA] não foi possível criar %s (%s): %s", nome, ao_apagar, e)


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
