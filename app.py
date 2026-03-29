from flask import Flask, render_template, request, redirect, Response, session
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

load_dotenv()

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
    # Já está no formato padrão?
    if valor.strip() in SERIES:
        return valor.strip()
    return _NORMALIZE_SERIE.get(chave)

app = Flask(__name__)
app.secret_key = '123'

def conectar():
    return sqlite3.connect("escola.db")

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

def validar_placa(placa):
    if not placa or placa.strip() == "":
        return True
    placa = placa.upper().strip()
    padrao_antigo = r'^[A-Z]{3}-\d{4}$'
    padrao_novo = r'^[A-Z]{3}\d[A-Z]\d{2}$'
    return re.match(padrao_antigo, placa) or re.match(padrao_novo, placa)

# ==================== LOGIN ====================

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        conn = conectar()
        user = conn.execute("SELECT role FROM usuarios WHERE username=? AND password=?", 
                           (request.form["u"], request.form["s"])).fetchone()
        conn.close()
        if user:
            session['user_id'] = user[0]
            session['role'] = user[0]
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
                print(e)
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
    
    # Buscar lista de usuários
    conn = conectar()
    usuarios = conn.execute("SELECT id, username, role, email FROM usuarios ORDER BY id").fetchall()
    conn.close()
    
    return render_template("novo.html", usuarios=usuarios)


@app.route("/deletar_usuario/<int:id_usuario>")
def deletar_usuario(id_usuario):
    if session.get('role') != 'admin':
        return "Acesso negado"
    
    conn = conectar()
    
    # Verificar se é o próprio usuário logado
    if id_usuario == session.get('user_id'):
        conn.close()
        return "Você não pode deletar seu próprio usuário! <a href='/novo'>Voltar</a>"
    
    # Verificar se é o último admin
    user = conn.execute("SELECT username, role FROM usuarios WHERE id = ?", (id_usuario,)).fetchone()
    if user and user[1] == 'admin':
        # Contar quantos admins restarão
        admins = conn.execute("SELECT COUNT(*) FROM usuarios WHERE role = 'admin' AND id != ?", (id_usuario,)).fetchone()[0]
        if admins == 0:
            conn.close()
            return "Não pode deletar o último administrador! <a href='/novo'>Voltar</a>"
    
    conn.execute("DELETE FROM usuarios WHERE id = ?", (id_usuario,))
    conn.commit()
    conn.close()
    
    return redirect("/novo")
# ==================== ROTAS (TODOS PODEM) ====================

@app.route("/encontrar_aluno", methods=["GET"])
def encontrar_aluno():
    if 'role' not in session:
        return redirect("/")
    
    conexao = conectar()
    cursor = conexao.cursor()
    busca = request.args.get("busca")
    
    if busca:
        cursor.execute("SELECT id, nome, turma, serie FROM alunos WHERE nome LIKE ? COLLATE NOCASE", ('%' + busca + '%',))
    else:
        cursor.execute("SELECT id, nome, turma, serie FROM alunos")
    
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
        veiculo = request.form.get("veiculo")
        placa = request.form.get("placa")
        responsavel = request.form.get("responsavel")
        
        if not data_saida:
            data_saida = datetime.now().strftime("%Y-%m-%d")
        
        if not horario or not motivo or not veiculo or not responsavel:
            mensagem_erro = "Todos os campos são obrigatórios!"
        elif not validar_placa(placa):
            mensagem_erro = "Formato de placa inválido!"
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
                    cursor.execute("""
                        INSERT INTO saidas
                        (aluno, data_saida, horario, motivo, veiculo, placa, responsavel, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (id_aluno, data_saida, horario, motivo, veiculo, placa, responsavel, "pendente"))
                    conexao.commit()
                    conexao.close()
                    log_operacao(session.get('username', 'usuario'), "REGISTROU SAÍDA", f"Aluno ID: {id_aluno}")
                    return redirect("/saidas")
        
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

    # Deletar registros concluídos com mais de 30 dias
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
        SELECT s.id, a.nome, s.horario, s.motivo, s.veiculo, s.placa, s.responsavel, s.status,
               a.serie, a.turma
        FROM saidas s
        LEFT JOIN alunos a ON s.aluno = a.id
        WHERE s.status = 'pendente' AND s.data_saida = ?
        ORDER BY s.horario ASC
    """, (data_selecionada,))
    pendentes = cursor.fetchall()

    cursor.execute("""
        SELECT s.id, a.nome, s.horario, s.motivo, s.veiculo, s.placa, s.responsavel, s.status,
               a.serie, a.turma
        FROM saidas s
        LEFT JOIN alunos a ON s.aluno = a.id
        WHERE s.status = 'concluida' AND s.data_saida = ?
        ORDER BY s.horario DESC
    """, (data_selecionada,))
    concluidas = cursor.fetchall()
    conexao.close()

    def to_dict(rows):
        return [{"id": s[0], "aluno": s[1], "horario": s[2], "motivo": s[3],
                 "veiculo": s[4], "placa": s[5], "responsavel": s[6], "status": s[7],
                 "serie": s[8], "turma": s[9]} for s in rows]

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
               saida_seg, saida_ter, saida_qua, saida_qui, saida_sex
        FROM alunos
        WHERE id = ?
    """, (id_aluno,))
    aluno_info = cursor.fetchone()
    
    if aluno_info:
        cursor.execute("""
            SELECT data_saida, horario, motivo, veiculo, placa, responsavel, status
            FROM saidas
            WHERE aluno = ? AND status = 'concluida'
            ORDER BY horario DESC
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
               s.motivo, s.veiculo, s.placa, s.responsavel, s.status
        FROM saidas s
        JOIN alunos a ON s.aluno = a.id
        ORDER BY s.data_saida DESC, s.horario DESC
    """)
    
    saidas = cursor.fetchall()
    conexao.close()
    
    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['Aluno', 'Série', 'Turma', 'Data', 'Horário',
                 'Motivo', 'Veículo', 'Placa', 'Responsável', 'Status'])
    cw.writerows(saidas)
    
    output = si.getvalue()
    si.close()
    
    log_operacao(session.get('username', 'usuario'), "EXPORTOU CSV", f"{len(saidas)} registros")
    
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=saidas.csv"}
    )

# ==================== ROTAS (SÓ ADMIN) ====================

@app.route("/cadastro_aluno", methods=["GET", "POST"])
def cadastro_aluno():
    if 'role' not in session:
        return redirect("/")
    mensagem_erro = ""
    
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        turma = request.form.get("turma", "").strip()
        serie = request.form.get("serie", "").strip()
        saida_seg = request.form.get("saida_seg")
        saida_ter = request.form.get("saida_ter")
        saida_qua = request.form.get("saida_qua")
        saida_qui = request.form.get("saida_qui")
        saida_sex = request.form.get("saida_sex")
        
        if not nome or not turma or not serie:
            mensagem_erro = "Preencher todos os itens é obrigatório!"
        else:
            conexao = conectar()
            cursor = conexao.cursor()
            cursor.execute("""
                INSERT INTO alunos
                (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex))
            conexao.commit()
            conexao.close()
            log_operacao(session.get('username', 'admin'), "CADASTROU ALUNO", f"Nome: {nome}, Turma: {turma}")
            return redirect("/cadastro_aluno")
    
    conexao = conectar()
    cursor = conexao.cursor()
    busca = request.args.get("busca")
    
    if busca:
        cursor.execute("SELECT id, nome, turma, serie FROM alunos WHERE nome LIKE ? COLLATE NOCASE", ('%' + busca + '%',))
    else:
        cursor.execute("SELECT id, nome, turma, serie FROM alunos")
    
    alunos = cursor.fetchall()
    conexao.close()

    alunos = sorted(alunos, key=lambda x: (SERIE_ORDEM.get(x[3], 99), x[2], x[1]))

    # Agrupar em Python para manter a ordem correta
    grupos = {}
    for aluno in alunos:
        chave = (aluno[3], aluno[2])  # (serie, turma)
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
    
    cursor.execute("DELETE FROM alunos WHERE id = ?", (id_aluno,))
    cursor.execute("DELETE FROM saidas WHERE aluno = ?", (id_aluno,))
    
    conexao.commit()
    conexao.close()
    log_operacao(session.get('username', 'admin'), "EXCLUIU ALUNO", f"ID: {id_aluno}")
    return redirect("/cadastro_aluno")

@app.route("/cadastro_massa", methods=["GET", "POST"])
def cadastro_massa():
    if session.get('role') != 'admin':
        return "Acesso negado"
    
    mensagem_erro = ""
    
    if request.method == "POST":
        if "arquivo_excel" not in request.files or request.files["arquivo_excel"].filename == "":
            nome = request.form.get("nome", "").strip()
            turma = request.form.get("turma", "").strip()
            serie = request.form.get("serie", "").strip()
            saida_seg = request.form.get("saida_seg", "").strip()
            saida_ter = request.form.get("saida_ter", "").strip()
            saida_qua = request.form.get("saida_qua", "").strip()
            saida_qui = request.form.get("saida_qui", "").strip()
            saida_sex = request.form.get("saida_sex", "").strip()
            if not nome or not turma or not serie:
                mensagem_erro = "Preencher nome, turma e série é obrigatório!"
            else:
                conexao = conectar()
                conexao.execute("""
                    INSERT INTO alunos (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex))
                conexao.commit()
                conexao.close()
                log_operacao(session.get('username', 'admin'), "CADASTROU ALUNO", f"Nome: {nome}, Turma: {turma}")
                return redirect("/cadastro_massa")

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
                                str(linha.get("saida_sex", "")).strip()
                            ))

                    if erros:
                        series_str = ", ".join(SERIES)
                        mensagem_erro = (
                            "Série não reconhecida nas seguintes linhas:<br>"
                            + "<br>".join(erros)
                            + "<br><br><strong>Valores aceitos:</strong> " + series_str
                        )
                    else:
                        conexao = conectar()
                        cursor = conexao.cursor()
                        for row in rows:
                            cursor.execute("""
                                INSERT INTO alunos
                                (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """, row)
                        conexao.commit()
                        conexao.close()
                        log_operacao(session.get('username', 'admin'), "IMPORTOU EXCEL", f"{len(rows)} alunos")
                        return redirect("/cadastro_aluno")
                except Exception as e:
                    print(e)
                    mensagem_erro = "Erro ao ler Excel. Verifique as colunas."

    return render_template("cadastro_massa.html", erro=mensagem_erro, series=SERIES)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8002, debug=True)
