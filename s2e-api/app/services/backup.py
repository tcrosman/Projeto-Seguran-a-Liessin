from app.core.backup import backup_database as core_backup
from app.core.logs import log_operacao
from app.core.database import get_db
from typing import Dict, List
import os
import glob
from datetime import datetime

class BackupService:
    """Serviço para gerenciar backups"""
    
    def __init__(self):
        self.backup_dir = 'backups'
        os.makedirs(self.backup_dir, exist_ok=True)
    
    def create_backup(self, username: str = None) -> str:
        """Cria um novo backup do banco de dados"""
        backup_path = core_backup()
        
        if username:
            log_operacao(username, "CRIOU BACKUP", backup_path)
        
        return backup_path
    
    def list_backups(self) -> List[Dict]:
        """Lista todos os backups disponíveis"""
        backups = glob.glob(f"{self.backup_dir}/*.db")
        result = []
        
        for backup in backups:
            stat = os.stat(backup)
            result.append({
                'filename': os.path.basename(backup),
                'path': backup,
                'size': stat.st_size,
                'size_mb': round(stat.st_size / (1024 * 1024), 2),
                'created_at': datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S")
            })
        
        # Ordenar por data (mais recente primeiro)
        result.sort(key=lambda x: x['created_at'], reverse=True)
        return result
    
    def get_latest_backup(self) -> Dict:
        """Retorna o backup mais recente"""
        backups = self.list_backups()
        return backups[0] if backups else None
    
    def get_backup_stats(self) -> Dict:
        """Retorna estatísticas de backups"""
        backups = self.list_backups()
        total_size = sum(b['size'] for b in backups)
        
        return {
            'total': len(backups),
            'total_size_mb': round(total_size / (1024 * 1024), 2),
            'latest': self.get_latest_backup()
        }