"""Report v2: strict model boundary and server-derived scoring semantics."""
from __future__ import annotations
import copy
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from .structured import StructuredOutputError

RUBRIC_VERSION = 'interview-evidence-v2'
PROMPT_VERSION = 'interview-feedback-v2'
DIMENSIONS = ('relevance', 'evidence', 'professional_content', 'expression')
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=600)]
Quote = Annotated[str, StringConstraints(min_length=1, max_length=1000)]
QuestionId = Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$')]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class Dimension(StrictModel):
    score: float = Field(ge=0, le=10)
    comment: Text


class Dimensions(StrictModel):
    relevance: Dimension
    evidence: Dimension
    professional_content: Dimension
    expression: Dimension


class Feedback(StrictModel):
    question_id: QuestionId
    attempt: int = Field(ge=1)
    dimensions: Dimensions
    confidence: Literal['high', 'medium', 'low']
    evidence_quotes: list[Quote] = Field(max_length=3)
    covered_points: list[Text] = Field(max_length=4)
    missed_points: list[Text] = Field(max_length=4)
    question_explanation: Text
    coaching_tip: Text
    improved_answer_outline: list[Text] = Field(max_length=4)
    reason_analysis: Text


class FeedbackBatch(StrictModel):
    question_feedback: list[Feedback] = Field(max_length=2)


class Practice(StrictModel):
    question_id: QuestionId
    focus: Text
    reason: Text


class ReportSummary(StrictModel):
    interview_tips: list[Text] = Field(max_length=5)
    practice_plan: list[Practice] = Field(max_length=5)


class ReportV2(StrictModel):
    schema_version: Literal[2]
    rubric_version: Literal['interview-evidence-v2']
    report_id: str
    input_fingerprint: str
    generated_at: str
    resume_quality: dict
    interview_performance: dict
    requirement_coverage: dict
    question_feedback: list[dict]
    interview_tips: list[str]
    practice_plan: list[dict]


def identity(turn):
    return turn['question_id'], turn.get('attempt', 1)


def validate_feedback(raw, turn):
    value = Feedback.model_validate(raw).model_dump()
    if identity(value) != identity(turn):
        raise StructuredOutputError('逐题反馈的题号或作答次数不匹配。')
    if turn.get('status') == 'unanswered':
        if any(d['score'] for d in value['dimensions'].values()) or value['evidence_quotes'] or value['covered_points']:
            raise StructuredOutputError('未回答问题不能获得分数或虚构作答证据。')
        # A model must not assign a psychological cause to missing evidence.
        value['reason_analysis'] = ('用户选择的原因：' + turn['skip_reason']) if turn.get('skip_reason') else '未提供跳题原因，无法判断原因。'
    else:
        answer = turn.get('answer', '')
        if not answer.strip() or not value['evidence_quotes'] or any(not q.strip() or q not in answer for q in value['evidence_quotes']):
            raise StructuredOutputError('逐题反馈缺少真实原回答引用，请重新生成。')
    return value


def validate_batch(raw, turns):
    data = FeedbackBatch.model_validate(raw).model_dump()
    wanted = {identity(t): t for t in turns}
    keys = [identity(f) for f in data['question_feedback']]
    if set(keys) != set(wanted) or len(keys) != len(wanted):
        raise StructuredOutputError('逐题反馈缺少条目或出现重复题号。')
    return {'question_feedback': [validate_feedback(f, wanted[identity(f)]) for f in data['question_feedback']]}


def validate_summary(raw, turns):
    value = ReportSummary.model_validate(raw).model_dump()
    known = {t['question_id'] for t in turns}
    if any(p['question_id'] not in known for p in value['practice_plan']):
        raise StructuredOutputError('训练计划引用了未发生的面试问题。')
    return value


def enrich_feedback(raw, turn, signature):
    value = validate_feedback(raw, turn)
    value.update({key: copy.deepcopy(turn.get(key)) for key in ('question', 'answer', 'status', 'skip_reason', 'blueprint_id', 'parent_question_id')})
    value['question_kind'] = turn.get('question_kind', 'retry' if turn.get('is_retry') else 'blueprint' if turn.get('blueprint_id') is not None else 'adaptive')
    value.update(score=round(sum(d['score'] for d in value['dimensions'].values()) / 4, 2),
                 max_score=10, turn_fingerprint=signature)
    if turn.get('voice_input'):
        value['voice_input'] = copy.deepcopy(turn['voice_input'])
    return value


def duplicate_skips(turns):
    """Only exclude the diagnosed legacy adaptive -> blueprint replay pattern.

    Keep source records and explicit retries intact. Ambiguous semantics are not
    retroactively classified, and two independent answered questions still count.
    """
    from .question_policy import questions_repeat
    excluded, seen = {}, []
    for turn in turns:
        if turn.get('is_retry') or turn.get('attempt', 1) != 1:
            continue
        if turn.get('status') == 'unanswered' and turn.get('question_kind') == 'blueprint':
            prior = next((item for item in seen if item.get('question_kind') == 'adaptive'
                          and item.get('blueprint_id') is None
                          and questions_repeat(turn.get('question', ''), item.get('question', ''))), None)
            if prior:
                excluded[identity(turn)] = prior['question_id']
        seen.append(turn)
    return excluded


def excluded_feedback(turn, duplicate_of, signature):
    note = f'旧版动态题与题纲题重复（对应 {duplicate_of}），本次跳过不重复扣分。'
    raw = {'question_id': turn['question_id'], 'attempt': turn.get('attempt', 1),
           'dimensions': {key: {'score': 0, 'comment': '系统重复题，不纳入评分'} for key in DIMENSIONS},
           'confidence': 'high', 'evidence_quotes': [], 'covered_points': [], 'missed_points': [],
           'question_explanation': note, 'coaching_tip': '', 'improved_answer_outline': [], 'reason_analysis': note}
    value = enrich_feedback(raw, turn, signature)
    value.update(score=None, scoring_excluded=True, duplicate_of=duplicate_of, exclusion_reason=note, reason_analysis=note)
    return value


def aggregate(items):
    count = len(items)
    return {
        'total': round(sum(sum(d['score'] for d in item['dimensions'].values()) / 4 for item in items) / count * 10, 1) if count else None,
        'question_count': count,
        'answered_count': sum(t['status'] != 'unanswered' for t in items),
        'unanswered_count': sum(t['status'] == 'unanswered' for t in items),
        'dimensions': {key: {'score': round(sum(t['dimensions'][key]['score'] for t in items) / count, 2) if count else None, 'max': 10, 'weight': 25} for key in DIMENSIONS},
        'scope_note': '仅含实际已问问题；跳题计零分，未问要求不进入分母。四维等权，分数仅用于训练比较。',
    }


def preparation_assessments(session):
    from .preparation_store import launch_material_fingerprint
    cfg = session['config']
    snapshot = session.get('preparation_snapshot') or {}
    version = cfg.get('resume_version_id')
    valid = (bool(cfg.get('preset_id') and version)
             and snapshot.get('preset_id') == cfg['preset_id']
             and snapshot.get('resume_version_id') == version
             and snapshot.get('material_fingerprint') == launch_material_fingerprint(cfg))
    assessment = copy.deepcopy(snapshot.get('resume_assessment')) if valid else None
    quality = {'status': 'assessed' if assessment else 'not_assessed', 'resume_version_id': version,
               'assessment': assessment, 'reason': '本次面试启动时保存的所选简历版本诊断。' if assessment else '本次所选材料没有匹配的简历诊断，未评估。',
               'source_task_id': snapshot.get('provenance', {}).get('resume', {}).get('task_id') if assessment else None}
    requirements = copy.deepcopy(snapshot.get('requirements') or []) if valid else []
    for item in requirements:
        item.update(interview_status='not_observed', interview_evidence=[])
    coverage = {'status': 'available' if requirements else 'not_assessed', 'requirements': requirements,
                'note': '准备材料的支持状态不等于面试已验证。当前未自动推断回答与岗位要求的关联；未观察不等于能力不具备，也不是录用概率。'}
    return quality, coverage


def assemble_report(session, feedback, summary, signature):
    groups = {}
    for item in feedback:
        if item.get('scoring_excluded'):
            continue
        groups.setdefault(item['question_id'], []).append(item)
    first, latest, paired_original, paired_latest, comparison = [], [], [], [], []
    for qid, items in groups.items():
        ordered = sorted(items, key=lambda t: t['attempt'])
        original = next((item for item in ordered if item['attempt'] == 1), None)
        retries = [item for item in ordered if item['attempt'] > 1]
        if original:
            first.append(original)
        if retries:
            retry = retries[-1]
            latest.append(retry)
        if original and retries:
            paired_original.append(original)
            paired_latest.append(retry)
            old_total, new_total = aggregate([original])['total'], aggregate([retry])['total']
            comparison.append({'question_id': qid, 'first_attempt': original['attempt'], 'latest_attempt': retry['attempt'],
                               'original_total': old_total, 'latest_total': new_total, 'delta': round(new_total - old_total, 1)})
    old_total, new_total = aggregate(paired_original)['total'], aggregate(paired_latest)['total']
    quality, coverage = preparation_assessments(session)
    first_aggregate = aggregate(first)
    excluded_count = sum(bool(item.get('scoring_excluded')) for item in feedback)
    first_aggregate.update(excluded_duplicate_count=excluded_count,
        scope_note=first_aggregate['scope_note'] + f' 已排除系统重复题 {excluded_count} 道；历史报告不自动改分。')
    return ReportV2(
        schema_version=2, rubric_version=RUBRIC_VERSION, report_id=uuid4().hex,
        input_fingerprint=signature, generated_at=datetime.now(timezone.utc).isoformat(),
        resume_quality=quality, requirement_coverage=coverage,
        interview_performance={'first_attempt': first_aggregate, 'latest_retry': aggregate(latest) if latest else None,
            'comparison': {'paired_question_count': len(paired_latest), 'original_total': old_total, 'latest_total': new_total,
                           'delta': round(new_total - old_total, 1) if paired_latest else None, 'items': comparison},
            'note': '首次面试与面后重答分开展示；改模型或评分规则属于新的评估版本，不能视为同条件进步。表达维度仅评用户确认文字的组织结构，不评估真实口吃、语速或现场流畅度；ASR文字并非录音真值。'},
        question_feedback=feedback, **summary,
    ).model_dump()
