def after_step(step, obs, ctx):
    # `step` is an object (.id/.argv/.inputs); `obs` is a dict.
    return {step.id: obs["attempts"]}
