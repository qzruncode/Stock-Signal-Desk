---
name: worktree
description: Use when the user invokes /worktree with a Plan Ref or needs a project-local git worktree paired one-to-one with a Human Plan so one plan can be executed in an isolated branch/session.
---

# Worktree

所有产出使用简体中文。代码标识、路径、分支名、命令和错误信息不翻译。

这是操作型技能：只创建或复用 worktree，并写入 Plan 指针；不产出、不修改 Human Plan，不执行开发或检查。

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

## Plan 指针

worktree 创建或复用后，在目标 worktree 写入：

```text
.claude/worktree-plan-ref
```

文件内容为“从目标 worktree 根目录指向原 Plan 文件”的相对 `Plan Ref`，必须保留 `@v<Version>`。

后续在该 worktree 内执行 loop 时，使用这个相对 `Plan Ref`。

如果目标 worktree 缺少 `.claude/skills`，同步当前项目的 `.claude/skills`。不得同步产品代码改动。

## 输出

成功后只输出：

- Worktree 路径
- 分支名
- worktree 内 Plan Ref
- 是否复用已有 worktree
- 下一步命令

下一步命令格式：

```bash
cd <Worktree 路径>
/dev <worktree 内 Plan Ref>
```

输出后立即停止。
