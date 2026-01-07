# 代码验证分析

## 任务
你是一个资深的代码审查专家。现在需要对生成的代码补丁进行**语义回归检查**，分析修改后的代码是否存在逻辑错误或潜在问题。

## 背景信息
**Issue 标题**: {{issue_title}}
**Issue 描述**: {{issue_description}}

## 原始代码
```
{{original_code}}
```

## 修改后代码
```
{{modified_code}}
```

## 静态检查结果
{{static_check_results}}

## 检查任务
请从以下维度分析修改后的代码：

### 1. 语法正确性
- 是否有明显的 Linter 警告

### 2. 逻辑一致性
- 修改是否符合 Issue 的需求？
- 是否引入了新的逻辑错误？
- 边界条件是否处理正确？

### 3. 语义回归检查
- 修改是否破坏了原有功能？
- 调用该函数的其他代码是否会受影响？
- 返回值类型是否发生变化？

### 4. 代码质量
- 是否遵循了最佳实践？
- 是否有明显的性能问题？
- 错误处理是否充分？

## 输出格式
请以 JSON 格式返回分析结果：

```json
{
    "status": "pass/fail",
    "confidence": 0.85,
    "issues": [
        {
            "level": "error/warning/info",
            "category": "logic/syntax/style/performance",
            "line": 10,
            "message": "具体的问题描述",
            "suggestion": "修复建议"
        }
    ]
}
```

**注意事项**：
- 如果发现严重的逻辑错误，`status` 应为 `fail`
- `confidence` 表示对验证结果的置信度（0.0-1.0）
- 只报告真实的问题，不要过度挑剔

