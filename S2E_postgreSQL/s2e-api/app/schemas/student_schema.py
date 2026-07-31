from datetime import datetime
from typing import Optional, List
import json

class StudentSchema:
    """Schema para validação e serialização de alunos"""
    
    @staticmethod
    def validate(data: dict) -> tuple[bool, Optional[str], dict]:
        """Valida dados de um aluno"""
        errors = []
        validated = {}
        
        # Campos obrigatórios
        nome = data.get('nome', '').strip()
        if not nome:
            errors.append('Nome é obrigatório')
        else:
            validated['nome'] = nome
        
        turma = data.get('turma', '').strip()
        if not turma:
            errors.append('Turma é obrigatória')
        else:
            validated['turma'] = turma
        
        serie = data.get('serie', '').strip()
        if not serie:
            errors.append('Série é obrigatória')
        else:
            validated['serie'] = serie
        
        # Campos opcionais
        validated['saida_seg'] = data.get('saida_seg', '')
        validated['saida_ter'] = data.get('saida_ter', '')
        validated['saida_qua'] = data.get('saida_qua', '')
        validated['saida_qui'] = data.get('saida_qui', '')
        validated['saida_sex'] = data.get('saida_sex', '')
        validated['responsaveis'] = data.get('responsaveis', '')
        validated['telefone'] = data.get('telefone', '')
        validated['email_responsavel'] = data.get('email_responsavel', '').lower()
        validated['data_nascimento'] = data.get('data_nascimento', '')
        validated['alergias'] = data.get('alergias', '')
        validated['observacoes'] = data.get('observacoes', '')
        validated['foto_path'] = data.get('foto_path')
        
        # Validação de email
        if validated['email_responsavel'] and '@' not in validated['email_responsavel']:
            errors.append('Email inválido')
        
        # Validação de data
        if validated['data_nascimento']:
            try:
                datetime.strptime(validated['data_nascimento'], '%Y-%m-%d')
            except ValueError:
                errors.append('Data de nascimento inválida (use YYYY-MM-DD)')
        
        if errors:
            return False, '; '.join(errors), {}
        
        return True, None, validated
    
    @staticmethod
    def serialize(student: dict) -> dict:
        """Serializa um aluno para JSON"""
        return {
            'id': student.get('id'),
            'nome': student.get('nome'),
            'turma': student.get('turma'),
            'serie': student.get('serie'),
            'saida_seg': student.get('saida_seg'),
            'saida_ter': student.get('saida_ter'),
            'saida_qua': student.get('saida_qua'),
            'saida_qui': student.get('saida_qui'),
            'saida_sex': student.get('saida_sex'),
            'responsaveis': student.get('responsaveis'),
            'telefone': student.get('telefone'),
            'email_responsavel': student.get('email_responsavel'),
            'data_nascimento': student.get('data_nascimento'),
            'alergias': student.get('alergias'),
            'observacoes': student.get('observacoes'),
            'foto_url': f"/uploads/{student.get('foto_path')}" if student.get('foto_path') else None
        }
    
    @staticmethod
    def serialize_list(students: list) -> list:
        """Serializa uma lista de alunos"""
        return [StudentSchema.serialize(s) for s in students]