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

    // ── 추출 버튼 ──
    const sessionId = data.play_url ? data.play_url.split("/play/").pop() : ""
    if (sessionId) {
      const section = document.createElement("div")
      section.className = "extract-section"
      if (data.m3u8_url) {
        const btn = document.createElement("button")
        btn.className = "extract-btn"
        btn.dataset.id = sessionId
        btn.textContent = "트랜스크립트 추출"
        section.appendChild(btn)
        const result = document.createElement("div")
        result.className = "result"
        result.id = "extract-result"
        section.appendChild(result)
        btn.addEventListener("click", () => handleExtract(sessionId))
      } else {
        const msg = document.createElement("p")
        msg.className = "progress-msg"
        msg.textContent = "이 세션에서 m3u8 URL을 찾을 수 없습니다. (로그인 필요 또는 자막 미제공)"
        section.appendChild(msg)
      }
      bodyEl.appendChild(section)
    }
  }

  async function handleExtract(sessionId) {
    const btn = document.querySelector(".extract-btn")
    const resultDiv = document.getElementById("extract-result")

    btn.disabled = true
    btn.setAttribute("aria-busy", "true")
    btn.textContent = "추출 중..."
    resultDiv.innerHTML = '<p class="progress-msg">자막을 다운로드하고 있습니다. 잠시 기다려 주세요...</p>'

    try {
      const resp = await fetch(`/api/extract/${sessionId}`, { method: "POST" })
      const data = await resp.json()

      if (!resp.ok) {
        throw new Error(data.detail || data.error || "추출 실패")
      }

      let html = "<p>추출 완료!</p>"
      if (data.files && data.files.length > 0) {
        html += "<p>"
        data.files.forEach(f => {
          html += `<a href="/api/download/${f}" role="button" class="outline">${f}</a> `
        })
        html += "</p>"
      }
      resultDiv.innerHTML = html
    } catch (err) {
      resultDiv.innerHTML = `<p class="error">추출 실패: ${err.message}</p>`
    } finally {
      btn.disabled = false
      btn.removeAttribute("aria-busy")
      btn.textContent = "트랜스크립트 추출"
    }
  }
})
