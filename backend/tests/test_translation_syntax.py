import re
from collections import Counter
import pytest
from app import skills

PROMPT = r"\begin{promptbox}[frametitle={Task Description ($\mathcal{I}_T$)}] \raggedright Task Description and Environment Characteristics: \\"
VARIABLE = r"1. Task Description: \\ \tcb{\{task\_description\}} \\"


def rebuild(source, ranges=None):
    segments, pieces = skills.latex_translation_plan(source, ranges)
    result = skills.restore_latex_segments({'segments': [{'id': s['id'], 'translation': s['text']} for s in segments]}, segments, pieces)
    assert result == source
    assert all(not re.search(r'[\\{}$]', s['text']) for s in segments)
    return segments, pieces


def test_promptbox_does_not_send_layout_or_math_to_model():
    segments, pieces = rebuild(PROMPT)
    assert [s['text'] for s in segments] == ['Task Description (', 'Task Description and Environment Characteristics:']
    output = {'segments': [{'id':'s0000','translation':'任务描述（'}, {'id':'s0001','translation':'任务描述与环境特征：'}]}
    translated = skills.restore_latex_segments(output, segments, pieces)
    assert r'frametitle=' in translated and r'$\mathcal{I}_T$' in translated
    assert translated.endswith(r'\\')


def test_nested_template_variables_remain_exact():
    segments, pieces = rebuild(VARIABLE)
    assert [s['text'] for s in segments] == ['. Task Description:']
    translated = skills.restore_latex_segments({'segments':[{'id':'s0000','translation':'. 任务描述：'}]}, segments, pieces)
    assert r'\tcb{\{task\_description\}}' in translated
    assert Counter(re.findall(skills.NUMERIC_PATTERN, translated)) == Counter(re.findall(skills.NUMERIC_PATTERN, VARIABLE))


@pytest.mark.parametrize('text', [
    r'API cost of \$0.2 and improvement 5\%.',
    r'State Reconstruction \& Knowledge Gap Bridging: \\[5pt] More text.',
    r'Use 1o\_10b\_vs\_1r and message\_design\_instruction() in communication(o).',
    r'\parindent=0pt\parskip=0pt\obeylines',
    r'\texttt{\{ "Evaluation": "Example output", "Missing\_Information": "hypothesis" \}}',
])
def test_control_symbols_identifiers_and_code_roundtrip(text):
    rebuild(text)


def test_fenced_code_spanning_blocks_never_reaches_model():
    fence = "'" * 3
    first = r'{\begingroup \parindent=0pt\parskip=0pt ' + fence + r'python def fn(): \# comment'
    second = r'return message\_description ' + fence + r' \endgroup}'
    blocks = [{'id':'a','text':first},{'id':'b','text':second}]
    ranges = skills.code_literal_ranges(blocks)
    assert rebuild(first, ranges['a'])[0] == []
    assert rebuild(second, ranges['b'])[0] == []


def test_model_cannot_introduce_commands_in_safe_segments():
    segments, pieces = rebuild(PROMPT)
    output = {'segments':[{'id':s['id'],'translation':r'错误\cite{invented}'} for s in segments]}
    with pytest.raises(ValueError, match='s0000'):
        skills.restore_latex_segments(output, segments, pieces)


@pytest.mark.parametrize('header', [
    r'\begin{minipage}[c]{0.54\textwidth}',
    r'\begin{minipage}[t][\dimexpr 3cm+2pt\relax][s]{\dimexpr\linewidth-2\fboxsep\relax}',
    r'\begin {minipage} [b] {0.44\textwidth}',
    r'\begin{minipage}{\linewidth}',
])
def test_minipage_parameters_are_protected_but_body_is_translated(header):
    from app.translation_policy import preflight
    assert rebuild(header)[0] == []
    assert preflight([{'id':'layout','text':header}])['items'][0]['kind'] == 'protected'
    source = header + r' Actual prose stays translatable. \end{minipage}'
    segments, pieces = rebuild(source)
    assert [s['text'] for s in segments] == ['Actual prose stays translatable.']
    translated = skills.restore_latex_segments({'segments':[{'id':'s0000','translation':'这里的正文仍需翻译。'}]}, segments, pieces)
    assert translated == header + r' 这里的正文仍需翻译。 \end{minipage}'


def test_minipage_does_not_hide_unknown_options_or_broken_source():
    from app.translation_policy import preflight
    assert rebuild(r'\begin{custom}[English title] Body text.')[0]
    for source in [r'\begin{minipage}[c', r'\begin{minipage}[c]{0.5\textwidth', r'\begin{minipage}[c]']:
        assert preflight([{'id':'broken','text':source}])['items'][0]['kind'] == 'blocked'


def test_latex_double_quotes_are_prose_not_markdown_code():
    source = r"\small ``Happy families are alike.'' \citet{tolstoy1878} ``Each failure is different.'' Berkeley'25."
    segments, pieces = rebuild(source)
    prose = ' '.join(s['text'] for s in segments)
    assert 'Happy families are alike.' in prose and 'Each failure is different.' in prose
    assert '``' not in prose and "''" not in prose
    assert '25' not in prose and 'tolstoy1878' not in prose
    assert 'keep_this' not in ' '.join(s['text'] for s in rebuild(r'Use `keep_this` and \textbf{prose}.')[0])


def test_model_version_identifiers_stay_literal():
    source = r'\small Compare GPT4, Qwen2.5, Claude-3.5 and CodeLlama-7b-Instruct-hf at 95.3\%.'
    segments, pieces = rebuild(source)
    prose = ' '.join(s['text'] for s in segments)
    for name in ('GPT4', 'Qwen2.5', 'Claude-3.5', 'CodeLlama-7b-Instruct-hf'):
        assert name not in prose and name in ''.join(v for k,v in pieces if k=='literal')


def test_box_options_are_not_translated_and_existing_layout_can_be_restored():
    from app.pdf_compat import restore_box_headers
    source = r'\begin{tcolorbox}[colback=custom!5!white,boxrule=0.5mm] Insight 2. \end{tcolorbox}'
    segments,_ = rebuild(source)
    assert all('colback' not in s['text'] and 'mm' not in s['text'] for s in segments)
    cached=source.replace('custom!5!white','自定义！5！白色').replace('0.5mm','0.5毫米').replace('Insight','洞见')
    assert restore_box_headers(source,cached)==source.replace('Insight','洞见')
