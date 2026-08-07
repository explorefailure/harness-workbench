"""Bind what ran to the identity of what it ran on.

Requires the `content-digest` capability -- it reads whatever provided it
through the record, never by importing that feature. Deliberately NOT
called 'signed': there is no key here, so this is a binding digest, not a
signature, and claiming otherwise would be false.
"""
import hashlib, json

CAPABILITY = "content-digest"

def after_run(spec, ctx):
    # Resolve the capability this feature DECLARED, via the provider map the
    # core builds from the manifests. The earlier version scanned every
    # feature's extras for any dict containing a "digests" key and took the
    # first hit -- so the declared edge and the real edge were different
    # things, and which provider won depended on dict order. Two features
    # writing `digests` silently bound the wrong one.
    provider = (ctx.get("providers") or {}).get(CAPABILITY)
    blob = (ctx.get("extras") or {}).get(provider) or {}
    inputs = blob.get("digests") or {}
    payload = {"run_id": ctx["run_id"], "spec_digest": spec.digest,
               "run_class": spec.run_class,
               "inputs_from": provider, "inputs": inputs}
    # The house canonical rule, matched exactly: sorted keys, no
    # insignificant whitespace, UTF-8, ensure_ascii=False. Omitting
    # ensure_ascii=False would diverge from the base on any non-ASCII
    # byte, and a self-attested digest that only matches for ASCII is
    # worse than none -- it would pass every test and fail in the wild.
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    return {"bound": payload,
            "digest": "sha256:" + hashlib.sha256(body).hexdigest(),
            "summary": "bound %d input digest(s)" % len(inputs)}
