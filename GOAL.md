# 当前项目目标

> 这是当前项目唯一的目标文件。每轮开始时读取本文件，任务完成后原地覆盖为下一轮的最新状态和任务；不追加历史版本、不创建按轮次编号的文件、不把正文再次输出到聊天。
>
> 固定启动语：`阅读这个文件，并执行`

## 目标

继续推进 `webees@uptime-kuma` 的生产可靠性与自动化收口，基于 `codex/production-audit-restore` 分支当前未提交改动完成验证、文档、提交、推送、PR、合并、部署和真实恢复演练。

## 最高代码质量标准

以下要求跨轮长期有效，覆盖每次 GOAL 更新；除非用户明确修改，不得删除、降级或改用平均值掩盖单项失败。每一项都必须有当前 SHA 的实际证据；无法验证的内容标记为未验证并说明原因。

1. **正确性与可证伪性**
   - 先复现缺陷或写失败测试，再做最小修复；覆盖正向、边界、负向、失败路径、清理、幂等、重启和回滚语义。
   - 禁止同义反复断言、吞错、占位测试、跳过失败、仅 mock 证明、虚假记录和未执行的成功声明。
   - 安全敏感行为使用操作系统 CSPRNG；测试中才注入确定性随机源。

2. **最小改动与架构质量**
   - 只改完成目标所必需的内容；禁止无关格式化、重写未触及区域、顺手重构或覆盖他人改动。
   - 优先使用静态数据、原生能力和清晰控制流；保持 deep modules 与 clean seams，避免重复逻辑、死代码、推测性抽象和 ball-of-mud。
   - 新增脚本、workflow、依赖或抽象前，必须证明现有能力不足；同时给出运行成本、失败边界、回滚方式和最小验证。

3. **工具与证据纪律**
   - 先检查真实 worktree、workflow、运行状态和当前 SHA；区分已确认事实、合理推断和未验证信息。
   - 代码结构优先使用 codebase-memory / 知识图谱和调用链证据，禁止仅凭文件名猜测调用关系。
   - 优先使用匹配的 Skill / MCP / App / 原生工具；所有文件修改使用 `apply_patch`；shell 仅用于读取、构建、测试和 git。

4. **测试与验证**
   - 运行与改动直接相关的最小测试，同时执行适用的构建、类型检查、lint、smoke 和真实流程验证。
   - 变更权威防护时必须运行 mutation control，证明移除或削弱防护会让测试变红。
   - 未运行的测试、部署、恢复演练或生产检查不得声称成功；必须写明未验证范围、原因和风险。

5. **安全、隐私与生产边界**
   - 落实最小权限、严格输入校验、有界输出、安全临时文件和清理；禁止泄露或提交 token、secret、webhook、凭据、生产标识和隐私数据。
   - 不得为让检查变绿而降低测试、扫描、审批、分支保护或 required checks；禁止绕过 CI 发布。
   - 生产变更只走受信 CI 路径；删除、覆盖、外部写入、发布和生产操作必须有明确授权，并保留可恢复状态。

6. **可审查交付**
   - 每个提交保持一个可独立审查的逻辑切片；保留现有改动和回滚价值，及时提交、推送并在 PR 中附实际证据。
   - README、运维说明、默认值、路径、测试和 `docs/recurring_issues.md` 必须与实现同步。
   - PR 必须完整写明结论、矩阵、改动、验证和残余风险；任何“已完成”都必须能由 diff、命令输出、CI、release 或线上检查复核。

7. **完成定义**
   - 工作区只包含本轮相关改动；没有未解释的残余问题、未分类风险或未验证的成功声明。
   - DNS、host、路径、地址、secret 和仓库派生值使用独立来源断言，不能与生成它们的同一函数或常量自证。
   - 发布后必须重新读取实际 release、digest、machine、health 和日志；历史成功不能替代当前 SHA 的证据。

## 当前状态

- 仓库：`/Users/x/Desktop/__GITHUB__/webees@uptime-kuma`
- 分支：`codex/production-audit-restore`
- 基线：`main` / `origin/main` = `691071e818cc6aab692564ca2b7bce88b171b701`
- 目标文件机制已写入 `docs/target_mode.md`；本文件是该机制唯一的可变任务简报。
- 最高代码质量标准已固化到本文件；后续覆盖 GOAL 时必须保留并执行，不得弱化。
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
   - `GOAL.md`
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

## GOAL 更新规则

每次最终回复前重新读取本文件，并依据本轮实际证据原地覆盖 `当前状态`、`本轮必须完成`、`约束` 和本节的下一轮重点。`最高代码质量标准` 是持久约束，更新时必须保留并检查是否被弱化。完成一轮后不得让本文件逐字节不变；日期、计数、时间戳或同义改写不算有效更新。

根据证据只选一种状态迁移：

1. **失败**：保留当前最高优先级，但把通过条件改写为具体失败行为及下一个可观察检查。
2. **通过**：推进到下一项可验证工作，并写出新的通过条件和所需证据。
3. **受阻**：记录缺少的权限、外部状态或输入，受阻项不得当作通过。
4. **新目标**：执行前替换 `目标`、`当前状态`、范围和完成条件；执行后按新证据再次更新。
5. **无缺陷**：推进到下一项有用的检查，并说明触发重开的证据；不得为制造更新而虚构工作。

更新后再次读取本文件，删除过时或矛盾内容。最终回复只报告结果、证据、验证状态、剩余风险和一行 `GOAL delta`；不要再次输出完整提示词，也不要追加“下一轮提示词”章节。本文件的更新必须与当轮代码或文档一起提交并推送。

最终回答必须包含：完成结果、主要改动、验证状态、PR/部署证据、仍存在的问题。
