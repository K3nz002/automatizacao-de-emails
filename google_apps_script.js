/**
 * google_apps_script.js
 * -----------------------------------------------------------------------------
 * Script para execução 24/7 em nuvem (Google Apps Script Web App).
 * Permite que o botão de "Descadastrar" funcione mesmo com a aplicação desktop desligada!
 * 
 * COMO INSTALAR NA SUA PLANILHA EM 1 MINUTO:
 * 1. Abra sua planilha Google Sheets (com as abas "Destinatários" e "Enviados").
 * 2. No menu superior, clique em: Extensões -> Apps Script.
 * 3. Apague o conteúdo do editor e cole todo este código.
 * 4. No canto superior direito, clique em "Implantar" (Deploy) -> "Nova implantação".
 * 5. Clique no ícone de engrenagem ao lado de "Selecione o tipo" e escolha "App da Web".
 * 6. Configure:
 *      - Descrição: Descadastro Politize
 *      - Executar como: Eu (seu e-mail)
 *      - Quem tem acesso: Qualquer pessoa (Anyone)
 * 7. Clique em "Implantar" e copie a URL gerada (ex: https://script.google.com/macros/s/.../exec).
 * 8. Cole essa URL no seu arquivo .env:
 *      UNSUBSCRIBE_URL=https://script.google.com/macros/s/SUA_URL_AQUI/exec
 * 
 * Pronto! Os e-mails enviados terão o link de descadastro funcionando 24 horas por dia na nuvem!
 * -----------------------------------------------------------------------------
 */

function doGet(e) {
  var params = e ? e.parameter : {};
  var email = params.email ? String(params.email).trim().toLowerCase() : "";
  var action = params.action ? String(params.action).trim().toLowerCase() : "unsubscribe";

  if (!email) {
    return HtmlService.createHtmlOutput(renderErrorPage("E-mail não especificado na solicitação."))
      .setTitle("Erro — Politize")
      .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
  }

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName("Destinatários");

  if (!sheet) {
    return HtmlService.createHtmlOutput(renderErrorPage('Aba "Destinatários" não encontrada na planilha.'))
      .setTitle("Erro — Politize")
      .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
  }

  var data = sheet.getDataRange().getValues();
  if (data.length < 1) {
    return HtmlService.createHtmlOutput(renderErrorPage("A planilha está vazia."))
      .setTitle("Erro — Politize");
  }

  var header = data[0];
  var emailCol = -1;
  var statusCol = -1;

  for (var c = 0; c < header.length; c++) {
    var h = String(header[c]).toLowerCase().replace(/[^a-z]/g, "");
    if (h === "email" || h === "correio" || h === "destinatario") {
      emailCol = c;
    }
    if (h === "status" || h === "situacao") {
      statusCol = c;
    }
  }

  if (emailCol === -1 || statusCol === -1) {
    return HtmlService.createHtmlOutput(renderErrorPage("Colunas de Email ou Status não foram encontradas na aba Destinatários."))
      .setTitle("Erro — Politize");
  }

  var targetRow = -1;
  for (var r = 1; r < data.length; r++) {
    var rowEmail = String(data[r][emailCol]).trim().toLowerCase();
    if (rowEmail === email) {
      targetRow = r + 1; // 1-indexado para a planilha
      break;
    }
  }

  if (targetRow === -1) {
    return HtmlService.createHtmlOutput(renderErrorPage("O e-mail informado (" + escapeHtml(email) + ") não consta na lista de destinatários."))
      .setTitle("Aviso — Politize");
  }

  if (action === "resubscribe") {
    sheet.getRange(targetRow, statusCol + 1).setValue("");
    return HtmlService.createHtmlOutput(renderResubscribedPage(email))
      .setTitle("Inscrição Reativada — Politize")
      .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
  }

  // Ação padrão: Descadastrar
  sheet.getRange(targetRow, statusCol + 1).setValue("Descadastrado");
  return HtmlService.createHtmlOutput(renderUnsubscribedPage(email))
    .setTitle("Inscrição Cancelada — Politize")
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderUnsubscribedPage(email) {
  var safeEmail = escapeHtml(email);
  return '<!doctype html>' +
    '<html lang="pt-BR">' +
    '<head>' +
    '  <meta charset="utf-8">' +
    '  <meta name="viewport" content="width=device-width, initial-scale=1">' +
    '  <title>Inscrição Cancelada — Politize</title>' +
    '  <style>' +
    '    body { margin: 0; background-color: #F4F4F5; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; padding: 20px; box-sizing: border-box; color: #16213E; }' +
    '    .card { background: #ffffff; border-radius: 16px; padding: 40px 32px; max-width: 460px; width: 100%; text-align: center; box-shadow: 0 10px 30px rgba(15,24,48,0.08); border: 1px solid #E2E6EF; }' +
    '    .badge { display: inline-flex; align-items: center; justify-content: center; width: 56px; height: 56px; border-radius: 50%; background: #DDF1EA; color: #17795E; font-size: 26px; margin-bottom: 20px; }' +
    '    h1 { font-size: 22px; font-weight: 700; margin: 0 0 12px; color: #16213E; }' +
    '    p { font-size: 14.5px; color: #5A6682; line-height: 1.6; margin: 0 0 20px; }' +
    '    .email-box { background: #FAFBFD; border: 1px solid #D9DEE8; border-radius: 8px; padding: 10px 14px; font-weight: 600; font-size: 14px; color: #16213E; margin-bottom: 24px; word-break: break-all; }' +
    '    .btn-resub { background: transparent; border: 1.5px solid #CBD5E1; color: #475569; padding: 10px 20px; border-radius: 8px; font-weight: 600; font-size: 13.5px; text-decoration: none; display: inline-block; transition: all .2s; }' +
    '    .btn-resub:hover { border-color: #16213E; color: #16213E; }' +
    '  </style>' +
    '</head>' +
    '<body>' +
    '  <div class="card">' +
    '    <div class="badge">✓</div>' +
    '    <h1>Inscrição Cancelada</h1>' +
    '    <p>Você não receberá novos comunicados enviados através desta lista institucional.</p>' +
    '    <div class="email-box">' + safeEmail + '</div>' +
    '    <p style="font-size:13px; color:#8592A6;">Clicou por engano?</p>' +
    // O Apps Script exibe a página dentro de um iframe: o link precisa da URL completa do
    // App da Web e de target="_top", senão o clique não abre a página de reativação.
    '    <a href="' + ScriptApp.getService().getUrl() + '?email=' + encodeURIComponent(email) + '&action=resubscribe" target="_top" class="btn-resub">Reativar minha inscrição</a>' +
    '  </div>' +
    '</body>' +
    '</html>';
}

function renderResubscribedPage(email) {
  var safeEmail = escapeHtml(email);
  return '<!doctype html>' +
    '<html lang="pt-BR">' +
    '<head>' +
    '  <meta charset="utf-8">' +
    '  <meta name="viewport" content="width=device-width, initial-scale=1">' +
    '  <title>Inscrição Reativada — Politize</title>' +
    '  <style>' +
    '    body { margin: 0; background-color: #F4F4F5; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; padding: 20px; box-sizing: border-box; color: #16213E; }' +
    '    .card { background: #ffffff; border-radius: 16px; padding: 40px 32px; max-width: 460px; width: 100%; text-align: center; box-shadow: 0 10px 30px rgba(15,24,48,0.08); border: 1px solid #E2E6EF; }' +
    '    .badge { display: inline-flex; align-items: center; justify-content: center; width: 56px; height: 56px; border-radius: 50%; background: #E0E7FF; color: #4338CA; font-size: 26px; margin-bottom: 20px; }' +
    '    h1 { font-size: 22px; font-weight: 700; margin: 0 0 12px; color: #16213E; }' +
    '    p { font-size: 14.5px; color: #5A6682; line-height: 1.6; margin: 0 0 20px; }' +
    '    .email-box { background: #FAFBFD; border: 1px solid #D9DEE8; border-radius: 8px; padding: 10px 14px; font-weight: 600; font-size: 14px; color: #16213E; margin-bottom: 24px; word-break: break-all; }' +
    '  </style>' +
    '</head>' +
    '<body>' +
    '  <div class="card">' +
    '    <div class="badge">↩</div>' +
    '    <h1>Inscrição Reativada</h1>' +
    '    <p>Seu e-mail está ativo novamente e apto a receber novidades e comunicados da Politize.</p>' +
    '    <div class="email-box">' + safeEmail + '</div>' +
    '  </div>' +
    '</body>' +
    '</html>';
}

function renderErrorPage(message) {
  var safeMsg = escapeHtml(message);
  return '<!doctype html>' +
    '<html lang="pt-BR">' +
    '<head>' +
    '  <meta charset="utf-8">' +
    '  <meta name="viewport" content="width=device-width, initial-scale=1">' +
    '  <title>Aviso — Politize</title>' +
    '  <style>' +
    '    body { margin: 0; background-color: #F4F4F5; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; padding: 20px; box-sizing: border-box; color: #16213E; }' +
    '    .card { background: #ffffff; border-radius: 16px; padding: 40px 32px; max-width: 460px; width: 100%; text-align: center; box-shadow: 0 10px 30px rgba(15,24,48,0.08); border: 1px solid #FEE2E2; }' +
    '    .badge { display: inline-flex; align-items: center; justify-content: center; width: 56px; height: 56px; border-radius: 50%; background: #FEE2E2; color: #DC2626; font-size: 26px; margin-bottom: 20px; }' +
    '    h1 { font-size: 22px; font-weight: 700; margin: 0 0 12px; color: #991B1B; }' +
    '    p { font-size: 14.5px; color: #5A6682; line-height: 1.6; margin: 0; }' +
    '  </style>' +
    '</head>' +
    '<body>' +
    '  <div class="card">' +
    '    <div class="badge">!</div>' +
    '    <h1>Aviso</h1>' +
    '    <p>' + safeMsg + '</p>' +
    '  </div>' +
    '</body>' +
    '</html>';
}
