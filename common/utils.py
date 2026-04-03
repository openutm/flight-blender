import datetime
import json
from dataclasses import asdict, is_dataclass

from django.core.serializers.json import DjangoJSONEncoder
from django.utils.encoding import force_str
from django.utils.functional import Promise


class LazyEncoder(DjangoJSONEncoder):
    def default(self, obj):
        if isinstance(obj, Promise):
            return force_str(obj)
        return super(LazyEncoder, self).default(obj)


class EnhancedJSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, (datetime.date, datetime.datetime)):
            return o.isoformat()
        if is_dataclass(o):
            return asdict(o)
        return super().default(o)


class EnhancedJSONDecoder(json.JSONDecoder):
    def __init__(self, *args, **kwargs):
        super().__init__(object_hook=self.object_hook, *args, **kwargs)

    def object_hook(self, obj):
        for key, value in obj.items():
            try:
                # Attempt to parse datetime strings back into datetime objects
                obj[key] = datetime.datetime.fromisoformat(value)
            except (ValueError, TypeError):
                pass
        return obj


def normalize_view_box(view: list[float]) -> tuple[float, float, float, float]:
    """
    Normalizes a bounding box so that min values are less than max values.
    Args:
        view (List[float]): [minx, miny, maxx, maxy]
    Returns:
        tuple[float, float, float, float]: (minx, miny, maxx, maxy) with correct ordering
    """
    minx, miny, maxx, maxy = view[0], view[1], view[2], view[3]
    if minx > maxx:
        minx, maxx = maxx, minx
    if miny > maxy:
        miny, maxy = maxy, miny
    return minx, miny, maxx, maxy
