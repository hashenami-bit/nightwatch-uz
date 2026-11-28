"""NightWatch UZ: hourly weather-model collector and overnight-minimum prediction."""
import os

# One OpenMP thread for scikit-learn's gradient boosting (M2). The tables are tiny (about 200 rows),
# so extra threads only wait for each other: one thread is about 7 times faster here and gives
# exactly the same predictions (checked 27 Nov 2026, D019). Must be set before scikit-learn loads.
os.environ.setdefault("OMP_NUM_THREADS", "1")
