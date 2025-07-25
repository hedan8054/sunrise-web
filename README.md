# Sunrise/Sunset Forecast (GitHub-only)

本仓库示例：用 **GitHub Actions** 跑 Python 脚本生成 JSON，用 **GitHub Pages** 展示结果。

## 使用步骤

1. 新建本仓库，把示例文件结构放进去。
2. 在仓库 Settings → Pages：Source 选 GitHub Actions。
3. （可选）Settings → Secrets → Actions 新增 `MB_API_KEY`。
4. 打开 `.github/workflows/run_forecast.yml`，调整定时 cron 或手动触发参数。
5. 手动触发：Actions → Generate Forecast JSON → Run workflow。
6. 成功后，在 `data/requests/` 里会多出 JSON。
7. 访问 GitHub Pages 页面，填写 owner/repo，点击“加载结果列表”。

## 如何改成“选日期/地点”

- 方案 B：继续用 Actions 手动触发（输入参数）。
- 或者你用 JS 在页面里调用 GitHub REST API 触发 workflow_dispatch（需要 token，不推荐放公开页面）。
- 方案 A：把 Python 逻辑搬到前端 JS（或 Pyodide），直接调用 API 现场计算。

## 目录说明

- backend/: Python 核心代码与 CLI 脚本
- frontend/: 静态页面（Pages）
- data/: JSON 结果
- .github/workflows/: Actions 工作流

祝你玩得开心！
