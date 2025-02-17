import tldextract


def process_localutm(subdomain: str, domain: str) -> str:
    uss_audience = subdomain + "." + domain
    return uss_audience

def generate_audience_from_base_url(base_url: str) -> str:
    switch = {
        "localhost": "localhost",
        "internal": "host.docker.internal",
        "test": "local.test",
        "localutm": "scdsc.uss2.localutm",  # TODO: Fix this, this need not be hard coded
    }

    try:
        ext = tldextract.extract(base_url)
    except Exception:
        return "localhost"

    if ext.domain in switch:
        return switch[ext.domain]

    if ext.domain == "localutm":
        return process_localutm(subdomain=ext.subdomain, domain=ext.domain)

    return ext.domain if not ext.suffix else ".".join(ext[:3])
