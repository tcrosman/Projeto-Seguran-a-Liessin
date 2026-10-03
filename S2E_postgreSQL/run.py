import os
import sys

# Aponta para s2e-api/ onde o módulo 'app' está
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 's2e-api'))

from app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.getenv('PORT', 8004))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
