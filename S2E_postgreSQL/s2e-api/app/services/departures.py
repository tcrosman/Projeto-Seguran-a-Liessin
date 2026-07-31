from app.services.base_services import BaseService
from app.repositories.departure_repo import DepartureRepository
from app.repositories.student_repo import StudentRepository
from app.core.audit_logger import log_operacao
from app.core.mailer import enviar_email
from app.schemas.departure_schema import DepartureSchema
from app.core.exceptions import ValidationError, NotFoundError
from typing import Dict, Optional, List
from datetime import datetime

class DepartureService(BaseService):
    """Serviço para gerenciar saídas"""
    
    def __init__(self):
        super().__init__(DepartureRepository())
        self.student_repo = StudentRepository()
    
    def create(self, data: Dict, usuario_id: int, username: str) -> int:
        """Registra uma nova saída"""
        # Validar dados
        valid, error, validated = DepartureSchema.validate(data)
        if not valid:
            raise ValidationError(error)
        
        aluno_id = validated['aluno_id']
        data_saida = validated['data_saida']
        
        # Verificar se aluno existe
        aluno = self.student_repo.get_by_id(aluno_id)
        if not aluno:
            raise NotFoundError(f"Aluno com ID {aluno_id} não encontrado")
        
        # Verificar se já tem saída pendente para esta data
        pending_count = self.repository.get_pending_count(aluno_id, data_saida)
        if pending_count > 0:
            raise ValidationError("Este aluno já tem uma saída pendente para esta data")
        
        # Criar saída
        validated['aluno'] = aluno_id  # Ajustar nome do campo
        departure_id = self.repository.create(validated)
        
        # Registrar log
        log_operacao(username, "REGISTROU SAÍDA", f"Aluno ID: {aluno_id}, Data: {data_saida}")
        
        return departure_id
    
    def update_pending(self, id: int, data: Dict, username: str) -> bool:
        """Atualiza uma saída pendente"""
        # Verificar se saída existe e está pendente
        saida = self.repository.get_by_id(id)
        if not saida:
            raise NotFoundError(f"Saída com ID {id} não encontrada")
        
        if saida['status'] != 'pendente':
            raise ValidationError("Apenas saídas pendentes podem ser editadas")
        
        # Atualizar
        success = self.repository.update(id, data)
        
        if success:
            log_operacao(username, "EDITOU SAÍDA", f"ID Saída: {id}")
        
        return success
    
    def complete(self, id: int, usuario_id: int, username: str) -> bool:
        """Autoriza/conclui uma saída e envia email"""
        # Verificar se saída existe e está pendente
        saida = self.repository.get_by_id(id)
        if not saida:
            raise NotFoundError(f"Saída com ID {id} não encontrada")
        
        if saida['status'] != 'pendente':
            raise ValidationError("Apenas saídas pendentes podem ser autorizadas")
        
        # Buscar dados do aluno para email
        aluno = self.student_repo.get_by_id(saida['aluno'])
        
        # Concluir saída
        success = self.repository.complete(id, usuario_id)
        
        if success:
            # Enviar email para o responsável
            if aluno and aluno.get('email_responsavel'):
                corpo = self._build_email_body(aluno, saida)
                enviar_email(aluno['email_responsavel'], 
                            f"Saída autorizada - {aluno['nome']}", 
                            corpo)
            
            log_operacao(username, "CONCLUIU SAÍDA", f"ID Saída: {id}")
        
        return success
    
    def _build_email_body(self, aluno: Dict, saida: Dict) -> str:
        """Constrói o corpo do email de notificação"""
        tipo_texto = 'Acompanhado(a)' if saida['tipo_saida'] == 'acompanhado' else 'Sozinho(a)'
        acompanhante_info = f" - {saida['acompanhante']}" if saida.get('acompanhante') else ''
        
        return f"""
        <h2>Saída autorizada</h2>
        <p>O aluno <strong>{aluno['nome']}</strong> teve a saída antecipada autorizada.</p>
        <ul>
            <li>Data: {saida['data_saida']}</li>
            <li>Horário: {saida['horario']}</li>
            <li>Motivo: {saida['motivo']}</li>
            <li>Responsável na escola: {saida['responsavel_escola']}</li>
            <li>Tipo de saída: {tipo_texto}{acompanhante_info}</li>
        </ul>
        """
    
    def get_by_date(self, date: str, search: str = '') -> List[Dict]:
        """Retorna saídas de uma data específica"""
        return self.repository.get_by_date(date, search)
    
    def get_with_filters(self, filters: Dict) -> List[Dict]:
        """Retorna saídas com filtros"""
        return self.repository.get_by_filters(filters)
    
    def get_student_history(self, student_id: int) -> Dict:
        """Retorna histórico completo de um aluno"""
        aluno = self.student_repo.get_by_id(student_id)
        if not aluno:
            raise NotFoundError(f"Aluno com ID {student_id} não encontrado")
        
        historico = self.repository.get_historic(student_id)
        
        return {
            'aluno': dict(aluno),
            'historico': historico
        }
    
    def cleanup_old(self, days: int = 30) -> int:
        """Remove saídas antigas"""
        return self.repository.cleanup_old(days)
    
    def get_stats(self) -> Dict:
        """Retorna estatísticas de saídas"""
        from app.core.database import get_db
        
        with get_db() as conn:
            # Total por status
            por_status = conn.execute("""
                SELECT status, COUNT(*) as total FROM saidas GROUP BY status
            """).fetchall()

            # Saídas hoje
            hoje = datetime.now().strftime("%Y-%m-%d")
            saidas_hoje = conn.execute(
                "SELECT COUNT(*) as total FROM saidas WHERE data_saida = %s", (hoje,)
            ).fetchone()['total']

            # Saídas por mês
            por_mes = conn.execute("""
                SELECT LEFT(data_saida, 7) as mes, COUNT(*) as total
                FROM saidas
                GROUP BY mes
                ORDER BY mes DESC
                LIMIT 6
            """).fetchall()
        
        return {
            'total': self.repository.count(),
            'por_status': [dict(row) for row in por_status],
            'hoje': saidas_hoje,
            'por_mes': [dict(row) for row in por_mes]
        }