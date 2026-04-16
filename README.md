# GDC Vault Transcript Browser

GDC(Game Developers Conference) Vault에서 세션을 검색하고, 영상 자막(VTT)을 추출하여 텍스트로 변환하는 웹 애플리케이션입니다.

추출된 트랜스크립트는 NotebookLM 등에 업로드하여 GDC 발표 내용을 요약·분석하는 데 활용할 수 있습니다.

## 주요 기능

- **세션 브라우징** — 연도별/카테고리별 GDC 세션 목록 검색
- **세션 상세 정보** — 발표자, 소속, 개요, 태그 등 메타데이터 확인
- **자막 추출** — 영상의 m3u8 스트림에서 VTT 자막 세그먼트를 자동 추출
- **3종 출력** — `.vtt` (원본), `_timestamped.txt` (타임스탬프 포함), `.txt` (정리본)
- **CLI 모드** — 웹 UI 없이 커맨드라인에서 직접 자막 추출 가능

## 기술 스택

| 구분 | 기술 |
|------|------|
| 언어 | Python 3.13 |
| 웹 프레임워크 | FastAPI + Uvicorn |
| 템플릿 | Jinja2 |
| HTTP 클라이언트 | aiohttp (비동기) |
| HTML 파싱 | BeautifulSoup4 |
| 프론트엔드 | HTML / CSS / Vanilla JS |

## 설치

```bash
pip install -r requirements.txt
```

## 사용법

### 웹 UI

```bash
uvicorn app:app --reload --port 8000
```

브라우저에서 `http://localhost:8000` 접속 후 GDC Vault 계정으로 로그인합니다.

### CLI (커맨드라인)

```bash
# GDC Vault URL로 추출
python gdc_transcript.py https://gdcvault.com/play/1034837

# m3u8 URL 직접 지정
python gdc_transcript.py https://cdn-a.blazestreaming.com/.../index.m3u8

# 언어 및 출력 디렉토리 지정
python gdc_transcript.py https://gdcvault.com/play/1034837 --lang jpn -o ./transcripts
```

지원 자막 언어: `eng` (영어), `spa` (스페인어), `zho` (중국어), `jpn` (일본어)

## 디렉토리 구조

```
├── app.py                 # FastAPI 진입점, 라우트 정의
├── auth.py                # GDC Vault 인증 (PHPSESSID 쿠키 기반)
├── scraper.py             # 세션 목록/상세 페이지 스크래핑
├── gdc_transcript.py      # m3u8 자막 추출기 (CLI + 라이브러리)
├── config.py              # URL, rate limit, 이벤트/카테고리 설정
├── requirements.txt       # Python 의존성
├── notebooklm_prompt.md   # NotebookLM용 프롬프트 템플릿
├── templates/             # Jinja2 HTML 템플릿
│   ├── base.html
│   ├── login.html
│   └── browse.html
├── static/                # 프론트엔드 정적 파일
│   ├── app.js
│   └── style.css
└── transcripts/           # 추출된 자막 파일 출력
```

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/` | 루트 (로그인 페이지 또는 브라우즈로 리다이렉트) |
| POST | `/login` | GDC Vault 로그인 처리 |
| GET | `/browse` | 세션 목록 (`?event=gdc-25&category=Pg`) |
| GET | `/session/{id}` | 세션 상세 정보 (JSON/HTML) |
| POST | `/api/extract/{id}` | 자막 추출 실행 |
| GET | `/api/download/{filename}` | 추출된 파일 다운로드 |
| GET | `/logout` | 로그아웃 |

## NotebookLM 활용

추출된 `.txt` 파일을 [NotebookLM](https://notebooklm.google.com/)에 업로드한 뒤, `notebooklm_prompt.md`에 포함된 프롬프트를 활용하면 다음 작업이 가능합니다:

1. **핵심 구조 파악** — 발표자, 주제, 핵심 문제/해결법 정리
2. **슬라이드 생성** — 15장 이내의 요약 슬라이드 구성
3. **인사이트 추출** — 설계/기술/프로덕션/비즈니스별 실무 인사이트
4. **한 장짜리 요약** — 팀 공유용 치트시트

## 참고사항

- GDC Vault **유료 구독 계정**이 필요합니다 (무료 세션은 로그인 없이도 일부 접근 가능)
- Rate limiting이 적용되어 있습니다 (요청 간 1.5초 딜레이, 동시 스크래핑 3개)
- 자막이 없는 영상은 추출할 수 없습니다
