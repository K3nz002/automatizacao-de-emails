"""
test.py
Script de teste rápido para envio de um único e-mail de validação.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

from email_sender import compile_mjml, render_template, send_email

# Garante saída UTF-8 no Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Carrega configurações do .env
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

api_key = os.getenv("RESEND_API_KEY")
from_email = os.getenv("FROM_EMAIL", "Politize <onboarding@resend.dev>")
to_email = "gkwfuji@gmail.com"
subject = os.getenv("EMAIL_SUBJECT", "Teste de Envio — Politize")

print("1. Compilando template MJML...")
template_path = Path(__file__).parent / "templates" / "email_template.mjml"
html_template = compile_mjml(str(template_path))

print("2. Renderizando template com dados do destinatário...")
html_body = render_template(html_template, {"nome": "Gabriel"})

print(f"3. Enviando e-mail de teste para {to_email}...")
email_id = send_email(
    api_key=api_key,
    from_email=from_email,
    to_email=to_email,
    subject=subject,
    html_body=html_body,
    dry_run=False,
)

print(f"[SUCESSO] E-mail enviado com sucesso para {to_email}! ID: {email_id}")
