"""
main.py
-------
Script principal de automação de e-mails.

Fluxo de execução:
  1. Carrega variáveis de ambiente (.env)
  2. Compila o template MJML → HTML (uma única vez)
  3. Lê os destinatários do Google Sheets
  4. Para cada destinatário:
     a. Verifica se já foi enviado (prevenção de duplicatas)
     b. Injeta variáveis personalizadas no HTML
     c. Envia via API do Resend
     d. Atualiza o status na planilha
  5. Exibe o relatório final de envios

Uso:
    python main.py

    # Para simular sem enviar (modo DRY RUN):
    DRY_RUN=True python main.py
"""

import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from email_sender import compile_mjml, render_template, send_email
from sheets_client import get_recipients, get_sent_emails, mark_as_sent

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO DE LOGGING
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),           # Saída no console
        logging.FileHandler("envios.log", encoding="utf-8"),  # Log em arquivo
    ],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CARREGAMENTO DAS VARIÁVEIS DE AMBIENTE
# ---------------------------------------------------------------------------
def load_config() -> dict:
    """
    Carrega as variáveis de ambiente do arquivo .env e valida as obrigatórias.
    Retorna um dicionário com todas as configurações necessárias.
    """
    # Procura o .env na mesma pasta do script
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        logger.info(f"Variáveis de ambiente carregadas de: {env_path}")
    else:
        load_dotenv()  # Tenta carregar do diretório atual
        logger.warning(".env não encontrado. Usando variáveis de ambiente do sistema.")

    # Variáveis obrigatórias — o script falha imediatamente se ausentes
    required_vars = ["RESEND_API_KEY", "SPREADSHEET_ID", "GOOGLE_CREDENTIALS_PATH"]
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        logger.error(f"Variáveis de ambiente obrigatórias não definidas: {missing}")
        logger.error("Copie o arquivo .env.example para .env e preencha os valores.")
        sys.exit(1)

    config = {
        "resend_api_key":       os.environ["RESEND_API_KEY"],
        "from_email":           os.getenv("FROM_EMAIL", "noreply@seudominio.com"),
        "spreadsheet_id":       os.environ["SPREADSHEET_ID"],
        "sheet_name":           os.getenv("SHEET_NAME", "Destinatários"),
        "credentials_path":     os.environ["GOOGLE_CREDENTIALS_PATH"],
        "email_subject":        os.getenv("EMAIL_SUBJECT", "Conheça o Politize"),
        "send_interval":        float(os.getenv("SEND_INTERVAL_SECONDS", "1")),
        "dry_run":              os.getenv("DRY_RUN", "False").lower() in ("true", "1", "yes"),
        "template_path":        str(Path(__file__).parent / "templates" / "email_template.mjml"),
    }

    if config["dry_run"]:
        logger.warning("=" * 60)
        logger.warning("  MODO DRY RUN ATIVADO — nenhum e-mail será enviado de fato")
        logger.warning("=" * 60)

    return config


# ---------------------------------------------------------------------------
# SCRIPT PRINCIPAL
# ---------------------------------------------------------------------------
def main():
    logger.info("=" * 60)
    logger.info("  Iniciando automação de e-mails — Politize")
    logger.info("=" * 60)

    # ── Etapa 1: Carregar configurações ────────────────────────────────────
    config = load_config()

    # ── Etapa 2: Compilar o template MJML → HTML (uma única vez) ──────────
    logger.info("Compilando template MJML...")
    try:
        html_template = compile_mjml(config["template_path"])
    except (FileNotFoundError, RuntimeError) as e:
        logger.error(f"Falha ao compilar o template MJML: {e}")
        sys.exit(1)

    # ── Etapa 3: Carregar destinatários e e-mails já enviados ──────────────
    logger.info("Lendo destinatários do Google Sheets...")
    try:
        recipients = get_recipients(
            config["credentials_path"],
            config["spreadsheet_id"],
            config["sheet_name"],
        )
        # Conjunto de e-mails já enviados para verificação O(1)
        already_sent: set[str] = get_sent_emails(
            config["credentials_path"],
            config["spreadsheet_id"],
            config["sheet_name"],
        )
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        logger.error(f"Falha ao acessar o Google Sheets: {e}")
        sys.exit(1)

    if not recipients:
        logger.warning("Nenhum destinatário encontrado na planilha. Encerrando.")
        sys.exit(0)

    # ── Etapa 4: Processar cada destinatário ───────────────────────────────
    stats = {"enviados": 0, "ignorados": 0, "erros": 0}

    for recipient in recipients:
        email  = recipient["email"]
        nome   = recipient["nome"]
        row    = recipient["row"]

        # ── 4a. Verificação de duplicata ───────────────────────────────────
        if email.lower() in already_sent:
            logger.info(f"[IGNORADO] {email} — e-mail já enviado anteriormente.")
            stats["ignorados"] += 1
            continue

        # ── 4b. Injeção de variáveis no template ──────────────────────────
        html_body = render_template(html_template, {"nome": nome})

        # ── 4c. Envio via Resend API ───────────────────────────────────────
        try:
            send_email(
                api_key=config["resend_api_key"],
                from_email=config["from_email"],
                to_email=email,
                subject=config["email_subject"],
                html_body=html_body,
                dry_run=config["dry_run"],
            )

            # ── 4d. Atualizar planilha após envio bem-sucedido ─────────────
            if not config["dry_run"]:
                mark_as_sent(
                    config["credentials_path"],
                    config["spreadsheet_id"],
                    config["sheet_name"],
                    row=row,
                )

            # Registra no conjunto para bloquear duplicatas na mesma execução
            already_sent.add(email.lower())
            stats["enviados"] += 1

        except Exception as e:
            # Captura qualquer falha (API Resend, rede, Sheets) sem interromper o loop
            logger.error(f"[ERRO] Falha ao processar {email} (linha {row}): {e}", exc_info=True)
            stats["erros"] += 1

        # ── Intervalo entre envios (evita rate limiting) ───────────────────
        if config["send_interval"] > 0:
            time.sleep(config["send_interval"])

    # ── Etapa 5: Relatório final ────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("  Relatório de Envios")
    logger.info("=" * 60)
    logger.info(f"  ✅ Enviados com sucesso : {stats['enviados']}")
    logger.info(f"  ⏭️  Ignorados (duplicata): {stats['ignorados']}")
    logger.info(f"  ❌ Erros                : {stats['erros']}")
    logger.info(f"  📋 Total processados    : {len(recipients)}")
    logger.info("=" * 60)
    logger.info("Automação encerrada. Verifique 'envios.log' para detalhes completos.")


if __name__ == "__main__":
    main()
