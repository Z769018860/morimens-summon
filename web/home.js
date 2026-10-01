const $ = id => document.getElementById(id);
const html = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));

// Local cache of previously-imported exports, so re-visiting doesn't require
// re-picking the same file. Stored entirely in this browser's localStorage —
// never sent anywhere. Keyed by player uid/nickname when available so
// re-importing the same person's newer export updates their entry in place.
const CACHE_INDEX_KEY = 'morimens-import-cache-index';
const CACHE_ENTRY_PREFIX = 'morimens-import-cache:';
const MAX_CACHE_ENTRIES = 12;

function loadCacheIndex() {
  try { return JSON.parse(localStorage.getItem(CACHE_INDEX_KEY) || '[]'); }
  catch { return []; }
}
function saveCacheIndex(list) {
  localStorage.setItem(CACHE_INDEX_KEY, JSON.stringify(list));
}
function cacheIdFor(parsed, filename) {
  const player = parsed.player || {};
  // Prefer a stable player identity; fall back to the filename so data
  // without a filled-in nickname/UID still gets remembered and re-selectable.
  return player.uid ? `uid:${player.uid}`
    : player.nickname ? `nick:${player.nickname}`
    : filename ? `file:${filename}` : null;
}
function storeInCache(raw, parsed, filename) {
  const id = cacheIdFor(parsed, filename);
  if (!id) return; // nothing stable to key on (shouldn't happen with a real file)
  let index = loadCacheIndex();
  const entry = {
    id,
    nickname: parsed.player?.nickname || '',
    uid: parsed.player?.uid || '',
    filename: filename || '',
    recordCount: Array.isArray(parsed.records) ? parsed.records.length : 0,
    generatedAt: parsed.generatedAt || null,
    savedAt: new Date().toISOString(),
  };
  index = index.filter(e => e.id !== id);
  index.unshift(entry);
  while (index.length > MAX_CACHE_ENTRIES) {
    const dropped = index.pop();
    try { localStorage.removeItem(CACHE_ENTRY_PREFIX + dropped.id); } catch {}
  }
  try {
    localStorage.setItem(CACHE_ENTRY_PREFIX + id, raw);
    saveCacheIndex(index);
  } catch (e) {
    // Quota exceeded or similar — drop the oldest other entry once and retry,
    // but never let caching failure block the actual import/redirect.
    if (index.length > 1) {
      const dropped = index.pop();
      try {
        localStorage.removeItem(CACHE_ENTRY_PREFIX + dropped.id);
        localStorage.setItem(CACHE_ENTRY_PREFIX + id, raw);
        saveCacheIndex(index);
      } catch {}
    }
  }
}
function removeFromCache(id) {
  saveCacheIndex(loadCacheIndex().filter(e => e.id !== id));
  try { localStorage.removeItem(CACHE_ENTRY_PREFIX + id); } catch {}
}
function openFromCache(id) {
  const raw = localStorage.getItem(CACHE_ENTRY_PREFIX + id);
  if (!raw) { removeFromCache(id); renderCacheList(); return; }
  sessionStorage.setItem('morimens-import', raw);
  location.href = 'analyzer.html?import=1';
}
function renderCacheList() {
  const section = $('cache-section');
  const index = loadCacheIndex();
  if (!index.length) { section.hidden = true; return; }
  section.hidden = false;
  $('cache-list').innerHTML = index.map(e => `
    <div class="cache-row">
      <div class="cache-info">
        <b>${html(e.nickname || e.filename || '未命名存档')}</b>
        ${e.uid ? `<small>UID ${html(e.uid)}</small>` : ''}
        <small>${e.recordCount} 条记录 · 保存于 ${new Date(e.savedAt).toLocaleString('zh-CN')}</small>
      </div>
      <div class="cache-actions">
        <button class="ghost" data-open="${html(e.id)}">直接查看 →</button>
        <button class="board-remove" data-remove="${html(e.id)}">删除</button>
      </div>
    </div>`).join('');
}

function readAndGo(file) {
  const err = $('import-error');
  err.textContent = '';
  if (!file) return;
  if (!/\.json$/i.test(file.name)) {
    err.textContent = '请选择 .json 格式的抽卡记录文件。';
    return;
  }
  const reader = new FileReader();
  reader.onerror = () => { err.textContent = '读取文件失败，请重试。'; };
  reader.onload = () => {
    try {
      const parsed = JSON.parse(reader.result);
      if (!Array.isArray(parsed.records)) throw new Error('缺少 records 字段');
      sessionStorage.setItem('morimens-import', reader.result);
      storeInCache(reader.result, parsed, file.name);
      location.href = 'analyzer.html?import=1';
    } catch (e) {
      err.textContent = `这不是一个有效的抽卡记录 JSON：${e.message}`;
    }
  };
  reader.readAsText(file);
}

$('import-file').addEventListener('change', e => readAndGo(e.target.files[0]));

const drop = $('drop');
['dragenter', 'dragover'].forEach(evt => drop.addEventListener(evt, e => {
  e.preventDefault(); drop.style.borderColor = '#9b8a68';
}));
['dragleave', 'drop'].forEach(evt => drop.addEventListener(evt, e => {
  e.preventDefault(); drop.style.borderColor = '';
}));
drop.addEventListener('drop', e => readAndGo(e.dataTransfer.files[0]));

document.addEventListener('click', e => {
  const open = e.target.closest('[data-open]');
  if (open) { openFromCache(open.dataset.open); return; }
  const remove = e.target.closest('[data-remove]');
  if (remove) { removeFromCache(remove.dataset.remove); renderCacheList(); }
});

renderCacheList();
