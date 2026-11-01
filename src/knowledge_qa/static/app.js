const messages = document.getElementById('messages');
const input = document.getElementById('question-input');
const sendButton = document.getElementById('send-button');
const chatScroll = document.getElementById('chat-scroll');
const welcome = document.getElementById('welcome');
const state = { history: [], busy: false };

const statusText = {
  completed: '已回答', needs_clarification: '需要确认', insufficient_information: '资料不足',
  tool_error: '工具失败', model_error: '模型错误', max_steps_exceeded: '超出步数上限'
};
const actionText = { search_documents: '检索资料', read_document: '读取原文', finish: '完成回答' };

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function scrollBottom() { requestAnimationFrame(() => { chatScroll.scrollTop = chatScroll.scrollHeight; }); }

function addUser(question) {
  const row = element('div', 'message user');
  const body = element('div', 'message-body', question);
  row.appendChild(body); messages.appendChild(row); scrollBottom();
}

function addLoading() {
  const row = document.getElementById('loading-template').content.firstElementChild.cloneNode(true);
  row.id = 'loading-row'; messages.appendChild(row); scrollBottom();
}

function citationCard(citation) {
  const card = element('div', 'citation');
  const meta = element('div', 'citation-meta');
  meta.appendChild(element('b', '', citation.doc_id));
  meta.appendChild(element('span', '', `版本 ${citation.version_date}`));
  card.appendChild(meta); card.appendChild(element('blockquote', '', citation.quote));
  return card;
}

function buildTrace(trace) {
  const strip = element('div', 'trace-strip');
  trace.forEach((step, index) => {
    if (index) strip.appendChild(element('span', 'trace-arrow', '→'));
    strip.appendChild(element('span', 'trace-chip', actionText[step.action] || step.action));
  });
  const details = element('details', 'trace-details');
  details.appendChild(element('summary', '', `查看完整执行轨迹 · ${trace.length} 步`));
  trace.forEach(step => {
    const item = element('div', 'trace-step');
    item.appendChild(element('strong', '', `${step.step}. ${actionText[step.action] || step.action}`));
    if (step.arguments && Object.keys(step.arguments).length) item.appendChild(element('code', '', JSON.stringify(step.arguments, null, 2)));
    item.appendChild(element('p', '', step.observation.summary || step.rationale || ''));
    details.appendChild(item);
  });
  return [strip, details];
}

function addAssistant(record) {
  const result = record.result;
  const row = element('div', 'message assistant');
  row.appendChild(element('div', 'avatar', 'K'));
  const body = element('div', 'message-body');
  const card = element('article', 'answer-card');
  const head = element('div', 'answer-head');
  head.appendChild(element('strong', '', 'Agent 回答'));
  head.appendChild(element('span', `status-badge status-${result.status}`, statusText[result.status] || result.status));
  card.appendChild(head); card.appendChild(element('div', 'answer-text', result.answer));
  if (result.citations.length) {
    const section = element('section', 'answer-section'); section.appendChild(element('h3', '', `原文引用 · ${result.citations.length}`));
    result.citations.forEach(citation => section.appendChild(citationCard(citation))); card.appendChild(section);
  }
  if (result.gaps.length) {
    const section = element('section', 'answer-section'); section.appendChild(element('h3', '', '资料缺口'));
    const list = element('ul', 'gap-list'); result.gaps.forEach(gap => list.appendChild(element('li', '', gap)));
    section.appendChild(list); card.appendChild(section);
  }
  if (result.clarifying_question) card.appendChild(element('div', 'clarify', result.clarifying_question));
  body.appendChild(card); buildTrace(record.trace).forEach(node => body.appendChild(node));
  row.appendChild(body); messages.appendChild(row); scrollBottom();
}

function addError(message) {
  const row = element('div', 'message assistant'); row.appendChild(element('div', 'avatar', 'K'));
  const body = element('div', 'message-body'); const card = element('div', 'answer-card');
  card.appendChild(element('div', 'answer-text', `请求失败：${message}`)); body.appendChild(card); row.appendChild(body); messages.appendChild(row);
}

function setBusy(busy) {
  state.busy = busy; sendButton.disabled = busy; input.disabled = busy;
  document.querySelectorAll('[data-question]').forEach(button => { button.disabled = busy; });
}

async function ask(question) {
  question = question.trim(); if (!question || state.busy) return;
  if (welcome) welcome.remove();
  const previousHistory = state.history.slice();
  state.history.push({ role: 'user', content: question });
  addUser(question); input.value = ''; input.style.height = 'auto'; setBusy(true); addLoading();
  try {
    const response = await fetch('/api/ask', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, history: previousHistory })
    });
    const data = await response.json(); if (!response.ok) throw new Error(data.error || '未知错误');
    document.getElementById('loading-row')?.remove(); addAssistant(data);
    state.history.push({ role: 'assistant', content: data.result.answer });
  } catch (error) {
    document.getElementById('loading-row')?.remove(); addError(error.message);
  } finally { setBusy(false); input.focus(); scrollBottom(); }
}

async function loadConfig() {
  try {
    const response = await fetch('/api/config'); const config = await response.json();
    document.getElementById('model-name').textContent = config.model_mode === 'offline_mock' ? '离线流程 Mock' : `真实模型 · ${config.model}`;
    if (config.model_mode === 'offline_mock') document.getElementById('model-pill').classList.add('mock');
    document.getElementById('step-limit').textContent = `最多 ${config.max_steps} 步`;
    document.getElementById('doc-count').textContent = config.documents.length;
    const list = document.getElementById('document-list');
    config.documents.forEach(doc => {
      const item = element('div', 'document-item'); item.appendChild(element('b', '', doc.doc_id));
      item.appendChild(element('span', '', doc.title)); item.title = `${doc.title} · ${doc.version_date}`; list.appendChild(item);
    });
  } catch (error) { document.getElementById('model-name').textContent = '服务连接失败'; }
}

sendButton.addEventListener('click', () => ask(input.value));
input.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); ask(input.value); } });
input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 110)}px`; });
document.querySelectorAll('[data-question]').forEach(button => button.addEventListener('click', () => ask(button.dataset.question)));
document.getElementById('new-chat').addEventListener('click', () => {
  state.history = []; messages.innerHTML = ''; input.value = ''; if (!document.getElementById('welcome')) location.reload();
});
loadConfig(); input.focus();
