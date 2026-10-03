from app.core.database import get_db


def run_migrations():
    """Aplica todas as migrações necessárias"""
    with get_db() as conn:
        # Tabela horarios_padrao
        conn.execute("""
            CREATE TABLE IF NOT EXISTS horarios_padrao (
                serie TEXT PRIMARY KEY,
                saida_seg TEXT,
                saida_ter TEXT,
                saida_qua TEXT,
                saida_qui TEXT,
                saida_sex TEXT
            )
        """)

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
