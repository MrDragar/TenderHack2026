"""Дообучение NLI-модели проверки на мусор. Запускать на GPU-хосте, не на ноутбуке.

Учим ту же задачу, что спрашиваем в пайплайне: посылка «товар. кусок текста», две
гипотезы, следует или противоречит. Голова модели та же, поэтому pipeline/trash.py
подхватывает результат без правок. rubert-base в fp16 с парами до 64 токенов занимает
около 3 ГБ VRAM, потребительской карты на 6 ГБ хватает.
"""

import argparse
import random

import torch
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    get_linear_schedule_with_warmup,
)

from pipeline.trash import (
    BASE_MODEL,
    MAX_TOKENS,
    NOT_PROPERTY,
    PROPERTY,
    TUNED_MODEL,
    decisions,
    entailment_id,
    premise,
)
from scripts.dataloader import load_data, load_portal
from scripts.trash_examples import Example, examples


def pairs(found: list[Example], yes: int, no: int) -> list[tuple[str, str, int]]:
    """Каждый пример даёт две пары: гипотеза «свойство» и гипотеза «мусор»."""
    out = []
    for product, told, is_property in found:
        text = premise(told, product)
        out.append((text, PROPERTY, yes if is_property else no))
        out.append((text, NOT_PROPERTY, no if is_property else yes))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--limit", type=int, default=40000)
    args = parser.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(BASE_MODEL).to(device)
    labels = model.config.id2label
    yes, no = entailment_id(labels), entailment_id(labels, "contra")

    # Учимся только на dev-части: test остаётся для scripts/eval_trash.py --test.
    found = examples(load_data(), load_portal(), decisions())[: args.limit]
    data = pairs(found, yes, no)
    print(f"примеров {len(found)}, пар {len(data)}, свойств {sum(e[2] for e in found)}")

    steps = args.epochs * (len(data) + args.batch - 1) // args.batch
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    schedule = get_linear_schedule_with_warmup(optimizer, steps // 10, steps)
    scaler = torch.amp.GradScaler(enabled=device == "cuda")
    model.train()
    for epoch in range(args.epochs):
        random.Random(epoch).shuffle(data)
        total = 0.0
        for start in range(0, len(data), args.batch):
            chunk = data[start : start + args.batch]
            batch = tokenizer(
                [p for p, _, _ in chunk],
                [h for _, h, _ in chunk],
                padding=True,
                truncation=True,
                max_length=MAX_TOKENS,
                return_tensors="pt",
            ).to(device)
            target = torch.tensor([label for _, _, label in chunk], device=device)
            with torch.autocast(device, dtype=torch.float16, enabled=device == "cuda"):
                loss = model(**batch, labels=target).loss
            optimizer.zero_grad()
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            schedule.step()
            total += loss.item() * len(chunk)
        print(f"эпоха {epoch + 1}: loss {total / len(data):.4f}")

    model.save_pretrained(TUNED_MODEL)
    tokenizer.save_pretrained(TUNED_MODEL)
    print(f"сохранено в {TUNED_MODEL}")


if __name__ == "__main__":
    main()
