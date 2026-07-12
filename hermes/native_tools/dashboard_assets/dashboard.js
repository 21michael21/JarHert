const $ = (id) => document.getElementById(id);
const text = (value) => String(value ?? "").trim();
const telegram = window.Telegram?.WebApp;
let editState = null;

function field(item, ...keys) {
  for (const key of keys) if (item?.[key]) return text(item[key]);
  return "Без названия";
}

function list(target, values, render) {
  const node = $(target);
  node.replaceChildren();
  if (!values?.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Пока пусто";
    node.append(empty);
    return;
  }
  values.forEach((value) => node.append(render(value)));
}

function plainItem(value) {
  const item = document.createElement("div");
  item.className = "list-item";
  item.textContent = text(value);
  return item;
}

function actionItem(title, actions) {
  const item = document.createElement("div");
  item.className = "list-item actionable-item";
  const body = document.createElement("span");
  body.textContent = title;
  const buttons = document.createElement("div");
  buttons.className = "item-actions";
  actions.forEach(({label, tone, click}) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `compact-button ${tone || ""}`.trim();
    button.textContent = label;
    button.addEventListener("click", (event) => { event.stopPropagation(); click(); });
    buttons.append(button);
  });
  item.append(body, buttons);
  return item;
}

function statusLine(label, value, good = true) {
  const row = document.createElement("div");
  row.className = "status-row";
  const name = document.createElement("span"); name.textContent = label;
  const state = document.createElement("b"); state.textContent = value; state.dataset.good = String(good);
  row.append(name, state); return row;
}

function showNotice(message) {
  const node = $("notice"); node.textContent = message; node.hidden = !message;
}

function showError(message) {
  $("loading-text").textContent = message;
  $("loading-panel").querySelector("h2").textContent = "Кабинет недоступен";
}

function render(snapshot) {
  const today = snapshot.today || {};
  $("task-count").textContent = (today.tasks || []).length;
  $("calendar-count").textContent = (today.calendar || []).length;
  $("reminder-count").textContent = (today.reminders || []).length;
  $("monitor-count").textContent = (snapshot.monitors || []).filter((item) => item.enabled !== false).length;
  $("mode-chip").textContent = `Режим: ${workModeLabel(snapshot.work_mode)}`;
  list("priorities", today.priorities, (item) => plainItem(field(item, "title", "text", "subject")));
  list("calendar", today.calendar, plainItem);
  list("tasks", today.tasks, plainItem);
  list("reminders", today.reminders, (item) => actionItem(
    `${field(item, "text", "title")} · ${formatDate(item.remind_at)}`,
    [
      {label: "Изменить", click: () => openReminderEditor(item)},
      {label: "Отменить", tone: "danger", click: () => cancelReminder(item)},
    ],
  ));
  list("notes", snapshot.notes, (item) => actionItem(
    `${field(item, "subject")} · ${field(item, "content")}`,
    [{label: "Править", click: () => openNoteEditor(item)}],
  ));
  list("projects", snapshot.projects, (item) => plainItem(field(item, "name", "project")));
  renderCapabilities(snapshot.capabilities || []);
  const system = $("system"); system.replaceChildren();
  const status = snapshot.status || {}; const integrations = snapshot.integrations || {};
  system.append(statusLine("Gateway", status.gateway?.active ? "работает" : "нет связи", Boolean(status.gateway?.active)));
  system.append(statusLine("Trello", integrations.trello_ok ? "подключён" : "проверь", Boolean(integrations.trello_ok)));
  system.append(statusLine("Calendar", integrations.calendar_ok ? "подключён" : "проверь", Boolean(integrations.calendar_ok)));
  system.append(statusLine("Backup", status.backup?.configured ? "настроен" : "проверь", Boolean(status.backup?.configured)));
  system.append(statusLine("Диск", `${status.resources?.disk_free_gib ?? "—"} GiB свободно`, true));
}

function renderCapabilities(capabilities) {
  const node = $("capabilities"); node.replaceChildren();
  capabilities.forEach((capability) => {
    const item = document.createElement("div");
    item.className = "capability";
    const title = document.createElement("strong"); title.textContent = field(capability, "title");
    const description = document.createElement("p"); description.textContent = field(capability, "text");
    item.append(title, description); node.append(item);
  });
}

function workModeLabel(mode) {
  const value = field(mode, "name", "mode").toLowerCase();
  return {fast: "Быстро", think: "Думаю", code: "Код"}[value] || "Быстро";
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  if (response.status === 401) throw new Error("session");
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "request failed");
  }
  return response.json();
}

async function refresh() {
  showNotice("Обновляю данные…");
  const snapshot = await request("/api/snapshot");
  render(snapshot); showNotice("");
}

function openReminderEditor(item) {
  editState = {kind: "reminder", id: item.id};
  $("edit-eyebrow").textContent = "НАПОМИНАНИЕ";
  $("edit-title").textContent = "Изменить время";
  $("edit-help").textContent = field(item, "text", "title");
  $("edit-field-name").textContent = "Когда";
  $("edit-value").hidden = true;
  $("edit-date").hidden = false;
  $("edit-date").value = toLocalDateTime(item.remind_at);
  $("recurrence-field").hidden = false;
  $("edit-dialog").showModal();
}

function openNoteEditor(item) {
  editState = {kind: "note", id: item.id};
  $("edit-eyebrow").textContent = "ЗАМЕТКА";
  $("edit-title").textContent = field(item, "subject");
  $("edit-help").textContent = item.project ? `Проект: ${item.project}` : "Личная заметка";
  $("edit-field-name").textContent = "Текст";
  $("edit-date").hidden = true;
  $("edit-value").hidden = false;
  $("edit-value").value = field(item, "content");
  $("recurrence-field").hidden = true;
  $("edit-dialog").showModal();
}

async function saveEdit(event) {
  event.preventDefault();
  if (!editState) return;
  const save = $("dialog-save"); save.disabled = true;
  try {
    if (editState.kind === "reminder") {
      const value = $("edit-date").value;
      if (!value) throw new Error("Укажи время");
      await request(`/api/reminders/${editState.id}/reschedule`, {
        method: "POST", headers: {"content-type": "application/json"},
        body: JSON.stringify({remind_at: new Date(value).toISOString(), recurrence: $("edit-recurrence").value}),
      });
      haptic("notification", "success"); showNotice("Напоминание обновлено");
    } else {
      const content = $("edit-value").value.trim();
      if (!content) throw new Error("Текст заметки пустой");
      await request(`/api/notes/${editState.id}`, {
        method: "PUT", headers: {"content-type": "application/json"}, body: JSON.stringify({content}),
      });
      haptic("notification", "success"); showNotice("Заметка обновлена");
    }
    $("edit-dialog").close();
    await refresh();
  } catch (error) {
    haptic("notification", "error"); showNotice(error.message === "session" ? "Telegram-сессия истекла. Открой кабинет снова." : error.message);
  } finally { save.disabled = false; }
}

async function cancelReminder(item) {
  const confirmed = await confirmAction(`Отменить напоминание «${field(item, "text", "title")}»?`);
  if (!confirmed) return;
  try {
    await request(`/api/reminders/${item.id}/cancel`, {method: "POST"});
    haptic("notification", "success"); showNotice("Напоминание отменено"); await refresh();
  } catch (error) {
    haptic("notification", "error"); showNotice(error.message === "session" ? "Telegram-сессия истекла. Открой кабинет снова." : error.message);
  }
}

function confirmAction(message) {
  if (telegram?.showConfirm) return new Promise((resolve) => telegram.showConfirm(message, resolve));
  return Promise.resolve(window.confirm(message));
}

function haptic(kind, value) {
  if (telegram?.HapticFeedback?.[kind]) telegram.HapticFeedback[kind](value);
}

function formatDate(value) {
  if (!value) return "без времени";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? text(value) : date.toLocaleString("ru-RU", {day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit"});
}

function toLocalDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const timezoneOffset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - timezoneOffset).toISOString().slice(0, 16);
}

async function startTelegramSession() {
  if (!telegram?.initData) {
    showError("Открой кабинет кнопкой в чате с JarHert: браузер не может подтвердить твою Telegram-сессию.");
    return;
  }
  telegram.ready(); telegram.expand();
  document.documentElement.dataset.telegramTheme = telegram.colorScheme || "dark";
  try {
    await request("/api/session/telegram", {
      method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({init_data: telegram.initData}),
    });
    $("loading-panel").hidden = true; $("cabinet").hidden = false;
    await refresh();
  } catch (error) {
    showError(error.message === "session" ? "Telegram-сессия не подтверждена." : "Не удалось подтвердить Telegram-сессию.");
  }
}

function init() {
  $("refresh").addEventListener("click", () => refresh().catch(() => showNotice("Не удалось обновить данные")));
  $("edit-form").addEventListener("submit", saveEdit);
  $("dialog-cancel").addEventListener("click", () => $("edit-dialog").close());
  startTelegramSession();
}
init();
