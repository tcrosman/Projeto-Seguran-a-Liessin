from functools import wraps
from flask import session, redirect, url_for, request
from datetime import datetime

def login_required(f):
    """Decorator para rotas que exigem login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect('/')
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

def log_access(f):
    """Decorator para registrar acesso a rotas"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' in session:
            with open('logs/system.log', 'a', encoding='utf-8') as log:
                log.write(f"[{datetime.now()}] {session['username']} - ACESSOU: {request.path}\n")
        return f(*args, **kwargs)
    return decorated_function