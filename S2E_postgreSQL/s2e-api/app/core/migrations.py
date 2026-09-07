from app.core.database import get_db, _conexao
from app.core.logging_config import obter

_log = obter()


def run_migrations(conn=None):
    """Aplica todas as migrações necessárias.

    `conn` é passado por app.core.database.aplicar_migracoes(), para esta etapa rodar na mesma
    transação — e sob o mesmo advisory lock — das demais.
    """
    with _conexao(conn) as conn:
        # A tela de "Horários Padrão" permitia configurar o horário de saída de cada série, e
        # nenhum fluxo do sistema lia esses valores: o admin preenchia 16 séries x 5 dias e aquilo
        # não tinha efeito nenhum. A tela foi removida e a tabela vai junto — estava vazia.
        conn.execute("DROP TABLE IF EXISTS horarios_padrao")

        # Tabela reset_tokens
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reset_tokens (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)

        # Uma única saída pendente por aluno e por dia — garantido pelo BANCO, não pela
        # aplicação. O caminho antigo era SELECT COUNT(*) seguido de INSERT em READ COMMITTED:
        # duas transações simultâneas leem "pendente = 0" e as duas inserem. Basta um duplo
        # clique em "Registrar", ou dois funcionários registrando ao mesmo tempo, ou um admin
        # aprovando uma solicitação enquanto a portaria registra a mesma saída. A portaria passa
        # a ver duas autorizações idênticas: a primeira é liberada, a segunda continua pendente
        # e serve para liberar a MESMA criança uma segunda vez, para outro acompanhante, no
        # mesmo dia.
        #
        # Índice PARCIAL: só as pendentes se excluem. Um aluno pode ter várias saídas concluídas
        # no histórico da mesma data, e as 'nao_realizada' também precisam poder coexistir.
        #
        # `ra` NULL (linhas anteriores à migração) não colide: no Postgres nulos são distintos
        # entre si num índice único.
        #
        # Dentro de SAVEPOINT porque esta é a única migração que pode falhar por causa de dado
        # já existente — se a base tiver duplicatas de antes, o índice não nasce e o motivo fica
        # no log, em vez de derrubar o boot. Mesmo tratamento das chaves estrangeiras.
        try:
            conn.execute("SAVEPOINT idx_saida_unica")
            conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_saidas_pendente_por_dia
                            ON saidas (ra, data_saida) WHERE status = 'pendente'""")
            conn.execute("RELEASE SAVEPOINT idx_saida_unica")
        except Exception as e:
            conn.execute("ROLLBACK TO SAVEPOINT idx_saida_unica")
            _log.warning("[SCHEMA] uq_saidas_pendente_por_dia não pôde ser criado (provavelmente "
                         "há saídas pendentes duplicadas na base): %s", e)

        # Índices para performance
        conn.execute("CREATE INDEX IF NOT EXISTS idx_saidas_data ON saidas(data_saida)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_saidas_aluno ON saidas(aluno)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alunos_nome ON alunos(nome)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alunos_serie ON alunos(serie)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_aluno ON logs_alunos(aluno_id)")


def reset_database():
    """Recria o banco do zero (cuidado!)"""
    with get_db() as conn:
        conn.execute("DROP TABLE IF EXISTS logs_alunos CASCADE")
        conn.execute("DROP TABLE IF EXISTS consentimentos CASCADE")
        conn.execute("DROP TABLE IF EXISTS reset_tokens CASCADE")
        conn.execute("DROP TABLE IF EXISTS horarios_padrao CASCADE")
        conn.execute("DROP TABLE IF EXISTS saidas CASCADE")
        conn.execute("DROP TABLE IF EXISTS usuarios CASCADE")
        conn.execute("DROP TABLE IF EXISTS alunos CASCADE")

        conn.execute("""
            CREATE TABLE alunos (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                turma TEXT NOT NULL,
                serie TEXT NOT NULL,
                saida_seg TEXT,
                saida_ter TEXT,
                saida_qua TEXT,
                saida_qui TEXT,
                saida_sex TEXT,
                responsaveis TEXT,
                foto_path TEXT,
                telefone TEXT,
                email_responsavel TEXT,
                data_nascimento TEXT,
                alergias TEXT,
                observacoes TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE saidas (
                id SERIAL PRIMARY KEY,
                aluno INTEGER NOT NULL,
                data_saida TEXT NOT NULL,
                horario TEXT NOT NULL,
                motivo TEXT NOT NULL,
                status TEXT NOT NULL,
                responsavel_escola TEXT,
                tipo_saida TEXT,
                acompanhante TEXT,
                documento_path TEXT,
                usuario_autorizou INTEGER
            )
        """)

        conn.execute("""
            CREATE TABLE usuarios (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'basico',
                email TEXT
            )
        """)

    run_migrations()
