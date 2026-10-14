# 分支与目标解析规则

## 目录

1. 分支来源
2. 支持格式
3. 仓库映射
4. 阻断规则

## 分支来源

按“显式参数 > code_targets > 当前提测文档”取值。禁止使用本地 checkout 分支，也禁止跨提测文档借用分支。

## 支持格式

识别 `feat`、`feature`、`bugfix`、`hotfix`、`release`、`dev`、`test`、`uat`、`master`，允许 `origin/` 前缀以及字母、数字、下划线、点、斜杠和短横线。

## 仓库映射

按显式目标、显式仓库、服务与语言唯一注册表映射、单目标默认仓库的顺序解析。注册表解析必须先于全局 `repo.path` 校验。

## 阻断规则

- 同一提测文档出现多个不同分支：`ambiguous_branch`。
- 注册表存在多个匹配：`ambiguous_repository_mapping`。
- 多目标无唯一仓库映射：`repository_mapping_missing`。
- 未获得分支：`branch_missing`。
