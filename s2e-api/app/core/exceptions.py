class S2EError(Exception):
    """Exceção base do sistema"""
    pass

class NotFoundError(S2EError):
    """Recurso não encontrado"""
    pass

class ValidationError(S2EError):
    """Erro de validação de dados"""
    pass

class UnauthorizedError(S2EError):
    """Não autorizado"""
    pass

class ForbiddenError(S2EError):
    """Acesso negado"""
    pass