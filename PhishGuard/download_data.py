"""
download_data.py — Скрипт загрузки датасета PhiUSIIL из UCI ML Repository.

Скачивает набор данных и сохраняет в data/dataset.csv
с колонками 'URL' и 'label'.

Использование:
    python download_data.py

Требуется подключение к интернету.
"""

import os
import sys

def main():
    print("=" * 60)
    print("  PhishGuard — Загрузка датасета")
    print("=" * 60)

    try:
        from ucimlrepo import fetch_ucirepo
    except ImportError:
        print("❌ Библиотека ucimlrepo не установлена.")
        print("   Установите её: pip install ucimlrepo")
        sys.exit(1)

    import pandas as pd

    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(data_dir, exist_ok=True)

    output_path = os.path.join(data_dir, "dataset.csv")

    if os.path.isfile(output_path):
        print(f"✅ Файл уже существует: {output_path}")
        df = pd.read_csv(output_path)
        print(f"   Строк: {len(df)}, Колонки: {list(df.columns)}")
        return

    print("📥 Загрузка датасета PhiUSIIL из UCI ML Repository...")
    print("   (Это может занять несколько минут)")

    try:
        dataset = fetch_ucirepo(id=967)
    except Exception as e:
        print(f"❌ Ошибка загрузки: {e}")
        print()
        print("📥 Альтернативный способ:")
        print("   1. Скачайте файл вручную:")
        print("      https://archive.ics.uci.edu/dataset/967/phiusiil+phishing+url+website")
        print("   2. Извлеките CSV и поместите в папку data/")
        sys.exit(1)

    X = dataset.data.features
    y = dataset.data.targets

    print(f"   Features: {X.shape}")
    print(f"   Targets: {y.shape}")
    print(f"   Feature columns: {list(X.columns)}")
    print(f"   Target columns: {list(y.columns)}")

    # Найти колонку URL
    url_col = None
    for col in ["URL", "url", "Url"]:
        if col in X.columns:
            url_col = col
            break

    if url_col is None:
        print(f"❌ Колонка URL не найдена. Доступные колонки: {list(X.columns)}")
        # Попробуем первую колонку
        print("   Попробуем использовать первую колонку...")
        url_col = X.columns[0]

    # Найти колонку с меткой
    label_col = y.columns[0] if len(y.columns) > 0 else None
    if label_col is None:
        print("❌ Колонка с меткой не найдена.")
        sys.exit(1)

    # Создать итоговый DataFrame
    df = pd.DataFrame({
        "URL": X[url_col].values,
        "label": y[label_col].values,
    })

    print(f"\n📊 Статистика:")
    print(f"   Всего URL: {len(df)}")
    print(f"   Легитимные (label=1): {(df['label'] == 1).sum()}")
    print(f"   Фишинговые (label=0): {(df['label'] == 0).sum()}")

    # Сохранить
    df.to_csv(output_path, index=False, encoding="utf-8")
    print(f"\n💾 Датасет сохранён: {output_path}")
    print(f"   Размер файла: {os.path.getsize(output_path) / (1024*1024):.1f} МБ")
    print("\n✅ Готово! Теперь запустите: python train_model.py")


if __name__ == "__main__":
    main()
