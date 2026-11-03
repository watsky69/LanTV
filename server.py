"""
LanUs Rating 3.0 (v5.0 PRO) - Entry point wrapper for FastAPI Server (main.py)
Сохраняет обратную совместимость со скриптом запуска START.bat.
"""

import os
import uvicorn
from main import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print("=================================================================")
    print("🚀 LANUS CS2 ANALYTICS FASTAPI SERVER (v5.0 PRO + DB) ЗАПУЩЕН!")
    print(f"🌐 Веб-интерфейс: http://localhost:{port}")
    print(f"📘 Swagger UI (REST API): http://localhost:{port}/docs")
    print("=================================================================")
    uvicorn.run("main:app", host="127.0.0.1", port=port, reload=True, log_level="info")
