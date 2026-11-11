"""
sheets_client.py
----------------
Módulo responsável por toda a integração com o Google Sheets.
Funções disponíveis:
  - get_recipients()     : lê todos os destinatários da planilha
  - mark_as_sent()       : atualiza o status de envio de um destinatário
  - get_sent_emails()    : retorna o conjunto de e-mails já enviados
"""

import logging
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

# Mapeamento das colunas esperadas na planilha (1-indexado para o gspread)
COL_NOME        = 1  # Coluna A
COL_EMAIL       = 2  # Coluna B
COL_STATUS      = 3  # Coluna C
COL_DATA_ENVIO  = 4  # Coluna D


def _get_worksheet(credentials_path: str, spreadsheet_id: str, sheet_name: str):
    """
    Autentica via Service Account e retorna o objeto worksheet do gspread.
    Lança exceção em caso de falha de autenticação ou planilha não encontrada.
    """
    try:
        creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(spreadsheet_id)
        worksheet = spreadsheet.worksheet(sheet_name)
        logger.info(f"Conexão com Google Sheets estabelecida. Aba: '{sheet_name}'")
        return worksheet
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Arquivo de credenciais não encontrado: '{credentials_path}'. "
            "Verifique GOOGLE_CREDENTIALS_PATH no .env"
        )
    except gspread.exceptions.APIError as e:
        raise RuntimeError(f"Erro na API do Google Sheets: {e}")
    except gspread.exceptions.WorksheetNotFound:
        raise ValueError(f"Aba '{sheet_name}' não encontrada na planilha {spreadsheet_id}")


def _normalize_key(key: str) -> str:
    """Normaliza nomes de colunas removendo acentos, espaços e caracteres especiais."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", key)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).strip().lower().replace("-", "").replace("_", "").replace(" ", "")


def _find_field(record: dict, *candidates: str) -> str:
    """Busca o valor no dicionário testando múltiplos nomes possíveis de coluna de forma flexível."""
    norm_map = {_normalize_key(k): str(v) for k, v in record.items()}
    for candidate in candidates:
        norm_cand = _normalize_key(candidate)
        if norm_cand in norm_map:
            return norm_map[norm_cand]
    return ""


def get_recipients(credentials_path: str, spreadsheet_id: str, sheet_name: str) -> list[dict]:
    """
    Lê todos os destinatários da planilha e retorna uma lista de dicionários.

    Formato esperado na planilha:
        Linha 1 (cabeçalho): Nome | Email | Status | Data de Envio
        Linhas 2+: dados dos destinatários

    Retorno:
        [
            {"row": 2, "nome": "João", "email": "joao@...", "status": "", "data_envio": ""},
            ...
        ]
    """
    worksheet = _get_worksheet(credentials_path, spreadsheet_id, sheet_name)

    # Busca todos os registros como lista de dicionários (usa a linha 1 como cabeçalho)
    records = worksheet.get_all_records()

    recipients = []
    for idx, record in enumerate(records, start=2):  # start=2 pois linha 1 é o cabeçalho
        nome       = _find_field(record, "Nome", "name", "destinatario", "contato").strip()
        email      = _find_field(record, "Email", "e-mail", "mail", "correio").strip()
        status     = _find_field(record, "Status", "situacao", "estado").strip()
        data_envio = _find_field(record, "Data de Envio", "Data de envio", "data", "data_envio", "sent_at").strip()

        # Pula linhas sem e-mail (evita processar linhas vazias)
        if not email:
            logger.debug(f"Linha {idx}: sem e-mail, pulando.")
            continue

        recipients.append({
            "row":        idx,
            "nome":       nome,
            "email":      email,
            "status":     status,
            "data_envio": data_envio,
        })

    logger.info(f"{len(recipients)} destinatário(s) lido(s) da planilha.")
    return recipients


def get_sent_emails(credentials_path: str, spreadsheet_id: str, sheet_name: str, recipients: Optional[list[dict]] = None) -> set[str]:
    """
    Retorna um conjunto (set) com todos os e-mails cujo status é 'Enviado'.
    Se `recipients` for fornecido, reutiliza os dados sem fazer nova chamada de rede.
    """
    if recipients is None:
        recipients = get_recipients(credentials_path, spreadsheet_id, sheet_name)
    sent = {r["email"].lower() for r in recipients if r["status"].lower() == "enviado"}
    logger.info(f"{len(sent)} e-mail(s) já marcado(s) como enviado na planilha.")
    return sent


def mark_as_sent(
    credentials_path: str,
    spreadsheet_id: str,
    sheet_name: str,
    row: int,
    timestamp: Optional[str] = None,
) -> None:
    """
    Atualiza o status da linha `row` na planilha para 'Enviado'
    e registra o timestamp do envio.

    Args:
        row: Número da linha (1-indexado) a atualizar.
        timestamp: String com a data/hora do envio (se None, usa o momento atual).
    """
    worksheet = _get_worksheet(credentials_path, spreadsheet_id, sheet_name)

    if timestamp is None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        # Atualiza coluna Status (C) e Data de Envio (D)
        worksheet.update_cell(row, COL_STATUS, "Enviado")
        worksheet.update_cell(row, COL_DATA_ENVIO, timestamp)
        logger.info(f"Linha {row} atualizada: Status='Enviado', Data='{timestamp}'")
    except gspread.exceptions.APIError as e:
        raise RuntimeError(f"Falha ao atualizar linha {row} no Google Sheets: {e}")


def add_recipient(
    credentials_path: str,
    spreadsheet_id: str,
    sheet_name: str,
    nome: str,
    email: str,
) -> dict:
    """
    Adiciona um novo destinatário na próxima linha disponível da planilha Google Sheets.
    Retorna o registro adicionado com o número da linha correspondente.
    """
    worksheet = _get_worksheet(credentials_path, spreadsheet_id, sheet_name)
    all_values = worksheet.get_all_values()
    next_row = len(all_values) + 1

    # Insere [Nome, Email, Status (vazio), Data de Envio (vazia)]
    worksheet.append_row([nome.strip(), email.strip(), "", ""])
    logger.info(f"Destinatário adicionado no Google Sheets (linha {next_row}): {nome} <{email}>")
    return {
        "row": next_row,
        "nome": nome.strip(),
        "email": email.strip(),
        "status": "",
        "data_envio": "",
    }
