(function () {
  "use strict";
  const CFG = Object.assign({ apiBase: "/api/v1", timeout: 15000, credentials: "same-origin" }, window.APP_CONFIG || {});
  const ASSIGNEES = ["张轶勃", "徐哲威", "宋东方", "张立肖", "孙杨宇鑫"];
  const STATUS = { todo: "待处理", in_progress: "处理中", resolved: "已解决" };
  const PRIORITY = { low: "低", medium: "中", high: "高" };
  const state = { items: [], editingId: null };
  const $ = (selector) => document.querySelector(selector);

  function escape(value) { return String(value ?? "").replace(/[&<>'"]/g, char => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "'":"&#39;", '"':"&quot;" })[char]); }
  function date(value) { return value ? new Intl.DateTimeFormat("zh-CN", { dateStyle:"medium", timeStyle:"short" }).format(new Date(value)) : "—"; }
  async function request(path, options) {
    const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), CFG.timeout);
    try { const res = await fetch(CFG.apiBase + path, Object.assign({ headers:{ Accept:"application/json" }, credentials:CFG.credentials, signal:controller.signal }, options)); const data = await res.json().catch(() => ({})); if (!res.ok) throw new Error(data.message || `请求失败（${res.status}）`); return data; } finally { clearTimeout(timer); }
  }
  function options(select, placeholder) {
    select.innerHTML = (placeholder ? `<option value="">${placeholder}</option>` : "")
      + ASSIGNEES.map(name => `<option value="${name}">${name}</option>`).join("");
  }
  function filters() { return { status: $("#statusFilter").value, assignee: $("#assigneeFilter").value, q: $("#searchFilter").value.trim() }; }
  async function load() {
    const params = new URLSearchParams(); Object.entries(filters()).forEach(([key, value]) => value && params.set(key, value));
    try { const result = await request(`/issues?${params}`); state.items = result.items; render(); $("#notice").hidden = true; } catch (error) { $("#notice").textContent = `暂时无法读取问题：${error.message}`; $("#notice").hidden = false; }
  }
  function render() {
    $("#openCount").textContent = state.items.filter(item => item.status !== "resolved").length;
    $("#board").innerHTML = Object.keys(STATUS).map(status => { const items = state.items.filter(item => item.status === status); return `<section class="column"><h2>${STATUS[status]} <span class="column__count">${items.length}</span></h2>${items.length ? items.map(card).join("") : '<p class="empty">暂无问题</p>'}</section>`; }).join("");
    document.querySelectorAll(".card").forEach(card => card.addEventListener("click", () => showDetail(card.dataset.id)));
  }
  function card(item) { return `<button class="card" type="button" data-id="${item.id}"><span class="badge priority-${item.priority}">${PRIORITY[item.priority]}优先级</span><h3>${escape(item.title)}</h3><p>${escape(item.description)}</p><div class="card__meta"><span>提：${escape(item.reporter)} · 责：${escape(item.assignee)}</span><time>${date(item.updated_at)}</time></div></button>`; }
  function openNew() { state.editingId = null; $("#issueForm").reset(); $("#dialogTitle").textContent = "新增问题"; $("#issueDialog").showModal(); }
  async function showDetail(id) {
    try { const issue = await request(`/issues/${id}`); const activities = issue.activities.map(activity => `<li>${date(activity.created_at)} · ${activity.action === "created" ? "创建问题" : "更新：" + Object.keys(activity.details).join("、")}</li>`).join(""); $("#detailContent").innerHTML = `<div class="detail"><div class="dialog__head"><h2>${escape(issue.title)}</h2><button class="icon-btn" type="button" data-close aria-label="关闭">×</button></div><div class="detail__meta"><span>提出人：${escape(issue.reporter)}</span><span>责任人：${escape(issue.assignee)}</span><span>${STATUS[issue.status]}</span><span>${PRIORITY[issue.priority]}优先级</span>${issue.component ? `<span>${escape(issue.component)}</span>` : ""}</div><p class="detail__description">${escape(issue.description)}</p><div><strong>处理记录</strong><ul class="activity">${activities || "<li>暂无记录</li>"}</ul></div><button class="primary" type="button" id="editIssue">编辑问题</button></div>`; $("#detailDialog").showModal(); $("#detailContent [data-close]").onclick = () => $("#detailDialog").close(); $("#editIssue").onclick = () => edit(issue); } catch (error) { alert(error.message); }
  }
  function edit(issue) { state.editingId = issue.id; $("#detailDialog").close(); $("#dialogTitle").textContent = "编辑问题"; const form = $("#issueForm"); ["title","description","reporter","assignee","priority","status","component","sr","ar"].forEach(key => form.elements[key].value = issue[key] || ""); $("#issueDialog").showModal(); }
  async function save(event) { event.preventDefault(); const form = event.currentTarget; const payload = Object.fromEntries(new FormData(form)); Object.keys(payload).forEach(key => { if (payload[key] === "") payload[key] = null; }); try { await request(state.editingId ? `/issues/${state.editingId}` : "/issues", { method: state.editingId ? "PATCH" : "POST", headers:{ "Content-Type":"application/json", Accept:"application/json" }, body:JSON.stringify(payload) }); $("#issueDialog").close(); await load(); } catch (error) { alert(`保存失败：${error.message}`); } }
  document.addEventListener("DOMContentLoaded", () => { options($("#assigneeFilter"), "全部人员"); options($("#formAssignee")); $("#newIssue").onclick = openNew; $("#issueForm").onsubmit = save; document.querySelectorAll("[data-close]").forEach(button => button.onclick = () => button.closest("dialog").close()); ["#statusFilter", "#assigneeFilter"].forEach(selector => $(selector).onchange = load); let timer; $("#searchFilter").oninput = () => { clearTimeout(timer); timer = setTimeout(load, 250); }; load(); });
}());
