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


async def extract_transcript(url, lang="eng", output_dir=None, session=None, video_id=None):
  """메인 추출 함수

  Args:
    url: GDC Vault URL 또는 m3u8 URL
    lang: 자막 언어 코드
    output_dir: 출력 디렉토리
    session: 외부에서 주입할 aiohttp.ClientSession (None이면 새로 생성)
    video_id: 파일명에 사용할 ID (None이면 URL에서 추출)
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

    # 영상 ID 추출 (파일명용)
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

    # 파일 출력
    # VTT 파일
    vtt_path = output_dir / f"{video_id}_{lang}.vtt"
    vtt_path.write_text(format_vtt(merged), encoding="utf-8")
    print(f"  저장: {vtt_path}")

    # 타임스탬프 포함 텍스트
    ts_path = output_dir / f"{video_id}_{lang}_timestamped.txt"
    ts_path.write_text(format_timestamped_text(merged), encoding="utf-8")
    print(f"  저장: {ts_path}")

    # 깔끔한 텍스트 (NotebookLM용)
    txt_path = output_dir / f"{video_id}_{lang}.txt"
    txt_path.write_text(format_text(merged), encoding="utf-8")
    print(f"  저장: {txt_path}")

    print(f"\n완료! NotebookLM에는 {txt_path.name} 파일을 업로드하세요.")
    return txt_path

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
  args = parser.parse_args()

  print("=" * 50)
  print("GDC Vault 자막 추출기")
  print("=" * 50)

  asyncio.run(extract_transcript(args.url, args.lang, args.output))


if __name__ == "__main__":
  main()
