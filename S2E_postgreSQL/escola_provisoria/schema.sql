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
