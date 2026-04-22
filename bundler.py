"""
NotebookLM용 세션 번들 생성

이미 session_dir에 존재하는 transcript/vtt에 더해 meta/chapters/glossary/keypoints/
related_articles/thumbnail을 추가 저장하고, 세션 폴더 전체를 ZIP으로 묶는다.
"""

import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

import aiohttp

log = logging.getLogger(__name__)


@dataclass
class BundleInputs:
  """build_bundle에 넘길 데이터 묶음"""
  session_id: str
  session_dir: Path  # 이미 기본 파일(transcript.txt, subtitle.txt 등)이 있는 폴더
  # SessionDetail 필드들 (bundler는 scraper에 의존하지 않기 위해 평문으로 받음)
  title: str = ""
  play_url: str = ""
  speakers: str = ""
  company: str = ""
  category: str = ""
  overview: str = ""
  tags: list[str] | None = None
  year: str = ""
  vault_free: bool = False
  thumbnail_url: str = ""
  # main transcript는 메타 헤더를 prepend 하기 위해 경로 필요
  main_txt_path: Path | None = None
  # 후처리 결과 (Markdown 문자열; 빈 문자열이면 파일 생성 스킵)
  chapters_md: str = ""
  glossary_md: str = ""
  keypoints_md: str = ""
  qa_md: str = ""
  design_brief_md: str = ""
  related_articles_md: str = ""


def _format_meta_md(b: BundleInputs) -> str:
  tags_str = ", ".join(b.tags) if b.tags else ""
  overview = b.overview.strip() if b.overview else "*(세션 개요 정보 없음)*"
  access = "Vault Free" if b.vault_free else "Vault Members"

  lines = [
    f"# {b.title or 'Untitled GDC Session'}",
    "",
    "## Session Metadata",
    "",
    f"- **Session ID:** {b.session_id}",
  ]
  if b.year:
    lines.append(f"- **Conference:** GDC {b.year}")
  if b.speakers:
    lines.append(f"- **Speakers:** {b.speakers}")
  if b.company:
    lines.append(f"- **Company:** {b.company}")
  if b.category:
    lines.append(f"- **Track / Format:** {b.category}")
  if tags_str:
    lines.append(f"- **Tags:** {tags_str}")
  if b.play_url:
    lines.append(f"- **Source URL:** {b.play_url}")
  lines.append(f"- **Access:** {access}")
  lines.append("")
  lines.append("## Overview")
  lines.append("")
  lines.append(overview)
  lines.append("")
  return "\n".join(lines)


def _format_main_txt_header(b: BundleInputs) -> str:
  """transcript.txt 상단에 붙일 메타 헤더 (NotebookLM이 본문 첫 문단으로 학습)"""
  header_lines = []
  if b.title:
    header_lines.append(f"Session: {b.title}")
  if b.year:
    header_lines.append(f"Conference: GDC {b.year}")
  if b.speakers:
    header_lines.append(f"Speakers: {b.speakers}")
  if b.company:
    header_lines.append(f"Company: {b.company}")
  if b.category:
    header_lines.append(f"Track: {b.category}")
  if b.tags:
    header_lines.append(f"Tags: {', '.join(b.tags)}")
  if b.play_url:
    header_lines.append(f"Source: {b.play_url}")
  if b.overview:
    header_lines.append("")
    header_lines.append("Overview:")
    header_lines.append(b.overview.strip())
  if header_lines:
    header_lines.append("")
    header_lines.append("---")
    header_lines.append("")
    return "\n".join(header_lines)
  return ""


async def _download_thumbnail(
  url: str, dest: Path, session: aiohttp.ClientSession
) -> bool:
  """썸네일 다운로드. 성공 시 True."""
  if not url:
    return False
  try:
    async with session.get(url, timeout=30) as resp:
      if resp.status != 200:
        log.warning("썸네일 다운로드 실패 (HTTP %d): %s", resp.status, url)
        return False
      data = await resp.read()
      dest.write_bytes(data)
      log.info("썸네일 저장: %s (%d bytes)", dest, len(data))
      return True
  except (aiohttp.ClientError, Exception) as e:
    log.warning("썸네일 다운로드 예외: %s", e)
    return False


def _prepend_header(path: Path, header: str) -> None:
  """transcript.txt 파일 맨 앞에 헤더 prepend (중복 방지)"""
  if not path or not path.exists() or not header:
    return
  original = path.read_text(encoding="utf-8")
  if original.lstrip().startswith("Session:"):
    return  # 이미 헤더가 있음
  path.write_text(header + original, encoding="utf-8")


async def build_bundle(
  inputs: BundleInputs,
  aio_session: aiohttp.ClientSession | None = None,
) -> Path:
  """세션 폴더에 추가 파일을 쓰고 폴더 전체를 ZIP으로 묶는다.

  반환: ZIP 파일 경로 (session_dir.parent / '{session_dir.name}.zip')
  """
  session_dir = Path(inputs.session_dir)
  session_dir.mkdir(parents=True, exist_ok=True)

  # 1) transcript.txt에 메타 헤더 prepend (이미 존재하는 경우에만)
  _prepend_header(inputs.main_txt_path, _format_main_txt_header(inputs))

  # 2) meta.md (항상 생성 — 메타데이터는 기본 구성 요소)
  (session_dir / "meta.md").write_text(_format_meta_md(inputs), encoding="utf-8")

  # 3) AI 후처리 결과물 (빈 문자열이면 skip)
  if inputs.chapters_md.strip():
    (session_dir / "chapters.md").write_text(inputs.chapters_md, encoding="utf-8")
  if inputs.glossary_md.strip():
    (session_dir / "glossary.md").write_text(inputs.glossary_md, encoding="utf-8")
  if inputs.keypoints_md.strip():
    (session_dir / "keypoints.md").write_text(inputs.keypoints_md, encoding="utf-8")
  qa_text = inputs.qa_md.strip()
  if qa_text and qa_text != "No Q&A section detected":
    (session_dir / "qa.md").write_text(inputs.qa_md, encoding="utf-8")
  if inputs.design_brief_md.strip():
    (session_dir / "design_brief.md").write_text(
      inputs.design_brief_md, encoding="utf-8"
    )
  if inputs.related_articles_md.strip():
    (session_dir / "related_articles.md").write_text(
      inputs.related_articles_md, encoding="utf-8"
    )

  # 4) 썸네일
  if inputs.thumbnail_url:
    thumb_path = session_dir / "thumbnail.jpg"
    if aio_session is not None:
      await _download_thumbnail(inputs.thumbnail_url, thumb_path, aio_session)
    else:
      async with aiohttp.ClientSession() as s:
        await _download_thumbnail(inputs.thumbnail_url, thumb_path, s)

  # 5) ZIP 묶기 (세션 폴더 전체를 부모 디렉토리의 {session_dir.name}.zip으로)
  zip_path = session_dir.parent / f"{session_dir.name}.zip"
  # 기존 ZIP은 덮어씀
  with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
    for file in sorted(session_dir.iterdir()):
      if file.is_file():
        zf.write(file, arcname=f"{session_dir.name}/{file.name}")

  log.info("번들 ZIP 생성: %s (파일 %d개)",
           zip_path, sum(1 for f in session_dir.iterdir() if f.is_file()))
  return zip_path
