"""Topic-led discovery from official main-conference proceedings, joined to arXiv."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import re
from threading import Lock
import time
from urllib.parse import urljoin, urlparse, urlencode

import httpx
from . import repository as repo
from .sources import ArxivSource, SourceError, with_date_filter

VERSION = 'conference-discovery-v1'
HOSTS = {'proceedings.neurips.cc', 'proceedings.mlr.press', 'aclanthology.org'}
POOL_SIZE = 50
_host_locks = {host:Lock() for host in HOSTS}


def normalized_title(title):
    return re.sub(r'[^\w]', '', unescape(title).casefold())


def cache_get(key):
    with repo.connect() as db:
        row=db.execute('SELECT payload,expires_at FROM source_cache WHERE cache_key=?',(key,)).fetchone()
    return (json.loads(row['payload']), row['expires_at'] > time.time()) if row else (None,False)


def cache_put(key,value,ttl):
    with repo.connect() as db:
        db.execute('INSERT OR REPLACE INTO source_cache VALUES(?,?,?,?)',
                   (key,json.dumps(value,ensure_ascii=False),repo.utc_now(),time.time()+ttl))


class Page(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self.anchor=None; self.text=[]; self.metas={}
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='a':self.anchor=[attrs.get('href',''),[]]
        if tag=='meta' and attrs.get('name'):self.metas.setdefault(attrs['name'],[]).append(attrs.get('content',''))
    def handle_data(self,data):
        self.text.append(data)
        if self.anchor:self.anchor[1].append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self.anchor:
            self.links.append((self.anchor[0],' '.join(''.join(self.anchor[1]).split())));self.anchor=None


def html_get(url):
    host=urlparse(url).hostname
    if host not in HOSTS or urlparse(url).scheme!='https':raise ValueError('会议来源不在允许列表')
    key=VERSION+':html:'+url
    with _host_locks[host]:
        cached,fresh=cache_get(key)
        if fresh:
            if 'error' in cached:raise ValueError(cached['error'])
            return cached['html']
        try:
            with httpx.Client(timeout=20,follow_redirects=False,headers={'User-Agent':'PaperPilot/0.3 research metadata'}) as client:
                target=url
                for _ in range(3):
                    with client.stream('GET',target) as response:
                        if response.status_code in {301,302,307,308}:
                            target=urljoin(target,response.headers.get('location',''))
                            parts=urlparse(target)
                            if parts.scheme!='https' or parts.hostname!=host or parts.port not in {None,443} or parts.username:raise ValueError('会议页面重定向到未允许来源')
                            continue
                        if response.status_code!=200:raise ValueError(f'会议来源 HTTP {response.status_code}')
                        data=bytearray();started=time.monotonic()
                        for chunk in response.iter_bytes():
                            if time.monotonic()-started>30:raise ValueError('会议索引下载超过总时间上限')
                            data.extend(chunk)
                            if len(data)>16*1024*1024:raise ValueError('会议索引超过 16 MiB 上限')
                        html=bytes(data).decode('utf-8',errors='replace')
                        if 'citation_title' not in html and 'proceedings' not in html.lower() and 'anthology' not in html.lower():raise ValueError('来源没有返回论文集页面')
                        cache_put(key,{'html':html},86400)
                        return html
                raise ValueError('会议页面重定向次数超限')
        except (httpx.RequestError,ValueError) as exc:
            message=str(exc) if isinstance(exc,ValueError) else '会议来源网络超时或连接失败'
            cache_put(key,{'error':message},300)
            raise ValueError(message) from None


def plain(value):
    parser=Page();parser.feed(value)
    return ' '.join(''.join(parser.text).split())


def parse_index(venue,year,url,html):
    page=Page();page.feed(html);rows=[]
    if venue=='ICML':
        if 'International Conference on Machine Learning' not in html:return []
        for part in re.split(r'<div\s+class="paper"\s*>',html)[1:]:
            title=re.search(r'<p class="title">(.*?)</p>',part,re.S)
            link=re.search(r'<a href="(https://proceedings\.mlr\.press/v\d+/[^"/]+\.html)">abs</a>',part)
            if title and link and not plain(title[1]).lower().startswith('position:'):rows.append((link[1],plain(title[1])))
    else:
        for href,title in page.links:
            resolved=urlparse(urljoin(url,href))
            if resolved.hostname != urlparse(url).hostname:continue
            path=resolved.path
            if venue=='NeurIPS' and re.fullmatch(fr'/paper_files/paper/{year}/hash/[a-f0-9]+-Abstract-Conference.html',path):rows.append((urljoin(url,href),title))
            if venue=='ACL' and re.fullmatch(fr'/{year}\.acl-long\.\d+/',path):rows.append((urljoin(url,href),title))
    return [{'title':title,'venue':venue,'year':year,'evidence_url':link,'index_url':url,
             'evidence_text':f'{venue} {year} 正式主会'+('长文论文集' if venue=='ACL' else '论文集'),
             'verified_at':repo.utc_now()} for link,title in rows if title]


def official_index(days):
    now=datetime.now(timezone.utc)
    first=max(now.year-2,(now-timedelta(days=days)).year) if days else now.year-2
    years=list(range(first,now.year+1))
    specs=[];statuses=[]
    # Discover the ICML volumes from PMLR itself instead of guessing volume numbers.
    try:
        root=html_get('https://proceedings.mlr.press/')
        for item in re.findall(r'<li[^>]*>(.*?)</li>',root,re.S):
            match=re.search(r'Proceedings of ICML (\d{4})',plain(item))
            link=re.search(r'href="(?:https://proceedings.mlr.press)?(/?v\d+/?)"',item)
            if match and link and int(match[1]) in years:
                specs.append(('ICML',int(match[1]),urljoin('https://proceedings.mlr.press/',link[1].rstrip('/')+'/')))
    except ValueError as exc:statuses.append({'source':'PMLR','status':'unavailable','message':str(exc)})
    for year in years:
        # NeurIPS 2025 separates the main track in a volume-specific index.
        suffix=f'/vol{year-1987}-main-conference' if year>=2025 else ''
        specs.extend([('NeurIPS',year,f'https://proceedings.neurips.cc/paper_files/paper/{year}{suffix}'),
                      ('ACL',year,f'https://aclanthology.org/events/acl-{year}/')])
    def fetch(spec):
        venue,year,url=spec
        try:
            rows=parse_index(venue,year,url,html_get(url))
            return rows,{'source':f'{venue} {year}','url':url,'status':'available' if rows else 'unavailable','count':len(rows),
                         'message':'' if rows else '未识别到正式主会论文，未推测录用'}
        except ValueError as exc:return [],{'source':f'{venue} {year}','url':url,'status':'unavailable','message':str(exc)}
    rows=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for papers,status in pool.map(fetch,specs):rows.extend(papers);statuses.append(status)
    return rows,statuses


def topic_terms(query):
    phrases=re.findall(r'(?:all|ti|abs):"([^"]+)"',query)
    if not phrases:phrases=re.findall(r'(?:all|ti|abs):([\w-]+)',query)
    return list(dict.fromkeys(p.casefold() for p in phrases))[:30]


def relevance(title,terms):
    text=re.sub(r'[-–—]',' ',title.casefold())
    return sum(1 for term in terms if re.search(r'(?<!\w)'+re.escape(re.sub(r'[-–—]',' ',term))+r'(?!\w)',text))


def verify_entry(paper,record):
    """Confirm title AND an author on the official landing page before assigning acceptance."""
    try:
        page=Page();page.feed(html_get(record['evidence_url']))
    except ValueError:return None
    titles=page.metas.get('citation_title',[])
    def author_key(name):return tuple(sorted(re.findall(r'\w+',name.casefold())))
    source_authors={author_key(a) for a in page.metas.get('citation_author',[]) if a.strip()}
    paper_authors={author_key(a) for a in paper.get('authors',[]) if a.strip()}
    if not any(normalized_title(t)==normalized_title(paper['title']) for t in titles) or not source_authors.intersection(paper_authors):return None
    evidence={**record,'matched_authors':sorted(source_authors.intersection(paper_authors)), 'method':'official_title_and_author'}
    cache_put(VERSION+':evidence:'+normalized_title(paper['title']),evidence,86400*30)
    return evidence


def evidence_for(paper):
    record,_=cache_get(VERSION+':evidence:'+normalized_title(paper['title']))
    authors={tuple(sorted(re.findall(r'\w+',name.casefold()))) for name in paper.get('authors',[])}
    if record and record.get('method')=='official_title_and_author' and any(tuple(a) in authors for a in record.get('matched_authors',[])) and normalized_title(record['title'])==normalized_title(paper['title']):return record
    return None


def source_links(query,days):
    text=' OR '.join('"'+term+'"' for term in topic_terms(query)[:8]) or query
    scholar={'q':text+' (NeurIPS OR ICML OR ACL)'}
    if days:scholar['as_ylo']=str((datetime.now(timezone.utc)-timedelta(days=days)).year)
    return [{'name':'Google Scholar 同主题检索','url':'https://scholar.google.com/scholar?'+urlencode(scholar),'automatic':False},
            {'name':'arXiv 同主题检索','url':'https://arxiv.org/search/?'+urlencode({'query':text,'searchtype':'all'}),'automatic':False}]


_build_lock=Lock()

def search(query,days):
    key=VERSION+':pool:'+hashlib.sha256(json.dumps([query,days]).encode()).hexdigest()
    with _build_lock:
        cached,fresh=cache_get(key)
        if fresh:return {**cached,'cached':True}
        warnings=[];queries=[];can_recall=True;base_failed=False
        deadline=time.monotonic()+60
        try:base=ArxivSource().search(query,0,POOL_SIZE,days,'match')
        except SourceError as exc:
            if exc.status == 400:raise
            base_failed=True
            can_recall = exc.status != 429
            base={'papers':[],'total':0,'query':with_date_filter(query,days),'cached':False,'stale':False,'fetched_at':repo.utc_now()}
            warnings.append(str(exc))
        if base.get('warning'):warnings.append(base['warning'])
        records,statuses=official_index(days)
        terms=topic_terms(query)
        title_map={normalized_title(row['title']):row for row in records}
        checked=0
        for paper in base['papers']:
            evidence=title_map.get(normalized_title(paper['title']))
            if evidence and checked<8 and time.monotonic()<deadline:
                verify_entry(paper,evidence);checked+=1
        candidates=sorted((r for r in records if relevance(r['title'],terms)),
                          key=lambda r:(relevance(r['title'],terms),r['year']),reverse=True)
        # Extra arXiv recall preserves the ENTIRE original query, including AND/NOT and dates.
        candidates=[r for r in candidates if normalized_title(r['title']) not in {normalized_title(p['title']) for p in base['papers']}][:12]
        extra=[]
        if candidates and can_recall and time.monotonic()<deadline:
            titles=' OR '.join('ti:"'+re.sub(r'[^\w\s-]',' ',r['title'])+'"' for r in candidates)
            actual=f'({query}) AND ({titles})'
            queries.append(with_date_filter(actual,days))
            try:
                recall=ArxivSource().search(actual,0,30,days,'match');queries[-1]=recall['query']
                for paper in recall['papers']:
                    evidence=title_map.get(normalized_title(paper['title']))
                    if evidence:
                        if time.monotonic()<deadline:verify_entry(paper,evidence)
                        extra.append(paper)
            except SourceError as exc:warnings.append('会议候选匹配 arXiv 失败：'+str(exc))
        unique={}
        for paper in base['papers']+extra:
            if paper['id'] not in unique or paper['version']>unique[paper['id']]['version']:unique[paper['id']]=paper
        if time.monotonic()>=deadline:warnings.append('本轮补充核验时间已达上限，保留已获取的 arXiv 结果；其余论文保持录用未核实。')
        if not any(status['status']=='available' for status in statuses):warnings.append('正式会议来源暂不可用，本次使用 arXiv 结果与已有证据；未核实论文不标 CCF A。')
        elif any(status['status']=='unavailable' for status in statuses):warnings.append('部分年份的正式论文集尚不可用，覆盖情况见来源记录。')
        if not unique and base_failed:raise SourceError('；'.join(warnings))
        result={**base,'papers':list(unique.values()),'total':len(unique),'cached':False,
                'warning':'；'.join(warnings),'discovery':{'scope':'bounded_candidate_pool','base_limit':POOL_SIZE,'recall_candidates':len(candidates),
                'added':len(extra),'indexed_papers':len(records),'sources':statuses,'queries':[base['query'],*queries],
                'links':source_links(query,days),'note':'本轮最多 50 篇主题候选，加上正式会议标题召回；仅展示成功匹配 arXiv 且满足原检索式的论文。按首次公开日期过滤，会议年份不替代发表日期。Google Scholar 链接由用户打开，不自动抓取。'}}
        cache_put(key,result,300 if base_failed else 1800)
        return result
