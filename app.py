"""
GDC Vault 트랜스크립트 브라우저 — FastAPI 진입점
"""

import logging
import re
from pathlib import Path

from fastapi import Body, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from deep_translator import GoogleTranslator

from auth import GDCAuth
from config import CATEGORIES, EVENTS as EVENTS_FALLBACK, TRANSCRIPT_DIR
from scraper import GDCScraper

# 로깅 설정
logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)

app = FastAPI(title="GDC Vault 브라우저")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def _compute_asset_version() -> str:
  """static/app.js와 style.css mtime 중 최신값으로 cache-busting 토큰 생성"""
  try:
    js = Path("static/app.js").stat().st_mtime
    css = Path("static/style.css").stat().st_mtime
    return str(int(max(js, css)))
  except OSError:
    return "0"


# 모든 Jinja 템플릿에서 {{ asset_version }}으로 참조 가능
# uvicorn --reload가 파일 변경 시 프로세스를 재시작하므로 자동 갱신됨
templates.env.globals["asset_version"] = _compute_asset_version()

# 전역 인증 객체 (단일 사용자용)
gdc_auth = GDCAuth()

# 트랜스크립트 디렉토리 생성
Path(TRANSCRIPT_DIR).mkdir(exist_ok=True)

# 이벤트 목록 캐시 (최초 로그인 이후 동적 로드)
_events_cache: list[dict] | None = None


async def get_events() -> list[dict]:
  """GDC Vault에서 이벤트 목록 가져오기 (캐싱, 실패 시 fallback)"""
  global _events_cache
  if _events_cache is not None:
    return _events_cache
  try:
    aio_session = await gdc_auth.ensure_session()
    scraper = GDCScraper(aio_session)
    events = await scraper.fetch_events()
    if events:
      _events_cache = events
      return _events_cache
  except Exception as e:
    log.warning("이벤트 목록 동적 로드 실패: %s", e)
  _events_cache = EVENTS_FALLBACK
  return _events_cache


# 템플릿 헬퍼 — 플래시 메시지 (단순 구현)
_flash_messages: list[str] = []


def flash(msg: str):
  _flash_messages.append(msg)


def get_flashed_messages() -> list[str]:
  msgs = _flash_messages.copy()
  _flash_messages.clear()
  return msgs


# --- 라우트 ---

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
  """루트: 로그인 상태면 browse, 아니면 login"""
  if gdc_auth.is_logged_in:
    return RedirectResponse(url="/browse", status_code=302)
  return templates.TemplateResponse("login.html", {
    "request": request,
    "logged_in": False,
    "error": None,
    "get_flashed_messages": get_flashed_messages,
  })


@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
  """로그인 처리"""
  success = await gdc_auth.login(email, password)
  if success:
    log.info("로그인 성공: %s", email)
    global _events_cache
    _events_cache = None  # 로그인 후 이벤트 목록 재로드
    return RedirectResponse(url="/browse", status_code=302)
  else:
    log.warning("로그인 실패: %s", email)
    server_msg = gdc_auth.last_error
    return templates.TemplateResponse("login.html", {
      "request": request,
      "logged_in": False,
      "error": f"로그인 실패 — GDC Vault 응답: {server_msg}",
      "get_flashed_messages": get_flashed_messages,
    })


@app.get("/browse", response_class=HTMLResponse)
async def browse(request: Request, event: str = "gdc-25", category: str = ""):
  """세션 목록 페이지"""
  if not gdc_auth.is_logged_in:
    return RedirectResponse(url="/", status_code=302)

  sessions = []
  error = None
  try:
    aio_session = await gdc_auth.ensure_session()
    scraper = GDCScraper(aio_session)
    sessions = await scraper.browse_sessions(event_slug=event, category=category)
    log.info("세션 %d개 로드 (event=%s, category=%s)", len(sessions), event, category)
  except Exception as e:
    log.error("세션 목록 로드 실패: %s", e)
    error = str(e)

  events = await get_events()
  return templates.TemplateResponse("browse.html", {
    "request": request,
    "logged_in": True,
    "sessions": sessions,
    "events": events,
    "categories": CATEGORIES,
    "current_event": event,
    "current_category": category,
    "error": error,
    "get_flashed_messages": get_flashed_messages,
  })


@app.get("/session/{session_id:path}")
async def session_detail(request: Request, session_id: str):
  """세션 상세 — JSON(fetch) 또는 HTML 응답"""
  if not gdc_auth.is_logged_in:
    return RedirectResponse(url="/", status_code=302)

  try:
    aio_session = await gdc_auth.ensure_session()
    scraper = GDCScraper(aio_session)
    play_url = f"https://gdcvault.com/play/{session_id}"
    detail = await scraper.get_session_detail(play_url)
  except Exception as e:
    log.error("세션 상세 로드 실패: %s", e)
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
      return JSONResponse({"error": str(e)}, status_code=500)
    flash(f"세션 로드 실패: {e}")
    return RedirectResponse(url="/browse", status_code=302)

  # 한국어 번역 (제목 + Overview)
  title_ko = ""
  overview_ko = ""
  translator = GoogleTranslator(source="en", target="ko")
  if detail.title:
    try:
      title_ko = translator.translate(detail.title)
    except Exception as e:
      log.warning("제목 번역 실패: %s", e)
  if detail.overview:
    try:
      overview_ko = translator.translate(detail.overview)
    except Exception as e:
      log.warning("Overview 번역 실패: %s", e)

  # JSON 응답 (fetch 요청)
  accept = request.headers.get("accept", "")
  if "application/json" in accept:
    return JSONResponse({
      "title": detail.title,
      "title_ko": title_ko,
      "play_url": detail.play_url,
      "speakers": detail.speakers,
      "company": detail.company,
      "category": detail.category,
      "overview": detail.overview,
      "overview_ko": overview_ko,
      "tags": detail.tags,
      "thumbnail": detail.thumbnail,
      "m3u8_url": detail.m3u8_url,
      "year": detail.year,
      "vault_free": detail.vault_free,
    })

  # HTML 폴백 — browse로 리다이렉트
  return RedirectResponse(url="/browse", status_code=302)


@app.post("/api/extract/{session_id:path}")
async def extract_transcript(
  session_id: str,
  options: dict = Body(default_factory=dict),
):
  """트랜스크립트 추출 API.

  options JSON body로 구성 요소 선택:
    include_chapters, include_glossary, include_keypoints, include_qa,
    include_design_brief, include_articles, include_thumbnail (모두 기본 True)
  """
  if not gdc_auth.is_logged_in:
    return JSONResponse({"error": "로그인 필요"}, status_code=401)

  def opt(key: str, default: bool = True) -> bool:
    v = options.get(key, default)
    return bool(v) if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes")

  include_chapters = opt("include_chapters")
  include_glossary = opt("include_glossary")
  include_keypoints = opt("include_keypoints")
  include_qa = opt("include_qa")
  include_design_brief = opt("include_design_brief")
  include_articles = opt("include_articles")
  include_thumbnail = opt("include_thumbnail")

  try:
    aio_session = await gdc_auth.ensure_session()
    scraper = GDCScraper(aio_session)
    play_url = f"https://gdcvault.com/play/{session_id}"

    # 세션 상세에서 m3u8 URL 가져오기
    detail = await scraper.get_session_detail(play_url)
    if not detail.m3u8_url:
      return JSONResponse(
        {"error": "m3u8 URL을 찾을 수 없습니다. 자막이 없거나 접근 권한이 필요합니다."},
        status_code=404,
      )

    # gdc_transcript 모듈로 추출
    # session_id에 슬래시가 포함될 수 있으므로 파일명용으로 변환
    safe_id = session_id.split("/")[0]  # 숫자 ID 부분만 사용
    from gdc_transcript import extract_transcript as do_extract
    result_path = await do_extract(
      url=detail.m3u8_url,
      lang="eng",
      output_dir=TRANSCRIPT_DIR,
      session=aio_session,
      video_id=f"gdc_{safe_id}",
      detail=detail,
      include_chapters=include_chapters,
      include_glossary=include_glossary,
      include_keypoints=include_keypoints,
      include_qa=include_qa,
      include_design_brief=include_design_brief,
      include_articles=include_articles,
      include_thumbnail=include_thumbnail,
    )

    # 생성된 세션 폴더와 ZIP 경로
    session_dir = Path(TRANSCRIPT_DIR) / f"gdc_{safe_id}"
    files = (
      sorted(f.name for f in session_dir.iterdir() if f.is_file())
      if session_dir.exists() else []
    )

    zip_name = f"gdc_{safe_id}.zip"
    bundle_available = (Path(TRANSCRIPT_DIR) / zip_name).exists()

    return JSONResponse({
      "ok": True,
      "session_dir": session_dir.name,
      "files": files,
      "path": str(result_path),
      "bundle": zip_name if bundle_available else None,
      "bundle_url": f"/api/download_bundle/{safe_id}" if bundle_available else None,
    })

  except Exception as e:
    log.error("추출 실패 (session %s): %s", session_id, e)
    return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/download_bundle/{session_id:path}")
async def download_bundle(session_id: str):
  """세션 번들 ZIP 다운로드"""
  safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", session_id.split("/")[0])
  zip_path = Path(TRANSCRIPT_DIR) / f"gdc_{safe_id}.zip"
  if not zip_path.exists():
    return JSONResponse(
      {"error": "번들이 아직 생성되지 않았습니다. 먼저 /api/extract를 호출하세요."},
      status_code=404,
    )
  return FileResponse(
    zip_path,
    filename=zip_path.name,
    media_type="application/zip",
  )


@app.get("/api/download/{session_id}/{filename}")
async def download_file(session_id: str, filename: str):
  """세션 폴더 내 개별 파일 다운로드"""
  safe_session = re.sub(r"[^a-zA-Z0-9_-]", "", session_id.split("/")[0])
  safe_name = Path(filename).name  # 경로 탐색 방지
  file_path = Path(TRANSCRIPT_DIR) / f"gdc_{safe_session}" / safe_name
  if not file_path.exists():
    return JSONResponse({"error": "파일을 찾을 수 없습니다"}, status_code=404)
  return FileResponse(file_path, filename=safe_name)


@app.get("/debug/play/{session_id:path}")
async def debug_play_page(session_id: str):
  """디버그: play 페이지 원본 HTML 확인"""
  if not gdc_auth.is_logged_in:
    return JSONResponse({"error": "로그인 필요"}, status_code=401)

  try:
    aio_session = await gdc_auth.ensure_session()
    play_url = f"https://gdcvault.com/play/{session_id}"
    async with aio_session.get(play_url) as resp:
      html = await resp.text()

    # 파일로 저장
    debug_path = Path(f"debug_play_{session_id}.html")
    debug_path.write_text(html, encoding="utf-8")
    log.info("디버그 HTML 저장: %s (%d bytes)", debug_path, len(html))

    # iframe 목록과 m3u8 관련 라인 추출
    import re as re_mod
    iframes = re_mod.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re_mod.IGNORECASE)
    m3u8_refs = re_mod.findall(r'[^\s"\'<>]*\.m3u8[^\s"\'<>]*', html, re_mod.IGNORECASE)

    return JSONResponse({
      "session_id": session_id,
      "html_length": len(html),
      "saved_to": str(debug_path),
      "iframes_found": iframes,
      "m3u8_refs_found": m3u8_refs,
      "title_snippet": html[:500],
    })
  except Exception as e:
    log.error("디버그 실패: %s", e)
    return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/logout")
async def logout():
  """로그아웃"""
  await gdc_auth.logout()
  return RedirectResponse(url="/", status_code=302)
