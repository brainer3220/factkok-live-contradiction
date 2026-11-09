import {
  applyEvent, beginSubmission, createRun, crossedSegments, filterPeople,
  finishSubmission, parseConfig, parseEvent, parsePeople, parseTranscript,
  pendingSegments, segmentPhase, sourceUrl,
} from './model.mjs';

const ids = [
  'people-view', 'people-main', 'people-search', 'clear-search', 'people-grid',
  'people-message', 'people-list-heading', 'people-status', 'detail-view',
  'detail-title', 'detail-portrait', 'detail-message', 'work-content', 'video',
  'back-to-people', 'restart-run', 'now-who', 'now-line', 'now-meta',
  'current-source', 'topic-nav', 'processing-status', 'comparison-result',
  'statement-context', 'store-info', 'run-notice', 'event-log', 'media-error',
  'transcript-list', 'photo-credits', 'photo-credit-status',
];
const dom = Object.fromEntries(ids.map(id => [id, document.getElementById(id)]));
const state = {
  view: 'people',
  people: {phase: 'loading', items: [], query: '', composing: false},
  listScroll: 0,
  lastPersonId: null,
  generation: 0,
  detail: null,
};
const PHASE_LABELS = {
  sending: '발언 전송 중', queued: '비교 순서 대기 중',
  searching: '과거 발언 검색 중', judging: 'OpenAI 모델 비교 중',
};

class HttpError extends Error {
  constructor(status) {
    super(`서버 응답 HTTP ${status}`);
    this.status = status;
  }
}

function element(tag, className = '', text = null) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== null) node.textContent = text;
  return node;
}

function button(text, action, className = 'retry') {
  const node = element('button', className, text);
  node.type = 'button';
  node.addEventListener('click', action);
  return node;
}

function formatTime(seconds) {
  const value = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
  return `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, '0')}`;
}

async function requestJson(path, {signal, body} = {}) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    cache: 'no-store',
    signal,
    headers: body === undefined ? {} : {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new HttpError(response.status);
  return response.json();
}

function isCurrent(detail) {
  return state.detail === detail && detail.generation === state.generation;
}

function portrait(person, className = '') {
  const frame = element('span', `people-portrait ${className}`.trim());
  frame.setAttribute('aria-hidden', 'true');
  const image = element('img');
  image.src = person.portrait;
  image.alt = '';
  image.loading = 'lazy';
  image.decoding = 'async';
  image.addEventListener('error', () => { frame.textContent = '사진 없음'; }, {once: true});
  frame.append(image);
  return frame;
}

function externalLink(text, url, className = '') {
  const safeUrl = sourceUrl(url);
  if (!safeUrl) return element('span', className, '원문 주소를 확인할 수 없습니다');
  const link = element('a', className, text);
  link.href = safeUrl;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  return link;
}

function renderPeople() {
  const people = filterPeople(state.people.items, state.people.query);
  dom['people-grid'].replaceChildren();
  dom['people-message'].replaceChildren();
  dom['people-message'].hidden = true;
  dom['clear-search'].hidden = !dom['people-search'].value;
  dom['people-list-heading'].textContent = state.people.query.trim() ? '검색 결과' : '전체 인물';
  if (state.people.phase !== 'ready') {
    dom['people-message'].hidden = false;
    dom['people-message'].append(element('h2', '', state.people.phase === 'loading' ?
      '인물 목록 불러오는 중' : '인물 목록에 연결하지 못했습니다'));
    if (state.people.phase === 'failed') {
      dom['people-message'].append(element('p', '', '서버 연결을 확인하고 다시 시도해 주세요.'),
          button('다시 불러오기', loadPeople));
    }
    dom['people-status'].textContent = dom['people-message'].textContent;
    return;
  }
  dom['people-status'].textContent = `${people.length}명의 인물이 표시됩니다.`;
  for (const person of people) {
    const item = element('li');
    const control = button('', () => openPerson(person), 'b-person');
    control.dataset.personId = person.id;
    control.setAttribute('aria-label', `${person.name}, ${person.status === 'ready' ? '기록 보기' : '자료 연결 전'}`);
    const photo = element('span', 'b-photo-frame');
    const arrow = element('span', 'b-photo-arrow', '↗');
    arrow.setAttribute('aria-hidden', 'true');
    photo.append(portrait(person, 'b-portrait'), arrow);
    const caption = element('span', 'b-person-caption');
    caption.append(element('span', 'b-person-name', person.name),
        element('span', `b-person-status${person.status === 'ready' ? ' is-available' : ''}`,
            person.status === 'ready' ? '기록 보기 ↗' : '자료 연결 전'));
    control.append(photo, caption);
    item.append(control);
    dom['people-grid'].append(item);
  }
  if (!people.length) {
    dom['people-message'].hidden = false;
    dom['people-message'].append(element('h2', '', state.people.items.length ? '찾는 인물이 없습니다' : '연결된 인물이 없습니다'),
        element('p', '', state.people.items.length ? '이름을 다시 확인해 주세요.' : '서버의 인물 자료를 확인해 주세요.'));
    if (state.people.items.length) dom['people-message'].append(button('전체 인물 보기', clearSearch));
  }
}

async function loadPeople() {
  state.people.phase = 'loading';
  renderPeople();
  try {
    state.people.items = parsePeople(await requestJson('/people'));
    state.people.phase = 'ready';
    renderPeople();
    const personId = new URLSearchParams(location.hash.slice(1)).get('person');
    const person = state.people.items.find(item => item.id === personId);
    if (person) await openPerson(person, false);
  } catch {
    state.people.phase = 'failed';
    renderPeople();
  }
}

function clearSearch() {
  dom['people-search'].value = '';
  state.people.query = '';
  renderPeople();
  dom['people-search'].focus();
}

function stopDetail() {
  state.generation += 1;
  const detail = state.detail;
  if (detail) {
    detail.controller.abort();
    detail.sessionController?.abort();
    detail.run?.events?.close();
    if (detail.captionUrl) URL.revokeObjectURL(detail.captionUrl);
  }
  dom.video.pause();
  dom.video.removeAttribute('src');
  dom.video.querySelectorAll('track').forEach(track => track.remove());
  dom.video.load();
  state.detail = null;
}

function showPeople() {
  stopDetail();
  state.view = 'people';
  document.title = '인물 · 팩트콕';
  dom['detail-view'].hidden = true;
  dom['people-view'].hidden = false;
  requestAnimationFrame(() => {
    window.scrollTo(0, state.listScroll);
    const control = [...dom['people-grid'].querySelectorAll('button')]
        .find(node => node.dataset.personId === state.lastPersonId);
    (control ?? dom['people-search']).focus({preventScroll: true});
  });
}

function backToPeople() {
  if (!state.detail) return;
  if (history.state?.factkokDetail) {
    stopDetail();
    history.back();
  } else {
    history.replaceState(null, '', `${location.pathname}${location.search}`);
    showPeople();
  }
}

function detailMessage(title, message, action = null) {
  dom['detail-message'].hidden = false;
  dom['work-content'].hidden = true;
  dom['detail-message'].replaceChildren(element('h2', '', title), element('p', '', message));
  if (action) dom['detail-message'].append(action);
}

async function openPerson(person, pushHistory = true) {
  if (state.view === 'people') state.listScroll = window.scrollY;
  state.lastPersonId = person.id;
  stopDetail();
  state.view = 'detail';
  const detail = {
    generation: state.generation,
    person,
    controller: new AbortController(),
    phase: 'loading',
    config: null,
    segments: [],
    selectedStatementId: null,
    activeSegmentIndex: null,
    run: null,
    runGeneration: 0,
    sessionPhase: 'idle',
    sessionError: '',
    sessionPromise: null,
    sessionController: null,
    lastPlaybackTime: null,
    seeking: false,
    captionUrl: null,
  };
  state.detail = detail;
  if (pushHistory) history.pushState({factkokDetail: true}, '', `#person=${encodeURIComponent(person.id)}`);
  document.title = `${person.name} · 팩트콕`;
  dom['people-view'].hidden = true;
  dom['detail-view'].hidden = false;
  dom['detail-title'].textContent = person.name;
  dom['detail-portrait'].replaceChildren(portrait(person));
  dom['event-log'].replaceChildren();
  dom['media-error'].hidden = true;
  dom['run-notice'].replaceChildren();
  dom['processing-status'].textContent = '';
  detailMessage('발언 자료 불러오는 중', `${person.name}의 영상과 자막을 확인하고 있습니다.`);
  window.scrollTo(0, 0);
  dom['detail-title'].focus({preventScroll: true});
  if (person.status === 'unavailable') {
    detail.phase = 'unavailable';
    detailMessage('자료 연결 전', '이 인물의 발언 자료를 아직 연결하지 않았습니다.',
        button('다른 인물 보기', backToPeople));
    dom['detail-message'].prepend(portrait(person));
    return;
  }
  try {
    const query = new URLSearchParams({person_id: person.id});
    const [config, transcript] = await Promise.all([
      requestJson(`/config?${query}`, {signal: detail.controller.signal}),
      requestJson(`/transcript.json?${query}`, {signal: detail.controller.signal}),
    ]);
    if (!isCurrent(detail)) return;
    detail.config = parseConfig(config, person.id);
    detail.segments = parseTranscript(transcript, detail.config.statements);
    if (!detail.config.statements.length) {
      detail.phase = 'empty';
      detailMessage('연결된 발언 기록 없음', '이 인물의 비교 가능한 발언을 아직 연결하지 않았습니다.',
          button('다른 인물 보기', backToPeople));
      return;
    }
    detail.phase = 'ready';
    detail.selectedStatementId = detail.config.statements[0].id;
    dom['detail-message'].hidden = true;
    dom['work-content'].hidden = false;
    dom.video.src = `/video?${query}`;
    addCaptions(detail);
    renderTopics(detail);
    renderTranscript(detail);
    renderQuote(detail);
    renderComparison(detail);
    renderRunStatus(detail);
    dom['store-info'].textContent = detail.config.storeLabel ? `검색 저장소 ${detail.config.storeLabel}` : '';
  } catch (error) {
    if (!isCurrent(detail) || error.name === 'AbortError') return;
    detail.phase = 'failed';
    const unavailable = error instanceof HttpError && error.status === 409;
    detailMessage(unavailable ? '자료 연결 전' : '발언 자료에 연결하지 못했습니다',
        unavailable ? '이 인물의 발언 자료가 준비되지 않았습니다.' : '서버 연결을 확인하고 다시 시도해 주세요.',
        button(unavailable ? '다른 인물 보기' : '다시 불러오기',
            unavailable ? backToPeople : () => openPerson(person, false)));
  }
}

function selectedStatement(detail) {
  return detail.config.statements.find(statement => statement.id === detail.selectedStatementId);
}

function selectStatement(detail, statementId, seek = false) {
  if (!isCurrent(detail)) return;
  detail.selectedStatementId = statementId;
  const statement = selectedStatement(detail);
  if (seek) {
    const segment = detail.segments.find(item => item.index === statement.current.segmentIndex);
    detail.lastPlaybackTime = segment.start;
    dom.video.currentTime = segment.start;
    renderQuote(detail, segment);
  }
  for (const control of dom['topic-nav'].querySelectorAll('button')) {
    control.setAttribute('aria-pressed', String(control.dataset.statementId === statementId));
  }
  renderComparison(detail);
  renderRunStatus(detail);
}

function renderTopics(detail) {
  dom['topic-nav'].replaceChildren();
  for (const statement of detail.config.statements) {
    const control = button(statement.topic, () => selectStatement(detail, statement.id, true), '');
    control.dataset.statementId = statement.id;
    control.setAttribute('aria-pressed', String(statement.id === detail.selectedStatementId));
    dom['topic-nav'].append(control);
  }
}

function renderQuote(detail, segment = null) {
  const statement = segment ? detail.config.statements.find(item => item.current.segmentIndex === segment.index) : selectedStatement(detail);
  if (segment && !statement) return;
  dom['now-who'].textContent = segment ? segment.speaker : `${detail.config.holder} · 현재 발언`;
  dom['now-line'].textContent = `“${segment?.text ?? statement.current.text}”`;
  dom['now-meta'].textContent = statement ? statement.current.date : '시연 편집 구간';
  dom['current-source'].hidden = !statement?.current.sourceUrl;
  if (statement?.current.sourceUrl) dom['current-source'].href = statement.current.sourceUrl;
  else dom['current-source'].removeAttribute('href');
}

function renderTranscript(detail) {
  dom['transcript-list'].replaceChildren();
  for (const segment of detail.segments) {
    const item = element('li');
    const control = button('', () => {
      if (!isCurrent(detail)) return;
      detail.lastPlaybackTime = segment.start;
      dom.video.currentTime = segment.start;
      renderQuote(detail, segment);
    }, '');
    control.append(element('time', '', formatTime(segment.start)), element('span', '', `${segment.speaker} · ${segment.text}`));
    item.append(control);
    dom['transcript-list'].append(item);
  }
}

function addCaptions(detail) {
  const timestamp = seconds => {
    const milliseconds = Math.round(seconds * 1000);
    return `${String(Math.floor(milliseconds / 3600000)).padStart(2, '0')}:${String(Math.floor(milliseconds / 60000) % 60).padStart(2, '0')}:${String(Math.floor(milliseconds / 1000) % 60).padStart(2, '0')}.${String(milliseconds % 1000).padStart(3, '0')}`;
  };
  const text = 'WEBVTT\n\n' + detail.segments.map(segment =>
    `${segment.index}\n${timestamp(segment.start)} --> ${timestamp(segment.end)}\n${segment.speaker}: ${segment.text.replace(/</g, '&lt;')}\n`).join('\n');
  detail.captionUrl = URL.createObjectURL(new Blob([text], {type: 'text/vtt'}));
  const track = element('track');
  track.kind = 'captions';
  track.label = '한국어';
  track.srclang = 'ko';
  track.src = detail.captionUrl;
  track.default = true;
  dom.video.append(track);
}

function statePanel(title, message, action = null) {
  const panel = element('div', 'state-panel');
  panel.append(element('h2', '', title), element('p', '', message));
  if (action) panel.append(action);
  return panel;
}

function renderComparison(detail) {
  const statement = selectedStatement(detail);
  const run = detail.run;
  const phase = segmentPhase(run, statement.current.segmentIndex);
  const card = run?.cards.get(statement.current.segmentIndex);
  dom['statement-context'].textContent = statement.current.context;
  const result = dom['comparison-result'];
  result.replaceChildren();
  if (card && phase === 'completed') {
    const heading = element('div', 'verdict');
    heading.append(element('span', 'small-label', '자동 예비 판정'),
        element('h2', '', card.relation === 'position_change' ? '입장 변화' : '다르게 말한 기록'));
    const timeline = element('div', 'timeline');
    const past = element('div', 'past');
    const dot = element('span', 'timeline-dot');
    dot.setAttribute('aria-hidden', 'true');
    const date = element('div', 'date-label', '이전');
    date.append(element('time', '', typeof card.past_date === 'string' ? card.past_date : '날짜 미확인'));
    past.append(dot, date, element('p', 'source-name', typeof card.past_venue === 'string' ? card.past_venue : ''),
        element('blockquote', '', `“${card.past_text}”`), externalLink('원문 확인 ↗', card.past_url, 'source-link'));
    const now = element('div', 'now');
    const nowDot = element('span', 'timeline-dot');
    nowDot.setAttribute('aria-hidden', 'true');
    const nowDate = element('div', 'date-label', '지금');
    nowDate.append(element('time', '', statement.current.date));
    now.append(nowDot, nowDate, element('p', '', typeof card.rationale === 'string' ? card.rationale : '두 발언의 원문과 맥락을 함께 확인하세요.'));
    timeline.append(past, now);
    result.append(heading, timeline, element('p', 'verdict-limit',
        '입장 변화는 거짓말 판정이 아닙니다. 원문을 직접 확인하세요.'));
    return;
  }
  const segment = detail.segments.find(item => item.index === statement.current.segmentIndex);
  if (!detail.config.judgeReady) {
    result.append(statePanel('판정기 미설정', '영상과 원문은 확인할 수 있습니다. 현재 서버에서는 새 비교를 실행할 수 없습니다.'));
  } else if (run?.connection === 'invalid') {
    result.append(statePanel('비교 세션 종료', '새 비교를 시작하면 영상을 처음부터 확인할 수 있습니다.'));
  } else if (phase === 'failed') {
    result.append(statePanel('발언 비교에 실패했습니다', '현재 발언과 원문은 유지했습니다.',
        button('비교 다시 시도', () => sendSegment(detail, segment, true))));
  } else if (phase === 'send_failed') {
    result.append(statePanel('발언을 전송하지 못했습니다', '서버 연결을 확인한 뒤 다시 전송해 주세요.',
        button('발언 다시 전송', () => sendSegment(detail, segment))));
  } else if (phase === 'completed' || phase === 'skipped') {
    result.append(statePanel('표시할 비교 기록 없음', '이번 비교에서 표시할 예비 판정이 없습니다. 참·거짓을 뜻하지 않습니다.'));
  } else if (PHASE_LABELS[phase]) {
    result.append(statePanel(PHASE_LABELS[phase], '현재 발언과 원문을 계속 확인할 수 있습니다.'));
  } else {
    result.append(statePanel('재생 후 과거 발언 비교', '이 발언이 끝나면 검색과 비교를 시작합니다.'));
  }
}

function renderRunStatus(detail) {
  const run = detail.run;
  const status = dom['processing-status'];
  const notice = dom['run-notice'];
  dom['restart-run'].disabled = detail.sessionPhase === 'starting';
  notice.replaceChildren();
  if (detail.sessionPhase === 'starting') status.textContent = '영상 비교 준비 중';
  else if (run && run.connection !== 'invalid') {
    const pending = pendingSegments(run);
    const selectedIndex = selectedStatement(detail).current.segmentIndex;
    const active = pending.find(([index]) => index === selectedIndex) ?? pending[0];
    const statement = active && detail.config.statements.find(item => item.current.segmentIndex === active[0]);
    status.textContent = active ? `${statement ? `${statement.topic} · ` : ''}${PHASE_LABELS[active[1].phase]}` : '';
  } else status.textContent = '';
  if (detail.sessionError) {
    notice.append(element('p', '', detail.sessionError), button('비교 다시 준비', () => restartRun(detail)));
  } else if (run?.connection === 'invalid') {
    notice.append(element('p', '', '비교 세션이 종료되어 영상을 멈췄습니다. 새 비교를 시작해 주세요.'),
        button('새 비교 시작', () => restartRun(detail)));
  } else {
    if (run?.connection === 'reconnecting') notice.append(element('p', '', '진행 상태에 다시 연결하는 중입니다. 받은 비교 결과는 유지됩니다.'));
    if (run?.notice) notice.append(element('p', '', run.notice));
  }
}

function addLog(message) {
  const text = String(message).slice(0, 600);
  dom['event-log'].prepend(element('li', '', `${new Date().toLocaleTimeString('ko-KR')} ${text}`));
  while (dom['event-log'].childElementCount > 120) dom['event-log'].lastChild.remove();
}

function invalidateRun(detail, run) {
  if (!isCurrent(detail) || detail.run !== run) return;
  run.connection = 'invalid';
  run.events?.close();
  dom.video.pause();
  renderComparison(detail);
  renderRunStatus(detail);
}

function connectEvents(detail, run) {
  const query = new URLSearchParams({run_id: run.id, after: String(run.cursor)});
  const events = new EventSource(`/events?${query}`);
  run.events = events;
  events.onopen = () => {
    if (!isCurrent(detail) || detail.run !== run) return events.close();
    run.connection = 'connected';
    renderRunStatus(detail);
  };
  events.onerror = () => {
    if (!isCurrent(detail) || detail.run !== run) return events.close();
    if (run.connection !== 'invalid') run.connection = 'reconnecting';
    renderRunStatus(detail);
  };
  events.addEventListener('stale', () => invalidateRun(detail, run));
  events.onmessage = message => {
    if (!isCurrent(detail) || detail.run !== run || run.connection === 'invalid') return;
    let payload;
    try { payload = JSON.parse(message.data); } catch { return; }
    const event = parseEvent(payload, run.id, message.lastEventId);
    if (!event || !applyEvent(run, event)) return;
    if (event.kind === 'log') addLog(`${event.segment_index === undefined ? '' : `#${event.segment_index} `}${event.message ?? ''}`);
    if (event.kind === 'card') addLog(`#${event.segment_index} 예비 판정 수신`);
    if (event.kind === 'progress') addLog(`#${event.segment_index} ${event.phase}`);
    if (event.kind !== 'log') renderComparison(detail);
    renderRunStatus(detail);
  };
}

async function prepareRun(detail) {
  if (detail.sessionPromise) return detail.sessionPromise;
  if (detail.run && detail.run.connection !== 'invalid') return detail.run;
  const runGeneration = ++detail.runGeneration;
  detail.sessionController = new AbortController();
  detail.sessionPhase = 'starting';
  detail.sessionError = '';
  renderRunStatus(detail);
  const operation = (async () => {
    const payload = await requestJson('/session', {
      signal: detail.sessionController.signal, body: {person_id: detail.person.id},
    });
    if (!isCurrent(detail) || detail.runGeneration !== runGeneration) return null;
    if (typeof payload.run_id !== 'string' || !payload.run_id || payload.person_id !== detail.person.id) {
      throw new Error('비교 세션 응답 형식이 올바르지 않습니다.');
    }
    const run = createRun(payload.run_id);
    detail.run = run;
    detail.sessionPhase = 'ready';
    connectEvents(detail, run);
    renderRunStatus(detail);
    return run;
  })();
  detail.sessionPromise = operation;
  try {
    return await operation;
  } catch (error) {
    if (!isCurrent(detail) || detail.runGeneration !== runGeneration || error.name === 'AbortError') return null;
    detail.sessionPhase = 'failed';
    detail.sessionError = '비교 세션을 준비하지 못해 영상을 멈췄습니다. 서버 연결을 확인하고 다시 시도해 주세요.';
    dom.video.pause();
    renderRunStatus(detail);
    return null;
  } finally {
    if (isCurrent(detail) && detail.runGeneration === runGeneration) detail.sessionPromise = null;
  }
}

async function playVideo(detail) {
  if (!isCurrent(detail)) return;
  detail.lastPlaybackTime = dom.video.currentTime;
  try { await dom.video.play(); } catch {
    if (isCurrent(detail)) addLog('비교 준비 완료. 영상의 재생 버튼을 눌러 주세요.');
  }
}

async function restartRun(detail) {
  if (!isCurrent(detail) || detail.phase !== 'ready') return;
  dom.video.pause();
  detail.run?.events?.close();
  detail.sessionController?.abort();
  detail.runGeneration += 1;
  detail.sessionPromise = null;
  detail.run = null;
  detail.sessionPhase = 'idle';
  detail.sessionError = '';
  detail.lastPlaybackTime = 0;
  dom.video.currentTime = 0;
  dom['event-log'].replaceChildren();
  selectStatement(detail, detail.config.statements[0].id);
  renderQuote(detail);
  if (!detail.config.judgeReady) return;
  const run = await prepareRun(detail);
  if (run) await playVideo(detail);
}

async function sendSegment(detail, segment, retry = false) {
  const run = detail.run;
  if (!isCurrent(detail) || !run || !beginSubmission(run, segment.index, retry)) return;
  renderComparison(detail);
  renderRunStatus(detail);
  try {
    await requestJson('/segments', {
      signal: detail.controller.signal,
      body: {run_id: run.id, index: segment.index, ...(retry ? {retry: true} : {})},
    });
    if (!isCurrent(detail) || detail.run !== run) return;
    finishSubmission(run, segment.index, true);
  } catch (error) {
    if (!isCurrent(detail) || detail.run !== run || error.name === 'AbortError') return;
    finishSubmission(run, segment.index, false);
    addLog(`#${segment.index} 전송 실패${error instanceof HttpError ? ` HTTP ${error.status}` : ''}`);
    if (error instanceof HttpError && [404, 409].includes(error.status)) invalidateRun(detail, run);
  }
  if (isCurrent(detail) && detail.run === run) {
    renderComparison(detail);
    renderRunStatus(detail);
  }
}

function playbackUpdate() {
  const detail = state.detail;
  if (!detail || detail.phase !== 'ready' || detail.seeking) return;
  const time = dom.video.currentTime;
  const current = detail.segments.find(segment => segment.start <= time && time < segment.end);
  if (current && current.index !== detail.activeSegmentIndex) {
    detail.activeSegmentIndex = current.index;
    renderQuote(detail, current);
    const statement = detail.config.statements.find(item => item.current.segmentIndex === current.index);
    if (statement) selectStatement(detail, statement.id);
  }
  if (detail.run && detail.config.judgeReady && (!dom.video.paused || dom.video.ended)) {
    for (const segment of crossedSegments(detail.segments, detail.lastPlaybackTime, time)) {
      if (segment.speaker === detail.config.holder) void sendSegment(detail, segment);
    }
  }
  detail.lastPlaybackTime = time;
}

async function loadCredits() {
  try {
    const credits = await requestJson('/assets/portrait-sources.json');
    if (!Array.isArray(credits)) throw new Error('사진 출처 형식 오류');
    dom['photo-credits'].replaceChildren();
    for (const credit of credits) {
      const item = element('li');
      item.append(externalLink(`${credit.name_ko} · ${credit.author}`, credit.source_page),
          externalLink(credit.license, credit.license_url));
      dom['photo-credits'].append(item);
    }
    dom['photo-credit-status'].hidden = true;
  } catch {
    dom['photo-credit-status'].replaceChildren(element('span', '', '사진 출처를 불러오지 못했습니다. '),
        button('다시 불러오기', loadCredits, 'text-button'));
  }
}

dom['people-search'].addEventListener('input', event => {
  dom['clear-search'].hidden = !event.currentTarget.value;
  if (!state.people.composing) {
    state.people.query = event.currentTarget.value;
    renderPeople();
  }
});
dom['people-search'].addEventListener('compositionstart', () => { state.people.composing = true; });
dom['people-search'].addEventListener('compositionend', event => {
  state.people.composing = false;
  state.people.query = event.currentTarget.value;
  renderPeople();
});
dom['people-search'].addEventListener('keydown', event => {
  if (event.key === 'Escape' && !event.isComposing && !state.people.composing) {
    event.preventDefault();
    clearSearch();
  }
});
dom['clear-search'].addEventListener('click', clearSearch);
dom['back-to-people'].addEventListener('click', backToPeople);
dom['restart-run'].addEventListener('click', () => { if (state.detail) void restartRun(state.detail); });
dom.video.addEventListener('play', async () => {
  const detail = state.detail;
  if (!detail || detail.phase !== 'ready') return dom.video.pause();
  if (detail.run?.connection === 'invalid') return dom.video.pause();
  if (!detail.config.judgeReady || detail.run) {
    detail.lastPlaybackTime = dom.video.currentTime;
    return;
  }
  dom.video.pause();
  const run = await prepareRun(detail);
  if (run) await playVideo(detail);
});
dom.video.addEventListener('timeupdate', playbackUpdate);
dom.video.addEventListener('ended', playbackUpdate);
dom.video.addEventListener('seeking', () => { if (state.detail) state.detail.seeking = true; });
dom.video.addEventListener('seeked', () => {
  if (!state.detail) return;
  state.detail.seeking = false;
  state.detail.activeSegmentIndex = null;
  state.detail.lastPlaybackTime = dom.video.currentTime;
  playbackUpdate();
});
dom.video.addEventListener('error', () => {
  const detail = state.detail;
  if (!detail || detail.phase !== 'ready' || !dom.video.getAttribute('src')) return;
  dom['media-error'].hidden = false;
  dom['media-error'].replaceChildren(element('p', '', '영상에 연결하지 못했습니다. 현재 발언과 원문은 아래에서 확인할 수 있습니다.'),
      button('영상 다시 불러오기', () => {
        dom['media-error'].hidden = true;
        dom.video.load();
      }));
});
window.addEventListener('popstate', () => {
  const personId = new URLSearchParams(location.hash.slice(1)).get('person');
  const person = state.people.items.find(item => item.id === personId);
  if (person) void openPerson(person, false);
  else showPeople();
});
for (const link of document.querySelectorAll('.b-skip-link')) {
  link.addEventListener('click', event => {
    const target = document.getElementById(link.hash.slice(1));
    if (!target) return;
    event.preventDefault();
    target.focus({preventScroll: true});
    target.scrollIntoView({block: 'start'});
  });
}
window.addEventListener('pagehide', stopDetail);

void loadPeople();
void loadCredits();
