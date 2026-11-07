# ============================================================
# SETTINGS
# ============================================================
import os
import sys
from pathlib import Path

# Корень приложения: рядом с exe в сборке или рядом с исходниками при запуске Python.
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent

SINGBOX_PATH = str(APP_DIR / "singBox" / "sing-box.exe")
SINGBOX_CONFIG = str(APP_DIR / "sing_box_config.json")

LOCAL_PORT_START = 10800

OUTPUT_FILENAME = str(APP_DIR / "fast_vless.txt")
CHECKPOINT_FILENAME = str(APP_DIR / ".fast_vless_checkpoint.json")

BATCH_SIZE = int(os.getenv("VLESS_BATCH_SIZE", "300"))

# Начальная / минимальная / максимальная конкуррентность.
INITIAL_CONCURRENT_TESTS = int(os.getenv("VLESS_CONCURRENCY", "100"))
MIN_CONCURRENT_TESTS = max(1, int(os.getenv("VLESS_MIN_CONCURRENCY", "10")))
MAX_CONCURRENT_TESTS = max(INITIAL_CONCURRENT_TESTS, int(os.getenv("VLESS_MAX_CONCURRENCY", "300")))
# GLOBAL STATE
current_concurrency = INITIAL_CONCURRENT_TESTS

PROXY_TIMEOUT = 6.0
BATCH_TIMEOUT = 30.0

HTTP_CONNECT_TIMEOUT = 2.5
HTTP_READ_TIMEOUT = 3.0 
HTTP_WRITE_TIMEOUT = 2.5
HTTP_POOL_TIMEOUT = 1.5

SINGBOX_START_DELAY = 1.5
CLEANUP_DELAY = 0.5

RETRY_COUNT = 0

TARGET_WORKING = int(os.getenv("VLESS_TARGET_WORKING", "60"))

TEST_URLS = "https://www.google.com"

IP_CHECK_URL = "https://api.ipify.org?format=json"

GIT_BRANCH = "main"

COMMIT_MESSAGE = (
    "PULSEWAVE: update checked proxy configuration list"
)

REPO_PATH = str(APP_DIR)
# Путь к файлу со ссылками
SOURCES_FILE = str(APP_DIR / "sources.txt")

# Автоматически читаем ссылки из файла, если он существует
if os.path.exists(SOURCES_FILE):
    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        # Читаем строки, убираем пробелы/переносы и отсекаем пустые строки или комментарии с #
        SOURCES = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
else:
    # Если файла нет (например, кто-то скачал код с GitHub), скрипт просто получит пустой список
    SOURCES = []
