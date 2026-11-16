"""端到端校验分区检测修复口径（通过真实 HTTP 接口验证，仅用标准库）。"""
import json
import sys
import threading
import urllib.request
from datetime import datetime, timedelta

BASE = "http://127.0.0.1:8011"
failures = []


def call(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail if not cond else ''}")
    if not cond:
        failures.append(name)


# 1. 负数期望分区数在登记入口被拒绝
r = call("POST", "/api/partitioncheck", {"values": {
    "规则编号": "PART-NEG", "数据表名": "t_neg", "期望分区数": -3,
    "分区延迟容忍": 10, "检测间隔分钟": 60}})
check("负数期望分区数被登记接口拒绝", r["ok"] is False, r)

r = call("POST", "/api/partitioncheck", {"values": {
    "规则编号": "PART-NEG2", "数据表名": "t_neg2", "期望分区数": 2,
    "分区延迟容忍": -5, "检测间隔分钟": 60}})
check("负数延迟容忍被登记接口拒绝", r["ok"] is False, r)

r = call("POST", "/api/partitioncheck", {"values": {
    "规则编号": "PART-X", "数据表名": "t_x", "期望分区数": "abc"}})
check("非整数期望分区数被拒绝", r["ok"] is False, r)

# 正常登记
r = call("POST", "/api/partitioncheck", {"values": {
    "规则编号": "PART-V1", "数据表名": "dwd_demo", "期望分区数": 3,
    "分区延迟容忍": 30, "检测间隔分钟": 60, "自动补齐开关": "开启",
    "补齐优先级": "高"}})
check("合法规则登记成功", r["ok"] is True, r)
rule = r["entry"]
rid = rule["id"]
check("新规则带规则版本1", rule["规则版本"] == 1)

# 2. 修改规则 -> 版本递增 + 历史保留
r = call("PUT", f"/api/partitioncheck/{rid}", {"values": {"期望分区数": 4}})
check("修改规则成功", r["ok"] is True, r)
check("规则版本递增到2", r["entry"]["规则版本"] == 2)
check("规则历史保留两个版本", len(r["entry"]["规则历史"]) == 2)
r = call("PUT", f"/api/partitioncheck/{rid}", {"values": {"期望分区数": -1}})
check("修改入口同样拒绝负数", r["ok"] is False)

# 启用规则
r = call("POST", f"/api/partitioncheck/{rid}/actions", {"values": {"action": "启用规则"}})
check("规则启用", r["entry"]["status"] == "已启用")

# 3. 检测：改回期望3个分区；容忍置0，使未到达分区一律算缺失，结论确定
call("PUT", f"/api/partitioncheck/{rid}",
     {"values": {"期望分区数": 3, "分区延迟容忍": 0, "检测间隔分钟": 1440}})
now = datetime.now()
expected = [(now - timedelta(days=i)).strftime("dt=%Y-%m-%d") for i in (2, 1, 0)]

# 登记分区标识：仅第1个分区到达
call("POST", "/api/partitionmark", {"values": {
    "标识编号": "PMK-T1", "数据表名": "dwd_demo",
    "分区标识": expected[0],
    "到达时间": (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S")}})

r = call("POST", f"/api/partitioncheck/{rid}/detect",
         {"values": {"batch_no": "B1", "expected_partitions": expected}})
rec = r["entry"]
check("检测生成缺失分区记录", r["ok"] is True and rec is not None)
check("缺失分区数=2", rec["缺失分区数"] == 2, rec["缺失分区清单"])
check("完整性结论=不完整", rec["分区完整性结论"] == "不完整")
check("记录固化规则版本", rec["规则版本"] == 3 and rec["规则快照"]["规则版本"] == 3)

# 4. 批次幂等
r1 = call("GET", f"/api/partitioncheck/missing-partitions?rule_id={rid}")
before = r1["total"]
r = call("POST", f"/api/partitioncheck/{rid}/detect",
         {"values": {"batch_no": "B1", "expected_partitions": expected}})
r2 = call("GET", f"/api/partitioncheck/missing-partitions?rule_id={rid}")
check("同批次重复检测回查原记录、不新增", before == r2["total"] and r["entry"]["id"] == rec["id"])

# 5. 延迟容忍窗口
call("PUT", f"/api/partitioncheck/{rid}",
     {"values": {"检测间隔分钟": 1, "分区延迟容忍": 60 * 24 * 30}})
r = call("POST", f"/api/partitioncheck/{rid}/detect",
         {"values": {"batch_no": "B2", "expected_partitions": expected}})
rec2 = r["entry"]
check("容忍窗口内未到达分区记为容忍中",
      len(rec2["容忍中分区"]) == 2 and rec2["缺失分区数"] == 0, rec2)
check("容忍窗口生效时完整性=完整", rec2["分区完整性结论"] == "完整")

call("PUT", f"/api/partitioncheck/{rid}",
     {"values": {"检测间隔分钟": 60, "分区延迟容忍": 0}})
r = call("POST", f"/api/partitioncheck/{rid}/detect",
         {"values": {"batch_no": "B3", "expected_partitions": expected}})
rec3 = r["entry"]
check("容忍窗口为0时未到达即缺失", rec3["缺失分区数"] == 2)

# 6. 暂停规则：旧记录保留，不新增、不建任务
call("POST", f"/api/partitioncheck/{rid}/actions", {"values": {"action": "暂停规则"}})
r1 = call("GET", f"/api/partitioncheck/missing-partitions?rule_id={rid}")
r = call("POST", f"/api/partitioncheck/{rid}/detect",
        {"values": {"batch_no": "B4-AFTER-PAUSE", "expected_partitions": expected}})
r2 = call("GET", f"/api/partitioncheck/missing-partitions?rule_id={rid}")
check("暂停后检测不新增记录", r1["total"] == r2["total"])
check("暂停后检测不生效", r["ok"] is False)

r = call("POST", "/api/runtime/fill-tasks", {"values": {"missing_record_id": rec3["id"]}})
check("暂停规则不创建补齐任务", r["ok"] is False, r)
tasks = call("GET", "/api/runtime/fill-tasks")
check("暂停后补齐任务表为空", tasks["total"] == 0)

# 7. 重新启用：预判定 -> 建任务
call("POST", f"/api/partitioncheck/{rid}/actions", {"values": {"action": "启用规则"}})
r = call("POST", "/api/runtime/fill-tasks/review",
         {"values": {"missing_record_id": rec3["id"]}})
check("补跑预判定通过", r["ok"] is True, r)
r = call("POST", "/api/runtime/fill-tasks",
         {"values": {"missing_record_id": rec3["id"], "runtime_id": 1}})
check("读取记录后创建补齐任务", r["ok"] is True, r)
task_id = r["entry"]["id"]
check("补齐任务带规则版本可回查", r["entry"]["规则版本"] == rec3["规则版本"])
r = call("POST", "/api/runtime/fill-tasks", {"values": {"missing_record_id": rec3["id"]}})
check("同一记录不重复创建补齐任务", r["ok"] is True and r["entry"]["id"] == task_id)

r = call("POST", "/api/runtime/fill-tasks", {"values": {"missing_record_id": rec2["id"]}})
check("完整记录不创建补齐任务", r["ok"] is False)

# 8. 规则版本过期
call("PUT", f"/api/partitioncheck/{rid}", {"values": {"补齐优先级": "低"}})
r = call("POST", "/api/runtime/fill-tasks/review",
         {"values": {"missing_record_id": rec3["id"]}})
check("规则版本过期后旧记录拒绝补齐", r["ok"] is False and "版本" in r["message"], r)

# 9. 并发检测同一规则：同批次并发只落一份记录（互斥语义另由服务层单测确定性验证）
results = []
barrier = threading.Barrier(5)

def worker():
    barrier.wait()
    rr = call("POST", f"/api/partitioncheck/{rid}/detect",
              {"values": {"batch_no": "B-CONCURRENT", "expected_partitions": expected}})
    results.append(rr)

threads = [threading.Thread(target=worker) for _ in range(5)]
for t in threads:
    t.start()
for t in threads:
    t.join()
record_ids = {x["entry"]["id"] for x in results if x.get("entry")}
batch_rows = call("GET", "/api/partitioncheck/missing-partitions?batch_no=B-CONCURRENT")
check("并发同批次检测只生成一份记录", len(record_ids) == 1 and batch_rows["total"] == 1,
      f"ids={record_ids} total={batch_rows['total']}")

# 10. 两个入口读到同一份记录
a = call("GET", f"/api/partitioncheck/missing-partitions?rule_id={rid}")
b = call("GET", f"/api/runtime/missing-partitions?rule_id={rid}")
ids_a = sorted(item["id"] for item in a["items"])
ids_b = sorted(item["id"] for item in b["items"])
check("分区检测与运行实例读到同一份缺失分区记录", ids_a == ids_b and len(ids_a) > 0)

# 11. 草稿规则不能检测
draft = call("POST", "/api/partitioncheck", {"values": {
    "规则编号": "PART-DRAFT", "数据表名": "dwd_draft", "期望分区数": 2}})["entry"]
r = call("POST", f"/api/partitioncheck/{draft['id']}/detect", {"values": {}})
check("草稿规则不允许检测", r["ok"] is False)

# 12. 期望清单数量不符拒绝重算
r = call("POST", f"/api/partitioncheck/{rid}/detect",
         {"values": {"expected_partitions": expected[:2]}})
check("期望清单数量不符拒绝重算", r["ok"] is False and "不一致" in r["message"], r)

# 13. 列表期望分区数均为非负整数
listed = call("GET", "/api/partitioncheck")["items"]
bad = [row for row in listed if not isinstance(row.get("期望分区数"), int) or row["期望分区数"] < 0]
check("列表期望分区数均为非负整数", not bad, bad[:2])

# 14. 分区标识重复登记拦截
r = call("POST", "/api/partitionmark", {"values": {
    "标识编号": "PMK-DUP", "数据表名": "dwd_demo",
    "分区标识": expected[0], "到达时间": "2026-11-16T00:00:00"}})
check("重复分区标识被拒绝", r["ok"] is False)

# 15. 检测详情可回查规则版本与快照
detail = call("GET", f"/api/partitioncheck/missing-partitions/{rec3['id']}")
check("检测详情含规则快照可回查", "规则快照" in detail and detail["规则快照"]["规则编号"] == rec3["规则编号"])

print()
if failures:
    print(f"FAILED {len(failures)}:", failures)
    sys.exit(1)
print("ALL CHECKS PASSED")
