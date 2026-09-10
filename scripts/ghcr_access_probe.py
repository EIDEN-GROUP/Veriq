import json
import urllib.request

req = urllib.request.Request(
    "https://ghcr.io/token?scope=repository:eiden-group/veriq:pull&service=ghcr.io",
    headers={"User-Agent": "veriq-probe/1.0"})
tok = json.loads(urllib.request.urlopen(req, timeout=20).read())
t = tok.get("token", "")
print("token issued:", len(t), "bytes")
r = urllib.request.Request(
    "https://ghcr.io/v2/eiden-group/veriq/manifests/latest",
    headers={"Authorization": "Bearer " + t,
             "Accept": "application/vnd.docker.distribution.manifest.list.v2+json, "
                       "application/vnd.oci.image.index.v1+json, "
                       "application/vnd.oci.image.manifest.v1+json"})
try:
    resp = urllib.request.urlopen(r, timeout=20)
    manifest = json.loads(resp.read())
    print("PUBLIC PULL OK — mediaType:", manifest.get("mediaType"),
          "| entries:", len(manifest.get("manifests", [])))
except urllib.error.HTTPError as e:
    body = e.read().decode()[:200]
    print(f"HTTP {e.code}: {body}")
    if e.code in (401, 403):
        print("=> package is PRIVATE (or hidden). Fix: package settings -> Change visibility -> Public,")
        print("   or keep private (CI pulls work via GITHUB_TOKEN packages:read — only manual pulls need auth).")
