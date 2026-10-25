#!/usr/bin/env python3
"""Offline tests of the patcher's generated JavaScript and request behavior."""
import os
from pathlib import Path
import subprocess
import tempfile

from apply_es_ax_patch import apply_patch

fixture = r'''
'use strict';
function fixture(_options, modifyResponse, proxy, express) {
    const optionsAlexa = {target:'https://alexa.amazon.com'};
    function replaceHosts(data) { return data; }
    function onProxyRes(proxyRes, req, res) {
        const url = req.originalUrl || req.url;
        if (url.endsWith('.js')) return;
        modifyResponse(res, '', function (body) {
            if (body) {
                const bodyOrig = body;
                body = replaceHosts(body);
                if (body !== bodyOrig) console.log('Changed');
            }
            return body;
        });
    }
    const myProxy = proxy(optionsAlexa);
    if (_options.expressInstance !== undefined) {
        _options.expressInstance.use(`${_options.proxyRootPath}`, myProxy);
    } else {
        const app = express();
        app.use(myProxy);
        _options.testApp = app;
    }
    return { onProxyRes };
}
module.exports = fixture;
'''

js_test = r'''
const assert = require('assert/strict');
const fixture = require(process.argv[2]);
const methods = [];
const app = { use(...args) { methods.push(args); } };
const opts = { proxyRootPath: '/proxy', baseAmazonPage:'amazon.com', expressInstance:app };
const render = (res, _, callback) => { res.body = callback(res.input); };
const {onProxyRes} = fixture(opts, render, () => () => {}, () => app);
const makeReq = (url, method='GET') => ({ path:url.split('?')[0], originalUrl:url, method });
const reply = { redirect(code, path) { this.code=code; this.location=path; } };
const rootHandler = methods[0][0];
assert.equal(methods.length, 2);
assert.equal(methods[1][0], '/proxy');
rootHandler(makeReq('/ax/claim?lang=en', 'POST'), reply, () => assert.fail('must redirect'));
assert.equal(reply.code, 307);
assert.equal(reply.location, '/proxy/www.amazon.com/ax/claim?lang=en');
for (const prefix of ['/ap/signin','/cvf/test','/aaut/test','/gp/test','/ax/claim/log']) {
   const r={redirect(code,location){this.code=code;this.location=location;}};
   rootHandler(makeReq(prefix), r, () => assert.fail(prefix));
   assert.equal(r.code, 307);
   assert.equal(r.location, '/proxy/www.amazon.com'+prefix);
}
for (const path of ['/axle', '/proxy/www.amazon.com/ax/claim', '/api/other']) {
   let nextCalled = false;
   rootHandler(makeReq(path), {}, () => { nextCalled=true; });
   assert.equal(nextCalled, true, path);
}
const res = {input:`<form action="/ax/claim" formaction='/ap/foo'>
<a href=/gp/test>go</a><script src="/assets/main.js"></script>
<a href="https://amazon.com/ap/test">absolute</a><a href="//cdn.example.com/a">cdn</a>
<a href="/proxy/www.amazon.com/ap/done">patched</a></form>`};
const upstream = {headers: {'content-type':'text/html; charset=UTF-8',
    'content-security-policy':"upgrade-insecure-requests",
    'strict-transport-security':'max-age=123'}};
onProxyRes(upstream, makeReq('/proxy/www.amazon.com/ap/signin'), res);
assert.equal(upstream.headers['content-security-policy'], undefined);
assert.equal(upstream.headers['strict-transport-security'], undefined);
assert.match(res.body, /action="\/proxy\/www.amazon.com\/ax\/claim"/);
assert.match(res.body, /formaction='\/proxy\/www.amazon.com\/ap\/foo'/);
assert.match(res.body, /href=\/proxy\/www.amazon.com\/gp\/test/);
assert.match(res.body, /src="\/proxy\/www.amazon.com\/assets\/main.js"/);
assert.match(res.body, /href="https:\/\/amazon.com\/ap\/test"/);
assert.match(res.body, /href="\/\/cdn.example.com\/a"/);
assert.equal((res.body.match(/\/proxy\/www.amazon.com\/ap\/done/g) || []).length, 1);
const javascriptRes = {input:'src="/js/dont-change.js"'};
const jsHeaders = {headers:{'content-type':'application/javascript','strict-transport-security':'max-age=1'}};
onProxyRes(jsHeaders, makeReq('/proxy/www.amazon.com/test'), javascriptRes);
assert.equal(javascriptRes.body, javascriptRes.input);
assert.equal(jsHeaders.headers['strict-transport-security'], undefined);
// Directly early-returned JS still has the upstream header removed.
const earlyHeaders={headers:{'content-security-policy':'default-src self'}};
onProxyRes(earlyHeaders, makeReq('/proxy/www.amazon.com/test.js'), {});
assert.equal(earlyHeaders.headers['content-security-policy'], undefined);
// Standalone branch registers redirect before proxy as well.
const standaloneOpts={proxyRootPath:'/proxy',baseAmazonPage:'amazon.com'};
fixture(standaloneOpts, render, () => () => {}, () => {
   const arr=[];
   return {use(...args){arr.push(args);this.handlers=arr;}};
});
assert.equal(standaloneOpts.testApp.handlers.length, 2);
assert.equal(typeof standaloneOpts.testApp.handlers[0][0], 'function');
console.log('PASS: header deletion, HTML URL rewriting, POST 307 routing, non-targets, both Express modes.');
'''

with tempfile.TemporaryDirectory() as d:
    p = Path(d) / 'proxy.js'
    p.write_text(apply_patch(fixture))
    test = Path(d) / 'test.js'
    test.write_text(js_test)
    check = subprocess.run(['node', '--check', str(p)], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    result = subprocess.run(['node', str(test), str(p)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    print(result.stdout.rstrip())
