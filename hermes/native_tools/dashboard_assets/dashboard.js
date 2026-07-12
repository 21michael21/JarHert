const $ = (id) => document.getElementById(id);
const telegram = window.Telegram?.WebApp;
const state = {
  activeView: "today", snapshot: null, tasks: {items: [], lists: [], priorities: []}, calendar: {items: []},
  coding: {items: []}, notes: {items: []}, knowledge: {items: []}, subscriptions: {items: []}, digest: {items: []}, noteQuery: "", quickType: "task", edit: null, plan: null, codingDraft: null, clip: null, lastUpdatedAt: null,
};
const VIEWS = new Set(["today", "tasks", "calendar", "code", "memory"]);

function text(value) { return String(value ?? "").trim(); }
function field(item, ...keys) { for (const key of keys) if (item?.[key]) return text(item[key]); return "Без названия"; }

function node(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (value !== undefined) element.textContent = value;
  return element;
}

function button(label, className, click, accessibleName = "") {
  const element = node("button", className, label);
  element.type = "button";
  if (accessibleName) element.setAttribute("aria-label", accessibleName);
  element.addEventListener("click", click);
  return element;
}

function list(target, values, render, empty = "Пока пусто") {
  const container = $(target); container.replaceChildren();
  if (!values?.length) { container.append(node("p", "empty", empty)); return; }
  values.forEach((item) => container.append(render(item)));
}

function showNotice(message) { const item = $("notice"); item.textContent = message; item.hidden = !message; }
function haptic(kind, value) { if (telegram?.HapticFeedback?.[kind]) telegram.HapticFeedback[kind](value); }

async function request(url, options = {}) {
  const response = await fetch(url, options);
  if (response.status === 401) throw new Error("session");
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || "request failed"); }
  return response.json();
}

async function refresh() {
  showNotice("Обновляю…");
  const snapshot = await request("/api/snapshot");
  const noteUrl = state.noteQuery ? `/api/notes?query=${encodeURIComponent(state.noteQuery)}` : "/api/notes";
  const [taskResult, calendarResult, codingResult, noteResult, knowledgeResult, subscriptionResult, digestResult] = await Promise.allSettled([
    request("/api/tasks"), request("/api/calendar"), request("/api/coding/jobs"), request(noteUrl), request("/api/knowledge/sources"), request("/api/subscriptions"), request("/api/monitors/digest"),
  ]);
  state.snapshot = snapshot;
  state.tasks = taskResult.status === "fulfilled" ? taskResult.value : {items: [], lists: [], priorities: []};
  state.calendar = calendarResult.status === "fulfilled" ? calendarResult.value : {items: []};
  state.coding = codingResult.status === "fulfilled" ? codingResult.value : {items: []};
  state.notes = noteResult.status === "fulfilled" ? noteResult.value : {items: []};
  state.knowledge = knowledgeResult.status === "fulfilled" ? knowledgeResult.value : {items: []};
  state.subscriptions = subscriptionResult.status === "fulfilled" ? subscriptionResult.value : {items: []};
  state.digest = digestResult.status === "fulfilled" ? digestResult.value : {items: []};
  state.lastUpdatedAt = new Date();
  render(); showNotice("");
}

function render() {
  const snapshot = state.snapshot || {};
  $("mode-chip").textContent = workModeLabel(snapshot.work_mode);
  $("last-sync").textContent = state.lastUpdatedAt ? `Обновлено ${formatTime(state.lastUpdatedAt)}` : "Собираю твой контур";
  renderToday(snapshot);
  renderTasks();
  renderCalendar();
  renderCode();
  renderMemory(snapshot);
  setView(state.activeView);
}

function renderToday(snapshot) {
  const priorities = snapshot.today?.priorities || [];
  const focus = findTask(field(priorities[0], "title", "text", "subject"));
  $("focus-title").textContent = focus?.title || (priorities.length ? field(priorities[0], "title", "text", "subject") : "Сегодня без жёсткого фокуса");
  $("focus-meta").textContent = focus ? taskMeta(focus) : priorities.length ? "Главный фокус на сегодня" : "Можно добавить первую задачу.";
  $("focus-state").textContent = focus ? "Фокус" : "Свободно";
  $("focus-done").disabled = !focus;
  $("focus-move").disabled = !focus;
  $("focus-done").onclick = () => focus && preparePlan([{type: "task.done", payload: {title: focus.title}}]);
  $("focus-move").onclick = () => focus && openTaskMove(focus);
  list("priorities", priorities, (item) => focusRow(findTask(field(item, "title", "text", "subject")) || {title: field(item, "title", "text", "subject")}), "На сегодня пока нет явных задач.");
  list("today-calendar", state.calendar.items.slice(0, 3), compactEventRow, "На ближайшие дни встреч нет.");
  renderOverview(snapshot);
  renderRadar(snapshot);
}

function renderOverview(snapshot) {
  const tasks = state.tasks.items || [];
  const calendar = state.calendar.items || [];
  const radarCount = (state.subscriptions.items || []).length + (snapshot.monitors || []).filter((item) => item.enabled !== false).length + (state.digest.items || []).length;
  $("overview-tasks-value").textContent = tasks.length;
  $("overview-tasks-meta").textContent = tasks.length ? `${tasks.filter((item) => item.list_name === "Today").length} в Today` : "добавить первую";
  $("overview-calendar-value").textContent = calendar.length;
  $("overview-calendar-meta").textContent = calendar.length ? "ближайшие 7 дней" : "окно свободно";
  $("overview-radar-value").textContent = radarCount;
  $("overview-radar-meta").textContent = state.digest.items?.length ? `${state.digest.items.length} в digest` : radarCount ? "источники включены" : "без сигналов";
}

function renderRadar(snapshot) {
  const subscriptions = (state.subscriptions.items || []).map((item) => ({
    title: item.name, meta: `Списание ${formatDayTime(item.next_charge_at)} · ${item.amount} ${item.currency}`,
  }));
  const monitors = (snapshot.monitors || []).filter((item) => item.enabled !== false).map((item) => ({
    title: field(item, "name", "source_type"), monitor: item,
    meta: radarMonitorMeta(item),
  }));
  const deferred = state.digest.items?.length ? [{title: "Digest радара", meta: `${state.digest.items.length} обновл. ждут общего дайджеста`}] : [];
  $("radar-state").textContent = String(subscriptions.length + monitors.length + deferred.length);
  list("radar", [...subscriptions, ...monitors, ...deferred], (item) => {
    const row = node("article", "work-row"); const copy = node("div", "row-copy");
    copy.append(node("strong", "row-title", item.title), node("span", "row-meta", item.meta));
    if (item.monitor?.id) { const actions = node("div", "row-actions"); actions.append(button("Тише", "row-button", () => openRadarSchedule(item.monitor))); row.append(copy, actions); }
    else row.append(copy);
    return row;
  }, "На ближайшее ничего не требует внимания.");
}

function radarMonitorMeta(item) {
  const config = item.source_config || {}; const quiet = text(config.quiet_hours);
  return quiet ? `Тихо ${quiet} · изменения придут одним digest` : "Monitor включён: напишет только при изменении";
}

function focusRow(task) {
  const row = node("article", "work-row");
  const copy = node("div", "row-copy");
  copy.append(node("strong", "row-title", task.title), node("span", "row-meta", taskMeta(task)));
  const actions = node("div", "row-actions");
  actions.append(button("Готово", "row-button", () => preparePlan([{type: "task.done", payload: {title: task.title}}])));
  row.append(copy, actions); return row;
}

function renderTasks() {
  const allItems = state.tasks.items || [];
  $("tasks-summary").textContent = allItems.length
    ? `${allItems.length} ${plural(allItems.length, "задача", "задачи", "задач")} в списках`
    : "Задач пока нет.";
  const filters = $("task-filters"); filters.replaceChildren();
  const choices = ["Все", ...(state.tasks.lists || [])];
  choices.forEach((choice) => {
    const active = (state.taskFilter || "Все") === choice;
    const filterButton = button(choice, `filter-button${active ? " is-active" : ""}`, () => { state.taskFilter = choice; renderTasks(); });
    filters.append(filterButton);
  });
  const items = allItems.filter((item) => !state.taskFilter || state.taskFilter === "Все" || item.list_name === state.taskFilter);
  list("task-list", items, taskRow, "Задач в этой колонке нет.");
}

function taskRow(task) {
  const row = node("article", "work-row task-row");
  const copy = node("div", "row-copy");
  const title = node("strong", "row-title", task.title);
  const meta = node("span", "row-meta", taskMeta(task));
  copy.append(title, meta);
  const actions = node("div", "row-actions");
  actions.append(button("Готово", "row-button", () => preparePlan([{type: "task.done", payload: {title: task.title}}])));
  actions.append(button("Перенести", "row-button", () => openTaskMove(task)));
  actions.append(button(task.priority || "Без приоритета", "priority-button", () => openTaskPriority(task), `Изменить приоритет: ${task.title}`));
  if (task.url) actions.append(button("Открыть", "row-button", () => openExternal(task.url)));
  row.append(copy, actions); return row;
}

function renderCalendar() {
  const items = state.calendar.items || [];
  $("calendar-summary").textContent = items.length ? `${items.length} ${plural(items.length, "событие", "события", "событий")} в ближайшие 7 дней` : "Ближайшие 7 дней свободны";
  list("calendar-list", items, eventRow, "На ближайшие 7 дней событий нет.");
}

function eventRow(event) {
  const row = node("article", "timeline-row");
  const when = node("time", "timeline-time", formatDayTime(event.start));
  const copy = node("div", "row-copy");
  copy.append(node("strong", "row-title", event.title), node("span", "row-meta", event.end ? `до ${formatTime(event.end)}` : "Весь день"));
  const actions = node("div", "row-actions");
  actions.append(button("Перенести", "row-button", () => openCalendarMove(event)));
  if (event.url) actions.append(button("Открыть", "row-button", () => openExternal(event.url)));
  row.append(when, copy, actions); return row;
}

function compactEventRow(event) {
  const row = node("article", "timeline-row compact-row");
  const when = node("time", "timeline-time", formatDayTime(event.start));
  const copy = node("div", "row-copy");
  copy.append(node("strong", "row-title", event.title), node("span", "row-meta", event.end ? `до ${formatTime(event.end)}` : "Весь день"));
  row.append(when, copy);
  row.addEventListener("click", () => setView("calendar"));
  return row;
}

function renderCode() {
  list("coding-jobs", state.coding.items || [], codingJobRow, "Кодовых задач пока нет. Добавь первую одной фразой.");
}

function codingJobRow(job) {
  const row = node("article", "work-row code-row");
  const copy = node("div", "row-copy");
  const status = codingStatus(job.status);
  copy.append(node("strong", "row-title", field(job, "prompt")), node("span", `row-meta ${status.tone}`, `${status.label} · ${job.mode === "research" ? "исследование" : "sandbox-код"}`));
  const actions = node("div", "row-actions");
  if (job.repository_url) actions.append(button("Проект", "row-button", () => openExternal(job.repository_url)));
  if (job.result_text || job.last_error) actions.append(button("Отчёт", "row-button", () => openReport(job)));
  row.append(copy, actions); return row;
}

function codingStatus(value) {
  return {
    queued: {label: "В очереди", tone: "warn"}, running: {label: "В работе", tone: "warn"},
    succeeded: {label: "Готово", tone: "good"}, failed: {label: "Ошибка", tone: "danger"}, cancelled: {label: "Отменено", tone: "muted"},
  }[value] || {label: "Неизвестно", tone: "muted"};
}

function openReport(job) {
  $("report-title").textContent = `Задача #${job.id}`;
  $("report-content").textContent = job.result_text || job.last_error || "Runner ещё не вернул отчёт.";
  $("report-dialog").showModal();
}

function renderMemory(snapshot) {
  const reminders = snapshot.today?.reminders || [];
  $("reminder-count").textContent = reminders.length;
  list("reminders", reminders, reminderRow, "Активных напоминаний нет.");
  list("notes", state.notes.items || [], noteRow, "Заметок пока нет.");
  list("knowledge-sources", state.knowledge.items || [], knowledgeRow, "Добавь первую ссылку: JarHert сохранит только эту страницу.");
  const system = $("system"); system.replaceChildren();
  const status = snapshot.status || {}; const integrations = snapshot.integrations || {};
  system.append(statusRow("Gateway", status.gateway?.active ? "работает" : "нет связи", Boolean(status.gateway?.active)));
  system.append(statusRow("Trello", integrations.trello_ok ? "подключён" : "проверь", Boolean(integrations.trello_ok)));
  system.append(statusRow("Calendar", integrations.calendar_ok ? "подключён" : "проверь", Boolean(integrations.calendar_ok)));
  system.append(statusRow("Backup", status.backup?.configured ? "настроен" : "проверь", Boolean(status.backup?.configured)));
}

function reminderRow(item) {
  const row = node("article", "work-row"); const copy = node("div", "row-copy");
  copy.append(node("strong", "row-title", field(item, "text", "title")), node("span", "row-meta", formatDayTime(item.remind_at)));
  const actions = node("div", "row-actions");
  actions.append(button("Править", "row-button", () => openReminderEditor(item)));
  actions.append(button("×", "icon-action danger", () => cancelReminder(item)));
  row.append(copy, actions); return row;
}

function noteRow(item) {
  const row = node("article", "work-row"); const copy = node("div", "row-copy");
  copy.append(node("strong", "row-title", field(item, "subject")), node("span", "row-meta", field(item, "content")));
  const actions = node("div", "row-actions");
  actions.append(button("История", "row-button", () => openNoteHistory(item)));
  actions.append(button("Править", "row-button", () => openNoteEditor(item)));
  actions.append(button("×", "icon-action danger", () => deleteNote(item), `Удалить заметку: ${field(item, "subject")}`));
  row.append(copy, actions); return row;
}

function knowledgeRow(item) {
  const row = node("article", "work-row"); const copy = node("div", "row-copy");
  copy.append(node("strong", "row-title", field(item, "title", "url")), node("span", "row-meta", [item.project, `${item.snapshot_count || 0} верс.`].filter(Boolean).join(" · ")));
  const actions = node("div", "row-actions");
  actions.append(button("Открыть", "row-button", () => openExternal(item.url)));
  row.append(copy, actions); return row;
}

async function openNoteHistory(item) {
  try {
    const history = await request(`/api/notes/${item.id}/history`);
    $("history-title").textContent = field(item, "subject");
    list("history-content", history.items || [], (revision) => node("div", "preview-row", `${formatDayTime(revision.changed_at)} · ${field(revision, "content")}`), "Правок пока не было.");
    $("history-dialog").showModal();
  } catch (error) { showNotice(friendlyError(error)); }
}

function openKnowledgeClip() {
  state.clip = null; $("clip-url").value = ""; $("clip-project").value = ""; $("clip-preview").replaceChildren(); $("clip-execute").hidden = true; $("clip-preview-action").hidden = false; $("clip-dialog").showModal();
}

async function previewKnowledgeClip(event) {
  event.preventDefault(); const control = $("clip-preview-action"); control.disabled = true;
  try {
    const clip = await request("/api/knowledge/clips/preview", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({request_id: requestId(), url: $("clip-url").value.trim(), project: $("clip-project").value.trim()})});
    state.clip = clip; list("clip-preview", clip.preview || [], (line) => node("div", "preview-row", line)); $("clip-execute").hidden = false; haptic("impactOccurred", "light");
  } catch (error) { showNotice(friendlyError(error)); haptic("notification", "error"); }
  finally { control.disabled = false; }
}

async function executeKnowledgeClip() {
  if (!state.clip) return; const control = $("clip-execute"); control.disabled = true;
  try {
    await request("/api/knowledge/clips/execute", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({request_id: state.clip.request_id, url: state.clip.url, project: state.clip.project || "", clip_token: state.clip.clip_token})});
    $("clip-dialog").close(); state.clip = null; showNotice("Ссылка сохранена в базу знаний"); haptic("notification", "success"); await refresh();
  } catch (error) { showNotice(friendlyError(error)); haptic("notification", "error"); }
  finally { control.disabled = false; }
}

function openRadarSchedule(item) {
  state.edit = {kind: "monitor", item}; setupEdit("Тихие часы", "Изменения в это время попадут в один digest."); $("edit-field-name").textContent = "Тихие часы"; $("edit-value").hidden = false; $("edit-value").value = item.source_config?.quiet_hours || ""; $("edit-dialog").showModal();
}

function statusRow(label, value, good) {
  const row = node("div", "status-row"); row.append(node("span", "", label), node("b", good ? "good" : "warn", value)); return row;
}

function taskMeta(task) {
  return [task.list_name, task.priority, task.due ? `до ${formatDayTime(task.due)}` : ""].filter(Boolean).join(" · ") || "Без срока";
}

function findTask(title) { return (state.tasks.items || []).find((item) => item.title === title); }
function formatDayTime(value) { if (!value) return "без времени"; const date = new Date(value); return Number.isNaN(date.getTime()) ? text(value) : date.toLocaleString("ru-RU", {weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit"}); }
function formatTime(value) { const date = value instanceof Date ? value : new Date(value); return Number.isNaN(date.getTime()) ? text(value) : date.toLocaleTimeString("ru-RU", {hour: "2-digit", minute: "2-digit"}); }
function toLocalDateTime(value) { const date = new Date(value); if (Number.isNaN(date.getTime())) return ""; return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16); }
function toIso(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : date.toISOString(); }
function workModeLabel(mode) { const value = field(mode, "name", "mode").toLowerCase(); return {fast: "Быстро", think: "Думаю", code: "Код"}[value] || "Быстро"; }
function plural(value, one, few, many) { const mod10 = value % 10; const mod100 = value % 100; return mod10 === 1 && mod100 !== 11 ? one : mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20) ? few : many; }

function setView(view, {syncHistory = true} = {}) {
  const nextView = VIEWS.has(view) ? view : "today";
  state.activeView = nextView;
  document.querySelectorAll(".view").forEach((item) => { item.hidden = item.id !== `view-${nextView}`; });
  document.querySelectorAll("[data-view]").forEach((item) => {
    const isActive = item.dataset.view === nextView;
    item.classList.toggle("is-active", isActive);
    item.setAttribute("aria-current", isActive ? "page" : "false");
  });
  if (syncHistory && window.location.hash !== `#${nextView}`) history.replaceState(null, "", `#${nextView}`);
  telegram?.BackButton?.hide();
}

function scheduleNoteSearch(value) {
  state.noteQuery = value.trim();
  window.clearTimeout(state.noteSearchTimer);
  state.noteSearchTimer = window.setTimeout(async () => {
    try {
      const url = state.noteQuery ? `/api/notes?query=${encodeURIComponent(state.noteQuery)}` : "/api/notes";
      state.notes = await request(url);
      renderMemory(state.snapshot || {});
    } catch (error) { showNotice(friendlyError(error)); }
  }, 180);
}

function openQuick(type = "task") { state.quickType = type; updateQuickForm(); $("quick-dialog").showModal(); window.setTimeout(() => $("quick-text").focus(), 0); }
function updateQuickForm() {
  const type = state.quickType;
  document.querySelectorAll("[data-quick-type]").forEach((item) => item.classList.toggle("is-active", item.dataset.quickType === type));
  const labels = {
    task: ["Новая задача", "Что сделать", "Задача попадёт в Inbox без приоритета. Это можно изменить позже."],
    event: ["Новая встреча", "Название", "Выбери время ниже — всё остальное можно поправить потом."],
    reminder: ["Новое напоминание", "О чём напомнить", "Выбери время ниже. Повтор можно настроить в разделе «Память»."],
    note: ["Новая заметка", "Запиши мысль", "Сохраним в личный Inbox, чтобы не потерять."],
  };
  $("quick-title").textContent = labels[type][0]; $("quick-label").textContent = labels[type][1];
  $("quick-help").textContent = labels[type][2];
  $("quick-project-field").hidden = type !== "note";
  $("quick-start-field").hidden = !["event", "reminder"].includes(type); $("quick-end-field").hidden = type !== "event";
  $("quick-start-label").textContent = type === "event" ? "Начало" : "Когда";
}

function quickAction() {
  const content = $("quick-text").value.trim(); const type = state.quickType;
  if (!content) throw new Error("Заполни поле");
  if (type === "task") return {type: "task.create", payload: {title: content}};
  if (type === "event") { const start = toIso($("quick-start").value); const end = toIso($("quick-end").value); if (!start || !end || end <= start) throw new Error("Укажи корректное время встречи"); return {type: "calendar.create", payload: {title: content, start, end}}; }
  if (type === "reminder") { const remindAt = toIso($("quick-start").value); if (!remindAt) throw new Error("Укажи время напоминания"); return {type: "reminder.create", payload: {text: content, remind_at: remindAt}}; }
  return {type: "note.save", payload: {subject: noteSubject(content), content, project: $("quick-project").value.trim() || null}};
}

function noteSubject(content) {
  const firstLine = content.split(/\r?\n/, 1)[0].trim();
  return firstLine.length <= 80 ? firstLine : `${firstLine.slice(0, 79).trimEnd()}…`;
}

async function preparePlan(actions) {
  try {
    const plan = await request("/api/plans", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({request_id: requestId(), actions})});
    state.plan = plan; state.codingDraft = null; $("plan-execute").textContent = "Применить"; renderPlan(plan); $("plan-dialog").showModal();
  } catch (error) { showNotice(friendlyError(error)); haptic("notification", "error"); }
}

function renderPlan(plan) { list("plan-preview", plan.preview || [], (line) => node("div", "preview-row", line), "В плане нет действий."); }
async function executePlan(event) {
  event.preventDefault(); if (!state.plan && !state.codingDraft) return; const control = $("plan-execute"); control.disabled = true;
  try {
    if (state.codingDraft) {
      await request("/api/coding/jobs/execute", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(state.codingDraft)});
      $("coding-prompt").value = "";
      showNotice("Кодовая задача в очереди");
    } else {
      await request(`/api/plans/${state.plan.id}/execute`, {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({plan_token: state.plan.plan_token})});
      $("quick-text").value = "";
      showNotice("Готово");
    }
    $("plan-dialog").close(); state.plan = null; state.codingDraft = null; haptic("notification", "success"); await refresh();
  }
  catch (error) { haptic("notification", "error"); showNotice(friendlyError(error)); }
  finally { control.disabled = false; }
}

function openCoding() { $("coding-dialog").showModal(); window.setTimeout(() => $("coding-prompt").focus(), 0); }
async function previewCoding(event) {
  event.preventDefault(); const control = $("coding-form").querySelector('button[type="submit"]'); const prompt = $("coding-prompt").value.trim();
  if (!prompt) { showNotice("Опиши, что нужно сделать"); return; }
  control.disabled = true;
  try {
    const draft = await request("/api/coding/jobs/preview", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({request_id: requestId(), mode: "coding", prompt})});
    state.codingDraft = {request_id: draft.request_id, mode: draft.mode, prompt: draft.prompt, coding_token: draft.coding_token};
    state.plan = null; $("plan-execute").textContent = "Поставить в очередь"; renderPlan(draft); $("coding-dialog").close(); $("plan-dialog").showModal();
  } catch (error) { haptic("notification", "error"); showNotice(friendlyError(error)); }
  finally { control.disabled = false; }
}

async function cancelPlan() {
  if (state.codingDraft) { state.codingDraft = null; $("plan-dialog").close(); return; }
  const plan = state.plan;
  if (!plan) { $("plan-dialog").close(); return; }
  const control = $("plan-cancel"); control.disabled = true;
  try { await request(`/api/plans/${plan.id}/cancel`, {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({plan_token: plan.plan_token})}); }
  catch (error) { showNotice(friendlyError(error)); }
  finally { control.disabled = false; state.plan = null; $("plan-dialog").close(); }
}

function openTaskMove(task) { openChoiceEditor("task.move", task, "Переместить задачу", "Куда", state.tasks.lists || []); }
function openTaskPriority(task) { openChoiceEditor("task.priority", task, "Приоритет задачи", "Приоритет", state.tasks.priorities || []); }
function openChoiceEditor(kind, item, title, label, choices) {
  state.edit = {kind, item}; setupEdit(title, taskMeta(item)); $("edit-field-name").textContent = label; $("edit-choice").hidden = false; $("edit-choice").replaceChildren(); choices.forEach((value) => $("edit-choice").append(new Option(value, value, false, value === item.priority || value === item.list_name))); $("edit-dialog").showModal();
}

function openCalendarMove(event) { state.edit = {kind: "calendar.move", item: event}; setupEdit("Перенести событие", event.title); $("edit-field-name").textContent = "Новое время"; $("edit-date").hidden = false; $("edit-end").hidden = false; $("edit-date").value = toLocalDateTime(event.start); $("edit-end").value = toLocalDateTime(event.end); $("edit-dialog").showModal(); }
function openReminderEditor(item) { state.edit = {kind: "reminder", item}; setupEdit("Изменить напоминание", field(item, "text", "title")); $("edit-field-name").textContent = "Когда"; $("edit-date").hidden = false; $("edit-date").value = toLocalDateTime(item.remind_at); $("recurrence-field").hidden = false; $("edit-dialog").showModal(); }
function openNoteEditor(item) { state.edit = {kind: "note", item}; setupEdit(field(item, "subject"), item.project ? `Проект: ${item.project}` : "Личная заметка"); $("edit-field-name").textContent = "Текст"; $("edit-value").hidden = false; $("edit-value").value = field(item, "content"); $("edit-dialog").showModal(); }
function setupEdit(title, help) { $("edit-title").textContent = title; $("edit-help").textContent = help; $("edit-date").hidden = true; $("edit-end").hidden = true; $("edit-choice").hidden = true; $("edit-value").hidden = true; $("recurrence-field").hidden = true; }

async function saveEdit(event) {
  event.preventDefault(); if (!state.edit) return; const {kind, item} = state.edit; const control = $("dialog-save"); control.disabled = true;
  try {
    if (kind === "reminder") { const remindAt = toIso($("edit-date").value); if (!remindAt) throw new Error("Укажи время"); await request(`/api/reminders/${item.id}/reschedule`, {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({remind_at: remindAt, recurrence: $("edit-recurrence").value})}); $("edit-dialog").close(); showNotice("Напоминание обновлено"); await refresh(); }
    else if (kind === "note") { const content = $("edit-value").value.trim(); if (!content) throw new Error("Текст заметки пустой"); await request(`/api/notes/${item.id}`, {method: "PUT", headers: {"content-type": "application/json"}, body: JSON.stringify({content})}); $("edit-dialog").close(); showNotice("Заметка обновлена"); await refresh(); }
    else if (kind === "monitor") { const quietHours = $("edit-value").value.trim(); if (quietHours && !/^\d{2}:\d{2}-\d{2}:\d{2}$/.test(quietHours)) throw new Error("Формат: 23:00-08:00"); await request(`/api/monitors/${item.id}/schedule`, {method: "PUT", headers: {"content-type": "application/json"}, body: JSON.stringify({quiet_hours: quietHours, timezone: item.source_config?.timezone || "Europe/Moscow"})}); $("edit-dialog").close(); showNotice(quietHours ? "Радар перейдёт в digest в тихие часы" : "Тихие часы отключены"); await refresh(); }
    else { let action; if (kind === "task.move") action = {type: kind, payload: {title: item.title, target_list: $("edit-choice").value}}; if (kind === "task.priority") action = {type: kind, payload: {title: item.title, priority: $("edit-choice").value}}; if (kind === "calendar.move") { const start = toIso($("edit-date").value); const end = toIso($("edit-end").value); if (!start || !end || end <= start) throw new Error("Укажи корректное время"); action = {type: kind, payload: {title: item.title, start, end}}; } $("edit-dialog").close(); await preparePlan([action]); }
  } catch (error) { haptic("notification", "error"); showNotice(friendlyError(error)); }
  finally { control.disabled = false; }
}

async function cancelReminder(item) { if (!await confirmAction(`Отменить напоминание «${field(item, "text", "title")}»?`)) return; try { await request(`/api/reminders/${item.id}/cancel`, {method: "POST"}); haptic("notification", "success"); showNotice("Напоминание отменено"); await refresh(); } catch (error) { showNotice(friendlyError(error)); } }
async function deleteNote(item) { if (!await confirmAction(`Удалить заметку «${field(item, "subject")}»?`)) return; try { await request(`/api/notes/${item.id}`, {method: "DELETE"}); haptic("notification", "success"); showNotice("Заметка удалена"); await refresh(); } catch (error) { haptic("notification", "error"); showNotice(friendlyError(error)); } }
function confirmAction(message) { if (telegram?.showConfirm) return new Promise((resolve) => telegram.showConfirm(message, resolve)); return Promise.resolve(window.confirm(message)); }
function openExternal(url) { if (telegram?.openLink) telegram.openLink(url); else window.open(url, "_blank", "noopener"); }
function requestId() { return window.crypto?.randomUUID?.().replaceAll("-", "") || `quick${Date.now()}${Math.random().toString(36).slice(2)}`; }
function friendlyError(error) { return error.message === "session" ? "Telegram-сессия истекла. Открой кабинет снова." : error.message || "Не удалось выполнить действие"; }

async function startTelegramSession() {
  if (!telegram?.initData) { $("loading-text").textContent = "Открой кабинет кнопкой в чате с JarHert."; return; }
  telegram.ready(); telegram.expand();
  try { await request("/api/session/telegram", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({init_data: telegram.initData})}); $("loading-panel").hidden = true; $("cabinet").hidden = false; await refresh(); }
  catch (error) { $("loading-text").textContent = friendlyError(error); }
}

function init() {
  $("refresh").addEventListener("click", () => refresh().catch((error) => showNotice(friendlyError(error))));
  document.querySelectorAll("[data-view]").forEach((item) => item.addEventListener("click", () => setView(item.dataset.view)));
  document.querySelectorAll("[data-open-view]").forEach((item) => item.addEventListener("click", () => setView(item.dataset.openView)));
  $("quick-add").addEventListener("click", () => openQuick()); $("quick-cancel").addEventListener("click", () => $("quick-dialog").close());
  document.querySelectorAll("[data-quick-type]").forEach((item) => item.addEventListener("click", () => { state.quickType = item.dataset.quickType; updateQuickForm(); }));
  $("quick-form").addEventListener("submit", (event) => { event.preventDefault(); try { const action = quickAction(); $("quick-dialog").close(); preparePlan([action]); } catch (error) { showNotice(friendlyError(error)); } });
  $("coding-add").addEventListener("click", openCoding); $("coding-cancel").addEventListener("click", () => $("coding-dialog").close()); $("coding-form").addEventListener("submit", previewCoding);
  $("edit-form").addEventListener("submit", saveEdit); $("dialog-cancel").addEventListener("click", () => $("edit-dialog").close());
  $("plan-form").addEventListener("submit", executePlan); $("plan-cancel").addEventListener("click", cancelPlan);
  $("note-search").addEventListener("input", (event) => scheduleNoteSearch(event.target.value));
  $("knowledge-add").addEventListener("click", openKnowledgeClip); $("clip-form").addEventListener("submit", previewKnowledgeClip); $("clip-execute").addEventListener("click", executeKnowledgeClip); $("clip-cancel").addEventListener("click", () => $("clip-dialog").close());
  $("open-trello").addEventListener("click", () => openExternal(state.tasks.board_url || "https://trello.com/")); $("open-calendar").addEventListener("click", () => openExternal("https://calendar.google.com/"));
  window.addEventListener("hashchange", () => setView(window.location.hash.slice(1), {syncHistory: false}));
  if (VIEWS.has(window.location.hash.slice(1))) state.activeView = window.location.hash.slice(1);
  startTelegramSession();
}
init();
