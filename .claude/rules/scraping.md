# 웹 스크래핑 규칙

## GDC Vault 구조
- 세션 목록: `GET /browse/{event_slug}?categories={code}&media=v`
- 세션 상세: `GET /play/{session_id}`
- 로그인: `POST /api/login.php` (AJAX, form-urlencoded)
- 인증 확인: `GET /account.php` (302면 미인증)

## HTML 파싱 순서
1. BeautifulSoup CSS 선택자 우선
2. 정규식은 JavaScript/동적 콘텐츠에서만 사용
3. `dataLayer` JSON에서 구조화된 메타데이터 추출

## m3u8 자막 추출 순서
1. play 페이지 HTML에서 직접 m3u8 URL 검색
2. blazestreaming iframe → video_id 파라미터 추출
3. iframe HTML에서 m3u8 URL 직접 검색
4. script_VOD.js 템플릿에서 URL 구성

## Rate Limiting
- `config.py`의 설정값 준수: REQUEST_DELAY=1.5초, MAX_CONCURRENT_SCRAPE=3
- VTT 세그먼트 다운로드: MAX_CONCURRENT_VTT=20
- 항상 `RateLimiter` 또는 `asyncio.Semaphore` 사용
