/* External research validation workspace.
   Published findings are treated as fixed benchmark ground truth. This UI never
   converts literature claims into validation credit until CIP Intelligence has
   actually been run against an external dataset or a clearly labeled research fixture. */
(function () {
  const VIEW = 'research';
  let catalog = null;

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  }

  function badge(text, tone = 'neutral') {
    return `<span class="research-badge research-badge-${tone}">${escapeHtml(text)}</span>`;
  }

  function statusTone(item) {
    if (item.id === 'external-mbr-2017') return 'ok';
    if ((item.validation_state || '').includes('awaiting')) return 'warn';
    return 'neutral';
  }

  function list(items) {
    if (!items?.length) return '<div class="research-empty">None reported for this benchmark.</div>';
    return `<ul>${items.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`;
  }

  function sourceLink(item) {
    return item.url ? `<a class="research-source" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">Open source ↗</a>` : '';
  }

  function benchmarkCard(item, index) {
    const findings = item.published_findings?.length
      ? list(item.published_findings)
      : '<div class="research-empty">No paper-derived performance claim is being used here; this is an external-data ingestion benchmark.</div>';
    return `<article class="card research-card" data-benchmark="${escapeHtml(item.id)}">
      <div class="research-card-head">
        <div>
          <div class="step-mark">BENCHMARK ${String(index + 1).padStart(2,'0')}</div>
          <h3>${escapeHtml(item.title)}</h3>
          <div class="research-meta">${escapeHtml(item.domain)} · ${escapeHtml(item.year)} · ${escapeHtml(item.source)}</div>
        </div>
        <div class="research-card-status">${badge(item.status, statusTone(item))}${badge(item.benchmark_type, 'data')}</div>
      </div>

      <div class="research-evidence-grid">
        <section>
          <div class="research-section-label">Published ground truth</div>
          ${findings}
        </section>
        <section>
          <div class="research-section-label">What CIP Intelligence must test</div>
          ${list(item.what_cip_intelligence_should_test)}
        </section>
      </div>

      <div class="research-signal-row">
        <div><strong>Signals / evidence reported</strong><span>${escapeHtml((item.signals_reported || []).join(' · '))}</span></div>
        ${item.sampling ? `<div><strong>Sampling</strong><span>${escapeHtml(item.sampling)}</span></div>` : ''}
        <div><strong>Data availability</strong><span>${escapeHtml(item.data_status)}</span></div>
      </div>

      <div class="research-validation-strip">
        <div>
          <span class="research-section-label">Current validation state</span>
          <strong>${escapeHtml(item.validation_state)}</strong>
        </div>
        <div class="research-actions">
          ${item.doi ? `<span class="research-doi">DOI ${escapeHtml(item.doi)}</span>` : ''}
          ${sourceLink(item)}
          <button class="card-action research-use-data" type="button">Use Add plant data →</button>
        </div>
      </div>
    </article>`;
  }

  function renderProtocol() {
    return `<div class="research-protocol card card-pad">
      <div class="card-head"><div><div class="step-mark">VALIDATION PROTOCOL</div><div class="card-title">Evidence before agreement</div><div class="card-subtitle">A paper becomes a benchmark before CIP Intelligence sees the answer. We then compare the system output with the locked published finding.</div></div>${badge('NO VALIDATION CREDIT YET','warn')}</div>
      <div class="research-steps">
        <div><span>01</span><strong>Lock ground truth</strong><p>Record exactly what the paper reports, including the boundary of the claim.</p></div>
        <div><span>02</span><strong>Run external evidence</strong><p>Use raw research data when available; otherwise label any reconstructed fixture explicitly.</p></div>
        <div><span>03</span><strong>Compare blind</strong><p>Score agreement, misses and false positives without rewriting the source result.</p></div>
        <div><span>04</span><strong>Publish failures too</strong><p>Only externally reproduced findings count as validation. Unsupported conclusions remain blocked.</p></div>
      </div>
    </div>`;
  }

  async function loadCatalog() {
    if (catalog) return catalog;
    const response = await fetch('/app/research-benchmarks.json', {headers:{'Accept':'application/json'}});
    if (!response.ok) throw new Error(`Benchmark catalog failed to load (${response.status})`);
    catalog = await response.json();
    return catalog;
  }

  async function renderResearch() {
    const root = document.querySelector('#view-research');
    if (!root) return;
    root.innerHTML = '<div class="card card-pad"><div class="skeleton"></div></div>';
    try {
      const data = await loadCatalog();
      const published = data.benchmarks.filter(b => b.benchmark_type.startsWith('published')).length;
      const external = data.benchmarks.filter(b => b.benchmark_type === 'external-ingestion benchmark').length;
      const validated = data.benchmarks.filter(b => (b.validation_state || '').startsWith('validated')).length;
      root.innerHTML = `
        <div class="research-hero">
          <div>
            <div class="eyebrow">EXTERNAL RESEARCH VALIDATION</div>
            <h2>Can CIP Intelligence reproduce published engineering findings?</h2>
            <p>These benchmarks keep the literature result separate from the application result. A benchmark is not called validated until CIP Intelligence independently reaches the supported conclusion from external evidence.</p>
          </div>
          <div class="research-scoreboard">
            <div><strong>${published}</strong><span>published benchmarks</span></div>
            <div><strong>${external}</strong><span>external-data stress test</span></div>
            <div><strong>${validated}</strong><span>independently reproduced</span></div>
          </div>
        </div>
        <div class="boundary-box research-boundary">${escapeHtml(data.principle)} Raw research data are not implied to exist when the source does not publish them.</div>
        ${renderProtocol()}
        <div class="research-stack">${data.benchmarks.map(benchmarkCard).join('')}</div>`;

      root.querySelectorAll('.research-use-data').forEach(button => button.addEventListener('click', () => {
        const target = document.querySelector('.nav-item[data-view="connections"]');
        if (target) target.click();
      }));
    } catch (error) {
      root.innerHTML = `<div class="error-box">${escapeHtml(error.message)}</div>`;
    }
  }

  function activateResearch(event) {
    const button = event.target.closest?.('.nav-item[data-view="research"]');
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    document.querySelectorAll('.nav-item').forEach(item => item.classList.toggle('active', item === button));
    document.querySelectorAll('.view').forEach(view => view.classList.toggle('active', view.id === 'view-research'));
    const eyebrow = document.querySelector('#pageEyebrow');
    const title = document.querySelector('#pageTitle');
    if (eyebrow) eyebrow.textContent = 'WORKSPACE / EXTERNAL VALIDATION';
    if (title) title.textContent = 'Research validation';
    renderResearch();
  }

  document.addEventListener('click', activateResearch, true);
  window.renderResearchValidation = renderResearch;
})();
