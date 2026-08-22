import os


class SchoolDirectoryClient:
    """Consulta dados cadastrais do aluno (RA, nome, foto, turma, série) no sistema da escola.

    A consulta em si é especificada por este sistema mas construída e mantida pela instituição —
    este client só chama o que ela expuser. Nada do que é retornado aqui é gravado no Postgres do
    S2E; quem chama deve manter isso só em memória de curto prazo (ver app/core/cache.py).
    """

    def __init__(self):
        self._url = os.getenv('SCHOOL_DIRECTORY_API_URL', '').rstrip('/')
        self._token = os.getenv('SCHOOL_DIRECTORY_API_TOKEN', '')

    def get_student(self, ra: str):
        """Retorna os dados de um aluno pelo RA, ou None se não encontrado."""
        raise NotImplementedError(
            "Fonte real da consulta ainda não definida pela instituição. "
            "Use SCHOOL_DIRECTORY_MOCK=true durante o desenvolvimento."
        )

    def get_students_by_ras(self, ras: list) -> dict:
        """Retorna {ra: dados} para uma lista de RAs — usada em telas de lista (evita 1 consulta por linha)."""
        raise NotImplementedError(
            "Fonte real da consulta ainda não definida pela instituição. "
            "Use SCHOOL_DIRECTORY_MOCK=true durante o desenvolvimento."
        )

    def get_students_for_guardian_email(self, email: str) -> list:
        """Retorna a lista de alunos vinculados a um e-mail de responsável."""
        raise NotImplementedError(
            "Fonte real da consulta ainda não definida pela instituição. "
            "Use SCHOOL_DIRECTORY_MOCK=true durante o desenvolvimento."
        )

    def search_students(self, query: str) -> list:
        """Busca alunos por nome ou RA parcial — usada pela portaria ao registrar uma saída."""
        raise NotImplementedError(
            "Fonte real da consulta ainda não definida pela instituição. "
            "Use SCHOOL_DIRECTORY_MOCK=true durante o desenvolvimento."
        )


class SchoolDirectoryMock(SchoolDirectoryClient):
    """Dados de teste locais, sem acessar a consulta real (SCHOOL_DIRECTORY_MOCK=true, padrão em dev).

    Reaproveita os e-mails já usados em TotusClientMock (app/services/totus_client.py) para que os
    dois mocks fiquem consistentes entre si durante testes manuais do portal dos pais.
    """

    _ALUNOS = {
        "2024001": {
            "ra": "2024001", "nome": "Ana Beatriz Souza", "turma": "A", "serie": "6º ano EF",
            "foto_url": None, "responsaveis_email": ["pai@teste.com", "mae@teste.com"],
        },
        "2024002": {
            "ra": "2024002", "nome": "Carlos Eduardo Lima", "turma": "B", "serie": "8º ano EF",
            "foto_url": None, "responsaveis_email": ["responsavel@teste.com"],
        },
        "2024003": {
            "ra": "2024003", "nome": "Theo Crosman", "turma": "A", "serie": "3º ano EM",
            "foto_url": None, "responsaveis_email": ["theocrosman@gmail.com"],
        },
    }

    def get_student(self, ra: str):
        aluno = self._ALUNOS.get(ra)
        return dict(aluno) if aluno else None

    def get_students_by_ras(self, ras: list) -> dict:
        return {ra: dict(self._ALUNOS[ra]) for ra in ras if ra in self._ALUNOS}

    def get_students_for_guardian_email(self, email: str) -> list:
        email_norm = email.strip().lower()
        return [dict(a) for a in self._ALUNOS.values() if email_norm in a['responsaveis_email']]

    def search_students(self, query: str) -> list:
        q = query.strip().lower()
        if not q:
            return []
        return [dict(a) for a in self._ALUNOS.values() if q in a['nome'].lower() or q in a['ra']]


def get_school_directory() -> SchoolDirectoryClient:
    """Retorna o mock ou o client real com base em SCHOOL_DIRECTORY_MOCK (padrão: true em dev)."""
    if os.getenv('SCHOOL_DIRECTORY_MOCK', 'true').lower() == 'true':
        return SchoolDirectoryMock()
    return SchoolDirectoryClient()
