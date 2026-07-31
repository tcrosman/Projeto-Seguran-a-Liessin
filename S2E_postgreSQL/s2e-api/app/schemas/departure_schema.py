from datetime import datetime
from typing import Optional, Dict, List

class DepartureSchema:
    """Schema para validação e serialização de saídas"""
    
    @staticmethod
    def validate(data: dict) -> tuple[bool, Optional[str], dict]:
        """Valida dados de uma saída"""
        errors = []
        validated = {}
        
        # Campos obrigatórios
        aluno_id = data.get('aluno_id')
        if not aluno_id:
            errors.append('ID do aluno é obrigatório')
        else:
            try:
                validated['aluno_id'] = int(aluno_id)
            except ValueError:
                errors.append('ID do aluno inválido')
        
        horario = data.get('horario', '').strip()
        if not horario:
            errors.append('Horário é obrigatório')
        else:
            validated['horario'] = horario
        
        motivo = data.get('motivo', '').strip()
        if not motivo:
            errors.append('Motivo é obrigatório')
        else:
            validated['motivo'] = motivo
        
        responsavel_escola = data.get('responsavel_escola', '').strip()
        if not responsavel_escola:
            errors.append('Responsável da escola é obrigatório')
        else:
            validated['responsavel_escola'] = responsavel_escola
        
        tipo_saida = data.get('tipo_saida', '').strip()
        if tipo_saida not in ['sozinho', 'acompanhado']:
            errors.append('Tipo de saída deve ser "sozinho" ou "acompanhado"')
        else:
            validated['tipo_saida'] = tipo_saida
        
        # Campos opcionais
        validated['data_saida'] = data.get('data_saida', datetime.now().strftime("%Y-%m-%d"))
        validated['acompanhante'] = data.get('acompanhante') if tipo_saida == 'acompanhado' else None
        validated['documento_path'] = data.get('documento_path')
        
        # Validação de data
        try:
            datetime.strptime(validated['data_saida'], '%Y-%m-%d')
        except ValueError:
            errors.append('Data inválida (use YYYY-MM-DD)')
        
        # Validação de horário
        if validated['horario']:
            try:
                datetime.strptime(validated['horario'], '%H:%M')
            except ValueError:
                errors.append('Horário inválido (use HH:MM)')
        
        if errors:
            return False, '; '.join(errors), {}
        
        return True, None, validated
    
    @staticmethod
    def serialize(departure: dict, student_name: str = None) -> dict:
        """Serializa uma saída para JSON"""
        result = {
            'id': departure.get('id'),
            'aluno_id': departure.get('aluno'),
            'data_saida': departure.get('data_saida'),
            'horario': departure.get('horario'),
            'motivo': departure.get('motivo'),
            'responsavel_escola': departure.get('responsavel_escola'),
            'tipo_saida': departure.get('tipo_saida'),
            'acompanhante': departure.get('acompanhante'),
            'status': departure.get('status'),
            'documento_url': f"/uploads/{departure.get('documento_path')}" if departure.get('documento_path') else None
        }
        
        if student_name:
            result['aluno_nome'] = student_name
        
        return result
    
    @staticmethod
    def serialize_list(departures: list, students: dict = None) -> list:
        """Serializa uma lista de saídas"""
        result = []
        for d in departures:
            student_name = None
            if students and d.get('aluno') in students:
                student_name = students[d.get('aluno')]
            result.append(DepartureSchema.serialize(d, student_name))
        return result