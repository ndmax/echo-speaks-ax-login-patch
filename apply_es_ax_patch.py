#!/usr/bin/env python3
"""Patch Echo Speaks' bundled alexa-cookie/lib/proxy.js for Amazon's /ax flow.

Usage:
    python3 apply_es_ax_patch.py /path/to/echo-speaks-server/libs/alexa-cookie/lib/proxy.js

The patcher intentionally fails if its exact source anchors are absent, protects
against double-patching, makes a timestamped backup and checks JavaScript syntax.
"""

import argparse
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


class PatchError(Exception):
    pass


def replace_once(source: str, needle: str, replacement: str, label: str) -> str:
    count = source.count(needle)
    if count != 1:
        raise PatchError(f"{label}: expected exactly one anchor; found {count}. No changes written.")
    return source.replace(needle, replacement, 1)


def apply_patch(source: str) -> str:
    if 'ES-AX-PATCH' in source:
        raise PatchError('File already contains ES-AX-PATCH markers. No changes written.')

    # (1) Always remove the upstream security headers, including when the
    # preexisting onProxyRes handler returns early for JS or image assets.
    anchor = 'function onProxyRes(proxyRes, req, res) {'
    added = '''function onProxyRes(proxyRes, req, res) {
        // ES-AX-PATCH 1: prevent upstream HSTS/CSP from escaping the local HTTP proxy.
        if (proxyRes && proxyRes.headers) {
            delete proxyRes.headers['content-security-policy'];
            delete proxyRes.headers['strict-transport-security'];
        }
'''
    source = replace_once(source, anchor, added.rstrip('\n'), 'Security headers')

    # (2) Rewrite form actions, buttons, stylesheet/script/asset links, etc.
    # Existing absolute URL rewriting in replaceHosts remains unchanged.
    anchor = 'function replaceHosts(data) {'
    added = '''// ES-AX-PATCH 2: keep root-relative HTML navigation inside Echo Speaks' proxy.
    function rewriteRootRelativeAttributes(html) {
        const proxyPath = `${_options.proxyRootPath}/www.${_options.baseAmazonPage}`;
        return html.replace(/\\b(action|formaction|href|src)(\\s*=\\s*)(?:"([^"]*)"|'([^']*)'|([^\\s>]+))/gi,
            function (match, attribute, equals, doubleQuoted, singleQuoted, unquoted) {
                const value = doubleQuoted !== undefined ? doubleQuoted :
                    (singleQuoted !== undefined ? singleQuoted : unquoted);
                if (!value.startsWith('/') || value.startsWith('//') ||
                    value === _options.proxyRootPath || value.startsWith(`${_options.proxyRootPath}/`)) {
                    return match;
                }
                const quote = doubleQuoted !== undefined ? '"' :
                    (singleQuoted !== undefined ? "'" : '');
                return `${attribute}${equals}${quote}${proxyPath}${value}${quote}`;
            });
    }

    function replaceHosts(data) {'''
    source = replace_once(source, anchor, added, 'HTML URL rewriting helper')

    anchor = 'body = replaceHosts(body);'
    added = '''body = replaceHosts(body);
                // ES-AX-PATCH 2: only rewrite attributes in HTML, not JSON or JavaScript.
                const contentType = (proxyRes && proxyRes.headers && proxyRes.headers['content-type']) || '';
                if (/^(?:text\\/html|application\\/xhtml\\+xml)(?:\\s*;|$)/i.test(contentType)) {
                    body = rewriteRootRelativeAttributes(body);
                }'''
    source = replace_once(source, anchor, added, 'HTML URL rewriting call')

    # (3) Root requests that escape the proxy are redirected *before* the
    # existing proxy middleware. 307 intentionally preserves POST method/body.
    anchor = 'const myProxy = proxy(optionsAlexa);'
    added = '''const myProxy = proxy(optionsAlexa);

    // ES-AX-PATCH 3: catch root-relative Amazon auth/API paths that escape /proxy.
    function redirectAmazonRootPaths(req, res, next) {
        if (!/^\\/(?:ax|ap|cvf|aaut|gp)(?:\\/|$)/.test(req.path)) {
            return next();
        }
        return res.redirect(307,
            `${_options.proxyRootPath}/www.${_options.baseAmazonPage}${req.originalUrl}`);
    }'''
    source = replace_once(source, anchor, added, 'Root request redirect helper')

    anchor = '_options.expressInstance.use(`${_options.proxyRootPath}`, myProxy);'
    added = '''_options.expressInstance.use(redirectAmazonRootPaths);
        _options.expressInstance.use(`${_options.proxyRootPath}`, myProxy);'''
    source = replace_once(source, anchor, added, 'Attached Express redirect route')

    anchor = 'app.use(myProxy);'
    added = '''app.use(redirectAmazonRootPaths);
        app.use(myProxy);'''
    source = replace_once(source, anchor, added, 'Standalone Express redirect route')

    return source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path, help='Path to libs/alexa-cookie/lib/proxy.js')
    parser.add_argument('--dry-run', action='store_true', help='Verify anchors without writing')
    args = parser.parse_args()
    target = args.file.expanduser().resolve()
    if not target.is_file():
        print(f'ERROR: file not found: {target}', file=sys.stderr)
        return 2

    try:
        original_bytes = target.read_bytes()
        original = original_bytes.decode('utf-8')
        newline = '\r\n' if b'\r\n' in original_bytes else '\n'
        patched = apply_patch(original.replace('\r\n', '\n'))
        patched_bytes = patched.replace('\n', newline).encode('utf-8')
        if args.dry_run:
            print('DRY RUN PASS: all six source anchors matched; all three patches can be applied.')
            return 0

        # Check syntax before touching the original, if Node is available.
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.js', dir=target.parent, delete=False) as f:
            f.write(patched_bytes)
            temp_path = Path(f.name)
        try:
            if shutil.which('node'):
                result = subprocess.run(['node', '--check', str(temp_path)], capture_output=True, text=True)
                if result.returncode:
                    raise PatchError('node --check failed:\n' + result.stderr)
            else:
                print('WARNING: Node.js not installed here; JavaScript syntax check skipped.')

            timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
            backup = target.with_name(f'{target.name}.bak-{timestamp}')
            if backup.exists():
                raise PatchError(f'Backup already exists: {backup}')
            shutil.copy2(target, backup)
            os.chmod(temp_path, target.stat().st_mode)
            os.replace(temp_path, target)
            print('Patched: ' + str(target))
            print('Backup:  ' + str(backup))
            print('Markers: ES-AX-PATCH 1, 2, 3')
            print('JavaScript syntax: ' + ('PASS' if shutil.which('node') else 'not checked'))
        finally:
            temp_path.unlink(missing_ok=True)
        return 0
    except (OSError, UnicodeError, PatchError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
