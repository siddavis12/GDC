# GDC Vault Transcript Browser

GDC(Game Developers Conference) Vault에서 세션을 검색하고, 영상 자막을 추출한 뒤 Claude + Perplexity 로 후처리하여 **NotebookLM 업로드용 소스 번들(ZIP)** 을 생성하는 FastAPI 웹앱 + CLI 도구입니다.

추출 결과는 챕터, 용어집, 핵심 포인트, Q&A, 관련 해외 언론 기사 링크까지 포함된 종합 자료로 구성되어 팀 내부 멘토링/분석 문서 생성에 바로 투입할 수 있습니다.

## 주요 기능

- **세션 브라우징** — 연도별/카테고리별 GDC 세션 목록 검색
- **세션 상세 정보** — 발표자, 소속, 개요, 태그 등 메타데이터 확인
- **자막 추출** — 영상의 m3u8 스트림에서 VTT 자막 세그먼트를 자동 추출
- **AI 후처리 (Claude)** — 챕터 / 용어집 / 핵심 포인트 / Q&A 자동 생성 + 파일 간 고유명사 일관성 정규화
- **관련 기사 수집 (Perplexity)** — 해외 메이저 게임 언론사 기사를 자동 검색 + 링크 표 생성
- **NotebookLM 번들 ZIP** — 모든 파생 파일을 한 폴더로 묶어 NotebookLM 에 일괄 업로드 가능
- **CLI 모드** — 웹 UI 없이 커맨드라인에서 직접 추출 + 후처리

## 기술 스택

| 구분 | 기술 |
|---|---|
| 언어 | Python 3.13 |
| 웹 프레임워크 | FastAPI + Uvicorn |
| 템플릿 | Jinja2 |
| HTTP 클라이언트 | aiohttp (비동기) |
| HTML 파싱 | BeautifulSoup4 |
| 프론트엔드 | HTML / CSS / Vanilla JS (별도 빌드 없음) |
| LLM | Anthropic Claude (`claude-sonnet-4-6` 기본) |
| 웹 리서치 | Perplexity (`sonar-pro`) |

## 설치

```bash
pip install -r requirements.txt
cp .env.example .env
# .env 에 ANTHROPIC_API_KEY, PERPLEXITY_API_KEY 설정
```

## 사용법

### 웹 UI
```bash
uvicorn app:app --reload --port 8000
```
브라우저에서 `http://localhost:8000` 접속 후 GDC Vault 계정으로 로그인.
세션 상세 페이지에서 추출 옵션 체크박스(챕터/용어집/핵심포인트/Q&A/기사/썸네일)를 선택하고 "추출 시작".

### CLI
```bash
# 기본 추출 (자막 3종만)
python gdc_transcript.py https://gdcvault.com/play/1034837

# 전체 후처리 포함 (Claude + Perplexity + 썸네일 + ZIP 번들)
python gdc_transcript.py https://gdcvault.com/play/1034837 --enhance

# 개별 옵션
python gdc_transcript.py <URL> --chapters --glossary --keypoints --qa --articles --thumbnail

# 언어 및 출력 디렉토리 지정
python gdc_transcript.py <URL> --lang jpn -o ./transcripts
```

지원 자막 언어: `eng` (영어), `spa` (스페인어), `zho` (중국어), `jpn` (일본어)

## 번들 구성

`--enhance` 옵션 또는 웹 UI로 전체 후처리를 실행하면 `transcripts/gdc_{id}/` 폴더와 동일 이름의 `.zip` 파일이 생성됩니다.

| 파일 | 설명 |
|---|---|
| `meta.md` | 세션 메타데이터 (제목, 발표자, 회사, 트랙, 태그, 공식 Overview) |
| `transcript.txt` | 메타 헤더가 포함된 전체 발화 정리본 |
| `transcript_timed.txt` | 타임스탬프가 포함된 발화본 |
| `subtitle.txt` | WebVTT 포맷 원본 (확장자만 `.txt` — NotebookLM 호환) |
| `chapters.md` | 5~10개 타임스탬프 챕터 (Claude) |
| `glossary.md` | 고유명사 정의집 — 게임·스튜디오·인물·전문 용어 (Claude) |
| `keypoints.md` | 3~5개 핵심 주장 + 증거 (Claude) |
| `qa.md` | 청중 Q&A 섹션 (있을 경우, Claude) |
| `related_articles.md` | 해외 게임 언론 관련 기사 표 (Perplexity) |
| `thumbnail.jpg` | 세션 썸네일 (선택) |

## 디렉토리 구조

```
├── app.py                 # FastAPI 진입점, 라우트 정의
├── auth.py                # GDC Vault 인증 (PHPSESSID 쿠키 기반)
├── scraper.py             # 세션 목록/상세 페이지 스크래핑
├── gdc_transcript.py      # 자막 추출 오케스트레이터 (CLI + 라이브러리)
├── ai_enhance.py          # Claude 후처리 (병렬 + 정규화 패스)
├── web_context.py         # Perplexity 기반 관련 기사 검색
├── bundler.py             # 세션 폴더 + ZIP 번들 생성
├── config.py              # 상수, 환경변수, 기능 플래그, 도메인 화이트리스트
├── requirements.txt       # Python 의존성
├── .env.example           # 환경변수 템플릿
├── notebooklm_prompt.md   # NotebookLM 업로드용 분석 프롬프트
├── templates/             # Jinja2 HTML (base, login, browse)
├── static/                # 프론트엔드 (app.js, style.css)
├── transcripts/           # 추출 결과 (세션별 폴더 + ZIP)
└── .claude/               # Claude Code 규칙 (CLAUDE.md, rules/*.md)
```

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/` | 루트 (로그인/브라우즈 리다이렉트) |
| POST | `/login` | GDC Vault 로그인 처리 |
| GET | `/browse` | 세션 목록 (`?event=gdc-25&category=Pg`) |
| GET | `/session/{id}` | 세션 상세 정보 (JSON/HTML) |
| POST | `/api/extract/{id}` | 자막 추출 + 선택적 후처리 (JSON body로 옵션 지정) |
| GET | `/api/download_bundle/{id}` | ZIP 번들 다운로드 |
| GET | `/logout` | 로그아웃 |

## NotebookLM 활용

1. 생성된 `gdc_{id}.zip` 을 풀어 내부 파일들을 NotebookLM 에 일괄 업로드
2. `notebooklm_prompt.md` 의 내용을 NotebookLM 에 붙여넣기
3. 슬라이드 덱 + 실무 인사이트 요약 + 심화 학습 레퍼런스가 자동 생성됨

`notebooklm_prompt.md` 는 번들 구성(`meta.md`, `chapters.md`, `keypoints.md`, `qa.md`, `related_articles.md` 등)을 전제로 작성되어 있어, 각 파일의 성격에 맞게 교차 인용하여 고품질 분석 결과를 산출합니다.

## 참고사항

- GDC Vault **유료 구독 계정**이 필요합니다 (무료 세션은 로그인 없이 일부 접근 가능).
- Rate limiting 적용: 요청 간 1.5초, 동시 스크래핑 3개, VTT 세그먼트 20개.
- 자막이 없는 영상은 추출할 수 없습니다.
- Claude/Perplexity 키가 없으면 해당 단계만 자동 스킵됩니다 (기본 자막 추출은 동작).
- Claude 호출은 ephemeral prompt caching 을 사용해 다수 병렬 태스크의 토큰 비용을 절감합니다.
