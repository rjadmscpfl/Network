#!/usr/bin/env python3
"""
Flask Web App for Cisco Switch MAC Address Finder
"""

import json
import queue
import threading
import uuid
from flask import Flask, render_template, request, Response, stream_with_context

from FindClinet import discover_and_search, suffix_to_partial

app = Flask(__name__)

# 진행 중인 탐색: search_id -> threading.Event
_stop_events: dict[str, threading.Event] = {}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/search", methods=["POST"])
def search():
    data = request.get_json()
    seed_ip  = data.get("seed_ip", "").strip()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    secret   = data.get("secret", "")
    suffix   = data.get("suffix", "").strip()

    if not all([seed_ip, username, password, suffix]):
        return {"error": "필수 항목을 모두 입력하세요."}, 400

    try:
        suffix4 = suffix_to_partial(suffix)
    except ValueError as e:
        return {"error": str(e)}, 400

    search_id = str(uuid.uuid4())
    stop_event = threading.Event()
    _stop_events[search_id] = stop_event

    msg_queue: queue.Queue = queue.Queue()

    def run():
        import sys
        old_stdout = sys.stdout

        class VerboseCapture:
            def __init__(self): self.buf = ""
            def write(self, s):
                self.buf += s
                while "\n" in self.buf:
                    line, self.buf = self.buf.split("\n", 1)
                    line = line.strip()
                    if line:
                        msg_queue.put({"type": "log", "text": line})
            def flush(self): pass

        sys.stdout = VerboseCapture()
        try:
            results = discover_and_search(
                seed_ip=seed_ip,
                username=username,
                password=password,
                secret=secret,
                suffix4=suffix4,
                verbose=True,
                stop_event=stop_event,
            )
            msg_queue.put({"type": "result", "data": [
                {
                    "switch_ip": r.switch_ip,
                    "switch_hostname": r.switch_hostname,
                    "mac_address": r.mac_address,
                    "vlan": r.vlan,
                    "interface": r.interface,
                    "mac_type": r.mac_type,
                }
                for r in results
            ]})
        except Exception as e:
            msg_queue.put({"type": "error", "text": str(e)})
        finally:
            sys.stdout = old_stdout
            _stop_events.pop(search_id, None)
            msg_queue.put(None)  # sentinel

    threading.Thread(target=run, daemon=True).start()

    def generate():
        # 첫 메시지로 search_id 전달
        yield f"data: {json.dumps({'type': 'init', 'search_id': search_id}, ensure_ascii=False)}\n\n"
        while True:
            item = msg_queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/stop/<search_id>", methods=["POST"])
def stop(search_id):
    event = _stop_events.get(search_id)
    if event:
        event.set()
        return {"ok": True}
    return {"ok": False, "reason": "해당 탐색을 찾을 수 없습니다."}, 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
