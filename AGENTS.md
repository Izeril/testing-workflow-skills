# 本仓库维护边界

本仓库属于 Codex testing-workflow-skills，与 jwy-testi-expert 独立并存。对外整包入口是 skills/SKILL.md（testing-workflow）；内部核心入口保留 skills/testcase-generator。四技能与 contracts 必须同版、同级且位于包内。

- 可以在本仓库修改、验证、commit/push 和打 tag；操作前核对 Git 根和 remote。
- 被审计业务仓库保持只读；不得移除业务 Git 保护，不得修改 jwy 源码、安装、配置或发布流程。
- 安装使用现有 skill-installer，把 skills 整目录安装为当前用户技能根下的 testing-workflow。更新只处理该完整包，下载/备份在用户临时或缓存区域，不在业务项目根创建安装管理目录。递归复制、删除或移动前校验解析后的绝对路径和重解析点，保留本机配置、上一版备份及未入库改动。
- config.local.yaml、凭据、真实服务默认值、需求/业务源码、业务报告、知识库、真实样本及运行缓存不得跟踪。
- 修改调用边界、配置或产物格式时运行中央契约及相关既有回归。缺少真实样本或归档对照时记录跳过原因。
- 保留业务评审、UAT、质量和归档规则。不得因仓库整理引入另一套发布系统或顺带重构业务工作流。
- 若根目录存在 .codegraph，理解或定位代码先用 CodeGraph；没有索引则不主动建立。
