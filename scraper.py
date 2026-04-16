"""
GDC Vault 스크래핑 — 세션 목록/상세 페이지 파싱
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field

import aiohttp
from bs4 import BeautifulSoup

from config import (
  GDC_BASE,
  GDC_BROWSE_URL,
  MAX_CONCURRENT_SCRAPE,
  REQUEST_DELAY,
  USER_AGENT,
)

log = logging.getLogger(__name__)


@dataclass
class SessionSummary:
  """세션 카드 정보"""
  title: str
  play_url: str  # /play/12345 형태
  speakers: str = ""
  company: str = ""
  category: str = ""
  thumbnail: str = ""
  vault_free: bool = False  # 무료/유료 구분


@dataclass
class SessionDetail:
  """세션 상세 정보"""
  title: str = ""
  play_url: str = ""
  speakers: str = ""
  company: str = ""
  category: str = ""
  overview: str = ""
  tags: list[str] = field(default_factory=list)
  thumbnail: str = ""
  m3u8_url: str = ""
  iframe_url: str = ""
  year: str = ""
  vault_free: bool = False


class RateLimiter:
  """요청 간 최소 딜레이를 보장하는 rate limiter"""

  def __init__(self, delay: float = REQUEST_DELAY, max_concurrent: int = MAX_CONCURRENT_SCRAPE):
    self._delay = delay
    self._semaphore = asyncio.Semaphore(max_concurrent)
    self._last_request = 0.0

  async def acquire(self):
    await self._semaphore.acquire()
    now = time.monotonic()
    wait = self._delay - (now - self._last_request)
    if wait > 0:
      await asyncio.sleep(wait)
    self._last_request = time.monotonic()

  def release(self):
    self._semaphore.release()


class GDCScraper:
  """GDC Vault 페이지 스크래핑"""

  def __init__(self, session: aiohttp.ClientSession):
    self._session = session
    self._limiter = RateLimiter()

  async def _fetch(self, url: str) -> str:
    """rate-limited GET 요청"""
    await self._limiter.acquire()
    try:
      log.info("GET %s", url)
      async with self._session.get(url) as resp:
        if resp.status != 200:
          raise Exception(f"HTTP {resp.status}: {url}")
        return await resp.text()
    finally:
      self._limiter.release()

  async def fetch_events(self) -> list[dict[str, str]]:
    """GDC Vault 브라우즈 페이지에서 이벤트 목록 동적 파싱

    GDC Vault 실제 구조에서 이벤트 링크(/browse/gdc-XX)를 추출하여
    slug와 표시명을 반환합니다. 실패 시 빈 리스트 반환.
    """
    from config import EVENTS as FALLBACK_EVENTS

    html = await self._fetch(GDC_BROWSE_URL)
    soup = BeautifulSoup(html, "html.parser")

    events: list[dict[str, str]] = []
    seen: set[str] = set()

    # 1차: <select> 또는 <option> 에서 이벤트 추출
    for option in soup.select("select option[value]"):
      val = option.get("value", "").strip()
      # /browse/gdc-XX 또는 gdc-XX 형태만 취급
      slug = val.lstrip("/").removeprefix("browse/")
      if not slug or not re.match(r"gdc[-_]\d{2,4}$", slug, re.IGNORECASE):
        continue
      if slug in seen:
        continue
      seen.add(slug)
      label = option.get_text(strip=True) or slug.upper()
      events.append({"slug": slug, "name": label})

    # 2차: 이벤트별 링크 (<a href="/browse/gdc-XX">)
    if not events:
      for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        m = re.match(r"^/browse/(gdc[-_]\d{2,4})/?$", href, re.IGNORECASE)
        if not m:
          continue
        slug = m.group(1).lower()
        if slug in seen:
          continue
        seen.add(slug)
        label = a.get_text(strip=True) or slug.upper()
        events.append({"slug": slug, "name": label})

    if events:
      # 연도 기준 내림차순 정렬 (최신이 맨 앞)
      def _year_key(e: dict[str, str]) -> int:
        m = re.search(r"(\d{2,4})", e["slug"])
        yr = int(m.group(1)) if m else 0
        return yr if yr > 100 else 2000 + yr

      events.sort(key=_year_key, reverse=True)
      log.info("이벤트 목록 동적 파싱 완료: %d개", len(events))
      return events

    log.warning("이벤트 목록 파싱 실패 — config 기본값 사용")
    return FALLBACK_EVENTS

  async def browse_sessions(
    self,
    event_slug: str = "gdc-25",
    category: str = "",
    media: str = "v",  # v=video
  ) -> list[SessionSummary]:
    """이벤트/카테고리별 세션 목록 가져오기"""
    # URL 구성: /browse/gdc-25/by_event/gdc-25
    # 필터: ?categories=Pg&media=v
    url = f"{GDC_BROWSE_URL}/{event_slug}"
    params = {}
    if category:
      params["categories"] = category
    if media:
      params["media"] = media

    if params:
      query = "&".join(f"{k}={v}" for k, v in params.items())
      url = f"{url}?{query}"

    html = await self._fetch(url)
    return self._parse_session_list(html)

  def _parse_session_list(self, html: str) -> list[SessionSummary]:
    """브라우즈 페이지 HTML에서 세션 카드 목록 파싱"""
    soup = BeautifulSoup(html, "html.parser")
    sessions = []

    # GDC Vault 실제 구조: ul.media_items > li > a.session_item
    rows = soup.select("ul.media_items > li")

    for row in rows:
      session = self._parse_session_card(row)
      if session:
        sessions.append(session)

    return sessions

  def _parse_session_card(self, element) -> SessionSummary | None:
    """개별 세션 카드/행에서 정보 추출

    실제 HTML 구조:
      <li class="featured">
        <a class="session_item" href="/play/1035359/...">
          <img class="members" .../>           <!-- 유료 전용 -->
          <div class="featured_image"><img .../></div>
          <div class="conference_info">
            <p>
              <span class="conference_name">...</span><br/>
              <strong>세션 제목</strong><br/>
              <span><em>by</em> 발표자 <strong>(회사)</strong></span><br/>
              <span class="track_name">트랙</span>
            </p>
          </div>
        </a>
      </li>
    """
    # 링크 (a.session_item)
    link = element.select_one("a.session_item")
    if not link:
      return None

    play_url = link.get("href", "")
    if not play_url.startswith("http"):
      play_url = f"{GDC_BASE}{play_url}"

    info = link.select_one(".conference_info")
    if not info:
      return None

    # 제목: conference_info 안의 첫 번째 <strong>
    title_el = info.select_one("strong")
    title = title_el.get_text(strip=True) if title_el else ""

    # 발표자 + 회사: <em>by</em> 뒤의 텍스트
    speakers = ""
    company = ""
    by_el = info.find("em", string=re.compile(r"by"))
    if by_el and by_el.parent:
      span = by_el.parent
      # 회사: span 안의 <strong> (예: "(Clockwork Labs)")
      company_el = span.find("strong")
      company = company_el.get_text(strip=True).strip("()") if company_el else ""
      # 발표자: span의 전체 텍스트에서 "by"와 회사명 제거
      full_text = span.get_text(strip=True)
      speakers = full_text
      if speakers.startswith("by"):
        speakers = speakers[2:].strip()
      if company_el:
        speakers = speakers.replace(company_el.get_text(strip=True), "").strip()

    # 카테고리 / 트랙
    track_el = info.select_one(".track_name")
    category = track_el.get_text(strip=True) if track_el else ""

    # 썸네일: featured_image 안의 img
    thumb_el = element.select_one(".featured_image img")
    thumbnail = thumb_el.get("src", "") if thumb_el else ""
    if thumbnail and not thumbnail.startswith("http"):
      thumbnail = f"{GDC_BASE}{thumbnail}"

    # 무료 여부: img.members가 없으면 무료
    vault_free = not element.select_one("img.members")

    return SessionSummary(
      title=title,
      play_url=play_url,
      speakers=speakers,
      company=company,
      category=category,
      thumbnail=thumbnail,
      vault_free=vault_free,
    )

  async def _extract_m3u8_url(self, html: str, play_url: str) -> str:
    """play 페이지 HTML에서 m3u8 URL 추출

    추출 순서:
    1. HTML에서 직접 m3u8 URL 검색
    2. blazestreaming iframe 발견 → id 파라미터 + script_VOD.js 템플릿으로 URL 구성
    3. iframe HTML 내에서 직접 m3u8 URL 검색
    """
    from urllib.parse import urljoin as _urljoin

    # 1) play 페이지 HTML에서 직접 m3u8 URL 검색
    m3u8_direct = re.search(
      r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
      html,
      re.IGNORECASE,
    )
    if m3u8_direct:
      log.info("play 페이지에서 직접 m3u8 발견: %s", m3u8_direct.group(1)[:80])
      return m3u8_direct.group(1)

    # 2) iframe에서 blazestreaming 플레이어 찾기
    iframes = re.findall(
      r'<iframe[^>]+src=["\']([^"\']+)["\']',
      html,
      re.IGNORECASE,
    )

    for iframe_src in iframes:
      if "blazestreaming" not in iframe_src:
        continue

      # iframe URL에서 video id 추출 (?id=xxx)
      id_match = re.search(r'[?&]id=([^&]+)', iframe_src)
      if not id_match:
        continue

      video_id = id_match.group(1)
      log.info("blazestreaming video_id: %s", video_id)

      try:
        iframe_html = await self._fetch(iframe_src)

        # iframe 내에서 직접 m3u8 URL 검색
        m3u8_match = re.search(
          r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
          iframe_html,
          re.IGNORECASE,
        )
        if m3u8_match:
          log.info("iframe에서 m3u8 발견: %s", m3u8_match.group(1)[:80])
          return m3u8_match.group(1)

        # script_VOD.js에서 URL 템플릿 추출 후 videoId 대입
        script_match = re.search(
          r'<script[^>]+src=["\']([^"\']*script_VOD[^"\']*)["\']',
          iframe_html,
          re.IGNORECASE,
        )
        if script_match:
          script_url = _urljoin(iframe_src, script_match.group(1))
          script_content = await self._fetch(script_url)

          # URL 템플릿에서 videoId 부분을 실제 값으로 치환
          # 예: 'https://cdn-a.blazestreaming.com/out/v1/'+videoId+'/xxx/.../index.m3u8'
          url_template = re.search(
            r"""['"]?(https?://[^'"]*?)\s*['"]\s*\+\s*videoId\s*\+\s*['"]\s*([^'"]*\.m3u8[^'"]*)['"]""",
            script_content,
          )
          if url_template:
            m3u8_url = url_template.group(1) + video_id + url_template.group(2)
            log.info("script_VOD.js 템플릿으로 m3u8 구성: %s", m3u8_url[:80])
            return m3u8_url

      except Exception as e:
        log.warning("blazestreaming iframe 처리 실패: %s", e)

    log.warning("m3u8 URL을 찾을 수 없음: %s", play_url)
    return ""

  def _parse_play_metadata(self, soup: BeautifulSoup) -> dict[str, str]:
    """play 페이지의 dl 구조에서 라벨-값 쌍 추출

    GDC Vault play 페이지 실제 구조:
    <dl class="player-info">
      <dt><strong>라벨:</strong></dt>
      <dd>값</dd>
    </dl>
    + Overview는 별도 <dl class="overview-section">에 위치.
    """
    meta = {}
    # player-info dl에서 메타데이터 추출
    for dl in soup.find_all("dl", class_=["player-info", "overview-section"]):
      dts = dl.find_all("dt")
      dds = dl.find_all("dd")
      for dt, dd in zip(dts, dds):
        # dt 안의 <strong> 또는 <h3>에서 라벨 추출
        label_el = dt.find(["strong", "h3"])
        if not label_el:
          continue
        label = label_el.get_text(strip=True)
        # 콜론 제거, 소문자 정규화
        label = label.rstrip(":").strip().lower()
        # dd에서 텍스트 추출 (연속 공백 정리)
        value = dd.get_text(" ", strip=True)
        value = re.sub(r"\s+", " ", value).strip()
        if label and value:
          meta[label] = value
    return meta

  def _parse_play_tags(self, soup: BeautifulSoup) -> list[str]:
    """play 페이지에서 태그 목록 추출

    실제 구조: <ul id="tags"><li>태그1</li><li>태그2</li></ul>
    """
    tags = []
    # 1차: ul#tags 내 li 탐색
    tag_container = soup.select_one("ul#tags")
    if tag_container:
      for li in tag_container.find_all("li"):
        text = li.get_text(strip=True)
        if text:
          tags.append(text)
    # 2차 폴백: 다른 가능한 태그 컨테이너
    if not tags:
      for container in soup.select("#tags, .tags_container, .session_tags"):
        for el in container.find_all(["a", "li", "span"]):
          text = el.get_text(strip=True)
          if text:
            tags.append(text)
    # 3차 폴백: 개별 태그 클래스
    if not tags:
      for el in soup.select(".tag, .session_tag, .tags a"):
        text = el.get_text(strip=True)
        if text:
          tags.append(text)
    return tags

  async def get_session_detail(self, play_url: str) -> SessionDetail:
    """세션 상세 페이지에서 메타데이터 + m3u8 URL 추출

    파싱 전략 (3단계 폴백):
    1차: BeautifulSoup 테이블 파싱 (_parse_play_metadata)
    2차: 정규식 폴백
    3차: CSS 선택자 (최후 수단)
    추가: dataLayer JSON에서 보완
    """
    html = await self._fetch(play_url)
    soup = BeautifulSoup(html, "html.parser")
    detail = SessionDetail(play_url=play_url)

    # 디버그 HTML 저장 (debug 레벨에서만)
    if log.isEnabledFor(logging.DEBUG):
      debug_file = f"debug_play_{play_url.split('/')[-1]}.html"
      try:
        with open(debug_file, "w", encoding="utf-8") as f:
          f.write(html)
        log.debug("디버그 HTML 저장: %s", debug_file)
      except Exception:
        pass

    # ── 1차: 테이블 기반 파싱 ──
    table_meta = self._parse_play_metadata(soup)
    if table_meta:
      log.info("테이블 메타데이터 파싱 결과: %s", list(table_meta.keys()))
      detail.title = table_meta.get("session name", "")
      detail.speakers = table_meta.get("speaker(s)", "")
      detail.company = table_meta.get("company name(s)", "")
      detail.category = table_meta.get("track / format", "")
      # Overview: HTML 태그 제거
      raw_overview = table_meta.get("overview", "")
      detail.overview = re.sub(r"<[^>]+>", "", raw_overview).strip()

    # ── 2차: 정규식 폴백 (dl/dt/dd 구조 매칭) ──
    if not detail.title:
      m = re.search(
        r"<strong>Session Name:?</strong>\s*</dt>\s*<dd[^>]*>\s*(.*?)\s*</dd>",
        html, re.IGNORECASE | re.DOTALL,
      )
      if m:
        detail.title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    if not detail.speakers:
      m = re.search(
        r"<strong>Speaker\(s\):?</strong>\s*</dt>\s*<dd[^>]*>\s*(.*?)\s*</dd>",
        html, re.IGNORECASE | re.DOTALL,
      )
      if m:
        detail.speakers = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        # 연속 공백 정리
        detail.speakers = re.sub(r"\s+", " ", detail.speakers).strip()
    if not detail.company:
      m = re.search(
        r"<strong>Company Name\(s\):?</strong>\s*</dt>\s*<dd[^>]*>\s*(.*?)\s*</dd>",
        html, re.IGNORECASE | re.DOTALL,
      )
      if m:
        detail.company = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        detail.company = re.sub(r"\s+", " ", detail.company).strip()
    if not detail.category:
      m = re.search(
        r"<strong>Track / Format:?</strong>\s*</dt>\s*<dd[^>]*>\s*(.*?)\s*</dd>",
        html, re.IGNORECASE | re.DOTALL,
      )
      if m:
        detail.category = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    if not detail.overview:
      m = re.search(
        r"<h3>Overview:?</h3>\s*</dt>\s*<dd[^>]*>\s*(.*?)\s*</dd>",
        html, re.IGNORECASE | re.DOTALL,
      )
      if m:
        detail.overview = re.sub(r"<[^>]+>", "", m.group(1)).strip()

    # ── 3차: CSS 선택자 폴백 (최후 수단) ──
    if not detail.title:
      el = soup.select_one("h1, .session_title, #session_title, h2.title")
      if el:
        detail.title = el.get_text(strip=True)
    if not detail.speakers:
      el = soup.select_one(".conference_speaker, .speaker_info, .speaker")
      if el:
        detail.speakers = el.get_text(strip=True)
    if not detail.company:
      el = soup.select_one(".company_info, .company")
      if el:
        detail.company = el.get_text(strip=True)
    if not detail.overview:
      el = soup.select_one(".overview_body, .session_description, #session_overview, .description")
      if el:
        detail.overview = el.get_text(strip=True)

    # 태그
    detail.tags = self._parse_play_tags(soup)

    # 썸네일
    img = soup.select_one(".session_image img, .video_thumb img, img.thumb")
    if img:
      detail.thumbnail = img.get("src", "")
      if detail.thumbnail and not detail.thumbnail.startswith("http"):
        detail.thumbnail = f"{GDC_BASE}{detail.thumbnail}"

    # dataLayer에서 구조화된 정보 보완
    # 실제 구조: window.dataLayer.push({ 'event': 'pageLoad', 'page': { 'attributes': { 'session': { ... } } } })
    dl_match = re.search(
      r"dataLayer\.push\(\s*(\{.*?\})\s*\)",
      html, re.DOTALL,
    )
    if dl_match:
      try:
        # JS 객체를 JSON으로 변환 (작은따옴표→큰따옴표, trailing comma 제거)
        raw = dl_match.group(1)
        raw = raw.replace("'", '"')
        raw = re.sub(r",\s*}", "}", raw)
        raw = re.sub(r",\s*]", "]", raw)
        data = json.loads(raw)
        session_data = data.get("page", {}).get("attributes", {}).get("session", {})
        if session_data:
          detail.title = detail.title or re.sub(r"&#0?39;", "'", session_data.get("sessionName", ""))
          detail.category = detail.category or session_data.get("sessionTrack", "")
          # conferenceName에서 연도 추출 (예: "GDC 2016")
          conf = session_data.get("conferenceName", "")
          if conf and not detail.year:
            year_m = re.search(r"(\d{4})", conf)
            detail.year = year_m.group(1) if year_m else ""
      except (json.JSONDecodeError, AttributeError):
        log.debug("dataLayer JSON 파싱 실패")

    # --- m3u8 URL 추출 ---
    detail.m3u8_url = await self._extract_m3u8_url(html, play_url)

    # 무료 여부
    detail.vault_free = "vault free" in html.lower() or "class=\"free\"" in html.lower()

    # 파싱 결과 요약 로그
    filled = [k for k in ["title", "speakers", "company", "category", "overview", "tags"]
              if getattr(detail, k)]
    log.info("세션 상세 파싱 완료 [%s]: 추출된 필드=%s", play_url.split("/")[-1], filled)

    return detail
