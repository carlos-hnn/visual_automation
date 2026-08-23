let state = null;
let selected = null;
let lastLog = 0;

const esc = (value) => String(value).replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
})[char]);

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}

function renderScripts() {
  const nav = document.getElementById('scripts');
  nav.innerHTML = '';
  for (const script of state.scripts) {
    const button = document.createElement('button');
    button.className = `script${script.id === selected ? ' selected' : ''}`;
    button.innerHTML = `${esc(script.label)}<small>${esc(script.id)}</small>`;
    button.onclick = () => {
      selected = script.id;
      renderScripts();
      renderFields();
    };
    nav.appendChild(button);
  }
}

function makeField(key, value, metadata = {}) {
  const field = document.createElement('div');
  const nested = typeof value === 'object' && value !== null;
  field.className = `field${nested ? ' wide' : ''}`;
  const label = document.createElement('label');
  label.innerHTML = `${esc(metadata.label || key)}<small>${esc(key)}</small>`;
  field.appendChild(label);

  let input;
  if (key === 'mouse_backend') {
    input = document.createElement('select');
    for (const option of ['standard', 'quartz']) {
      const item = document.createElement('option');
      item.value = option;
      item.textContent = option === 'standard' ? 'Standard — moves cursor' : 'Quartz — experimental background click';
      item.selected = value === option;
      input.appendChild(item);
    }
  } else if (key === 'platform') {
    input = document.createElement('select');
    for (const option of ['auto', 'mac', 'windows']) {
      const item = document.createElement('option');
      item.value = option;
      item.textContent = option === 'auto' ? 'Auto detect' : option;
      item.selected = value === option;
      input.appendChild(item);
    }
  } else if (typeof value === 'boolean') {
    input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = value;
  } else if (nested) {
    input = document.createElement('textarea');
    input.value = JSON.stringify(value, null, 2);
  } else {
    input = document.createElement('input');
    input.type = typeof value === 'number' ? 'number' : 'text';
    if (typeof value === 'number') input.step = 'any';
    input.value = value;
  }
  input.dataset.key = key;
  input.dataset.type = Array.isArray(value) ? 'array' : typeof value;
  field.appendChild(input);
  return field;
}

function renderFields() {
  const script = state.scripts.find((item) => item.id === selected);
  document.getElementById('title').textContent = script.label;
  const operation = document.getElementById('fields');
  const advanced = document.getElementById('advanced-fields');
  operation.innerHTML = '';
  advanced.innerHTML = '';
  let advancedCount = 0;
  for (const [key, value] of Object.entries(script.config)) {
    const metadata = script.metadata?.[key] || {};
    const target = metadata.section === 'advanced' ? advanced : operation;
    if (target === advanced) advancedCount += 1;
    target.appendChild(makeField(key, value, metadata));
  }
  document.getElementById('advanced-count').textContent = `(${advancedCount})`;
  updateButtons();
}

function collect() {
  const result = {};
  for (const input of document.querySelectorAll('[data-key]')) {
    let value;
    if (input.dataset.type === 'boolean') value = input.checked;
    else if (input.dataset.type === 'number') value = Number(input.value);
    else if (input.dataset.type === 'object' || input.dataset.type === 'array') value = JSON.parse(input.value);
    else value = input.value;
    result[input.dataset.key] = value;
  }
  return result;
}

async function save() {
  const config = collect();
  await api('/api/save', { method: 'POST', body: JSON.stringify({ id: selected, config }) });
  state.scripts.find((item) => item.id === selected).config = config;
  flash('Settings saved');
}

async function start() {
  const config = collect();
  if (config.dry_run === false) {
    const detail = config.mouse_backend === 'quartz'
      ? 'The experimental Quartz backend will send background click events. RuneLite compatibility is not yet confirmed.'
      : 'This automation will move and click the mouse.';
    if (!confirm(`Live mode is enabled. ${detail} Start it?`)) return;
  }
  await api('/api/start', { method: 'POST', body: JSON.stringify({ id: selected, config }) });
  state.scripts.find((item) => item.id === selected).config = config;
  await refreshStatus();
}

async function stop() {
  await api('/api/stop', { method: 'POST', body: '{}' });
  setTimeout(refreshStatus, 250);
}

function flash(text) {
  const element = document.getElementById('status');
  element.textContent = text;
  setTimeout(refreshStatus, 900);
}

function updateButtons() {
  const running = state?.process?.running;
  document.getElementById('start').disabled = running;
  document.getElementById('stop').disabled = !running;
}

async function refreshStatus() {
  const process = await api('/api/status');
  state.process = process;
  const status = document.getElementById('status');
  if (process.running) {
    const script = state.scripts.find((item) => item.id === process.script_id);
    status.textContent = `Running · ${script ? script.label : process.script_id}`;
    status.className = 'status running';
  } else {
    status.textContent = 'Idle';
    status.className = 'status';
  }
  updateButtons();
}

async function pollLogs() {
  try {
    const data = await api(`/api/logs?after=${lastLog}`);
    const logs = document.getElementById('logs');
    for (const log of data.logs) {
      lastLog = log.id;
      const line = document.createElement('span');
      line.className = log.kind === 'system' ? 'log-system' : '';
      line.textContent = `${log.text}\n`;
      logs.appendChild(line);
    }
    if (data.logs.length) logs.scrollTop = logs.scrollHeight;
  } catch (_) {
    // The next poll retries automatically.
  }
  setTimeout(pollLogs, 700);
}

async function init() {
  state = await api('/api/state');
  selected = state.scripts[0].id;
  renderScripts();
  renderFields();
  refreshStatus();
  pollLogs();
}

document.getElementById('start').onclick = () => start().catch((error) => alert(error.message));
document.getElementById('stop').onclick = () => stop().catch((error) => alert(error.message));
document.getElementById('save').onclick = () => save().catch((error) => alert(`Invalid setting: ${error.message}`));
init();
