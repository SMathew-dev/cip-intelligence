/* V1.2 semantic discovery presentation layer.
   This augments inspection evidence only; it does not approve mappings. */
(function () {
  function measurementLabel(candidate) {
    if (!candidate) return null;
    return candidate.measurement_type.replaceAll('_', ' ');
  }

  function confidenceLabel(value) {
    if (value == null) return '';
    return `${Math.round(Number(value) * 100)}%`;
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
        <div class="boundary-box" style="margin-top:18px">${esc(data.discovery_principle || 'Suggestions require engineering confirmation before analysis.')}</div>
      </div>`;
    root.scrollIntoView({behavior:'smooth', block:'start'});
  };
})();
