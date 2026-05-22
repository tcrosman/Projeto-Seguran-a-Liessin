import sqlite3

print("Criando banco de dados S2E...")

conexao = sqlite3.connect("escola.db")
cursor = conexao.cursor()

# Tabela alunos
cursor.execute("""
CREATE TABLE IF NOT EXISTS alunos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    turma TEXT NOT NULL,
    serie TEXT NOT NULL,
    saida_seg TEXT,
    saida_ter TEXT,
    saida_qua TEXT,
    saida_qui TEXT,
    saida_sex TEXT,
    responsaveis TEXT,
    foto_path TEXT,
    telefone TEXT,
    email_responsavel TEXT,
    data_nascimento TEXT,
    alergias TEXT,
    observacoes TEXT
)
""")

# Tabela saidas
cursor.execute("""
CREATE TABLE IF NOT EXISTS saidas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aluno INTEGER NOT NULL,
    data_saida TEXT NOT NULL,
    horario TEXT NOT NULL,
    motivo TEXT NOT NULL,
    veiculo TEXT,
    placa TEXT,
    responsavel TEXT,
    status TEXT NOT NULL,
    responsavel_escola TEXT,
    tipo_saida TEXT,
    acompanhante TEXT,
    documento_path TEXT,
    usuario_autorizou INTEGER
)
""")

# Tabela usuarios
cursor.execute("""
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'basico',
    email TEXT
)
""")

# Tabela reset_tokens
cursor.execute("""
CREATE TABLE IF NOT EXISTS reset_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token TEXT UNIQUE NOT NULL,
    expires_at TEXT NOT NULL
)
""")

# Tabela horarios_padrao
cursor.execute("""
CREATE TABLE IF NOT EXISTS horarios_padrao (
    serie TEXT PRIMARY KEY,
    saida_seg TEXT,
    saida_ter TEXT,
    saida_qua TEXT,
    saida_qui TEXT,
    saida_sex TEXT
)
""")

# Tabela logs_alunos (auditoria)
cursor.execute("""
CREATE TABLE IF NOT EXISTS logs_alunos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aluno_id INTEGER NOT NULL,
    usuario_id INTEGER NOT NULL,
    acao TEXT NOT NULL,
    dados_antigos TEXT,
    dados_novos TEXT,
    data_hora TEXT NOT NULL
)
""")

# Tabela consentimentos (LGPD)
cursor.execute("""
CREATE TABLE IF NOT EXISTS consentimentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aluno_id INTEGER NOT NULL,
    aceitou BOOLEAN NOT NULL,
    ip TEXT NOT NULL,
    data_hora TEXT NOT NULL
)
""")

# Usuário inicial (apenas se não existir nenhum)
cursor.execute("SELECT COUNT(*) FROM usuarios")
count = cursor.fetchone()[0]
if count == 0:
    cursor.execute("""
        INSERT INTO usuarios (username, password, role, email) 
        VALUES ('admin', 'admin123', 'admin', 'admin@s2e.com')
    """)
    print("Usuário inicial 'admin' criado (senha: admin123)")

conexao.commit()
conexao.close()

print("Banco de dados criado com sucesso!")