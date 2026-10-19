# Edge–cloud workflow simulation

24 scenarios × 12 seeds × 4 predictors = 1,152 simulation runs. See REPORT.txt and results/summary.csv for all outcomes and limitations.

These are synthetic infrastructure/load experiments using public JellyBean service profiles, not hardware validation or a reproduction of a published scheduling baseline. The 16.92% equal-weight mean is diagnostic-scenario dependent; 14/24 conditions passed the prespecified threshold. Shared-cloud queue correction accounts for most gains; separate queues and slow WAN show approximately zero gain.

Run from this directory with Python 3.12 and numpy, scipy, matplotlib:

```sh
python3 verify_suite.py
python3 run_suite.py
python3 analyze_suite.py
python3 make_report.py
```

The full raw-run archive remains in the previously delivered experiment package; this commit includes code, design, profiles, and summaries. Existing manifest records the original complete package, including files not committed here. Semantica was unavailable during this upload.
