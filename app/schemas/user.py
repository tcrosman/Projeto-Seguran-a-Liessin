from typing import Optional, Dict, List
import re

class UserSchema:
    """Schema para validação e serialização de usuários"""
    
    @staticmethod
    def validate(data: dict, is_update: bool = False) -> tuple[bool, Optional[str], dict]:
        """Valida dados de um usuário"""
        errors = []
        validated = {}
        
        # Username
        username = data.get('username', '').strip()
        if not is_update or username:
            if not username:
                errors.append('Nome de usuário é obrigatório')
            elif len(username) < 3:
                errors.append('Nome de usuário deve ter no mínimo 3 caracteres')
            elif not re.match(r'^[a-zA-Z0-9_]+$', username):
                errors.append('Nome de usuário deve conter apenas letras, números e underscore')
            else:
                validated['username'] = username
        
        # Senha
        password = data.get('password', '')
        if not is_update or password:
            if not password:
                errors.append('Senha é obrigatória')
            elif len(password) < 6:
                errors.append('Senha deve ter no mínimo 6 caracteres')
            else:
                validated['password'] = password
        
        # Role
        role = data.get('role', 'basico')
        if role not in ['admin', 'basico']:
            errors.append('Tipo deve ser "admin" ou "basico"')
        else:
            validated['role'] = role
        
        # Email
        email = data.get('email', '').strip().lower()
        if email:
            if '@' not in email or '.' not in email:
                errors.append('Email inválido')
            else:
                validated['email'] = email
        else:
            validated['email'] = ''
        
        if errors:
            return False, '; '.join(errors), {}
        
        return True, None, validated
    
    @staticmethod
    def validate_password_reset(new_password: str, confirm_password: str) -> tuple[bool, Optional[str]]:
        """Valida redefinição de senha"""
        if len(new_password) < 6:
            return False, 'A senha deve ter no mínimo 6 caracteres'
        
        if new_password != confirm_password:
            return False, 'As senhas não coincidem'
        
        return True, None
    
    @staticmethod
    def serialize(user: dict, include_email: bool = True) -> dict:
        """Serializa um usuário para JSON (sem senha)"""
        result = {
            'id': user.get('id'),
            'username': user.get('username'),
            'role': user.get('role')
        }
        
        if include_email:
            result['email'] = user.get('email')
        
        return result
    
    @staticmethod
    def serialize_list(users: list, include_email: bool = True) -> list:
        """Serializa uma lista de usuários"""
        return [UserSchema.serialize(u, include_email) for u in users]