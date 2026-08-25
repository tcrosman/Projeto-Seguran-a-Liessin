# -*- coding: utf-8 -*-
"""Cria o banco SQL provisório da escola e popula com dados de exemplo.

    python escola_provisoria/seed.py            # cria escola_provisoria/escola.db
    python escola_provisoria/seed.py --force    # recria por cima de um banco existente

Os dados cobrem de propósito os casos-limite que o sistema precisa saber tratar:
aluno sem responsável, responsável com dois filhos, responsável sem vínculo ativo,
aluno desligado, e nomes com acento/caixa variada para testar a busca.
"""
import argparse
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(BASE_DIR, "schema.sql")
DB_PADRAO = os.path.join(BASE_DIR, "escola.db")

# (ra, nome, turma, serie, foto_url, ativo)
ALUNOS = [
    # Os três do mock original — mantidos para os fluxos de teste já conhecidos continuarem valendo
    ("2024001", "Ana Beatriz Souza",      "A", "6º ano EF", None, 1),
    ("2024002", "Carlos Eduardo Lima",    "B", "8º ano EF", None, 1),
    ("2024003", "Theo Crosman",           "A", "3º ano EM", None, 1),

    ("2024004", "Mariana Gonçalves",      "A", "Berçário 1", None, 1),
    ("2024005", "Pedro Henrique Alves",   "B", "Pré 2",      None, 1),
    ("2024006", "JÚLIA MENDES",           "C", "1º ano EF",  None, 1),  # caixa alta: testa busca
    ("2024007", "Lucas Ferreira Rocha",   "A", "4º ano EF",  None, 1),
    ("2024008", "Beatriz Camões",         "D", "7º ano EF",  None, 1),  # acento: testa busca
    ("2024009", "Rafael Nunes",           "B", "9º ano EF",  None, 1),
    ("2024010", "Sofia Almeida",          "A", "1º ano EM",  None, 1),  # irmã de 2024011
    ("2024011", "Miguel Almeida",         "C", "2º ano EM",  None, 1),  # irmão de 2024010
    ("2024012", "Isabela Martins",        "B", "5º ano EF",  None, 1),  # SEM responsável vinculado
    ("2024013", "Gabriel Torres",         "D", "3º ano EF",  None, 0),  # DESLIGADO: não pode aparecer
]

# (email, nome, ativo)
RESPONSAVEIS = [
    ("pai@teste.com",           "Roberto Souza",      1),
    ("mae@teste.com",           "Fernanda Souza",     1),
    ("responsavel@teste.com",   "Marcos Lima",        1),
    ("theocrosman@gmail.com",   "Responsável Crosman", 1),
    ("claudia.almeida@teste.com", "Cláudia Almeida",  1),  # tem DOIS filhos
    ("jose.mendes@teste.com",   "José Mendes",        1),
    ("patricia.rocha@teste.com", "Patrícia Rocha",    1),
    ("sem.vinculo@teste.com",   "Antiga Responsável", 1),  # ativo, mas SEM vínculo -> deve ser barrado
    ("bloqueado@teste.com",     "Responsável Inativo", 0),  # inativo -> deve ser barrado
    ("pai.desligado@teste.com", "Pai do Desligado",   1),  # só vinculado ao aluno inativo -> barrado
]

# (ra, email, parentesco)
VINCULOS = [
    ("2024001", "pai@teste.com",             "pai"),
    ("2024001", "mae@teste.com",             "mãe"),   # aluno com DOIS responsáveis
    ("2024002", "responsavel@teste.com",     "pai"),
    ("2024003", "theocrosman@gmail.com",     "mãe"),
    ("2024004", "jose.mendes@teste.com",     "pai"),
    ("2024005", "patricia.rocha@teste.com",  "mãe"),
    ("2024006", "jose.mendes@teste.com",     "pai"),
    ("2024007", "patricia.rocha@teste.com",  "mãe"),
    ("2024008", "pai@teste.com",             "avô"),
    ("2024009", "responsavel@teste.com",     "pai"),
    ("2024010", "claudia.almeida@teste.com", "mãe"),   # dois filhos: 2024010 e 2024011
    ("2024011", "claudia.almeida@teste.com", "mãe"),
    # 2024012 (Isabela) fica de propósito SEM vínculo nenhum
    ("2024013", "pai.desligado@teste.com",   "pai"),   # único vínculo é com aluno desligado
    ("2024001", "bloqueado@teste.com",       "tio"),   # vínculo existe, mas responsável inativo
]


def criar(db_path, force):
    if os.path.exists(db_path):
        if not force:
            print(f"ERRO: {db_path} já existe. Use --force para recriar.", file=sys.stderr)
            return 1
        os.remove(db_path)

    with open(SCHEMA, encoding="utf-8") as f:
        ddl = f.read()

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(ddl)
        conn.executemany("INSERT INTO alunos (ra, nome, turma, serie, foto_url, ativo) VALUES (?,?,?,?,?,?)", ALUNOS)
        conn.executemany("INSERT INTO responsaveis (email, nome, ativo) VALUES (?,?,?)", RESPONSAVEIS)
        conn.executemany("INSERT INTO vinculos (ra, email, parentesco) VALUES (?,?,?)", VINCULOS)
        conn.commit()
    finally:
        conn.close()

    ativos = sum(1 for a in ALUNOS if a[5])
    print(f"Banco criado: {db_path}")
    print(f"  {len(ALUNOS)} alunos ({ativos} ativos, {len(ALUNOS) - ativos} desligado)")
    print(f"  {len(RESPONSAVEIS)} responsáveis, {len(VINCULOS)} vínculos")
    print()
    print("Aponte o app para ele com:")
    print("  SCHOOL_SQL_MOCK=false")
    print("  SCHOOL_SQL_ENGINE=sqlite")
    print(f"  SCHOOL_SQL_DATABASE={db_path}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Cria o banco provisório da escola com dados de exemplo.")
    p.add_argument("--db", default=DB_PADRAO, help=f"caminho do arquivo (padrão: {DB_PADRAO})")
    p.add_argument("--force", action="store_true", help="recria por cima de um banco existente")
    args = p.parse_args()
    sys.exit(criar(args.db, args.force))
