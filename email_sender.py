"""
email_sender.py
---------------
Módulo responsável por:
  1. Compilar o template MJML → HTML (via pacote 'mjml' para Python ou fallback com Node.js)
  2. Injetar variáveis personalizadas no HTML
  3. Disparar o e-mail via API do Resend
"""

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import resend

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. COMPILAÇÃO DO TEMPLATE MJML
# ---------------------------------------------------------------------------

def _compile_mjml_via_nodejs(mjml_source: str) -> str:
    """
    Compila MJML para HTML usando o CLI do Node.js (mjml instalado via npm).
    Utilizado como método principal caso o pacote 'mjml' Python não esteja disponível.

    Requisito: Node.js e 'mjml' instalados globalmente (npm install -g mjml)
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".mjml", delete=False, encoding="utf-8"
    ) as tmp_in:
        tmp_in.write(mjml_source)
        tmp_in_path = tmp_in.name

    tmp_out_path = tmp_in_path.replace(".mjml", ".html")

    try:
        result = subprocess.run(
            ["mjml", tmp_in_path, "-o", tmp_out_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Erro ao compilar MJML via Node.js:\n{result.stderr}"
            )

        html = Path(tmp_out_path).read_text(encoding="utf-8")
        logger.debug("MJML compilado com sucesso via Node.js CLI.")
        return html

    finally:
        # Limpeza dos arquivos temporários
        Path(tmp_in_path).unlink(missing_ok=True)
        Path(tmp_out_path).unlink(missing_ok=True)


def _compile_mjml_via_python(mjml_source: str) -> str:
    """
    Compila MJML para HTML usando o pacote Python 'mjml'.
    Requer: pip install mjml  (internamente também usa Node.js)
    """
    import mjml  # importação tardia para não quebrar se não estiver instalado

    result = mjml.mjml_to_html(mjml_source)
    if result.errors:
        raise RuntimeError(f"Erros ao compilar MJML: {result.errors}")
    return result.html


def compile_mjml(template_path: str) -> str:
    """
    Lê o arquivo MJML e tenta compilá-lo para HTML.
    Tenta primeiro o pacote Python 'mjml', depois o CLI do Node.js.

    Args:
        template_path: Caminho absoluto ou relativo para o arquivo .mjml

    Returns:
        String com o HTML compilado.

    Raises:
        RuntimeError: Se nenhum método de compilação estiver disponível.
    """
    path = Path(template_path)
    if not path.exists():
        raise FileNotFoundError(f"Template MJML não encontrado: '{template_path}'")

    mjml_source = path.read_text(encoding="utf-8")
    logger.info(f"Compilando template MJML: {template_path}")

    # Tenta o pacote Python primeiro
    try:
        html = _compile_mjml_via_python(mjml_source)
        logger.info("Compilação MJML concluída via pacote Python.")
        return html
    except ImportError:
        logger.warning("Pacote 'mjml' Python não encontrado. Tentando Node.js CLI...")

    # Fallback para o CLI do Node.js
    try:
        html = _compile_mjml_via_nodejs(mjml_source)
        logger.info("Compilação MJML concluída via Node.js CLI.")
        return html
    except FileNotFoundError:
        raise RuntimeError(
            "Não foi possível compilar o MJML. "
            "Instale o pacote Python com 'pip install mjml' "
            "ou o CLI do Node.js com 'npm install -g mjml'."
        )


# ---------------------------------------------------------------------------
# 2. INJEÇÃO DE VARIÁVEIS NO TEMPLATE
# ---------------------------------------------------------------------------

def render_template(html_template: str, variables: dict) -> str:
    """
    Substitui os marcadores {{chave}} no HTML pelas variáveis fornecidas.

    Args:
        html_template: HTML compilado contendo marcadores como {{nome}}.
        variables: Dicionário com os valores a substituir, ex.: {"nome": "João"}.

    Returns:
        HTML com todas as variáveis substituídas.

    Exemplo:
        render_template("<p>Olá, {{nome}}!</p>", {"nome": "Maria"})
        # → "<p>Olá, Maria!</p>"
    """
    html = html_template
    for key, value in variables.items():
        placeholder = "{{" + key + "}}"
        html = html.replace(placeholder, str(value))

    # Avisa sobre marcadores não preenchidos que restaram no HTML
    remaining = re.findall(r"\{\{(\w+)\}\}", html)
    if remaining:
        logger.warning(f"Marcadores não substituídos no template: {remaining}")

    return html


# ---------------------------------------------------------------------------
# 3. ENVIO VIA API RESEND
# ---------------------------------------------------------------------------

def send_email(
    api_key: str,
    from_email: str,
    to_email: str,
    subject: str,
    html_body: str,
    dry_run: bool = False,
) -> Optional[str]:
    """
    Envia um e-mail HTML via API do Resend.

    Args:
        api_key:    Chave de API do Resend (RESEND_API_KEY).
        from_email: Endereço remetente verificado no Resend.
        to_email:   Endereço do destinatário.
        subject:    Assunto do e-mail.
        html_body:  Conteúdo HTML do e-mail.
        dry_run:    Se True, simula o envio sem chamar a API (modo de teste).

    Returns:
        ID do e-mail retornado pelo Resend em caso de sucesso, ou None em dry_run.

    Raises:
        Exception: Propaga qualquer erro da API Resend para tratamento externo.
    """
    if dry_run:
        logger.info(f"[DRY RUN] Simulando envio para {to_email} | Assunto: {subject}")
        return None

    # Configura a chave da API
    resend.api_key = api_key

    params = {
        "from":    from_email,
        "to":      [to_email],
        "subject": subject,
        "html":    html_body,
    }

    logger.debug(f"Enviando e-mail para {to_email}...")
    email_response = resend.Emails.send(params)

    email_id = email_response.get("id") if isinstance(email_response, dict) else getattr(email_response, "id", None)
    logger.info(f"E-mail enviado com sucesso para {to_email} | ID: {email_id}")
    return email_id
