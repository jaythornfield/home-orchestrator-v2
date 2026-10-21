"""Synthetic local editor/control tests. No product listener or real keyboard claim."""
import io,json,sys,time,threading,types,unittest
from unittest.mock import patch,Mock
from tools.console_commands import CommandEditor,ConsoleOutput,PROMPT,HELP,ACCEPTED,console_output_available
from tools import portable_launcher as launcher

def feed(editor,text):return [editor.feed(c) for c in text]

class EditorTests(unittest.TestCase):
    def test_first_line_single_enter_accepts_only_on_submit(self):
        e=CommandEditor();self.assertNotIn('ACCEPT',feed(e,'STOP'));self.assertEqual(e.display(),'STOP')
        self.assertEqual(e.feed('\r'),'ACCEPT');self.assertEqual(feed(e,'\nSTOP\r\r'),['NONE']*7)
    def test_visible_backspace_esc_and_empty_line(self):
        e=CommandEditor();feed(e,'STOQ\bP');self.assertEqual(e.display(),'STOP')
        e.feed('\x1b');self.assertEqual(e.display(),'');self.assertEqual(e.feed('\b'),'DRAW')
        self.assertEqual(e.feed('\r'),'EMPTY');self.assertFalse(e.accepted)
        self.assertEqual(e.feed('\n'),'NONE');self.assertEqual(feed(e,'STOP\n')[-1],'ACCEPT')
    def test_ascii_case_and_whitespace_console_only(self):
        for text in ('STOP','stop','sToP',' \tStop\t '):
            self.assertEqual(feed(CommandEditor(),text+'\n')[-1],'ACCEPT')
    def test_invalid_lines_are_not_substrings_and_recover(self):
        for text in ('XSTOP','STOPX','ST OP','ＳＴＯＰ','中文STOP','STOP中文','\x01STOP','STOP\x7f'):
            e=CommandEditor();self.assertEqual(feed(e,text+'\r')[-1],'HELP');self.assertFalse(e.accepted)
            self.assertEqual(feed(e,'STOP\r')[-1],'ACCEPT')
    def test_invalid_and_overflow_are_latched_until_line_reset(self):
        e=CommandEditor();feed(e,'x'*65+'\b'*64+'STOP');self.assertLessEqual(len(e.buffer),64)
        self.assertEqual(e.feed('\r'),'HELP');self.assertEqual(feed(e,'STOP\r')[-1],'ACCEPT')
        e=CommandEditor();feed(e,'中文\bSTOP');self.assertEqual(e.feed('\r'),'HELP')
        e=CommandEditor();feed(e,'x'*65+'\x1bSTOP');self.assertEqual(e.feed('\r'),'ACCEPT')
    def test_delayed_extended_code_is_consumed_without_polling_for_it(self):
        for prefix in ('\x00','\xe0'):
            e=CommandEditor();self.assertEqual(e.feed(prefix),'NONE');self.assertTrue(e.extended)
            self.assertEqual(e.feed('S'),'NONE');self.assertFalse(e.extended)
            self.assertEqual(feed(e,'STOP\r')[-1],'ACCEPT')
    def test_cr_lf_crlf_multiline_and_repeated_enter(self):
        for text in ('STOP\r','STOP\n','STOP\r\n','invalid\r\nSTOP\r\n','\r\n\r\nSTOP\nSTOP\n'):
            self.assertEqual(feed(CommandEditor(),text).count('ACCEPT'),1)
    def test_no_input_escape_or_nonascii_is_rendered(self):
        e=CommandEditor();feed(e,'A\t\x1bB中文\x07')
        display=e.display();self.assertTrue(display.isascii());self.assertNotIn('\x1b',display)
        self.assertNotIn('\t',display);self.assertTrue(e.invalid)

class OutputTests(unittest.TestCase):
    def test_draw_matches_editing_and_help_only_once_per_invalid_submit(self):
        output=io.StringIO();screen=ConsoleOutput(output);e=CommandEditor();screen.draw(e.display())
        for c in 'STOQ\bP':
            e.feed(c);screen.draw(e.display())
        self.assertTrue(output.getvalue().endswith(PROMPT+'STOP'))
        self.assertNotIn(HELP,output.getvalue());screen.submitted('EMPTY');self.assertNotIn(HELP,output.getvalue())
        screen.submitted('HELP');self.assertEqual(output.getvalue().count(HELP),1)
    def test_short_message_redraw_retains_half_line_without_replaying_keystrokes(self):
        stream=io.StringIO();screen=ConsoleOutput(stream);screen.draw('ST')
        screen.message('READY');self.assertTrue(stream.getvalue().endswith('READY\n'+PROMPT+'ST'))
        self.assertEqual(screen.current,'ST')
    def test_display_never_writes_wrap_column(self):
        screen=ConsoleOutput(io.StringIO());screen.columns=80;screen.draw('X'*64+' [line too long: clear or submit]')
        self.assertLess(screen.width,80)
        screen.columns=20;self.assertLess(len(screen._line('X'*64)),20)
        self.assertTrue(screen._line('STOP').endswith('STOP'))
        class TerminalStream(io.StringIO):
            def fileno(self):return 1
        narrow=ConsoleOutput(TerminalStream())
        with patch('tools.console_commands.os.get_terminal_size',return_value=types.SimpleNamespace(columns=20)):
            self.assertEqual(narrow._line('STOP'),'STOP> STOP')
        with patch('tools.console_commands.os.get_terminal_size',return_value=types.SimpleNamespace(columns=12)):
            self.assertTrue(narrow._line('STOP').endswith('STOP'))
            self.assertLess(len(narrow._erase()),15)
    def test_output_failure_is_fixed_flag_without_input_or_exception_export(self):
        class Bad:
            def write(self,text):raise OSError('FICTIONAL_UNSAFE_EXCEPTION_MUST_NOT_EXPORT')
            def flush(self):pass
        screen=ConsoleOutput(Bad());self.assertFalse(screen.draw('SYNTHETIC'));self.assertTrue(screen.failed)
    def test_concurrent_messages_and_drawing_are_serialized(self):
        stream=io.StringIO();screen=ConsoleOutput(stream)
        ts=[threading.Thread(target=lambda:[screen.message('READY') for _ in range(20)]),
            threading.Thread(target=lambda:[screen.draw('ST') for _ in range(20)])]
        for t in ts:t.start()
        for t in ts:t.join(1);self.assertFalse(t.is_alive())
        self.assertEqual(stream.getvalue().count('READY'),20);self.assertEqual(screen.current,'ST')
    def test_redirected_stdout_does_not_claim_real_console(self):
        self.assertFalse(console_output_available(io.StringIO()))

class ControlTests(unittest.TestCase):
    def tearDown(self):launcher.CONSOLE_OUTPUT=None;launcher.OUTPUT_FAILED.clear()
    def fake_console(self,text):
        values=list(text)
        return types.SimpleNamespace(kbhit=lambda:bool(values),getwch=lambda:values.pop(0))
    def test_accepted_stop_precedes_fallible_notification(self):
        control=launcher.StopControl(deadline=1)
        original=control.request;trace=[]
        def requested(reason,source='INTERNAL'):trace.append(reason);original(reason,source)
        control.request=requested
        class BadOutput(ConsoleOutput):
            def submitted(self,result):
                self_assert=control.event.is_set() and control.reason=='OPERATOR_STOP'
                if not self_assert:raise AssertionError('STOP_NOT_SET_FIRST')
                return False
        with patch('tools.console_commands.console_output_available',return_value=True),patch('tools.console_commands.ConsoleOutput',side_effect=lambda stream:BadOutput(io.StringIO())):
            control._read_console(self.fake_console('STOP\r'))
        self.assertEqual(control.reason,'OPERATOR_STOP');self.assertEqual(control.source,'CONSOLE')
        self.assertEqual(trace[0],'OPERATOR_STOP');self.assertTrue(launcher.OUTPUT_FAILED.is_set())
    def test_console_to_redirected_output_has_no_prompt_or_echo(self):
        control=launcher.StopControl(deadline=1);stream=io.StringIO()
        with patch('tools.console_commands.console_output_available',return_value=False),patch.object(sys,'stdout',stream):
            control._read_console(self.fake_console('stop\r'))
        self.assertEqual(control.reason,'OPERATOR_STOP');self.assertEqual(stream.getvalue(),'')
    def test_half_line_does_not_block_deadline_page_stop_or_close(self):
        for reason in ('PAGE_STOP','NATURAL_DEADLINE'):
            control=launcher.StopControl(deadline=.12 if reason=='NATURAL_DEADLINE' else 1)
            with patch('tools.console_commands.console_output_available',return_value=False):
                t=threading.Thread(target=control._read_console,args=(self.fake_console('ST'),));t.start()
                control.initialized.wait(1)
                if reason=='PAGE_STOP':time.sleep(.07);control.request(reason)
                t.join(.7);self.assertFalse(t.is_alive())
            self.assertEqual(control.reason,reason)
    def test_existing_interrupt_cause_is_not_relabelled(self):
        control=launcher.StopControl(deadline=1)
        with patch('tools.console_commands.console_output_available',return_value=False):control._read_console(self.fake_console('\x03'))
        self.assertEqual(control.reason,'USER_INTERRUPT')
    def test_private_pipe_go_split_stop_and_eof_keep_no_editor(self):
        for ending,reason in [('STOP NATURAL_DEADLINE\r\n','NATURAL_DEADLINE'),('STOP\r\n','OPERATOR_STOP'),('stop\r\n','PARENT_INPUT_EOF'),('', 'PARENT_INPUT_EOF')]:
            token='0'*32;alias='run_'+'1'*32
            chunks=[('GO '+token+' '+repr(time.monotonic()+10)+' .1 0 '+alias+' 10\n').encode(),ending[:2].encode(),ending[2:].encode()]
            chunks=[x for x in chunks if x];kernel=types.SimpleNamespace(GetFileType=Mock(return_value=3),PeekNamedPipe=Mock(),GetConsoleMode=Mock(return_value=0))
            calls=0
            def peek(handle,a,b,c,count,d):
                nonlocal calls
                calls+=1
                if calls==1:return 1
                if chunks:count._obj.value=len(chunks[0]);return 1
                return 0
            kernel.PeekNamedPipe.side_effect=peek
            fake=types.SimpleNamespace(get_osfhandle=lambda fd:0)
            control=launcher.StopControl(deadline=1,start_token=token,accept_forwarded=True)
            original=control.request
            def request(r,source='INTERNAL'):original(r,source);control.closed.set()
            control.request=request;messages=[]
            with patch.dict(sys.modules,{'msvcrt':fake}),patch.object(launcher.ctypes,'WinDLL',return_value=kernel),patch.object(launcher.os,'read',side_effect=lambda *a:chunks.pop(0)),patch.object(launcher,'safe_print',side_effect=messages.append),patch.object(launcher,'CONSOLE_OUTPUT',None):
                control._read()
            self.assertTrue(control.admitted.is_set());self.assertEqual(control.reason,reason)
            self.assertFalse(any(token in x or 'Command (' in x or '\r' in x for x in messages))

if __name__=='__main__':unittest.main()
