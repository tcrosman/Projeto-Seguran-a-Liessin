from functools import wraps
from flask import session, redirect, g
from app.core.database import get_db
from app.services.school_directory import SchoolDirectoryError
from app.services.school_sync import refresh_parent

# LGPD Art. 46 (💻 App obligation) — need-to-know access control: routes forbidden to vigia role
_VIGIA_ALLOWED_ENDPOINTS = {'inicio', 'lista_saidas'}


def active_staff_role():
    """Confere revogações e alterações de perfil em cada requisição."""
    if 'user_id' not in session:
        return None
    if not hasattr(g, 'staff_role'):
        with get_db() as conn:
            row = conn.execute("SELECT role, auth_version FROM usuarios WHERE id = %s", (session['user_id'],)).fetchone()
        g.staff_role = row['role'] if row else None
        if (g.staff_role != session.get('role') or
                (row and row['auth_version'] != session.get('auth_version'))):
            session.clear()
            g.staff_role = None
    return g.staff_role


def active_parent_id():
    if 'pai_id' not in session:
        return None
    if not hasattr(g, 'parent_id'):
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, email, auth_version FROM responsaveis WHERE id = %s AND status = 'aprovado'",
                (session['pai_id'],),
            ).fetchone()
        g.parent_id = row['id'] if row else None
        if g.parent_id is None or row['auth_version'] != session.get('auth_version'):
            session.clear()
            g.parent_id = None
        elif row['email'] != session.get('pai_email'):
            session.clear()
            g.parent_id = None
        else:
            try:
                confirmed = refresh_parent(g.parent_id, row['email'])
            except SchoolDirectoryError:
                # Não usa vínculos locais antigos quando a consulta escolar falha.
                session.clear()
                g.parent_id = None
            else:
                if not confirmed:
                    session.clear()
                    g.parent_id = None
    return g.parent_id

def login_required(f):
    """Decorator para rotas que exigem login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        role = active_staff_role()
        if role is None:
            return redirect('/')
        if role == 'vigia' and f.__name__ not in _VIGIA_ALLOWED_ENDPOINTS:
            return "Acesso negado: porteiros só podem acessar a lista de saídas.", 403
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Decorator para rotas que exigem admin"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        role = active_staff_role()
        if role is None:
            return redirect('/')
        if role != 'admin':
            return "Acesso negado", 403
        return f(*args, **kwargs)
    return decorated_function

def pai_required(f):
    """Decorator para rotas do portal dos responsáveis (sessão pai_id separada da sessão de staff)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if active_parent_id() is None:
            return redirect('/pais/login')
        return f(*args, **kwargs)
    return decorated_function

def solicitacao_required(f):
    """Permite revisar solicitações aos perfis admin e básico."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        role = active_staff_role()
        if role is None:
            return redirect('/')
        if role not in ('admin', 'basico'):
            return "Acesso negado", 403
        return f(*args, **kwargs)
    return decorated_function


def release_required(f):
    """Permite liberar uma saída apenas à portaria ou a um administrador."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        role = active_staff_role()
        if role is None:
            return redirect('/')
        if role not in ('admin', 'vigia'):
            return "Acesso negado", 403
        return f(*args, **kwargs)
    return decorated_function
