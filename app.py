import pandas as pd
from flask import Flask, render_template, request, redirect, Response
import sqlite3
import os
from datetime import datetime
import re
import csv
from io import StringIO

app = Flask(__name__)


def conectar():
    return sqlite3.connect("escola.db")


# ==================== FUNÇÕES AUXILIARES ====================

def log_operacao(usuario, acao, detalhes):
    """Registra operações em arquivo de log"""
    try:
        with open("log_sistema.txt", "a", encoding="utf-8") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] {usuario} - {acao}: {detalhes}\n")
    except:
        pass


def validar_placa(placa):
    """Valida formato de placa (antigo ABC-1234 ou novo ABC1D23)"""
    if not placa or placa.strip() == "":
        return True  # Placa é opcional
    placa = placa.upper().strip()
    padrao_antigo = r'^[A-Z]{3}-\d{4}$'
    padrao_novo = r'^[A-Z]{3}\d[A-Z]\d{2}$'
    return re.match(padrao_antigo, placa) or re.match(padrao_novo, placa)


# ==================== ROTAS ====================

@app.route("/")
def inicio():
    return render_template("index.html")

@app.route("/cadastro_aluno", methods=["GET", "POST"])
@app.route("/encontrar_aluno", methods=["GET", "POST"])
@app.route("/procurar_alunos", methods=["GET", "POST"])
def cadastro_aluno():
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
            log_operacao("admin", "CADASTROU ALUNO", f"Nome: {nome}, Turma: {turma}")
            return redirect("/cadastro_aluno")

    conexao = conectar()
    cursor = conexao.cursor()
    busca = request.args.get("busca")

    if busca:
        cursor.execute("""
            SELECT id, nome, turma, serie
            FROM alunos
            WHERE nome LIKE ? COLLATE NOCASE
            ORDER BY
                CASE serie
                    WHEN '6º ano' THEN 1
                    WHEN '7º ano' THEN 2
                    WHEN '8º ano' THEN 3
                    WHEN '9º ano' THEN 4
                    WHEN '1º EM' THEN 5
                    WHEN '2º EM' THEN 6
                    WHEN '3º EM' THEN 7
                END,
                turma,
                nome
        """, ('%' + busca + '%',))
    else:
        cursor.execute("""
            SELECT id, nome, turma, serie
            FROM alunos
            ORDER BY
                CASE serie
                    WHEN '6º ano' THEN 1
                    WHEN '7º ano' THEN 2
                    WHEN '8º ano' THEN 3
                    WHEN '9º ano' THEN 4
                    WHEN '1º EM' THEN 5
                    WHEN '2º EM' THEN 6
                    WHEN '3º EM' THEN 7
                END,
                turma,
                nome
        """)

    alunos = cursor.fetchall()
    conexao.close()
    return render_template("cadastro_aluno.html", alunos=alunos, erro=mensagem_erro, busca=busca)


@app.route("/deletar_aluno/<int:id_aluno>")
def deletar_aluno(id_aluno):
    conexao = conectar()
    cursor = conexao.cursor()

    # Verifica se existem saídas pendentes
    cursor.execute("SELECT COUNT(*) FROM saidas WHERE aluno = ? AND status = 'pendente'", (id_aluno,))
    pendentes = cursor.fetchone()[0]

    if pendentes > 0:
        conexao.close()
        # Como não podemos usar flash messages, redirecionamos com parâmetro de erro
        return redirect(f"/cadastro_aluno?erro=aluno_possui_pendencias")

    # Se não tiver pendências, exclui aluno e suas saídas (opcional)
    cursor.execute("DELETE FROM alunos WHERE id = ?", (id_aluno,))
    cursor.execute("DELETE FROM saidas WHERE aluno = ?", (id_aluno,))  # apaga histórico também

    conexao.commit()
    conexao.close()
    log_operacao("admin", "EXCLUIU ALUNO", f"ID: {id_aluno}")
    return redirect("/cadastro_aluno")


@app.route("/cadastro_massa", methods=["GET", "POST"])
def cadastro_massa():
    mensagem_erro = ""

    if request.method == "POST":
        # Upload de Excel
        print(request)
        if "arquivo_excel" in request.files:
            arquivo = request.files["arquivo_excel"]
            if arquivo.filename != "":
                try:
                    df = pd.read_excel(arquivo)
                    df.columns = df.columns.str.lower().str.strip()

                    conexao = conectar()
                    cursor = conexao.cursor()
                    total = 0
                    for _, linha in df.iterrows():
                        cursor.execute("""
                            INSERT INTO alunos
                            (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            str(linha["nome"]).strip(),
                            str(linha["turma"]).strip(),
                            str(linha["serie"]).strip(),
                            str(linha["saida_seg"]).strip(),
                            str(linha["saida_ter"]).strip(),
                            str(linha["saida_qua"]).strip(),
                            str(linha["saida_qui"]).strip(),
                            str(linha["saida_sex"]).strip()
                        ))
                        total += 1
                    conexao.commit()
                    conexao.close()
                    log_operacao("admin", "IMPORTOU EXCEL", f"{total} alunos")
                    return redirect("/cadastro_aluno")
                except Exception as e:
                    print(e)
                    mensagem_erro = "Erro ao ler Excel. Verifique as colunas."
                    print(f"Erro ao ler Excel: {e}")

        # Cadastro manual por listas
        nomes = request.form.get("nomes")
        turmas = request.form.get("turmas")
        series = request.form.get("series")
        seg = request.form.get("saida_seg")
        ter = request.form.get("saida_ter")
        qua = request.form.get("saida_qua")
        qui = request.form.get("saida_qui")
        sex = request.form.get("saida_sex")

        if nomes and turmas and series:
            lista_nomes = nomes.strip().split("\n")
            lista_turmas = turmas.strip().split("\n")
            lista_series = series.strip().split("\n")
            lista_seg = seg.strip().split("\n") if seg else []
            lista_ter = ter.strip().split("\n") if ter else []
            lista_qua = qua.strip().split("\n") if qua else []
            lista_qui = qui.strip().split("\n") if qui else []
            lista_sex = sex.strip().split("\n") if sex else []

            if not (len(lista_nomes) == len(lista_turmas) == len(lista_series) ==
                    len(lista_seg) == len(lista_ter) == len(lista_qua) ==
                    len(lista_qui) == len(lista_sex)):
                mensagem_erro = "Todas as listas precisam ter o mesmo número de linhas!"
            else:
                conexao = conectar()
                cursor = conexao.cursor()
                for i in range(len(lista_nomes)):
                    cursor.execute("""
                        INSERT INTO alunos
                        (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        lista_nomes[i].strip(),
                        lista_turmas[i].strip(),
                        lista_series[i].strip(),
                        lista_seg[i].strip() if i < len(lista_seg) else "",
                        lista_ter[i].strip() if i < len(lista_ter) else "",
                        lista_qua[i].strip() if i < len(lista_qua) else "",
                        lista_qui[i].strip() if i < len(lista_qui) else "",
                        lista_sex[i].strip() if i < len(lista_sex) else ""
                    ))
                conexao.commit()
                conexao.close()
                log_operacao("admin", "CADASTRO EM MASSA", f"{len(lista_nomes)} alunos")
                return redirect("/cadastro_aluno")

    return render_template("cadastro_massa.html", erro=mensagem_erro)


@app.route("/registrar_saida", methods=["GET", "POST"])
def registrar_saida():
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

        # Data automática se não preenchida
        if not data_saida:
            data_saida = datetime.now().strftime("%Y-%m-%d")

        # Validações
        if not horario or not motivo or not veiculo or not responsavel:
            mensagem_erro = "Todos os campos são obrigatórios!"
        elif not validar_placa(placa):
            mensagem_erro = "Formato de placa inválido! Use ABC-1234 ou ABC1D23"
        else:
            # Verifica se o aluno existe
            cursor.execute("SELECT id FROM alunos WHERE id = ?", (id_aluno,))
            if not cursor.fetchone():
                mensagem_erro = "Aluno não encontrado!"
            else:
                # Impedir saída em fim de semana
                try:
                    data_obj = datetime.strptime(data_saida, "%Y-%m-%d")
                    if data_obj.weekday() >= 5:  # sábado=5, domingo=6
                        mensagem_erro = "Não é possível registrar saída aos finais de semana!"
                except:
                    pass  # data inválida, ignora

                if not mensagem_erro:
                    # Verificar duplicata
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
                        log_operacao("admin", "REGISTROU SAÍDA", f"Aluno ID: {id_aluno}, Data: {data_saida}")
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
    conexao = conectar()
    cursor = conexao.cursor()

    cursor.execute("""
        SELECT 
            s.id,
            a.nome,
            s.horario,
            s.motivo,
            s.veiculo,
            s.placa,
            s.responsavel,
            s.status,
            a.serie,
            a.turma
        FROM saidas s
        LEFT JOIN alunos a ON s.aluno = a.id
        ORDER BY 
            CASE WHEN a.serie IS NULL THEN '' ELSE a.serie END,
            CASE WHEN a.turma IS NULL THEN '' ELSE a.turma END,
            CASE WHEN a.nome IS NULL THEN '' ELSE a.nome END,
            s.horario
    """)

    todas = cursor.fetchall()
    conexao.close()

    pendentes = []
    concluidas = []
    for s in todas:
        saida = {
            "id": s[0],
            "aluno": s[1],
            "horario": s[2],
            "motivo": s[3],
            "veiculo": s[4],
            "placa": s[5],
            "responsavel": s[6],
            "status": s[7],
            "serie": s[8],
            "turma": s[9]
        }
        if saida["status"] == "pendente":
            pendentes.append(saida)
        else:
            concluidas.append(saida)

    return render_template("lista_saidas.html", pendentes=pendentes, concluidas=concluidas)


@app.route("/historico")
def historico():
    conexao = conectar()
    cursor = conexao.cursor()
    cursor.execute("SELECT id, nome, serie, turma FROM alunos ORDER BY serie, turma, nome")
    alunos = cursor.fetchall()
    conexao.close()
    return render_template("historico.html", alunos=alunos)


@app.route("/concluir_saida/<int:id_saida>")
def concluir_saida(id_saida):
    conexao = conectar()
    cursor = conexao.cursor()
    cursor.execute("UPDATE saidas SET status = 'concluida' WHERE id = ?", (id_saida,))
    conexao.commit()
    conexao.close()
    log_operacao("admin", "CONCLUIU SAÍDA", f"ID Saída: {id_saida}")
    return redirect("/saidas")


@app.route("/historico/<int:id_aluno>")
def historico_aluno(id_aluno):
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

    log_operacao("admin", "EXPORTOU CSV", f"{len(saidas)} registros")

    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=saidas.csv"}
    )


if __name__ == "__main__":
    print("APP INICIANDO...")
    app.run(host="0.0.0.0", port=8001, debug=True)