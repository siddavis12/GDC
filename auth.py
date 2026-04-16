"""
GDC Vault 인증 관리

GDC Vault는 AJAX 기반 로그인을 사용:
- POST /api/login.php (form-urlencoded)
- 성공: HTTP 200 + 빈 본문 (또는 리다이렉트 URL)
- 실패: HTTP 500 + 에러 메시지 텍스트
- 인증 유지: PHPSESSID 쿠키
"""

import logging
import aiohttp
from config import GDC_BASE, GDC_LOGIN_URL, USER_AGENT

log = logging.getLogger(__name__)


class GDCAuth:
  """GDC Vault 로그인 및 세션 관리"""

  def __init__(self):
    self._session: aiohttp.ClientSession | None = None
    self._logged_in = False
    self.last_error: str = ""

  async def login(self, email: str, password: str) -> bool:
    """GDC Vault에 로그인, 성공 여부 반환"""
    await self._close_session()
    self.last_error = ""

    jar = aiohttp.CookieJar()
    # User-Agent만 기본 헤더로 — AJAX 헤더는 POST에서만 사용
    self._session = aiohttp.ClientSession(
      cookie_jar=jar,
      headers={"User-Agent": USER_AGENT},
    )

    try:
      # 1) 로그인 페이지 방문 — PHPSESSID + Cloudflare 쿠키 확보
      async with self._session.get(f"{GDC_BASE}/login") as resp:
        log.info("로그인 페이지 접근: HTTP %s", resp.status)

      log.info("로그인 시도: email=%s", email)

      # 2) POST /api/login.php — AJAX 방식 (브라우저의 jquery.ajaxForm과 동일)
      payload = {
        "email": email,
        "password": password,
        "remember_me": "on",
      }
      async with self._session.post(
        GDC_LOGIN_URL,
        data=payload,
        headers={
          "X-Requested-With": "XMLHttpRequest",
          "Origin": GDC_BASE,
          "Referer": f"{GDC_BASE}/login",
        },
      ) as resp:
        status = resp.status
        body = await resp.text()
        log.info("로그인 POST: HTTP %s, body=%r", status, body[:200])

        # GDC Vault는 실패 시 HTTP 500 + 에러 메시지
        if status >= 400:
          self.last_error = body.strip() or f"HTTP {status}"
          log.warning("로그인 실패 (HTTP %s): %s", status, self.last_error)
          await self._close_session()
          return False

      # 3) 쿠키 확인
      cookies = {c.key: c.value for c in jar}
      log.info("세션 쿠키: %s", list(cookies.keys()))

      # 4) 실제 인증 확인 — 회원 전용 페이지 접근 테스트
      self._logged_in = await self.check_login_status()
      if not self._logged_in:
        self.last_error = "로그인 POST는 성공했지만 세션 인증을 확인할 수 없습니다."
        log.warning(self.last_error)
      return self._logged_in

    except Exception as e:
      self.last_error = str(e)
      log.error("로그인 중 예외: %s", e)
      await self._close_session()
      return False

  async def check_login_status(self) -> bool:
    """현재 세션이 인증 상태인지 확인"""
    if not self._session:
      return False

    try:
      # /account.php는 로그인해야만 접근 가능
      # 비로그인 시 /login으로 리다이렉트됨
      async with self._session.get(
        f"{GDC_BASE}/account.php",
        allow_redirects=False,
      ) as resp:
        log.info("인증 확인 (/account.php): HTTP %s", resp.status)
        if resp.status == 200:
          return True

      # 대안: 홈페이지에서 로그아웃 링크 확인
      async with self._session.get(f"{GDC_BASE}/") as resp:
        if resp.status == 200:
          html = await resp.text()
          has_logout = 'href="/logout"' in html or 'id="logout"' in html
          log.info("홈페이지 로그아웃 링크: %s", has_logout)
          return has_logout

      return False
    except Exception as e:
      log.error("인증 확인 실패: %s", e)
      return False

  async def ensure_session(self) -> aiohttp.ClientSession:
    """인증된 세션 반환, 없으면 예외"""
    if not self._session or not self._logged_in:
      raise RuntimeError("로그인이 필요합니다")
    return self._session

  @property
  def is_logged_in(self) -> bool:
    return self._logged_in

  async def logout(self):
    """로그아웃 및 세션 정리"""
    if self._session:
      try:
        async with self._session.get(f"{GDC_BASE}/logout") as resp:
          log.info("로그아웃: HTTP %s", resp.status)
      except Exception:
        pass
    await self._close_session()
    self._logged_in = False

  async def _close_session(self):
    if self._session and not self._session.closed:
      await self._session.close()
    self._session = None
