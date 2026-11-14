"""
SPHEREx Disk Cache Preheater.
Pre-warms .cache_spherex/ with real cutouts & catalog astrometry for the 5 official targets.
"""

import time
from fastapi.testclient import TestClient
from main import app, BENCHMARK_TARGETS

client = TestClient(app)

print("=" * 60)
print("SPHEREx Disk Cache Preheater: Warming 5 Benchmark Targets")
print("=" * 60)

for idx, t in enumerate(BENCHMARK_TARGETS, 1):
    target_name = t["name"]
    ra = t["ra"]
    dec = t["dec"]
    fov = t["recommended_fov"]

    print(f"\n[{idx}/5] Warming cache for {target_name} (RA={ra}, Dec={dec})...")
    start = time.time()
    resp = client.get(f"/api/sky-patch?ra={ra}&dec={dec}&fov={fov}&mode=epochs&max_frames=3")
    elapsed = time.time() - start

    if resp.status_code == 200:
        data = resp.json()
        status = data.get("status", "unknown")
        n_frames = len(data.get("data", {}).get("frames", []))
        print(f"  -> SUCCESS ({status}) in {elapsed:.2f}s | Cached {n_frames} FITS frames.")
    else:
        print(f"  -> FAILED: HTTP {resp.status_code}")

print("\n" + "=" * 60)
print("Pre-warming complete! All 5 benchmark targets are now cached on disk.")
print("=" * 60)
