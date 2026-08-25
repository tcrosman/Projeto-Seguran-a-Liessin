from unittest.mock import patch

from app.api.pais import _responsavel_reconhecido_pela_escola


def test_email_reconhecido_libera_cadastro():
    class _Ok:
        def responsavel_reconhecido(self, email):
            return True

    with patch("app.api.pais.get_school_sql_directory", return_value=_Ok()):
        assert _responsavel_reconhecido_pela_escola("pai@teste.com") is True


def test_email_nao_reconhecido_bloqueia_cadastro():
    class _Nao:
        def responsavel_reconhecido(self, email):
            return False

    with patch("app.api.pais.get_school_sql_directory", return_value=_Nao()):
        assert _responsavel_reconhecido_pela_escola("estranho@teste.com") is False


def test_falha_na_consulta_bloqueia_cadastro_fail_closed():
    """Servidor SQL fora do ar, config ausente, etc — não pode liberar cadastro só porque
    a checagem quebrou. Autocadastro sem confirmação de vínculo com a escola é o cenário
    que essa validação existe pra evitar."""
    class _Quebrado:
        def responsavel_reconhecido(self, email):
            raise RuntimeError("Banco SQL de responsáveis ainda não definido pela instituição")

    with patch("app.api.pais.get_school_sql_directory", return_value=_Quebrado()):
        assert _responsavel_reconhecido_pela_escola("pai@teste.com") is False
