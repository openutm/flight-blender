import datetime
import json
from dataclasses import asdict, is_dataclass


class EnhancedJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, (datetime.date, datetime.datetime)):
            return o.isoformat()
        if is_dataclass(o):
            return asdict(o)
        return super().default(o)
