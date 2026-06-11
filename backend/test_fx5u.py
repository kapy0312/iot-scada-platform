import pymcprotocol
import time

plc = pymcprotocol.Type3E(plctype="iQ-L")
plc.connect("127.0.0.1", 5011)

print("✅ 連線成功\n")

for i in range(5):
    values = plc.batchread_wordunits(headdevice="D100", readsize=3)
    motor_speed = values[0]
    temperature = values[1] / 10.0
    pressure    = values[2] / 10.0
    print(f"[{i+1}] motor_speed={motor_speed} RPM | temp={temperature}°C | pressure={pressure} bar")
    time.sleep(1)

plc.close()