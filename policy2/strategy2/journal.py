from pathlib import Path
import json
import time
import numpy as np


def json_default(value):
    if isinstance(value,np.ndarray):
        return value.tolist()
    if isinstance(value,np.generic):
        return value.item()
    raise TypeError(f"Cannot serialize {type(value)}")


class Journal:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        # Never silently overwrite an earlier experiment.
        self.file = self.path.open("x",encoding="utf-8")

    def __call__(self, event, **data):
        self.file.write(json.dumps({"event":event,"monotonic":time.monotonic(),**data},
                                  ensure_ascii=False,allow_nan=False,default=json_default)+"\n")
        self.file.flush()

    def close(self):
        self.file.close()
