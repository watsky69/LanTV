"""
train_model.py — Скрипт обучения ML-модели для PhishGuard.

Загружает набор данных PhiUSIIL из CSV, обучает модель классификации
фишинговых URL на основе TF-IDF символьных n-грамм и Logistic Regression,
сохраняет обученный пайплайн и метрики в папку models/.

Использование:
    python train_model.py

Набор данных:
    Ожидается CSV-файл в data/PhiUSIIL_Phishing_URL_Dataset.csv
    с колонками 'URL' и 'label' (1 = легитимный, 0 = фишинговый).

Результаты:
    models/phishguard_model.joblib — обученный пайплайн (TF-IDF + модель)
    models/metrics.joblib — метрики качества на тестовой выборке
"""

import os
import sys
import json
import time
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    confusion_matrix,
    classification_report,
)
import joblib


# ─── Настройки ────────────────────────────────────────────────
RANDOM_STATE = 42
TEST_SIZE = 0.20
MAX_SAMPLES = 100_000  # Ограничение выборки (None = без ограничения)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")

# Возможные имена файла датасета
POSSIBLE_DATA_FILES = [
    "PhiUSIIL_Phishing_URL_Dataset.csv",
    "phiusiil_phishing_url_dataset.csv",
    "dataset.csv",
]

# Возможные имена колонки с URL
POSSIBLE_URL_COLUMNS = ["URL", "url", "Url", "URI", "uri"]
# Возможные имена колонки с меткой
POSSIBLE_LABEL_COLUMNS = ["label", "Label", "LABEL", "class", "Class", "phishing"]


def find_dataset():
    """Ищет файл датасета в папке data/."""
    for fname in POSSIBLE_DATA_FILES:
        path = os.path.join(DATA_DIR, fname)
        if os.path.isfile(path):
            return path

    # Попробовать найти любой CSV в data/
    if os.path.isdir(DATA_DIR):
        csv_files = [f for f in os.listdir(DATA_DIR) if f.endswith(".csv")]
        if len(csv_files) == 1:
            return os.path.join(DATA_DIR, csv_files[0])
        elif len(csv_files) > 1:
            print(f"⚠️  В папке data/ найдено несколько CSV-файлов: {csv_files}")
            print("   Переименуйте нужный файл в 'dataset.csv'.")
            return None

    return None


def load_and_prepare_data(filepath):
    """
    Загружает данные и подготавливает их для обучения.

    Возвращает (urls, labels) — массивы строк URL и меток (0/1).
    В исходном наборе: label=1 означает легитимный, label=0 означает фишинговый.
    Мы ИНВЕРТИРУЕМ метки для удобства: 1 = фишинг, 0 = легитимный.
    """
    print(f"📂 Загрузка данных из: {filepath}")
    df = pd.read_csv(filepath, low_memory=False)
    print(f"   Размер датасета: {len(df)} строк, {len(df.columns)} колонок")
    print(f"   Колонки: {list(df.columns[:10])}{'...' if len(df.columns) > 10 else ''}")

    # --- Найти колонку URL ---
    url_col = None
    for col in POSSIBLE_URL_COLUMNS:
        if col in df.columns:
            url_col = col
            break
    if url_col is None:
        print("❌ Не найдена колонка с URL. Ожидаемые имена:", POSSIBLE_URL_COLUMNS)
        print("   Найденные колонки:", list(df.columns))
        sys.exit(1)

    # --- Найти колонку с меткой ---
    label_col = None
    for col in POSSIBLE_LABEL_COLUMNS:
        if col in df.columns:
            label_col = col
            break
    if label_col is None:
        print("❌ Не найдена колонка с меткой. Ожидаемые имена:", POSSIBLE_LABEL_COLUMNS)
        print("   Найденные колонки:", list(df.columns))
        sys.exit(1)

    print(f"   Колонка URL: '{url_col}', колонка метки: '{label_col}'")

    # --- Извлечь только URL и метку ---
    df = df[[url_col, label_col]].copy()
    df.columns = ["url", "original_label"]

    # --- Очистка ---
    df = df.dropna(subset=["url", "original_label"])
    df["url"] = df["url"].astype(str).str.strip()
    df = df[df["url"].str.len() > 0]

    # Удаление полных дубликатов
    n_before = len(df)
    df = df.drop_duplicates(subset=["url"], keep="first")
    n_after = len(df)
    if n_before != n_after:
        print(f"   Удалено дубликатов URL: {n_before - n_after}")

    # --- Интерпретация меток ---
    # В исходном наборе PhiUSIIL: label=1 — легитимный, label=0 — фишинговый.
    # Инвертируем: 1 = фишинг, 0 = легитимный (более привычная интерпретация).
    unique_labels = sorted(df["original_label"].unique())
    print(f"   Уникальные значения меток: {unique_labels}")

    df["label"] = 1 - df["original_label"].astype(int)
    # Теперь: label=1 — фишинг, label=0 — легитимный

    print(f"\n📊 Распределение классов (после инверсии меток):")
    print(f"   Легитимные (0): {(df['label'] == 0).sum()}")
    print(f"   Фишинговые (1): {(df['label'] == 1).sum()}")

    # --- Ограничение размера выборки ---
    if MAX_SAMPLES is not None and len(df) > MAX_SAMPLES:
        print(f"\n✂️  Ограничение выборки до {MAX_SAMPLES} записей "
              f"(с сохранением баланса классов)...")
        samples_per_class = MAX_SAMPLES // 2
        df_legit = df[df["label"] == 0].sample(
            n=min(len(df[df["label"] == 0]), samples_per_class),
            random_state=RANDOM_STATE,
        )
        df_phish = df[df["label"] == 1].sample(
            n=min(len(df[df["label"] == 1]), samples_per_class),
            random_state=RANDOM_STATE,
        )
        df = pd.concat([df_legit, df_phish], ignore_index=True)
        print(f"   Итоговый размер: {len(df)}")
        print(f"   Легитимные: {(df['label'] == 0).sum()}")
        print(f"   Фишинговые: {(df['label'] == 1).sum()}")

    return np.array(df["url"].tolist()), np.array(df["label"].tolist())


def train_pipeline(X_train, y_train):
    """
    Создаёт и обучает пайплайн: TF-IDF (символьные n-граммы) + Logistic Regression.
    """
    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer(
            analyzer="char_wb",     # Символьные n-граммы с учётом границ слов
            ngram_range=(3, 5),     # Триграммы, 4-граммы и 5-граммы
            max_features=100_000,   # Максимальное количество признаков
            sublinear_tf=True,      # Логарифмическое масштабирование TF
            min_df=2,               # Минимальная частота документа
        )),
        ("classifier", LogisticRegression(
            max_iter=1000,
            C=1.0,
            solver="lbfgs",
            random_state=RANDOM_STATE,
            n_jobs=-1,
        )),
    ])

    print("\n🔧 Обучение модели...")
    start = time.time()
    pipeline.fit(X_train, y_train)
    elapsed = time.time() - start
    print(f"   Обучение завершено за {elapsed:.1f} секунд.")

    return pipeline


def evaluate_model(pipeline, X_test, y_test):
    """
    Оценивает качество модели на тестовой выборке.
    Возвращает словарь с метриками.
    """
    print("\n📏 Оценка качества на тестовой выборке...")
    y_pred = pipeline.predict(X_test)

    acc = accuracy_score(y_test, y_pred)
    prec = precision_score(y_test, y_pred, zero_division=0)
    rec = recall_score(y_test, y_pred, zero_division=0)
    f1 = f1_score(y_test, y_pred, zero_division=0)
    cm = confusion_matrix(y_test, y_pred)
    report = classification_report(
        y_test, y_pred,
        target_names=["Легитимный (0)", "Фишинговый (1)"],
        zero_division=0,
    )

    metrics = {
        "accuracy": float(acc),
        "precision": float(prec),
        "recall": float(rec),
        "f1_score": float(f1),
        "confusion_matrix": cm.tolist(),
        "classification_report": report,
        "test_size": len(y_test),
        "train_size": None,  # Будет заполнено позже
    }

    print(f"\n{'='*50}")
    print(f"  Accuracy  (точность):  {acc:.4f}")
    print(f"  Precision (точность+): {prec:.4f}")
    print(f"  Recall    (полнота):   {rec:.4f}")
    print(f"  F1-score:              {f1:.4f}")
    print(f"{'='*50}")
    print(f"\n  Матрица ошибок:")
    print(f"                    Предсказано")
    print(f"                  Легит.  Фишинг")
    print(f"  Реально Легит.  {cm[0][0]:>6}  {cm[0][1]:>6}")
    print(f"  Реально Фишинг  {cm[1][0]:>6}  {cm[1][1]:>6}")
    print(f"\n{report}")

    return metrics


def main():
    """Основная функция: загрузка данных, обучение, оценка, сохранение."""

    print("=" * 60)
    print("  PhishGuard — Обучение модели обнаружения фишинга")
    print("=" * 60)

    # --- Поиск датасета ---
    filepath = find_dataset()
    if filepath is None:
        print("\n❌ Файл датасета не найден!")
        print(f"\n📥 Инструкция по получению данных:")
        print(f"   1. Скачайте набор данных PhiUSIIL:")
        print(f"      https://archive.ics.uci.edu/dataset/967/phiusiil+phishing+url+website")
        print(f"   2. Извлеките CSV-файл из архива.")
        print(f"   3. Поместите его в папку: {DATA_DIR}")
        print(f"   4. Запустите этот скрипт снова.")
        sys.exit(1)

    # --- Загрузка и подготовка данных ---
    urls, labels = load_and_prepare_data(filepath)

    # --- Разбиение на обучающую и тестовую выборки ---
    print(f"\n🔀 Разбиение данных: {int((1 - TEST_SIZE) * 100)}% обучение / "
          f"{int(TEST_SIZE * 100)}% тест...")
    X_train, X_test, y_train, y_test = train_test_split(
        urls, labels,
        test_size=TEST_SIZE,
        random_state=RANDOM_STATE,
        stratify=labels,  # Сохранение пропорций классов
    )
    print(f"   Обучающая выборка: {len(X_train)} URL")
    print(f"   Тестовая выборка:  {len(X_test)} URL")

    # --- Обучение ---
    pipeline = train_pipeline(X_train, y_train)

    # --- Оценка ---
    metrics = evaluate_model(pipeline, X_test, y_test)
    metrics["train_size"] = len(X_train)

    # --- Сохранение ---
    os.makedirs(MODELS_DIR, exist_ok=True)

    model_path = os.path.join(MODELS_DIR, "phishguard_model.joblib")
    metrics_path = os.path.join(MODELS_DIR, "metrics.joblib")

    print(f"\n💾 Сохранение модели: {model_path}")
    joblib.dump(pipeline, model_path)

    print(f"💾 Сохранение метрик: {metrics_path}")
    joblib.dump(metrics, metrics_path)

    # Также сохраним метрики в JSON для удобного просмотра
    metrics_json_path = os.path.join(MODELS_DIR, "metrics.json")
    metrics_json = {k: v for k, v in metrics.items() if k != "classification_report"}
    with open(metrics_json_path, "w", encoding="utf-8") as f:
        json.dump(metrics_json, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Обучение завершено успешно!")
    print(f"   Файл модели:  {model_path}")
    print(f"   Файл метрик:  {metrics_path}")

    # --- Проверка на нескольких примерах ---
    print(f"\n🔍 Быстрая проверка на примерах:")
    test_urls = [
        "https://www.google.com",
        "http://192.168.1.1/login/secure-paypal.com/verify",
        "https://github.com/python/cpython",
        "http://paypal-secure-login.suspicious-domain.xyz/verify?id=12345",
    ]
    for test_url in test_urls:
        pred = pipeline.predict([test_url])[0]
        proba = pipeline.predict_proba([test_url])[0]
        label = "🔴 Фишинг" if pred == 1 else "🟢 Легитимный"
        conf = max(proba) * 100
        print(f"   {label} ({conf:.0f}%) — {test_url}")


if __name__ == "__main__":
    main()
