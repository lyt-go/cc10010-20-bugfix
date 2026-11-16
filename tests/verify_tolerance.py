"""验证延迟容忍窗口的区分能力：旧分区超窗算缺失，最新分区窗内算容忍中。"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta

BASE = "http://127.0.0.1:8011"


def call(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


# 建一条规则：期望3个日分区，间隔1440分钟（1天），容忍60分钟
r = call("POST", "/api/partitioncheck", {"values": {
    "规则编号": "PART-TOL", "数据表名": "dwd_tolerance", "期望分区数": 3,
    "分区延迟容忍": 60, "检测间隔分钟": 1440, "自动补齐开关": "开启"}})
assert r["ok"], r
rid = r["entry"]["id"]
call("POST", f"/api/partitioncheck/{rid}/actions", {"values": {"action": "启用规则"}})

now = datetime.now()
expected = [(now - timedelta(days=i)).strftime("dt=%Y-%m-%d") for i in (2, 1, 0)]
# 仅登记中间那天到达；最旧(2天前)与最新(今天)均未到达
call("POST", "/api/partitionmark", {"values": {
    "标识编号": "PMK-TOL", "数据表名": "dwd_tolerance",
    "分区标识": expected[1], "到达时间": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")}})

r = call("POST", f"/api/partitioncheck/{rid}/detect",
         {"values": {"batch_no": "B-TOL", "expected_partitions": expected}})
assert r["ok"], r
rec = r["entry"]
print("已到达:", rec["已到达分区"])
print("容忍中:", [x["分区标识"] for x in rec["容忍中分区"]])
print("缺失:", [x["分区标识"] for x in rec["缺失分区清单"]])

# 最新分区（今天）刚到期、还在60分钟容忍窗内 -> 容忍中；最旧分区（2天前）远超窗口 -> 真实缺失
assert rec["缺失分区数"] == 1, rec
assert rec["缺失分区清单"][0]["分区标识"] == expected[0], rec
assert len(rec["容忍中分区"]) == 1 and rec["容忍中分区"][0]["分区标识"] == expected[2], rec
assert rec["分区完整性结论"] == "不完整"
print("TOLERANCE WINDOW DISTINCTION PASSED")
