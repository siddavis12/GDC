"""
Perplexity 기반 해외 게임 언론 기사 검색

GDC 세션 정보와 Claude가 뽑은 고유명사를 이용해
메이저 해외 게임 언론의 기사·인터뷰·후속 보도를 찾아 Markdown으로 정리한다.

품질 개선:
  - 엔티티 팬아웃 쿼리: 상위 엔티티 각각을 독립 쿼리로 → 관련성 향상
  - " OR " 리터럴 제거: Perplexity는 텍스트로 해석하므로 공백 구분 키워드 사용
  - 엔티티 언급 검증 dedupe: title/summary 에 쿼리 엔티티가 매치되는지 확인
  - 결과 희박 시 확장 폴백: API 도메인 필터 제거 후 재시도, 포스트 필터는 전체 whitelist 사용
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import aiohttp

from config import (
  PERPLEXITY_API_KEY,
  PERPLEXITY_ENDPOINT,
  PERPLEXITY_MODEL,
  PREFERRED_GAME_NEWS_DOMAINS,
  WEB_CONTEXT_MAX_QUERIES,
  WEB_CONTEXT_WIDEN_IF_FEWER,
)

log = logging.getLogger(__name__)


@dataclass
class Article:
  title: str
  url: str
  domain: str
  date: str = ""
  summary: str = ""

  def as_markdown_block(self, index: int) -> str:
    """AI 소비용 마크다운 블록 — 테이블 셀 제약 없이 구조화된 메타데이터."""
    title = (self.title or "(제목 없음)").strip()
    date = self.date.strip() if self.date else "N/A"
    summary = self.summary.strip() or "(요약 없음)"
    lines = [
      f"## {index}. {title}",
      "",
      f"- **Source**: {self.domain}",
      f"- **Date**: {date}",
      f"- **URL**: {self.url}",
      "",
      "**Summary**",
      "",
      summary,
    ]
    return "\n".join(lines)


def _domain_of(url: str) -> str:
  try:
    host = urlparse(url).hostname or ""
  except Exception:
    return ""
  return host.lower().removeprefix("www.")


def _is_preferred(url: str) -> bool:
  """URL이 PREFERRED_GAME_NEWS_DOMAINS 전체 리스트에 포함되는지 검사."""
  domain = _domain_of(url)
  if not domain:
    return False
  return any(
    domain == d or domain.endswith("." + d)
    for d in PREFERRED_GAME_NEWS_DOMAINS
  )


_LOOSE_PATH_KEYWORDS = re.compile(
  r"(game|games|gaming|gamedev|studio|developer|indie)", re.IGNORECASE,
)


def _is_preferred_loose(url: str) -> bool:
  """확장 폴백 모드 — PREFERRED 도메인 또는 URL 경로에 게임 관련 키워드."""
  if _is_preferred(url):
    return True
  try:
    parsed = urlparse(url)
    blob = f"{parsed.hostname or ''}{parsed.path or ''}"
    return bool(_LOOSE_PATH_KEYWORDS.search(blob))
  except Exception:
    return False


def _is_gdc_context(url: str, title: str, summary: str) -> bool:
  """무관한 'GDC' 약어(치과 컨퍼런스 등) 필터링."""
  blob = f"{url} {title} {summary}".lower()
  return "game" in blob or "gdc" in blob or "studio" in blob or "developer" in blob


def _mentions_entity(article: Article, entities: list[str]) -> bool:
  """article 제목/요약/URL 에 엔티티 중 하나라도 word-boundary 매치되는지.
  엔티티 리스트가 비어있으면 검사 패스."""
  if not entities:
    return True
  blob = f"{article.title} {article.summary} {article.url}".lower()
  for e in entities:
    e_clean = e.strip().lower()
    if len(e_clean) < 3:
      continue
    # 단어 경계 매칭 (특수문자 포함 엔티티는 substring fallback)
    if re.search(r"\b" + re.escape(e_clean) + r"\b", blob):
      return True
    if e_clean in blob:
      return True
  return False


SYSTEM_PROMPT = (
  "You are a research assistant finding articles from major English-language "
  "video game journalism outlets. Return ONLY articles that are clearly about "
  "game development, the game industry, or specific games. Exclude non-English "
  "sources and non-gaming outlets. For each article return strict JSON only, "
  "no prose, no markdown fences, matching this schema:\n"
  '{"articles":[{"title":"...","url":"...","date":"YYYY-MM-DD or empty",'
  '"summary":"two sentences"}]}'
)


def _build_queries(
  title: str, speakers: str, company: str, year: str, tags: list[str],
  entities: list[str],
) -> list[str]:
  """세션 커버리지 + 발표자 인터뷰 + 상위 엔티티 팬아웃 쿼리 목록 구성.

  상한: WEB_CONTEXT_MAX_QUERIES.
  " OR " 리터럴을 사용하지 않음 (Perplexity는 boolean으로 해석 안 함).
  """
  tag_hint = tags[0] if tags else ""
  queries: list[str] = []

  # 쿼리 1: 세션 직접 커버리지
  year_part = f"GDC {year}" if year else "GDC"
  q1_parts: list[str] = []
  if title:
    q1_parts.append(f'"{title}"')
  if speakers:
    q1_parts.append(f"by {speakers}")
  q1_parts.append(year_part)
  q1_parts.append("talk recap summary coverage")
  queries.append(" ".join(q1_parts).strip())

  # 쿼리 2: 발표자 인터뷰
  if speakers:
    q2 = f"Interviews with {speakers}"
    if company:
      q2 += f" from {company}"
    q2 += " about game development"
    if tag_hint:
      q2 += f" {tag_hint}"
    queries.append(q2)

  # 쿼리 3~: 엔티티별 팬아웃 (최상위 3개)
  for ent in (entities or [])[:3]:
    ent_clean = ent.strip()
    if not ent_clean:
      continue
    parts = [ent_clean]
    if company and company.lower() not in ent_clean.lower():
      parts.append(company)
    parts.append("game industry coverage")
    queries.append(" ".join(parts))

  # 비어있는 쿼리 제거, 상한 적용
  queries = [q for q in queries if q]
  return queries[:WEB_CONTEXT_MAX_QUERIES]


async def _perplexity_query(
  session: aiohttp.ClientSession,
  query: str,
  use_domain_filter: bool = True,
) -> list[Article]:
  """단일 Perplexity 쿼리 → Article 리스트.

  use_domain_filter=True면 선호 도메인(앞 10개) 필터 적용.
  False면 확장 폴백 모드로 필터 없이 검색.
  """
  headers = {
    "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
    "Content-Type": "application/json",
  }
  body: dict = {
    "model": PERPLEXITY_MODEL,
    "messages": [
      {"role": "system", "content": SYSTEM_PROMPT},
      {"role": "user", "content": query},
    ],
    "return_citations": True,
    "temperature": 0.2,
  }
  if use_domain_filter:
    body["search_domain_filter"] = PREFERRED_GAME_NEWS_DOMAINS[:10]

  try:
    async with session.post(
      PERPLEXITY_ENDPOINT, headers=headers, json=body, timeout=60
    ) as resp:
      if resp.status != 200:
        text = await resp.text()
        log.warning("Perplexity %d: %s", resp.status, text[:200])
        return []
      data = await resp.json()
  except (aiohttp.ClientError, asyncio.TimeoutError) as e:
    log.warning("Perplexity 요청 실패: %s", e)
    return []

  choices = data.get("choices") or []
  if not choices:
    return []
  content = choices[0].get("message", {}).get("content", "")
  citations = data.get("citations") or data.get("search_results") or []

  return _parse_articles(content, citations)


def _parse_articles(content: str, citations: list) -> list[Article]:
  """JSON 응답에서 Article 추출. 실패 시 citations로 폴백."""
  articles: list[Article] = []

  cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.MULTILINE)
  cleaned = cleaned.strip()

  parsed = None
  try:
    parsed = json.loads(cleaned)
  except json.JSONDecodeError:
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
      try:
        parsed = json.loads(m.group(0))
      except json.JSONDecodeError:
        parsed = None

  if isinstance(parsed, dict):
    for item in parsed.get("articles", []) or []:
      url = (item.get("url") or "").strip()
      if not url:
        continue
      articles.append(Article(
        title=(item.get("title") or "").strip(),
        url=url,
        domain=_domain_of(url),
        date=(item.get("date") or "").strip(),
        summary=(item.get("summary") or "").strip(),
      ))

  if not articles and citations:
    for c in citations:
      if isinstance(c, str):
        url = c
        title = ""
      else:
        url = (c.get("url") or "").strip()
        title = (c.get("title") or "").strip()
      if not url:
        continue
      articles.append(Article(
        title=title,
        url=url,
        domain=_domain_of(url),
      ))

  return articles


async def _head_ok(session: aiohttp.ClientSession, url: str) -> bool:
  """URL 유효성 HEAD 체크."""
  try:
    async with session.head(url, allow_redirects=True, timeout=10) as resp:
      return 200 <= resp.status < 400
  except (aiohttp.ClientError, asyncio.TimeoutError):
    return False


def _format_markdown(articles: list[Article], queries: list[str]) -> str:
  lines = [
    "# 관련 기사 및 인터뷰",
    "",
    "해외 메이저 게임 언론사에서 수집한 이 세션 관련 기사입니다.",
    "",
  ]

  if not articles:
    lines.append("*관련 기사를 찾지 못했습니다.*")
    lines.append("")
    lines.append("## 검색 쿼리")
    lines.append("")
    for q in queries:
      lines.append(f"- {q}")
    return "\n".join(lines)

  lines.append(f"총 {len(articles)}건 수집.")
  lines.append("")
  for idx, a in enumerate(articles, start=1):
    lines.append(a.as_markdown_block(idx))
    lines.append("")
    lines.append("---")
    lines.append("")

  lines.append("## 검색 쿼리")
  lines.append("")
  for q in queries:
    lines.append(f"- {q}")

  return "\n".join(lines)


async def _run_queries(
  session: aiohttp.ClientSession,
  queries: list[str],
  entities: list[str],
  use_domain_filter: bool,
  preferred_check,
) -> list[Article]:
  """쿼리 리스트 실행 → 필터링 → Article 병합."""
  results = await asyncio.gather(*[
    _perplexity_query(session, q, use_domain_filter=use_domain_filter)
    for q in queries
  ], return_exceptions=True)

  seen: set[str] = set()
  merged: list[Article] = []
  for res in results:
    if isinstance(res, Exception):
      log.warning("Perplexity 쿼리 예외: %s", res)
      continue
    for a in res:
      if a.url in seen:
        continue
      if not preferred_check(a.url):
        continue
      if not _is_gdc_context(a.url, a.title, a.summary):
        continue
      if not _mentions_entity(a, entities):
        continue
      seen.add(a.url)
      merged.append(a)
  return merged


async def find_related_articles(
  title: str = "",
  speakers: str = "",
  company: str = "",
  year: str = "",
  tags: list[str] | None = None,
  entities: list[str] | None = None,
  max_articles: int = 10,
) -> str:
  """관련 기사 검색 → Markdown 반환. 키가 없으면 빈 문자열."""
  if not PERPLEXITY_API_KEY:
    log.info("PERPLEXITY_API_KEY 없음 — related_articles 단계 스킵")
    return ""

  queries = _build_queries(
    title=title, speakers=speakers, company=company, year=year,
    tags=tags or [], entities=entities or [],
  )
  if not queries:
    log.info("유효한 검색 쿼리를 구성하지 못했습니다.")
    return _format_markdown([], [])

  async with aiohttp.ClientSession() as session:
    merged = await _run_queries(
      session, queries, entities or [],
      use_domain_filter=True,
      preferred_check=_is_preferred,
    )

    # 확장 폴백: 결과가 희박하면 도메인 필터 제거하고 재시도
    if len(merged) < WEB_CONTEXT_WIDEN_IF_FEWER:
      log.info(
        "결과 희박(%d건) — 확장 폴백 쿼리 재실행", len(merged)
      )
      widened = await _run_queries(
        session, queries, entities or [],
        use_domain_filter=False,
        preferred_check=_is_preferred_loose,
      )
      # 기존 URL 중복 제거하며 병합
      existing = {a.url for a in merged}
      for a in widened:
        if a.url not in existing:
          merged.append(a)
          existing.add(a.url)

    # URL 유효성 검증 (병렬 HEAD)
    if merged:
      checks = await asyncio.gather(*[
        _head_ok(session, a.url) for a in merged
      ], return_exceptions=True)
      merged = [
        a for a, ok in zip(merged, checks)
        if ok is True
      ]

    merged = merged[:max_articles]

  log.info("관련 기사 %d건 수집 (쿼리 %d개)", len(merged), len(queries))
  return _format_markdown(merged, queries)
