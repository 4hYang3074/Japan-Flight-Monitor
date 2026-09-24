# Japan Flight Monitor

每天马来西亚时间早上 8 点前，自动扫描 2027 关西樱花季 KUL↔KIX、SIN↔KIX 往返机票，更新 `docs/` 下的网页（GitHub Pages）。

- 扫描：`scraper/scan.py`（Google Flights，经 `fast-flights`），GitHub Actions 每天 22:17 UTC 运行
- 组合与统计：`scraper/build.py` → `docs/data.json`、`docs/history.json`
- 设置：`config.yaml`（扫描窗口、樱花满开日、航司、行李额度）

樱花正式预测发布后，把 `config.yaml` 里的 `sakura.full_bloom` 改成预测日期、`official` 改成 `true`，扫描窗口会自动变成满开日 ±15 天。

手动运行：Actions → Daily flight scan → Run workflow。
