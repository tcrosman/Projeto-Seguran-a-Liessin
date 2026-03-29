import sqlite3

print("Criando banco de dados...")

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
    saida_sex TEXT
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
    veiculo TEXT NOT NULL,
    placa TEXT,
    responsavel TEXT NOT NULL,
    status TEXT NOT NULL
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

# Adicionar coluna email se não existir (para bancos já criados)
try:
    cursor.execute("ALTER TABLE usuarios ADD COLUMN email TEXT")
except:
    pass

# Tabela reset_tokens
cursor.execute("""
CREATE TABLE IF NOT EXISTS reset_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token TEXT UNIQUE NOT NULL,
    expires_at TEXT NOT NULL
)
""")

# Criar usuário inicial (User0) - apenas se não existir nenhum usuário
cursor.execute("SELECT COUNT(*) FROM usuarios")
count = cursor.fetchone()[0]

if count == 0:
    cursor.execute("INSERT INTO usuarios (username, password, role, email) VALUES ('User0', '000000', 'admin', 'sistema.liessin1@gmail.com')")
    print(" Usuário inicial User0 criado")
else:
    print(f" Já existem {count} usuários, não foi necessário criar User0")

conexao.commit()
conexao.close()

print("Banco de dados criado com sucesso!")