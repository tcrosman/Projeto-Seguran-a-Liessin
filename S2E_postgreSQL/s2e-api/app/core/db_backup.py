from datetime import datetime


def backup_database():
    """No PostgreSQL/Supabase, o backup é gerenciado pelo próprio Supabase.
    Esta função retorna um aviso descritivo para ser exibido na UI."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    raise NotImplementedError(
        f"Backup local não disponível com PostgreSQL. "
        f"Use o painel do Supabase para gerenciar backups. "
        f"Timestamp: {timestamp}"
    )