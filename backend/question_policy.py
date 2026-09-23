"""Bounded question selection and a shared ledger derived from saved questions.

Blueprint IDs prevent repeatedly revisiting a topic; lexical checks are a second,
conservative guard, not a claim of arbitrary semantic equivalence. No model or
storage access happens here, including while loading historical sessions.
"""
from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher


FOLLOWUP_FOCI = {
    'personal_contribution': '个人具体贡献', 'constraints': '限制与约束',
    'decision': '决策及取舍', 'evidence': '成果证据',
    'validation': '验证方法', 'reflection': '复盘与改进',
}
MAX_FOLLOWUPS = 2
_MATERIAL_CONDITIONS = re.compile(r'\d+(?:[.,]\d+)*(?:%|％)?|[一二三四五六七八九十百千万亿两]+(?:倍|年|月|天|次|个|元)|成功|失败|之前|之后|不|没|无')


def _without_greeting(text):
    text = unicodedata.normalize('NFKC', str(text or '')).casefold()
    return re.sub(r'^(?:请问|请你|能否|可以|能不能|能|请)', '', text)


def _normalized(text):
    text = _without_greeting(text)
    return ''.join(char for char in text if char.isalnum()).rstrip('吗呢')


def questions_repeat(left: str, right: str) -> bool:
    """High-confidence exact/lightly rewritten duplicate; do not score semantics."""
    a, b = _normalized(left), _normalized(right)
    if not a or not b:
        return False
    if _MATERIAL_CONDITIONS.findall(_without_greeting(left)) != _MATERIAL_CONDITIONS.findall(_without_greeting(right)):
        return False
    if a == b:
        return True
    if min(len(a), len(b)) < 12:
        return False
    # Avoid treating a short new detail question as its longer background prompt.
    if min(len(a), len(b)) / max(len(a), len(b)) < 0.72:
        return False
    return SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.86


def asked_questions(session):
    """One record per question; explicit single-question retries are not new topics."""
    records, seen = [], set()
    sources = list(session.get('turns') or [])
    if session.get('active_question'):
        sources.append(session['active_question'])
    # Transcript-only legacy sessions can still avoid asking exact old questions.
    sources.extend({**item, 'question': item.get('content', '')}
                   for item in session.get('transcript') or [] if item.get('role') == 'interviewer')
    for record in sources:
        if record.get('is_retry') or not record.get('question'):
            continue
        identity = record.get('question_id') or ('legacy-text', record['question'])
        if identity in seen:
            continue
        seen.add(identity)
        records.append(record)
    return records


def _blueprints(session):
    return [item for item in session.get('blueprint') or []
            if item.get('id') is not None and isinstance(item.get('question'), str) and item['question'].strip()]


def covered_blueprint_ids(session):
    """Explicit mappings plus conservative text inference for pre-ledger sessions."""
    covered = set()
    for record in asked_questions(session):
        if record.get('blueprint_id') is not None:
            covered.add(str(record['blueprint_id']))
        # Legacy adaptive questions did not store IDs. Only infer strong matches;
        # never silently rewrite the original record or guess from question order.
        for item in _blueprints(session):
            if questions_repeat(record['question'], item['question']):
                covered.add(str(item['id']))
    return covered


def next_uncovered_question(session):
    covered = covered_blueprint_ids(session)
    asked = asked_questions(session)
    return next((item for item in _blueprints(session)
                 if str(item['id']) not in covered
                 and not any(questions_repeat(item['question'], prior['question']) for prior in asked)), None)


def _followup_context(session):
    turns = session.get('turns') or []
    last = turns[-1] if turns else {}
    if last.get('status') != 'answered' or last.get('is_retry'):
        return None, []
    blueprint_id = last.get('blueprint_id')
    if blueprint_id is None:
        match = next((item for item in _blueprints(session)
                      if questions_repeat(item['question'], last.get('question', ''))), None)
        blueprint_id = match['id'] if match else None
    chain = []
    by_id = {turn['question_id']: turn for turn in turns if turn.get('question_id')}
    node = last
    visited = set()
    while node.get('question_kind') == 'adaptive' and node.get('question_id') not in visited:
        visited.add(node.get('question_id'))
        chain.append(node)
        node = by_id.get(node.get('parent_question_id'), {})
    if blueprint_id is None:
        blueprint_id = node.get('blueprint_id')
    return blueprint_id, chain


def selection_instruction(session):
    target = next_uncovered_question(session)
    blueprint_id, chain = _followup_context(session)
    remaining = [key for key in FOLLOWUP_FOCI if key not in {item.get('followup_focus') for item in chain}]
    last = (session.get('turns') or [{}])[-1]
    may_follow = last.get('status') == 'answered' and not last.get('is_retry') and len(chain) < MAX_FOLLOWUPS
    return (
        '输出协议优先于前文口语输出要求。只输出一个 JSON 对象，不输出 Markdown 或解释：'
        '{"kind":"blueprint 或 followup","blueprint_id":题纲编号或 null,'
        '"followup_focus":焦点英文编号或 null,"question":"给候选人的一个自然问题"}。\n'
        f'已考察（包括跳过）的题纲编号：{json.dumps(sorted(covered_blueprint_ids(session)), ensure_ascii=False)}。\n'
        f'下一道尚未考察的题（kind=blueprint 时只能选择此题，可结合答案自然改写但不可改变考察主题）：{json.dumps(target, ensure_ascii=False)}。\n'
        f'允许追问：{may_follow}；追问所属 blueprint_id：{json.dumps(blueprint_id)}；'
        f'尚可选择的 followup_focus：{json.dumps({key: FOLLOWUP_FOCI[key] for key in remaining}, ensure_ascii=False)}。\n'
        'followup 只可针对最近一份真实回答追问一个尚未问过的新细节，不复述原问题或换个说法重问。'
        '跳过代表用户不再回答该题，不得要求他再次回答；本轮不得给提示、讲解、建议答案或评分。'
    )


def validate_candidate(raw, session):
    """Validate provider output before it is displayed or stored as a question."""
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError('下一题没有遵守结构化选择协议') from exc
    if not isinstance(value, dict) or set(value) - {'kind', 'blueprint_id', 'followup_focus', 'question'}:
        raise ValueError('下一题字段无效')
    question = value.get('question')
    if not isinstance(question, str) or not question.strip() or len(question) > 4000:
        raise ValueError('下一题文字无效')
    question = question.strip()
    if any(questions_repeat(question, record['question']) for record in asked_questions(session)):
        raise ValueError('下一题与已问内容重复')
    if value.get('kind') == 'blueprint':
        target = next_uncovered_question(session)
        if not target or str(value.get('blueprint_id')) != str(target['id']) or value.get('followup_focus') is not None:
            raise ValueError('下一题未选择尚未考察的指定题纲')
        if any(str(item['id']) != str(target['id']) and questions_repeat(question, item['question'])
               for item in _blueprints(session)):
            raise ValueError('下一题文字与所选题纲编号不匹配')
        covered = covered_blueprint_ids(session)
        if any(str(item['id']) in covered and questions_repeat(question, item['question']) for item in _blueprints(session)):
            raise ValueError('下一题重新询问已覆盖题纲')
        return {'question': question, 'blueprint_id': target['id'], 'question_kind': 'blueprint', 'followup_focus': None}
    if value.get('kind') == 'followup':
        blueprint_id, chain = _followup_context(session)
        last = (session.get('turns') or [{}])[-1]
        focus = value.get('followup_focus')
        if (last.get('status') != 'answered' or last.get('is_retry') or len(chain) >= MAX_FOLLOWUPS
                or str(value.get('blueprint_id')) != str(blueprint_id)
                or not isinstance(focus, str) or focus not in FOLLOWUP_FOCI
                or focus in {item.get('followup_focus') for item in chain}):
            raise ValueError('追问需要新的细节焦点，且不能无限追问或重新追问跳过题')
        # A mislabeled follow-up must not repeat any already-covered base prompt.
        if any(str(item['id']) in covered_blueprint_ids(session) and questions_repeat(question, item['question'])
               for item in _blueprints(session)):
            raise ValueError('追问不能复述原题')
        return {'question': question, 'blueprint_id': blueprint_id, 'question_kind': 'adaptive', 'followup_focus': focus}
    raise ValueError('下一题类型无效')


def fallback_candidate(session):
    target = next_uncovered_question(session)
    if target:
        return {'question': target['question'].strip(), 'blueprint_id': target['id'],
                'question_kind': 'blueprint', 'followup_focus': None}
    return None
