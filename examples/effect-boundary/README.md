# Filesystem-effects example

The watched root is explicit and narrow; there is deliberately no default:

```console
$ hwb effects clean.json --watch state --allow state/allowed.txt
...
WITHIN ENVELOPE under the declared endpoint sensor -- not a global clean verdict
```

The known-red `spill` feature returns a legal annotation **and** writes a
second file behind the record channel. `hwb confine` cannot see that class of
effect; this campaign can because the file survives into the after snapshot:

```console
$ hwb effects breach.json --watch state --allow state/allowed.txt
...
BREACH  added           state/spill.txt
```

Remove `state/allowed.txt` and `state/spill.txt` between manual runs if you
want the examples to show `added` rather than `content_changed`. The verdict
is the same either way.
