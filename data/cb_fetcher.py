"""
Phase 1.1: 全量可转债数据拉取 (极简版 - 无 calendar 污染)
运行: python data/cb_fetcher.py
"""
import os, sys, time, csv

# 关键: 只加项目根目录，不加 data/ 子目录 (否则 calendar.py 遮蔽 stdlib)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import akshare as ak
import pandas as pd

CACHE_DIR = os.path.join(PROJECT_ROOT, "data", "cb_cache")
FAILED_LOG = os.path.join(PROJECT_ROOT, "data", "cb_fetch_failed.csv")
os.makedirs(CACHE_DIR, exist_ok=True)


def get_all_codes():
    codes = set()
    try:
        spot = ak.bond_zh_hs_cov_spot()
        for _, r in spot.iterrows():
            sym = r["symbol"]
            codes.add(sym[2:] if len(sym) > 2 else sym)
    except Exception as e:
        print(f"[WARN] spot: {e}")
    try:
        cb_list = ak.bond_zh_cov()
        for _, r in cb_list.iterrows():
            code = str(r.get("债券代码", ""))
            if code and len(code) >= 6:
                codes.add(code)
    except Exception as e:
        print(f"[WARN] list: {e}")
    return sorted(codes)


def main():
    codes = get_all_codes()
    print(f"总代码: {len(codes)}")

    existing = sum(1 for c in codes if os.path.exists(os.path.join(CACHE_DIR, f"{c}.csv")))
    print(f"已有缓存: {existing} | 待拉取: {len(codes) - existing}")

    success = 0
    failed = 0
    t0 = time.time()

    for i, code in enumerate(codes):
        fpath = os.path.join(CACHE_DIR, f"{code}.csv")
        if os.path.exists(fpath):
            success += 1
        else:
            ok = False
            for attempt in range(3):
                try:
                    df = ak.bond_zh_cov_value_analysis(symbol=code)
                    if df is not None and len(df) > 0:
                        df.to_csv(fpath, index=False)
                        ok = True
                        break
                except:
                    time.sleep(1.5)
            if ok:
                success += 1
            else:
                failed += 1
                with open(FAILED_LOG, "a", newline="") as f:
                    csv.writer(f).writerow([code, "3次重试失败"])

            time.sleep(0.25)

        if (i + 1) % 100 == 0:
            elapsed = time.time() - t0
            print(f"  {i+1}/{len(codes)} ok={success} fail={failed} {elapsed:.0f}s")

    elapsed = time.time() - t0
    print(f"\n完成: {elapsed:.0f}s | 成功={success} | 失败={failed}")
    print(f"缓存: {len([f for f in os.listdir(CACHE_DIR) if f.endswith('.csv')])} 支")


if __name__ == "__main__":
    main()
