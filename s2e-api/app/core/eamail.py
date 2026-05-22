import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import current_app

def enviar_email(destinatario, assunto, corpo_html):
    """Envia email via SMTP"""
    if not destinatario:
        return False
    
    smtp_user = current_app.config.get('SMTP_USER', '')
    smtp_password = current_app.config.get('SMTP_PASSWORD', '')
    
    if not smtp_user or not smtp_password:
        print("Email não configurado")
        return False
    
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = assunto
        msg['From'] = smtp_user
        msg['To'] = destinatario
        parte_html = MIMEText(corpo_html, 'html', 'utf-8')
        msg.attach(parte_html)
        
        with smtplib.SMTP_SSL(current_app.config.get('SMTP_HOST', 'smtp.gmail.com'), 
                              current_app.config.get('SMTP_PORT', 465)) as smtp:
            smtp.login(smtp_user, smtp_password)
            smtp.send_message(msg)
        return True
    except Exception as e:
        print("Erro ao enviar e-mail:", e)
        return False