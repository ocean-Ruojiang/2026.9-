from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import time
import numpy as np


def serializable(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value,np.ndarray):
        return value.tolist()
    if isinstance(value,np.generic):
        return value.item()
    if isinstance(value,(set,tuple)):
        return list(value)
    if isinstance(value,Path):
        return str(value)
    raise TypeError(f'Unsupported JSON value: {type(value).__name__}')


def write_json(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=serializable,allow_nan=False),encoding='utf-8')


class Journal:
    def __init__(self,folder):
        self.folder=Path(folder)
        self.folder.mkdir(parents=True,exist_ok=True)
        self.file=(self.folder/'events.jsonl').open('w',encoding='utf-8')
        self.started=time.monotonic()

    def __call__(self,event,**data):
        self.file.write(json.dumps(dict(event=event,elapsed_s=time.monotonic()-self.started,**data),
                                  ensure_ascii=False,default=serializable,allow_nan=False)+'\n')
        self.file.flush()

    def close(self):
        self.file.close()
