# GDC Vault Transcript Browser

GDC(Game Developers Conference) Vault에서 세션을 검색하고, 자막을 추출한 뒤 Claude + Perplexity 로 후처리하여 **NotebookLM 업로드용 소스 번들**을 생성하는 FastAPI 웹앱 + CLI.

## 기술 스택
- Python 3.13
- FastAPI + Uvicorn (웹), Jinja2 (템플릿), Vanilla JS/CSS (프론트)
- aiohttp (비동기 HTTP), BeautifulSoup4 (HTML 파싱)
- Anthropic SDK (Claude) + Perplexity REST API
- 모든 LLM 호출은 `AsyncAnthropic` + ephemeral prompt caching

---

## 파일/모듈 색인

| 파일 | 역할 |
|---|---|
| `app.py` | FastAPI 엔트리, 라우트, 로그인/세션 목록/상세/추출 API |
| `auth.py` | GDC Vault 로그인 (PHPSESSID 쿠키 기반), `GDCAuth` 싱글톤 |
| `scraper.py` | 세션 목록·상세 스크래핑, `GDCScraper`, rate limiting |
| `gdc_transcript.py` | **오케스트레이터**: m3u8→VTT→AI 후처리→번들. CLI + 라이브러리 양용 |
| `ai_enhance.py` | Claude 후처리: chapters/glossary/keypoints/qa/entities + 정규화 패스 |
| `web_context.py` | Perplexity 기반 관련 해외 언론 기사 검색 + 도메인 필터 + 확장 폴백 |
| `bundler.py` | 세션 폴더에 meta/qa/기타 파일 저장 + ZIP 번들 생성 |
| `config.py` | 상수, 환경변수, API 키, 기능 플래그, 선호 도메인 리스트 |
| `templates/` | `base.html`, `login.html`, `browse.html` |
| `static/app.js` | UI 렌더링, 추출 옵션 체크박스, 결과 표시 |
| `static/style.css` | 스타일 |
| `transcripts/` | 추출 결과 (세션별 폴더 + `gdc_{id}.zip`) |
| `notebooklm_prompt.md` | NotebookLM에 붙여넣을 분석 프롬프트 (소스 구성 기준) |
| `.claude/rules/python.md` | Python 코딩 컨벤션 |
| `.claude/rules/scraping.md` | GDC Vault 스크래핑 규칙 |
| `.claude/rules/ai-pipeline.md` | Claude + Perplexity 파이프라인 규칙 |

## 핵심 파이프라인 (6단계)

1. **스크래핑** (`scraper.py`): 세션 상세 페이지 → blazestreaming iframe → `video_id`
2. **m3u8 URL 구성** (`scraper.py`): `script_VOD.js` 템플릿에서 cdn-a URL 추출
3. **VTT 세그먼트 병렬 다운로드** (`gdc_transcript.py`): MAX_CONCURRENT_VTT=20
4. **3종 텍스트 생성** (`gdc_transcript.py`): `subtitle.txt` (WebVTT 원본), `transcript_timed.txt`, `transcript.txt`
5. **Claude 후처리** (`ai_enhance.py`): 5개 병렬 태스크 (chapters/glossary/keypoints/qa/entities) + 1회 순차 정규화 패스
6. **Perplexity 기사 검색** (`web_context.py`) + **번들 생성** (`bundler.py`): `meta.md`, `qa.md`, `related_articles.md` 추가 후 ZIP

## 출력 파일 (번들 구성)

| 파일 | 생성 주체 | 설명 |
|---|---|---|
| `meta.md` | bundler | 세션 메타 헤더 + Overview. 캐노니컬 철자 기준 |
| `transcript.txt` | gdc_transcript | 메타 헤더 prepend된 정제본 (ASR 원본, 철자 교정 없음) |
| `transcript_timed.txt` | gdc_transcript | 타임스탬프 포함 버전 |
| `subtitle.txt` | gdc_transcript | WebVTT 원본 포맷 (확장자만 .txt — NotebookLM 호환) |
| `chapters.md` | ai_enhance | 5~10개 타임스탬프 챕터 |
| `glossary.md` | ai_enhance | 8~20개 고유명사 정의 |
| `keypoints.md` | ai_enhance | 3~5개 핵심 주장 + 증거 |
| `qa.md` | ai_enhance | 청중 Q&A 섹션 (있을 경우만 생성) |
| `related_articles.md` | web_context | 해외 게임 언론 관련 기사 표 |
| `thumbnail.jpg` | bundler | 세션 썸네일 (선택) |

## 주요 명령어

| 목적 | 명령 |
|---|---|
| 설치 | `pip install -r requirements.txt` |
| 개발 서버 | `uvicorn app:app --reload --port 8000` |
| CLI 자막 추출 (기본) | `python gdc_transcript.py <URL>` |
| CLI + AI 후처리 | `python gdc_transcript.py <URL> --enhance` |
| CLI 개별 옵션 | `--chapters --glossary --keypoints --qa --articles --thumbnail` |

## 환경 변수 (`.env.example` 참조)

- `ANTHROPIC_API_KEY` (AI 후처리 필수)
- `PERPLEXITY_API_KEY` (관련 기사 필수)
- `AI_MODEL` (기본 `claude-sonnet-4-6`)
- `AI_ENHANCE_ENABLED`, `WEB_CONTEXT_ENABLED` — 마스터 토글
- `AI_NORMALIZE_ENABLED` — 정규화 패스 (기본 true, 일관성 보장)
- `AI_QA_ENABLED` — Q&A 별도 추출 (기본 true)
- `WEB_CONTEXT_MAX_QUERIES` (기본 5), `WEB_CONTEXT_WIDEN_IF_FEWER` (기본 3)

## 전역 규칙

- **2칸 스페이스, 쌍따옴표, 한국어 주석/로그, Python 3.10+ 유니온 문법** — 상세는 `rules/python.md`
- **단일 사용자 인증**: `GDCAuth` 싱글톤이 `app.py` 전역에서 aiohttp 세션 관리
- **Rate limiting 준수**: `config.py`의 `REQUEST_DELAY`, `MAX_CONCURRENT_*` 상수 — 상세는 `rules/scraping.md`
- **LLM 호출 시**: SessionContext 주입, prompt caching, `return_exceptions=True` — 상세는 `rules/ai-pipeline.md`
- **디스크 저장은 ASR 원본 보존**: `transcript.txt` 에는 철자 교정 없이 저장 (Claude 출력에만 정규화 적용)

---

## 주요 작업 시나리오

### 1) AI 후처리 프롬프트 수정 / 새 출력 파일 추가
1. **프롬프트 정의** → `ai_enhance.py` 의 `*_SYSTEM` 상수 및 `_call_claude` 호출
2. **병렬 태스크 추가** → `enhance_transcript()` 내 `tasks` dict + `_unwrap_call`
3. **결과 필드** → `EnhancementResult` dataclass + `bundler.py`의 `BundleInputs`
4. **번들 기록** → `bundler.py`의 `build_bundle()` 에서 파일 쓰기 분기
5. **전달 연결** → `gdc_transcript.py`에서 `include_*` 파라미터 추가, `BundleInputs` 넘기기
6. **API/CLI 노출** → `app.py` `opt("include_*")` + `static/app.js` COMPONENTS 배열 + CLI `--` 플래그
7. **규칙 확인** → `rules/ai-pipeline.md`

### 2) 관련 기사 검색 품질 개선
1. **쿼리 구성** → `web_context.py` `_build_queries()` — `" OR "` 리터럴 금지, 엔티티 팬아웃
2. **도메인 화이트리스트** → `config.py` `PREFERRED_GAME_NEWS_DOMAINS` — 앞 10개가 Perplexity API 필터, 뒤는 포스트 필터만
3. **확장 폴백** → `find_related_articles()` — 결과 < `WEB_CONTEXT_WIDEN_IF_FEWER` 시 도메인 필터 해제 후 재시도
4. **엔티티 매치 검증** → `_mentions_entity()` 로 관련성 없는 기사 배제

### 3) 새 세션 카테고리/이벤트 지원
1. **카테고리 맵** → `config.py` `CATEGORIES` (코드 → 표시명)
2. **이벤트 리스트** → `config.py` `EVENTS` (dynamic 로드 실패 시 fallback)
3. **파싱 검증** → `scraper.py`의 세션 목록/상세 함수 — CSS 선택자 갱신 가능
4. **UI 드롭다운** → `templates/browse.html` + `static/app.js` 의 이벤트/카테고리 핸들러

### 4) UI에 새 추출 옵션 추가
1. **백엔드 수락** → `app.py` `/api/extract` 에 `opt("include_xxx")` 추가
2. **CLI 플래그** → `gdc_transcript.py` `argparse` + `--enhance` 단축 확장
3. **오케스트레이터 분기** → `extract_transcript()` 파라미터 + 조건부 실행
4. **UI 체크박스** → `static/app.js` `COMPONENTS` 배열 (기본 checked 여부 결정)
5. **힌트 텍스트** → `static/app.js` 의 hint 문구 갱신

### 5) GDC Vault 로그인/쿠키 로직 변경
1. **폼/엔드포인트** → `auth.py` `GDCAuth.login()` (POST `/api/login.php`)
2. **세션 검증** → `GET /account.php` 302 vs 200 체크 로직
3. **에러 저장** → `last_error` 필드로 UI에 노출
4. **상세** → `rules/scraping.md` 의 인증 섹션

### 6) NotebookLM 프롬프트 튜닝
1. **프롬프트 파일** → `notebooklm_prompt.md` — 번들에 포함된 소스 구성이 바뀌면 업데이트
2. **소스 목록 동기화** → bundler에서 추가/제거된 파일이 있으면 프롬프트의 "업로드된 소스" 섹션 반영

---

## 위험/주의사항

- **서버 프로세스 관리 (Windows)**: `uvicorn --reload` 는 reloader + worker 2개 프로세스. `taskkill /F /PID` 만으로는 자식 워커가 좀비로 남을 수 있음 → **반드시 `/T` 플래그로 트리 종료**.
- **ASR 오인식**: Claude가 고유명사를 오인할 수 있으므로 `SessionContext` 로 캐노니컬 철자 주입 필수. 단, `transcript.txt` 에는 원본 유지.
- **프롬프트 캐시 무효화**: `SYSTEM_BASE` 나 `_build_system_blocks` 수정 시 모든 세션의 캐시가 무효화됨 (일회성 비용).
- **Perplexity 도메인 필터 10개 제한**: API가 최대 10개만 허용하므로 `PREFERRED_GAME_NEWS_DOMAINS[:10]` 슬라이싱됨. 중요도 순서로 유지.
- **GDC Vault 유료 콘텐츠**: `vault_free=False` 세션은 로그인된 세션으로만 접근 가능.
- **VTT → .txt 저장**: NotebookLM이 `.vtt` 확장자 거절하므로 `subtitle.txt` 로 저장 (내용은 WebVTT 포맷 유지, 첫 줄 `WEBVTT`).
- **`__pycache__` 문제**: 파이썬 모듈이 재로드 안 될 때 `__pycache__` 폴더 삭제 후 서버 재시작.

## 디버깅 체크리스트

- **Claude usage 가 비정상적으로 낮다** (예: `input=109`) → 병렬 태스크가 cancel됐거나 옛 프로세스가 처리 중. 로그의 `Claude 병렬 호출 결과: 성공=N/M` 확인.
- **고유명사 교정이 안 됨** → `meta.md` 의 speakers/title 이 올바르게 스크랩됐는지 확인 → SessionContext 가 enhance_transcript 로 전달됐는지 확인 → `rules/ai-pipeline.md` 의 정규화 패스 조건 검토.
- **related_articles 결과 없음** → `WEB_CONTEXT_WIDEN_IF_FEWER` 이상이 나왔는지 로그 확인 → `_mentions_entity` 가 너무 엄격한지 (엔티티 리스트가 짧은지) 검토.
- **Uvicorn 재로드 실패** → 자식 워커 좀비 가능성. `tasklist //FI "IMAGENAME eq python.exe"` + `wmic process where "ProcessId=X" get CommandLine` 로 추적.
