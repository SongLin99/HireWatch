import hashlib
import html
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta

CHINA = timezone(timedelta(hours=8))
RULE_DEFAULTS = {
    'keywords': [], 'exclude': [], 'companies': [], 'cities': [], 'mode': 'any',
    'job_types': [], 'internship_types': [], 'departments': [], 'degrees': [],
    'tags': [], 'exclude_tags': [], 'tag_mode': 'any',
    'available_days': 0, 'available_months': 0, 'unknown_policy': 'include',
}
ANALYZED_RULE_FIELDS = ('job_types','departments','degrees','tags','exclude_tags','available_days','available_months')


def now():
    return datetime.now(CHINA).isoformat(timespec="seconds")


def clean(value):
    text = str(value or "")
    text = re.sub(r"<(?:br|/p|/li)[^>]*>", "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"[ \t]+", " ", text).strip()


def internship_evidence(title, recruitment="", *texts):
    """Return explicit hiring evidence, not a mention of previous experience."""
    fields=[]
    for value in (title,recruitment,*texts):
        text=str(value or '').strip()
        fields.append(clean(text) if '<' in text or '&' in text else text)
    negative = r'(?:不接受|不招收|不招聘|不招|不支持|不提供|谢绝|暂不接受|暂不招收|暂不招)[^。\n；;]{0,8}实习(?:生)?|(?:not accepting|no)\s+interns?\b'
    if any(re.search(negative, field, re.I) for field in fields if '实习' in field or 'intern' in field.lower()):
        return ''
    intern = r'(?<!非)实习|\bintern(?:ship)?\b'
    heading = r'(?:岗位|职位|招聘|聘用|用工|工作|雇佣|就业)(?:类别|类型|性质|形式|对象)|(?:employment|job|position|contract)\s*(?:type|category)'
    for field in fields[2:]:
        if not any(word in field.lower() for word in ('类别','类型','性质','形式','对象','type','category')):continue
        field=field.replace('**','')
        for match in re.finditer(r'[【\[]?(?:' + heading + r')\s*[】\]）)]?\s*[:：]\s*([^。\n；;]{1,80})', field, re.I):
            if re.search(intern, match[1], re.I):
                return match[0]
    for field in fields[:2]:
        if re.search(intern, field, re.I):
            return field
    for field in fields[2:]:
        if '实习' not in field and 'intern' not in field.lower():continue
        for clause in re.split(r'[\n。；;！？]', field):
            if re.search(r'(?:接受|欢迎|支持|可招|招收|招募|招聘)\s*(?:同时|也|应届生和|全职和|全职及|兼职和|优秀|在校|各类|相关专业|的|及|或|/|、|与|\s){0,12}实习生|实习生\s*(?:可|可以|也可|均可)?\s*(?:投递|申请|应聘)|(?:可|可以|支持)\s*(?:选择|以|进行)?\s*实习(?:或全职|/全职)?(?:方式|形式|入职|生)?|(?:提供|开放)\s*实习(?:岗位|机会)|(?:accept|welcome|hire|hiring)\s+(?:both\s+)?interns?\b', clause, re.I):
                return clause.strip()
    return ''


def internship_kind(title, recruitment="", *texts):
    evidence = internship_evidence(title, recruitment, *texts)
    return '实习（兼招全职）' if re.search(r'全职|full[ -]?time', evidence, re.I) else '日常实习'


def explicit_availability(text):
    """Only recognize an explicitly stated weekly minimum or internship duration."""
    digits={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
    day=re.search(r'(?:每周|一周|每星期)[^\n。；]{0,12}?([1-7一二两三四五六七])(?:\s*[-–~至到/]\s*[1-7一二两三四五六七])?\s*(?:个)?(?:工作)?天',text)
    month=re.search(r'(?:实习|至少|最少|不少于)[^\n。；]{0,12}?([1-9]\d?(?:\.\d+)?|[一二两三四五六七八九十])(?:\s*[-–~至到]\s*\d+)?\s*个?月',text)
    days=digits.get(day[1],int(day[1]) if day[1].isdigit() else None) if day else None
    months=float(month[1]) if month and re.fullmatch(r'\d+(?:\.\d+)?',month[1]) else digits.get(month[1]) if month else None
    if not months and re.search(r'实习[^\n。；]{0,12}半年',text):months=6
    return days,months


@dataclass
class Job:
    source: str
    external_id: str
    company: str
    title: str
    city: str
    category: str
    department: str
    internship_type: str
    description: str
    requirements: str
    url: str
    published_at: str = ""
    source_updated_at: str = ""

    @property
    def id(self):
        return f"{self.source}:{self.external_id}"

    @property
    def fingerprint(self):
        payload = asdict(self)
        for field in ("published_at", "source_updated_at"):
            payload.pop(field)
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def matches(job, rule):
    def raw(key, default=None):
        return job.get(key, default) if isinstance(job, dict) else getattr(job, key, default)
    def value(key):
        return str(raw(key) or '')
    if rule.get("companies") and value("source") not in rule["companies"]:
        return False
    if rule.get("cities") and not any(c.casefold() in value("city").casefold() for c in rule["cities"]):
        return False
    if rule.get('job_types') and value('job_type') not in rule['job_types']:
        return False
    if rule.get('internship_types') and not any(kind.casefold() in value('internship_type').casefold() for kind in rule['internship_types']):
        return False
    if rule.get('departments') and not any(d.casefold() in value('department').casefold() for d in rule['departments']):
        return False
    keep_unknown = rule.get('unknown_policy','include') == 'include'
    degree = value('degree') or '未说明'
    if rule.get('degrees') and degree not in rule['degrees'] and not (degree == '未说明' and keep_unknown):
        return False
    tags = {str(t).casefold() for key in ('skills','tags','labels') for t in (raw(key,[]) or [])} if rule.get('tags') or rule.get('exclude_tags') else set()
    if any(t.casefold() in tags for t in rule.get('exclude_tags',[])):
        return False
    if rule.get('tags'):
        tests = [t.casefold() in tags for t in rule['tags']]
        if not (all(tests) if rule.get('tag_mode')=='all' else any(tests)):
            return False
    if rule.get('available_days') or rule.get('available_months'):
        days,months = (None,None) if isinstance(job,dict) and 'min_days' in job and 'min_months' in job else explicit_availability(value('requirements'))
        for required, available in ((raw('min_days') or days, rule.get('available_days')), (raw('min_months') or months, rule.get('available_months'))):
            if available and (required is None and not keep_unknown or required is not None and required > available):
                return False
    if not rule.get('keywords') and not rule.get('exclude'):return True
    text = raw('_search_text') or "\n".join(value(k) for k in ("title", "company", "city", "category", "department", "internship_type", "description", "requirements", "skills", "tags", "labels")).casefold()
    if any(k.casefold() in text for k in rule.get("exclude", [])):
        return False
    keywords = rule.get("keywords", [])
    if not keywords:
        return True
    tests = [k.casefold() in text for k in keywords]
    return all(tests) if rule.get("mode") == "all" else any(tests)


def subscription_state(job, rule):
    """Share eligibility between previews, the dashboard and actual delivery."""
    if job.get('status','active') != 'active' or job.get('employment') == '非实习':
        return 'excluded'
    basic = {k:v for k,v in rule.items() if k not in ANALYZED_RULE_FIELDS}
    if not matches(job,basic):
        return 'excluded'
    if any(rule.get(k) for k in ANALYZED_RULE_FIELDS) and job.get('ai_status') != 'complete':
        return 'pending'
    return 'matched' if matches(job,rule) else 'excluded'
