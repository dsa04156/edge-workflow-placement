"""Capture client output and collector-host events, including interrupted phases."""
from datetime import datetime, timezone
import json
import subprocess


def capture_phase(command, input_text, base, stem, timeout=180):
    assert not any((base/f'{stem}.{ext}').exists() for ext in ['log','stderr','execution.json']), 'Preserve phase evidence'
    event={'started_at':datetime.now(timezone.utc).isoformat(),'timeout_seconds':timeout}
    def save(stdout,stderr):
        for extension,value in [('log',stdout),('stderr',stderr)]:
            # TimeoutExpired may carry bytes even when text=True.
            data=value if isinstance(value,bytes) else (value or '').encode()
            (base/f'{stem}.{extension}').write_bytes(data)
    try:
        result=subprocess.run(command,input=input_text,capture_output=True,text=True,timeout=timeout)
        save(result.stdout,result.stderr)
        event.update(status='completed',returncode=result.returncode)
        return result
    except subprocess.TimeoutExpired as error:
        save(error.stdout,error.stderr)
        event.update(status='timeout',error_type=type(error).__name__)
        raise
    except BaseException as error:
        event.update(status='failed',error_type=type(error).__name__,message=str(error))
        raise
    finally:
        event['completed_at']=datetime.now(timezone.utc).isoformat()
        (base/f'{stem}.execution.json').write_text(json.dumps(event,indent=2)+'\n')
