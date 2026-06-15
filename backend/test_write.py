import pymcprotocol

plc = pymcprotocol.Type3E(plctype='iQ-L')
plc.connect('192.168.0.20', 5011)

before = plc.batchread_bitunits(headdevice='M100', readsize=1)
print(f'前 M100 = {before}')

plc.batchwrite_bitunits(headdevice='M100', values=[1])

after = plc.batchread_bitunits(headdevice='M100', readsize=1)
print(f'後 M100 = {after}')

plc.close()