# SecureEdu (S2E)

Sistema de controle de saída de alunos. Substitui o caderno da portaria por um
registro auditável de quem autorizou a saída de qual aluno, quando e com quem.

Duas frentes no mesmo servidor:

- **Equipe da escola** — portaria registra a saída, coordenação acompanha, administração
  gerencia usuários e responsáveis. Papéis: `admin`, `basico`, `vigia`.
- **Portal dos pais** (`/pais`) — responsável solicita a saída antecipadamente, acompanha
  o histórico do filho. Entrada protegida por segundo fator enviado por e-mail.

O sistema lida com dados de menores de idade, o que orienta boa parte das decisões de
projeto: retenção com prazo, trilha de auditoria separada, minimização de dados
(o cadastro do aluno **não** é copiado para cá — é lido ao vivo do banco da escola e
mantido em cache por 5 minutos) e falha fechada quando a origem dos dados está fora.

---

## Stack

Python + Flask, com Jinja2 renderizado no servidor. **Não há build de frontend** — sem
Node, sem bundler, sem pipeline de assets. Persistência em PostgreSQL, via SQL puro
(sem ORM), com migrações idempotentes aplicadas no boot.

Dependências: `flask`, `python-dotenv`, `psycopg2-binary`, `gunicorn`, `flask-wtf`,
`flask-talisman`. Todas com piso e teto de versão, de propósito.

---

## Estrutura

```
S2E_postgreSQL/
├── escola_provisoria/     base SQLite de alunos para desenvolvimento.
│                          schema.sql é a especificação do que a escola precisa expor.
├── manutencao.py          rotina de retenção/expiração avulsa, para cron externo
├── run_migrations.py      aplica só as migrações
├── teste_banco.py         testa a conexão com o Postgres
└── s2e-api/               <-- a aplicação
    ├── run.py             ponto de entrada (run:app)
    ├── setup_db.py        cria o schema e o primeiro administrador
    ├── .env.example       todas as variáveis, comentadas
    ├── deploy/            infraestrutura de produção — ver DEPLOY.md
    ├── tests/             mais de 400 testes, sem precisar de banco real
    └── app/
        ├── api/           web.py (equipe), pais.py (portal), middleware.py (RBAC)
        ├── core/          banco, migrações, e-mail, rate limit, senhas, auditoria
        ├── services/      leitura do banco de alunos da escola
        ├── templates/     31 telas Jinja2
        └── static/
```

> O `requirements.txt` da raiz de `S2E_postgreSQL/` é legado e conflita com o do
> `s2e-api/`. Use sempre o de `s2e-api/`.

---

## Rodando localmente

Precisa de Python 3.9+ e um PostgreSQL acessível.

```bash
cd S2E_postgreSQL/s2e-api
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

cp .env.example .env
```

No `.env`, o mínimo para subir:

```
SECRET_KEY=<gere com: python3 -c "import os; print(os.urandom(32).hex())">
DATABASE_URL=postgresql://usuario:senha@host:5432/banco
SCHOOL_SQL_MOCK=true
INITIAL_ADMIN_PASSWORD=<senha forte: 8+ chars, maiúscula, número, especial>
```

Prepare o banco. Num banco **vazio** o `--reset` é obrigatório: as tabelas base só são
criadas por ele, e sem elas a aplicação não sobe.

```bash
CONFIRM_RESET_DATABASE=APAGAR-TODOS-OS-DADOS .venv/bin/python setup_db.py --reset
```

Nas vezes seguintes, apenas `.venv/bin/python setup_db.py` — o `--reset` **apaga tudo**.

```bash
.venv/bin/python run.py     # http://127.0.0.1:8002 — login: admin
```

### Com dados de aluno mais realistas

`SCHOOL_SQL_MOCK=true` serve três alunos fixos. Para os 13 alunos de exemplo, com casos
de borda (nome com acento, responsável compartilhado, aluno sem foto):

```bash
cd ..                                        # S2E_postgreSQL/
s2e-api/.venv/bin/python escola_provisoria/seed.py
```

E no `.env`:

```
SCHOOL_SQL_MOCK=false
SCHOOL_SQL_ENGINE=sqlite
SCHOOL_SQL_DATABASE=../escola_provisoria/escola.db
```

Detalhes em [escola_provisoria/README.md](escola_provisoria/README.md).

### Testes

```bash
cd s2e-api && .venv/bin/python -m pytest
```

Não precisam de banco: as conexões são dubladas em `tests/conftest.py`.

---

## Configuração

Todas as variáveis estão comentadas em [s2e-api/.env.example](s2e-api/.env.example).
As que a aplicação **se recusa a subir sem**:

| Variável | Por quê |
|---|---|
| `SECRET_KEY` | mínimo 32 caracteres; assina os cookies de sessão |
| `DATABASE_URL` | conexão com o Postgres |
| `SCHOOL_SQL_MOCK` | escolha explícita da origem dos dados de aluno |

A última não tem padrão de propósito: quando tinha (`true`), um deploy sem configurar
nada subia sem erro nenhum servindo três alunos de teste como se fossem o cadastro real
da escola. Configuração ausente que não quebra nada e entrega dado falso com cara de
dado verdadeiro é o pior tipo de falha.

---

## Produção

Ver **[s2e-api/deploy/DEPLOY.md](s2e-api/deploy/DEPLOY.md)** — instalação num VPS do
zero, operação, backup, restauração e diagnóstico.

### Checklist de implantação

- Use infraestrutura que permaneça ativa, sem hibernação de plano gratuito, e com
  capacidade compatível com os picos da portaria.
- Defina `SCHOOL_SQL_MOCK=false` e configure explicitamente o diretório escolar real.
- Ative `TRUST_PROXY=true` somente atrás do nginx configurado pelo projeto.
- Gere uma `SECRET_KEY` exclusiva para cada instituição.
- Mantenha `MAILER_FALLBACK_LOG=false` para não registrar códigos 2FA nem links de
  redefinição quando o provedor de e-mail falhar.
- Use banco, credenciais, backups, domínio e remetente separados por instituição.

Resumo: nginx com TLS na frente, gunicorn em `127.0.0.1:8002` sob systemd, PostgreSQL
local. Um comando (`deploy/deploy.sh`) para atualizar, com reversão automática se o
healthcheck falhar.

## Homologação e expansão

- [Plano de homologação para diretor e TI](docs/PLANO_HOMOLOGACAO.md)
- [Arquitetura para múltiplas instituições](docs/ARQUITETURA_MULTIINSTITUICAO.md)
