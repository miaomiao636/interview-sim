const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function runtime() {
  const downloads = [];
  const context = { document: { addEventListener() {} }, setTimeout, clearTimeout, URLSearchParams };
  vm.createContext(context);
  for (const name of ['app', 'reports']) {
    vm.runInContext(fs.readFileSync(require.resolve(`../frontend/${name}.js`), 'utf8'), context);
  }
  context.capture = (...args) => downloads.push(args);
  vm.runInContext('downloadFile = (...args) => capture(...args); this.markdown = renderReportMarkdown;', context);
  return {context, downloads};
}

function sample() {
  const aggregate = total => ({total, question_count: 2, answered_count: 1, unanswered_count: 1,
    dimensions: Object.fromEntries(['relevance', 'evidence', 'professional_content', 'expression'].map(k => [k, {score: total / 10, max: 10, weight: 25}]))});
  return {schema_version: 2, rubric_version: 'interview-evidence-v1', report_id: 'synthetic-report', input_fingerprint: 'synthetic-fingerprint',
    resume_quality: {status: 'not_assessed', assessment: null, resume_version_id: 'version-one'},
    interview_performance: {first_attempt: aggregate(30), latest_retry: {...aggregate(80), question_count: 1, unanswered_count: 0},
      comparison: {paired_question_count: 1, original_total: 60, latest_total: 80, delta: 20, items: []}},
    requirement_coverage: {status: 'not_assessed', requirements: []},
    question_feedback: [], interview_tips: [], practice_plan: []};
}

test('Markdown and JSON retain independent first/retry scores and exact paired delta', () => {
  const {context, downloads} = runtime();
  const data = sample();
  const original = JSON.stringify(data);
  const markdown = context.markdown(data);
  assert.match(markdown, /首次面试表现：30\/100/);
  assert.match(markdown, /最新重答表现：80\/100/);
  assert.match(markdown, /同题配对：60 → 80；变化 \+20 分/);
  assert.match(markdown, /简历质量：未评估/);
  assert.doesNotMatch(markdown, /旧版五维|岗位定制简历|总分：/);
  assert.equal(JSON.stringify(data), original);
  context.sample = data;
  vm.runInContext('state.lastReport = sample; exportReport("json"); exportReport("markdown");', context);
  assert.deepEqual(JSON.parse(downloads[0][1]), data);
  assert.equal(downloads[1][1], markdown);
});

test('null and missing assessments are never displayed as numerical zero', () => {
  const {context} = runtime();
  const data = sample();
  data.interview_performance.first_attempt.total = null;
  data.interview_performance.latest_retry = null;
  data.interview_performance.comparison = {paired_question_count: 0};
  const markdown = context.markdown(data);
  assert.match(markdown, /首次面试表现：未评估/);
  assert.match(markdown, /最新重答表现：未评估/);
  assert.doesNotMatch(markdown, /同题配对：0|最新重答表现：0/);
});

test('legacy reports retain their original total and do not masquerade as new scores', () => {
  const {context} = runtime();
  const legacy = {score: {total: 47, conclusion: '旧评估'}, question_feedback: []};
  const markdown = context.markdown(legacy);
  assert.match(markdown, /旧版五维报告/);
  assert.match(markdown, /总分：47\/100/);
  assert.doesNotMatch(markdown, /首次面试表现|最新重答表现/);
});

test('export distinguishes excluded system repetitions and keeps voice provenance', () => {
  const {context} = runtime();
  const data = sample();
  data.interview_performance.first_attempt.excluded_duplicate_count = 1;
  data.question_feedback = [
    {question_id: 'q-2', attempt: 1, question: '重复题', status: 'unanswered', scoring_excluded: true, score: null, exclusion_reason: '与 q-1 重复'},
    {question_id: 'q-1', attempt: 1, question: '原题', answer: '我负责接口。', score: 6, voice_input: {raw_transcript: '我，我负责接口。', cleaned_transcript: '我负责接口。', cleanup_status: 'cleaned'}},
  ];
  const markdown = context.markdown(data);
  assert.match(markdown, /系统重复题.*未纳入评分/);
  assert.match(markdown, /原始转写：我，我负责接口。/);
  assert.match(markdown, /整理稿：我负责接口。/);
  assert.doesNotMatch(markdown.split('### q-2')[1].split('### q-1')[0], /得分：0|未回答，计0分/);
});

test('system duplicate report cards explain exclusion without zero score or retry advice', () => {
  const {context} = runtime();
  context.feedbackSample = [{question_id: 'q-duplicate', question: '<重复题>', scoring_excluded: true,
    exclusion_reason: '与前题重复 <script>unsafe</script>', status: 'unanswered', score: 0}];
  const html = vm.runInContext('renderQuestionFeedback(feedbackSample)', context);
  assert.match(html, /系统重复题，未纳入评分/);
  assert.match(html, /&lt;重复题&gt;/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /turn-score|重答这题|下一次只改|本题 0 分|<script>/);
});

test('historical voice evidence stays escaped and collapsed after cleanup retirement', () => {
  const {context} = runtime();
  context.feedbackSample = [{question_id: 'q-voice', question: '原题', answer: '最终确认文本', score: 6,
    voice_input: {raw_transcript: '<原始>我我参与', cleaned_transcript: '我参与<script>', cleanup_status: 'cleaned'}}];
  const html = vm.runInContext('renderQuestionFeedback(feedbackSample)', context);
  assert.match(html, /<details class="voice-original-details">/);
  assert.match(html, /&lt;原始&gt;我我参与/);
  assert.match(html, /我参与&lt;script&gt;/);
  assert.match(html, /评分仅评确认文本/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b|<script>/);
});
