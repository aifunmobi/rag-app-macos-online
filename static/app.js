/* ============================================================
   RAG Chat — app.js
   Pure vanilla JS, no frameworks, no build step.
   ============================================================ */

'use strict';

// ── State ──────────────────────────────────────────────────────────────────
const state = {
  conversations: [],      // [{id, title, created_at, updated_at}]
  activeId: null,         // current conversation id
  streaming: false,       // true while SSE stream is in progress
  models: [],
  indexDocs: 0,
  indexChunks: 0,
  uploading: false,       // true while an upload request is in flight
};

// ── DOM refs ───────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const historyList   = $('history-list');
const messageArea   = $('messages');
const messageInput  = $('message-input');
const sendBtn       = $('send-btn');
const modelSelect   = $('model-select');
const reindexBtn    = $('reindex-btn');
const themeToggle   = $('theme-toggle');
const ollamaBanner  = $('ollama-banner');
const bannerRetry   = $('banner-retry-btn');
const bannerDismiss = $('banner-dismiss-btn');
const emptyState    = $('empty-state');
const toast         = $('toast');
const newChatBtn    = $('new-chat-btn');
const fileInput     = $('file-input');
const dropOverlay   = $('drop-overlay');
const ingestBanner  = $('ingest-banner');
const ingestText    = ingestBanner ? ingestBanner.querySelector('.ingest-text') : null;
const docsModal     = $('docs-modal');
const docsModalBody = $('docs-modal-body');
const helpModal     = $('help-modal');

// ── Theme ──────────────────────────────────────────────────────────────────
function initTheme() {
  const saved = localStorage.getItem('rag-theme');
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const theme = saved || (prefersDark ? 'dark' : 'light');
  applyTheme(theme);
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('rag-theme', theme);
  const icon = $('theme-icon');
  const label = $('theme-label');
  if (theme === 'dark') {
    icon.textContent = '☀️';
    label.textContent = 'Light mode';
  } else {
    icon.textContent = '🌙';
    label.textContent = 'Dark mode';
  }
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'light';
  applyTheme(current === 'dark' ? 'light' : 'dark');
}

// ── Markdown renderer (tiny, inline) ──────────────────────────────────────
// Returns an HTML string. Intentionally minimal but covers the key cases.
function renderMarkdown(raw) {
  // Escape helper — used for non-code content
  function esc(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  // 1. Extract fenced code blocks so we can protect their content
  const codeBlocks = [];
  let text = raw.replace(/```([^\n]*)\n([\s\S]*?)```/g, (_, lang, code) => {
    const idx = codeBlocks.length;
    const safeCode = code.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    const langLabel = lang.trim() || 'code';
    const html =
      `<pre>` +
        `<div class="code-header">` +
          `<span>${esc(langLabel)}</span>` +
          `<button class="code-copy-btn" data-code="${encodeURIComponent(code)}">Copy</button>` +
        `</div>` +
        `<code>${safeCode}</code>` +
      `</pre>`;
    codeBlocks.push(html);
    return `\x00CODEBLOCK${idx}\x00`;
  });

  // 2. Inline code (protect before escaping rest)
  const inlineCodes = [];
  text = text.replace(/`([^`]+)`/g, (_, code) => {
    const idx = inlineCodes.length;
    inlineCodes.push(`<code>${esc(code)}</code>`);
    return `\x00INLINE${idx}\x00`;
  });

  // 3. Escape the remaining text
  text = text
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;');

  // 4. Block-level: process line by line
  const lines = text.split('\n');
  const out = [];
  let inUl = false, inOl = false;

  function closeLists() {
    if (inUl) { out.push('</ul>'); inUl = false; }
    if (inOl) { out.push('</ol>'); inOl = false; }
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Code block placeholder
    if (/^\x00CODEBLOCK\d+\x00$/.test(line.trim())) {
      closeLists();
      const idx = parseInt(line.trim().replace(/[^\d]/g,''), 10);
      out.push(codeBlocks[idx]);
      continue;
    }

    // Headings
    const hMatch = line.match(/^(#{1,6})\s+(.*)/);
    if (hMatch) {
      closeLists();
      const level = hMatch[1].length;
      out.push(`<h${level}>${inlineFormat(hMatch[2])}</h${level}>`);
      continue;
    }

    // HR
    if (/^(\*{3,}|-{3,}|_{3,})$/.test(line.trim())) {
      closeLists();
      out.push('<hr>');
      continue;
    }

    // Blockquote
    if (line.startsWith('&gt;')) {
      closeLists();
      out.push(`<blockquote>${inlineFormat(line.slice(4).trim())}</blockquote>`);
      continue;
    }

    // Unordered list
    const ulMatch = line.match(/^[-*+]\s+(.*)/);
    if (ulMatch) {
      if (!inUl) { if (inOl) { out.push('</ol>'); inOl = false; } out.push('<ul>'); inUl = true; }
      out.push(`<li>${inlineFormat(ulMatch[1])}</li>`);
      continue;
    }

    // Ordered list
    const olMatch = line.match(/^\d+\.\s+(.*)/);
    if (olMatch) {
      if (!inOl) { if (inUl) { out.push('</ul>'); inUl = false; } out.push('<ol>'); inOl = true; }
      out.push(`<li>${inlineFormat(olMatch[1])}</li>`);
      continue;
    }

    closeLists();

    // Empty line → paragraph break
    if (line.trim() === '') {
      out.push('<br>');
      continue;
    }

    // Normal paragraph line
    out.push(`<p>${inlineFormat(line)}</p>`);
  }

  closeLists();

  let result = out.join('\n');

  // Restore inline codes
  result = result.replace(/\x00INLINE(\d+)\x00/g, (_, i) => inlineCodes[parseInt(i,10)]);

  return result;
}

function inlineFormat(s) {
  // Bold **text** or __text__
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/__(.+?)__/g, '<strong>$1</strong>');
  // Italic *text* or _text_
  s = s.replace(/\*([^*]+?)\*/g, '<em>$1</em>');
  s = s.replace(/_([^_]+?)_/g, '<em>$1</em>');
  // Strikethrough
  s = s.replace(/~~(.+?)~~/g, '<del>$1</del>');
  // Inline code placeholder (already extracted, restore marker)
  s = s.replace(/\x00INLINE(\d+)\x00/g, '\x00INLINE$1\x00');
  // Links
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return s;
}

// ── API helpers ────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  };
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ── Status & bootstrap ─────────────────────────────────────────────────────
async function loadStatus() {
  try {
    const s = await api('GET', '/api/status');
    updateOllamaBanner(!s.ollama);
    updateIndexStatus(s.index);
    if (s.models && s.models.length) populateModels(s.models, s.chat_model);
    else await loadModels();
  } catch (e) {
    console.warn('Status fetch failed', e);
  }
}

function updateOllamaBanner(show) {
  ollamaBanner.classList.toggle('visible', show);
}

function updateIndexStatus(index) {
  if (!index) return;
  state.indexDocs   = index.documents || 0;
  state.indexChunks = index.chunks || 0;
  $('index-status-text').innerHTML =
    `<strong>${state.indexDocs}</strong> docs · <strong>${state.indexChunks}</strong> chunks`;
  updateEmptyState();
}

// Empty pane adapts to whether anything is indexed (not just whether there
// are chat messages) — so an indexed corpus invites a question instead of
// claiming "nothing indexed yet".
function updateEmptyState() {
  const hasDocs = state.indexDocs > 0;
  const icon  = $('empty-icon');
  const title = $('empty-title');
  const desc  = $('empty-desc');
  if (!title || !desc) return;
  if (hasDocs) {
    if (icon) icon.textContent = '💬';
    title.textContent = 'Ask your documents';
    desc.innerHTML =
      `<strong>${state.indexDocs}</strong> file${state.indexDocs !== 1 ? 's' : ''} · ` +
      `<strong>${state.indexChunks}</strong> chunks indexed. ` +
      `Type a question below for grounded answers with citations.`;
  } else {
    if (icon) icon.textContent = '📂';
    title.textContent = 'Nothing indexed yet';
    desc.innerHTML =
      `<strong>Drag &amp; drop files anywhere</strong>, or use the Upload button. ` +
      `They’re saved to <code>input/</code> and indexed automatically.<br>` +
      `<span class="accepted-types">Accepted: PDF, Word (.docx), text, Markdown, ` +
      `HTML, and code files.</span>`;
  }
}

async function loadModels() {
  try {
    const models = await api('GET', '/api/models');
    populateModels(models, null);
  } catch (e) {
    console.warn('Models fetch failed', e);
  }
}

function populateModels(models, selectedModel) {
  state.models = models;
  modelSelect.innerHTML = '';
  models.forEach(m => {
    const opt = document.createElement('option');
    opt.value = m;
    opt.textContent = m;
    if (m === selectedModel) opt.selected = true;
    modelSelect.appendChild(opt);
  });
  if (!models.length) {
    const opt = document.createElement('option');
    opt.textContent = 'No models';
    modelSelect.appendChild(opt);
  }
}

// ── Conversation history ───────────────────────────────────────────────────
async function loadHistory() {
  try {
    const history = await api('GET', '/api/history');
    state.conversations = history;
    renderHistory();
  } catch (e) {
    console.warn('History fetch failed', e);
  }
}

function renderHistory() {
  historyList.innerHTML = '';
  if (!state.conversations.length) {
    historyList.innerHTML = '<div class="history-empty">No conversations yet</div>';
    return;
  }
  // Newest first
  const sorted = [...state.conversations].sort((a, b) =>
    new Date(b.updated_at || b.created_at) - new Date(a.updated_at || a.created_at)
  );
  sorted.forEach(conv => {
    const item = document.createElement('div');
    item.className = 'history-item' + (conv.id === state.activeId ? ' active' : '');
    item.dataset.id = conv.id;

    const title = document.createElement('span');
    title.className = 'history-item-title';
    title.textContent = conv.title || 'Untitled';
    title.title = conv.title || 'Untitled';

    const del = document.createElement('button');
    del.className = 'history-delete-btn';
    del.textContent = '✕';
    del.title = 'Delete conversation';
    del.addEventListener('click', e => {
      e.stopPropagation();
      deleteConversation(conv.id);
    });

    item.appendChild(title);
    item.appendChild(del);
    item.addEventListener('click', () => loadConversation(conv.id));
    historyList.appendChild(item);
  });
}

async function deleteConversation(id) {
  try {
    await api('DELETE', `/api/history/${id}`);
    if (state.activeId === id) {
      state.activeId = null;
      clearMessages();
    }
    state.conversations = state.conversations.filter(c => c.id !== id);
    renderHistory();
  } catch (e) {
    console.warn('Delete failed', e);
  }
}

async function startNewChat() {
  try {
    const { id } = await api('POST', '/api/history');
    await loadHistory();
    await loadConversation(id);
  } catch (e) {
    // Allow starting fresh without a persisted ID
    state.activeId = null;
    clearMessages();
    renderHistory();
  }
}

async function loadConversation(id) {
  state.activeId = id;
  renderHistory(); // refresh active highlight
  clearMessages();
  try {
    const messages = await api('GET', `/api/history/${id}`);
    messages.forEach(msg => appendMessage(msg.role, msg.content, msg.sources));
  } catch (e) {
    console.warn('Load conversation failed', e);
  }
  scrollToBottom();
}

// ── Message rendering ──────────────────────────────────────────────────────
function clearMessages() {
  messageArea.innerHTML = '';
  showEmptyState(true);
}

function showEmptyState(show) {
  emptyState.style.display = show ? 'flex' : 'none';
  messageArea.style.display = show ? 'none' : 'flex';
}

function appendMessage(role, content, sources) {
  showEmptyState(false);

  const row = document.createElement('div');
  row.className = `message-row ${role}`;

  const bubble = document.createElement('div');
  bubble.className = 'bubble';

  if (role === 'assistant') {
    const mdDiv = document.createElement('div');
    mdDiv.className = 'md';
    mdDiv.innerHTML = renderMarkdown(content || '');

    // Wire up code copy buttons
    mdDiv.querySelectorAll('.code-copy-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const code = decodeURIComponent(btn.dataset.code || '');
        copyText(code, btn);
      });
    });

    bubble.appendChild(mdDiv);

    // Footer: copy + sources
    const footer = document.createElement('div');
    footer.className = 'bubble-footer';

    const actions = document.createElement('div');
    actions.className = 'bubble-actions';

    const copyBtn = document.createElement('button');
    copyBtn.className = 'bubble-copy-btn';
    copyBtn.textContent = 'Copy';
    copyBtn.addEventListener('click', () => copyText(content, copyBtn));
    actions.appendChild(copyBtn);
    footer.appendChild(actions);

    if (sources && sources.length) {
      const sourceEl = buildSourcesEl(sources);
      footer.appendChild(sourceEl);
    }

    bubble.appendChild(footer);

  } else {
    // User bubble — plain escaped text
    const p = document.createElement('p');
    p.textContent = content;
    bubble.appendChild(p);
  }

  row.appendChild(bubble);
  messageArea.appendChild(row);
  return bubble;
}

function buildSourcesEl(sources) {
  const wrapper = document.createElement('div');

  const toggle = document.createElement('button');
  toggle.className = 'sources-toggle';
  toggle.innerHTML = `<i class="chevron">›</i> ${sources.length} source${sources.length !== 1 ? 's' : ''}`;

  const list = document.createElement('div');
  list.className = 'sources-list hidden';

  sources.forEach(src => {
    const item = document.createElement('div');
    item.className = 'source-item';

    const fname = (src.doc_path || '').split('/').pop() || src.doc_path || '?';
    const score = src.score != null ? (src.score * 100).toFixed(0) + '%' : '';

    item.innerHTML =
      `<span class="source-filename">${escapeHtml(fname)}</span>` +
      `<span>chunk #${src.chunk_index ?? '?'}</span>` +
      (score ? `<span class="source-score">${score}</span>` : '');

    list.appendChild(item);
  });

  toggle.addEventListener('click', () => {
    const open = list.classList.toggle('hidden');
    toggle.classList.toggle('open', !open);
  });

  wrapper.appendChild(toggle);
  wrapper.appendChild(list);
  return wrapper;
}

// Live streaming bubble — returns mdDiv and bubble for in-place updates
function createStreamingBubble() {
  showEmptyState(false);

  const row = document.createElement('div');
  row.className = 'message-row assistant';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';

  const mdDiv = document.createElement('div');
  mdDiv.className = 'md';
  // Show typing indicator while waiting for first token
  mdDiv.innerHTML =
    '<div class="typing-indicator"><span></span><span></span><span></span></div>';

  bubble.appendChild(mdDiv);
  row.appendChild(bubble);
  messageArea.appendChild(row);
  scrollToBottom();

  return { row, bubble, mdDiv };
}

function finalizeStreamingBubble(bubble, mdDiv, fullText, sources) {
  mdDiv.innerHTML = renderMarkdown(fullText);

  mdDiv.querySelectorAll('.code-copy-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const code = decodeURIComponent(btn.dataset.code || '');
      copyText(code, btn);
    });
  });

  const footer = document.createElement('div');
  footer.className = 'bubble-footer';

  const actions = document.createElement('div');
  actions.className = 'bubble-actions';

  const copyBtn = document.createElement('button');
  copyBtn.className = 'bubble-copy-btn';
  copyBtn.textContent = 'Copy';
  copyBtn.addEventListener('click', () => copyText(fullText, copyBtn));
  actions.appendChild(copyBtn);
  footer.appendChild(actions);

  if (sources && sources.length) {
    footer.appendChild(buildSourcesEl(sources));
  }

  bubble.appendChild(footer);
}

// ── Sending / SSE-via-fetch streaming ─────────────────────────────────────
async function sendMessage() {
  const text = messageInput.value.trim();
  if (!text || state.streaming) return;

  messageInput.value = '';
  autoResizeTextarea();

  // Append user bubble immediately
  appendMessage('user', text, null);
  scrollToBottom();

  state.streaming = true;
  sendBtn.disabled = true;

  const { row, bubble, mdDiv } = createStreamingBubble();

  let fullText = '';
  let sources  = [];

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        conversation_id: state.activeId || null,
        message: text,
      }),
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Parse SSE frames: split on blank lines
      const frames = buffer.split(/\n\n/);
      buffer = frames.pop(); // keep partial frame

      for (const frame of frames) {
        // Each frame may have multiple lines; find the data: line
        for (const line of frame.split('\n')) {
          if (!line.startsWith('data:')) continue;
          const jsonStr = line.slice(5).trim();
          if (!jsonStr) continue;

          let evt;
          try { evt = JSON.parse(jsonStr); } catch { continue; }

          if (evt.type === 'sources') {
            sources = evt.sources || [];
          } else if (evt.type === 'token') {
            if (fullText === '') {
              // Clear typing indicator on first token
              mdDiv.innerHTML = '';
            }
            fullText += evt.text || '';
            // Live render — lightweight: just update innerHTML
            mdDiv.innerHTML = renderMarkdown(fullText);
            scrollToBottom();
          } else if (evt.type === 'error') {
            mdDiv.innerHTML = `<p style="color:var(--color-error-text)">${escapeHtml(evt.message || 'Unknown error')}</p>`;
          } else if (evt.type === 'done') {
            if (evt.conversation_id) {
              state.activeId = evt.conversation_id;
            }
            finalizeStreamingBubble(bubble, mdDiv, fullText, sources);
            await loadHistory();
          }
        }
      }
    }
  } catch (err) {
    mdDiv.innerHTML = `<p style="color:var(--color-error-text)">Error: ${escapeHtml(err.message)}</p>`;
  } finally {
    state.streaming = false;
    sendBtn.disabled = false;
    scrollToBottom();
  }
}

// ── File upload (drag-drop + browse) ───────────────────────────────────────
async function uploadFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;

  const fd = new FormData();
  files.forEach(f => fd.append('files', f, f.name));

  // Show the glowing banner immediately; the poller takes over with live
  // per-file progress once the server starts ingesting.
  state.uploading = true;
  ingestBanner.classList.add('visible');
  ingestBanner.setAttribute('aria-hidden', 'false');
  if (ingestText) {
    ingestText.textContent =
      `Uploading ${files.length} file${files.length !== 1 ? 's' : ''}…`;
  }

  try {
    const res = await fetch('/api/upload', { method: 'POST', body: fd });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.index) updateIndexStatus(data.index);

    const saved = (data.saved || []).length;
    const skipped = (data.skipped || []).length;
    let msg = `Uploaded ${saved} file${saved !== 1 ? 's' : ''}`;
    if (skipped) msg += ` · ${skipped} skipped (unsupported)`;
    showToast(msg);

    await loadStatus();      // refresh index count + ollama banner
  } catch (e) {
    console.warn('Upload failed', e);
    showToast('Upload failed');
  } finally {
    state.uploading = false;   // poller hides the banner once ingest finishes
  }
}

// ── Ingest progress banner (polled) ────────────────────────────────────────
let _wasIngesting = false;

function showIngestBanner(s) {
  let msg;
  if (s && s.total > 0) {
    msg = `Please wait — ingesting file ${s.current} of ${s.total}`;
    if (s.file) msg += `: ${s.file}`;
  } else {
    msg = 'Please wait — ingesting files…';
  }
  if (ingestText) ingestText.textContent = msg;
  ingestBanner.classList.add('visible');
  ingestBanner.setAttribute('aria-hidden', 'false');
}

function hideIngestBanner() {
  ingestBanner.classList.remove('visible');
  ingestBanner.setAttribute('aria-hidden', 'true');
}

async function pollIngestStatus() {
  try {
    const s = await api('GET', '/api/ingest-status');
    if (s.active) {
      _wasIngesting = true;
      showIngestBanner(s);
    } else if (_wasIngesting) {
      // Transition active -> idle: hide and refresh counts.
      _wasIngesting = false;
      hideIngestBanner();
      loadStatus();
    } else if (!state.uploading) {
      hideIngestBanner();
    }
  } catch (e) {
    /* ignore transient poll errors */
  }
}

// ── Indexed-files modal ────────────────────────────────────────────────────
async function openDocsModal() {
  docsModal.classList.add('visible');
  docsModal.setAttribute('aria-hidden', 'false');
  docsModalBody.innerHTML = '<div class="docs-loading">Loading…</div>';
  try {
    const docs = await api('GET', '/api/documents');
    renderDocs(docs);
  } catch (e) {
    docsModalBody.innerHTML = '<div class="docs-empty">Could not load the file list.</div>';
  }
}

let _docsDirty = false;   // a file was deleted while the modal was open

function closeDocsModal() {
  docsModal.classList.remove('visible');
  docsModal.setAttribute('aria-hidden', 'true');
  // Re-index on close to reconcile after any deletions.
  if (_docsDirty) {
    _docsDirty = false;
    api('POST', '/api/reindex').then(() => loadStatus()).catch(() => {});
  }
}

async function deleteDoc(path, liEl) {
  try {
    const res = await fetch('/api/documents/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.index) updateIndexStatus(data.index);
    if (liEl) liEl.remove();
    _docsDirty = true;
    showToast('File deleted');
    if (!docsModalBody.querySelector('.docs-item')) {
      docsModalBody.innerHTML =
        '<div class="docs-empty">No files indexed.<br>Drag &amp; drop files to add some.</div>';
    }
  } catch (e) {
    console.warn('Delete failed', e);
    showToast('Delete failed');
  }
}

// ── Help / info modal ──────────────────────────────────────────────────────
function openHelpModal() {
  helpModal.classList.add('visible');
  helpModal.setAttribute('aria-hidden', 'false');
}

function closeHelpModal() {
  helpModal.classList.remove('visible');
  helpModal.setAttribute('aria-hidden', 'true');
}

function renderDocs(docs) {
  if (!docs || !docs.length) {
    docsModalBody.innerHTML =
      '<div class="docs-empty">No files indexed yet.<br>Drag &amp; drop files to add some.</div>';
    return;
  }
  const totalChunks = docs.reduce((a, d) => a + (d.n_chunks || 0), 0);
  const parts = [
    `<div class="docs-summary">${docs.length} file${docs.length !== 1 ? 's' : ''} · ${totalChunks} chunks</div>`,
    '<ul class="docs-list">',
  ];
  docs.forEach(d => {
    const name = escapeHtml(d.name || d.path || '?');
    const chunks = d.n_chunks ?? 0;
    let when = '';
    if (d.indexed_at) {
      const dt = new Date(d.indexed_at);
      when = isNaN(dt) ? '' : dt.toLocaleString();
    }
    parts.push(
      '<li class="docs-item">' +
        '<div class="docs-item-main">' +
          `<span class="docs-item-name" title="${escapeHtml(d.path || '')}">${name}</span>` +
          `<span class="docs-item-meta">${chunks} chunk${chunks !== 1 ? 's' : ''}` +
          (when ? ` · ${escapeHtml(when)}` : '') +
          '</span>' +
        '</div>' +
        `<button class="docs-delete-btn" title="Delete this file" ` +
          `data-path="${escapeHtml(d.path || '')}" aria-label="Delete ${name}">🗑</button>` +
      '</li>'
    );
  });
  parts.push('</ul>');
  docsModalBody.innerHTML = parts.join('');

  docsModalBody.querySelectorAll('.docs-delete-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      deleteDoc(btn.dataset.path, btn.closest('.docs-item'));
    });
  });
}

function wireUpload() {
  const openPicker = () => fileInput && fileInput.click();
  const sidebarBtn = $('upload-btn');
  const emptyBtn = $('empty-upload-btn');
  if (sidebarBtn) sidebarBtn.addEventListener('click', openPicker);
  if (emptyBtn) emptyBtn.addEventListener('click', openPicker);

  if (fileInput) {
    fileInput.addEventListener('change', () => {
      uploadFiles(fileInput.files);
      fileInput.value = '';   // allow re-selecting the same file later
    });
  }

  // Full-window drag-and-drop. dragDepth handles nested enter/leave events.
  let dragDepth = 0;
  const hasFiles = e =>
    e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');

  window.addEventListener('dragenter', e => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth++;
    dropOverlay.classList.add('visible');
  });
  window.addEventListener('dragover', e => {
    if (hasFiles(e)) e.preventDefault();   // required to allow a drop
  });
  window.addEventListener('dragleave', e => {
    if (!hasFiles(e)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) dropOverlay.classList.remove('visible');
  });
  window.addEventListener('drop', e => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth = 0;
    dropOverlay.classList.remove('visible');
    uploadFiles(e.dataTransfer.files);
  });
}

// ── Utilities ──────────────────────────────────────────────────────────────
function scrollToBottom() {
  messageArea.scrollTop = messageArea.scrollHeight;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}

function copyText(text, btn) {
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = orig; }, 1500);
  }).catch(() => {
    // Fallback
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = orig; }, 1500);
  });
}

function showToast(msg) {
  toast.textContent = msg;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 3000);
}

function autoResizeTextarea() {
  messageInput.style.height = 'auto';
  messageInput.style.height = Math.min(messageInput.scrollHeight, 200) + 'px';
}

// ── Event wiring ──────────────────────────────────────────────────────────
function wireEvents() {
  // Theme toggle
  themeToggle.addEventListener('click', toggleTheme);

  // New chat
  newChatBtn.addEventListener('click', startNewChat);

  // Model selection
  modelSelect.addEventListener('change', async () => {
    try {
      await api('POST', '/api/models/select', { model: modelSelect.value });
    } catch (e) {
      console.warn('Model select failed', e);
    }
  });

  // Reindex
  reindexBtn.addEventListener('click', async () => {
    reindexBtn.disabled = true;
    reindexBtn.textContent = '…';
    try {
      const res = await api('POST', '/api/reindex');
      updateIndexStatus(res);
      showToast(`Re-indexed: ${res.documents ?? '?'} docs, ${res.chunks ?? '?'} chunks`);
    } catch (e) {
      showToast('Re-index failed');
    } finally {
      reindexBtn.disabled = false;
      reindexBtn.textContent = 'Re-index';
    }
  });

  // Ollama banner
  bannerRetry.addEventListener('click', () => {
    bannerDismiss.click(); // hide banner
    loadStatus();
  });
  bannerDismiss.addEventListener('click', () => {
    ollamaBanner.classList.remove('visible');
  });

  // Composer
  messageInput.addEventListener('input', autoResizeTextarea);
  messageInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  sendBtn.addEventListener('click', sendMessage);

  // File upload (drag-drop + browse)
  wireUpload();

  // Indexed-files modal (Index button)
  const indexBtn = $('index-label-btn');
  if (indexBtn) indexBtn.addEventListener('click', openDocsModal);
  const docsClose = $('docs-modal-close');
  if (docsClose) docsClose.addEventListener('click', closeDocsModal);
  if (docsModal) {
    docsModal.addEventListener('click', e => {
      if (e.target === docsModal) closeDocsModal();   // click backdrop to close
    });
  }

  // Help / info modal (? button)
  const helpBtn = $('help-btn');
  if (helpBtn) helpBtn.addEventListener('click', openHelpModal);
  const helpClose = $('help-modal-close');
  if (helpClose) helpClose.addEventListener('click', closeHelpModal);
  if (helpModal) {
    helpModal.addEventListener('click', e => {
      if (e.target === helpModal) closeHelpModal();
    });
  }

  // Esc closes whichever modal is open
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if (docsModal && docsModal.classList.contains('visible')) closeDocsModal();
    if (helpModal && helpModal.classList.contains('visible')) closeHelpModal();
  });
}

// ── Init ───────────────────────────────────────────────────────────────────
async function init() {
  initTheme();
  wireEvents();
  showEmptyState(true);

  await Promise.all([
    loadStatus(),
    loadHistory(),
  ]);

  // Poll ingest progress so the glowing banner reflects uploads AND folder drops.
  setInterval(pollIngestStatus, 600);
}

document.addEventListener('DOMContentLoaded', init);
