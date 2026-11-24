"""
sheets_client.py
----------------
Módulo responsável por toda a integração com o Google Sheets.
Estrutura oficial de abas fixas:
  - 'Destinatários' : Guarda exclusivamente a lista de contatos/empresas e opt-out.
  - 'Enviados'      : Controla todo o histórico de e-mails disparados (data, assunto, hash).
"""

import logging
import re
from datetime import datetime
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

# Escopos mínimos necessários para leitura e escrita na planilha
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

# Nomes fixos das abas do sistema
TAB_DESTINATARIOS = "Destinatários"
TAB_ENVIADOS = "Enviados"

# Cabeçalhos padrão
DEFAULT_DESTINATARIOS_HEADERS = ["Nome", "Email", "Nome da Empresa", "Email da Empresa", "Status"]
DEFAULT_ENVIADOS_HEADERS = ["Nome", "Email", "Nome da Empresa", "Email da Empresa", "Assunto", "Data de Envio", "Hash Mensagem", "Status"]

# Mantido por compatibilidade
DEFAULT_HEADERS = DEFAULT_DESTINATARIOS_HEADERS

MSG_ESTRUTURA_INVALIDA = (
    'Não existem as abas "Destinatários" e "Enviados" na planilha para o funcionamento '
    'do programa adicione pelo botão "Planilha Google Sheets".'
)


def _extract_spreadsheet_id(spreadsheet_id_or_url: str) -> str:
    """Extrai o ID da planilha mesmo se o usuário tiver fornecido a URL completa."""
    s = str(spreadsheet_id_or_url).strip()
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", s)
    if match:
        return match.group(1)
    return s


def _get_spreadsheet_client(credentials_path: str):
    """Autentica via Service Account e retorna o cliente gspread."""
    creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
    return gspread.authorize(creds)


def _get_worksheet(credentials_path: str, spreadsheet_id: str, sheet_name: str):
    """
    Autentica via Service Account e retorna o objeto worksheet do gspread.
    Lança exceção em caso de falha de autenticação ou planilha/aba não encontrada.
    """
    clean_id = _extract_spreadsheet_id(spreadsheet_id)
    try:
        client = _get_spreadsheet_client(credentials_path)
        spreadsheet = client.open_by_key(clean_id)
        worksheet = spreadsheet.worksheet(sheet_name)
        logger.debug(f"Conexão com Google Sheets estabelecida. Aba: '{sheet_name}'")
        return worksheet
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Arquivo de credenciais não encontrado: '{credentials_path}'. "
            "Verifique GOOGLE_CREDENTIALS_PATH no .env"
        )
    except gspread.exceptions.APIError as e:
        raise RuntimeError(f"Erro na API do Google Sheets: {e}")
    except gspread.exceptions.WorksheetNotFound:
        raise ValueError(f"Aba '{sheet_name}' não encontrada na planilha {clean_id}")


def _ensure_headers(worksheet, default_headers: Optional[list[str]] = None) -> list[str]:
    """Garante que a linha 1 contenha o cabeçalho padrão especificado."""
    if default_headers is None:
        default_headers = DEFAULT_DESTINATARIOS_HEADERS

    values = worksheet.get_all_values()
    if not values or not any(values[0]):
        worksheet.update(range_name="A1", values=[default_headers])
        logger.info(f"Cabeçalhos padrão criados na linha 1 da aba '{worksheet.title}': {default_headers}")
        return default_headers
    return values[0]


def validate_spreadsheet_structure(credentials_path: str, spreadsheet_id: str) -> dict:
    """
    Verifica se a planilha contém estritamente as abas fixas 'Destinatários' e 'Enviados'.
    Caso alguma não exista, retorna indicador e mensagem obrigatória para o usuário.
    """
    clean_id = _extract_spreadsheet_id(spreadsheet_id)
    try:
        client = _get_spreadsheet_client(credentials_path)
        spreadsheet = client.open_by_key(clean_id)
        worksheets = [ws.title for ws in spreadsheet.worksheets()]

        missing = []
        if TAB_DESTINATARIOS not in worksheets:
            missing.append(TAB_DESTINATARIOS)
        if TAB_ENVIADOS not in worksheets:
            missing.append(TAB_ENVIADOS)

        if missing:
            return {
                "valid": False,
                "missing": missing,
                "error_message": MSG_ESTRUTURA_INVALIDA,
                "worksheets": worksheets,
                "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{clean_id}/edit",
            }

        # Inicializa cabeçalhos se abas estiverem vazias
        ws_dest = spreadsheet.worksheet(TAB_DESTINATARIOS)
        _ensure_headers(ws_dest, DEFAULT_DESTINATARIOS_HEADERS)

        ws_env = spreadsheet.worksheet(TAB_ENVIADOS)
        _ensure_headers(ws_env, DEFAULT_ENVIADOS_HEADERS)

        return {
            "valid": True,
            "missing": [],
            "error_message": "",
            "worksheets": worksheets,
            "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{clean_id}/edit",
        }
    except Exception as e:
        logger.error(f"Erro ao validar estrutura da planilha: {e}")
        return {
            "valid": False,
            "missing": [TAB_DESTINATARIOS, TAB_ENVIADOS],
            "error_message": MSG_ESTRUTURA_INVALIDA,
            "details": str(e),
            "worksheets": [],
            "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{clean_id}/edit",
        }


def _normalize_key(key: str) -> str:
    """Normaliza nomes de colunas removendo acentos, espaços e caracteres especiais."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", str(key))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).strip().lower().replace("-", "").replace("_", "").replace(" ", "")


def _find_field(record: dict, *candidates: str) -> str:
    """Busca o valor no dicionário testando múltiplos nomes possíveis de coluna de forma flexível."""
    norm_map = {_normalize_key(k): str(v) for k, v in record.items()}
    for candidate in candidates:
        norm_cand = _normalize_key(candidate)
        if norm_cand in norm_map:
            return norm_map[norm_cand]
    return ""


def _get_col_indices(worksheet, default_headers: Optional[list[str]] = None) -> dict[str, int]:
    """Retorna os índices das colunas (1-indexados) mapeados dinamicamente pelos nomes do cabeçalho."""
    header = _ensure_headers(worksheet, default_headers or DEFAULT_DESTINATARIOS_HEADERS)
    indices = {
        "nome": 1,
        "email": 2,
        "empresa": 3,
        "email_empresa": 4,
        "status": 5,
    }
    for idx, col_name in enumerate(header, start=1):
        norm = _normalize_key(col_name)
        if norm in ("nome", "name", "destinatario", "contato"):
            indices["nome"] = idx
        elif norm in ("email", "emaildestinatario", "mail", "correio"):
            indices["email"] = idx
        elif norm in ("nomedaempresa", "empresa", "escola", "instituicao", "company"):
            indices["empresa"] = idx
        elif norm in ("emaildaempresa", "emailempresa", "companyemail", "emailcorporativo", "emailinstitucional"):
            indices["email_empresa"] = idx
        elif norm in ("status", "situacao", "estado"):
            indices["status"] = idx
        elif norm in ("datadeenvio", "dataenvio", "data", "sentat", "datahora"):
            indices["data_envio"] = idx
    return indices


def get_recipients(credentials_path: str, spreadsheet_id: str, sheet_name: str = TAB_DESTINATARIOS) -> list[dict]:
    """
    Lê todos os destinatários da aba 'Destinatários'.
    A aba 'Destinatários' guarda exclusivamente a lista de e-mails da Politize (sem coluna de data de envio).
    """
    worksheet = _get_worksheet(credentials_path, spreadsheet_id, sheet_name)
    _ensure_headers(worksheet, DEFAULT_DESTINATARIOS_HEADERS)

    records = worksheet.get_all_records()
    recipients = []
    for idx, record in enumerate(records, start=2):
        nome          = _find_field(record, "Nome", "name", "destinatario", "contato").strip()
        email         = _find_field(record, "Email", "e-mail", "mail", "correio").strip()
        empresa       = _find_field(record, "Nome da Empresa", "Empresa", "Escola", "Instituição", "Instituicao", "Company").strip()
        email_empresa = _find_field(record, "Email da Empresa", "Email Empresa", "E-mail da Empresa", "Company Email", "Email Institucional").strip()
        status        = _find_field(record, "Status", "situacao", "estado").strip()

        if not email:
            logger.debug(f"Linha {idx}: sem e-mail, pulando.")
            continue

        recipients.append({
            "row":           idx,
            "nome":          nome,
            "email":         email,
            "empresa":       empresa,
            "email_empresa": email_empresa,
            "status":        status,
            "data_envio":    "",  # Não mais na aba Destinatários; preenchido via histórico de Enviados
        })

    logger.info(f"{len(recipients)} destinatário(s) lido(s) da aba '{sheet_name}'.")
    return recipients


def get_sent_history(credentials_path: str, spreadsheet_id: str, sheet_name: str = TAB_ENVIADOS) -> list[dict]:
    """
    Lê todo o histórico de disparos da aba 'Enviados'.
    Retorna lista ordenada cronologicamente dos envios realizados.
    """
    try:
        worksheet = _get_worksheet(credentials_path, spreadsheet_id, sheet_name)
    except Exception as e:
        logger.warning(f"Aba '{sheet_name}' não acessível para histórico de envios: {e}")
        return []

    _ensure_headers(worksheet, DEFAULT_ENVIADOS_HEADERS)
    records = worksheet.get_all_records()

    history = []
    for idx, record in enumerate(records, start=2):
        email         = _find_field(record, "Email", "e-mail", "mail").strip()
        if not email:
            continue
        history.append({
            "row":           idx,
            "nome":          _find_field(record, "Nome", "name").strip(),
            "email":         email,
            "empresa":       _find_field(record, "Nome da Empresa", "Empresa").strip(),
            "email_empresa": _find_field(record, "Email da Empresa", "Email Empresa").strip(),
            "assunto":       _find_field(record, "Assunto", "subject").strip(),
            "data_envio":    _find_field(record, "Data de Envio", "Data", "data_envio").strip(),
            "hash_mensagem": _find_field(record, "Hash Mensagem", "hash", "msghash").strip(),
            "status":        _find_field(record, "Status", "situacao").strip() or "Enviado",
        })
    return history


def record_sent_email(
    credentials_path: str,
    spreadsheet_id: str,
    nome: str,
    email: str,
    empresa: str = "",
    email_empresa: str = "",
    assunto: str = "",
    timestamp: Optional[str] = None,
    hash_mensagem: str = "",
    status: str = "Enviado",
    sheet_name: str = TAB_ENVIADOS,
) -> dict:
    """
    Registra um disparo de e-mail na aba 'Enviados' para controle histórico.
    """
    worksheet = _get_worksheet(credentials_path, spreadsheet_id, sheet_name)
    _ensure_headers(worksheet, DEFAULT_ENVIADOS_HEADERS)

    if timestamp is None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    row_data = [
        nome.strip(),
        email.strip(),
        empresa.strip(),
        email_empresa.strip(),
        assunto.strip(),
        timestamp,
        hash_mensagem.strip(),
        status.strip(),
    ]

    worksheet.append_row(row_data)
    logger.info(f"Disparo registrado na aba '{sheet_name}' para {email} em {timestamp} (Hash: {hash_mensagem})")
    return {
        "nome": nome,
        "email": email,
        "empresa": empresa,
        "email_empresa": email_empresa,
        "assunto": assunto,
        "data_envio": timestamp,
        "hash_mensagem": hash_mensagem,
        "status": status,
    }


def mark_as_sent(
    credentials_path: str,
    spreadsheet_id: str,
    sheet_name: str = TAB_DESTINATARIOS,
    row: Optional[int] = None,
    timestamp: Optional[str] = None,
    nome: str = "",
    email: str = "",
    empresa: str = "",
    email_empresa: str = "",
    assunto: str = "",
    hash_mensagem: str = "",
    status: str = "Enviado",
) -> None:
    """
    Grava o disparo de e-mail na aba 'Enviados' e, se fornecido 'row', atualiza a lista de apoio.
    Mantém compatibilidade com chamadas existentes.
    """
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. Registra na aba 'Enviados' (controle oficial)
    try:
        record_sent_email(
            credentials_path=credentials_path,
            spreadsheet_id=spreadsheet_id,
            nome=nome,
            email=email,
            empresa=empresa,
            email_empresa=email_empresa,
            assunto=assunto,
            timestamp=timestamp,
            hash_mensagem=hash_mensagem,
            status=status,
            sheet_name=TAB_ENVIADOS,
        )
    except Exception as e:
        logger.error(f"Erro ao registrar envio na aba '{TAB_ENVIADOS}': {e}")
        raise RuntimeError(f"Falha ao registrar envio na aba '{TAB_ENVIADOS}': {e}")


def get_sent_emails(credentials_path: str, spreadsheet_id: str, sheet_name: str = TAB_ENVIADOS, recipients: Optional[list[dict]] = None) -> set[str]:
    """Retorna o conjunto de e-mails que constam no histórico de 'Enviados'."""
    history = get_sent_history(credentials_path, spreadsheet_id, sheet_name)
    sent = {r["email"].lower() for r in history if r.get("status", "").lower() in ("enviado", "simulado")}
    return sent


def get_unsubscribed_emails(credentials_path: str, spreadsheet_id: str, sheet_name: str = TAB_DESTINATARIOS, recipients: Optional[list[dict]] = None) -> set[str]:
    """Retorna o conjunto de e-mails descadastrados (opt-out) da aba 'Destinatários'."""
    if recipients is None:
        recipients = get_recipients(credentials_path, spreadsheet_id, sheet_name)
    unsub = {r["email"].lower() for r in recipients if r.get("status", "").lower() in ("descadastrado", "optout", "opt-out")}
    return unsub


def unsubscribe_recipient(
    credentials_path: str,
    spreadsheet_id: str,
    sheet_name: str = TAB_DESTINATARIOS,
    email: str = "",
    timestamp: Optional[str] = None,
) -> bool:
    """
    Atualiza o status do destinatário correspondente a 'email' na aba 'Destinatários' para 'Descadastrado'.
    """
    worksheet = _get_worksheet(credentials_path, spreadsheet_id, sheet_name)
    clean_email = email.strip().lower()
    col_indices = _get_col_indices(worksheet, DEFAULT_DESTINATARIOS_HEADERS)
    all_values = worksheet.get_all_values()

    target_row = None
    email_col_idx = col_indices["email"] - 1
    for row_idx, row_vals in enumerate(all_values[1:], start=2):
        if len(row_vals) > email_col_idx and row_vals[email_col_idx].strip().lower() == clean_email:
            target_row = row_idx
            break

    if not target_row:
        logger.warning(f"E-mail '{email}' não encontrado na aba '{sheet_name}' para descadastro.")
        return False

    try:
        worksheet.update_cell(target_row, col_indices["status"], "Descadastrado")
        logger.info(f"Destinatário '{email}' marcado como Descadastrado (linha {target_row}).")
        return True
    except gspread.exceptions.APIError as e:
        raise RuntimeError(f"Falha ao descadastrar na planilha: {e}")


def resubscribe_recipient(
    credentials_path: str,
    spreadsheet_id: str,
    sheet_name: str = TAB_DESTINATARIOS,
    email: str = "",
) -> bool:
    """Reativa um destinatário descadastrado na aba 'Destinatários'."""
    worksheet = _get_worksheet(credentials_path, spreadsheet_id, sheet_name)
    clean_email = email.strip().lower()
    col_indices = _get_col_indices(worksheet, DEFAULT_DESTINATARIOS_HEADERS)
    all_values = worksheet.get_all_values()

    target_row = None
    email_col_idx = col_indices["email"] - 1
    for row_idx, row_vals in enumerate(all_values[1:], start=2):
        if len(row_vals) > email_col_idx and row_vals[email_col_idx].strip().lower() == clean_email:
            target_row = row_idx
            break

    if not target_row:
        return False

    try:
        worksheet.update_cell(target_row, col_indices["status"], "")
        logger.info(f"Destinatário '{email}' reativado na aba '{sheet_name}' (linha {target_row}).")
        return True
    except gspread.exceptions.APIError as e:
        raise RuntimeError(f"Falha ao reativar destinatário na planilha: {e}")


def add_recipient(
    credentials_path: str,
    spreadsheet_id: str,
    sheet_name: str = TAB_DESTINATARIOS,
    nome: str = "",
    email: str = "",
    empresa: str = "",
    email_empresa: str = "",
) -> dict:
    """
    Adiciona um novo destinatário na próxima linha da aba 'Destinatários'.
    """
    worksheet = _get_worksheet(credentials_path, spreadsheet_id, sheet_name)
    header = _ensure_headers(worksheet, DEFAULT_DESTINATARIOS_HEADERS)
    all_values = worksheet.get_all_values()
    next_row = len(all_values) + 1

    col_indices = _get_col_indices(worksheet, DEFAULT_DESTINATARIOS_HEADERS)
    num_cols = max(len(header), max(col_indices.values(), default=5))
    row_data = [""] * num_cols

    row_data[col_indices["nome"] - 1] = nome.strip()
    row_data[col_indices["email"] - 1] = email.strip()
    if "empresa" in col_indices and col_indices["empresa"] - 1 < num_cols:
        row_data[col_indices["empresa"] - 1] = empresa.strip()
    if "email_empresa" in col_indices and col_indices["email_empresa"] - 1 < num_cols:
        row_data[col_indices["email_empresa"] - 1] = email_empresa.strip()

    worksheet.append_row(row_data)
    logger.info(
        f"Destinatário adicionado na aba '{sheet_name}' (linha {next_row}): "
        f"{nome} <{email}> [Empresa: {empresa} | Email Empresa: {email_empresa}]"
    )
    return {
        "row": next_row,
        "nome": nome.strip(),
        "email": email.strip(),
        "empresa": empresa.strip(),
        "email_empresa": email_empresa.strip(),
        "status": "",
        "data_envio": "",
    }
