"""No listener, no product, independent 15-second cooperative deadline."""
import sys
import time
from tools.portable_launcher import StopControl

control = StopControl(deadline=15)
control.start()
print('READY ' + sys.argv[1], flush=True)
control.event.wait(16)
reason = control.reason
joined = control.close()
print('ACK ' + str(reason), flush=True)
sys.exit(0 if reason == 'OPERATOR_STOP' and joined else 1)
