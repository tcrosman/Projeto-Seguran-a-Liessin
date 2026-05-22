from app.repositories.base_repositories import BaseRepository
from app.core.database import get_db
from typing import List, Dict, Optional

class UserRepository(BaseRepository):
    """Repositório para usuários"""
    
    def __init__(self):
        super().__init__('usuarios')
    
    def get_by_username(self, username: str) -> Optional[Dict]:
        """Busca usuário por nome de usuário"""
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, username, password, role, email FROM usuarios WHERE username = ?",
                (username,)
            ).fetchone()
            return dict(row) if row else None
    
    def get_by_email(self, email: str) -> Optional[Dict]:
        """Busca usuário por email"""
        with get_db() as conn:
            row = conn.execute(
                "SELECT id FROM usuarios WHERE LOWER(email) = ?",
                (email.lower(),)
            ).fetchone()
            return dict(row) if row else None
    
    def create(self, data: Dict) -> int:
        """Cria um novo usuário"""
        with get_db() as conn:
            cursor = conn.execute("""
                INSERT INTO usuarios (username, password, role, email)
                VALUES (?, ?, ?, ?)
            """, (
                data.get('username'), data.get('password'),
                data.get('role', 'basico'), data.get('email', '').lower()
            ))
            return cursor.lastrowid
    
    def update_password(self, id: int, new_password: str) -> bool:
        """Atualiza a senha do usuário"""
        with get_db() as conn:
            cursor = conn.execute(
                "UPDATE usuarios SET password = ? WHERE id = ?",
                (new_password, id)
            )
            return cursor.rowcount > 0
    
    def get_admin_count_excluding(self, user_id: int) -> int:
        """Conta admins excluindo um usuário específico"""
        with get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as total FROM usuarios WHERE role = 'admin' AND id != ?",
                (user_id,)
            ).fetchone()
            return row['total'] if row else 0
    
    def get_all_without_passwords(self) -> List[Dict]:
        """Retorna todos usuários sem o campo password"""
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, username, role, email FROM usuarios ORDER BY id"
            ).fetchall()
            return [dict(row) for row in rows]