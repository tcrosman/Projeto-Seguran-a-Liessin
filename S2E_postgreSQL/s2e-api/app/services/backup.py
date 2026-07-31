from app.core.audit_logger import log_operacao
from typing import Dict, List, Optional


class BackupService:
    """Serviço para backups — gerenciados automaticamente pelo Supabase."""

    def create_backup(self, username: str = None) -> str:
        msg = "Backups são gerenciados automaticamente pelo Supabase."
        if username:
            log_operacao(username, "TENTOU CRIAR BACKUP", msg)
        raise NotImplementedError(msg)

    def list_backups(self) -> List[Dict]:
        return []

    def get_latest_backup(self) -> Optional[Dict]:
        return None

    def get_backup_stats(self) -> Dict:
        return {'total': 0, 'total_size_mb': 0, 'latest': None, 'managed_by': 'Supabase'}
