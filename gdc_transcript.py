"""
GDC Vault 자막 추출기

GDC Vault 영상에서 자막(VTT)을 추출하여
깔끔한 텍스트 파일로 변환합니다.

사용법:
  python gdc_transcript.py <GDC_VAULT_URL_또는_M3U8_URL> [옵션]

예시:
  python gdc_transcript.py https://gdcvault.com/play/1034837
  python gdc_transcript.py https://cdn-a.blazestreaming.com/.../index.m3u8
  python gdc_transcript.py https://gdcvault.com/play/1034837 --lang jpn
"""

import argparse
import asyncio
import logging
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

try:
  import aiohttp
except ImportError:
  print("aiohttp가 필요합니다: pip install aiohttp")
  sys.exit(1)

log = logging.getLogger(__name__)


# 자막 언어 코드 매핑
LANG_MAP = {
  "eng": "English",
  "spa": "Spanish",
  "zho": "Chinese",
  "jpn": "Japanese",
}


async def fetch_text(session, url):
  """URL에서 텍스트를 가져옴"""
  async with session.get(url) as resp:
    if resp.status != 200:
      raise Exception(f"HTTP {resp.status}: {url}")
    return await resp.text()


async def fetch_page_for_m3u8(session, vault_url):
  """GDC Vault 페이지에서 m3u8 URL을 추출"""
  # GDC Vault 페이지 가져오기
  async with session.get(vault_url) as resp:
    if resp.status != 200:
      raise Exception(f"GDC Vault 페이지 접근 실패 (HTTP {resp.status})")
    html = await resp.text()

  # Blazestreaming iframe URL 찾기
  iframe_match = re.search(
    r'<iframe[^>]+src=["\']([^"\']*blazestreaming[^"\']*)["\']',
    html,
    re.IGNORECASE,
  )
  if not iframe_match:
    raise Exception(
      "Blazestreaming iframe을 찾을 수 없습니다.\n"
      "유료 콘텐츠라면 로그인이 필요할 수 있습니다.\n"
      "대안: 브라우저 DevTools에서 m3u8 URL을 직접 복사하여 입력하세요."
    )

  iframe_url = iframe_match.group(1)
  print(f"  iframe 발견: {iframe_url[:80]}...")

  # iframe 페이지에서 PLAYBACK_URL 또는 m3u8 URL 찾기
  async with session.get(iframe_url) as resp:
    iframe_html = await resp.text()

  m3u8_match = re.search(
    r'(?:PLAYBACK_URL|playbackUrl|source)["\s:=]+["\']?(https://[^"\'\s]+\.m3u8[^"\'\s]*)',
    iframe_html,
    re.IGNORECASE,
  )
  if not m3u8_match:
    raise Exception(
      "m3u8 URL을 자동으로 찾을 수 없습니다.\n"
      "브라우저 DevTools > Network 탭에서 m3u8 URL을 직접 복사하여 입력하세요."
    )

  return m3u8_match.group(1)


def parse_master_manifest(content, base_url):
  """마스터 m3u8에서 자막 트랙 정보 파싱"""
  subtitles = {}
  for match in re.finditer(
    r'#EXT-X-MEDIA:TYPE=SUBTITLES.*?LANGUAGE="(\w+)".*?URI="([^"]+)"',
    content,
  ):
    lang = match.group(1)
    uri = match.group(2)
    full_url = urljoin(base_url, uri)
    subtitles[lang] = full_url
  return subtitles


def parse_subtitle_playlist(content, base_url):
  """자막 playlist에서 VTT 세그먼트 URL 목록 파싱"""
  segments = []
  for line in content.strip().split("\n"):
    line = line.strip()
    if line and not line.startswith("#"):
      # 상대 경로를 절대 URL로 변환
      full_url = urljoin(base_url, line)
      segments.append(full_url)
  return segments


def parse_vtt_segment(content):
  """VTT 세그먼트에서 타임스탬프와 텍스트를 추출"""
  entries = []
  lines = content.strip().split("\n")
  i = 0

  while i < len(lines):
    # 타임스탬프 라인 찾기 (00:00:00.000 --> 00:00:00.000)
    timestamp_match = re.match(
      r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})",
      lines[i],
    )
    if timestamp_match:
      start = timestamp_match.group(1)
      end = timestamp_match.group(2)
      text_lines = []
      i += 1
      # 타임스탬프 다음에 오는 텍스트 라인들 수집
      while i < len(lines) and lines[i].strip() and not re.match(
        r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->", lines[i]
      ):
        text_lines.append(lines[i].strip())
        i += 1
      text = " ".join(text_lines)
      if text and text != "[MUSIC PLAYING]" and text != "[MUSIC]":
        entries.append({"start": start, "end": end, "text": text})
    else:
      i += 1

  return entries


def merge_entries(all_entries):
  """중복 제거 및 연속된 동일 텍스트 병합"""
  if not all_entries:
    return []

  # 타임스탬프 기준 정렬
  all_entries.sort(key=lambda e: e["start"])

  # 중복 제거 (같은 시작 시간 + 같은 텍스트)
  seen = set()
  unique = []
  for entry in all_entries:
    key = (entry["start"], entry["text"])
    if key not in seen:
      seen.add(key)
      unique.append(entry)

  # 연속된 동일 텍스트 병합
  merged = [unique[0]]
  for entry in unique[1:]:
    if entry["text"] == merged[-1]["text"]:
      merged[-1]["end"] = entry["end"]
    else:
      merged.append(entry)

  return merged


def format_vtt(entries):
  """VTT 형식으로 출력"""
  lines = ["WEBVTT", ""]
  for i, entry in enumerate(entries, 1):
    lines.append(str(i))
    lines.append(f"{entry['start']} --> {entry['end']}")
    lines.append(entry["text"])
    lines.append("")
  return "\n".join(lines)


def format_text(entries):
  """NotebookLM용 깔끔한 텍스트로 출력"""
  paragraphs = []
  current_paragraph = []

  for entry in entries:
    text = entry["text"].strip()
    if not text:
      continue

    current_paragraph.append(text)

    # 문장이 끝나면 단락 구분
    if text.endswith((".", "!", "?", '."', '!"', '?"')):
      paragraphs.append(" ".join(current_paragraph))
      current_paragraph = []

  # 남은 텍스트 추가
  if current_paragraph:
    paragraphs.append(" ".join(current_paragraph))

  return "\n\n".join(paragraphs)


def format_timestamped_text(entries):
  """타임스탬프 포함 텍스트 출력"""
  lines = []
  for entry in entries:
    # HH:MM:SS 형식으로 축약
    timestamp = entry["start"][:8]
    lines.append(f"[{timestamp}] {entry['text']}")
  return "\n".join(lines)


async def download_segments(session, segment_urls, max_concurrent=20):
  """VTT 세그먼트를 병렬로 다운로드"""
  semaphore = asyncio.Semaphore(max_concurrent)
  results = [None] * len(segment_urls)

  async def fetch_one(idx, url):
    async with semaphore:
      text = await fetch_text(session, url)
      results[idx] = text

  tasks = [fetch_one(i, url) for i, url in enumerate(segment_urls)]
  await asyncio.gather(*tasks)
  return results


async def extract_transcript(
  url,
  lang="eng",
  output_dir=None,
  session=None,
  video_id=None,
  detail=None,
  include_chapters: bool = False,
  include_glossary: bool = False,
  include_keypoints: bool = False,
  include_qa: bool = False,
  include_articles: bool = False,
  include_thumbnail: bool = False,
):
  """메인 추출 함수

  Args:
    url: GDC Vault URL 또는 m3u8 URL
    lang: 자막 언어 코드
    output_dir: 출력 디렉토리
    session: 외부에서 주입할 aiohttp.ClientSession (None이면 새로 생성)
    video_id: 파일명에 사용할 ID (None이면 URL에서 추출)
    detail: scraper.SessionDetail (선택) — 메타 헤더/번들용. None이면 CLI에서 추정
    include_chapters/glossary/keypoints/articles/thumbnail: 번들에 포함할 구성 요소.
      어느 하나라도 True면 번들 ZIP이 생성된다. 모두 False면 기존 3종 파일만 출력.
  """
  if output_dir is None:
    output_dir = Path(".")
  else:
    output_dir = Path(output_dir)
  output_dir.mkdir(parents=True, exist_ok=True)

  # 외부 세션이 주입되면 그대로 사용, 아니면 새로 생성
  own_session = session is None
  if own_session:
    session = aiohttp.ClientSession()

  try:
    # 1. URL 유형 판별
    if "m3u8" in url:
      master_url = url
      print(f"[1/4] m3u8 URL 사용")
    elif "gdcvault.com" in url:
      print(f"[1/4] GDC Vault 페이지에서 m3u8 검색 중...")
      master_url = await fetch_page_for_m3u8(session, url)
    else:
      raise Exception("지원하지 않는 URL입니다. GDC Vault URL 또는 m3u8 URL을 입력하세요.")

    print(f"  마스터 URL: {master_url[:80]}...")

    # 2. 마스터 매니페스트 파싱
    print(f"[2/4] 매니페스트 파싱 중...")
    master_content = await fetch_text(session, master_url)
    subtitles = parse_master_manifest(master_content, master_url)

    if not subtitles:
      raise Exception(
        "자막 트랙을 찾을 수 없습니다.\n"
        "이 영상에는 자막이 제공되지 않을 수 있습니다."
      )

    print(f"  사용 가능한 자막: {', '.join(f'{LANG_MAP.get(k, k)} ({k})' for k in subtitles)}")

    if lang not in subtitles:
      available = ", ".join(subtitles.keys())
      raise Exception(f"'{lang}' 자막을 찾을 수 없습니다. 사용 가능: {available}")

    # 3. 자막 세그먼트 다운로드
    subtitle_url = subtitles[lang]
    print(f"  선택된 자막: {LANG_MAP.get(lang, lang)} ({lang})")

    subtitle_content = await fetch_text(session, subtitle_url)
    segment_urls = parse_subtitle_playlist(subtitle_content, subtitle_url)
    total = len(segment_urls)
    print(f"[3/4] 자막 세그먼트 {total}개 다운로드 중...")

    start_time = time.time()
    segments = await download_segments(session, segment_urls)
    elapsed = time.time() - start_time
    print(f"  다운로드 완료 ({elapsed:.1f}초)")

    # 4. VTT 파싱 및 병합
    print(f"[4/4] 트랜스크립트 생성 중...")
    all_entries = []
    for seg_content in segments:
      if seg_content:
        entries = parse_vtt_segment(seg_content)
        all_entries.extend(entries)

    merged = merge_entries(all_entries)
    print(f"  총 {len(merged)}개 자막 항목 추출")

    # 세션 ID 추출 (파일명/폴더용)
    if video_id is None:
      video_id = "gdc_transcript"
      id_match = re.search(r"/play/(\d+)", url)
      if id_match:
        video_id = f"gdc_{id_match.group(1)}"
      else:
        # m3u8 URL에서 첫 번째 ID 사용
        id_match = re.search(r"/out/v1/([a-f0-9]+)/", url)
        if id_match:
          video_id = f"gdc_{id_match.group(1)[:12]}"

    # ── 세션별 하위 폴더 ──
    safe_dir = re.sub(r"[^a-zA-Z0-9_-]", "", video_id) or "gdc_session"
    session_dir = output_dir / safe_dir
    session_dir.mkdir(parents=True, exist_ok=True)

    # 기본 파일 (언어 suffix 없이 단순 이름)
    # NotebookLM이 .vtt 확장자를 거절하므로 .txt로 저장.
    # 내용은 WebVTT 포맷 그대로 — 첫 줄 "WEBVTT" 로 포맷 식별 가능.
    vtt_path = session_dir / "subtitle.txt"
    vtt_path.write_text(format_vtt(merged), encoding="utf-8")
    print(f"  저장: {vtt_path}")

    ts_path = session_dir / "transcript_timed.txt"
    ts_path.write_text(format_timestamped_text(merged), encoding="utf-8")
    print(f"  저장: {ts_path}")

    txt_path = session_dir / "transcript.txt"
    plain_text = format_text(merged)
    txt_path.write_text(plain_text, encoding="utf-8")
    print(f"  저장: {txt_path}")

    wants_enhancement = any([
      include_chapters, include_glossary, include_keypoints, include_qa,
      include_articles, include_thumbnail,
    ])

    if not wants_enhancement:
      print(f"\n완료! {session_dir} 에 3개 파일이 저장되었습니다.")
      return txt_path

    # ── 확장: Claude + Perplexity 후처리 및 번들 생성 ──
    from config import AI_ENHANCE_ENABLED, WEB_CONTEXT_ENABLED

    # 영상 총 길이 (마지막 entry의 end 시각에서 초 단위)
    max_seconds = 0
    if merged:
      last_ts = merged[-1]["end"][:8]  # HH:MM:SS
      try:
        h, m, s = last_ts.split(":")
        max_seconds = int(h) * 3600 + int(m) * 60 + int(s)
      except ValueError:
        max_seconds = 0

    timed_text = format_timestamped_text(merged)

    enhancement = None
    needs_claude = include_chapters or include_glossary or include_keypoints or include_qa
    # 엔티티는 관련 기사 검색에도 필요하므로 articles 옵션 시 엔티티 추출
    needs_entities = include_articles

    if needs_claude or needs_entities:
      if AI_ENHANCE_ENABLED:
        print(f"[5/6] Claude 후처리 중 "
              f"(챕터={include_chapters}, 용어집={include_glossary}, "
              f"핵심포인트={include_keypoints}, QA={include_qa})")
        from ai_enhance import SessionContext, enhance_transcript
        ctx = None
        if detail is not None:
          ctx = SessionContext(
            title=getattr(detail, "title", "") or "",
            speakers=getattr(detail, "speakers", "") or "",
            company=getattr(detail, "company", "") or "",
            category=getattr(detail, "category", "") or "",
            year=getattr(detail, "year", "") or "",
            tags=list(getattr(detail, "tags", []) or []),
            overview=getattr(detail, "overview", "") or "",
          )
        try:
          enhancement = await enhance_transcript(
            transcript_plain=plain_text,
            transcript_timed=timed_text,
            max_seconds=max_seconds,
            context=ctx,
            include_chapters=include_chapters,
            include_glossary=include_glossary,
            include_keypoints=include_keypoints,
            include_qa=include_qa,
            include_entities=needs_entities,
          )
        except Exception as e:
          log.exception("Claude 후처리 실패: %s", e)
      else:
        print(f"[5/6] Claude 비활성화 또는 API 키 없음 — 스킵")

    related_md = ""
    if include_articles:
      if WEB_CONTEXT_ENABLED and detail is not None:
        print(f"[6/6] Perplexity로 관련 기사 검색 중...")
        from web_context import find_related_articles
        try:
          entities = enhancement.keypoint_entities if enhancement else []
          related_md = await find_related_articles(
            title=getattr(detail, "title", ""),
            speakers=getattr(detail, "speakers", ""),
            company=getattr(detail, "company", ""),
            year=getattr(detail, "year", ""),
            tags=getattr(detail, "tags", []) or [],
            entities=entities or [],
          )
        except Exception as e:
          log.exception("Perplexity 검색 실패: %s", e)
      else:
        if detail is None:
          log.info("detail이 없어 관련 기사 검색 건너뜀")
        else:
          log.info("Perplexity 비활성화 또는 API 키 없음 — 스킵")

    # 번들 생성: session_dir에 추가 파일을 쓰고 같은 이름의 ZIP을 부모 디렉토리에 생성
    from bundler import BundleInputs, build_bundle
    safe_id = safe_dir.replace("gdc_", "") or "session"
    inputs = BundleInputs(
      session_id=safe_id,
      session_dir=session_dir,
      title=getattr(detail, "title", "") if detail else "",
      play_url=getattr(detail, "play_url", "") if detail else "",
      speakers=getattr(detail, "speakers", "") if detail else "",
      company=getattr(detail, "company", "") if detail else "",
      category=getattr(detail, "category", "") if detail else "",
      overview=getattr(detail, "overview", "") if detail else "",
      tags=getattr(detail, "tags", []) if detail else [],
      year=getattr(detail, "year", "") if detail else "",
      vault_free=getattr(detail, "vault_free", False) if detail else False,
      thumbnail_url=getattr(detail, "thumbnail", "") if detail and include_thumbnail else "",
      main_txt_path=txt_path,
      chapters_md=enhancement.chapters_md if enhancement and include_chapters else "",
      glossary_md=enhancement.glossary_md if enhancement and include_glossary else "",
      keypoints_md=enhancement.keypoints_md if enhancement and include_keypoints else "",
      qa_md=enhancement.qa_md if enhancement and include_qa else "",
      related_articles_md=related_md,
    )
    zip_path = await build_bundle(inputs, aio_session=session)
    print(f"  번들 ZIP: {zip_path}")
    print(f"\n완료! NotebookLM에는 {zip_path.name}을 업로드(또는 언집 후 업로드)하세요.")
    return zip_path

  finally:
    # 직접 생성한 세션만 닫기
    if own_session and session and not session.closed:
      await session.close()


def main():
  parser = argparse.ArgumentParser(
    description="GDC Vault 자막 추출기",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="""
예시:
  python gdc_transcript.py https://gdcvault.com/play/1034837
  python gdc_transcript.py https://cdn-a.blazestreaming.com/.../index.m3u8
  python gdc_transcript.py https://gdcvault.com/play/1034837 --lang jpn
  python gdc_transcript.py https://gdcvault.com/play/1034837 -o ./transcripts
    """,
  )
  parser.add_argument("url", help="GDC Vault URL 또는 m3u8 URL")
  parser.add_argument(
    "--lang",
    default="eng",
    choices=["eng", "spa", "zho", "jpn"],
    help="자막 언어 (기본: eng)",
  )
  parser.add_argument(
    "-o", "--output",
    default="./transcripts",
    help="출력 디렉토리 (기본: ./transcripts)",
  )
  parser.add_argument(
    "--enhance",
    action="store_true",
    help="--chapters --glossary --keypoints --qa --articles --thumbnail 를 한 번에 켜는 단축 옵션",
  )
  parser.add_argument("--chapters", action="store_true", help="Claude로 챕터 생성")
  parser.add_argument("--glossary", action="store_true", help="Claude로 용어집 생성")
  parser.add_argument("--keypoints", action="store_true", help="Claude로 핵심 포인트 생성")
  parser.add_argument("--qa", action="store_true", help="Claude로 Q&A 섹션 추출")
  parser.add_argument("--articles", action="store_true", help="Perplexity로 관련 기사 검색")
  parser.add_argument("--thumbnail", action="store_true", help="세션 썸네일 저장")
  args = parser.parse_args()
  if args.enhance:
    args.chapters = True
    args.glossary = True
    args.keypoints = True
    args.qa = True
    args.articles = True
    args.thumbnail = True

  logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
  )

  print("=" * 50)
  print("GDC Vault 자막 추출기")
  print("=" * 50)

  asyncio.run(_cli_run(args))


async def _cli_run(args):
  """CLI 실행. GDC Vault URL이면 scraper로 detail을 먼저 가져옵니다."""
  any_enhance = any([
    args.chapters, args.glossary, args.keypoints, args.qa,
    args.articles, args.thumbnail,
  ])
  async with aiohttp.ClientSession() as session:
    detail = None
    if any_enhance and "gdcvault.com" in args.url:
      try:
        from scraper import GDCScraper
        scraper = GDCScraper(session)
        detail = await scraper.get_session_detail(args.url)
        log.info("세션 메타데이터 로드: %s", detail.title or "(제목 없음)")
      except Exception as e:
        log.warning("메타데이터 로드 실패 (메타 헤더 없이 진행): %s", e)
    await extract_transcript(
      url=args.url,
      lang=args.lang,
      output_dir=args.output,
      session=session,
      detail=detail,
      include_chapters=args.chapters,
      include_glossary=args.glossary,
      include_keypoints=args.keypoints,
      include_qa=args.qa,
      include_articles=args.articles,
      include_thumbnail=args.thumbnail,
    )


if __name__ == "__main__":
  main()
