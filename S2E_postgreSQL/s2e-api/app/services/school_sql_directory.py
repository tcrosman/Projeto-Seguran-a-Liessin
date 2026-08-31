import os
from app.core.logging_config import obter
from app.core.validators import escapar_like

_log = obter()


class SchoolSqlDirectoryClient:
    """Consulta dados cadastrais de aluno (RA, nome, foto, turma, série) e confirma responsáveis
    reconhecidos, direto via SQL num banco separado mantido pela instituição — alimentado a partir
    do TOTVS, mas sem o S2E falar com o TOTVS diretamente. Nada do que é retornado aqui é gravado
    no Postgres do S2E; quem chama deve manter isso só em memória de curto prazo (ver
    app/core/cache.py).

    O banco real da instituição ainda não foi definido. Enquanto isso, `SCHOOL_SQL_ENGINE=sqlite`
    aponta para o banco provisório de `S2E_postgreSQL/escola_provisoria/` — cujo schema serve de
    especificação do mínimo que a escola precisa expor: `alunos(ra, nome, turma, serie, foto_url,
    ativo)`, `responsaveis(email, nome, ativo)` e `vinculos(ra, email)`.

    Para plugar o banco real: acrescentar o engine em `_conectar()` (psycopg2 já está no projeto
    para Postgres; pyodbc/pymssql para SQL Server; cx_Oracle para Oracle) e ajustar os nomes de
    tabela/coluna se a escola usar outros. O texto das queries já usa um placeholder neutro, então
    a diferença de sintaxe (`?` no SQLite vs `%s` no psycopg2) não exige reescrevê-las.

    Nenhum método propaga exceção: falha de conexão ou de consulta vira log + retorno seguro
    (None/{}/[]/False), para uma indisponibilidade do banco da escola não derrubar o S2E.
    """

    def __init__(self):
        self._engine = os.getenv('SCHOOL_SQL_ENGINE', 'sqlite').lower()
        self._host = os.getenv('SCHOOL_SQL_HOST', '')
        self._port = os.getenv('SCHOOL_SQL_PORT', '')
        self._database = os.getenv('SCHOOL_SQL_DATABASE', '')
        self._user = os.getenv('SCHOOL_SQL_USER', '')
        self._password = os.getenv('SCHOOL_SQL_PASSWORD', '')

    @property
    def _ph(self):
        """Placeholder de parâmetro do driver em uso."""
        return '?' if self._engine == 'sqlite' else '%s'

    @staticmethod
    def _normalizar(texto):
        """Minúsculas sem acento — 'JÚLIA' e 'julia' passam a casar na busca.

        O LOWER() do SQLite só cobre ASCII (deixaria 'JÚLIA' como 'jÚlia'), então esta função é
        registrada na conexão para ser usada dentro do SQL. No Postgres o equivalente nativo é
        unaccent(lower(...)) — ver _expr_norm().
        """
        if texto is None:
            return None
        import unicodedata
        sem_acento = unicodedata.normalize('NFD', str(texto))
        return ''.join(c for c in sem_acento if unicodedata.category(c) != 'Mn').lower()

    def _expr_norm(self, coluna):
        """Expressão SQL que normaliza um texto, na sintaxe do engine em uso."""
        if self._engine == 'sqlite':
            return f"norm_texto({coluna})"
        return f"unaccent(lower({coluna}))"  # Postgres: exige a extensão unaccent

    def _conectar(self):
        if self._engine == 'sqlite':
            if not self._database:
                raise RuntimeError("SCHOOL_SQL_DATABASE (caminho do arquivo .db) não configurado no .env")
            import sqlite3
            conn = sqlite3.connect(self._database)
            conn.row_factory = sqlite3.Row
            conn.create_function("norm_texto", 1, self._normalizar)
            return conn
        if self._engine in ('postgres', 'postgresql'):
            if not self._host or not self._database:
                raise RuntimeError("SCHOOL_SQL_HOST e SCHOOL_SQL_DATABASE não configurados no .env")
            import psycopg2
            import psycopg2.extras
            return psycopg2.connect(
                host=self._host, port=self._port or 5432, dbname=self._database,
                user=self._user, password=self._password,
                cursor_factory=psycopg2.extras.RealDictCursor,
            )
        raise RuntimeError(
            f"SCHOOL_SQL_ENGINE='{self._engine}' não suportado. Use 'sqlite' (banco provisório) "
            "ou 'postgres'. Para outro banco, acrescente o driver em _conectar()."
        )

    def _consultar(self, sql, params=()):
        """Roda um SELECT e devolve lista de dicts. Levanta em caso de erro — quem chama trata."""
        conn = self._conectar()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    @staticmethod
    def _montar_aluno(row, emails):
        return {
            "ra": row["ra"], "nome": row["nome"], "turma": row["turma"],
            "serie": row["serie"], "foto_url": row["foto_url"],
            "responsaveis_email": emails,
        }

    def _emails_por_ra(self, ras):
        """Resolve {ra: [emails]} para vários RAs de uma vez, sem uma consulta por aluno."""
        if not ras:
            return {}
        marcadores = ",".join([self._ph] * len(ras))
        linhas = self._consultar(
            f"""SELECT v.ra, v.email FROM vinculos v
                JOIN responsaveis r ON r.email = v.email AND r.ativo = 1
                WHERE v.ra IN ({marcadores})""",
            tuple(ras),
        )
        agrupado = {ra: [] for ra in ras}
        for l in linhas:
            agrupado[l["ra"]].append(l["email"])
        return agrupado

    def get_student(self, ra: str):
        """Retorna os dados de um aluno pelo RA, ou None se não encontrado."""
        try:
            linhas = self._consultar(
                f"SELECT ra, nome, turma, serie, foto_url FROM alunos WHERE ra = {self._ph} AND ativo = 1",
                (ra,),
            )
            if not linhas:
                return None
            return self._montar_aluno(linhas[0], self._emails_por_ra([ra]).get(ra, []))
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao buscar aluno: {e}")
            return None

    def get_students_by_ras(self, ras: list) -> dict:
        """Retorna {ra: dados} para uma lista de RAs — usada em telas de lista (evita 1 consulta por linha)."""
        ras = list(ras)
        if not ras:
            return {}
        try:
            marcadores = ",".join([self._ph] * len(ras))
            linhas = self._consultar(
                f"SELECT ra, nome, turma, serie, foto_url FROM alunos WHERE ra IN ({marcadores}) AND ativo = 1",
                tuple(ras),
            )
            emails = self._emails_por_ra([l["ra"] for l in linhas])
            return {l["ra"]: self._montar_aluno(l, emails.get(l["ra"], [])) for l in linhas}
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao buscar alunos em lote: {e}")
            return {}

    def get_students_for_guardian_email(self, email: str) -> list:
        """Retorna a lista de alunos vinculados a um e-mail de responsável."""
        try:
            linhas = self._consultar(
                f"""SELECT a.ra, a.nome, a.turma, a.serie, a.foto_url
                    FROM alunos a
                    JOIN vinculos v ON v.ra = a.ra
                    JOIN responsaveis r ON r.email = v.email AND r.ativo = 1
                    WHERE LOWER(v.email) = {self._ph} AND a.ativo = 1
                    ORDER BY a.nome""",
                (email.strip().lower(),),
            )
            emails = self._emails_por_ra([l["ra"] for l in linhas])
            return [self._montar_aluno(l, emails.get(l["ra"], [])) for l in linhas]
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao buscar alunos do responsável: {e}")
            return []

    def search_students(self, query: str) -> list:
        """Busca alunos por nome ou RA parcial — usada pela portaria ao registrar uma saída."""
        q = query.strip()
        if not q:
            return []
        try:
            # ESCAPE explícito: o SQLite não tem caractere de escape padrão no LIKE, então sem
            # isso um '%' digitado na busca da portaria listaria a escola inteira.
            linhas = self._consultar(
                f"""SELECT ra, nome, turma, serie, foto_url FROM alunos
                    WHERE ativo = 1 AND ({self._expr_norm('nome')} LIKE {self._ph} ESCAPE '\\'
                                         OR ra LIKE {self._ph} ESCAPE '\\')
                    ORDER BY nome LIMIT 20""",
                (f"%{escapar_like(self._normalizar(q))}%", f"%{escapar_like(q)}%"),
            )
            emails = self._emails_por_ra([l["ra"] for l in linhas])
            return [self._montar_aluno(l, emails.get(l["ra"], [])) for l in linhas]
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao buscar alunos: {e}")
            return []

    def get_guardian_emails_for_ra(self, ra: str) -> list:
        """Retorna os e-mails dos responsáveis vinculados a um RA — usada para notificar após liberar a saída."""
        try:
            return self._emails_por_ra([ra]).get(ra, [])
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao buscar responsáveis: {e}")
            return []

    def responsavel_reconhecido(self, email: str) -> bool:
        """Retorna True se o e-mail corresponde a um responsável reconhecido pela escola —
        usada no autocadastro e no login do portal dos pais.

        Reconhecido = responsável ativo E com pelo menos um vínculo a aluno ativo. Quem perdeu o
        vínculo (filho saiu da escola) deixa de ser reconhecido e perde o acesso ao portal.
        """
        try:
            linhas = self._consultar(
                f"""SELECT 1 AS ok FROM responsaveis r
                    JOIN vinculos v ON v.email = r.email
                    JOIN alunos a ON a.ra = v.ra AND a.ativo = 1
                    WHERE LOWER(r.email) = {self._ph} AND r.ativo = 1
                    LIMIT 1""",
                (email.strip().lower(),),
            )
            return bool(linhas)
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao validar responsável: {e}")
            return False


class SchoolSqlDirectoryMock(SchoolSqlDirectoryClient):
    """Dados de teste locais, sem acessar o banco real (SCHOOL_SQL_MOCK=true, padrão em dev)."""

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

    def get_guardian_emails_for_ra(self, ra: str) -> list:
        aluno = self._ALUNOS.get(ra)
        return list(aluno['responsaveis_email']) if aluno else []

    def responsavel_reconhecido(self, email: str) -> bool:
        # Deriva da lista de alunos em vez de manter uma lista de e-mails separada —
        # uma fonte só, sem risco de desalinhar entre os dois conjuntos de dados de teste.
        email_norm = email.strip().lower()
        return any(email_norm in a['responsaveis_email'] for a in self._ALUNOS.values())


def get_school_sql_directory() -> SchoolSqlDirectoryClient:
    """Retorna o mock ou o client real com base em SCHOOL_SQL_MOCK (padrão: true em dev)."""
    if os.getenv('SCHOOL_SQL_MOCK', 'true').lower() == 'true':
        return SchoolSqlDirectoryMock()
    return SchoolSqlDirectoryClient()
