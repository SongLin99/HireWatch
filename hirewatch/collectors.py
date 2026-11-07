"""Adapters for public official recruitment pages.

These interfaces are website internals rather than contracted public APIs.
Every collector finishes its pagination before a snapshot is committed.
Browser collectors use normal first-party navigation, without login or CAPTCHA bypass.
"""
import json
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode

import requests

from .models import CHINA, Job, clean

SOURCES = {
    "baidu": {"name": "百度", "url": "https://talent.baidu.com/jobs/list?recruitType=INTERN", "description": "官方实习列表与岗位详情，按页检查。"},
    "meituan": {"name": "美团", "url": "https://job.meituan.com/web/campus?hiringType=4_6", "description": "官方转正实习与日常实习，按页检查。"},
    "tencent": {"name": "腾讯", "url": "https://join.qq.com/", "description": "国内官方实习项目，包含日常、应届与专项实习。"},
    "bytedance": {"name": "字节跳动", "url": "https://jobs.bytedance.com/campus/position?type=3", "description": "浏览器加载官方日常实习与 ByteIntern 列表。"},
    "alibaba": {"name": "阿里巴巴", "url": "https://campus-talent.alibaba.com/", "description": "浏览器加载官方日常实习与研究型实习项目。"},
}
MAX_PAGES = 1000
PAUSE_SECONDS = float(os.getenv("HIREWATCH_REQUEST_PAUSE", "0.25"))
_sessions = threading.local()


@dataclass
class Result:
    jobs: list[Job]
    empty_verified: bool = False
    coverage: dict | None = None


def session():
    if not hasattr(_sessions, "http"):
        _sessions.http = requests.Session()
        _sessions.http.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36", "Accept-Language": "zh-CN,zh;q=0.9"})
    return _sessions.http


def request_json(method, url, **kwargs):
    for attempt in range(3):
        try:
            r = session().request(method, url, timeout=(8, 30), **kwargs)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(1 + attempt * 2)
                continue
            r.raise_for_status()
            r.encoding = "utf8"
            return r.json()
        except requests.RequestException:
            if attempt == 2:
                raise ValueError("官方接口请求失败，请稍后重试或核对官网") from None
        except ValueError:
            raise ValueError("官网未返回岗位数据，可能需要验证或接口已变更") from None
    raise ValueError("官方接口暂时不可用")


def stamp(value):
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000 if value > 10**11 else value, CHINA).isoformat(timespec="seconds")
        except (ValueError, OSError):
            return ""
    return str(value or "")


def label(value):
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, dict):
        for key in ("zh_cn", "i18n", "i18n_name", "name", "en_name"):
            if value.get(key):
                return label(value[key])
    return ""


def checked_page(rows, seen, key):
    if not isinstance(rows, list):
        raise ValueError("官网岗位列表格式变更")
    ids = {str(row.get(key)) for row in rows if row.get(key) is not None}
    if rows and not ids - seen:
        raise ValueError("官网重复返回同一页，已停止本轮采集，保留历史数据")
    seen.update(ids)


def baidu_job(row):
    if not row.get("postId") or not row.get("name"):
        raise ValueError("百度岗位字段缺失")
    return Job("baidu", row["postId"], "百度", clean(row["name"]), clean(row.get("workPlace")), clean(row.get("postType")), clean(row.get("bgShortName")), clean(row.get("projectType") or "实习"), clean(row.get("workContent")), clean(row.get("serviceCondition")), f"https://talent.baidu.com/jobs/detail/INTERN/{row['postId']}", stamp(row.get("publishDate")), stamp(row.get("updateDate")))


def baidu():
    http = session()
    http.headers.update({"Referer": SOURCES["baidu"]["url"], "Origin": "https://talent.baidu.com", "X-Requested-With": "XMLHttpRequest"})
    r = http.get(SOURCES["baidu"]["url"], timeout=(8, 30))
    r.raise_for_status()
    jobs, seen = [], set()
    for page in range(1, MAX_PAGES+1):
        response = request_json("POST", "https://talent.baidu.com/httservice/getPostListNew", data={"recruitType": "INTERN", "curPage": page, "pageSize": 10, "keyWord": "", "projectType": ""})
        if response.get("status") != "ok":
            raise ValueError("百度接口未接受查询，请核对访问状态或接口参数")
        data = response.get("data") or {}
        rows = data.get("list")
        checked_page(rows, seen, "postId")
        jobs.extend(baidu_job(row) for row in rows)
        if len(seen) >= int(data.get("total") or 0):
            return Result(jobs,coverage={'reported':int(data.get('total') or 0),'checked':len(seen),'included':len({j.id for j in jobs}),'scope':'官方 INTERN 项目完整分页及正文','entries':[{'url':SOURCES['baidu']['url'],'reported':int(data.get('total') or 0),'checked':len(seen)}],'complete':True})
        if not rows:
            raise ValueError("百度分页提前结束，保留上一轮记录")
        time.sleep(PAUSE_SECONDS)
    raise ValueError("百度分页超过上限，请检查接口")


def meituan_job(row):
    if not row.get("jobUnionId") or not row.get("name"):
        raise ValueError("美团岗位字段缺失")
    if str(row.get("jobType")) != "2":
        raise ValueError("美团接口未正确筛选实习岗位")
    return Job("meituan", str(row["jobUnionId"]), "美团", clean(row["name"]), " / ".join(label(c) for c in row.get("cityList", [])), clean(row.get("jobFamilyGroup")), " / ".join(label(d) for d in row.get("department", [])), "转正实习" if str(row.get("jobSpecialCode")) == "2" else "日常实习", clean(row.get("jobDuty")), clean(row.get("jobRequirement")), f"https://job.meituan.com/web/position/detail?jobUnionId={row['jobUnionId']}&highlightType=campus", stamp(row.get("firstPostTime")), stamp(row.get("refreshTime")))


def meituan():
    jobs, seen = [], set()
    for page in range(1, MAX_PAGES+1):
        payload = {"page": {"pageNo": page, "pageSize": 50}, "jobShareType": "1", "jobType": [{"code": "2", "subCode": ["2", "6"]}], "typeCode": ["2", "6"]}
        response = request_json("POST", "https://job.meituan.com/api/official/job/getJobList", json=payload)
        data = response.get("data") or {}
        rows = data.get("list")
        checked_page(rows, seen, "jobUnionId")
        jobs.extend(meituan_job(row) for row in rows)
        paging = data.get("paging") or data.get("page") or {}
        count = int(paging.get("totalCount") or 0)
        last_page = int(paging.get("totalPage") or 0)
        if (last_page and page >= last_page) or (count and len(seen) >= count) or not count and len(rows) < 50:
            return Result(jobs,coverage={'reported':count,'checked':len(seen),'included':len({j.id for j in jobs}),'scope':'官方日常实习及转正实习项目','entries':[{'url':SOURCES['meituan']['url'],'reported':count,'checked':len(seen)}],'complete':True})
        if not rows:
            raise ValueError("美团分页提前结束，保留上一轮记录")
        time.sleep(PAUSE_SECONDS)
    raise ValueError("美团分页超过上限，请检查接口")


def tencent():
    root = "https://join.qq.com"
    response = request_json("GET", root+"/api/v1/position/getProjectMapping")
    mapping = response.get("data")
    if response.get("status") != 0 or not isinstance(mapping, list):
        raise ValueError("腾讯招聘项目列表格式变更")
    ids = []
    for group in mapping:
        for project in group.get("subProjectList", []):
            if group.get("recruitType") == 2 or "实习" in str(project.get("projectName")):
                ids.append(project["mappingId"])
    if not ids:
        raise ValueError("腾讯当前未提供实习项目，请核对官网")
    rows, seen = [], set()
    for page in range(1, MAX_PAGES+1):
        response = request_json("POST", root+"/api/v1/position/searchPosition", json={"projectMappingIdList": ids, "workCountryType": 1, "pageIndex": page, "pageSize": 1000})
        if response.get("status") != 0:
            raise ValueError("腾讯未接受岗位查询")
        data = response.get("data") or {}
        chunk = data.get("positionList")
        checked_page(chunk, seen, "id")
        rows.extend(chunk)
        if len(seen) >= int(data.get("count") or 0):
            break
        if not chunk:
            raise ValueError("腾讯分页提前结束")
        time.sleep(PAUSE_SECONDS)
    else:
        raise ValueError("腾讯分页超过上限")

    def detail(row):
        time.sleep(PAUSE_SECONDS)
        response = request_json("GET", root+"/api/v1/jobDetails/getJobDetailsByPostId", params={"postId": row["postId"]})
        if response.get("status") != 0 or not isinstance(response.get("data"), dict):
            raise ValueError("腾讯岗位详情未返回，保留上一轮完整记录")
        d = response["data"]
        # IDs in the list represent a project-specific opening. Keep those instead of collapsing shared post IDs.
        return Job("tencent", str(row["id"]), "腾讯", clean(row["positionTitle"]), clean(row.get("workCities") or d.get("workCity")), clean(d.get("tidName")), clean(row.get("bgs")), clean(row.get("projectName") or row.get("recruitLabelName") or "实习"), clean(d.get("desc")), clean(d.get("request")), root+f"/post_detail.html?postid={row['postId']}&pid={row.get('projectId','')}&id={row.get('position','')}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs=list(pool.map(detail, rows))
    return Result(jobs,coverage={'reported':int(data.get('count') or 0),'checked':len(seen),'details':len(jobs),'included':len({j.id for j in jobs}),'scope':f'官网实习项目 {len(ids)} 个；国内岗位及完整详情','entries':[{'url':SOURCES['tencent']['url'],'reported':int(data.get('count') or 0),'checked':len(seen)}],'complete':True})


def browser_instance(playwright):
    preferred = os.getenv("HIREWATCH_BROWSER", "msedge")
    channels=[None,preferred,'chrome'] if getattr(sys,'frozen',False) and sys.platform=='darwin' else [preferred,'chrome',None]
    for channel in dict.fromkeys(channels):
        try:
            return playwright.chromium.launch(channel=channel, headless=True)
        except Exception:
            pass
    raise ValueError("未找到可用浏览器，请安装 Microsoft Edge 或 Google Chrome 后重新检查来源")


def byte_job(row):
    if not row.get("id") or not row.get("title"):
        raise ValueError("字节岗位字段缺失")
    if str((row.get("recruit_type") or {}).get("id")) not in {"202", "301"}:
        raise ValueError("字节返回了实习分类以外的岗位")
    cities = row.get("city_list") or [row.get("city_info") or {}]
    return Job("bytedance", row["id"], "字节跳动", clean(row["title"]), " / ".join(label(c) for c in cities), label(row.get("job_category")), "", label((row.get("job_subject") or {}).get("name")) or "实习", clean(row.get("description")), clean(row.get("requirement")), f"https://jobs.bytedance.com/campus/position/{row['id']}/detail", stamp(row.get("publish_time")))


def bytedance():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = browser_instance(p)
        try:
            page = browser.new_page(locale="zh-CN")
            def matching_response(r, offset):
                if "/search/job/posts" not in r.url or r.status != 200:
                    return False
                try:
                    return json.loads(r.request.post_data or "{}").get("offset") == offset
                except ValueError:
                    return False
            with page.expect_response(lambda r: matching_response(r, 0), timeout=60000) as response:
                page.goto("https://jobs.bytedance.com/campus/position?type=3&limit=100", wait_until="domcontentloaded", timeout=45000)
            jobs, seen = [], set()
            for index in range(MAX_PAGES):
                data = response.value.json()
                if data.get("code") != 0:
                    raise ValueError("字节招聘网站未提供岗位数据")
                payload = data.get("data") or {}
                rows = payload.get("job_post_list")
                checked_page(rows, seen, "id")
                jobs.extend(byte_job(row) for row in rows)
                if len(seen) >= int(payload.get("count") or 0):
                    return Result(jobs,coverage={'reported':int(payload.get('count') or 0),'checked':len(seen),'included':len({j.id for j in jobs}),'scope':'官网日常实习及 ByteIntern 完整分页与正文','entries':[{'url':SOURCES['bytedance']['url'],'reported':int(payload.get('count') or 0),'checked':len(seen)}],'complete':True})
                if not rows:
                    raise ValueError("字节分页提前结束")
                # Let the site's own pagination prepare its normal request.
                page.wait_for_timeout(1500 if index == 0 else 700)
                later = page.get_by_text("稍后再说", exact=True)
                if later.count() and later.first.is_visible():
                    later.first.click(timeout=10000)
                with page.expect_response(lambda r: matching_response(r, (index+1)*100), timeout=60000) as response:
                    page.locator("li.atsx-pagination-next").click(timeout=15000)
            raise ValueError("字节分页超过上限")
        finally:
            browser.close()


def ali_job(row, batch):
    if not row.get("id") or not row.get("name"):
        raise ValueError("阿里岗位字段缺失")
    return Job("alibaba", str(row["id"]), "阿里巴巴", clean(row["name"]), " / ".join(row.get("workLocations") or []), clean(row.get("categoryName")), " / ".join(row.get("circleNames") or []), clean(batch["name"]), clean(row.get("description")), clean(row.get("requirement")), f"https://campus-talent.alibaba.com/campus/position/{row['id']}", stamp(row.get("publishTime")), stamp(row.get("modifyTime")))


def alibaba():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = browser_instance(p)
        try:
            page = browser.new_page(locale="zh-CN")
            with page.expect_response(lambda r: "/searchCondition/listBatch" in r.url and r.status == 200, timeout=60000) as initial:
                page.goto("https://campus-talent.alibaba.com/campus/position", wait_until="domcontentloaded", timeout=45000)
            batches_response = initial.value.json()
            batches = (batches_response.get("content") or {}).get("internship")
            if not batches_response.get("success") or not batches:
                raise ValueError("阿里实习招聘项目未返回，请核对官网")
            # Use the normal first-party CSRF headers and browser session.
            csrf = parse_qs(urlparse(initial.value.url).query).get("_csrf")
            if not csrf:
                raise ValueError("阿里招聘页面未提供正常会话令牌")
            endpoint = "https://campus-talent.alibaba.com/position/search?" + urlencode({"_csrf": csrf[0]})
            jobs = [];entries=[]
            for batch in batches:
                seen = set()
                for index in range(1, MAX_PAGES+1):
                    response = page.evaluate("""async ({url,body}) => {
                        const response = await fetch(url, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(30000)});
                        return {ok:response.ok, payload:await response.json()};
                    }""", {"url": endpoint, "body": {"batchId": batch["id"], "pageIndex": index, "pageSize": 50, "channel": "campus_group_official_site", "language": "zh"}})
                    payload = response["payload"]
                    if not response["ok"] or not payload.get("success"):
                        raise ValueError("阿里未接受实习列表查询，请在官网检查访问状态")
                    content = payload.get("content") or {}
                    rows = content.get("datas")
                    checked_page(rows, seen, "id")
                    jobs.extend(ali_job(row, batch) for row in rows)
                    if len(seen) >= int(content.get("totalCount") or 0):
                        break
                    if not rows:
                        raise ValueError("阿里分页提前结束")
                    page.wait_for_timeout(300)
                else:
                    raise ValueError("阿里分页超过上限")
                entries.append({'url':SOURCES['alibaba']['url'],'name':clean(batch['name']),'reported':int(content.get('totalCount') or 0),'checked':len(seen)})
            return Result(jobs,coverage={'reported':sum(e['reported'] for e in entries),'checked':sum(e['checked'] for e in entries),'included':len({j.id for j in jobs}),'scope':'官网所有实习批次完整分页及正文','entries':entries,'complete':True})
        finally:
            browser.close()


COLLECTORS = {"baidu": baidu, "meituan": meituan, "tencent": tencent, "bytedance": bytedance, "alibaba": alibaba}

from .extra_sources import EXTRA_COLLECTORS, EXTRA_INFO
SOURCES.update(EXTRA_INFO)
COLLECTORS.update(EXTRA_COLLECTORS)


def collect(source):
    if source not in COLLECTORS:
        raise ValueError("未知的招聘来源")
    return COLLECTORS[source]()
