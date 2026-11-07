"""Verify onboarding and optional AI in a fresh, offline-only browser session."""
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

import uvicorn
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from hirewatch.app import create_app
from hirewatch.collectors import browser_instance


def main():
    for name in ('OPENROUTER_API_KEY', 'DEEPSEEK_API_KEY'):
        os.environ.pop(name, None)
    output = ROOT/'docs/images'
    with tempfile.TemporaryDirectory(prefix='hirewatch-public-ui-') as temporary:
        app = create_app(Path(temporary)/'empty.sqlite3', scheduler=False)
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.05)
            assert server.started
            with sync_playwright() as automation:
                browser = browser_instance(automation)
                try:
                    page = browser.new_page(viewport={'width': 1440, 'height': 1000}, locale='zh-CN')
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    base = f'http://127.0.0.1:{port}'
                    page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base+'/') else route.abort())
                    page.goto(base, wait_until='networkidle')
                    expect(page.locator('#tutorial-dialog')).to_be_visible()
                    page.locator('#tutorial-dialog').screenshot(path=str(output/'onboarding.png'))
                    page.locator('#tutorial-skip').click()
                    expect(page.locator('#saved-nav')).to_have_text('0')
                    page.screenshot(path=str(output/'interface.png'), full_page=True)
                    page.locator('[data-view="settings"]').click()
                    expect(page.locator('#openrouter-api-key')).to_have_value('')
                    expect(page.locator('#ai-enabled')).not_to_be_checked()
                    expect(page.locator('#ai-model')).to_have_value('deepseek/deepseek-v3.2')
                    page.locator('#ai-provider').select_option('deepseek')
                    expect(page.locator('#deepseek-key-fields')).to_be_visible()
                    expect(page.locator('#deepseek-api-key')).to_have_value('')
                    expect(page.locator('#ai-model')).to_have_value('deepseek-flash')
                    with page.expect_response(lambda response: '/api/settings' in response.url and response.request.method == 'PUT') as saved:
                        page.locator('#save-settings').click()
                    assert saved.value.ok
                    assert not saved.value.json()['ai']['configured']
                    assert not errors, errors
                finally:
                    browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            listener.close()
    print('Fresh profile UI passed: tutorial, empty keys, both providers, save without a key, no browser JS errors.')


if __name__ == '__main__':
    main()
