# test_write_m.py 修改
import pymcprotocol
import time

plc = pymcprotocol.Type3E(plctype='iQ-L')
plc.connect('192.168.0.20', 5011)

before = plc.batchread_bitunits(headdevice='M100', readsize=1)
print(f'寫入前 M100 = {before}')

plc.batchwrite_bitunits(headdevice='M100', values=[1])
time.sleep(0.1)

after = plc.batchread_bitunits(headdevice='M100', readsize=1)
print(f'寫入後 M100 = {after}')

plc.close()