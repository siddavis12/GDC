/**
 * GDC Vault 브라우저 — 프론트엔드 스크립트
 */

document.addEventListener("DOMContentLoaded", () => {
  // 세션 상세 다이얼로그
  const dialog = document.getElementById("session-detail")
  if (!dialog) return

  const titleEl = document.getElementById("detail-title")
  const bodyEl = document.getElementById("detail-body")

  // 세션 카드 클릭 → 상세 로드
  document.querySelectorAll(".session-card a").forEach(link => {
    link.addEventListener("click", async (e) => {
      e.preventDefault()
      const href = link.getAttribute("href")
      dialog.showModal()
      titleEl.textContent = "로딩 중..."
      bodyEl.innerHTML = '<p aria-busy="true">세션 정보를 불러오는 중...</p>'

      try {
        const resp = await fetch(href, {
          headers: { "Accept": "application/json" }
        })
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const data = await resp.json()
        renderDetail(data)
      } catch (err) {
        bodyEl.innerHTML = `<p class="error">불러오기 실패: ${err.message}</p>`
      }
    })
  })

  // 닫기 버튼
  dialog.querySelectorAll(".close-detail").forEach(btn => {
    btn.addEventListener("click", () => dialog.close())
  })

  // 다이얼로그 외부 클릭으로 닫기
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) dialog.close()
  })

  function renderDetail(data) {
    titleEl.textContent = data.title || "제목 없음"

    // 컨테이너 초기화
    bodyEl.innerHTML = ""

    // ── 메타데이터 (dl: definition list) ──
    const metaItems = []
    if (data.speakers) metaItems.push(["Speaker(s)", data.speakers])
    if (data.company) metaItems.push(["Company", data.company])
    if (data.category) metaItems.push(["Track / Format", data.category])
    if (data.year) metaItems.push(["Year", data.year])

    if (metaItems.length > 0) {
      const dl = document.createElement("dl")
      dl.className = "detail-meta"
      metaItems.forEach(([label, value]) => {
        const dt = document.createElement("dt")
        dt.textContent = label
        const dd = document.createElement("dd")
        dd.textContent = value
        dl.appendChild(dt)
        dl.appendChild(dd)
      })
      bodyEl.appendChild(dl)
    }

    // ── Overview (원문 + 번역 탭) ──
    if (data.overview) {
      const section = document.createElement("div")
      section.className = "detail-overview"

      const header = document.createElement("div")
      header.className = "overview-header"
      const heading = document.createElement("h4")
      heading.textContent = "Overview"
      header.appendChild(heading)

      // 번역이 있으면 탭 버튼 표시
      if (data.overview_ko) {
        const tabs = document.createElement("div")
        tabs.className = "overview-tabs"
        const btnEn = document.createElement("button")
        btnEn.textContent = "EN"
        btnEn.className = "overview-tab active"
        const btnKo = document.createElement("button")
        btnKo.textContent = "KO"
        btnKo.className = "overview-tab"
        tabs.appendChild(btnEn)
        tabs.appendChild(btnKo)
        header.appendChild(tabs)

        const bodyEn = document.createElement("p")
        bodyEn.textContent = data.overview
        bodyEn.className = "overview-text"
        const bodyKo = document.createElement("p")
        bodyKo.textContent = data.overview_ko
        bodyKo.className = "overview-text"
        bodyKo.style.display = "none"

        btnEn.addEventListener("click", () => {
          titleEl.textContent = data.title || "제목 없음"
          bodyEn.style.display = ""
          bodyKo.style.display = "none"
          btnEn.classList.add("active")
          btnKo.classList.remove("active")
        })
        btnKo.addEventListener("click", () => {
          titleEl.textContent = data.title_ko || data.title || "제목 없음"
          bodyEn.style.display = "none"
          bodyKo.style.display = ""
          btnKo.classList.add("active")
          btnEn.classList.remove("active")
        })

        section.appendChild(header)
        section.appendChild(bodyEn)
        section.appendChild(bodyKo)
      } else {
        section.appendChild(header)
        const body = document.createElement("p")
        body.textContent = data.overview
        body.className = "overview-text"
        section.appendChild(body)
      }

      bodyEl.appendChild(section)
    }

    // ── 태그 (컬러 pill) ──
    if (data.tags && data.tags.length > 0) {
      const tagWrap = document.createElement("div")
      tagWrap.className = "detail-tags"
      data.tags.forEach((t, i) => {
        const pill = document.createElement("span")
        pill.className = "tag-pill"
        // 인덱스 기반 hue 회전 (골든 앵글 ~137도)
        pill.style.setProperty("--tag-hue", String((i * 137) % 360))
        pill.textContent = t
        tagWrap.appendChild(pill)
      })
      bodyEl.appendChild(tagWrap)
    }

    // ── 추출 버튼 + 구성 요소 선택 ──
    const sessionId = data.play_url ? data.play_url.split("/play/").pop() : ""
    if (sessionId) {
      const section = document.createElement("div")
      section.className = "extract-section"
      if (data.m3u8_url) {
        section.appendChild(renderComponentPicker())

        const btn = document.createElement("button")
        btn.className = "extract-btn"
        btn.dataset.id = sessionId
        btn.textContent = "추출 시작"
        section.appendChild(btn)

        const result = document.createElement("div")
        result.className = "result"
        result.id = "extract-result"
        section.appendChild(result)

        btn.addEventListener("click", () => handleExtract(sessionId, section))
      } else {
        const msg = document.createElement("p")
        msg.className = "progress-msg"
        msg.textContent = "이 세션에서 m3u8 URL을 찾을 수 없습니다. (로그인 필요 또는 자막 미제공)"
        section.appendChild(msg)
      }
      bodyEl.appendChild(section)
    }
  }

  const COMPONENTS = [
    { key: "include_chapters",     label: "챕터 자동 생성 (Claude)",          default: true },
    { key: "include_glossary",     label: "용어집 (Claude)",                  default: true },
    { key: "include_keypoints",    label: "핵심 포인트 (Claude)",             default: true },
    { key: "include_qa",           label: "Q&A 섹션 별도 추출 (Claude)",      default: true },
    { key: "include_design_brief", label: "슬라이드 비주얼 테마 브리프 (Claude)", default: true },
    { key: "include_articles",     label: "관련 해외 기사 (Perplexity)",      default: true },
    { key: "include_thumbnail",    label: "세션 썸네일 이미지",               default: true },
  ]

  function renderComponentPicker() {
    const wrap = document.createElement("fieldset")
    wrap.className = "component-picker"
    const legend = document.createElement("legend")
    legend.textContent = "추출할 구성 요소"
    wrap.appendChild(legend)

    const hint = document.createElement("p")
    hint.className = "component-hint"
    hint.textContent = "자막(subtitle.txt / transcript.txt / transcript_timed.txt)과 meta.md는 항상 생성됩니다."
    wrap.appendChild(hint)

    COMPONENTS.forEach(c => {
      const row = document.createElement("label")
      row.className = "component-row"
      const cb = document.createElement("input")
      cb.type = "checkbox"
      cb.checked = c.default
      cb.dataset.key = c.key
      row.appendChild(cb)
      row.appendChild(document.createTextNode(" " + c.label))
      wrap.appendChild(row)
    })
    return wrap
  }

  function collectOptions(section) {
    const options = {}
    section.querySelectorAll(".component-row input[type=checkbox]").forEach(cb => {
      options[cb.dataset.key] = cb.checked
    })
    return options
  }

  async function handleExtract(sessionId, section) {
    const btn = section.querySelector(".extract-btn")
    const resultDiv = section.querySelector("#extract-result")
    const options = collectOptions(section)
    const anyAI = options.include_chapters || options.include_glossary
                || options.include_keypoints || options.include_qa
                || options.include_design_brief || options.include_articles

    btn.disabled = true
    btn.setAttribute("aria-busy", "true")
    btn.textContent = "추출 중..."
    const initialMsg = anyAI
      ? "자막 다운로드 → AI 후처리 → ZIP 생성 (1–3분 소요)"
      : "자막을 다운로드하고 있습니다..."
    resultDiv.innerHTML = `<p class="progress-msg" aria-busy="true">${initialMsg}</p>`

    try {
      const resp = await fetch(`/api/extract/${sessionId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(options),
      })
      const data = await resp.json()

      if (!resp.ok) {
        throw new Error(data.detail || data.error || "추출 실패")
      }

      let html = "<p><strong>추출 완료!</strong></p>"
      if (data.bundle_url) {
        html += `<p><a href="${data.bundle_url}" role="button" class="primary bundle-download">
          📦 NotebookLM 번들 다운로드 (${data.bundle})
        </a></p>`
        html += `<p class="bundle-hint">폴더: <code>transcripts/${data.session_dir}/</code></p>`
      }
      if (data.files && data.files.length > 0) {
        html += `<details><summary>개별 파일 (${data.files.length}개)</summary><p>`
        const sid = data.session_dir.replace(/^gdc_/, "")
        data.files.forEach(f => {
          html += `<a href="/api/download/${sid}/${f}" role="button" class="outline">${f}</a> `
        })
        html += "</p></details>"
      }
      resultDiv.innerHTML = html
    } catch (err) {
      resultDiv.innerHTML = `<p class="error">추출 실패: ${err.message}</p>`
    } finally {
      btn.disabled = false
      btn.removeAttribute("aria-busy")
      btn.textContent = "추출 시작"
    }
  }
})
