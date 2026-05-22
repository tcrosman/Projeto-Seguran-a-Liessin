from flask import request, jsonify, session
from app.api.middleware import login_required, admin_required
from app.core.database import get_db
from app.core.validators import normalizar_serie
from app.config import Config
from datetime import datetime
import secrets
import json

def register_rest_routes(app):
    """Registra todas as rotas da API REST"""
    
    # ==================== AUTENTICAÇÃO API ====================
    @app.route("/api/v1/login", methods=["POST"])
    def api_login():
        """Autentica e retorna token de acesso"""
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
        
        if not username or not password:
            return jsonify({'error': 'Usuário e senha obrigatórios'}), 400
        
        with get_db() as conn:
            user = conn.execute(
                "SELECT id, role, username FROM usuarios WHERE username=? AND password=?",
                (username, password)
            ).fetchone()
        
        if user:
            # Gerar token simples (em produção usar JWT)
            token = secrets.token_urlsafe(32)
            return jsonify({
                'success': True,
                'token': token,
                'user_id': user['id'],
                'role': user['role'],
                'username': user['username']
            })
        
        return jsonify({'error': 'Credenciais inválidas'}), 401
    
    # ==================== ALUNOS ====================
    @app.route("/api/v1/students", methods=["GET"])
    def api_get_students():
        """Lista todos os alunos"""
        with get_db() as conn:
            rows = conn.execute("""
                SELECT id, nome, turma, serie, responsaveis, telefone, 
                       email_responsavel, data_nascimento, alergias, observacoes
                FROM alunos ORDER BY serie, turma, nome
            """).fetchall()
            students = [dict(row) for row in rows]
        return jsonify(students)
    
    @app.route("/api/v1/students/<int:student_id>", methods=["GET"])
    def api_get_student(student_id):
        """Busca aluno por ID"""
        with get_db() as conn:
            row = conn.execute("""
                SELECT id, nome, turma, serie, responsaveis, telefone, 
                       email_responsavel, data_nascimento, alergias, observacoes
                FROM alunos WHERE id = ?
            """, (student_id,)).fetchone()
            
            if not row:
                return jsonify({'error': 'Aluno não encontrado'}), 404
            
            return jsonify(dict(row))
    
    @app.route("/api/v1/students/search", methods=["GET"])
    def api_search_students():
        """Busca alunos por nome"""
        nome = request.args.get('nome', '')
        if not nome:
            return jsonify({'error': 'Parâmetro "nome" obrigatório'}), 400
        
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, nome, turma, serie FROM alunos WHERE nome LIKE ? LIMIT 50",
                (f'%{nome}%',)
            ).fetchall()
            return jsonify([dict(row) for row in rows])
    
    @app.route("/api/v1/students", methods=["POST"])
    def api_create_student():
        """Cadastra novo aluno (requer admin)"""
        # Verificar admin via header
        # Em produção: validar token JWT
        
        data = request.get_json()
        
        required = ['nome', 'turma', 'serie']
        for field in required:
            if not data.get(field):
                return jsonify({'error': f'Campo {field} obrigatório'}), 400
        
        # Normalizar série
        serie_normalizada = normalizar_serie(data['serie'])
        if not serie_normalizada:
            return jsonify({'error': f"Série '{data['serie']}' não reconhecida"}), 400
        
        with get_db() as conn:
            cursor = conn.execute("""
                INSERT INTO alunos (nome, turma, serie, responsaveis, telefone, 
                                   email_responsavel, data_nascimento, alergias, observacoes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data['nome'], data['turma'], serie_normalizada,
                data.get('responsaveis', ''), data.get('telefone', ''),
                data.get('email_responsavel', ''), data.get('data_nascimento', ''),
                data.get('alergias', ''), data.get('observacoes', '')
            ))
            student_id = cursor.lastrowid
        
        return jsonify({'success': True, 'id': student_id}), 201
    
    # ==================== SAÍDAS ====================
    @app.route("/api/v1/departures", methods=["POST"])
    def api_register_departure():
        """Registra uma nova saída"""
        data = request.get_json()
        
        required = ['aluno_id', 'horario', 'motivo', 'responsavel_escola', 'tipo_saida']
        for field in required:
            if not data.get(field):
                return jsonify({'error': f'Campo {field} obrigatório'}), 400
        
        aluno_id = data['aluno_id']
        data_saida = data.get('data_saida', datetime.now().strftime("%Y-%m-%d"))
        
        # Verificar se aluno existe
        with get_db() as conn:
            aluno = conn.execute("SELECT id FROM alunos WHERE id = ?", (aluno_id,)).fetchone()
            if not aluno:
                return jsonify({'error': 'Aluno não encontrado'}), 404
            
            # Verificar saída pendente
            pendente = conn.execute("""
                SELECT COUNT(*) as total FROM saidas 
                WHERE aluno = ? AND data_saida = ? AND status = 'pendente'
            """, (aluno_id, data_saida)).fetchone()
            
            if pendente['total'] > 0:
                return jsonify({'error': 'Aluno já tem saída pendente para esta data'}), 409
            
            # Registrar saída
            cursor = conn.execute("""
                INSERT INTO saidas (aluno, data_saida, horario, motivo, responsavel_escola,
                                   tipo_saida, acompanhante, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pendente')
            """, (
                aluno_id, data_saida, data['horario'], data['motivo'],
                data['responsavel_escola'], data['tipo_saida'],
                data.get('acompanhante')
            ))
            departure_id = cursor.lastrowid
        
        return jsonify({'success': True, 'id': departure_id}), 201
    
    @app.route("/api/v1/departures", methods=["GET"])
    def api_get_departures():
        """Lista saídas com filtros"""
        data = request.args.get('data', datetime.now().strftime("%Y-%m-%d"))
        status = request.args.get('status', '')
        
        query = """
            SELECT s.id, a.nome, s.horario, s.motivo, s.responsavel_escola,
                   s.tipo_saida, s.acompanhante, s.status
            FROM saidas s
            JOIN alunos a ON s.aluno = a.id
            WHERE s.data_saida = ?
        """
        params = [data]
        
        if status:
            query += " AND s.status = ?"
            params.append(status)
        
        query += " ORDER BY s.horario ASC"
        
        with get_db() as conn:
            rows = conn.execute(query, params).fetchall()
            return jsonify([dict(row) for row in rows])
    
    @app.route("/api/v1/departures/<int:departure_id>/complete", methods=["PUT"])
    def api_complete_departure(departure_id):
        """Autoriza uma saída"""
        with get_db() as conn:
            # Buscar dados para email
            info = conn.execute("""
                SELECT s.aluno, s.data_saida, s.horario, s.motivo, s.responsavel_escola,
                       a.email_responsavel, a.nome
                FROM saidas s
                JOIN alunos a ON s.aluno = a.id
                WHERE s.id = ? AND s.status = 'pendente'
            """, (departure_id,)).fetchone()
            
            if not info:
                return jsonify({'error': 'Saída não encontrada ou já autorizada'}), 404
            
            # Atualizar status
            conn.execute(
                "UPDATE saidas SET status = 'concluida' WHERE id = ?",
                (departure_id,)
            )
            
            # Em produção: enviar email aqui
        
        return jsonify({'success': True, 'message': 'Saída autorizada'})
    
    # ==================== HISTÓRICO ====================
    @app.route("/api/v1/students/<int:student_id>/history", methods=["GET"])
    def api_student_history(student_id):
        """Histórico de saídas de um aluno"""
        with get_db() as conn:
            # Verificar se aluno existe
            aluno = conn.execute("SELECT nome FROM alunos WHERE id = ?", (student_id,)).fetchone()
            if not aluno:
                return jsonify({'error': 'Aluno não encontrado'}), 404
            
            # Buscar histórico
            rows = conn.execute("""
                SELECT data_saida, horario, motivo, responsavel_escola, tipo_saida, 
                       acompanhante, status
                FROM saidas
                WHERE aluno = ? AND status = 'concluida'
                ORDER BY data_saida DESC, horario DESC
            """, (student_id,)).fetchall()
            
            return jsonify({
                'student_id': student_id,
                'student_name': aluno['nome'],
                'history': [dict(row) for row in rows]
            })
    
    # ==================== HEALTH CHECK ====================
    @app.route("/api/v1/health", methods=["GET"])
    def api_health():
        """Verifica se a API está funcionando"""
        try:
            with get_db() as conn:
                conn.execute("SELECT 1")
            return jsonify({
                'status': 'ok',
                'timestamp': datetime.now().isoformat(),
                'version': '1.0.0'
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'error': str(e)
            }), 500
    
    # ==================== ESTATÍSTICAS ====================
    @app.route("/api/v1/stats", methods=["GET"])
    def api_stats():
        """Estatísticas gerais do sistema"""
        with get_db() as conn:
            total_alunos = conn.execute("SELECT COUNT(*) as total FROM alunos").fetchone()['total']
            total_saidas = conn.execute("SELECT COUNT(*) as total FROM saidas").fetchone()['total']
            saidas_hoje = conn.execute(
                "SELECT COUNT(*) as total FROM saidas WHERE data_saida = date('now')"
            ).fetchone()['total']
            
            # Saídas por mês
            saidas_por_mes = conn.execute("""
                SELECT strftime('%Y-%m', data_saida) as mes, COUNT(*) as total
                FROM saidas
                GROUP BY mes
                ORDER BY mes DESC
                LIMIT 6
            """).fetchall()
        
        return jsonify({
            'total_students': total_alunos,
            'total_departures': total_saidas,
            'departures_today': saidas_hoje,
            'departures_by_month': [dict(row) for row in saidas_por_mes]
        })
    
    return app