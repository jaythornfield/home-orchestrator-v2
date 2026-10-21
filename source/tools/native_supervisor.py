"""Finite task-owned native-handle observer. Never signals or kills a process."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid

KERNEL = ctypes.WinDLL('kernel32', use_last_error=True)
KERNEL.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
KERNEL.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
KERNEL.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]


def native_identity(process):
    times = [ctypes.c_ulonglong() for _ in range(4)]
    if not KERNEL.GetProcessTimes(int(process._handle), *(ctypes.byref(t) for t in times)):
        raise ctypes.WinError(ctypes.get_last_error())
    size = wintypes.DWORD(32768)
    image = ctypes.create_unicode_buffer(size.value)
    if not KERNEL.QueryFullProcessImageNameW(int(process._handle), 0, image, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    class ProcessBasic(ctypes.Structure):
        _fields_ = [('reserved1',ctypes.c_void_p),('peb',ctypes.c_void_p),
                    ('reserved2',ctypes.c_void_p*2),('unique_pid',ctypes.c_size_t),
                    ('parent_pid',ctypes.c_size_t)]
    ntdll = ctypes.WinDLL('ntdll')
    ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, wintypes.ULONG,
        ctypes.c_void_p,wintypes.ULONG,ctypes.POINTER(wintypes.ULONG)]
    ntdll.NtQueryInformationProcess.restype = wintypes.LONG
    info = ProcessBasic()
    returned = wintypes.ULONG()
    status = ntdll.NtQueryInformationProcess(int(process._handle),0,ctypes.byref(info),ctypes.sizeof(info),ctypes.byref(returned))
    if status != 0 or info.unique_pid != process.pid or info.parent_pid != os.getpid():
        raise RuntimeError('Native parent/child identity mismatch')
    return {'pid':process.pid, 'creator_pid':os.getpid(),'native_parent_pid':info.parent_pid,
            'creation_filetime':times[0].value,
            'image':image.value, 'observer':'CREATION_RETAINED_POPEN_NATIVE_HANDLE'}


class OwnedProcess:
    def __init__(self, command, cwd, *, env=None):
        self.started = time.monotonic()
        self.process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
        self.identity = None
        self.identity_error = None
        try:
            self.identity = native_identity(self.process)
        except Exception as exc:
            # Keep the created handle; caller must observe the bounded child exit.
            self.identity_error = type(exc).__name__
        self.lines = []
        self.messages = queue.Queue()
        self.key = None
        self.private_ready = None
        self.control_ack_at = None
        self.control_ack_reason = None
        def drain():
            while True:
                data = self.process.stdout.readline(65536)
                if not data:
                    return
                line = data.decode('utf-8', errors='replace').rstrip()
                if 'Local preview code: ' in line:
                    self.key = line.split('Local preview code: ',1)[1]
                    line = 'Local preview code: [REDACTED]'
                if line.startswith('PRIVATE_READY '):
                    try:
                        self.private_ready = json.loads(line[len('PRIVATE_READY '):])
                    except (ValueError,TypeError):
                        self.output_error = 'PRIVATE_READY_INVALID'
                    continue
                if line.startswith('PRIVATE_ACK '):
                    try:
                        frame = json.loads(line[len('PRIVATE_ACK '):])
                        if (set(frame)=={'kind','reason'} and frame['kind']=='LAUNCHER_INPUT_CONSUMED'
                                and frame['reason'] in {'OPERATOR_STOP','PAGE_STOP','NATURAL_DEADLINE','PARENT_INPUT_EOF',
                                    'CONTROL_INPUT_UNAVAILABLE','CONTROL_INPUT_ERROR','USER_INTERRUPT','STARTUP_DEADLINE',
                                    'STARTUP_ADMISSION_DEADLINE','READY_IDENTITY_ERROR','SERVICE_ERROR','LAUNCHER_ERROR',
                                    'AUDIT_FAILURE','SAFETY_CONTROL_FAILURE','PREPARATION_CANCELLED'}):
                            self.control_ack_at = time.monotonic()
                            self.control_ack_reason = frame['reason']
                    except (ValueError,TypeError):
                        self.output_error = 'PRIVATE_ACK_INVALID'
                    continue
                self.lines.append({'at':time.monotonic(),'text':line})
                self.messages.put(line)
        self.thread = threading.Thread(target=drain, name='owned-output', daemon=True)
        self.output_error = None
        try:
            self.thread.start()
        except RuntimeError as exc:
            self.output_error = type(exc).__name__

    def send_line(self, line):
        self.process.stdin.write((line+'\n').encode('ascii'))
        self.process.stdin.flush()

    def send_stop(self, reason=None):
        self.stop_input_at = time.monotonic()
        if reason is None:
            self.send_line('STOP')
        else:
            permitted = {'OPERATOR_STOP','PAGE_STOP','NATURAL_DEADLINE','PARENT_INPUT_EOF',
                'CONTROL_INPUT_UNAVAILABLE','CONTROL_INPUT_ERROR','USER_INTERRUPT','STARTUP_DEADLINE',
                'STARTUP_ADMISSION_DEADLINE','READY_IDENTITY_ERROR','SERVICE_ERROR','LAUNCHER_ERROR',
                'AUDIT_FAILURE','SAFETY_CONTROL_FAILURE','PREPARATION_CANCELLED'}
            if reason not in permitted:
                raise ValueError('INVALID_STOP_REASON')
            self.send_line('STOP '+reason)

    def wait(self, timeout=20):
        self.process.wait(timeout=timeout)
        native = wintypes.DWORD()
        if not KERNEL.GetExitCodeProcess(int(self.process._handle), ctypes.byref(native)):
            raise ctypes.WinError(ctypes.get_last_error())
        if self.thread.ident is not None:
            self.thread.join(2)
        return {'native_exit_code':native.value, 'wait_return_code':self.process.returncode,
                'observed_at':time.monotonic(), 'output_drain_joined':not self.thread.is_alive(),'output_error':self.output_error,
                'stdout':self.lines}


if __name__ == '__main__':
    print('Native ownership is an internal library. Use the source-safe acceptance harness.')
    raise SystemExit(2)
