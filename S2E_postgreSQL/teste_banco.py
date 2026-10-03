import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / 's2e-api' / '.env')
sys.path.insert(0, str(Path(__file__).parent / 's2e-api'))

from app.core.database import get_db


def main():
    """Diagnóstico sem listar usuários ou detalhes da conexão."""
    try:
        with get_db() as conn:
            conn.execute('SELECT 1')
        print('Conexão PostgreSQL disponível.')
    except Exception:
        print('Conexão PostgreSQL indisponível.')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
