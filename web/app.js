'use strict';
/* OCI Pilot · 前端 SPA（vanilla JS） */

/* ================= 状态 ================= */
const S = {
  token: localStorage.getItem('op_token') || '',
  user: '',
  accountId: localStorage.getItem('op_account') || '',
  accounts: [],
  page: 'overview',
  instances: [],
  tenancyCache: {},
  termTabs: {},
  activeTab: null,
};
try { S.tenancyCache = JSON.parse(localStorage.getItem('op_tenancy') || '{}'); } catch (e) { /* ignore */ }
function saveTenancyCache() { localStorage.setItem('op_tenancy', JSON.stringify(S.tenancyCache)); }

/* ================= 工具 ================= */
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function fmtGB(v) { return v == null ? '-' : Number(v).toLocaleString(); }
function fmtTime(s) { return s ? String(s).slice(0, 19).replace('T', ' ') : '-'; }
function $(id) { return document.getElementById(id); }
function pageEl() { return $('page'); }

async function api(method, path, body) {
  const headers = {};
  if (S.token) headers['Authorization'] = 'Bearer ' + S.token;
  const opt = { method, headers };
  if (body instanceof FormData) { opt.body = body; }
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch('/api' + path, opt);
  if (r.status === 401) { logout(); throw new Error('登录已过期，请重新登录'); }
  const text = await r.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (e) { /* 非 JSON */ }
  if (!r.ok) throw new Error((data && (data.detail || data.message)) || ('请求失败 HTTP ' + r.status));
  return data;
}
/* 需要原始 Response（文件下载） */
async function apiRaw(method, path, body) {
  const headers = {};
  if (S.token) headers['Authorization'] = 'Bearer ' + S.token;
  const opt = { method, headers };
  if (body instanceof FormData) opt.body = body;
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch('/api' + path, opt);
  if (r.status === 401) { logout(); throw new Error('登录已过期'); }
  if (!r.ok) {
    const t = await r.text(); let d = {};
    try { d = JSON.parse(t); } catch (e) {}
    throw new Error((d && (d.detail || d.message)) || ('请求失败 HTTP ' + r.status));
  }
  return r;
}

let _loadingN = 0;
function showLoading() { _loadingN++; $('loading-overlay').classList.remove('hidden'); }
function hideLoading() { _loadingN = Math.max(0, _loadingN - 1); if (!_loadingN) $('loading-overlay').classList.add('hidden'); }
async function withLoading(fn) { showLoading(); try { return await fn(); } finally { hideLoading(); } }

function toast(msg, type) {
  const root = $('toast-root');
  const el = document.createElement('div');
  el.className = 'toast ' + (type || 'info');
  el.textContent = msg;
  root.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .4s'; setTimeout(() => el.remove(), 400); }, 4200);
}

function openModal(title, html, wide) {
  const root = $('modal-root');
  const mask = document.createElement('div');
  mask.className = 'modal-mask';
  mask.innerHTML = '<div class="modal' + (wide ? ' wide' : '') + '"><h3>' + esc(title) +
    '</h3><div class="mbody"></div><div class="modal-foot">' +
    '<button class="btn ghost" data-close>取消</button></div></div>';
  const body = mask.querySelector('.mbody');
  body.innerHTML = html;
  const close = () => mask.remove();
  mask.querySelector('[data-close]').addEventListener('click', close);
  mask.addEventListener('mousedown', e => { if (e.target === mask) close(); });
  root.appendChild(mask);
  return { el: mask, body, close };
}
/* 表单弹窗：onOk(formEl) 返回 true 则关闭 */
function formModal(title, html, onOk, wide, okLabel) {
  const m = openModal(title, '<form>' + html +
    '<div class="modal-foot" style="padding:0;border:0"><button type="button" class="btn ghost" data-cancel>取消</button>' +
    '<button type="submit" class="btn primary">' + esc(okLabel || '确定') + '</button></div></form>', wide);
  const dup = m.el.querySelector('.modal > .modal-foot');
  if (dup) dup.remove();
  const form = m.body.querySelector('form');
  form.querySelector('[data-cancel]').addEventListener('click', m.close);
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const btn = form.querySelector('[type=submit]');
    btn.disabled = true;
    try { if (await onOk(form)) m.close(); }
    catch (err) { toast(err.message, 'err'); }
    finally { btn.disabled = false; }
  });
  return m;
}
function confirmDlg(title, msg, okLabel) {
  return new Promise(resolve => {
    const m = openModal(title, '<p>' + msg + '</p><div class="modal-foot" style="padding:0;border:0">' +
      '<button class="btn ghost" data-no>取消</button>' +
      '<button class="btn danger" data-yes>' + esc(okLabel || '确认') + '</button></div>');
    const dupFoot = m.el.querySelector('.modal > .modal-foot');
    if (dupFoot) dupFoot.remove(); // 去掉 openModal 默认的 foot，用自定义的
    m.body.querySelector('[data-no]').addEventListener('click', () => { m.close(); resolve(false); });
    m.body.querySelector('[data-yes]').addEventListener('click', () => { m.close(); resolve(true); });
  });
}
function field(label, inner, cls) {
  return '<label class="f' + (cls ? ' ' + cls : '') + '"><span>' + esc(label) + '</span>' + inner + '</label>';
}
function inputVal(form, name) {
  const el = form.querySelector('[name=' + name + ']');
  if (!el) return '';
  if (el.type === 'checkbox') return el.checked;
  return el.value.trim();
}
function copyText(t) {
  navigator.clipboard.writeText(t).then(() => toast('已复制', 'ok'), () => toast('复制失败', 'err'));
}

/* ================= 认证 ================= */
function showLogin() {
  $('login-view').classList.remove('hidden');
  $('app').classList.add('hidden');
  // 首次部署(库里没账号)显示 Telegram 开户指引
  fetch('/api/auth/setup-needed').then(r => r.json()).then(d => {
    if (d && d.needed === true) $('setup-hint').classList.remove('hidden');
  }).catch(() => {});
  $('form-login-code').addEventListener('submit', async e => {
    e.preventDefault();
    const code = $('login-code').value.trim();
    if (!code) {
      $('login-err').textContent = '请先输入 6 位验证码（在 Telegram 里给机器人发 /logincode 获取）';
      return;
    }
    try {
      const r = await api('POST', '/auth/login', { login_code: code });
      S.token = r.token; localStorage.setItem('op_token', r.token); S.user = r.username || '';
      enterApp();
    } catch (err) { $('login-err').textContent = err.message; }
  });
}
function logout() {
  S.token = ''; localStorage.removeItem('op_token');
  location.hash = '#/login'; location.reload();
}

async function enterApp() {
  $('login-view').classList.add('hidden');
  $('app').classList.remove('hidden');
  renderNav();
  $('btn-logout').addEventListener('click', logout);
  $('btn-acc-refresh').addEventListener('click', () => loadAccounts(true));
  $('account-select').addEventListener('change', e => {
    S.accountId = e.target.value;
    localStorage.setItem('op_account', S.accountId);
    S.instances = []; // 账号变了，实例缓存失效
    route();
  });
  setInterval(() => { $('tb-clock').textContent = new Date().toLocaleString('zh-CN'); }, 1000);
  await loadAccounts();
  if (!location.hash || location.hash === '#/login') location.hash = '#/overview';
  route();
}

async function loadAccounts(silent) {
  try {
    S.accounts = await api('GET', '/accounts');
  } catch (e) { if (!silent) toast(e.message, 'err'); S.accounts = []; }
  const sel = $('account-select');
  sel.innerHTML = S.accounts.map(a =>
    '<option value="' + a.id + '">' + esc(a.name) + '（' + esc(a.region) + '）</option>').join('');
  if (!S.accounts.find(a => String(a.id) === String(S.accountId))) {
    S.accountId = S.accounts.length ? String(S.accounts[0].id) : '';
    localStorage.setItem('op_account', S.accountId);
  }
  sel.value = S.accountId;
  $('side-user').textContent = '👤 ' + (S.user || '');
}

/* ================= 导航/路由 ================= */
const NAV = [
  { id: 'overview', label: '总览', ico: '📊' },
  { id: 'instances', label: '实例管理', ico: '🖥️' },
  { id: 'network', label: '网络', ico: '🌐' },
  { id: 'volumes', label: '硬盘', ico: '💽' },
  { id: 'objectstore', label: '对象存储', ico: '🗄️' },
  { id: 'console', label: '串行控制台', ico: '🖧' },
  { id: 'monitors', label: '监控告警', ico: '🔔' },
  { id: 'ssh', label: 'SSH 终端', ico: '💻' },
  { id: 'ops', label: '服务器运维', ico: '🛠️' },
  { id: 'settings', label: '设置', ico: '⚙️' },
];
function renderNav() {
  $('nav').innerHTML = NAV.map(n =>
    '<a href="#/' + n.id + '" data-nav="' + n.id + '"><span class="ico">' + n.ico +
    '</span>' + n.label + '</a>').join('');
}
function route() {
  const id = (location.hash || '#/overview').replace('#/', '').split('?')[0] || 'overview';
  S.page = NAV.find(n => n.id === id) ? id : 'overview';
  document.querySelectorAll('#nav a').forEach(a =>
    a.classList.toggle('active', a.dataset.nav === S.page));
  const fn = PAGES[S.page];
  pageEl().innerHTML = '<div class="empty">加载中…</div>';
  withLoading(() => fn()).catch(e => {
    pageEl().innerHTML = '<div class="card"><p style="color:var(--red)">加载失败：' + esc(e.message) + '</p>' +
      '<button class="btn" onclick="route()">重试</button></div>';
  });
}
window.addEventListener('hashchange', route);

function needAccount() {
  if (!S.accounts.length) {
    pageEl().innerHTML = '<div class="card"><h3>还没有 OCI 账号</h3>' +
      '<p class="muted">请先去「设置」页添加 OCI Profile（Tenancy OCID / User OCID / 指纹 / 私钥 / Region）。</p>' +
      '<a class="btn primary" href="#/settings">去设置</a></div>';
    return false;
  }
  return true;
}
/* compartment 解析：手动输入 > 建账号时本地缓存的 tenancy > 空（后端默认租户） */
function resolveCompartment(manual) {
  return (manual || '').trim() || S.tenancyCache[String(S.accountId)] || '';
}
const PAGES = {};

/* ================= 总览 ================= */
PAGES.overview = async function () {
  if (!needAccount()) return;
  const el = pageEl();
  const d = await api('GET', '/overview?account_id=' + S.accountId);
  el.innerHTML =
    '<div class="page-head"><h2>总览</h2><button class="btn sm" onclick="route()">↻ 刷新</button></div>' +
    '<div class="grid c5">' +
    statCard(d.instances_total, '实例总数', '') +
    statCard(d.running, '运行中', 'green') +
    statCard(d.stopped, '已停止', 'yellow') +
    statCard(d.tasks_running, '抢机任务进行中', 'cyan') +
    statCard(fmtGB(d.month_egress_gb), '本月流出 (GB)', '') +
    '</div>' +
    '<div class="card"><h3>Profile / OCI 账号</h3><div class="grid c3">' +
    (d.accounts || []).map(a =>
      '<div class="stat"><div class="v" style="font-size:17px">' + esc(a.name) + '</div>' +
      '<div class="k mono">' + esc(a.region) + ' · #' + a.id + '</div></div>').join('') +
    '</div></div>';
};
function statCard(v, k, cls) {
  return '<div class="stat"><div class="v ' + cls + '">' + esc(v) + '</div><div class="k">' + esc(k) + '</div></div>';
}

/* ================= 设置（OCI Profile） ================= */
PAGES.settings = async function () {
  const el = pageEl();
  const accs = S.accounts;
  el.innerHTML =
    '<div class="page-head"><h2>设置 · OCI Profile</h2>' +
    '<button class="btn primary" id="btn-add-acc">＋ 添加账号</button></div>' +
    '<div class="card"><div class="tbl-wrap"><table class="tbl"><thead><tr>' +
    '<th>名称</th><th>Region</th><th>Tenancy</th><th>代理</th><th>操作</th></tr></thead><tbody>' +
    accs.map(a => '<tr><td><b>' + esc(a.name) + '</b></td><td class="mono">' + esc(a.region) + '</td>' +
      '<td class="mono">' + esc(a.tenancy) + '</td><td class="mono small">' + esc(a.proxy || '-') + '</td>' +
      '<td><div class="btn-row"><button class="btn sm" data-test="' + a.id + '">测活</button>' +
      '<button class="btn sm danger" data-del="' + a.id + '">删除</button></div></td></tr>').join('') +
    '</tbody></table></div>' + (accs.length ? '' : '<div class="empty">还没有账号，点击右上添加</div>') + '</div>' +
    '<div class="card"><h3>数据说明</h3><p class="muted small">所有 API 私钥经 Fernet 加密后只保存在本机 <code>data/</code> 目录；' +
    'Telegram Bot 不存储任何密钥。删除账号会一并删除其抢机任务与监控配置。</p></div>';

  el.querySelector('#btn-add-acc').addEventListener('click', () => {
    formModal('添加 OCI 账号', accFormHtml(), async form => {
      const data = {
        name: inputVal(form, 'name'), tenancy_ocid: inputVal(form, 'tenancy_ocid'),
        user_ocid: inputVal(form, 'user_ocid'), fingerprint: inputVal(form, 'fingerprint'),
        region: inputVal(form, 'region'), private_key_pem: form.querySelector('[name=private_key_pem]').value,
        proxy: inputVal(form, 'proxy'),
      };
      if (!data.name || !data.tenancy_ocid || !data.user_ocid || !data.fingerprint || !data.region || !data.private_key_pem.trim()) {
        toast('请填完必填项', 'warn'); return false;
      }
      const r = await withLoading(() => api('POST', '/accounts', data));
      const id = r.id != null ? r.id : r.account_id;
      if (id) { S.tenancyCache[String(id)] = data.tenancy_ocid; saveTenancyCache(); }
      toast('账号已添加', 'ok');
      await loadAccounts(); route(); return true;
    }, true);
  });
  el.querySelectorAll('[data-test]').forEach(b => b.addEventListener('click', async () => {
    const id = b.dataset.test;
    b.disabled = true;
    try {
      const r = await withLoading(() => api('POST', '/accounts/' + id + '/test'));
      toast(r.ok ? '✅ 测活成功：' + (r.tenancy_name || '') : '❌ 测活失败：' + (r.error || ''), r.ok ? 'ok' : 'err');
    } catch (e) { toast(e.message, 'err'); }
    b.disabled = false;
  }));
  el.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', async () => {
    if (!await confirmDlg('删除账号', '确定删除该 OCI 账号及其本地配置吗？', '删除')) return;
    await withLoading(() => api('DELETE', '/accounts/' + b.dataset.del));
    delete S.tenancyCache[String(b.dataset.del)]; saveTenancyCache();
    toast('已删除', 'ok'); await loadAccounts(); route();
  }));

};
function accFormHtml() {
  return field('名称 *', '<input name="name" placeholder="如：hk-free">') +
    field('Tenancy OCID *', '<input name="tenancy_ocid" placeholder="ocid1.tenancy.oc1...">') +
    field('User OCID *', '<input name="user_ocid" placeholder="ocid1.user.oc1...">') +
    field('API 指纹 *', '<input name="fingerprint" placeholder="aa:bb:cc:...">') +
    '<div class="frow">' +
    field('Region *', '<input name="region" placeholder="如 ap-tokyo-1">') +
    field('出站代理（可选）', '<input name="proxy" placeholder="http://127.0.0.1:7890">') + '</div>' +
    field('API 私钥 PEM *', '<textarea name="private_key_pem" rows="6" placeholder="-----BEGIN RSA PRIVATE KEY----- ..."></textarea>') +
    '<div class="hint">从 OCI 控制台 → 用户 → API 密钥 处获取以上信息</div>';
}

/* ================= 实例管理 ================= */
async function fetchInstances() {
  S.instances = await api('GET', '/instances?account_id=' + S.accountId);
  return S.instances;
}
PAGES.instances = async function () {
  if (!needAccount()) return;
  const el = pageEl();
  await fetchInstances();
  const tasks = await api('GET', '/launch/tasks').catch(() => []);
  const running = tasks.filter(t => t.status === 'running');

  el.innerHTML =
    '<div class="page-head"><h2>实例管理</h2><div class="toolbar">' +
    '<button class="btn sm" id="btn-a1">🩺 A1 免费额度体检</button>' +
    '<button class="btn sm" id="btn-refresh">↻ 刷新</button>' +
    '<button class="btn primary" id="btn-launch">＋ 创建实例</button></div></div>' +
    '<div class="card"><div class="tbl-wrap"><table class="tbl"><thead><tr>' +
    '<th>名称</th><th>形状</th><th>配置</th><th>状态</th><th>公网 IP</th><th>可用域</th><th>操作</th></tr></thead><tbody id="inst-tbody"></tbody></table></div>' +
    (S.instances.length ? '' : '<div class="empty">该账号下没有实例</div>') + '</div>' +
    '<div class="card"><h3>抢机任务（' + running.length + ' 进行中）</h3>' +
    '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>任务</th><th>账号</th><th>状态</th><th>尝试</th><th>最后错误</th><th>创建时间</th><th></th></tr></thead><tbody>' +
    tasks.map(t => '<tr><td><b>' + esc(t.name) + '</b> <span class="muted small">#' + t.id + '</span></td>' +
      '<td>#' + t.account_id + '</td><td><span class="badge ' + t.status + '">' + t.status + '</span></td>' +
      '<td class="mono">' + t.attempts + '</td><td class="small muted">' + esc((t.last_error || '').slice(0, 80)) + '</td>' +
      '<td class="small">' + fmtTime(t.created_at) + '</td>' +
      '<td>' + (t.status === 'running' ? '<button class="btn sm danger" data-cancel-task="' + t.id + '">取消</button>' : '') + '</td></tr>').join('') +
    '</tbody></table></div>' + (tasks.length ? '' : '<div class="empty">暂无抢机任务</div>') + '</div>';

  renderInstRows(el);
  el.querySelector('#btn-refresh').addEventListener('click', () => route());
  el.querySelector('#btn-launch').addEventListener('click', openLaunchModal);
  el.querySelector('#btn-a1').addEventListener('click', openA1Audit);
  el.querySelectorAll('[data-cancel-task]').forEach(b => b.addEventListener('click', async () => {
    if (!await confirmDlg('取消任务', '确定取消抢机任务 #' + b.dataset.cancelTask + ' 吗？')) return;
    await api('POST', '/launch/tasks/' + b.dataset.cancelTask + '/cancel');
    toast('已取消', 'ok'); route();
  }));
};
function renderInstRows(el) {
  const tb = el.querySelector('#inst-tbody');
  tb.innerHTML = S.instances.map(i => {
    const vnic = (i.vnics && i.vnics[0]) || {};
    return '<tr><td><b>' + esc(i.name) + '</b><div class="muted small mono">' + esc(i.id).slice(-12) + '</div></td>' +
      '<td class="mono small">' + esc(i.shape) + '</td>' +
      '<td class="mono small">' + esc(i.ocpus || '-') + 'C / ' + esc(i.memory_gb || '-') + 'G</td>' +
      '<td><span class="badge ' + i.state + '">' + i.state + '</span></td>' +
      '<td class="mono">' + esc(i.public_ip || '-') + '</td>' +
      '<td class="small muted">' + esc((i.ad || '').split(':').pop()) + '</td>' +
      '<td><div class="btn-row">' +
      '<button class="btn sm" data-act="START" data-id="' + i.id + '">开机</button>' +
      '<button class="btn sm" data-act="STOP" data-id="' + i.id + '">关机</button>' +
      '<button class="btn sm" data-act="RESET" data-id="' + i.id + '">重启</button>' +
      '<button class="btn sm" data-detail="' + i.id + '">详情</button>' +
      '<button class="btn sm" data-ip="' + i.id + '" data-vnic="' + esc(vnic.vnic_id || '') + '">换IP</button>' +
      '<button class="btn sm" data-rename="' + i.id + '">改名</button>' +
      '<button class="btn sm" data-resize="' + i.id + '">升降配</button>' +
      '<button class="btn sm warn" data-reinstall="' + i.id + '">重装</button>' +
      '<button class="btn sm danger" data-term="' + i.id + '">终止</button>' +
      '<button class="btn sm danger" data-termkeep="' + i.id + '">终止(留盘)</button>' +
      '</div></td></tr>';
  }).join('');

  tb.querySelectorAll('[data-act]').forEach(b => b.addEventListener('click', async () => {
    const act = b.dataset.act;
    if (['STOP', 'RESET'].includes(act) &&
      !await confirmDlg('确认', '对实例执行 <b>' + act + '</b> 吗？')) return;
    await withLoading(() => api('POST', '/instances/' + b.dataset.id + '/action',
      { account_id: +S.accountId, action: act }));
    toast(act + ' 指令已发送', 'ok'); setTimeout(route, 1200);
  }));
  tb.querySelectorAll('[data-term]').forEach(b => b.addEventListener('click', () =>
    terminateInst(b.dataset.term, false)));
  tb.querySelectorAll('[data-termkeep]').forEach(b => b.addEventListener('click', () =>
    terminateInst(b.dataset.termkeep, true)));
  tb.querySelectorAll('[data-reinstall]').forEach(b => b.addEventListener('click', async () => {
    if (!await confirmDlg('重装系统', '将<b>终止当前实例并用相同配置重开一台</b>（数据会丢失），继续吗？', '重装')) return;
    const r = await withLoading(() => api('POST', '/instances/' + b.dataset.reinstall + '/reinstall',
      { account_id: +S.accountId }));
    toast('重装任务已提交 #' + (r.task_id || ''), 'ok'); route();
  }));
  tb.querySelectorAll('[data-detail]').forEach(b => b.addEventListener('click', () => showInstDetail(b.dataset.detail)));
  tb.querySelectorAll('[data-ip]').forEach(b => b.addEventListener('click', () => changeIp(b.dataset.id, b.dataset.vnic)));
  tb.querySelectorAll('[data-rename]').forEach(b => b.addEventListener('click', () => {
    const inst = S.instances.find(x => x.id === b.dataset.rename);
    formModal('改名', field('新名称', '<input name="n" value="' + esc(inst.name) + '">'), async form => {
      await api('PUT', '/instances/' + inst.id, { account_id: +S.accountId, display_name: inputVal(form, 'n') });
      toast('已改名', 'ok'); route(); return true;
    });
  }));
  tb.querySelectorAll('[data-resize]').forEach(b => b.addEventListener('click', () => {
    const inst = S.instances.find(x => x.id === b.dataset.resize);
    formModal('升降配（弹性实例需先关机）',
      field('OCPU', '<input name="ocpus" type="number" step="0.5" min="1" value="' + esc(inst.ocpus || '') + '">') +
      field('内存 GB', '<input name="memory_gb" type="number" step="1" min="1" value="' + esc(inst.memory_gb || '') + '">'),
      async form => {
        await api('PUT', '/instances/' + inst.id, {
          account_id: +S.accountId,
          ocpus: parseFloat(inputVal(form, 'ocpus')) || undefined,
          memory_gb: parseFloat(inputVal(form, 'memory_gb')) || undefined,
        });
        toast('配置已更新', 'ok'); route(); return true;
      });
  }));
}
async function terminateInst(id, preserve) {
  const ok = await confirmDlg('终止实例',
    preserve ? '终止实例但<b>保留启动盘</b>（之后可从启动盘重新开机），继续吗？' : '彻底<b>终止实例并删除所有磁盘</b>，不可恢复，继续吗？', '终止');
  if (!ok) return;
  await withLoading(() => api('POST', '/instances/' + id + '/terminate',
    { account_id: +S.accountId, preserve_boot_volume: preserve }));
  toast('终止指令已发送', 'ok'); setTimeout(route, 1200);
}
async function showInstDetail(id) {
  const i = await withLoading(() => api('GET', '/instances/' + id + '?account_id=' + S.accountId));
  const vnics = (i.vnics || []).map((v, k) =>
    '<div class="card"><h3>网卡 ' + (k + 1) + '</h3><dl class="kv">' +
    '<dt>公网 IP</dt><dd>' + esc(v.public_ip || '-') + '</dd>' +
    '<dt>内网 IP</dt><dd>' + esc(v.private_ip || '-') + '</dd>' +
    '<dt>IPv6</dt><dd>' + esc((v.ipv6 || []).join(', ') || '-') + '</dd>' +
    '<dt>VNIC ID</dt><dd>' + esc(v.vnic_id || '') + '</dd></dl></div>').join('');
  openModal('实例详情', '<dl class="kv">' +
    '<dt>名称</dt><dd>' + esc(i.name) + '</dd><dt>ID</dt><dd>' + esc(i.id) + '</dd>' +
    '<dt>形状</dt><dd>' + esc(i.shape) + '</dd>' +
    '<dt>配置</dt><dd>' + esc(i.ocpus || '-') + 'C / ' + esc(i.memory_gb || '-') + 'G</dd>' +
    '<dt>状态</dt><dd>' + esc(i.state) + '</dd><dt>可用域</dt><dd>' + esc(i.ad) + '</dd>' +
    '<dt>镜像</dt><dd>' + esc(i.image_id || '-') + '</dd>' +
    '<dt>创建时间</dt><dd>' + fmtTime(i.time_created) + '</dd></dl>' + vnics, true);
}
async function changeIp(instId, vnicId) {
  if (!vnicId) { toast('该实例没有网卡信息', 'warn'); return; }
  if (!await confirmDlg('换 IP', '将更换该实例的公网 IP（旧 IP 释放），继续吗？', '换 IP')) return;
  const r = await withLoading(() => api('POST', '/network/vnic/' + vnicId + '/change-ip', { account_id: +S.accountId }));
  const m = openModal('换 IP 成功', '<p>新 IP：<code>' + esc(r.new_ip) + '</code>' +
    '<span class="copy-btn" id="cp-newip">复制</span></p>');
  m.body.querySelector('#cp-newip').addEventListener('click', () => copyText(r.new_ip));
  route();
}

/* ---------- 创建实例（开机 / 抢机） ---------- */
async function openLaunchModal(bootVolume) {
  const presets = await api('GET', '/compute/presets').catch(() => ({}));
  const ads = await api('GET', '/compute/ads?account_id=' + S.accountId).catch(() => []);
  const subnets = await api('GET', '/compute/subnets?account_id=' + S.accountId).catch(() => []);
  const comp = resolveCompartment('');

  const html =
    '<div class="btn-row" style="margin-bottom:12px">' +
    Object.entries(presets).map(([k, p]) =>
      '<button type="button" class="btn sm" data-preset="' + k + '">⚡ ' + esc(p.label) + '</button>').join('') +
    '</div>' +
    '<div class="tabs" style="border:0;padding:0;margin-bottom:12px">' +
    '<button type="button" class="active" data-mode="image">全新镜像</button>' +
    '<button type="button" data-mode="boot">从现有启动盘开机</button></div>' +
    '<div class="frow">' +
    field('形状 Shape *', '<input name="shape" placeholder="VM.Standard.A1.Flex">') +
    field('Compartment OCID', '<input name="compartment_id" value="' + esc(comp) + '" placeholder="留空=租户根">') + '</div>' +
    '<div class="frow">' +
    field('OCPU', '<input name="ocpus" type="number" step="1" value="2">') +
    field('内存 GB', '<input name="memory_gb" type="number" step="1" value="12">') + '</div>' +
    '<div class="frow">' + field('可用域 AD *', '<select name="ad">' + ads.map(a => '<option>' + esc(a) + '</option>').join('') + '</select>') +
    field('子网 *', '<select name="subnet_id">' + subnets.map(s =>
      '<option value="' + s.id + '">' + esc(s.vcn + ' / ' + s.name) + (s.public ? '' : ' (内网)') + '</option>').join('') + '</select>') + '</div>' +
    '<div id="lm-image">' +
    field('硬盘 GB（留空默认）', '<input name="boot_gb" type="number" placeholder="">') +
    field('镜像搜索', '<div class="frow"><input name="img_kw" placeholder="如 Ubuntu" style="flex:1"><button type="button" class="btn sm" id="btn-img-search">搜索</button></div>') +
    field('镜像 *', '<select name="image_id"><option value="">— 先搜索镜像 —</option></select>') +
    '</div>' +
    '<div id="lm-boot" class="hidden">' +
    field('选择未挂载的启动盘 *', '<select name="boot_volume_id"><option value="">加载中…</option></select>') +
    '<div class="hint">从启动盘开机只能开 1 台，且实例必须位于该盘所在的可用域；形状 / OCPU / 内存按上方配置</div></div>' +
    '<div class="frow">' +
    field('实例显示名', '<input name="display_name" placeholder="默认自动生成">') +
    field('主机名前缀', '<input name="hostname_label" placeholder="可选">') + '</div>' +
    field('SSH 公钥 *', '<textarea name="ssh_key" rows="3" placeholder="ssh-rsa/ssh-ed25519 开头"></textarea>') +
    field('启动脚本（cloud-init user_data，可选）', '<textarea name="user_data" rows="3" placeholder="#!/bin/bash ..."></textarea>') +
    '<div class="frow">' +
    field('抢机延迟区间（秒）', '<div class="frow"><input name="delay_min" type="number" value="5"><input name="delay_max" type="number" value="15"></div>') +
    field('任务名', '<input name="task_name" placeholder="可选">') + '</div>' +
    '<label class="checkbox-row"><input type="checkbox" name="assign_public_ip" checked> 分配公网 IP</label>';

  const m = formModal('创建实例 · 抢机', html, async form => {
    const mode = form.querySelector('[data-mode].active').dataset.mode;
    const params = {
      compartment_id: resolveCompartment(inputVal(form, 'compartment_id')),
      ad: inputVal(form, 'ad'),
      shape: inputVal(form, 'shape'),
      ocpus: parseFloat(inputVal(form, 'ocpus')) || 1,
      memory_gb: parseFloat(inputVal(form, 'memory_gb')) || 1,
      subnet_id: inputVal(form, 'subnet_id'),
      ssh_key: form.querySelector('[name=ssh_key]').value.trim(),
      display_name: inputVal(form, 'display_name'),
      hostname_label: inputVal(form, 'hostname_label'),
      user_data: form.querySelector('[name=user_data]').value,
      assign_public_ip: form.querySelector('[name=assign_public_ip]').checked,
    };
    if (!params.shape || !params.subnet_id || !params.ad) { toast('形状 / 可用域 / 子网不能为空', 'warn'); return false; }
    if (mode === 'image') {
      params.image_id = inputVal(form, 'image_id');
      params.boot_gb = parseInt(inputVal(form, 'boot_gb')) || 0;
      if (!params.image_id) { toast('请选择镜像', 'warn'); return false; }
    } else {
      params.boot_volume_id = inputVal(form, 'boot_volume_id');
      if (!params.boot_volume_id) { toast('请选择启动盘', 'warn'); return false; }
    }
    if (!params.ssh_key) { toast('SSH 公钥不能为空', 'warn'); return false; }
    const r = await withLoading(() => api('POST', '/launch', {
      account_id: +S.accountId, name: inputVal(form, 'task_name') || params.shape,
      params, delay_min: parseInt(inputVal(form, 'delay_min')) || 5,
      delay_max: parseInt(inputVal(form, 'delay_max')) || 15,
    }));
    toast('✅ 抢机任务已提交 #' + r.task_id + '，缺货会自动重试，结果推送到 Telegram', 'ok');
    route(); return true;
  }, true, '提交抢机任务');

  const form = m.body.querySelector('form');
  // 模式切换
  form.querySelectorAll('[data-mode]').forEach(b => b.addEventListener('click', () => {
    form.querySelectorAll('[data-mode]').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    const isImg = b.dataset.mode === 'image';
    form.querySelector('#lm-image').classList.toggle('hidden', !isImg);
    form.querySelector('#lm-boot').classList.toggle('hidden', isImg);
  }));
  // 快速预设
  form.querySelectorAll('[data-preset]').forEach(b => b.addEventListener('click', () => {
    const p = presets[b.dataset.preset]; if (!p) return;
    form.querySelector('[name=shape]').value = p.shape || '';
    form.querySelector('[name=ocpus]').value = p.ocpus || '';
    form.querySelector('[name=memory_gb]').value = p.memory_gb || '';
    form.querySelector('[name=img_kw]').value = p.os_hint || '';
    toast('已填入：' + p.label, 'ok');
  }));
  // 镜像搜索
  form.querySelector('#btn-img-search').addEventListener('click', async () => {
    const kw = form.querySelector('[name=img_kw]').value.trim();
    const shape = form.querySelector('[name=shape]').value.trim();
    const imgs = await withLoading(() => api('GET', '/compute/images?account_id=' + S.accountId +
      '&keyword=' + encodeURIComponent(kw) + (shape ? '&shape=' + encodeURIComponent(shape) : '')));
    const sel = form.querySelector('[name=image_id]');
    sel.innerHTML = imgs.map(i => '<option value="' + i.id + '">' + esc(i.name) + '（' + esc(i.os || '') + '）</option>').join('')
      || '<option value="">无结果</option>';
  });
  // 启动盘下拉
  (async () => {
    try {
      const vols = await api('GET', '/volumes?account_id=' + S.accountId);
      const atts = await api('GET', '/volumes/attachments?account_id=' + S.accountId);
      const attached = new Set(atts.map(a => a.volume_id));
      const boots = vols.filter(v => v.is_boot && !attached.has(v.id));
      form.querySelector('[name=boot_volume_id]').innerHTML =
        boots.map(v => '<option value="' + v.id + '">' + esc(v.name) + '（' + v.size_gb + 'GB · ' + esc((v.ad || '').split(':').pop()) + '）</option>').join('')
        || '<option value="">没有可用的未挂载启动盘</option>';
      if (bootVolume) {
        form.querySelector('[data-mode="boot"]').click();
        form.querySelector('[name=boot_volume_id]').value = bootVolume;
        // 启动盘所在 AD 自动选中
        const bv = boots.find(v => v.id === bootVolume);
        if (bv) form.querySelector('[name=ad]').value = bv.ad;
      }
    } catch (e) { /* ignore */ }
  })();
}

/* ---------- A1 免费额度体检 ---------- */
async function openA1Audit() {
  const r = await withLoading(() => api('GET', '/compute/a1-audit?account_id=' + S.accountId));
  const over = r.over_quota;
  openModal('🩺 A1 免费额度体检',
    '<p>ARM 实例共 <b>' + r.instances.length + '</b> 台，合计 ' +
    '<b class="' + (r.total_ocpu > r.free_ocpu ? 'mono" style="color:var(--red)' : 'mono') + '">' + r.total_ocpu + '</b> OCPU / ' +
    '<b class="' + (r.total_mem > r.free_mem ? 'mono" style="color:var(--red)' : 'mono') + '">' + r.total_mem + '</b> GB 内存</p>' +
    '<p class="muted small">免费额度：' + r.free_ocpu + ' OCPU / ' + r.free_mem + ' GB</p>' +
    (over ? '<p style="color:var(--yellow)">⚠️ 已超出免费额度，甲骨文可能回收超额实例，建议降配（实例需先关机再调整）</p>'
      : '<p style="color:var(--green)">✅ 未超免费额度</p>') +
    '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>实例</th><th>配置</th><th>状态</th><th></th></tr></thead><tbody>' +
    r.instances.map(i => '<tr><td>' + esc(i.name) + '</td><td class="mono">' + i.ocpus + 'C/' + i.memory_gb + 'G</td>' +
      '<td><span class="badge ' + i.state + '">' + i.state + '</span></td>' +
      '<td><button class="btn sm" data-a1resize="' + i.id + '">调整配置</button></td></tr>').join('') +
    '</tbody></table></div>', true)
    .body.querySelectorAll('[data-a1resize]').forEach(b => b.addEventListener('click', () => {
      document.querySelector('#modal-root').innerHTML = '';
      const inst = S.instances.find(x => x.id === b.dataset.a1resize);
      formModal('降配 ' + inst.name,
        field('OCPU', '<input name="ocpus" type="number" step="1" value="' + esc(inst.ocpus) + '">') +
        field('内存 GB', '<input name="memory_gb" type="number" step="1" value="' + esc(inst.memory_gb) + '">'),
        async form => {
          await api('PUT', '/instances/' + inst.id, { account_id: +S.accountId,
            ocpus: parseFloat(inputVal(form, 'ocpus')) || undefined,
            memory_gb: parseFloat(inputVal(form, 'memory_gb')) || undefined });
          toast('已更新', 'ok'); route(); return true;
        });
    }));
}

/* ================= 网络 ================= */
PAGES.network = async function () {
  if (!needAccount()) return;
  const el = pageEl();
  if (!S.instances.length) await fetchInstances().catch(() => { S.instances = []; });
  const ips = await api('GET', '/network/public-ips?account_id=' + S.accountId).catch(() => []);
  const vcns = await api('GET', '/network/vcns?account_id=' + S.accountId).catch(() => []);

  el.innerHTML =
    '<div class="page-head"><h2>网络</h2></div>' +
    '<div class="card"><h3>实例网卡 · 换 IP / IPv6</h3>' +
    '<div class="toolbar" style="margin-bottom:12px"><select id="net-inst" style="min-width:260px">' +
    S.instances.map(i => '<option value="' + i.id + '">' + esc(i.name) + '（' + esc(i.public_ip || '无公网IP') + '）</option>').join('') +
    '</select></div><div id="net-vnics"></div></div>' +
    '<div class="card"><div class="page-head"><h3>保留 IP</h3><button class="btn sm primary" id="btn-rip-new">＋ 创建保留 IP</button></div>' +
    '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>IP</th><th>类型</th><th>状态</th><th>已绑定</th><th>操作</th></tr></thead><tbody>' +
    ips.map(p => '<tr><td class="mono"><b>' + esc(p.ip) + '</b></td><td>' + (p.lifetime === 'RESERVED' ? '保留' : '临时') + '</td>' +
      '<td><span class="badge ' + (p.state === 'ASSIGNED' ? 'RUNNING' : 'STOPPED') + '">' + esc(p.state) + '</span></td>' +
      '<td>' + (p.assigned ? '是' : '否') + '</td><td><div class="btn-row">' +
      (p.assigned ? '<button class="btn sm" data-unassign="' + p.id + '">解绑</button>'
        : '<button class="btn sm" data-assign="' + p.id + '">绑定</button>') +
      '<button class="btn sm danger" data-delip="' + p.id + '">删除</button></div></td></tr>').join('') +
    '</tbody></table></div>' + (ips.length ? '' : '<div class="empty">暂无公网 IP</div>') + '</div>' +
    '<div class="card"><h3>安全组 · 一键开放端口</h3><div class="frow">' +
    field('VCN', '<select id="sg-vcn">' + vcns.map(v => '<option value="' + v.id + '">' + esc(v.name) + '</option>').join('') + '</select>') +
    field('安全组', '<select id="sg-list"><option>先选 VCN</option></select>') +
    field('端口', '<input id="sg-port" type="number" placeholder="如 443">') +
    field('协议', '<select id="sg-proto"><option value="tcp">TCP</option><option value="udp">UDP</option><option value="icmp">ICMP</option></select>') +
    '</div><button class="btn primary" id="btn-sg-open">开放端口</button></div>';

  const instSel = el.querySelector('#net-inst');
  async function loadVnics() {
    const box = el.querySelector('#net-vnics');
    const inst = S.instances.find(x => x.id === instSel.value);
    if (!inst) { box.innerHTML = '<div class="empty">无实例</div>'; return; }
    const d = await withLoading(() => api('GET', '/instances/' + inst.id + '?account_id=' + S.accountId));
    box.innerHTML = (d.vnics || []).map((v, k) =>
      '<div class="card" style="background:var(--bg2)"><h3>网卡 ' + (k + 1) + '</h3><dl class="kv">' +
      '<dt>公网 IP</dt><dd><b>' + esc(v.public_ip || '无') + '</b></dd>' +
      '<dt>内网 IP</dt><dd>' + esc(v.private_ip || '-') + '</dd>' +
      '<dt>IPv6</dt><dd>' + ((v.ipv6 || []).map(ip =>
        esc(ip.ip || ip) + ' <button class="btn sm danger" data-del6="' + esc(ip.id || ip) + '">删除</button>').join('<br>') || '-') + '</dd>' +
      '</dl><div class="btn-row" style="margin-top:10px">' +
      '<button class="btn sm warn" data-changeip="' + v.vnic_id + '">🔄 换 IP</button>' +
      '<button class="btn sm" data-add6="' + v.vnic_id + '">＋ 附加 IPv6</button></div></div>').join('')
      || '<div class="empty">无网卡</div>';
    box.querySelectorAll('[data-changeip]').forEach(b => b.addEventListener('click', () => changeIp(inst.id, b.dataset.changeip)));
    box.querySelectorAll('[data-add6]').forEach(b => b.addEventListener('click', async () => {
      const r = await withLoading(() => api('POST', '/network/vnic/' + b.dataset.add6 + '/ipv6', { account_id: +S.accountId }));
      toast('IPv6 已附加：' + r.ip, 'ok'); loadVnics();
    }));
    box.querySelectorAll('[data-del6]').forEach(b => b.addEventListener('click', async () => {
      if (!await confirmDlg('删除 IPv6', '确定删除该 IPv6 地址吗？')) return;
      await api('DELETE', '/network/ipv6/' + b.dataset.del6 + '?account_id=' + S.accountId);
      toast('已删除', 'ok'); loadVnics();
    }));
  }
  instSel.addEventListener('change', loadVnics);
  if (S.instances.length) loadVnics();

  el.querySelector('#btn-rip-new').addEventListener('click', () => {
    formModal('创建保留 IP', field('备注名', '<input name="name" placeholder="可选">'), async form => {
      const r = await api('POST', '/network/public-ips', { account_id: +S.accountId, name: inputVal(form, 'name') });
      toast('保留 IP 已创建：' + r.ip, 'ok'); route(); return true;
    });
  });
  el.querySelectorAll('[data-assign]').forEach(b => b.addEventListener('click', () => {
    const m = formModal('绑定保留 IP',
      field('目标实例', '<select name="inst">' +
        S.instances.map(i => '<option value="' + i.id + '">' + esc(i.name) + '</option>').join('') + '</select>') +
      field('内网 IP OCID（private_ip_id）', '<input name="priv" placeholder="自动获取失败时，从 OCI 控制台粘贴">') +
      '<div class="hint">选择实例后会自动尝试获取其主网卡的内网 IP OCID；获取不到时请去 OCI 控制台 → 实例 → 附加的 VNIC → 主专用 IP 复制 OCID</div>',
      async form => {
        const priv = inputVal(form, 'priv');
        if (!priv) { toast('请填写内网 IP OCID', 'warn'); return false; }
        await api('POST', '/network/public-ips/' + b.dataset.assign + '/assign',
          { account_id: +S.accountId, private_ip_id: priv });
        toast('绑定成功', 'ok'); route(); return true;
      });
    const form = m.body.querySelector('form');
    async function autoPriv() {
      const d = await api('GET', '/instances/' + inputVal(form, 'inst') + '?account_id=' + S.accountId).catch(() => null);
      const v = d && d.vnics && d.vnics[0];
      if (v) {
        if (v.private_ip_id) { form.querySelector('[name=priv]').value = v.private_ip_id; return; }
        // 兜底：查网卡详情
        const vd = await api('GET', '/network/vnic/' + v.vnic_id + '?account_id=' + S.accountId).catch(() => null);
        if (vd && vd.private_ip_id) form.querySelector('[name=priv]').value = vd.private_ip_id;
      }
    }
    form.querySelector('[name=inst]').addEventListener('change', autoPriv);
    autoPriv();
  }));
  el.querySelectorAll('[data-unassign]').forEach(b => b.addEventListener('click', async () => {
    if (!await confirmDlg('解绑', '确定解绑该保留 IP 吗？')) return;
    await api('POST', '/network/public-ips/' + b.dataset.unassign + '/unassign', { account_id: +S.accountId });
    toast('已解绑', 'ok'); route();
  }));
  el.querySelectorAll('[data-delip]').forEach(b => b.addEventListener('click', async () => {
    if (!await confirmDlg('删除', '确定删除该公网 IP 吗？', '删除')) return;
    await api('DELETE', '/network/public-ips/' + b.dataset.delip + '?account_id=' + S.accountId);
    toast('已删除', 'ok'); route();
  }));

  const vcnSel = el.querySelector('#sg-vcn'), sgSel = el.querySelector('#sg-list');
  async function loadSg() {
    const sls = await api('GET', '/network/security-lists?account_id=' + S.accountId + '&vcn_id=' + vcnSel.value).catch(() => []);
    sgSel.innerHTML = sls.map(s => '<option value="' + s.id + '">' + esc(s.name) + '</option>').join('') || '<option value="">无</option>';
  }
  vcnSel.addEventListener('change', loadSg); loadSg();
  el.querySelector('#btn-sg-open').addEventListener('click', async () => {
    const port = parseInt(el.querySelector('#sg-port').value);
    if (!sgSel.value || !port) { toast('请选择安全组并填写端口', 'warn'); return; }
    if (!await confirmDlg('开放端口', '在安全组上对 <b>0.0.0.0/0</b> 开放 <b>' + port + '/' + el.querySelector('#sg-proto').value + '</b> 吗？')) return;
    await withLoading(() => api('POST', '/network/security-lists/' + sgSel.value + '/open-port',
      { account_id: +S.accountId, port, protocol: el.querySelector('#sg-proto').value }));
    toast('端口已开放', 'ok');
  });
};

/* ================= 硬盘 ================= */
PAGES.volumes = async function () {
  if (!needAccount()) return;
  const el = pageEl();
  const vols = await withLoading(() => api('GET', '/volumes?account_id=' + S.accountId));
  const atts = await api('GET', '/volumes/attachments?account_id=' + S.accountId).catch(() => []);
  if (!S.instances.length) await fetchInstances().catch(() => { S.instances = []; });
  const attMap = {};
  atts.forEach(a => { attMap[a.volume_id] = a; });

  el.innerHTML =
    '<div class="page-head"><h2>硬盘</h2><button class="btn primary" id="btn-vol-new">＋ 创建数据盘</button></div>' +
    '<div class="card"><div class="tbl-wrap"><table class="tbl"><thead><tr>' +
    '<th>名称</th><th>类型</th><th>大小</th><th>性能</th><th>状态</th><th>可用域</th><th>挂载</th><th>操作</th></tr></thead><tbody>' +
    vols.map(v => {
      const att = attMap[v.id];
      const inst = att && S.instances.find(x => x.id === att.instance_id);
      return '<tr><td><b>' + esc(v.name) + '</b></td>' +
        '<td>' + (v.is_boot ? '启动盘' : '数据盘') + '</td>' +
        '<td class="mono">' + v.size_gb + ' GB</td>' +
        '<td class="mono">' + esc(v.vpus_per_gb != null ? v.vpus_per_gb + ' VPUs' : '-') + '</td>' +
        '<td><span class="badge ' + (v.state === 'AVAILABLE' ? 'RUNNING' : 'STOPPED') + '">' + esc(v.state) + '</span></td>' +
        '<td class="small muted">' + esc((v.ad || '').split(':').pop()) + '</td>' +
        '<td class="small">' + (inst ? esc(inst.name) : '<span class="muted">未挂载</span>') + '</td>' +
        '<td><div class="btn-row">' +
        '<button class="btn sm" data-resize="' + v.id + '" data-boot="' + (v.is_boot ? 1 : 0) + '">扩容</button>' +
        '<button class="btn sm" data-perf="' + v.id + '" data-boot="' + (v.is_boot ? 1 : 0) + '">调性能</button>' +
        (att ? '<button class="btn sm warn" data-detach="' + att.id + '">卸载</button>'
          : '<button class="btn sm" data-attach="' + v.id + '" data-boot="' + (v.is_boot ? 1 : 0) + '">挂载</button>') +
        (v.is_boot && !att ? '<button class="btn sm primary" data-bootlaunch="' + v.id + '">从此盘开机</button>' : '') +
        '<button class="btn sm danger" data-del="' + v.id + '" data-boot="' + (v.is_boot ? 1 : 0) + '">删除</button>' +
        '</div></td></tr>';
    }).join('') + '</tbody></table></div>' + (vols.length ? '' : '<div class="empty">暂无硬盘</div>') + '</div>';

  el.querySelector('#btn-vol-new').addEventListener('click', async () => {
    const ads = await api('GET', '/compute/ads?account_id=' + S.accountId).catch(() => []);
    formModal('创建数据盘',
      field('可用域', '<select name="ad">' + ads.map(a => '<option>' + esc(a) + '</option>').join('') + '</select>') +
      '<div class="frow">' + field('大小 GB', '<input name="size_gb" type="number" value="50">') +
      field('性能 VPUs', '<select name="vpus"><option value="10">10 均衡</option><option value="20">20 高性能</option><option value="0">0 低成本</option></select>') + '</div>' +
      field('名称', '<input name="name" placeholder="可选">') +
      field('Compartment OCID', '<input name="compartment_id" value="' + esc(resolveCompartment('')) + '" placeholder="留空=租户根">'),
      async form => {
        await api('POST', '/volumes', { account_id: +S.accountId,
          compartment_id: resolveCompartment(inputVal(form, 'compartment_id')),
          ad: inputVal(form, 'ad'), size_gb: parseInt(inputVal(form, 'size_gb')) || 50,
          name: inputVal(form, 'name'), vpus_per_gb: parseInt(inputVal(form, 'vpus')) });
        toast('数据盘已创建', 'ok'); route(); return true;
      });
  });
  el.querySelectorAll('[data-resize]').forEach(b => b.addEventListener('click', () => {
    formModal('扩容（只能调大）', field('新大小 GB', '<input name="size" type="number">'), async form => {
      const size = parseInt(inputVal(form, 'size'));
      if (!size) { toast('请输入大小', 'warn'); return false; }
      await api('PUT', '/volumes/' + b.dataset.resize + '/resize',
        { account_id: +S.accountId, size_gb: size, is_boot: b.dataset.boot === '1' });
      toast('扩容已提交（系统内还需 resize2fs / growpart）', 'ok'); route(); return true;
    });
  }));
  el.querySelectorAll('[data-perf]').forEach(b => b.addEventListener('click', () => {
    formModal('调整性能', field('VPUs 每 GB', '<select name="v"><option value="10">10 均衡</option><option value="20">20 高性能</option><option value="0">0 低成本</option></select>'), async form => {
      await api('PUT', '/volumes/' + b.dataset.perf + '/performance',
        { account_id: +S.accountId, vpus_per_gb: parseInt(inputVal(form, 'v')) || 10, is_boot: b.dataset.boot === '1' });
      toast('性能已调整', 'ok'); route(); return true;
    });
  }));
  el.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', async () => {
    if (!await confirmDlg('删除硬盘', '确定删除这块盘吗？数据不可恢复。', '删除')) return;
    await api('DELETE', '/volumes/' + b.dataset.del + '?account_id=' + S.accountId + '&is_boot=' + (b.dataset.boot === '1'));
    toast('已删除', 'ok'); route();
  }));
  el.querySelectorAll('[data-attach]').forEach(b => b.addEventListener('click', () => {
    formModal('挂载到实例', field('目标实例（需先关机再挂启动盘）',
      '<select name="inst">' + S.instances.map(i => '<option value="' + i.id + '">' + esc(i.name) + '（' + i.state + '）</option>').join('') + '</select>'),
      async form => {
        await api('POST', '/volumes/' + b.dataset.attach + '/attach',
          { account_id: +S.accountId, instance_id: inputVal(form, 'inst'), is_boot: b.dataset.boot === '1' });
        toast('挂载指令已发送', 'ok'); route(); return true;
      });
  }));
  el.querySelectorAll('[data-detach]').forEach(b => b.addEventListener('click', async () => {
    if (!await confirmDlg('卸载', '确定从实例上卸载这块盘吗？', '卸载')) return;
    await api('POST', '/volumes/attachments/' + b.dataset.detach + '/detach', { account_id: +S.accountId });
    toast('卸载指令已发送', 'ok'); route();
  }));
  el.querySelectorAll('[data-bootlaunch]').forEach(b => b.addEventListener('click', () => openLaunchModal(b.dataset.bootlaunch)));
};

/* ================= 对象存储 ================= */
let OS = { bucket: '', prefix: '' };
PAGES.objectstore = async function () {
  if (!needAccount()) return;
  const el = pageEl();
  const buckets = await withLoading(() => api('GET', '/objectstore/buckets?account_id=' + S.accountId));

  el.innerHTML =
    '<div class="page-head"><h2>对象存储</h2><button class="btn primary" id="btn-bk-new">＋ 创建 Bucket</button></div>' +
    '<div class="card"><div class="tbl-wrap"><table class="tbl"><thead><tr><th>Bucket</th><th>创建时间</th><th>操作</th></tr></thead><tbody>' +
    buckets.map(b => '<tr><td><b>' + esc(b.name) + '</b></td><td class="small">' + fmtTime(b.time_created) + '</td>' +
      '<td><div class="btn-row"><button class="btn sm primary" data-open="' + esc(b.name) + '">打开</button>' +
      '<button class="btn sm danger" data-delbk="' + esc(b.name) + '">删除</button></div></td></tr>').join('') +
    '</tbody></table></div>' + (buckets.length ? '' : '<div class="empty">暂无 Bucket</div>') + '</div>' +
    '<div id="os-objs"></div>';

  el.querySelector('#btn-bk-new').addEventListener('click', () => {
    formModal('创建 Bucket',
      field('名称', '<input name="name" placeholder="小写字母/数字/中划线">') +
      field('Compartment OCID', '<input name="compartment_id" value="' + esc(resolveCompartment('')) + '" placeholder="留空=租户根">'),
      async form => {
        await api('POST', '/objectstore/buckets', { account_id: +S.accountId,
          compartment_id: resolveCompartment(inputVal(form, 'compartment_id')), name: inputVal(form, 'name') });
        toast('Bucket 已创建', 'ok'); route(); return true;
      });
  });
  el.querySelectorAll('[data-open]').forEach(b => b.addEventListener('click', () => { OS.bucket = b.dataset.open; OS.prefix = ''; renderObjects(el); }));
  el.querySelectorAll('[data-delbk]').forEach(b => b.addEventListener('click', async () => {
    if (!await confirmDlg('删除 Bucket', 'Bucket 必须为空才能删除。确定删除 <b>' + esc(b.dataset.delbk) + '</b> 吗？', '删除')) return;
    await api('DELETE', '/objectstore/buckets/' + encodeURIComponent(b.dataset.delbk) + '?account_id=' + S.accountId);
    toast('已删除', 'ok'); route();
  }));
  if (OS.bucket && buckets.find(b => b.name === OS.bucket)) renderObjects(el);
};
async function renderObjects(el) {
  const box = el.querySelector('#os-objs');
  const objs = await withLoading(() => api('GET', '/objectstore/objects?account_id=' + S.accountId +
    '&bucket=' + encodeURIComponent(OS.bucket) + '&prefix=' + encodeURIComponent(OS.prefix)));
  const crumbs = OS.prefix.split('/').filter(Boolean);
  box.innerHTML = '<div class="card"><div class="page-head"><h3>📁 ' + esc(OS.bucket) + '</h3>' +
    '<div class="toolbar"><label class="btn sm primary" style="cursor:pointer">⬆ 上传<input type="file" id="os-file" class="hidden"></label>' +
    '<button class="btn sm" id="os-close">关闭</button></div></div>' +
    '<div class="sftp-path"><span class="crumb" data-p="">根</span>' +
    crumbs.map((c, i) => ' / <span class="crumb" data-p="' + esc(crumbs.slice(0, i + 1).join('/') + '/') + '">' + esc(c) + '</span>').join('') +
    '</div><div class="tbl-wrap"><table class="tbl"><thead><tr><th>对象</th><th>大小</th><th>修改时间</th><th>操作</th></tr></thead><tbody>' +
    objs.map(o => {
      return '<tr><td class="mono small">' + esc(o.name.replace(OS.prefix, '')) + '</td>' +
        '<td class="mono small">' + fmtSize(o.size) + '</td><td class="small">' + fmtTime(o.time_modified) + '</td>' +
        '<td><div class="btn-row"><button class="btn sm" data-dl="' + esc(o.name) + '">下载</button>' +
        '<button class="btn sm danger" data-delobj="' + esc(o.name) + '">删除</button></div></td></tr>';
    }).join('') + '</tbody></table></div>' + (objs.length ? '' : '<div class="empty">空</div>') + '</div>';

  box.querySelector('#os-close').addEventListener('click', () => { OS.bucket = ''; OS.prefix = ''; box.innerHTML = ''; });
  box.querySelectorAll('.crumb').forEach(c => c.addEventListener('click', () => { OS.prefix = c.dataset.p; renderObjects(el); }));
  box.querySelector('#os-file').addEventListener('change', async e => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData();
    fd.append('account_id', S.accountId); fd.append('bucket', OS.bucket);
    fd.append('prefix', OS.prefix); fd.append('file', f);
    await withLoading(() => apiRaw('POST', '/objectstore/upload', fd));
    toast('上传成功', 'ok'); renderObjects(el);
  });
  box.querySelectorAll('[data-dl]').forEach(b => b.addEventListener('click', async () => {
    const r = await withLoading(() => apiRaw('GET', '/objectstore/download?account_id=' + S.accountId +
      '&bucket=' + encodeURIComponent(OS.bucket) + '&name=' + encodeURIComponent(b.dataset.dl)));
    const blob = await r.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = b.dataset.dl.split('/').pop();
    a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }));
  box.querySelectorAll('[data-delobj]').forEach(b => b.addEventListener('click', async () => {
    if (!await confirmDlg('删除对象', '确定删除 <b>' + esc(b.dataset.delobj) + '</b> 吗？', '删除')) return;
    await api('DELETE', '/objectstore/objects?account_id=' + S.accountId +
      '&bucket=' + encodeURIComponent(OS.bucket) + '&name=' + encodeURIComponent(b.dataset.delobj));
    toast('已删除', 'ok'); renderObjects(el);
  }));
}
function fmtSize(b) {
  if (b == null) return '-';
  b = +b; if (b < 1024) return b + ' B';
  if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
  if (b < 1073741824) return (b / 1048576).toFixed(1) + ' MB';
  return (b / 1073741824).toFixed(2) + ' GB';
}

/* ================= 串行控制台 ================= */
PAGES.console = async function () {
  if (!needAccount()) return;
  const el = pageEl();
  if (!S.instances.length) await fetchInstances().catch(() => { S.instances = []; });
  el.innerHTML =
    '<div class="page-head"><h2>串行控制台</h2></div>' +
    '<div class="card"><h3>SSH 连不上时的救命通道</h3>' +
    '<div class="toolbar" style="margin-bottom:12px"><select id="cc-inst" style="min-width:260px">' +
    S.instances.map(i => '<option value="' + i.id + '">' + esc(i.name) + '（' + i.state + '）</option>').join('') +
    '</select><button class="btn primary" id="btn-cc-vnc">🖥 图形控制台 (VNC)</button>' +
    '<button class="btn" id="btn-cc-new">创建串行连接 (SSH)</button>' +
    '<button class="btn" id="btn-cc-hist">查看控制台历史</button></div>' +
    '<div id="cc-out"></div></div>' +
    '<div class="hint">图形控制台会在新窗口直接打开实例的 VNC 画面(Windows 实例救机首选, 顶部有 Ctrl+Alt+Del 按钮)；串行连接则生成 SSH 命令在本机执行, 进串口后可挂载 Netboot.xyz（<code>https://netboot.xyz</code>）引导救援系统。</div>';

  el.querySelector('#btn-cc-vnc').addEventListener('click', async () => {
    const instId = el.querySelector('#cc-inst').value;
    // 先占住新窗口(异步后再 open 会被浏览器弹窗拦截), 拿到会话再跳转
    const w = window.open('', '_blank', 'width=1120,height=760');
    if (w) w.document.write('<title>图形控制台 · OCI Pilot</title>' +
      '<p style="font-family:sans-serif;padding:24px;color:#333">正在创建控制台连接并建立隧道…</p>');
    try {
      const r = await withLoading(() => api('POST', '/console/vnc-sessions', { account_id: +S.accountId, instance_id: instId }));
      const url = '/vnc.html?s=' + encodeURIComponent(r.session_id);
      if (w) w.location = url;
      else window.open(url, '_blank', 'width=1120,height=760');
      toast('图形控制台已在新窗口打开', 'ok');
    } catch (e) {
      if (w) w.close();
      toast(e.message || '创建图形控制台失败', 'err');
      el.querySelector('#cc-out').innerHTML =
        '<div class="card" style="background:var(--bg2)"><h3>图形控制台创建失败</h3>' +
        '<p class="small" style="color:var(--red,#d70015)">' + esc(e.message || '未知错误') + '</p></div>';
    }
  });
  el.querySelector('#btn-cc-new').addEventListener('click', async () => {
    const instId = el.querySelector('#cc-inst').value;
    const box = el.querySelector('#cc-out');
    let r;
    try {
      r = await withLoading(() => api('POST', '/console/connections', { account_id: +S.accountId, instance_id: instId }));
    } catch (e) {
      box.innerHTML = '<div class="card" style="background:var(--bg2)"><h3>创建失败</h3>' +
        '<p class="small" style="color:var(--red,#d70015)">' + esc(e.message || '未知错误') + '</p></div>';
      return;
    }
    box.innerHTML = '<div class="card" style="background:var(--bg2)"><h3>连接已创建</h3>' +
      '<p class="muted small">1. 下载私钥到本机；2. 在本机终端执行下面的 SSH 命令（需能访问公网）</p>' +
      '<pre class="code" id="cc-cmd">' + esc(r.ssh_command) + '</pre>' +
      '<div class="btn-row"><button class="btn sm" id="cc-copy">复制命令</button>' +
      '<button class="btn sm primary" id="cc-key">⬇ 下载私钥</button>' +
      '<button class="btn sm danger" id="cc-del">删除连接</button></div></div>';
    box.querySelector('#cc-copy').addEventListener('click', () => copyText(r.ssh_command));
    box.querySelector('#cc-key').addEventListener('click', async () => {
      const resp = await withLoading(() => apiRaw('GET', '/console/key/' + encodeURIComponent(r.key_file)));
      const blob = await resp.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = r.key_file; a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    });
    box.querySelector('#cc-del').addEventListener('click', async () => {
      if (!await confirmDlg('删除连接', '确定删除该串行控制台连接吗？', '删除')) return;
      await api('DELETE', '/console/connections/' + r.connection_id + '?account_id=' + S.accountId);
      toast('已删除', 'ok'); box.innerHTML = '';
    });
  });
  el.querySelector('#btn-cc-hist').addEventListener('click', async () => {
    const instId = el.querySelector('#cc-inst').value;
    const box = el.querySelector('#cc-out');
    box.innerHTML = '<div class="empty">正在截取控制台输出…</div>';
    let h;
    try {
      h = await withLoading(() => api('POST', '/console/history', { account_id: +S.accountId, instance_id: instId }));
    } catch (e) {
      box.innerHTML = '<div class="card" style="background:var(--bg2)"><h3>截取失败</h3>' +
        '<p class="small" style="color:var(--red,#d70015)">' + esc(e.message || '未知错误') + '</p></div>';
      return;
    }
    async function poll() {
      const d = await api('GET', '/console/history/' + h.history_id + '?account_id=' + S.accountId);
      box.innerHTML = '<div class="card" style="background:var(--bg2)"><h3>控制台历史 <span class="muted small">' + esc(d.state) + '</span></h3>' +
        '<pre class="code">' + esc(d.content || '(空)') + '</pre>' +
        (d.state !== 'SUCCEEDED' ? '<button class="btn sm" id="cc-repoll">↻ 刷新</button>' : '') + '</div>';
      const rp = box.querySelector('#cc-repoll');
      if (rp) rp.addEventListener('click', poll);
    }
    poll();
  });
};

/* ================= 监控告警 ================= */

/* ================= 服务器运维 ================= */
const OPS = { poll: null, taskId: null };

function opsStopPoll() { if (OPS.poll) { clearInterval(OPS.poll); OPS.poll = null; } }

PAGES.ops = async function () {
  opsStopPoll();
  const el = pageEl();
  const [hosts, actions, keys] = await Promise.all([
    api('GET', '/ssh/hosts'), api('GET', '/ops/actions'), api('GET', '/ssh/keys')]);
  OPS.keys = keys;
  OPS.actionsCache = actions;
  el.innerHTML =
    '<div class="page-head"><h2>服务器运维</h2></div>' +
    '<div class="card"><div class="field"><label>目标主机</label>' +
    '<select id="ops-host">' + hosts.map(h =>
      '<option value="' + h.id + '">' + esc(h.name) + '（' + esc(h.username) + '@' +
      esc(h.hostname) + ':' + h.port + '）</option>').join('') + '</select></div>' +
    '<p class="muted">动作均为固定模板在目标机上执行，全程日志可见。主机需先在「SSH 终端」页配置好登录凭证。重装系统在「实例」页（终止重开）；这里是系统内的常用运维。</p></div>' +
    '<div class="ops-grid">' + actions.map(a =>
      '<div class="ops-card" data-op="' + a.id + '"><h4>' + esc(a.name) +
      (a.danger ? ' <span class="chip red">谨慎</span>' : '') + '</h4>' +
      '<p class="muted">' + esc(a.desc) + '</p>' +
      '<button class="btn sm primary">执行</button></div>').join('') + '</div>' +
    '<div class="card hidden" id="ops-log-card"><h3>执行日志 <span id="ops-log-status"></span></h3>' +
    '<pre class="ops-log" id="ops-log"></pre></div>' +
    '<div class="card"><h3>最近任务</h3><div id="ops-history"></div></div>';

  if (!hosts.length) toast('还没有主机，请先在 SSH 终端页添加或从 OCI 同步', 'warn');
  el.querySelectorAll('[data-op]').forEach(c => c.addEventListener('click', () => {
    if (!el.querySelector('#ops-host').value) { toast('请先选择主机', 'warn'); return; }
    openOpsModal(actions.find(a => a.id === c.dataset.op),
                 el.querySelector('#ops-host').value);
  }));
  loadOpsHistory();
};

async function loadOpsHistory() {
  const box = document.querySelector('#ops-history');
  if (!box) return;
  const tasks = await api('GET', '/ops/tasks?limit=20');
  const names = {}; (OPS.actionsCache || []).forEach(a => names[a.id] = a.name);
  box.innerHTML = tasks.length ? '<table><thead><tr><th>时间</th><th>主机</th><th>动作</th><th>状态</th><th></th></tr></thead><tbody>' +
    tasks.map(t => '<tr><td>' + new Date(t.created_at).toLocaleString() + '</td><td>' +
      esc(t.host_name) + '</td><td>' + esc(names[t.action] || t.action) + '</td><td>' +
      (t.status === 'done' ? '<span class="chip green">成功</span>' :
       t.status === 'failed' ? '<span class="chip red">失败</span>' :
       '<span class="chip">运行中</span>') +
      '</td><td><button class="btn sm ghost" data-log="' + t.id + '">日志</button></td></tr>').join('') +
    '</tbody></table>' : '<p class="muted">暂无记录</p>';
  box.querySelectorAll('[data-log]').forEach(b => b.addEventListener('click', () => watchOpsTask(+b.dataset.log)));
}

function opsFieldHtml(f) {
  const id = 'opf-' + f.key;
  if (f.type === 'int') return '<div class="field" data-field="' + f.key + '"><label>' + esc(f.label) + '</label>' +
    '<input type="number" id="' + id + '" value="' + (f.default ?? '') + '"' +
    (f.min !== undefined ? ' min="' + f.min + '"' : '') + (f.max !== undefined ? ' max="' + f.max + '"' : '') + '></div>';
  if (f.type === 'bool') return '<div class="field" data-field="' + f.key + '"><label>' +
    '<input type="checkbox" id="' + id + '"' + (f.default ? ' checked' : '') + '> ' + esc(f.label) + '</label></div>';
  if (f.type === 'select') return '<div class="field" data-field="' + f.key + '"><label>' + esc(f.label) + '</label>' +
    '<select id="' + id + '">' + (f.options || []).map(o =>
      '<option value="' + esc(o.v) + '"' + (o.v === f.default ? ' selected' : '') + '>' + esc(o.t) + '</option>').join('') + '</select></div>';
  if (f.type === 'key_id') return '<div class="field" data-field="' + f.key + '"><label>' + esc(f.label) + '</label>' +
    '<select id="' + id + '">' + (OPS.keys || []).map(k =>
      '<option value="' + k.id + '">' + esc(k.name) + '</option>').join('') + '</select>' +
    ((OPS.keys || []).length ? '' : '<p class="muted">（暂无已保存密钥，请先到 SSH 终端页添加）</p>') + '</div>';
  if (f.type === 'textarea') return '<div class="field" data-field="' + f.key + '"><label>' + esc(f.label) + '</label>' +
    '<textarea id="' + id + '" rows="3" placeholder="' + esc(f.help || '') + '"></textarea></div>';
  return '<div class="field" data-field="' + f.key + '"><label>' + esc(f.label) + '</label>' +
    '<input type="text" id="' + id + '" value="' + esc(f.default || '') + '"></div>';
}

function openOpsModal(action, hostId) {
  openModal('执行：' + action.name,
    '<p class="muted">' + esc(action.desc) + '</p>' +
    (action.params || []).map(opsFieldHtml).join('') +
    '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px">' +
    '<button class="btn" onclick="closeModal()">取消</button>' +
    '<button class="btn primary" id="ops-run">开始执行</button></div>');

  const srcSel = document.querySelector('#opf-source');
  const syncSource = () => {
    if (!srcSel) return;
    const stored = srcSel.value === 'stored';
    const kRow = document.querySelector('[data-field="key_id"]');
    const pRow = document.querySelector('[data-field="public_key"]');
    const dBox = document.querySelector('#opf-disable_password');
    if (kRow) kRow.classList.toggle('hidden', !stored);
    if (pRow) pRow.classList.toggle('hidden', stored);
    if (dBox) { dBox.disabled = !stored; if (!stored) dBox.checked = false; }
  };
  if (srcSel) { srcSel.addEventListener('change', syncSource); syncSource(); }

  document.querySelector('#ops-run').addEventListener('click', async () => {
    const params = {};
    (action.params || []).forEach(f => {
      const n = document.querySelector('#opf-' + f.key);
      if (!n) return;
      if (f.type === 'int' || f.type === 'key_id') params[f.key] = n.value === '' ? null : +n.value;
      else if (f.type === 'bool') params[f.key] = n.checked;
      else params[f.key] = n.value;
    });
    if (action.danger && !await confirmDlg(action.name,
      '将在所选主机上执行「<b>' + esc(action.name) + '</b>」，请确认参数无误。继续吗？', '确认执行')) return;
    try {
      const t = await withLoading(() => api('POST', '/ops/tasks',
        { host_id: +hostId, action: action.id, params }));
      closeModal();
      toast('任务已启动 #' + t.id, 'ok');
      watchOpsTask(t.id);
      loadOpsHistory();
    } catch (e) { toast(e.message, 'error'); }
  });
}

function watchOpsTask(taskId) {
  opsStopPoll();
  OPS.taskId = taskId;
  const card = document.querySelector('#ops-log-card');
  const pre = document.querySelector('#ops-log');
  const st = document.querySelector('#ops-log-status');
  if (!card || !pre) return;
  card.classList.remove('hidden');
  card.scrollIntoView({ behavior: 'smooth', block: 'start' });
  const tick = async () => {
    if (!document.contains(pre)) { opsStopPoll(); return; }
    try {
      const t = await api('GET', '/ops/tasks/' + taskId);
      pre.textContent = t.log || '（等待输出…）';
      pre.scrollTop = pre.scrollHeight;
      st.innerHTML = t.status === 'done' ? '<span class="chip green">成功</span>' :
        t.status === 'failed' ? '<span class="chip red">失败</span>' : '<span class="chip">运行中…</span>';
      if (t.status !== 'running') { opsStopPoll(); loadOpsHistory(); }
    } catch (e) { opsStopPoll(); }
  };
  tick();
  OPS.poll = setInterval(tick, 1500);
}

PAGES.monitors = async function () {
  if (!needAccount()) return;
  const el = pageEl();
  if (!S.instances.length) await fetchInstances().catch(() => { S.instances = []; });
  const [watch, traffic, daily] = await withLoading(() => Promise.all([
    api('GET', '/monitors/watch').catch(() => []),
    api('GET', '/monitors/traffic').catch(() => []),
    api('GET', '/monitors/daily').catch(() => ({ enabled: false })),
  ]));

  el.innerHTML =
    '<div class="page-head"><h2>监控告警</h2></div>' +
    '<div class="card"><div class="page-head"><h3>🛡️ 实例保活（停机通知 / 自动拉起）</h3>' +
    '<button class="btn sm primary" id="w-add">＋ 添加</button></div>' +
    '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>实例</th><th>账号</th><th>自动重启</th><th>启用</th><th>操作</th></tr></thead><tbody>' +
    watch.map(w => '<tr><td><b>' + esc(w.display_name || w.instance_id) + '</b><div class="muted small mono">' + esc(w.instance_id).slice(-16) + '</div></td>' +
      '<td>#' + w.account_id + '</td>' +
      '<td><button class="btn sm" data-wauto="' + w.id + '"><span class="badge ' + (w.auto_restart ? 'on' : 'off') + '">' + (w.auto_restart ? '开' : '关') + '</span></button></td>' +
      '<td><button class="btn sm" data-wen="' + w.id + '"><span class="badge ' + (w.enabled ? 'on' : 'off') + '">' + (w.enabled ? '开' : '关') + '</span></button></td>' +
      '<td><button class="btn sm danger" data-wdel="' + w.id + '">删除</button></td></tr>').join('') +
    '</tbody></table></div>' + (watch.length ? '' : '<div class="empty">暂无保活任务</div>') + '</div>' +
    '<div class="card"><div class="page-head"><h3>📊 流量守护（超阈值通知 / 自动关机）</h3>' +
    '<button class="btn sm primary" id="t-add">＋ 添加</button></div>' +
    '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>实例</th><th>本月已用 / 阈值</th><th>动作</th><th>启用</th><th>操作</th></tr></thead><tbody>' +
    traffic.map(t => '<tr><td><b>' + esc(t.display_name || t.instance_id) + '</b></td>' +
      '<td class="mono">' + fmtGB(t.used_gb) + ' / ' + fmtGB(t.threshold_gb) + ' GB</td>' +
      '<td>' + (t.action === 'stop' ? '自动关机' : '仅通知') + '</td>' +
      '<td><button class="btn sm" data-ten="' + t.id + '"><span class="badge ' + (t.enabled ? 'on' : 'off') + '">' + (t.enabled ? '开' : '关') + '</span></button></td>' +
      '<td><div class="btn-row"><button class="btn sm" data-tuse="' + t.id + '" data-acc="' + t.account_id + '">用量</button>' +
      '<button class="btn sm danger" data-tdel="' + t.id + '">删除</button></div></td></tr>').join('') +
    '</tbody></table></div>' + (traffic.length ? '' : '<div class="empty">暂无流量守护</div>') +
    '<div class="hint" style="margin-top:8px">免费账号每月 10TB 出站流量；建议阈值设为 9000 GB</div></div>' +
    '<div class="card"><h3>📅 每日报告</h3><p class="muted small">每天推送实例状态 / 流量 / 抢机任务汇总到 Telegram</p>' +
    '<button class="btn ' + (daily.enabled ? '' : 'primary') + '" id="daily-toggle">' +
    (daily.enabled ? '已开启，点击关闭' : '开启每日报告') + '</button></div>';

  el.querySelector('#w-add').addEventListener('click', () => {
    formModal('添加实例保活',
      field('实例', '<select name="inst">' + S.instances.map(i =>
        '<option value="' + i.id + '" data-n="' + esc(i.name) + '">' + esc(i.name) + '（' + i.state + '）</option>').join('') + '</select>') +
      '<label class="checkbox-row"><input type="checkbox" name="auto"> 意外停机时自动开机拉起</label>',
      async form => {
        const sel = form.querySelector('[name=inst]');
        await api('POST', '/monitors/watch', { account_id: +S.accountId,
          instance_id: inputVal(form, 'inst'),
          display_name: sel.options[sel.selectedIndex].dataset.n,
          auto_restart: form.querySelector('[name=auto]').checked });
        toast('已添加', 'ok'); route(); return true;
      });
  });
  el.querySelectorAll('[data-wauto]').forEach(b => b.addEventListener('click', async () => {
    const w = watch.find(x => String(x.id) === b.dataset.wauto);
    await api('PUT', '/monitors/watch/' + w.id, { auto_restart: !w.auto_restart });
    route();
  }));
  el.querySelectorAll('[data-wen]').forEach(b => b.addEventListener('click', async () => {
    const w = watch.find(x => String(x.id) === b.dataset.wen);
    await api('PUT', '/monitors/watch/' + w.id, { enabled: !w.enabled });
    route();
  }));
  el.querySelectorAll('[data-wdel]').forEach(b => b.addEventListener('click', async () => {
    if (!await confirmDlg('删除', '确定删除该保活任务吗？', '删除')) return;
    await api('DELETE', '/monitors/watch/' + b.dataset.wdel);
    toast('已删除', 'ok'); route();
  }));

  el.querySelector('#t-add').addEventListener('click', async () => {
    const m = formModal('添加流量守护',
      field('实例', '<select name="inst">' + S.instances.map(i =>
        '<option value="' + i.id + '" data-n="' + esc(i.name) + '">' + esc(i.name) + '</option>').join('') + '</select>') +
      field('网卡', '<select name="vnic"><option>选择实例后加载</option></select>') +
      field('月阈值 GB', '<input name="th" type="number" value="9000">') +
      field('超阈值动作', '<select name="act"><option value="notify">仅 Telegram 通知</option><option value="stop">通知并自动关机</option></select>'),
      async form => {
        const sel = form.querySelector('[name=inst]');
        await api('POST', '/monitors/traffic', { account_id: +S.accountId,
          instance_id: inputVal(form, 'inst'),
          display_name: sel.options[sel.selectedIndex].dataset.n,
          vnic_id: inputVal(form, 'vnic'),
          threshold_gb: parseFloat(inputVal(form, 'th')) || 9000,
          action: inputVal(form, 'act') });
        toast('已添加', 'ok'); route(); return true;
      });
    const form = m.body.querySelector('form');
    async function loadV() {
      const d = await api('GET', '/instances/' + inputVal(form, 'inst') + '?account_id=' + S.accountId);
      form.querySelector('[name=vnic]').innerHTML = (d.vnics || []).map(v =>
        '<option value="' + v.vnic_id + '">' + esc(v.public_ip || v.private_ip || v.vnic_id) + '</option>').join('');
    }
    form.querySelector('[name=inst]').addEventListener('change', loadV);
    loadV();
  });
  el.querySelectorAll('[data-tuse]').forEach(b => b.addEventListener('click', async () => {
    const u = await withLoading(() => api('GET', '/monitors/traffic/' + b.dataset.tuse + '/usage?account_id=' + b.dataset.acc));
    openModal('本月流量', '<dl class="kv"><dt>已用</dt><dd>' + fmtGB(u.used_gb) + ' GB</dd>' +
      '<dt>阈值</dt><dd>' + fmtGB(u.threshold_gb) + ' GB</dd><dt>月份</dt><dd>' + esc(u.month) + '</dd></dl>' +
      '<div class="progress"><i style="width:' + Math.min(100, (u.used_gb / u.threshold_gb) * 100) + '%"></i></div>');
  }));
  el.querySelectorAll('[data-ten]').forEach(b => b.addEventListener('click', async () => {
    const t = traffic.find(x => String(x.id) === b.dataset.ten);
    await api('PUT', '/monitors/traffic/' + t.id, { enabled: !t.enabled });
    route();
  }));
  el.querySelectorAll('[data-tdel]').forEach(b => b.addEventListener('click', async () => {
    if (!await confirmDlg('删除', '确定删除该流量守护吗？', '删除')) return;
    await api('DELETE', '/monitors/traffic/' + b.dataset.tdel);
    toast('已删除', 'ok'); route();
  }));
  el.querySelector('#daily-toggle').addEventListener('click', async () => {
    await api('PUT', '/monitors/daily', { enabled: !daily.enabled });
    toast(daily.enabled ? '每日报告已关闭' : '每日报告已开启', 'ok'); route();
  });
};

/* ================= SSH 终端 ================= */
let SSHS = { hosts: [], keys: [], sftpPath: '/', sftpSid: null };
PAGES.ssh = async function () {
  const el = pageEl();
  el.innerHTML =
    '<div class="page-head"><h2>SSH 终端</h2><div class="toolbar">' +
    '<button class="btn sm" id="ssh-sync">⇅ 从 OCI 同步主机</button>' +
    '<button class="btn sm" id="ssh-host-new">＋ 主机</button>' +
    '<button class="btn sm" id="ssh-key-mgr">🔑 密钥管理</button></div></div>' +
    '<div class="ssh-layout">' +
    '<div class="ssh-side"><h4>主机列表</h4><div id="ssh-hosts"></div></div>' +
    '<div class="ssh-main"><div class="term-tabs" id="term-tabs">' +
    '<button class="btn sm ghost" id="btn-sftp" style="margin-left:auto">📁 SFTP</button></div>' +
    '<div class="term-body"><div id="term-panes" style="flex:1;display:flex;min-width:0"></div>' +
    '<div class="sftp-drawer" id="sftp-drawer">' +
    '<div class="sftp-head"><b>📁 SFTP</b><button class="btn sm ghost" id="sftp-x">✕</button></div>' +
    '<div class="sftp-path" id="sftp-path"></div><div class="sftp-list" id="sftp-list"></div>' +
    '<div class="sftp-foot"><label class="btn sm" style="cursor:pointer">⬆ 上传<input type="file" id="sftp-file" class="hidden" multiple></label>' +
    '<button class="btn sm" id="sftp-mkdir">新建目录</button>' +
    '<button class="btn sm" id="sftp-reload">↻</button></div>' +
    '</div></div></div></div>';

  el.querySelector('#btn-sftp').addEventListener('click', () => {
    const t = S.termTabs[S.activeTab];
    if (!t) { toast('请先连接一台主机', 'warn'); return; }
    SSHS.sftpSid = t.sessionId;
    el.querySelector('#sftp-drawer').classList.toggle('open');
    if (el.querySelector('#sftp-drawer').classList.contains('open')) { SSHS.sftpPath = '/root'; sftpGo(); t.fit && setTimeout(() => t.fit.fit(), 50); }
  });
  el.querySelector('#sftp-x').addEventListener('click', () => el.querySelector('#sftp-drawer').classList.remove('open'));
  el.querySelector('#sftp-reload').addEventListener('click', sftpGo);
  el.querySelector('#sftp-mkdir').addEventListener('click', async () => {
    const n = prompt('目录名：'); if (!n) return;
    await api('POST', '/ssh/sftp/mkdir', { session_id: SSHS.sftpSid, path: joinP(SSHS.sftpPath, n) });
    sftpGo();
  });
  el.querySelector('#sftp-file').addEventListener('change', async e => {
    for (const f of e.target.files) {
      const fd = new FormData();
      fd.append('session_id', SSHS.sftpSid); fd.append('path', SSHS.sftpPath); fd.append('file', f);
      await withLoading(() => apiRaw('POST', '/ssh/sftp/upload', fd));
    }
    toast('上传完成', 'ok'); e.target.value = ''; sftpGo();
  });

  el.querySelector('#ssh-host-new').addEventListener('click', () => hostModal());
  el.querySelector('#ssh-key-mgr').addEventListener('click', keyMgrModal);
  el.querySelector('#ssh-sync').addEventListener('click', () => {
    formModal('从 OCI 同步主机', '<p class="muted small">将当前 Profile 下所有实例导入为 SSH 主机（root 用户）</p>' +
      field('OCI 账号', '<select name="acc">' + S.accounts.map(a =>
        '<option value="' + a.id + '"' + (String(a.id) === S.accountId ? ' selected' : '') + '>' + esc(a.name) + '</option>').join('') + '</select>'),
      async form => {
        const r = await withLoading(() => api('POST', '/ssh/sync-oci', { account_id: +inputVal(form, 'acc') }));
        toast('已同步 ' + (r.synced != null ? r.synced : '') + ' 台主机', 'ok');
        loadHosts(); return true;
      });
  });

  await loadKeys();
  await loadHosts();
  if (!window._sshResizeBound) {
    window._sshResizeBound = true;
    window.addEventListener('resize', () => {
      const t = S.termTabs[S.activeTab];
      if (t && t.fit) { t.fit.fit(); sendResize(t); }
    });
  }
};
async function loadHosts() {
  SSHS.hosts = await api('GET', '/ssh/hosts').catch(() => []);
  const box = $('ssh-hosts'); if (!box) return;
  box.innerHTML = SSHS.hosts.map(h =>
    '<div class="host-item"><span class="nm" title="' + esc(h.hostname) + '">🖥️ ' + esc(h.name) + '</span>' +
    '<span class="btn-row"><button class="btn sm primary" data-conn="' + h.id + '">连接</button>' +
    '<button class="btn sm ghost" data-edit="' + h.id + '">✎</button>' +
    '<button class="btn sm danger" data-delh="' + h.id + '">✕</button></span></div>').join('')
    || '<div class="empty">暂无主机</div>';
  box.querySelectorAll('[data-conn]').forEach(b => b.addEventListener('click', () =>
    sshConnect(SSHS.hosts.find(h => String(h.id) === b.dataset.conn))));
  box.querySelectorAll('[data-delh]').forEach(b => b.addEventListener('click', async () => {
    if (!await confirmDlg('删除主机', '确定删除该主机配置吗？', '删除')) return;
    await api('DELETE', '/ssh/hosts/' + b.dataset.delh);
    toast('已删除', 'ok'); loadHosts();
  }));
  box.querySelectorAll('[data-edit]').forEach(b => b.addEventListener('click', () =>
    hostModal(SSHS.hosts.find(h => String(h.id) === b.dataset.edit))));
}
function hostModal(h) {
  h = h || {};
  formModal(h.id ? '编辑主机' : '添加主机',
    field('名称', '<input name="name" value="' + esc(h.name || '') + '">') +
    '<div class="frow">' + field('主机/IP', '<input name="hostname" value="' + esc(h.hostname || '') + '">') +
    field('端口', '<input name="port" type="number" value="' + esc(h.port || 22) + '">') + '</div>' +
    '<div class="frow">' + field('用户名', '<input name="username" value="' + esc(h.username || 'root') + '">') +
    field('认证方式', '<select name="auth_type"><option value="password"' + (h.auth_type !== 'key' ? ' selected' : '') + '>密码</option><option value="key"' + (h.auth_type === 'key' ? ' selected' : '') + '>密钥</option></select>') + '</div>' +
    field('密码（密码认证）', '<input name="password" type="password" placeholder="留空不修改">') +
    field('密钥（密钥认证）', '<select name="key_id"><option value="">选择密钥</option>' +
      SSHS.keys.map(k => '<option value="' + k.id + '"' + (String(h.key_id) === String(k.id) ? ' selected' : '') + '>' + esc(k.name) + '</option>').join('') + '</select>') +
    field('标签', '<input name="tags" value="' + esc(h.tags || '') + '" placeholder="逗号分隔">'),
    async form => {
      const data = { name: inputVal(form, 'name'), hostname: inputVal(form, 'hostname'),
        port: parseInt(inputVal(form, 'port')) || 22, username: inputVal(form, 'username'),
        auth_type: inputVal(form, 'auth_type'), tags: inputVal(form, 'tags') };
      const pw = form.querySelector('[name=password]').value;
      if (pw) data.password = pw;
      if (data.auth_type === 'key') data.key_id = parseInt(inputVal(form, 'key_id')) || null;
      if (h.id) await api('PUT', '/ssh/hosts/' + h.id, data);
      else await api('POST', '/ssh/hosts', data);
      toast('已保存', 'ok'); loadHosts(); return true;
    }, true);
}
async function loadKeys() { SSHS.keys = await api('GET', '/ssh/keys').catch(() => []); }
function keyMgrModal() {
  const m = openModal('🔑 密钥管理',
    '<div class="tbl-wrap"><table class="tbl"><tbody>' +
    SSHS.keys.map(k => '<tr><td><b>' + esc(k.name) + '</b></td>' +
      '<td><button class="btn sm danger" data-delk="' + k.id + '">删除</button></td></tr>').join('') +
    '</tbody></table></div>' + (SSHS.keys.length ? '' : '<div class="empty">暂无密钥</div>') +
    '<div class="modal-foot" style="padding:12px 0 0;border:0"><button class="btn primary" id="key-add">＋ 添加密钥</button></div>', true);
  m.body.querySelectorAll('[data-delk]').forEach(b => b.addEventListener('click', async () => {
    if (!await confirmDlg('删除密钥', '确定删除该密钥吗？', '删除')) return;
    await api('DELETE', '/ssh/keys/' + b.dataset.delk);
    toast('已删除', 'ok'); await loadKeys(); m.close(); keyMgrModal();
  }));
  m.body.querySelector('#key-add').addEventListener('click', () => {
    formModal('添加密钥', field('名称', '<input name="name">') +
      field('私钥（PEM）', '<textarea name="pk" rows="6" placeholder="-----BEGIN ..."></textarea>'),
      async form => {
        await api('POST', '/ssh/keys', { name: inputVal(form, 'name'),
          private_key: form.querySelector('[name=pk]').value.trim() });
        toast('已添加', 'ok'); await loadKeys(); m.close(); keyMgrModal(); return true;
      }, true);
  });
}

/* ---- 终端标签页 ---- */
async function sshConnect(host) {
  if (!host) return;
  if (typeof Terminal === 'undefined') { toast('xterm.js 未加载（CDN 不可达），终端不可用', 'err'); return; }
  const r = await withLoading(() => api('POST', '/ssh/sessions', { host_id: host.id }));
  const tabId = 'tab' + Date.now();
  const tabsEl = $('term-tabs'), panesEl = $('term-panes');

  const tabBtn = document.createElement('div');
  tabBtn.className = 'term-tab';
  tabBtn.innerHTML = '<span>💻 ' + esc(host.name) + '</span><span class="x" title="关闭">✕</span>';
  const pane = document.createElement('div');
  pane.className = 'term-pane';
  panesEl.appendChild(pane);
  tabsEl.insertBefore(tabBtn, $('btn-sftp'));

  const term = new Terminal({ cursorBlink: true, fontSize: 13, scrollback: 5000,
    theme: { background: '#000000', foreground: '#dbe2ec' } });
  let fit = null;
  if (typeof FitAddon !== 'undefined') { fit = new FitAddon.FitAddon(); term.loadAddon(fit); }
  term.open(pane);

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(proto + '://' + location.host + '/api/ssh/ws/ssh?token=' +
    encodeURIComponent(S.token) + '&session_id=' + encodeURIComponent(r.session_id));
  ws.binaryType = 'arraybuffer';
  const t = { id: tabId, host, sessionId: r.session_id, term, fit, ws, tabBtn, pane };
  S.termTabs[tabId] = t;

  ws.onopen = () => { fit && fit.fit(); sendResize(t); term.focus(); };
  ws.onmessage = ev => {
    if (typeof ev.data === 'string') term.write(ev.data);
    else if (ev.data.byteLength) term.write(new Uint8Array(ev.data));
  };
  ws.onclose = () => term.write('\r\n\x1b[31m[连接已断开]\x1b[0m\r\n');
  ws.onerror = () => term.write('\r\n\x1b[31m[WebSocket 错误]\x1b[0m\r\n');
  term.onData(d => { if (ws.readyState === 1) ws.send(JSON.stringify({ t: 'input', d })); });

  tabBtn.addEventListener('click', e => {
    if (e.target.classList.contains('x')) { closeTab(tabId); return; }
    activateTab(tabId);
  });
  activateTab(tabId);
  term.write('\x1b[32m正在连接 ' + host.username + '@' + host.hostname + ' …\x1b[0m\r\n');
}
function activateTab(tabId) {
  S.activeTab = tabId;
  Object.values(S.termTabs).forEach(t => {
    const on = t.id === tabId;
    t.tabBtn.classList.toggle('active', on);
    t.pane.classList.toggle('active', on);
  });
  const t = S.termTabs[tabId];
  if (t) {
    if (t.fit) setTimeout(() => { t.fit.fit(); sendResize(t); }, 60);
    t.term.focus();
    if ($('sftp-drawer') && $('sftp-drawer').classList.contains('open')) {
      SSHS.sftpSid = t.sessionId; sftpGo();
    }
  }
}
function sendResize(t) {
  if (t.ws.readyState === 1 && t.term.cols)
    t.ws.send(JSON.stringify({ t: 'resize', cols: t.term.cols, rows: t.term.rows }));
}
async function closeTab(tabId) {
  const t = S.termTabs[tabId]; if (!t) return;
  try { t.ws.close(); } catch (e) {}
  try { await api('DELETE', '/ssh/sessions/' + t.sessionId); } catch (e) {}
  t.term.dispose(); t.tabBtn.remove(); t.pane.remove();
  delete S.termTabs[tabId];
  const rest = Object.keys(S.termTabs);
  if (rest.length) activateTab(rest[rest.length - 1]);
  else { S.activeTab = null; const dr = $('sftp-drawer'); if (dr) dr.classList.remove('open'); }
}

/* ---- SFTP ---- */
function joinP(a, b) { return (a.replace(/\/+$/, '') + '/' + b).replace(/\/+/g, '/'); }
async function sftpGo() {
  const listEl = $('sftp-list'), pathEl = $('sftp-path');
  if (!SSHS.sftpSid || !listEl) return;
  pathEl.textContent = SSHS.sftpPath;
  let items = [];
  try {
    items = await api('GET', '/ssh/sftp/list?session_id=' + SSHS.sftpSid + '&path=' + encodeURIComponent(SSHS.sftpPath));
  } catch (e) { listEl.innerHTML = '<div class="empty">' + esc(e.message) + '</div>'; return; }
  const arr = Array.isArray(items) ? items : (items.items || items.files || []);
  arr.sort((a, b) => (isDir(b) ? 1 : 0) - (isDir(a) ? 1 : 0));
  const up = SSHS.sftpPath !== '/'
    ? '<div class="sftp-row" data-up="1">📁 <b>..</b></div>' : '';
  listEl.innerHTML = up + arr.map((e, i) => {
    const dir = isDir(e);
    return '<div class="sftp-row" data-i="' + i + '"><span>' + (dir ? '📁' : '📄') + '</span>' +
      '<span>' + esc(e.name || e.filename) + '</span>' +
      (dir ? '' : '<span class="sz">' + fmtSize(e.size) + '</span>') +
      '<span class="btn-row" style="margin-left:auto">' +
      (dir ? '' : '<button class="btn sm" data-dl="' + i + '" title="下载">⬇</button>' +
        '<button class="btn sm" data-editf="' + i + '" title="在线编辑">✎</button>') +
      '<button class="btn sm" data-ren="' + i + '" title="重命名">✏️</button>' +
      '<button class="btn sm danger" data-rm="' + i + '" title="删除">🗑</button></span></div>';
  }).join('') || '<div class="empty">空目录</div>';
  listEl._items = arr;
  listEl.querySelectorAll('.sftp-row').forEach(row => {
    const i = row.dataset.i;
    if (row.dataset.up) { row.addEventListener('click', () => {
      SSHS.sftpPath = SSHS.sftpPath.replace(/\/[^/]+\/?$/, '') || '/'; sftpGo(); }); return; }
    const e = arr[+i];
    row.addEventListener('dblclick', () => {
      if (isDir(e)) { SSHS.sftpPath = joinP(SSHS.sftpPath, e.name || e.filename); sftpGo(); }
      else openFileEditor(joinP(SSHS.sftpPath, e.name || e.filename));
    });
  });
  listEl.querySelectorAll('[data-dl]').forEach(b => b.addEventListener('click', async ev => {
    ev.stopPropagation();
    const e = arr[+b.dataset.dl], p = joinP(SSHS.sftpPath, e.name || e.filename);
    const r = await withLoading(() => apiRaw('GET', '/ssh/sftp/download?session_id=' + SSHS.sftpSid + '&path=' + encodeURIComponent(p)));
    const blob = await r.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = e.name || e.filename; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }));
  listEl.querySelectorAll('[data-editf]').forEach(b => b.addEventListener('click', ev => {
    ev.stopPropagation();
    openFileEditor(joinP(SSHS.sftpPath, arr[+b.dataset.editf].name || arr[+b.dataset.editf].filename));
  }));
  listEl.querySelectorAll('[data-ren]').forEach(b => b.addEventListener('click', async ev => {
    ev.stopPropagation();
    const e = arr[+b.dataset.ren], old = e.name || e.filename;
    const n = prompt('重命名为：', old); if (!n || n === old) return;
    await api('POST', '/ssh/sftp/rename', { session_id: SSHS.sftpSid, path: joinP(SSHS.sftpPath, old), new_name: n });
    toast('已重命名', 'ok'); sftpGo();
  }));
  listEl.querySelectorAll('[data-rm]').forEach(b => b.addEventListener('click', async ev => {
    ev.stopPropagation();
    const e = arr[+b.dataset.rm], p = joinP(SSHS.sftpPath, e.name || e.filename);
    if (!await confirmDlg('删除', '确定删除 <b>' + esc(p) + '</b> 吗？', '删除')) return;
    await api('POST', '/ssh/sftp/delete', { session_id: SSHS.sftpSid, path: p });
    toast('已删除', 'ok'); sftpGo();
  }));
}
function isDir(e) {
  const t = String(e.type || '').toLowerCase();
  return t === 'dir' || t === 'directory' || e.is_dir === true || e.isdir === true;
}
async function openFileEditor(path) {
  let content = '';
  try {
    const d = await withLoading(() => api('GET', '/ssh/sftp/read?session_id=' + SSHS.sftpSid + '&path=' + encodeURIComponent(path)));
    content = d.content != null ? d.content : (d.data || '');
  } catch (e) { toast('读取失败：' + e.message, 'err'); return; }
  const m = openModal('编辑 ' + path,
    '<textarea id="fedit" rows="20" style="min-height:300px">' + esc(content) + '</textarea>' +
    '<div class="modal-foot" style="padding:12px 0 0;border:0">' +
    '<button class="btn ghost" data-c>取消</button> ' +
    '<button class="btn primary" data-s>💾 保存</button></div>', true);
  const dupF = m.el.querySelector('.modal > .modal-foot');
  if (dupF) dupF.remove();
  m.body.querySelector('[data-c]').addEventListener('click', m.close);
  m.body.querySelector('[data-s]').addEventListener('click', async () => {
    await withLoading(() => api('POST', '/ssh/sftp/write',
      { session_id: SSHS.sftpSid, path, content: m.body.querySelector('#fedit').value }));
    toast('已保存', 'ok'); m.close();
  });
}

/* ================= 启动 ================= */
document.addEventListener('DOMContentLoaded', async () => {
  if (S.token) {
    try {
      const me = await api('GET', '/auth/me');
      S.user = me.username || '';
      enterApp();
    } catch (e) { showLogin(); }
  } else {
    showLogin();
  }
});
