from flask import Flask, render_template, request, redirect, session, flash, send_from_directory
from app.api.middleware import login_required, admin_required, solicitacao_required, release_required, active_staff_role, active_parent_id
from app.core.database import get_db, expirar_saidas_nao_liberadas
from app.core.rate_limit import is_limited, record_failure, clear_failures, login_identity
from app.core.tokens import digest_token
from app.core.validators import validar_agendamento
from app.core.clock import school_now
from app.core.audit_logger import log_operacao
from app.services.school_directory import SchoolDirectoryError
from app.services.school_sync import refresh_parent
from app.config import Config
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime, timedelta
import os
import secrets
from html import escape


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
        ip = request.remote_addr
        
        if request.method == "POST":
            username = request.form.get("u", "").strip()
            senha = request.form.get("s", "")
            limit_key = login_identity(ip, username)
            if is_limited('staff_login', limit_key):
                return render_template("auth/login.html", erro="Muitas tentativas de login. Tente novamente em 5 minutos.")
            with get_db() as conn:
                user = conn.execute(
                    "SELECT id, role, username, password, auth_version FROM usuarios WHERE LOWER(TRIM(username))=LOWER(%s)",
                    (username,)
                ).fetchone()

            if user and check_password_hash(user['password'], senha):
                # Login OK — zera contadores deste IP
                clear_failures('staff_login', limit_key)
                log_operacao(user['username'], "LOGIN_SUCESSO", f"role={user['role']}", ip=ip)
                session.clear()
                session['user_id'] = user['id']
                session['role'] = user['role']
                session['username'] = user['username']
                session['auth_version'] = user['auth_version']
                return redirect("/inicio")
            else:
                record_failure('staff_login', limit_key)
                tentativa_usuario = username
                log_operacao(tentativa_usuario or "desconhecido", "LOGIN_FALHA", "senha incorreta ou usuário inexistente", ip=ip)
                return render_template("auth/login.html", erro="Usuário ou senha incorretos.", usuario=username)

        return render_template("auth/login.html")
    
    @app.route("/inicio")
    @login_required
    def inicio():
        if session.get('role') == 'vigia':
            return redirect("/saidas")
        solicitacoes_pendentes = 0
        responsaveis_pendentes = 0
        if session.get('role') in ('admin', 'basico'):
            with get_db() as conn:
                r = conn.execute("SELECT COUNT(*) AS c FROM solicitacoes_saida WHERE status = 'aguardando'").fetchone()
                solicitacoes_pendentes = r['c'] if r else 0
                if session.get('role') == 'admin':
                    r2 = conn.execute("SELECT COUNT(*) AS c FROM responsaveis WHERE status = 'pendente'").fetchone()
                    responsaveis_pendentes = r2['c'] if r2 else 0
        return render_template("dashboard/home.html",
                               solicitacoes_pendentes=solicitacoes_pendentes,
                               responsaveis_pendentes=responsaveis_pendentes)
    
    @app.route("/logout", methods=["POST"])
    def logout():
        log_operacao(session.get('username', 'desconhecido'), "LOGOUT", "", ip=request.remote_addr)
        session.clear()
        return redirect("/")
    
    @app.route("/esqueci_senha", methods=["GET", "POST"])
    def esqueci_senha():
        from app.core.mailer import enviar_email
        mensagem = ""
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            link_para_enviar = None

            if is_limited('staff_reset', email):
                return render_template("auth/forgot.html", mensagem="Se este email estiver cadastrado, você receberá um link em breve.")
            record_failure('staff_reset', email)

            # Bloco de DB isolado: gera e persiste o token antes de qualquer envio
            with get_db() as conn:
                user = conn.execute("SELECT id FROM usuarios WHERE LOWER(email) = %s", (email,)).fetchone()
                if user:
                    import secrets
                    token = secrets.token_urlsafe(32)
                    expires_at = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
                    conn.execute("DELETE FROM reset_tokens WHERE user_id = %s", (user['id'],))
                    conn.execute("INSERT INTO reset_tokens (user_id, token, expires_at) VALUES (%s, %s, %s)",
                                 (user['id'], digest_token(token), expires_at))
                    base_url = app.config.get('BASE_URL', 'http://localhost:8002').rstrip('/')
                    link_para_enviar = f"{base_url}/resetar_senha/{token}"

            # Envio de email fora do bloco de DB: falha no SMTP não faz rollback do token
            if link_para_enviar:
                corpo = f"""
                <div style="font-family:sans-serif; max-width:480px; margin:0 auto; padding:32px 24px;">
                  <h2 style="color:#111827; margin-bottom:8px;">Redefinição de senha</h2>
                  <p style="color:#6b7280; margin-bottom:24px;">
                    Recebemos uma solicitação para redefinir a senha da sua conta no <strong>SecureEdu</strong>.
                    Clique no botão abaixo para criar uma nova senha. O link é válido por <strong>1 hora</strong>.
                  </p>
                  <a href="{link_para_enviar}" style="display:inline-block; background:#2563eb; color:#fff;
                     padding:12px 28px; border-radius:8px; text-decoration:none; font-weight:600; font-size:15px;">
                    Redefinir minha senha
                  </a>
                  <p style="color:#9ca3af; font-size:12px; margin-top:24px;">
                    Se você não solicitou isso, ignore este email. Sua senha não será alterada.
                  </p>
                </div>
                """
                enviado = enviar_email(email, "Redefinição de senha — SecureEdu", corpo)
                if not enviado:
                    print("[MAILER] Não foi possível enviar o email de redefinição de senha.")

            mensagem = "Se este email estiver cadastrado, você receberá um link em breve."
        return render_template("auth/forgot.html", mensagem=mensagem)
    
    @app.route("/resetar_senha/<token>", methods=["GET", "POST"])
    def resetar_senha(token):
        from datetime import datetime
        erro = ""
        user_id = None
        
        with get_db() as conn:
            registro = conn.execute(
                "SELECT user_id, expires_at FROM reset_tokens WHERE token IN (%s, %s)",
                (digest_token(token), token),
            ).fetchone()

            if registro:
                expires_at = datetime.strptime(registro['expires_at'], "%Y-%m-%d %H:%M:%S")
                if datetime.now() <= expires_at:
                    user_id = registro['user_id']
                else:
                    conn.execute("DELETE FROM reset_tokens WHERE token IN (%s, %s)", (digest_token(token), token))
                    erro = "Link expirado. Solicite um novo."
            else:
                erro = "Link inválido."

        if request.method == "POST" and user_id:
            nova = request.form.get("senha", "")
            confirma = request.form.get("confirma", "")
            from app.schemas.user_schema import UserSchema as _US
            _err = _US._check_password_strength(nova)
            if _err:
                erro = _err + '.'
            elif nova != confirma:
                erro = "As senhas não coincidem."
            else:
                with get_db() as conn:
                    locked = conn.execute(
                        "SELECT user_id, expires_at FROM reset_tokens WHERE token IN (%s, %s) FOR UPDATE",
                        (digest_token(token), token),
                    ).fetchone()
                    if locked and datetime.now() <= datetime.strptime(locked['expires_at'], "%Y-%m-%d %H:%M:%S"):
                        conn.execute("UPDATE usuarios SET password = %s, auth_version = auth_version + 1 WHERE id = %s", (generate_password_hash(nova, method='pbkdf2:sha256'), locked['user_id']))
                        conn.execute("DELETE FROM reset_tokens WHERE token IN (%s, %s)", (digest_token(token), token))
                        return redirect("/?resetado=1")
                erro = "Link inválido ou expirado. Solicite um novo."
        
        return render_template("auth/reset.html", erro=erro, token=token if user_id else None)
    
    # ==================== ALUNOS ====================
    @app.route("/cadastro_aluno")
    @login_required
    def cadastro_aluno():
        busca = request.args.get("busca")
        _ordem_serie = """
            CASE serie
                WHEN 'Berçário 1' THEN 1  WHEN 'Berçário 2' THEN 2
                WHEN 'Pré 1'      THEN 3  WHEN 'Pré 2'      THEN 4
                WHEN '1º ano EF'  THEN 5  WHEN '2º ano EF'  THEN 6
                WHEN '3º ano EF'  THEN 7  WHEN '4º ano EF'  THEN 8
                WHEN '5º ano EF'  THEN 9  WHEN '6º ano EF'  THEN 10
                WHEN '7º ano EF'  THEN 11 WHEN '8º ano EF'  THEN 12
                WHEN '9º ano EF'  THEN 13
                WHEN '1º ano EM'  THEN 14 WHEN '2º ano EM'  THEN 15
                WHEN '3º ano EM'  THEN 16 ELSE 99
            END, turma, nome
        """
        with get_db() as conn:
            if busca:
                rows = conn.execute(
                    f"SELECT id, nome, turma, serie, foto_path FROM alunos WHERE nome ILIKE %s ORDER BY {_ordem_serie}",
                    (f'%{busca}%',)
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT id, nome, turma, serie, foto_path FROM alunos ORDER BY {_ordem_serie}"
                ).fetchall()

        from itertools import groupby
        alunos = [dict(row) for row in rows]
        grupos = [
            {'serie': serie, 'turma': turma, 'alunos': list(membros)}
            for (serie, turma), membros in groupby(alunos, key=lambda a: (a['serie'], a['turma']))
        ]
        return render_template("students/list_of_students.html", grupos=grupos, busca=busca, series=Config.SERIES)
    
    @app.route("/editar_aluno/<int:id_aluno>", methods=["GET", "POST"])
    @admin_required
    def editar_aluno(id_aluno):
        log_operacao(session.get('username'), "ACESSO_DADOS_SAUDE", f"editou aluno ID={id_aluno}", ip=request.remote_addr)
        from app.core.validators import normalizar_serie

        with get_db() as conn:
            aluno = conn.execute("SELECT * FROM alunos WHERE id = %s", (id_aluno,)).fetchone()
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
                            nome=%s, turma=%s, serie=%s, saida_seg=%s, saida_ter=%s, saida_qua=%s, saida_qui=%s, saida_sex=%s,
                            responsaveis=%s, telefone=%s, data_nascimento=%s, alergias=%s, observacoes=%s
                        WHERE id=%s
                    """, (
                        nome, turma, serie_norm,
                        request.form.get("saida_seg", ""),
                        request.form.get("saida_ter", ""),
                        request.form.get("saida_qua", ""),
                        request.form.get("saida_qui", ""),
                        request.form.get("saida_sex", ""),
                        request.form.get("responsaveis", ""),
                        request.form.get("telefone", ""),
                        request.form.get("data_nascimento", ""),
                        request.form.get("alergias", ""),
                        request.form.get("observacoes", ""),
                        id_aluno
                    ))
                    flash("Aluno atualizado!", "success")
                    return redirect(f"/historico_aluno/{id_aluno}")
            
            return render_template("students/edit_students.html", aluno=dict(aluno), series=Config.SERIES)
    
    @app.route("/deletar_aluno/<int:id_aluno>", methods=["POST"])
    @admin_required
    def deletar_aluno(id_aluno):
        with get_db() as conn:
            relacionado = conn.execute("""
                SELECT (
                    EXISTS(SELECT 1 FROM saidas WHERE aluno = %s)
                    OR EXISTS(SELECT 1 FROM solicitacoes_saida WHERE aluno_id = %s)
                    OR EXISTS(SELECT 1 FROM vinculos_pais_alunos WHERE aluno_id = %s)
                ) AS existe
            """, (id_aluno, id_aluno, id_aluno)).fetchone()['existe']

            if relacionado:
                flash("Não é possível remover aluno com histórico, solicitações ou vínculos. Consulte a TI para arquivamento.", "error")
                return redirect("/cadastro_aluno")

            conn.execute("DELETE FROM alunos WHERE id = %s", (id_aluno,))
        
        log_operacao(session.get('username'), "EXCLUIU ALUNO", f"ID: {id_aluno}")
        flash("Aluno removido!", "success")
        return redirect("/cadastro_aluno")
    
    @app.route("/historico_aluno/<int:id_aluno>")
    @login_required
    def historico_aluno(id_aluno):
        log_operacao(session.get('username'), "HISTORICO_ALUNO", f"visualizou histórico aluno ID={id_aluno}", ip=request.remote_addr)
        with get_db() as conn:
            aluno = conn.execute("""
                SELECT id, nome, turma, serie, foto_path, responsaveis,
                       saida_seg, saida_ter, saida_qua, saida_qui, saida_sex
                FROM alunos WHERE id = %s
            """, (id_aluno,)).fetchone()
            if not aluno:
                flash("Aluno não encontrado", "error")
                return redirect("/cadastro_aluno")

            expirar_saidas_nao_liberadas(conn)
            historico = conn.execute("""
                SELECT data_saida, horario, motivo, responsavel_escola, tipo_saida, acompanhante, status
                FROM saidas
                WHERE aluno = %s
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
                expirar_saidas_nao_liberadas(conn)
                rows = conn.execute("""
                    SELECT a.id, a.nome, a.turma, a.serie, a.foto_path,
                           s.data_saida, s.horario, s.motivo, s.responsavel_escola, s.tipo_saida, s.acompanhante, s.status
                    FROM alunos a
                    LEFT JOIN saidas s ON a.id = s.aluno
                    WHERE a.nome ILIKE %s
                    ORDER BY s.data_saida DESC, s.horario DESC
                """, (f'%{nome}%',)).fetchall()
                resultados = [dict(row) for row in rows]
        
        return render_template("departures/history_of_departures.html", resultados=resultados, nome=nome)
    
    # ==================== SAÍDAS ====================
    @app.route("/registrar_saida", methods=["GET", "POST"])
    @admin_required
    def registrar_saida():
        aluno_pre_selecionado = request.args.get("aluno_id")
        
        if request.method == "POST":
            with get_db() as conn:
                aluno_id = request.form.get("aluno_id")
                data_saida = request.form.get("data_saida", school_now().strftime("%Y-%m-%d"))
                horario = request.form.get("horario")
                motivo = request.form.get("motivo")
                responsavel_escola = request.form.get("responsavel_escola")
                tipo_saida = request.form.get("tipo_saida")
                acompanhante = request.form.get("acompanhante") if tipo_saida == 'acompanhado' else None
                
                if (not aluno_id or not aluno_id.isdecimal() or not motivo or not responsavel_escola
                        or tipo_saida not in ('sozinho', 'acompanhado')
                        or (tipo_saida == 'acompanhado' and not acompanhante)):
                    flash("Dados da saída incompletos ou inválidos.", "error")
                elif validar_agendamento(data_saida, horario):
                    flash(validar_agendamento(data_saida, horario), "error")
                else:
                    aluno_id = int(aluno_id)
                    conn.execute("SELECT pg_advisory_xact_lock(%s)", (aluno_id,))
                    aluno_exists = conn.execute("SELECT 1 FROM alunos WHERE id = %s", (aluno_id,)).fetchone()
                    if not aluno_exists:
                        flash("Aluno não encontrado.", "error")
                        return redirect("/registrar_saida")
                    documento_path = None
                    doc = request.files.get('documento')
                    if doc and doc.filename != '':
                        from app.core.validators import validar_upload_documento
                        valido, erro_upload = validar_upload_documento(doc)
                        if valido:
                            ext = doc.filename.rsplit('.', 1)[1].lower()
                            filename = f"{secrets.token_hex(16)}.{ext}"
                            upload_folder = app.config.get('UPLOAD_FOLDER', 'storage')
                            os.makedirs(os.path.join(upload_folder, 'documents'), exist_ok=True)
                            doc.save(os.path.join(upload_folder, 'documents', filename))
                            documento_path = os.path.join('documents', filename)
                        else:
                            flash(erro_upload, "error")
                            return redirect("/registrar_saida")

                    pendente = conn.execute(
                        "SELECT COUNT(*) as total FROM saidas WHERE aluno = %s AND data_saida = %s AND status = 'pendente'",
                        (aluno_id, data_saida)
                    ).fetchone()['total']

                    if pendente > 0:
                        flash("Este aluno já tem uma saída pendente para hoje!", "error")
                    else:
                        conn.execute("""
                            INSERT INTO saidas (aluno, data_saida, horario, motivo, responsavel_escola, tipo_saida, acompanhante, documento_path, status)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pendente')
                        """, (aluno_id, data_saida, horario, motivo, responsavel_escola, tipo_saida, acompanhante, documento_path))
                        log_operacao(session.get('username'), "REGISTROU SAÍDA", f"Aluno ID: {aluno_id}")
                        flash("Saída registrada!", "success")
                        return redirect("/saidas")
        
        with get_db() as conn:
            alunos = conn.execute("SELECT id, nome, serie, turma FROM alunos ORDER BY nome").fetchall()
        
        return render_template("departures/register.html", 
                               alunos=[dict(a) for a in alunos],
                               aluno_selecionado=aluno_pre_selecionado,
                               today=school_now().strftime("%Y-%m-%d"))
    
    @app.route("/saidas")
    @login_required
    def lista_saidas():
        data_selecionada = request.args.get("data", school_now().strftime("%Y-%m-%d"))
        if session.get('role') == 'vigia':
            data_selecionada = school_now().strftime("%Y-%m-%d")
        busca = request.args.get("busca", "").strip()
        
        with get_db() as conn:
            # Marca como não realizadas as saídas aprovadas cujo dia já passou sem liberação
            expirar_saidas_nao_liberadas(conn)

            # A portaria só precisa ver saídas pendentes de liberação; não deve
            # receber histórico ou registros ainda em revisão.
            status_clause = " AND s.status = 'pendente'" if session.get('role') == 'vigia' else ''
            if busca:
                rows = conn.execute("""
                    SELECT s.id, a.nome as aluno, s.horario, s.motivo, s.responsavel_escola, s.tipo_saida, s.acompanhante, s.documento_path, s.status,
                           a.serie, a.turma, a.foto_path
                    FROM saidas s
                    JOIN alunos a ON s.aluno = a.id
                    WHERE s.data_saida = %s AND a.nome ILIKE %s""" + status_clause + """
                    ORDER BY s.horario ASC
                """, (data_selecionada, f'%{busca}%')).fetchall()
            else:
                rows = conn.execute("""
                    SELECT s.id, a.nome as aluno, s.horario, s.motivo, s.responsavel_escola, s.tipo_saida, s.acompanhante, s.documento_path, s.status,
                           a.serie, a.turma, a.foto_path
                    FROM saidas s
                    JOIN alunos a ON s.aluno = a.id
                    WHERE s.data_saida = %s""" + status_clause + """
                    ORDER BY s.horario ASC
                """, (data_selecionada,)).fetchall()
            
            saidas = [dict(row) for row in rows]
        
        pendentes = [s for s in saidas if s['status'] == 'pendente']
        concluidas = [s for s in saidas if s['status'] == 'concluida']
        nao_realizadas = [s for s in saidas if s['status'] == 'nao_realizada']

        return render_template("departures/list_of_exits.html",
                               pendentes=pendentes,
                               concluidas=concluidas,
                               nao_realizadas=nao_realizadas,
                               data_selecionada=data_selecionada,
                               busca=busca)
    
    @app.route("/editar_saida/<int:id_saida>", methods=["GET", "POST"])
    @admin_required
    def editar_saida(id_saida):
        if session.get('role') == 'vigia':
            flash("Acesso negado", "error")
            return redirect("/saidas")

        with get_db() as conn:
            saida = conn.execute("""
                SELECT s.*, a.nome as aluno
                FROM saidas s
                JOIN alunos a ON s.aluno = a.id
                WHERE s.id = %s AND s.status = 'pendente'
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
                    UPDATE saidas SET horario=%s, motivo=%s, responsavel_escola=%s, tipo_saida=%s, acompanhante=%s
                    WHERE id=%s
                """, (horario, motivo, responsavel_escola, tipo_saida, acompanhante, id_saida))
                flash("Saída atualizada!", "success")
                return redirect("/saidas")
            
            return render_template("departures/edit_exits.html", saida=dict(saida))
    
    @app.route("/concluir_saida/<int:id_saida>", methods=["POST"])
    @release_required
    def concluir_saida(id_saida):
        with get_db() as conn:
            saida = conn.execute("""
                SELECT s.data_saida, s.horario, a.id AS aluno_id, a.nome AS aluno_nome
                FROM saidas s
                JOIN alunos a ON a.id = s.aluno
                WHERE s.id = %s AND s.status = 'pendente' AND s.data_saida = %s
                FOR UPDATE
            """, (id_saida, school_now().strftime('%Y-%m-%d'))).fetchone()

            if not saida:
                flash("Saída não encontrada ou já liberada.", "error")
                return redirect("/saidas")

            conn.execute("UPDATE saidas SET status = 'concluida', usuario_autorizou = %s WHERE id = %s",
                         (session['user_id'], id_saida))

            responsaveis = conn.execute("""
                SELECT r.id, r.email
                FROM vinculos_pais_alunos v
                JOIN responsaveis r ON r.id = v.responsavel_id
                WHERE v.aluno_id = %s AND r.status = 'aprovado'
            """, (saida['aluno_id'],)).fetchall() if saida else []

        if saida:
            from app.core.mailer import enviar_email
            for r in responsaveis:
                try:
                    confirmed = refresh_parent(r['id'], r['email'])
                except SchoolDirectoryError:
                    confirmed = False
                if not confirmed:
                    continue
                with get_db() as conn:
                    still_linked = conn.execute("""
                        SELECT 1 FROM vinculos_pais_alunos
                        WHERE responsavel_id = %s AND aluno_id = %s
                    """, (r['id'], saida['aluno_id'])).fetchone()
                if not still_linked:
                    continue
                enviar_email(
                    r['email'],
                    "Saída liberada — SecureEdu",
                    f"""<p>Olá! A saída de <strong>{escape(saida['aluno_nome'])}</strong> foi
                    <strong style="color:#16a34a;">liberada pela segurança da escola</strong>
                    hoje às <strong>{saida['horario']}</strong>.</p>"""
                )

        log_operacao(session.get('username'), "CONCLUIU SAÍDA", f"ID Saída: {id_saida}")
        flash("Saída autorizada!", "success")
        return redirect("/saidas")
    
    # ==================== ADMIN ====================

    @app.route("/cadastro_massa", methods=["GET", "POST"])
    @admin_required
    def cadastro_massa():
        from app.core.audit_logger import log_operacao
        import pandas as pd
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
                    if not arquivo.filename.lower().endswith('.xlsx') or arquivo.stream.read(4) != b'PK\x03\x04':
                        raise ValueError('formato de planilha inválido')
                    arquivo.stream.seek(0)
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
                        mensagem_erro = "Série não reconhecida nas seguintes linhas:\n" + "\n".join(erros) + f"\n\nValores aceitos: {series_str}"
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
                            from app.core.validators import validar_upload_imagem
                            valid_photo, _ = validar_upload_imagem(foto_file)
                            if not valid_photo:
                                continue
                            from app.core.normalize import normalizar_nome_para_foto
                            nome_sem_ext = Path(nome_arquivo).stem
                            nome_normalizado = normalizar_nome_para_foto(nome_sem_ext)
                            foto_map[nome_normalizado] = (foto_file.read(), ext)
                        
                        upload_folder = app.config.get('UPLOAD_FOLDER', 'storage')
                        from app.core.normalize import normalizar_nome_para_foto
                        
                        with get_db() as conn:
                            alunos_inseridos = 0
                            for row in rows:
                                aluno_id = conn.execute("""
                                    INSERT INTO alunos (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex, responsaveis)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
                                """, row).fetchone()['id']
                                nome_aluno = row[0]
                                nome_normalizado = normalizar_nome_para_foto(nome_aluno)
                                if nome_normalizado in foto_map:
                                    foto_bytes, foto_ext = foto_map[nome_normalizado]
                                    filename = f"{aluno_id}_{secrets.token_hex(8)}.{foto_ext}"
                                    os.makedirs(os.path.join(upload_folder, 'photos'), exist_ok=True)
                                    with open(os.path.join(upload_folder, 'photos', filename), 'wb') as f:
                                        f.write(foto_bytes)
                                    foto_path = os.path.join('photos', filename)
                                    conn.execute("UPDATE alunos SET foto_path = %s WHERE id = %s", (foto_path, aluno_id))
                                alunos_inseridos += 1
                        
                        log_operacao(session.get('username'), "IMPORTOU EXCEL", f"{alunos_inseridos} alunos")
                        flash(f"{alunos_inseridos} alunos importados!", "success")
                        return redirect("/cadastro_aluno")
                        
                except Exception:
                    mensagem_erro = "Não foi possível processar a planilha. Confira o formato e tente novamente."
            
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
                            "SELECT saida_seg, saida_ter, saida_qua, saida_qui, saida_sex FROM horarios_padrao WHERE serie = %s",
                            (serie,)
                        ).fetchone()

                    foto_path = None
                    if 'foto' in request.files:
                        file = request.files['foto']
                        if file and file.filename != '':
                            from app.core.validators import validar_upload_imagem
                            valido, erro_upload = validar_upload_imagem(file)
                            if valido:
                                ext = file.filename.rsplit('.', 1)[1].lower()
                                filename = f"{secrets.token_hex(16)}.{ext}"
                                upload_folder = app.config.get('UPLOAD_FOLDER', 'storage')
                                os.makedirs(os.path.join(upload_folder, 'photos'), exist_ok=True)
                                file.save(os.path.join(upload_folder, 'photos', filename))
                                foto_path = os.path.join('photos', filename)
                            else:
                                return render_template(
                                    "students/bulk.html", erro=erro_upload,
                                    series=Config.SERIES, horarios_padrao=horarios_padrao
                                )

                    with get_db() as conn:
                        conn.execute("""
                            INSERT INTO alunos (nome, turma, serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex, responsaveis, foto_path)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    flash(f"Aluno {nome} cadastrado com sucesso!", "success")
                    return redirect("/cadastro_aluno")
        
        return render_template("students/bulk.html", erro=mensagem_erro, series=Config.SERIES, horarios_padrao=horarios_padrao)

    @app.route("/configuracoes")
    @admin_required
    def configuracoes():
        hoje = datetime.now().strftime("%Y-%m-%d")
        with get_db() as conn:
            total_alunos = conn.execute("SELECT COUNT(*) as total FROM alunos").fetchone()['total']
            total_usuarios = conn.execute("SELECT COUNT(*) as total FROM usuarios").fetchone()['total']
            total_saidas = conn.execute("SELECT COUNT(*) as total FROM saidas").fetchone()['total']
            saidas_hoje = conn.execute(
                "SELECT COUNT(*) as total FROM saidas WHERE data_saida = %s", (hoje,)
            ).fetchone()['total']

        return render_template("admin/settings.html",
                               total_alunos=total_alunos,
                               total_usuarios=total_usuarios,
                               total_saidas=total_saidas,
                               saidas_hoje=saidas_hoje)

    @app.route("/admin/backup")
    @admin_required
    def admin_backup():
        flash("Backups são gerenciados automaticamente pelo Supabase. Acesse o painel do Supabase para exportar os dados.", "success")
        return redirect("/configuracoes")
    
    @app.route("/novo", methods=["GET", "POST"])
    @admin_required
    def gerenciar_usuarios():
        from app.repositories.user_repo import UserRepository
        repo = UserRepository()
        
        if request.method == "POST":
            from app.schemas.user_schema import UserSchema
            data = {
                'username': request.form.get("u", "").strip(),
                'password': request.form.get("s", ""),
                'role':     request.form.get("r", "basico"),
                'email':    request.form.get("e", "").strip().lower(),
            }
            ok, err, validated = UserSchema.validate(data)
            if not ok:
                flash(err, "error")
            else:
                try:
                    repo.create(validated)
                    flash(f"Usuário {validated['username']} criado!", "success")
                except Exception:
                    flash("Não foi possível criar o usuário. Confira os dados e tente novamente.", "error")
            return redirect("/novo")
        
        usuarios = repo.get_all_without_passwords()
        return render_template("admin/users.html", usuarios=usuarios)
    
    @app.route("/deletar_usuario/<int:id_usuario>", methods=["POST"])
    @admin_required
    def deletar_usuario(id_usuario):
        from app.repositories.user_repo import UserRepository
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
                        INSERT INTO horarios_padrao
                            (serie, saida_seg, saida_ter, saida_qua, saida_qui, saida_sex)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (serie) DO UPDATE SET
                            saida_seg = EXCLUDED.saida_seg,
                            saida_ter = EXCLUDED.saida_ter,
                            saida_qua = EXCLUDED.saida_qua,
                            saida_qui = EXCLUDED.saida_qui,
                            saida_sex = EXCLUDED.saida_sex
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
    def uploaded_file(filename):
        # Aceita tanto sessão de funcionário quanto de responsável
        staff_role = active_staff_role() if 'user_id' in session else None
        parent_id = active_parent_id() if 'pai_id' in session else None
        if staff_role is None and parent_id is None:
            return redirect('/')
        normalized = os.path.normpath(filename).replace('\\', '/')
        if normalized.startswith('../') or normalized.startswith('/'):
            return "Arquivo não encontrado", 404

        if parent_id is not None:
            if not normalized.startswith('photos/'):
                return "Acesso negado", 403
            with get_db() as conn:
                permitido = conn.execute("""
                    SELECT 1 FROM vinculos_pais_alunos v
                    JOIN alunos a ON a.id = v.aluno_id
                    WHERE v.responsavel_id = %s AND a.foto_path = %s
                """, (parent_id, normalized)).fetchone()
            if not permitido:
                return "Acesso negado", 403
        elif staff_role == 'vigia':
            with get_db() as conn:
                if normalized.startswith('photos/'):
                    permitido = conn.execute("""
                        SELECT 1 FROM alunos a JOIN saidas s ON s.aluno = a.id
                        WHERE a.foto_path = %s AND s.status = 'pendente'
                          AND s.data_saida = %s
                    """, (normalized, school_now().strftime('%Y-%m-%d'))).fetchone()
                elif normalized.startswith('documents/'):
                    permitido = conn.execute("""
                        SELECT 1 FROM saidas
                        WHERE documento_path = %s AND status = 'pendente'
                          AND data_saida = %s
                    """, (normalized, school_now().strftime('%Y-%m-%d'))).fetchone()
                else:
                    permitido = None
            if not permitido:
                return "Acesso negado", 403

        upload_folder = app.config.get('UPLOAD_FOLDER', 'storage')
        response = send_from_directory(upload_folder, normalized)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Disposition'] = 'inline' if normalized.startswith('photos/') else 'attachment'
        return response
    
    # ==================== ADMIN: RESPONSÁVEIS ====================

    @app.route("/admin/responsaveis")
    @admin_required
    def admin_responsaveis():
        with get_db() as conn:
            todos = conn.execute(
                "SELECT id, nome, email, status, criado_em FROM responsaveis ORDER BY criado_em DESC"
            ).fetchall()
        pendentes = [r for r in todos if r['status'] == 'pendente']
        return render_template("admin/responsaveis.html", todos=todos, pendentes=pendentes)

    @app.route("/admin/responsaveis/<int:resp_id>/aprovar", methods=["POST"])
    @admin_required
    def admin_aprovar_responsavel(resp_id):
        with get_db() as conn:
            resp = conn.execute("SELECT nome, email FROM responsaveis WHERE id = %s", (resp_id,)).fetchone()
            if resp:
                conn.execute("UPDATE responsaveis SET status = 'aprovado' WHERE id = %s", (resp_id,))
                flash(f"Conta de {resp['nome']} aprovada.", "success")
        return redirect("/admin/responsaveis")

    @app.route("/admin/responsaveis/<int:resp_id>/bloquear", methods=["POST"])
    @admin_required
    def admin_bloquear_responsavel(resp_id):
        with get_db() as conn:
            resp = conn.execute("SELECT nome FROM responsaveis WHERE id = %s", (resp_id,)).fetchone()
            if resp:
                conn.execute("UPDATE responsaveis SET status = 'bloqueado' WHERE id = %s", (resp_id,))
                flash(f"Conta de {resp['nome']} bloqueada.", "success")
        return redirect("/admin/responsaveis")

    # ==================== ADMIN: SOLICITAÇÕES DE SAÍDA ====================

    @app.route("/admin/solicitacoes")
    @solicitacao_required
    def admin_solicitacoes():
        with get_db() as conn:
            expirar_saidas_nao_liberadas(conn)
            rows = conn.execute("""
                SELECT ss.id, ss.data_solicitada, ss.horario_solicitado, ss.motivo, ss.status,
                       ss.tipo_saida, ss.acompanhante,
                       a.nome AS aluno_nome, a.turma, a.serie,
                       r.nome AS responsavel_nome, r.email AS responsavel_email,
                       s.status AS saida_status
                FROM solicitacoes_saida ss
                JOIN alunos a ON a.id = ss.aluno_id
                JOIN responsaveis r ON r.id = ss.responsavel_id
                LEFT JOIN saidas s ON s.solicitacao_id = ss.id
                ORDER BY ss.criado_em DESC
                LIMIT 100
            """).fetchall()
        aguardando = [r for r in rows if r['status'] == 'aguardando']
        historico  = [r for r in rows if r['status'] != 'aguardando']
        return render_template("admin/solicitacoes.html", aguardando=aguardando, historico=historico)

    @app.route("/admin/solicitacoes/<int:sol_id>/aprovar", methods=["POST"])
    @solicitacao_required
    def admin_aprovar_solicitacao(sol_id):
        with get_db() as conn:
            sol = conn.execute("""
                SELECT ss.aluno_id, ss.data_solicitada, ss.horario_solicitado, ss.motivo,
                       ss.tipo_saida, ss.acompanhante,
                       r.nome AS responsavel_nome, r.email AS responsavel_email, a.nome AS aluno_nome
                FROM solicitacoes_saida ss
                JOIN responsaveis r ON r.id = ss.responsavel_id
                JOIN alunos a ON a.id = ss.aluno_id
                WHERE ss.id = %s AND ss.status = 'aguardando'
                FOR UPDATE OF ss
            """, (sol_id,)).fetchone()

            if not sol:
                flash("Solicitação não encontrada ou já revisada.", "error")
                return redirect("/admin/solicitacoes")

            if validar_agendamento(sol['data_solicitada'], sol['horario_solicitado']):
                flash("A data ou o horário da solicitação já passou ou é inválido.", "error")
                return redirect("/admin/solicitacoes")

            # Aprova a solicitação
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (sol['aluno_id'],))
            already_pending = conn.execute(
                "SELECT 1 FROM saidas WHERE aluno = %s AND data_saida = %s AND status = 'pendente'",
                (sol['aluno_id'], sol['data_solicitada']),
            ).fetchone()
            if already_pending:
                flash("Já existe uma saída pendente para este aluno nessa data.", "error")
                return redirect("/admin/solicitacoes")
            conn.execute(
                "UPDATE solicitacoes_saida SET status = 'aprovado', revisado_por = %s, revisado_em = NOW() WHERE id = %s",
                (session['user_id'], sol_id)
            )
            # Cria a saída real na tabela saidas para a portaria ver
            conn.execute("""
                INSERT INTO saidas (aluno, data_saida, horario, motivo, responsavel_escola, tipo_saida, acompanhante, status, solicitacao_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pendente', %s)
            """, (
                sol['aluno_id'],
                sol['data_solicitada'],
                sol['horario_solicitado'] or '',
                sol['motivo'] or 'Solicitado pelo responsável',
                session.get('username', 'admin'),
                sol['tipo_saida'] or 'acompanhado',
                sol['acompanhante'] or sol['responsavel_nome'],
                sol_id,
            ))

        flash(f"Saída de {sol['aluno_nome']} aprovada e registrada.", "success")
        return redirect("/admin/solicitacoes")

    @app.route("/admin/solicitacoes/<int:sol_id>/rejeitar", methods=["POST"])
    @solicitacao_required
    def admin_rejeitar_solicitacao(sol_id):
        with get_db() as conn:
            sol = conn.execute("""
                SELECT ss.id, r.email AS responsavel_email, a.nome AS aluno_nome
                FROM solicitacoes_saida ss
                JOIN responsaveis r ON r.id = ss.responsavel_id
                JOIN alunos a ON a.id = ss.aluno_id
                WHERE ss.id = %s AND ss.status = 'aguardando'
            """, (sol_id,)).fetchone()

            if not sol:
                flash("Solicitação não encontrada ou já revisada.", "error")
                return redirect("/admin/solicitacoes")

            conn.execute(
                "UPDATE solicitacoes_saida SET status = 'rejeitado', revisado_por = %s, revisado_em = NOW() WHERE id = %s",
                (session['user_id'], sol_id)
            )

        flash(f"Solicitação rejeitada.", "success")
        return redirect("/admin/solicitacoes")

    # ==================== MANUAL ====================
    @app.route("/manual")
    @login_required
    def manual():
        if session.get('role') == 'admin':
            return redirect('/manual/avancado')
        return redirect('/manual/basico')

    @app.route("/manual/basico")
    @login_required
    def manual_basico():
        return render_template("help/manual_basico.html")

    @app.route("/manual/avancado")
    @login_required
    def manual_avancado():
        return render_template("help/manual_avancado.html")

    return app
