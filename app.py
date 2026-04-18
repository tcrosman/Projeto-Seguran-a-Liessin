from flask import Flask, render_template, request, redirect, Response, session, send_from_directory
import sqlite3
from datetime import datetime, timedelta
import re
import csv
import secrets
import smtplib
from email.mime.text import MIMEText
from io import StringIO
from dotenv import load_dotenv
import os
from werkzeug.utils import secure_filename
import unicodedata
from pathlib import Path

load_dotenv()

def normalizar_nome_para_foto(nome):
    """
    Remove acentos, converte para minúsculas e elimina espaços extras.
    Exemplo: "João da Silva" -> "joao da silva"
    """
    if not nome:
        return ""
    # Normaliza para forma NFD (separa acentos)
    nfkd = unicodedata.normalize('NFKD', nome)
    # Remove os caracteres combinantes (acentos)
    sem_acento = "".join([c for c in nfkd if not unicodedata.combining(c)])
    # Converte para minúsculas e remove espaços extras nas pontas
    return sem_acento.lower().strip()


# Configuração de upload
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB

# Lista oficial de séries
SERIES = [
    'Berçário 1', 'Berçário 2',
    'Pré 1', 'Pré 2',
    '1º ano EF', '2º ano EF', '3º ano EF', '4º ano EF', '5º ano EF',
    '6º ano EF', '7º ano EF', '8º ano EF', '9º ano EF',
    '1º ano EM', '2º ano EM', '3º ano EM',
]

SERIE_ORDEM = {s: i for i, s in enumerate(SERIES)}

# Mapeamento de variações para o nome padrão
_NORMALIZE_SERIE = {
    # Berçário
    'bercario 1': 'Berçário 1', 'berçario 1': 'Berçário 1', 'berçário 1': 'Berçário 1',
    'bercario 2': 'Berçário 2', 'berçario 2': 'Berçário 2', 'berçário 2': 'Berçário 2',
    # Pré
    'pre 1': 'Pré 1', 'pré 1': 'Pré 1', 'pre i': 'Pré 1', 'pré i': 'Pré 1',
    'pre 2': 'Pré 2', 'pré 2': 'Pré 2', 'pre ii': 'Pré 2', 'pré ii': 'Pré 2',
    # EF
    '1 ano ef': '1º ano EF', '1º ano ef': '1º ano EF', '1° ano ef': '1º ano EF', '1 ano': '1º ano EF', '1º ano': '1º ano EF',
    '2 ano ef': '2º ano EF', '2º ano ef': '2º ano EF', '2° ano ef': '2º ano EF', '2 ano': '2º ano EF', '2º ano': '2º ano EF',
    '3 ano ef': '3º ano EF', '3º ano ef': '3º ano EF', '3° ano ef': '3º ano EF', '3 ano': '3º ano EF', '3º ano': '3º ano EF',
    '4 ano ef': '4º ano EF', '4º ano ef': '4º ano EF', '4° ano ef': '4º ano EF', '4 ano': '4º ano EF', '4º ano': '4º ano EF',
    '5 ano ef': '5º ano EF', '5º ano ef': '5º ano EF', '5° ano ef': '5º ano EF', '5 ano': '5º ano EF', '5º ano': '5º ano EF',
    '6 ano ef': '6º ano EF', '6º ano ef': '6º ano EF', '6° ano ef': '6º ano EF', '6 ano': '6º ano EF', '6º ano': '6º ano EF',
    '7 ano ef': '7º ano EF', '7º ano ef': '7º ano EF', '7° ano ef': '7º ano EF', '7 ano': '7º ano EF', '7º ano': '7º ano EF',
    '8 ano ef': '8º ano EF', '8º ano ef': '8º ano EF', '8° ano ef': '8º ano EF', '8 ano': '8º ano EF', '8º ano': '8º ano EF',
    '9 ano ef': '9º ano EF', '9º ano ef': '9º ano EF', '9° ano ef': '9º ano EF', '9 ano': '9º ano EF', '9º ano': '9º ano EF',
    # EM
    '1 em': '1º ano EM', '1º em': '1º ano EM', '1° em': '1º ano EM', '1 ano em': '1º ano EM', '1º ano em': '1º ano EM', '1º EM': '1º ano EM',
    '2 em': '2º ano EM', '2º em': '2º ano EM', '2° em': '2º ano EM', '2 ano em': '2º ano EM', '2º ano em': '2º ano EM', '2º EM': '2º ano EM',
    '3 em': '3º ano EM', '3º em': '3º ano EM', '3° em': '3º ano EM', '3 ano em': '3º ano EM', '3º ano em': '3º ano EM', '3º EM': '3º ano EM',
    # ensino fundamental / médio por extenso
    '1 ano ensino fundamental': '1º ano EF', '2 ano ensino fundamental': '2º ano EF',
    '3 ano ensino fundamental': '3º ano EF', '4 ano ensino fundamental': '4º ano EF',
    '5 ano ensino fundamental': '5º ano EF', '6 anno ensino fundamental': '6ºano EF',
    '7 ano ensino fundamental': '7º ano EF', '8 ano ensino fundamental': '8º ano EF',
    '9 ano ensino fundamental': '9º ano EF',
    '1 ano ensino medio': '1º ano EM', '2 ano ensino medio': '2º ano EM', '3 ano ensino medio': '3º ano EM',
    '1 ano ensino médio': '1º ano EM', '2 ano ensino médio': '2º ano EM', '3 ano ensino médio': '3º ano EM',
}

def normalizar_serie(valor):
    """Converte qualquer variação para o nome padrão. Retorna None se não reconhecer."""
    chave = str(valor).strip().lower()
    if valor.strip() in SERIES:
        return valor.strip()
    return _NORMALIZE_SERIE.get(chave)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def allowed_zip(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() == 'zip'

app = Flask(__name__)
app.secret_key = '123'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

# Criar diretórios de upload
os.makedirs(os.path.join(UPLOAD_FOLDER, 'fotos'), exist_ok=True)
os.makedirs(os.path.join(UPLOAD_FOLDER, 'documentos'), exist_ok=True)

def migrar_banco():
    """Cria tabelas e colunas que ainda não existam no banco (migrações seguras)."""
    conn = sqlite3.connect("escola.db")
    # Criar tabelas ausentes
    conn.execute("""
        CREATE TABLE IF NOT EXISTS horarios_padrao (
            serie TEXT PRIMARY KEY,
            saida_seg TEXT,
            saida_ter TEXT,
            saida_qua TEXT,
            saida_qui TEXT,
            saida_sex TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            token TEXT,
            expires_at TEXT
        )
    """)
    # Adicionar colunas ausentes
    alter_migrations = [
        "ALTER TABLE alunos ADD COLUMN responsaveis TEXT",
        "ALTER TABLE alunos ADD COLUMN foto_path TEXT",
        "ALTER TABLE saidas ADD COLUMN responsavel_escola TEXT",
        "ALTER TABLE saidas ADD COLUMN tipo_saida TEXT",
        "ALTER TABLE saidas ADD COLUMN acompanhante TEXT",
        "ALTER TABLE saidas ADD COLUMN documento_path TEXT",
    ]
    for sql in alter_migrations:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # coluna já existe
    conn.commit()
    conn.close()

migrar_banco()

def conectar():
    conn = sqlite3.connect("escola.db", timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def log_operacao(usuario, acao, detalhes):
    try:
        with open("log_sistema.txt", "a", encoding="utf-8") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] {usuario} - {acao}: {detalhes}\n")
    except:
        pass

def enviar_email_reset(destinatario, token):
    link = f"http://localhost:8002/resetar_senha/{token}"
    corpo = f"""Olá,

Recebemos uma solicitação para redefinir sua senha no Sistema de Saídas da Escola.

Clique no link abaixo para criar uma nova senha (válido por 1 hora):

{link}

Se você não solicitou isso, ignore este email.
"""
    msg = MIMEText(corpo, "plain", "utf-8")
    msg["Subject"] = "Redefinição de Senha - Sistema Escola"
    msg["From"] = os.getenv("GMAIL_USER")
    msg["To"] = destinatario

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(os.getenv("GMAIL_USER"), os.getenv("GMAIL_PASSWORD"))
        smtp.send_message(msg)

# ==================== LOGIN ====================

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        conn = conectar()
        user = conn.execute(
            "SELECT id, role, username FROM usuarios WHERE username=? AND password=?",
            (request.form["u"], request.form["s"])
        ).fetchone()
        conn.close()
        if user:
            session['user_id'] = user[0]
            session['role'] = user[1]
            session['username'] = user[2]
            return redirect("/inicio")
    return render_template("login.html")

@app.route("/inicio")
def inicio():
    if 'role' not in session:
        return redirect("/")
    return render_template("index.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/esqueci_senha", methods=["GET", "POST"])
def esqueci_senha():
    mensagem = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        conn = conectar()
        user = conn.execute("SELECT id FROM usuarios WHERE LOWER(email) = ?", (email,)).fetchone()
        if user:
            token = secrets.token_urlsafe(32)
            expires_at = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("DELETE FROM reset_tokens WHERE user_id = ?", (user[0],))
            conn.execute("INSERT INTO reset_tokens (user_id, token, expires_at) VALUES (?, ?, ?)",
                        (user[0], token, expires_at))
            conn.commit()
            try:
                enviar_email_reset(email, token)
            except Exception as e:
                with open("log_sistema.txt", "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.now()}] ERRO EMAIL: {str(e)}\n")
        conn.close()
        mensagem = "Se este email estiver cadastrado, você receberá um link em breve."
    return render_template("esqueci_senha.html", mensagem=mensagem)

@app.route("/resetar_senha/<token>", methods=["GET", "POST"])
def resetar_senha(token):
    conn = conectar()
    registro = conn.execute(
        "SELECT user_id, expires_at FROM reset_tokens WHERE token = ?", (token,)
    ).fetchone()

    if not registro:
        conn.close()
        return render_template("resetar_senha.html", erro="Link inválido ou já utilizado.", token=None)

    if datetime.now() > datetime.strptime(registro[1], "%Y-%m-%d %H:%M:%S"):
        conn.execute("DELETE FROM reset_tokens WHERE token = ?", (token,))
        conn.commit()
        conn.close()
        return render_template("resetar_senha.html", erro="Link expirado. Solicite um novo.", token=None)

    erro = ""
    if request.method == "POST":
        nova = request.form.get("senha", "")
        confirma = request.form.get("confirma", "")
        if len(nova) < 6:
            erro = "A senha deve ter no mínimo 6 caracteres."
        elif nova != confirma:
            erro = "As senhas não coincidem."
        else:
            conn.execute("UPDATE usuarios SET password = ? WHERE id = ?", (nova, registro[0]))
            conn.execute("DELETE FROM reset_tokens WHERE token = ?", (token,))
            conn.commit()
            conn.close()
            return redirect("/?resetado=1")

    conn.close()
    return render_template("resetar_senha.html", erro=erro, token=token)

@app.route("/novo", methods=["GET", "POST"])
def novo():
    if session.get('role') != 'admin':
        return "Acesso negado"

    if request.method == "POST":
        conn = conectar()
        try:
            conn.execute("INSERT INTO usuarios (username, password, role, email) VALUES (?,?,?,?)",
                        (request.form["u"], request.form["s"], request.form["r"], request.form.get("e", "").strip().lower()))
            conn.commit()
            conn.close()
            return redirect("/novo?sucesso=1")
        except:
            conn.close()
            return redirect("/novo?erro=1")

    conn = conectar()
    usuarios = conn.execute("SELECT id, username, role, email FROM usuarios ORDER BY id").fetchall()
    conn.close()
    return render_template("novo.html", usuarios=usuarios)

@app.route("/deletar_usuario/<int:id_usuario>")
def deletar_usuario(id_usuario):
    if session.get('role') != 'admin':
        return "Acesso negado"

    conn = conectar()

    if id_usuario == session.get('user_id'):
        conn.close()
        return "Você não pode deletar seu próprio usuário! <a href='/novo'>Voltar</a>"

    user = conn.execute("SELECT username, role FROM usuarios WHERE id = ?", (id_usuario,)).fetchone()
    if user and user[1] == 'admin':
        admins = conn.execute("SELECT COUNT(*) FROM usuarios WHERE role = 'admin' AND id != ?", (id_usuario,)).fetchone()[0]
        if admins == 0:
            conn.close()
            return "Não pode deletar o último administrador! <a href='/novo'>Voltar</a>"

    conn.execute("DELETE FROM usuarios WHERE id = ?", (id_usuario,))
    conn.commit()
    conn.close()
    return redirect("/novo")

# ==================== ROTAS GERAIS ====================

@app.route("/encontrar_aluno", methods=["GET"])
def encontrar_aluno():
    if 'role' not in session:
        return redirect("/")

    conexao = conectar()
    cursor = conexao.cursor()
    busca = request.args.get("busca")

    if busca:
        cursor.execute("SELECT id, nome, turma, serie, foto_path FROM alunos WHERE nome LIKE ? COLLATE NOCASE", ('%' + busca + '%',))
    else:
        cursor.execute("SELECT id, nome, turma, serie, foto_path FROM alunos")

    alunos = cursor.fetchall()
    conexao.close()

    alunos = sorted(alunos, key=lambda x: (SERIE_ORDEM.get(str(x[3]).strip(), 99), x[2], x[1]))

    grupos = {}
    for aluno in alunos:
        chave = (aluno[3], aluno[2])
        grupos.setdefault(chave, []).append(aluno)
    alunos_agrupados = [(serie, turma, lista) for (serie, turma), lista in grupos.items()]

    return render_template("encontrar_aluno.html", alunos_agrupados=alunos_agrupados, busca=busca)

@app.route("/registrar_saida", methods=["GET", "POST"])
def registrar_saida():
    if 'role' not in session:
        return redirect("/")

    aluno_pre_selecionado = request.args.get("aluno_id")
    mensagem_erro = ""

    if request.method == "POST":
        conexao = conectar()
        cursor = conexao.cursor()

        data_saida = request.form.get("data_saida")
        id_aluno = request.form.get("aluno_id")
        horario = request.form.get("horario")
        motivo = request.form.get("motivo")
        responsavel = ''
        responsavel_escola = request.form.get("responsavel_escola")
        tipo_saida = request.form.get("tipo_saida")
        acompanhante = request.form.get("acompanhante") if tipo_saida == 'acompanhado' else None

        if not data_saida:
            data_saida = datetime.now().strftime("%Y-%m-%d")

        try:
            if not horario or not motivo or not responsavel_escola or not tipo_saida:
                mensagem_erro = "Todos os campos são obrigatórios!"
            else:
                cursor.execute("SELECT id FROM alunos WHERE id = ?", (id_aluno,))
                if not cursor.fetchone():
                    mensagem_erro = "Aluno não encontrado!"
                else:
                    cursor.execute("""
                        SELECT COUNT(*) FROM saidas
                        WHERE aluno = ? AND data_saida = ? AND status = 'pendente'
                    """, (id_aluno, data_saida))
                    if cursor.fetchone()[0] > 0:
                        mensagem_erro = "Este aluno já tem uma saída pendente para hoje!"
                    else:
                        # Processar upload de documento
                        documento_path = None
                        if 'documento' in request.files:
                            file = request.files['documento']
                            if file and file.filename != '' and allowed_file(file.filename):
                                filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
                                file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'documentos', filename))
                                documento_path = os.path.join('documentos', filename)

                        cursor.execute("""
                            INSERT INTO saidas
                            (aluno, data_saida, horario, motivo, veiculo, placa, responsavel, responsavel_escola, tipo_saida, acompanhante, documento_path, status)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (id_aluno, data_saida, horario, motivo, '', '', responsavel, responsavel_escola, tipo_saida, acompanhante, documento_path, "pendente"))
                        conexao.commit()
                        log_operacao(session.get('username', 'usuario'), "REGISTROU SAÍDA", f"Aluno ID: {id_aluno}")
                        return redirect("/saidas")
        finally:
            conexao.close()

    conexao = conectar()
    cursor = conexao.cursor()
    cursor.execute("SELECT id, nome, turma, serie FROM alunos")
    alunos = cursor.fetchall()
    conexao.close()

    return render_template("registrar_saida.html", alunos=alunos, erro=mensagem_erro, aluno_selecionado=aluno_pre_selecionado)

@app.route("/saidas")
def lista_saidas():
    if 'role' not in session:
        return redirect("/")

    conexao = conectar()
    conexao.execute("""
        DELETE FROM saidas
        WHERE status = 'concluida'
        AND data_saida < date('now', '-30 days')
    """)
    conexao.commit()

    data_selecionada = request.args.get("data", datetime.now().strftime("%Y-%m-%d"))
    data_min = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    cursor = conexao.cursor()

    cursor.execute("""
        SELECT s.id, a.nome, s.horario, s.motivo, s.responsavel, s.responsavel_escola,
               s.tipo_saida, s.acompanhante, s.documento_path, s.status,
               a.serie, a.turma, a.foto_path
        FROM saidas s
        LEFT JOIN alunos a ON s.aluno = a.id
        WHERE s.status = 'pendente' AND s.data_saida = ?
        ORDER BY s.horario ASC
    """, (data_selecionada,))
    pendentes = cursor.fetchall()

    cursor.execute("""
        SELECT s.id, a.nome, s.horario, s.motivo, s.responsavel, s.responsavel_escola,
               s.tipo_saida, s.acompanhante, s.documento_path, s.status,
               a.serie, a.turma, a.foto_path
        FROM saidas s
        LEFT JOIN alunos a ON s.aluno = a.id
        WHERE s.status = 'concluida' AND s.data_saida = ?
        ORDER BY s.horario DESC
    """, (data_selecionada,))
    concluidas = cursor.fetchall()
    conexao.close()

    def to_dict(rows):
        return [{"id": s[0], "aluno": s[1], "horario": s[2], "motivo": s[3],
                 "responsavel": s[4], "responsavel_escola": s[5], "tipo_saida": s[6],
                 "acompanhante": s[7], "documento_path": s[8], "status": s[9],
                 "serie": s[10], "turma": s[11], "foto_path": s[12]} for s in rows]

    return render_template("lista_saidas.html",
                           pendentes=to_dict(pendentes),
                           concluidas=to_dict(concluidas),
                           data_selecionada=data_selecionada,
                           data_min=data_min,
                           data_max=datetime.now().strftime("%Y-%m-%d"))

@app.route("/concluir_saida/<int:id_saida>")
def concluir_saida(id_saida):
    if 'role' not in session:
        return redirect("/")

    conexao = conectar()
    cursor = conexao.cursor()
    cursor.execute("UPDATE saidas SET status = 'concluida' WHERE id = ?", (id_saida,))
    conexao.commit()
    conexao.close()
    log_operacao(session.get('username', 'usuario'), "CONCLUIU SAÍDA", f"ID Saída: {id_saida}")
    return redirect("/saidas")

@app.route("/historico/<int:id_aluno>")
def historico_aluno(id_aluno):
    if 'role' not in session:
        return redirect("/")

    conexao = conectar()
    cursor = conexao.cursor()

    cursor.execute("""
        SELECT id, nome, serie, turma,
               saida_seg, saida_ter, saida_qua, saida_qui, saida_sex, responsaveis, foto_path
        FROM alunos
        WHERE id = ?
    """, (id_aluno,))
    aluno_info = cursor.fetchone()

    if aluno_info:
        cursor.execute("""
            SELECT data_saida, horario, motivo, responsavel, responsavel_escola,
                   tipo_saida, acompanhante, documento_path, status
            FROM saidas
            WHERE aluno = ? AND status = 'concluida'
            ORDER BY data_saida DESC, horario DESC
        """, (id_aluno,))
        historico = cursor.fetchall()
    else:
        historico = []

    conexao.close()
    return render_template("historico_aluno.html", aluno=aluno_info, historico=historico)

@app.route("/exportar_saidas")
def exportar_saidas():
    if 'role' not in session:
        return redirect("/")

    conexao = conectar()
    cursor = conexao.cursor()

    cursor.execute("""
        SELECT a.nome, a.serie, a.turma, s.data_saida, s.horario,
               s.motivo, s.responsavel, s.responsavel_escola, s.tipo_saida, s.acompanhante, s.status
        FROM saidas s
        JOIN alunos a ON s.aluno = a.id
        ORDER BY s.data_saida DESC, s.horario DESC
    """)

    saidas = cursor.fetchall()
    conexao.close()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['Aluno', 'Série', 'Turma', 'Data', 'Horário',
                 'Motivo', 'Responsável', 'Responsável Escola', 'Tipo Saída', 'Acompanhante', 'Status'])
    cw.writerows(saidas)

    output = si.getvalue()
    si.close()

    log_operacao(session.get('username', 'usuario'), "EXPORTOU CSV", f"{len(saidas)} registros")

    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=saidas.csv"}
    )

# ==================== ROTAS ADMIN ====================

@app.route("/configurar_horarios", methods=["GET", "POST"])
def configurar_horarios():
    if session.get('role') != 'admin':
        return "Acesso negado"

    conn = conectar()
    if request.method == "POST":
        for serie in SERIES:
            seg = request.form.get(f"seg_{serie}", "")
            ter = request.form.get(f"ter_{serie}", "")
            qua = request.form.get(f"qua_{serie}", "")
            qui = request.form.get(f"qui_{serie}", "")
            sex = request.form.get(f"sex_{serie}", "")
            conn.execute("""
                INSERT OR REPLACE INTO horarios_padrao (serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (serie, seg, ter, qua, qui, sex))
        conn.commit()
        conn.close()
        return redirect("/configurar_horarios?sucesso=1")

    horarios = {}
    rows = conn.execute("SELECT serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex FROM horarios_padrao").fetchall()
    for row in rows:
        horarios[row[0]] = {
            'seg': row[1] or '',
            'ter': row[2] or '',
            'qua': row[3] or '',
            'qui': row[4] or '',
            'sex': row[5] or ''
        }
    conn.close()
    return render_template("configurar_horarios.html", series=SERIES, horarios=horarios)

@app.route("/cadastro_aluno", methods=["GET", "POST"])
def cadastro_aluno():
    if 'role' not in session:
        return redirect("/")
    mensagem_erro = ""

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        turma = request.form.get("turma", "").strip()
        serie = request.form.get("serie", "").strip()
        responsaveis = request.form.get("responsaveis", "").strip()
        saida_seg = request.form.get("saida_seg", "")
        saida_ter = request.form.get("saida_ter", "")
        saida_qua = request.form.get("saida_qua", "")
        saida_qui = request.form.get("saida_qui", "")
        saida_sex = request.form.get("saida_sex", "")

        if not nome or not turma or not serie:
            mensagem_erro = "Preencher todos os itens obrigatórios!"
        else:
            conexao = conectar()
            cursor = conexao.cursor()

            # Buscar horários padrão da série
            padrao = conexao.execute("SELECT saida_seg, saida_ter, saida_qua, saida_qui, saida_sex FROM horarios_padrao WHERE serie = ?", (serie,)).fetchone()
            if padrao:
                saida_seg = saida_seg or padrao[0]
                saida_ter = saida_ter or padrao[1]
                saida_qua = saida_qua or padrao[2]
                saida_qui = saida_qui or padrao[3]
                saida_sex = saida_sex or padrao[4]

            # Processar foto
            foto_path = None
            if 'foto' in request.files:
                file = request.files['foto']
                if file and file.filename != '' and allowed_file(file.filename):
                    filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'fotos', filename))
                    foto_path = os.path.join('fotos', filename)

            cursor.execute("""
                INSERT INTO alunos
                (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex, responsaveis, foto_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex, responsaveis, foto_path))
            conexao.commit()
            conexao.close()
            log_operacao(session.get('username', 'admin'), "CADASTROU ALUNO", f"Nome: {nome}, Turma: {turma}")
            return redirect("/cadastro_aluno")

    conexao = conectar()
    cursor = conexao.cursor()
    busca = request.args.get("busca")

    if busca:
        cursor.execute("SELECT id, nome, turma, serie, foto_path FROM alunos WHERE nome LIKE ? COLLATE NOCASE", ('%' + busca + '%',))
    else:
        cursor.execute("SELECT id, nome, turma, serie, foto_path FROM alunos")

    alunos = cursor.fetchall()
    conexao.close()

    alunos = sorted(alunos, key=lambda x: (SERIE_ORDEM.get(x[3], 99), x[2], x[1]))

    grupos = {}
    for aluno in alunos:
        chave = (aluno[3], aluno[2])
        grupos.setdefault(chave, []).append(aluno)
    alunos_agrupados = [(serie, turma, lista) for (serie, turma), lista in grupos.items()]

    return render_template("cadastro_aluno.html", alunos_agrupados=alunos_agrupados, erro=mensagem_erro, busca=busca, series=SERIES)

@app.route("/deletar_aluno/<int:id_aluno>")
def deletar_aluno(id_aluno):
    if session.get('role') != 'admin':
        return "Acesso negado"

    conexao = conectar()
    cursor = conexao.cursor()

    cursor.execute("SELECT COUNT(*) FROM saidas WHERE aluno = ? AND status = 'pendente'", (id_aluno,))
    pendentes = cursor.fetchone()[0]

    if pendentes > 0:
        conexao.close()
        return redirect("/encontrar_aluno?erro=aluno_possui_pendencias")

    try:
        cursor.execute("BEGIN")
        cursor.execute("DELETE FROM alunos WHERE id = ?", (id_aluno,))
        conexao.commit()
    except Exception as e:
        conexao.rollback()
        conexao.close()
        return f"Erro ao deletar aluno: {e}"

    conexao.close()
    log_operacao(session.get('username', 'admin'), "EXCLUIU ALUNO", f"ID: {id_aluno}")
    return redirect("/cadastro_aluno")

@app.route("/cadastro_massa", methods=["GET", "POST"])
def cadastro_massa():
    if session.get('role') != 'admin':
        return "Acesso negado"

    mensagem_erro = ""

    if request.method == "POST":
        # --- CADASTRO INDIVIDUAL (mantido) ---
        if "arquivo_excel" not in request.files or request.files["arquivo_excel"].filename == "":
            nome = request.form.get("nome", "").strip()
            turma = request.form.get("turma", "").strip()
            serie = request.form.get("serie", "").strip()
            responsaveis = request.form.get("responsaveis", "").strip()
            saida_seg = request.form.get("saida_seg", "").strip()
            saida_ter = request.form.get("saida_ter", "").strip()
            saida_qua = request.form.get("saida_qua", "").strip()
            saida_qui = request.form.get("saida_qui", "").strip()
            saida_sex = request.form.get("saida_sex", "").strip()

            if not nome or not turma or not serie:
                mensagem_erro = "Preencher nome, turma e série é obrigatório!"
            else:
                conexao = conectar()
                # Horários padrão da série
                padrao = conexao.execute(
                    "SELECT saida_seg, saida_ter, saida_qua, saida_qui, saida_sex FROM horarios_padrao WHERE serie = ?",
                    (serie,)
                ).fetchone()
                if padrao:
                    saida_seg = saida_seg or padrao[0]
                    saida_ter = saida_ter or padrao[1]
                    saida_qua = saida_qua or padrao[2]
                    saida_qui = saida_qui or padrao[3]
                    saida_sex = saida_sex or padrao[4]

                # Upload de foto (individual)
                foto_path = None
                if 'foto' in request.files:
                    file = request.files['foto']
                    if file and file.filename != '' and allowed_file(file.filename):
                        filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
                        file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'fotos', filename))
                        foto_path = os.path.join('fotos', filename)

                conexao.execute("""
                    INSERT INTO alunos
                    (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex, responsaveis, foto_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex, responsaveis, foto_path))
                conexao.commit()
                conexao.close()
                log_operacao(session.get('username', 'admin'), "CADASTROU ALUNO", f"Nome: {nome}, Turma: {turma}")
                return redirect("/cadastro_massa")

        # --- IMPORTAÇÃO EXCEL (com suporte a ZIP de fotos) ---
        if "arquivo_excel" in request.files:
            arquivo = request.files["arquivo_excel"]
            if arquivo.filename != "":
                try:
                    import pandas as pd
                    df = pd.read_excel(arquivo)
                    df.columns = df.columns.str.lower().str.strip()

                    erros = []
                    rows = []
                    for i, linha in df.iterrows():
                        serie_raw = str(linha["serie"]).strip()
                        serie = normalizar_serie(serie_raw)
                        if not serie:
                            erros.append(f"Linha {i + 2}: série não reconhecida → \"{serie_raw}\"")
                        else:
                            rows.append((
                                str(linha["nome"]).strip(),
                                str(linha["turma"]).strip(),
                                serie,
                                str(linha.get("saida_seg", "")).strip(),
                                str(linha.get("saida_ter", "")).strip(),
                                str(linha.get("saida_qua", "")).strip(),
                                str(linha.get("saida_qui", "")).strip(),
                                str(linha.get("saida_sex", "")).strip(),
                                str(linha.get("responsaveis", "")).strip()
                            ))

                    if erros:
                        series_str = ", ".join(SERIES)
                        mensagem_erro = (
                            "Série não reconhecida nas seguintes linhas:<br>"
                            + "<br>".join(erros)
                            + "<br><br><strong>Valores aceitos:</strong> " + series_str
                        )
                    else:
                        # Processar fotos da pasta selecionada (upload múltiplo)
                        foto_map = {}  # chave = nome normalizado, valor = bytes da imagem
                        fotos_files = request.files.getlist('fotos')
                        for foto_file in fotos_files:
                            if not foto_file or foto_file.filename == '':
                                continue
                            # Pega só o nome do arquivo (ignora caminho da pasta)
                            nome_arquivo = Path(foto_file.filename).name
                            ext = nome_arquivo.rsplit('.', 1)[-1].lower() if '.' in nome_arquivo else ''
                            if ext not in {'png', 'jpg', 'jpeg'}:
                                continue
                            nome_sem_ext = Path(nome_arquivo).stem
                            nome_normalizado = normalizar_nome_para_foto(nome_sem_ext)
                            foto_map[nome_normalizado] = foto_file.read()

                        conexao = conectar()
                        cursor = conexao.cursor()
                        alunos_inseridos = 0
                        for row in rows:
                            cursor.execute("""
                                INSERT INTO alunos
                                (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex, responsaveis)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, row)
                            aluno_id = cursor.lastrowid
                            nome_aluno = row[0]
                            nome_normalizado = normalizar_nome_para_foto(nome_aluno)
                            if nome_normalizado in foto_map:
                                # Define extensão padrão como .jpg (pode ajustar conforme necessário)
                                filename = secure_filename(f"{aluno_id}_{nome_aluno}.jpg")
                                filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'fotos', filename)
                                with open(filepath, 'wb') as f:
                                    f.write(foto_map[nome_normalizado])
                                foto_path = os.path.join('fotos', filename)
                                cursor.execute("UPDATE alunos SET foto_path = ? WHERE id = ?", (foto_path, aluno_id))
                            alunos_inseridos += 1

                        conexao.commit()
                        conexao.close()
                        log_operacao(session.get('username', 'admin'), "IMPORTOU EXCEL", f"{alunos_inseridos} alunos")
                        return redirect("/cadastro_aluno")

                except Exception as e:
                    # Em caso de erro, exibe mensagem genérica (log detalhado no terminal)
                    print("Erro na importação:", e)
                    mensagem_erro = "Erro ao processar o arquivo. Verifique se o Excel está no formato correto e se o ZIP contém apenas imagens."

    return render_template("cadastro_massa.html", erro=mensagem_erro, series=SERIES)

@app.route("/manual")
def manual():
    if 'role' not in session:
        return redirect("/")
    return render_template("manual.html")

@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    if 'role' not in session:
        return redirect("/")
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8002, debug=True)