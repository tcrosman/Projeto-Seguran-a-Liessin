import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

load_dotenv(dotenv_path=Path(__file__).parent / '.env')

sys.path.insert(0, str(Path(__file__).parent))

from app.core.migrations import run_migrations
from app.core.database import migrate_database, get_db


def main():
    """Aplica apenas migrações aditivas no banco já existente."""

    admin_password = os.getenv('INITIAL_ADMIN_PASSWORD', '')
    if len(admin_password) < 12:
        raise SystemExit("INITIAL_ADMIN_PASSWORD deve ter pelo menos 12 caracteres.")

    print("Aplicando migrações aditivas...")
    migrate_database()
    run_migrations()

    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) as total FROM usuarios").fetchone()['total']
        if total == 0:
            senha_hash = generate_password_hash(admin_password, method='pbkdf2:sha256')
            conn.execute(
                "INSERT INTO usuarios (username, password, role, email) VALUES (%s, %s, %s, %s)",
                ('admin', senha_hash, 'admin', os.getenv('INITIAL_ADMIN_EMAIL', ''))
            )
            print("Usuário administrador inicial criado; a senha não será exibida.")

    print("Configuração concluída sem apagar tabelas existentes.")


if __name__ == '__main__':
    main()
