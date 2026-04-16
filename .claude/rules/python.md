# Python 코딩 규칙

## 스타일
- 들여쓰기: 2칸 스페이스
- 문자열: 쌍따옴표 (`"hello"`)
- 타입 힌트: 내장 유니온 문법 사용 (`str | None`, `list[str]`)
- f-string 선호 (로그 메시지 제외 — 로그는 `%s` 포맷 사용)
- import 순서: stdlib → 서드파티 → 로컬 모듈

## 비동기 패턴
- aiohttp.ClientSession은 외부에서 주입받거나 직접 생성 후 반드시 정리
- `asyncio.Semaphore`로 동시 요청 수 제한
- rate limiting은 `RateLimiter` 클래스 패턴 사용

## 에러 처리
- FastAPI 라우트: try/except로 잡아서 적절한 HTTP 응답 반환
- 스크래핑: 실패 시 빈 값 반환 또는 예외를 상위로 전파
- 로그인 실패: `last_error`에 서버 메시지 저장

## 데이터 모델
- dataclass 사용 (`@dataclass`, `field(default_factory=...)`)
- Pydantic 미사용 — 단순 데이터 컨테이너는 dataclass로 충분
