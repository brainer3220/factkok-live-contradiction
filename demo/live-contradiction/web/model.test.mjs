import assert from 'node:assert/strict';
import test from 'node:test';
import {
  applyEvent, beginSubmission, createRun, crossedSegments, filterPeople,
  finishSubmission, parseConfig, parseEvent, parsePeople, parseTranscript,
  pendingSegments, segmentPhase, sourceUrl,
} from './model.mjs';

const segments = [
  {index: 0, start: 0, end: 10, speaker: '이재명', text: '첫 발언'},
  {index: 1, start: 10, end: 30, speaker: '편집 안내', text: '편집 구간'},
  {index: 2, start: 30, end: 42, speaker: '이재명', text: '다음 발언'},
];
const card = {
  kind: 'card', run_id: 'run-a', event_id: 4, segment_index: 0,
  statement_id: 'tax', relation: 'position_change', confidence: 0.8,
  now_text: '현재 발언', past_text: '이전 발언',
};

function event(payload) {
  return parseEvent({run_id: 'run-a', ...payload}, 'run-a');
}

test('이름 검색은 한글 공백을 정규화하고 가나다순을 유지한다', () => {
  const people = [{name: '이준석'}, {name: '한동훈'}, {name: '이재명'}];
  assert.deepEqual(filterPeople(people, '이 ').map(person => person.name), ['이재명', '이준석']);
  assert.equal(filterPeople(people, '없는이름').length, 0);
});

test('원문 링크는 절대 HTTP(S) 주소만 허용한다', () => {
  assert.equal(sourceUrl('javascript:alert(1)'), null);
  assert.equal(sourceUrl('data:text/html,test'), null);
  assert.equal(sourceUrl('/local'), null);
  assert.equal(sourceUrl('https://example.org/news'), 'https://example.org/news');
});

test('인물과 발언은 서버 경계에서 검증하며 선계산 판정은 모델에 넣지 않는다', () => {
  assert.throws(() => parsePeople({people: [{id: 'a', name: '인물', status: 'ready', portrait: 'https://example.org/a.jpg'}]}));
  const people = parsePeople({people: [{id: 'a', name: '인물', status: 'ready', portrait: '/assets/people/lee-jae-myung.jpg', record_count: 99}]});
  assert.deepEqual(Object.keys(people[0]).sort(), ['id', 'name', 'portrait', 'status']);
  const config = parseConfig({person_id: 'a', holder: '이재명', judge_ready: true, statements: [{
    statement_id: 'tax', topic: '세금', expected_relation: 'contradiction',
    current: {text: '현재 발언', segment_index: 0, expected_relation: 'contradiction'},
  }]}, 'a');
  assert.equal(config.statements[0].expected_relation, undefined);
  assert.equal(config.statements[0].current.expected_relation, undefined);
  assert.equal(parseTranscript(segments, config.statements).length, 3);
  assert.throws(() => parseConfig({...config, person_id: 'another'}, 'a'));
  assert.throws(() => parseTranscript(segments, [{current: {segmentIndex: 999}}]));
});

test('발언 전송은 성공 ACK 후에만 중복을 막고 전송 실패는 재시도할 수 있다', () => {
  const run = createRun('run-a');
  assert.equal(beginSubmission(run, 0), true);
  assert.equal(beginSubmission(run, 0), false);
  assert.equal(run.acknowledged.has(0), false);
  finishSubmission(run, 0, false);
  assert.equal(segmentPhase(run, 0), 'send_failed');
  assert.equal(beginSubmission(run, 0), true);
  finishSubmission(run, 0, true);
  assert.equal(segmentPhase(run, 0), 'queued');
  assert.equal(beginSubmission(run, 0), false);
});

test('검색 완료 로그는 대기를 해제하지 않고 모델 비교와 완료를 기다린다', () => {
  const run = createRun('run-a');
  beginSubmission(run, 0);
  finishSubmission(run, 0, true);
  applyEvent(run, event({kind: 'progress', event_id: 1, segment_index: 0, phase: 'searching'}));
  applyEvent(run, event({kind: 'log', event_id: 2, segment_index: 0, message: '검색 완료'}));
  assert.equal(pendingSegments(run).length, 1);
  assert.equal(segmentPhase(run, 0), 'searching');
  applyEvent(run, event({kind: 'progress', event_id: 3, segment_index: 0, phase: 'judging'}));
  assert.equal(segmentPhase(run, 0), 'judging');
  applyEvent(run, parseEvent(card, 'run-a'));
  assert.equal(pendingSegments(run).length, 0);
  assert.equal(run.cards.get(0).past_text, '이전 발언');
});

test('SSE 재연결 시 event ID와 발언 키로 같은 카드의 중복 표시를 막는다', () => {
  const run = createRun('run-a');
  const first = parseEvent(card, 'run-a');
  assert.equal(applyEvent(run, first), true);
  assert.equal(applyEvent(run, first), false);
  applyEvent(run, parseEvent({...card, event_id: 5}, 'run-a'));
  assert.equal(run.cardKeys.size, 1);
  assert.equal(run.cards.size, 1);
  assert.equal(run.cursor, 5);
  assert.equal(parseEvent({...card, run_id: 'run-b'}, 'run-a'), null);
  assert.equal(parseEvent({...card, confidence: 7}, 'run-a'), null);
});

test('다른 실행의 결과와 종료된 세션은 새 작업을 만들지 않는다', () => {
  const oldRun = createRun('run-old');
  assert.equal(applyEvent(oldRun, parseEvent(card, 'run-a')), false);
  assert.equal(oldRun.cards.size, 0);
  oldRun.connection = 'invalid';
  assert.equal(beginSubmission(oldRun, 0), false);
  assert.equal(beginSubmission(oldRun, 0, true), false);
});

test('모델 실패는 terminal이며 retry 명령이 다시 보내기 상태를 만든다', () => {
  const run = createRun('run-a');
  beginSubmission(run, 0);
  finishSubmission(run, 0, true);
  applyEvent(run, event({kind: 'progress', event_id: 1, segment_index: 0, phase: 'failed'}));
  assert.equal(pendingSegments(run).length, 0);
  assert.equal(beginSubmission(run, 0), false);
  assert.equal(beginSubmission(run, 0, true), true);
  assert.equal(segmentPhase(run, 0), 'sending');
});

test('ACK보다 먼저 받은 완료 이벤트를 ACK가 대기 상태로 되돌리지 않는다', () => {
  const run = createRun('run-a');
  beginSubmission(run, 0);
  applyEvent(run, parseEvent(card, 'run-a'));
  finishSubmission(run, 0, true);
  assert.equal(segmentPhase(run, 0), 'completed');
  assert.equal(pendingSegments(run).length, 0);
});

test('HTTP 응답만 유실돼도 SSE 수락 증거가 있으면 완료 발언을 재전송하지 않는다', () => {
  const run = createRun('run-a');
  beginSubmission(run, 0);
  applyEvent(run, parseEvent(card, 'run-a'));
  finishSubmission(run, 0, false);
  assert.equal(run.acknowledged.has(0), true);
  assert.equal(segmentPhase(run, 0), 'completed');
  assert.equal(beginSubmission(run, 0), false);
  assert.equal(run.cards.get(0).past_text, '이전 발언');
  assert.equal(pendingSegments(run).length, 0);
});

test('SSE 모델 실패도 수락 증거이며 명시적 모델 재시도는 허용한다', () => {
  const run = createRun('run-a');
  beginSubmission(run, 0);
  applyEvent(run, event({kind: 'progress', event_id: 1, segment_index: 0, phase: 'failed'}));
  finishSubmission(run, 0, false);
  assert.equal(beginSubmission(run, 0), false);
  assert.equal(beginSubmission(run, 0, true), true);
});

test('실제 재생으로 2초 안에 끝을 지난 구간만 제출하고 seek 건너뛰기는 제외한다', () => {
  assert.deepEqual(crossedSegments(segments, 9.8, 10.1).map(segment => segment.index), [0]);
  assert.deepEqual(crossedSegments(segments, 0, 31), []);
  assert.deepEqual(crossedSegments(segments, 10.1, 9), []);
  assert.deepEqual(crossedSegments(segments, null, 10), []);
  assert.deepEqual(crossedSegments(segments, 41.9, 42.1).map(segment => segment.index), [2]);
});
