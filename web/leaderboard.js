const $ = id => document.getElementById(id);
const html = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const STORE_KEY = 'morimens-leaderboard';

function load() { try { return JSON.parse(localStorage.getItem(STORE_KEY) || '[]'); } catch { return []; } }
function save(list) { localStorage.setItem(STORE_KEY, JSON.stringify(list)); }

function summarize(parsed) {
  if (!Array.isArray(parsed.records)) throw new Error('缺少 records 字段，不是本工具导出的抽卡记录');
  const records = parsed.records.filter(r => !r.excluded);
  const ssrs = records.filter(r => r.rarity === 'SSR');
  const valid = records.map(r => r.pullsSinceSSR).filter(Number.isFinite);
  const average = valid.length ? valid.reduce((a, b) => a + b, 0) / valid.length : null;
  const up = ssrs.filter(r => r.upStatus === 'up').length;
  const off = ssrs.filter(r => r.upStatus === 'off').length;
  return {
    id: (parsed.player && (parsed.player.uid || parsed.player.nickname)) || `guest-${Date.now()}-${Math.random().toString(36).slice(2,7)}`,
    uid: parsed.player && parsed.player.uid || '',
    nickname: parsed.player && parsed.player.nickname || '匿名玩家',
    totalPulls: records.length,
    ssrCount: ssrs.length,
    up, off,
    sampleSize: valid.length,
    average,
    achievementTags: Array.isArray(parsed.achievementTags) ? parsed.achievementTags : [],
    importedAt: new Date().toISOString(),
  };
}

function upsert(entry) {
  const list = load();
  const idx = list.findIndex(x => x.id === entry.id);
  if (idx >= 0) list[idx] = entry; else list.push(entry);
  save(list);
  render();
}

function luckBadge(entry) {
  if (entry.sampleSize < 5) return { label: '样本不足', cls: 'muted' };
  if (entry.average <= 22) return { label: '欧皇', cls: 'up' };
  if (entry.average >= 40) return { label: '非酋', cls: 'off' };
  return { label: '正常', cls: 'muted' };
}

function render() {
  const list = load();
  $('board-count').textContent = `当前榜单：${list.length} 位玩家`;
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
        <td><button class="board-remove" data-id="${html(e.id)}">移除</button></td>
      </tr>`;
    }).join('')}</tbody></table>`;
}

function handleFiles(files) {
  const err = $('board-error');
  err.textContent = '';
  [...files].forEach(file => {
    if (!/\.json$/i.test(file.name)) { err.textContent = '请选择 .json 格式的抽卡记录文件。'; return; }
    const reader = new FileReader();
    reader.onload = () => {
      try { upsert(summarize(JSON.parse(reader.result))); }
      catch (e) { err.textContent = `${file.name} 不是有效的抽卡记录 JSON：${e.message}`; }
    };
    reader.onerror = () => { err.textContent = `读取 ${file.name} 失败。`; };
    reader.readAsText(file);
  });
}

$('board-file').addEventListener('change', e => handleFiles(e.target.files));
const drop = $('drop');
['dragenter', 'dragover'].forEach(evt => drop.addEventListener(evt, e => { e.preventDefault(); drop.style.borderColor = '#9b8a68'; }));
['dragleave', 'drop'].forEach(evt => drop.addEventListener(evt, e => { e.preventDefault(); drop.style.borderColor = ''; }));
drop.addEventListener('drop', e => handleFiles(e.dataTransfer.files));
$('clear-board').onclick = () => { if (confirm('确定清空本机榜单吗？此操作不可撤销。')) { save([]); render(); } };
document.addEventListener('click', e => {
  const btn = e.target.closest('.board-remove');
  if (btn) { save(load().filter(x => x.id !== btn.dataset.id)); render(); }
});
render();
