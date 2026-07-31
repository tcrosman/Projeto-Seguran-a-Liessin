from app.services.base_services import BaseService
from app.repositories.student_repo import StudentRepository
from app.repositories.departure_repo import DepartureRepository
from app.core.audit_logger import log_aluno, log_operacao
from app.core.validators import normalizar_serie
from app.schemas.student_schema import StudentSchema
from app.core.exceptions import ValidationError, NotFoundError
from typing import Dict, Optional, List
from datetime import datetime

class StudentService(BaseService):
    """Serviço para gerenciar alunos"""
    
    def __init__(self):
        super().__init__(StudentRepository())
        self.departure_repo = DepartureRepository()
    
    def get_all_grouped(self) -> List[Dict]:
        """Retorna todos os alunos agrupados por série/turma"""
        return self.repository.get_all_grouped()
    
    def search_by_name(self, search: str) -> List[Dict]:
        """Busca alunos por nome"""
        if not search:
            return self.get_all_grouped()
        return self.repository.search_by_name(search)
    
    def create(self, data: Dict, usuario_id: int) -> int:
        """Cria um novo aluno"""
        # Validar dados
        valid, error, validated = StudentSchema.validate(data)
        if not valid:
            raise ValidationError(error)
        
        # Normalizar série
        serie_normalizada = normalizar_serie(validated['serie'])
        if not serie_normalizada:
            raise ValidationError(f"Série '{validated['serie']}' não reconhecida")
        validated['serie'] = serie_normalizada
        
        # Buscar horários padrão se não informados
        if not any([validated.get('saida_seg'), validated.get('saida_ter'), 
                    validated.get('saida_qua'), validated.get('saida_qui'), 
                    validated.get('saida_sex')]):
            from app.core.database import get_db
            with get_db() as conn:
                padrao = conn.execute(
                    "SELECT saida_seg, saida_ter, saida_qua, saida_qui, saida_sex FROM horarios_padrao WHERE serie = %s",
                    (serie_normalizada,)
                ).fetchone()
                if padrao:
                    validated['saida_seg'] = padrao['saida_seg'] or ''
                    validated['saida_ter'] = padrao['saida_ter'] or ''
                    validated['saida_qua'] = padrao['saida_qua'] or ''
                    validated['saida_qui'] = padrao['saida_qui'] or ''
                    validated['saida_sex'] = padrao['saida_sex'] or ''
        
        # Criar aluno
        aluno_id = self.repository.create(validated)
        
        # Registrar logs
        log_aluno(aluno_id, usuario_id, 'INSERT', None, validated)
        log_operacao(f"user_{usuario_id}", "CADASTROU ALUNO", f"ID: {aluno_id}, Nome: {validated['nome']}")
        
        return aluno_id
    
    def update(self, id: int, data: Dict, usuario_id: int) -> bool:
        """Atualiza um aluno existente"""
        # Buscar dados antigos
        old_data = self.repository.get_by_id(id)
        if not old_data:
            raise NotFoundError(f"Aluno com ID {id} não encontrado")
        
        # Validar novos dados
        valid, error, validated = StudentSchema.validate(data)
        if not valid:
            raise ValidationError(error)
        
        # Normalizar série
        if validated.get('serie'):
            serie_normalizada = normalizar_serie(validated['serie'])
            if not serie_normalizada:
                raise ValidationError(f"Série '{validated['serie']}' não reconhecida")
            validated['serie'] = serie_normalizada
        
        # Atualizar
        success = self.repository.update(id, validated)
        
        if success:
            # Registrar log
            log_aluno(id, usuario_id, 'UPDATE', dict(old_data), validated)
            log_operacao(f"user_{usuario_id}", "EDITOU ALUNO", f"ID: {id}")
        
        return success
    
    def delete(self, id: int, usuario_id: int) -> bool:
        """Remove um aluno (verifica pendências primeiro)"""
        # Verificar se tem saídas pendentes
        if self.repository.has_pending_departures(id):
            raise ValidationError("Não é possível remover aluno com saídas pendentes")
        
        # Buscar dados para log
        old_data = self.repository.get_by_id(id)
        if not old_data:
            raise NotFoundError(f"Aluno com ID {id} não encontrado")
        
        # Deletar
        success = self.repository.delete(id)
        
        if success:
            # Registrar log
            log_aluno(id, usuario_id, 'DELETE', dict(old_data), None)
            log_operacao(f"user_{usuario_id}", "DELETOU ALUNO", f"ID: {id}")
        
        return success
    
    def get_with_history(self, id: int) -> Dict:
        """Retorna aluno com histórico de saídas"""
        aluno = self.repository.get_by_id(id)
        if not aluno:
            raise NotFoundError(f"Aluno com ID {id} não encontrado")
        
        historico = self.departure_repo.get_historic(id)
        
        return {
            'aluno': dict(aluno),
            'historico': historico
        }
    
    def get_stats(self) -> Dict:
        """Retorna estatísticas de alunos"""
        total = self.repository.count()
        
        from app.core.database import get_db
        with get_db() as conn:
            # Alunos por série
            por_serie = conn.execute("""
                SELECT serie, COUNT(*) as total FROM alunos GROUP BY serie ORDER BY serie
            """).fetchall()
            
            # Alunos por turma
            por_turma = conn.execute("""
                SELECT turma, COUNT(*) as total FROM alunos GROUP BY turma ORDER BY turma
            """).fetchall()
        
        return {
            'total': total,
            'por_serie': [dict(row) for row in por_serie],
            'por_turma': [dict(row) for row in por_turma]
        }