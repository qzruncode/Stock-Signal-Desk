---
name: worktree
description: Use when the user invokes /worktree with a Plan Ref or needs a project-local git worktree paired one-to-one with a Human Plan so one plan can be executed in an isolated branch/session with the required local workflow context available.
---

# Worktree

所有产出使用简体中文。代码标识、路径、分支名、命令和错误信息不翻译。

这是操作型技能：只创建或复用 worktree，并准备本地工作流上下文；不产出、不修改 Human Plan，不执行开发或检查。

## 命令

`/worktree <Plan Ref>`

`Plan Ref` 格式必须是 `<Plan 文件路径>@v<Version>`。

## 规则

- 在当前 git 仓库内执行。
- 读取 `Plan Ref` 指向的文件；Version 必须匹配，`Plan ID` 必须非空。
- `Needs Reconfirmation` 非空或 `Status` 为 `reconfirmation-pending` 时停止，并返回当前合法下一步。
- 从 `Plan ID` 生成 slug：小写，只保留 `a-z`、`0-9`、`-`，合并连续 `-`，去掉首尾 `-`。
- Worktree 路径固定为 `.claude/worktrees/<slug>`。
- 分支名固定为 `worktree/<slug>`。
- 同一 Plan 复用已有 worktree；路径或分支已被其他 Plan 占用时停止。
- 创建前确保 `.git/info/exclude` 包含 `.claude/worktrees/`。
- 从当前 `HEAD` 创建 worktree；不复制未提交代码改动。
- 不执行破坏性 git 操作。

## 本地上下文

worktree 创建或复用后，使用相对符号链接准备本地上下文：

- 在目标 worktree 的同相对路径创建 Plan 文件符号链接，指向原 `Plan Ref` 文件；保留 `@v<Version>` 作为后续命令参数。
- 如果目标 worktree 缺少 `.claude/skills`，创建指向当前项目 `.claude/skills` 的符号链接。
- 如果当前项目存在 `.claude/settings.local.json`，在目标 worktree 创建同路径符号链接。
- 已存在且指向相同目标时复用；已存在但不是同一目标时停止。
- 不复制 Plan 文件、批次索引、产品代码改动、worktree 目录、缓存或日志。

## 输出

成功后只输出：

- Worktree 路径
- 分支名
- 是否复用已有 worktree
- Plan Ref
- 下一步命令

下一步命令格式：

```bash
cd <Worktree 路径>
/dev <Plan Ref>
```

输出后立即停止。
