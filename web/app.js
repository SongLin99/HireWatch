const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const sourceNames = {baidu:'百度',meituan:'美团',tencent:'腾讯',bytedance:'字节跳动',alibaba:'阿里巴巴',zhipu:'智谱',mihoyo:'米哈游',minimax:'MiniMax',deepseek:'DeepSeek',shailab:'上海 AI Lab',kimi:'月之暗面 Kimi',stepfun:'阶跃星辰'};
const sourceLetters = {baidu:'百',meituan:'美',tencent:'腾',bytedance:'字',alibaba:'阿',zhipu:'智',mihoyo:'米',minimax:'M',deepseek:'D',shailab:'AI',kimi:'K',stepfun:'阶'};
let currentView = 'jobs', company = '', page = 1, total = 0, settings, currentJobs = [], requestId = 0, toastTimer;
let clearProvider = null;
const clearAIProviders=new Set(),aiModelsByProvider={openrouter:'deepseek/deepseek-v3.2',deepseek:'deepseek-flash'};
let selectedAIProvider='openrouter',modelCatalogRequest=0,initialLibraryEmpty=null,aiCatalog=[];
let activeMatch = null, matchResult = null, matchTimer = null, facets = null, statusBusy = false;
let jobsController=null,jobsPendingKey='',jobsLoadedKey='',jobsRevision=null,jobsLoadedRevision=null,facetsBusy=false;
let matchController=null,matchRequestId=0;
let savedVersion=0,detailJob=null;
const pendingSaves=new Set(),savedMutations=new Map(),viewFilters={};
const filterInputs=['search','city','kind','job-status','role-type','degree','tag-filter','department','ai-filter','available-days','available-months','recent','sort'];
function savedButton(job,compact=false){return `<button class="${compact?'save-button':'secondary job-save-action'} ${job.saved?'saved':''}" data-job-save="${esc(job.id)}" data-job-title="${esc(job.title)}" data-compact="${compact}" aria-pressed="${!!job.saved}" aria-label="${job.saved?'取消收藏':'收藏'} ${esc(job.title)}" title="${job.saved?'取消收藏':'收藏到已收藏岗位'}" ${pendingSaves.has(job.id)?'disabled':''}>${job.saved?'★':'☆'}${compact?'':job.saved?' 已收藏':' 收藏'}</button>`;}
function reconcileSaved(job,version){const local=savedMutations.get(job.id);if(local&&local.version>version)job.saved=local.saved;return job;}
function syncSavedButtons(id,saved){document.querySelectorAll('[data-job-save]').forEach(button=>{if(button.dataset.jobSave!==id)return;if(saved!==undefined){button.classList.toggle('saved',saved);button.setAttribute('aria-pressed',String(!!saved));button.setAttribute('aria-label',`${saved?'取消收藏':'收藏'} ${button.dataset.jobTitle}`);button.title=saved?'取消收藏':'收藏到已收藏岗位';button.textContent=(saved?'★':'☆')+(button.dataset.compact==='true'?'':saved?' 已收藏':' 收藏');}button.disabled=pendingSaves.has(id);});}
async function toggleSaved(id,wasSaved){
  if(pendingSaves.has(id))return;
  pendingSaves.add(id);syncSavedButtons(id);
  try{
    const result=await api('/api/jobs/'+encodeURIComponent(id)+'/saved',{method:'PATCH',body:JSON.stringify({saved:!wasSaved})});
    savedMutations.set(id,{version:++savedVersion,saved:result.saved});
    const jobs=[...currentJobs,...(matchResult?.results||[]).map(r=>r.job),...(matchResult?.unscored||[]).map(r=>r.job),detailJob].filter(Boolean);
    jobs.forEach(job=>{if(job.id===id)job.saved=result.saved;});syncSavedButtons(id,result.saved);
    if(Number.isFinite(result.saved_count)){$('saved-nav').textContent=result.saved_count;$('stat-saved').textContent=result.saved_count.toLocaleString();}else loadStatus();
    jobsLoadedKey='';
    if(['jobs','saved'].includes(currentView))await loadJobs({force:true,quiet:true});
  }catch(error){toast(error.message,true);}
  finally{pendingSaves.delete(id);syncSavedButtons(id);}
}
document.addEventListener('click',event=>{const button=event.target.closest('[data-job-save]');if(button)toggleSaved(button.dataset.jobSave,button.getAttribute('aria-pressed')==='true');});
const date = (value, short = false) => value ? String(value).slice(0, short ? 10 : 19).replace('T',' ') : '尚无记录';
function safeURL(value) { try { const u = new URL(value); return u.protocol === 'https:' ? u.href : '#'; } catch { return '#'; } }
function sourceCoverage(source) {
  const c=source.coverage;
  if(!c?.complete)return '';
  const entries=c.entries||[];
  return `<details class="source-coverage"><summary>${source.last_error?"上次成功检查":"已检查"} ${esc(c.checked)} 条 · ${entries.length||1} 个入口</summary><p>${esc(c.scope)}${c.details!=null?`<br>详情核查 ${esc(c.details)} 条` : ''}${c.empty_details?`<br>官网未填正文 ${esc(c.empty_details)} 条，已核对公开岗位信息`:''}${c.excluded!=null?` · 排除 ${esc(c.excluded)} 个` : ''}${c.cached_details!=null?`<br>复用详情 ${esc(c.cached_details)} 条 · 本轮请求 ${esc(c.fetched_details)} 条<br>列表变化立即复查；其余详情每小时复查` : ''}</p>${entries.map((e,i)=>`<p><a href="${esc(safeURL(e.url))}" target="_blank" rel="noopener noreferrer">官方入口 ${i+1} ↗</a> · 检查 ${esc(e.checked)} 条${e.reported!=null?` / 官网 ${esc(e.reported)} 条`:''}</p>`).join('')}${entries.length>1?'<p>检查数按入口累计，收录按岗位 ID 去重。</p>':''}</details>${(c.warnings||[]).map(w=>`<p class="source-error">${esc(w)}</p>`).join('')}`;
}
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'Content-Type':'application/json', ...(options.headers||{})}});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败（${response.status}）`);
  return data;
}
function toast(text, error = false) {
  clearTimeout(toastTimer); $('toast').textContent = text; $('toast').classList.toggle('error', error); $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 5000);
}
function queryParams() {
  const params = new URLSearchParams({q:$('search').value, company, city:$('city').value, status:$('job-status').value, kind:$('kind').value,
    saved:currentView==='saved', matching:$('matching').checked, job_type:$('role-type').value,degree:$('degree').value,tag:$('tag-filter').value,
    department:$('department').value,ai_status:$('ai-filter').value,days:$('available-days').value,months:$('available-months').value,recent:$('recent').value,sort:$('sort').value});
  return params;
}
async function loadJobs({force=false,quiet=false}={}) {
  const params = queryParams();
  $('export').href = '/api/export?' + params.toString();
  params.set('page',page); params.set('page_size',20);
  const key=params.toString();
  if(!force && (key===jobsPendingKey || key===jobsLoadedKey && jobsLoadedRevision===jobsRevision))return;
  jobsController?.abort();jobsController=new AbortController();
  const controller=jobsController,id=++requestId,revision=jobsRevision,saveVersion=savedVersion;jobsPendingKey=key;
  document.querySelector('.jobs-table').setAttribute('aria-busy','true');
  if(!quiet)$('result-count').textContent='正在筛选岗位…';
  try {
    const data = await api('/api/jobs?' + params,{signal:controller.signal});
    if (id !== requestId) return;
    currentJobs = data.items.map(job=>reconcileSaved(job,saveVersion)); total = data.total;jobsLoadedKey=key;jobsLoadedRevision=revision;
    const filterCount = ['role-type','degree','tag-filter','department','ai-filter','available-days','available-months','recent'].filter(id=>$(id).value && $(id).value!=='0').length;
    $('filter-count').textContent = filterCount ? `${filterCount} 项已启用` : '';
    if (page>1 && !currentJobs.length && total) { page=1; return loadJobs({force:true}); }
    $('result-count').textContent = `共 ${total.toLocaleString()} 个${currentView==='saved'?'收藏':'匹配'}岗位`;
    $('empty').hidden = !!currentJobs.length; document.querySelector('.jobs-table').hidden = !currentJobs.length;
    $('empty-text').textContent = $('search').value || company || $('city').value || $('matching').checked || filterCount || $('kind').value || currentView==='saved'&&$('job-status').value ? '试试调整关键词、城市或公司筛选。' : currentView==='saved'?'还没有收藏岗位。可在岗位看板或 AI 匹配结果中点击收藏。':'点击“检查最新岗位”，首次采集完成后岗位会出现在这里。';
    $('job-list').innerHTML = currentJobs.map((job,index) => `<article class="job-row" data-job-id="${esc(job.id)}">
      <div class="job-heading"><span class="company-logo ${esc(job.source)}">${esc(sourceLetters[job.source])}</span><div>
      <button class="job-title" data-detail="${index}">${esc(job.title)}</button>
      <p class="job-subtitle">${esc(job.company)}${job.department?' · '+esc(job.department):''}${job.category?' · '+esc(job.category):''}</p>
      <div class="job-tags">${(job.tags||[]).slice(0,4).map(t=>`<button class="tag ai-tag" data-tag="${esc(t)}">${esc(t)}</button>`).join('')}${(job.labels||[]).map(t=>`<button class="tag custom-tag" data-tag="${esc(t)}">${esc(t)}</button>`).join('')}${job.ai_status==='complete'?'<span class="tag parsed">✦ 已解析</span>':'<span class="tag pending-tag">待解析</span>'}${job.status==='unseen'?'<span class="tag unseen">最近未见</span>':''}${job.first_seen!==job.changed_at?'<span class="tag updated">内容更新</span>':''}</div><p class="job-summary">${esc(job.analysis?.summary||job.description.slice(0,100).replace(/\n/g,' '))}</p></div></div>
      <span class="job-city">${esc(job.city||'地点未说明')}</span><span class="job-kind">${esc(job.analysis?.internship_type||job.internship_type||'实习')}<small>${esc(job.degree==='未说明'?'学历未说明':job.degree==='不限'?'学历不限':job.degree+'及以上')}</small></span>
      <span class="job-date">${esc(date(job.changed_at,true))}<small>发现 ${esc(date(job.first_seen,true))}</small></span>
      ${savedButton(job,true)}</article>`).join('');
    $('page-info').textContent = total ? `第 ${page} / ${Math.ceil(total/20)} 页` : '暂无岗位';
    $('prev').disabled = page<=1; $('next').disabled = page*20>=total;
  } catch (error) { if(error.name!=='AbortError' && id===requestId)toast(error.message,true); }
  finally {if(id===requestId){jobsPendingKey='';document.querySelector('.jobs-table').setAttribute('aria-busy','false');}}
}
function showDetail(job) {
  detailJob=job;
  $('detail-content').innerHTML = `<h2 class="detail-title">${esc(job.title)}</h2><div class="detail-meta"><span class="tag">${esc(job.company)}</span><span class="tag">${esc(job.city||'地点见官网')}</span><span class="tag">${esc(job.internship_type)}</span>${job.department?`<span class="tag">${esc(job.department)}</span>`:''}</div>
    ${job.analysis?`<section class="analysis-card"><div class="analysis-heading"><span>✦ AI 结构化解析</span><small>基于官网原文 · ${Math.round(job.analysis.confidence*100)}% 解析置信度</small></div><p>${esc(job.analysis.summary)}</p><div class="analysis-facts"><span>方向<strong>${esc(job.job_type)}</strong></span><span>学历<strong>${esc(job.degree)}</strong></span><span>每周到岗<strong>${job.min_days?job.min_days+' 天起':'未说明'}</strong></span><span>实习时长<strong>${job.min_months?job.min_months+' 个月起':'未说明'}</strong></span></div><div class="job-tags">${job.skills.map(s=>`<span class="tag ai-tag">${esc(s)}</span>`).join('')}</div>${job.analysis.evidence?`<blockquote>${esc(job.analysis.evidence)}</blockquote>`:''}</section>`:'<p class="hint">AI 解析正在排队，下面是官网原文。</p>'}
      ${job.internship_evidence?`<section class="detail-section"><h3>实习收录依据</h3><p class="detail-text">${esc(job.internship_evidence)}</p></section>`:''}
      <section class="detail-section"><h3>官网岗位描述</h3><p class="detail-text">${esc(job.description||'官方列表未提供详细描述，请点击下方链接查看官网。')}</p></section>
    <section class="detail-section"><h3>岗位要求</h3><p class="detail-text">${esc(job.requirements||'以官网岗位详情为准。')}</p></section>
    <section class="detail-section"><h3>收录记录</h3><p class="detail-text">首次发现：${esc(date(job.first_seen))}\n最近发现：${esc(date(job.last_seen))}\n官网发布时间：${esc(job.published_at||'官网未提供')}\n官网更新时间：${esc(job.source_updated_at||'官网未提供')}\n采集状态：${job.status==='active'?'最近仍在列表':'连续两轮未见，尚未确认关闭'}</p></section>
    <section class="detail-section label-editor"><h3>我的标签</h3><form id="label-form"><input id="job-labels" value="${esc((job.labels||[]).join(', '))}" placeholder="例如：优先投递, 已投递, 待准备" aria-label="岗位自定义标签"><button class="secondary" type="submit">保存标签</button></form><div class="label-suggestions"><button data-label="优先投递">＋优先投递</button><button data-label="已投递">＋已投递</button><button data-label="待准备">＋待准备</button></div><p class="hint">逗号分隔，自定义标签与 AI 标签分别保存。</p></section>
    <div class="detail-footer">${savedButton(job)}<a class="primary" href="${esc(safeURL(job.url))}" target="_blank" rel="noopener noreferrer">查看官网 · 了解与投递 ↗</a><small>收录时间不等于官网发布时间</small></div>`;
  $('label-form').addEventListener('submit',async event=>{event.preventDefault();const values=$('job-labels').value.split(/[,，\n]/).map(s=>s.trim()).filter(Boolean);try{await api('/api/jobs/'+encodeURIComponent(job.id)+'/labels',{method:'PATCH',body:JSON.stringify({labels:values})});job.labels=values;toast('岗位标签已保存');loadFacets();if(['jobs','saved'].includes(currentView))loadJobs({force:true});}catch(error){toast(error.message,true);}});
  document.querySelectorAll('[data-label]').forEach(button=>button.addEventListener('click',()=>{const values=$('job-labels').value.split(/[,，]/).map(s=>s.trim()).filter(Boolean);if(!values.includes(button.dataset.label))values.push(button.dataset.label);$('job-labels').value=values.join(', ');}));
  $('detail-dialog').showModal();
}
async function loadStatus() {
  if(statusBusy)return;
  statusBusy=true;
  const saveVersion=savedVersion;
  try {
    const data = await api('/api/status');
    for (const key of ['total','active','saved','pending']) {if(key!=='saved'||saveVersion===savedVersion)$('stat-'+key).textContent = data.stats[key].toLocaleString();}
    if(saveVersion===savedVersion)$('saved-nav').textContent = data.stats.saved;
    const ai=data.ai;
    if(initialLibraryEmpty===null)initialLibraryEmpty=data.stats.total===0;
    if(ai){const label=ai.provider==='deepseek'?'DeepSeek 官方':'OpenRouter',model=ai.model||ai.model_id||settings?.ai.model||'';$('ai-progress-text').textContent=!ai.configured?'配置 AI 后可自动整理岗位方向与标签':ai.enabled?'AI 正在为岗位整理方向与标签':'AI 自动解析已暂停';$('ai-progress-count').textContent=`${ai.complete.toLocaleString()} / ${ai.total.toLocaleString()}`;$('ai-progress').value=ai.total?ai.complete/ai.total*100:0;$('ai-model-caption').textContent=ai.last_error||(ai.running?`本批 ${ai.current_batch} 个岗位 · ${model}`:'已解析的结果自动复用');$('openrouter-config').textContent=ai.configured?`● 当前已保存配置：${label} · ${model}`:`○ 当前已保存配置：${label} 尚未配置密钥`;$('ai-cost').textContent='$'+ai.spent_today_usd.toFixed(4);$('ai-error').textContent=ai.last_error||'';}
    const engine = data.engine; const failures = data.sources.filter(s=>s.enabled&&(s.last_error||s.coverage?.warnings?.length));
    $('scan').disabled = engine.running; $('scan').textContent = engine.running ? '↻ 正在采集岗位…' : '↻ 检查最新岗位';
    $('live-status').textContent = engine.running ? `正在检查 ${engine.current_sources.length} 个来源` : engine.last_finished ? failures.length ? `${failures.length} 个来源需检查` : '监控中 · '+date(engine.next_run).slice(11,16)+' 下次检查' : '等待首次检查';
    $('live-status').title=engine.running?engine.current_sources.map(s=>sourceNames[s]).join('、'):'';
    $('live-status').classList.toggle('busy',engine.running); $('live-status').classList.toggle('error',!engine.running&&!!failures.length);
    $('source-grid').innerHTML = data.sources.map(s=>`<article class="source-card"><div class="source-top"><h3><span class="company-logo ${esc(s.id)}">${esc(sourceLetters[s.id])}</span>${esc(s.name)}</h3><span class="source-badge ${s.last_error||s.coverage?.warnings?.length?'failed':''}">${!s.enabled?'已暂停':engine.current_sources.includes(s.id)?'采集中':s.last_error?'本轮失败':s.coverage?.warnings?.length?'需复核':s.last_success?'采集正常':'等待采集'}</span></div><p class="source-detail">上次成功 ${esc(date(s.last_success))}<br>最近尝试 ${esc(date(s.last_attempt))}<br>${s.last_error?"上次成功收录":"本轮收录"} ${s.count||0} 个 · 新增 ${s.added||0} 个 · 更新 ${s.updated||0} 个<br>${esc(s.description)}</p>${sourceCoverage(s)}${s.last_error?`<p class="source-error">${esc(s.last_error)}</p>`:''}<a href="${esc(safeURL(s.url))}" target="_blank" rel="noopener noreferrer">查看官方招聘网站 ↗</a></article>`).join('');
    $('delivery-list').innerHTML = data.deliveries.length ? data.deliveries.map(d=>`<div class="delivery-row ${d.success?'':'fail'}"><span>${esc(date(d.created_at))}</span><span>${d.provider==='serverchan'?'Server酱':'企业微信'} · ${d.count} 个岗位</span><span>${esc(d.message)}</span></div>`).join('') : '<p class="hint">还没有发送记录。保存接收设置后，可发送一条测试通知。</p>';
    if (settings) $('push-caption').textContent = settings.notifications.enabled ? `每 ${settings.notifications.cooldown_minutes} 分钟合并推送` : '微信推送尚未启用';
    const changed=jobsRevision!==null && jobsRevision!==data.jobs_revision;
    jobsRevision=data.jobs_revision;
    if ((changed || !jobsLoadedKey&&!jobsPendingKey) && (currentView==='jobs'||currentView==='saved'))loadJobs({force:true,quiet:!!jobsLoadedKey});
    if(changed)loadFacets();
  } catch (error) { $('live-status').textContent='服务连接失败'; $('live-status').classList.add('error'); }finally{statusBusy=false;}
}
function switchView(view) {
  const previous=currentView;
  if(previous!==view&&['jobs','saved'].includes(previous))viewFilters[previous]={values:Object.fromEntries(filterInputs.map(id=>[id,$(id).value])),matching:$('matching').checked,company,page};
  if(previous!==view&&['jobs','saved'].includes(view)){
    const state=viewFilters[view]||{values:Object.fromEntries(filterInputs.map(id=>[id,['available-days','available-months','recent'].includes(id)?'0':id==='sort'?'changed':''])),matching:false,company:'',page:1};
    for(const [id,value] of Object.entries(state.values))$(id).value=value;
    $('matching').checked=state.matching;company=state.company;page=state.page;
    document.querySelectorAll('.chip').forEach(b=>b.classList.toggle('active',b.dataset.company?company.split(',').includes(b.dataset.company):!company));
  }else page=1;
  currentView=view;
  document.querySelectorAll('.nav').forEach(button=>button.classList.toggle('active',button.dataset.view===view));
  $('jobs-view').hidden = !['jobs','saved'].includes(view); $('sources-view').hidden=view!=='sources'; $('settings-view').hidden=view!=='settings';
  $('match-view').hidden=view!=='match';
  const names={jobs:['岗位看板','更早发现，更适合你的机会。','连接大厂与 AI 实验室的官方实习岗位，让下一步更清晰。'],match:['AI 岗位匹配','找到与你同频的实习。','用你的经历与偏好，比较岗位的内容、技能与到岗条件。'],saved:['已收藏岗位','值得投递的机会。','岗位看板与 AI 匹配的收藏统一保存在这里，按自己的节奏跟进。'],sources:['监控来源','机会，来自第一手渠道。','查看每个招聘网站的采集结果与更新时间。'],settings:['设置与推送','让工作台按你的习惯运行。','设置监控与订阅规则，连接自己的 AI 服务和微信推送。']};
  $('breadcrumb').textContent='工作台 / '+names[view][0]; $('page-title').textContent=names[view][1]; $('page-subtitle').textContent=names[view][2];
  $('list-title').textContent=view==='saved'?'已收藏岗位':'发现机会';
  if (view==='jobs'||view==='saved') loadJobs();
  if(view==='match')loadMatchHistory();
}
function checks(container, selected, names=sourceNames) {
  $(container).innerHTML=Object.entries(names).map(([id,name])=>`<label class="check"><input type="checkbox" value="${esc(id)}" ${selected.includes(id)?'checked':''}>${esc(name)}</label>`).join('');
}
const checked = container => [...$(container).querySelectorAll('input:checked')].map(input=>input.value);
const words = id => $(id).value.split(/[,，\n]/).map(s=>s.trim()).filter(Boolean);
function readSubscriptionRule() {
  return {keywords:words('rule-keywords'),exclude:words('rule-exclude'),companies:checked('rule-companies'),cities:words('rule-cities'),mode:$('rule-mode').value,
    job_types:checked('rule-job-types'),internship_types:checked('rule-internship-types'),departments:words('rule-departments'),degrees:checked('rule-degrees'),
    tags:words('rule-tags'),exclude_tags:words('rule-exclude-tags'),tag_mode:$('rule-tag-mode').value,
    available_days:Number($('rule-days').value),available_months:Number($('rule-months').value),unknown_policy:$('rule-unknown-policy').value};
}
function renderSubscriptionTypes(selected=checked('rule-job-types')) {
  // The server renders the authoritative type list before any API request.
  const defaults=[...$('rule-job-types').querySelectorAll('input')].map(input=>input.value);
  const types=[...new Set([...(facets?.role_types?.length?facets.role_types:defaults),...selected])];
  checks('rule-job-types',selected,Object.fromEntries(types.map(t=>[t,t])));
}
function invalidateSubscriptionPreview() {$('subscription-preview').hidden=true;}
$('preview-subscription').addEventListener('click',async()=>{
  if(!$('settings-form').reportValidity())return;
  $('preview-subscription').disabled=true;
  const rule=readSubscriptionRule();
  try {
    const r=await api('/api/subscription/preview',{method:'POST',body:JSON.stringify(rule)});
    if(JSON.stringify(rule)!==JSON.stringify(readSubscriptionRule()))return;
    $('subscription-preview').innerHTML=`<strong>当前符合 ${r.matched.toLocaleString()} 个岗位</strong><p>活跃实习库 ${r.total.toLocaleString()} 个 · 基础条件符合、待解析 ${r.waiting_for_analysis.toLocaleString()} 个</p>${r.items.length?`<ul>${r.items.map(j=>`<li><a href="${esc(safeURL(j.url))}" target="_blank" rel="noopener noreferrer">${esc(j.title)} ↗</a><span>${esc(j.company)} · ${esc(j.city||'地点未说明')}</span></li>`).join('')}</ul>`:'<p>暂时没有完成核对且符合全部条件的岗位，可放宽条件或等待解析完成。</p>'}<p>仅预览当前岗位库；保存后按新规则处理待推送及新发现的动态，不补推历史岗位。</p>`;
    $('subscription-preview').hidden=false;
  } catch(e){toast(e.message,true);}finally{$('preview-subscription').disabled=false;}
});
$('settings-form').addEventListener('input',invalidateSubscriptionPreview);
$('settings-form').addEventListener('change',invalidateSubscriptionPreview);
function providerFields() {
  const wecom=$('notify-provider').value==='wecom'; $('serverchan-fields').hidden=wecom; $('wecom-fields').hidden=!wecom;
  if(settings){const configured=wecom?settings.notifications.has_webhook:settings.notifications.has_sendkey;$('secret-status').textContent=configured?'此渠道已有密钥，留空可保留。':'此渠道尚未配置密钥。';}
}
function aiProviderFields(resetCatalog=false){
  const provider=$('ai-provider').value,deepseek=provider==='deepseek';
  $('openrouter-key-fields').hidden=deepseek;$('deepseek-key-fields').hidden=!deepseek;
  const configured=settings?.ai[deepseek?'has_deepseek_api_key':'has_openrouter_api_key'];
  $('ai-secret-status').textContent=clearAIProviders.has(provider)?'保存后将清除此服务商密钥。':configured?'此服务商已有密钥，留空可保留。':'此服务商尚未配置密钥。';
  $('ai-model').placeholder=deepseek?'例如 deepseek-flash':'例如 deepseek/deepseek-v3.2';
  if(resetCatalog){modelCatalogRequest++;aiCatalog=[];$('load-ai-models').disabled=false;$('ai-model-catalog').innerHTML='<option value="">可手动填写模型 ID</option>';$('ai-model-note').textContent='先保存密钥，再获取列表；获取列表不会生成内容。';}
}
function fillAISettings(){
  const a=settings.ai;selectedAIProvider=a.provider||'openrouter';$('ai-provider').value=selectedAIProvider;
  aiModelsByProvider[selectedAIProvider]=a.model||aiModelsByProvider[selectedAIProvider];$('ai-model').value=aiModelsByProvider[selectedAIProvider];
  $('ai-enabled').checked=a.enabled;$('ai-budget').value=a.daily_budget_usd;
  $('ai-input-price').value=a.input_price_usd_per_million??'';$('ai-output-price').value=a.output_price_usd_per_million??'';
  $('openrouter-api-key').value='';$('deepseek-api-key').value='';clearAIProviders.clear();aiProviderFields(true);
}
$('ai-provider').addEventListener('change',()=>{aiModelsByProvider[selectedAIProvider]=$('ai-model').value.trim();selectedAIProvider=$('ai-provider').value;$('ai-model').value=aiModelsByProvider[selectedAIProvider];$('ai-input-price').value='';$('ai-output-price').value='';aiProviderFields(true);});
$('clear-ai-secret').addEventListener('click',()=>{const provider=$('ai-provider').value;clearAIProviders.add(provider);$(provider+'-api-key').value='';aiProviderFields();});
for(const provider of ['openrouter','deepseek'])$(provider+'-api-key').addEventListener('input',()=>{if($(provider+'-api-key').value.trim())clearAIProviders.delete(provider);aiProviderFields();});
$('load-ai-models').addEventListener('click',async()=>{
  const provider=$('ai-provider').value,request=++modelCatalogRequest;$('load-ai-models').disabled=true;$('ai-model-note').textContent='正在获取此服务商的模型列表…';
  try{const result=await api('/api/ai/models?provider='+encodeURIComponent(provider));if(request!==modelCatalogRequest||provider!==$('ai-provider').value)return;
    const models=result.models||[];aiCatalog=models;$('ai-model-catalog').innerHTML='<option value="">选择模型，或手动填写上方 ID</option>'+models.map(m=>`<option value="${esc(m.id)}">${esc(m.name||m.id)} · ${esc(m.id)}</option>`).join('');
    $('ai-model-note').textContent=models.length?`已获取 ${models.length} 个模型。选择后会填入模型 ID；不会发起内容生成。`:'暂未返回模型，可直接填写模型 ID。';
    if(models.some(m=>m.id===$('ai-model').value))$('ai-model-catalog').value=$('ai-model').value;
  }catch(error){if(request===modelCatalogRequest)$('ai-model-note').textContent=error.message+'。可手动填写模型 ID，保存密钥后重试。';}
  finally{if(request===modelCatalogRequest)$('load-ai-models').disabled=false;}
});
$('ai-model-catalog').addEventListener('change',()=>{if($('ai-model-catalog').value){$('ai-model').value=$('ai-model-catalog').value;$('ai-input-price').value='';$('ai-output-price').value='';const item=aiCatalog.find(m=>m.id===$('ai-model-catalog').value);if(item)$('ai-model-note').textContent=(item.input_price_usd_per_million!=null&&item.output_price_usd_per_million!=null?`每百万 tokens：输入 $${item.input_price_usd_per_million} / 输出 $${item.output_price_usd_per_million}。`:'此模型尚无自动单价，请展开自定义计费填写。')+(item.pricing_note||'');}});
$('ai-model').addEventListener('input',()=>{$('ai-model-catalog').value='';$('ai-model-note').textContent='将使用填写的模型 ID。无法自动识别单价时，需要填写自定义计费。';});
async function loadSettings() {
  $('save-settings').disabled=true;
  settings=await api('/api/settings'); const n=settings.notifications,r=settings.rule;
  $('interval').value=settings.interval_minutes;checks('enabled-sources',settings.enabled_sources);checks('rule-companies',r.companies);
  $('rule-keywords').value=r.keywords.join(', ');$('rule-exclude').value=r.exclude.join(', ');$('rule-cities').value=r.cities.join(', ');$('rule-mode').value=r.mode;
  renderSubscriptionTypes(r.job_types||[]);
  checks('rule-internship-types',r.internship_types||[],{'日常':'日常实习','暑期':'暑期实习','研究':'研究型实习','ByteIntern':'ByteIntern','应届实习':'应届实习','转正':'转正实习','兼招全职':'实习 / 全职兼招'});
  checks('rule-degrees',r.degrees||[],Object.fromEntries(['专科','本科','硕士','博士','不限','未说明'].map(d=>[d,d])));
  $('rule-departments').value=(r.departments||[]).join(', ');$('rule-tags').value=(r.tags||[]).join(', ');$('rule-exclude-tags').value=(r.exclude_tags||[]).join(', ');
  $('rule-tag-mode').value=r.tag_mode||'any';$('rule-days').value=r.available_days||0;$('rule-months').value=r.available_months||0;$('rule-unknown-policy').value=r.unknown_policy||'include';
  invalidateSubscriptionPreview();
  $('notify-enabled').checked=n.enabled;$('notify-provider').value=n.provider;$('cooldown').value=n.cooldown_minutes;$('daily-limit').value=n.daily_limit;$('notify-updates').checked=n.notify_updates;
  $('sendkey').value='';$('webhook').value='';clearProvider=null;providerFields();
  fillAISettings();
  $('save-settings').disabled=false;$('retry-settings').hidden=true;
  $('settings-message').textContent='更改后点击保存，下一轮检查即可使用。';
}
function settingsLoadFailed(error) {
  $('save-settings').disabled=true;$('retry-settings').hidden=false;
  $('settings-message').textContent='设置加载失败：'+error.message+'。请重试，避免覆盖原有配置。';
  toast('设置加载失败，请点击“重试加载设置”',true);
}
$('retry-settings').addEventListener('click',async()=>{try{await loadSettings();}catch(error){settingsLoadFailed(error);}});
document.querySelectorAll('.nav').forEach(button=>button.addEventListener('click',()=>switchView(button.dataset.view)));
$('company-chips').innerHTML=[['','全部公司'],...Object.entries(sourceNames)].map(([id,name])=>`<button class="chip ${id===''?'active':''}" data-company="${id}">${name}</button>`).join('');
$('company-chips').addEventListener('click',event=>{const button=event.target.closest('[data-company]');if(!button)return;const id=button.dataset.company;let selected=new Set(company?company.split(','):[]);if(!id)selected.clear();else if(selected.has(id))selected.delete(id);else selected.add(id);company=[...selected].join(',');page=1;document.querySelectorAll('.chip').forEach(b=>b.classList.toggle('active',b.dataset.company?selected.has(b.dataset.company):!selected.size));loadJobs();});
let debounce;
for(const id of ['search','city','department']) $(id).addEventListener('input',()=>{clearTimeout(debounce);debounce=setTimeout(()=>{page=1;loadJobs();},250);});
for(const id of ['kind','job-status','matching','role-type','degree','tag-filter','available-days','available-months','recent','ai-filter','sort']) $(id).addEventListener('change',()=>{page=1;loadJobs();});
$('prev').addEventListener('click',()=>{page=Math.max(1,page-1);loadJobs();});$('next').addEventListener('click',()=>{page++;loadJobs();});
$('reset-filters').addEventListener('click',()=>{for(const id of ['search','city','kind','department','role-type','degree','tag-filter','ai-filter'])$(id).value='';for(const id of ['available-days','available-months','recent'])$(id).value='0';$('job-status').value='';$('matching').checked=false;company='';page=1;document.querySelectorAll('.chip').forEach(b=>b.classList.toggle('active',b.dataset.company===''));loadJobs();});
$('job-list').addEventListener('click',event=>{const detail=event.target.closest('[data-detail]'),tag=event.target.closest('[data-tag]');if(detail)showDetail(currentJobs[Number(detail.dataset.detail)]);if(tag){selectTag(tag.dataset.tag);page=1;loadJobs();}});
$('close-detail').addEventListener('click',()=>$('detail-dialog').close());$('detail-dialog').addEventListener('click',event=>{if(event.target===$('detail-dialog')){const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)event.target.close();}});
$('scan').addEventListener('click',async()=>{$('scan').disabled=true;try{await api('/api/scan',{method:'POST'});toast('已开始采集。可在“监控来源”查看进度。');await loadStatus();}catch(error){toast(error.message,true);$('scan').disabled=false;}});
$('notify-provider').addEventListener('change',providerFields);
$('clear-secret').addEventListener('click',()=>{clearProvider=$('notify-provider').value;$(clearProvider==='serverchan'?'sendkey':'webhook').value='';$('secret-status').textContent='保存设置后将清除当前渠道密钥。';});
$('settings-form').addEventListener('submit',async event=>{
  event.preventDefault();
  if(Boolean($('ai-input-price').value)!==Boolean($('ai-output-price').value)){toast('自定义计费需同时填写输入和输出单价，或同时留空。',true);return;}
  $('save-settings').disabled=true;
  const data={interval_minutes:Number($('interval').value),enabled_sources:checked('enabled-sources'),rule:readSubscriptionRule(),notifications:{enabled:$('notify-enabled').checked,provider:$('notify-provider').value,cooldown_minutes:Number($('cooldown').value),daily_limit:Number($('daily-limit').value),notify_updates:$('notify-updates').checked,sendkey:$('sendkey').value.trim()||(clearProvider==='serverchan'?'':null),webhook:$('webhook').value.trim()||(clearProvider==='wecom'?'':null)},ai:{enabled:$('ai-enabled').checked,daily_budget_usd:Number($('ai-budget').value),batch_size:settings.ai.batch_size,provider:$('ai-provider').value,model:$('ai-model').value.trim(),openrouter_api_key:$('openrouter-api-key').value.trim()||(clearAIProviders.has('openrouter')?'':null),deepseek_api_key:$('deepseek-api-key').value.trim()||(clearAIProviders.has('deepseek')?'':null),input_price_usd_per_million:$('ai-input-price').value===''?null:Number($('ai-input-price').value),output_price_usd_per_million:$('ai-output-price').value===''?null:Number($('ai-output-price').value)}};
  try{settings=await api('/api/settings',{method:'PUT',body:JSON.stringify(data)});$('sendkey').value='';$('webhook').value='';clearProvider=null;providerFields();fillAISettings();$('settings-message').textContent='已保存。后续 AI 调用与岗位推送将使用新的配置。';toast('设置已保存');await loadStatus();}catch(error){toast(error.message,true);}finally{$('save-settings').disabled=false;}
});
$('test-push').addEventListener('click',async()=>{$('test-push').disabled=true;try{const data=await api('/api/notifications/test',{method:'POST'});toast(data.message);}catch(error){toast(error.message,true);}finally{$('test-push').disabled=false;loadStatus();}});
async function loadFacets(){if(facetsBusy)return;facetsBusy=true;try{facets=await api('/api/facets');const role=$('role-type').value;$('role-type').innerHTML='<option value="">不限方向</option>'+facets.role_types.map(r=>`<option>${esc(r)}</option>`).join('');$('role-type').value=role;const tag=$('tag-filter').value;const options=[...new Set([...facets.labels.map(r=>r.name),...facets.tags.map(r=>r.name)])];$('tag-filter').innerHTML='<option value="">不限标签</option>'+options.map(t=>`<option>${esc(t)}</option>`).join('');if(tag)selectTag(tag);renderSubscriptionTypes();$('subscription-tag-options').innerHTML=options.map(t=>`<option value="${esc(t)}"></option>`).join('');}catch{}finally{facetsBusy=false;}}
function selectTag(tag){if(![...$('tag-filter').options].some(o=>o.value===tag))$('tag-filter').add(new Option(tag,tag));$('tag-filter').value=tag;document.querySelector('.advanced-filters').open=true;}
$('open-match').addEventListener('click',()=>switchView('match'));
document.querySelectorAll('[data-layout]').forEach(b=>b.addEventListener('click',()=>{document.querySelector('.jobs-table').classList.toggle('cards',b.dataset.layout==='cards');document.querySelectorAll('[data-layout]').forEach(x=>x.classList.toggle('selected',x===b));localStorage.setItem('hirewatch-layout',b.dataset.layout);}));
if(localStorage.getItem('hirewatch-layout')==='cards')document.querySelector('[data-layout="cards"]').click();
$('retry-ai').addEventListener('click',async()=>{try{const r=await api('/api/ai/retry',{method:'POST'});toast(r.message);}catch(e){toast(e.message,true);}});
$('apply-subscription').addEventListener('click',async()=>{try{const s=await api('/api/settings');s.rule={...s.rule,keywords:$('search').value.replace(/[,，]/g,' ').split(/\s+/).filter(Boolean),companies:company?company.split(','):[],cities:$('city').value.trim()?[$('city').value.trim()]:[],mode:'all',job_types:$('role-type').value?[$('role-type').value]:[],internship_types:$('kind').value?[$('kind').value]:[],degrees:$('degree').value?[$('degree').value]:[],tags:$('tag-filter').value?[$('tag-filter').value]:[],departments:$('department').value.trim()?[$('department').value.trim()]:[],available_days:Number($('available-days').value),available_months:Number($('available-months').value)};await api('/api/settings',{method:'PUT',body:JSON.stringify(s)});await loadSettings();toast('关键词、公司、城市与详细岗位条件已同步到订阅');}catch(e){toast(e.message,true);}});
const examples={llm:'我有 Python、PyTorch 项目经验，想在北京或上海做大模型、Agent 或推理优化实习。每周可以到岗4天，持续4个月。优先参与研发，不考虑销售。',research:'我在读计算机硕士，做过多模态和强化学习项目，想找上海的研究实习，优先 AI 实验室和大模型团队。每周5天，实习6个月。请指出需要确认的学历、论文和到岗要求。',game:'我有 C++、Unity 和 Python 项目经验，希望在上海找 AI 与游戏结合的研发实习，对米哈游和大模型应用团队感兴趣。每周4天，能实习4个月，不考虑纯运营岗位。'};
document.querySelectorAll('[data-example]').forEach(b=>b.addEventListener('click',()=>{$('match-prompt').value=examples[b.dataset.example];$('match-prompt').focus();}));
$('match-form').addEventListener('submit',async e=>{e.preventDefault();clearTimeout(matchTimer);matchController?.abort();activeMatch=null;matchResult=null;const request=++matchRequestId;$('run-match').disabled=true;$('match-progress').hidden=false;$('match-empty').hidden=true;$('match-results').innerHTML='';$('match-summary').hidden=true;try{const scope=$('match-use-filters').checked?(viewFilters.jobs||{company,values:{city:$('city').value}}):null;const r=await api('/api/matches',{method:'POST',body:JSON.stringify({prompt:$('match-prompt').value,companies:scope?.company?scope.company.split(','):[],cities:scope?.values.city.trim()?[scope.values.city.trim()]:[],candidates:Number($('match-candidates').value)})});if(request!==matchRequestId)return;activeMatch=r.id;await pollMatch();}catch(error){if(request!==matchRequestId)return;$('run-match').disabled=false;$('match-progress').hidden=true;toast(error.message,true);}});
let preserveMatchResults=false;
function matchActions(){
  let actions=$('match-actions');
  if(!actions){actions=document.createElement('div');actions.id='match-actions';actions.className='match-actions';actions.innerHTML='<button id="complete-match" class="secondary" hidden>补全失败岗位</button><button id="refresh-match" class="secondary" hidden>刷新查看最新结果</button><span id="match-update-note" role="status"></span>';$('match-summary').after(actions);}
  return actions;
}
matchActions();
function resetMatchView(){preserveMatchResults=false;$('complete-match').hidden=true;$('refresh-match').hidden=true;$('match-update-note').textContent='';$('match-progress').hidden=true;}
$('match-form').addEventListener('submit',resetMatchView,true);
$('match-history').addEventListener('click',e=>{if(e.target.closest('[data-match-run]'))resetMatchView();});
async function pollMatch(forceRender=false){
  clearTimeout(matchTimer);if(!activeMatch)return;
  const runId=activeMatch,request=++matchRequestId,saveVersion=savedVersion;
  matchController?.abort();matchController=new AbortController();
  try{
    const r=await api('/api/matches/'+encodeURIComponent(runId),{signal:matchController.signal});
    if(runId!==activeMatch || request!==matchRequestId)return;
    for(const entry of [...(r.result?.results||[]),...(r.result?.unscored||[])])reconcileSaved(entry.job,saveVersion);
    $('match-prompt').value=r.prompt;$('match-progress-text').textContent=r.progress;
    const running=['pending','running'].includes(r.state),hasResult=Boolean(r.result?.intent);
    if(forceRender&&!running)preserveMatchResults=false;
    $('match-progress').hidden=!running;
    $('complete-match').hidden=running||!r.result?.unscored?.length;
    $('complete-match').disabled=running;
    $('refresh-match').hidden=!preserveMatchResults||!hasResult;
    $('match-update-note').textContent=preserveMatchResults?(running?'后台正在补全，当前列表保持不变。可随时刷新查看进度。':r.state==='complete'?'补全已完成，点击刷新查看全部结果。':'本轮补全已结束，点击刷新查看已完成的结果。'):'';
    if(hasResult&&(!preserveMatchResults||forceRender)){matchResult={...r.result,view_state:r.state,warning:r.error};renderMatch();}
    if(running){$('run-match').disabled=true;matchTimer=setTimeout(()=>pollMatch(),2500);return;}
    $('run-match').disabled=false;
    if(r.state==='failed'&&!hasResult){$('match-summary').hidden=false;$('match-summary').textContent=r.error.includes('未展示为完整匹配')?'这条旧任务因评分异常中断，当时没有保存结果。请重新匹配；新版会保留结果并支持补全。':r.error;$('match-results').innerHTML='';}
    loadMatchHistory();
  }catch(error){if(error.name==='AbortError' || runId!==activeMatch || request!==matchRequestId)return;$('run-match').disabled=false;$('match-progress').hidden=true;toast(error.message,true);}
}
function renderMatch(){
  if(!matchResult)return;
  const missing=matchResult.unscored||[],running=['pending','running'].includes(matchResult.view_state);
  const count=matchResult.candidate_count??matchResult.evaluated;
  $('match-summary').hidden=false;
  $('match-summary').innerHTML=`<strong>${esc(matchResult.intent.summary)}</strong><p>岗位库 ${matchResult.total_pool.toLocaleString()} 个 · 满足筛选 ${matchResult.eligible_pool.toLocaleString()} 个 · 已评分 ${matchResult.evaluated} / ${count} 个候选</p>${missing.length?`<p class="match-warning">${running?'评分进行中':'匹配未全部完成'} · ${missing.length} 个${running?'待评分':'评分失败'}。${running?'已完成的结果先展示在下方。':'已有结果已保留，可点击“补全失败岗位”继续。'}</p>`:''}${matchResult.warning?`<p class="match-warning">${esc(matchResult.warning)}</p>`:''}`;
  const rows=matchResult.results.filter(r=>r.score>=Number($('minimum-score').value));$('match-empty').hidden=true;
  $('match-results').innerHTML=rows.map(r=>`<article class="match-card"><div class="match-card-top"><div class="match-company"><span class="company-logo ${esc(r.job.source)}">${esc(sourceLetters[r.job.source])}</span><div><span>${esc(r.job.company)} · ${esc(r.job.city||'地点未说明')}</span><button class="job-title" data-match-detail="${esc(r.id)}">${esc(r.job.title)}</button></div></div><div class="match-score ${r.score<60?'low-score':''}"><strong>${r.score}</strong><span>匹配分</span></div></div><p class="match-reason">${esc(r.reason)}</p><div class="match-lists"><div><h4>与你的偏好相符</h4>${r.strengths.map(s=>`<p>＋ ${esc(s)}</p>`).join('')}</div><div><h4>需要确认或补足</h4>${r.gaps.map(s=>`<p>· ${esc(s)}</p>`).join('')}</div></div>${r.evidence?`<blockquote>${esc(r.evidence)}</blockquote>`:''}<div class="match-card-footer"><span>${r.stale?'岗位信息已变化，请重新匹配':'根据提示词与官网原文比较'}</span><button class="secondary" data-match-detail="${esc(r.id)}">岗位详情 ↗</button><a href="${esc(safeURL(r.job.url))}" target="_blank" rel="noopener noreferrer">官网投递 ↗</a></div></article>`).join('')+missing.map(r=>`<article class="match-card match-unscored"><div class="match-card-top"><div class="match-company"><span class="company-logo ${esc(r.job.source)}">${esc(sourceLetters[r.job.source]||'职')}</span><div><span>${esc(r.job.company)} · ${esc(r.job.city||'地点未说明')}</span><button class="job-title" data-match-detail="${esc(r.id)}">${esc(r.job.title)}</button></div></div><span class="match-failure-label">${running?'待评分':'评分失败'}</span></div><p class="match-reason">${running?'该候选尚未完成评分。':'尚未获得有效评分；这不表示岗位不适合你，可在后台补全。'}</p><div class="match-card-footer"><span>${r.stale?'岗位信息已变化，请核实官网':'保留候选，未计为低分或零分'}</span><button class="secondary" data-match-detail="${esc(r.id)}">岗位详情 ↗</button><a href="${esc(safeURL(r.job.url))}" target="_blank" rel="noopener noreferrer">官网投递 ↗</a></div></article>`).join('');
  if(!rows.length){$('match-results').insertAdjacentHTML('afterbegin',`<div class="empty"><h3>${count===0?'当前条件下没有候选岗位':matchResult.results.length?'没有达到当前匹配分的候选':'尚无完成评分的岗位'}</h3><p>${count===0?'可调整公司、城市或到岗条件后重新匹配。':matchResult.results.length?'可调整匹配分筛选；未评分岗位仍列在下方。':'候选已保留，完成评分后可查看匹配分和理由。'}</p></div>`);}
  const entries=new Map([...rows,...missing].map(entry=>[entry.id,entry]));
  for(const card of $('match-results').querySelectorAll('.match-card')){
    const entry=entries.get(card.querySelector('[data-match-detail]').dataset.matchDetail);
    if(entry){card.dataset.jobId=entry.id;card.querySelector('.match-card-footer [data-match-detail]').insertAdjacentHTML('beforebegin',savedButton(entry.job));}
  }
}
$('minimum-score').addEventListener('change',renderMatch);
$('match-results').addEventListener('click',e=>{const b=e.target.closest('[data-match-detail]');if(b){const r=[...matchResult.results,...(matchResult.unscored||[])].find(x=>x.id===b.dataset.matchDetail);if(r)showDetail(r.job);}});
$('complete-match').addEventListener('click',async()=>{const runId=activeMatch;$('complete-match').disabled=true;try{await api('/api/matches/'+encodeURIComponent(runId)+'/retry',{method:'POST'});if(runId!==activeMatch)return;preserveMatchResults=true;await pollMatch();}catch(error){if(runId!==activeMatch)return;$('complete-match').disabled=false;toast(error.message,true);}});
$('refresh-match').addEventListener('click',()=>pollMatch(true));
async function loadMatchHistory(){try{const rows=await api('/api/matches');$('match-history').innerHTML=rows.length?rows.map(r=>`<button data-match-run="${esc(r.id)}"><span>${esc(r.prompt.slice(0,50))}</span><small>${esc(date(r.created_at).slice(5,16))} · ${r.state==='complete'?'已完成':r.state==='partial'?'部分完成':r.state==='failed'?'需重试':'进行中'}</small></button>`).join(''):'<p class="hint">匹配记录会保存在本机。</p>';}catch{}}
$('match-history').addEventListener('click',e=>{const b=e.target.closest('[data-match-run]');if(b){activeMatch=b.dataset.matchRun;matchResult=null;$('match-results').innerHTML='';$('match-empty').hidden=true;$('match-summary').hidden=false;$('match-summary').textContent='正在读取已保存的匹配结果…';pollMatch();}});
const tutorialVersion='hirewatch-tutorial-v1';
let tutorialStep=0;
const tutorialSteps=[
  {icon:'◉',kicker:'欢迎使用 / 01',title:'把官方实习机会，放进自己的工作台。',text:'招聘雷达直接检查公司官方招聘渠道，在本机保留岗位记录，帮你筛选、比较和收藏。第一次使用，花两分钟认识它。',extra:'<div class="tutorial-feature-grid"><span>◎ 官方招聘来源</span><span>⌕ 多条件筛选</span><span>✦ AI 解析与匹配</span><span>★ 统一收藏</span></div><p class="tutorial-note">AI 和微信推送使用你自己的密钥；不填也能先浏览和收藏。</p>'},
  {icon:'◎',kicker:'发现机会 / 02',title:'先收录，再按自己的条件筛选。',text:'在“设置与推送”选择公司和检查间隔，点击“检查最新岗位”开始采集。首次检查可能需要几分钟，可在“监控来源”查看进度。',extra:'<div class="tutorial-example"><strong>试试这样找</strong><p>城市选“上海” → 岗位方向选“大模型 / Agent” → 输入“Python” → 打开岗位详情查看官网原文。</p></div><p class="tutorial-note">这里只收录实习及实习 / 全职兼招岗位。“最近未见”表示连续检查未发现，是否在招以官网为准。电脑与程序运行时才能持续检查。</p>'},
  {icon:'✦',kicker:'连接 AI（可选） / 03',title:'选自己的服务商，控制自己的预算。',text:'在设置的“AI 服务与预算”选择 OpenRouter 或 DeepSeek 官方 API，填写自己的密钥与模型 ID，再保存。也能获取模型列表后选择模型。',extra:'<div class="tutorial-example"><strong>一个服务商 + 一个模型 + 每日预算</strong><p>AI 会从官网原文整理城市、方向、部门、学历和技能；解析和提示词匹配共用每日预算，已有结果自动复用。</p></div><p class="tutorial-note">未配置 AI 也能采集和收藏。密钥保存在本机；AI 调用会将岗位原文和你填写的匹配偏好发给所选服务商，并按其价格计费。</p><button type="button" class="secondary" data-tutorial-settings="ai">去配置我的 AI 服务 ↗</button>'},
  {icon:'★',kicker:'比较与收藏 / 04',title:'写下你的偏好，把机会留下来。',text:'到“AI 岗位匹配”描述技能、方向、城市和到岗时间，模型会给出比较分数与理由。看板和匹配结果的收藏共用左侧“已收藏岗位”。',extra:'<div class="tutorial-example"><strong>可以直接使用的描述</strong><p>我会 Python 和 PyTorch，想在上海做 Agent 实习，每周能到岗 4 天，持续 4 个月。希望参与研发，请指出需要确认的条件。</p></div><p class="tutorial-note">评分失败的候选仍会展示。点击“补全失败岗位”可在后台继续处理，当前列表保持不变，完成后点击“刷新查看最新结果”。</p>'},
  {icon:'↗',kicker:'微信提醒（可选） / 05',title:'用现有微信，接收值得关注的动态。',text:'在“我的订阅规则”设置城市、岗位类型、标签和到岗条件。个人微信选择 Server酱，按其页面绑定接收通道，把自己的 SendKey 填入设置。',extra:'<div class="tutorial-example"><strong>不需要注册新的微信号</strong><p>也支持企业微信群机器人。先保存设置，再按需发送测试消息；测试会发出真实消息并消耗服务额度。</p></div><p class="tutorial-note">首次采集建立基线，不推送已有岗位。订阅规则只影响提醒和“符合订阅规则”的筛选，不会减少岗位收录。</p><button type="button" class="secondary" data-tutorial-settings="push">去设置订阅与提醒 ↗</button>'},
  {icon:'✓',kicker:'准备就绪 / 06',title:'从一份感兴趣的岗位开始。',text:'现在可以检查官方岗位、筛选方向，把喜欢的机会收藏下来。需要 AI 或微信提醒时，再到设置中连接自己的服务。',extra:'<div class="tutorial-feature-grid"><span>1. 检查最新岗位</span><span>2. 筛选与查看官网</span><span>3. 收藏并比较机会</span><span>4. 按需开启提醒</span></div><p class="tutorial-note">教程随时可以从左下角“使用教程”或程序菜单重新打开。关闭网页后，程序仍可通过 Windows 托盘或 Mac 菜单栏运行；退出程序或关闭电脑后停止监控。</p>'}
];
function renderTutorial(){const step=tutorialSteps[tutorialStep];$('tutorial-body').innerHTML=`<span class="tutorial-hero-icon" aria-hidden="true">${step.icon}</span><p class="eyebrow">${step.kicker}</p><h2 id="tutorial-title" tabindex="-1">${step.title}</h2><p class="tutorial-intro">${step.text}</p>${step.extra}`;$('tutorial-position').textContent=`${tutorialStep+1} / ${tutorialSteps.length}`;$('tutorial-prev').hidden=tutorialStep===0;$('tutorial-next').textContent=tutorialStep===tutorialSteps.length-1?'开始使用':'下一步 →';if($('tutorial-dialog').open)$('tutorial-title').focus();}
function tutorialSeen(){try{return !!localStorage.getItem(tutorialVersion);}catch{return false;}}
function closeTutorial(){try{localStorage.setItem(tutorialVersion,'seen');}catch{}$('tutorial-dialog').close();}
function openTutorial(){tutorialStep=0;renderTutorial();if(!$('tutorial-dialog').open)$('tutorial-dialog').showModal();$('tutorial-next').focus();}
$('open-tutorial').addEventListener('click',openTutorial);
$('tutorial-skip').addEventListener('click',closeTutorial);
$('tutorial-dialog').addEventListener('cancel',event=>{event.preventDefault();closeTutorial();});
$('tutorial-prev').addEventListener('click',()=>{tutorialStep=Math.max(0,tutorialStep-1);renderTutorial();});
$('tutorial-next').addEventListener('click',()=>{if(tutorialStep===tutorialSteps.length-1)closeTutorial();else{tutorialStep++;renderTutorial();}});
$('tutorial-body').addEventListener('click',event=>{const button=event.target.closest('[data-tutorial-settings]');if(!button)return;closeTutorial();switchView('settings');const target=button.dataset.tutorialSettings==='ai'?'.ai-settings-panel':'#notify-enabled';document.querySelector(target).scrollIntoView({behavior:'smooth',block:'center'});toast('设置后可从左下角重新打开教程。');});
async function init(){await Promise.allSettled([loadSettings().catch(settingsLoadFailed),loadFacets(),loadStatus(),loadMatchHistory()]);const url=new URL(location.href),requested=url.searchParams.get('tutorial')==='1';if(requested){url.searchParams.delete('tutorial');history.replaceState(null,'',url.href);}if(requested||initialLibraryEmpty===true&&!tutorialSeen())openTutorial();}
init();setInterval(()=>{if(!document.hidden)loadStatus();},10000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)loadStatus();});
