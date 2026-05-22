from flask import Flask, render_template, request, redirect, session, flash, send_from_directory
from app.api.middleware import login_required, admin_required
from app.core.database import get_db
from app.core.logs import log_operacao
from app.config import Config
from datetime import datetime
import os

def register_routes(app):
    """Registra todas as rotas web"""
    
    # ==================== ARQUIVOS ESTÁTICOS ====================
    @app.route('/static/css/style.css')
    def serve_css():
        static_folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')
        return send_from_directory(os.path.join(static_folder, 'css'), 'style.css')
    
    @app.route('/static/images/<path:filename>')
    def serve_images(filename):
        static_folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')
        return send_from_directory(os.path.join(static_folder, 'images'), filename)
    
    # ==================== AUTENTICAÇÃO ====================
    @app.route("/", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            with get_db() as conn:
                user = conn.execute(
                    "SELECT id, role, username FROM usuarios WHERE username=? AND password=?",
                    (request.form["u"], request.form["s"])
                ).fetchone()
            if user:
                session['user_id'] = user['id']
                session['role'] = user['role']
                session['username'] = user['username']
                return redirect("/inicio")
        return render_template("auth/login.html")
    
    @app.route("/inicio")
    @login_required
    def inicio():
        return render_template("dashboard/home.html")
    
    @app.route("/logout")
    def logout():
        session.clear()
        return redirect("/")
    
    @app.route("/esqueci_senha", methods=["GET", "POST"])
    def esqueci_senha():
        mensagem = ""
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            with get_db() as conn:
                user = conn.execute("SELECT id FROM usuarios WHERE LOWER(email) = ?", (email,)).fetchone()
                if user:
                    import secrets
                    from datetime import datetime, timedelta
                    token = secrets.token_urlsafe(32)
                    expires_at = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
                    conn.execute("DELETE FROM reset_tokens WHERE user_id = ?", (user['id'],))
                    conn.execute("INSERT INTO reset_tokens (user_id, token, expires_at) VALUES (?, ?, ?)",
                                 (user['id'], token, expires_at))
                    # Em produção: enviar email aqui
                    print(f"Link de reset: /resetar_senha/{token}")
            mensagem = "Se este email estiver cadastrado, você receberá um link em breve."
        return render_template("auth/forgot.html", mensagem=mensagem)
    
    @app.route("/resetar_senha/<token>", methods=["GET", "POST"])
    def resetar_senha(token):
        from datetime import datetime
        erro = ""
        user_id = None
        
        with get_db() as conn:
            registro = conn.execute(
                "SELECT user_id, expires_at FROM reset_tokens WHERE token = ?", (token,)
            ).fetchone()
            
            if registro:
                expires_at = datetime.strptime(registro['expires_at'], "%Y-%m-%d %H:%M:%S")
                if datetime.now() <= expires_at:
                    user_id = registro['user_id']
                else:
                    conn.execute("DELETE FROM reset_tokens WHERE token = ?", (token,))
                    erro = "Link expirado. Solicite um novo."
            else:
                erro = "Link inválido."
        
        if request.method == "POST" and user_id:
            nova = request.form.get("senha", "")
            confirma = request.form.get("confirma", "")
            if len(nova) < 6:
                erro = "A senha deve ter no mínimo 6 caracteres."
            elif nova != confirma:
                erro = "As senhas não coincidem."
            else:
                with get_db() as conn:
                    conn.execute("UPDATE usuarios SET password = ? WHERE id = ?", (nova, user_id))
                    conn.execute("DELETE FROM reset_tokens WHERE token = ?", (token,))
                return redirect("/?resetado=1")
        
        return render_template("auth/reset.html", erro=erro, token=token if user_id else None)
    
    # ==================== ALUNOS ====================
    @app.route("/cadastro_aluno", methods=["GET", "POST"])
    @login_required
    def cadastro_aluno():
        if request.method == "POST":
            nome = request.form.get("nome", "").strip()
            turma = request.form.get("turma", "").strip()
            serie = request.form.get("serie", "").strip()
            
            if not nome or not turma or not serie:
                flash("Preencher nome, turma e série", "error")
            else:
                with get_db() as conn:
                    padrao = conn.execute(
                        "SELECT saida_seg, saida_ter, saida_qua, saida_qui, saida_sex FROM horarios_padrao WHERE serie = ?",
                        (serie,)
                    ).fetchone()
                
                foto_path = None
                if 'foto' in request.files:
                    file = request.files['foto']
                    if file and file.filename != '':
                        from werkzeug.utils import secure_filename
                        filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
                        upload_folder = app.config.get('UPLOAD_FOLDER', 'storage')
                        os.makedirs(os.path.join(upload_folder, 'photos'), exist_ok=True)
                        file.save(os.path.join(upload_folder, 'photos', filename))
                        foto_path = os.path.join('photos', filename)
                
                with get_db() as conn:
                    conn.execute("""
                        INSERT INTO alunos (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex,
                                           responsaveis, foto_path, telefone, email_responsavel, data_nascimento, alergias, observacoes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        nome, turma, serie,
                        request.form.get("saida_seg") or (padrao['saida_seg'] if padrao else ''),
                        request.form.get("saida_ter") or (padrao['saida_ter'] if padrao else ''),
                        request.form.get("saida_qua") or (padrao['saida_qua'] if padrao else ''),
                        request.form.get("saida_qui") or (padrao['saida_qui'] if padrao else ''),
                        request.form.get("saida_sex") or (padrao['saida_sex'] if padrao else ''),
                        request.form.get("responsaveis", ""),
                        foto_path,
                        request.form.get("telefone", ""),
                        request.form.get("email_responsavel", ""),
                        request.form.get("data_nascimento", ""),
                        request.form.get("alergias", ""),
                        request.form.get("observacoes", "")
                    ))
                log_operacao(session.get('username'), "CADASTROU ALUNO", f"Nome: {nome}")
                flash(f"Aluno {nome} cadastrado!", "success")
                return redirect("/cadastro_aluno")
        
        busca = request.args.get("busca")
        with get_db() as conn:
            if busca:
                rows = conn.execute(
                    "SELECT id, nome, turma, serie, foto_path FROM alunos WHERE nome LIKE ? COLLATE NOCASE ORDER BY serie, turma, nome",
                    (f'%{busca}%',)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, nome, turma, serie, foto_path FROM alunos ORDER BY serie, turma, nome"
                ).fetchall()
        
        alunos = [dict(row) for row in rows]
        return render_template("students/list_of_students.html", alunos=alunos, busca=busca, series=Config.SERIES)
    
    @app.route("/editar_aluno/<int:id_aluno>", methods=["GET", "POST"])
    @admin_required
    def editar_aluno(id_aluno):
        from app.core.validators import normalizar_serie
        
        with get_db() as conn:
            aluno = conn.execute("SELECT * FROM alunos WHERE id = ?", (id_aluno,)).fetchone()
            if not aluno:
                flash("Aluno não encontrado", "error")
                return redirect("/cadastro_aluno")
            
            if request.method == "POST":
                nome = request.form.get("nome", "").strip()
                turma = request.form.get("turma", "").strip()
                serie = request.form.get("serie", "").strip()
                
                if not nome or not turma or not serie:
                    flash("Preencher nome, turma e série", "error")
                else:
                    serie_norm = normalizar_serie(serie) or serie
                    conn.execute("""
                        UPDATE alunos SET 
                            nome=?, turma=?, serie=?, saida_seg=?, saida_ter=?, saida_qua=?, saida_qui=?, saida_sex=?,
                            responsaveis=?, telefone=?, email_responsavel=?, data_nascimento=?, alergias=?, observacoes=?
                        WHERE id=?
                    """, (
                        nome, turma, serie_norm,
                        request.form.get("saida_seg", ""),
                        request.form.get("saida_ter", ""),
                        request.form.get("saida_qua", ""),
                        request.form.get("saida_qui", ""),
                        request.form.get("saida_sex", ""),
                        request.form.get("responsaveis", ""),
                        request.form.get("telefone", ""),
                        request.form.get("email_responsavel", ""),
                        request.form.get("data_nascimento", ""),
                        request.form.get("alergias", ""),
                        request.form.get("observacoes", ""),
                        id_aluno
                    ))
                    flash("Aluno atualizado!", "success")
                    return redirect(f"/historico_aluno/{id_aluno}")
            
            return render_template("students/edit_students.html", aluno=dict(aluno), series=Config.SERIES)
    
    @app.route("/deletar_aluno/<int:id_aluno>")
    @admin_required
    def deletar_aluno(id_aluno):
        with get_db() as conn:
            pendentes = conn.execute(
                "SELECT COUNT(*) as total FROM saidas WHERE aluno = ? AND status = 'pendente'",
                (id_aluno,)
            ).fetchone()['total']
            
            if pendentes > 0:
                flash("Não é possível remover aluno com saídas pendentes", "error")
                return redirect("/cadastro_aluno")
            
            conn.execute("DELETE FROM alunos WHERE id = ?", (id_aluno,))
        
        log_operacao(session.get('username'), "EXCLUIU ALUNO", f"ID: {id_aluno}")
        flash("Aluno removido!", "success")
        return redirect("/cadastro_aluno")
    
    @app.route("/historico_aluno/<int:id_aluno>")
    @login_required
    def historico_aluno(id_aluno):
        with get_db() as conn:
            aluno = conn.execute("SELECT * FROM alunos WHERE id = ?", (id_aluno,)).fetchone()
            if not aluno:
                flash("Aluno não encontrado", "error")
                return redirect("/cadastro_aluno")
            
            historico = conn.execute("""
                SELECT data_saida, horario, motivo, responsavel, responsavel_escola, tipo_saida, acompanhante, status
                FROM saidas
                WHERE aluno = ? AND status = 'concluida'
                ORDER BY data_saida DESC, horario DESC
            """, (id_aluno,)).fetchall()
        
        return render_template("students/history_students.html", aluno=dict(aluno), historico=[dict(h) for h in historico])
    
    @app.route("/historico", methods=["GET"])
    @login_required
    def historico_geral():
        nome = request.args.get("nome", "").strip()
        resultados = []
        
        if nome:
            with get_db() as conn:
                rows = conn.execute("""
                    SELECT a.id, a.nome, a.turma, a.serie, a.foto_path,
                           s.data_saida, s.horario, s.motivo, s.responsavel_escola, s.tipo_saida, s.acompanhante, s.status
                    FROM alunos a
                    LEFT JOIN saidas s ON a.id = s.aluno
                    WHERE a.nome LIKE ?
                    ORDER BY s.data_saida DESC, s.horario DESC
                """, (f'%{nome}%',)).fetchall()
                resultados = [dict(row) for row in rows]
        
        return render_template("departures/history_of_departures.html", resultados=resultados, nome=nome)
    
    # ==================== SAÍDAS ====================
    @app.route("/registrar_saida", methods=["GET", "POST"])
    @login_required
    def registrar_saida():
        aluno_pre_selecionado = request.args.get("aluno_id")
        
        if request.method == "POST":
            with get_db() as conn:
                aluno_id = request.form.get("aluno_id")
                data_saida = request.form.get("data_saida", datetime.now().strftime("%Y-%m-%d"))
                horario = request.form.get("horario")
                motivo = request.form.get("motivo")
                responsavel_escola = request.form.get("responsavel_escola")
                tipo_saida = request.form.get("tipo_saida")
                acompanhante = request.form.get("acompanhante") if tipo_saida == 'acompanhado' else None
                
                if not horario or not motivo or not responsavel_escola or not tipo_saida:
                    flash("Todos os campos são obrigatórios!", "error")
                else:
                    pendente = conn.execute(
                        "SELECT COUNT(*) as total FROM saidas WHERE aluno = ? AND data_saida = ? AND status = 'pendente'",
                        (aluno_id, data_saida)
                    ).fetchone()['total']
                    
                    if pendente > 0:
                        flash("Este aluno já tem uma saída pendente para hoje!", "error")
                    else:
                        conn.execute("""
                            INSERT INTO saidas (aluno, data_saida, horario, motivo, responsavel_escola, tipo_saida, acompanhante, status)
                            VALUES (?, ?, ?, ?, ?, ?, ?, 'pendente')
                        """, (aluno_id, data_saida, horario, motivo, responsavel_escola, tipo_saida, acompanhante))
                        log_operacao(session.get('username'), "REGISTROU SAÍDA", f"Aluno ID: {aluno_id}")
                        flash("Saída registrada!", "success")
                        return redirect("/saidas")
        
        with get_db() as conn:
            alunos = conn.execute("SELECT id, nome, serie, turma FROM alunos ORDER BY nome").fetchall()
        
        return render_template("departures/register.html", 
                               alunos=[dict(a) for a in alunos],
                               aluno_selecionado=aluno_pre_selecionado,
                               today=datetime.now().strftime("%Y-%m-%d"))
    
    @app.route("/saidas")
    @login_required
    def lista_saidas():
        data_selecionada = request.args.get("data", datetime.now().strftime("%Y-%m-%d"))
        busca = request.args.get("busca", "").strip()
        
        with get_db() as conn:
            # Limpar saídas antigas
            conn.execute("DELETE FROM saidas WHERE status = 'concluida' AND data_saida < date('now', '-30 days')")
            
            if busca:
                rows = conn.execute("""
                    SELECT s.id, a.nome, s.horario, s.motivo, s.responsavel_escola, s.tipo_saida, s.acompanhante, s.documento_path, s.status,
                           a.serie, a.turma, a.foto_path
                    FROM saidas s
                    JOIN alunos a ON s.aluno = a.id
                    WHERE s.data_saida = ? AND a.nome LIKE ?
                    ORDER BY s.horario ASC
                """, (data_selecionada, f'%{busca}%')).fetchall()
            else:
                rows = conn.execute("""
                    SELECT s.id, a.nome, s.horario, s.motivo, s.responsavel_escola, s.tipo_saida, s.acompanhante, s.documento_path, s.status,
                           a.serie, a.turma, a.foto_path
                    FROM saidas s
                    JOIN alunos a ON s.aluno = a.id
                    WHERE s.data_saida = ?
                    ORDER BY s.horario ASC
                """, (data_selecionada,)).fetchall()
            
            saidas = [dict(row) for row in rows]
        
        pendentes = [s for s in saidas if s['status'] == 'pendente']
        concluidas = [s for s in saidas if s['status'] == 'concluida']
        
        return render_template("departures/list_of_exits.html",
                               pendentes=pendentes,
                               concluidas=concluidas,
                               data_selecionada=data_selecionada,
                               busca=busca)
    
    @app.route("/editar_saida/<int:id_saida>", methods=["GET", "POST"])
    @admin_required
    def editar_saida(id_saida):
        with get_db() as conn:
            saida = conn.execute("""
                SELECT s.*, a.nome as aluno_nome
                FROM saidas s
                JOIN alunos a ON s.aluno = a.id
                WHERE s.id = ? AND s.status = 'pendente'
            """, (id_saida,)).fetchone()
            
            if not saida:
                flash("Saída não encontrada ou já autorizada", "error")
                return redirect("/saidas")
            
            if request.method == "POST":
                horario = request.form.get("horario")
                motivo = request.form.get("motivo")
                responsavel_escola = request.form.get("responsavel_escola")
                tipo_saida = request.form.get("tipo_saida")
                acompanhante = request.form.get("acompanhante") if tipo_saida == 'acompanhado' else None
                
                conn.execute("""
                    UPDATE saidas SET horario=?, motivo=?, responsavel_escola=?, tipo_saida=?, acompanhante=?
                    WHERE id=?
                """, (horario, motivo, responsavel_escola, tipo_saida, acompanhante, id_saida))
                flash("Saída atualizada!", "success")
                return redirect("/saidas")
            
            return render_template("departures/edit_exits.html", saida=dict(saida))
    
    @app.route("/concluir_saida/<int:id_saida>")
    @login_required
    def concluir_saida(id_saida):
        with get_db() as conn:
            conn.execute("UPDATE saidas SET status = 'concluida', usuario_autorizou = ? WHERE id = ?",
                        (session['user_id'], id_saida))
        log_operacao(session.get('username'), "CONCLUIU SAÍDA", f"ID Saída: {id_saida}")
        flash("Saída autorizada!", "success")
        return redirect("/saidas")
    
    # ==================== ADMIN ====================

    @app.route("/cadastro_massa", methods=["GET", "POST"])
    @admin_required
    def cadastro_massa():
        from app.core.logs import log_operacao
        import pandas as pd
        from werkzeug.utils import secure_filename
        import os
        from app.core.validators import normalizar_serie
        from pathlib import Path

        mensagem_erro = ""
        horarios_padrao = {}

        # Carregar horários padrão para o frontend
        with get_db() as conn:
            rows = conn.execute("SELECT serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex FROM horarios_padrao").fetchall()
            for row in rows:
                horarios_padrao[row['serie']] = {
                    'seg': row['saida_seg'] or '',
                    'ter': row['saida_ter'] or '',
                    'qua': row['saida_qua'] or '',
                    'qui': row['saida_qui'] or '',
                    'sex': row['saida_sex'] or ''
                }

        if request.method == "POST":
            # Verificar se é upload de Excel ou cadastro individual
            if "arquivo_excel" in request.files and request.files["arquivo_excel"].filename != "":
                # Importação Excel
                arquivo = request.files["arquivo_excel"]
                try:
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
                        series_str = ", ".join(Config.SERIES)
                        mensagem_erro = "Série não reconhecida nas seguintes linhas:<br>" + "<br>".join(erros) + f"<br><br><strong>Valores aceitos:</strong> {series_str}"
                    else:
                        # Processar fotos
                        foto_map = {}
                        fotos_files = request.files.getlist('fotos')
                        for foto_file in fotos_files:
                            if not foto_file or foto_file.filename == '':
                                continue
                            nome_arquivo = Path(foto_file.filename).name
                            ext = nome_arquivo.rsplit('.', 1)[-1].lower() if '.' in nome_arquivo else ''
                            if ext not in {'png', 'jpg', 'jpeg'}:
                                continue
                            from app.core.normalize import normalizar_nome_para_foto
                            nome_sem_ext = Path(nome_arquivo).stem
                            nome_normalizado = normalizar_nome_para_foto(nome_sem_ext)
                            foto_map[nome_normalizado] = foto_file.read()
                        
                        upload_folder = app.config.get('UPLOAD_FOLDER', 'storage')
                        from app.core.normalize import normalizar_nome_para_foto
                        
                        with get_db() as conn:
                            alunos_inseridos = 0
                            for row in rows:
                                conn.execute("""
                                    INSERT INTO alunos (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex, responsaveis)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, row)
                                aluno_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                                nome_aluno = row[0]
                                nome_normalizado = normalizar_nome_para_foto(nome_aluno)
                                if nome_normalizado in foto_map:
                                    filename = secure_filename(f"{aluno_id}_{nome_aluno}.jpg")
                                    os.makedirs(os.path.join(upload_folder, 'photos'), exist_ok=True)
                                    with open(os.path.join(upload_folder, 'photos', filename), 'wb') as f:
                                        f.write(foto_map[nome_normalizado])
                                    foto_path = os.path.join('photos', filename)
                                    conn.execute("UPDATE alunos SET foto_path = ? WHERE id = ?", (foto_path, aluno_id))
                                alunos_inseridos += 1
                        
                        log_operacao(session.get('username'), "IMPORTOU EXCEL", f"{alunos_inseridos} alunos")
                        flash(f"{alunos_inseridos} alunos importados!", "success")
                        return redirect("/cadastro_aluno")
                        
                except Exception as e:
                    mensagem_erro = f"Erro ao processar o arquivo: {str(e)}"
            
            else:
                # Cadastro individual
                nome = request.form.get("nome", "").strip()
                turma = request.form.get("turma", "").strip()
                serie = request.form.get("serie", "").strip()
                responsaveis = request.form.get("responsaveis", "").strip()
                
                if not nome or not turma or not serie:
                    mensagem_erro = "Preencher nome, turma e série é obrigatório!"
                else:
                    # Buscar horários padrão
                    with get_db() as conn:
                        padrao = conn.execute(
                            "SELECT saida_seg, saida_ter, saida_qua, saida_qui, saida_sex FROM horarios_padrao WHERE serie = ?",
                            (serie,)
                        ).fetchone()
                    
                    foto_path = None
                    if 'foto' in request.files:
                        file = request.files['foto']
                        if file and file.filename != '':
                            from app.core.validators import allowed_file
                            if allowed_file(file.filename):
                                filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
                                upload_folder = app.config.get('UPLOAD_FOLDER', 'storage')
                                os.makedirs(os.path.join(upload_folder, 'photos'), exist_ok=True)
                                file.save(os.path.join(upload_folder, 'photos', filename))
                                foto_path = os.path.join('photos', filename)
                    
                    with get_db() as conn:
                        conn.execute("""
                            INSERT INTO alunos (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex, responsaveis, foto_path)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            nome, turma, serie,
                            request.form.get("saida_seg") or (padrao['saida_seg'] if padrao else ''),
                            request.form.get("saida_ter") or (padrao['saida_ter'] if padrao else ''),
                            request.form.get("saida_qua") or (padrao['saida_qua'] if padrao else ''),
                            request.form.get("saida_qui") or (padrao['saida_qui'] if padrao else ''),
                            request.form.get("saida_sex") or (padrao['saida_sex'] if padrao else ''),
                            responsaveis, foto_path
                        ))
                    log_operacao(session.get('username'), "CADASTROU ALUNO", f"Nome: {nome}")
                    flash(f"Aluno {nome} cadastrado!", "success")
                    return redirect("/cadastro_massa")
        
        return render_template("students/bulk.html", erro=mensagem_erro, series=Config.SERIES, horarios_padrao=horarios_padrao)

    @app.route("/configuracoes")
    @admin_required
    def configuracoes():
        import glob
        with get_db() as conn:
            total_alunos = conn.execute("SELECT COUNT(*) as total FROM alunos").fetchone()['total']
            total_usuarios = conn.execute("SELECT COUNT(*) as total FROM usuarios").fetchone()['total']
            total_saidas = conn.execute("SELECT COUNT(*) as total FROM saidas").fetchone()['total']
            saidas_hoje = conn.execute(
                "SELECT COUNT(*) as total FROM saidas WHERE data_saida = date('now')"
            ).fetchone()['total']
        
        backups = glob.glob("backups/*.db")
        total_backups = len(backups)
        ultimo_backup = backups[-1].split('/')[-1] if backups else None
        
        return render_template("admin/settings.html",
                               total_alunos=total_alunos,
                               total_usuarios=total_usuarios,
                               total_saidas=total_saidas,
                               saidas_hoje=saidas_hoje,
                               total_backups=total_backups,
                               ultimo_backup=ultimo_backup)
    
    @app.route("/admin/backup")
    @admin_required
    def admin_backup():
        from app.core.backup import backup_database
        try:
            backup_path = backup_database()
            filename = backup_path.split('/')[-1]
            flash(f"Backup criado: {filename}", "success")
            return redirect("/configuracoes")
        except Exception as e:
            flash(f"Erro no backup: {e}", "error")
            return redirect("/configuracoes")
    
    @app.route("/novo", methods=["GET", "POST"])
    @admin_required
    def gerenciar_usuarios():
        from app.repositories.users import UserRepository
        repo = UserRepository()
        
        if request.method == "POST":
            username = request.form.get("u")
            password = request.form.get("s")
            role = request.form.get("r", "basico")
            email = request.form.get("e", "").strip().lower()
            
            if not username or not password:
                flash("Usuário e senha obrigatórios", "error")
            else:
                try:
                    repo.create({
                        'username': username,
                        'password': password,
                        'role': role,
                        'email': email
                    })
                    flash(f"Usuário {username} criado!", "success")
                except Exception as e:
                    flash(f"Erro: {e}", "error")
            return redirect("/novo")
        
        usuarios = repo.get_all_without_passwords()
        return render_template("admin/users.html", usuarios=usuarios)
    
    @app.route("/deletar_usuario/<int:id_usuario>")
    @admin_required
    def deletar_usuario(id_usuario):
        from app.repositories.users import UserRepository
        repo = UserRepository()
        
        if id_usuario == session.get('user_id'):
            flash("Você não pode deletar seu próprio usuário!", "error")
            return redirect("/novo")
        
        user = repo.get_by_id(id_usuario)
        if user and user['role'] == 'admin':
            admin_count = repo.get_admin_count_excluding(id_usuario)
            if admin_count == 0:
                flash("Não pode deletar o último administrador!", "error")
                return redirect("/novo")
        
        repo.delete(id_usuario)
        flash("Usuário deletado!", "success")
        return redirect("/novo")
    
    @app.route("/configurar_horarios", methods=["GET", "POST"])
    @admin_required
    def configurar_horarios():
        if request.method == "POST":
            with get_db() as conn:
                for serie in Config.SERIES:
                    seg = request.form.get(f"seg_{serie}", "")
                    ter = request.form.get(f"ter_{serie}", "")
                    qua = request.form.get(f"qua_{serie}", "")
                    qui = request.form.get(f"qui_{serie}", "")
                    sex = request.form.get(f"sex_{serie}", "")
                    conn.execute("""
                        INSERT OR REPLACE INTO horarios_padrao
                        (serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (serie, seg, ter, qua, qui, sex))
            flash("Horários salvos!", "success")
            return redirect("/configurar_horarios")
        
        with get_db() as conn:
            rows = conn.execute("SELECT serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex FROM horarios_padrao").fetchall()
        
        horarios = {}
        for row in rows:
            horarios[row['serie']] = {
                'seg': row['saida_seg'] or '',
                'ter': row['saida_ter'] or '',
                'qua': row['saida_qua'] or '',
                'qui': row['saida_qui'] or '',
                'sex': row['saida_sex'] or ''
            }
        
        return render_template("admin/schedules.html", series=Config.SERIES, horarios=horarios)
    
    # ==================== ARQUIVOS ESTÁTICOS E UPLOADS ====================
    @app.route("/uploads/<path:filename>")
    @login_required
    def uploaded_file(filename):
        upload_folder = app.config.get('UPLOAD_FOLDER', 'storage')
        return send_from_directory(upload_folder, filename)
    
    # ==================== MANUAL ====================
    @app.route("/manual")
    @login_required
    def manual():
        return render_template("help/manual.html")
    
    return app