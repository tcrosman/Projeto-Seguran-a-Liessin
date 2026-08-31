from flask import render_template, request, redirect, session, flash
from markupsafe import escape
from app.api.middleware import pai_required
from app.core.database import get_db
from app.core.mailer import enviar_email_async
from app.core import rate_limit
from app.core.passwords import senha_confere, verificar_forca
from app.core.validators import horario_valido, data_valida
from app.services.school_sql_directory import get_school_sql_directory
from werkzeug.security import generate_password_hash
from datetime import datetime, timedelta
import secrets
from app.core.logging_config import obter

_log = obter()


def _responsavel_reconhecido_pela_escola(email):
    """Confirma no banco SQL da escola se o e-mail é um responsável reconhecido — usada no
    autocadastro (/pais/cadastro) e no login (/pais/login). Falha na consulta é tratada como
    "não reconhecido" (fail-closed): sem confirmar o vínculo, não se cria conta nem se libera acesso."""
    try:
        return bool(get_school_sql_directory().responsavel_reconhecido(email))
    except Exception as e:
        _log.warning(f"[SCHOOL_SQL] Erro ao validar responsável: {e}")
        return False


def _resolver_dados_solicitacoes(rows):
    """Enriquece linhas de `solicitacoes_saida` com nome/turma/série do aluno.

    Linhas novas (com ra): resolvidas ao vivo via school_sql_directory.
    Linhas antigas (pré-migração RA, sem ra): usam os campos já trazidos pelo LEFT JOIN
    legado com a tabela `alunos`, até serem migradas na Fase 5 do redesenho.
    """
    rows = [dict(r) for r in rows]
    ras = [r['ra'] for r in rows if r.get('ra')]
    diretorio = {}
    if ras:
        try:
            diretorio = get_school_sql_directory().get_students_by_ras(ras)
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao resolver solicitações: {e}")

    for r in rows:
        if r.get('ra'):
            info = diretorio.get(r['ra'])
            r['aluno_nome'] = info['nome'] if info else f"RA {r['ra']}"
            r['serie'] = (info or {}).get('serie')
            r['turma'] = (info or {}).get('turma')
        else:
            r['aluno_nome'] = r.get('nome_legado')
            r['serie'] = r.get('serie_legado')
            r['turma'] = r.get('turma_legado')
    return rows


def register_parent_routes(app):
    """Registra todas as rotas do portal dos responsáveis."""

    # Tentativas erradas aceitas por código de 2FA antes de descartá-lo (ver /pais/verificar).
    _MAX_TENTATIVAS_2FA = 5

    def _gerar_token_6digitos():
        """Código de 6 dígitos com gerador criptográfico — `random` é previsível a partir de
        saídas observadas e não serve para um fator de autenticação."""
        return f"{secrets.randbelow(1_000_000):06d}"

    def _limpar_sessao_temp():
        """Descarta a sessão parcial criada entre a senha e o 2FA."""
        for chave in ('pai_temp_id', 'pai_temp_email', 'pai_temp_nome'):
            session.pop(chave, None)

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
            # Mesma política dos funcionários: é esta conta que dá acesso a dados de menores.
            erro_senha = verificar_forca(senha)
            if erro_senha:
                return render_template("pais/cadastro.html", erro=erro_senha + '.')

            # Só permite autocadastro se a escola reconhece este e-mail como responsável.
            if not _responsavel_reconhecido_pela_escola(email):
                return render_template(
                    "pais/cadastro.html",
                    erro="Não encontramos esse e-mail como responsável cadastrado na escola. Entre em contato com a secretaria."
                )

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

            # nome e email vêm crus do formulário público de autocadastro. Sem escapar, qualquer
            # um poderia injetar HTML — um link de phishing, por exemplo — no e-mail que os
            # administradores recebem.
            corpo_admin = (
                f"""<p>O responsável <strong>{escape(nome)}</strong> ({escape(email)}) se cadastrou """
                """e aguarda aprovação.</p>
                <p><a href="/admin/responsaveis">Revisar cadastros</a></p>"""
            )
            for admin in admins:
                enviar_email_async(admin['email'], "Novo responsável aguardando aprovação — SecureEdu", corpo_admin)

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

            # Só no POST: o limite custa uma consulta ao banco e não há o que limitar num GET.
            if rate_limit.esta_bloqueado(rate_limit.PAIS_LOGIN_IP, ip) or (
                email and rate_limit.esta_bloqueado(rate_limit.PAIS_LOGIN_CONTA, email)
            ):
                return render_template("pais/login.html", erro="Muitas tentativas. Tente novamente em 5 minutos.")

            with get_db() as conn:
                resp = conn.execute(
                    "SELECT id, nome, email, password_hash, status FROM responsaveis WHERE email = %s",
                    (email,)
                ).fetchone()

            # Verifica credenciais locais
            if not senha_confere(resp['password_hash'] if resp else None, senha):
                rate_limit.registrar_falha(rate_limit.PAIS_LOGIN_IP, ip)
                if email:
                    rate_limit.registrar_falha(rate_limit.PAIS_LOGIN_CONTA, email)
                return render_template("pais/login.html", erro="Email ou senha incorretos.")

            rate_limit.limpar(rate_limit.PAIS_LOGIN_CONTA, email)
            rate_limit.limpar(rate_limit.PAIS_LOGIN_IP, ip)

            if resp['status'] == 'pendente':
                return render_template("pais/login.html", erro="Sua conta ainda aguarda aprovação da escola.")
            if resp['status'] == 'bloqueado':
                return render_template("pais/login.html", erro="Sua conta foi bloqueada. Entre em contato com a escola.")

            # Confirma no banco SQL da escola que o e-mail ainda é um responsável reconhecido
            if not _responsavel_reconhecido_pela_escola(email):
                return render_template("pais/login.html", erro="Não foi possível confirmar seu vínculo com a escola. Entre em contato com a secretaria.")

            # Gera e envia token 2FA — protege contra duplo-submit
            with get_db() as conn:
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
                        (resp['id'], token, expires_at)
                    )
                    enviar_email_async(
                        email,
                        "Código de verificação — SecureEdu",
                        f"""<div style="font-family:sans-serif;max-width:400px;margin:0 auto;padding:32px 24px;">
                          <h2 style="color:#111827;margin-bottom:8px;">Código de verificação</h2>
                          <p style="color:#6b7280;margin-bottom:24px;">Use o código abaixo para acessar o portal. Ele expira em <strong>10 minutos</strong>.</p>
                          <div style="font-size:36px;font-weight:800;letter-spacing:8px;color:#2563eb;text-align:center;
                               background:#eff6ff;padding:20px;border-radius:12px;margin-bottom:24px;">{token}</div>
                          <p style="color:#9ca3af;font-size:12px;">Se você não solicitou isso, ignore este email.</p>
                        </div>""",
                        fallback_log=f"[2FA FALLBACK] Token para {email}: {token}",
                    )

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

        ip = request.remote_addr

        if request.method == "POST":
            # Escopo próprio, separado do login: errar o código de 2FA não pode consumir o limite
            # de tentativas de senha nem trancar quem só quer entrar com a senha do mesmo IP da
            # escola. O limite por conta aqui é o contador em tokens_2fa.tentativas, logo abaixo.
            if rate_limit.esta_bloqueado(rate_limit.PAIS_2FA_IP, ip):
                return render_template("pais/verificar_2fa.html",
                                       erro="Muitas tentativas. Tente novamente em 5 minutos.",
                                       expirado=True)

            codigo = request.form.get("codigo", "").strip()
            pai_id = session['pai_temp_id']

            # Todo o acesso ao banco fica num bloco só, e o desfecho vira um rótulo tratado
            # depois. rate_limit abre a própria conexão: chamá-lo aqui dentro seguraria duas
            # conexões do pool ao mesmo tempo e, sob concorrência, travaria o pool.
            with get_db() as conn:
                token_row = conn.execute(
                    "SELECT id, expires_at FROM tokens_2fa WHERE responsavel_id = %s AND token = %s AND usado = FALSE",
                    (pai_id, codigo)
                ).fetchone()

                if not token_row:
                    # Código errado: conta a tentativa nos tokens abertos deste responsável.
                    tentativas = conn.execute(
                        """UPDATE tokens_2fa SET tentativas = tentativas + 1
                           WHERE responsavel_id = %s AND usado = FALSE
                           RETURNING tentativas""",
                        (pai_id,)
                    ).fetchall()
                    if any(t['tentativas'] >= _MAX_TENTATIVAS_2FA for t in tentativas):
                        # Queima o código: o responsável precisa refazer o login para receber outro.
                        conn.execute("DELETE FROM tokens_2fa WHERE responsavel_id = %s", (pai_id,))
                        desfecho = 'queimado'
                    else:
                        desfecho = 'invalido'
                else:
                    expires = token_row['expires_at']
                    if not isinstance(expires, datetime):
                        expires = datetime.fromisoformat(str(expires))
                    if datetime.now() > expires:
                        conn.execute("DELETE FROM tokens_2fa WHERE id = %s", (token_row['id'],))
                        desfecho = 'expirado'
                    # Consome o código de forma atômica: se outra request já o usou, rowcount é 0
                    # e a sessão não é promovida (o mesmo código não vale duas vezes).
                    elif conn.execute(
                        "UPDATE tokens_2fa SET usado = TRUE WHERE id = %s AND usado = FALSE",
                        (token_row['id'],)
                    ).rowcount:
                        desfecho = 'ok'
                    else:
                        desfecho = 'ja_usado'

            if desfecho in ('invalido', 'queimado'):
                rate_limit.registrar_falha(rate_limit.PAIS_2FA_IP, ip)
            if desfecho == 'invalido':
                return render_template("pais/verificar_2fa.html", erro="Código inválido. Verifique o email e tente novamente.")
            if desfecho == 'queimado':
                _limpar_sessao_temp()
                return render_template("pais/verificar_2fa.html",
                                       erro="Código incorreto muitas vezes. Faça login novamente para receber um novo código.",
                                       expirado=True)
            if desfecho == 'expirado':
                return render_template("pais/verificar_2fa.html", erro="Código expirado. Faça login novamente.", expirado=True)
            if desfecho == 'ja_usado':
                return render_template("pais/verificar_2fa.html",
                                       erro="Este código já foi utilizado. Faça login novamente.",
                                       expirado=True)

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
        try:
            filhos = get_school_sql_directory().get_students_for_guardian_email(session['pai_email'])
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao carregar dashboard: {e}")
            filhos = []
        for f in filhos:
            f['foto_src'] = f.get('foto_url')
        return render_template("pais/dashboard.html", filhos=filhos, nome=session['pai_nome'])

    # ==================== SOLICITAR SAÍDA ====================

    @app.route("/pais/solicitar/<ra>", methods=["GET", "POST"])
    @pai_required
    def pais_solicitar(ra):
        email = session['pai_email']

        # Garante que este aluno pertence ao responsável logado (via consulta ao vivo)
        try:
            filhos = get_school_sql_directory().get_students_for_guardian_email(email)
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao solicitar saída: {e}")
            filhos = []
        aluno = next((f for f in filhos if f['ra'] == ra), None)

        if not aluno:
            flash("Aluno não encontrado ou sem vínculo com sua conta.", "error")
            return redirect("/pais/dashboard")

        if request.method == "POST":
            data_solicitada = request.form.get("data_solicitada", "")
            horario = request.form.get("horario", "").strip()
            motivo = request.form.get("motivo", "").strip()
            tipo_saida = request.form.get("tipo_saida", "").strip()
            acompanhante = request.form.get("acompanhante", "").strip()

            today = datetime.now().strftime("%Y-%m-%d")
            if not data_solicitada or not motivo:
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today,
                                       erro="Preencha a data e o motivo.")
            if not horario_valido(horario):
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today,
                                       erro="Informe o horário da saída no formato HH:MM.")
            if not data_valida(data_solicitada):
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today,
                                       erro="Data inválida. Use o formato AAAA-MM-DD.")
            # A comparação é textual, então só é confiável com HH:MM zero-preenchido — que é o
            # que horario_valido() acabou de garantir ("9:00" passaria despercebido sem ela).
            if data_solicitada == today and horario <= datetime.now().strftime("%H:%M"):
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today,
                                       erro="O horário informado já passou. Escolha um horário futuro.")
            if not tipo_saida:
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today,
                                       erro="Selecione o tipo de saída (Sozinho ou Acompanhado).")
            if tipo_saida == "acompanhado" and not acompanhante:
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today,
                                       erro="Informe com quem o aluno vai sair.")

            with get_db() as conn:
                # Verifica se já existe solicitação aguardando para este aluno nesta data
                existente = conn.execute(
                    "SELECT id FROM solicitacoes_saida WHERE ra = %s AND data_solicitada = %s AND status = 'aguardando'",
                    (ra, data_solicitada)
                ).fetchone()
                if existente:
                    return render_template("pais/solicitar_saida.html", aluno=aluno, today=today,
                                           erro="Já existe uma solicitação aguardando para este aluno nesta data.")

                conn.execute(
                    """INSERT INTO solicitacoes_saida
                       (responsavel_id, ra, data_solicitada, horario_solicitado, motivo,
                        tipo_saida, acompanhante, status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'aguardando')""",
                    (session['pai_id'], ra, data_solicitada, horario, motivo,
                     tipo_saida, acompanhante or None)
                )

            flash(f"Solicitação de saída para {aluno['nome']} enviada com sucesso! Aguarde aprovação da escola.", "success")
            return redirect("/pais/minhas_solicitacoes")

        today = datetime.now().strftime("%Y-%m-%d")
        return render_template("pais/solicitar_saida.html", aluno=aluno, today=today)

    # ==================== EDITAR SOLICITAÇÃO ====================

    @app.route("/pais/editar_solicitacao/<int:sol_id>", methods=["GET", "POST"])
    @pai_required
    def pais_editar_solicitacao(sol_id):
        with get_db() as conn:
            sol_row = conn.execute(
                """SELECT ss.*, a.nome AS nome_legado, a.turma AS turma_legado, a.serie AS serie_legado,
                          s.status AS saida_status
                   FROM solicitacoes_saida ss
                   LEFT JOIN alunos a ON a.id = ss.aluno_id
                   LEFT JOIN saidas s ON (
                       (ss.ra IS NOT NULL AND s.ra = ss.ra AND s.data_saida = ss.data_solicitada)
                       OR (ss.aluno_id IS NOT NULL AND s.aluno = ss.aluno_id AND s.data_saida = ss.data_solicitada)
                   )
                   WHERE ss.id = %s AND ss.responsavel_id = %s""",
                (sol_id, session['pai_id'])
            ).fetchone()

        if not sol_row:
            flash("Solicitação não encontrada.", "error")
            return redirect("/pais/minhas_solicitacoes")

        sol = _resolver_dados_solicitacoes([sol_row])[0]

        if sol['status'] == 'rejeitado':
            flash("Solicitações rejeitadas não podem ser editadas. Envie uma nova solicitação.", "error")
            return redirect("/pais/minhas_solicitacoes")

        if sol['status'] == 'aprovado' and sol['saida_status'] in ('concluida', 'nao_realizada'):
            flash("Esta saída já passou da data e não pode mais ser editada.", "error")
            return redirect("/pais/minhas_solicitacoes")

        aluno = {"nome": sol['aluno_nome'], "turma": sol['turma'], "serie": sol['serie']}
        today = datetime.now().strftime("%Y-%m-%d")

        if request.method == "POST":
            data_solicitada = request.form.get("data_solicitada", "")
            horario = request.form.get("horario", "").strip()
            motivo = request.form.get("motivo", "").strip()
            tipo_saida = request.form.get("tipo_saida", "").strip()
            acompanhante = request.form.get("acompanhante", "").strip()

            if not data_solicitada or not motivo:
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today, sol=sol,
                                       voltar_url="/pais/minhas_solicitacoes",
                                       erro="Preencha a data e o motivo.")
            if not horario_valido(horario):
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today, sol=sol,
                                       voltar_url="/pais/minhas_solicitacoes",
                                       erro="Informe o horário da saída no formato HH:MM.")
            if not data_valida(data_solicitada):
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today, sol=sol,
                                       voltar_url="/pais/minhas_solicitacoes",
                                       erro="Data inválida. Use o formato AAAA-MM-DD.")
            if data_solicitada == today and horario <= datetime.now().strftime("%H:%M"):
                return render_template("pais/solicitar_saida.html", aluno=aluno, today=today, sol=sol,
                                       voltar_url="/pais/minhas_solicitacoes",
                                       erro="O horário informado já passou. Escolha um horário futuro.")
            if not tipo_saida:
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
                    if sol['ra']:
                        conn.execute(
                            "DELETE FROM saidas WHERE ra=%s AND data_saida=%s AND status='pendente'",
                            (sol['ra'], sol['data_solicitada'])
                        )
                    else:
                        conn.execute(
                            "DELETE FROM saidas WHERE aluno=%s AND data_saida=%s AND status='pendente'",
                            (sol['aluno_id'], sol['data_solicitada'])
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
                        if sol['ra']:
                            conn.execute(
                                "UPDATE saidas SET motivo=%s WHERE ra=%s AND data_saida=%s AND status='pendente'",
                                (motivo, sol['ra'], sol['data_solicitada'])
                            )
                        else:
                            conn.execute(
                                "UPDATE saidas SET motivo=%s WHERE aluno=%s AND data_saida=%s AND status='pendente'",
                                (motivo, sol['aluno_id'], sol['data_solicitada'])
                            )
                    flash("Solicitação atualizada com sucesso!", "success")

            return redirect("/pais/minhas_solicitacoes")

        return render_template("pais/solicitar_saida.html", aluno=aluno, today=today, sol=sol,
                               voltar_url="/pais/minhas_solicitacoes")

    # ==================== HISTÓRICO DE SAÍDAS DO FILHO ====================

    @app.route("/pais/historico/<ra>")
    @pai_required
    def pais_historico_filho(ra):
        try:
            filhos = get_school_sql_directory().get_students_for_guardian_email(session['pai_email'])
        except Exception as e:
            _log.warning(f"[SCHOOL_SQL] Erro ao carregar histórico: {e}")
            filhos = []
        aluno = next((f for f in filhos if f['ra'] == ra), None)

        if not aluno:
            flash("Aluno não encontrado ou sem vínculo com sua conta.", "error")
            return redirect("/pais/dashboard")

        with get_db() as conn:
            historico = conn.execute(
                """SELECT data_saida, horario, tipo_saida, acompanhante, status
                   FROM saidas
                   WHERE ra = %s
                   ORDER BY data_saida DESC, horario DESC""",
                (ra,)
            ).fetchall()

        return render_template("pais/historico_filho.html", aluno=aluno, historico=historico)

    # ==================== HISTÓRICO DO PAI ====================

    @app.route("/pais/minhas_solicitacoes")
    @pai_required
    def pais_minhas_solicitacoes():
        with get_db() as conn:
            rows = conn.execute(
                """SELECT ss.id, ss.data_solicitada, ss.horario_solicitado, ss.motivo,
                          ss.status, ss.criado_em, ss.ra,
                          a.nome AS nome_legado, a.turma AS turma_legado, a.serie AS serie_legado,
                          s.status AS saida_status
                   FROM solicitacoes_saida ss
                   LEFT JOIN alunos a ON a.id = ss.aluno_id
                   LEFT JOIN saidas s ON (
                       (ss.ra IS NOT NULL AND s.ra = ss.ra AND s.data_saida = ss.data_solicitada)
                       OR (ss.aluno_id IS NOT NULL AND s.aluno = ss.aluno_id AND s.data_saida = ss.data_solicitada)
                   )
                   WHERE ss.responsavel_id = %s
                   ORDER BY ss.criado_em DESC""",
                (session['pai_id'],)
            ).fetchall()
        solicitacoes = _resolver_dados_solicitacoes(rows)
        return render_template("pais/minhas_solicitacoes.html", solicitacoes=solicitacoes, nome=session['pai_nome'])

    # ==================== ESQUECI A SENHA ====================

    @app.route("/pais/esqueci_senha", methods=["GET", "POST"])
    def pais_esqueci_senha():
        mensagem = ""
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            link_para_enviar = None

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
                        (resp['id'], token, expires_at)
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
                enviar_email_async(
                    email, "Redefinição de senha — SecureEdu", corpo,
                    fallback_log=f"[FALLBACK] Link de reset para {email}: {link_para_enviar}",
                )

            mensagem = "Se este email estiver cadastrado, você receberá um link em breve."

        return render_template("pais/forgot.html", mensagem=mensagem)

    @app.route("/pais/resetar_senha/<token>", methods=["GET", "POST"])
    def pais_resetar_senha(token):
        erro = ""
        responsavel_id = None

        with get_db() as conn:
            registro = conn.execute(
                "SELECT responsavel_id, expires_at FROM reset_tokens_pais WHERE token = %s", (token,)
            ).fetchone()

            if registro:
                expires = registro['expires_at']
                if not isinstance(expires, datetime):
                    expires = datetime.fromisoformat(str(expires))
                if datetime.now() <= expires:
                    responsavel_id = registro['responsavel_id']
                else:
                    conn.execute("DELETE FROM reset_tokens_pais WHERE token = %s", (token,))
                    erro = "Link expirado. Solicite um novo."
            else:
                erro = "Link inválido."

        if request.method == "POST" and responsavel_id:
            nova = request.form.get("senha", "")
            confirma = request.form.get("confirma", "")
            erro_senha = verificar_forca(nova)
            if erro_senha:
                erro = erro_senha + '.'
            elif nova != confirma:
                erro = "As senhas não coincidem."
            else:
                with get_db() as conn:
                    conn.execute(
                        "UPDATE responsaveis SET password_hash = %s WHERE id = %s",
                        (generate_password_hash(nova, method='pbkdf2:sha256'), responsavel_id)
                    )
                    conn.execute("DELETE FROM reset_tokens_pais WHERE token = %s", (token,))
                return redirect("/pais/login?resetado=1")

        return render_template("pais/reset.html", erro=erro, token=token if responsavel_id else None)

    # ==================== LOGOUT ====================

    # POST apenas — mesmo motivo do /logout dos funcionários.
    @app.route("/pais/logout", methods=["POST"])
    def pais_logout():
        session.pop('pai_id', None)
        session.pop('pai_email', None)
        session.pop('pai_nome', None)
        _limpar_sessao_temp()
        return redirect("/pais/login")
