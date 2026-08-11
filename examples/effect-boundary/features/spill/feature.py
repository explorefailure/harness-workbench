"""Known-red fixture: a legal annotation with an undeclared file write."""
import os


def after_run(spec, ctx):
    path = os.path.join(spec.dir, "state", "spill.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("feature effect outside the declared allowance\n")
    return {"summary": "wrote outside the effect allowance"}
