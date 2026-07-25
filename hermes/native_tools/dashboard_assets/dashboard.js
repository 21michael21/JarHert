const $ = (id) => document.getElementById(id);
const telegram = window.Telegram?.WebApp;
const state = {
  activeView: "today", snapshot: null, tasks: {items: [], lists: [], priorities: []}, calendar: {items: []},
  coding: {items: []}, notes: {items: []}, knowledge: {items: []}, subscriptions: {items: []}, digest: {items: []}, noteQuery: "", taskQuery: "", taskFilter: "Все", taskMenu: null, quickType: "task", edit: null, plan: null, codingDraft: null, clip: null, architectureScenario: "plan", lastUpdatedAt: null,
  calendarDay: null, toastTimer: null, toastUndo: null, voiceRecognition: null, nowTimer: null,
};
const RING_RADIUS = 31;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;
const VIEWS = new Set(["today", "tasks", "calendar", "code", "memory"]);
const ARCHITECTURE_SCENARIOS = {
  question: {
    eyebrow: "СЦЕНАРИЙ · ВОПРОС", title: "Короткий ответ без лишнего круга", summary: "Обычный вопрос не трогает внешние сервисы: JarHert понимает контекст и отвечает в чате.",
    nodes: [
      {label: "Telegram", title: "Сообщение приходит в gateway", copy: "Текст попадает в единственный активный вход JarHert.", guard: "Повтор одного update не запустит второй ответ."},
      {label: "Codex + SOUL", title: "Понимание запроса и человеческий тон", copy: "Codex формирует ответ, а SOUL держит русский стиль, краткость и честность.", guard: "Необязательные инструменты не открываются."},
      {label: "Ответ", title: "Итог возвращается в Telegram", copy: "Ты получаешь короткий ответ по делу — без очереди, preview и побочных действий.", guard: "Если данных не хватает, JarHert задаст один ясный вопрос."},
    ],
  },
  plan: {
    eyebrow: "СЦЕНАРИЙ · ЗАДАЧА", title: "Один план, одно подтверждение", summary: "Задача, встреча, напоминание и заметка могут уйти одним понятным планом.",
    nodes: [
      {label: "Telegram", title: "Ты пишешь как человеку", copy: "Например: «завтра в 12 напомни про ML и поставь встречу в 13».", guard: "Сообщение получает свой ключ идемпотентности."},
      {label: "План", title: "JarHert собирает только нужные действия", copy: "Роутер разделяет фразу на задачу, календарь, напоминание или заметку.", guard: "Непонятное действие не исполняется наугад."},
      {label: "Preview", title: "Один понятный контрольный экран", copy: "Ты видишь, что именно будет создано или изменено, и подтверждаешь весь план одной кнопкой.", guard: "До подтверждения Trello и Calendar не меняются.", gate: true},
      {label: "Сервисы", title: "Trello и Calendar выполняют план", copy: "Очередь создаёт карточки, события и напоминания надёжно, с повторной доставкой при сбое.", guard: "Повтор запроса не создаёт дубли."},
      {label: "Итог", title: "Результат приходит обратно сюда", copy: "В Telegram приходит компактный итог с тем, что реально создано.", guard: "Все части плана видны в trace и outbox."},
    ],
  },
  voice: {
    eyebrow: "СЦЕНАРИЙ · ГОЛОС", title: "Голосовой dump превращается в план", summary: "Можно наговорить мысли подряд: система выделит действия, но не применит их без твоего общего ок.",
    nodes: [
      {label: "Голос", title: "Голосовое приходит в Telegram", copy: "Исходное сообщение остаётся в твоём чате, а JarHert получает аудио для разбора.", guard: "Сначала проверяется размер и тип файла."},
      {label: "Текст", title: "Локальная транскрипция", copy: "Голос превращается в текст; даты и время дополнительно проверяются перед созданием действий.", guard: "Неразборчивые фрагменты не выдаются за факт."},
      {label: "Разбор", title: "Мысли раскладываются по пунктам", copy: "Заметки, задачи, встречи и обещания собираются в один список без потери исходного смысла.", guard: "Сомнительный пункт остаётся в preview, а не уходит в сервис."},
      {label: "Preview", title: "Один ответ на весь голосовой dump", copy: "Ты подтверждаешь весь список одной кнопкой или правишь нужный пункт.", guard: "Никакой очереди уточнений по одной на каждую мысль.", gate: true},
      {label: "Итог", title: "Сохранено и видно в кабинете", copy: "Результат появляется в Telegram, а задачи, заметки и встречи — в соответствующих разделах.", guard: "Сырой голос можно сохранить только по твоему явному выбору."},
    ],
  },
  research: {
    eyebrow: "СЦЕНАРИЙ · РЕПОЗИТОРИЙ", title: "Репозиторий разбирается отдельно", summary: "Ссылка на GitHub идёт через read-only анализ; кодовая работа — в отдельную очередь и изолированный runner.",
    nodes: [
      {label: "Ссылка", title: "Ты кидаешь репу или гипотезу", copy: "Вопрос и ссылка поступают через Telegram или Code Desk в кабинете.", guard: "Ничего в репозитории не меняется от одной ссылки."},
      {label: "GitHub", title: "Read-only обзор репы, PR и CI", copy: "GitHub MCP читает структуру, последние PR, issues и статусы CI в режиме только чтения.", guard: "Push, merge и изменение репы не входят в этот маршрут."},
      {label: "Очередь", title: "Сложная задача становится кодовым job", copy: "В job сохраняются цель, репозиторий, критерий готовности и trace.", guard: "Ты видишь состояние, а очередь переживает перезапуск."},
      {label: "Runner", title: "Проверка идёт в песочнице", copy: "Изолированный runner возвращает diff, тесты, ветку и commit, если задача просит готовый фикс.", guard: "Push и deploy идут только после отдельного ок владельца."},
      {label: "Отчёт", title: "Человеческий итог в Telegram и кабинете", copy: "Вместо простыни приходит кратко: причина, что изменено, тесты и следующий выбор.", guard: "Полный отчёт остаётся доступен в Code Desk."},
    ],
  },
};
let architecturePlaybackTimer = null;

function text(value) { return String(value ?? "").trim(); }
function field(item, ...keys) { for (const key of keys) if (item?.[key]) return text(item[key]); return "Без названия"; }

function node(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (value !== undefined) element.textContent = value;
  return element;
}

function icon(name, className = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("aria-hidden", "true");
  svg.classList.add("icon");
  if (className) svg.classList.add(...className.split(" "));
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#icon-${name}`);
  svg.append(use);
  return svg;
}

function button(label, className, click, accessibleName = "", iconName = "") {
  const element = node("button", className);
  element.type = "button";
  if (iconName) element.append(icon(iconName));
  if (label) element.append(document.createTextNode(label));
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
function haptic(kind, value) {
  const method = {impact: "impactOccurred", notification: "notificationOccurred", selection: "selectionChanged"}[kind] || kind;
  if (telegram?.HapticFeedback?.[method]) telegram.HapticFeedback[method](value);
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  if (response.status === 401) throw new Error("session");
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || "request failed"); }
  return response.json();
}

async function refresh() {
  const control = $("refresh");
  control?.classList.add("is-loading");
  if (control) control.disabled = true;
  showNotice("Обновляю…");
  try {
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
  } finally {
    control?.classList.remove("is-loading");
    if (control) control.disabled = false;
  }
}

function render() {
  const snapshot = state.snapshot || {};
  $("mode-chip-label").textContent = workModeLabel(snapshot.work_mode);
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
  renderFocusRing(snapshot);
  renderMomentum(snapshot);
  list("priorities", priorities, (item) => focusRow(findTask(field(item, "title", "text", "subject")) || {title: field(item, "title", "text", "subject")}), "На сегодня пока нет явных задач. Кинь первую одной фразой через «Добавить».");
  list("today-calendar", state.calendar.items.slice(0, 3), compactEventRow, "На ближайшие дни встреч нет. Дыши спокойно.");
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
  renderSparkline("spark-tasks", (snapshot.completion?.daily || []).map((item) => item.done), "spark-accent");
  renderSparkline("spark-calendar", eventsPerDay(calendar, 7), "spark-cyan");
  renderSparkline("spark-radar", upcomingChargesPerDay(state.subscriptions.items || [], 7), "spark-good");
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
    if (item.monitor?.id) { const actions = node("div", "row-actions"); actions.append(button("Тише", "row-button", () => openRadarSchedule(item.monitor), "", "moon")); row.append(copy, actions); }
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
  actions.append(button("Готово", "row-button", () => preparePlan([{type: "task.done", payload: {title: task.title}}]), "", "check"));
  row.append(copy, actions); return row;
}

function renderTasks() {
  const allItems = state.tasks.items || [];
  const filter = $("task-list-filter");
  const choices = [...new Set(["Все", ...(state.tasks.lists || []).filter(Boolean)])];
  if (!choices.includes(state.taskFilter)) state.taskFilter = "Все";
  filter.replaceChildren(...choices.map((choice) => new Option(choice, choice, false, choice === state.taskFilter)));
  filter.value = state.taskFilter;
  const visible = allItems.filter((item) => (
    (state.taskFilter === "Все" || item.list_name === state.taskFilter)
    && taskMatchesQuery(item, state.taskQuery)
  ));
  $("tasks-summary").textContent = taskSummary(allItems.length, visible.length, state.taskQuery, state.taskFilter);
  list("task-list", visible, taskRow, state.taskQuery ? "Ничего не нашлось. Попробуй другое слово." : "Задач в этом списке нет.");
}

function taskRow(task) {
  const row = node("article", "work-row task-row");
  const copy = node("div", "row-copy");
  const title = node("strong", "row-title task-title", taskDisplayTitle(task));
  title.title = task.title;
  const meta = node("span", "row-meta", taskMeta(task));
  copy.append(title, meta);
  const actions = node("div", "row-actions");
  actions.append(button("Готово", "row-button task-complete", () => preparePlan([{type: "task.done", payload: {title: task.title}}]), "", "check"));
  actions.append(button("", "task-menu-button", () => openTaskMenu(task), `Другие действия с задачей: ${task.title}`, "more-horizontal"));
  row.append(copy, actions); return row;
}

function renderCalendar() {
  const items = state.calendar.items || [];
  $("calendar-summary").textContent = items.length ? `${items.length} ${plural(items.length, "событие", "события", "событий")} в ближайшие 7 дней` : "Ближайшие 7 дней свободны";
  renderWeekStrip(items);
  renderCalendarTimeline(items);
}

function renderWeekStrip(items) {
  const strip = $("week-strip");
  strip.replaceChildren();
  const today = startOfDay(new Date());
  const counts = {};
  items.forEach((event) => {
    const key = startOfDay(new Date(event.start)).toDateString();
    counts[key] = (counts[key] || 0) + 1;
  });
  const allButton = node("button", "week-day" + (state.calendarDay === null ? " is-active" : ""));
  allButton.type = "button";
  allButton.setAttribute("aria-pressed", String(state.calendarDay === null));
  allButton.append(node("span", "week-day-name", "Все"), node("strong", "week-day-num", String(items.length)));
  allButton.addEventListener("click", () => { state.calendarDay = null; haptic("selection"); renderCalendar(); });
  strip.append(allButton);
  for (let offset = 0; offset < 7; offset++) {
    const day = new Date(today);
    day.setDate(day.getDate() + offset);
    const key = day.toDateString();
    const count = counts[key] || 0;
    const isActive = state.calendarDay === key;
    const dayButton = node("button", "week-day" + (isActive ? " is-active" : "") + (offset === 0 ? " is-today" : ""));
    dayButton.type = "button";
    dayButton.setAttribute("aria-pressed", String(isActive));
    dayButton.append(
      node("span", "week-day-name", offset === 0 ? "Сег" : day.toLocaleDateString("ru-RU", {weekday: "short"}).replace(".", "")),
      node("strong", "week-day-num", String(day.getDate())),
    );
    if (count) {
      const dots = node("span", "week-day-dots");
      for (let dotIndex = 0; dotIndex < Math.min(count, 3); dotIndex++) dots.append(node("i"));
      dayButton.append(dots);
    }
    dayButton.addEventListener("click", () => {
      state.calendarDay = state.calendarDay === key ? null : key;
      haptic("selection");
      renderCalendar();
    });
    strip.append(dayButton);
  }
}

function renderCalendarTimeline(items) {
  const container = $("calendar-list");
  container.replaceChildren();
  const filtered = state.calendarDay
    ? items.filter((event) => startOfDay(new Date(event.start)).toDateString() === state.calendarDay)
    : items;
  if (!filtered.length) {
    container.append(node("p", "empty", state.calendarDay ? "В этот день пока пусто. Отличная возможность выдохнуть." : "На ближайшие 7 дней событий нет. Дыши спокойно."));
    stopNowLine();
    return;
  }
  const groups = {};
  filtered.forEach((event) => {
    const key = startOfDay(new Date(event.start)).toDateString();
    (groups[key] = groups[key] || []).push(event);
  });
  const now = new Date();
  const todayKey = startOfDay(now).toDateString();
  Object.keys(groups).sort((a, b) => new Date(a) - new Date(b)).forEach((key) => {
    const dayDate = new Date(key);
    const isToday = key === todayKey;
    const group = node("section", "cal-day");
    const head = node("header", "cal-day-head");
    head.append(
      node("span", "cal-day-label", dayLabelFor(dayDate, now)),
      node("span", "cal-day-date", dayDate.toLocaleDateString("ru-RU", {day: "2-digit", month: "long"})),
    );
    group.append(head);
    const dayEvents = groups[key].slice().sort((a, b) => new Date(a.start) - new Date(b.start));
    let nowInserted = false;
    dayEvents.forEach((event) => {
      if (isToday && !nowInserted && new Date(event.start) > now) {
        group.append(buildNowLine(now));
        nowInserted = true;
      }
      group.append(calendarBlock(event, isToday, now));
    });
    if (isToday && !nowInserted) group.append(buildNowLine(now));
    container.append(group);
  });
  scheduleNowLineTick();
}

function calendarBlock(event, isToday, now) {
  const start = new Date(event.start);
  const end = event.end ? new Date(event.end) : null;
  const isLive = isToday && end && now >= start && now <= end;
  const row = node("article", "cal-event" + (isLive ? " is-live" : ""));
  const gutter = node("div", "cal-gutter");
  gutter.append(node("time", "cal-time", end ? formatTime(start) : "—"));
  const rail = node("div", "cal-rail");
  rail.append(node("span", "cal-dot"));
  const card = node("div", "cal-card");
  card.append(node("strong", "cal-title", event.title));
  const minutes = end ? Math.max(0, Math.round((end - start) / 60000)) : 0;
  const range = end ? `${formatTime(start)}–${formatTime(end)}` : "Весь день";
  card.append(node("span", "cal-meta", minutes >= 45 ? `${range} · ${durationLabel(minutes)}` : range));
  if (isLive) card.append(node("span", "cal-live-badge", "идёт сейчас"));
  const actions = node("div", "cal-actions");
  actions.append(button("", "row-button", () => openCalendarMove(event), `Перенести: ${event.title}`, "clock"));
  if (event.url) actions.append(button("", "row-button", () => openExternal(event.url), `Открыть: ${event.title}`, "external-link"));
  card.append(actions);
  row.append(gutter, rail, card);
  return row;
}

function buildNowLine(now) {
  const row = node("div", "cal-now");
  row.append(node("time", "cal-now-time", formatTime(now)), node("span", "cal-now-line"), node("span", "cal-now-label", "сейчас"));
  return row;
}

function scheduleNowLineTick() {
  window.clearInterval(state.nowTimer);
  state.nowTimer = window.setInterval(() => {
    if (state.activeView !== "calendar" && state.activeView !== "today") return;
    const line = document.querySelector(".cal-now");
    if (line) {
      const time = line.querySelector(".cal-now-time");
      if (time) time.textContent = formatTime(new Date());
    }
  }, 30000);
}

function stopNowLine() {
  window.clearInterval(state.nowTimer);
  state.nowTimer = null;
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
  if (job.repository_url) actions.append(button("Проект", "row-button", () => openExternal(job.repository_url), "", "github"));
  if (job.result_text || job.last_error) actions.append(button("Отчёт", "row-button", () => openReport(job), "", "file-text"));
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

function showArchitectureNode(index) {
  const scenario = ARCHITECTURE_SCENARIOS[state.architectureScenario] || ARCHITECTURE_SCENARIOS.plan;
  const nodeData = scenario.nodes[index] || scenario.nodes[0];
  $("architecture-detail-eyebrow").textContent = `ШАГ ${index + 1} · ${nodeData.label.toUpperCase()}`;
  $("architecture-detail-title").textContent = nodeData.title;
  $("architecture-detail-copy").textContent = nodeData.copy;
  $("architecture-detail-guard").textContent = nodeData.guard;
  document.querySelectorAll("[data-architecture-node]").forEach((item) => {
    const nodeIndex = Number(item.dataset.architectureNode);
    item.classList.toggle("is-active", nodeIndex === index);
    item.classList.toggle("is-traversed", nodeIndex <= index);
    item.setAttribute("aria-pressed", String(nodeIndex === index));
  });
  document.querySelectorAll("[data-architecture-arrow]").forEach((item) => {
    item.classList.toggle("is-traversed", Number(item.dataset.architectureArrow) < index);
  });
}

function renderArchitectureNodes(scenario) {
  const container = $("architecture-flow-nodes");
  container.replaceChildren();
  scenario.nodes.forEach((nodeData, index) => {
    const nodeButton = button("", "architecture-flow-node", () => {
      window.clearTimeout(architecturePlaybackTimer);
      showArchitectureNode(index);
    });
    nodeButton.dataset.architectureNode = String(index);
    nodeButton.setAttribute("aria-pressed", "false");
    const marker = node("span", "architecture-flow-marker", String(index + 1));
    const copy = node("span", "architecture-flow-copy");
    copy.append(node("strong", "", nodeData.label), node("small", "", index === 0 ? "Запрос" : nodeData.gate ? "Твоё подтверждение" : "Следующий шаг"));
    nodeButton.append(marker, copy);
    container.append(nodeButton);
    if (index < scenario.nodes.length - 1) {
      const arrow = node("span", "architecture-flow-arrow", "↓");
      arrow.dataset.architectureArrow = String(index);
      arrow.setAttribute("aria-hidden", "true");
      container.append(arrow);
    }
  });
}

function playArchitectureFlow() {
  window.clearTimeout(architecturePlaybackTimer);
  const scenario = ARCHITECTURE_SCENARIOS[state.architectureScenario] || ARCHITECTURE_SCENARIOS.plan;
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
    showArchitectureNode(0);
    return;
  }
  let index = 0;
  const move = () => {
    showArchitectureNode(index);
    index = (index + 1) % scenario.nodes.length;
    architecturePlaybackTimer = window.setTimeout(move, 1250);
  };
  move();
}

function showArchitectureScenario(key) {
  const scenario = ARCHITECTURE_SCENARIOS[key] || ARCHITECTURE_SCENARIOS.plan;
  state.architectureScenario = ARCHITECTURE_SCENARIOS[key] ? key : "plan";
  $("architecture-flow-eyebrow").textContent = scenario.eyebrow;
  $("architecture-flow-title").textContent = scenario.title;
  $("architecture-flow-summary").textContent = scenario.summary;
  document.querySelectorAll("[data-architecture-scenario]").forEach((item) => {
    const selected = item.dataset.architectureScenario === state.architectureScenario;
    item.classList.toggle("is-active", selected);
    item.setAttribute("aria-pressed", String(selected));
  });
  renderArchitectureNodes(scenario);
  playArchitectureFlow();
}

function openArchitecture() {
  const dialog = $("architecture-dialog");
  if (!dialog.open) dialog.showModal();
  haptic("impact", "light");
  showArchitectureScenario("plan");
}

function renderMemory(snapshot) {
  const reminders = snapshot.today?.reminders || [];
  $("reminder-count").textContent = reminders.length;
  list("reminders", reminders, reminderRow, "Напоминаний нет. И это тоже хорошая новость.");
  list("notes", state.notes.items || [], noteRow, "В голове пусто? Запиши первую мысль — не потеряем.");
  list("knowledge-sources", state.knowledge.items || [], knowledgeRow, "Добавь первую ссылку: JarHert сохранит только эту страницу.");
  const system = $("system"); system.replaceChildren();
  const status = snapshot.status || {}; const integrations = snapshot.integrations || {};
  const runtime = status.runtime || {}; const queue = status.coding_queue || {};
  system.append(statusRow("JarHert", runtimeLabel(runtime.state), runtime.state === "healthy"));
  system.append(statusRow("Gateway", status.gateway?.active ? "работает" : "нет связи", Boolean(status.gateway?.active)));
  system.append(statusRow("Trello", integrations.trello_ok ? "подключён" : "проверь", Boolean(integrations.trello_ok)));
  system.append(statusRow("Calendar", integrations.calendar_ok ? "подключён" : "проверь", Boolean(integrations.calendar_ok)));
  const github = status.github_mcp || {};
  system.append(statusRow("GitHub", githubMcpLabel(github.state), github.state === "ready"));
  system.append(statusRow("Runner", workerLabel(queue.worker_state), queue.worker_state !== "attention"));
  system.append(statusRow("Backup", status.backup?.configured ? "настроен" : "проверь", Boolean(status.backup?.configured)));
}

function reminderRow(item) {
  const row = node("article", "work-row"); const copy = node("div", "row-copy");
  copy.append(node("strong", "row-title", field(item, "text", "title")), node("span", "row-meta", `${formatDayTime(item.remind_at)} · ${relativeTime(item.remind_at)}`));
  const actions = node("div", "row-actions");
  actions.append(button("Править", "row-button", () => openReminderEditor(item), "", "pencil"));
  actions.append(button("", "icon-action danger", () => cancelReminder(item), `Отменить напоминание: ${field(item, "text", "title")}`, "trash-2"));
  row.append(copy, actions); return row;
}

function noteRow(item) {
  const row = node("article", "work-row"); const copy = node("div", "row-copy");
  copy.append(node("strong", "row-title", field(item, "subject")), node("span", "row-meta", field(item, "content")));
  const actions = node("div", "row-actions");
  actions.append(button("История", "row-button", () => openNoteHistory(item), "", "history"));
  actions.append(button("Править", "row-button", () => openNoteEditor(item), "", "pencil"));
  actions.append(button("", "icon-action danger", () => deleteNote(item), `Удалить заметку: ${field(item, "subject")}`, "trash-2"));
  row.append(copy, actions); return row;
}

function knowledgeRow(item) {
  const row = node("article", "work-row"); const copy = node("div", "row-copy");
  copy.append(node("strong", "row-title", field(item, "title", "url")), node("span", "row-meta", [item.project, `${item.snapshot_count || 0} верс.`].filter(Boolean).join(" · ")));
  const actions = node("div", "row-actions");
  actions.append(button("Открыть", "row-button", () => openExternal(item.url), "", "external-link"));
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
  const icons = {JarHert: "sparkles", Gateway: "radio", Trello: "list-todo", Calendar: "calendar-days", GitHub: "github", Runner: "terminal-square", Backup: "archive"};
  const row = node("div", "status-row");
  const title = node("span", "status-label");
  title.append(icon(icons[label] || "radio"), document.createTextNode(label));
  row.append(title, node("b", good ? "good" : "warn", value));
  return row;
}

function githubMcpLabel(state) {
  return {ready: "read-only готов", disabled: "выключен", needs_token: "нужен токен", missing_binary: "нужна установка"}[state] || "проверь";
}

function runtimeLabel(state) {
  return {healthy: "в порядке", attention: "нужно внимание", offline: "не в сети"}[state] || "проверяется";
}

function workerLabel(state) {
  return {busy: "в работе", idle: "ожидает", attention: "есть ошибка"}[state] || "проверяется";
}

function taskMeta(task) {
  return [task.list_name, task.priority, task.due ? `до ${formatDayTime(task.due)}` : ""].filter(Boolean).join(" · ") || "Без срока";
}

function taskMatchesQuery(task, query) {
  const needle = text(query).toLocaleLowerCase("ru-RU");
  if (!needle) return true;
  return [task.title, task.list_name, task.priority, ...(task.labels || [])]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase("ru-RU")
    .includes(needle);
}

function taskSummary(total, visible, query, filter) {
  if (!total) return "Задач пока нет.";
  if (query || filter !== "Все") return `Показано ${visible} из ${total}`;
  return `${total} ${plural(total, "задача", "задачи", "задач")} в списках`;
}

function taskDisplayTitle(task) {
  const title = text(task.title);
  try {
    const url = new URL(title);
    return `Ссылка: ${url.hostname.replace(/^www\./, "")}`;
  } catch (_) {
    return title;
  }
}

function findTask(title) { return (state.tasks.items || []).find((item) => item.title === title); }
function formatDayTime(value) { if (!value) return "без времени"; const date = new Date(value); return Number.isNaN(date.getTime()) ? text(value) : date.toLocaleString("ru-RU", {weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit"}); }
function formatTime(value) { const date = value instanceof Date ? value : new Date(value); return Number.isNaN(date.getTime()) ? text(value) : date.toLocaleTimeString("ru-RU", {hour: "2-digit", minute: "2-digit"}); }
function toLocalDateTime(value) { const date = new Date(value); if (Number.isNaN(date.getTime())) return ""; return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16); }
function toIso(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : date.toISOString(); }
function workModeLabel(mode) { const value = field(mode, "name", "mode").toLowerCase(); return {fast: "Быстро", think: "Думаю", code: "Код"}[value] || "Быстро"; }
function plural(value, one, few, many) { const mod10 = value % 10; const mod100 = value % 100; return mod10 === 1 && mod100 !== 11 ? one : mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20) ? few : many; }

function setView(view, {syncHistory = true, hapticFeedback = false} = {}) {
  const nextView = VIEWS.has(view) ? view : "today";
  const changed = state.activeView !== nextView;
  state.activeView = nextView;
  document.querySelectorAll(".view").forEach((item) => {
    const isVisible = item.id === `view-${nextView}`;
    item.hidden = !isVisible;
    if (isVisible && changed) {
      item.classList.remove("is-entering");
      void item.offsetWidth;
      item.classList.add("is-entering");
    }
  });
  document.querySelectorAll("[data-view]").forEach((item) => {
    const isActive = item.dataset.view === nextView;
    item.classList.toggle("is-active", isActive);
    item.setAttribute("aria-current", isActive ? "page" : "false");
  });
  if (syncHistory && window.location.hash !== `#${nextView}`) history.replaceState(null, "", `#${nextView}`);
  if (changed && hapticFeedback) haptic("selection");
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

function openQuick(type = "task") { state.quickType = type; updateQuickForm(); haptic("impact", "light"); $("quick-dialog").showModal(); window.setTimeout(() => $("quick-text").focus(), 0); }
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
      $("coding-repository").value = "";
      $("coding-sources").value = "";
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

function openCoding() { state.codingDraft = null; updateCodingForm(); $("coding-dialog").showModal(); window.setTimeout(() => $("coding-prompt").focus(), 0); }
function updateCodingForm() {
  const research = $("coding-mode").value === "research";
  $("coding-repository-field").hidden = research;
  $("coding-sources-field").hidden = !research;
  $("coding-prompt").placeholder = research
    ? "Какая гипотеза? Например: PDF тормозит из-за двойного рендера"
    : "PDF тупит при перелистывании: найди причину и подготовь фикс с тестами";
}
function codingRequestPayload() {
  const mode = $("coding-mode").value;
  const prompt = $("coding-prompt").value.trim();
  if (!prompt) throw new Error("Опиши, что нужно проверить");
  if (mode === "research") {
    const sourceUrls = $("coding-sources").value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
    if (!sourceUrls.length) throw new Error("Добавь хотя бы одну HTTPS ссылку");
    return {request_id: requestId(), mode, prompt, source_urls: sourceUrls};
  }
  const repositoryUrl = $("coding-repository").value.trim();
  if (!repositoryUrl) throw new Error("Добавь GitHub-репозиторий");
  return {request_id: requestId(), mode, prompt, repository_url: repositoryUrl};
}
async function previewCoding(event) {
  event.preventDefault(); const control = $("coding-form").querySelector('button[type="submit"]');
  let payload;
  try { payload = codingRequestPayload(); } catch (error) { showNotice(friendlyError(error)); return; }
  control.disabled = true;
  try {
    const draft = await request("/api/coding/jobs/preview", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(payload)});
    state.codingDraft = {
      request_id: draft.request_id,
      mode: draft.mode,
      prompt: draft.prompt,
      repository_url: draft.repository_url,
      source_urls: draft.source_urls,
      coding_token: draft.coding_token,
    };
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
function openTaskMenu(task) {
  state.taskMenu = task;
  $("task-menu-title").textContent = taskDisplayTitle(task);
  $("task-menu-open").hidden = !task.url;
  $("task-menu-dialog").showModal();
}
function closeTaskMenu() { $("task-menu-dialog").close(); }
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

async function cancelReminder(item) {
  try {
    await request(`/api/reminders/${item.id}/cancel`, {method: "POST"});
    haptic("notification", "success");
    await refresh();
    showToast(`Напоминание «${field(item, "text", "title")}» отменено`, "Отменить", async () => {
      try {
        await request(`/api/reminders/${item.id}/reschedule`, {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({remind_at: item.remind_at, recurrence: "keep"})});
        haptic("notification", "success");
        await refresh();
      } catch (error) { showNotice(friendlyError(error)); }
    });
  } catch (error) { showNotice(friendlyError(error)); }
}

async function deleteNote(item) {
  const subject = field(item, "subject");
  const content = text(item.content);
  const project = item.project || null;
  try {
    await request(`/api/notes/${item.id}`, {method: "DELETE"});
    haptic("notification", "success");
    await refresh();
    showToast(`Заметка «${subject}» удалена`, "Вернуть", async () => {
      try {
        const plan = await request("/api/plans", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({request_id: requestId(), actions: [{type: "note.save", payload: {subject, content, project}}]})});
        await request(`/api/plans/${plan.id}/execute`, {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({plan_token: plan.plan_token})});
        haptic("notification", "success");
        await refresh();
      } catch (error) { showNotice(friendlyError(error)); }
    });
  } catch (error) { haptic("notification", "error"); showNotice(friendlyError(error)); }
}

function openExternal(url) { if (telegram?.openLink) telegram.openLink(url); else window.open(url, "_blank", "noopener"); }
function requestId() { return window.crypto?.randomUUID?.().replaceAll("-", "") || `quick${Date.now()}${Math.random().toString(36).slice(2)}`; }
function friendlyError(error) { return error.message === "session" ? "Telegram-сессия истекла. Открой кабинет снова." : error.message || "Не удалось выполнить действие"; }

function startOfDay(date) { const d = new Date(date); d.setHours(0, 0, 0, 0); return d; }

function dayLabelFor(dayDate, now) {
  const today = startOfDay(now).getTime();
  const target = startOfDay(dayDate).getTime();
  const diffDays = Math.round((target - today) / 86400000);
  if (diffDays === 0) return "Сегодня";
  if (diffDays === 1) return "Завтра";
  if (diffDays === -1) return "Вчера";
  return dayDate.toLocaleDateString("ru-RU", {weekday: "short"}).replace(".", "");
}

function durationLabel(minutes) {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours && rest) return `${hours} ч ${rest} мин`;
  if (hours) return `${hours} ч`;
  return `${rest} мин`;
}

function relativeTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return text(value);
  const diffMs = date - new Date();
  const absMinutes = Math.round(Math.abs(diffMs) / 60000);
  if (absMinutes < 1) return "сейчас";
  const label = absMinutes < 60 ? `${absMinutes} мин` : durationLabel(absMinutes);
  return diffMs >= 0 ? `через ${label}` : `${label} назад`;
}

function renderFocusRing(snapshot) {
  const completion = snapshot.completion || {};
  const done = Number(completion.done_today) || 0;
  const tasks = state.tasks.items || [];
  const openToday = tasks.filter((item) => item.list_name === "Today").length || tasks.length;
  const total = done + openToday;
  const progress = total > 0 ? Math.min(1, done / total) : 0;
  const ring = $("ring-progress");
  ring.style.strokeDasharray = String(RING_CIRCUMFERENCE);
  ring.style.strokeDashoffset = String(RING_CIRCUMFERENCE * (1 - progress));
  $("ring-done").textContent = String(done);
  $("ring-total").textContent = total > 0 ? `из ${total}` : "пока пусто";
  $("focus-ring").classList.toggle("is-complete", total > 0 && done >= total);
  $("focus-ring").setAttribute("aria-label", `Закрыто ${done} из ${total} сегодня`);
}

function renderMomentum(snapshot) {
  const streak = Number(snapshot.completion?.streak) || 0;
  const element = $("momentum");
  if (streak >= 2) {
    element.hidden = false;
    element.replaceChildren(
      icon("flame", "icon-warm"),
      document.createTextNode(` ${streak} ${plural(streak, "день", "дня", "дней")} подряд закрываешь задачи — так держать`),
    );
  } else {
    element.hidden = true;
  }
}

function renderSparkline(id, values, tone) {
  const svg = $(id);
  if (!svg) return;
  svg.replaceChildren();
  const data = (values || []).map(Number);
  if (!data.length || data.every((item) => item === 0)) { svg.style.display = "none"; return; }
  svg.style.display = "";
  const max = Math.max(...data, 1);
  const width = 64, height = 20, pad = 2;
  const step = data.length > 1 ? (width - pad * 2) / (data.length - 1) : 0;
  const points = data.map((value, index) => [pad + index * step, height - pad - (value / max) * (height - pad * 2)]);
  const ns = "http://www.w3.org/2000/svg";
  const area = document.createElementNS(ns, "polygon");
  area.setAttribute("points", `${pad},${height - pad} ` + points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ") + ` ${points[points.length - 1][0].toFixed(1)},${height - pad}`);
  area.setAttribute("class", `spark-area ${tone}`);
  const line = document.createElementNS(ns, "polyline");
  line.setAttribute("points", points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" "));
  line.setAttribute("class", `spark-line ${tone}`);
  const dot = document.createElementNS(ns, "circle");
  dot.setAttribute("cx", points[points.length - 1][0].toFixed(1));
  dot.setAttribute("cy", points[points.length - 1][1].toFixed(1));
  dot.setAttribute("r", "2.2");
  dot.setAttribute("class", `spark-dot ${tone}`);
  svg.append(area, line, dot);
}

function eventsPerDay(events, days) {
  const today = startOfDay(new Date());
  const buckets = new Array(days).fill(0);
  (events || []).forEach((event) => {
    const diff = Math.round((startOfDay(new Date(event.start)).getTime() - today.getTime()) / 86400000);
    if (diff >= 0 && diff < days) buckets[diff] += 1;
  });
  return buckets;
}

function upcomingChargesPerDay(subscriptions, days) {
  const today = startOfDay(new Date());
  const buckets = new Array(days).fill(0);
  subscriptions.forEach((item) => {
    if (!item.next_charge_at) return;
    const diff = Math.round((startOfDay(new Date(item.next_charge_at)).getTime() - today.getTime()) / 86400000);
    if (diff >= 0 && diff < days) buckets[diff] += 1;
  });
  return buckets;
}

function showToast(message, actionLabel, actionFn) {
  const toast = $("toast");
  const actionButton = $("toast-action");
  window.clearTimeout(state.toastTimer);
  state.toastUndo = null;
  $("toast-text").textContent = message;
  if (actionLabel && typeof actionFn === "function") {
    actionButton.hidden = false;
    actionButton.textContent = actionLabel;
    state.toastUndo = actionFn;
    actionButton.onclick = () => {
      const undo = state.toastUndo;
      hideToast();
      if (undo) undo();
    };
  } else {
    actionButton.hidden = true;
    actionButton.onclick = null;
  }
  toast.hidden = false;
  requestAnimationFrame(() => toast.classList.add("is-visible"));
  state.toastTimer = window.setTimeout(hideToast, 5000);
}

function hideToast() {
  const toast = $("toast");
  toast.classList.remove("is-visible");
  window.clearTimeout(state.toastTimer);
  state.toastUndo = null;
  window.setTimeout(() => { if (!toast.classList.contains("is-visible")) toast.hidden = true; }, 200);
}

function applyTelegramTheme() {
  const params = telegram?.themeParams;
  if (!params) return;
  const root = document.documentElement.style;
  if (params.bg_color) root.setProperty("--bg", params.bg_color);
  if (params.secondary_bg_color) root.setProperty("--surface-soft", params.secondary_bg_color);
  if (params.text_color) root.setProperty("--text", params.text_color);
  if (params.hint_color) { root.setProperty("--muted", params.hint_color); root.setProperty("--faint", params.hint_color); }
  if (params.link_color) root.setProperty("--accent-bright", params.link_color);
  if (params.button_color) { root.setProperty("--accent", params.button_color); root.setProperty("--accent-strong", params.button_color); }
  if (params.button_text_color) root.setProperty("--accent-ink", params.button_text_color);
}

function initVoiceInput() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const toggle = $("voice-toggle");
  if (!SpeechRecognition || !toggle) return;
  toggle.hidden = false;
  const recognition = new SpeechRecognition();
  recognition.lang = "ru-RU";
  recognition.continuous = false;
  recognition.interimResults = true;
  let baseline = "";
  recognition.onresult = (event) => {
    let interim = "";
    for (let index = event.resultIndex; index < event.results.length; index++) interim += event.results[index][0].transcript;
    $("quick-text").value = (baseline + " " + interim).trim();
    if (event.results[event.results.length - 1].isFinal) baseline = $("quick-text").value;
  };
  recognition.onend = () => { $("voice-status").hidden = true; toggle.classList.remove("is-listening"); };
  recognition.onerror = () => { $("voice-status").hidden = true; toggle.classList.remove("is-listening"); showNotice("Не расслышал. Попробуй ещё раз или напиши руками."); };
  toggle.addEventListener("click", () => {
    if (toggle.classList.contains("is-listening")) { recognition.stop(); return; }
    baseline = $("quick-text").value.trim();
    try { recognition.start(); toggle.classList.add("is-listening"); $("voice-status").hidden = false; haptic("impact", "light"); }
    catch (error) { showNotice("Голосовой ввод недоступен в этом браузере."); }
  });
  state.voiceRecognition = recognition;
}

function initPullToRefresh() {
  let startY = null;
  const indicator = $("pull-indicator");
  document.addEventListener("touchstart", (event) => {
    if (window.scrollY <= 0 && event.touches.length === 1) startY = event.touches[0].clientY;
    else startY = null;
  }, {passive: true});
  document.addEventListener("touchmove", (event) => {
    if (startY === null) return;
    const delta = event.touches[0].clientY - startY;
    if (delta > 12 && window.scrollY <= 0) {
      indicator.style.opacity = String(Math.min(1, delta / 90));
      indicator.classList.toggle("is-ready", delta > 70);
    }
  }, {passive: true});
  document.addEventListener("touchend", () => {
    if (startY === null) return;
    const ready = indicator.classList.contains("is-ready");
    indicator.style.opacity = "0";
    indicator.classList.remove("is-ready");
    startY = null;
    if (ready) { haptic("impact", "light"); refresh().catch(() => {}); }
  }, {passive: true});
}

function initSwipeToComplete() {
  let activeRow = null, startX = 0, currentX = 0, swiping = false;
  document.addEventListener("touchstart", (event) => {
    const row = event.target.closest(".task-row");
    if (!row || event.touches.length !== 1) return;
    activeRow = row; startX = event.touches[0].clientX; currentX = startX; swiping = true;
  }, {passive: true});
  document.addEventListener("touchmove", (event) => {
    if (!swiping || !activeRow) return;
    currentX = event.touches[0].clientX;
    const delta = Math.max(0, currentX - startX);
    activeRow.style.transform = delta ? `translateX(${Math.min(delta, 90)}px)` : "";
    activeRow.classList.toggle("is-swiping", delta > 24);
  }, {passive: true});
  document.addEventListener("touchend", () => {
    if (!swiping || !activeRow) return;
    const delta = currentX - startX;
    const row = activeRow;
    swiping = false; activeRow = null;
    row.style.transform = "";
    row.classList.remove("is-swiping");
    if (delta > 70) {
      const title = row.querySelector(".task-title")?.title || row.querySelector(".task-title")?.textContent;
      if (title) { haptic("notification", "success"); preparePlan([{type: "task.done", payload: {title}}]); }
    }
  }, {passive: true});
}

async function startTelegramSession() {
  if (!telegram?.initData) { $("loading-text").textContent = "Открой кабинет кнопкой в чате с JarHert."; return; }
  telegram.ready(); telegram.expand();
  try { await request("/api/session/telegram", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({init_data: telegram.initData})}); $("loading-panel").hidden = true; $("cabinet").hidden = false; await refresh(); }
  catch (error) { $("loading-text").textContent = friendlyError(error); }
}

function init() {
  applyTelegramTheme();
  telegram?.onEvent?.("themeChanged", applyTelegramTheme);
  initVoiceInput();
  initPullToRefresh();
  initSwipeToComplete();
  $("refresh").addEventListener("click", () => { haptic("impact", "light"); refresh().catch((error) => showNotice(friendlyError(error))); });
  document.querySelectorAll("[data-view]").forEach((item) => item.addEventListener("click", () => setView(item.dataset.view, {hapticFeedback: true})));
  document.querySelectorAll("[data-open-view]").forEach((item) => item.addEventListener("click", () => setView(item.dataset.openView, {hapticFeedback: true})));
  $("quick-add").addEventListener("click", () => openQuick()); $("quick-cancel").addEventListener("click", () => $("quick-dialog").close());
  document.querySelectorAll("[data-quick-type]").forEach((item) => item.addEventListener("click", () => { state.quickType = item.dataset.quickType; haptic("selection"); updateQuickForm(); }));
  $("quick-form").addEventListener("submit", (event) => { event.preventDefault(); try { const action = quickAction(); $("quick-dialog").close(); preparePlan([action]); } catch (error) { showNotice(friendlyError(error)); } });
  $("coding-add").addEventListener("click", openCoding); $("coding-cancel").addEventListener("click", () => $("coding-dialog").close()); $("coding-mode").addEventListener("change", updateCodingForm); $("coding-form").addEventListener("submit", previewCoding);
  $("edit-form").addEventListener("submit", saveEdit); $("dialog-cancel").addEventListener("click", () => $("edit-dialog").close());
  $("plan-form").addEventListener("submit", executePlan); $("plan-cancel").addEventListener("click", cancelPlan);
  $("task-search").addEventListener("input", (event) => { state.taskQuery = event.target.value; renderTasks(); });
  $("task-list-filter").addEventListener("change", (event) => { state.taskFilter = event.target.value; renderTasks(); });
  $("task-menu-move").addEventListener("click", () => { const task = state.taskMenu; closeTaskMenu(); if (task) openTaskMove(task); });
  $("task-menu-priority").addEventListener("click", () => { const task = state.taskMenu; closeTaskMenu(); if (task) openTaskPriority(task); });
  $("task-menu-open").addEventListener("click", () => { const task = state.taskMenu; closeTaskMenu(); if (task?.url) openExternal(task.url); });
  $("note-search").addEventListener("input", (event) => scheduleNoteSearch(event.target.value));
  $("knowledge-add").addEventListener("click", openKnowledgeClip); $("clip-form").addEventListener("submit", previewKnowledgeClip); $("clip-execute").addEventListener("click", executeKnowledgeClip); $("clip-cancel").addEventListener("click", () => $("clip-dialog").close());
  $("architecture-open").addEventListener("click", openArchitecture); $("architecture-open-home").addEventListener("click", openArchitecture);
  document.querySelectorAll("[data-architecture-scenario]").forEach((item) => item.addEventListener("click", () => { haptic("selection"); showArchitectureScenario(item.dataset.architectureScenario); }));
  $("architecture-dialog").addEventListener("close", () => window.clearTimeout(architecturePlaybackTimer));
  $("open-trello").addEventListener("click", () => openExternal(state.tasks.board_url || "https://trello.com/")); $("open-calendar").addEventListener("click", () => openExternal("https://calendar.google.com/"));
  window.addEventListener("hashchange", () => setView(window.location.hash.slice(1), {syncHistory: false}));
  if (VIEWS.has(window.location.hash.slice(1))) state.activeView = window.location.hash.slice(1);
  startTelegramSession();
}
init();
