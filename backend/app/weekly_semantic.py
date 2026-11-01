"""Bounded, opt-in semantic enrichment of weekly candidates; no implicit paid retries."""
import json
import time
from . import jobs, llm, repository as repo, semantic
from .config import get_settings


def enrich(record, owner, papers, config, source):
    checkpoint = jobs.job_record(record['id']).get('checkpoint') or {}
    state = checkpoint.get('weekly_semantic', {})
    settings = get_settings()
    enabled = config.get('model_enabled', False)
    ready = bool(enabled and settings.llm_api_key and settings.llm_model and record['model'] == settings.llm_model
                 and record['input'].get('_provider_url') == settings.llm_base_url)
    limit = config.get('model_limit', 5)

    def save():
        jobs.save_checkpoint(record['id'], owner, {'weekly_source': source, 'weekly_semantic': state})

    for index, paper in enumerate(papers):
        jobs.ensure_running(record['id'], owner)
        key = semantic.context(paper, [config['topic']])
        # Budget is the number of candidates assessed; cache hits also consume a candidate slot.
        if not enabled or index >= limit or not ready:
            paper['weekly_semantic'] = {'status': 'disabled' if not enabled else 'budget_limit' if index >= limit else 'unavailable',
                'message': '模型增强未开启' if not enabled else '本期候选评估上限外，使用关键词分' if index >= limit else '模型未配置或配置已变化，使用关键词分'}
            continue
        previous = state.get(key)
        if previous is None:
            with repo.connect() as db:
                cached = db.execute('SELECT payload FROM source_cache WHERE cache_key=?', ('semantic:'+key,)).fetchone()
                # Reuse independently completed ranker tasks with the exact same topic/model/source context.
                prior = db.execute("SELECT result_json FROM jobs WHERE skill_id='semantic_ranker' AND status='succeeded' AND json_extract(input_json,'$._semantic_context')=? LIMIT 1", (key,)).fetchone() if not cached else None
            result = json.loads(cached['payload']) if cached else json.loads(prior['result_json']) if prior else None
            if result:
                previous = state[key] = {'status': 'cached', 'result': result, 'attempted': False}
                save()
            else:
                # Persist intent before dispatch. A crash in-flight is never silently billed again.
                state[key] = {'status': 'started', 'attempted': True}
                save()
                jobs.update_progress(record['id'], owner, f'本期主题语义评估 {index+1}/{min(limit,len(papers))}', 58 + int(index/max(1,limit)*20))
                candidate = {**record, 'paper': paper, 'input': {**record['input'], '_research_interests': [config['topic']], '_semantic_context': key}}
                try:
                    result = semantic.run(candidate, owner, persist=False)
                    previous = state[key] = {'status': 'succeeded', 'result': result, 'attempted': True}
                    # Response checkpoint first; a later cache failure must not trigger another call.
                    save()
                    with repo.connect() as db:
                        db.execute('INSERT OR REPLACE INTO source_cache VALUES(?,?,?,?)', ('semantic:'+key, jobs.encode(result), jobs.utc_now(), time.time()+365*86400))
                except (llm.ModelError, ValueError) as exc:
                    previous = state[key] = {'status': 'fallback', 'attempted': True,
                        'message': str(exc), 'ambiguous': bool(getattr(exc, 'ambiguous', False))}
                    save()
        if previous['status'] == 'started':
            previous = state[key] = {**previous, 'status': 'fallback', 'message': '前次调用结果未确认，本期不自动重调；使用关键词分', 'ambiguous': True}
            save()
        paper['weekly_semantic'] = previous
    for paper in papers:
        semantic_result = paper['weekly_semantic'].get('result')
        paper['weekly_rank'] = {'base': semantic_result['score'] if semantic_result else paper['recommendation']['score'],
                               'method': 'semantic' if semantic_result else 'keyword', 'feedback_adjustment': paper['feedback']['adjustment']}
        paper['weekly_rank']['score'] = max(0, min(100, paper['weekly_rank']['base'] + paper['feedback']['adjustment']))
    papers.sort(key=lambda p: (config['source']=='ccfa_first' and p['publication']['status']=='verified', p['weekly_rank']['score'], p['published']), reverse=True)
    calls = sum(bool(item.get('attempted')) for item in state.values())
    return {'model_calls': calls, 'model_cache_hits': sum(item['status']=='cached' for item in state.values()),
            'model_successes': sum(item['status'] in {'cached','succeeded'} for item in state.values()),
            'model_failures': sum(item['status']=='fallback' for item in state.values()),
            'model_limit': limit if enabled else 0,
            'model_note': '仅对关键词初排候选在上限内评估本订阅主题；其余或失败项使用关键词分，两个分数不代表质量概率。' if enabled else '未开启模型增强，本期只使用关键词分。'}
