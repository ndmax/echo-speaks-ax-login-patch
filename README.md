# Echo Speaks: Amazon `/ax` Login Patch

Target: `tonesto7/echo-speaks-server`, bundled `libs/alexa-cookie/lib/proxy.js` [[file link]](https://github.com/tonesto7/echo-speaks-server/blob/master/libs/alexa-cookie/lib/proxy.js).

Background: Amazon's identifier-first sign-in now posts to root-relative URLs
such as `/ax/claim`. Echo Speaks normally mounts its Amazon proxy at `/proxy`.
The patch changes only the proxy code, not login-success detection, cookies,
or token acquisition.

## What changes

Every inserted section has an `ES-AX-PATCH` marker:

1. **Header removal** — deletes Amazon's `content-security-policy` and
   `strict-transport-security` from upstream proxied responses, before existing
   early-return branches.
2. **HTML rewriting** — rewrites root-relative `action`, `formaction`, `href`,
   and `src` attributes to `${proxyRootPath}/www.${baseAmazonPage}/...`. This
   handles double-quoted, single-quoted, and unquoted attribute values. It
   deliberately ignores full external URLs, protocol-relative (`//`) URLs,
   and URLs already beneath the configured proxy root. Rewriting is limited
   to HTML/XHTML responses and leaves the existing `replaceHosts` behavior.
3. **307 safety net** — redirects requests at the application's `/ax`, `/ap`,
   `/cvf`, `/aaut`, and `/gp` routes to the corresponding proxied path while
   retaining the method, body, path and query string. Middleware is mounted
   before the proxy in both embedded-Express and standalone modes.

## Patch an existing file

```sh
python3 apply_es_ax_patch.py /path/to/libs/alexa-cookie/lib/proxy.js --dry-run
python3 apply_es_ax_patch.py /path/to/libs/alexa-cookie/lib/proxy.js
```

The tool refuses to apply the patch twice or when its six expected source
anchors don't occur exactly once. It creates `proxy.js.bak-YYYYMMDD-HHMMSS`
and checks syntax using `node --check` when Node is available. It patches only
`proxy.js`; no dependencies or credentials are required.

## Docker: persistent installation

The upstream project's Dockerfile uses `/usr/src/app` as its work directory.
Verify your own image and container name if they differ.

```sh
# Replace "echo-speaks" with your actual container name.
docker exec echo-speaks sh -c 'test -f /usr/src/app/libs/alexa-cookie/lib/proxy.js'

# Copy the original from the running container to a persistent folder.
# Replace "/persistent/patch/folder with your preferred path.
mkdir -p /persistent/patch/folder
docker cp echo-speaks:/usr/src/app/libs/alexa-cookie/lib/proxy.js \
  /persistent/patch/folder/proxy.js

# Run the patcher (put apply_es_ax_patch.py wherever convenient).
python3 apply_es_ax_patch.py /persistent/patch/folder/proxy.js --dry-run
python3 apply_es_ax_patch.py /persistent/patch/folder/proxy.js
```

Add this **file** bind mount to the existing Echo Speaks container definition,
*in addition to* the existing data-directory mounts:

```yaml
volumes:
  - /persistent/patch/folder/proxy.js:/usr/src/app/libs/alexa-cookie/lib/proxy.js:ro
```

Recreate the container to apply the new mount (for Docker Compose,
`docker compose up -d --force-recreate` in the stack directory). Once the
mount is in place, a normal container restart loads any newer version of the
mounted file. A host-file mount persists across image upgrades/recreations,
though after a major server update the patch should be reviewed for
compatibility.

### Quick, nonpersistent test (alternative)

Instead of adding a bind mount, copy the patched file into the container:

```sh
docker cp /persistent/patch/folder/proxy.js \
  echo-speaks:/usr/src/app/libs/alexa-cookie/lib/proxy.js
docker restart echo-speaks
```

This method is **lost when the container is recreated**.

## Check the redirect without Amazon credentials

With the Echo Speaks login proxy active, request a sample root URL:

```sh
curl -si -X POST 'http://YOUR-SERVER:8091/ax/claim?check=1' \
  --data 'test=1' | head -n 15
```

The expected response is `HTTP/1.1 307 Temporary Redirect` with a
`Location` similar to `/proxy/www.amazon.com/ax/claim?check=1` (the Amazon
domain is based on your app's configuration). Do not use `curl -L` for this
smoke test, as it would forward the sample POST into Amazon.

Finally, sign in through the normal Echo Speaks page and inspect the browser's
Network panel if needed. Form posts should target the `/proxy/www.amazon.com/...`
path, not the bare `/ax/...` path. A real login must still be tested; this
package does not verify Amazon's live sign-in flow.

## Roll back

For a bind-mounted installation, restore the original backup to the mounted
`proxy.js` file and restart the container:

```sh
cp /persistent/patch/folder/proxy.js.bak-YYYYMMDD-HHMMSS \
   /persistent/patch/folder/proxy.js
docker restart echo-speaks
```

Replace the timestamp with the backup filename printed by the patcher. You can
also remove the bind mount entirely and recreate the container, which restores
the image's bundled proxy.

## Security notes

This removes two Amazon-supplied security headers **only on upstream proxy
responses**. That removes defense-in-depth controls, including the usual CSP
restrictions; it does not make the authentication proxy safe to publish on the
open internet. Keep port 8091 behind a trusted LAN or access-controlled HTTPS
reverse proxy. If HSTS was previously cached for the local hostname, removing
an upstream header does not necessarily clear the browser's prior cache.

## Validation

Run `python3 test_es_ax_patch.py` in a folder containing both Python files.
The offline tests check generated JavaScript syntax, header stripping, HTML
rewriting, path/query/method-preserving redirects, and both Express mounting
modes. They do not perform live Amazon authentication.
