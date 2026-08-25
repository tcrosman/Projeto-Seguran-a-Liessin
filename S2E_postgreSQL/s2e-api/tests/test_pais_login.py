"""O /pais/login usava o TOTVS para reconfirmar o vínculo do responsável a cada login; agora
usa o mesmo _responsavel_reconhecido_pela_escola do cadastro, contra o banco SQL da escola.
Estes testes cobrem essa validação no contexto do login — se ela parar de bloquear, um e-mail
que a escola não reconhece mais (responsável desvinculado) continuaria entrando no portal.
"""
from unittest.mock import patch

from app.api.pais import _responsavel_reconhecido_pela_escola


def test_login_prossegue_para_2fa_quando_escola_reconhece():
    class _Ok:
        def responsavel_reconhecido(self, email):
            return True

    with patch("app.api.pais.get_school_sql_directory", return_value=_Ok()):
        assert _responsavel_reconhecido_pela_escola("pai@teste.com") is True


def test_login_bloqueado_quando_escola_nao_reconhece_mais():
    """Responsável que perdeu o vínculo com a escola não pode logar, mesmo com conta
    local aprovada e senha correta."""
    class _Nao:
        def responsavel_reconhecido(self, email):
            return False

    with patch("app.api.pais.get_school_sql_directory", return_value=_Nao()):
        assert _responsavel_reconhecido_pela_escola("ex-responsavel@teste.com") is False


def test_login_bloqueado_quando_consulta_falha_fail_closed():
    """Banco SQL da escola fora do ar não pode virar liberação de acesso."""
    class _Quebrado:
        def responsavel_reconhecido(self, email):
            raise RuntimeError("Banco SQL da escola ainda não definido pela instituição")

    with patch("app.api.pais.get_school_sql_directory", return_value=_Quebrado()):
        assert _responsavel_reconhecido_pela_escola("pai@teste.com") is False
