"""Entry point.

Production (systemd):  waitress-serve --listen=127.0.0.1:5000 --threads=4 --call wsgi:build
Local development:     python wsgi.py
"""
from onto import create_app


def build():
    return create_app()


if __name__ == "__main__":
    build().run(debug=True, port=5000)
