"""
Тесты основных функций PhishGuard.

Запуск:
    python -m pytest tests/test_url_features.py -v
    или:
    python tests/test_url_features.py
"""

import os
import sys

# Добавляем корень проекта в path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from url_features import extract_heuristic_features, get_suspicion_summary, is_valid_url


def test_is_valid_url():
    """Тест валидации URL."""
    # Валидные URL
    assert is_valid_url("https://www.google.com") is True
    assert is_valid_url("http://example.com/path") is True
    assert is_valid_url("example.com") is True
    assert is_valid_url("sub.domain.example.com") is True
    assert is_valid_url("https://example.com/path?query=1&other=2") is True

    # Невалидные URL
    assert is_valid_url("") is False
    assert is_valid_url("   ") is False
    assert is_valid_url("abc") is False
    assert is_valid_url("localhost") is False  # Нет точки

    print("✅ test_is_valid_url — все тесты пройдены")


def test_extract_features_normal_url():
    """Тест извлечения признаков для обычного URL."""
    features = extract_heuristic_features("https://www.google.com")

    assert features["Длина URL"] == len("https://www.google.com")
    assert features["Количество поддоменов"] == 1  # www
    assert features["Символ @ в URL"] is False
    assert features["IP-адрес вместо домена"] is False
    assert features["Использует HTTPS"] is True
    assert features["Сокращённая ссылка"] is False

    print("✅ test_extract_features_normal_url — все тесты пройдены")


def test_extract_features_suspicious_url():
    """Тест извлечения признаков для подозрительного URL."""
    url = "http://192.168.1.1/@user/login/paypal-secure/verify"
    features = extract_heuristic_features(url)

    assert features["Символ @ в URL"] is True
    assert features["IP-адрес вместо домена"] is True
    assert features["Использует HTTPS"] is False

    print("✅ test_extract_features_suspicious_url — все тесты пройдены")


def test_extract_features_with_brands():
    """Тест обнаружения упоминаний брендов."""
    url = "http://paypal-login.example.com/verify-google"
    features = extract_heuristic_features(url)

    brands = features.get("Упоминание известных брендов")
    assert brands is not None
    assert "paypal" in brands
    assert "google" in brands
    assert "login" in brands

    print("✅ test_extract_features_with_brands — все тесты пройдены")


def test_extract_features_shortener():
    """Тест обнаружения сокращённых ссылок."""
    url = "https://bit.ly/3xYzAbC"
    features = extract_heuristic_features(url)

    assert features["Сокращённая ссылка"] is True

    print("✅ test_extract_features_shortener — все тесты пройдены")


def test_suspicion_summary_clean():
    """Тест: обычный URL не должен генерировать много предупреждений."""
    features = extract_heuristic_features("https://www.google.com")
    warnings = get_suspicion_summary(features)

    # Для google.com не должно быть критических предупреждений
    # (возможно только предупреждение о поддоменах www, если > 2)
    assert len(warnings) <= 1  # Максимум 1 мелкое предупреждение

    print("✅ test_suspicion_summary_clean — все тесты пройдены")


def test_suspicion_summary_suspicious():
    """Тест: подозрительный URL должен генерировать предупреждения."""
    url = "http://192.168.1.1/@user/paypal-secure-login.example.com/verify?id=12345"
    features = extract_heuristic_features(url)
    warnings = get_suspicion_summary(features)

    # Должно быть несколько предупреждений
    assert len(warnings) >= 3

    print("✅ test_suspicion_summary_suspicious — все тесты пройдены")


def test_empty_and_edge_cases():
    """Тест граничных случаев."""
    # Пустая строка
    features = extract_heuristic_features("")
    assert features["Длина URL"] == 0

    # Очень длинный URL
    long_url = "https://example.com/" + "a" * 1000
    features = extract_heuristic_features(long_url)
    assert features["Длина URL"] > 1000

    # URL только с IP
    features = extract_heuristic_features("http://1.2.3.4")
    assert features["IP-адрес вместо домена"] is True

    print("✅ test_empty_and_edge_cases — все тесты пройдены")


def test_typosquatting_detection():
    """Тест обнаружения тайпсквоттинга (замена символов, опечатки)."""
    # 1. Замена буквы o на цифру 0 (y0utube)
    f_y0utube = extract_heuristic_features("https://www.y0utube.com/")
    assert f_y0utube["Обнаружен тайпсквоттинг"] is not None
    assert f_y0utube["Обнаружен тайпсквоттинг"]["brand"] == "youtube"

    # 2. Замена о на 00 (g00gle)
    f_g00gle = extract_heuristic_features("https://g00gle.com")
    assert f_g00gle["Обнаружен тайпсквоттинг"] is not None
    assert f_g00gle["Обнаружен тайпсквоттинг"]["brand"] == "google"

    # 3. Замена l на 1 (paypa1)
    f_paypa1 = extract_heuristic_features("http://paypa1.com/login")
    assert f_paypa1["Обнаружен тайпсквоттинг"] is not None
    assert f_paypa1["Обнаружен тайпсквоттинг"]["brand"] == "paypal"

    # 4. Опечатка / расстояние 1 (yutube)
    f_yutube = extract_heuristic_features("https://yutube.com")
    assert f_yutube["Обнаружен тайпсквоттинг"] is not None
    assert f_yutube["Обнаружен тайпсквоттинг"]["brand"] == "youtube"

    # 5. Легитимные адреса НЕ должны помечаться как тайпсквоттинг
    f_youtube_clean = extract_heuristic_features("https://www.youtube.com/watch?v=123")
    assert f_youtube_clean["Обнаружен тайпсквоттинг"] is None

    f_google_clean = extract_heuristic_features("https://www.google.com")
    assert f_google_clean["Обнаружен тайпсквоттинг"] is None

    print("✅ test_typosquatting_detection — все тесты пройдены")


def run_all_tests():
    """Запускает все тесты последовательно."""
    print("=" * 50)
    print("  PhishGuard — Тестирование")
    print("=" * 50)
    print()

    test_is_valid_url()
    test_extract_features_normal_url()
    test_extract_features_suspicious_url()
    test_extract_features_with_brands()
    test_extract_features_shortener()
    test_suspicion_summary_clean()
    test_suspicion_summary_suspicious()
    test_empty_and_edge_cases()
    test_typosquatting_detection()

    print()
    print("=" * 50)
    print("  ✅ Все тесты пройдены успешно!")
    print("=" * 50)


if __name__ == "__main__":
    run_all_tests()
