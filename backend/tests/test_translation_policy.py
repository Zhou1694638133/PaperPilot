from app.translation_policy import preflight
from app.skills import code_literal_ranges


def block(text, **kw):
    return {'id':'p1', 'text':text, 'translatable':True, 'location':'test', **kw}


def test_names_are_not_a_paper_specific_whitelist():
    for text in [r'SMAC-Comm: bane\_vs\_hM', r'GRF: Run\_pass\_and\_shoot', r'XYZ-Bench: new\_task\_42', 'GPT-4']:
        assert preflight([block(text)])['items'][0]['kind']=='identifier'
    for text in ['RESULTS AND DISCUSSION', 'We study PPO on task_42.', 'A New Approach to Learning', 'RESULTS task_1']:
        assert preflight([block(text)])['items'][0]['kind']=='prose'


def test_math_primes_and_protected_captions_are_not_code_fences():
    primes="'"*3
    blocks=[block('$a'+primes+'$ is a mathematical symbol.'), block('Ordinary prose.',id='p2')]
    assert all(i['kind']=='prose' for i in preflight(blocks)['items'])
    blocks[0]=block('Caption contains a'+primes,translatable=False)
    assert preflight(blocks)['items'][1]['kind']=='prose'
    assert all(not spans for spans in code_literal_ranges(blocks).values())


def test_reserved_tokens_are_blocked_before_any_model_call():
    assert preflight([block(r'Text __PP_PROTECTED_0000__ with \command')])['items'][0]['kind']=='blocked'



def test_partial_translation_cannot_download_complete_pdf(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from app import reading_api
    monkeypatch.setattr(reading_api.jobs,'get_job',lambda _: {'skill_id':'paper_translator','result':{'partial':True,'markdown':'incomplete'}})
    with pytest.raises(HTTPException) as error:
        reading_api.report_pdf(job_id='test',ids='')
    assert error.value.status_code==409
