"""Read-only source-boundary verification; no bot imports, database or credentials."""
import hashlib
import json
from pathlib import Path


ROOT=Path(__file__).resolve().parent


def verify(root=ROOT):
    receipt=json.loads((root/'development'/'v025-baseline.json').read_text(encoding='utf-8-sig'))
    if receipt.get('kind')!='source-only' or receipt.get('version')!=1:
        raise ValueError('SOURCE_BOUNDARY_RECEIPT_REJECTED')
    checked=0
    for name,digest in receipt['files'].items():
        if (Path(name).name!=name or '/' in name or '\\' in name
                or not (Path(name).suffix in ('.py','.ps1','.md','.psql') or name=='requirements.txt')):
            raise ValueError('SOURCE_BOUNDARY_NAME_REJECTED')
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('V024_SOURCE_CHANGED: '+name)
        checked+=1
    return checked


if __name__=='__main__':
    print('v0.24 source boundary verified:',verify(),'original source files unchanged.')
