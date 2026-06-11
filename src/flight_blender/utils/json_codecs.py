import datetime
import json
import uuid
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum


class LazyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        if isinstance(obj, uuid.UUID):
            return str(obj)
        return super().default(obj)


class EnhancedJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, (datetime.date, datetime.datetime)):
            return o.isoformat()
        if is_dataclass(o):
            return asdict(o)
        if isinstance(o, Enum):
            return o.value
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, uuid.UUID):
            return str(o)
        return super().default(o)


class EnhancedJSONDecoder(json.JSONDecoder):
    def __init__(self, *args, **kwargs):
        super().__init__(object_hook=self.object_hook, *args, **kwargs)

    def object_hook(self, obj):
        for key, value in obj.items():
            try:
                obj[key] = datetime.datetime.fromisoformat(value)
            except (ValueError, TypeError):
                pass
        return obj
