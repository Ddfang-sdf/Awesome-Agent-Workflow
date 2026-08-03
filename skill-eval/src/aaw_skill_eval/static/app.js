const state = {
  dashboard: [], suites: [], experiments: [], draft: null, poller: null,
  runtime: null, controlsInitialized: false
};
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[ch]);
}
function fmtScore(value) { return value == null ? "—" : Number(value).toFixed(1); }
function fmtDelta(value) {
  if (value == null) return '<span class="delta">—</span>';
  const cls = value > 0 ? "up" : value < 0 ? "down" : "";
  return `<span class="delta ${cls}">${value > 0 ? "+" : ""}${Number(value).toFixed(1)}</span>`;
}
function fmtTime(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}).format(new Date(value));
}
function providerName(value) { return value === "chrys" ? "Chrys" : "Codex"; }
function modelName(role) { return role.model_name || role.model || "—"; }
function toast(message) {
  const node = $("#toast"); node.textContent = message; node.classList.add("show");
  clearTimeout(node._timer); node._timer = setTimeout(() => node.classList.remove("show"), 2600);
}
async function api(path, options = {}) {
  const response = await fetch(path, {headers:{"Content-Type":"application/json",...(options.headers||{})},...options});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.message || `HTTP ${response.status}`);
  return body;
}
function lines(value) { return value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean); }

async function loadAll() {
  const runtimeRequest = state.runtime ? Promise.resolve(state.runtime) : api("/api/v1/runtime");
  const [runtime, dashboard, suites, experiments] = await Promise.all([
    runtimeRequest, api("/api/v1/dashboard/skills"), api("/api/v1/suites"), api("/api/v1/experiments?limit=50")
  ]);
  state.runtime = runtime; state.dashboard = dashboard.items; state.suites = suites.items; state.experiments = experiments.items;
  renderRuntime(); renderProviderControls(); renderScoreFilters(); renderMetrics(); renderSkills(); renderSuites(); renderExperiments(); updatePolling();
}
function renderRuntime() {
  const providers = state.runtime.providers || {};
  const ready = Object.entries(providers).filter(([,item])=>item.available);
  const badge = $("#runtimeBadge"); badge.classList.toggle("ok", ready.length > 0);
  badge.innerHTML = `<i></i>${ready.length ? ready.map(([name,item])=>`${providerName(name)} ${escapeHtml(item.version || "已就绪")}`).join(" · ") : "未找到可用 Agent"}`;
}
function optionMarkup(items, selected) {
  return items.map(item=>`<option value="${escapeHtml(item.value)}"${item.value===selected?' selected':''}>${escapeHtml(item.label)}</option>`).join("");
}
function availableProviders() {
  const providers = state.runtime?.providers || {};
  return ["chrys","codex"].filter(name=>providers[name]?.available);
}
function renderProviderControls() {
  const providers = availableProviders();
  const runner = $("#runnerProvider"), judge = $("#judgeProvider");
  const previousRunner = runner.value, previousJudge = judge.value;
  const options = providers.map(value=>({value,label:providerName(value)}));
  runner.innerHTML = optionMarkup(options, providers.includes(previousRunner) ? previousRunner : providers[0]);
  judge.innerHTML = optionMarkup(options, providers.includes(previousJudge) ? previousJudge : providers[0]);
  const models = state.runtime?.providers?.chrys?.models || [];
  const sortedModels = [...models].sort((a,b)=>Number(b.active)-Number(a.active)||String(a.name).localeCompare(String(b.name)));
  ["#runnerChrysModel","#judgeChrysModel"].forEach(selector=>{
    const select=$(selector), previous=select.value;
    select.innerHTML=optionMarkup(sortedModels.map(item=>({value:item.id,label:`${item.name}${item.active?" · active":""}`})),previous);
  });
  if (!state.controlsInitialized) {
    const preferred = providers.includes("chrys") ? "chrys" : providers[0];
    if (preferred) { runner.value=preferred; judge.value=preferred; }
    state.controlsInitialized = true;
  }
  syncProviderControls();
}
function syncProviderControls() {
  const runnerProvider=$("#runnerProvider").value, judgeProvider=$("#judgeProvider").value;
  $("#runnerCodexConfig").classList.toggle("hidden",runnerProvider!=="codex");
  $("#runnerChrysConfig").classList.toggle("hidden",runnerProvider!=="chrys");
  const same=$("#judgeSame").checked;
  $("#judgeConfig").classList.toggle("hidden",same);
  $("#judgeCodexConfig").classList.toggle("hidden",judgeProvider!=="codex");
  $("#judgeChrysConfig").classList.toggle("hidden",judgeProvider!=="chrys");
  const isolation=runnerProvider==="chrys"?"Chrys 软隔离 · 网络未强制隔离":"Codex workspace-write · 网络按 Profile 控制";
  $("#profileHint").textContent=`${isolation}。实验会固化 Runner、Judge、模型、版本与评分配置。`;
}
function profileLabel(item) {
  const p=item.profile;
  return `${providerName(p.runner.provider)} ${modelName(p.runner)} → ${providerName(p.judge.provider)} ${modelName(p.judge)}`;
}
function setFilterOptions(selector, values, allLabel, labelFn=x=>x) {
  const select=$(selector), previous=select.value;
  const options=[{value:"",label:allLabel},...values.map(value=>({value,label:labelFn(value)}) )];
  select.innerHTML=optionMarkup(options,values.includes(previous)?previous:"");
}
function renderScoreFilters() {
  const runners=[...new Set(state.dashboard.map(x=>x.profile.runner.provider))];
  const judges=[...new Set(state.dashboard.map(x=>x.profile.judge.provider))];
  setFilterOptions("#runnerFilter",runners,"全部 Runner",providerName);
  setFilterOptions("#judgeFilter",judges,"全部 Judge",providerName);
  const select=$("#profileFilter"), previous=select.value;
  const profiles=new Map();state.dashboard.forEach(item=>profiles.set(item.profile.hash,profileLabel(item)));
  select.innerHTML=optionMarkup([{value:"",label:"选择 Profile 看均分"},...[...profiles].map(([value,label])=>({value,label}))],profiles.has(previous)?previous:"");
}
function filteredRows() {
  const runner=$("#runnerFilter").value,judge=$("#judgeFilter").value,mode=$("#modeFilter").value,profile=$("#profileFilter").value;
  return state.dashboard.filter(item=>(!runner||item.profile.runner.provider===runner)&&(!judge||item.profile.judge.provider===judge)&&(!mode||item.mode===mode)&&(!profile||item.profile.hash===profile));
}
function renderMetrics() {
  const skills=new Set(state.dashboard.map(x=>x.skill_name)),projects=new Set(state.dashboard.map(x=>x.project_path));
  const active=state.experiments.filter(x=>["queued","preparing","running"].includes(x.status)).length;
  $("#metricSkills").textContent=skills.size;$("#metricProjects").textContent=projects.size;$("#metricExperiments").textContent=state.experiments.length;$("#metricQueue").textContent=active;
  const profile=$("#profileFilter").value,rows=profile?filteredRows():[];
  const scores=rows.map(x=>x.score).filter(x=>x!=null),deltas=rows.map(x=>x.delta_no_skill).filter(x=>x!=null);
  $("#heroScore").textContent=scores.length?fmtScore(scores.reduce((a,b)=>a+b,0)/scores.length):"—";
  const averageDelta=deltas.length?deltas.reduce((a,b)=>a+b,0)/deltas.length:null;
  $("#heroDelta").textContent=averageDelta==null?"—":`${averageDelta>0?"+":""}${fmtScore(averageDelta)}`;
}
function roleCell(role,selfJudge=false) {
  return `<span class="provider-badge ${role.provider}">${providerName(role.provider)}</span>${selfJudge?'<span class="self-judge">Self-judge</span>':""}<small>${escapeHtml(modelName(role))}</small>`;
}
function renderSkills() {
  const body=$("#skillRows"),rows=filteredRows();
  if(!rows.length){body.innerHTML='<tr><td colspan="8" class="empty">当前筛选下尚无评测数据。</td></tr>';return;}
  body.innerHTML=rows.map(item=>`<tr>
    <td class="skill-name"><strong>${escapeHtml(item.skill_name)}</strong><small>${escapeHtml(item.source_path)}</small></td>
    <td class="skill-name"><strong>${escapeHtml(item.project_path.split(/[\\/]/).pop())}</strong><small>${escapeHtml(item.project_path)}</small></td>
    <td class="runtime-cell">${roleCell(item.profile.runner)}</td><td class="runtime-cell">${roleCell(item.profile.judge,item.profile.self_judge)}</td>
    <td class="score">${fmtScore(item.score)}</td><td>${fmtDelta(item.delta_no_skill)}</td><td>${fmtDelta(item.delta_baseline)}</td>
    <td><span class="mode-tag">${item.mode==="formal"?"正式":"快速"}</span><br><small>${fmtTime(item.latest_experiment_at)}</small></td>
  </tr>`).join("");
}
function renderSuites() {
  $("#runSuite").innerHTML=state.suites.length?state.suites.map(s=>`<option value="${s.id}">${escapeHtml(s.name)} · ${escapeHtml(s.skill_name)}</option>`).join(""):'<option value="">请先创建测试套件</option>';
}
function renderExperiments() {
  const list=$("#experimentList");if(!state.experiments.length){list.innerHTML='<div class="table-card empty">尚无实验记录</div>';return;}
  list.innerHTML=state.experiments.map(item=>`<article class="experiment-item">
    <div><h3>${escapeHtml(item.suite_name)}</h3><div class="experiment-meta"><span class="pill ${item.status}">${escapeHtml(item.status)}</span><span>${providerName(item.profile.runner.provider)} → ${providerName(item.profile.judge.provider)}</span><span>${item.mode==="formal"?"正式":"快速"} · ${item.trials} trial</span><span>${fmtTime(item.created_at)}</span><span>${escapeHtml(item.project_commit.slice(0,8))}</span></div></div>
    <div><strong class="score">${fmtScore(item.scores.current)}</strong> <button class="button button-ghost button-small" data-detail="${item.id}">详情</button></div>
  </article>`).join("");
  $$('[data-detail]').forEach(button=>button.addEventListener("click",()=>showDetail(button.dataset.detail)));
}
function updatePolling(){const active=state.experiments.some(x=>["queued","preparing","running"].includes(x.status));if(active&&!state.poller)state.poller=setInterval(()=>loadAll().catch(()=>{}),4000);if(!active&&state.poller){clearInterval(state.poller);state.poller=null;}}

async function loadExpectedMarkdown(event){
  const input=event.currentTarget,file=input.files?.[0],status=$("#expectedFileStatus");
  status.classList.remove("loaded","error");
  if(!file){status.textContent="文件仅在浏览器本地读取，内容会填入上方文本框。";return;}
  if(!/\.(md|markdown)$/i.test(file.name)){input.value="";status.textContent="请选择 .md 或 .markdown 文件。";status.classList.add("error");toast("预期效果只支持 Markdown 文件");return;}
  if(file.size>2*1024*1024){input.value="";status.textContent="文件超过 2 MB，请精简后重试。";status.classList.add("error");toast("Markdown 文件不能超过 2 MB");return;}
  try{const content=await file.text();if(!content.trim())throw new Error("Markdown 文件内容为空");$("#expected").value=content;invalidateDraft();status.textContent=`已导入 ${file.name} · ${(file.size/1024).toFixed(1)} KB`;status.classList.add("loaded");}
  catch(error){input.value="";status.textContent=error.message||"无法读取 Markdown 文件。";status.classList.add("error");toast(status.textContent);}
}

async function generateDraft(){
  const required=[$("#projectPath"),$("#skillPath"),$("#skillInput"),$("#expected")],missing=required.find(field=>!field.value.trim());
  if(missing){showMessage("请先填写项目地址、Skill、输入和预期效果。",true);missing.focus();return;}
  const button=$("#draftButton");button.disabled=true;button.textContent="正在验证项目与 Skill…";
  try{const payload={project_path:$("#projectPath").value.trim(),skill_path:$("#skillPath").value.trim(),input:$("#skillInput").value.trim(),expected:$("#expected").value.trim()};state.draft=await api("/api/v1/rubric-drafts",{method:"POST",body:JSON.stringify(payload)});$("#verifiedContext").innerHTML=`<div><small>项目基线</small><strong>${escapeHtml(state.draft.project.name)} · ${state.draft.project.commit.slice(0,10)}</strong></div><div><small>Skill 修订</small><strong>${escapeHtml(state.draft.skill.name)} · ${state.draft.skill.content_hash.slice(0,10)}</strong></div>`;$("#suiteName").value=`${state.draft.skill.name} / ${state.draft.project.name}`;$("#caseJson").value=JSON.stringify(state.draft.case,null,2);$("#draftPanel").classList.remove("hidden");showMessage("评分草案已生成。请确认 Rubric、权重和验证命令。",false);}catch(error){showMessage(error.message,true);}finally{button.disabled=false;button.textContent="生成评分草案";}
}
function showMessage(message,error){const node=$("#suiteMessage");node.textContent=message;node.classList.remove("hidden","error");if(error)node.classList.add("error");}
async function saveSuite(){let saved;try{if(!state.draft)throw new Error("请先生成评分草案");const name=$("#suiteName").value.trim();if(!name){$("#suiteName").focus();throw new Error("请填写套件名称");}let caseData;try{caseData=JSON.parse($("#caseJson").value);}catch{throw new Error("评分用例 JSON 格式不正确");}const payload={name,project_path:$("#projectPath").value.trim(),skill_path:$("#skillPath").value.trim(),setup:{commands:lines($("#setupCommands").value),preflight:lines($("#preflightCommands").value),network:false,timeout_seconds:900},cases:[caseData]};saved=await api("/api/v1/suites",{method:"POST",body:JSON.stringify(payload)});}catch(error){showMessage(error.message,true);return;}$("#newSuite").close();resetSuiteForm();toast(`测试套件已保存：${saved.name}`);try{await loadAll();}catch{toast("套件已保存，但列表刷新失败，请手动刷新");}}
function invalidateDraft(){if(!state.draft)return;state.draft=null;$("#draftPanel").classList.add("hidden");showMessage("输入已变更，请重新生成评分草案。",false);}
function resetSuiteForm(){$("#suiteForm").reset();state.draft=null;$("#draftPanel").classList.add("hidden");$("#verifiedContext").innerHTML="";$("#suiteMessage").textContent="";$("#suiteMessage").classList.add("hidden");const status=$("#expectedFileStatus");status.textContent="文件仅在浏览器本地读取，内容会填入上方文本框。";status.classList.remove("loaded","error");}

function selectedModel(provider,role){return provider==="chrys"?$(`#${role}ChrysModel`).value:$(`#${role}CodexModel`).value.trim();}
async function launchExperiment(){
  const suiteId=$("#runSuite").value;if(!suiteId)return toast("请先创建测试套件");
  const runnerProvider=$("#runnerProvider").value;if(!runnerProvider)return toast("没有可用的 Runner");
  const runnerModel=selectedModel(runnerProvider,"runner");if(!runnerModel)return toast("请选择或填写 Runner 模型");
  const same=$("#judgeSame").checked,judgeProvider=same?runnerProvider:$("#judgeProvider").value,judgeModel=same?runnerModel:selectedModel(judgeProvider,"judge");
  if(!judgeProvider||!judgeModel)return toast("请选择或填写 Judge 模型");
  const runnerEffort=$("#runnerEffort").value,judgeEffort=same?runnerEffort:$("#judgeEffort").value;
  const profileName=`${runnerProvider}-${runnerModel}__${judgeProvider}-${judgeModel}`;
  try{const body={suite_id:suiteId,mode:$("#runMode").value,profile:{schema_version:2,name:profileName,runner_provider:runnerProvider,runner_model:runnerModel,runner_reasoning_effort:runnerEffort,judge_provider:judgeProvider,judge_model:judgeModel,judge_reasoning_effort:judgeEffort,timeout_seconds:1800,network:false,allowed_mcp_servers:[]}};const result=await api("/api/v1/experiments",{method:"POST",body:JSON.stringify(body)});toast(`实验 ${result.id.slice(0,8)} 已加入队列`);try{await loadAll();}catch{toast("实验已入队，但列表刷新失败，请手动刷新");}}catch(error){toast(error.message);}
}
async function showDetail(id){try{const item=await api(`/api/v1/experiments/${id}`);$("#detailTitle").textContent=item.suite_name;const rows=item.runs.map(run=>`<tr><td>${escapeHtml(run.case_id)}</td><td>${escapeHtml(run.group)}</td><td>${run.trial}</td><td class="score">${fmtScore(run.quality_score)}</td><td>${run.hard_gates.passed}/${run.hard_gates.total}</td><td>${escapeHtml(run.scores?.skill_invoked||"unknown")}</td><td>${run.duration_ms==null?"—":(run.duration_ms/1000).toFixed(1)+"s"}</td><td><span class="pill ${run.status}">${escapeHtml(run.status)}</span></td><td><button class="text-button" data-evidence="${run.id}">证据</button><button class="text-button" data-review="${run.id}">复核${run.reviews.length?` (${run.reviews.length})`:""}</button></td></tr>`).join("");const p=item.profile;$("#detailBody").innerHTML=`<div class="experiment-meta"><span class="pill ${item.status}">${escapeHtml(item.status)}</span><span>commit ${escapeHtml(item.project_commit.slice(0,10))}</span><span>${providerName(p.runner.provider)} ${escapeHtml(modelName(p.runner))} → ${providerName(p.judge.provider)} ${escapeHtml(modelName(p.judge))}</span>${p.self_judge?'<span class="self-judge">Self-judge</span>':""}</div><div class="profile-strip"><span>Runner 隔离：${escapeHtml(p.runner.isolation)}</span><span>网络：${escapeHtml(p.runner.network_policy)}</span><span>Profile ${escapeHtml(p.hash.slice(0,10))}</span></div><div class="detail-scores"><div class="detail-score"><small>无 Skill</small><strong>${fmtScore(item.scores.no_skill)}</strong></div><div class="detail-score"><small>上一基准</small><strong>${fmtScore(item.scores.baseline)}</strong></div><div class="detail-score"><small>当前候选</small><strong>${fmtScore(item.scores.current)}</strong></div></div><p>当前 vs 无 Skill：${fmtDelta(item.delta_no_skill)}　 当前 vs 基准：${fmtDelta(item.delta_baseline)}</p>${item.error_message?`<div class="message error">${escapeHtml(item.error_message)}</div>`:""}<div class="table-card"><table class="run-table"><thead><tr><th>用例</th><th>组别</th><th>Trial</th><th>质量分</th><th>Hard gates</th><th>Skill 调用</th><th>耗时</th><th>状态</th><th>检查</th></tr></thead><tbody>${rows||'<tr><td colspan="9" class="empty">准备运行中</td></tr>'}</tbody></table></div>${item.status==="completed"?'<button class="button button-ghost" id="setBaselineButton">将当前修订设为基准版本</button>':""}`;const baseline=$("#setBaselineButton");if(baseline)baseline.addEventListener("click",()=>setBaseline(item));$$('[data-evidence]').forEach(button=>button.addEventListener("click",()=>showEvidence(button.dataset.evidence)));$$('[data-review]').forEach(button=>button.addEventListener("click",()=>addReview(id,button.dataset.review)));if(!$("#detailModal").open)$("#detailModal").showModal();}catch(error){toast(error.message);}}
async function setBaseline(item){try{await api(`/api/v1/skills/${item.skill_id}/baseline`,{method:"POST",body:JSON.stringify({revision_id:item.current_revision_id})});toast("已设为基准版本");try{await loadAll();}catch{toast("基准已保存，但页面刷新失败");}}catch(error){toast(error.message);}}
async function showEvidence(runId){try{const result=await api(`/api/v1/runs/${runId}/artifacts`);if(!result.items.length)return toast("这个 run 暂无证据文件");const preferred=result.items.find(item=>item.name==="scores.json")||result.items[0];window.open(preferred.url,"_blank","noopener");}catch(error){toast(error.message);}}
async function addReview(experimentId,runId){const scoreText=window.prompt("人工复核分（0–100）");if(scoreText===null)return;const score=Number(scoreText);if(!Number.isFinite(score)||score<0||score>100)return toast("请输入 0–100 的分数");const note=window.prompt("复核说明（可留空）")??"";try{await api(`/api/v1/runs/${runId}/reviews`,{method:"POST",body:JSON.stringify({score,note,reviewer:"local-user"})});toast("人工复核已保存");await showDetail(experimentId);}catch(error){toast(error.message);}}

document.addEventListener("DOMContentLoaded",()=>{
  $$('[data-open]').forEach(button=>button.addEventListener("click",()=>$("#"+button.dataset.open).showModal()));$$('[data-close]').forEach(button=>button.addEventListener("click",()=>$("#"+button.dataset.close).close()));$$('[data-scroll]').forEach(button=>button.addEventListener("click",()=>$("#"+button.dataset.scroll).scrollIntoView()));
  $("#draftButton").addEventListener("click",generateDraft);$("#saveSuiteButton").addEventListener("click",saveSuite);$("#runButton").addEventListener("click",launchExperiment);$("#refreshButton").addEventListener("click",()=>{state.runtime=null;loadAll().then(()=>toast("已刷新"));});
  $("#expectedFile").addEventListener("change",loadExpectedMarkdown);
  $("#suiteForm").addEventListener("submit",event=>event.preventDefault());["#projectPath","#skillPath","#skillInput","#expected"].forEach(selector=>$(selector).addEventListener("input",invalidateDraft));
  ["#runnerProvider","#judgeProvider","#judgeSame"].forEach(selector=>$(selector).addEventListener("change",syncProviderControls));
  ["#runnerFilter","#judgeFilter","#modeFilter","#profileFilter"].forEach(selector=>$(selector).addEventListener("change",()=>{renderMetrics();renderSkills();}));
  loadAll().catch(error=>toast(error.message));
});
