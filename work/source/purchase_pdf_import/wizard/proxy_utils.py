from urllib.parse import quote


def build_proxy_url(host, port, login, password):
    host = (host or '').strip()
    if not host:
        return None
    port = (port or '').strip() or '8080'
    if login:
        auth = f"{quote(login, safe='')}:{quote(password or '', safe='')}@"
    else:
        auth = ''
    return f"http://{auth}{host}:{port}"
