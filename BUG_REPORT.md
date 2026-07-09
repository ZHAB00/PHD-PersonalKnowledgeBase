# Bug 报告：前端重构时 JS 文件中文编码损坏导致页面卡死

## 概述

2026-07-09 进行前端全面重构（HTML 从 base64 迁移至独立模板），在将 UI 文本中文化时，
使用错误的字符串编码方式生成 JavaScript 文件，导致浏览器解析 JS 失败，整个页面卡死（所有按钮无响应）。

## 影响范围

- **时间**: 2026-07-09 全天，至少 4-5 轮修复迭代
- **影响**: 前端完全不可用 — 新建对话、文档管理、知识库切换、退出登录等所有按钮点击无反应
- **用户可见现象**: 页面白屏或"连接中..."卡住，控制台报 `Uncaught SyntaxError: Invalid or unexpected token`

## 根因分析

### 错误的做法

在 Python 脚本中生成 JS 文件时，使用 Python 的 `\uXXXX` unicode-escape 语法写入中文字符：

```python
# ❌ 错误写法 — Python 源码中的 \uXXXX 转义
JS = u"""
  $("statusText").textContent = "\u5df2\u8fde\u63a5";
  d.innerHTML = '<span>\u5220\u9664</span>';
"""
with open("app.js", "w", encoding="utf-8") as f:
    f.write(JS)
```

**问题**: Python 的 `open("w", encoding="utf-8")` 将 `\u5df2\u8fde\u63a5` 当作字面 18 个 ASCII 字符写入文件，
而不是将这 6 个字节解释为 Unicode 码点再编码为 UTF-8。

### 文件里实际写入的内容

```
# 预期（正确UTF-8）:
$("statusText").textContent = "已连接";

# 实际（乱码字节）:
$("statusText").textContent = "å·²è¿žæŽ¥";
```

浏览器加载该 JS 时，乱码字节破坏了 JavaScript 字符串字面量的边界，导致语法错误，
整个 IIFE 无法执行，后续所有 `on()` 事件绑定全部失效。

### 正确的做法

使用 `json.dumps(s, ensure_ascii=False)` 来生成 JavaScript 字符串字面量：

```python
# ✅ 正确写法
import json

status_text = json.dumps("已连接", ensure_ascii=False)  # 输出: "已连接"（真正的UTF-8字节）

JS = f"""
  $("statusText").textContent = {status_text};
"""
with open("app.js", "w", encoding="utf-8") as f:
    f.write(JS)
```

`json.dumps` 会将中文字符以正确的 UTF-8 多字节序列写入文件目标字符串，
再通过 `open("w", encoding="utf-8")` 写入磁盘。

## 为什么这个问题拖了一整天

1. **后端测试 33/33 全绿** — 所有 API、RAG、chunker 测试都通过，让人误以为没问题
2. **JS 括号平衡检查通过** — `(` `)` `{` `}` 计数正确，没有静态语法错误提示
3. **浏览器缓存干扰** — 多次修改 JS 后浏览器仍加载旧版本（`v=2` 不变），
   修改版本号为 `v=3` 后才真正加载到损坏的文件
4. **PowerShell 编码问题混淆** — 之前用 PowerShell `-replace` 修改 JS 也出过编码问题，
   让人以为是同类问题，实际根因不同

## 经验教训

| 教训 | 具体做法 |
|------|---------|
| **永远不要手写 unicode-escape** | 用 `json.dumps(ensure_ascii=False)` 或直接写 UTF-8 源码文件 |
| **前端改动后必须清除缓存验证** | 改 version hash 或 Ctrl+Shift+R 强制刷新 |
| **不要只信后端测试** | 前端 JS 语法错误不会在 pytest 中暴露，需要浏览器 Console 检查 |
| **生成代码时用结构化方式** | 避免在 Python 字符串中嵌套大量 JavaScript 代码，考虑模板引擎 |
| **编码问题优先怀疑写入链** | Python `open("w")` 写入和 `\u` 转义交互是最容易出错的环节 |

## 修复记录

最终使用 `_gen_final.py` 脚本，通过 `json.dumps(..., ensure_ascii=False)` 生成所有中文字符串，
确保 JS 文件中的中文以正确 UTF-8 编码写入。修复后页面恢复正常，所有按钮可点击。

---

*报告生成时间: 2026-07-09*
*关联文件: app/static/js/app.js, app/templates/index.html, OPERATIONS.md*
