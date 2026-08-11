# Security policy

## Supported versions

No Harness Workbench version has been published yet. Untagged commits and
development branches do not receive security support.

After the first public release, only the newest published release will receive
security fixes. A fixed release supersedes older releases; this table will be
updated if the project adopts a longer support window.

| Version | Supported |
|---|---|
| newest published release | yes |
| older releases | no |
| untagged development snapshots | no |

## Reporting a vulnerability

For the public repository, report vulnerabilities through GitHub's private
vulnerability-reporting form:

<https://github.com/explorefailure/harness-workbench/security/advisories/new>

The release gate requires the maintainer to enable private vulnerability
reporting before making the repository public. If that link does not present a
private report form, **do not post exploit details, secrets, or proof-of-concept
code in a public issue**. There is currently no published security email
address. Open a content-free issue asking the maintainer to restore the private
reporting route, or wait until the private form is available.

Include the affected version or commit, impact, reproduction conditions, and a
minimal proof of concept in the private report. Please avoid accessing other
people's data, disrupting services, or publishing details before a fix is
available.

## Execution trust boundary

Harness Workbench intentionally executes workload commands and imports feature
modules. Specs, `steps[].argv`, referenced feature roots, `feature.py` files,
and preserved feature source used by replay are trusted inputs. They run with
the current user's filesystem, process, environment, and network permissions.

Harness Workbench is not a hostile-code sandbox. Feature powers constrain the
dispatcher and make some breaches measurable; they do not provide OS-level
containment. Replay uses a copied workload directory to avoid ordinary state
collisions, not to isolate code. Use an external container, virtual machine,
or other operating-system security boundary when executing code you do not
trust.

Run evidence can contain command output, selected environment values, specs,
and feature source. Treat run and campaign stores according to the sensitivity
of the data they capture.
