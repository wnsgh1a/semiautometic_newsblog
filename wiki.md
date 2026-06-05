# System Infrastructure & Troubleshooting Wiki

## 1. 외부 API 스펙 및 정산 정보

- **DeepSeek API:** OpenAI SDK 호환 구조 사용 (`base_url="https://api.deepseek.com"`). `deepseek-chat` 모델 사용 시 토큰당 비용이 매우 저렴하므로 공장형 수집에 최적화됨.
- **Discord Webhook:** Rate Limit(초당 요청 제한) 유의. 단시간에 수십 개의 메시지를 쏠 경우 디스코드 측에서 블록할 수 있으므로, 초기 대량 적재 시 `time.sleep(1)` 등 딜레이 구현 필요.

## 2. 배포 및 실행 환경 (Deployment)

- **로컬 테스트:** 노트북 내 MySQL Workbench 활성화 및 로컬 파이썬 런타임 환경에서 수행.
- **운영 환경:** 오라클 클라우드 프리티어 (Ubuntu 가상 머신) 호스트 환경.
- **스케줄러 설정:** 리눅스 `crontab -e`를 통해 1분~10분 주기로 자동 실행 제어.
- 예시 (매 5분마다 실행): `*/5 * * * * /usr/bin/python3 /path/to/script.py`

## 3. 트러블슈팅 히스토리 (유지보수 시 지속 업데이트)

- **Issue 01 (타임아웃):** 해외 언론사 RSS 응답 지연 시 스크립트가 늘어지는 현상 방지를 위해 `requests` 및 `feedparser` 호출 시 내부 타임아웃 세팅(기본 10초) 필수 적용.
- **Issue 02 (문자열 인코딩):** 이모지 및 특수문자가 포함된 축구 뉴스 가공 시 MySQL 저장 에러 방지를 위해 데이터베이스 및 커넥션 규격을 반드시 `utf8mb4`로 통일할 것.
- **Issue 03 (Discord 2000자 제한):** 큐레이션 결과에 이미지 프롬프트·태그 섹션이 추가되며 메시지가 길어짐. `notify_discord()`에서 1900자 단위로 분할 전송하여 길이 초과 전송 실패를 방지함.
- **Issue 04 (비용 폭발/재처리):** 초기 대량 적재 시 DeepSeek 비용 폭발 방지를 위해 `MAX_ENTRIES_PER_FEED`/`MAX_AGE_HOURS`로 수집량 제한. Discord 전송 실패 시 DeepSeek 재호출을 막기 위해 가공 직후 DB에 `notified=0`으로 선저장하고, 다음 실행 시 미전송분만 재발송함.
- **Issue 05 (운영 로깅):** crontab 운영 시 `print` 로그 휘발 문제로 `RotatingFileHandler`(기본 `logs/autoblog.log`, 5MB×3) 파일 로깅 병행. 시작 시 필수 환경변수 일괄 검증 후 누락 시 즉시 종료.
