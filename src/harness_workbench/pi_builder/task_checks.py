"""User-declared argv checks, independently bounded and distinct from capture."""
import math
from harness_workbench.capture import capture_bytes, run_bounded


def validate(checks):
    if not isinstance(checks, list) or not checks:
        raise ValueError('checks must be a nonempty list')
    seen=set()
    for check in checks:
        if not isinstance(check,dict) or set(check)-{'id','argv','timeout','expected_exit'}:
            raise ValueError('each check accepts id, argv, timeout and expected_exit')
        name=check.get('id');argv=check.get('argv');timeout=check.get('timeout',60)
        if not isinstance(name,str) or not name or name in seen:
            raise ValueError('check IDs must be unique nonempty strings')
        seen.add(name)
        if not isinstance(argv,list) or not argv or not all(isinstance(a,str) and '\x00' not in a for a in argv) or not argv[0]:
            raise ValueError('check argv must be a nonempty string list without NUL')
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or timeout<=0:
            raise ValueError('check timeout must be finite and positive')
        if type(check.get('expected_exit',0)) is not int:
            raise ValueError('check expected_exit must be an integer')
    return checks


def run(checks,workspace,env,redactions=()):
    validate(checks)
    results=[]
    for check in checks:
        try:
            result=run_bounded(check['argv'],cwd=workspace,env=env,timeout=check.get('timeout',60),
                stdout_limit=1_048_576,stderr_limit=1_048_576,termination_grace=1)
            passed=(result.returncode==check.get('expected_exit',0) and result.termination_reason is None
                and not result.group_alive_after_cleanup and not result.group_alive_before_cleanup)
            results.append({'id':check['id'],'argv':check['argv'],'passed':passed,
                'expected_exit':check.get('expected_exit',0),'returncode':result.returncode,
                'termination_reason':result.termination_reason,'group_alive_after_cleanup':result.group_alive_after_cleanup,
                'stdout':capture_bytes(result.stdout,source_bytes=result.stdout_source_bytes,redactions=redactions),
                'stderr':capture_bytes(result.stderr,source_bytes=result.stderr_source_bytes,redactions=redactions)})
        except OSError as error:
            results.append({'id':check['id'],'argv':check['argv'],'passed':False,'error':str(error)})
    return {'passed':all(r['passed'] for r in results),'results':results,
        'scope':'only the declared independent checks; capture validity is separate'}
