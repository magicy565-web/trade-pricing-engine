#!/usr/bin/env python3
"""Generate inspectable JSON Schemas from the exact runtime models."""
from pathlib import Path
import inspect
import json
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from trade_pricing import models
out=ROOT/'schemas';out.mkdir(exist_ok=True)
for name,cls in vars(models).items():
    if inspect.isclass(cls) and issubclass(cls,models.Contract) and cls is not models.Contract:
        (out/f'{name}.json').write_text(json.dumps(cls.model_json_schema(),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(f'Exported {len(list(out.glob("*.json")))} model schemas.')
