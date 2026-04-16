"""
GDC Vault 브라우저 설정
"""

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
