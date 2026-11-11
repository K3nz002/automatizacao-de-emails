"""
app.py
------
Launcher principal do Politize Mailer (Desktop Web Application).

Uso:
    python app.py
    # Ou clique duas vezes em iniciar_app.bat
"""

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Ajusta encoding no terminal Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def print_banner(host: str, port: int):
    url = f"http://{host}:{port}"
    print("=" * 64)
    print("   ⚖️  POLITIZE — AUTOMAÇÃO DE E-MAILS (APP)")
    print("=" * 64)
    print(f"   Status     : Aplicativo iniciado com sucesso!")
    print(f"   Acesse em  : {url}")
    print(f"   Swagger    : {url}/docs")
    print("   Para encerrar: Pressione Ctrl + C no terminal")
    print("=" * 64)
    print()

def open_browser_delayed(url: str, delay: float = 1.2):
    time.sleep(delay)
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"Aviso: Não foi possível abrir o navegador automaticamente: {e}")

def main():
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "8000"))
    url = f"http://{host}:{port}"

    print_banner(host, port)

    # Inicia abertura do navegador em thread secundária
    threading.Thread(target=open_browser_delayed, args=(url,), daemon=True).start()

    # Inicia o servidor Uvicorn
    import uvicorn
    from api import app

    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    except KeyboardInterrupt:
        print("\n[INFO] Aplicativo encerrado pelo usuário. Até logo!")

if __name__ == "__main__":
    main()

