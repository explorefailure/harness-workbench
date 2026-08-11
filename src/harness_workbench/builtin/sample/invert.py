"""sample, with its decision inverted: one draw, whatever `n` says.

sample's decision is not a predicate. It answers "how many draws is this
step worth?", and every other inversion in this tree answers a yes/no --
which is why this file did not exist for as long as it didn't. `decision`
had come to mean `boolean`, and a feature whose decision is a QUANTITY read
as a feature with no decision at all.

That reading is worth correcting rather than working around, because the
quantity case is not a corner: a budget's cap, a timeout's bound and a
sampler's `n` are all numbers, and a control that silently ignores its own
configuration is precisely the failure this family exists to expose. A
`sample` wired to draw once regardless of `n` passes verify, conform,
fidelity and confine -- measured, all four green -- and `diff` catches it
only if someone already has an honest run to compare against, which is
exactly what you do not have for a feature that was never right.

The inversion keeps the config READ, so what is inverted is the decision
taken on it and not the ability to see it. Inverting the read would be a
fault, and faults are Family 2's experiment.
"""


def around_step(step, run_step, ctx):
    int(ctx.config.get("n", 3))          # read, then deliberately disregarded
    return [run_step()]
