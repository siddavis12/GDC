"""
GDC Vault 브라우저 설정
"""

import os

from dotenv import load_dotenv

# .env 자동 로드 (루트 디렉토리에 위치)
load_dotenv()


def _env_bool(name: str, default: bool = True) -> bool:
  val = os.getenv(name)
  if val is None:
    return default
  return val.strip().lower() in ("1", "true", "yes", "on")


# GDC Vault URL
GDC_BASE = "https://gdcvault.com"
GDC_LOGIN_URL = f"{GDC_BASE}/api/login.php"
GDC_BROWSE_URL = f"{GDC_BASE}/browse"

# Rate limiting
REQUEST_DELAY = 1.5  # 요청 간 최소 대기 (초)
MAX_CONCURRENT_SCRAPE = 3  # 동시 스크래핑 요청 수
MAX_CONCURRENT_VTT = 20  # VTT 세그먼트 동시 다운로드 수

# User-Agent
USER_AGENT = (
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
  "AppleWebKit/537.36 (KHTML, like Gecko) "
  "Chrome/133.0.0.0 Safari/537.36"
)

# 이벤트 목록 (최신순) — 동적 로드 실패 시 fallback으로 사용
EVENTS = [
  {"slug": "gdc-2026", "name": "GDC 2026"},
  {"slug": "gdc-25", "name": "GDC 2025"},
  {"slug": "gdc-2024", "name": "GDC 2024"},
  {"slug": "gdc-2023", "name": "GDC 2023"},
  {"slug": "gdc-2022", "name": "GDC 2022"},
  {"slug": "gdc-2021", "name": "GDC 2021"},
  {"slug": "gdc-2020", "name": "GDC 2020"},
  {"slug": "gdc-2019", "name": "GDC 2019"},
  {"slug": "gdc-2018", "name": "GDC 2018"},
]

# 카테고리 맵 (GDC Vault 실제 코드 → 표시명)
CATEGORIES = {
  "Ad": "Advocacy",
  "Ai": "AI",
  "Au": "Audio",
  "Bm": "Business & Marketing",
  "Cm": "Community Management",
  "De": "Design",
  "Es": "eSports",
  "Ed": "Game Career / Education",
  "Gn": "Game Narrative",
  "In": "Independent Games",
  "Lq": "Localization / QA",
  "Mo": "Monetization",
  "Or": "Other",
  "Pr": "Production",
  "Pg": "Programming",
  "Sg": "Serious Games",
  "Ta": "Smartphone / Tablet Games",
  "On": "Social / Online Games",
  "Vr": "Virtual / Augmented Reality",
  "Va": "Visual Arts",
}

# 트랜스크립트 저장 디렉토리
TRANSCRIPT_DIR = "transcripts"

# ── NotebookLM 소스 풍부화 설정 ──

# API 키 (.env에서 로드)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")

# AI 모델 (.env에서 override 가능)
AI_MODEL = os.getenv("AI_MODEL", "claude-sonnet-4-6")
PERPLEXITY_MODEL = os.getenv("PERPLEXITY_MODEL", "sonar-pro")
PERPLEXITY_ENDPOINT = "https://api.perplexity.ai/chat/completions"

# 기능 플래그 (.env에서 제어). 키가 없으면 자동으로 비활성화됨.
AI_ENHANCE_ENABLED = _env_bool("AI_ENHANCE_ENABLED", True) and bool(ANTHROPIC_API_KEY)
WEB_CONTEXT_ENABLED = _env_bool("WEB_CONTEXT_ENABLED", True) and bool(PERPLEXITY_API_KEY)

# 품질 개선 토글
AI_NORMALIZE_ENABLED = _env_bool("AI_NORMALIZE_ENABLED", True)
AI_QA_ENABLED = _env_bool("AI_QA_ENABLED", True)

# Claude API 동시 호출 제한 (챕터/용어집/핵심포인트/Q&A/엔티티 병렬 안정성 위해 제한)
MAX_CONCURRENT_CLAUDE = 3

# Perplexity 관련 상수 — 결과 희박 시 확장 폴백 트리거 및 쿼리 상한
WEB_CONTEXT_MAX_QUERIES = int(os.getenv("WEB_CONTEXT_MAX_QUERIES", "5"))
WEB_CONTEXT_WIDEN_IF_FEWER = int(os.getenv("WEB_CONTEXT_WIDEN_IF_FEWER", "3"))

# Perplexity 선호 도메인 — 해외 메이저 게임 언론사만 허용
# 앞 10개는 Perplexity search_domain_filter 에 직접 전달 (API 상한이 10).
# 뒤 항목은 API 필터에는 포함되지 않지만 _is_preferred 포스트 필터는 통과시킴.
PREFERRED_GAME_NEWS_DOMAINS = [
  # Top 10 — 게임 전문지/업계지 우선 (Perplexity API 필터 전달)
  "gamedeveloper.com",
  "gamesindustry.biz",
  "polygon.com",
  "rockpapershotgun.com",
  "pcgamer.com",
  "kotaku.com",
  "ign.com",
  "eurogamer.net",
  "80.lv",
  "venturebeat.com",
  # 그 외 (포스트 필터 전용)
  "gamasutra.com",
  "edge-online.com",
  "gamesradar.com",
  "vg247.com",
  "theverge.com",
  "arstechnica.com",
  "cgmagonline.com",
]
