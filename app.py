from flask import Flask, render_template, request, redirect, Response, session, send_from_directory, flash
import sqlite3
from datetime import datetime, timedelta
import secrets
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from io import StringIO
from dotenv import load_dotenv
import os
from werkzeug.utils import secure_filename
import unicodedata
from pathlib import Path
import json
import shutil

load_dotenv()

# Configurações
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}
MAX_CONTENT_LENGTH = 16 * 1024 * 1024

# Lista oficial de séries
SERIES = [
    'Berçário 1', 'Berçário 2',
    'Pré 1', 'Pré 2',
    '1º ano EF', '2º ano EF', '3º ano EF', '4º ano EF', '5º ano EF',
    '6º ano EF', '7º ano EF', '8º ano EF', '9º ano EF',
    '1º ano EM', '2º ano EM', '3º ano EM',
]
SERIE_ORDEM = {s: i for i, s in enumerate(SERIES)}

# Mapeamento de variações de série
_NORMALIZE_SERIE = {
    'bercario 1': 'Berçário 1', 'berçario 1': 'Berçário 1', 'berçário 1': 'Berçário 1',
    'bercario 2': 'Berçário 2', 'berçario 2': 'Berçário 2', 'berçário 2': 'Berçário 2',
    'pre 1': 'Pré 1', 'pré 1': 'Pré 1', 'pre i': 'Pré 1', 'pré i': 'Pré 1',
    'pre 2': 'Pré 2', 'pré 2': 'Pré 2', 'pre ii': 'Pré 2', 'pré ii': 'Pré 2',
    '1 ano ef': '1º ano EF', '1º ano ef': '1º ano EF', '1° ano ef': '1º ano EF',
    '2 ano ef': '2º ano EF', '2º ano ef': '2º ano EF', '2° ano ef': '2º ano EF',
    '3 ano ef': '3º ano EF', '3º ano ef': '3º ano EF', '3° ano ef': '3º ano EF',
    '4 ano ef': '4º ano EF', '4º ano ef': '4º ano EF', '4° ano ef': '4º ano EF',
    '5 ano ef': '5º ano EF', '5º ano ef': '5º ano EF', '5° ano ef': '5º ano EF',
    '6 ano ef': '6º ano EF', '6º ano ef': '6º ano EF', '6° ano ef': '6º ano EF',
    '7 ano ef': '7º ano EF', '7º ano ef': '7º ano EF', '7° ano ef': '7º ano EF',
    '8 ano ef': '8º ano EF', '8º ano ef': '8º ano EF', '8° ano ef': '8º ano EF',
    '9 ano ef': '9º ano EF', '9º ano ef': '9º ano EF', '9° ano ef': '9º ano EF',
    '1 em': '1º ano EM', '1º em': '1º ano EM', '1° em': '1º ano EM', '1 ano em': '1º ano EM',
    '2 em': '2º ano EM', '2º em': '2º ano EM', '2° em': '2º ano EM', '2 ano em': '2º ano EM',
    '3 em': '3º ano EM', '3º em': '3º ano EM', '3° em': '3º ano EM', '3 ano em': '3º ano EM',
    '1 ano ensino fundamental': '1º ano EF', '2 ano ensino fundamental': '2º ano EF',
    '3 ano ensino fundamental': '3º ano EF', '4 ano ensino fundamental': '4º ano EF',
    '5 ano ensino fundamental': '5º ano EF', '6 anno ensino fundamental': '6º ano EF',
    '7 ano ensino fundamental': '7º ano EF', '8 ano ensino fundamental': '8º ano EF',
    '9 ano ensino fundamental': '9º ano EF',
    '1 ano ensino medio': '1º ano EM', '2 ano ensino medio': '2º ano EM', '3 ano ensino medio': '3º ano EM',
    '1 ano ensino médio': '1º ano EM', '2 ano ensino médio': '2º ano EM', '3 ano ensino médio': '3º ano EM',
}

def normalizar_serie(valor):
    chave = str(valor).strip().lower()
    if valor.strip() in SERIES:
        return valor.strip()
    return _NORMALIZE_SERIE.get(chave)

def normalizar_nome_para_foto(nome):
    if not nome:
        return ""
    nfkd = unicodedata.normalize('NFKD', nome)
    sem_acento = "".join([c for c in nfkd if not unicodedata.combining(c)])
    return sem_acento.lower().strip()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'troque-esta-chave-em-producao')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

os.makedirs(os.path.join(UPLOAD_FOLDER, 'fotos'), exist_ok=True)
os.makedirs(os.path.join(UPLOAD_FOLDER, 'documentos'), exist_ok=True)
os.makedirs('backups', exist_ok=True)

def conectar():
    conn = sqlite3.connect("escola.db", timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def migrar_banco():
    conn = conectar()
    for col in ['telefone', 'email_responsavel', 'data_nascimento', 'alergias', 'observacoes']:
        try:
            conn.execute(f"ALTER TABLE alunos ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute("ALTER TABLE saidas ADD COLUMN usuario_autorizou INTEGER")
    except sqlite3.OperationalError:
        pass
    conn.execute("""
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
    conn.commit()
    conn.close()

migrar_banco()

def log_operacao(usuario, acao, detalhes):
    with open("log_sistema.txt", "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now()}] {usuario} - {acao}: {detalhes}\n")

def log_aluno(aluno_id, usuario_id, acao, dados_antigos=None, dados_novos=None):
    conn = conectar()
    conn.execute("""
        INSERT INTO logs_alunos (aluno_id, usuario_id, acao, dados_antigos, dados_novos, data_hora)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (aluno_id, usuario_id, acao,
          json.dumps(dados_antigos, default=str) if dados_antigos else None,
          json.dumps(dados_novos, default=str) if dados_novos else None,
          datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def enviar_email(destinatario, assunto, corpo_html):
    if not destinatario:
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = assunto
        msg['From'] = os.getenv("GMAIL_USER")
        msg['To'] = destinatario
        parte_html = MIMEText(corpo_html, 'html', 'utf-8')
        msg.attach(parte_html)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(os.getenv("GMAIL_USER"), os.getenv("GMAIL_PASSWORD"))
            smtp.send_message(msg)
        return True
    except Exception as e:
        print("Erro ao enviar e-mail:", e)
        return False

def backup_database():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"backups/escola_{timestamp}.db"
    shutil.copy2("escola.db", backup_path)
    return backup_path

# ------------------ LOGIN E AUTENTICAÇÃO ------------------
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
                link = f"http://localhost:8002/resetar_senha/{token}"
                corpo = f"Clique no link para redefinir sua senha: {link}"
                enviar_email(email, "Redefinição de senha", corpo)
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
            flash("Usuário criado com sucesso!", "success")
            return redirect("/novo")
        except:
            conn.close()
            flash("Erro! Usuário já existe ou dados inválidos.", "error")
            return redirect("/novo")
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
    flash("Usuário removido.", "success")
    return redirect("/novo")

# ------------------ ROTAS PRINCIPAIS ------------------
@app.route("/encontrar_aluno", methods=["GET"])
def encontrar_aluno():
    return redirect("/historico")

@app.route("/historico_avancado", methods=["GET", "POST"])
def historico_avancado():
    return redirect("/historico")

@app.route("/registrar_saida", methods=["GET", "POST"])
def registrar_saida():
    if 'role' not in session:
        return redirect("/")
    aluno_pre_selecionado = request.args.get("aluno_id")
    mensagem_erro = ""
    if request.method == "POST":
        conn = conectar()
        cursor = conn.cursor()
        data_saida = request.form.get("data_saida")
        id_aluno = request.form.get("aluno_id")
        horario = request.form.get("horario")
        motivo = request.form.get("motivo")
        responsavel_escola = request.form.get("responsavel_escola")
        tipo_saida = request.form.get("tipo_saida")
        acompanhante = request.form.get("acompanhante") if tipo_saida == 'acompanhado' else None
        if not data_saida:
            data_saida = datetime.now().strftime("%Y-%m-%d")
        if not horario or not motivo or not responsavel_escola or not tipo_saida:
            mensagem_erro = "Todos os campos são obrigatórios!"
        else:
            cursor.execute("SELECT id FROM alunos WHERE id = ?", (id_aluno,))
            if not cursor.fetchone():
                mensagem_erro = "Aluno não encontrado!"
            else:
                cursor.execute("SELECT COUNT(*) FROM saidas WHERE aluno = ? AND data_saida = ? AND status = 'pendente'", (id_aluno, data_saida))
                if cursor.fetchone()[0] > 0:
                    mensagem_erro = "Este aluno já tem uma saída pendente para hoje!"
                else:
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
                    """, (id_aluno, data_saida, horario, motivo, '', '', '', responsavel_escola, tipo_saida, acompanhante, documento_path, "pendente"))
                    conn.commit()
                    log_operacao(session.get('username'), "REGISTROU SAÍDA", f"Aluno ID: {id_aluno}")
                    flash("Saída registrada com sucesso!", "success")
                    return redirect("/saidas")
        conn.close()
    conn = conectar()
    alunos = conn.execute("SELECT id, nome, turma, serie FROM alunos").fetchall()
    conn.close()
    return render_template("registrar_saida.html", alunos=alunos, erro=mensagem_erro, aluno_selecionado=aluno_pre_selecionado)

@app.route("/editar_saida/<int:id_saida>", methods=["GET", "POST"])
def editar_saida(id_saida):
    if 'role' not in session:
        return redirect("/")
    conn = conectar()
    if request.method == "POST":
        horario = request.form.get("horario")
        motivo = request.form.get("motivo")
        responsavel_escola = request.form.get("responsavel_escola")
        tipo_saida = request.form.get("tipo_saida")
        acompanhante = request.form.get("acompanhante") if tipo_saida == 'acompanhado' else None
        conn.execute("""
            UPDATE saidas SET horario=?, motivo=?, responsavel_escola=?, tipo_saida=?, acompanhante=?
            WHERE id=? AND status='pendente'
        """, (horario, motivo, responsavel_escola, tipo_saida, acompanhante, id_saida))
        conn.commit()
        conn.close()
        flash("Saída atualizada com sucesso!", "success")
        return redirect("/saidas")
    saida = conn.execute("""
        SELECT s.id, s.horario, s.motivo, s.responsavel_escola, s.tipo_saida, s.acompanhante, a.nome
        FROM saidas s
        JOIN alunos a ON s.aluno = a.id
        WHERE s.id=? AND s.status='pendente'
    """, (id_saida,)).fetchone()
    conn.close()
    if not saida:
        return "Saída não encontrada ou já autorizada", 404
    return render_template("editar_saida.html", saida=saida)

@app.route("/saidas")
def lista_saidas():
    if 'role' not in session:
        return redirect("/")
    conn = conectar()
    conn.execute("DELETE FROM saidas WHERE status='concluida' AND data_saida < date('now', '-30 days')")
    conn.commit()
    data_selecionada = request.args.get("data", datetime.now().strftime("%Y-%m-%d"))
    busca = request.args.get("busca", "").strip()
    query = """
        SELECT s.id, a.nome, s.horario, s.motivo, s.responsavel_escola,
               s.tipo_saida, s.acompanhante, s.documento_path, s.status,
               a.serie, a.turma, a.foto_path
        FROM saidas s
        JOIN alunos a ON s.aluno = a.id
        WHERE s.data_saida = ?
    """
    params = [data_selecionada]
    if busca:
        query += " AND a.nome LIKE ?"
        params.append(f"%{busca}%")
    query += " ORDER BY s.horario ASC"
    rows = conn.execute(query, params).fetchall()
    pendentes = []
    concluidas = []
    for r in rows:
        entry = {
            "id": r[0], "aluno": r[1], "horario": r[2], "motivo": r[3],
            "responsavel_escola": r[4], "tipo_saida": r[5], "acompanhante": r[6],
            "documento_path": r[7], "status": r[8], "serie": r[9], "turma": r[10], "foto_path": r[11]
        }
        if r[8] == "pendente":
            pendentes.append(entry)
        else:
            concluidas.append(entry)
    data_min = conn.execute("SELECT MIN(data_saida) FROM saidas").fetchone()[0] or datetime.now().strftime("%Y-%m-%d")
    data_max = datetime.now().strftime("%Y-%m-%d")
    conn.close()
    return render_template("lista_saidas.html",
                           pendentes=pendentes,
                           concluidas=concluidas,
                           data_selecionada=data_selecionada,
                           data_min=data_min,
                           data_max=data_max,
                           busca=busca)

@app.route("/concluir_saida/<int:id_saida>")
def concluir_saida(id_saida):
    if 'role' not in session:
        return redirect("/")
    conn = conectar()
    info = conn.execute("""
        SELECT s.aluno, s.data_saida, s.horario, s.motivo, s.responsavel_escola,
               s.tipo_saida, s.acompanhante, a.email_responsavel, a.nome
        FROM saidas s
        JOIN alunos a ON s.aluno = a.id
        WHERE s.id = ?
    """, (id_saida,)).fetchone()
    if info:
        conn.execute("UPDATE saidas SET status='concluida', usuario_autorizou=? WHERE id=?", (session['user_id'], id_saida))
        conn.commit()
        if info[7]:
            corpo = f"""
            <h2>Saída autorizada</h2>
            <p>O aluno <strong>{info[8]}</strong> teve a saída antecipada autorizada.</p>
            <ul>
                <li>Data: {info[1]}</li>
                <li>Horário: {info[2]}</li>
                <li>Motivo: {info[3]}</li>
                <li>Responsável na escola: {info[4]}</li>
                <li>Tipo de saída: {'Acompanhado(a) - ' + info[6] if info[5]=='acompanhado' else 'Sozinho(a)'}</li>
            </ul>
            """
            enviar_email(info[7], f"Saída autorizada - {info[8]}", corpo)
        log_operacao(session.get('username'), "CONCLUIU SAÍDA", f"ID Saída: {id_saida}")
        flash("Saída autorizada!", "success")
    conn.close()
    return redirect("/saidas")



@app.route("/exportar_saidas")
def exportar_saidas():
    if 'role' not in session:
        return redirect("/")
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT a.nome, a.serie, a.turma, s.data_saida, s.horario,
               s.motivo, s.responsavel, s.responsavel_escola, s.tipo_saida, s.acompanhante, s.status
        FROM saidas s
        JOIN alunos a ON s.aluno = a.id
        ORDER BY s.data_saida DESC, s.horario DESC
    """)
    saidas = cursor.fetchall()
    conn.close()
    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['Aluno', 'Série', 'Turma', 'Data', 'Horário',
                 'Motivo', 'Responsável', 'Responsável Escola', 'Tipo Saída', 'Acompanhante', 'Status'])
    cw.writerows(saidas)
    output = si.getvalue()
    si.close()
    log_operacao(session.get('username'), "EXPORTOU CSV", f"{len(saidas)} registros")
    return Response(output, mimetype="text/csv", headers={"Content-disposition": "attachment; filename=saidas.csv"})

# ------------------ ROTAS ADMIN ------------------
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
        flash("Horários salvos com sucesso!", "success")
        return redirect("/configurar_horarios")
    horarios = {}
    rows = conn.execute("SELECT serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex FROM horarios_padrao").fetchall()
    for row in rows:
        horarios[row[0]] = {'seg': row[1] or '', 'ter': row[2] or '', 'qua': row[3] or '', 'qui': row[4] or '', 'sex': row[5] or ''}
    conn.close()
    return render_template("configurar_horarios.html", series=SERIES, horarios=horarios)


@app.route("/cadastro_aluno", methods=["GET", "POST"])
def cadastro_aluno():
    if 'role' not in session:
        return redirect("/")
    erro = ""
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        turma = request.form.get("turma", "").strip()
        serie = request.form.get("serie", "").strip()
        responsaveis = request.form.get("responsaveis", "").strip()
        telefone = request.form.get("telefone", "").strip()
        email_responsavel = request.form.get("email_responsavel", "").strip()
        data_nascimento = request.form.get("data_nascimento", "").strip()
        alergias = request.form.get("alergias", "").strip()
        observacoes = request.form.get("observacoes", "").strip()
        saida_seg = request.form.get("saida_seg", "")
        saida_ter = request.form.get("saida_ter", "")
        saida_qua = request.form.get("saida_qua", "")
        saida_qui = request.form.get("saida_qui", "")
        saida_sex = request.form.get("saida_sex", "")
        if not nome or not turma or not serie:
            erro = "Preencher nome, turma e série"
        else:
            conn = conectar()
            padrao = conn.execute("SELECT saida_seg, saida_ter, saida_qua, saida_qui, saida_sex FROM horarios_padrao WHERE serie = ?", (serie,)).fetchone()
            if padrao:
                saida_seg = saida_seg or padrao[0]
                saida_ter = saida_ter or padrao[1]
                saida_qua = saida_qua or padrao[2]
                saida_qui = saida_qui or padrao[3]
                saida_sex = saida_sex or padrao[4]
            foto_path = None
            if 'foto' in request.files:
                file = request.files['foto']
                if file and file.filename != '' and allowed_file(file.filename):
                    filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'fotos', filename))
                    foto_path = os.path.join('fotos', filename)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO alunos
                (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex,
                 responsaveis, foto_path, telefone, email_responsavel, data_nascimento, alergias, observacoes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex,
                  responsaveis, foto_path, telefone, email_responsavel, data_nascimento, alergias, observacoes))
            aluno_id = cursor.lastrowid
            conn.commit()
            log_aluno(aluno_id, session['user_id'], 'INSERT', None, {'nome': nome, 'turma': turma, 'serie': serie})
            log_operacao(session.get('username'), "CADASTROU ALUNO", f"Nome: {nome}")
            conn.close()
            flash(f"Aluno {nome} cadastrado com sucesso!", "success")
            return redirect("/cadastro_aluno")
    conn = conectar()
    busca = request.args.get("busca")
    if busca:
        alunos = conn.execute("SELECT id, nome, turma, serie, foto_path FROM alunos WHERE nome LIKE ?", ('%'+busca+'%',)).fetchall()
    else:
        alunos = conn.execute("SELECT id, nome, turma, serie, foto_path FROM alunos").fetchall()
    conn.close()
    alunos = sorted(alunos, key=lambda x: (SERIE_ORDEM.get(x[3], 99), x[2], x[1]))
    grupos = {}
    for a in alunos:
        chave = (a[3], a[2])
        grupos.setdefault(chave, []).append(a)
    alunos_agrupados = [(serie, turma, lista) for (serie, turma), lista in grupos.items()]
    return render_template("cadastro_aluno.html", alunos_agrupados=alunos_agrupados, erro=erro, busca=busca, series=SERIES)

@app.route("/editar_aluno/<int:id_aluno>", methods=["GET", "POST"])
def editar_aluno(id_aluno):
    if session.get('role') != 'admin':
        return "Acesso negado"
    conn = conectar()
    if request.method == "POST":
        antigo = conn.execute("SELECT * FROM alunos WHERE id=?", (id_aluno,)).fetchone()
        nome = request.form.get("nome")
        turma = request.form.get("turma")
        serie = request.form.get("serie")
        responsaveis = request.form.get("responsaveis")
        telefone = request.form.get("telefone")
        email_responsavel = request.form.get("email_responsavel")
        data_nascimento = request.form.get("data_nascimento")
        alergias = request.form.get("alergias")
        observacoes = request.form.get("observacoes")
        saida_seg = request.form.get("saida_seg")
        saida_ter = request.form.get("saida_ter")
        saida_qua = request.form.get("saida_qua")
        saida_qui = request.form.get("saida_qui")
        saida_sex = request.form.get("saida_sex")
        conn.execute("""
            UPDATE alunos SET nome=?, turma=?, serie=?, responsaveis=?, telefone=?, email_responsavel=?,
            data_nascimento=?, alergias=?, observacoes=?, saida_seg=?, saida_ter=?, saida_qua=?, saida_qui=?, saida_sex=?
            WHERE id=?
        """, (nome, turma, serie, responsaveis, telefone, email_responsavel, data_nascimento,
              alergias, observacoes, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex, id_aluno))
        conn.commit()
        novo = conn.execute("SELECT * FROM alunos WHERE id=?", (id_aluno,)).fetchone()
        colunas = [desc[0] for desc in conn.execute("PRAGMA table_info(alunos)").fetchall()]
        antigo_dict = dict(zip(colunas, antigo))
        novo_dict = dict(zip(colunas, novo))
        log_aluno(id_aluno, session['user_id'], 'UPDATE', antigo_dict, novo_dict)
        conn.close()
        flash("Dados do aluno atualizados.", "success")
        return redirect("/cadastro_aluno")
    aluno = conn.execute("SELECT * FROM alunos WHERE id=?", (id_aluno,)).fetchone()
    conn.close()
    if not aluno:
        return "Aluno não encontrado", 404
    return render_template("editar_aluno.html", aluno=aluno, series=SERIES)

@app.route("/deletar_aluno/<int:id_aluno>")
def deletar_aluno(id_aluno):
    if session.get('role') != 'admin':
        return "Acesso negado"
    conn = conectar()
    pendentes = conn.execute("SELECT COUNT(*) FROM saidas WHERE aluno=? AND status='pendente'", (id_aluno,)).fetchone()[0]
    if pendentes > 0:
        conn.close()
        flash("Não é possível remover aluno com saídas pendentes.", "error")
        return redirect("/cadastro_aluno")
    aluno = conn.execute("SELECT * FROM alunos WHERE id=?", (id_aluno,)).fetchone()
    if aluno:
        log_aluno(id_aluno, session['user_id'], 'DELETE', dict(aluno), None)
        conn.execute("DELETE FROM alunos WHERE id=?", (id_aluno,))
        conn.commit()
        flash(f"Aluno removido.", "success")
    conn.close()
    return redirect("/cadastro_aluno")

@app.route("/cadastro_massa", methods=["GET", "POST"])
def cadastro_massa():
    if session.get('role') != 'admin':
        return "Acesso negado"
    mensagem_erro = ""
    if request.method == "POST":
        # Cadastro individual
        if "arquivo_excel" not in request.files or request.files["arquivo_excel"].filename == "":
            nome = request.form.get("nome", "").strip()
            turma = request.form.get("turma", "").strip()
            serie = request.form.get("serie", "").strip()
            responsaveis = request.form.get("responsaveis", "").strip()
            telefone = request.form.get("telefone", "").strip()
            email_responsavel = request.form.get("email_responsavel", "").strip()
            data_nascimento = request.form.get("data_nascimento", "").strip()
            alergias = request.form.get("alergias", "").strip()
            observacoes = request.form.get("observacoes", "").strip()
            saida_seg = request.form.get("saida_seg", "").strip()
            saida_ter = request.form.get("saida_ter", "").strip()
            saida_qua = request.form.get("saida_qua", "").strip()
            saida_qui = request.form.get("saida_qui", "").strip()
            saida_sex = request.form.get("saida_sex", "").strip()
            if not nome or not turma or not serie:
                mensagem_erro = "Preencher nome, turma e série é obrigatório!"
            else:
                conn = conectar()
                padrao = conn.execute("SELECT saida_seg, saida_ter, saida_qua, saida_qui, saida_sex FROM horarios_padrao WHERE serie = ?", (serie,)).fetchone()
                if padrao:
                    saida_seg = saida_seg or padrao[0]
                    saida_ter = saida_ter or padrao[1]
                    saida_qua = saida_qua or padrao[2]
                    saida_qui = saida_qui or padrao[3]
                    saida_sex = saida_sex or padrao[4]
                foto_path = None
                if 'foto' in request.files:
                    file = request.files['foto']
                    if file and file.filename != '' and allowed_file(file.filename):
                        filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
                        file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'fotos', filename))
                        foto_path = os.path.join('fotos', filename)
                conn.execute("""
                    INSERT INTO alunos
                    (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex, responsaveis, foto_path,
                     telefone, email_responsavel, data_nascimento, alergias, observacoes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex, responsaveis, foto_path,
                      telefone, email_responsavel, data_nascimento, alergias, observacoes))
                conn.commit()
                conn.close()
                log_operacao(session.get('username'), "CADASTROU ALUNO", f"Nome: {nome}")
                flash(f"Aluno {nome} cadastrado com sucesso!", "success")
                return redirect("/cadastro_massa")
        # Importação Excel
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
                        erros.append(f"Linha {i+2}: série não reconhecida → \"{serie_raw}\"")
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
                            str(linha.get("responsaveis", "")).strip(),
                            str(linha.get("telefone", "")).strip(),
                            str(linha.get("email_responsavel", "")).strip(),
                            str(linha.get("data_nascimento", "")).strip(),
                            str(linha.get("alergias", "")).strip(),
                            str(linha.get("observacoes", "")).strip()
                        ))
                if erros:
                    series_str = ", ".join(SERIES)
                    mensagem_erro = "Série não reconhecida:<br>" + "<br>".join(erros) + f"<br><br><strong>Valores aceitos:</strong> {series_str}"
                else:
                    foto_map = {}
                    fotos_files = request.files.getlist('fotos')
                    for foto_file in fotos_files:
                        if not foto_file or foto_file.filename == '':
                            continue
                        nome_arquivo = Path(foto_file.filename).name
                        ext = nome_arquivo.rsplit('.', 1)[-1].lower() if '.' in nome_arquivo else ''
                        if ext not in {'png', 'jpg', 'jpeg'}:
                            continue
                        nome_sem_ext = Path(nome_arquivo).stem
                        nome_normalizado = normalizar_nome_para_foto(nome_sem_ext)
                        foto_map[nome_normalizado] = foto_file.read()
                    conn = conectar()
                    cursor = conn.cursor()
                    alunos_inseridos = 0
                    for row in rows:
                        cursor.execute("""
                            INSERT INTO alunos
                            (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex, responsaveis,
                             telefone, email_responsavel, data_nascimento, alergias, observacoes)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, row)
                        aluno_id = cursor.lastrowid
                        nome_aluno = row[0]
                        nome_normalizado = normalizar_nome_para_foto(nome_aluno)
                        if nome_normalizado in foto_map:
                            filename = secure_filename(f"{aluno_id}_{nome_aluno}.jpg")
                            filepath = os.path.join(app.config['UPLOAD_FOLDER'], 'fotos', filename)
                            with open(filepath, 'wb') as f:
                                f.write(foto_map[nome_normalizado])
                            foto_path = os.path.join('fotos', filename)
                            cursor.execute("UPDATE alunos SET foto_path = ? WHERE id = ?", (foto_path, aluno_id))
                        alunos_inseridos += 1
                    conn.commit()
                    conn.close()
                    log_operacao(session.get('username'), "IMPORTOU EXCEL", f"{alunos_inseridos} alunos")
                    flash(f"{alunos_inseridos} aluno{'s' if alunos_inseridos != 1 else ''} importado{'s' if alunos_inseridos != 1 else ''} com sucesso!", "success")
                    return redirect("/cadastro_aluno")
            except Exception as e:
                print("Erro na importação:", e)
                mensagem_erro = "Erro ao processar o arquivo. Verifique o formato do Excel."
    conn = conectar()
    rows = conn.execute("SELECT serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex FROM horarios_padrao").fetchall()
    conn.close()
    horarios_padrao = {r[0]: {'seg': r[1] or '', 'ter': r[2] or '', 'qua': r[3] or '', 'qui': r[4] or '', 'sex': r[5] or ''} for r in rows}
    return render_template("cadastro_massa.html", erro=mensagem_erro, series=SERIES, horarios_padrao=horarios_padrao)

@app.route("/historico", methods=["GET", "POST"])
def historico_unificado():
    if 'role' not in session:
        return redirect("/")
    
    conn = conectar()
    query = """
        SELECT a.id, a.nome, a.turma, a.serie, s.data_saida, s.horario, s.motivo, 
               s.responsavel_escola, s.tipo_saida, s.acompanhante, s.status,
               a.foto_path
        FROM saidas s
        JOIN alunos a ON s.aluno = a.id
        WHERE 1=1
    """
    params = []
    
    # Capturar filtros
    nome = request.args.get("nome", "").strip() or request.form.get("nome", "").strip()
    data_ini = request.args.get("data_ini", "").strip() or request.form.get("data_ini", "").strip()
    data_fim = request.args.get("data_fim", "").strip() or request.form.get("data_fim", "").strip()
    turma = request.args.get("turma", "").strip() or request.form.get("turma", "").strip()
    serie = request.args.get("serie", "").strip() or request.form.get("serie", "").strip()
    
    if nome:
        query += " AND a.nome LIKE ?"
        params.append(f"%{nome}%")
    if data_ini:
        query += " AND s.data_saida >= ?"
        params.append(data_ini)
    if data_fim:
        query += " AND s.data_saida <= ?"
        params.append(data_fim)
    if turma:
        query += " AND a.turma = ?"
        params.append(turma)
    if serie:
        query += " AND a.serie = ?"
        params.append(serie)
    
    query += " ORDER BY s.data_saida DESC, s.horario DESC"
    
    cursor = conn.cursor()
    cursor.execute(query, params)
    resultados = cursor.fetchall()
    
    # Obter listas para os filtros
    turmas = [t[0] for t in conn.execute("SELECT DISTINCT turma FROM alunos ORDER BY turma").fetchall()]
    series = [s[0] for s in conn.execute("SELECT DISTINCT serie FROM alunos ORDER BY serie").fetchall()]
    
    conn.close()
    
    return render_template("historico_unificado.html", 
                           resultados=resultados, 
                           turmas=turmas, 
                           series=series,
                           nome=nome,
                           data_ini=data_ini,
                           data_fim=data_fim,
                           turma_selecionada=turma,
                           serie_selecionada=serie)

@app.route("/historico_aluno/<int:id_aluno>")
def historico_aluno(id_aluno):
    if 'role' not in session:
        return redirect("/")
    conn = conectar()
    aluno = conn.execute("""
        SELECT id, nome, serie, turma, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex,
               responsaveis, foto_path, telefone, email_responsavel, data_nascimento, alergias, observacoes
        FROM alunos WHERE id = ?
    """, (id_aluno,)).fetchone()
    if aluno:
        historico = conn.execute("""
            SELECT data_saida, horario, motivo, responsavel, responsavel_escola,
                   tipo_saida, acompanhante, documento_path, status
            FROM saidas
            WHERE aluno = ? AND status = 'concluida'
            ORDER BY data_saida DESC, horario DESC
        """, (id_aluno,)).fetchall()
    else:
        historico = []
    conn.close()
    return render_template("historico_aluno.html", aluno=aluno, historico=historico)

@app.route("/configuracoes")
def configuracoes():
    if session.get('role') != 'admin':
        return redirect("/inicio")
    conn = conectar()
    total_alunos = conn.execute("SELECT COUNT(*) FROM alunos").fetchone()[0]
    total_usuarios = conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]
    total_saidas = conn.execute("SELECT COUNT(*) FROM saidas").fetchone()[0]
    saidas_hoje = conn.execute(
        "SELECT COUNT(*) FROM saidas WHERE DATE(data_saida) = DATE('now', 'localtime')"
    ).fetchone()[0]
    conn.close()
    backups = sorted(Path("backups").glob("*.db")) if Path("backups").exists() else []
    ultimo_backup = backups[-1].name if backups else None
    total_backups = len(backups)
    return render_template("configuracoes.html",
        total_alunos=total_alunos,
        total_usuarios=total_usuarios,
        total_saidas=total_saidas,
        saidas_hoje=saidas_hoje,
        ultimo_backup=ultimo_backup,
        total_backups=total_backups,
    )

@app.route("/admin/backup")
def admin_backup():
    if session.get('role') != 'admin':
        return redirect("/inicio")
    try:
        os.makedirs("backups", exist_ok=True)
        backup_path = backup_database()
        return redirect(f"/configuracoes?backup=ok&arquivo={os.path.basename(backup_path)}")
    except Exception as e:
        return redirect(f"/configuracoes?backup=erro&msg={str(e)}")

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
    app.run(host="0.0.0.0", port=8002, debug=False)