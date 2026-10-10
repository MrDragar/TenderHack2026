"""Скачивает NLI-модель проверки на мусор в models/nli. Нужен интернет, GPU не нужен."""

import sys

from huggingface_hub import snapshot_download

from pipeline.trash import BASE_MODEL, MODEL_ID

if __name__ == "__main__":
    model = sys.argv[1] if len(sys.argv) > 1 else MODEL_ID
    snapshot_download(model, local_dir=BASE_MODEL)
    print(f"{model} → {BASE_MODEL}")
