import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 's2e-api'))

from app import create_app
from app.core.database import get_db
from werkzeug.security import generate_password_hash

app = create_app()

with app.app_context():
    with get_db() as conn:
        # Verifica se já existe
        exists = conn.execute("SELECT id FROM usuarios WHERE username = 'admin'").fetchone()
        if exists:
            print("ℹ️ Usuário admin já existe!")
        else:
            conn.execute("""
                INSERT INTO usuarios (username, password, role, email)
                VALUES (%s, %s, %s, %s)
            """, (
                'admin',
                generate_password_hash('Admin@2024!', method='pbkdf2:sha256'),
                'admin',
                'admin@secureedu.com'
            ))
            print("✅ Usuário admin criado!")
            print("   👤 Usuário: admin")
            print("   🔑 Senha: Admin@2024!")
