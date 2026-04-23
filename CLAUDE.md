# NerdVana CLI - Claude Code设置

创建者: 최진호
创建日: 2026-04-19
修改日: 2026-04-19

## 项目简介

NerdVana CLI是支持13个AI平台的AI驱动CLI开发工具。本项目为Python项目，主要语言为Python。

## ⭐ Serena MCP 优先使用规则

**Serena MCP(pmcp.nerdvana.kr)是本项目的首选代码探索/编辑工具。**

### 何时必须使用Serena

代码探索、搜索、重构工作时，必须首先使用Serena（而不直接使用grep等基础工具）：

- 符号搜索 (`serena_find_symbol`) - 查找类/函数/变量定义
- 引用查找 (`serena_find_referencing_symbols`) - 查找符号引用
- 代码概览 (`serena_get_symbols_overview`) - 获取文件的符号概览
- 符号重命名 (`serena_rename_symbol`) - 整个工作区的符号重命名
- 符号体替换 (`serena_replace_symbol_body`) - 替换符号的完整定义
- 符号前后插入 (`serena_insert_after_symbol`, `serena_insert_before_symbol`) - 在符号定义前后插入代码
- 安全删除 (`serena_safe_delete_symbol`) - 安全删除符号（检查引用）

### 何时可能不使用

- **仅查看文件** - 使用 `read` 工具
- **grep搜索** - 仅需要简单文本搜索时
- **外部仓库/文档查询** - 使用 `librarian` 或 `context7` 工具

### Serena工具优先级

当任务涉及以下内容时，**必须首先使用Serena工具**：

1. **理解和探索代码结构** - 使用 `serena_get_symbols_overview` 获取文件概览
2. **查找特定符号** - 使用 `serena_find_symbol`，而不是grep
3. **理解代码依赖关系** - 使用 `serena_find_referencing_symbols`
4. **代码重命名/重构** - 使用 `serena_rename_symbol` 进行工作区级重命名
5. **修改符号定义** - 使用 `serena_replace_symbol_body` 或 `serena_insert_*_symbol`

### 工具映射表

| 任务 | 传统工具 | Serena优先工具 |
|------|----------|---------------|
| 文件符号概览 | lsp_symbols | serena_get_symbols_overview |
| 查找函数/类定义 | grep + lsp_goto_definition | serena_find_symbol |
| 查找引用 | lsp_find_references | serena_find_referencing_symbols |
| 重命名 | lsp_rename + grep | serena_rename_symbol |
| 替换函数体 | edit (多文件) | serena_replace_symbol_body |
| 删除代码 | edit (风险) | serena_safe_delete_symbol |

## 代码质量规则

- **类型注解必须** - 所有函数必须有类型提示
- **文档字符串必须** - 公共API必须有docstring
- **测试必须** - 新功能需要测试覆盖

## 环境设置

- Python版本: 3.11+
- 运行方式: `uv run nerdvana` 或 `nc`

## 常用命令

```bash
# 开发模式启动
uv run nerdvana

# 单次执行
uv run nerdvana run "解释这个项目的架构"

# 测试
pytest

# 类型检查
mypy nerdvana_cli/

# Lint
ruff check nerdvana_cli/
```

## 注意事项

- 修改代码时，确保使用Serena工具进行符号级操作，以保持代码完整性
- 提交前运行 `mypy` 和 `ruff check` 确保代码质量
- 遵循项目的代码风格（PEP 8 + ruff规则）