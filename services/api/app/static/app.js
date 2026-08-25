const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  view: 'overview',
  scenario: 'normal',
  metric: 'temperature_c',
  overview: null,
  dataHealth: null,
  cycle: null,
};

const viewMeta = {
  overview: ['PLANT OVERVIEW', 'Good morning'],
  cycles: ['CYCLE EXPLORER', 'Understand every cleaning event'],
  investigations: ['INVESTIGATIONS', 'Evidence before diagnosis'],
  optimization: ['CONTROLLED OPTIMIZATION', 'Improve only what the evidence supports'],
  'data-health': ['DATA HEALTH', 'Trust the evidence first'],
};

function esc(v) {
  return String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function fmt(n, digits = 1) {
  if (n == null || Number.isNaN(Number(n))) return '—';
  return Number(n).toLocaleString(undefined, {maximumFractionDigits: digits, minimumFractionDigits: digits});
}

function pct(n, digits = 0) { return n == null ? '—' : `${fmt(n * 100, digits)}%`; }
function minFmt(seconds) { return seconds == null ? '—' : `${fmt(seconds / 60, 1)} min`; }
function money(n, currency = 'USD') {
  if (n == null) return '—';
  return new Intl.NumberFormat(undefined, {style:'currency', currency, maximumFractionDigits:0}).format(n);
}

function toneForStatus(status = '') {
  const s = status.toUpperCase();
  if (['COMPLIANT','NORMAL','GOOD','CONFIRMED_CONDITION','CONTEXTUALLY_TYPICAL','ELIGIBLE_FOR_CONTROLLED_VALIDATION'].includes(s)) return 'ok';
  if (s.includes('DEVIATION') || s.includes('HIGHLY_UNUSUAL') || s.includes('FAIL') || s.includes('BLOCKED')) return 'bad';
  if (s.includes('DATA') || s.includes('NOT_EVALUABLE') || s.includes('INSUFFICIENT')) return 'data';
  if (s.includes('UNUSUAL') || s.includes('WARNING') || s.includes('REVIEW') || s.includes('HYPOTHESIS')) return 'warn';
  return 'neutral';
}

function statusChip(status, label = null) {
  return `<span class="status-chip status-${toneForStatus(status)}">${esc(label ?? String(status).replaceAll('_',' '))}</span>`;
}

function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => el.classList.remove('show'), 1800);
}

async function api(path) {
  const r = await fetch(path, {headers:{'Accept':'application/json'}});
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${r.status})`);
  }
  return r.json();
}

function setView(view) {
  state.view = view;
  $$('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  $$('.view').forEach(v => v.classList.toggle('active', v.id === `view-${view}`));
  const [eyebrow, title] = viewMeta[view];
  $('#pageEyebrow').textContent = eyebrow;
  $('#pageTitle').textContent = title;
  if (view === 'overview') renderOverview();
  if (view === 'cycles') loadCycle(state.scenario);
  if (view === 'investigations') renderInvestigations();
  if (view === 'optimization') renderOptimization();
  if (view === 'data-health') renderDataHealth();
}

function metricCard(label, value, unit, foot, cls = '') {
  return `<div class="card metric-card">
    <div class="metric-label">${esc(label)}</div>
    <div class="metric-row"><div class="metric-value ${cls}">${esc(value)}</div>${unit ? `<div class="metric-unit">${esc(unit)}</div>` : ''}</div>
    <div class="metric-foot">${foot}</div>
  </div>`;
}

async function renderOverview(force = false) {
  const root = $('#view-overview');
  if (!state.overview || force) {
    root.innerHTML = `<div class="grid grid-4">${'<div class="card metric-card"><div class="skeleton"></div></div>'.repeat(4)}</div>`;
    try { state.overview = await api('/v1/demo/ui/overview'); }
    catch (e) { root.innerHTML = `<div class="error-box">${esc(e.message)}</div>`; return; }
  }
  const d = state.overview;
  root.innerHTML = `
    <div class="grid grid-4" style="margin-bottom:14px">
      ${metricCard('CIP cycles · 24h', d.summary.cycles_24h, '', `<span class="trend-good">${d.summary.compliant} compliant</span> · ${d.summary.process_deviations} deviations`)}
      ${metricCard('Needs attention', d.summary.process_deviations + d.summary.data_review, '', `<span class="trend-bad">${d.summary.process_deviations} process</span> · <span class="trend-warn">${d.summary.data_review} data</span>`)}
      ${metricCard('Behavioral alerts', d.summary.behavioral_alerts, '', `Across ${d.assets.length} monitored assets`)}
      ${metricCard('Measured water', fmt(d.summary.measured_water_m3,1), 'm³ / 24h', `Dedicated utility meters only`)}
    </div>

    <div class="grid grid-3" style="margin-bottom:14px">
      <div class="card card-pad span-2">
        <div class="card-head"><div><div class="card-title">Asset status</div><div class="card-subtitle">Compliance, behavioral intelligence, and evidence confidence by cleaning circuit.</div></div><button class="card-action" data-jump="cycles">Open cycle explorer →</button></div>
        <div class="table-wrap"><table>
          <thead><tr><th>Asset</th><th>L2 Compliance</th><th>L3 Behavior</th><th>Data confidence</th><th>Last CIP</th><th>Findings</th></tr></thead>
          <tbody>${d.assets.map(a => `<tr>
            <td><div class="asset-name">${esc(a.asset)}</div><div class="cell-muted">${esc(a.type)} · ${esc(a.area)}</div></td>
            <td>${statusChip(a.assessment)}</td><td>${statusChip(a.behavior)}</td>
            <td class="confidence">${pct(a.data_confidence,0)}</td><td>${fmt(a.last_cip_minutes,0)} min</td><td>${a.open_findings || '—'}</td>
          </tr>`).join('')}</tbody>
        </table></div>
      </div>
      <div class="card card-pad">
        <div class="card-head"><div><div class="card-title">Needs attention</div><div class="card-subtitle">Ordered by process severity, not by AI novelty.</div></div></div>
        <div class="attention-list">${d.attention.map(x => `<div class="attention-item">
          <div class="attention-bar" style="background:${x.severity==='HIGH'?'var(--red)':x.severity==='MEDIUM'?'var(--amber)':'var(--blue)'}"></div>
          <div><div class="attention-title">${esc(x.asset)} · ${esc(x.title)}</div><div class="attention-copy">${esc(x.detail)}</div></div>
          <button class="attention-action" data-jump="${x.severity==='DATA'?'data-health':'cycles'}">${esc(x.action)}</button>
        </div>`).join('')}</div>
      </div>
    </div>

    <div class="card card-pad">
      <div class="card-head"><div><div class="card-title">Recent CIP cycles</div><div class="card-subtitle">The assessment stack remains separate: compliance does not get overwritten by anomaly detection.</div></div></div>
      <div class="table-wrap"><table>
        <thead><tr><th>Cycle</th><th>Asset</th><th>When</th><th>Compliance</th><th>Behavior</th><th>Duration</th><th>Evidence</th></tr></thead>
        <tbody>${d.recent_cycles.map(c => `<tr><td class="asset-name">${esc(c.cycle_id)}</td><td>${esc(c.asset)}</td><td>${esc(c.ago)}</td><td>${statusChip(c.assessment)}</td><td>${statusChip(c.behavior)}</td><td>${c.duration_min} min</td><td class="confidence">${pct(c.confidence,0)}</td></tr>`).join('')}</tbody>
      </table></div>
    </div>`;
  $$('[data-jump]', root).forEach(b => b.addEventListener('click', () => setView(b.dataset.jump)));
}

const scenarios = [
  ['normal','Normal CIP'],['excessive_rinse','Excessive final rinse'],['profile_shift','Abnormal flow profile'],['compliant_low_flow','Compliant low-flow behavior'],['low_temp','Low caustic temperature'],['low_flow','Low return flow'],['sensor_freeze','Frozen flow sensor']
];

async function fetchCycleBundle(scenario) {
  const paths = {
    timeseries: `/v1/demo/ui/timeseries/${scenario}`,
    reconstruction: `/v1/demo/reconstruct/${scenario}?mode=explicit`,
    compliance: `/v1/demo/compliance/${scenario}?mode=explicit`,
    behavior: `/v1/demo/behavior/${scenario}`,
  };
  const allowedBehavior = ['normal','excessive_rinse','profile_shift','compliant_low_flow','low_temp','sensor_freeze'];
  const requests = [api(paths.timeseries), api(paths.reconstruction), api(paths.compliance)];
  if (allowedBehavior.includes(scenario)) requests.push(api(paths.behavior)); else requests.push(Promise.resolve(null));
  const [timeseries, reconstruction, compliance, behavior] = await Promise.all(requests);
  return {timeseries, reconstruction, compliance, behavior};
}

async function loadCycle(scenario, force = false) {
  state.scenario = scenario;
  const root = $('#view-cycles');
  root.innerHTML = `<div class="card card-pad"><div class="skeleton" style="height:20px;width:35%"></div><div class="skeleton" style="height:260px;margin-top:18px"></div></div>`;
  try {
    state.cycle = await fetchCycleBundle(scenario);
    renderCycle();
  } catch (e) {
    root.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
  }
}

function renderCycle() {
  const root = $('#view-cycles');
  const b = state.cycle;
  const cycle = b.reconstruction.cycles?.[0];
  const comp = b.compliance;
  const beh = b.behavior?.behavior;
  const behaviorStatus = beh?.behavioral_assessment ?? beh?.overall_behavior ?? beh?.assessment ?? 'NOT_EVALUABLE';
  const recConfidence = cycle?.confidence ?? 0;
  const findings = comp.findings || [];
  const failureCount = findings.filter(f => f.status === 'FAIL').length;
  const unknownCount = findings.filter(f => f.status === 'NOT_EVALUABLE').length;

  root.innerHTML = `
    <div class="controls-row">
      <div class="select-wrap"><span class="select-label">Scenario</span><select id="scenarioSelect">${scenarios.map(([v,l]) => `<option value="${v}" ${v===state.scenario?'selected':''}>${l}</option>`).join('')}</select></div>
      <button class="secondary-btn" id="openEvidenceBtn">Show evidence</button>
      <span style="margin-left:auto;color:var(--muted);font-size:9.5px">Simulator fixture · ${esc(cycle?.asset || '')}</span>
    </div>

    <div class="hero-strip">
      <div class="hero-cell primary"><div class="hero-kicker">Overall assessment</div><div class="hero-value">${statusChip(comp.overall_assessment)}</div><div class="hero-note">L2 validated process execution</div></div>
      <div class="hero-cell"><div class="hero-kicker">Behavior</div><div class="hero-value">${statusChip(behaviorStatus)}</div><div class="hero-note">Asset + recipe historical comparison</div></div>
      <div class="hero-cell"><div class="hero-kicker">Reconstruction</div><div class="hero-value">${pct(recConfidence,1)}</div><div class="hero-note">${esc(cycle?.reconstruction_mode || '')} · ${esc(cycle?.completeness || '')}</div></div>
      <div class="hero-cell"><div class="hero-kicker">Cycle duration</div><div class="hero-value">${minFmt(cycle?.duration_seconds)}</div><div class="hero-note">${failureCount} failed · ${unknownCount} not evaluable</div></div>
    </div>

    <div class="card chart-card" style="margin-bottom:14px">
      <div class="chart-toolbar"><div><div class="card-title">Process signals</div><div class="card-subtitle">Raw simulator readings with reconstructed CIP phase context.</div></div>
        <div class="metric-tabs">
          ${[['temperature_c','Temperature'],['flow_lpm','Flow'],['conductivity_mscm','Conductivity'],['pressure_bar','Pressure']].map(([v,l])=>`<button class="metric-tab ${state.metric===v?'active':''}" data-metric="${v}">${l}</button>`).join('')}
        </div>
      </div>
      <div class="chart-wrap" id="processChart"></div>
      <div class="phase-band">${phaseBandHtml(b.timeseries.phases)}</div>
    </div>

    <div class="card card-pad" style="margin-bottom:14px">
      <div class="card-head"><div><div class="card-title">Evidence layers</div><div class="card-subtitle">Each layer has its own authority; an inferred behavior finding never changes deterministic L2 compliance.</div></div></div>
      <div class="layer-grid">
        <div class="layer-card"><div class="layer-label">L0 / L1</div><div class="layer-value">${pct(recConfidence,1)} reconstruction</div><div class="layer-detail">${esc(cycle?.reconstruction_mode)} phase evidence</div></div>
        <div class="layer-card"><div class="layer-label">L2 Compliance</div><div class="layer-value">${esc(comp.overall_assessment.replaceAll('_',' '))}</div><div class="layer-detail">${comp.requirements_passed}/${comp.requirements_total} requirements passed</div></div>
        <div class="layer-card"><div class="layer-label">L3 Behavior</div><div class="layer-value">${esc(String(behaviorStatus).replaceAll('_',' '))}</div><div class="layer-detail">Plant-specific historical fingerprint</div></div>
        <div class="layer-card"><div class="layer-label">L5 Diagnosis</div><div class="layer-value">Not assumed</div><div class="layer-detail">Requires joint evidence / confirmed outcome</div></div>
        <div class="layer-card"><div class="layer-label">Control</div><div class="layer-value">Read only</div><div class="layer-detail">No PLC/HMI write path</div></div>
      </div>
    </div>

    <div class="grid grid-2">
      <div class="card card-pad"><div class="card-head"><div><div class="card-title">Validated requirements</div><div class="card-subtitle">Plant-approved recipe revision: ${esc(comp.recipe?.name)} · Rev ${esc(comp.recipe?.revision)}</div></div></div>
        <div class="finding-list">${findings.map(f => `<div class="finding"><div><div class="finding-title">${esc(f.title)}</div><div class="finding-copy">${esc(f.conclusion)}</div></div><div class="finding-meta">${statusChip(f.status)}<div class="finding-class">${esc(f.finding_class)}</div></div></div>`).join('')}</div>
      </div>
      <div class="card card-pad"><div class="card-head"><div><div class="card-title">Phase reconstruction</div><div class="card-subtitle">Every phase preserves its evidence source and confidence.</div></div></div>
        <div class="table-wrap"><table><thead><tr><th>Phase</th><th>Duration</th><th>Evidence</th><th>Confidence</th></tr></thead><tbody>${cycle.phases.map(p=>`<tr><td class="asset-name">${esc(p.phase.replaceAll('_',' '))}</td><td>${minFmt(p.duration_seconds)}</td><td>${esc(p.evidence_source)}</td><td class="confidence">${pct(p.confidence,1)}</td></tr>`).join('')}</tbody></table></div>
      </div>
    </div>`;

  $('#scenarioSelect').addEventListener('change', e => loadCycle(e.target.value));
  $('#openEvidenceBtn').addEventListener('click', () => setView('investigations'));
  $$('.metric-tab', root).forEach(btn => btn.addEventListener('click', () => { state.metric = btn.dataset.metric; renderCycle(); }));
  drawChart($('#processChart'), b.timeseries.samples, state.metric);
}

function phaseBandHtml(phases) {
  const total = phases.reduce((s,p)=>s+p.duration_seconds,0) || 1;
  const cls = p => ({PRE_RINSE:'phase-pre',CAUSTIC:'phase-caustic',INTERMEDIATE_RINSE:'phase-intermediate',ACID:'phase-acid',FINAL_RINSE:'phase-final'}[p] || '');
  return phases.map(p => `<div class="phase-segment ${cls(p.phase)}" style="width:${(p.duration_seconds/total)*100}%" title="${esc(p.phase)} · ${minFmt(p.duration_seconds)}">${esc(p.phase.replaceAll('_',' '))}</div>`).join('');
}

function drawChart(root, samples, metric) {
  if (!root || !samples?.length) return;
  const spec = {
    temperature_c: ['Temperature','°C'], flow_lpm:['Return flow','L/min'], conductivity_mscm:['Conductivity','mS/cm'], pressure_bar:['Return pressure','bar']
  }[metric];
  const W=1000,H=250,L=48,R=16,T=18,B=34;
  const vals = samples.map(x => Number(x[metric])).filter(Number.isFinite);
  const min = Math.min(...vals), max = Math.max(...vals), pad = Math.max((max-min)*.12, Math.abs(max)*.025, .5);
  const yMin = min-pad, yMax=max+pad;
  const tMax = Math.max(...samples.map(x=>x.t_seconds)) || 1;
  const x = t => L + (t/tMax)*(W-L-R);
  const y = v => T + (1-(v-yMin)/(yMax-yMin))*(H-T-B);
  const path = samples.map((s,i)=>`${i?'L':'M'}${x(s.t_seconds).toFixed(1)},${y(s[metric]).toFixed(1)}`).join(' ');
  const grid = [0,.25,.5,.75,1].map(f=>{
    const yy = T + f*(H-T-B); const val = yMax - f*(yMax-yMin);
    return `<line x1="${L}" y1="${yy}" x2="${W-R}" y2="${yy}" stroke="#e9edef" stroke-width="1"/><text x="${L-8}" y="${yy+3}" text-anchor="end" font-size="9" fill="#7f8b91">${fmt(val,metric==='pressure_bar'?2:1)}</text>`;
  }).join('');
  const ticks = [0,.25,.5,.75,1].map(f=>{ const xx=L+f*(W-L-R); return `<text x="${xx}" y="${H-9}" text-anchor="middle" font-size="9" fill="#89949a">${fmt((tMax*f)/60,0)}m</text>`}).join('');
  root.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${spec[0]} over CIP cycle">
    ${grid}
    <path d="${path}" fill="none" stroke="#14796f" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>
    <text x="${L}" y="11" font-size="9" font-weight="700" fill="#68757e">${spec[0]} · ${spec[1]}</text>
    ${ticks}
  </svg>`;
}

async function renderInvestigations() {
  const root = $('#view-investigations');
  root.innerHTML = `<div class="grid grid-2"><div class="card card-pad"><div class="skeleton" style="height:180px"></div></div><div class="card card-pad"><div class="skeleton" style="height:180px"></div></div></div>`;
  try {
    const [confirmed, verification, frozen] = await Promise.all([
      api('/v1/demo/diagnostics/restriction_confirmed'),
      api('/v1/demo/diagnostics/verification_failure'),
      api('/v1/demo/diagnostics/sensor_freeze'),
    ]);
    const c = confirmed.l5;
    const confirmedItem = c.confirmed_conditions?.[0];
    const maint = c.linked_evidence?.maintenance_events?.[0];
    root.innerHTML = `
      <div class="grid grid-3" style="margin-bottom:14px">
        ${metricCard('Open investigations','3','',`2 engineering · 1 data quality`)}
        ${metricCard('Confirmed conditions','1','',`Closed-loop maintenance evidence`)}
        ${metricCard('Unresolved hypotheses','1','',`Inference remains explicitly unconfirmed`)}
      </div>
      <div class="grid grid-2">
        <div class="card card-pad">
          <div class="card-head"><div><div class="card-title">Evidence graph · HTST-01</div><div class="card-subtitle">The system preserves the difference between detection, hypothesis, and physical confirmation.</div></div>${statusChip(c.diagnostic_status)}</div>
          <div class="evidence-stack">
            <div class="evidence-node"><div class="evidence-node-title">Hydraulic deviation observed</div><div class="evidence-node-copy">Return-flow behavior deviated from plant-specific baseline.</div></div>
            <div class="evidence-node"><div class="evidence-node-title">Joint signal evidence</div><div class="evidence-node-copy">Flow ↓ with pressure ↑ supports a restriction hypothesis; low flow alone would not.</div></div>
            <div class="evidence-node"><div class="evidence-node-title">Possible restriction · INFERRED</div><div class="evidence-node-copy">Root cause remains a hypothesis until physical evidence arrives.</div></div>
            <div class="evidence-node"><div class="evidence-node-title">${esc(confirmedItem?.title || 'Maintenance confirmation')} · CONFIRMED</div><div class="evidence-node-copy">${esc(confirmedItem?.conclusion || '')} ${maint?.component ? `Component: ${esc(maint.component)}.`:''}</div></div>
          </div>
        </div>
        <div class="card card-pad">
          <div class="card-head"><div><div class="card-title">Outcome investigation</div><div class="card-subtitle">A failed verification triggers investigation; it does not identify root cause by itself.</div></div>${statusChip(verification.l5?.diagnostic_status || 'REVIEW')}</div>
          <div class="finding-list">
            <div class="finding"><div><div class="finding-title">Post-CIP verification failure</div><div class="finding-copy">Validated bulk process execution can still coexist with a failed ATP/micro/inspection outcome. Investigate local cleanability, coverage, production context, and sampling evidence.</div></div><div class="finding-meta">${statusChip('REVIEW')}<div class="finding-class">OUTCOME</div></div></div>
            <div class="finding"><div><div class="finding-title">Sensor-freeze safeguard</div><div class="finding-copy">${esc(frozen.l5?.reliability_boundary || 'Unreliable instrumentation blocks root-cause inference.')}</div></div><div class="finding-meta">${statusChip('DATA_REVIEW_REQUIRED')}<div class="finding-class">BOUNDARY</div></div></div>
          </div>
        </div>
      </div>`;
  } catch (e) { root.innerHTML = `<div class="error-box">${esc(e.message)}</div>`; }
}

async function renderOptimization() {
  const root = $('#view-optimization');
  root.innerHTML = `<div class="card card-pad"><div class="skeleton" style="height:240px"></div></div>`;
  try {
    const d = await api('/v1/demo/optimization/excessive_rinse');
    const c = d.candidate;
    const trial = c.proposed_controlled_trial;
    root.innerHTML = `
      <div class="optimization-hero" style="margin-bottom:14px">
        <div style="display:flex;justify-content:space-between;gap:15px;align-items:flex-start"><div><div class="eyebrow">ELIGIBLE CANDIDATE</div><div class="opt-title">Final-rinse tail reduction · ${esc(c.asset)}</div></div>${statusChip(c.eligibility)}</div>
        <div class="opt-copy">The validated rinse endpoint was achieved materially before the phase ended. CIP Intelligence proposes a conservative controlled-validation envelope; it does not authorize or implement a recipe change.</div>
        <div class="opt-numbers">
          <div class="opt-number"><strong>${minFmt(c.current_final_rinse_seconds)}</strong><span>Current final rinse</span></div>
          <div class="opt-number"><strong>${minFmt(c.observed_endpoint.seconds_from_phase_start_to_validated_hold)}</strong><span>Validated endpoint hold achieved</span></div>
          <div class="opt-number"><strong>${minFmt(trial.nominal_review_target_seconds)}</strong><span>Nominal trial review target</span></div>
          <div class="opt-number"><strong>${money(c.economics_evidence.annualized_opportunity_scenario,c.economics_evidence.currency)}</strong><span>Simulator annual opportunity scenario</span></div>
        </div>
        <div class="boundary-box"><strong>Non-negotiable boundary:</strong> ${esc(trial.critical_boundary)}</div>
      </div>
      <div class="grid grid-2">
        <div class="card card-pad"><div class="card-head"><div><div class="card-title">Evidence gates</div><div class="card-subtitle">A candidate exists only because every required gate passed.</div></div></div>
          <div class="finding-list">
            ${[['L2 process compliance','COMPLIANT','Deterministic validated requirements achieved.'],['Historical reference','GOOD',`${c.historical_reference.training_cycles} comparable cycles available.`],['QA outcome evidence','GOOD',`${pct(c.outcome_evidence.coverage,1)} coverage · ${pct(c.outcome_evidence.decisive_pass_rate,1)} decisive pass rate.`],['Diagnostic blockers','GOOD','No unresolved condition blocks this candidate.']].map(([t,s,copy])=>`<div class="finding"><div><div class="finding-title">${t}</div><div class="finding-copy">${copy}</div></div><div class="finding-meta">${statusChip(s)}</div></div>`).join('')}
          </div>
        </div>
        <div class="card card-pad"><div class="card-head"><div><div class="card-title">Controlled validation workflow</div><div class="card-subtitle">Humans retain authority at every approval step.</div></div></div>
          <div class="evidence-stack">
            <div class="evidence-node"><div class="evidence-node-title">1 · Engineering review</div><div class="evidence-node-copy">Review evidence, equipment constraints, and proposed trial envelope.</div></div>
            <div class="evidence-node"><div class="evidence-node-title">2 · QA approval</div><div class="evidence-node-copy">Approve a controlled validation protocol and required verification sampling.</div></div>
            <div class="evidence-node"><div class="evidence-node-title">3 · Controlled trial · minimum ${trial.minimum_trial_cycles} cycles</div><div class="evidence-node-copy">Endpoint remains authoritative on every single cycle.</div></div>
            <div class="evidence-node"><div class="evidence-node-title">4 · Human change control</div><div class="evidence-node-copy">Even successful trials only support human review; CIP Intelligence cannot approve a new recipe.</div></div>
          </div>
        </div>
      </div>`;
  } catch (e) { root.innerHTML = `<div class="error-box">${esc(e.message)}</div>`; }
}

async function renderDataHealth(force = false) {
  const root = $('#view-data-health');
  if (!state.dataHealth || force) {
    root.innerHTML = `<div class="card card-pad"><div class="skeleton" style="height:220px"></div></div>`;
    try { state.dataHealth = await api('/v1/demo/ui/data-health'); }
    catch (e) { root.innerHTML = `<div class="error-box">${esc(e.message)}</div>`; return; }
  }
  const d = state.dataHealth;
  root.innerHTML = `
    <div class="grid grid-3" style="margin-bottom:14px">
      <div class="card card-pad"><div class="health-score"><div class="score-ring" style="--score:${d.overall_score*100}"><span>${Math.round(d.overall_score*100)}</span></div><div class="health-stat"><strong>Overall data confidence</strong><span>Mapping + coverage + quality evidence</span></div></div></div>
      ${metricCard('Trusted signals',d.trusted_signals,'',`${d.warning_signals} warning · ${d.blocked_signals} blocked`)}
      ${metricCard('Last acquisition',d.last_ingestion,'',esc(d.mapping_revision))}
    </div>
    <div class="card card-pad">
      <div class="card-head"><div><div class="card-title">Signal health</div><div class="card-subtitle">Bad evidence is blocked upstream so downstream intelligence cannot silently rely on it.</div></div></div>
      <div class="table-wrap"><table><thead><tr><th>Physical tag</th><th>Semantic meaning</th><th>Asset</th><th>Coverage</th><th>Status</th><th>Issue</th><th>Freshness</th></tr></thead>
        <tbody>${d.sensors.map(s=>`<tr><td class="asset-name">${esc(s.tag)}</td><td>${esc(s.concept)}</td><td>${esc(s.asset)}</td><td><div style="display:flex;align-items:center;gap:7px"><div class="coverage-bar"><div class="coverage-fill" style="width:${s.coverage*100}%"></div></div><span class="confidence">${pct(s.coverage,0)}</span></div></td><td>${statusChip(s.status)}</td><td>${esc(s.issue || '—')}</td><td>${esc(s.last_seen)}</td></tr>`).join('')}</tbody>
      </table></div>
    </div>`;
}

function bindGlobal() {
  $$('.nav-item').forEach(btn => btn.addEventListener('click', () => setView(btn.dataset.view)));
  $('#refreshBtn').addEventListener('click', () => {
    if (state.view === 'overview') { state.overview = null; renderOverview(true); }
    if (state.view === 'cycles') loadCycle(state.scenario, true);
    if (state.view === 'investigations') renderInvestigations();
    if (state.view === 'optimization') renderOptimization();
    if (state.view === 'data-health') { state.dataHealth = null; renderDataHealth(true); }
    toast('Refreshed');
  });
}

bindGlobal();
renderOverview();
