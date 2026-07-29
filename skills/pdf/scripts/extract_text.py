from __future__ import annotations
from pathlib import Path
from pydantic import BaseModel, Field
TOOL_ID="extract_text"; DESCRIPTION="Extract text from a PDF."; DETERMINISTIC=True; REQUIRES_APPROVAL=False
class Input(BaseModel):
    path: str
    max_chars: int = Field(default=200_000, ge=1)
class Output(BaseModel):
    ok: bool = True
    path: str
    text: str
    pages: int = 0
def run(args: Input) -> Output:
    p = Path(args.path).expanduser()
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ImportError("pdf.extract_text requires pypdf: uv add pypdf") from exc
    reader = PdfReader(str(p))
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    text = "\n".join(parts)
    if len(text) > args.max_chars:
        text = text[: args.max_chars] + "\n…[truncated]"
    return Output(ok=True, path=str(p.resolve()), text=text, pages=len(reader.pages))
