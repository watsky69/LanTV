"""
url_features.py — Модуль анализа текстовых признаков URL.

Этот модуль извлекает из строки URL набор эвристических признаков,
включая детекцию тайпсквоттинга (подделки доменов известных брендов с помощью
замены символов на похожие или цифры: leetspeak, опечатки, расстояние Левенштейна).

Признаки используются для информирования пользователя и гибридного анализа.
ML-модель работает отдельно — на основе TF-IDF символьных n-грамм текста URL.
"""

import re
from urllib.parse import urlparse

# Популярные сервисы и бренды, часто становящиеся мишенями фишинга (для проверки домена)
POPULAR_TARGETS = [
    "youtube", "google", "yandex", "vkontakte", "vk", "telegram",
    "sberbank", "sber", "tinkoff", "tbank", "gosuslugi", "mail",
    "paypal", "apple", "microsoft", "amazon", "netflix",
    "facebook", "instagram", "whatsapp", "twitter", "roblox",
    "steam", "discord", "binance", "yahoo", "wikipedia"
]

# Ключевые слова и бренды для поиска в строке URL
SUSPICIOUS_KEYWORDS = [
    "paypal", "apple", "google", "microsoft", "facebook",
    "instagram", "amazon", "netflix", "bank", "secure",
    "login", "verify", "update", "account", "signin",
    "youtube", "yandex", "vkontakte", "telegram",
    "сбербанк", "сбер", "тинькофф", "госуслуги",
]


def levenshtein_distance(s1: str, s2: str) -> int:
    """
    Вычисляет расстояние Левенштейна (редакционное расстояние)
    между двумя строками.
    """
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def extract_domain_body(hostname: str) -> str:
    """
    Извлекает смысловое тело доменного имени (Second-Level Domain),
    отсекая поддомен www и зоны (.com, .ru, .co.uk и др.).
    Например:
      'www.y0utube.com' -> 'y0utube'
      'google.co.uk' -> 'google'
      'sub.vk0ntakte.ru' -> 'vk0ntakte'
    """
    if not hostname:
        return ""
    h = hostname.lower().strip()
    if h.startswith("www."):
        h = h[4:]

    parts = h.split(".")
    if len(parts) >= 2:
        two_part_tlds = {"co.uk", "com.ru", "org.ru", "net.ru", "msk.ru"}
        if len(parts) >= 3 and ".".join(parts[-2:]) in two_part_tlds:
            return parts[-3]
        return parts[-2]
    return parts[0]


def check_typosquatting(domain_body: str) -> dict | None:
    """
    Проверяет доменное имя на признаки тайпсквоттинга и подделки брендов:
    1. Замена символов на цифры / похожие буквы (leetspeak: 0->o, 1->l, 3->e и др.).
    2. Расстояние Левенштейна (опечатки, вставка/удаление 1-2 символов).
    """
    if not domain_body:
        return None

    d = domain_body.lower()

    # Замены символов (leetspeak / омоглифы)
    leets = [
        ("0", "o"),
        ("1", "l"),
        ("3", "e"),
        ("4", "a"),
        ("5", "s"),
        ("8", "b"),
        ("vv", "w"),
        ("rn", "m"),
    ]
    norm = d
    for char, rep in leets:
        norm = norm.replace(char, rep)

    for brand in POPULAR_TARGETS:
        # Если это легитимный бренд в чистом виде — не считаем нарушением
        if d == brand:
            continue

        # 1. Замена символов/цифр на leetspeak (например y0utube -> youtube)
        if norm == brand:
            return {
                "brand": brand,
                "reason": f"замена символов (leetspeak), имитирующая бренд '{brand}'",
                "is_critical": True,
            }

        # 2. Небольшое расстояние Левенштейна (опечатка или вставка)
        dist = levenshtein_distance(d, brand)
        if dist == 1 and len(brand) >= 4:
            return {
                "brand": brand,
                "reason": f"опечатка или замена 1 символа (расстояние=1), имитирующая '{brand}'",
                "is_critical": True,
            }
        if dist == 2 and len(brand) >= 6:
            return {
                "brand": brand,
                "reason": f"искажение 2 символов (расстояние=2), имитирующее '{brand}'",
                "is_critical": False,
            }

    return None


def extract_heuristic_features(url: str) -> dict:
    """
    Извлекает набор эвристических признаков из URL.

    Возвращает dict со всеми параметрами.
    """
    features = {}

    # --- Длина URL ---
    features["Длина URL"] = len(url)

    # --- Разбор URL ---
    try:
        parsed = urlparse(url if "://" in url else "http://" + url)
    except Exception:
        parsed = urlparse("http://invalid")

    hostname = parsed.hostname or ""
    path = parsed.path or ""

    domain_body = extract_domain_body(hostname)
    features["Основной домен"] = domain_body

    # --- Количество поддоменов ---
    parts = hostname.split(".")
    subdomain_count = max(0, len(parts) - 2)
    features["Количество поддоменов"] = subdomain_count

    # --- Наличие символа @ ---
    features["Символ @ в URL"] = "@" in url

    # --- Наличие IP-адреса вместо домена ---
    ip_pattern = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")
    features["IP-адрес вместо домена"] = bool(ip_pattern.match(hostname))

    # --- Наличие подозрительных символов и паттернов ---
    suspicious_patterns = {
        "Дефис в домене": "-" in hostname,
        "Символ // в пути": "//" in path,
        "Много точек в домене (>3)": hostname.count(".") > 3,
        "Цифры в имени домена": bool(re.search(r"\d", domain_body)),
    }
    features.update(suspicious_patterns)

    # --- Использование HTTPS ---
    features["Использует HTTPS"] = url.lower().startswith("https://")

    # --- Длина домена ---
    features["Длина доменного имени"] = len(hostname)

    # --- Количество спецсимволов в URL ---
    special_chars = sum(1 for c in url if c in "?&=#%!~|^")
    features["Спецсимволы в URL"] = special_chars

    # --- Проверка тайпсквоттинга ---
    typo_info = check_typosquatting(domain_body)
    features["Обнаружен тайпсквоттинг"] = typo_info

    # --- Наличие популярных брендов и ключевых слов в URL ---
    url_lower = url.lower()
    found_brands = [b for b in SUSPICIOUS_KEYWORDS if b in url_lower]
    features["Упоминание известных брендов"] = found_brands if found_brands else None

    # --- Сокращённые ссылки ---
    shorteners = [
        "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly",
        "is.gd", "buff.ly", "clck.ru", "cutt.ly", "rb.gy",
        "short.link", "clc.to",
    ]
    is_shortened = any(s in hostname for s in shorteners)
    features["Сокращённая ссылка"] = is_shortened

    return features


def get_suspicion_summary(features: dict) -> list:
    """
    Формирует список подозрительных находок на основе признаков.
    """
    warnings = []

    # 1. Критическая проверка: тайпсквоттинг
    typo = features.get("Обнаружен тайпсквоттинг")
    if typo:
        warnings.append(
            f"🚨 **ОБНАРУЖЕН ТАЙПСКВОТТИНГ (подделка бренда)**: "
            f"Доменное имя '{features.get('Основной домен')}' имитирует '{typo['brand']}' "
            f"({typo['reason']}). Это один из наиболее опасных видов фишинга!"
        )

    if features.get("Цифры в имени домена") and not typo:
        warnings.append(
            f"🔸 В имени домена присутствуют цифры ('{features.get('Основной домен')}') — "
            f"может использоваться для замены букв (leetspeak) или генерации доменов."
        )

    if features.get("Длина URL", 0) > 75:
        warnings.append(
            f"🔸 Длина URL ({features['Длина URL']} символов) — "
            f"длинные ссылки чаще используются для маскировки."
        )

    if features.get("Количество поддоменов", 0) > 2:
        warnings.append(
            f"🔸 Много поддоменов ({features['Количество поддоменов']}) — "
            f"может использоваться для имитации доверенного домена."
        )

    if features.get("Символ @ в URL"):
        warnings.append(
            "🔸 URL содержит символ @ — этот символ может использоваться "
            "для перенаправления на другой адрес."
        )

    if features.get("IP-адрес вместо домена"):
        warnings.append(
            "🔸 Вместо доменного имени используется IP-адрес — "
            "легитимные сайты обычно используют доменные имена."
        )

    if features.get("Много точек в домене (>3)"):
        warnings.append(
            "🔸 Домен содержит много точек — возможна попытка "
            "имитировать известный ресурс."
        )

    if features.get("Дефис в домене"):
        warnings.append(
            "🔸 Домен содержит дефис — фишинговые сайты часто "
            "используют дефисы для имитации (например, pay-pal.com)."
        )

    brands = features.get("Упоминание известных брендов")
    if brands and not typo:
        warnings.append(
            f"🔸 В URL упоминаются известные бренды: {', '.join(brands)} — "
            f"убедитесь, что это действительно официальный сайт компании."
        )

    if features.get("Сокращённая ссылка"):
        warnings.append(
            "🔸 Ссылка выглядит как сокращённая — "
            "это скрывает реальный адрес назначения."
        )

    if not features.get("Использует HTTPS"):
        warnings.append(
            "🔸 Ссылка не использует HTTPS — "
            "большинство легитимных сайтов используют защищённое соединение."
        )

    return warnings


def is_valid_url(url: str) -> bool:
    """
    Проверяет корректность формата строки URL.
    """
    if not url or not url.strip():
        return False

    url = url.strip()

    if len(url) < 4:
        return False

    if "://" not in url:
        url = "http://" + url

    try:
        parsed = urlparse(url)
        if not parsed.hostname:
            return False
        if "." not in parsed.hostname:
            return False
        return True
    except Exception:
        return False
