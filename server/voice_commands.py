"""Bounded Korean command grammar for a human-reviewed voice draft.

The JSON pool supplies literal aliases and full-match templates. This module
does not diagnose, coerce medical values, call a hospital, or execute commands.
Unsafe command-shaped text is explicitly rejected so auto mode cannot mistake
it for dictation. Ordinary clinical negation remains available to extraction.
"""
import copy
import json
from pathlib import Path
import re

_DEFAULT = json.loads((Path(__file__).resolve().parents[1]/'config/voice_commands.json').read_text(encoding='utf-8'))
_CONTROL = re.compile(r'수정|정정|고쳐|고치|고칠|바꿔|바꾸|바꿀|바꿨|변경|추가|입력|기록|저장|삭제|지워|지우|지울|지웠|빼\s*줘|빼\s*주세요|초기화|확인|확정|전송|보내|보낼|보냈|반영|취소|되돌|읽어|읽을|읽었|불러|알려|보여|말해|질문')
_NEGATIVE = re.compile(r'(?:하지|시키지|보내지|바꾸지|지우지|읽지|묻지|말하지|고치지|빼지|넘기지|넣지|적지)\s*(?:마|말|않|못)|'
                       r'(?:수정|정정|변경|추가|입력|기록|저장|삭제|확인|확정|전송|반영|취소)\s*(?:은|는|을|를)?\s*안\s*(?:해|하|할)|'
                       r'(?:수정|정정|변경|추가|입력|기록|저장|삭제|확인|확정|전송|반영|취소)\s*(?:은|는|을|를)?\s*말(?:아|고|라)|'
                       r'(?:안|못)\s*(?:보내|지워|바꿔|읽어|고쳐)|(?:하지|보내지|바꾸지|지우지)\s*않|'
                       r'아니라고\s*(?:했|말|하|들)|아니라는데|아니라는\s*(?:말|뜻|거|것)')
_CONDITIONAL = re.compile(r'하면|한다면|된다면|되면|이면|오면|나면|라면|거든|할\s*경우|인\s*경우|했으면|할\s*때|했을\s*때|일\s*때|가정|'
                          r'(?:해|바꿔|고쳐|지워|보내|읽어)도\s*(?:돼|되|될)|해\s*볼까|(?:할|지울|보낼|바꿀|고칠|읽을)까')
_HISTORY = re.compile(r'(?:수정|정정|변경|삭제|확인|확정|전송|반영|취소|저장|입력|기록)\s*(?:했었|했다|했다고|했대|했어요|했었어)|'
                     r'보냈|지웠|바꿨|읽었|(?:수정|정정|삭제|전송|확인|취소|저장|입력|기록).{0,12}(?:라고|하라고|하래|했는데)|'
                     r'(?:어제|지난번|과거|예전에).{0,40}(?:수정|확인|전송|삭제|저장|입력|기록)')
_COMPOUND = re.compile(r'(?:수정|정정|변경|추가|삭제|확인|확정|전송|반영|취소|지우|바꾸|고치|보내|읽어|입력|기록|저장)'
                      r'.{0,12}(?:하고|한후|한다음|그리고|고나서|고|,|;|해서).{0,100}'
                      r'(?:수정|정정|변경|추가|삭제|확인|확정|전송|반영|취소|지워|지우|보내|읽어|입력|기록|저장)')
_REQUEST = r'(?:해(?:줘|주세요|주십시오|줄래|줄래요|주실래요|주시겠어요|줄수있어|줄수있나요|봐|봐줘)?|줘|주세요|주십시오|줄래|줄래요|주실래요|주시겠어요|봐|봐줘)?'
_DIRECT_REQUEST = r'(?:줘|주세요|주십시오|줄래|줄래요|주실래요|주시겠어요|줄수있어|줄수있나요|봐|봐줘)?'
_POLITE_PREFIX = re.compile(r'^(?:(?:저기|자|일단|우선|그러면|그럼|이제|혹시|제발)\s*[,，]?\s*){1,3}')
_REMOVE_WORD = re.compile(r'(?:(?:삭제|제거|초기화)' + _REQUEST + r'|(?:지워|빼|비워)' + _DIRECT_REQUEST + r')$')
_EDIT_END = re.compile(r'(?:으로|로)?(?:수정|정정|변경)' + _REQUEST + r'$|(?:으로|로)?(?:고쳐|바꿔)' + _DIRECT_REQUEST + r'$')
_EDIT_PREFIX = re.compile(r'^(?:수정|정정|변경)(?:해)?[:：]?')
_ADD_END = re.compile(r'(?:추가|기록|입력|저장)' + _REQUEST + r'$|(?:적어|넣어)' + _DIRECT_REQUEST + r'$')
_ADD_PREFIX = re.compile(r'^(?:추가|기록|입력|저장)[:：]?')
_QUERY = re.compile(r'(?:알려|읽어|불러|말해|보여)' + _DIRECT_REQUEST + r'$|확인' + _REQUEST + r'$|'
                    r'조회' + _REQUEST + r'$|'
                    r'(?:얼마|몇)(?:야|이야|지|이지|니|으로저장됐어|로저장됐어|으로기록됐어|로기록됐어|으로입력됐어|로입력됐어|로되어있어)?$|'
                    r'(?:저장|기록|입력)(?:됐어|되어있어|돼있어|된거야)$|어떻게(?:됐어|되어있어|돼있어)$')
_SAVED_QUERY = re.compile(r'(?:저장|기록|입력)(?:됐는지|했는지|되었는지|돼있는|되어있는|된값|한값|된수치|한수치)|'
                          r'(?:값|수치|숫자)(?:이|가)?맞는지|맞는지(?:확인|알려|조회)')
_FIELD_HISTORY = re.compile(r'(?:최근|마지막|직전|지금까지)?(?:변경|수정|입력)?(?:이력|내역)'
                           r'(?:을|를|만)?(?:다시)?(?:(?:알려|읽어|보여)' + _DIRECT_REQUEST +
                           r'|확인' + _REQUEST + r')?$')


def catalog():
    """Return a detached seed pool suitable for database import and inspection."""
    return copy.deepcopy(_DEFAULT)


def _compact(value):
    return re.sub(r'\s+', '', value).casefold()


def _content_at(original, compact_start, compact_end=None):
    positions = [index for index, char in enumerate(original) if not char.isspace()]
    start = positions[compact_start] if compact_start < len(positions) else len(original)
    end = positions[compact_end] if compact_end is not None and compact_end < len(positions) else len(original)
    return original[start:end].strip()


def _field_call(resolve_field, text, include_readback=False):
    return resolve_field(text, include_readback=include_readback)


def _targets(content, config, resolve_field):
    """Resolve whole field names, not a field hidden inside an item expression."""
    value = _compact(content)
    value = re.sub(r'^(?:입력한|입력된|저장된|현재|지금|방금입력한|최근|마지막|직전)', '', value)
    value = re.sub(r'(?:항목|항목값|측정값|기록|값)(?:만|을|를|은|는)?$', '', value)
    chunks = re.split(r'이랑|랑|하고|및|,|와|과', value)
    aliases = {_compact(alias): field for field, names in config.get('field_aliases', {}).items() for alias in names}
    fields = []
    for chunk in chunks:
        candidate = re.sub(r'(?:만|을|를|은|는)$', '', chunk)
        field = aliases.get(candidate)
        if field is None:
            return []
        # Resolver remains the authority for supported server fields.
        resolved = _field_call(resolve_field, candidate, include_readback=True)
        if resolved != field:
            return []
        if field not in fields:
            fields.append(field)
    return fields


def parse(text, resolve_field, *, observation_fields=None, config=None):
    """Classify one intent; return rejected=True for unsafe command-like input.

    resolve_field(text, include_readback=False) returns one canonical field or
    None. The optional observation_fields callback preserves the existing
    extractor's conservative definition of a fresh observation.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError('명령은 1~4000자의 문자열이어야 합니다.')
    config = _DEFAULT if config is None else config
    original = text
    value = original.strip().strip('.!?。').strip()
    compact = _compact(value)
    unknown = dict(action='unknown', field=None, text=original)

    def reject(reason):
        return {**unknown, 'rejected': True, 'reason': reason}

    has_control = bool(_CONTROL.search(value))
    if has_control and _NEGATIVE.search(value):
        return reject('NEGATED_COMMAND')
    if has_control and re.search(r'["“”「」『』]|\'.+\'', value):
        return reject('QUOTED_COMMAND')
    if has_control and _CONDITIONAL.search(value):
        return reject('CONDITIONAL_COMMAND')
    if has_control and _COMPOUND.search(compact):
        return reject('MULTIPLE_COMMANDS')

    trimmed = _POLITE_PREFIX.sub('', value).strip()
    compact_trimmed = _compact(trimmed)
    actions = config.get('actions', {})

    def review_result(selected=None, selected_fields=None):
        result = dict(action='review', field=selected, text=original)
        if selected_fields:
            result['fields'] = selected_fields
        # A number inside an explicit query about a saved record is a value to
        # compare with the record, not a new measurement. Auto routing must
        # retain this marker even when it sees numeric tokens.
        if _SAVED_QUERY.search(compact_trimmed):
            result['read_only'] = True
        return result
    # Exact phrases have explicit intent. In particular '확인했어' retains the
    # legacy confirmation meaning, while reports of past actions are rejected.
    for action, spec in actions.items():
        aliases = spec.get('aliases', [])
        if any(compact_trimmed == _compact(alias) for alias in aliases):
            return review_result() if action == 'review' else dict(action=action, field=None, text=original)
    if has_control and _HISTORY.search(value):
        return reject('HISTORICAL_COMMAND')
    for action, spec in actions.items():
        if any(re.fullmatch(pattern, compact_trimmed) for pattern in spec.get('patterns', [])):
            return review_result() if action == 'review' else dict(action=action, field=None, text=original)

    field_history = _FIELD_HISTORY.search(compact_trimmed)
    if field_history:
        content = _content_at(trimmed, 0, field_history.start())
        selected = _targets(content, config, resolve_field)
        if len(selected) == 1:
            return dict(action='history', field=selected[0], text=original)
        if len(selected) > 1:
            return dict(action='history', field=None, fields=selected, text=original)

    removal = _REMOVE_WORD.search(compact_trimmed)
    if removal:
        content = _content_at(trimmed, 0, removal.start())
        fields = _targets(content, config, resolve_field)
        if not fields:
            return reject('UNSCOPED_REMOVAL')
        if len(fields) == 1:
            return dict(action='remove', field=fields[0], text=original)
        return dict(action='remove', field=None, fields=fields, text=original)

    edit = _EDIT_END.search(compact_trimmed)
    prefix = _EDIT_PREFIX.match(compact_trimmed)
    if edit or prefix:
        content = (_content_at(trimmed, 0, edit.start()) if edit else
                   _content_at(trimmed, prefix.end())).strip()
        if not content:
            return reject('MISSING_COMMAND_VALUE')
        return dict(action='edit', field=_field_call(resolve_field, content), text=content)

    addition = _ADD_END.search(compact_trimmed)
    prefix = _ADD_PREFIX.match(compact_trimmed)
    if addition or prefix:
        content = (_content_at(trimmed, 0, addition.start()) if addition else
                   _content_at(trimmed, prefix.end())).strip()
        if not content:
            return reject('MISSING_COMMAND_VALUE')
        return dict(action='add', field=None, text=content)

    field = _field_call(resolve_field, trimmed, include_readback=True)
    if field and _QUERY.search(compact_trimmed):
        return review_result(field)
    if _QUERY.search(compact_trimmed):
        content = _QUERY.sub('', compact_trimmed)
        fields = _targets(content, config, resolve_field)
        if len(fields) > 1:
            return review_result(selected_fields=fields)
        if _SAVED_QUERY.search(compact_trimmed):
            # The query may mention several numeric comparisons. Do not guess
            # which number is a field target; reading the draft is harmless.
            return review_result()
    if observation_fields and observation_fields(original):
        return dict(action='observe', field=field, text=original)
    return unknown
