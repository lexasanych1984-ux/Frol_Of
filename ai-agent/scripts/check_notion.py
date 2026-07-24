"""Разовая проверка: работает ли запрос к Trading Journal через прямой
REST API (в отличие от MCP query_data_sources, который упирался в
"requires Business plan"). Запускать из корня проекта с активным venv:

    python scripts\\check_notion.py
"""
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

from src.notion_sync.client import NotionClient  # noqa: E402


def main():
    client = NotionClient()
    print("Пробую запросить Trading Journal через /v1/data_sources/{id}/query ...")
    count = 0
    try:
        for page in client.query_trading_journal():
            count += 1
            if count <= 3:
                title_prop = next(
                    (p for p in page.get("properties", {}).values() if p.get("type") == "title"),
                    None,
                )
                title = "".join(t["plain_text"] for t in title_prop["title"]) if title_prop else "?"
                print(f"  #{count}: {page['id']} — № {title!r}")
        print(f"\nУспех. Всего страниц: {count}")
    except Exception as e:
        print(f"\nОШИБКА: {e}")
        print("\nЕсли в тексте ошибки снова 'Business plan' — прямой REST API тоже упирается")
        print("в это ограничение плана воркспейса, придётся либо апгрейдить план Notion,")
        print("либо переписать client.py на постраничный обход через search/fetch.")


if __name__ == "__main__":
    main()
