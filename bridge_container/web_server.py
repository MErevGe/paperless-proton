import threading
from pathlib import Path

from waitress import serve

from bridge_container.bridge_api import BridgeAPI
from bridge_container.web_app import WebApp


def run_web(data: Path) -> None:
    bridge = BridgeAPI(data)
    stopped = threading.Event()
    worker = threading.Thread(target=bridge.watch, args=(stopped,), daemon=True)
    expiry = threading.Thread(target=bridge.expire_logins, args=(stopped,), daemon=True)
    worker.start()
    expiry.start()
    try:
        serve(
            WebApp(data, bridge),
            host="127.0.0.1",
            port=8080,
            threads=4,
            connection_limit=32,
            channel_timeout=30,
            max_request_body_size=65536,
            max_request_header_size=16384,
            clear_untrusted_proxy_headers=True,
        )
    finally:
        stopped.set()
        if bridge.channel is not None:
            bridge.channel.close()
        worker.join(timeout=5)
        expiry.join(timeout=5)
