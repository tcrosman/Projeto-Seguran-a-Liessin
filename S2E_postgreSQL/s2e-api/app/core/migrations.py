from app.core.database import get_db


def run_migrations():
    """Aplica todas as migrações necessárias"""
    with get_db() as conn:
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

    # Aqui havia uma chamada a run_migrations(), e ela tornava `setup_db.py --reset`
    # impossível de rodar num banco vazio — ou seja, não havia caminho nenhum para
    # preparar uma instalação nova.
    #
    # A ordem certa é: reset_database() cria as três tabelas base, migrate_database()
    # cria as demais (logs_alunos, consentimentos, tokens_2fa, auditoria...) e só então
    # run_migrations() indexa. Mas run_migrations() chamada daqui rodava ANTES de
    # migrate_database(), e a linha
    #     CREATE INDEX idx_logs_aluno ON logs_alunos(aluno_id)
    # estourava com relation "logs_alunos" does not exist — tabela que este mesmo
    # reset acabara de dropar no início da função.
    #
    # setup_db.py já executa a sequência inteira na ordem correta logo depois de
    # chamar reset_database(), e é o único lugar que a chama. Então esta linha era, ao
    # mesmo tempo, redundante e a causa da falha.

