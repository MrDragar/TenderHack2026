const CATEGORY = { clothing: "Одежда", furniture: "Мебель", tools: "Инструменты" };
const SOURCE = {
  ktru: ["КТРУ", "status-blue"],
  portal: ["Портал", "status-sea"],
  new: ["Новая", "status-orange"],
};
const STEP_DELAY = 300;
const CODE_QUESTION = "Код КТРУ";
const LIVE_DELAY = 500;
const BAD = new Set(["wrong_unit", "out_of_range", "not_in_list", "conflict"]);

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
    notice.append(`Для СТЕ не хватает обязательных характеристик: ${missing}. Заполните их в таблице.`);
    box.append(notice);
  }
  return box;
}

function askQuestion(question) {
  const box = el("div", "question");
  const head = el("p", "outcome-line p2");
  const isCode = question.char === CODE_QUESTION;
  const title = isCode
    ? "Характеристиками коды не различить, выберите код"
    : `Уточните: ${question.char}`;
  head.append(el("i", "mark-icon", "!"), title);
  const options = el("div", isCode ? "options options-codes" : "options");
  for (const option of question.options) {
    const label = isCode ? `${option} ${codeName(option)}` : option;
    const button = el("button", "btn btn-white", label);
    button.type = "button";
    button.onclick = () => answer(question.char, option);
    options.append(button);
  }
  box.append(head, options);
  box.append(el("p", "p6 hint", "Выберите вариант или допишите его в описание сами."));
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
  else if (result.question) node = askQuestion(result.question);
  else node = notFound(result.steps);
  $("outcome").replaceChildren(node);
}

function renderCategory(category) {
  const badge = $("category");
  badge.className = category ? "status status-blue" : "status status-gray";
  badge.textContent = category ? CATEGORY[category] : "Категория не определена";
}

function markClass(c) {
  if (BAD.has(c.kind)) return "mark-bad";
  if (c.kind) return "mark-fix";
  if (c.source === "new") return "mark-new";
  return "mark-ok";
}

function highlight(text, result) {
  const chars = [...result.chars].sort((a, b) => a.start - b.start);
  const box = $("source-text");
  box.replaceChildren(el("div", "p6 muted", "Ваше описание: зелёным понятое, жёлтым исправленное, красным подозрительное"));
  const line = el("div");
  let pos = 0;
  for (const c of chars) {
    if (c.start < pos || c.end <= c.start) continue;
    const mark = el("mark", markClass(c), text.slice(c.start, c.end));
    mark.title = c.fix || `${c.name}: ${formatValue(c.value)}`;
    line.append(text.slice(pos, c.start), mark);
    pos = c.end;
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
  const options = last.result.options[name];
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
  for (const c of result.chars) {
    const row = el("tr", c.kind ? `row-${markClass(c)}` : "");
    const [label, cls] = SOURCE[c.source] || [c.source, "status-gray"];
    const source = el("td");
    source.append(el("span", `status ${cls}`, label));
    const value = el("td");
    value.append(valueEditor(c.name, String(c.value), (v) => rewrite(c.start, c.end, `${c.name}: ${v}`)));
    const remove = el("td");
    remove.append(removeButton(() => rewrite(c.start, c.end, "")));
    row.append(nameCell(c.name, c.required), el("td", "fragment", `«${c.fragment}»`), value, el("td", "", c.unit ? c.unit.name : "—"), source, remove);
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
    item.append(el("s", "", r.was), " → ", el("b", "", r.now), el("div", "p6 muted", r.why));
    list.append(item);
  }
}

function render(text, result, animate) {
  last = { text, result };
  renderCategory(result.category);
  renderSteps(result.steps, animate);
  renderOutcome(result);
  highlight(text, result);
  renderChars(result);
  renderMissing(result.missing_required);
  renderRemarks(result.remarks);
  $("result").hidden = false;
}

// Правка таблицы переписывает описание: текст остаётся единственным источником правды.
function rewrite(start, end, replacement) {
  const text = last.text;
  const fragment = text.slice(start, end);
  // Кусок внутри фразы («столешница ЛДСП») убираем, а пару дописываем отдельно в конец.
  if (replacement && !fragment.includes(":")) {
    input.value = tidy(`${text.slice(0, start)}${text.slice(end)}, ${replacement}`);
  } else {
    input.value = tidy(text.slice(0, start) + replacement + text.slice(end));
  }
  send(false);
}

function setChar(name, value) {
  const found = last.result.chars.find((c) => c.name === name);
  if (found) rewrite(found.start, found.end, `${name}: ${value}`);
  else append(`${name}: ${value}`);
}

function append(part) {
  input.value = tidy(`${input.value.trim()}, ${part}`);
  send(false);
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
    $("outcome").replaceChildren(codeFound(option));
    return;
  }
  setChar(char, option);
}

function setLoading(on) {
  submit.disabled = on;
  submit.textContent = on ? "Подбираем…" : "Подобрать";
  $("loading").hidden = !on;
}

async function send(animate = true) {
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
      body: JSON.stringify({ text }),
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
    setLoading(false);
  }
}

form.onsubmit = (event) => {
  event.preventDefault();
  send();
};
input.oninput = () => {
  showFieldError("");
  clearTimeout(timer);
  if (input.value.trim().length >= 3) timer = setTimeout(() => send(false), LIVE_DELAY);
};
for (const example of document.querySelectorAll(".example")) {
  example.onclick = () => {
    input.value = example.textContent;
    send();
  };
}
