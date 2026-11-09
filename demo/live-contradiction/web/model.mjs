const ACTIVE_PHASES = new Set(['sending', 'queued', 'searching', 'judging']);
const PROGRESS_PHASES = new Set(['searching', 'judging', 'completed', 'failed', 'skipped']);
const EVENT_KINDS = new Set(['progress', 'card', 'log', 'banner', 'banner_clear']);

export function normalizeSearch(value) {
  return value.normalize('NFKC').replace(/\s+/g, '').toLocaleLowerCase('ko-KR');
}

export function filterPeople(people, query) {
  const search = normalizeSearch(query);
  return people.filter(person => normalizeSearch(person.name).includes(search))
      .sort((left, right) => left.name.localeCompare(right.name, 'ko'));
}

export function sourceUrl(value) {
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function requireValue(condition, message) {
  if (!condition) throw new Error(message);
}

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isText(value) {
  return typeof value === 'string' && value.length > 0;
}

export function parsePeople(payload) {
  requireValue(isRecord(payload) && Array.isArray(payload.people), '인물 목록 형식이 올바르지 않습니다.');
  const ids = new Set();
  return payload.people.map(person => {
    requireValue(isRecord(person) && isText(person.id) && isText(person.name) &&
        ['ready', 'unavailable'].includes(person.status) &&
        typeof person.portrait === 'string' && /^\/assets\/people\/[a-z0-9-]+\.(jpg|png)$/.test(person.portrait) &&
        !ids.has(person.id), '인물 정보 형식이 올바르지 않습니다.');
    ids.add(person.id);
    return {id: person.id, name: person.name, status: person.status, portrait: person.portrait};
  });
}

export function parseConfig(payload, personId) {
  requireValue(isRecord(payload) && payload.person_id === personId &&
      isText(payload.holder) && typeof payload.judge_ready === 'boolean' &&
      Array.isArray(payload.statements), '발언 설정 형식이 올바르지 않습니다.');
  const ids = new Set();
  const statements = payload.statements.map(statement => {
    requireValue(isRecord(statement), '발언 자료 형식이 올바르지 않습니다.');
    const current = statement.current;
    requireValue(isText(statement.statement_id) && isText(statement.topic) &&
        isRecord(current) && isText(current.text) && Number.isInteger(current.segment_index) &&
        current.segment_index >= 0 && !ids.has(statement.statement_id),
        '발언 자료 형식이 올바르지 않습니다.');
    ids.add(statement.statement_id);
    return {
      id: statement.statement_id,
      topic: statement.topic,
      current: {
        text: current.text,
        date: typeof current.date === 'string' ? current.date : '',
        sourceUrl: sourceUrl(current.source_url),
        context: typeof current.context === 'string' ? current.context : '',
        segmentIndex: current.segment_index,
      },
    };
  });
  return {
    holder: payload.holder,
    judgeReady: payload.judge_ready,
    judgeModel: typeof payload.judge_model === 'string' ? payload.judge_model : '',
    storeLabel: typeof payload.store_label === 'string' ? payload.store_label : '',
    statements,
  };
}

export function parseTranscript(payload, statements) {
  requireValue(Array.isArray(payload), '자막 형식이 올바르지 않습니다.');
  const indexes = new Set();
  let previousEnd = 0;
  const segments = payload.map(segment => {
    requireValue(isRecord(segment) && Number.isInteger(segment.index) && segment.index >= 0 &&
        Number.isFinite(segment.start) && Number.isFinite(segment.end) &&
        segment.start >= previousEnd && segment.end > segment.start &&
        isText(segment.speaker) && isText(segment.text) && !indexes.has(segment.index),
        '자막 구간 형식이 올바르지 않습니다.');
    indexes.add(segment.index);
    previousEnd = segment.end;
    return {index: segment.index, start: segment.start, end: segment.end,
      speaker: segment.speaker, text: segment.text};
  });
  requireValue(statements.every(statement => indexes.has(statement.current.segmentIndex)),
      '발언에 연결된 자막 구간이 없습니다.');
  return segments;
}

export function createRun(id) {
  return {
    id,
    connection: 'connecting',
    cursor: 0,
    eventIds: new Set(),
    cardKeys: new Set(),
    cards: new Map(),
    segments: new Map(),
    acknowledged: new Set(),
    inFlight: new Set(),
    notice: '',
  };
}

export function segmentPhase(run, index) {
  return run?.segments.get(index)?.phase ?? 'idle';
}

export function pendingSegments(run) {
  return [...run.segments.entries()].filter(([, segment]) => ACTIVE_PHASES.has(segment.phase));
}

export function beginSubmission(run, index, retry = false) {
  if (run.connection === 'invalid' || run.inFlight.has(index) ||
      (!retry && run.acknowledged.has(index))) return false;
  run.inFlight.add(index);
  run.segments.set(index, {phase: 'sending'});
  return true;
}

export function finishSubmission(run, index, accepted) {
  run.inFlight.delete(index);
  if (accepted) {
    run.acknowledged.add(index);
    if (segmentPhase(run, index) === 'sending') run.segments.set(index, {phase: 'queued'});
  } else if (segmentPhase(run, index) === 'sending') {
    run.segments.set(index, {phase: 'send_failed'});
  }
}

export function parseEvent(payload, runId, lastEventId = '') {
  if (!isRecord(payload) || payload.run_id !== runId || !EVENT_KINDS.has(payload.kind)) return null;
  const eventId = Number(payload.event_id ?? lastEventId);
  if (!Number.isSafeInteger(eventId) || eventId < 1) return null;
  const event = {...payload, eventId};
  if (['progress', 'card'].includes(event.kind) &&
      (!Number.isInteger(event.segment_index) || event.segment_index < 0)) return null;
  if (event.kind === 'progress' && !PROGRESS_PHASES.has(event.phase)) return null;
  if (event.kind === 'card' && (!isText(event.statement_id) ||
      !isText(event.now_text) || !isText(event.past_text) ||
      !['contradiction', 'position_change'].includes(event.relation) ||
      !Number.isFinite(event.confidence) || event.confidence < 0 || event.confidence > 1)) return null;
  return event;
}

export function applyEvent(run, event) {
  if (event.run_id !== run.id || run.eventIds.has(event.eventId)) return false;
  run.eventIds.add(event.eventId);
  run.cursor = Math.max(run.cursor, event.eventId);
  if (event.kind === 'progress' || event.kind === 'card') {
    run.acknowledged.add(event.segment_index);
  }
  if (event.kind === 'progress') {
    run.segments.set(event.segment_index, {phase: event.phase});
  } else if (event.kind === 'card') {
    const key = `${run.id}:${event.segment_index}:${event.statement_id}`;
    if (!run.cardKeys.has(key)) {
      run.cardKeys.add(key);
      run.cards.set(event.segment_index, event);
    }
    run.segments.set(event.segment_index, {phase: 'completed'});
  } else if (event.kind === 'banner') {
    run.notice = typeof event.message === 'string' ? event.message : '';
  } else if (event.kind === 'banner_clear') {
    run.notice = '';
  }
  return true;
}

export function crossedSegments(segments, previousTime, currentTime) {
  if (previousTime === null || currentTime < previousTime || currentTime - previousTime > 2) return [];
  return segments.filter(segment => segment.end > previousTime && segment.end <= currentTime &&
      segment.end >= currentTime - 2);
}
