from functools import wraps
from flask import session, redirect, request

from app.core.audit_logger import log_operacao
from app.core.cache import TTLCache
from app.core.database import get_db

# Quanto tempo o status de um responsável vale sem reconsultar o banco.
#
# Bloquear uma conta gravava `responsaveis.status` e mais nada: nenhuma rota do portal relia essa
# coluna depois do login. Quem já estava logado quando a escola descobriu que ele não pode mais
# retirar a criança mantinha acesso total — dashboard, nova solicitação, edição — por até 8h de
# sessão ou 30 min de inatividade. O bloqueio só passava a valer no login seguinte.
#
# Reler a cada request resolveria, mas somaria uma consulta por clique justamente no pico das
# 15h. 30 segundos é o meio-termo: a janela de bloqueio efetivo cai de horas para meio minuto, e
# o banco leva uma consulta por responsável a cada 30s em vez de uma por página.
#
# O cache é por processo e o gunicorn tem mais de um worker — por isso quem bloqueia não tenta
# invalidá-lo: é o TTL que garante o limite, em qualquer worker, sem coordenação entre eles.
_TTL_STATUS_RESPONSAVEL = 30
_cache_status_responsavel = TTLCache(ttl_seconds=_TTL_STATUS_RESPONSAVEL)

# Único status que dá acesso ao portal. 'pendente' (aguardando aprovação) e 'bloqueado' ficam de
# fora, exatamente como já acontece no login.
_STATUS_ATIVO = 'aprovado'


def responsavel_ativo(pai_id):
    """Relê `responsaveis.status`, com cache curto de processo.

    Linha ausente conta como inativa: é o caso da conta apagada com a sessão ainda aberta.

    Falha do banco não é tratada aqui de propósito — psycopg2.OperationalError já cai no
    errorhandler que mostra a página de indisponibilidade, e é o mesmo desfecho de qualquer outra
    rota quando o banco próprio está fora do ar.
    """
    status = _cache_status_responsavel.get(pai_id)
    if status is None:
        with get_db() as conn:
            linha = conn.execute(
                "SELECT status FROM responsaveis WHERE id = %s", (pai_id,)
            ).fetchone()
        status = linha['status'] if linha else 'inexistente'
        _cache_status_responsavel.set(pai_id, status)
    return status == _STATUS_ATIVO

# LGPD Art. 46 (💻 App obligation) — need-to-know access control: routes forbidden to vigia role
_VIGIA_BLOCKED_ENDPOINTS = {
    'historico_geral', 'registrar_saida', 'editar_saida',
    # Mesma razão do histórico geral: a lista de alunos e a ficha individual são o cadastro da
    # escola inteira, e o porteiro só precisa das saídas do dia.
    'lista_alunos', 'historico_do_aluno',
    # E a busca que alimenta a tela de registrar saída, pelo mesmo motivo — ela ficava de fora
    # da lista e entregava em JSON (ra, nome, turma, série) exatamente o cadastro que as duas
    # linhas acima negam. Bastavam algumas dezenas de `?q=` para baixar a escola inteira. O
    # endpoint só existe para /registrar_saida, que o vigia já não acessa.
    'portaria_buscar_aluno',
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
        # Ter entrado não é ter permanecido: o bloqueio precisa alcançar a sessão já aberta.
        # Ver responsavel_ativo() no topo do módulo para o custo e o tamanho da janela.
        if not responsavel_ativo(session['pai_id']):
            log_operacao(f"responsavel:{session.get('pai_email', 'desconhecido')}",
                         "SESSÃO ENCERRADA", "conta não está mais ativa",
                         ip=request.remote_addr)
            session.clear()
            return redirect('/pais/login?bloqueado=1')
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