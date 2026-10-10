const CATEGORY = { clothing: "Одежда", furniture: "Мебель", tools: "Инструменты" };
const SOURCE = {
  ktru: ["КТРУ", "status-blue"],
  portal: ["Портал", "status-sea"],
  new: ["Новая", "status-orange"],
  learned: ["Из опыта", "status-green"],
};
const STATUS = new Set(["ready", "needs_input", "portal_only", "learned_only", "not_found"]);
const STEP_DELAY = 300;
const CODE_QUESTION = "Код КТРУ";
const LIVE_DELAY = 500;
const BAD = new Set([
  "wrong_unit",
  "out_of_range",
  "not_in_list",
  "conflict",
  "garbage",
  "incompatible",
  "rejected",
]);
const LEARNING_ACTION = {
  saved: ["Сохранена", "status-green"],
  learned: ["Сохранена", "status-green"],
  created: ["Сохранена", "status-green"],
  updated: ["Обновлена", "status-green"],
  reused: ["Использована снова", "status-blue"],
  known: ["Уже известна", "status-blue"],
  already_known: ["Уже известна", "status-blue"],
  pending: ["Нужно подтверждение", "status-orange"],
  skipped: ["Не сохранена", "status-gray"],
  rejected: ["Не сохранена", "status-red"],
  failed: ["Не удалось сохранить", "status-red"],
};
const REJECTED_KIND = {
  garbage: "Мусор",
  irrelevant: "Не относится к товару",
  unmatched: "Не распознано",
  invalid: "Некорректное значение",
};

let last = { text: "", result: null };
let pending = 0;
let timer = 0;
let rendered = 0;

const $ = (id) => document.getElementById(id);
const form = $("form");
const input = $("text");
const submit = $("submit");

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function codesWord(n) {
  const last = n % 10;
  const lastTwo = n % 100;
  if (last === 1 && lastTwo !== 11) return "код";
  if (last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14)) return "кода";
  return "кодов";
}

function formatValue(value) {
  return typeof value === "number" ? value.toLocaleString("ru-RU") : value;
}

function inferStatus(result, questions) {
  if (STATUS.has(result.status)) return result.status;
  if (result.code) return "ready";
  if (questions.length) return "needs_input";
  return "not_found";
}

function normalizeResult(result) {
  const chars = Array.isArray(result.chars)
    ? result.chars
    : Array.isArray(result.characteristics)
      ? result.characteristics
      : [];
  const questions = Array.isArray(result.questions) && result.questions.length
    ? result.questions
    : result.question
      ? [result.question]
      : [];
  const edits = Array.isArray(result.edits) && result.edits.length
    ? result.edits
    : Array.isArray(result.remarks)
      ? result.remarks
      : [];
  return {
    ...result,
    status: inferStatus(result, questions),
    steps: Array.isArray(result.steps) ? result.steps : [],
    chars,
    candidates: Array.isArray(result.candidates) ? result.candidates : [],
    missing_required: Array.isArray(result.missing_required) ? result.missing_required : [],
    options: result.options && typeof result.options === "object" ? result.options : {},
    unknown: Array.isArray(result.unknown) ? result.unknown : [],
    questions,
    edits,
    rejected: Array.isArray(result.rejected) ? result.rejected : [],
    learning: Array.isArray(result.learning) ? result.learning : [],
  };
}

function showFieldError(message) {
  $("field-error").textContent = message;
  $("field-error").hidden = !message;
  input.classList.toggle("error", Boolean(message));
}

function renderSteps(steps, animate) {
  const list = $("steps");
  list.replaceChildren();
  // Отложенные шаги прошлого ответа не должны дописаться в новый список.
  const generation = ++rendered;
  const first = steps.length ? Math.max(steps[0].left, 1) : 1;
  steps.forEach((step, i) => {
    const show = () => {
      if (generation !== rendered) return;
      let cls = "step";
      if (step.left === 0) cls += " step-error";
      if (step.ignored) cls += " step-warn";
      const item = el("li", cls);
      item.append(el("span", "step-num", step.ignored ? "!" : String(i + 1)));
      const body = el("div", "step-body");
      body.append(el("div", "step-what", step.what));
      body.append(el("div", "p6 muted step-left", `Осталось ${step.left} ${codesWord(step.left)}`));
      const bar = el("div", "progress");
      const fill = el("span");
      fill.style.width = `${Math.max((step.left / first) * 100, step.left ? 3 : 0)}%`;
      bar.append(fill);
      body.append(bar);
      item.append(body);
      list.append(item);
    };
    if (animate) setTimeout(show, i * STEP_DELAY);
    else show();
  });
}

function codeName(code) {
  const found = (last.result.candidates || []).find((c) => c.code === code);
  return found ? found.name : "";
}

function codeFound(code) {
  const box = el("div");
  const line = el("div", "outcome-line p2");
  line.append(el("i", "ok-icon", "✓"), "Код КТРУ подобран");
  box.append(line, el("div", "code", code), el("div", "p4 code-name", codeName(code)));
  const missing = last.result.missing_required.length;
  if (missing) {
    const notice = el("div", "notice notice-warn p5");
    notice.append(`Для карточки не хватает обязательных характеристик: ${missing}. Заполните их в таблице.`);
    box.append(notice);
  }
  return box;
}

function questionName(question) {
  const name = question.char || question.name || question.characteristic || "";
  return String(name).replace(/\s*:\s*$/, "");
}

function questionPrompt(question) {
  if (question.prompt) return question.prompt;
  const name = questionName(question);
  if (name === CODE_QUESTION) return "Характеристиками коды не различить, выберите код";
  return name ? `Уточните: ${name}` : "Уточните описание товара";
}

function optionValue(option) {
  if (option && typeof option === "object") {
    return option.value ?? option.code ?? option.label ?? option.name ?? "";
  }
  return option;
}

function optionLabel(option, isCode) {
  if (option && typeof option === "object" && option.label) return option.label;
  const value = optionValue(option);
  return isCode ? `${value} ${codeName(value)}`.trim() : String(value);
}

function questionBlock(question) {
  const box = el("div", "question");
  const head = el("p", "outcome-line p2");
  const name = questionName(question);
  const isCode = name === CODE_QUESTION;
  head.append(el("i", "mark-icon", "!"), questionPrompt(question));
  const options = el("div", isCode ? "options options-codes" : "options");
  for (const option of question.options || []) {
    const value = optionValue(option);
    const button = el("button", "btn btn-white", optionLabel(option, isCode));
    button.type = "button";
    button.onclick = () => answer(name, value);
    options.append(button);
  }
  box.append(head);
  if (options.childElementCount) {
    box.append(options);
  } else if (name) {
    const answerRow = el("div", "question-answer");
    const field = el("input", "input input-white");
    field.placeholder = "Введите значение";
    const button = el("button", "btn btn-white", "Добавить");
    button.type = "button";
    const submitAnswer = () => {
      const value = field.value.trim();
      if (value) answer(name, value);
    };
    button.onclick = submitAnswer;
    field.onkeydown = (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        submitAnswer();
      }
    };
    answerRow.append(field, button);
    box.append(answerRow);
  }
  if (question.reason) box.append(el("p", "p6 question-reason", question.reason));
  box.append(el("p", "p6 hint", "Ответ добавится в описание, и результат пересчитается."));
  return box;
}

function needsInput() {
  const box = el("div");
  const line = el("div", "outcome-line p2");
  line.append(el("i", "mark-icon", "!"), "Нужно уточнить описание");
  box.append(line, el("p", "p5 muted hint", "Ответьте на вопросы ниже, чтобы завершить подбор."));
  return box;
}

function portalOnly() {
  const box = el("div");
  const line = el("div", "outcome-line p2");
  line.append(el("i", "ok-icon", "✓"), "Характеристики подобраны по Порталу");
  box.append(
    line,
    el("p", "p5 muted hint", "Подходящего кода КТРУ нет, поэтому код не придуман."),
  );
  return box;
}

function readyWithoutCode() {
  const box = el("div");
  const line = el("div", "outcome-line p2");
  line.append(el("i", "ok-icon", "✓"), "Характеристики подготовлены");
  box.append(line);
  return box;
}

function learnedOnly() {
  const box = el("div");
  const line = el("div", "outcome-line p2");
  line.append(el("i", "ok-icon", "✓"), "Характеристики подготовлены по локальной схеме");
  box.append(
    line,
    el(
      "p",
      "p5 muted hint",
      "Подходящего кода КТРУ и категории Портала нет. Новые поля показаны отдельно.",
    ),
  );
  return box;
}

function notFound(steps) {
  const box = el("div");
  const notice = el("div", "notice notice-error");
  const lastStep = steps.length ? steps[steps.length - 1].what : "Не нашли подходящий код КТРУ";
  notice.append(el("i", "mark-icon", "!"), lastStep);
  box.append(notice);
  box.append(
    el(
      "p",
      "p5 muted hint",
      "Начните с того, что это за товар: стол, кресло, халат, молоток. Затем материал, размеры, назначение.",
    ),
  );
  return box;
}

function renderOutcome(result) {
  let node;
  if (result.code) node = codeFound(result.code);
  else if (result.status === "portal_only") node = portalOnly();
  else if (result.status === "learned_only") node = learnedOnly();
  else if (result.status === "ready") node = readyWithoutCode();
  else if (result.status === "needs_input" || result.questions.length) node = needsInput();
  else node = notFound(result.steps);
  $("outcome").replaceChildren(node);
}

function renderQuestions(questions) {
  $("questions-card").hidden = questions.length === 0;
  const box = $("questions");
  box.replaceChildren();
  for (const question of questions) box.append(questionBlock(question));
}

function renderCategory(category) {
  const badge = $("category");
  badge.className = category ? "status status-blue" : "status status-gray";
  badge.textContent = category ? CATEGORY[category] : "Категория не определена";
}

function markClass(c) {
  if (c.status === "rejected") return "mark-bad";
  if (c.status === "needs_confirmation") return "mark-fix";
  if (BAD.has(c.kind)) return "mark-bad";
  if (c.kind) return "mark-fix";
  if (c.source === "new") return "mark-new";
  if (c.source === "learned") return "mark-learned";
  return "mark-ok";
}

function validSpan(item, text) {
  return (
    Number.isInteger(item.start) &&
    Number.isInteger(item.end) &&
    item.start >= 0 &&
    item.end > item.start &&
    item.end <= text.length
  );
}

function highlightSpans(text, result) {
  const spans = [];
  for (const char of result.chars) {
    if (!validSpan(char, text)) continue;
    spans.push({
      ...char,
      className: markClass(char),
      title: char.fix || `${char.name}: ${formatValue(char.value)}`,
      priority: 1,
    });
  }
  for (const edit of result.edits) {
    if (!validSpan(edit, text)) continue;
    spans.push({
      ...edit,
      className: BAD.has(edit.kind) ? "mark-bad" : "mark-fix",
      title: edit.why || `${edit.was || text.slice(edit.start, edit.end)} → ${edit.now || "удалено"}`,
      priority: 2,
    });
  }
  for (const rejected of result.rejected) {
    if (!validSpan(rejected, text)) continue;
    spans.push({
      ...rejected,
      className: "mark-bad",
      title: rejected.reason || "Не относится к товару",
      priority: 3,
    });
  }

  const selected = [];
  spans.sort((a, b) => b.priority - a.priority || a.start - b.start || b.end - a.end);
  for (const span of spans) {
    const overlaps = selected.some((kept) => span.start < kept.end && kept.start < span.end);
    if (!overlaps) selected.push(span);
  }
  return selected.sort((a, b) => a.start - b.start);
}

function highlight(text, result) {
  const spans = highlightSpans(text, result);
  const box = $("source-text");
  box.replaceChildren(
    el(
      "div",
      "p6 muted",
      "Ваше описание: зелёным понятое, жёлтым исправленное, красным отклонённое",
    ),
  );
  const line = el("div");
  let pos = 0;
  for (const span of spans) {
    const mark = el("mark", span.className, text.slice(span.start, span.end));
    mark.title = span.title;
    line.append(text.slice(pos, span.start), mark);
    pos = span.end;
  }
  line.append(text.slice(pos));
  box.append(line);
  if (result.unknown.length) {
    box.append(
      el("div", "p6 unknown", `Не поняли, к чему относится: ${result.unknown.join(", ")}. Это слово не потеряно, допишите название характеристики через двоеточие.`),
    );
  }
}

function valueEditor(name, current, onPick) {
  const options = last.result.options[name] || [];
  if (options && options.length) {
    const select = el("select", "input input-white cell-input");
    if (!options.includes(current)) select.append(new Option(current || "Выберите", current || ""));
    for (const option of options) select.append(new Option(option, option));
    select.value = current || "";
    select.onchange = () => select.value && onPick(select.value);
    return select;
  }
  const field = el("input", "input input-white cell-input");
  field.value = current;
  field.placeholder = "Впишите значение";
  field.onchange = () => field.value.trim() && onPick(field.value.trim());
  return field;
}

function nameCell(name, required) {
  const cell = el("td", "", name);
  cell.append(el("div", "p6 muted", required ? "обязательная по КТРУ" : "необязательная"));
  return cell;
}

function removeButton(onClick) {
  const button = el("button", "icon-btn", "✕");
  button.type = "button";
  button.title = "Удалить характеристику из описания";
  button.onclick = onClick;
  return button;
}

function renderChars(result) {
  const body = $("chars");
  body.replaceChildren();
  $("no-chars").hidden = result.chars.length > 0 || result.missing_required.length > 0;
  for (const c of result.chars) {
    const row = el("tr", c.kind ? `row-${markClass(c)}` : "");
    const [label, cls] = SOURCE[c.source] || [c.source, "status-gray"];
    const source = el("td");
    source.append(el("span", `status ${cls}`, label));
    const value = el("td");
    value.append(
      valueEditor(c.name, c.value == null ? "" : String(c.value), (v) =>
        rewrite(c.start, c.end, `${c.name}: ${v}`),
      ),
    );
    const remove = el("td");
    remove.append(removeButton(() => rewrite(c.start, c.end, "")));
    const unit = typeof c.unit === "string" ? c.unit : c.unit?.name;
    row.append(
      nameCell(c.name, c.required),
      el("td", "fragment", `«${c.fragment || "нет фрагмента"}»`),
      value,
      el("td", "", unit || "—"),
      source,
      remove,
    );
    body.append(row);
    if (c.fix) {
      const note = el("tr", `fix-row ${markClass(c)}`);
      const cell = el("td");
      cell.colSpan = 6;
      cell.append(el("s", "", c.fragment), " → ", el("b", "", `${formatValue(c.value)}${c.unit ? ` ${c.unit.name.toLowerCase()}` : ""}`), `: ${c.fix}`);
      note.append(cell);
      body.append(note);
    }
  }
  for (const name of result.missing_required) {
    const row = el("tr", "row-missing");
    const value = el("td");
    value.append(valueEditor(name, "", (v) => append(`${name}: ${v}`)));
    const badge = el("td");
    badge.append(el("span", "status status-red", "Обязательная"));
    row.append(nameCell(name, true), el("td", "fragment", "нет в тексте"), value, el("td", "", "—"), badge, el("td"));
    body.append(row);
  }
  body.append(addRow());
}

function addRow() {
  const row = el("tr", "row-add");
  const name = el("input", "input input-white cell-input");
  name.placeholder = "Своя характеристика";
  const value = el("input", "input input-white cell-input");
  value.placeholder = "Значение";
  const button = el("button", "btn btn-white", "Добавить");
  button.type = "button";
  button.onclick = () => {
    if (name.value.trim() && value.value.trim()) append(`${name.value.trim()}: ${value.value.trim()}`);
  };
  const nameCell = el("td");
  nameCell.append(name);
  const valueCell = el("td");
  valueCell.append(value);
  const buttonCell = el("td");
  buttonCell.colSpan = 4;
  buttonCell.append(button);
  row.append(nameCell, el("td"), valueCell, buttonCell);
  return row;
}

function renderMissing(names) {
  $("missing").hidden = names.length === 0;
  $("missing-list").textContent = names.join(", ");
}

function renderRemarks(remarks) {
  $("remarks-card").hidden = remarks.length === 0;
  const list = $("remarks");
  list.replaceChildren();
  for (const r of remarks) {
    const item = el("li", `p5 ${BAD.has(r.kind) ? "remark-bad" : ""}`);
    item.append(
      el("s", "", r.was || "исходный фрагмент"),
      " → ",
      el("b", "", r.now || "удалено"),
      el("div", "p6 muted", r.why || "Значение приведено к унифицированному виду"),
    );
    list.append(item);
  }
}

function renderCorrected(text) {
  const shown = typeof text === "string" && text.trim();
  $("corrected-card").hidden = !shown;
  $("corrected-text").textContent = shown ? text : "";
}

function renderRejected(rejected) {
  $("rejected-card").hidden = rejected.length === 0;
  const list = $("rejected");
  list.replaceChildren();
  for (const rejectedItem of rejected) {
    const item = el("li", "rejected-item");
    const fragment = rejectedItem.fragment || "Фрагмент без текста";
    const head = el("div", "rejected-head");
    head.append(el("div", "rejected-fragment p5", `«${fragment}»`));
    head.append(
      el("span", "status status-red", REJECTED_KIND[rejectedItem.kind] || "Отклонено"),
    );
    item.append(
      head,
      el("div", "p6", rejectedItem.reason || "Не относится к выбранному товару"),
    );
    list.append(item);
  }
}

function learningContext(context) {
  if (!context) return "";
  if (typeof context === "string") return context;
  if (typeof context !== "object") return String(context);
  return Object.values(context)
    .filter((value) => value !== null && value !== undefined && value !== "")
    .join(", ");
}

function renderLearning(learning) {
  $("learning-card").hidden = learning.length === 0;
  const list = $("learning");
  list.replaceChildren();
  for (const learned of learning) {
    const item = el("li", "learning-item");
    const action = LEARNING_ACTION[learned.action] || [learned.action || "Обработана", "status-gray"];
    const head = el("div", "learning-head");
    head.append(el("div", "p4 learning-name", learned.name || "Новая характеристика"));
    head.append(el("span", `status ${action[1]}`, action[0]));
    item.append(head);
    const context = learningContext(learned.context);
    if (context) item.append(el("div", "p6 muted", context));
    if (learned.reason) item.append(el("div", "p6 learning-reason", learned.reason));
    list.append(item);
  }
}

function render(text, result, animate) {
  result = normalizeResult(result);
  last = { text, result };
  renderCategory(result.category);
  renderSteps(result.steps, animate);
  renderOutcome(result);
  renderQuestions(result.questions);
  highlight(text, result);
  renderChars(result);
  renderMissing(result.missing_required);
  renderCorrected(result.corrected_text);
  renderRemarks(result.edits);
  renderRejected(result.rejected);
  renderLearning(result.learning);
  $("result").hidden = false;
}

// Правка таблицы переписывает описание: текст остаётся единственным источником правды.
function rewrite(start, end, replacement) {
  const text = last.text;
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end < start) return;
  const fragment = text.slice(start, end);
  // Кусок внутри фразы («столешница ЛДСП») убираем, а пару дописываем отдельно в конец.
  if (replacement && !fragment.includes(":")) {
    input.value = tidy(`${text.slice(0, start)}${text.slice(end)}, ${replacement}`);
  } else {
    input.value = tidy(text.slice(0, start) + replacement + text.slice(end));
  }
  send(false, false);
}

function setChar(name, value) {
  const found = last.result.chars.find((c) => c.name === name);
  if (found) rewrite(found.start, found.end, `${name}: ${value}`);
  else append(`${name}: ${value}`);
}

function append(part) {
  input.value = tidy(`${input.value.trim()}, ${part}`);
  send(false, false);
}

function tidy(text) {
  return text
    .replace(/\s+,/g, ",")
    .replace(/,(\s*,)+/g, ",")
    .replace(/^\s*,\s*|\s*,\s*$/g, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}

function answer(char, option) {
  if (char === CODE_QUESTION) {
    last.result.code = option;
    last.result.questions = last.result.questions.filter(
      (question) => questionName(question) !== CODE_QUESTION,
    );
    $("outcome").replaceChildren(codeFound(option));
    renderQuestions(last.result.questions);
    return;
  }
  setChar(char, option);
}

function setLoading(on) {
  submit.disabled = on;
  submit.textContent = on ? "Подбираем…" : "Подобрать";
  $("loading").hidden = !on;
}

async function send(animate = true, learn = false) {
  const text = input.value.trim();
  $("server-error").hidden = true;
  if (!text) {
    showFieldError("Опишите товар");
    return;
  }
  const id = ++pending;
  showFieldError("");
  setLoading(true);
  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, learn }),
    });
    const data = await response.json();
    // Ответ на устаревший текст приходит позже свежего, когда человек печатает быстро.
    if (id !== pending) return;
    if (response.status === 400) showFieldError(data.error);
    else if (!response.ok) throw new Error(data.error);
    else render(text, data, animate);
  } catch (error) {
    // TypeError и SyntaxError значат, что сервер не ответил JSON: свой текст понятнее браузерного.
    const fromServer = error.name === "Error" && error.message;
    $("server-error").textContent = fromServer || "Сервер не отвечает, попробуйте ещё раз";
    $("server-error").hidden = false;
  } finally {
    if (id === pending) setLoading(false);
  }
}

form.onsubmit = (event) => {
  event.preventDefault();
  clearTimeout(timer);
  send(true, true);
};
input.oninput = () => {
  showFieldError("");
  clearTimeout(timer);
  if (input.value.trim().length >= 3) {
    timer = setTimeout(() => send(false, false), LIVE_DELAY);
  }
};
for (const example of document.querySelectorAll(".example")) {
  example.onclick = () => {
    input.value = example.textContent;
    send(true, false);
  };
}
