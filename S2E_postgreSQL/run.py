import os
import sys

# Aponta para s2e-api/ onde o módulo 'app' está
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 's2e-api'))

from app import create_app

app = create_app()

if __name__ == "__main__":
    # Servidor de desenvolvimento. Produção sobe por gunicorn — ver
    # s2e-api/deploy/DEPLOY.md.
    #
    # host vem do ambiente com padrão fechado, igual ao s2e-api/run.py. Antes era
    # '0.0.0.0' fixo: rodar este arquivo numa máquina acessível publicava para a rede
    # inteira o console interativo do Werkzeug junto com os tracebacks. O teste que
    # protege contra isso (tests/test_deploy_e_arestas.py) só lê o s2e-api/run.py,
    # então este aqui tinha escapado.
    port = int(os.getenv('PORT', 8003))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    host = os.getenv('DEV_HOST', '127.0.0.1')
    app.run(host=host, port=port, debug=debug)
