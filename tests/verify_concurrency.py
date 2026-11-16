"""确定性验证并发互斥：先持有规则锁（模拟检测进行中），并发请求必须全部被拒。"""
import sys
import threading

sys.path.insert(0, "/workspace/backend")

from app.services.partition_detection import detection_service, get_detection_lock  # noqa: E402
from app.store import store  # noqa: E402

# 取一条已启用规则
rule = next(r for r in store.rows("partitioncheck") if r["status"] == "已启用")
rid = int(rule["id"])
before = len(store.rows("missingpartition"))

lock = get_detection_lock(rid)
acquired = lock.acquire(blocking=False)
assert acquired, "规则锁应可被首次获取"

results = []
barrier = threading.Barrier(5)


def worker():
    barrier.wait()
    results.append(detection_service.detect(rid, batch_no=f"CONC-{threading.get_ident()}"))


threads = [threading.Thread(target=worker) for _ in range(5)]
for t in threads:
    t.start()
for t in threads:
    t.join()
lock.release()

effective = [r for r in results if r[2]]
rejected = [r for r in results if r[0] is None and "正在检测中" in r[1][0]]
print("被拒并发数:", len(rejected), " 生效数:", len(effective))
assert len(rejected) == 5 and not effective
assert len(store.rows("missingpartition")) == before, "互斥期间不得写入任何记录"

# 锁释放后，新检测正常生效
rec, msgs, ok = detection_service.detect(rid, batch_no="CONC-AFTER-LOCK")
assert ok and rec is not None and rec["是否生效"] is True
assert len(store.rows("missingpartition")) == before + 1
print("锁释放后检测正常生效，记录数 +1")
print("CONCURRENCY CHECK PASSED")
