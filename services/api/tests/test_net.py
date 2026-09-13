"""Egress SSRF guard: scheme, literal-IP class, and DNS-resolved addresses."""

from sixsentences_server.core.net import is_public_http_url


def test_public_literals_and_hosts_are_allowed() -> None:
    assert is_public_http_url("https://example.com/x", resolve=False)
    assert is_public_http_url("http://93.184.216.34/x")  # a public literal IP
    assert is_public_http_url("https://8.8.8.8/")


def test_non_http_schemes_are_refused() -> None:
    assert not is_public_http_url("file:///etc/passwd")
    assert not is_public_http_url("ftp://example.com/x")
    assert not is_public_http_url("gopher://example.com/x")
    assert not is_public_http_url("not a url")


def test_literal_internal_ips_are_refused_without_dns() -> None:
    for url in (
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata (link-local)
        "http://127.0.0.1/x",
        "http://10.0.0.5/x",
        "http://192.168.1.1/x",
        "http://172.16.0.1/x",
        "http://100.64.0.1/x",  # CGNAT
        "http://0.0.0.0/x",
        "http://[::1]/x",
        "http://[::ffff:169.254.169.254]/x",  # ipv4-mapped metadata endpoint
        "http://localhost/x",
        "https://metadata.google.internal/x",
    ):
        assert not is_public_http_url(url, resolve=False), url


def test_hostname_resolving_to_private_ip_is_refused() -> None:
    # a hostname whose A-record points inward must be caught by resolution
    rebind = {"evil.example": ["169.254.169.254"], "good.example": ["93.184.216.34"]}
    resolver = lambda host: rebind[host]  # noqa: E731
    assert not is_public_http_url("https://evil.example/x", resolver=resolver)
    assert is_public_http_url("https://good.example/x", resolver=resolver)


def test_any_private_address_in_the_set_refuses() -> None:
    # if a name resolves to several IPs, one internal address is enough to refuse
    resolver = lambda host: ["93.184.216.34", "10.0.0.1"]  # noqa: E731
    assert not is_public_http_url("https://mixed.example/x", resolver=resolver)


def test_unresolvable_host_is_refused() -> None:
    def boom(host: str) -> list[str]:
        raise OSError("nxdomain")

    assert not is_public_http_url("https://nope.invalid/x", resolver=boom)
