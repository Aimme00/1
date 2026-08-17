from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for raw_line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
os.environ.update(
    {
        "ASKDATA_ENV": "testing",
        "ASKDATA_ALLOW_MOCK_MODEL": "false",
        "ASKDATA_DATABASE_TYPE": "sqlite",
        "ASKDATA_RUNTIME_DIR": "/private/tmp/askdata-realapi-smoke-20260814",
        "ASKDATA_GUEST_QUERY_LIMIT": "100",
        "ASKDATA_COOKIE_SECURE": "false",
    }
)

from backend.service import AskDataApplicationService


def main() -> None:
    runtime_dir = Path(os.environ["ASKDATA_RUNTIME_DIR"])
    service = AskDataApplicationService(runtime_dir=runtime_dir)
    try:
        record = service.submit_chat(
            query="统计最近30天已完成订单的销售总额，只返回销售总额，字段别名为 total_sales。",
            session_id="real-api-smoke",
            user_id="real-api-smoke",
            generate_chart=False,
        )
        run_id = record.run_id
        deadline = time.time() + 180
        while time.time() < deadline:
            run = service.runs.get(run_id)
            if run.status in {"completed", "failed"}:
                break
            time.sleep(0.25)
        snapshot = service.runs.snapshot(run_id)
        result = snapshot.get("result") or {}
        table = result.get("table") or {}
        print(
            {
                "status": snapshot.get("status"),
                "error": snapshot.get("error"),
                "provider": os.getenv("ASKDATA_LLM_PROVIDER"),
                "mock_allowed": os.getenv("ASKDATA_ALLOW_MOCK_MODEL"),
                "columns": table.get("columns"),
                "rows": table.get("rows"),
                "sql": result.get("sql"),
            }
        )
    finally:
        service.close()


if __name__ == "__main__":
    main()
