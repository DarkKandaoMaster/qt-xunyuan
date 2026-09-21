const $ = selector => document.querySelector(selector);
const pollingJobs = new Set();
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const fmtBytes = n => n > 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(2)} GB` : `${(n / 1024 ** 2).toFixed(1)} MB`;
const PAGE_SIZE = 20;
const listPages = {sources: 1, analyzed: 1, accepted: 1, processed: 1, rejected: 1};
const listFilters = {sources: '', analyzed: ''};

async function api(url, options = {}) {
  const response = await fetch(url, {headers: {'Content-Type': 'application/json'}, ...options});
  const payload = await response.json();
  if (!response.ok || payload.ok === false) throw new Error(payload.error || '请求失败');
  return payload;
}

function escapeHtml(value) {
  const element = document.createElement('div');
  element.textContent = value ?? '';
  return element.innerHTML;
}

function notice(text, error = false) {
  const element = $('#notice');
  element.textContent = text;
  element.className = `notice show${error ? ' error' : ''}`;
}

function humanError(raw = '') {
  if (/sign in to confirm|not a bot/i.test(raw)) return 'YouTube 要求登录确认，无法匿名下载；请换一个公开来源。';
  if (/Requested format is not available/i.test(raw)) return '来源没有可用的代理格式，请换来源或使用公开直链。';
  if (/HTTP Error 403/i.test(raw)) return '来源拒绝访问（HTTP 403），请换公开可下载的来源。';
  if (/HTTP Error 404/i.test(raw)) return '来源地址不存在（HTTP 404）。';
  if (/timed out|timeout/i.test(raw)) return '操作超时，请检查网络后重试。';
  if (/服务重启/.test(raw)) return raw;
  return raw.split('\n').filter(Boolean).slice(-1)[0] || '操作失败，请查看完整错误。';
}

function metric(name, value) { return `<div class="metric"><span>${name}</span><strong>${value}</strong></div>`; }

function renderPagination(selector, key, data) {
  const pageCount = Math.max(1, Math.ceil(Number(data.total || 0) / Number(data.page_size || PAGE_SIZE)));
  listPages[key] = Number(data.page || 1);
  $(selector).innerHTML = `<span>共 ${Number(data.total || 0)} 条 · 第 ${listPages[key]} / ${pageCount} 页</span><button onclick="changePage('${key}',${listPages[key] - 1})" ${listPages[key] <= 1 ? 'disabled' : ''}>上一页</button><button onclick="changePage('${key}',${listPages[key] + 1})" ${listPages[key] >= pageCount ? 'disabled' : ''}>下一页</button>`;
}

function changePage(key, page) {
  listPages[key] = Math.max(1, page);
  return ['sources', 'analyzed'].includes(key) ? loadSources() : loadQueues();
}

async function loadRules() {
  const data = await api('/api/rules');
  const options = ['<option value="">目标单元（可选）</option>', ...Object.entries(data.units).map(([id, unit]) =>
    `<option value="${id}">${id} ${unit.name}${unit.requires_confirmation ? ' ⚠' : ''}</option>`)].join('');
  $('#discover-unit').innerHTML = options;
  $('#import-unit').innerHTML = options;
  const bucketOptions = ['<option value="">全部分类</option>', ...Object.entries(data.buckets).map(([id, bucket]) => `<option value="${id}">${id} ${bucket.name}</option>`), '<option value="unassigned">未分类</option>'].join('');
  $('#sources-bucket-filter').innerHTML = bucketOptions;
  $('#analyzed-bucket-filter').innerHTML = bucketOptions;
}

async function loadDashboard() {
  const {dashboard: data, tools, quota, traffic_warning_bytes: warningBytes} = await api('/api/dashboard');
  $('#metrics').innerHTML = [metric('已发现候选', data.sources), metric('待审核', data.counts.WAITING_REVIEW || 0),
    metric('今日审核', data.reviewed_today), metric('已接受', data.accepted), metric('最终 QA 通过', data.qa_passed),
    metric('预计收入', `¥${data.estimated_income}`), metric('今日下载', fmtBytes(data.traffic.today))].join('');
  $('#tools').innerHTML = Object.entries(tools).map(([name, ok]) => `<span class="tool ${ok ? 'ok' : 'bad'}">${ok ? '●' : '○'} ${name}</span>`).join('');
  if (data.traffic.today > warningBytes) notice(`今日下载已超过软上限：${fmtBytes(data.traffic.today)}。系统仅警告，不会强制停止。`, true);
  $('#quota').innerHTML = quota.map(item => {
    const first = Math.min(100, item.first_person.actual / Math.max(1, item.first_person.target) * 100);
    const third = Math.min(100, item.third_person.actual / Math.max(1, item.third_person.target) * 100);
    return `<div class="quota-row"><strong>${item.bucket} ${item.name}</strong><div class="quota-item"><span>第一人称 ${item.first_person.actual}/${item.first_person.target}</span><div class="progress"><i style="width:${first}%"></i></div></div><div class="quota-item"><span>第三人称 ${item.third_person.actual}/${item.third_person.target}</span><div class="progress"><i style="width:${third}%"></i></div></div><span class="badge warn">最缺：${item.largest_gap}</span></div>`;
  }).join('');
}

async function loadSources(startPolling = true) {
  const sourceBucket = encodeURIComponent(listFilters.sources);
  const analyzedBucket = encodeURIComponent(listFilters.analyzed);
  const [candidateData, analyzedData] = await Promise.all([
    api(`/api/sources?view=candidate&bucket=${sourceBucket}&page=${listPages.sources}&page_size=${PAGE_SIZE}`),
    api(`/api/sources?view=analyzed&bucket=${analyzedBucket}&page=${listPages.analyzed}&page_size=${PAGE_SIZE}`)
  ]);
  const items = candidateData.items;
  $('#sources').innerHTML = items.length ? items.map(source => {
    const active = Boolean(source.active_job_id);
    const downloading = source.active_job_kind === 'proxy';
    const analyzing = source.active_job_kind === 'analyze';
    const error = source.error ? `<small class="source-error" title="${escapeHtml(source.error)}">${escapeHtml(humanError(source.error))}</small>` : '';
    return `<tr data-source-id="${source.id}"><td><strong>${escapeHtml(source.title || source.url)}</strong><small>${escapeHtml(source.uploader || source.url)}</small></td><td>${source.target_unit || '—'}<small>${escapeHtml(source.search_query || '')}</small></td><td>${source.duration ? `${Number(source.duration).toFixed(1)}s` : '—'}<small>${source.resolution || ''}</small></td><td>${Number(source.source_score || 0).toFixed(0)}</td><td><span class="badge ${source.status === 'ERROR' ? 'fail' : active ? 'warn' : ''}">${source.status}</span>${error}</td><td><div class="toolbox"><button class="secondary" onclick="proxy(${source.id},this)" ${source.proxy_path || active ? 'disabled' : ''}>${downloading ? '下载中…' : source.proxy_path ? '代理已就绪' : source.status === 'ERROR' ? '重试下载' : '下载代理'}</button><button onclick="analyze(${source.id},this)" ${!source.proxy_path || active ? 'disabled' : ''}>${analyzing ? '分析中…' : '镜头分析'}</button></div></td></tr>`;
  }).join('') : '<tr><td colspan="6" class="empty">尚无来源。可自动搜索或导入一个公开 URL。</td></tr>';
  $('#analyzed-sources').innerHTML = analyzedData.items.length ? analyzedData.items.map(source => {
    const error = source.error ? `<small class="source-error" title="${escapeHtml(source.error)}">${escapeHtml(humanError(source.error))}</small>` : '';
    return `<tr data-source-id="${source.id}"><td><strong>${escapeHtml(source.title || source.url)}</strong><small>${escapeHtml(source.uploader || source.url)}</small></td><td>${source.target_unit || '—'}<small>${escapeHtml(source.search_query || '')}</small></td><td>${source.duration ? `${Number(source.duration).toFixed(1)}s` : '—'}<small>${source.resolution || ''}</small></td><td>${Number(source.candidate_count || 0)}</td><td><span class="badge pass">已分析</span>${error}</td><td><button class="secondary" onclick="restoreSource(${source.id},this)">移回候选来源</button></td></tr>`;
  }).join('') : '<tr><td colspan="6" class="empty">暂无已分析来源。</td></tr>';
  renderPagination('#sources-pagination', 'sources', candidateData);
  renderPagination('#analyzed-pagination', 'analyzed', analyzedData);
  if (startPolling) for (const source of items) if (source.active_job_id) pollJob(Number(source.active_job_id), source.active_job_kind);
}

async function pollJob(jobId, kind) {
  if (pollingJobs.has(jobId)) return;
  pollingJobs.add(jobId);
  try {
    while (true) {
      await sleep(1500);
      const {job} = await api(`/api/jobs/${jobId}`);
      if (job.status === 'DONE') { notice(job.result?.message || (kind === 'proxy' ? '代理下载完成。' : '镜头分析完成。')); break; }
      if (job.status === 'FAILED') { notice(humanError(job.error), true); break; }
      await loadSources(false);
    }
  } catch (error) {
    notice(`后台任务状态读取失败：${error.message}。点击“刷新”可继续查看。`, true);
  } finally {
    pollingJobs.delete(jobId);
    await Promise.all([loadSources(), loadDashboard(), loadQueues()]);
  }
}

async function startSourceJob(kind, id, button) {
  button.disabled = true;
  button.textContent = '正在排队…';
  try {
    const data = await api(`/api/sources/${id}/${kind}`, {method: 'POST', body: '{}'});
    notice(kind === 'proxy' ? '代理下载已进入后台；可继续浏览或处理其他来源。' : '镜头分析已进入后台；可继续操作其他来源。');
    await loadSources(false);
    pollJob(Number(data.job_id), kind);
  } catch (error) {
    notice(humanError(error.message), true);
    button.disabled = false;
    button.textContent = kind === 'proxy' ? '重试下载' : '镜头分析';
    await loadSources();
  }
}

function proxy(id, button) { return startSourceJob('proxy', id, button); }
function analyze(id, button) { return startSourceJob('analyze', id, button); }
async function restoreSource(id, button) { button.disabled = true; try { await api(`/api/sources/${id}/analysis-state`, {method: 'POST', body: JSON.stringify({completed: false})}); notice('已移回候选来源列表。'); await loadSources(); } catch (error) { notice(error.message, true); button.disabled = false; } }

async function loadQueues() {
  const [accepted, processed, rejected] = await Promise.all([
    api(`/api/final-candidates?state=pending&page=${listPages.accepted}&page_size=${PAGE_SIZE}`),
    api(`/api/final-candidates?state=processed&page=${listPages.processed}&page_size=${PAGE_SIZE}`),
    api(`/api/candidates?status=REJECTED&page=${listPages.rejected}&page_size=${PAGE_SIZE}`)
  ]);
  $('#accepted').innerHTML = accepted.items.length ? accepted.items.map(candidate => {
    const finalized = candidate.qa_status === 'PASS';
    const qa = candidate.qa_status === 'TRIM_REQUIRED' ? '<small><span class="badge warn">需按主体重新切分</span></small>' : candidate.qa_status && candidate.qa_status !== 'PASS' ? `<small><span class="badge fail">QA ${candidate.qa_status}</span></small>` : finalized ? '<small><span class="badge warn">已处理，等待导出</span></small>' : '';
    return `<tr><td>#${candidate.id}<small>${escapeHtml(candidate.source_title)}</small></td><td>${candidate.candidate_unit || '—'} · ${candidate.candidate_viewpoint === 'first_person' ? '第一人称' : candidate.candidate_viewpoint === 'third_person' ? '第三人称' : '待定'}</td><td>${Number(candidate.duration).toFixed(1)}s${qa}</td><td><button onclick="finalize(${candidate.id},this)">${finalized ? '重新处理' : '最终处理'}</button></td></tr>`;
  }).join('') : '<tr><td colspan="4" class="empty">暂无待最终处理候选</td></tr>';
  $('#processed').innerHTML = processed.items.length ? processed.items.map(candidate => `<tr><td>#${candidate.id}<small>${escapeHtml(candidate.source_title)}</small></td><td>${candidate.candidate_unit || '—'} · ${candidate.candidate_viewpoint === 'first_person' ? '第一人称' : '第三人称'}</td><td>${new Date(candidate.exported_at).toLocaleString('zh-CN')}</td><td><button class="secondary" onclick="restoreProcessed(${candidate.id},this)">移回最终处理</button></td></tr>`).join('') : '<tr><td colspan="4" class="empty">暂无已处理候选。</td></tr>';
  $('#rejected').innerHTML = rejected.items.length ? rejected.items.map(candidate => `<tr><td>#${candidate.id}<small>${escapeHtml(candidate.source_title)}</small></td><td>${candidate.candidate_unit || '—'}</td><td><button class="secondary" onclick="restore(${candidate.id},this)">恢复</button></td></tr>`).join('') : '<tr><td colspan="3" class="empty">暂无拒绝记录</td></tr>';
  renderPagination('#accepted-pagination', 'accepted', accepted);
  renderPagination('#processed-pagination', 'processed', processed);
  renderPagination('#rejected-pagination', 'rejected', rejected);
}

async function finalize(id, button) { button.disabled = true; notice('正在获取最终源、剪片并执行最终 QA；大文件可能需要较长时间…'); try { const data = await api(`/api/candidates/${id}/finalize`, {method: 'POST', body: '{}'}); const trim = data.qa_status === 'TRIM_REQUIRED'; notice(trim ? '检测到主体持续离场：整条不判失败，请把来源移回候选列表后重新运行镜头分析。' : `最终 QA：${data.qa_status}${data.final_path ? '，已进入交付目录' : ''}`, data.qa_status !== 'PASS'); await Promise.all([loadQueues(), loadDashboard()]); } catch (error) { notice(error.message, true); button.disabled = false; } }
async function restore(id, button) { button.disabled = true; try { await api(`/api/candidates/${id}/review`, {method: 'POST', body: JSON.stringify({decision: 'RESTORE', notes: '从拒绝列表恢复'})}); notice('已恢复到人工审核队列。'); await Promise.all([loadQueues(), loadDashboard()]); } catch (error) { notice(error.message, true); button.disabled = false; } }
async function restoreProcessed(id, button) { button.disabled = true; try { await api(`/api/candidates/${id}/export-state`, {method: 'POST', body: JSON.stringify({processed: false})}); notice('已移回最终处理列表。'); await loadQueues(); } catch (error) { notice(error.message, true); button.disabled = false; } }

async function submitForm(form, url) {
  const data = Object.fromEntries(new FormData(form)); if (data.limit) data.limit = Number(data.limit); notice('处理中…'); [...form.elements].forEach(element => element.disabled = true);
  try { const result = await api(url, {method: 'POST', body: JSON.stringify(data)}); const excluded = Number(result.excluded || 0); notice(result.created === false ? '该来源已存在，未重复导入。' : `完成：新增 ${result.created ?? 1}，发现 ${result.found ?? 1}${excluded ? `，已过滤 ${excluded} 条超过 ${Number(result.max_duration_seconds || 600) / 60} 分钟的视频` : ''}。`); form.reset(); await Promise.all([loadSources(), loadDashboard()]); }
  catch (error) { notice(error.message, true); }
  finally { [...form.elements].forEach(element => element.disabled = false); }
}

$('#discover-form').addEventListener('submit', event => { event.preventDefault(); submitForm(event.currentTarget, '/api/sources/discover'); });
$('#import-form').addEventListener('submit', event => { event.preventDefault(); submitForm(event.currentTarget, '/api/sources/import'); });
$('#sources-bucket-filter').onchange = event => { listFilters.sources = event.target.value; listPages.sources = 1; loadSources(); };
$('#analyzed-bucket-filter').onchange = event => { listFilters.analyzed = event.target.value; listPages.analyzed = 1; loadSources(); };
$('#export').onclick = async () => { try { const data = await api('/api/export', {method: 'POST', body: '{}'}); notice(`已导出 ${data.rows} 条：${data.path}`); await Promise.all([loadQueues(), loadDashboard()]); } catch (error) { notice(error.message, true); } };
$('#refresh').onclick = () => Promise.all([loadSources(), loadDashboard(), loadQueues()]);
Promise.all([loadRules(), loadSources(), loadDashboard(), loadQueues()]).catch(error => notice(error.message, true));
