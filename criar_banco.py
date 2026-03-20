import sqlite3

print("Criando/atualizando banco de dados...")

conexao = sqlite3.connect("escola.db")
cursor = conexao.cursor()

# =========================
# TABELA ALUNOS
# =========================
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

# =========================
# TABELA SAIDAS
# =========================
cursor.execute("""
CREATE TABLE IF NOT EXISTS saidas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aluno INTEGER NOT NULL,
    data_saida TEXT,
    horario TEXT NOT NULL,
    motivo TEXT NOT NULL,
    veiculo TEXT NOT NULL,
    placa TEXT,
    responsavel TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pendente'
)
""")

conexao.commit()

# =========================
# GARANTIR COLUNAS (caso banco antigo já exista)
# =========================

def adicionar_coluna_se_nao_existir(tabela, coluna, tipo):
    try:
        cursor.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")
        print(f"Coluna {coluna} adicionada em {tabela}")
    except:
        pass  # já existe

# alunos
adicionar_coluna_se_nao_existir("alunos", "saida_seg", "TEXT")
adicionar_coluna_se_nao_existir("alunos", "saida_ter", "TEXT")
adicionar_coluna_se_nao_existir("alunos", "saida_qua", "TEXT")
adicionar_coluna_se_nao_existir("alunos", "saida_qui", "TEXT")
adicionar_coluna_se_nao_existir("alunos", "saida_sex", "TEXT")

# saidas
adicionar_coluna_se_nao_existir("saidas", "data_saida", "TEXT")
adicionar_coluna_se_nao_existir("saidas", "status", "TEXT DEFAULT 'pendente'")

conexao.commit()
conexao.close()

print("Banco atualizado com sucesso!")