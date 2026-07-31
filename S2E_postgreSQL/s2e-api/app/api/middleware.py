from functools import wraps
from flask import session, redirect, url_for, request
from datetime import datetime

# LGPD Art. 46 (💻 App obligation) — need-to-know access control: routes forbidden to vigia role
_VIGIA_BLOCKED_ENDPOINTS = {
    'cadastro_aluno', 'editar_aluno', 'deletar_aluno', 'historico_aluno',
    'historico_geral', 'registrar_saida', 'editar_saida',
    'configuracoes', 'admin_backup', 'gerenciar_usuarios', 'deletar_usuario',
    'configurar_horarios', 'cadastro_massa', 'manual', 'manual_basico', 'manual_avancado',
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
            with open('logs/system.log', 'a', encoding='utf-8') as log:
                log.write(f"[{datetime.now()}] {session['username']} - ACESSOU: {request.path}\n")
        return f(*args, **kwargs)
    return decorated_function