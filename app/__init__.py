from flask import Flask
from dotenv import load_dotenv
import os

# Carrega variáveis de ambiente
load_dotenv()

def create_app():
    """Factory do Flask - cria e configura a aplicação"""
    app = Flask(__name__,
                static_folder=os.path.join(os.path.dirname(__file__), '..', 'static'),
                template_folder=os.path.join(os.path.dirname(__file__), 'templates'))
    
    # Configurações
    app.secret_key = os.getenv('SECRET_KEY', 'troque-esta-chave-em-producao')
    app.config['UPLOAD_FOLDER'] = os.getenv('UPLOAD_FOLDER', 'storage')
    app.config['MAX_CONTENT_LENGTH'] = int(os.getenv('MAX_CONTENT_LENGTH', 16 * 1024 * 1024))
    
    # Criar pastas necessárias
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'photos'), exist_ok=True)
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'documents'), exist_ok=True)
    os.makedirs('backups', exist_ok=True)
    os.makedirs('logs', exist_ok=True)
    
    # Registrar rotas web e API REST
    from app.api import web, reset
    web.register_routes(app)
    reset.register_rest_routes(app)

    return app