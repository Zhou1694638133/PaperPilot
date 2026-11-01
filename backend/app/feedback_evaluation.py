"""Exploratory comparison using explicit researcher labels, never invented ground truth."""
import json
from datetime import datetime, timezone
from . import repository as repo
from .ranking import rank_paper, matches_filters
from .semantic import context
from .research import publication


def evaluate_feedback(mode='live'):
    profile=repo.get_profile();now=datetime.now(timezone.utc)
    with repo.connect() as db:
        rows=db.execute("SELECT paper_id,value,reason FROM recommendation_feedback WHERE mode=? AND value IN ('relevant','not_relevant')",(mode,)).fetchall()
    samples=[];paired=[]
    for row in rows:
        paper=repo.get_paper(row['paper_id'],mode)
        if not paper or not matches_filters(paper,profile,730,now):continue
        if datetime.fromisoformat(paper['published'].replace('Z','+00:00'))>now:continue
        key=context(paper,profile['interests'])
        with repo.connect() as db:
            found=db.execute("SELECT result_json FROM jobs WHERE skill_id='semantic_ranker' AND status='succeeded' AND json_extract(input_json,'$._semantic_context')=? ORDER BY created_at DESC LIMIT 1",(key,)).fetchone()
        score=json.loads(found['result_json'])['score'] if found else None
        sample={'id':paper['id'],'title':paper['title'],'label':row['value'],'reason':row['reason'],
                'keyword_score':rank_paper(paper,profile,'feedback-evaluation')['score'], 'semantic_score':score,
                'verified_ccfa':publication(paper)['status']=='verified'}
        samples.append(sample)
        if score is not None:paired.append(sample)
    metrics=None
    if len(paired)>=30:
        def precision(field):
            top=sorted(paired,key=lambda p:(p['verified_ccfa'],p[field],p['id']),reverse=True)[:10]
            return {'precision_at_10':sum(p['label']=='relevant' for p in top)/len(top),'ids':[p['id'] for p in top]}
        metrics={'keyword':precision('keyword_score'),'semantic':precision('semantic_score')}
    return {'label_count':len(samples),'paired_count':len(paired),'target_minimum':30,'target_recommended':50,
            'interests':profile['interests'],'window_days':730,'metrics':metrics,'samples':samples,
            'note':'仅使用你明确标注的相关/不相关论文；过于基础和缺少实验不当作不相关。对同一已标注候选集比较关键词与当前兴趣/模型语义结果，CCF A 优先，未添加反馈分以避免标签泄漏。这是选择偏差下的探索性对照，不是全库准确率，也不是独立留出集评测；不足30个可对照样本不显示指标。'}
