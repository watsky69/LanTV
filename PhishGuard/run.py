"""
run.py — Удобный и надёжный запуск PhishGuard.

Автоматически:
- Проверяет целостность проекта и наличие модели
- Находит свободный сетевой порт (8501, 8502, 8503...)
- Запускает Streamlit
- Открывает браузер
- Показывает понятные сообщения на русском языке в случае любых проблем
"""

import os
import sys
import socket
import time
import webbrowser
import subprocess

def is_port_in_use(port: int) -> bool:
    """Проверяет, занят ли указанный TCP-порт."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0

def find_available_port(start_port: int = 8501, max_attempts: int = 20) -> int:
    """Находит ближайший свободный порт."""
    for p in range(start_port, start_port + max_attempts):
        if not is_port_in_use(p):
            return p
    return start_port

def main():
    print("=" * 60)
    print("  🛡️  PhishGuard — Интеллектуальный запуск приложения")
    print("=" * 60)
    print()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)

    # 1. Проверка файлов
    app_path = os.path.join(base_dir, "app.py")
    model_path = os.path.join(base_dir, "models", "phishguard_model.joblib")

    if not os.path.isfile(app_path):
        print(f"❌ ОШИБКА: Файл app.py не найден в папке: {base_dir}")
        input("\nНажмите Enter для выхода...")
        sys.exit(1)

    if not os.path.isfile(model_path):
        print(f"⚠️ ВНИМАНИЕ: Файл обученной модели не найден: {model_path}")
        print("   Сначала обучите модель командой: python train_model.py")
        print()

    # 2. Выбор свободного порта
    port = find_available_port(8501)
    url = f"http://localhost:{port}"

    print(f"✅ Выбран порт: {port}")
    print(f"🌐 Адрес приложения: {url}")
    print()
    print("⏳ Запуск веб-сервера Streamlit...")
    print("   (Чтобы остановить приложение, нажмите Ctrl+C или закройте это окно)")
    print()

    # 3. Открываем браузер через 1.5 секунды после старта
    def open_browser():
        time.sleep(1.5)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    import threading
    threading.Thread(target=open_browser, daemon=True).start()

    # 4. Формируем команду запуска
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "app.py",
        f"--server.port={port}",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
    ]

    try:
        # Запускаем Streamlit в текущем окне консоли
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"\n⚠️ Приложение завершилось с кодом ошибки {result.returncode}")
            input("\nНажмите Enter для выхода...")
    except KeyboardInterrupt:
        print("\n\n🛑 Приложение остановлено пользователем.")
    except Exception as e:
        print(f"\n❌ Непредвиденная ошибка при запуске: {e}")
        input("\nНажмите Enter для выхода...")

if __name__ == "__main__":
    main()
