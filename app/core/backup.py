import shutil
from datetime import datetime
import os

def backup_database(db_path='escola.db'):
    """Cria backup do banco de dados"""
    os.makedirs('backups', exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"backups/escola_{timestamp}.db"
    shutil.copy2(db_path, backup_path)
    return backup_path