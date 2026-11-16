Host: 4 vCPU, Linux-6.18.44-fc-v470-x86_64-with-glibc2.39, Python 3.13.18, driver `docker`. Repo: `https://github.com/pallets/flask`.

n = 20 sequential runs per mode.

| mode | p50 total | p95 total | p50 container | p50 checkout |
|---|---|---|---|---|
| cold | 2753.4 ms | 2976.2 ms | 139.3 ms | 2533.4 ms |
| cached | 358.9 ms | 391.6 ms | 148.8 ms | 124.7 ms |
| warm | 151.0 ms | 226.9 ms | 0.0 ms | 92.9 ms |
