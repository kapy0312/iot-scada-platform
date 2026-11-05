"""
透過 OPC UA 操作 PLCSIM 裡的模擬機台（不經過後端）

用法（在 backend 資料夾）：
    python tools/plcsim_ctl.py status          # 讀目前數值
    python tools/plcsim_ctl.py run             # 啟動馬達，轉速設定 1480
    python tools/plcsim_ctl.py stop            # 停止馬達
    python tools/plcsim_ctl.py fault 1         # 注入故障：1=背壓過高 2=冷卻風扇故障 3=負載降低
    python tools/plcsim_ctl.py fault 0         # 解除故障
"""
import asyncio
import os
import sys

from asyncua import Client, ua

PLC_URL = os.getenv("PLC_URL", "opc.tcp://192.168.0.10:4840")
NS = 3
FAULTS = {0: "正常", 1: "下游阻塞背壓過高（手冊 5.2）", 2: "冷卻風扇故障（手冊 3.1）", 3: "負載突然降低（手冊 5.1）"}


async def node(client: Client, name: str):
    return await client.nodes.root.get_child(
        ["0:Objects", f"{NS}:PLC_1", f"{NS}:DataBlocksGlobal", f"{NS}:DB1", f"{NS}:{name}"]
    )


async def write(client: Client, name: str, value, vtype):
    n = await node(client, name)
    await n.write_value(ua.DataValue(ua.Variant(value, vtype)))


async def status(client: Client):
    vals = {}
    for name in ("motor_enable", "speed_setpoint", "fault_mode", "motor_speed", "temperature", "pressure"):
        vals[name] = await (await node(client, name)).read_value()
    print(f"啟動={vals['motor_enable']}  設定={vals['speed_setpoint']:.0f} RPM  "
          f"故障={vals['fault_mode']}（{FAULTS.get(vals['fault_mode'], '?')}）")
    print(f"轉速={vals['motor_speed']:.1f} RPM  溫度={vals['temperature']:.2f} °C  壓力={vals['pressure']:.3f} bar")


async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    async with Client(url=PLC_URL) as client:
        if cmd == "run":
            await write(client, "speed_setpoint", 1480.0, ua.VariantType.Float)
            await write(client, "motor_enable", True, ua.VariantType.Boolean)
            print("已啟動，轉速設定 1480 RPM（約 10 秒到達）")
        elif cmd == "stop":
            await write(client, "motor_enable", False, ua.VariantType.Boolean)
            print("已停止")
        elif cmd == "fault":
            mode = int(sys.argv[2]) if len(sys.argv) > 2 else 0
            if mode not in FAULTS:
                print(f"故障模式只能是 {list(FAULTS)}")
                return
            await write(client, "fault_mode", mode, ua.VariantType.Int16)
            print(f"故障模式 → {mode}：{FAULTS[mode]}")
        elif cmd != "status":
            print(__doc__)
            return
        await status(client)


if __name__ == "__main__":
    asyncio.run(main())
