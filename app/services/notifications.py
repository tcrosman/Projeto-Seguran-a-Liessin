from app.core.eamail import enviar_email
from app.repositories.students import StudentRepository
from app.repositories.departures import DepartureRepository
from typing import Dict, List, Optional
from datetime import datetime

class NotificationService:
    """Serviço para notificações (email, webhook)"""
    
    def __init__(self):
        self.student_repo = StudentRepository()
        self.departure_repo = DepartureRepository()
    
    def notify_departure_authorized(self, departure_id: int, webhook_url: str = None) -> bool:
        """Notifica que uma saída foi autorizada"""
        departure = self.departure_repo.get_by_id(departure_id)
        if not departure:
            return False
        
        student = self.student_repo.get_by_id(departure['aluno'])
        if not student:
            return False
        
        # Enviar email
        email_sent = False
        if student.get('email_responsavel'):
            body = self._build_notification_body(student, departure)
            email_sent = enviar_email(
                student['email_responsavel'],
                f"Saída autorizada - {student['nome']}",
                body
            )
        
        # Enviar webhook (opcional)
        webhook_sent = False
        if webhook_url:
            webhook_sent = self._send_webhook(webhook_url, {
                'event': 'departure_authorized',
                'departure_id': departure_id,
                'student_id': student['id'],
                'student_name': student['nome'],
                'data_saida': departure['data_saida'],
                'horario': departure['horario'],
                'timestamp': datetime.now().isoformat()
            })
        
        return email_sent or webhook_sent
    
    def _build_notification_body(self, student: Dict, departure: Dict) -> str:
        """Constrói corpo do email de notificação"""
        tipo_texto = 'Acompanhado(a)' if departure['tipo_saida'] == 'acompanhado' else 'Sozinho(a)'
        acompanhante_info = f" - {departure['acompanhante']}" if departure.get('acompanhante') else ''
        
        return f"""
        <h2>Saída autorizada</h2>
        <p>O aluno <strong>{student['nome']}</strong> teve a saída antecipada autorizada.</p>
        <ul>
            <li>Data: {departure['data_saida']}</li>
            <li>Horário: {departure['horario']}</li>
            <li>Motivo: {departure['motivo']}</li>
            <li>Responsável na escola: {departure['responsavel_escola']}</li>
            <li>Tipo de saída: {tipo_texto}{acompanhante_info}</li>
        </ul>
        """
    
    def _send_webhook(self, url: str, data: Dict) -> bool:
        """Envia webhook para URL configurada"""
        import requests
        try:
            response = requests.post(url, json=data, timeout=5)
            return response.status_code == 200
        except Exception as e:
            print(f"Webhook error: {e}")
            return False