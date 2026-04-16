# GDC Vault Transcript Browser

GDC Vault에서 세션을 검색하고, 영상 자막(VTT)을 추출하여 텍스트로 변환하는 FastAPI 웹 애플리케이션

## 기술 스택
- 언어: Python 3.13
- 웹 프레임워크: FastAPI + Uvicorn
- 템플릿: Jinja2
- HTTP 클라이언트: aiohttp (비동기)
- HTML 파싱: BeautifulSoup4
- 프론트엔드: 순수 HTML/CSS/JS (별도 빌드 없음)

## 주요 명령어
- 설치: `pip install -r requirements.txt`
- 개발 서버: `uvicorn app:app --reload --port 8000`
- CLI 자막 추출: `python gdc_transcript.py <URL> [--lang eng] [-o ./transcripts]`

## 디렉토리 구조
- `app.py` — FastAPI 진입점, 라우트 정의
- `auth.py` — GDC Vault 인증 (PHPSESSID 쿠키 기반)
- `scraper.py` — 세션 목록/상세 페이지 스크래핑 (rate limiting 포함)
- `gdc_transcript.py` — m3u8에서 VTT 자막 추출 및 텍스트 변환 (CLI + 라이브러리)
- `config.py` — URL, rate limit, 이벤트/카테고리 설정
- `templates/` — Jinja2 HTML 템플릿 (base, login, browse)
- `static/` — 프론트엔드 JS (`app.js`) 및 CSS (`style.css`)
- `transcripts/` — 추출된 자막 파일 출력 디렉토리

## 핵심 아키텍처
- 단일 사용자 인증: `GDCAuth` 인스턴스가 앱 전역에서 aiohttp 세션 관리
- 스크래핑: `GDCScraper`가 rate-limited 요청으로 GDC Vault HTML 파싱
- m3u8 자막 추출: blazestreaming iframe → script_VOD.js 템플릿 → VTT 세그먼트 병렬 다운로드
- 출력 형식: `.vtt` (원본), `_timestamped.txt` (타임스탬프 포함), `.txt` (NotebookLM용 정리본)

## 코딩 컨벤션
- 들여쓰기: 2칸 스페이스
- 문자열: 쌍따옴표
- 세미콜론: 없음 (ASI, JS 파일에만 해당)
- 주석/로그 메시지: 한국어
- 타입 힌트: Python 3.10+ 유니온 문법 (`str | None`)
- 비동기: `async/await` 패턴 (aiohttp 기반)
- 로깅: `logging` 모듈 사용 (print는 CLI 모드에서만)

## 프로젝트 특이사항
- Git 리포지토리가 아님 — 현재 버전 관리 미설정
- GDC Vault 로그인 필요 — 유료 콘텐츠 접근 시 인증 세션 사용
- Rate limiting: 요청 간 1.5초 딜레이, 스크래핑 동시 3개, VTT 다운로드 동시 20개
- debug_*.html 파일은 디버깅용 임시 파일
