from flask import render_template, request, redirect, session, flash
from app.api.middleware import pai_required
from app.core.database import get_db
from app.core.mailer import enviar_email
from app.services.totus_client import get_totus_client
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime, timedelta
from collections import defaultdict
import random
import string


def register_parent_routes(app):
    """Registra todas as rotas do portal dos responsáveis."""

    # ==================== RATE LIMIT (isolado dos funcionários) ====================
    _falhas_pai = defaultdict(list)
    _bloqueios_pai = {}

    def _pai_rate_limited(ip):
        agora = datetime.now()
        if ip in _bloqueios_pai:
            if agora < _bloqueios_pai[ip]:
                return True
            del _bloqueios_pai[ip]
            _falhas_pai.pop(ip, None)
        return False

    def _registrar_falha_pai(ip):
        agora = datetime.now()
        _falhas_pai[ip] = [t for t in _falhas_pai[ip] if agora - t < timedelta(minutes=15)]
        _falhas_pai[ip].append(agora)
        if len(_falhas_pai[ip]) >= 5:
            _bloqueios_pai[ip] = agora + timedelta(minutes=5)
            _falhas_pai.pop(ip, None)

    def _gerar_token_6digitos():
        return ''.join(random.choices(string.digits, k=6))

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

            with get_db() as conn:
                existente = conn.execute(
                    "SELECT id FROM responsaveis WHERE email = %s", (email,)
                ).fetchone()
                if existente:
                    return render_template("pais/cadastro.html", erro="Este email já está cadastrado.")

                conn.execute(
                    "INSERT INTO responsaveis (email, nome, password_hash, status) VALUES (%s, %s, %s, 'pendente')",
                    (email, nome, generate_password_hash(senha, method='pbkdf2:sha256'))
                )
                # Notifica admins sobre novo cadastro pendente
                admins = conn.execute(
                    "SELECT email FROM usuarios WHERE role = 'admin' AND email IS NOT NULL AND email != ''"
                ).fetchall()

            for admin in admins:
                enviar_email(
                    admin['email'],
                    "Novo responsável aguardando aprovação — SecureEdu",
                    f"""<p>O responsável <strong>{nome}</strong> ({email}) se cadastrou e aguarda aprovação.</p>
                    <p><a href="/admin/responsaveis">Revisar cadastros</a></p>"""
                )

            return render_template("pais/cadastro.html", sucesso=True)

        return render_template("pais/cadastro.html")

    # ==================== LOGIN ====================

    @app.route("/pais/login", methods=["GET", "POST"])
    def pais_login():
        if 'pai_id' in session:
            return redirect("/pais/dashboard")

        ip = request.remote_addr
        if _pai_rate_limited(ip):
            return render_template("pais/login.html", erro="Muitas tentativas. Tente novamente em 5 minutos.")

        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            senha = request.form.get("senha", "")

            with get_db() as conn:
                resp = conn.execute(
                    "SELECT id, nome, email, password_hash, status FROM responsaveis WHERE email = %s",
                    (email,)
                ).fetchone()

            # Verifica credenciais locais
            if not resp or not check_password_hash(resp['password_hash'], senha):
                _registrar_falha_pai(ip)
                return render_template("pais/login.html", erro="Email ou senha incorretos.")

            if resp['status'] == 'pendente':
                return render_template("pais/login.html", erro="Sua conta ainda aguarda aprovação da escola.")
            if resp['status'] == 'bloqueado':
                return render_template("pais/login.html", erro="Sua conta foi bloqueada. Entre em contato com a escola.")

            # Valida com o TOTVS
            totus = get_totus_client()
            if not totus.validar_responsavel(email):
                return render_template("pais/login.html", erro="Não foi possível confirmar seu vínculo com a escola. Entre em contato com a secretaria.")

            # Gera e envia token 2FA
            token = _gerar_token_6digitos()
            expires_at = datetime.now() + timedelta(minutes=10)
            with get_db() as conn:
                conn.execute("DELETE FROM tokens_2fa WHERE responsavel_id = %s", (resp['id'],))
                conn.execute(
                    "INSERT INTO tokens_2fa (responsavel_id, token, expires_at) VALUES (%s, %s, %s)",
                    (resp['id'], token, expires_at)
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
                print(f"[2FA FALLBACK] Token para {email}: {token}")

            # Armazena ID temporário (sem criar sessão completa ainda)
            session['pai_temp_id'] = resp['id']
            session['pai_temp_email'] = email
            session['pai_temp_nome'] = resp['nome']
            return redirect("/pais/verificar")

        return render_template("pais/login.html")

    # ==================== 2FA ====================

    @app.route("/pais/verificar", methods=["GET", "POST"])
    def pais_verificar():
        if 'pai_id' in session:
            return redirect("/pais/dashboard")
        if 'pai_temp_id' not in session:
            return redirect("/pais/login")

        if request.method == "POST":
            codigo = request.form.get("codigo", "").strip()
            pai_id = session['pai_temp_id']

            with get_db() as conn:
                token_row = conn.execute(
                    "SELECT id, expires_at FROM tokens_2fa WHERE responsavel_id = %s AND token = %s AND usado = FALSE",
                    (pai_id, codigo)
                ).fetchone()

                if not token_row:
                    return render_template("pais/verificar_2fa.html", erro="Código inválido. Verifique o email e tente novamente.")

                expires = token_row['expires_at']
                if not isinstance(expires, datetime):
                    expires = datetime.fromisoformat(str(expires))
                if datetime.now() > expires:
                    conn.execute("DELETE FROM tokens_2fa WHERE id = %s", (token_row['id'],))
                    return render_template("pais/verificar_2fa.html", erro="Código expirado. Faça login novamente.", expirado=True)

                conn.execute("UPDATE tokens_2fa SET usado = TRUE WHERE id = %s", (token_row['id'],))

            # Promove para sessão completa
            session['pai_id'] = session.pop('pai_temp_id')
            session['pai_email'] = session.pop('pai_temp_email')
            session['pai_nome'] = session.pop('pai_temp_nome')
            return redirect("/pais/dashboard")

        return render_template("pais/verificar_2fa.html")

    # ==================== DASHBOARD ====================

    @app.route("/pais/dashboard")
    @pai_required
    def pais_dashboard():
        email = session['pai_email']
        with get_db() as conn:
            filhos = conn.execute(
                "SELECT id, nome, turma, serie, foto_path FROM alunos WHERE LOWER(email_responsavel) = %s ORDER BY nome",
                (email,)
            ).fetchall()
        return render_template("pais/dashboard.html", filhos=filhos, nome=session['pai_nome'])

    # ==================== SOLICITAR SAÍDA ====================

    @app.route("/pais/solicitar/<int:aluno_id>", methods=["GET", "POST"])
    @pai_required
    def pais_solicitar(aluno_id):
        email = session['pai_email']

        # Garante que este aluno pertence ao responsável logado
        with get_db() as conn:
            aluno = conn.execute(
                "SELECT id, nome, turma, serie FROM alunos WHERE id = %s AND LOWER(email_responsavel) = %s",
                (aluno_id, email)
            ).fetchone()

        if not aluno:
            flash("Aluno não encontrado ou sem vínculo com sua conta.", "error")
            return redirect("/pais/dashboard")

        if request.method == "POST":
            data_solicitada = request.form.get("data_solicitada", "")
            horario = request.form.get("horario", "")
            motivo = request.form.get("motivo", "").strip()

            if not data_solicitada or not motivo:
                return render_template("pais/solicitar_saida.html", aluno=aluno,
                                       erro="Preencha a data e o motivo.")

            with get_db() as conn:
                # Verifica se já existe solicitação aguardando para este aluno nesta data
                existente = conn.execute(
                    "SELECT id FROM solicitacoes_saida WHERE aluno_id = %s AND data_solicitada = %s AND status = 'aguardando'",
                    (aluno_id, data_solicitada)
                ).fetchone()
                if existente:
                    return render_template("pais/solicitar_saida.html", aluno=aluno,
                                           erro="Já existe uma solicitação aguardando para este aluno nesta data.")

                conn.execute(
                    """INSERT INTO solicitacoes_saida
                       (responsavel_id, aluno_id, data_solicitada, horario_solicitado, motivo, status)
                       VALUES (%s, %s, %s, %s, %s, 'aguardando')""",
                    (session['pai_id'], aluno_id, data_solicitada, horario, motivo)
                )

            flash(f"Solicitação de saída para {aluno['nome']} enviada com sucesso! Aguarde aprovação da escola.", "success")
            return redirect("/pais/minhas_solicitacoes")

        today = datetime.now().strftime("%Y-%m-%d")
        return render_template("pais/solicitar_saida.html", aluno=aluno, today=today)

    # ==================== HISTÓRICO DO PAI ====================

    @app.route("/pais/minhas_solicitacoes")
    @pai_required
    def pais_minhas_solicitacoes():
        with get_db() as conn:
            solicitacoes = conn.execute(
                """SELECT ss.id, ss.data_solicitada, ss.horario_solicitado, ss.motivo,
                          ss.status, ss.criado_em,
                          a.nome AS aluno_nome, a.turma, a.serie
                   FROM solicitacoes_saida ss
                   JOIN alunos a ON a.id = ss.aluno_id
                   WHERE ss.responsavel_id = %s
                   ORDER BY ss.criado_em DESC""",
                (session['pai_id'],)
            ).fetchall()
        return render_template("pais/minhas_solicitacoes.html", solicitacoes=solicitacoes, nome=session['pai_nome'])

    # ==================== LOGOUT ====================

    @app.route("/pais/logout")
    def pais_logout():
        session.pop('pai_id', None)
        session.pop('pai_email', None)
        session.pop('pai_nome', None)
        session.pop('pai_temp_id', None)
        session.pop('pai_temp_email', None)
        session.pop('pai_temp_nome', None)
        return redirect("/pais/login")
