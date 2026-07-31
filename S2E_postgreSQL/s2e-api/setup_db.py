import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

load_dotenv(dotenv_path=Path(__file__).parent / '.env')

sys.path.insert(0, str(Path(__file__).parent))

from app.core.migrations import reset_database
from app.core.database import migrate_database, get_db

print("Criando banco de dados PostgreSQL...")
reset_database()
migrate_database()

with get_db() as conn:
    total = conn.execute("SELECT COUNT(*) as total FROM usuarios").fetchone()['total']
    if total == 0:
        senha_hash = generate_password_hash('admin123', method='pbkdf2:sha256')
        conn.execute(
            "INSERT INTO usuarios (username, password, role, email) VALUES (%s, %s, %s, %s)",
            ('admin', senha_hash, 'admin', 'admin@secureedu.com')
        )
        print("Usuário 'admin' criado com senha temporária: admin123")
        print("IMPORTANTE: Altere a senha no primeiro acesso!")

print("Banco de dados criado com sucesso!")
