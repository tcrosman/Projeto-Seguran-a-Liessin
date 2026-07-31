from app.repositories.user_repo import UserRepository
from app.repositories.audit_repo import LogRepository
from app.core.database import get_db
from app.schemas.user_schema import UserSchema
from app.core.exceptions import ValidationError, UnauthorizedError, NotFoundError
from werkzeug.security import check_password_hash
from datetime import datetime, timedelta
import secrets
from typing import Dict, Optional

class AuthService:
    """Serviço para autenticação e gerenciamento de usuários"""
    
    def __init__(self):
        self.user_repo = UserRepository()
        self.log_repo = LogRepository()
    
    def login(self, username: str, password: str) -> Optional[Dict]:
        """Autentica um usuário"""
        user = self.user_repo.get_by_username(username)
        
        if not user or not check_password_hash(user['password'], password):
            return None
        
        return {
            'id': user['id'],
            'username': user['username'],
            'role': user['role']
        }
    
    def create_user(self, data: Dict, is_admin: bool = False) -> int:
        """Cria um novo usuário"""
        valid, error, validated = UserSchema.validate(data)
        if not valid:
            raise ValidationError(error)
        
        # Verificar se usuário já existe
        existing = self.user_repo.get_by_username(validated['username'])
        if existing:
            raise ValidationError("Nome de usuário já existe")
        
        return self.user_repo.create(validated)
    
    def delete_user(self, user_id: int, current_user_id: int, current_user_role: str) -> bool:
        """Remove um usuário (com verificações de segurança)"""
        if current_user_role != 'admin':
            raise UnauthorizedError("Apenas administradores podem deletar usuários")
        
        if user_id == current_user_id:
            raise ValidationError("Você não pode deletar seu próprio usuário")
        
        user = self.user_repo.get_by_id(user_id)
        if not user:
            raise NotFoundError("Usuário não encontrado")
        
        # Verificar se é o último admin
        if user['role'] == 'admin':
            admin_count = self.user_repo.get_admin_count_excluding(user_id)
            if admin_count == 0:
                raise ValidationError("Não é possível deletar o último administrador")
        
        return self.user_repo.delete(user_id)
    
    def get_all_users(self) -> list:
        """Retorna todos os usuários (sem senhas)"""
        return self.user_repo.get_all_without_passwords()
    
    def create_reset_token(self, email: str) -> Optional[str]:
        """Cria um token para redefinição de senha"""
        user = self.user_repo.get_by_email(email)
        if not user:
            return None
        
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        
        with get_db() as conn:
            # Remove tokens antigos
            conn.execute("DELETE FROM reset_tokens WHERE user_id = %s", (user['id'],))
            # Cria novo token
            conn.execute(
                "INSERT INTO reset_tokens (user_id, token, expires_at) VALUES (%s, %s, %s)",
                (user['id'], token, expires_at)
            )

        return token

    def validate_reset_token(self, token: str) -> Optional[int]:
        """Valida um token de redefinição de senha"""
        with get_db() as conn:
            row = conn.execute(
                "SELECT user_id, expires_at FROM reset_tokens WHERE token = %s",
                (token,)
            ).fetchone()

            if not row:
                return None

            expires_at = datetime.strptime(row['expires_at'], "%Y-%m-%d %H:%M:%S")
            if datetime.now() > expires_at:
                # Token expirado
                conn.execute("DELETE FROM reset_tokens WHERE token = %s", (token,))
                return None

            return row['user_id']

    def reset_password(self, token: str, new_password: str, confirm_password: str) -> bool:
        """Redefine a senha usando um token"""
        # Validar senha
        valid, error = UserSchema.validate_password_reset(new_password, confirm_password)
        if not valid:
            raise ValidationError(error)

        # Validar token
        user_id = self.validate_reset_token(token)
        if not user_id:
            raise ValidationError("Link inválido ou expirado")

        # Atualizar senha
        success = self.user_repo.update_password(user_id, new_password)

        if success:
            # Remover token usado
            with get_db() as conn:
                conn.execute("DELETE FROM reset_tokens WHERE token = %s", (token,))
        
        return success