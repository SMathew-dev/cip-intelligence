/* V1.2 semantic discovery + engineer-confirmed onboarding.
   Discovery remains advisory until the engineer explicitly confirms mappings. */
(function () {
  let selectedPlantFile = null;

  document.addEventListener('change', event => {
    if (event.target?.id === 'plantFile') selectedPlantFile = event.target.files?.[0] || null;
  });
  document.addEventListener('drop', event => {
    if (event.target?.closest?.('#dropZone') && event.dataTransfer?.files?.[0]) {
      selectedPlantFile = event.dataTransfer.files[0];
    }
  }, true);

  function measurementLabel(candidate) {
    if (!candidate) return null;
    return candidate.measurement_type.replaceAll('_', ' ');
  }

  function confidenceLabel(value) {
    if (value == null) return '';
    return `${Math.round(Number(value) * 100)}%`;
  }

  function conceptOptions(selected = '') {
    const groups = [
      ['','Ignore / context only'],
      ['cip.return.temperature','CIP return temperature'],
      ['cip.supply.temperature','CIP supply temperature'],
      ['cip.return.flow','CIP return flow'],
      ['cip.supply.flow','CIP supply flow'],
      ['cip.return.conductivity','CIP return conductivity'],
      ['cip.return.pressure','CIP return pressure'],
      ['cip.supply.pressure','CIP supply pressure'],
      ['cip.return.ph','CIP return pH'],
      ['cip.sequence.phase','CIP sequence / cleaning phase'],
      ['cip.supply_pump.state','CIP supply pump state'],
      ['cip.return_valve.state','CIP return valve state'],
      ['cip.utility.fresh_water.flow','Dedicated fresh-water flow'],
      ['cip.utility.wastewater.flow','Dedicated wastewater flow'],
    ];
    return groups.map(([value,label]) => `<option value="${esc(value)}" ${value===selected?'selected':''}>${esc(label)}</option>`).join('');
  }

  function sourceUnitFor(column) {
    return column.mapping_candidates?.[0]?.source_unit_guess || column.measurement_candidate?.source_unit_guess || '';
  }

  function assetForSignal(organization, sourceColumn) {
    if (organization.mode !== 'wide_tag_prefix') return null;
    const found = organization.assets?.find(asset => asset.signal_columns?.includes(sourceColumn));
    return found?.proposed_name || null;
  }

  function needsEngineeringUnit(concept) {
    return new Set([
      'cip.return.temperature','cip.supply.temperature','cip.return.flow','cip.supply.flow',
      'cip.return.conductivity','cip.return.pressure','cip.supply.pressure',
      'cip.utility.fresh_water.flow','cip.utility.wastewater.flow'
    ]).has(concept);
  }

  async function requestJson(url, options) {
    const response = await fetch(url, options);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
    return body;
  }

  function renderConfirmation(data, organization, timestamp) {
    const timestampColumn = timestamp.column;
    const assetSource = organization.mode === 'row_identity' ? organization.source_column : null;
    const reviewColumns = data.columns.filter(c => c.source_column !== timestampColumn && c.source_column !== assetSource);
    const needsManualAsset = organization.mode === 'unresolved';

    return `<div class="card card-pad confirmation-card" id="confirmationCard">
      <div class="card-head"><div><div class="step-mark">02 · ENGINEER CONFIRMATION</div><div class="card-title">Confirm plant context and analyze</div><div class="card-subtitle">Only confirmed mappings become analytical evidence. Generic measurements stay context-only until you assign a CIP role.</div></div><span class="review-badge">READ ONLY</span></div>
      <div class="confirmation-grid">
        <div class="confirmation-field"><label for="confirmPlant">Plant / site</label><input id="confirmPlant" value="Uploaded Plant" /></div>
        <div class="confirmation-field"><label for="confirmSource">Source system</label><input id="confirmSource" value="CSV export" /></div>
        <div class="confirmation-field"><label for="confirmTimezone">Plant timezone</label><input id="confirmTimezone" value="UTC" /></div>
        <div class="confirmation-field"><label for="confirmAsset">${needsManualAsset ? 'Equipment / circuit' : 'Equipment organization'}</label>${needsManualAsset ? '<input id="confirmAsset" placeholder="e.g. MBR-01, POL1, HTST-01" />' : `<input id="confirmAsset" value="${esc(organization.mode === 'row_identity' ? 'From equipment column' : `${organization.assets.length} proposed group(s)`)}" disabled />`}</div>
      </div>
      <div class="confirm-warning">A generic process state is not a CIP cleaning phase. Select <strong>CIP sequence / cleaning phase</strong> only when you know the source column truly represents the cleaning recipe step.</div>
      <div class="table-wrap mapping-confirm-table"><table><thead><tr><th>Source signal</th><th>Discovery</th><th>Confirmed CIP role</th><th>Source unit</th></tr></thead><tbody>
        ${reviewColumns.map(c => {
          const exact = c.mapping_candidates?.[0];
          const generic = c.measurement_candidate;
          const defaultConcept = exact?.concept || '';
          const discovery = exact ? exact.concept.replace('cip.','').replaceAll('.',' / ') : generic ? measurementLabel(generic) : 'unresolved';
          const hint = generic?.requires_context ? 'Process context required' : exact ? 'Semantic suggestion requires confirmation' : 'Leave ignored unless known';
          const assignedAsset = assetForSignal(organization, c.source_column);
          return `<tr data-confirm-row data-source-column="${esc(c.source_column)}" data-asset="${esc(assignedAsset || '')}">
            <td class="source-cell"><strong>${esc(c.source_column)}</strong>${assignedAsset ? `<div class="context-hint">Proposed equipment: ${esc(assignedAsset)}</div>` : ''}</td>
            <td class="context-cell">${esc(discovery)}<div class="context-hint">${esc(hint)}</div></td>
            <td><select data-concept>${conceptOptions(defaultConcept)}</select></td>
            <td><input data-unit value="${esc(sourceUnitFor(c))}" placeholder="unit if required" /></td>
          </tr>`;
        }).join('')}
      </tbody></table></div>
      <label class="confirmation-check"><input type="checkbox" id="confirmEvidence" /><span>I confirm that the selected signal meanings, equipment assignment, and units reflect my engineering understanding of this export. I understand this analysis is read-only and does not prove microbiological cleanliness or authorize sanitation release.</span></label>
      <div class="analysis-actions"><button class="primary-btn" id="analyzeConfirmed" disabled>Confirm mappings & analyze</button><div class="analysis-status" id="analysisStatus">Nothing is treated as plant truth until you confirm.</div></div>
      <div id="analysisResult"></div>
    </div>`;
  }

  function bindConfirmation(data) {
    const checkbox = document.querySelector('#confirmEvidence');
    const button = document.querySelector('#analyzeConfirmed');
    const status = document.querySelector('#analysisStatus');
    if (!checkbox || !button) return;
    checkbox.addEventListener('change', () => { button.disabled = !checkbox.checked; });

    button.addEventListener('click', async () => {
      const fileInput = document.querySelector('#plantFile');
      const file = selectedPlantFile || fileInput?.files?.[0];
      const timestampColumn = data.timestamp_candidate?.column;
      const organization = data.organization_proposal || {mode:'unresolved', assets:[]};
      const assetSource = organization.mode === 'row_identity' ? organization.source_column : null;
      const assetDefault = organization.mode === 'unresolved' ? document.querySelector('#confirmAsset')?.value?.trim() : null;

      if (!file) { status.textContent = 'Re-select the source CSV before analysis.'; return; }
      if (!timestampColumn) { status.textContent = 'A confirmed timestamp column is required.'; return; }
      if (organization.mode === 'unresolved' && !assetDefault) { status.textContent = 'Enter the equipment or circuit name before analysis.'; return; }

      const mappings = [];
      if (assetSource) mappings.push({source_column: assetSource, concept:'cip.asset', source_unit:null});
      for (const row of document.querySelectorAll('[data-confirm-row]')) {
        const concept = row.querySelector('[data-concept]')?.value || '';
        if (!concept) continue;
        const sourceColumn = row.dataset.sourceColumn;
        const sourceUnit = row.querySelector('[data-unit]')?.value?.trim() || null;
        if (needsEngineeringUnit(concept) && !sourceUnit) {
          status.textContent = `${sourceColumn}: enter the source engineering unit before analysis.`;
          return;
        }
        const mapping = {source_column: sourceColumn, concept, source_unit: sourceUnit};
        if (organization.mode === 'wide_tag_prefix') {
          const assigned = row.dataset.asset;
          if (!assigned) { status.textContent = `${sourceColumn}: equipment assignment is unresolved.`; return; }
          mapping.asset_override = assigned;
        }
        mappings.push(mapping);
      }
      if (!mappings.some(m => m.concept !== 'cip.asset')) { status.textContent = 'Confirm at least one analytical signal mapping.'; return; }

      const profileName = `review-${String(data.sha256 || Date.now()).slice(0,12)}`;
      const profile = {
        name: profileName,
        plant: document.querySelector('#confirmPlant')?.value?.trim() || 'Uploaded Plant',
        source_system: document.querySelector('#confirmSource')?.value?.trim() || 'CSV export',
        timezone: document.querySelector('#confirmTimezone')?.value?.trim() || 'UTC',
        timestamp_column: timestampColumn,
        asset_default: assetDefault || null,
        mappings,
      };

      button.disabled = true;
      status.textContent = 'Saving confirmed mapping…';
      try {
        await requestJson('/v1/mappings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(profile)});
        status.textContent = 'Normalizing confirmed plant evidence…';
        const form = new FormData(); form.append('file', file);
        const ingestion = await requestJson(`/v1/ingestion/${encodeURIComponent(profileName)}`, {method:'POST', body:form});
        status.textContent = 'Reconstructing supported CIP cycles…';
        const reconstruction = await requestJson(`/v1/reconstruction/ingestions/${encodeURIComponent(ingestion.ingestion_id)}`, {method:'POST'});
        const summary = ingestion.summary || {};
        const result = reconstruction.result || {};
        const coverage = summary.data_coverage == null ? '—' : `${Math.round(Number(summary.data_coverage) * 100)}%`;
        const cycles = Number(result.cycle_count || 0);
        document.querySelector('#analysisResult').innerHTML = `<div class="analysis-result">
          <div class="analysis-metric"><span>Source rows</span><strong>${esc(summary.rows_in_source ?? '—')}</strong></div>
          <div class="analysis-metric"><span>Normalized points</span><strong>${esc(summary.normalized_points ?? '—')}</strong></div>
          <div class="analysis-metric"><span>Good evidence</span><strong>${esc(coverage)}</strong></div>
          <div class="analysis-metric"><span>Reconstructed cycles</span><strong>${esc(cycles)}</strong></div>
          <div class="analysis-note">${cycles ? `${cycles} cycle(s) were reconstructed from the mappings you explicitly confirmed. Review the Cycle Explorer and data-quality issues before drawing engineering conclusions.` : 'No sufficiently supported CIP cycle was reconstructed. That is a valid result: CIP Intelligence will not manufacture a cleaning cycle from generic process data.'} Compliance remains unavailable until plant-approved recipe requirements are configured.</div>
        </div>`;
        status.textContent = 'Analysis complete · read-only evidence workspace created.';
      } catch (error) {
        status.textContent = error.message;
      } finally {
        button.disabled = !checkbox.checked;
      }
    });
  }

  window.renderInspection = function renderInspectionV12(data) {
    const root = document.querySelector('#inspectionResults');
    const concepts = new Set(data.columns.map(suggestedConcept).filter(Boolean));
    const organization = data.organization_proposal || {mode:'unresolved', assets:[], rule:'Equipment identity requires review.'};
    const core = ['cip.return.temperature','cip.return.flow','cip.return.conductivity'];
    const reconstruction = Boolean(data.timestamp_candidate?.column) && core.some(x => concepts.has(x));
    const compliance = core.every(x => concepts.has(x));
    const resources = [...concepts].some(x => x.startsWith('cip.utility.') || x.startsWith('cip.chemical.'));
    const timestamp = data.timestamp_candidate || {};
    const timestampSupported = Boolean(timestamp.column && timestamp.value_supported);

    root.innerHTML = `<div class="inspection-head">
        <div><div class="eyebrow">INSPECTION RESULT · SEMANTIC DISCOVERY</div><h2>${esc(data.filename)}</h2></div>
        <div class="file-facts"><span>${data.row_count_previewed} rows sampled</span><span>${data.columns.length} columns</span><span>${esc(data.encoding)}</span></div>
      </div>
      <div class="card card-pad discovery-banner">
        <div>
          <div class="step-mark">TIMESTAMP EVIDENCE</div>
          <div class="card-title">${timestamp.column ? esc(timestamp.column) : 'Timestamp unresolved'}</div>
          <div class="card-subtitle">${timestamp.column ? esc(timestamp.reason || 'Header/value evidence requires review.') : 'No column had enough chronological evidence to propose a timestamp.'}</div>
        </div>
        <span class="review-badge">${timestamp.column ? `${confidenceLabel(timestamp.confidence)} · ${timestampSupported ? 'VALUE SUPPORTED' : 'REVIEW'}` : 'NEEDS EVIDENCE'}</span>
      </div>
      <div class="readiness-grid">
        ${readinessItem('Cycle reconstruction', reconstruction, reconstruction ? 'Timestamp and CIP-specific process evidence were detected.' : 'Discovery can identify generic signals, but CIP-specific direction/equipment evidence is still required.')}
        ${readinessItem('Compliance evaluation', compliance, compliance ? 'Core CIP signals detected. An approved recipe is still required.' : 'Confirmed return temperature, flow and conductivity evidence are incomplete.')}
        ${readinessItem('Resource accounting', resources, resources ? 'A dedicated utility or chemical signal was detected.' : 'Do not interpret recirculating process flow as water consumption.')}
      </div>
      <div class="card card-pad organization-review">
        <div class="card-head"><div><div class="card-title">Draft plant organization</div><div class="card-subtitle">${esc(organization.rule)}</div></div><span class="review-badge">${organization.assets.length ? `${organization.assets.length} PROPOSED` : 'IDENTITY NEEDED'}</span></div>
        ${organization.assets.length ? `<div class="asset-proposal-grid">${organization.assets.map(asset => `<div class="asset-proposal">
          <div class="asset-proposal-head"><span class="asset-monogram">${esc(asset.proposed_name.slice(0,2))}</span><div><strong>${esc(asset.proposed_name)}</strong><span>${asset.signal_columns.length} signal${asset.signal_columns.length===1?'':'s'} assigned</span></div></div>
          <div class="signal-list">${asset.signal_columns.slice(0,5).map(signal => `<span>${esc(signal)}</span>`).join('')}${asset.signal_columns.length>5 ? `<span>+${asset.signal_columns.length-5} more</span>` : ''}</div>
        </div>`).join('')}</div>` : `<div class="unresolved-organization"><strong>No equipment groups created</strong><span>CIP Intelligence will not invent equipment identity. Confirm an asset/circuit column or assign signals to equipment before ingestion.</span></div>`}
      </div>
      <div class="card card-pad mapping-review">
        <div class="card-head"><div><div class="card-title">Review discovered signals</div><div class="card-subtitle">Generic measurement recognition is advisory. A temperature is not automatically a CIP return temperature until an engineer confirms process context.</div></div><span class="review-badge">ENGINEERING REVIEW</span></div>
        <div class="table-wrap"><table><thead><tr><th>Source column</th><th>Detected type</th><th>Proposed meaning</th><th>Evidence</th></tr></thead>
        <tbody>${data.columns.map(c => {
          const candidate = c.mapping_candidates?.[0];
          const generic = c.measurement_candidate;
          const isTimestamp = timestamp.column === c.source_column;
          let meaning = '<span class="cell-muted">No safe suggestion</span>';
          let evidence = '<span class="status-chip status-data">Unmapped</span>';
          if (isTimestamp) {
            meaning = 'timestamp';
            evidence = `<span class="status-chip ${timestampSupported ? 'status-ok' : 'status-warn'}">${confidenceLabel(timestamp.confidence)} · ${timestampSupported ? 'Value supported' : 'Review'}</span>`;
          } else if (candidate) {
            meaning = esc(candidate.concept.replace('cip.','').replaceAll('.',' / '));
            evidence = `<span class="status-chip status-warn">Confirm${candidate.source_unit_guess ? ` · ${esc(candidate.source_unit_guess)}` : ''} · ${confidenceLabel(candidate.confidence)}</span>`;
          } else if (generic) {
            meaning = `<strong>${esc(measurementLabel(generic))}</strong><div class="cell-muted">Process context needed</div>`;
            evidence = `<span class="status-chip status-data">Candidate${generic.source_unit_guess ? ` · ${esc(generic.source_unit_guess)}` : ''} · ${confidenceLabel(generic.confidence)}</span>`;
          }
          const profile = c.value_profile || {};
          const type = profile.numeric_fraction >= .8 ? 'Numeric' : profile.numeric_fraction > 0 ? 'Mixed' : 'Text';
          return `<tr><td class="asset-name">${esc(c.source_column)}</td><td>${type}</td><td>${meaning}</td><td>${evidence}</td></tr>`;
        }).join('')}</tbody></table></div>
        <div class="boundary-box">${esc(data.discovery_principle || 'Suggestions require engineering confirmation before analysis.')}</div>
      </div>
      ${renderConfirmation(data, organization, timestamp)}`;
    bindConfirmation(data);
    root.scrollIntoView({behavior:'smooth', block:'start'});
  };
})();
