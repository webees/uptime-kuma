# 当前目标模式提示词

> 这是当前项目唯一的目标模式提示词。每轮开始时读取本文件，任务完成后原地覆盖为下一轮的最新状态和任务；不追加历史版本、不创建按轮次编号的文件、不把正文再次输出到聊天。
>
> 固定启动语：`阅读这个文件，并执行`

## 目标

继续推进 `webees@uptime-kuma` 的生产可靠性与自动化收口，基于 `codex/production-audit-restore` 分支当前未提交改动完成验证、文档、提交、推送、PR、合并、部署和真实恢复演练。

## 当前状态

- 仓库：`/Users/x/Desktop/__GITHUB__/webees@uptime-kuma`
- 分支：`codex/production-audit-restore`
- 基线：`main` / `origin/main` = `691071e818cc6aab692564ca2b7bce88b171b701`
- 提示词机制已写入 `docs/target_mode.md`；本文件是该机制唯一的可变任务简报。
- 当前代码改动仍未提交；旧草稿 `pr_body.md`、`pr_body_npm_dependency_lane.md` 不得提交，删除前必须确认。
- 生产应用：`bxx-uptime-kuma`
- 生产 release：`v75`
- 生产镜像：`ghcr.io/webees/uptime-kuma@sha256:8500396d816d461ebbdf28d33fc4a09343086d7b687a27ec24e558aa27ca0651`
- 当前生产镜像尚未包含本分支新增的真实恢复启动检查。

## 本轮必须完成

1. 先检查 `git status`、`git diff` 和仓库内 `AGENTS.md`，保留已有改动，不碰无关文件。
2. 验证最新的 Deploy Drift 公共端点检查：
   - `/health` 必须为 `200`
   - `/` 必须为 `302`
   - `Location` 必须为 `/dashboard`
   - 运行 `tests.test_deploy_drift` 相关测试及 workflow assertion mutation control。
3. 更新 `docs/automation_inventory.md` 和 `docs/recurring_issues.md`，明确 Deploy Drift 同时验证 Fly machine 健康、`/health`、`/` 和 `/dashboard` 跳转，并记录 mutation control 证据。
4. 执行完整验证，不得只跑单元测试：
   - `python3.13 -m unittest discover -s tests -v`
   - `python3.13 tests/runtime_smoke.py validate:local`
   - `actionlint -no-color`
   - `shellcheck scripts/*.sh scripts/entrypoint/*.sh`
   - `python3.13 -m py_compile $(find scripts tests -name '*.py')`
   - `git diff --check`
5. 检查 diff，只保留本轮相关文件：
   - `NEXT_PROMPT.md`
   - `docs/target_mode.md`
   - `.github/workflows/deploy-drift.yml`
   - `README.md`
   - `docs/automation_inventory.md`
   - `docs/recurring_issues.md`
   - `scripts/deploy_drift.py`
   - `scripts/restic.sh`
   - `tests/runtime_smoke.py`
   - `tests/test_deploy_drift.py`
6. 生成九类八列审计矩阵，并创建唯一 PR。PR 正文必须包含：
   - `## Conclusion`
   - `## Matrix`
   - `## Changes`
   - `## Verification`
   - `## Residual`
7. 及时提交并推送；不要等到最后一次性提交。PR 创建后附上 URL。
8. 等 required checks 全绿后合并；生产变更已获用户授权。合并会触发生产重建。
9. 监控 `main` 完整部署链：`validate → build → scan → smoke → deploy → verify → promote`。
10. 新版本上线后重新取证：
    - 新 Fly release 为 `complete`
    - machine 运行新 digest 且 `state=started`、`host_status=ok`
    - health check 为 `passing`
    - `/health=200`
    - `/=302 -> /dashboard`
    - Deploy Drift 日志出现 `in sync`
11. 新镜像上线后执行真实生产恢复演练：
    - `flyctl ssh console -a bxx-uptime-kuma -C "/restic.sh restore-test"`
    - 必须看到 `/ returned HTTP 302` 和最终 restore drill success。
12. 真实恢复演练通过后再评估旧 Fly token 撤销、schedule watchdog 失败注入和外部告警阻塞。

## 约束

- 代码、提交信息正文和最终回答使用中文；commit/PR 标题和正文使用英文。
- 文件修改只能使用 `apply_patch`。
- 不泄露、不提交任何 token、secret 或 webhook。
- 不创建重复 PR，不强制推送 `main`，不执行破坏性 git 操作。
- 未实际运行的测试、部署或恢复演练不得声称成功；无法验证时明确写出原因。
- 保留现有自动化脚本和已有改动；删除任何文件前先证明无调用、无运行依赖、无回滚价值并取得确认。

## 完成后的固定动作

完成后不要再次输出一份目标提示词，也不要追加一节“下一轮提示词”。直接用下一轮的真实状态、证据、剩余工作和授权边界覆盖本文件，保持下一轮仍可通过 `阅读这个文件，并执行` 继续。本文件的更新必须与当轮代码或文档一起提交并推送。

最终回答必须包含：完成结果、主要改动、验证状态、PR/部署证据、仍存在的问题。
