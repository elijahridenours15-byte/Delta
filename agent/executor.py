import subprocess
import sys
import os
import traceback

try:
    import resource
except Exception:
    resource = None


def _limit_resources(cpu_seconds=5, mem_bytes=None):
    if resource is None:
        return
    try:
        # Limit CPU time
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        # Limit address space if available (may not be enforced on all platforms)
        if mem_bytes is not None:
            try:
                resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
            except Exception:
                pass
    except Exception:
        pass


def execute_file(path, timeout=5, python_exe=None):
    """Execute a Python file with simple resource limits and return output dict."""
    python_exe = python_exe or sys.executable
    cwd = os.path.dirname(path) or os.getcwd()

    def _preexec():
        _limit_resources(cpu_seconds=timeout, mem_bytes=512 * 1024 * 1024)

    try:
        proc = subprocess.run([python_exe, path], capture_output=True, text=True, cwd=cwd, timeout=timeout + 2, preexec_fn=_preexec)
        return {'stdout': proc.stdout, 'stderr': proc.stderr, 'returncode': proc.returncode}
    except subprocess.TimeoutExpired:
        return {'error': 'Execution timed out'}
    except Exception as exc:
        return {'error': 'Execution failed', 'exception': str(exc), 'trace': traceback.format_exc()}


def execute_code(code_str, timeout=5, python_exe=None):
    import tempfile
    import os
    fd, path = tempfile.mkstemp(prefix='agent_code_', suffix='.py')
    os.close(fd)
    try:
        with open(path, 'w') as f:
            f.write(code_str)
        return execute_file(path, timeout=timeout, python_exe=python_exe)
    finally:
        try:
            os.remove(path)
        except Exception:
            pass
