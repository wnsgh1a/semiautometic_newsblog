# System Requirements & Specifications

## 1. 시스템 목적

해외 유력 축구 언론사의 RSS 피드를 실시간 모니터링하여 신규 기사를 수집하고, DeepSeek LLM을 통해 국내 축구 팬 성향에 맞게 가공한 뒤 디스코드 웹훅으로 운영자에게 전송하는 반자동 시스템.

## 2. 기능 요구사항 (Functional Requirements)

- **F-01 (멀티 RSS 수집):** BBC, Sky Sports, Daily Mail, The Independent, The Guardian, ESPN, [Goal.com](http://Goal.com)의 공식 RSS 피드를 순회 파싱한다.

- **F-02 (중복 필터링):** 기사의 고유 URL을 DB와 대조하여 이미 처리된 기사는 DeepSeek API 호출을 차단한다.

- **F-03 (LLM 큐레이션):** DeepSeek API`deepseek-chat`)를 활용해 [출처], [SEO 최적화 제목], [3줄 요약], [본문 번역 및 분석], [추천 이미지 프롬프트(영문)], [추천 태그] 구조의 한국어 텍스트를 생성한다.

- **F-04 (디스코드 전송):** 가공된 콘텐츠와 함께 하단에 하이퍼링크 형태의 원문 URL을 포함하여 디스코드 웹훅으로 발송한다. 메시지가 디스코드 2000자 제한을 넘으면 분할 전송한다.

- **F-05 (수집량 제어):** 피드당 처리 건수(`MAX_ENTRIES_PER_FEED`)와 기사 최신성(`MAX_AGE_HOURS`)을 제한하여 초기 대량 적재 시 DeepSeek 비용 폭발과 Discord rate limit을 방지한다.

- **F-06 (전송 보장):** DeepSeek 가공 결과를 DB에 먼저 저장(`notified=0`)한 뒤 Discord로 전송하고, 성공 시 `notified=1`로 갱신한다. 전송 실패분은 다음 실행 시 DeepSeek 재호출 없이 DB에서 재발송한다.

## 3. 데이터베이스 정의 (MySQL)

- **DB Name:** `football_db`

- **Table Name:** `processed_news`

```sql
CREATE TABLE IF NOT EXISTS processed_news (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    url          VARCHAR(768) UNIQUE NOT NULL,
    title        VARCHAR(255),
    content      MEDIUMTEXT,
    tags         VARCHAR(512),
    image_prompt TEXT,
    notified     TINYINT(1) NOT NULL DEFAULT 0,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

- `content`: DeepSeek 가공 전문(블로그 발행 재활용용)
- `tags`: 추출된 추천 태그(쉼표 구분)
- `image_prompt`: 추출된 영문 이미지 생성 프롬프트
- `notified`: Discord 전송 완료 여부 (0=미전송, 1=전송완료)

> 기존 테이블이 구버전 스키마인 경우 `main.py`의 `ensure_table()`이 `information_schema` 기반으로 누락 컬럼을 자동 `ALTER` 한다.