from functools import wraps
from flask import session, redirect, request

from app.core.audit_logger import log_operacao

# LGPD Art. 46 (💻 App obligation) — need-to-know access control: routes forbidden to vigia role
_VIGIA_BLOCKED_ENDPOINTS = {
    'historico_geral', 'registrar_saida', 'editar_saida',
    # Mesma razão do histórico geral: a lista de alunos e a ficha individual são o cadastro da
    # escola inteira, e o porteiro só precisa das saídas do dia.
    'lista_alunos', 'historico_do_aluno',
    'configuracoes', 'admin_backup', 'gerenciar_usuarios', 'deletar_usuario',
    'manual', 'manual_basico', 'manual_avancado',
}

def login_required(f):
    """Decorator para rotas que exigem login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect('/')
        # LGPD Art. 46 (💻 App obligation) — block vigia from non-operational routes
        if session.get('role') == 'vigia' and f.__name__ in _VIGIA_BLOCKED_ENDPOINTS:
            return "Acesso negado: porteiros só podem acessar a lista de saídas.", 403
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Decorator para rotas que exigem admin"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect('/')
        if session.get('role') != 'admin':
            return "Acesso negado", 403
        return f(*args, **kwargs)
    return decorated_function

def pai_required(f):
    """Decorator para rotas do portal dos responsáveis (sessão pai_id separada da sessão de staff)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'pai_id' not in session:
            return redirect('/pais/login')
        return f(*args, **kwargs)
    return decorated_function

def log_access(f):
    """Decorator para registrar acesso a rotas"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' in session:
            log_operacao(session['username'], "ACESSOU", request.path, ip=request.remote_addr)
        return f(*args, **kwargs)
    return decorated_function