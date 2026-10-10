const CATEGORY = { clothing: "Одежда", furniture: "Мебель", tools: "Инструменты" };
const SOURCE = {
  ktru: ["КТРУ", "status-blue"],
  portal: ["Портал", "status-sea"],
  new: ["Новая", "status-orange"],
};
const STEP_DELAY = 300;
const CODE_QUESTION = "Код КТРУ";

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

function renderSteps(steps) {
  const list = $("steps");
  list.replaceChildren();
  const first = steps.length ? Math.max(steps[0].left, 1) : 1;
  steps.forEach((step, i) => {
    setTimeout(() => {
      const item = el("li", step.left === 0 ? "step step-error" : "step");
      item.append(el("span", "step-num", String(i + 1)));
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
    }, i * STEP_DELAY);
  });
}

function codeFound(code) {
  const box = el("div");
  const line = el("div", "outcome-line p2");
  line.append(el("i", "ok-icon", "✓"), "Код КТРУ подобран");
  box.append(line, el("div", "code", code));
  return box;
}

function askQuestion(question) {
  const box = el("div", "question");
  const head = el("p", "outcome-line p2");
  head.append(el("i", "mark-icon", "!"), `Уточните: ${question.char}`);
  const options = el("div", "options");
  for (const option of question.options) {
    const button = el("button", "btn btn-white", option);
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
  const last = steps.length ? steps[steps.length - 1].what : "Не нашли подходящий код КТРУ";
  notice.append(el("i", "mark-icon", "!"), last);
  box.append(notice);
  box.append(
    el("p", "p5 muted hint", "Опишите товар подробнее: что это за товар, материал, размеры, назначение."),
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

function highlight(text, chars) {
  const lower = text.toLowerCase();
  const ranges = [];
  for (const c of chars) {
    const start = c.fragment ? lower.indexOf(c.fragment.toLowerCase()) : -1;
    if (start >= 0) ranges.push([start, start + c.fragment.length]);
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const box = $("source-text");
  box.replaceChildren(el("div", "p6 muted", "Ваше описание, найденные значения подсвечены"));
  const line = el("div");
  let pos = 0;
  for (const [start, end] of ranges) {
    if (start < pos) continue;
    line.append(text.slice(pos, start), el("mark", "", text.slice(start, end)));
    pos = end;
  }
  line.append(text.slice(pos));
  box.append(line);
}

function renderChars(chars) {
  const body = $("chars");
  body.replaceChildren();
  for (const c of chars) {
    const row = el("tr");
    const [label, cls] = SOURCE[c.source] || [c.source, "status-gray"];
    const source = el("td");
    source.append(el("span", `status ${cls}`, label));
    row.append(
      el("td", "", c.name),
      el("td", "", formatValue(c.value)),
      el("td", "", c.unit ? c.unit.name : "—"),
      source,
      el("td", "fragment", c.fragment ? `«${c.fragment}»` : "—"),
    );
    body.append(row);
  }
  $("no-chars").hidden = chars.length > 0;
  body.parentElement.hidden = chars.length === 0;
}

function renderMissing(names) {
  $("missing").hidden = names.length === 0;
  const list = $("missing-list");
  list.replaceChildren();
  for (const name of names) {
    const tag = el("button", "tag", `${name} +`);
    tag.type = "button";
    tag.onclick = () => {
      input.value = `${input.value.trim()}, ${name}: `;
      input.focus();
    };
    list.append(tag);
  }
}

function renderRemarks(remarks) {
  $("remarks-card").hidden = remarks.length === 0;
  const list = $("remarks");
  list.replaceChildren();
  for (const r of remarks) {
    const item = el("li", "p5");
    item.append(el("s", "", r.was), " → ", el("b", "", r.now), el("div", "p6 muted", r.why));
    list.append(item);
  }
}

function render(text, result) {
  renderCategory(result.category);
  renderSteps(result.steps);
  renderOutcome(result);
  highlight(text, result.chars);
  renderChars(result.chars);
  renderMissing(result.missing_required);
  renderRemarks(result.remarks);
  $("result").hidden = false;
}

function answer(char, option) {
  if (char === CODE_QUESTION) {
    $("outcome").replaceChildren(codeFound(option));
    return;
  }
  input.value = `${input.value.trim()}, ${char}: ${option}`;
  send();
}

function setLoading(on) {
  submit.disabled = on;
  submit.textContent = on ? "Подбираем…" : "Подобрать";
  $("loading").hidden = !on;
}

async function send() {
  const text = input.value.trim();
  $("server-error").hidden = true;
  if (!text) {
    showFieldError("Опишите товар");
    return;
  }
  showFieldError("");
  setLoading(true);
  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await response.json();
    if (response.status === 400) showFieldError(data.error);
    else if (!response.ok) throw new Error(data.error);
    else render(text, data);
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
input.oninput = () => showFieldError("");
for (const example of document.querySelectorAll(".example")) {
  example.onclick = () => {
    input.value = example.textContent;
    send();
  };
}
