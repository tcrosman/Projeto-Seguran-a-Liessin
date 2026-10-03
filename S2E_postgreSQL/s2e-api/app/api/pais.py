from flask import render_template, request, redirect, session, flash
from app.api.middleware import pai_required
from app.core.database import get_db, expirar_saidas_nao_liberadas
from app.core.rate_limit import is_limited, record_failure, clear_failures, login_identity
from app.core.tokens import digest_token
from app.core.validators import validar_agendamento
from app.core.clock import school_now
from app.core.mailer import enviar_email
from app.services.school_directory import SchoolDirectoryError
from app.services.school_sync import refresh_parent
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime, timedelta
import secrets
import string
from html import escape


def register_parent_routes(app):
    """Registra todas as rotas do portal dos responsáveis."""

    def _gerar_token_6digitos():
        return ''.join(secrets.choice(string.digits) for _ in range(6))

    # ==================== ENTRY POINT ====================

    @app.route("/pais")
    def pais_redirect():
        if 'pai_id' in session:
            return redirect("/pais/dashboard")
        return redirect("/pais/login")

    # ==================== CADASTRO ====================

    @app.route("/pais/cadastro", methods=["GET", "POST"])
    def pais_cadastro():
        if request.method == "POST":
            nome = request.form.get("nome", "").strip()
            email = request.form.get("email", "").strip().lower()
            senha = request.form.get("senha", "")
            confirmar = request.form.get("confirmar", "")

            if not nome or not email or not senha:
                return render_template("pais/cadastro.html", erro="Preencha todos os campos.")
            if senha != confirmar:
                return render_template("pais/cadastro.html", erro="As senhas não coincidem.")
            if len(senha) < 8:
                return render_template("pais/cadastro.html", erro="A senha deve ter pelo menos 8 caracteres.")
            from app.schemas.user_schema import UserSchema
            password_error = UserSchema._check_password_strength(senha)
            if password_error:
                return render_template("pais/cadastro.html", erro=password_error + ".")

            with get_db() as conn:
                existente = conn.execute(
                    "SELECT id FROM responsaveis WHERE email = %s", (email,)
                ).fetchone()
                if existente:
                    return render_template("pais/cadastro.html", sucesso=True)

                conn.execute(
                    "INSERT INTO responsaveis (email, nome, password_hash, status) VALUES (%s, %s, %s, 'pendente')",
                    (email, nome, generate_password_hash(senha, method='pbkdf2:sha256'))
                )
                # Notifica admins sobre novo cadastro pendente
                admins = conn.execute(
                    "SELECT email FROM usuarios WHERE role = 'admin' AND email IS NOT NULL AND email != ''"
                ).fetchall()

            for admin in admins:
                base_url = app.config['BASE_URL']
                enviar_email(
                    admin['email'],
                    "Novo responsável aguardando aprovação — SecureEdu",
                    f"""<p>O responsável <strong>{escape(nome)}</strong> ({escape(email)}) se cadastrou e aguarda aprovação.</p>
                    <p><a href="{base_url}/admin/responsaveis">Revisar cadastros</a></p>"""
                )

            return render_template("pais/cadastro.html", sucesso=True)

        return render_template("pais/cadastro.html")

    # ==================== LOGIN ====================

    @app.route("/pais/login", methods=["GET", "POST"])
    def pais_login():
        if 'pai_id' in session:
            return redirect("/pais/dashboard")

        ip = request.remote_addr
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            senha = request.form.get("senha", "")
            limit_key = login_identity(ip, email)
            if is_limited('parent_login', limit_key):
                return render_template("pais/login.html", erro="Muitas tentativas. Tente novamente em 5 minutos.")

            with get_db() as conn:
                resp = conn.execute(
                    "SELECT id, nome, email, password_hash, status, auth_version FROM responsaveis WHERE email = %s",
                    (email,)
                ).fetchone()

            # Verifica credenciais locais
            if not resp or not check_password_hash(resp['password_hash'], senha):
                record_failure('parent_login', limit_key)
                return render_template("pais/login.html", erro="Email ou senha incorretos.")

            if resp['status'] == 'pendente':
                return render_template("pais/login.html", erro="Sua conta ainda aguarda aprovação da escola.")
            if resp['status'] == 'bloqueado':
                return render_template("pais/login.html", erro="Sua conta foi bloqueada. Entre em contato com a escola.")

            # Consulta a fonte escolar e sincroniza os filhos antes do 2FA.
            try:
                school_confirmed = refresh_parent(resp['id'], email)
            except SchoolDirectoryError:
                school_confirmed = False
            if not school_confirmed:
                return render_template("pais/login.html", erro="Não foi possível confirmar seu vínculo com a escola. Entre em contato com a secretaria.")

            clear_failures('parent_login', limit_key)

            # Gera e envia token 2FA — protege contra duplo-submit
            email_failed = False
            with get_db() as conn:
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (resp['id'],))
                recente = conn.execute(
                    "SELECT 1 FROM tokens_2fa WHERE responsavel_id = %s AND criado_em > NOW() - INTERVAL '60 seconds'",
                    (resp['id'],)
                ).fetchone()

                if not recente:
                    token = _gerar_token_6digitos()
                    expires_at = datetime.now() + timedelta(minutes=10)
                    conn.execute("DELETE FROM tokens_2fa WHERE responsavel_id = %s", (resp['id'],))
                    conn.execute(
                        "INSERT INTO tokens_2fa (responsavel_id, token, expires_at) VALUES (%s, %s, %s)",
                        (resp['id'], digest_token(token), expires_at)
                    )
                    enviado = enviar_email(
                        email,
                        "Código de verificação — SecureEdu",
                        f"""<div style="font-family:sans-serif;max-width:400px;margin:0 auto;padding:32px 24px;">
                          <h2 style="color:#111827;margin-bottom:8px;">Código de verificação</h2>
                          <p style="color:#6b7280;margin-bottom:24px;">Use o código abaixo para acessar o portal. Ele expira em <strong>10 minutos</strong>.</p>
                          <div style="font-size:36px;font-weight:800;letter-spacing:8px;color:#2563eb;text-align:center;
                               background:#eff6ff;padding:20px;border-radius:12px;margin-bottom:24px;">{token}</div>
                          <p style="color:#9ca3af;font-size:12px;">Se você não solicitou isso, ignore este email.</p>
                        </div>"""
                    )
                    if not enviado:
                        print("[MAILER] Não foi possível enviar o código de verificação.")
                        conn.execute("DELETE FROM tokens_2fa WHERE responsavel_id = %s", (resp['id'],))
                        email_failed = True

            if email_failed:
                return render_template("pais/login.html", erro="Não foi possível enviar o código. Tente novamente mais tarde.")

            # Armazena ID temporário (sem criar sessão completa ainda)
            session.clear()
            session['pai_temp_id'] = resp['id']
            session['pai_temp_email'] = email
            session['pai_temp_nome'] = resp['nome']
            session['pai_temp_auth_version'] = resp['auth_version']
            return redirect("/pais/verificar")

        return render_template("pais/login.html")

    # ==================== 2FA ====================

    @app.route("/pais/verificar", methods=["GET", "POST"])
    def pais_verificar():
        if 'pai_id' in session:
            return redirect("/pais/dashboard")
        if 'pai_temp_id' not in session:
            return redirect("/pais/login")
        with get_db() as conn:
            account = conn.execute(
                "SELECT auth_version FROM responsaveis WHERE id = %s AND status = 'aprovado'",
                (session['pai_temp_id'],),
            ).fetchone()
        if not account or account['auth_version'] != session.get('pai_temp_auth_version'):
            session.clear()
            return redirect("/pais/login")

        if request.method == "POST":
            codigo = request.form.get("codigo", "").strip()
            pai_id = session['pai_temp_id']

            if is_limited('parent_2fa', pai_id):
                return render_template("pais/verificar_2fa.html", erro="Muitas tentativas. Faça login novamente em alguns minutos.")

            with get_db() as conn:
                token_row = conn.execute(
                    "SELECT id, expires_at FROM tokens_2fa WHERE responsavel_id = %s AND token = %s AND usado = FALSE",
                    (pai_id, digest_token(codigo))
                ).fetchone()

                if not token_row:
                    record_failure('parent_2fa', pai_id)
                    if is_limited('parent_2fa', pai_id):
                        conn.execute("DELETE FROM tokens_2fa WHERE responsavel_id = %s", (pai_id,))
                        session.pop('pai_temp_id', None)
                        session.pop('pai_temp_email', None)
                        session.pop('pai_temp_nome', None)
                        return redirect("/pais/login")
                    return render_template("pais/verificar_2fa.html", erro="Código inválido. Verifique o email e tente novamente.")

                expires = token_row['expires_at']
                if not isinstance(expires, datetime):
                    expires = datetime.fromisoformat(str(expires))
                if datetime.now() > expires:
                    conn.execute("DELETE FROM tokens_2fa WHERE id = %s", (token_row['id'],))
                    return render_template("pais/verificar_2fa.html", erro="Código expirado. Faça login novamente.", expirado=True)

                consumed = conn.execute(
                    "UPDATE tokens_2fa SET usado = TRUE WHERE id = %s AND usado = FALSE RETURNING id",
                    (token_row['id'],)
                ).fetchone()
                if not consumed:
                    return render_template("pais/verificar_2fa.html", erro="Código inválido. Faça login novamente.")
                conn.execute("DELETE FROM tokens_2fa WHERE responsavel_id = %s", (pai_id,))

            # Promove para sessão completa
            clear_failures('parent_2fa', pai_id)
            session['pai_id'] = session.pop('pai_temp_id')
            session['pai_email'] = session.pop('pai_temp_email')
            session['pai_nome'] = session.pop('pai_temp_nome')
            session['auth_version'] = session.pop('pai_temp_auth_version')
            return redirect("/pais/dashboard")

        return render_template("pais/verificar_2fa.html")

    # ==================== DASHBOARD ====================

    @app.route("/pais/dashboard")
    @pai_required
    def pais_dashboard():
        with get_db() as conn:
            filhos = conn.execute(
                """SELECT a.id, a.nome, a.turma, a.serie, a.foto_path
                   FROM vinculos_pais_alunos v
                   JOIN alunos a ON a.id = v.aluno_id
                   WHERE v.responsavel_id = %s
                   ORDER BY a.nome""",
                (session['pai_id'],)
            ).fetchall()
        return render_template("pais/dashboard.html", filhos=filhos, nome=session['pai_nome'])

    # ==================== VINCULAR FILHO ====================

    @app.route("/pais/vincular")
    @pai_required
    def pais_vincular():
        flash("Os filhos são vinculados automaticamente pelos dados confirmados pela escola.", "success")
        return redirect("/pais/dashboard")

    # ==================== SOLICITAR SAÍDA ====================

    @app.route("/pais/solicitar/<int:aluno_id>", methods=["GET", "POST"])
    @pai_required
    def pais_solicitar(aluno_id):
        email = session['pai_email']

        # Garante que este aluno pertence ao responsável logado
        with get_db() as conn:
            aluno = conn.execute(
                """SELECT a.id, a.nome, a.turma, a.serie FROM alunos a
                   JOIN vinculos_pais_alunos v ON v.aluno_id = a.id
                   WHERE a.id = %s AND v.responsavel_id = %s""",
                (aluno_id, session['pai_id'])
            ).fetchone()

        if not aluno:
            flash("Aluno não encontrado ou sem vínculo com sua conta.", "error")
            return redirect("/pais/dashboard")

        if request.method == "POST":
            data_solicitada = request.form.get("data_solicitada", "")
            horario = request.form.get("horario", "")
            motivo = request.form.get("motivo", "").strip()
            tipo_saida = request.form.get("tipo_saida", "").strip()
            acompanhante = request.form.get("acompanhante", "").strip()

            today = school_now().strftime("%Y-%m-%d")
            if not data_solicitada or not motivo:
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today,
                                       erro="Preencha a data e o motivo.")
            if not horario:
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today,
                                       erro="Informe o horário da saída.")
            schedule_error = validar_agendamento(data_solicitada, horario)
            if schedule_error:
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today,
                                       erro=schedule_error)
            if tipo_saida not in ('sozinho', 'acompanhado'):
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today,
                                       erro="Selecione o tipo de saída (Sozinho ou Acompanhado).")
            if tipo_saida == "acompanhado" and not acompanhante:
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today,
                                       erro="Informe com quem o aluno vai sair.")

            with get_db() as conn:
                # Verifica se já existe solicitação aguardando para este aluno nesta data
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (aluno_id,))
                existente = conn.execute(
                    "SELECT id FROM solicitacoes_saida WHERE aluno_id = %s AND data_solicitada = %s AND status = 'aguardando'",
                    (aluno_id, data_solicitada)
                ).fetchone()
                if existente:
                    return render_template("pais/solicitar_saida.html", aluno=aluno, today=today,
                                           erro="Já existe uma solicitação aguardando para este aluno nesta data.")

                conn.execute(
                    """INSERT INTO solicitacoes_saida
                       (responsavel_id, aluno_id, data_solicitada, horario_solicitado, motivo,
                        tipo_saida, acompanhante, status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'aguardando')""",
                    (session['pai_id'], aluno_id, data_solicitada, horario, motivo,
                     tipo_saida, acompanhante or None)
                )

            flash(f"Solicitação de saída para {aluno['nome']} enviada com sucesso! Aguarde aprovação da escola.", "success")
            return redirect("/pais/minhas_solicitacoes")

        today = school_now().strftime("%Y-%m-%d")
        return render_template("pais/solicitar_saida.html", aluno=aluno, today=today)

    # ==================== EDITAR SOLICITAÇÃO ====================

    @app.route("/pais/editar_solicitacao/<int:sol_id>", methods=["GET", "POST"])
    @pai_required
    def pais_editar_solicitacao(sol_id):
        with get_db() as conn:
            expirar_saidas_nao_liberadas(conn)
            sol = conn.execute(
                """SELECT ss.*, a.nome, a.turma, a.serie,
                          s.id AS saida_id, s.status AS saida_status
                   FROM solicitacoes_saida ss
                   JOIN alunos a ON a.id = ss.aluno_id
                   LEFT JOIN saidas s ON s.solicitacao_id = ss.id
                   WHERE ss.id = %s AND ss.responsavel_id = %s""",
                (sol_id, session['pai_id'])
            ).fetchone()

        if not sol:
            flash("Solicitação não encontrada.", "error")
            return redirect("/pais/minhas_solicitacoes")

        if sol['status'] == 'rejeitado':
            flash("Solicitações rejeitadas não podem ser editadas. Envie uma nova solicitação.", "error")
            return redirect("/pais/minhas_solicitacoes")

        if sol['status'] == 'aprovado' and sol['saida_status'] != 'pendente':
            flash("Esta saída não pode ser editada. Solicite ajuda à escola se for um registro antigo.", "error")
            return redirect("/pais/minhas_solicitacoes")

        aluno = {"id": sol['aluno_id'], "nome": sol['nome'], "turma": sol['turma'], "serie": sol['serie']}
        today = school_now().strftime("%Y-%m-%d")

        if request.method == "POST":
            data_solicitada = request.form.get("data_solicitada", "")
            horario = request.form.get("horario", "")
            motivo = request.form.get("motivo", "").strip()
            tipo_saida = request.form.get("tipo_saida", "").strip()
            acompanhante = request.form.get("acompanhante", "").strip()

            if not data_solicitada or not motivo:
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today, sol=sol,
                                       voltar_url="/pais/minhas_solicitacoes",
                                       erro="Preencha a data e o motivo.")
            if not horario:
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today, sol=sol,
                                       voltar_url="/pais/minhas_solicitacoes",
                                       erro="Informe o horário da saída.")
            schedule_error = validar_agendamento(data_solicitada, horario)
            if schedule_error:
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today, sol=sol,
                                       voltar_url="/pais/minhas_solicitacoes",
                                       erro=schedule_error)
            if tipo_saida not in ('sozinho', 'acompanhado'):
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today, sol=sol,
                                       voltar_url="/pais/minhas_solicitacoes",
                                       erro="Selecione o tipo de saída (Sozinho ou Acompanhado).")
            if tipo_saida == "acompanhado" and not acompanhante:
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today, sol=sol,
                                       voltar_url="/pais/minhas_solicitacoes",
                                       erro="Informe com quem o aluno vai sair.")

            # Mudanças que afetam a execução física da saída exigem nova aprovação
            precisa_reaprovar = (
                sol['status'] == 'aprovado' and (
                    data_solicitada != sol['data_solicitada'] or
                    horario != (sol['horario_solicitado'] or '') or
                    tipo_saida != (sol['tipo_saida'] or '') or
                    (acompanhante or None) != sol['acompanhante']
                )
            )

            with get_db() as conn:
                current = conn.execute(
                    "SELECT status FROM solicitacoes_saida WHERE id = %s AND responsavel_id = %s FOR UPDATE",
                    (sol_id, session['pai_id']),
                ).fetchone()
                if not current or current['status'] != sol['status']:
                    flash("A solicitação mudou durante a edição. Reabra e confira o estado atual.", "error")
                    return redirect("/pais/minhas_solicitacoes")
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (sol['aluno_id'],))
                duplicate = conn.execute(
                    """SELECT 1 FROM solicitacoes_saida
                       WHERE aluno_id = %s AND data_solicitada = %s
                         AND status = 'aguardando' AND id <> %s""",
                    (sol['aluno_id'], data_solicitada, sol_id),
                ).fetchone()
                if duplicate:
                    flash("Já existe uma solicitação aguardando para este aluno nessa data.", "error")
                    return redirect("/pais/minhas_solicitacoes")
                if sol['status'] == 'aprovado':
                    current_exit = conn.execute(
                        "SELECT status FROM saidas WHERE solicitacao_id = %s FOR UPDATE", (sol_id,)
                    ).fetchone()
                    if not current_exit or current_exit['status'] != 'pendente':
                        flash("A saída já foi liberada ou não pode mais ser editada.", "error")
                        return redirect("/pais/minhas_solicitacoes")
                if precisa_reaprovar:
                    conn.execute(
                        """UPDATE solicitacoes_saida
                           SET data_solicitada=%s, horario_solicitado=%s, motivo=%s,
                               tipo_saida=%s, acompanhante=%s,
                               status='aguardando', revisado_por=NULL, revisado_em=NULL
                           WHERE id=%s""",
                        (data_solicitada, horario, motivo, tipo_saida, acompanhante or None, sol_id)
                    )
                    # Remove a saída pendente antiga da portaria — precisa ser reaprovada
                    conn.execute(
                        "DELETE FROM saidas WHERE solicitacao_id=%s AND status='pendente'",
                        (sol_id,)
                    )
                    flash("Solicitação atualizada. Como o horário, tipo ou acompanhante mudou, "
                          "ela voltou para aguardando aprovação da escola.", "success")
                else:
                    conn.execute(
                        """UPDATE solicitacoes_saida
                           SET data_solicitada=%s, horario_solicitado=%s, motivo=%s,
                               tipo_saida=%s, acompanhante=%s
                           WHERE id=%s""",
                        (data_solicitada, horario, motivo, tipo_saida, acompanhante or None, sol_id)
                    )
                    if sol['status'] == 'aprovado':
                        # Mantém a saída já aprovada em sincronia com o novo motivo
                        conn.execute(
                            "UPDATE saidas SET motivo=%s WHERE solicitacao_id=%s AND status='pendente'",
                            (motivo, sol_id)
                        )
                    flash("Solicitação atualizada com sucesso!", "success")

            return redirect("/pais/minhas_solicitacoes")

        return render_template("pais/solicitar_saida.html", aluno=aluno, today=today, sol=sol,
                               voltar_url="/pais/minhas_solicitacoes")

    # ==================== HISTÓRICO DE SAÍDAS DO FILHO ====================

    @app.route("/pais/historico/<int:aluno_id>")
    @pai_required
    def pais_historico_filho(aluno_id):
        with get_db() as conn:
            aluno = conn.execute(
                """SELECT a.id, a.nome, a.turma, a.serie FROM alunos a
                   JOIN vinculos_pais_alunos v ON v.aluno_id = a.id
                   WHERE a.id = %s AND v.responsavel_id = %s""",
                (aluno_id, session['pai_id'])
            ).fetchone()

            if not aluno:
                flash("Aluno não encontrado ou sem vínculo com sua conta.", "error")
                return redirect("/pais/dashboard")

            expirar_saidas_nao_liberadas(conn)
            historico = conn.execute(
                """SELECT data_saida, horario, tipo_saida, acompanhante, status
                   FROM saidas
                   WHERE aluno = %s
                   ORDER BY data_saida DESC, horario DESC""",
                (aluno_id,)
            ).fetchall()

        return render_template("pais/historico_filho.html", aluno=aluno, historico=historico)

    # ==================== HISTÓRICO DO PAI ====================

    @app.route("/pais/minhas_solicitacoes")
    @pai_required
    def pais_minhas_solicitacoes():
        with get_db() as conn:
            expirar_saidas_nao_liberadas(conn)
            solicitacoes = conn.execute(
                """SELECT ss.id, ss.data_solicitada, ss.horario_solicitado, ss.motivo,
                          ss.status, ss.criado_em,
                          a.nome AS aluno_nome, a.turma, a.serie,
                          s.status AS saida_status
                   FROM solicitacoes_saida ss
                   JOIN alunos a ON a.id = ss.aluno_id
                   LEFT JOIN saidas s ON s.solicitacao_id = ss.id
                   WHERE ss.responsavel_id = %s
                   ORDER BY ss.criado_em DESC""",
                (session['pai_id'],)
            ).fetchall()
        return render_template("pais/minhas_solicitacoes.html", solicitacoes=solicitacoes, nome=session['pai_nome'])

    # ==================== ESQUECI A SENHA ====================

    @app.route("/pais/esqueci_senha", methods=["GET", "POST"])
    def pais_esqueci_senha():
        import secrets
        mensagem = ""
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            link_para_enviar = None

            if is_limited('parent_reset', email):
                return render_template("pais/forgot.html", mensagem="Se este email estiver cadastrado, você receberá um link em breve.")
            record_failure('parent_reset', email)

            with get_db() as conn:
                resp = conn.execute(
                    "SELECT id FROM responsaveis WHERE email = %s", (email,)
                ).fetchone()
                if resp:
                    token = secrets.token_urlsafe(32)
                    expires_at = datetime.now() + timedelta(hours=1)
                    conn.execute("DELETE FROM reset_tokens_pais WHERE responsavel_id = %s", (resp['id'],))
                    conn.execute(
                        "INSERT INTO reset_tokens_pais (responsavel_id, token, expires_at) VALUES (%s, %s, %s)",
                        (resp['id'], digest_token(token), expires_at)
                    )
                    base_url = app.config.get('BASE_URL', 'http://localhost:8002').rstrip('/')
                    link_para_enviar = f"{base_url}/pais/resetar_senha/{token}"

            if link_para_enviar:
                corpo = f"""
                <div style="font-family:sans-serif; max-width:480px; margin:0 auto; padding:32px 24px;">
                  <h2 style="color:#111827; margin-bottom:8px;">Redefinição de senha</h2>
                  <p style="color:#6b7280; margin-bottom:24px;">
                    Recebemos uma solicitação para redefinir a senha da sua conta no portal de responsáveis do
                    <strong>SecureEdu</strong>. Clique no botão abaixo para criar uma nova senha.
                    O link é válido por <strong>1 hora</strong>.
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

        return render_template("pais/forgot.html", mensagem=mensagem)

    @app.route("/pais/resetar_senha/<token>", methods=["GET", "POST"])
    def pais_resetar_senha(token):
        erro = ""
        responsavel_id = None

        with get_db() as conn:
            registro = conn.execute(
                "SELECT responsavel_id, expires_at FROM reset_tokens_pais WHERE token IN (%s, %s)",
                (digest_token(token), token),
            ).fetchone()

            if registro:
                expires = registro['expires_at']
                if not isinstance(expires, datetime):
                    expires = datetime.fromisoformat(str(expires))
                if datetime.now() <= expires:
                    responsavel_id = registro['responsavel_id']
                else:
                    conn.execute("DELETE FROM reset_tokens_pais WHERE token IN (%s, %s)", (digest_token(token), token))
                    erro = "Link expirado. Solicite um novo."
            else:
                erro = "Link inválido."

        if request.method == "POST" and responsavel_id:
            nova = request.form.get("senha", "")
            confirma = request.form.get("confirma", "")
            if len(nova) < 8:
                erro = "A senha deve ter pelo menos 8 caracteres."
            elif nova != confirma:
                erro = "As senhas não coincidem."
            else:
                from app.schemas.user_schema import UserSchema
                password_error = UserSchema._check_password_strength(nova)
                if password_error:
                    return render_template("pais/reset.html", erro=password_error + ".", token=token)
                with get_db() as conn:
                    locked = conn.execute(
                        "SELECT responsavel_id, expires_at FROM reset_tokens_pais WHERE token IN (%s, %s) FOR UPDATE",
                        (digest_token(token), token),
                    ).fetchone()
                    if locked:
                        locked_expires = locked['expires_at']
                        if not isinstance(locked_expires, datetime):
                            locked_expires = datetime.fromisoformat(str(locked_expires))
                        if datetime.now() <= locked_expires:
                            conn.execute(
                                "UPDATE responsaveis SET password_hash = %s, auth_version = auth_version + 1 WHERE id = %s",
                                (generate_password_hash(nova, method='pbkdf2:sha256'), locked['responsavel_id'])
                            )
                            conn.execute("DELETE FROM reset_tokens_pais WHERE token IN (%s, %s)", (digest_token(token), token))
                            conn.execute("DELETE FROM tokens_2fa WHERE responsavel_id = %s", (locked['responsavel_id'],))
                            return redirect("/pais/login?resetado=1")
                erro = "Link inválido ou expirado. Solicite um novo."

        return render_template("pais/reset.html", erro=erro, token=token if responsavel_id else None)

    # ==================== LOGOUT ====================

    @app.route("/pais/logout", methods=["POST"])
    def pais_logout():
        session.pop('pai_id', None)
        session.pop('pai_email', None)
        session.pop('pai_nome', None)
        session.pop('pai_temp_id', None)
        session.pop('pai_temp_email', None)
        session.pop('pai_temp_nome', None)
        session.pop('pai_temp_auth_version', None)
        session.pop('auth_version', None)
        session.pop('pai_2fa_falhas', None)
        return redirect("/pais/login")
