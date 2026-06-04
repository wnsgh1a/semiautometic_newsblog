# System Requirements & Specifications

## 1. 시스템 목적

해외 유력 축구 언론사의 RSS 피드를 실시간 모니터링하여 신규 기사를 수집하고, DeepSeek LLM을 통해 국내 축구 팬 성향에 맞게 가공한 뒤 디스코드 웹훅으로 운영자에게 전송하는 반자동 시스템.

## 2. 기능 요구사항 (Functional Requirements)

- **F-01 (멀티 RSS 수집):** BBC, Sky Sports, Daily Mail, The Independent, The Guardian, ESPN, [Goal.com](http://Goal.com)의 공식 RSS 피드를 순회 파싱한다.

- **F-02 (중복 필터링):** 기사의 고유 URL을 DB와 대조하여 이미 처리된 기사는 DeepSeek API 호출을 차단한다.

- **F-03 (LLM 큐레이션):** DeepSeek API`deepseek-chat`)를 활용해 [출처], [SEO 최적화 제목], [3줄 요약], [본문 번역 및 분석] 구조의 한국어 텍스트를 생성한다.

- **F-04 (디스코드 전송):** 가공된 콘텐츠와 함께 하단에 하이퍼링크 형태의 원문 URL을 포함하여 디스코드 웹훅으로 발송한다.

## 3. 데이터베이스 정의 (MySQL)

- **DB Name:** `football_db`

- **Table Name:** `processed_news`

```sql

CREATE TABLE IF NOT EXISTS processed_news (

    id INT AUTO_INCREMENT PRIMARY KEY,

    url VARCHAR(768) UNIQUE NOT NULL,

    title VARCHAR(255),

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

);