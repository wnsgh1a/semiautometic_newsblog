import os
import sys
import time
import feedparser
import requests
import mysql.connector
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

RSS_SOURCES = [
    {"name": "BBC Sport",          "url": "https://feeds.bbci.co.uk/sport/football/rss.xml"},
    {"name": "Sky Sports",         "url": "https://www.skysports.com/rss/12040"},
    {"name": "Daily Mail",         "url": "https://www.dailymail.co.uk/sport/football/index.rss"},
    {"name": "The Independent",    "url": "https://www.independent.co.uk/sport/football/rss"},
    {"name": "The Guardian",       "url": "https://www.theguardian.com/football/rss"},
    {"name": "ESPN FC",            "url": "https://www.espn.com/espn/rss/soccer/news"},
    {"name": "Goal.com",           "url": "https://www.goal.com/feeds/en/news"},
]

CURATION_PROMPT = """\
다음 해외 축구 뉴스 기사를 국내 축구 팬을 위해 한국어로 가공해줘.
반드시 아래 섹션 헤더와 순서를 그대로 지켜. 다른 말머리나 추가 섹션을 넣지 마.

[출처] {source}
[SEO 최적화 제목] (검색 최적화된 한국어 제목 한 줄)
[3줄 요약]
- 핵심 요약 1
- 핵심 요약 2
- 핵심 요약 3
[본문 번역 및 분석]
(원문을 자연스럽게 번역하고, 한국 팬 시각에서의 의미와 맥락을 분석)

[추천 이미지 프롬프트 (영문)]
(기사 핵심 장면·인물·구단·대회를 반영한, Midjourney/DALL-E에 바로 붙여넣을 수 있는 영어 프롬프트 한 덩어리.
 80~150단어. 문단 없이 한 줄 또는 쉼표로 이어진 상세 묘사.
 subject, action, kit colors, stadium mood, lighting, camera angle, lens style, photorealistic 또는 editorial sports illustration 중 하나를 명시.
 no text, no watermark, no logo, no distorted faces 를 반드시 포함.)

[추천 태그 (5개 내외, 쉼표 구분)]
(티스토리·네이버 검색에 유리한 한국어 키워드 4~6개. 쉼표와 공백으로만 구분. # 기호 금지.)

---
원문 출처: {source}
원문 제목: {title}
원문 요약: {summary}
"""


def get_db_connection():
    return mysql.connector.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", 3306)),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        charset="utf8mb4",
        collation="utf8mb4_unicode_ci",
    )


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS processed_news (
                id         INT AUTO_INCREMENT PRIMARY KEY,
                url        VARCHAR(768) UNIQUE NOT NULL,
                title      VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)
    conn.commit()


def is_duplicate(conn, url: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM processed_news WHERE url = %s LIMIT 1", (url,))
        return cur.fetchone() is not None


def record_processed(conn, url: str, title: str):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT IGNORE INTO processed_news (url, title) VALUES (%s, %s)",
            (url, title[:255] if title else ""),
        )
    conn.commit()


def curate(source_name: str, title: str, summary: str) -> str:
    client = OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com",
    )
    prompt = CURATION_PROMPT.format(
        source=source_name,
        title=title,
        summary=summary or "(원문 요약 없음)",
    )
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2048,
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()


def send_discord(curated_text: str, article_url: str):
    payload = {"content": f"{curated_text}\n\n🔗 **원문 보기:** {article_url}"}
    resp = requests.post(
        os.environ["DISCORD_WEBHOOK_URL"],
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()


def log(source: str, status: str, detail: str = ""):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    suffix = f" — {detail}" if detail else ""
    print(f"[{ts}] [{source}] {status}{suffix}")


def process_source(conn, source: dict):
    name = source["name"]
    try:
        feed = feedparser.parse(
            source["url"],
            request_headers={"User-Agent": "Mozilla/5.0"},
        )
    except Exception as exc:
        log(name, "ERROR (피드 파싱)", str(exc))
        return

    if not feed.entries:
        log(name, "WARN", "수집된 항목 없음")
        return

    for entry in feed.entries:
        url = getattr(entry, "link", None)
        if not url:
            continue

        title   = getattr(entry, "title",   "")
        summary = getattr(entry, "summary", "")

        try:
            if is_duplicate(conn, url):
                log(name, "SKIP (중복)", title[:70])
                continue

            curated = curate(name, title, summary)
            send_discord(curated, url)
            record_processed(conn, url, title)
            log(name, "SUCCESS", title[:70])

            time.sleep(1)

        except Exception as exc:
            log(name, "ERROR (기사 처리)", f"{title[:50]} | {exc}")


def main():
    try:
        conn = get_db_connection()
    except (KeyError, mysql.connector.Error):
        log("SYSTEM", "ERROR", "데이터베이스 연결 실패")
        sys.exit(1)

    try:
        ensure_table(conn)
        for source in RSS_SOURCES:
            process_source(conn, source)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
