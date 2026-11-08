const ui = Object.fromEntries(["liters", "connection", "reading-note", "last-success", "contact", "records", "notice", "refresh"].map(id => [id, document.getElementById(id)]));
const number = new Intl.NumberFormat("ru-RU", {maximumFractionDigits: 3});
let busy = false;

function formatDate(value) {
  if (!value) return "—";
  let text = String(value).replace(" ", "T");
  if (!/(?:Z|[+-]\d\d:\d\d)$/.test(text)) text += "Z";
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("ru-RU");
}

async function getJSON(path) {
  const response = await fetch(path, {cache: "no-store", signal: AbortSignal.timeout(8000)});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function showRecords(records) {
  ui.records.replaceChildren();
  if (!records.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 2;
    cell.textContent = "Пока нет сохранённых показаний.";
    row.append(cell);
    ui.records.append(row);
    return;
  }
  for (const record of records) {
    const row = document.createElement("tr");
    const date = document.createElement("td");
    const liters = document.createElement("td");
    date.textContent = formatDate(record.time);
    liters.textContent = number.format(record.liters);
    row.append(date, liters);
    ui.records.append(row);
  }
}

async function refresh() {
  if (busy) return;
  busy = true;
  ui.refresh.disabled = true;
  const [statusResult, historyResult] = await Promise.allSettled([getJSON("/status"), getJSON("/water/stats")]);
  let history = [];
  if (historyResult.status === "fulfilled") {
    history = historyResult.value.last_records || [];
    showRecords(history);
  }
  if (statusResult.status === "fulfilled") {
    const status = statusResult.value;
    const online = status.connected && !status.stale;
    ui.connection.textContent = online ? "ESP32 на связи" : (status.enabled === false ? "Сбор показаний выключен" : "Нет связи с ESP32");
    ui.connection.className = `badge ${online ? "online" : "offline"}`;
    const liters = Number.isFinite(status.liters) ? status.liters : history[0]?.liters;
    if (Number.isFinite(liters)) ui.liters.textContent = number.format(liters);
    ui["last-success"].textContent = formatDate(status.last_success);
    ui.contact.textContent = status.contact_closed == null ? "—" : (status.contact_closed ? "Замкнут · D2 горит" : "Разомкнут · D2 погашен");
    ui["reading-note"].textContent = online ? "Показание получено от устройства и проверено в базе." : "Показано последнее сохранённое значение. Данные могут быть неактуальны.";
    ui.notice.classList.toggle("error", !online);
    ui.notice.textContent = online ? "Связь установлена. Новое значение появляется в истории после изменения показания." : "Подключите ноутбук к Wi-Fi WaterMeter и убедитесь, что ESP32 включена. Сборщик повторит запрос автоматически.";
  } else {
    ui.connection.textContent = "Нет связи с приложением";
    ui.connection.className = "badge offline";
    ui["reading-note"].textContent = "Показано последнее сохранённое значение. Данные могут быть неактуальны.";
    if (history.length) ui.liters.textContent = number.format(history[0].liters);
    ui.notice.classList.add("error");
    ui.notice.textContent = "Проверьте, запущено ли приложение. Повторим проверку автоматически.";
  }
  if (historyResult.status === "rejected") {
    ui.notice.classList.add("error");
    ui.notice.textContent = "Не удалось прочитать историю из базы. Последнее показание сохранено на экране.";
  }
  busy = false;
  ui.refresh.disabled = false;
}

ui.refresh.addEventListener("click", refresh);
refresh();
setInterval(refresh, 5000);
