"""
代码相关的数据模型
"""

from pydantic import BaseModel, Field


class CodeSnippet(BaseModel):
    """代码片段数据模型 - 对应Milvus数据库Schema"""

    id: int | None = Field(None, description="主键ID，自增")
    project_name: str = Field(..., description="项目名称", max_length=50)
    file_path: str = Field(..., description="代码文件路径", max_length=1024)
    symbol_name: str = Field(..., description="类名/函数名", max_length=256)
    language: str = Field(..., description="编程语言", max_length=32)
    start_line: int = Field(..., description="代码起始行", ge=1)
    end_line: int = Field(..., description="代码结束行", ge=1)
    content: str = Field(..., description="代码原文", max_length=65535)
    summary: str | None = Field(None, description="函数摘要/Docstring", max_length=1024)
    last_updated: int = Field(..., description="时间戳（秒）")
    use_count: int = Field(default=0, description="成功修复被采纳次数", ge=0)
    embedding: list[float] | None = Field(None, description="代码向量（1024维）")
    summary_embedding: list[float] | None = Field(
        None, description="摘要向量（1024维）"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "project_name": "my-project",
                "file_path": "src/utils/helper.py",
                "symbol_name": "calculate_sum",
                "language": "python",
                "start_line": 10,
                "end_line": 15,
                "content": "def calculate_sum(a, b):\n    return a + b",
                "last_updated": 1735574400,
                "use_count": 0,
            }
        }
