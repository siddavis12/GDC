"""
Claude API 기반 transcript 후처리

GDC Vault 세션 자막을 받아 챕터, 용어집, 핵심 포인트, Q&A를 생성한다.
prompt caching으로 동일 transcript의 다중 호출 비용을 절감한다.

품질 개선 요소:
  - SessionContext 앵커링: 세션 제목/화자/회사/태그/개요를 캐시 마킹된 시스템 블록으로
    주입하여 Claude가 ASR 오인식 고유명사를 교정할 수 있도록 힌트를 제공한다.
  - 일관성 정규화 패스: 4개 병렬 호출 결과의 고유명사 철자를 순차 1회 호출로 통일한다.
  - Q&A 분리 추출: 청중 Q&A 섹션을 별도로 구조화하여 메인 강연 keypoints와 분리한다.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field

from anthropic import AsyncAnthropic

from config import (
  AI_MODEL,
  AI_NORMALIZE_ENABLED,
  AI_QA_ENABLED,
  ANTHROPIC_API_KEY,
  MAX_CONCURRENT_CLAUDE,
)

log = logging.getLogger(__name__)


@dataclass
class SessionContext:
  """Claude에 주입할 세션 메타데이터.

  Claude는 이 정보를 authoritative proper-noun source로 취급하여
  transcript의 ASR 오인식을 교정한다.
  """
  title: str = ""
  speakers: str = ""
  company: str = ""
  category: str = ""
  year: str = ""
  tags: list[str] = field(default_factory=list)
  overview: str = ""

  def as_prompt_block(self) -> str:
    """프롬프트에 삽입할 <session_context> XML 블록."""
    lines = ["<session_context>"]
    if self.title:
      lines.append(f"Talk title: {self.title}")
    if self.speakers:
      lines.append(f"Speaker(s): {self.speakers}")
    if self.company:
      lines.append(f"Company/affiliation: {self.company}")
    if self.year:
      lines.append(f"Conference: GDC {self.year}")
    if self.category:
      lines.append(f"Track: {self.category}")
    if self.tags:
      lines.append(f"Tags: {', '.join(self.tags)}")
    if self.overview:
      lines.append("")
      lines.append("Session overview:")
      lines.append(self.overview.strip())
    lines.append("</session_context>")
    return "\n".join(lines)


@dataclass
class EnhancementResult:
  """Claude 후처리 결과 묶음"""
  chapters_md: str = ""
  glossary_md: str = ""
  keypoints_md: str = ""
  qa_md: str = ""
  keypoint_entities: list[str] = field(default_factory=list)
  usage_input: int = 0
  usage_output: int = 0
  usage_cache_read: int = 0


SYSTEM_BASE = (
  "You are an expert analyst summarizing Game Developers Conference (GDC) talks "
  "for a research notebook. Be precise, factual, and preserve the speaker's "
  "original terminology. Respond with clean Markdown only — no preamble, no "
  "meta-commentary. Never invent information that is not present in the transcript.\n\n"
  "CRITICAL — PROPER NOUN NORMALIZATION:\n"
  "The transcript was produced by automatic speech recognition and contains "
  "phonetic mis-transcriptions of names. The <session_context> block (if present) "
  "lists the AUTHORITATIVE spellings of the talk title, speaker name, company, "
  "and key entities. For every proper noun you mention in your output:\n"
  "  1. Check if it refers to an entity named in <session_context>. If yes, use "
  "the context spelling verbatim (including hyphens, capitalization, punctuation).\n"
  "  2. Common ASR mis-transcriptions to correct: 'Balachot'/'Ballad Show'/"
  "'Bellatro'/'Balladtrie'/'Balletrae' → 'Balatro'; 'Localfunk' → 'LocalThunk'; "
  "'Smith-Bode' → 'Smith-Bodie' (if the session context says 'Smith-Bodie').\n"
  "  3. When the speaker says 'I am Emma Smith-Bode', that is an ASR error of "
  "the speaker's actual name from the session context — use the context spelling.\n"
  "  4. For well-known games/studios you can identify with high confidence, use "
  "their canonical spelling even if absent from <session_context>.\n"
  "  5. Never invent names; only correct clear ASR variants."
)


def _time_to_seconds(ts: str) -> int:
  """HH:MM:SS 또는 MM:SS → seconds"""
  parts = ts.split(":")
  try:
    if len(parts) == 3:
      h, m, s = parts
      return int(h) * 3600 + int(m) * 60 + int(float(s))
    if len(parts) == 2:
      m, s = parts
      return int(m) * 60 + int(float(s))
  except ValueError:
    return 0
  return 0


def _validate_chapter_timestamps(chapters_md: str, max_seconds: int) -> str:
  """챕터 타임스탬프가 VTT 총 길이를 넘으면 경고 로그."""
  if max_seconds <= 0:
    return chapters_md
  bad = []
  for m in re.finditer(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]", chapters_md):
    if _time_to_seconds(m.group(1)) > max_seconds + 60:
      bad.append(m.group(1))
  if bad:
    log.warning("챕터 타임스탬프 범위 초과: %s (영상 길이 %ds)", bad, max_seconds)
  return chapters_md


def _cached_transcript_block(transcript: str) -> list[dict]:
  """transcript를 ephemeral cache로 마킹한 메시지 블록."""
  return [
    {
      "type": "text",
      "text": "<transcript>\n" + transcript + "\n</transcript>",
      "cache_control": {"type": "ephemeral"},
    }
  ]


def _build_system_blocks(
  per_task_system: str,
  context: SessionContext | None,
) -> list[dict]:
  """SYSTEM_BASE → session_context → per-task system 순의 system 배열.

  session_context 블록은 cache_control 로 마킹되어 병렬 호출 간 캐시 재사용.
  """
  blocks: list[dict] = [
    {"type": "text", "text": SYSTEM_BASE},
  ]
  if context is not None:
    ctx_text = context.as_prompt_block()
    if ctx_text.strip():
      blocks.append({
        "type": "text",
        "text": ctx_text,
        "cache_control": {"type": "ephemeral"},
      })
  blocks.append({
    "type": "text",
    "text": per_task_system,
    "cache_control": {"type": "ephemeral"},
  })
  return blocks


async def _call_claude(
  client: AsyncAnthropic,
  system: str,
  transcript: str,
  instruction: str,
  context: SessionContext | None = None,
  max_tokens: int = 2048,
) -> tuple[str, dict]:
  """단일 Claude 호출. (응답 텍스트, usage dict) 반환"""
  msg = await client.messages.create(
    model=AI_MODEL,
    max_tokens=max_tokens,
    system=_build_system_blocks(system, context),
    messages=[
      {
        "role": "user",
        "content": _cached_transcript_block(transcript) + [
          {"type": "text", "text": instruction}
        ],
      }
    ],
  )
  text = "".join(
    block.text for block in msg.content if getattr(block, "type", "") == "text"
  ).strip()
  usage = {
    "input": getattr(msg.usage, "input_tokens", 0),
    "output": getattr(msg.usage, "output_tokens", 0),
    "cache_read": getattr(msg.usage, "cache_read_input_tokens", 0) or 0,
    "cache_write": getattr(msg.usage, "cache_creation_input_tokens", 0) or 0,
  }
  return text, usage


CHAPTERS_SYSTEM = (
  "Given a timestamped GDC talk transcript, produce 5 to 10 chapter markers that "
  "reflect the talk's logical flow. Each chapter MUST use a timestamp that appears "
  "in the transcript. Format each chapter as a Markdown list item:\n"
  "- `[HH:MM:SS] Chapter Title` — one or two sentence summary in English.\n"
  "The first chapter must start at the first timestamp in the transcript."
)

GLOSSARY_SYSTEM = (
  "Extract proper nouns from the GDC talk: game titles, studios, engines, tools, "
  "frameworks, people, acronyms, and domain-specific jargon. For each term provide "
  "a concise definition (1–2 sentences) and briefly note why it matters in this "
  "talk. Exclude generic terms. Produce 8–20 entries ordered alphabetically.\n"
  "Format as a Markdown definition list:\n"
  "**Term** — Definition. Relevance to this talk."
)

KEYPOINTS_SYSTEM = (
  "Identify the 3–5 central claims the speaker makes in this GDC talk. For each "
  "claim, capture the supporting evidence the speaker cites (numbers, examples, "
  "anecdotes, comparisons). Do not add outside commentary. Format:\n"
  "## Key Point N — <short claim>\n"
  "**Claim:** <one sentence restating the claim>.\n"
  "**Evidence:** <bulleted list of supporting facts from the transcript>."
)

QA_SYSTEM = (
  "Identify the Q&A section at the end of this GDC talk. The Q&A typically begins "
  "after the speaker finishes the prepared talk and invites audience questions "
  "(phrases like 'any questions?', 'thank you', 'microphones in the aisles'). "
  "For each audience question followed by the speaker's answer, extract the "
  "exchange. Paraphrase the question for clarity and summarize the speaker's "
  "answer in 2–3 sentences. If no Q&A section exists, return exactly: "
  "No Q&A section detected.\n"
  "Format each exchange as:\n"
  "### Q<N>: <question paraphrased in one line>\n"
  "<speaker's answer summarized in 2-3 sentences>\n\n"
  "Use proper-noun spellings from <session_context> when applicable."
)

ENTITY_INSTRUCTION = (
  "List the most important proper nouns mentioned in this GDC talk that would help "
  "a researcher find related news articles: specific game titles, studio names, "
  "tool/engine names, and project codenames. Exclude common words and the GDC "
  "event itself. Return 5–10 terms as a plain comma-separated list on a single "
  "line. No explanations. Use canonical spellings from <session_context> if the "
  "transcript contains variants."
)


async def _extract_entities(
  client: AsyncAnthropic,
  transcript: str,
  context: SessionContext | None,
) -> tuple[list[str], dict]:
  text, usage = await _call_claude(
    client,
    system=(
      "You identify named entities from GDC talks for downstream web search. "
      "Respond with a single comma-separated line, no Markdown, no prose."
    ),
    transcript=transcript,
    instruction=ENTITY_INSTRUCTION,
    context=context,
    max_tokens=256,
  )
  raw = text.splitlines()[0] if text else ""
  entities = [e.strip(" .\"'") for e in raw.split(",")]
  entities = [e for e in entities if 2 <= len(e) <= 60]
  return entities[:10], usage


_GLOSSARY_TERM_RE = re.compile(r"^\s*\*\*([^*]+?)\*\*", re.MULTILINE)


def _extract_canonical_names(
  glossary_md: str,
  context: SessionContext | None,
  entities: list[str],
) -> list[str]:
  """glossary + context + entities 에서 캐노니컬 고유명사 리스트 구성.
  중복 제거, 순서는 발견 순."""
  names: list[str] = []
  seen: set[str] = set()

  def add(n: str) -> None:
    n = n.strip()
    if not n or len(n) < 2 or len(n) > 80:
      return
    key = n.lower()
    if key in seen:
      return
    seen.add(key)
    names.append(n)

  if context:
    if context.speakers:
      for s in re.split(r",|&| and ", context.speakers):
        add(s)
    if context.company:
      add(context.company)
    # title 에서 따옴표로 감싼 게임명 추출
    if context.title:
      for m in re.finditer(r"['\"]([^'\"]{2,40})['\"]", context.title):
        add(m.group(1))

  for m in _GLOSSARY_TERM_RE.finditer(glossary_md or ""):
    add(m.group(1))

  for e in entities or []:
    add(e)

  return names


NORMALIZE_SYSTEM = (
  "You are a proof-reader harmonizing proper-noun spellings across multiple "
  "Markdown documents derived from the same GDC talk. You will receive a "
  "canonical name list and three Markdown blocks. Rewrite each block using ONLY "
  "the canonical spellings from the list when they refer to the same entity. "
  "Preserve all other content, structure, and formatting exactly. Do not add "
  "or remove bullets, headings, or sentences. Only fix proper-noun spellings."
)

_NORMALIZE_DELIM = {
  "chapters": "<<<CHAPTERS>>>",
  "glossary": "<<<GLOSSARY>>>",
  "keypoints": "<<<KEYPOINTS>>>",
  "qa": "<<<QA>>>",
  "end": "<<<END>>>",
}


async def _normalize_outputs(
  client: AsyncAnthropic,
  context: SessionContext | None,
  canonical_names: list[str],
  chapters_md: str,
  glossary_md: str,
  keypoints_md: str,
  qa_md: str,
) -> tuple[str, str, str, str, dict]:
  """4개 결과를 1회 호출로 일관성 교정. 실패 시 원본 그대로 반환."""
  if not canonical_names:
    return chapters_md, glossary_md, keypoints_md, qa_md, {
      "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
    }

  names_block = "\n".join(f"- {n}" for n in canonical_names)
  qa_section = ""
  if qa_md and qa_md.strip() and qa_md.strip() != "No Q&A section detected":
    qa_section = (
      f"\n{_NORMALIZE_DELIM['qa']}\n{qa_md.strip()}\n"
    )

  instruction = (
    "Canonical name list (use these spellings exactly when referring to these entities):\n"
    f"{names_block}\n\n"
    "Rewrite each of the following Markdown blocks. Return them in the same order, "
    f"each preceded by its delimiter line and terminated by {_NORMALIZE_DELIM['end']}. "
    "Do NOT include any other text.\n\n"
    f"{_NORMALIZE_DELIM['chapters']}\n{chapters_md.strip()}\n\n"
    f"{_NORMALIZE_DELIM['glossary']}\n{glossary_md.strip()}\n\n"
    f"{_NORMALIZE_DELIM['keypoints']}\n{keypoints_md.strip()}\n"
    f"{qa_section}"
    f"\n{_NORMALIZE_DELIM['end']}"
  )

  try:
    text, usage = await _call_claude(
      client,
      system=NORMALIZE_SYSTEM,
      transcript="(no transcript needed — operating on derived Markdown only)",
      instruction=instruction,
      context=context,
      max_tokens=6000,
    )
  except Exception as e:
    log.warning("정규화 패스 실패, 원본 사용: %s", e)
    return chapters_md, glossary_md, keypoints_md, qa_md, {
      "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
    }

  parsed = _parse_normalize_response(text, qa_section != "")
  if parsed is None:
    log.warning("정규화 응답 파싱 실패, 원본 사용")
    return chapters_md, glossary_md, keypoints_md, qa_md, usage

  new_chapters, new_glossary, new_keypoints, new_qa = parsed
  return (
    new_chapters or chapters_md,
    new_glossary or glossary_md,
    new_keypoints or keypoints_md,
    new_qa or qa_md,
    usage,
  )


def _parse_normalize_response(
  text: str, has_qa: bool,
) -> tuple[str, str, str, str] | None:
  """정규화 응답에서 4개 섹션 추출."""
  d = _NORMALIZE_DELIM
  try:
    # <<<END>>> 전까지 취득
    if d["end"] in text:
      text = text.split(d["end"], 1)[0]

    def _between(start: str, end_options: list[str]) -> str:
      if start not in text:
        return ""
      after = text.split(start, 1)[1]
      for e in end_options:
        if e in after:
          after = after.split(e, 1)[0]
          break
      return after.strip()

    chapters = _between(d["chapters"], [d["glossary"], d["keypoints"], d["qa"]])
    glossary = _between(d["glossary"], [d["keypoints"], d["qa"]])
    keypoints = _between(d["keypoints"], [d["qa"]])
    if has_qa:
      qa = _between(d["qa"], [])
    else:
      qa = ""

    # 최소 하나는 비어있지 않아야 유효
    if not any([chapters, glossary, keypoints, qa]):
      return None
    return chapters, glossary, keypoints, qa
  except Exception as e:
    log.warning("정규화 파싱 예외: %s", e)
    return None


async def enhance_transcript(
  transcript_plain: str,
  transcript_timed: str,
  max_seconds: int = 0,
  context: SessionContext | None = None,
  include_chapters: bool = True,
  include_glossary: bool = True,
  include_keypoints: bool = True,
  include_entities: bool = True,
  include_qa: bool = True,
  normalize: bool | None = None,
) -> EnhancementResult:
  """transcript에 대해 Claude 후처리를 선택적으로 병렬 실행.

  Args:
    transcript_plain: 타임스탬프 없는 정리본 (용어집/핵심포인트/엔티티/Q&A용)
    transcript_timed: 타임스탬프 포함본 (챕터용)
    max_seconds: VTT 총 길이 (챕터 타임스탬프 검증)
    context: 세션 메타데이터 — Claude가 ASR 교정에 활용
    include_*: 각 생성 작업 개별 on/off. 모두 False면 빈 결과 반환.
    normalize: 일관성 정규화 후처리 사용 여부. None이면 AI_NORMALIZE_ENABLED 사용.
  """
  if not ANTHROPIC_API_KEY:
    log.warning("ANTHROPIC_API_KEY가 없어 enhance 단계를 건너뜁니다.")
    return EnhancementResult()

  if include_qa and not AI_QA_ENABLED:
    include_qa = False

  requested = [
    include_chapters, include_glossary, include_keypoints,
    include_entities, include_qa,
  ]
  if not any(requested):
    return EnhancementResult()

  client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
  sem = asyncio.Semaphore(MAX_CONCURRENT_CLAUDE)

  async def guarded(coro):
    async with sem:
      return await coro

  tasks: dict[str, asyncio.Task] = {}
  if include_chapters:
    tasks["chapters"] = asyncio.create_task(guarded(_call_claude(
      client, CHAPTERS_SYSTEM, transcript_timed,
      "Produce the chapter markers now.",
      context=context, max_tokens=2048,
    )))
  if include_glossary:
    tasks["glossary"] = asyncio.create_task(guarded(_call_claude(
      client, GLOSSARY_SYSTEM, transcript_plain,
      "Produce the glossary now.",
      context=context, max_tokens=3000,
    )))
  if include_keypoints:
    tasks["keypoints"] = asyncio.create_task(guarded(_call_claude(
      client, KEYPOINTS_SYSTEM, transcript_plain,
      "Produce the key points now.",
      context=context, max_tokens=2048,
    )))
  if include_qa:
    tasks["qa"] = asyncio.create_task(guarded(_call_claude(
      client, QA_SYSTEM, transcript_plain,
      "Produce the Q&A extraction now.",
      context=context, max_tokens=2500,
    )))
  if include_entities:
    tasks["entities"] = asyncio.create_task(guarded(
      _extract_entities(client, transcript_plain, context)
    ))

  usages = []
  # return_exceptions=True: 한 태스크가 실패해도 다른 태스크들이 cancel되지 않고 완주
  gather_results = await asyncio.gather(*tasks.values(), return_exceptions=True)
  for key, result in zip(tasks.keys(), gather_results):
    if isinstance(result, Exception):
      log.warning("Claude 병렬 호출 실패 (%s): %s", key, result)
  log.info(
    "Claude 병렬 호출 결과: 성공=%d/%d",
    sum(1 for r in gather_results if not isinstance(r, Exception)),
    len(gather_results),
  )

  def _unwrap_call(key: str) -> tuple[str, dict]:
    if key not in tasks:
      return "", {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    try:
      return tasks[key].result()
    except Exception as e:
      log.warning("%s 태스크 실패: %s", key, e)
      return "", {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

  def _unwrap_entities() -> tuple[list[str], dict]:
    if "entities" not in tasks:
      return [], {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    try:
      return tasks["entities"].result()
    except Exception as e:
      log.warning("entities 태스크 실패: %s", e)
      return [], {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

  chapters_text, chapters_usage = _unwrap_call("chapters")
  glossary_text, glossary_usage = _unwrap_call("glossary")
  keypoints_text, keypoints_usage = _unwrap_call("keypoints")
  qa_text, qa_usage = _unwrap_call("qa")
  entity_list, entity_usage = _unwrap_entities()
  usages = [chapters_usage, glossary_usage, keypoints_usage, qa_usage, entity_usage]

  if chapters_text:
    chapters_text = _validate_chapter_timestamps(chapters_text, max_seconds)

  # 일관성 정규화 후처리
  do_normalize = AI_NORMALIZE_ENABLED if normalize is None else normalize
  have_enough_to_normalize = any([chapters_text, glossary_text, keypoints_text, qa_text])
  log.info(
    "정규화 판정: AI_NORMALIZE_ENABLED=%s, normalize=%s → do_normalize=%s, "
    "have_enough=%s (chapters=%d, glossary=%d, keypoints=%d, qa=%d)",
    AI_NORMALIZE_ENABLED, normalize, do_normalize, have_enough_to_normalize,
    len(chapters_text), len(glossary_text), len(keypoints_text), len(qa_text),
  )
  if do_normalize and have_enough_to_normalize:
    canonical = _extract_canonical_names(glossary_text, context, entity_list)
    log.info("정규화 진입: canonical names (%d) = %s",
             len(canonical), canonical[:12])
    try:
      chapters_text, glossary_text, keypoints_text, qa_text, norm_usage = (
        await _normalize_outputs(
          client, context, canonical,
          chapters_text, glossary_text, keypoints_text, qa_text,
        )
      )
      usages.append(norm_usage)
      log.info(
        "정규화 패스 완료 (canonical=%d names, usage input=%d output=%d)",
        len(canonical), norm_usage.get("input", 0), norm_usage.get("output", 0),
      )
    except Exception as e:
      log.warning("정규화 실행 실패, 스킵: %s", e)

  try:
    await client.close()
  except Exception:
    pass

  total_in = sum(u["input"] for u in usages)
  total_out = sum(u["output"] for u in usages)
  total_cache_read = sum(u["cache_read"] for u in usages)
  log.info(
    "Claude usage: input=%d, output=%d, cache_read=%d (세션당 합계)",
    total_in, total_out, total_cache_read,
  )

  return EnhancementResult(
    chapters_md=chapters_text,
    glossary_md=glossary_text,
    keypoints_md=keypoints_text,
    qa_md=qa_text,
    keypoint_entities=entity_list,
    usage_input=total_in,
    usage_output=total_out,
    usage_cache_read=total_cache_read,
  )
