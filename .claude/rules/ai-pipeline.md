# AI 파이프라인 규칙 (Claude + Perplexity)

`ai_enhance.py` / `web_context.py` / `bundler.py` 수정 시 반드시 준수.

## Claude 호출 구조

### 시스템 블록 3단
```python
system=[
  {"type": "text", "text": SYSTEM_BASE},
  {"type": "text", "text": ctx.as_prompt_block(), "cache_control": {"type": "ephemeral"}},
  {"type": "text", "text": PER_TASK_SYSTEM, "cache_control": {"type": "ephemeral"}},
]
```
- **`SYSTEM_BASE`** — 전역 지침 (ASR 교정 원칙 포함). 캐시 마커 없음 — 모든 호출에서 공유되지만 이게 바뀌면 모든 세션의 캐시가 invalidate.
- **`<session_context>`** — 세션별 메타데이터. 캐시 마킹하여 동일 세션의 병렬 호출이 재사용.
- **per-task system** — 각 태스크(chapters/glossary/keypoints/qa/entities)별 지침. 캐시 마킹.

### 사용자 메시지 구조
```python
messages=[{
  "role": "user",
  "content": _cached_transcript_block(transcript) + [
    {"type": "text", "text": instruction}
  ],
}]
```
- transcript 는 **반드시 `cache_control: ephemeral`** 로 마킹 — 이게 최대 토큰 소비원.

## SessionContext 원칙

- 모든 Claude 호출에 `context: SessionContext | None = None` 전달.
- None 으로 호출 가능하되 **ASR 교정 품질이 떨어짐** — CLI 테스트 외에는 지양.
- 필드: `title, speakers, company, category, year, tags, overview`.
- `as_prompt_block()` 이 XML-ish `<session_context>...</session_context>` 생성 — 포맷 변경 시 `SYSTEM_BASE` 의 참조 문구도 동기화.

## 병렬 실행 + 정규화 패스

### 병렬 gather 규칙
```python
gather_results = await asyncio.gather(*tasks.values(), return_exceptions=True)
```
- **`return_exceptions=True` 고정**. False 로 두면 한 태스크 실패 시 나머지가 cancel 되어 output이 급감한다 (이전 버그).
- 결과는 `_unwrap_call()` 로 개별 처리 — 예외는 빈 결과 + 경고 로그.
- 성공 카운트를 반드시 `Claude 병렬 호출 결과: 성공=N/M` 로 로그.

### 정규화 패스 조건
```python
do_normalize = AI_NORMALIZE_ENABLED if normalize is None else normalize
have_enough = any([chapters_text, glossary_text, keypoints_text, qa_text])
if do_normalize and have_enough:
  canonical = _extract_canonical_names(glossary_text, context, entity_list)
  # _normalize_outputs() 호출
```
- 병렬 5개 완료 **후** 순차 1회. 목적: 파일 간 고유명사 철자 통일.
- Canonical names 소스 우선순위: `context.speakers/company/title` → glossary `**Term**` → entities.
- 정규화 응답 파싱 실패 시 **원본 유지** (`_parse_normalize_response` 가 None 반환 시).

## 태스크 추가 체크리스트

새 후처리 출력 파일(`xyz.md`) 추가 시:
1. `ai_enhance.py`
   - `XYZ_SYSTEM` 상수 정의
   - `EnhancementResult.xyz_md` 필드 추가
   - `include_xyz: bool = True` 파라미터 추가
   - `tasks["xyz"] = asyncio.create_task(...)` 등록
   - `_unwrap_call("xyz")` 로 언래핑
   - 정규화 대상이면 `_normalize_outputs` delim 리스트에 추가
2. `bundler.py`
   - `BundleInputs.xyz_md: str = ""` 필드
   - `build_bundle()` 에서 빈 문자열/"No XYZ" 센티넬 체크 후 write
3. `gdc_transcript.py`
   - `extract_transcript(..., include_xyz: bool = False)` 추가
   - `enhancement.xyz_md` 를 `BundleInputs` 에 전달
   - CLI `--xyz` 플래그 + `--enhance` 단축에 포함
4. `app.py`
   - `/api/extract` 의 `opt("include_xyz")` 추가
   - `do_extract` 호출에 전달
5. `static/app.js`
   - `COMPONENTS` 배열에 `{key: "include_xyz", label: "...", default: true}` 추가
6. **`notebooklm_prompt.md`** 의 "업로드된 소스" 섹션에 파일 설명 추가
7. **`.claude/CLAUDE.md`** 의 출력 파일 표 갱신

## Perplexity 쿼리 규칙

- **`" OR "` 리터럴 금지** — Perplexity sonar-pro 는 boolean 이 아닌 단순 텍스트로 처리함. 공백 구분 키워드 사용.
- **엔티티 팬아웃**: 상위 3개 엔티티 각각을 독립 쿼리로. 일괄 덤프 금지.
- **도메인 필터 우선순위**: `PREFERRED_GAME_NEWS_DOMAINS` 앞 10개만 API 필터로 전달 (Perplexity 제한). 뒤 항목은 `_is_preferred` 포스트 필터에서만 통과.
- **확장 폴백**: `len(merged) < WEB_CONTEXT_WIDEN_IF_FEWER` 시 `use_domain_filter=False` + `_is_preferred_loose` 로 재시도.
- **엔티티 매치 dedupe**: `_mentions_entity()` 로 title/summary/url 에 엔티티 word-boundary 매치 확인.

## 로깅 컨벤션

- **로그 모듈**: `log = logging.getLogger(__name__)` — `print()` 는 CLI 진행 바에만.
- **한국어 메시지**: 진단 로그는 한국어로. 코드 식별자는 원문 유지.
- **정규화 단계 3줄 보장**:
  1. `정규화 판정: AI_NORMALIZE_ENABLED=... do_normalize=... have_enough=... (chapters=N ...)`
  2. `정규화 진입: canonical names (N) = [...]`
  3. `정규화 패스 완료 (canonical=N names, usage input=... output=...)`
  이 3줄이 없으면 normalize가 실행되지 않은 것 — 조건 검토.

## 프롬프트 캐시 취급

- `SYSTEM_BASE` 변경 = 모든 프로젝트 사용자의 최초 호출 1회 cache_write 비용. 자주 바꾸지 말 것.
- ephemeral 캐시 TTL 5분 — 병렬 호출 안에서만 공유. 다른 세션 간 공유 안 됨.
- 캐시 히트 여부는 `cache_read_input_tokens` 로 확인. 병렬 호출 첫 번째만 write, 나머지는 read.

## 모델 변경

- `AI_MODEL` (기본 `claude-sonnet-4-6`)만 조정. Opus/Haiku 는 `.env` 에서 오버라이드.
- **Opus 는 정규화 패스 품질이 눈에 띄게 향상** — 비용 대비 효과가 중요한 용도면 검토.
- **Haiku 는 chapters/entities 같은 단순 태스크에 적합** — 전체 교체보다는 태스크별 분리 모델 도입 검토 가능 (향후 작업).
