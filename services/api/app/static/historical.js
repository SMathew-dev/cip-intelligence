// V1.1 Historical Intelligence extension. Kept separate from the V1 UI bundle
// so the historical feature can be reviewed independently from the mature V1 UI.
(() => {
  const historyStatusChip = (status) => {
    if (status === 'STABLE') return statusChip('NORMAL', 'STABLE');
    if (status === 'WATCH') return statusChip('UNUSUAL', 'WATCH');
    if (status === 'ATTENTION') return statusChip('HIGHLY_UNUSUAL', 'ATTENTION');
    return statusChip(status);
  };

  window.renderHistoricalIntelligence = async function(force = false) {
    const root = document.querySelector('#view-history');
    if (!root) return;
    root.innerHTML = `<div class="card card-pad"><div class="skeleton" style="height:20px;width:35%"></div><div class="skeleton" style="height:240px;margin-top:18px"></div></div>`;
    try {
      const days = window.cipHistoryDays || 90;
      if (!window.cipHistoryFixture || force) window.cipHistoryFixture = await api('/app/historical-data.json');
      const d = window.cipHistoryFixture[String(days)];
      if (!d) throw new Error(`Historical fixture unavailable for ${days} days`);
      window.cipHistoryData = d;
      root.innerHTML = `
        <div class="controls-row">
          <div class="select-wrap"><span class="select-label">History window</span><select id="historyWindow">
            ${[30,60,90].map(v => `<option value="${v}" ${v===days?'selected':''}>${v} days</option>`).join('')}
          </select></div>
          <span style="margin-left:auto;color:var(--muted);font-size:9.5px">Deterministic simulator history · investigation prioritization only</span>
        </div>
        <div class="grid grid-4" style="margin-bottom:14px">
          ${metricCard('Historical cycles', d.summary.cycles, '', `${d.summary.assets} monitored assets`)}
          ${metricCard('Process deviations', d.summary.process_deviations, '', `${d.summary.behavioral_alerts} behavioral alerts`)}
          ${metricCard('Measured water', fmt(d.summary.water_m3,1), 'm³', `Dedicated simulated utility measurements`)}
          ${metricCard('Excess vs stable fixture', fmt(d.summary.estimated_excess_water_m3,1), 'm³', `Screening estimate · not a savings claim`)}
        </div>
        <div class="card card-pad" style="margin-bottom:14px">
          <div class="card-head"><div><div class="card-title">Asset attention ranking</div><div class="card-subtitle">Ranks historical evidence for engineering review. It never overrides L2 compliance or authorizes a process change.</div></div></div>
          <div class="table-wrap"><table>
            <thead><tr><th>Priority</th><th>Asset</th><th>Status</th><th>Flow trend</th><th>Duration trend</th><th>Deviations</th><th>Unusual</th><th>Water</th></tr></thead>
            <tbody>${d.asset_ranking.map((a,i)=>`<tr>
              <td><div class="asset-name">#${i+1}</div><div class="cell-muted">Score ${a.attention_score}/100</div></td>
              <td><div class="asset-name">${esc(a.asset)}</div><div class="cell-muted">${esc(a.asset_type)} · ${a.cycles} cycles</div></td>
              <td>${historyStatusChip(a.status)}</td>
              <td><span class="${a.flow_change_lpm < -5 ? 'trend-bad' : 'trend-good'}">${a.flow_change_lpm>0?'+':''}${fmt(a.flow_change_lpm,1)} L/min</span><div class="cell-muted">median ${fmt(a.median_flow_lpm,1)}</div></td>
              <td><span class="${a.duration_change_min > 3 ? 'trend-warn' : 'trend-good'}">${a.duration_change_min>0?'+':''}${fmt(a.duration_change_min,1)} min</span><div class="cell-muted">median ${fmt(a.median_duration_min,1)}</div></td>
              <td>${a.process_deviations}${a.data_reviews ? `<div class="cell-muted">${a.data_reviews} data review</div>` : ''}</td>
              <td>${a.unusual_cycles}</td><td>${fmt(a.total_water_m3,1)} m³</td>
            </tr>`).join('')}</tbody>
          </table></div>
        </div>
        <div class="grid grid-2">
          <div class="card card-pad"><div class="card-head"><div><div class="card-title">What the ranking is seeing</div><div class="card-subtitle">Transparent trends rather than a black-box score.</div></div></div>
            <div class="finding-list">${d.asset_ranking.slice(0,3).map(a=>`<div class="finding"><div><div class="finding-title">${esc(a.asset)} · ${esc(a.status)}</div><div class="finding-copy">Flow ${a.flow_change_lpm>0?'+':''}${fmt(a.flow_change_lpm,1)} L/min across the window; duration ${a.duration_change_min>0?'+':''}${fmt(a.duration_change_min,1)} min; ${a.process_deviations} process deviations; ${a.unusual_cycles} unusual cycles.</div></div><div class="finding-meta">${historyStatusChip(a.status)}</div></div>`).join('')}</div>
          </div>
          <div class="card card-pad"><div class="card-head"><div><div class="card-title">Interpretation boundary</div><div class="card-subtitle">Historical intelligence is context, not control authority.</div></div></div>
            <div class="boundary-panel"><div class="boundary-panel-title">${esc(d.interpretation)}</div><div class="boundary-panel-copy">The public V1.1 history is synthetic and known-answer by design. Real-plant use requires anonymized historical validation, plant-approved specifications, and engineering/QA review.</div></div>
          </div>
        </div>`;
      document.querySelector('#historyWindow')?.addEventListener('change', e => {
        window.cipHistoryDays = Number(e.target.value);
        renderHistoricalIntelligence();
      });
    } catch (e) {
      root.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
    }
  };

  // app.js binds its V1 navigation before this extension loads. Replacing the
  // history button removes that generic listener, which does not know the new
  // viewMeta key, while preserving every mature V1 navigation handler unchanged.
  const oldHistoryButton = document.querySelector('.nav-item[data-view="history"]');
  if (oldHistoryButton) {
    const historyButton = oldHistoryButton.cloneNode(true);
    oldHistoryButton.replaceWith(historyButton);
    historyButton.addEventListener('click', () => {
      state.view = 'history';
      document.querySelectorAll('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.view === 'history'));
      document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === 'view-history'));
      document.querySelector('#pageEyebrow').textContent = 'HISTORICAL INTELLIGENCE';
      document.querySelector('#pageTitle').textContent = 'See degradation before it becomes routine';
      renderHistoricalIntelligence();
    });
  }

  document.querySelector('#refreshBtn')?.addEventListener('click', () => {
    if (state.view === 'history') renderHistoricalIntelligence(true);
  });
})();
