"""
Инструмент локального профилирования точек выдачи расписания.

Замеряет время отклика, размер сырого тела ответа и размер со сжатием gzip
для различных эндпоинтов на боевом датасете КТМУ с помощью стандартной библиотеки.
"""
import gzip
import http.cookiejar
import json
import time
import urllib.request
from typing import Any

BASE_URL = "http://127.0.0.1:5000"


def create_session_opener() -> urllib.request.OpenerDirector:
    """Создает opener с поддержкой сессионных cookie для авторизованных запросов."""
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    login_data = json.dumps({"login": "timetable", "password": "12345678"}).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/api/auth/login",
        data=login_data,
        headers={"Content-Type": "application/json", "Origin": BASE_URL},
    )
    with opener.open(req) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Login failed: status {resp.status}")
    return opener


def benchmark_single_url(
    opener: urllib.request.OpenerDirector, path: str, iterations: int = 3
) -> dict[str, Any]:
    """Замеряет среднее время, сырой размер и размер в gzip для указанного пути."""
    times: list[float] = []
    content = b""
    for _ in range(iterations):
        t0 = time.perf_counter()
        req = urllib.request.Request(f"{BASE_URL}{path}")
        with opener.open(req) as resp:
            content = resp.read()
        times.append(time.perf_counter() - t0)

    compressed = gzip.compress(content, compresslevel=6)
    return {
        "path": path,
        "avg_ms": (sum(times) / len(times)) * 1000.0,
        "raw_bytes": len(content),
        "gzip_bytes": len(compressed),
    }


def format_benchmark_row(item: dict[str, Any]) -> str:
    """Форматирует строку таблицы замеров в удобочитаемом виде."""
    p = item["path"]
    ms = f"{item['avg_ms']:.1f} ms"
    raw_kb = f"{item['raw_bytes'] / 1024:.1f} KB"
    gz_kb = f"{item['gzip_bytes'] / 1024:.1f} KB"
    ratio = f"{(1 - item['gzip_bytes'] / max(1, item['raw_bytes'])) * 100:.1f}%"
    return f"| `{p}` | {raw_kb} | {gz_kb} | {ratio} | {ms} |"


def run_benchmark_suite() -> list[dict[str, Any]]:
    """Прогоняет замеры по всем ключевым публичным и приватным эндпоинтам."""
    endpoints = [
        "/api/storage/schedule-data-v2?full=1",
        "/api/editor/schedule-data-v2?section=bootstrap",
        "/api/editor/schedule-data-v2/schedule?groupId=9b28grdj",
        "/api/editor/schedule-data-v2/schedule?groupId=9b28grdj&week=1",
        "/api/editor/schedule-data-v2?section=loads",
        "/api/editor/schedule-data-v2?section=graphState",
        "/api/public-index",
        "/api/public-schedule/group/9b28grdj",
    ]
    opener = create_session_opener()
    return [benchmark_single_url(opener, ep) for ep in endpoints]


def main() -> None:
    """Точка входа бенчмарка: собирает данные и выводит Markdown-отчёт."""
    results = run_benchmark_suite()
    header = (
        "| Эндпоинт | Raw Size | Gzip Size | Экономия сжатия | Latency (avg) |\n"
        "|---|---|---|---|---|"
    )
    rows = [format_benchmark_row(res) for res in results]
    print("\n".join([header] + rows))


if __name__ == "__main__":
    main()
