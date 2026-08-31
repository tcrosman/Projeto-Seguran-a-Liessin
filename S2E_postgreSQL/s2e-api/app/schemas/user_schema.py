from typing import Optional
import re

from app.core.passwords import verificar_forca

class UserSchema:
    """Schema para validação e serialização de usuários"""

    @staticmethod
    def _check_password_strength(password: str) -> Optional[str]:
        """Retorna mensagem de erro se a senha não atende à política, ou None se válida.

        A política em si mora em app/core/passwords.py, compartilhada com o portal dos
        responsáveis; aqui fica só o encaminhamento, para não haver duas regras divergentes.
        """
        return verificar_forca(password)

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
            else:
                err = UserSchema._check_password_strength(password)
                if err:
                    errors.append(err)
                else:
                    validated['password'] = password
        
        # Role — LGPD Art. 46 (💻 App obligation): vigia is a restricted role for guards
        role = data.get('role', 'basico')
        if role not in ['admin', 'basico', 'vigia']:
            errors.append('Tipo deve ser "admin", "basico" ou "vigia"')
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
        err = UserSchema._check_password_strength(new_password)
        if err:
            return False, err

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