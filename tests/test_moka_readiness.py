"""Regression cases for migrated portals and asynchronously rendered counts."""
import pytest

from hirewatch.collectors import browser_instance
from hirewatch.extra_sources import moka_ready_total, moka_detail_text, moka_job, MOKA_SITES, MOKA_EXTRA_SITES, EXTRA_COLLECTORS


def test_zhipu_collects_both_new_official_portals(monkeypatch):
    calls=[]
    monkeypatch.setattr('hirewatch.extra_sources.moka',lambda source:calls.append(source))
    EXTRA_COLLECTORS['zhipu']()
    assert calls==['zhipu']
    assert MOKA_SITES['zhipu']=='https://app.mokahr.com/campus-recruitment/zphz/148984'
    assert MOKA_EXTRA_SITES['zhipu']==['https://app.mokahr.com/social-recruitment/zphz/148983']


def test_unconfirmed_empty_page_cannot_be_a_successful_snapshot():
    from playwright.sync_api import TimeoutError
    class UnloadedPage:
        def wait_for_function(self,*args,**kwargs):
            raise TimeoutError('no rendered jobs')
    with pytest.raises(ValueError,match='保留历史数据'):
        moka_ready_total(UnloadedPage())


def test_zero_placeholder_waits_for_real_count_and_cards():
    # No external network: reproduce the actual DOM transition from the bug.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try: browser=browser_instance(p)
        except ValueError: pytest.skip('No browser installed for offline DOM regression')
        try:
            page=browser.new_page()
            page.set_content('<div id="count">0 结果</div><div id="cards"></div>')
            page.evaluate('''() => {
                setTimeout(() => document.querySelector('#count').textContent='36 结果',100);
                setTimeout(() => document.querySelector('#cards').innerHTML=
                    '<a href="#/job/abc"><div class="card-content-test">实习</div></a>',300);
            }''')
            assert moka_ready_total(page)==36
            assert page.locator('a[href*="#/job/"]').count()==1
        finally: browser.close()


@pytest.mark.parametrize('title,info,expected',[
    ('渠道销售','职位名称\n渠道销售\n职位性质\n全职',False),
    ('算法实习生','职位名称\n算法实习生\n职位性质\n实习',True),
    ('算法实习生','职位名称\n其他岗位\n职位性质\n实习',None),
    ('算法实习生','职位名称\n算法实习生\n职位性质\n-',None),
])
def test_empty_jd_requires_matching_populated_metadata(title,info,expected):
    class Texts:
        def __init__(self,text):self.text=text
        def all_inner_texts(self):return [self.text]
    class Page:
        def locator(self,selector):return Texts(info if 'info-container' in selector else '')
        def wait_for_timeout(self,ms):pass
    card=dict(title=title,url='https://app.mokahr.com/social-recruitment/zphz/148983#/job/abcdef',info='',published='')
    if expected is None:
        with pytest.raises(ValueError,match='保留历史数据'):moka_detail_text(Page(),card)
    else:
        text=moka_detail_text(Page(),card)
        assert text.startswith('官方未填写职位描述')
        assert bool(moka_job({**card,'text':text},'zhipu')) is expected
