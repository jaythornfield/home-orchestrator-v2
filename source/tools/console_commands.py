"""Bounded local console editor; never used for the private worker protocol.

The editor retains at most 64 printable ASCII characters/tabs in memory only.
Invalid/overflow flags latch until Enter/Esc; no truncated suffix can be STOP.
Terminal output consists of selected ASCII plus our own CR/space operations.
No terminal modes are changed. The nonblocking input owner remains StopControl.
"""
import ctypes
from ctypes import wintypes
import os
import threading

LIMIT = 64
PROMPT = 'Command (STOP + Enter): '
HELP = 'Command not accepted. Type STOP then Enter; Esc clears this line.'
ACCEPTED = 'STOP accepted. Closing this app only; no further input is needed.'

class CommandEditor:
    def __init__(self):
        self.buffer = ''
        self.invalid = False
        self.overflow = False
        self.extended = False
        self.after_cr = False
        self.accepted = False

    def clear(self):
        self.buffer = ''
        self.invalid = self.overflow = False

    def feed(self, char):
        if self.accepted:
            return 'NONE'
        if self.extended:
            self.extended = False
            return 'NONE'
        if char in ('\x00', '\xe0'):
            self.extended = True
            return 'NONE'
        if char == '\n' and self.after_cr:
            self.after_cr = False
            return 'NONE'
        self.after_cr = char == '\r'
        if char in ('\r', '\n'):
            valid = not (self.invalid or self.overflow)
            stop = valid and self.buffer.strip(' \t').upper() == 'STOP'
            nonempty = bool(self.buffer or self.invalid or self.overflow)
            self.clear()
            if stop:
                self.accepted = True
                return 'ACCEPT'
            return 'HELP' if nonempty else 'EMPTY'
        if char == '\x1b':
            self.clear()
            return 'DRAW'
        if char == '\b':
            self.buffer = self.buffer[:-1]
            return 'DRAW'
        if type(char) is not str or len(char) != 1 or not (char == '\t' or ' ' <= char <= '~'):
            self.invalid = True
            return 'DRAW'
        if len(self.buffer) >= LIMIT:
            self.overflow = True
        elif not self.overflow:
            self.buffer += char
        return 'DRAW'

    def display(self):
        text = self.buffer.replace('\t', '    ')
        if self.overflow:
            text += ' [line too long: clear or submit]'
        elif self.invalid:
            text += ' [ASCII commands only: clear or submit]'
        return text


def console_output_available(stream):
    """Real handle check, never rely on isatty or a tool's name."""
    if os.name != 'nt':
        return False
    try:
        import msvcrt
        handle = msvcrt.get_osfhandle(stream.fileno())
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetFileType.argtypes = [wintypes.HANDLE]
        kernel.GetFileType.restype = wintypes.DWORD
        kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        mode = wintypes.DWORD()
        return kernel.GetFileType(handle) == 2 and bool(kernel.GetConsoleMode(handle, ctypes.byref(mode)))
    except (OSError, ValueError, AttributeError):
        return False


class ConsoleOutput:
    """Short output-only lock; never holds it while waiting for input or exit."""
    def __init__(self, stream):
        self.stream = stream
        self.lock = threading.RLock()
        self.current = ''
        self.width = 0
        self.closed = False
        self.failed = False
        try:
            self.columns = max(2, min(500, os.get_terminal_size(stream.fileno()).columns))
        except (OSError, ValueError, AttributeError):
            self.columns = 80

    def _write(self, text):
        try:
            self.stream.write(text)
            self.stream.flush()
            return True
        except (OSError, UnicodeError, ValueError):
            self.failed = True
            return False

    def _refresh_columns(self):
        try:
            self.columns = max(2, min(500, os.get_terminal_size(self.stream.fileno()).columns))
        except (OSError, ValueError, AttributeError):
            pass

    def _line(self, current):
        # Refresh on every paint; a short prompt keeps text visible in narrow windows.
        self._refresh_columns()
        available = self.columns - 1
        prompt = PROMPT if available >= len(PROMPT) + 8 else 'STOP> ' if available >= 10 else '> '
        prompt = prompt[:max(0, available-1)]
        room = available - len(prompt)
        text = current
        if len(text) > room:
            text = ('...' + text[-(room-3):]) if room > 3 else text[-room:]
        return prompt + text

    def _erase(self):
        self._refresh_columns()
        return '\r' + ' ' * min(self.width, self.columns-1) + '\r'

    def draw(self, current):
        with self.lock:
            if self.closed:
                return True
            self.current = current
            text = self._line(current)
            output = self._erase() + text
            self.width = len(text)
            return self._write(output)

    def message(self, text):
        with self.lock:
            if self.closed:
                return self._write(text + '\n')
            line = self._line(self.current)
            output = self._erase() + text + '\n' + line
            self.width = len(line)
            return self._write(output)

    def submitted(self, result):
        with self.lock:
            self.current = ''
            if result == 'ACCEPT':
                self.closed = True
                output = self._erase() + ACCEPTED + '\n'
                self.width = 0
                return self._write(output)
            if result == 'HELP':
                output = self._erase() + HELP + '\n' + self._line('')
            else:
                output = self._erase() + self._line('')
            self.width = len(self._line(''))
            return self._write(output)

    def close(self):
        with self.lock:
            if self.closed:
                return True
            self.closed = True
            self.current = ''
            output = self._erase()
            self.width = 0
            return self._write(output)
