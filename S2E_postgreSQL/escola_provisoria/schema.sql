-- Banco SQL provisório que faz o papel do sistema de cadastro da escola.
--
-- O S2E NÃO escreve aqui: só lê (ver app/services/school_sql_directory.py). Enquanto a
-- instituição não define o banco real, este schema serve de especificação do mínimo que
-- ela precisa expor para o sistema funcionar.

CREATE TABLE alunos (
    ra       TEXT PRIMARY KEY,
    nome     TEXT NOT NULL,
    turma    TEXT NOT NULL,                  -- A, B, C, D
    serie    TEXT NOT NULL,                  -- deve bater com Config.SERIES (app/config.py)
    foto_url TEXT,                           -- URL resolvível pelo navegador, ou NULL
    ativo    INTEGER NOT NULL DEFAULT 1      -- 0 = aluno desligado da escola
);

CREATE TABLE responsaveis (
    email TEXT PRIMARY KEY,
    nome  TEXT NOT NULL,
    ativo INTEGER NOT NULL DEFAULT 1         -- 0 = responsável sem acesso
);

-- Um responsável pode ter vários filhos e um aluno pode ter vários responsáveis.
CREATE TABLE vinculos (
    ra         TEXT NOT NULL REFERENCES alunos(ra),
    email      TEXT NOT NULL REFERENCES responsaveis(email),
    parentesco TEXT,                         -- informativo (mãe, pai, avó, ...)
    PRIMARY KEY (ra, email)
);

CREATE INDEX idx_alunos_nome ON alunos(nome);
CREATE INDEX idx_vinculos_email ON vinculos(email);

-- ---------------------------------------------------------------------------------------------
-- O que o banco REAL da escola precisa ter alem das tabelas acima
-- ---------------------------------------------------------------------------------------------
--
-- A busca de aluno (search_students) faz LIKE '%termo%' sobre o nome normalizado. Um indice
-- B-tree comum, como o idx_alunos_nome acima, NAO atende esse padrao: o curinga no inicio o
-- descarta, e cada busca vira varredura da tabela inteira. No autocomplete da portaria isso
-- acontece a cada tecla digitada, por porteiro, na hora de maior movimento.
--
-- No SQLite (este banco provisorio) nao ha o que fazer, e nem precisa: sao poucos alunos e o
-- banco e local. No PostgreSQL da instituicao, peca:
--
--   CREATE EXTENSION IF NOT EXISTS unaccent;   -- exigida por _expr_norm(); sem ela toda
--                                              -- consulta de busca falha
--   CREATE EXTENSION IF NOT EXISTS pg_trgm;
--
--   -- unaccent() nao e IMMUTABLE por padrao e por isso nao pode entrar direto num indice.
--   -- O caminho e um wrapper IMMUTABLE:
--   CREATE FUNCTION nome_normalizado(texto text) RETURNS text
--     AS $$ SELECT unaccent(lower($1)) $$ LANGUAGE sql IMMUTABLE;
--   CREATE INDEX idx_alunos_nome_busca ON alunos USING gin (nome_normalizado(nome) gin_trgm_ops);
--
-- E tambem:
--   * `ativo` como INTEGER 0/1 ou BOOLEAN — as duas formas sao aceitas (ver _cond_ativo em
--     app/services/school_sql_directory.py), mas diga qual e para a checagem de saude conferir;
--   * um usuario SOMENTE LEITURA para o S2E. O sistema nunca escreve neste banco.
