"""
HTTP client helper -- IPv4-forced outbound requests.

`ipv4_only_request()` works around a common WSL2 issue where outbound IPv6
routes are broken/unreachable even though the network interface reports an
IPv6 address: the OS resolver returns the IPv6 address first, httpx tries
to connect to THAT address, and the connection attempt just hangs until it
times out (`httpx.ConnectTimeout`) -- even though the exact same host is
reachable instantly over IPv4. `curl` doesn't show this problem because it
races IPv4 and IPv6 in parallel ("Happy Eyeballs") and simply uses whichever
answers first; httpx makes a single attempt and does not race/fall back.

We sidestep this entirely by resolving the hostname to an IPv4 address
ourselves and connecting directly to that IP, while still sending the
correct `Host` header and TLS SNI hostname so the request and the
certificate validation both still target the real hostname -- the remote
server and TLS handshake behave exactly as if we'd connected normally.

Used for our calls to Supabase's Auth REST API (see app/api/v1/endpoints
/auth.py) so signup/signin work regardless of a given machine's IPv6
routing quirks, without needing any WSL/OS-level configuration change.
"""
import asyncio
import socket
from typing import Optional

import httpx


async def ipv4_only_request(
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    json: Optional[dict] = None,
    timeout: float = 10.0,
) -> httpx.Response:
    parsed = httpx.URL(url)
    host = parsed.host
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        # socket.getaddrinfo is a blocking call -- run it off the event loop.
        infos = await asyncio.to_thread(
            socket.getaddrinfo, host, port, socket.AF_INET, socket.SOCK_STREAM
        )
        ipv4_address = infos[0][4][0]
    except socket.gaierror:
        # Host has no IPv4 address at all (rare) -- fall back to a normal
        # request rather than failing outright.
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.request(method, url, headers=headers, json=json)

    ip_url = parsed.copy_with(host=ipv4_address)
    final_headers = dict(headers or {})
    final_headers.setdefault("Host", host)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.request(
                method,
                str(ip_url),
                headers=final_headers,
                json=json,
                extensions={"sni_hostname": host},
            )
    except httpx.ConnectTimeout:
        # IPv4 itself somehow also unreachable (e.g. genuinely no internet,
        # or the resolved IP is stale) -- one last plain attempt so the
        # actual underlying error (not our IPv4 workaround) is what surfaces.
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.request(method, url, headers=headers, json=json)