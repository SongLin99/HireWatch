import argparse
import uvicorn
from hirewatch.app import create_app

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="招聘雷达本地看板与监控服务")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-scheduler", action="store_true", help="只查看已有数据，关闭自动采集")
    args = parser.parse_args()
    print(f"招聘雷达：http://127.0.0.1:{args.port}（退出此程序会停止监控）", flush=True)
    uvicorn.run(create_app(scheduler=not args.no_scheduler), host="127.0.0.1", port=args.port, log_level="warning")
