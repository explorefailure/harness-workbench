"""receipt, with its binding inverted. Family 7's mutant.

receipt's decision is a claim: "this digest is the digest OF this payload."
The inversion keeps the payload and publishes a digest of something else,
which is the smallest possible lie a binding feature can tell -- and exactly
the lie a receipt exists to make impossible.

WELL-FORMED. Same keys, same types, same summary, no raise. Only the claim
is false. That matters: a receipt that crashes tests containment, and a
receipt that quietly binds the wrong bytes tests whether anyone checks.

The expected kill is by `conform`, not by `diff`. `_self_attestation`
verifies a declared payload/digest pair generically -- which is what makes
this the case worth having: it proves a checker, not just a comparison, has
this feature's back. A feature killed only by `diff` is load-bearing; one
killed by `conform` is load-bearing and guarded.
"""
import hashlib, json

CAPABILITY = "content-digest"


def after_run(spec, ctx):
    provider = (ctx.get("providers") or {}).get(CAPABILITY)
    blob = (ctx.get("extras") or {}).get(provider) or {}
    inputs = blob.get("digests") or {}
    payload = {"run_id": ctx["run_id"], "spec_digest": spec.digest,
               "run_class": spec.run_class,
               "inputs_from": provider, "inputs": inputs}

    # THE INVERSION. The digest is taken over a payload that is NOT the one
    # published below -- one extra key, nothing else. Byte-for-byte a
    # plausible sha256; simply not a digest of what it sits beside.
    lie = dict(payload)
    lie["inputs_from"] = "(not the published payload)"
    body = json.dumps(lie, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    return {"bound": payload,
            "digest": "sha256:" + hashlib.sha256(body).hexdigest(),
            "summary": "bound %d input digest(s)" % len(inputs)}
