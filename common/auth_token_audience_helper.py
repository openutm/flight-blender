import tldextract


def process_localutm(subdomain: str, domain: str) -> str:
    uss_audience = subdomain + "." + domain
    return uss_audience


def generate_audience_from_base_url(base_url: str) -> str:
    switch = {
        "localhost": "localhost",
        "internal": "host.docker.internal",
        "test": "local.test",
    }

    try:
        ext = tldextract.extract(base_url)
    except Exception:
        return "localhost"

    if ext.domain in switch:
        return switch[ext.domain]

    elif ext.domain == "localutm":
        return process_localutm(subdomain=ext.subdomain, domain=ext.domain)

    return ext.subdomain + "." + ext.domain + "." + ext.suffix
