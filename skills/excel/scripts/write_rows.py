from __future__ import annotations
import csv
from pathlib import Path
from pydantic import BaseModel
TOOL_ID="write_rows"; DESCRIPTION="Write list of dicts to csv or xlsx."; DETERMINISTIC=True; REQUIRES_APPROVAL=False
FILESYSTEM_ROOTS=("run:work:read-write","run:output:read-write","run:tmp:read-write")
TOOL_EFFECT = 'write'
IDEMPOTENCY = 'reconcile'
ALLOW_IN_PROD = True
TIMEOUT_SECONDS = 120.0
SECRET_NAMES = ()
NETWORK_HOSTS = ()


class Input(BaseModel):
    path: str
    sheet: str = ""
    rows: list[dict] = []
    headers: list[str] = []
class Output(BaseModel):
    ok: bool = True
    path: str
    count: int
def run(args: Input) -> Output:
    p = Path(args.path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    headers = list(args.headers) or (list(args.rows[0].keys()) if args.rows else [])
    if p.suffix.lower() == ".csv":
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=headers)
            w.writeheader()
            for row in args.rows:
                w.writerow({h: row.get(h, "") for h in headers})
    else:
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active
        if args.sheet:
            ws.title = args.sheet
        ws.append(headers)
        for row in args.rows:
            ws.append([row.get(h, "") for h in headers])
        wb.save(p)
    return Output(ok=True, path=str(p.resolve()), count=len(args.rows))
