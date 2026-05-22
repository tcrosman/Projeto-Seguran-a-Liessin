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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    import os
    db_path = 'escola.db'
    if os.path.exists(db_path):
        os.remove(db_path)
    
    # Criar banco vazio e rodar migrations
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE alunos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                aluno INTEGER NOT NULL,
                data_saida TEXT NOT NULL,
                horario TEXT NOT NULL,
                motivo TEXT NOT NULL,
                veiculo TEXT,
                placa TEXT,
                responsavel TEXT,
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'basico',
                email TEXT
            )
        """)
    
    run_migrations()