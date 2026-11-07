import json
import re
import threading
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs

import requests

from .models import subscription_state, now


def validate_secret(provider, value):
    if not value:
        return
    if provider == "serverchan" and not re.fullmatch(r"SCT[A-Za-z0-9]+", value):
        raise ValueError("请输入 Server酱 Turbo 的 SCT 开头 SendKey")
    if provider == "wecom":
        u = urlparse(value)
        if (u.scheme != "https" or u.netloc != "qyapi.weixin.qq.com"
                or u.path != "/cgi-bin/webhook/send" or not parse_qs(u.query).get("key")):
            raise ValueError("请输入企业微信官方群机器人的完整 Webhook 地址")


def send_message(config, title, body):
    # Never return exception strings: HTTP errors can contain the credential-bearing URL.
    provider = config["provider"]
    try:
        if provider == "serverchan":
            if not config["sendkey"]:
                raise ValueError("尚未配置 Server酱 SendKey")
            response = requests.post(f"https://sctapi.ftqq.com/{config['sendkey']}.send",
                                     data={"title": title[:32], "desp": body}, timeout=(8, 20))
            response.raise_for_status()
            data = response.json()
            if data.get("code") != 0:
                raise ValueError("Server酱拒绝发送，请在服务控制台检查额度和密钥")
        elif provider == "wecom":
            if not config["webhook"]:
                raise ValueError("尚未配置企业微信 Webhook")
            response = requests.post(config["webhook"], json={"msgtype": "markdown", "markdown": {"content": body}}, timeout=(8, 20))
            response.raise_for_status()
            if response.json().get("errcode") != 0:
                raise ValueError("企业微信拒绝发送，请检查机器人配置和消息频率")
        else:
            raise ValueError("不支持的推送渠道")
    except requests.RequestException:
        raise ValueError("推送服务网络请求失败，待下一次检查重试") from None
    except (json.JSONDecodeError, KeyError):
        raise ValueError("推送服务响应格式异常") from None


class Notifier:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()

    def test(self):
        with self.lock:
            config = self.store.settings()["notifications"]
            self._send(config, "招聘雷达测试通知", "**招聘雷达已连接**\n\n这是一条由你主动触发的测试消息。", [], test=True)

    def _send(self, config, title, body, ids, test=False):
        stamp = now()
        error = ""
        try:
            send_message(config, title, body)
        except ValueError as exc:
            error = str(exc)
        with self.store.connection() as db:
            db.execute("INSERT INTO deliveries(provider,created_at,success,message,count) VALUES(?,?,?,?,?)",
                       (config["provider"], stamp, int(not error), error or ("测试成功" if test else "推送服务已接受"), len(ids)))
            if ids:
                placeholders = ','.join('?' for _ in ids)
                if error:
                    db.execute(f"UPDATE events SET attempts=attempts+1,error=? WHERE id IN ({placeholders})", (error, *ids))
                else:
                    db.execute(f"UPDATE events SET delivery='sent',sent_at=?,error='' WHERE id IN ({placeholders})", (stamp, *ids))
        if test and error:
            raise ValueError(error)

    def dispatch(self):
        if not self.lock.acquire(blocking=False):
            return
        try:
            settings = self.store.settings()
            config = settings["notifications"]
            secret = config["sendkey"] if config["provider"] == "serverchan" else config["webhook"]
            if not config["enabled"] or not secret:
                return
            attempts = self.store.rows("SELECT * FROM deliveries ORDER BY id DESC")
            stamp = datetime.fromisoformat(now())
            today = stamp.date().isoformat()
            # Count all attempts to cap retries as well as successful sends.
            if sum(a["created_at"].startswith(today) for a in attempts) >= config["daily_limit"]:
                return
            if attempts and stamp - datetime.fromisoformat(attempts[0]["created_at"]) < timedelta(minutes=config["cooldown_minutes"]):
                return
            pending = self.store.rows("SELECT * FROM events WHERE delivery='pending' ORDER BY id")
            if not pending:return
            current = {j['id']:j for j in self.store.job_feed(e['job_id'] for e in pending)}
            selected = []
            dropped = []
            for event in pending:
                job = current.get(event['job_id'])
                state = subscription_state(job,settings['rule']) if job else 'excluded'
                if state == 'excluded' or (event["kind"] == "updated" and not config["notify_updates"]):
                    dropped.append(event["id"])
                    continue
                if state == 'pending':continue
                selected.append((event, job))
            if dropped:
                with self.store.connection() as db:
                    db.executemany("UPDATE events SET delivery='ignored' WHERE id=?", [(i,) for i in dropped])
            if not selected:
                return
            # Byte budget is below WeCom's 4096-byte Markdown limit. Excess stays queued.
            body = "**招聘雷达 · 匹配岗位动态**\n\n"
            ids = []
            for event, job in selected:
                title = job['title'].replace('[', '').replace(']', '').replace('\n', ' ')[:90]
                line = f"{'新增' if event['kind']=='new' else '更新'} · [{title}]({job['url']})\n{job['company']} · {job['city'] or '地点见官网'}\n\n"
                if len((body + line).encode("utf8")) > 3400 or len(ids) >= 8:
                    break
                body += line
                ids.append(event["id"])
            if ids:
                body += f"本次 {len(ids)} 条，待推送 {len(selected)-len(ids)} 条。\n发现时间：{now()}"
                self._send(config, f"招聘雷达：{len(ids)} 条岗位动态", body, ids)
        finally:
            self.lock.release()
