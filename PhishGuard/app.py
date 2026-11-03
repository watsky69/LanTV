"""
app.py — Главное Streamlit-приложение PhishGuard.

Интеллектуальная система обнаружения фишинговых веб-ресурсов
на основе анализа URL с использованием машинного обучения.

Запуск:
    streamlit run app.py
"""

import os
import sys
import streamlit as st
import joblib
import numpy as np
import matplotlib.pyplot as plt
import matplotlib

# Настройка matplotlib для работы без GUI
matplotlib.use("Agg")

# Добавляем текущую папку в path для импорта
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from url_features import extract_heuristic_features, get_suspicion_summary, is_valid_url

# ─── Пути к файлам ────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "models", "phishguard_model.joblib")
METRICS_PATH = os.path.join(BASE_DIR, "models", "metrics.joblib")

# ─── Настройка страницы ───────────────────────────────────────
st.set_page_config(
    page_title="PhishGuard — Обнаружение фишинга",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def load_model():
    """Загружает обученную модель из файла."""
    if not os.path.isfile(MODEL_PATH):
        return None
    try:
        model = joblib.load(MODEL_PATH)
        return model
    except Exception as e:
        st.error(f"Ошибка загрузки модели: {e}")
        return None


@st.cache_data
def load_metrics():
    """Загружает метрики качества модели."""
    if not os.path.isfile(METRICS_PATH):
        return None
    try:
        metrics = joblib.load(METRICS_PATH)
        return metrics
    except Exception as e:
        st.error(f"Ошибка загрузки метрик: {e}")
        return None


def render_sidebar():
    """Боковая панель навигации."""
    st.sidebar.title("🛡️ PhishGuard")
    st.sidebar.markdown("**Система обнаружения фишинга**")
    st.sidebar.markdown("---")

    page = st.sidebar.radio(
        "Навигация",
        [
            "🔍 Проверка URL",
            "📖 Как работает система",
            "📊 Результаты исследования",
            "⚠️ Об ограничениях",
        ],
        label_visibility="collapsed",
    )

    st.sidebar.markdown("---")
    st.sidebar.caption(
        "PhishGuard — школьный исследовательский проект. "
        "Результаты являются оценкой модели и не гарантируют безопасность."
    )

    return page


def page_check_url(model):
    """Страница проверки URL."""
    st.title("🔍 Проверка URL")
    st.markdown(
        "Введите ссылку для анализа. Программа оценит вероятность "
        "того, что URL является фишинговым, на основе обученной ML-модели "
        "и эвристических признаков."
    )

    if model is None:
        st.error(
            "⚠️ Модель не найдена! Сначала обучите модель, выполнив команду:\n\n"
            "```\npython train_model.py\n```\n\n"
            f"Ожидаемый путь к файлу модели: `{MODEL_PATH}`"
        )
        return

    # --- Поле ввода ---
    url_input = st.text_input(
        "Введите URL для проверки:",
        placeholder="Например: https://example.com/login",
        help="Введите полный адрес ссылки, включая http:// или https://",
    )

    col_btn, col_spacer = st.columns([1, 3])
    with col_btn:
        check_button = st.button("🔍 Проверить ссылку", type="primary", use_container_width=True)

    if check_button:
        if not url_input or not url_input.strip():
            st.warning("⚠️ Пожалуйста, введите URL для проверки.")
            return

        url_input = url_input.strip()

        if not is_valid_url(url_input):
            st.warning(
                "⚠️ Введённая строка не похожа на URL. "
                "Убедитесь, что адрес содержит доменное имя "
                "(например, example.com)."
            )
            return

        # --- Предсказание модели ---
        try:
            prediction = model.predict([url_input])[0]
            probabilities = model.predict_proba([url_input])[0]
        except Exception as e:
            st.error(f"Ошибка при анализе URL: {e}")
            return

        is_phishing = prediction == 1
        confidence = max(probabilities) * 100
        phishing_prob = probabilities[1] * 100  # Вероятность фишинга

        features = extract_heuristic_features(url_input)
        warnings = get_suspicion_summary(features)
        typo = features.get("Обнаружен тайпсквоттинг")

        st.markdown("---")

        # --- Итоговый вердикт (Гибридный анализ) ---
        st.subheader("Итоговый вердикт системы")

        if typo:
            st.error(
                f"🔴 **ВЫСОКИЙ РИСК: Обнаружен тайпсквоттинг (подделка бренда)!**"
            )
            st.markdown(
                f"**Доменное имя имитирует официальный сервис `{typo['brand']}`**: {typo['reason']}.\n\n"
                f"⚠️ *Это классическая атака социальной инженерии.* Злоумышленники заменяют похожие символы "
                f"(например, букву **o** на цифру **0**, или **l** на **1**), чтобы обмануть невнимательного пользователя."
            )
            st.progress(0.98)
            st.caption("Шкала общего уровня риска: КРИТИЧЕСКИЙ (98%)")
        elif is_phishing:
            if confidence > 85:
                st.error(f"🔴 **Возможный фишинг** (уверенность модели: {confidence:.0f}%)")
            else:
                st.warning(f"🟡 **Возможный фишинг** (уверенность модели: {confidence:.0f}%)")
            st.markdown(
                f"Модель оценила вероятность фишинга в **{phishing_prob:.1f}%**. "
                f"Рекомендуется соблюдать осторожность при переходе по этой ссылке."
            )
            st.progress(phishing_prob / 100)
        else:
            st.success(
                f"🟢 **Явные признаки фишинга не обнаружены** "
                f"(уверенность модели: {confidence:.0f}%)"
            )
            st.markdown(
                f"Модель оценила вероятность фишинга в **{phishing_prob:.1f}%**. "
                f"Однако это не гарантирует полную безопасность ссылки."
            )
            st.progress(phishing_prob / 100)

        st.markdown("---")

        # --- Детализация работы компонентов ---
        with st.expander("🔬 Детализация работы алгоритмов (ML + Эвристика)", expanded=True):
            col_m1, col_m2 = st.columns(2)
            with col_m1:
                st.markdown("**1. Статистическая ML-модель (TF-IDF + LogReg):**")
                ml_label = "🔴 Фишинг" if is_phishing else "🟢 Легитимный"
                st.markdown(f"- Вердикт классификатора: **{ml_label}**")
                st.markdown(f"- Расчётная вероятность фишинга: **{phishing_prob:.1f}%**")
                st.caption(
                    "Анализирует частотные подстроки (n-граммы) URL без учета семантики бренда."
                )

            with col_m2:
                st.markdown("**2. Анализатор тайпсквоттинга и омоглифов:**")
                if typo:
                    st.markdown(f"- Статус: **🚨 Обнаружена подделка ({typo['brand']})**")
                    st.markdown(f"- Причина: {typo['reason']}")
                else:
                    st.markdown("- Статус: ✅ Подделки известных брендов не выявлено")
                st.caption(
                    "Анализирует омоглифы (0↔o, 1↔l), leetspeak и расстояние Левенштейна."
                )

        st.markdown("---")

        # --- Эвристические признаки ---
        st.subheader("📋 Анализ структуры и параметров URL")
        st.caption(
            "Ниже перечислены структурные характеристики URL, обнаруженные с помощью правил разбора. "
            "Они помогают оценить общую надежность ресурса."
        )

        # Таблица признаков
        col1, col2 = st.columns(2)
        feature_items = list(features.items())
        mid = len(feature_items) // 2 + 1

        with col1:
            for name, value in feature_items[:mid]:
                display_value = format_feature_value(value)
                st.markdown(f"• **{name}:** {display_value}")

        with col2:
            for name, value in feature_items[mid:]:
                display_value = format_feature_value(value)
                st.markdown(f"• **{name}:** {display_value}")

        # Подозрительные находки
        if warnings:
            st.markdown("---")
            st.markdown("**Подозрительные признаки (эвристика):**")
            for w in warnings:
                st.markdown(w)
            st.caption(
                "⚠️ Наличие этих признаков не означает автоматическую блокировку, "
                "но требует повышенной бдительности."
            )

        # --- Предупреждение ---
        st.markdown("---")
        st.info(
            "ℹ️ **Важно:** результат является комплексной оценкой модели машинного обучения "
            "и эвристических анализаторов. Программа НЕ гарантирует абсолютную безопасность. "
            "Всегда внимательно проверяйте адресную строку в браузере перед вводом паролей!"
        )


def format_feature_value(value):
    """Форматирует значение признака для отображения."""
    if isinstance(value, bool):
        return "✅ Да" if value else "❌ Нет"
    if isinstance(value, dict):
        return f"🚨 Да (мимикрия под {value.get('brand', 'бренд')})"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) if value else "—"
    if value is None:
        return "—"
    return str(value)


def page_how_it_works():
    """Страница «Как работает система»."""
    st.title("📖 Как работает система")

    st.markdown("""
    ### Общий принцип

    PhishGuard анализирует **текст URL-адреса** (ссылки) и определяет,
    похожа ли она на фишинговую. Программа **не открывает** проверяемые
    сайты и **не переходит** по ссылкам — анализируется только строка адреса.

    ### Этапы работы

    1. **Ввод URL** — пользователь вводит ссылку в поле проверки.
    2. **Анализ текста URL** — программа передаёт строку обученной модели.
    3. **Классификация** — модель машинного обучения оценивает вероятность фишинга.
    4. **Эвристический анализ** — дополнительно извлекаются понятные признаки URL.
    5. **Результат** — пользователь видит оценку модели и подробности анализа.
    """)

    st.markdown("---")

    st.markdown("""
    ### Машинное обучение простыми словами

    **Машинное обучение (ML)** — это способ научить компьютер распознавать
    закономерности на примерах, вместо того чтобы описывать каждое правило вручную.

    **Аналогия:** представьте, что вы учитесь отличать спам-письма от обычных.
    Вместо того чтобы составлять список всех признаков спама, вы просматриваете
    тысячи примеров писем, помеченных как «спам» или «не спам», и постепенно
    начинаете замечать закономерности. Компьютер делает то же самое, но быстрее
    и с большим количеством данных.

    ### Как обучается наша модель

    1. **Данные**: мы используем набор из тысяч размеченных URL —
       каждый помечен как «легитимный» или «фишинговый».

    2. **Преобразование текста (TF-IDF)**: каждый URL разбивается на короткие
       последовательности символов (n-граммы), и вычисляется их «важность»
       (TF-IDF). Так текст превращается в набор чисел, понятных алгоритму.

    3. **Обучение классификатора (Logistic Regression)**: алгоритм находит
       математическую границу, которая лучше всего разделяет фишинговые
       и легитимные URL на основе их числовых представлений.

    4. **Проверка**: обученная модель тестируется на отдельной выборке URL,
       которые она не видела при обучении.
    """)

    st.markdown("---")

    st.markdown("""
    ### Архитектура программы

    ```
    PhishGuard/
    ├── app.py              ← Веб-интерфейс (Streamlit)
    ├── train_model.py      ← Обучение модели
    ├── url_features.py     ← Эвристический анализ URL
    ├── requirements.txt    ← Зависимости
    ├── README.md           ← Документация
    ├── models/
    │   ├── phishguard_model.joblib  ← Обученная модель
    │   └── metrics.joblib           ← Метрики качества
    ├── data/
    │   └── (CSV-файл датасета)
    └── tests/
        └── test_url_features.py     ← Тесты
    ```
    """)

    st.markdown("""
    ### Используемые технологии

    | Технология | Назначение |
    |---|---|
    | Python | Основной язык программирования |
    | Streamlit | Веб-интерфейс приложения |
    | Scikit-learn | Машинное обучение (TF-IDF + Logistic Regression) |
    | Pandas, NumPy | Обработка данных |
    | Matplotlib | Визуализация результатов |
    | Joblib | Сохранение и загрузка модели |
    """)


def page_results():
    """Страница «Результаты исследования»."""
    st.title("📊 Результаты исследования")

    metrics = load_metrics()

    if metrics is None:
        st.warning(
            "⚠️ Метрики модели не найдены. Сначала обучите модель:\n\n"
            "```\npython train_model.py\n```"
        )
        return

    st.markdown(
        "Ниже представлены **реальные результаты** тестирования обученной модели "
        "на отложенной тестовой выборке, которая **не использовалась** при обучении."
    )

    st.markdown("---")

    # --- Основные метрики ---
    st.subheader("Основные метрики качества")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Accuracy", f"{metrics['accuracy']:.2%}")
    with col2:
        st.metric("Precision", f"{metrics['precision']:.2%}")
    with col3:
        st.metric("Recall", f"{metrics['recall']:.2%}")
    with col4:
        st.metric("F1-score", f"{metrics['f1_score']:.2%}")

    st.markdown("---")

    # --- Объяснение метрик ---
    with st.expander("📖 Что означают эти метрики?"):
        st.markdown("""
        - **Accuracy (точность)** — доля правильных предсказаний среди всех проверок.
        - **Precision (точность по фишингу)** — из всех URL, отмеченных моделью
          как фишинговые, какая доля действительно является фишинговой.
          Высокий precision = мало ложных тревог.
        - **Recall (полнота по фишингу)** — из всех реально фишинговых URL,
          какую долю модель обнаружила. Высокий recall = мало пропущенных угроз.
          **Это особенно важный показатель** для системы безопасности.
        - **F1-score** — среднее гармоническое precision и recall.
          Учитывает баланс между полнотой и точностью.
        """)

    st.markdown("---")

    # --- Матрица ошибок ---
    st.subheader("Матрица ошибок (Confusion Matrix)")

    cm = np.array(metrics["confusion_matrix"])

    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(cm, cmap="Blues", interpolation="nearest")

    ax.set_xticks([0, 1])
    ax.set_yticks([0, 1])
    ax.set_xticklabels(["Легитимный", "Фишинговый"], fontsize=11)
    ax.set_yticklabels(["Легитимный", "Фишинговый"], fontsize=11)
    ax.set_xlabel("Предсказание модели", fontsize=12)
    ax.set_ylabel("Реальный класс", fontsize=12)
    ax.set_title("Матрица ошибок", fontsize=13)

    # Добавляем числа в ячейки
    for i in range(2):
        for j in range(2):
            color = "white" if cm[i, j] > cm.max() / 2 else "black"
            ax.text(j, i, str(cm[i, j]), ha="center", va="center",
                    fontsize=16, fontweight="bold", color=color)

    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    st.pyplot(fig)
    plt.close(fig)

    # Объяснение матрицы
    st.markdown(f"""
    **Как читать матрицу ошибок:**
    - **{cm[0][0]}** легитимных URL правильно определены как легитимные ✅
    - **{cm[0][1]}** легитимных URL ошибочно отмечены как фишинговые (ложная тревога) ⚠️
    - **{cm[1][0]}** фишинговых URL пропущены (не обнаружены) ❌
    - **{cm[1][1]}** фишинговых URL правильно обнаружены ✅
    """)

    st.markdown("---")

    # --- Информация о данных ---
    st.subheader("Информация об обучении")

    info_col1, info_col2 = st.columns(2)
    with info_col1:
        st.markdown(f"**Размер обучающей выборки:** {metrics.get('train_size', '—')}")
        st.markdown(f"**Размер тестовой выборки:** {metrics.get('test_size', '—')}")
    with info_col2:
        st.markdown("**Алгоритм:** TF-IDF + Logistic Regression")
        st.markdown("**Признаки:** символьные n-граммы (3–5) из текста URL")

    # --- Полный отчёт ---
    with st.expander("📄 Полный отчёт классификации"):
        report = metrics.get("classification_report", "Отчёт не найден.")
        st.code(report, language=None)


def page_limitations():
    """Страница «Об ограничениях»."""
    st.title("⚠️ Об ограничениях")

    st.markdown("""
    ### Что программа умеет

    - ✅ Анализировать **текст URL** и оценивать вероятность фишинга.
    - ✅ Показывать **эвристические признаки** подозрительности.
    - ✅ Работать **полностью локально**, без подключения к интернету.
    - ✅ Давать **быстрый** результат за доли секунды.
    """)

    st.markdown("---")

    st.markdown("""
    ### Что программа НЕ делает

    - ❌ **Не гарантирует** безопасность или опасность ссылки.
    - ❌ **Не открывает** и **не загружает** проверяемые сайты.
    - ❌ **Не анализирует** содержимое веб-страниц.
    - ❌ **Не проверяет** SSL-сертификаты или DNS-записи.
    - ❌ **Не обновляет** базу знаний автоматически.
    """)

    st.markdown("---")

    st.markdown("""
    ### Возможные ошибки модели

    Любая модель машинного обучения может ошибаться:

    - **Ложноположительные (False Positive):** легитимная ссылка ошибочно
      отмечена как фишинговая. Это может произойти, если легитимный URL
      содержит нехарактерные символы или длинный путь.

    - **Ложноотрицательные (False Negative):** фишинговая ссылка пропущена
      и отмечена как легитимная. Это может произойти с новыми,
      нетипичными фишинговыми URL, которых не было в обучающих данных.

    ### Почему ошибки неизбежны

    - Модель обучена на **конечном наборе данных**, который не охватывает
      все возможные виды фишинга.
    - Мошенники постоянно придумывают **новые способы маскировки** ссылок.
    - Некоторые фишинговые URL **почти неотличимы** от легитимных
      по одному только тексту адреса.
    """)

    st.markdown("---")

    st.markdown("""
    ### Направления улучшения

    В будущем проект можно расширить:

    1. **Анализ содержимого страниц** — проверка HTML-кода и визуального
       оформления (с учётом безопасности).
    2. **Проверка WHOIS и DNS** — анализ информации о домене.
    3. **Регулярное обновление модели** — переобучение на новых данных.
    4. **Ансамблевые методы** — комбинирование нескольких моделей.
    5. **Расширение набора данных** — добавление русскоязычных
       фишинговых ссылок.
    """)

    st.markdown("---")

    st.info(
        "🛡️ PhishGuard — это исследовательский проект, демонстрирующий "
        "применение машинного обучения для задачи информационной безопасности. "
        "Для реальной защиты используйте комплексные решения: антивирусы, "
        "расширения браузера, проверку SSL-сертификатов и здравый смысл."
    )


def main():
    """Основная функция приложения."""
    page = render_sidebar()
    model = load_model()

    if page == "🔍 Проверка URL":
        page_check_url(model)
    elif page == "📖 Как работает система":
        page_how_it_works()
    elif page == "📊 Результаты исследования":
        page_results()
    elif page == "⚠️ Об ограничениях":
        page_limitations()


if __name__ == "__main__":
    main()
