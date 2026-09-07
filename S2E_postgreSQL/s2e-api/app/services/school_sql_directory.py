import os
from contextlib import contextmanager
from threading import BoundedSemaphore, Lock

from app.core.logging_config import obter
from app.core.validators import escapar_like

_log = obter()

# Pool de conexões do banco da escola — mesmo desenho do pool do banco próprio, em
# app/core/database.py, e pelo mesmo motivo.
#
# Antes, cada consulta abria e fechava a própria conexão: TCP + autenticação por query. E as
# consultas vêm em grupo, não isoladas — get_student são 2 conexões (por causa de
# _emails_por_ra), _nome_e_emails_para_saida são 3, /alunos são 2, e o autocomplete da portaria
# são 2 a cada tecla digitada. Numa tarde de saídas isso é o handshake dominando a latência de
# uma consulta que já é a mais lenta do sistema.
#
# Só para Postgres. Com SQLite (o banco provisório de escola_provisoria/) abrir o arquivo é
# barato e a conexão não pode ser compartilhada entre threads sem cuidado extra — não vale a
# complexidade por um banco que é de desenvolvimento.
POOL_MAX = int(os.getenv('SCHOOL_SQL_POOL_MAX', 4))

_pool = None
_vagas = None       # semáforo: quantas conexões ainda podem ser retiradas do pool
_trava_pool = Lock()

# A extensão `unaccent` existe no banco da escola? None = ainda não foi verificado.
#
# Ela é exigida por _expr_norm() no Postgres, e não vem instalada por padrão. Sem ela, TODA busca
# de aluno falhava com "function unaccent(text) does not exist" — erro capturado pelo `except`
# de cada método e transformado em lista vazia. O sintoma para quem estava na portaria era
# "nenhum aluno encontrado", às 15h, no dia da virada do mock para o banco real.
#
# Verificado uma vez por processo, e não a cada busca: é uma propriedade do banco, não da
# consulta. Enquanto for None a busca degrada para lower() puro, que é o comportamento seguro.
_unaccent = None


def _reiniciar_pool():
    """Descarta o pool e o que foi descoberto sobre o banco da escola. Existe para os testes
    trocarem de configuração sem herdar o estado anterior — em produção isso vive enquanto o
    worker viver."""
    global _pool, _vagas, _unaccent
    _unaccent = None
    with _trava_pool:
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:
                pass
        _pool, _vagas = None, None


class SchoolSqlIndisponivel(RuntimeError):
    """O banco da escola não respondeu: conexão recusada, timeout, configuração ausente, erro de
    consulta.

    Existe para separar duas respostas que antes chegavam iguais a quem chama: "este aluno não
    existe" e "não consegui perguntar". Cada método capturava a falha, registrava no log e
    devolvia None/{}/[]/False — o mesmo valor de um resultado vazio legítimo. Com isso:

      * o cache servia o último valor conhecido como se a consulta tivesse dado certo e o aluno
        tivesse sumido do cadastro, ou o contrário, sem meio de distinguir (A10);
      * "Aluno não encontrado no cadastro da escola" aparecia na tela quando o banco estava fora
        do ar, e a secretaria repetia a busca achando que o aluno tinha sido desligado (M7);
      * a liberação de saída seguia sem notificar ninguém, sem distinguir "não há e-mail
        cadastrado" de "não consegui perguntar" (A11).

    Herda de RuntimeError de propósito: todo chamador já envolve estas consultas em
    `except Exception`, então a promessa que valia antes — indisponibilidade do banco da escola
    não derruba o S2E — continua valendo. O que muda é que agora dá para saber o que aconteceu.
    """


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

    Para plugar o banco real: acrescentar o engine em `_conexao()` — com pool, no molde de
    `_pool_postgres()`, que é o que o Postgres já usa — e ajustar os nomes de tabela/coluna se a
    escola usar outros (pyodbc/pymssql para SQL Server; cx_Oracle para Oracle). O texto das
    queries já usa um placeholder neutro, então a diferença de sintaxe (`?` no SQLite vs `%s` no
    psycopg2) não exige reescrevê-las.

    Falha de conexão ou de consulta vira log + `SchoolSqlIndisponivel`. Retorno vazio
    (None/{}/[]/False) passa a significar exatamente uma coisa: a pergunta foi feita e a
    resposta é "não existe". Quem chama trata as duas situações — e tem de tratar, porque elas
    pedem telas diferentes: "aluno não encontrado" e "cadastro da escola indisponível".
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

    def _expr_ativo(self, coluna):
        """Condição SQL para "está ativo", aceitando as duas formas que a escola pode usar.

        As consultas comparavam `ativo = 1` direto. Isso só funciona se a coluna for numérica —
        e `ativo` como BOOLEAN é o normal em PostgreSQL, que é o engine que o render.yaml já
        fixa. Contra um BOOLEAN, toda consulta dava "operator does not exist: boolean = integer",
        o erro era capturado pelo `except` de cada método e virava resultado vazio. O app subia,
        nenhum erro além de um warning, e ninguém conseguia registrar uma saída às 15h.

        No Postgres o cast resolve os dois casos de uma vez: `1::boolean` é true e
        `true::boolean` é ele mesmo. No SQLite não existe tipo booleano — 0/1 é a única forma.
        """
        if self._engine == 'sqlite':
            return f"{coluna} = 1"
        return f"({coluna})::boolean IS TRUE"

    def _tem_unaccent(self):
        """A extensão `unaccent` está instalada no banco da escola? Verificado uma vez por processo.

        Não vem instalada por padrão no PostgreSQL. Sem ela, a expressão de _expr_norm() falha e
        a busca de aluno devolve vazio — silenciosamente, porque cada método captura a exceção.
        Melhor descobrir e degradar do que buscar às cegas.
        """
        global _unaccent
        if self._engine == 'sqlite':
            return False        # lá a normalização é a função Python registrada na conexão
        if _unaccent is None:
            try:
                linhas = self._consultar(
                    "SELECT 1 AS ok FROM pg_extension WHERE extname = 'unaccent'")
            except Exception as e:
                # Sem resposta não dá para concluir nada; degrada agora e tenta de novo depois.
                _log.warning("[SCHOOL_SQL] Não foi possível verificar a extensão unaccent: %s", e)
                return False
            _unaccent = bool(linhas)
            if not _unaccent:
                _log.warning(
                    "[SCHOOL_SQL] Extensão `unaccent` ausente no banco da escola. A busca de "
                    "aluno vai funcionar, mas sem ignorar acentos: 'Julia' não encontra 'Júlia'. "
                    "Peça à instituição: CREATE EXTENSION unaccent;")
        return _unaccent

    def _expr_norm(self, coluna):
        """Expressão SQL que normaliza um texto, na sintaxe do engine em uso.

        Sem a extensão `unaccent` o Postgres degrada para lower() puro — a busca perde a
        insensibilidade a acento, mas continua funcionando. É melhor que o comportamento
        anterior, em que a consulta inteira falhava e a tela dizia "nenhum aluno encontrado".
        """
        if self._engine == 'sqlite':
            return f"norm_texto({coluna})"
        if self._tem_unaccent():
            return f"unaccent(lower({coluna}))"
        return f"lower({coluna})"

    def _normalizar_termo(self, texto):
        """Normaliza o termo buscado do MESMO jeito que _expr_norm() normaliza a coluna.

        Se os dois lados discordarem a busca simplesmente não casa: tirar o acento do termo e
        não da coluna faz 'julia' procurar por 'júlia' e não achar nada.
        """
        if self._engine == 'sqlite' or self._tem_unaccent():
            return self._normalizar(texto)
        return (texto or '').lower()

    def _conectar(self):
        """Abre uma conexão avulsa. Só o SQLite passa por aqui — no Postgres a conexão vem do
        pool (ver _conexao/_pool_postgres), que é onde os mesmos limites de tempo são aplicados.
        """
        if self._engine == 'sqlite':
            if not self._database:
                raise RuntimeError("SCHOOL_SQL_DATABASE (caminho do arquivo .db) não configurado no .env")
            import sqlite3
            conn = sqlite3.connect(self._database, timeout=self._timeout)
            conn.row_factory = sqlite3.Row
            conn.create_function("norm_texto", 1, self._normalizar)
            return conn
        raise RuntimeError(
            f"SCHOOL_SQL_ENGINE='{self._engine}' não suportado. Use 'sqlite' (banco provisório) "
            "ou 'postgres'. Para outro banco, acrescente o driver em _conectar()."
        )

    def _pool_postgres(self):
        """Devolve (pool, semáforo) do banco da escola, criando-os na primeira chamada.

        Criado sob demanda, nunca no import: o gunicorn faz fork dos workers, e um pool criado
        antes do fork teria os sockets compartilhados entre processos.
        """
        global _pool, _vagas
        if _pool is None:
            with _trava_pool:
                if _pool is None:
                    import psycopg2.pool
                    import psycopg2.extras
                    novo = psycopg2.pool.ThreadedConnectionPool(
                        minconn=1,
                        maxconn=POOL_MAX,
                        host=self._host, port=self._port or 5432, dbname=self._database,
                        user=self._user, password=self._password,
                        cursor_factory=psycopg2.extras.RealDictCursor,
                        # Sem estes dois limites, o banco da escola fora do ar pendura a
                        # request até o timeout de TCP do sistema (mais de um minuto). Como essas
                        # consultas rodam dentro de rotas e o gunicorn tem poucos workers,
                        # bastavam alguns acessos simultâneos para o S2E inteiro parar de
                        # responder — inclusive a tela da portaria.
                        connect_timeout=self._timeout,                            # abrir a conexão
                        options=f'-c statement_timeout={self._timeout * 1000}',   # e a query
                    )
                    # getconn() não espera: com o pool cheio ele levanta PoolError na hora. O
                    # semáforo faz a requisição excedente aguardar — e por no máximo o mesmo
                    # tempo dos outros limites do banco da escola, para uma tela nunca ficar
                    # presa aqui além do que já se aceita esperar por ele.
                    _vagas = BoundedSemaphore(POOL_MAX)
                    _pool = novo
        return _pool, _vagas

    @contextmanager
    def _conexao(self):
        """Empresta uma conexão: do pool no Postgres, aberta e fechada no SQLite."""
        if self._engine == 'sqlite':
            conn = self._conectar()
            try:
                yield conn
            finally:
                conn.close()
            return

        if not self._host or not self._database:
            raise RuntimeError("SCHOOL_SQL_HOST e SCHOOL_SQL_DATABASE não configurados no .env")
        if self._engine not in ('postgres', 'postgresql'):
            raise RuntimeError(
                f"SCHOOL_SQL_ENGINE='{self._engine}' não suportado. Use 'sqlite' (banco "
                "provisório) ou 'postgres'. Para outro banco, acrescente o driver em _conectar()."
            )

        import psycopg2
        pool, vagas = self._pool_postgres()
        if not vagas.acquire(timeout=self._timeout):
            raise RuntimeError(
                f"Sem conexão livre para o banco da escola após {self._timeout}s "
                "(SCHOOL_SQL_POOL_MAX)."
            )
        try:
            conn = pool.getconn()
            # O servidor pode ter fechado a conexão enquanto ela estava ociosa no pool.
            if conn.closed:
                pool.putconn(conn, close=True)
                conn = pool.getconn()
            descartar = False
            try:
                yield conn
                # Só leitura, mas o psycopg2 abre transação implícita no primeiro execute: sem
                # encerrá-la, a conexão volta ao pool com um snapshot preso, e o "idle in
                # transaction" segura recursos do banco da escola indefinidamente.
                conn.rollback()
            except Exception:
                try:
                    conn.rollback()
                except psycopg2.Error:
                    descartar = True    # conexão quebrada: não pode voltar para o pool
                raise
            finally:
                pool.putconn(conn, close=descartar or conn.closed)
        finally:
            # Só depois de devolver a conexão — senão outra thread acorda sem nada disponível.
            vagas.release()

    def _consultar(self, sql, params=()):
        """Roda um SELECT e devolve lista de dicts. Levanta em caso de erro — quem chama trata."""
        with self._conexao() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

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
                JOIN responsaveis r ON r.email = v.email AND {self._expr_ativo('r.ativo')}
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
                f"SELECT ra, nome, turma, serie, foto_url FROM alunos "
                f"WHERE ra = {self._ph} AND {self._expr_ativo('ativo')}",
                (ra,),
            )
            if not linhas:
                return None
            return self._montar_aluno(linhas[0], self._emails_por_ra([ra]).get(ra, []))
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao buscar aluno: {e}")
            raise SchoolSqlIndisponivel("banco da escola indisponível ao buscar aluno") from e

    def get_students_by_ras(self, ras: list) -> dict:
        """Retorna {ra: dados} para uma lista de RAs — usada em telas de lista (evita 1 consulta por linha)."""
        ras = list(ras)
        if not ras:
            return {}
        try:
            marcadores = ",".join([self._ph] * len(ras))
            linhas = self._consultar(
                f"SELECT ra, nome, turma, serie, foto_url FROM alunos "
                f"WHERE ra IN ({marcadores}) AND {self._expr_ativo('ativo')}",
                tuple(ras),
            )
            emails = self._emails_por_ra([l["ra"] for l in linhas])
            return {l["ra"]: self._montar_aluno(l, emails.get(l["ra"], [])) for l in linhas}
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao buscar alunos em lote: {e}")
            raise SchoolSqlIndisponivel("banco da escola indisponível ao buscar alunos") from e

    def get_students_for_guardian_email(self, email: str) -> list:
        """Retorna a lista de alunos vinculados a um e-mail de responsável."""
        try:
            linhas = self._consultar(
                f"""SELECT a.ra, a.nome, a.turma, a.serie, a.foto_url
                    FROM alunos a
                    JOIN vinculos v ON v.ra = a.ra
                    JOIN responsaveis r ON r.email = v.email AND {self._expr_ativo('r.ativo')}
                    WHERE LOWER(v.email) = {self._ph} AND {self._expr_ativo('a.ativo')}
                    ORDER BY a.nome""",
                (email.strip().lower(),),
            )
            emails = self._emails_por_ra([l["ra"] for l in linhas])
            return [self._montar_aluno(l, emails.get(l["ra"], [])) for l in linhas]
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao buscar alunos do responsável: {e}")
            raise SchoolSqlIndisponivel(
                "banco da escola indisponível ao buscar alunos do responsável") from e

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
                    WHERE {self._expr_ativo('ativo')} ORDER BY nome LIMIT {self._ph}""",
                (limite,),
            )
            emails = self._emails_por_ra([l["ra"] for l in linhas])
            return [self._montar_aluno(l, emails.get(l["ra"], [])) for l in linhas]
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao listar alunos: {e}")
            raise SchoolSqlIndisponivel("banco da escola indisponível ao listar alunos") from e

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
                    WHERE {self._expr_ativo('ativo')}
                      AND ({self._expr_norm('nome')} LIKE {self._ph} ESCAPE '\\'
                                         OR ra LIKE {self._ph} ESCAPE '\\')
                    ORDER BY nome LIMIT {self._ph}""",
                (f"%{escapar_like(self._normalizar_termo(q))}%", f"%{escapar_like(q)}%", limite),
            )
            emails = self._emails_por_ra([l["ra"] for l in linhas])
            return [self._montar_aluno(l, emails.get(l["ra"], [])) for l in linhas]
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao buscar alunos: {e}")
            raise SchoolSqlIndisponivel("banco da escola indisponível na busca de alunos") from e

    def get_guardian_emails_for_ra(self, ra: str) -> list:
        """Retorna os e-mails dos responsáveis vinculados a um RA — usada para notificar após liberar a saída."""
        try:
            return self._emails_por_ra([ra]).get(ra, [])
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao buscar responsáveis: {e}")
            raise SchoolSqlIndisponivel(
                "banco da escola indisponível ao buscar responsáveis") from e

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
                    JOIN alunos a ON a.ra = v.ra AND {self._expr_ativo('a.ativo')}
                    WHERE LOWER(r.email) = {self._ph} AND {self._expr_ativo('r.ativo')}
                    LIMIT 1""",
                (email.strip().lower(),),
            )
            return bool(linhas)
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao validar responsável: {e}")
            raise SchoolSqlIndisponivel(
                "banco da escola indisponível ao validar responsável") from e


    def verificar_saude(self) -> dict:
        """Conversa de verdade com o banco da escola. Devolve um diagnóstico, nunca levanta.

        Existe porque a checagem que havia era cosmética: o boot só conferia se a classe era o
        mock. O `__init__` não valida nada, e o RuntimeError por SCHOOL_SQL_HOST/DATABASE ausentes
        só aparecia dentro de `_conectar()`, ou seja, no meio de uma requisição — capturado pelo
        except de cada método e transformado em lista vazia. Como render.yaml marca essas
        variáveis como `sync: false` (preenchidas à mão no painel), esquecer uma fazia o app subir
        saudável, health check verde, e toda tela mostrar "nenhum aluno encontrado".

        São dois passos, e o segundo é o que importa. `SELECT 1` prova que dá para conectar e
        nada mais — passa com o schema errado. `list_students(limite=1)` é uma consulta real:
        prova que as tabelas existem, que os nomes de coluna batem e que a comparação de `ativo`
        funciona contra o tipo que a instituição escolheu (ver A12).
        """
        diagnostico = {'modo': 'sql', 'engine': self._engine, 'ok': False,
                       'detalhe': None, 'unaccent': None}
        try:
            self._consultar("SELECT 1 AS ok")
        except Exception as e:
            diagnostico['detalhe'] = f"não foi possível conectar: {e}"
            return diagnostico
        try:
            self.list_students(limite=1)
        except Exception as e:
            diagnostico['detalhe'] = f"conectou, mas a consulta de alunos falhou: {e}"
            return diagnostico
        diagnostico['ok'] = True
        diagnostico['unaccent'] = self._tem_unaccent()
        return diagnostico


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

    def verificar_saude(self) -> dict:
        """O mock não tem banco atrás; o que ele precisa relatar é que É um mock — quem lê o
        health check tem de conseguir ver que os alunos são dados de teste."""
        return {'modo': 'mock', 'engine': None, 'ok': True,
                'detalhe': 'dados de teste (SCHOOL_SQL_MOCK=true)', 'unaccent': None}


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
