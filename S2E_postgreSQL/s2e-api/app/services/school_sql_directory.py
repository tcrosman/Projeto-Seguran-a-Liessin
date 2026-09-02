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
        # Segundos para abrir a conexão e para cada consulta. Curto de propósito: é melhor a tela
        # mostrar o aluno sem foto, ou a busca voltar vazia, do que a request inteira travar.
        self._timeout = int(os.getenv('SCHOOL_SQL_TIMEOUT_SEG', 5))

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
            conn = sqlite3.connect(self._database, timeout=self._timeout)
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
                # Sem estes dois limites, o banco da escola fora do ar pendura a request até o
                # timeout de TCP do sistema (mais de um minuto). Como essas consultas rodam dentro
                # de rotas e o gunicorn tem poucos workers, bastavam alguns acessos simultâneos
                # para o S2E inteiro parar de responder — inclusive a tela da portaria.
                connect_timeout=self._timeout,                                   # abrir a conexão
                options=f'-c statement_timeout={self._timeout * 1000}',          # e executar a query
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

    LIMITE_BUSCA_PADRAO = 20

    def list_students(self, limite: int = None) -> list:
        """Lista os alunos ativos em ordem alfabética — a tela de alunos da escola.

        Existe separada de `search_students` porque aquela exige um termo: sem ele devolve lista
        vazia, e a tela abriria em branco. O limite é o que segura o tamanho da resposta; quem
        chama avisa na tela quando o corte aconteceu.
        """
        limite = int(limite or self.LIMITE_BUSCA_PADRAO)
        try:
            linhas = self._consultar(
                f"""SELECT ra, nome, turma, serie, foto_url FROM alunos
                    WHERE ativo = 1 ORDER BY nome LIMIT {self._ph}""",
                (limite,),
            )
            emails = self._emails_por_ra([l["ra"] for l in linhas])
            return [self._montar_aluno(l, emails.get(l["ra"], [])) for l in linhas]
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao listar alunos: {e}")
            return []

    def search_students(self, query: str, limite: int = None) -> list:
        """Busca alunos por nome ou RA parcial.

        O padrão de 20 serve à portaria, onde a lista é um autocomplete e ninguém rola além dos
        primeiros nomes. O histórico passa um limite maior: lá o nome vira a lista de RAs usada
        para filtrar as saídas, e cortar em 20 faria sumir, sem aviso, o histórico dos demais
        alunos que casam com um sobrenome comum.
        """
        q = query.strip()
        if not q:
            return []
        limite = int(limite or self.LIMITE_BUSCA_PADRAO)
        try:
            # ESCAPE explícito: o SQLite não tem caractere de escape padrão no LIKE, então sem
            # isso um '%' digitado na busca da portaria listaria a escola inteira.
            linhas = self._consultar(
                f"""SELECT ra, nome, turma, serie, foto_url FROM alunos
                    WHERE ativo = 1 AND ({self._expr_norm('nome')} LIKE {self._ph} ESCAPE '\\'
                                         OR ra LIKE {self._ph} ESCAPE '\\')
                    ORDER BY nome LIMIT {self._ph}""",
                (f"%{escapar_like(self._normalizar(q))}%", f"%{escapar_like(q)}%", limite),
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

    def list_students(self, limite: int = None) -> list:
        ordenados = sorted(self._ALUNOS.values(), key=lambda a: a['nome'])
        return [dict(a) for a in ordenados][:int(limite or self.LIMITE_BUSCA_PADRAO)]

    def search_students(self, query: str, limite: int = None) -> list:
        q = query.strip().lower()
        if not q:
            return []
        achados = [dict(a) for a in self._ALUNOS.values()
                   if q in a['nome'].lower() or q in a['ra']]
        return achados[:int(limite or self.LIMITE_BUSCA_PADRAO)]

    def get_guardian_emails_for_ra(self, ra: str) -> list:
        aluno = self._ALUNOS.get(ra)
        return list(aluno['responsaveis_email']) if aluno else []

    def responsavel_reconhecido(self, email: str) -> bool:
        # Deriva da lista de alunos em vez de manter uma lista de e-mails separada —
        # uma fonte só, sem risco de desalinhar entre os dois conjuntos de dados de teste.
        email_norm = email.strip().lower()
        return any(email_norm in a['responsaveis_email'] for a in self._ALUNOS.values())


def aluno_vinculado_ao_responsavel(diretorio, email, ra):
    """Reconfere se o aluno `ra` continua vinculado ao responsável `email`.

    Devolve 'ok', 'sem_vinculo' ou 'indisponivel'.

    Existe separada de `responsavel_reconhecido()` porque as duas respondem perguntas
    diferentes. Aquela confirma que a pessoa tem ALGUM vínculo ativo, e isso basta para o login.
    Esta pergunta por um aluno específico, que é o que autoriza agir sobre a saída dele: quem
    perdeu o vínculo com um filho e mantém o de outro continua entrando no portal normalmente, e
    sem esta checagem alcançaria a solicitação antiga do primeiro — mudando data, horário e
    acompanhante, isto é, quem busca a criança.

    Recebe o cliente pronto em vez de chamar `get_school_sql_directory()` por conta própria: cada
    rota já resolve o diretório pelo seu próprio módulo, e é esse ponto que os testes substituem.

    Fail-closed de propósito: sem resposta do banco da escola o resultado é 'indisponivel', que
    quem chama trata como recusa. Uma saída de menor não pode ser autorizada por omissão de quem
    deveria confirmar o vínculo.
    """
    if not email or not ra:
        return 'sem_vinculo'
    try:
        filhos = diretorio.get_students_for_guardian_email(email)
    except Exception as e:
        _log.warning("[SCHOOL_SQL] Vínculo pai-aluno não pôde ser reconferido: %s", e)
        return 'indisponivel'
    return 'ok' if any(f.get('ra') == ra for f in filhos) else 'sem_vinculo'


def get_school_sql_directory() -> SchoolSqlDirectoryClient:
    """Retorna o mock ou o client real conforme SCHOOL_SQL_MOCK, que precisa estar definido.

    Antes o padrão era `true`: faltando a variável, o sistema subia sem erro nenhum servindo os
    três alunos de teste como se fossem o corpo discente da escola. É o pior tipo de falha — a
    configuração ausente não quebrava nada, só entregava dado falso com cara de dado real, e o
    `render.yaml` não declarava nenhuma variável SCHOOL_SQL_*.

    Agora a escolha é obrigatória e explícita, no mesmo espírito da checagem de SECRET_KEY em
    create_app(): sem ela o app se recusa a subir, em vez de fingir que está funcionando.
    """
    modo = os.getenv('SCHOOL_SQL_MOCK')
    if modo is None or modo.strip() == '':
        raise RuntimeError(
            "SCHOOL_SQL_MOCK não definida. Escolha explicitamente a origem dos dados de aluno:\n"
            "  SCHOOL_SQL_MOCK=true   -> três alunos fixos, para desenvolvimento e testes\n"
            "  SCHOOL_SQL_MOCK=false  -> banco SQL da instituição (exige SCHOOL_SQL_ENGINE e "
            "SCHOOL_SQL_DATABASE)\n"
            "Sem essa variável o sistema serviria dados de teste como se fossem reais."
        )
    if modo.strip().lower() == 'true':
        return SchoolSqlDirectoryMock()
    return SchoolSqlDirectoryClient()
