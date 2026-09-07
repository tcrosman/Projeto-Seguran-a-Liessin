from unittest.mock import patch

from app.api.web import _nome_e_emails_para_saida


class _ClienteQuebrado:
    """Simula SchoolSqlDirectoryClient real falhando (banco fora, config ausente, etc)."""

    def get_student(self, ra):
        raise RuntimeError("Banco SQL da escola ainda não definido pela instituição")

    def get_guardian_emails_for_ra(self, ra):
        raise RuntimeError("Banco SQL da escola ainda não definido pela instituição")


def test_diretorio_indisponivel_nao_impede_conclusao():
    """/concluir_saida já persistiu o UPDATE antes de chamar isso — uma falha aqui não pode
    voltar a propagar (o que faria get_db() dar rollback na liberação que já aconteceu)."""
    with patch("app.api.web.get_school_sql_directory", return_value=_ClienteQuebrado()):
        nome_aluno, emails, indisponivel = _nome_e_emails_para_saida("2024001")

    assert nome_aluno == "RA 2024001"
    assert emails == []
    # A indisponibilidade é reportada, e não confundida com "não há e-mail cadastrado" (A11).
    assert indisponivel is True


def test_diretorio_disponivel_resolve_nome_e_emails():
    class _ClienteOk:
        def get_student(self, ra):
            return {"ra": ra, "nome": "Ana Beatriz Souza"}

        def get_guardian_emails_for_ra(self, ra):
            return ["pai@teste.com", "mae@teste.com"]

    with patch("app.api.web.get_school_sql_directory", return_value=_ClienteOk()):
        nome_aluno, emails, indisponivel = _nome_e_emails_para_saida("2024001")

    assert nome_aluno == "Ana Beatriz Souza"
    assert emails == ["pai@teste.com", "mae@teste.com"]
    assert indisponivel is False


def test_aluno_nao_encontrado_usa_fallback_de_ra():
    class _ClienteSemAluno:
        def get_student(self, ra):
            return None

        def get_guardian_emails_for_ra(self, ra):
            return []

    with patch("app.api.web.get_school_sql_directory", return_value=_ClienteSemAluno()):
        nome_aluno, emails, indisponivel = _nome_e_emails_para_saida("2024999")

    assert nome_aluno == "RA 2024999"
    assert emails == []
    # O diretório respondeu: aluno sem e-mail cadastrado, não indisponibilidade.
    assert indisponivel is False
