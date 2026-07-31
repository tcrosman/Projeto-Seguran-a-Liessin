from app.repositories.base_repositories import BaseRepository
from app.core.database import get_db
from typing import List, Dict, Optional
from datetime import datetime

class DepartureRepository(BaseRepository):
    """Repositório para saídas"""
    
    def __init__(self):
        super().__init__('saidas')
    
    def create(self, data: Dict) -> int:
        """Cria um novo registro de saída"""
        with get_db() as conn:
            row = conn.execute("""
                INSERT INTO saidas (
                    aluno, data_saida, horario, motivo,
                    responsavel_escola, tipo_saida, acompanhante, documento_path, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (
                data.get('aluno'), data.get('data_saida'), data.get('horario'),
                data.get('motivo'), data.get('responsavel_escola'),
                data.get('tipo_saida'), data.get('acompanhante'),
                data.get('documento_path'), 'pendente'
            )).fetchone()
            return row['id']

    def update(self, id: int, data: Dict) -> bool:
        """Atualiza uma saída pendente"""
        with get_db() as conn:
            cursor = conn.execute("""
                UPDATE saidas SET
                    horario=%s, motivo=%s, responsavel_escola=%s, tipo_saida=%s, acompanhante=%s
                WHERE id=%s AND status='pendente'
            """, (
                data.get('horario'), data.get('motivo'), data.get('responsavel_escola'),
                data.get('tipo_saida'), data.get('acompanhante'), id
            ))
            return cursor.rowcount > 0

    def complete(self, id: int, usuario_id: int) -> bool:
        """Marca saída como concluída/autorizada"""
        with get_db() as conn:
            cursor = conn.execute("""
                UPDATE saidas SET status='concluida', usuario_autorizou=%s
                WHERE id=%s AND status='pendente'
            """, (usuario_id, id))
            return cursor.rowcount > 0

    def get_by_date(self, date: str, search: str = '') -> List[Dict]:
        """Retorna saídas de uma data específica"""
        with get_db() as conn:
            if search:
                rows = conn.execute("""
                    SELECT s.id, a.nome as aluno, s.horario, s.motivo, s.responsavel_escola,
                           s.tipo_saida, s.acompanhante, s.documento_path, s.status,
                           a.serie, a.turma, a.foto_path
                    FROM saidas s
                    JOIN alunos a ON s.aluno = a.id
                    WHERE s.data_saida = %s AND a.nome ILIKE %s
                    ORDER BY s.horario ASC
                """, (date, f'%{search}%')).fetchall()
            else:
                rows = conn.execute("""
                    SELECT s.id, a.nome as aluno, s.horario, s.motivo, s.responsavel_escola,
                           s.tipo_saida, s.acompanhante, s.documento_path, s.status,
                           a.serie, a.turma, a.foto_path
                    FROM saidas s
                    JOIN alunos a ON s.aluno = a.id
                    WHERE s.data_saida = %s
                    ORDER BY s.horario ASC
                """, (date,)).fetchall()
            return [dict(row) for row in rows]

    def get_pending_count(self, aluno_id: int, data_saida: str) -> int:
        """Verifica se aluno já tem saída pendente para uma data"""
        with get_db() as conn:
            row = conn.execute("""
                SELECT COUNT(*) as total FROM saidas
                WHERE aluno = %s AND data_saida = %s AND status = 'pendente'
            """, (aluno_id, data_saida)).fetchone()
            return row['total'] if row else 0

    def get_historic(self, aluno_id: int) -> List[Dict]:
        """Retorna histórico de saídas de um aluno"""
        with get_db() as conn:
            rows = conn.execute("""
                SELECT data_saida, horario, motivo, responsavel_escola,
                       tipo_saida, acompanhante, documento_path, status
                FROM saidas
                WHERE aluno = %s AND status = 'concluida'
                ORDER BY data_saida DESC, horario DESC
            """, (aluno_id,)).fetchall()
            return [dict(row) for row in rows]

    def get_by_filters(self, filters: Dict) -> List[Dict]:
        """Busca saídas com múltiplos filtros"""
        query = """
            SELECT a.id, a.nome, a.turma, a.serie, s.data_saida, s.horario, s.motivo,
                   s.responsavel_escola, s.tipo_saida, s.acompanhante, s.status, a.foto_path
            FROM saidas s
            JOIN alunos a ON s.aluno = a.id
            WHERE 1=1
        """
        params = []

        if filters.get('nome'):
            query += " AND a.nome ILIKE %s"
            params.append(f"%{filters['nome']}%")
        if filters.get('data_ini'):
            query += " AND s.data_saida >= %s"
            params.append(filters['data_ini'])
        if filters.get('data_fim'):
            query += " AND s.data_saida <= %s"
            params.append(filters['data_fim'])
        if filters.get('turma'):
            query += " AND a.turma = %s"
            params.append(filters['turma'])
        if filters.get('serie'):
            query += " AND a.serie = %s"
            params.append(filters['serie'])

        query += " ORDER BY s.data_saida DESC, s.horario DESC"

        with get_db() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def cleanup_old(self, days: int = 30) -> int:
        """Remove saídas concluídas mais antigas que X dias"""
        with get_db() as conn:
            cursor = conn.execute(f"""
                DELETE FROM saidas
                WHERE status='concluida'
                AND data_saida < TO_CHAR(CURRENT_DATE - INTERVAL '{days} days', 'YYYY-MM-DD')
            """)
            return cursor.rowcount