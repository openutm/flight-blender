class StatusCode:
    @classmethod
    def list(cls):
        """Return the StatusCode options as a list of mapped key / value items."""
        return list(cls.dict().values())

    @classmethod
    def text(cls, key):
        """Text for supplied status code."""
        return cls.options.get(key, None)

    @classmethod
    def items(cls):
        """All status code items."""
        return cls.options.items()

    @classmethod
    def keys(cls):
        """All status code keys."""
        return cls.options.keys()

    @classmethod
    def labels(cls):
        return cls.options.values()

    @classmethod
    def names(cls):
        """Return a map of all 'names' of status codes in this class

        Will return a dict object, with the attribute name indexed to the integer value.

        e.g.
        {
            'PENDING': 10,
            'IN_PROGRESS': 20,
        }
        """
        keys = cls.keys()
        status_names = {}

        for d in dir(cls):
            if d.startswith("_"):
                continue
            # if d != d.upper():
            #     continue

            value = getattr(cls, d, None)

            if value is None:
                continue
            if callable(value):
                continue
            if not isinstance(value, int):
                continue
            if value not in keys:
                continue

            status_names[d] = value

        return status_names

    @classmethod
    def dict(cls):
        """Return a dict representation containing all required information"""
        values = {}

        for (
            name,
            value,
        ) in cls.names().items():
            entry = {
                "key": value,
                "name": name,
                "label": cls.label(value),
            }

            if hasattr(cls, "colors"):
                if color := cls.colors.get(value, None):
                    entry["color"] = color

            values[name] = entry

        return values

    @classmethod
    def label(cls, value):
        """Return the status code label associated with the provided value."""
        return cls.options.get(value, value)

    @classmethod
    def value(cls, label):
        """Return the value associated with the provided label."""
        label = label if isinstance(label, int) else label.lower()
        for k in cls.options.keys():
            if cls.options[k].lower() == label:
                return k

        raise ValueError("Label not found")

    @classmethod
    def state_code(cls, key):
        """Return the value associated with the provided label."""
        names = cls.names()
        for k, v in names.items():
            if v == key:
                return k

        raise ValueError("Key not found")


class ConformanceChecksList(StatusCode):
    """A list of conformance checks and their status"""

    # 1 is reserved for True / check
    C2 = 2
    C3 = 3
    C4 = 4
    C5 = 5
    C6 = 6
    C7a = 7
    C7b = 8
    C8 = 9
    C9a = 10
    C9b = 11
    C10 = 12
    C11 = 13

    options = {
        C2: "Flight Auth not granted",
        C3: "Telemetry Auth mismatch",
        C4: "Operation state invalid",
        C5: "Operation not activated",
        C6: "Telemetry time incorrect",
        C7a: "Flight out of bounds",
        C7b: "Flight altitude out of bounds",
        C8: "Geofence breached",
        C9a: "Telemetry not received",
        C9b: "Telemetry not received within last 15 secs",
        C10: "State not in accepted, non-conforming, activated",
        C11: "No Flight Authorization",
    }
