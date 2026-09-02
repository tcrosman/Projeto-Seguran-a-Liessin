import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    # Servidor de desenvolvimento. Produção sobe por gunicorn — ver Procfile e render.yaml.
    #
    # host e debug vêm do ambiente, com padrão fechado. Antes eram `0.0.0.0` e `debug=True`
    # fixos no código: rodar `python run.py` numa máquina acessível publicava para a rede
    # inteira o console interativo do Werkzeug e os tracebacks com o código-fonte.
    #
    # Para acessar de outro dispositivo na rede local (celular, por exemplo), rode com
    # DEV_HOST=0.0.0.0 — explicitamente, e sem FLASK_DEBUG.
    debug = os.getenv('FLASK_DEBUG', 'false').strip().lower() == 'true'
    host = os.getenv('DEV_HOST', '127.0.0.1')
    port = int(os.getenv('PORT', 8002))
    app.run(host=host, port=port, debug=debug)
