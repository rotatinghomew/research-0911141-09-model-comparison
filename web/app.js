const state = {context: null, current: null, comparison: null, freezeId: null};
const element = identifier => document.getElementById(identifier);

async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '请求失败');
  return value;
}

function message(value, error = false, target = 'status') {
  element(target).textContent = value;
  element(target).className = error ? 'error' : '';
}

async function guarded(action) {
  try { await action(); } catch (error) { message(error.message, true); }
}

function selected() {
  const selection = {};
  for (const control of state.context.product.options) selection[control[0]] = element('select-' + control[0]).value;
  return {revision: element('revision').value, selection};
}

function chosenCandidates() {
  return [...document.querySelectorAll('#candidates input:checked')].map(input => input.value);
}

function numberOrNull(identifier) {
  const raw = element(identifier).value.trim();
  if (raw === '') return null;
  const value = Number(raw);
  if (!Number.isFinite(value)) throw new Error('数值字段应填写有限数字');
  return value;
}

function comparisonSpec() {
  return {
    candidates: chosenCandidates(),
    metric: element('metric').value,
    threshold: numberOrNull('threshold'),
    fn_cost: numberOrNull('fn-cost'),
    fp_cost: numberOrNull('fp-cost'),
    latency_cost_per_ms: numberOrNull('latency-cost'),
    min_coverage: numberOrNull('min-coverage'),
    max_fn_rate: numberOrNull('max-fn'),
    max_mean_latency_ms: numberOrNull('max-latency')
  };
}

function renderControls() {
  const revision = element('revision').value || state.context.default_revision;
  element('revision').replaceChildren();
  for (const dataset of state.context.datasets) {
    const option = document.createElement('option');
    option.value = dataset.revision;
    option.textContent = dataset.label + ' · ' + dataset.revision.slice(0, 9);
    element('revision').append(option);
  }
  element('revision').value = revision;
  if (!element('revision').value) element('revision').value = state.context.default_revision;
  if (element('controls').children.length) return;
  for (const [key, labelText, choices] of state.context.product.options) {
    const label = document.createElement('label');
    label.textContent = labelText;
    const select = document.createElement('select');
    select.id = 'select-' + key;
    for (const choice of choices) {
      const option = document.createElement('option');
      option.value = choice;
      option.textContent = choice;
      select.append(option);
    }
    select.value = state.context.product.selection[key];
    label.append(select);
    element('controls').append(label);
  }
}

function renderCandidateChoices(candidates) {
  const chosen = chosenCandidates();
  element('candidates').replaceChildren();
  const legend = document.createElement('legend');
  legend.textContent = '参与比较的候选（冻结后不可增删）';
  element('candidates').append(legend);
  for (const candidate of candidates) {
    const label = document.createElement('label');
    label.className = 'inline';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.value = candidate;
    input.checked = chosen.length ? chosen.includes(candidate) : true;
    label.append(input, document.createTextNode(candidate));
    element('candidates').append(label);
  }
}

function renderMetrics() {
  if (element('metric').children.length) return;
  for (const [key, metric] of Object.entries(state.context.metrics)) {
    const option = document.createElement('option');
    option.value = key;
    option.textContent = metric.label;
    element('metric').append(option);
  }
  const defaults = state.context.comparison_defaults;
  for (const [identifier, key] of [['threshold', 'threshold'], ['fn-cost', 'fn_cost'],
      ['fp-cost', 'fp_cost'], ['latency-cost', 'latency_cost_per_ms'],
      ['min-coverage', 'min_coverage']]) element(identifier).value = defaults[key];
}

function displayValue(value) {
  if (value === null || value === undefined) return '未观测';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4);
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

function drawChart(series) {
  const canvas = element('chart');
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, canvas.width, canvas.height);
  const values = series.filter(point => typeof point.y === 'number' && Number.isFinite(point.y));
  context.font = '12px sans-serif';
  if (!values.length) { context.fillText('该范围没有可定量的描述值', 30, 50); return; }
  const low = Math.min(0, ...values.map(point => point.y));
  const high = Math.max(1, ...values.map(point => point.y));
  const scale = value => 250 - (value - low) / (high - low || 1) * 210;
  context.strokeStyle = '#90a5ad';
  context.beginPath(); context.moveTo(45, 20); context.lineTo(45, 250); context.lineTo(940, 250); context.stroke();
  context.fillStyle = '#0a7168';
  values.forEach((point, index) => {
    const position = 55 + index * 875 / values.length;
    context.fillRect(position, Math.min(scale(point.y), scale(0)), Math.max(3, 800 / values.length), Math.max(1, Math.abs(scale(point.y) - scale(0))));
    if (values.length < 12) context.fillText(point.label, position, 275);
  });
  context.fillStyle = '#29434e';
  context.fillText(high.toFixed(2), 0, 45); context.fillText(low.toFixed(2), 0, 250);
  context.fillText('按表中顺序展示描述值；缺测不绘制为零', 50, 20);
}

function drawPairs(pairs) {
  const canvas = element('pair-chart');
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.font = '12px sans-serif';
  if (!pairs.length) { context.fillText('没有可比较的配对', 30, 40); return; }
  const lows = pairs.map(pair => pair.advantage_ci[0]);
  const highs = pairs.map(pair => pair.advantage_ci[1]);
  const low = Math.min(0, ...lows);
  const high = Math.max(0, ...highs);
  const scale = value => 200 - (value - low) / (high - low || 1) * 170;
  const width = 860;
  context.strokeStyle = '#90a5ad';
  context.beginPath(); context.moveTo(50, 15); context.lineTo(50, 200); context.lineTo(930, 200); context.stroke();
  context.strokeStyle = '#9c302d';
  context.beginPath(); context.moveTo(50, scale(0)); context.lineTo(930, scale(0)); context.stroke();
  pairs.forEach((pair, index) => {
    const center = 90 + index * width / pairs.length;
    context.strokeStyle = pair.evidence.status === 'confirmatory' ? '#0a7168' : '#b57b23';
    context.beginPath();
    context.moveTo(center, scale(pair.advantage_ci[0])); context.lineTo(center, scale(pair.advantage_ci[1]));
    context.moveTo(center - 7, scale(pair.advantage_ci[0])); context.lineTo(center + 7, scale(pair.advantage_ci[0]));
    context.moveTo(center - 7, scale(pair.advantage_ci[1])); context.lineTo(center + 7, scale(pair.advantage_ci[1]));
    context.stroke();
    context.fillStyle = pair.distinguishable ? '#0a7168' : '#4c6570';
    context.beginPath(); context.arc(center, scale(pair.advantage), 4, 0, Math.PI * 2); context.fill();
    context.fillStyle = '#29434e';
    context.fillText(pair.left + ' − ' + pair.right, center - 45, 225);
    context.fillText(pair.conclusion, center - 30, 240);
  });
  context.fillStyle = '#29434e';
  context.fillText('正值表示左侧更好；红线为零差异', 55, 15);
}

function table(columnsId, rowsId, rows, onPick) {
  const fields = rows.length ? Object.keys(rows[0]) : [];
  const header = document.createElement('tr');
  for (const field of fields) { const cell = document.createElement('th'); cell.textContent = field; header.append(cell); }
  element(columnsId).replaceChildren(header);
  element(rowsId).replaceChildren();
  for (const row of rows) {
    const line = document.createElement('tr'); line.tabIndex = 0;
    for (const field of fields) {
      const cell = document.createElement('td'); cell.textContent = displayValue(row[field]); line.append(cell);
    }
    if (onPick) {
      line.onclick = () => onPick(row);
      line.onkeydown = event => { if (event.key === 'Enter') line.click(); };
    }
    element(rowsId).append(line);
  }
}

function render(result, heading) {
  state.current = result;
  element('summary').replaceChildren();
  for (const [label, value] of Object.entries(result.view.summary)) {
    const card = document.createElement('article');
    const title = document.createElement('span'); title.textContent = label;
    const amount = document.createElement('strong'); amount.textContent = displayValue(value);
    card.append(title, amount); element('summary').append(card);
  }
  element('notes').replaceChildren();
  for (const note of result.view.notes) {
    const paragraph = document.createElement('p'); paragraph.textContent = note; element('notes').append(paragraph);
  }
  const rows = result.view.rows;
  table('columns', 'rows', rows, row => { element('details').textContent = JSON.stringify(row, null, 2); });
  drawChart(result.view.series);
  element('details').textContent = JSON.stringify({revision: result.revision, selection: result.selection,
    history: result.view.history || null}, null, 2);
  message((heading || '已更新描述') + ' · 数据 ' + result.revision.slice(0, 12));
}

function assessmentText(assessment) {
  const labels = {confirmatory: '确认性比较', exploratory: '探索性比较', mixed: '部分确认、部分探索'};
  return labels[assessment.status] || assessment.status;
}

function renderComparison(result) {
  state.comparison = result;
  const assessment = result.assessment;
  element('assessment').replaceChildren();
  const banner = document.createElement('p');
  banner.className = 'assessment ' + assessment.status;
  const registrationStatus = result.registration ? result.registration.status : 'unregistered';
  const registrationLabels = {confirmatory: '冻结规格一致', deviated: '已偏离冻结规格',
    exploratory: '冻结规格本身为探索性', unregistered: '未绑定冻结记录'};
  banner.textContent = '证据等级：' + assessmentText(assessment)
    + '（' + (registrationLabels[registrationStatus] || registrationStatus) + '）'
    + (assessment.confirmatory_pairs.length ? '；确认对：' + assessment.confirmatory_pairs.join('、') : '');
  element('assessment').append(banner);
  const listing = document.createElement('ul');
  for (const reason of assessment.reasons) {
    const item = document.createElement('li'); item.textContent = reason; listing.append(item);
  }
  if (assessment.reasons.length) element('assessment').append(listing);
  if (result.registration && result.registration.status !== 'confirmatory') {
    const note = document.createElement('p');
    note.className = 'hint';
    note.textContent = '冻结规格状态：' + result.registration.status
      + (result.registration.reasons.length ? ' · ' + result.registration.reasons.join('；') : '');
    element('assessment').append(note);
  }

  element('compare-notes').replaceChildren();
  for (const note of result.notes) {
    const paragraph = document.createElement('p'); paragraph.className = 'hint'; paragraph.textContent = note;
    element('compare-notes').append(paragraph);
  }

  element('joint-hint').textContent = '配对与共同描述只使用所有冻结候选都完整覆盖的 '
    + result.joint_support.clusters.length + ' 个分折簇、' + result.joint_support.units + ' 个单元；'
    + '业务取舍表按各候选自身完整覆盖的簇评估。预测行数不是样本量。';
  const summaryRows = result.summaries.map(row => ({
    '候选': row.candidate, '共同单元': row.joint_units, '共同簇': row.joint_clusters,
    '自身覆盖单元': row.own_units, '预测行数': row.prediction_rows,
    '伪重复倍数': row.pseudo_replication_factor && Number(row.pseudo_replication_factor.toFixed(2)),
    '共同指标值': row.metric_mean, '漏判率': row.false_negative_rate, '误报率': row.false_positive_rate,
    '平均时延ms': row.mean_latency_ms, '覆盖率': row.coverage_rate, '覆盖达标': row.coverage_ok,
    '选型历史': row.selection_history || '无'
  }));
  table('summary-columns', 'summary-rows', summaryRows);

  const pairRows = result.pairs.map(pair => ({
    '左侧': pair.left, '右侧': pair.right, '共同单元': pair.common_units, '共同簇': pair.common_clusters.length,
    '被排除簇': pair.excluded_clusters.length, '均值差': pair.mean_difference,
    '差异区间低': pair.difference_ci[0], '差异区间高': pair.difference_ci[1],
    '左侧优势': pair.advantage, '优势区间': pair.advantage_ci.map(value => Number(value.toFixed(4))).join(' ~ '),
    '结论': pair.conclusion, '证据': pair.evidence.status,
    '区间可靠': pair.interval_reliable,
    '独立簇': pair.evidence.independent_clusters.length
  }));
  table('pair-columns', 'pair-rows', pairRows, row => {
    const pair = result.pairs.find(item => item.left === row['左侧'] && item.right === row['右侧']);
    element('compare-details').textContent = JSON.stringify({
      pair: {'左侧': pair.left, '右侧': pair.right, '结论': pair.conclusion, '证据': pair.evidence,
        '共同簇': pair.common_clusters, '被排除簇': pair.excluded_clusters,
        '独立比较': pair.independent_comparison}, decomposition: pair.decomposition,
      assessment, scope: result.scope, registration: result.registration}, null, 2);
  });
  drawPairs(result.pairs);

  element('decomposition').replaceChildren();
  for (const pair of result.pairs) {
    const block = document.createElement('div');
    const heading = document.createElement('p');
    heading.textContent = pair.left + ' − ' + pair.right + '：';
    block.append(heading);
    const breakdown = document.createElement('ul');
    for (const row of pair.decomposition.dataset_breakdown) {
      const item = document.createElement('li');
      item.textContent = row.dataset + '（' + row.clusters + ' 个簇）均值差 ' + displayValue(row.mean_difference);
      breakdown.append(item);
    }
    const stats = document.createElement('p');
    stats.className = 'hint';
    const share = pair.decomposition.between_dataset_share;
    stats.textContent = '数据集之间解释 ' + (share === null ? '不可估计' : (share * 100).toFixed(0) + '%')
      + ' 的簇间差异；重复分折标准差的占比 '
      + (pair.decomposition.repeat_share === null ? '不可估计' : (pair.decomposition.repeat_share * 100).toFixed(0) + '%')
      + '。重复折不是新增证据，只会缩小表面方差。';
    block.append(breakdown, stats);
    if (!pair.interval_reliable) {
      const warning = document.createElement('p');
      warning.className = 'hint';
      warning.textContent = '该对只有 ' + pair.common_clusters.length
        + ' 个共同分折簇，区间不能反映分折之间的不确定性，应视为描述值而非推断。';
      block.append(warning);
    }
    element('decomposition').append(block);
  }

  element('constraints').replaceChildren();
  const constraintRows = result.constraints.map(row => ({
    '候选': row.candidate, '评估单元': row.units, '指标值': row.metric_mean,
    '漏判率': row.false_negative_rate, '平均时延ms': row.mean_latency_ms,
    '覆盖达标': row.coverage_ok, '业务可接受': row.acceptable,
    '未通过项': row.failures.join('、') || '无'
  }));
  table('constraint-columns', 'constraint-rows', constraintRows);
  const acceptable = document.createElement('p');
  acceptable.textContent = result.acceptable.length
    ? '在当前成本与约束下可接受的候选：' + result.acceptable.join('、') + '。这不等于统计上可区分。'
    : '没有候选同时满足成本、漏判与覆盖约束；应扩充证据或调整业务约束，而不是按总榜选优。';
  element('constraints').append(acceptable);
  if (result.undecided.length) {
    const undecided = document.createElement('p');
    undecided.textContent = '不足以区分：' + result.undecided.map(row => row.left + ' / ' + row.right).join('、')
      + '。允许把“没有可证实差异”作为结论。';
    element('constraints').append(undecided);
  }

  renderMisjudgments(result);
  renderFolds(result);

  element('compare-status').className = '';
  element('compare-status').textContent = '比较完成 · ' + result.pairs.length + ' 个候选对 · '
    + result.joint_support.clusters.length + ' 个共同分折簇 · 证据等级 ' + assessmentText(assessment)
    + (result.registration && result.registration.status === 'deviated'
      ? ' · 已偏离冻结规格，只作事后探索' : '');
  element('compare-details').textContent = JSON.stringify({
    engine: result.engine, metric: result.metric, spec: result.spec, registration: result.registration,
    assessment: result.assessment, scope: result.scope, coverage: result.coverage,
    joint_support: result.joint_support}, null, 2);
}

function renderMisjudgments(result) {
  const filter = element('misjudgment-filter');
  const previous = filter.value;
  filter.replaceChildren();
  const all = document.createElement('option'); all.value = ''; all.textContent = '全部候选';
  filter.append(all);
  for (const candidate of result.spec.candidates) {
    const option = document.createElement('option'); option.value = candidate; option.textContent = candidate;
    filter.append(option);
  }
  filter.value = [...filter.options].some(option => option.value === previous) ? previous : '';
  const rows = result.misjudgments.filter(row => !filter.value || row.candidate === filter.value);
  table('misjudgment-columns', 'misjudgment-rows', rows, row => {
    element('compare-details').textContent = JSON.stringify(row, null, 2);
  });
  if (!rows.length) {
    element('misjudgment-columns').replaceChildren(); element('misjudgment-rows').replaceChildren();
    const line = document.createElement('tr');
    const cell = document.createElement('td'); cell.textContent = '当前筛选下没有误判记录。';
    line.append(cell); element('misjudgment-rows').append(line);
  } else if (result.misjudgments_truncated) {
    message('误判行超过 2000 条，已截断显示；区间仍使用完整数据。', false, 'compare-status');
  }
}

function renderFolds(result) {
  const rows = result.folds.map(fold => ({
    '分折簇': fold.cluster, '数据集': fold.dataset, '观测数': fold.observations.length,
    '分折': fold.splits.map(split => split.split_id + '（repeat ' + split.repeat + ' / fold ' + split.fold
      + ' / ' + split.release + '）').join('；'),
    '覆盖': Object.entries(fold.coverage).map(([candidate, value]) =>
      candidate + ' ' + value.covered_units + '/' + value.expected_units).join('；')
  }));
  table('fold-columns', 'fold-rows', rows, row => {
    const fold = result.folds.find(item => item.cluster === row['分折簇']);
    element('compare-details').textContent = JSON.stringify(fold, null, 2);
  });
}

function renderFreezeState() {
  const frozen = state.freezeId ? state.context.freezes.find(item => item.id === state.freezeId) : null;
  if (!frozen) {
    element('freeze-state').textContent = '未绑定冻结记录：当前比较会记为探索。';
    return;
  }
  element('freeze-state').textContent = '已绑定冻结 #' + frozen.id + '（' + frozen.status + '）：'
    + frozen.title + '。改变候选、指标或范围会降级为事后比较。';
}

async function refresh() {
  state.context = await request('/api/context');
  renderControls();
  renderMetrics();
  element('archive').replaceChildren();
  for (const notebook of state.context.notebooks) {
    const button = document.createElement('button'); button.textContent = notebook.title + ' #' + notebook.id;
    button.onclick = () => guarded(async () => {
      const saved = await request('/api/notebooks/' + notebook.id);
      element('revision').value = saved.revision;
      for (const [key, value] of Object.entries(saved.selection)) {
        const control = element('select-' + key); if (control) control.value = value;
      }
      render(saved.result, '已读取保存的描述，不随新数据变化');
    });
    element('archive').append(button);
  }
  element('freeze-archive').replaceChildren();
  for (const freeze of state.context.freezes) {
    const button = document.createElement('button');
    button.textContent = '冻结 #' + freeze.id + ' · ' + freeze.title + '（' + freeze.status + '）';
    button.onclick = () => guarded(async () => {
      const record = await request('/api/freezes/' + freeze.id);
      state.freezeId = record.id;
      element('revision').value = record.revision;
      for (const [key, value] of Object.entries(record.selection)) {
        const control = element('select-' + key); if (control) control.value = value;
      }
      element('metric').value = record.spec.metric;
      for (const [identifier, key] of [['threshold', 'threshold'], ['fn-cost', 'fn_cost'],
          ['fp-cost', 'fp_cost'], ['latency-cost', 'latency_cost_per_ms'],
          ['min-coverage', 'min_coverage']]) element(identifier).value = record.spec[key];
      element('max-fn').value = record.spec.max_fn_rate === null ? '' : record.spec.max_fn_rate;
      element('max-latency').value = record.spec.max_mean_latency_ms === null ? '' : record.spec.max_mean_latency_ms;
      renderCandidateChoices(record.spec.candidates);
      for (const input of document.querySelectorAll('#candidates input')) input.checked = record.spec.candidates.includes(input.value);
      renderFreezeState();
      message('已读取冻结 #' + record.id + '，可运行比较');
    });
    element('freeze-archive').append(button);
  }
  element('comparison-archive').replaceChildren();
  for (const comparison of state.context.comparisons) {
    const button = document.createElement('button');
    button.textContent = '比较 #' + comparison.id + ' · ' + comparison.title;
    button.onclick = () => guarded(async () => {
      const record = await request('/api/comparisons/' + comparison.id);
      renderComparison(record.result);
      message('已读取保存的比较材料，不随新数据变化');
      if (record.deviated) message('该比较在冻结后偏离了规格，记录为探索性。', false, 'compare-status');
    });
    element('comparison-archive').append(button);
  }
  renderFreezeState();
}

element('inspect').onclick = () => guarded(async () => {
  render(await request('/api/explore', selected()));
});
element('save').onclick = () => guarded(async () => {
  const saved = await request('/api/notebooks', {...selected(), title: element('title').value});
  await refresh(); render(saved.result, '已保存记录 #' + saved.id);
});
element('raw').onclick = () => guarded(async () => {
  const revision = state.current ? state.current.revision : element('revision').value;
  element('details').textContent = JSON.stringify(await request('/api/data?revision=' + encodeURIComponent(revision)), null, 2);
});
element('upload').onchange = () => guarded(async () => {
  const file = element('upload').files[0]; if (!file) return;
  const data = JSON.parse(await file.text());
  const uploaded = await request('/api/datasets', {label: file.name, data});
  await refresh(); element('revision').value = uploaded.revision;
  render(await request('/api/explore', selected()), '已导入独立数据版本');
  const uploaded_data = await request('/api/data?revision=' + encodeURIComponent(uploaded.revision));
  renderCandidateChoices(uploaded_data.candidates.map(row => row.id));
});
element('freeze').onclick = () => guarded(async () => {
  const record = await request('/api/freezes', {...selected(), title: element('title').value,
    spec: comparisonSpec()});
  state.freezeId = record.id;
  await refresh();
  message('已冻结 #' + record.id + ' · ' + (record.status === 'confirmatory'
    ? '证据范围可支撑确认性比较' : '探索性：' + record.reasons.join('；')));
});
element('compare').onclick = () => guarded(async () => {
  if (!state.freezeId) message('未绑定冻结记录：本次比较会记为探索。请先冻结候选、指标与证据范围。', false, 'compare-status');
  const response = await request('/api/compare', {...selected(),
    freeze_id: state.freezeId, spec: comparisonSpec()});
  renderComparison(response.result);
});
element('save-comparison').onclick = () => guarded(async () => {
  if (!state.comparison) throw new Error('请先运行比较');
  const saved = await request('/api/comparisons', {...selected(),
    freeze_id: state.freezeId, spec: comparisonSpec(), title: element('title').value});
  await refresh();
  renderComparison(saved.result);
  message('已保存比较材料 #' + saved.id + (saved.deviated ? '（偏离冻结规格，记为探索）' : ''));
});
element('misjudgment-filter').onchange = () => { if (state.comparison) renderMisjudgments(state.comparison); };
element('export').onclick = () => guarded(async () => {
  if (!state.current) throw new Error('请先查看一个范围');
  const data = await request('/api/data?revision=' + encodeURIComponent(state.current.revision));
  const blob = new Blob([JSON.stringify({description: state.current, data}, null, 2)], {type: 'application/json'});
  const url = URL.createObjectURL(blob); const link = document.createElement('a');
  link.href = url; link.download = 'analysis-' + state.current.revision.slice(0, 12) + '.json'; link.click();
  URL.revokeObjectURL(url);
});
guarded(async () => {
  await refresh();
  const result = await request('/api/explore', selected());
  render(result);
  const data = await request('/api/data?revision=' + encodeURIComponent(result.revision));
  renderCandidateChoices(data.candidates.map(row => row.id));
  renderFreezeState();
});