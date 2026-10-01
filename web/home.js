const $ = id => document.getElementById(id);

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
      location.href = '/analyzer.html?import=1';
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
