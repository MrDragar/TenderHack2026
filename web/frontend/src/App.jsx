import { useCallback, useEffect, useRef, useState } from "react";

const CATEGORIES = {
  clothing: "Одежда",
  furniture: "Мебель",
  tools: "Инструменты",
};
const SOURCES = {
  ktru: ["КТРУ", "status-blue"],
  portal: ["Портал", "status-sea"],
  new: ["Новая", "status-orange"],
};
const EXAMPLES = [
  "Стол офисный, ДСП, высота метр десять, цвет красный",
  "Молоток слесарный 500 г",
  "Стол письменный, столешница ЛДСП",
  "Джемпер рост 1,7",
  "Блузка женская, рост 300 см",
  "cnjk письменный деревяный, столешница 2,2 см",
  "Стол письменный, Тип каркаса: пластик, цвет: красный",
];
const BAD_KINDS = new Set(["wrong_unit", "out_of_range", "not_in_list", "conflict"]);
const CODE_QUESTION = "Код КТРУ";
const LIVE_DELAY = 500;
const STEP_DELAY = 300;

function codesWord(number) {
  const last = number % 10;
  const lastTwo = number % 100;
  if (last === 1 && lastTwo !== 11) return "код";
  if (last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14)) return "кода";
  return "кодов";
}

function formatValue(value) {
  return typeof value === "number" ? value.toLocaleString("ru-RU") : value;
}

function tidy(text) {
  return text
    .replace(/\s+,/g, ",")
    .replace(/,(\s*,)+/g, ",")
    .replace(/^\s*,\s*|\s*,\s*$/g, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}

function markClass(characteristic) {
  if (BAD_KINDS.has(characteristic.kind)) return "mark-bad";
  if (characteristic.kind) return "mark-fix";
  if (characteristic.source === "new") return "mark-new";
  return "mark-ok";
}

function Header() {
  return (
    <header className="header">
      <div className="topbar">
        <a href="https://zakupki.mos.ru/ru">
          <img src="/logo.svg" alt="Портал поставщиков" width="179" height="40" />
        </a>
        <a className="burger" href="https://zakupki.mos.ru/ru">
          <img src="/icons/menu.svg" alt="Меню" width="30" height="30" />
        </a>
        <nav className="top-links" aria-label="Сервисы портала">
          <HeaderLink icon="search" label="Поиск" />
          <HeaderLink icon="message" label="Оставить обращение" />
          <HeaderLink icon="support" label="Центр поддержки" support />
          <HeaderLink icon="cart" label="Корзина" />
          <span className="lang" title="Русский"><i className="flag-ru" /></span>
        </nav>
        <div className="auth">
          <a className="btn btn-text" href="https://zakupki.mos.ru/ru">Войти</a>
          <a className="btn btn-red" href="https://zakupki.mos.ru/ru">Зарегистрироваться</a>
        </div>
      </div>
    </header>
  );
}

function HeaderLink({ icon, label, support = false }) {
  const href = support
    ? "https://zakupki.mos.ru/knowledgebase/main"
    : "https://zakupki.mos.ru/ru";
  const size = icon === "message" ? 24 : icon === "support" ? 31 : 28;
  return (
    <a className="top-link" href={href}>
      <img src={`/icons/${icon}.svg`} alt="" width={size} height={size} />
      <span>{label}</span>
    </a>
  );
}

function SearchForm({ text, setText, fieldError, loading, onSubmit, onExample }) {
  return (
    <form className="card" noValidate onSubmit={onSubmit}>
      <div className="label-row">
        <label className="label" htmlFor="text">Описание товара</label>
        {fieldError && <span className="tooltip-error" role="alert">{fieldError}</span>}
      </div>
      <div className="search">
        <input
          id="text"
          className={`input input-white${fieldError ? " error" : ""}`}
          maxLength="1000"
          autoComplete="off"
          placeholder="Например: стол письменный деревянный, столешница ЛДСП 22 мм"
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
        <button className="btn btn-red" type="submit" disabled={loading}>
          {loading ? "Подбираем…" : "Подобрать"}
        </button>
      </div>
      <div className="examples p6 muted">
        Примеры:
        {EXAMPLES.map((example) => (
          <button key={example} type="button" className="example" onClick={() => onExample(example)}>
            {example}
          </button>
        ))}
      </div>
      {loading && <div className="progress loading" aria-label="Идёт подбор"><span /></div>}
    </form>
  );
}

function Steps({ steps, animate }) {
  const [visible, setVisible] = useState(animate ? 0 : steps.length);

  useEffect(() => {
    setVisible(animate ? 0 : steps.length);
    if (!animate) return undefined;
    const timers = steps.map((_, index) => (
      window.setTimeout(() => setVisible(index + 1), index * STEP_DELAY)
    ));
    return () => timers.forEach(window.clearTimeout);
  }, [steps, animate]);

  const first = steps.length ? Math.max(steps[0].left, 1) : 1;
  return (
    <section className="card steps-card">
      <div className="h7">Как сужали коды</div>
      <ol className="steps">
        {steps.slice(0, visible).map((step, index) => {
          const className = [
            "step",
            step.left === 0 ? "step-error" : "",
            step.ignored ? "step-warn" : "",
          ].filter(Boolean).join(" ");
          const width = Math.max((step.left / first) * 100, step.left ? 3 : 0);
          return (
            <li className={className} key={`${step.what}-${index}`}>
              <span className="step-num">{step.ignored ? "!" : index + 1}</span>
              <div className="step-body">
                <div className="step-what">{step.what}</div>
                <div className="p6 muted step-left">
                  Осталось {step.left} {codesWord(step.left)}
                </div>
                <div className="progress"><span style={{ width: `${width}%` }} /></div>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function SourceText({ text, result }) {
  const parts = [];
  let position = 0;
  [...result.chars].sort((a, b) => a.start - b.start).forEach((characteristic) => {
    if (characteristic.start < position || characteristic.end <= characteristic.start) return;
    parts.push(text.slice(position, characteristic.start));
    parts.push(
      <mark
        className={markClass(characteristic)}
        title={characteristic.fix || `${characteristic.name}: ${formatValue(characteristic.value)}`}
        key={`${characteristic.start}-${characteristic.end}`}
      >
        {text.slice(characteristic.start, characteristic.end)}
      </mark>,
    );
    position = characteristic.end;
  });
  parts.push(text.slice(position));

  return (
    <div className="source-text p4">
      <div className="p6 muted">
        Ваше описание: зелёным понятое, жёлтым исправленное, красным подозрительное
      </div>
      <div>{parts}</div>
      {result.unknown.length > 0 && (
        <div className="p6 unknown">
          Не поняли, к чему относится: {result.unknown.join(", ")}. Это слово не потеряно,
          допишите название характеристики через двоеточие.
        </div>
      )}
    </div>
  );
}

function Outcome({ result, selectedCode, onAnswer }) {
  const code = selectedCode || result.code;
  const codeName = useCallback((value) => (
    result.candidates?.find((candidate) => candidate.code === value)?.name || ""
  ), [result.candidates]);

  if (code) {
    return (
      <div>
        <div className="outcome-line p2"><i className="ok-icon">✓</i>Код КТРУ подобран</div>
        <div className="code">{code}</div>
        <div className="p4 code-name">{codeName(code)}</div>
        {result.missing_required.length > 0 && (
          <div className="notice notice-warn p5">
            Для СТЕ не хватает обязательных характеристик: {result.missing_required.length}.
            Заполните их в таблице.
          </div>
        )}
      </div>
    );
  }

  if (result.question) {
    const isCode = result.question.char === CODE_QUESTION;
    return (
      <div className="question">
        <p className="outcome-line p2">
          <i className="mark-icon">!</i>
          {isCode ? "Характеристиками коды не различить, выберите код" : `Уточните: ${result.question.char}`}
        </p>
        <div className={isCode ? "options options-codes" : "options"}>
          {result.question.options.map((option) => (
            <button className="btn btn-white" type="button" key={option} onClick={() => onAnswer(result.question.char, option)}>
              {isCode ? `${option} ${codeName(option)}` : option}
            </button>
          ))}
        </div>
        <p className="p6 hint">Выберите вариант или допишите его в описание сами.</p>
      </div>
    );
  }

  const message = result.steps.at(-1)?.what || "Не нашли подходящий код КТРУ";
  return (
    <div>
      <div className="notice notice-error"><i className="mark-icon">!</i>{message}</div>
      <p className="p5 muted hint">
        Начните с того, что это за товар: стол, кресло, халат, молоток. Затем материал,
        размеры, назначение.
      </p>
    </div>
  );
}

function ValueEditor({ name, current, options, onPick }) {
  if (options?.length) {
    const values = options.includes(current) ? options : [current || "", ...options];
    return (
      <select className="input input-white cell-input" defaultValue={current || ""} onChange={(event) => event.target.value && onPick(event.target.value)}>
        {values.map((option) => <option value={option} key={option}>{option || "Выберите"}</option>)}
      </select>
    );
  }
  return (
    <input
      className="input input-white cell-input"
      defaultValue={current}
      placeholder="Впишите значение"
      aria-label={`Значение характеристики ${name}`}
      onBlur={(event) => event.target.value.trim() && event.target.value.trim() !== current && onPick(event.target.value.trim())}
    />
  );
}

function NameCell({ name, required }) {
  return (
    <td>
      {name}
      <div className="p6 muted">{required ? "обязательная по КТРУ" : "необязательная"}</div>
    </td>
  );
}

function AddCharacteristic({ onAppend }) {
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const add = () => {
    if (!name.trim() || !value.trim()) return;
    onAppend(`${name.trim()}: ${value.trim()}`);
    setName("");
    setValue("");
  };
  return (
    <tr className="row-add">
      <td><input className="input input-white cell-input" placeholder="Своя характеристика" value={name} onChange={(event) => setName(event.target.value)} /></td>
      <td />
      <td><input className="input input-white cell-input" placeholder="Значение" value={value} onChange={(event) => setValue(event.target.value)} /></td>
      <td colSpan="4"><button className="btn btn-white" type="button" onClick={add}>Добавить</button></td>
    </tr>
  );
}

function Characteristics({ result, onRewrite, onAppend }) {
  return (
    <section className="card">
      <h4>Характеристики</h4>
      <div className="table-wrap">
        <table className="table chars">
          <thead>
            <tr><th>Характеристика</th><th>Из текста</th><th>Значение</th><th>Ед. измерения</th><th>Источник</th><th /></tr>
          </thead>
          <tbody>
            {result.chars.map((characteristic) => {
              const [source, sourceClass] = SOURCES[characteristic.source] || [characteristic.source, "status-gray"];
              const kindClass = characteristic.kind ? `row-${markClass(characteristic)}` : "";
              return (
                <CharacterRows
                  characteristic={characteristic}
                  kindClass={kindClass}
                  source={source}
                  sourceClass={sourceClass}
                  options={result.options[characteristic.name]}
                  onRewrite={onRewrite}
                  key={`${characteristic.name}-${characteristic.start}-${characteristic.end}`}
                />
              );
            })}
            {result.missing_required.map((name) => (
              <tr className="row-missing" key={name}>
                <NameCell name={name} required />
                <td className="fragment">нет в тексте</td>
                <td><ValueEditor name={name} current="" options={result.options[name]} onPick={(value) => onAppend(`${name}: ${value}`)} /></td>
                <td>—</td>
                <td><span className="status status-red">Обязательная</span></td>
                <td />
              </tr>
            ))}
            <AddCharacteristic onAppend={onAppend} />
          </tbody>
        </table>
      </div>
    </section>
  );
}

function CharacterRows({ characteristic, kindClass, source, sourceClass, options, onRewrite }) {
  const replacement = (value) => onRewrite(
    characteristic.start,
    characteristic.end,
    `${characteristic.name}: ${value}`,
  );
  return (
    <>
      <tr className={kindClass}>
        <NameCell name={characteristic.name} required={characteristic.required} />
        <td className="fragment">«{characteristic.fragment}»</td>
        <td><ValueEditor name={characteristic.name} current={String(characteristic.value)} options={options} onPick={replacement} /></td>
        <td>{characteristic.unit?.name || "—"}</td>
        <td><span className={`status ${sourceClass}`}>{source}</span></td>
        <td>
          <button className="icon-btn" type="button" title="Удалить характеристику из описания" aria-label={`Удалить характеристику ${characteristic.name}`} onClick={() => onRewrite(characteristic.start, characteristic.end, "")}>✕</button>
        </td>
      </tr>
      {characteristic.fix && (
        <tr className={`fix-row ${markClass(characteristic)}`}>
          <td colSpan="6">
            <s>{characteristic.fragment}</s> → <b>{formatValue(characteristic.value)}{characteristic.unit ? ` ${characteristic.unit.name.toLowerCase()}` : ""}</b>: {characteristic.fix}
          </td>
        </tr>
      )}
    </>
  );
}

function Result({ text, result, animate, selectedCode, onAnswer, onRewrite, onAppend }) {
  const category = result.category ? CATEGORIES[result.category] : "Категория не определена";
  return (
    <div className="result">
      <Steps steps={result.steps} animate={animate} />
      <div className="result-main">
        <section className="card">
          <div className="result-head">
            <div className="h7">Результат</div>
            <span className={`status ${result.category ? "status-blue" : "status-gray"}`}>{category}</span>
          </div>
          <Outcome result={result} selectedCode={selectedCode} onAnswer={onAnswer} />
          <SourceText text={text} result={result} />
        </section>
        {result.missing_required.length > 0 && (
          <section className="card missing">
            <div className="missing-head p2"><i className="mark-icon">!</i>Заполните обязательные характеристики</div>
            <p className="p5">{result.missing_required.join(", ")}</p>
            <p className="p6 muted">Выберите значения в таблице характеристик ниже, описание допишется само.</p>
          </section>
        )}
        <Characteristics result={result} onRewrite={onRewrite} onAppend={onAppend} />
        {result.remarks.length > 0 && (
          <section className="card">
            <h4>Исправления</h4>
            <ul className="remarks">
              {result.remarks.map((remark, index) => (
                <li className={`p5 ${BAD_KINDS.has(remark.kind) ? "remark-bad" : ""}`} key={`${remark.was}-${index}`}>
                  <s>{remark.was}</s> → <b>{remark.now}</b><div className="p6 muted">{remark.why}</div>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}

async function postJson(url, body, headers = { "Content-Type": "application/json" }) {
  const response = await fetch(url, { method: "POST", headers, body });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error);
  return data;
}

function BatchForm({ onStart, onItem }) {
  const [error, setError] = useState("");
  const [progress, setProgress] = useState(null);
  const runId = useRef(0);

  const upload = async (event) => {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file) return;
    const id = ++runId.current;
    setError("");
    setProgress(null);
    onStart();
    let texts;
    try {
      texts = (await postJson("/api/csv", file, { "Content-Type": "text/csv" })).texts;
    } catch (failure) {
      setError(failure.name === "Error" ? failure.message : "Сервер не отвечает, попробуйте ещё раз");
      return;
    }
    if (id !== runId.current) return;
    setProgress({ done: 0, total: texts.length });
    for (const [index, text] of texts.entries()) {
      let item;
      try {
        item = { text, result: await postJson("/api/run", JSON.stringify({ text })) };
      } catch (failure) {
        item = { text, error: failure.name === "Error" ? failure.message : "Сервер не отвечает" };
      }
      if (id !== runId.current) return;
      onItem(item);
      setProgress({ done: index + 1, total: texts.length });
    }
  };

  const running = progress && progress.done < progress.total;
  return (
    <div className="card batch-form">
      <div className="label-row">
        <div>
          <div className="label">Проверить много описаний сразу</div>
          <div className="p6 muted">CSV с одним столбцом «Наименование товара», по описанию в строке</div>
        </div>
        {error && <span className="tooltip-error" role="alert">{error}</span>}
      </div>
      <label className="btn btn-white batch-upload">
        Загрузить CSV
        <input type="file" accept=".csv,text/csv" hidden onChange={upload} />
      </label>
      {progress && (
        <div className="p6 muted batch-progress">
          {running ? "Проверяем" : "Проверено"} {progress.done} из {progress.total}
          <div className="progress">
            <span style={{ width: `${(progress.done / progress.total) * 100}%` }} />
          </div>
        </div>
      )}
    </div>
  );
}

function BatchStatus({ item, selectedCode }) {
  if (item.error) return <span className="status status-red">{item.error}</span>;
  const { result } = item;
  const code = selectedCode || result.code;
  if (code) return <span className="status status-green">{code}</span>;
  if (result.question) {
    const label = result.question.char === CODE_QUESTION ? "Выбрать код" : `Уточнить: ${result.question.char}`;
    return <span className="status status-blue">{label}</span>;
  }
  return <span className="status status-red">Код не найден</span>;
}

function BatchItem({ item, number, handlers }) {
  const [open, setOpen] = useState(false);
  const [selectedCode, setSelectedCode] = useState(null);
  return (
    <details className="batch-item" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>
        <span className="p6 muted batch-number">{number}</span>
        <span className="batch-text">{item.text}</span>
        <BatchStatus item={item} selectedCode={selectedCode} />
      </summary>
      {open && item.result && (
        <div className="batch-body">
          <Result
            text={item.text}
            result={item.result}
            animate={false}
            selectedCode={selectedCode}
            {...handlers(setSelectedCode)}
          />
        </div>
      )}
    </details>
  );
}

function Footer() {
  return (
    <footer className="footer">
      <div className="container">
        <nav className="footer-links" aria-label="Разделы портала">
          <a href="https://zakupki.mos.ru/about">О портале</a>
          <a href="https://zakupki.mos.ru/ru">Контакты</a>
          <a href="https://zakupki.mos.ru/news/list">Новости</a>
          <a href="https://zakupki.mos.ru/knowledgebase/main">Центр поддержки</a>
          <a href="https://zakupki.mos.ru/sitemap">Карта сайта</a>
        </nav>
        <div className="footer-info">
          <div className="footer-orgs">
            <a className="footer-org" href="https://www.mos.ru/tender/" target="_blank" rel="noreferrer"><img src="/footer/dkp.png" alt="" height="48" /><span><b>Департамент города Москвы</b> по конкурентной политике</span></a>
            <a className="footer-org" href="https://www.mos.ru/dit/" target="_blank" rel="noreferrer"><img src="/footer/dit.png" alt="" height="48" /><span><b>Департамент информационных технологий города Москвы</b></span></a>
            <a className="footer-channel" href="https://max.ru/Supplier_Portal" target="_blank" rel="noreferrer"><img src="/footer/channel.svg" alt="" width="40" height="40" />Информационный канал</a>
          </div>
          <div className="footer-service">
            <FooterLink icon="headset" label="Служба качества" />
            <FooterLink icon="message" label="Техническая поддержка" />
            <FooterLink icon="eye" label="Версия для слабовидящих" />
            <FooterLink icon="dev" label="Доступ для разработчиков" href="https://zakupki.mos.ru/public-api-access" />
          </div>
        </div>
        <div className="footer-copy">© 2017-2026</div>
      </div>
      <div className="footer-agreement">
        <a href="https://www.mos.ru/legal/rules/" target="_blank" rel="noreferrer">Соглашение о пользовании информационными системами и ресурсами города Москвы</a>
      </div>
    </footer>
  );
}

function FooterLink({ icon, label, href = "https://zakupki.mos.ru/ru" }) {
  return <a href={href}><img src={`/icons/${icon}.svg`} alt="" width="24" height="24" />{label}</a>;
}

export default function App() {
  const [text, setTextState] = useState("");
  const [fieldError, setFieldError] = useState("");
  const [serverError, setServerError] = useState("");
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState(null);
  const [selectedCode, setSelectedCode] = useState(null);
  const [batch, setBatch] = useState([]);
  const requestId = useRef(0);
  const liveTimer = useRef(null);
  const skipLive = useRef(null);

  const request = useCallback(async (rawText, animate = true) => {
    const cleanText = rawText.trim();
    window.clearTimeout(liveTimer.current);
    setServerError("");
    if (!cleanText) {
      setFieldError("Опишите товар");
      return;
    }
    const id = ++requestId.current;
    setFieldError("");
    setLoading(true);
    try {
      const apiResponse = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: cleanText }),
      });
      const data = await apiResponse.json();
      if (id !== requestId.current) return;
      if (apiResponse.status === 400) setFieldError(data.error);
      else if (!apiResponse.ok) throw new Error(data.error);
      else {
        setResponse({ text: cleanText, result: data, animate });
        setSelectedCode(null);
      }
    } catch (error) {
      if (id !== requestId.current) return;
      const message = error instanceof Error && error.name === "Error"
        ? error.message
        : "Сервер не отвечает, попробуйте ещё раз";
      setServerError(message);
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, []);

  const setText = (value) => {
    setTextState(value);
    setFieldError("");
  };

  useEffect(() => {
    if (skipLive.current === text) {
      skipLive.current = null;
      return undefined;
    }
    window.clearTimeout(liveTimer.current);
    if (text.trim().length >= 3) {
      liveTimer.current = window.setTimeout(() => request(text, false), LIVE_DELAY);
    }
    return () => window.clearTimeout(liveTimer.current);
  }, [text, request]);

  const updateAndRequest = (value, animate = false) => {
    skipLive.current = value;
    setTextState(value);
    request(value, animate);
  };

  // Правка результата из файла переносит описание в форму сверху и подбирает заново там.
  const editHandlers = (source, pickCode, moved = false) => {
    const edit = (value) => {
      updateAndRequest(value);
      if (moved) window.scrollTo({ top: 0, behavior: "smooth" });
    };
    const rewrite = (start, end, replacement) => {
      const fragment = source.text.slice(start, end);
      edit(replacement && !fragment.includes(":")
        ? tidy(`${source.text.slice(0, start)}${source.text.slice(end)}, ${replacement}`)
        : tidy(source.text.slice(0, start) + replacement + source.text.slice(end)));
    };
    const append = (part) => edit(tidy(`${source.text}, ${part}`));
    const answer = (name, option) => {
      if (name === CODE_QUESTION) {
        pickCode(option);
        return;
      }
      const found = source.result.chars.find((characteristic) => characteristic.name === name);
      if (found) rewrite(found.start, found.end, `${name}: ${option}`);
      else append(`${name}: ${option}`);
    };
    return { onRewrite: rewrite, onAppend: append, onAnswer: answer };
  };

  return (
    <>
      <Header />
      <main className="container page">
        <h2>Подбор кода КТРУ</h2>
        <SearchForm
          text={text}
          setText={setText}
          fieldError={fieldError}
          loading={loading}
          onSubmit={(event) => { event.preventDefault(); request(text); }}
          onExample={(example) => updateAndRequest(example, true)}
        />
        {serverError && <div className="notice notice-dark" role="alert">{serverError}</div>}
        <BatchForm onItem={(item) => setBatch((items) => [...items, item])} onStart={() => setBatch([])} />
        {response && (
          <Result
            {...response}
            selectedCode={selectedCode}
            {...editHandlers(response, setSelectedCode)}
          />
        )}
        {batch.length > 0 && (
          <section className="card batch">
            <h4>Результаты по файлу</h4>
            {batch.map((item, index) => (
              <BatchItem
                item={item}
                number={index + 1}
                handlers={(pickCode) => editHandlers(item, pickCode, true)}
                key={index}
              />
            ))}
          </section>
        )}
      </main>
      <Footer />
    </>
  );
}
