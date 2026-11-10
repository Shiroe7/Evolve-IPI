"""Preserve the first aggregate prediction seal when a campaign resumes."""
import json
import math
import time
from pathlib import Path


def seal_predictions(path, predictions, orders, base_rates):
    path=Path(path)
    payload={'predictions':predictions,'orders':orders,'base_rates':base_rates}
    if path.exists():
        existing=json.loads(path.read_text())
        stamp=existing.get('at_unix')
        if (isinstance(stamp,bool) or not isinstance(stamp,(int,float)) or
            not math.isfinite(stamp) or stamp<=0 or
            set(existing)!=set(payload)|{'at_unix'} or
            any(existing[k]!=v for k,v in payload.items())):
            raise RuntimeError('Existing prediction seal differs; do not overwrite or rerun')
        return existing
    sealed={'at_unix':time.time(),**payload}
    with path.open('x') as handle:
        json.dump(sealed,handle,ensure_ascii=False,indent=2)
        handle.write('\n')
    return sealed
