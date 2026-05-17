import sqlite3
from contextlib import contextmanager
from flask import current_app
import os

@contextmanager
def get_db():
    """Context manager para conexão com banco de dados"""
    conn = sqlite3.connect(current_app.config.get('DATABASE_PATH', 'escola.db'), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row  # Retorna dicionários
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db(app):
    """Inicializa o banco de dados e aplica migrações"""
    with app.app_context():
        migrate_database()

def migrate_database():
    """Migrações automáticas (mesmo do original)"""
    with get_db() as conn:
        # Colunas da tabela alunos
        for col in ['telefone', 'email_responsavel', 'data_nascimento', 'alergias', 'observacoes']:
            try:
                conn.execute(f"ALTER TABLE alunos ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass
        
        # Coluna usuario_autorizou na tabela saidas
        try:
            conn.execute("ALTER TABLE saidas ADD COLUMN usuario_autorizou INTEGER")
        except sqlite3.OperationalError:
            pass
        
        # Tabela de logs de alunos (auditoria)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS logs_alunos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                aluno_id INTEGER NOT NULL,
                aceitou BOOLEAN NOT NULL,
                ip TEXT NOT NULL,
                data_hora TEXT NOT NULL
            )
        """)