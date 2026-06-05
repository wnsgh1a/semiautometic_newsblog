import os
import sys
import time
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta

import feedparser
import requests
import mysql.connector
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

REQUIRED_ENV = [
    "DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME",
    "DEEPSEEK_API_KEY", "DISCORD_WEBHOOK_URL",
]

MAX_ENTRIES_PER_FEED = int(os.environ.get("MAX_ENTRIES_PER_FEED", 5))
MAX_AGE_HOURS        = int(os.environ.get("MAX_AGE_HOURS", 48))
MAX_PENDING_RETRY    = int(os.environ.get("MAX_PENDING_RETRY", 20))
LOG_FILE             = os.environ.get("LOG_FILE", "logs/autoblog.log")
DISCORD_CHUNK_LIMIT  = 1900

RSS_SOURCES = [
    {"name": "BBC (Man Utd)",      "url": "https://feeds.bbci.co.uk/sport/football/teams/manchester-united/rss.xml"},
    {"name": "Guardian (Man Utd)", "url": "https://www.theguardian.com/football/manchester-united/rss"},
    {"name": "MEN (Man Utd)",      "url": "https://www.manchestereveningnews.co.uk/all-about/manchester-united-fc/?service=rss"},
]

TEAM_KEYWORDS = [
    "manchester united", "man united", "man utd", "man u",
    "red devils", "old trafford", "mufc",
    "맨체스터 유나이티드", "맨유", "맨United",
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

SECTION_MARKERS = [
    ("title",        "[SEO 최적화 제목]"),
    ("summary",      "[3줄 요약]"),
    ("body",         "[본문 번역 및 분석]"),
    ("image_prompt", "[추천 이미지 프롬프트"),
    ("tags",         "[추천 태그"),
]

logger = logging.getLogger("autoblog")


def setup_logging():
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        fmt="[%(asctime)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    try:
        os.makedirs(os.path.dirname(LOG_FILE) or ".", exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        pass


def log(source: str, status: str, detail: str = ""):
    suffix = f" - {detail}" if detail else ""
    logger.info(f"[{source}] {status}{suffix}")


def validate_env():
    missing = [key for key in REQUIRED_ENV if not os.environ.get(key)]
    if missing:
        log("SYSTEM", "ERROR", f"필수 환경변수 누락: {', '.join(missing)}")
        sys.exit(1)


def with_retry(fn, *, attempts=3, base_delay=2.0):
    last_exc = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(base_delay * (2 ** attempt))
    raise last_exc


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
                id           INT AUTO_INCREMENT PRIMARY KEY,
                url          VARCHAR(768) UNIQUE NOT NULL,
                title        VARCHAR(255),
                content      MEDIUMTEXT,
                tags         VARCHAR(512),
                image_prompt TEXT,
                notified     TINYINT(1) NOT NULL DEFAULT 0,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
        """)

        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = %s AND table_name = 'processed_news'
            """,
            (os.environ["DB_NAME"],),
        )
        existing = {row[0].lower() for row in cur.fetchall()}

        migrations = {
            "content":      "MEDIUMTEXT",
            "tags":         "VARCHAR(512)",
            "image_prompt": "TEXT",
            "notified":     "TINYINT(1) NOT NULL DEFAULT 0",
        }
        for column, ddl in migrations.items():
            if column not in existing:
                cur.execute(f"ALTER TABLE processed_news ADD COLUMN {column} {ddl}")
    conn.commit()


def is_duplicate(conn, url: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM processed_news WHERE url = %s LIMIT 1", (url,))
        return cur.fetchone() is not None


def save_article(conn, url, title, content, tags, image_prompt) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT IGNORE INTO processed_news
                (url, title, content, tags, image_prompt, notified)
            VALUES (%s, %s, %s, %s, %s, 0)
            """,
            (url, (title or "")[:255], content, (tags or "")[:512], image_prompt),
        )
        conn.commit()
        return cur.lastrowid


def mark_notified(conn, row_id: int):
    with conn.cursor() as cur:
        cur.execute("UPDATE processed_news SET notified = 1 WHERE id = %s", (row_id,))
    conn.commit()


def fetch_pending(conn):
    with conn.cursor(dictionary=True) as cur:
        cur.execute(
            """
            SELECT id, url, content FROM processed_news
            WHERE notified = 0 ORDER BY id ASC LIMIT %s
            """,
            (MAX_PENDING_RETRY,),
        )
        return cur.fetchall()


def parse_curation(text: str) -> dict:
    sections = {key: "" for key, _ in SECTION_MARKERS}
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        matched = next(
            (key for key, prefix in SECTION_MARKERS if stripped.startswith(prefix)),
            None,
        )
        if matched:
            current = matched
            remainder = stripped.split("]", 1)[1].strip() if "]" in stripped else ""
            sections[current] = remainder
            continue
        if current and stripped:
            sections[current] = (sections[current] + "\n" + stripped).strip()
    return sections


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

    def call():
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            temperature=0.7,
            timeout=60,
        )
        return response.choices[0].message.content.strip()

    return with_retry(call)


def chunk_text(text: str, size: int):
    chunks, current = [], ""
    for line in text.split("\n"):
        while len(line) > size:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:size])
            line = line[size:]
        if len(current) + len(line) + 1 > size:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def notify_discord(content: str, url: str):
    full = f"{content}\n\n🔗 원문 보기: {url}"
    webhook = os.environ["DISCORD_WEBHOOK_URL"]
    for chunk in chunk_text(full, DISCORD_CHUNK_LIMIT):
        def post(payload=chunk):
            resp = requests.post(webhook, json={"content": payload}, timeout=10)
            resp.raise_for_status()

        with_retry(post)
        time.sleep(1)


def retry_pending_notifications(conn):
    pending = fetch_pending(conn)
    for row in pending:
        try:
            notify_discord(row["content"] or "", row["url"])
            mark_notified(conn, row["id"])
            log("RECOVERY", "SUCCESS", f"미전송 재발송 #{row['id']}")
        except Exception as exc:
            log("RECOVERY", "ERROR", f"재발송 실패 #{row['id']} | {exc}")


def is_recent(entry) -> bool:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return True
    published = datetime.fromtimestamp(time.mktime(parsed))
    return datetime.now() - published <= timedelta(hours=MAX_AGE_HOURS)


def is_relevant(title: str, summary: str) -> bool:
    haystack = f"{title} {summary}".lower()
    return any(keyword.lower() in haystack for keyword in TEAM_KEYWORDS)


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

    processed = 0
    for entry in feed.entries:
        if processed >= MAX_ENTRIES_PER_FEED:
            break

        url = getattr(entry, "link", None)
        if not url:
            continue

        title = getattr(entry, "title", "")
        summary = getattr(entry, "summary", "")

        try:
            if is_duplicate(conn, url):
                log(name, "SKIP (중복)", title[:70])
                continue

            if not is_recent(entry):
                log(name, "SKIP (기간초과)", title[:70])
                continue

            if not is_relevant(title, summary):
                log(name, "SKIP (비관련)", title[:70])
                continue

            curated = curate(name, title, summary)
            sections = parse_curation(curated)

            row_id = save_article(
                conn, url, title, curated,
                sections.get("tags", ""),
                sections.get("image_prompt", ""),
            )
            processed += 1

            try:
                notify_discord(curated, url)
                mark_notified(conn, row_id)
                log(name, "SUCCESS", title[:70])
            except Exception as exc:
                log(name, "WARN (전송보류)", f"{title[:50]} | {exc}")

        except Exception as exc:
            log(name, "ERROR (기사 처리)", f"{title[:50]} | {exc}")


def main():
    setup_logging()
    validate_env()

    try:
        conn = get_db_connection()
    except mysql.connector.Error as exc:
        log("SYSTEM", "ERROR", f"데이터베이스 연결 실패 | {exc}")
        sys.exit(1)

    try:
        ensure_table(conn)
        retry_pending_notifications(conn)
        for source in RSS_SOURCES:
            process_source(conn, source)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
