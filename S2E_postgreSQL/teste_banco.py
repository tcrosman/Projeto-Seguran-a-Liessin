import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / 's2e-api' / '.env')
sys.path.insert(0, str(Path(__file__).parent / 's2e-api'))

from app.core.database import get_db

print('Testando conexão PostgreSQL...')
try:
    with get_db() as conn:
        users = conn.execute('SELECT username, role FROM usuarios').fetchall()
    print('Conexão OK!')
    print('Usuários no banco:')
    for u in users:
        print(f'  Usuário: {u["username"]} | Função: {u["role"]}')
except Exception as e:
    print(f'ERRO: {e}')
