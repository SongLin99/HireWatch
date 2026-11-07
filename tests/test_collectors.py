import pytest
from hirewatch.collectors import baidu_job, meituan_job, byte_job, checked_page, meituan


def test_api_filters_must_return_internships():
    with pytest.raises(ValueError):
        meituan_job({'jobUnionId':'1','name':'高级工程师','jobType':'1'})
    with pytest.raises(ValueError):
        byte_job({'id':'1','title':'应届生','recruit_type':{'id':'201'}})


def test_baidu_detail_link_preserves_internship_route_type():
    assert baidu_job({'postId':'abc','name':'测试实习生'}).url=='https://talent.baidu.com/jobs/detail/INTERN/abc'


def test_repeated_pages_cannot_be_reported_as_complete():
    seen=set()
    checked_page([{'id':'1'}],seen,'id')
    with pytest.raises(ValueError):
        checked_page([{'id':'1'}],seen,'id')


def test_meituan_fields_include_real_requirements_and_timestamps():
    j=meituan_job({'jobUnionId':'1','name':'后端实习生','jobType':'2','jobSpecialCode':'6','cityList':[{'name':'北京'}],'jobDuty':'开发','jobRequirement':'Java 每周 5 天','firstPostTime':1791771841794})
    assert j.requirements=='Java 每周 5 天' and j.city=='北京'
    assert j.published_at.startswith('2026-10')


def test_meituan_uses_official_last_page_even_when_live_list_overlaps(monkeypatch):
    first={'jobUnionId':'1','name':'Python实习生','jobType':'2','jobSpecialCode':'6'}
    second={**first,'jobUnionId':'2'}
    third={**first,'jobUnionId':'3'}
    calls=[]
    def request(method,url,**kwargs):
        page=kwargs['json']['page']['pageNo'];calls.append(page)
        assert page <= 2
        return {'data':{'list':[first,second] if page==1 else [second,third], 'page':{'totalCount':4,'totalPage':2}}}
    monkeypatch.setattr('hirewatch.collectors.request_json',request)
    monkeypatch.setattr('hirewatch.collectors.PAUSE_SECONDS',0)
    assert len(meituan().jobs)==4
    assert calls==[1,2]
