"""Additional official internship sources. Mixed career sites are filtered before storage."""
import json
import re
import time
import os
import hashlib
import sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from .models import Job, clean, internship_evidence, internship_kind
from .paths import DATA_DIR
from .collectors import Result, browser_instance, request_json, label, stamp, checked_page, PAUSE_SECONDS

EXTRA_INFO = {
    "baichuan":{"name":"百川智能", "url":"https://careers.baichuan-inc.com/", "description":"官方日常实习、校园及社会招聘入口；保留正文明确接受实习的岗位。"},
    "modelbest":{"name":"面壁智能", "url":"https://modelbest.jobs.feishu.cn/campus", "description":"官方校招与社招完整列表，包含专项及日常实习。"},
    "baai":{"name":"北京智源研究院", "url":"https://app.mokahr.com/campus-recruitment/baai/42174", "description":"智源官方校招及社招职位库中的研究与其他实习岗位。"},
    "shengshu":{"name":"生数科技", "url":"https://shengshu.jobs.feishu.cn/", "description":"官方校招与社招完整列表；核查岗位正文中的实习条件。"},
    "zhipu":{"name":"智谱", "url":"https://www.zhipuai.cn/zh/joinus", "description":"官网链接的 Moka 校招及社招入口，检查完整正文中的实习机会。"},
    "mihoyo":{"name":"米哈游", "url":"https://jobs.mihoyo.com/#/campus", "description":"官方校园招聘的实习生投递项目。"},
    "minimax":{"name":"MiniMax", "url":"https://vrfi1sk8a0.jobs.feishu.cn/", "description":"官方招聘页中的实习与可实习职位。"},
    "deepseek":{"name":"DeepSeek", "url":"https://talent.deepseek.com/", "description":"官网链接的 Moka 职位库，保留明确可实习的岗位。"},
    "shailab":{"name":"上海 AI Lab", "url":"https://www.shlab.org.cn/joinus/campus", "description":"上海人工智能实验室官方实习项目。"},
    "kimi":{"name":"月之暗面 Kimi", "url":"https://careers.kimi.com/", "description":"Kimi 官网链接的招聘库中的实习岗位。"},
    "stepfun":{"name":"阶跃星辰", "url":"https://app.mokahr.com/campus-recruitment/step/94905#/jobs", "description":"官方校园招聘与 StepStar 的实习职位。"},
}


def intern_evidence(title, recruitment="", requirements=""):
    return bool(internship_evidence(title,recruitment,requirements))


def feishu_job(row,source,base,path):
    recruitment=label(row.get('recruit_type'))
    if not internship_evidence(row.get('title'),recruitment,row.get('description'),row.get('requirement')):return None
    cities=row.get('city_list') or [row.get('city_info') or {}]
    subject=label((row.get('job_subject') or {}).get('name'))
    kind=internship_kind(row['title'],recruitment,row.get('description'),row.get('requirement'))
    department=label((row.get('job_post_info') or {}).get('department'))
    return Job(source,str(row['id']),EXTRA_INFO[source]['name'],clean(row['title']),' / '.join(label(c) for c in cities),
               label(row.get('job_function')) or label(row.get('job_category')),department,kind if '兼招' in kind else subject or kind,
               clean(row.get('description')),clean(row.get('requirement')),f"{base}/{path}/position/{row['id']}/detail",stamp(row.get('publish_time')))


def feishu(source,base,paths):
    from playwright.sync_api import sync_playwright
    jobs=[];entries=[]
    with sync_playwright() as p:
        browser=browser_instance(p)
        try:
            for path in paths:
                page=browser.new_page(locale='zh-CN')
                def expected(response,offset=None):
                    if '/search/job/posts' not in response.url or response.status!=200:return False
                    if offset is None:return True
                    try:return json.loads(response.request.post_data or '{}').get('offset')==offset
                    except ValueError:return False
                url=f'{base}/{path}?limit=100' if path.endswith('/position/list') else f'{base}/{path}/?limit=100' if path else f'{base}/?limit=100'
                with page.expect_response(lambda r:expected(r),timeout=60000) as response:
                    navigation=page.goto(url,wait_until='domcontentloaded',timeout=45000)
                    if navigation and navigation.status>=400:
                        raise ValueError(f'{EXTRA_INFO[source]["name"]}招聘入口返回 HTTP {navigation.status}，可能已迁移；保留历史数据')
                detail_path=path.split('/position/')[0] if '/position/' in path else path or 'index'
                seen=set();offset=0
                for _ in range(1000):
                    payload=response.value.json()
                    if payload.get('code')!=0:raise ValueError(f'{EXTRA_INFO[source]["name"]}官网暂未提供岗位列表')
                    data=payload.get('data') or {};rows=data.get('job_post_list')
                    checked_page(rows,seen,'id')
                    for row in rows:
                        job=feishu_job(row,source,base,detail_path)
                        if job:jobs.append(job)
                    if len(seen)>=int(data.get('count') or 0):break
                    if not rows:raise ValueError('官方招聘分页提前结束，保留历史数据')
                    size=json.loads(response.value.request.post_data)['limit'];offset+=size
                    page.wait_for_timeout(600)
                    with page.expect_response(lambda r:expected(r,offset),timeout=60000) as response:
                        page.locator('li.atsx-pagination-next').click(timeout=15000)
                else:raise ValueError('官方分页超过上限')
                entries.append({'url':page.url,'reported':int(data.get('count') or 0),'checked':len(seen)})
                page.close()
        finally:browser.close()
    jobs=list({j.id:j for j in jobs}.values())
    return Result(jobs,empty_verified=True,coverage={'entries':entries,'checked':sum(e['checked'] for e in entries),'included':len(jobs),'scope':'已配置官方入口；完整职位描述和要求','complete':True})


def shailab_mode(mode):
    jobs=[];seen=set();token='';tokens=set()
    for _ in range(1000):
        params={'mode':mode,'limit':7}
        if token:params['page_token']=token
        payload=request_json('GET','https://www.shlab.org.cn/api/getJobList',params=params,headers={'Referer':'https://www.shlab.org.cn/joinus/'+mode})
        if payload.get('errno')!=0:raise ValueError('上海 AI Lab 列表未返回成功状态')
        data=payload.get('data') or {};rows=data.get('items');checked_page(rows,seen,'id')
        for row in rows:
            recruitment=label(row.get('job_recruitment_type'))
            if not internship_evidence(row.get('title'),recruitment,row.get('description'),row.get('requirement')):continue
            addresses=row.get('address_list') or [row.get('address') or {}]
            cities=list(dict.fromkeys(label(a.get('city',{}).get('name')) for a in addresses))
            jobs.append(Job('shailab',str(row['id']),'上海 AI Lab',clean(row['title']),' / '.join(cities),
                label(row.get('job_function') or row.get('job_type')),label((row.get('job_department') or {}).get('name')),
                internship_kind(row['title'],recruitment,row.get('description'),row.get('requirement')) if internship_kind(row['title'],recruitment,row.get('description'),row.get('requirement'))=='实习（兼招全职）' else '研究型实习' if '研究' in row['title'] else '日常实习',clean(row.get('description')),clean(row.get('requirement')),
                f"https://www.shlab.org.cn/joinus/detail/{row['id']}?mode={mode}",stamp(int(row.get('create_time') or 0)) if row.get('create_time') else '',stamp(int(row.get('modify_time') or 0)) if row.get('modify_time') else ''))
        if not data.get('has_more'):return jobs,{'url':'https://www.shlab.org.cn/joinus/'+mode,'checked':len(seen)}
        token=data.get('page_token')
        if not token or token in tokens:raise ValueError('上海 AI Lab 分页令牌未更新')
        tokens.add(token);time.sleep(PAUSE_SECONDS)
    raise ValueError('上海 AI Lab 分页超过上限')


def shailab():
    jobs={};entries=[]
    for mode in ('campus','social'):
        chunk,entry=shailab_mode(mode)
        entries.append(entry)
        for job in chunk:jobs.setdefault(job.id,job)
    return Result(list(jobs.values()),empty_verified=True,coverage={'entries':entries,'checked':sum(e['checked'] for e in entries),'included':len(jobs),'scope':'校招及社招完整分页；官网描述和要求','complete':True})


MOKA_SITES={
    'baai':'https://app.mokahr.com/campus-recruitment/baai/42174',
    'zhipu':'https://app.mokahr.com/campus-recruitment/zphz/148984',
    'deepseek':'https://app.mokahr.com/social-recruitment/high-flyer/140576',
    'kimi':'https://app.mokahr.com/apply/moonshot/148506',
    'stepfun':'https://app.mokahr.com/campus-recruitment/step/94905',
}

# Both destinations are linked by the company's official Join Us page. Some
# career portals do not link back to their sibling campus/social portal.
MOKA_EXTRA_SITES={
    'baai':['https://app.mokahr.com/social-recruitment/baai/42173'],
    'zhipu':['https://app.mokahr.com/social-recruitment/zphz/148983'],
}


def moka_ready_total(page):
    """A response event precedes rendering: the initial '0 results' is not data.

    Zero is deliberately inconclusive without a contracted empty-state signal;
    retain the last successful snapshot rather than mark every job unavailable.
    """
    from playwright.sync_api import TimeoutError as BrowserTimeout
    try:
        page.wait_for_function(r"""() => {
            const m = document.body.innerText.match(/(\d+)\s*结果/);
            return m && Number(m[1]) > 0 &&
                document.querySelector('a[href*="#/job/"] [class^="card-content-"]');
        }""",timeout=20000)
    except BrowserTimeout:
        raise ValueError('官网岗位列表尚未加载完成或显示零结果，无法确认空列表；保留历史数据，请稍后重试') from None
    count=re.search(r'(\d+)\s*结果',page.locator('body').inner_text())
    if not count or int(count.group(1))<=0:
        raise ValueError('官网岗位总数尚未稳定，保留历史数据')
    return int(count.group(1))


def moka_cards(page):
    return page.locator('a[href*="#/job/"]').evaluate_all("""els => els.filter(e=>e.querySelector('[class^="card-content-"]')).map(e=>({
        url:e.href,title:e.querySelector('[class^="title-"]')?.innerText || '',
        info:e.querySelector('[class^="info-"]')?.innerText || '',text:e.innerText,
        published:e.querySelector('[class^="published-at-"]')?.innerText || ''
    }))""")


def moka_job(card,source):
    if not internship_evidence(card['title'],card['info'],card['text']):return None
    match=re.search(r'#/job/([a-f0-9-]+)',card['url'])
    if not match or not card['title']:raise ValueError('官方岗位卡片缺少 ID 或标题')
    # Keep public original text intact. AI normalizes fields later without guessing a city.
    cities=list(dict.fromkeys(re.findall(r'(北京|上海|杭州|深圳|广州|成都|武汉|南京|西安|苏州|合肥|重庆|天津|乌兰察布|香港|新加坡)(?:市)?',card['info'])))
    info=[clean(v) for v in card['info'].splitlines() if v.strip() and v.strip() not in {'|','实习','全职'}]
    category=next((v for v in info if not any(c in v for c in cities)), '')
    text=clean(card['text'])
    requirements=re.split(r'[【\[]?(?:职位要求|岗位要求|任职要求|你需要具备)[】\]]?[:：]?',text,maxsplit=1)
    published=re.search(r'\d{4}-\d{2}-\d{2}',card['published'])
    return Job(source,match.group(1),EXTRA_INFO[source]['name'],clean(card['title']),' / '.join(cities),category,'',
        internship_kind(card['title'],card['info'],text),text,requirements[-1] if len(requirements)>1 else '',card['url'],published.group(0) if published else '')


class MokaDetailCache:
    """Cache public full JDs for one hour, including jobs without internship evidence.

    A list change invalidates the entry immediately. Expiry checks detail-only edits.
    This cache makes no LLM calls and is separate from the AI fingerprint cache.
    """
    VERSION = 'full-description-v1'
    TTL = 3600

    def __init__(self, path=None):
        self.path = Path(path or os.getenv('HIREWATCH_DETAIL_CACHE', str(DATA_DIR / 'moka_details.sqlite3')))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS details (url TEXT PRIMARY KEY, signature TEXT NOT NULL, fetched REAL NOT NULL, text TEXT NOT NULL)')

    def signature(self, card):
        return hashlib.sha256(json.dumps([self.VERSION,card['title'],card['info'],card['text']],ensure_ascii=False).encode()).hexdigest()

    def get(self, card):
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT signature,fetched,text FROM details WHERE url=?',(card['url'],)).fetchone()
        if row and row[0] == self.signature(card) and time.time()-row[1] < self.TTL:
            return row[2]
        return None

    def put(self, card, text):
        if not text.strip():raise ValueError('官方岗位详情为空，保留历史数据')
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT OR REPLACE INTO details VALUES(?,?,?,?)',(card['url'],self.signature(card),time.time(),text))


def moka_detail(page, card):
    # Each navigation starts from a fresh DOM so an old job cannot satisfy the wait.
    page.goto(card['url'],wait_until='domcontentloaded',timeout=45000)
    page.get_by_text(card['title'],exact=True).first.wait_for(timeout=25000)
    descriptions=page.locator('[class^="job-description-"]:visible')
    descriptions.first.wait_for(timeout=25000)
    return moka_detail_text(page,card)


def moka_detail_text(page,card):
    descriptions=page.locator('[class^="job-description-"]:visible')
    text='\n\n'.join(dict.fromkeys(t.strip() for t in descriptions.all_inner_texts() if t.strip()))
    if not text:
        # Some migrated records really have an empty JD. Give rendering a chance
        # to finish, then require the matching job's populated metadata panel.
        page.wait_for_timeout(1000)
        text='\n\n'.join(dict.fromkeys(t.strip() for t in descriptions.all_inner_texts() if t.strip()))
        if not text:
            info='\n'.join(page.locator('[class^="info-container-"]:visible').all_inner_texts())
            if card['title'] not in info or '职位名称' not in info or not re.search(r'职位性质\s+(?:职位性质\s+)?(?:全职|实习|兼职|其他)',info):
                raise ValueError('官方岗位详情未完整加载，保留历史数据')
            return clean('官方未填写职位描述；以下为该岗位的公开信息：\n'+info)
    # Only the job description; navigation and related openings are excluded.
    return clean(text)


def moka_listing(page,base):
    seen=set();cards=[];links=[]
    for index in range(1,1001):
        def expected(response):
            if '/website/jobs/v2' not in response.url or response.url.endswith('Aggregations') or response.status!=200:return False
            try:return json.loads(response.request.post_data or '{}').get('offset')==(index-1)*30
            except ValueError:return False
        # A fresh document prevents the previous page's count/cards satisfying
        # readiness while the next response is still being rendered.
        page.goto('about:blank')
        with page.expect_response(expected,timeout=60000):
            page.goto(base+f'#/jobs?page={index}',wait_until='domcontentloaded',timeout=45000)
        total=moka_ready_total(page)
        if index==1:
            links=page.locator('a').evaluate_all("""els => els.filter(e => /校招|实习|校园|社招|社会招聘/.test(e.innerText) && /^https:\/\/app\.mokahr\.com\//.test(e.href)).map(e=>e.href)""")
        page.locator('a[href*="#/job/"] [class^="card-content-"]').first.wait_for(timeout=20000)
        page.wait_for_timeout(350)
        chunk=moka_cards(page)
        ids={re.search(r'#/job/([a-f0-9-]+)',c['url']).group(1) for c in chunk}
        if not ids-seen:raise ValueError('招聘网页重复或为空页，保留历史数据')
        seen.update(ids);cards.extend(chunk)
        if index*30>=total:
            if len(seen)<total:raise ValueError('实际检查的岗位数小于官网总数，请重试完整分页；保留历史数据')
            return cards,{'url':base+'#/jobs','reported':total,'checked':len(seen)},links
    raise ValueError('招聘网页分页超过上限')


def moka(source):
    from playwright.sync_api import sync_playwright
    jobs={};all_cards=[];entries=[];visited=set();queue=[MOKA_SITES[source],*MOKA_EXTRA_SITES.get(source,[])]
    cache=MokaDetailCache();cached=fetched=empty_details=0
    with sync_playwright() as p:
        browser=browser_instance(p)
        try:
            page=browser.new_page(locale='zh-CN')
            while queue:
                base=queue.pop(0)
                if '/su/' in base:
                    page.goto(base,wait_until='domcontentloaded',timeout=45000)
                    page.wait_for_url(re.compile(r'app\.mokahr\.com/(?:apply|campus-recruitment|social-recruitment)/'),timeout=25000)
                    base=page.url
                base=base.split('#')[0].split('?')[0].rstrip('/')
                if base in visited:continue
                if len(visited)>=8:raise ValueError('官网招聘入口超过检查上限，保留历史数据')
                visited.add(base)
                cards,entry,links=moka_listing(page,base)
                all_cards.extend(cards);entries.append(entry)
                queue.extend(links)
            detail_page=browser.new_page(locale='zh-CN')
            for card in {c['url']:c for c in all_cards}.values():
                # Inspect every detail before exclusion, even a list tagged full-time.
                text=cache.get(card)
                if text is None:
                    detail_page.goto('about:blank')
                    text=moka_detail(detail_page,card)
                    cache.put(card,text)
                    fetched+=1
                    time.sleep(PAUSE_SECONDS)
                else:cached+=1
                if text.startswith('官方未填写职位描述'):empty_details+=1
                job=moka_job({**card,'text':text},source)
                if job:jobs.setdefault(job.id,job)
            unique_ids={re.search(r'#/job/([a-f0-9-]+)',c['url']).group(1) for c in all_cards}
            return Result(list(jobs.values()),empty_verified=True,coverage={'reported':sum(e['reported'] for e in entries),'checked':sum(e['checked'] for e in entries),'unique_checked':len(unique_ids),'details':fetched+cached,'empty_details':empty_details,'included':len(jobs),'excluded':len(unique_ids)-len(jobs),'cached_details':cached,'fetched_details':fetched,'scope':'官方校招及社招入口；正文或官网明确展示的岗位信息','entries':entries,'complete':True})
        finally:browser.close()


def mihoyo():
    root='https://ats.openout.mihoyo.com/ats-portal/v1/job/'
    headers={'Origin':'https://jobs.mihoyo.com','Referer':'https://jobs.mihoyo.com/'}
    rows=[];seen=set()
    for index in range(1,1001):
        response=request_json('POST',root+'list',headers=headers,json={'projectIds':['4'],'pageNo':index,'pageSize':100,'channelDetailIds':[1],'hireType':1})
        if response.get('code')!=0 or not response.get('success'):raise ValueError('米哈游官方列表请求失败')
        data=response.get('data') or {};chunk=data.get('list');checked_page(chunk,seen,'id');rows.extend(chunk)
        if len(seen)>=int(data.get('total') or 0):break
        if not chunk:raise ValueError('米哈游实习分页提前结束')
        time.sleep(PAUSE_SECONDS)
    else:raise ValueError('米哈游分页超过上限')
    def detail(row):
        if not intern_evidence(row.get('title',''),row.get('jobNature','')):raise ValueError('米哈游返回了实习以外的招聘项目')
        time.sleep(PAUSE_SECONDS)
        response=request_json('POST',root+'info',headers=headers,json={'id':row['id'],'channelDetailIds':[1],'hireType':1})
        if response.get('code')!=0 or not response.get('success') or not response.get('data'):raise ValueError('米哈游岗位详情未完整返回')
        d=response['data']
        requirements=clean(d.get('jobRequire'))
        if d.get('objectName'):requirements+='\n招聘对象：'+d['objectName']
        if d.get('addition'):requirements+='\n'+clean(d['addition'])
        return Job('mihoyo',str(d['id']),'米哈游',clean(d['title']),' / '.join(clean(a.get('addressDetail')) for a in d.get('addressDetailList',[])),
            clean(d.get('competencyType')),'','日常实习',clean(d.get('description')),requirements,
            f"https://jobs.mihoyo.com/#/campus/position/{d['id']}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs=list(pool.map(detail,rows))
    return Result(jobs,empty_verified=True,coverage={'reported':int(data.get('total') or 0),'checked':len(seen),'details':len(jobs),'included':len(jobs),'scope':'官方实习生项目完整分页及详情','entries':[{'url':'https://jobs.mihoyo.com/#/campus','reported':int(data.get('total') or 0),'checked':len(seen)}],'complete':True})


EXTRA_COLLECTORS={
    'baichuan':lambda:feishu('baichuan','https://cq6qe6bvfr6.jobs.feishu.cn',['646926','baichuanzhaopin']),
    'modelbest':lambda:feishu('modelbest','https://modelbest.jobs.feishu.cn',['campus/position/list','career/position/list']),
    'baai':lambda:moka('baai'),
    'shengshu':lambda:feishu('shengshu','https://shengshu.jobs.feishu.cn',['index/position/list','692892/position/list']),
    'zhipu':lambda:moka('zhipu'),
    'minimax':lambda:feishu('minimax','https://vrfi1sk8a0.jobs.feishu.cn',['','379481']),
    'deepseek':lambda:moka('deepseek'),'kimi':lambda:moka('kimi'),'stepfun':lambda:moka('stepfun'),
    'shailab':shailab,'mihoyo':mihoyo,
}
