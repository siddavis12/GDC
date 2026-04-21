# 웹 스크래핑 규칙

## GDC Vault 구조
- 세션 목록: `GET /browse/{event_slug}?categories={code}&media=v`
- 세션 상세: `GET /play/{session_id}`
- 로그인: `POST /api/login.php` (AJAX, form-urlencoded)
- 인증 확인: `GET /account.php` (302면 미인증)

## 인증 세션 쿠키
로그인 성공 후 aiohttp 세션이 보유해야 하는 쿠키:
`AWSALB`, `AWSALBCORS`, `PHPSESSID`, `__cf_bm`, `user_hash`
— `GDCAuth.login()` 의 로그(`필수 쿠키: [...]`)로 확인 가능.

## HTML 파싱 순서
1. BeautifulSoup CSS 선택자 우선
2. 정규식은 JavaScript/동적 콘텐츠에서만 사용
3. `dataLayer` JSON 에서 구조화된 메타데이터 추출
4. 세션 상세 페이지의 `테이블 메타데이터` (session name, overview, speaker(s), company name(s), track / format) 도 보조 소스

## m3u8 자막 추출 순서
1. play 페이지 HTML 에서 직접 m3u8 URL 검색
2. blazestreaming iframe → `video_id` 파라미터 추출
3. iframe HTML 에서 m3u8 URL 직접 검색
4. `script_VOD.js` 템플릿에서 URL 구성 (`https://cdn-a.blazestreaming.com/out/v1/{video_id}/{token}/...`)

## Rate Limiting
- `config.py` 의 설정값 준수: `REQUEST_DELAY=1.5초`, `MAX_CONCURRENT_SCRAPE=3`
- VTT 세그먼트 다운로드: `MAX_CONCURRENT_VTT=20`
- 항상 `RateLimiter` 또는 `asyncio.Semaphore` 사용

## SessionDetail 파싱 성공 기준
`scraper.get_session_detail()` 로그의 `채워진 필드=[...]` 에 다음이 있어야 AI 후처리 품질이 보장됨:
- 필수: `title`, `speakers` — 없으면 SessionContext 앵커링 효과 급감
- 강력 권장: `company`, `category`, `overview`, `tags`
- m3u8 추출 필수: `m3u8_url` (또는 `video_id`)

## 디버깅
- `debug_*.html` 파일은 파싱 실패 시 응답 본문을 저장하는 임시 파일 — 커밋 금지
