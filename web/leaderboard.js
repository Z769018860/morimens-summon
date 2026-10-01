const $ = id => document.getElementById(id);
const html = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));

// Cross-device board: entries are posted as Waline "comments" grouped under
// one fixed path, since Waline (a comment widget) is the shared backend this
// site was pointed at — there is no purpose-built leaderboard API here, so a
// comment's free-text body carries a compact encoded summary instead of
// prose. Only the summary + the identity you choose is sent; never the full
// gacha record list.
const WALINE_BASE = 'https://testbox.qingdengbuyi.top';
const BOARD_PATH = '/morimens-summon/leaderboard';
const ENTRY_TAG = 'MSV1';

// Entries you've hidden on this device only (does not delete them from the
// server — this client has no Waline admin credentials to do that).
const HIDDEN_KEY = 'morimens-leaderboard-hidden';
function loadHidden() { try { return new Set(JSON.parse(localStorage.getItem(HIDDEN_KEY) || '[]')); } catch { return new Set(); } }
function saveHidden(set) { localStorage.setItem(HIDDEN_KEY, JSON.stringify([...set])); }
let hidden = loadHidden();

function summarize(parsed) {
  if (!Array.isArray(parsed.records)) throw new Error('缺少 records 字段，不是本工具导出的抽卡记录');
  const records = parsed.records.filter(r => !r.excluded);
  const ssrs = records.filter(r => r.rarity === 'SSR');
  const valid = records.map(r => r.pullsSinceSSR).filter(Number.isFinite);
  const average = valid.length ? valid.reduce((a, b) => a + b, 0) / valid.length : null;
  const up = ssrs.filter(r => r.upStatus === 'up').length;
  const off = ssrs.filter(r => r.upStatus === 'off').length;
  return {
    totalPulls: records.length,
    ssrCount: ssrs.length,
    up, off,
    sampleSize: valid.length,
    average,
    achievementTags: Array.isArray(parsed.achievementTags) ? parsed.achievementTags : [],
  };
}

function luckBadge(entry) {
  if (entry.sampleSize < 5) return { label: '样本不足', cls: 'muted' };
  if (entry.average <= 22) return { label: '欧皇', cls: 'up' };
  if (entry.average >= 40) return { label: '非酋', cls: 'off' };
  return { label: '正常', cls: 'muted' };
}

// --- Waline wire format -----------------------------------------------
// Waline stores/returns the comment body as rendered markdown/HTML, so a
// literal JSON payload risks being mangled (quotes escaped, wrapped in <p>).
// A flat, delimiter-based line survives that round trip much more reliably.
const SEP = ';;';
function clean(s) { return String(s ?? '').replace(/[;<>]/g, '').replace(/\s+/g, ' ').trim(); }
function encodeEntry(nickname, uid, s) {
  return [ENTRY_TAG, clean(nickname), clean(uid), s.totalPulls, s.ssrCount, s.up, s.off,
    s.sampleSize, s.average == null ? '' : s.average.toFixed(2), s.achievementTags.map(clean).join('|')
  ].join(SEP);
}
function decodeEntry(raw, meta) {
  const text = String(raw || '')
    .replace(/<[^>]+>/g, '')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .trim();
  if (!text.startsWith(ENTRY_TAG + SEP)) return null;
  const parts = text.split(SEP);
  if (parts.length < 10) return null;
  const [, nickname, uid, totalPulls, ssrCount, up, off, sampleSize, average, tags] = parts;
  return {
    id: meta.id, nickname: nickname || '匿名玩家', uid,
    totalPulls: Number(totalPulls) || 0, ssrCount: Number(ssrCount) || 0,
    up: Number(up) || 0, off: Number(off) || 0, sampleSize: Number(sampleSize) || 0,
    average: average === '' ? null : Number(average),
    achievementTags: tags ? tags.split('|').filter(Boolean) : [],
    postedAt: meta.postedAt,
  };
}

async function fetchBoard() {
  const url = `${WALINE_BASE}/api/comment?path=${encodeURIComponent(BOARD_PATH)}&pageSize=200&page=1&sortBy=insertedAt_desc`;
  const res = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!res.ok) throw new Error(`榜单服务器返回 ${res.status}`);
  const body = await res.json();
  const list = body?.data?.data || body?.data || [];
  if (!Array.isArray(list)) throw new Error('榜单服务器返回的数据格式无法识别');
  return list
    .map(c => decodeEntry(c.comment ?? c.orig ?? '', { id: c.objectId || c.id, postedAt: c.insertedAt || c.time }))
    .filter(Boolean);
}

async function postEntry(nickname, uid, s) {
  const mail = uid ? `u${encodeURIComponent(uid)}@anon.local` : `anon${Date.now().toString(36)}@anon.local`;
  const body = {
    comment: encodeEntry(nickname, uid, s),
    nick: nickname || '匿名玩家',
    mail,
    link: '',
    url: BOARD_PATH,
    path: BOARD_PATH,
    ua: navigator.userAgent,
  };
  const res = await fetch(`${WALINE_BASE}/api/comment`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json())?.errmsg || ''; } catch {}
    throw new Error(`提交失败（HTTP ${res.status}）${detail}`);
  }
}

// --- Identity confirmation modal ---------------------------------------
// Always asked at upload time, pre-filled from the file's own player info
// when present; either field can be left blank (anonymous).
function askIdentity(prefill) {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `<div class="update-modal" role="dialog" aria-modal="true">
      <button class="modal-close" aria-label="取消">×</button>
      <span class="kicker">UPLOAD TO SHARED BOARD</span>
      <h2>上传到跨设备榜单</h2>
      <p class="modal-intro">只会发送统计摘要（已记录抽数、SSR 数、UP/歪次数、平均出货抽数、成就标签）和下面你填写的昵称/UID 到 <b>${html(WALINE_BASE.replace('https://',''))}</b>，不会上传完整抽卡记录。昵称和 UID 都可以留空，留空即匿名上传。</p>
      <div class="profile-row" style="margin-top:18px">
        <label>昵称（可留空）<input id="id-nick" value="${html(prefill.nickname || '')}" placeholder="匿名"></label>
        <label>UID（可留空）<input id="id-uid" value="${html(prefill.uid || '')}" placeholder="匿名"></label>
      </div>
      <div class="modal-actions">
        <button class="ghost" id="id-cancel">取消</button>
        <button class="ghost" id="id-anon">清空（匿名上传）</button>
        <button class="primary" id="id-ok">确认上传</button>
      </div>
    </div>`;
    document.body.appendChild(overlay);
    const close = result => { overlay.remove(); resolve(result); };
    overlay.querySelector('.modal-close').onclick = () => close(null);
    overlay.querySelector('#id-cancel').onclick = () => close(null);
    overlay.querySelector('#id-anon').onclick = () => { overlay.querySelector('#id-nick').value = ''; overlay.querySelector('#id-uid').value = ''; };
    overlay.querySelector('#id-ok').onclick = () => close({
      nickname: overlay.querySelector('#id-nick').value.trim(),
      uid: overlay.querySelector('#id-uid').value.trim(),
    });
  });
}

function render(list) {
  $('board-count').textContent = `当前榜单：${list.length} 位玩家 · 跨设备共享`;
  if (!list.length) {
    $('board-wrap').innerHTML = '<div class="board-empty">还没有人加入榜单，导入一份 JSON 开始吧。</div>';
    return;
  }
  const ranked = [...list].filter(e => e.sampleSize >= 5).sort((a, b) => a.average - b.average);
  const unranked = list.filter(e => e.sampleSize < 5);
  const rows = [...ranked, ...unranked];
  $('board-wrap').innerHTML = `<table class="board-table"><thead><tr>
    <th>名次</th><th>玩家</th><th>已记录抽数</th><th>SSR / UP / 歪</th><th>平均出货</th><th>欧非评价</th><th>成就标签</th><th></th>
    </tr></thead><tbody>${rows.map((e, i) => {
      const rank = i < ranked.length ? i + 1 : '—';
      const luck = luckBadge(e);
      return `<tr>
        <td class="rank ${rank===1?'top1':''}">${rank===1?'🏆 ':''}${rank}</td>
        <td class="player"><b>${html(e.nickname)}</b>${e.uid ? `<small>UID ${html(e.uid)}</small>` : ''}</td>
        <td>${e.totalPulls}</td>
        <td>${e.ssrCount} / ${e.up} / ${e.off}</td>
        <td>${e.average != null ? e.average.toFixed(1) + ' 抽' : '—'}<br><small>${e.sampleSize} 个有效间隔</small></td>
        <td><span class="pill ${luck.cls}">${luck.label}</span></td>
        <td class="tagcell">${(e.achievementTags||[]).map(t => `<span>${html(t)}</span>`).join('') || '<span>—</span>'}</td>
        <td><button class="board-remove" data-id="${html(e.id)}" title="只在你这台设备上隐藏，不会删除服务器上的记录">本机隐藏</button></td>
      </tr>`;
    }).join('')}</tbody></table>`;
}

let currentList = [];
async function refresh() {
  $('board-wrap').innerHTML = '<div class="board-empty">正在从榜单服务器读取…</div>';
  $('board-error').textContent = '';
  try {
    const list = await fetchBoard();
    currentList = list.filter(e => !hidden.has(e.id));
    render(currentList);
  } catch (e) {
    $('board-wrap').innerHTML = '<div class="board-empty">暂时连不上榜单服务器，请稍后重试。</div>';
    $('board-error').textContent = `读取榜单失败：${e.message}`;
  }
}

async function handleFiles(files) {
  const err = $('board-error');
  err.textContent = '';
  const failures = [];
  for (const file of files) {
    if (!/\.json$/i.test(file.name)) { failures.push('请选择 .json 格式的抽卡记录文件。'); continue; }
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      const summary = summarize(parsed);
      const prefill = parsed.player || {};
      const identity = await askIdentity(prefill);
      if (!identity) continue; // user cancelled this file
      await postEntry(identity.nickname, identity.uid, summary);
    } catch (e) {
      failures.push(`${file.name} 上传失败：${e.message}`);
    }
  }
  await refresh(); // refresh() clears #board-error itself, so set upload failures after it resolves
  if (failures.length) err.textContent = failures.join('；');
}

$('board-file').addEventListener('change', e => handleFiles(e.target.files));
const drop = $('drop');
['dragenter', 'dragover'].forEach(evt => drop.addEventListener(evt, e => { e.preventDefault(); drop.style.borderColor = '#9b8a68'; }));
['dragleave', 'drop'].forEach(evt => drop.addEventListener(evt, e => { e.preventDefault(); drop.style.borderColor = ''; }));
drop.addEventListener('drop', e => handleFiles(e.dataTransfer.files));
$('clear-board').onclick = () => {
  if (confirm('只会在这台设备上把当前看到的记录隐藏，不会删除服务器上的数据，确定吗？')) {
    hidden = new Set([...hidden, ...currentList.map(e => e.id)]);
    saveHidden(hidden);
    refresh();
  }
};
document.addEventListener('click', e => {
  const btn = e.target.closest('.board-remove');
  if (btn) { hidden.add(btn.dataset.id); saveHidden(hidden); currentList = currentList.filter(x => x.id !== btn.dataset.id); render(currentList); }
});
refresh();
