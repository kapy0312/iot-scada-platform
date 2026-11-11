"""
透過 OPC UA 操作 PLCSIM 裡的模擬機台（不經過後端）

用法（在 backend 資料夾）：
    python tools/plcsim_ctl.py status          # 讀目前數值
    python tools/plcsim_ctl.py run             # 啟動馬達，轉速設定 1480
    python tools/plcsim_ctl.py stop            # 停止馬達
    python tools/plcsim_ctl.py fault 1         # 注入故障：1=背壓過高 2=冷卻風扇故障 3=負載降低 4=軸承磨損 5=機械卡滯
    python tools/plcsim_ctl.py fault 0         # 解除故障

  生產狀態（OEE，需要 DB1 已加入 plc/DB1_新增變數.txt 列的變數）：
    python tools/plcsim_ctl.py planned on      # 計畫停機（休息、保養）；off 解除
    python tools/plcsim_ctl.py changeover on   # 換模；off 解除
    python tools/plcsim_ctl.py fault-stop      # 停機故障（拴鎖，馬達會停）
    python tools/plcsim_ctl.py fault-reset     # 解除停機故障並送復歸脈衝
    python tools/plcsim_ctl.py product 2       # 切換品號
    python tools/plcsim_ctl.py counter-reset   # 良品／NG 計數歸零（模擬換班歸零）
"""
import asyncio
import os
import sys

from asyncua import Client, ua

PLC_URL = os.getenv("PLC_URL", "opc.tcp://192.168.0.10:4840")
NS = 3
STATES = {0: "計畫停機", 1: "運轉", 2: "待機", 3: "故障", 4: "換模"}
FAULTS = {
    0: "正常",
    1: "下游阻塞背壓過高（手冊 5.2）",
    2: "冷卻風扇故障（手冊 3.1）",
    3: "負載突然降低（手冊 5.1）",
    4: "軸承磨損（手冊3 4.1）",
    5: "機械卡滯（手冊3 4.2）",
}


async def node(client: Client, name: str):
    return await client.nodes.root.get_child(
        ["0:Objects", f"{NS}:PLC_1", f"{NS}:DataBlocksGlobal", f"{NS}:DB1", f"{NS}:{name}"]
    )


async def write(client: Client, name: str, value, vtype):
    n = await node(client, name)
    await n.write_value(ua.DataValue(ua.Variant(value, vtype)))


async def pulse(client: Client, name: str):
    """正緣觸發：TRUE 維持 0.5 秒（OB30 每 100 ms 掃一次，一定看得到）再回 FALSE"""
    await write(client, name, True, ua.VariantType.Boolean)
    await asyncio.sleep(0.5)
    await write(client, name, False, ua.VariantType.Boolean)


def on_off(arg: str | None) -> bool | None:
    return {"on": True, "off": False}.get((arg or "").lower())


async def status(client: Client):
    vals = {}
    for name in ("motor_enable", "speed_setpoint", "fault_mode", "motor_speed", "temperature", "pressure",
                 "vibration", "current"):
        try:
            vals[name] = await (await node(client, name)).read_value()
        except Exception:
            vals[name] = None  # PLC 程式還沒有這個變數
    print(f"啟動={vals['motor_enable']}  設定={vals['speed_setpoint']:.0f} RPM  "
          f"故障={vals['fault_mode']}（{FAULTS.get(vals['fault_mode'], '?')}）")
    print(f"轉速={vals['motor_speed']:.1f} RPM  溫度={vals['temperature']:.2f} °C  壓力={vals['pressure']:.3f} bar")
    vib = "（DB1 沒有此變數）" if vals["vibration"] is None else f"{vals['vibration']:.2f} mm/s"
    cur = "（DB1 沒有此變數）" if vals["current"] is None else f"{vals['current']:.1f} A"
    print(f"振動={vib}  電流={cur}")

    prod = {}
    for name in ("machine_state", "good_count", "ng_count", "product_id", "planned_stop", "changeover_req", "fault_stop"):
        try:
            prod[name] = await (await node(client, name)).read_value()
        except Exception:
            prod[name] = None
    if prod["machine_state"] is None:
        print("生產狀態：（DB1 沒有 machine_state，請依 plc/DB1_新增變數.txt 加入）")
        return
    total = (prod["good_count"] or 0) + (prod["ng_count"] or 0)
    print(f"狀態={prod['machine_state']}（{STATES.get(prod['machine_state'], '?')}）  品號={prod['product_id']}  "
          f"良品={prod['good_count']}  NG={prod['ng_count']}  總數={total}")
    print(f"計畫停機={prod['planned_stop']}  換模={prod['changeover_req']}  停機故障={prod['fault_stop']}")


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
        elif cmd in ("planned", "changeover"):
            flag = on_off(sys.argv[2] if len(sys.argv) > 2 else None)
            if flag is None:
                print(f"用法：python tools/plcsim_ctl.py {cmd} on|off")
                return
            name = "planned_stop" if cmd == "planned" else "changeover_req"
            await write(client, name, flag, ua.VariantType.Boolean)
            print(f"{'計畫停機' if cmd == 'planned' else '換模'} → {'開始' if flag else '結束'}")
        elif cmd == "fault-stop":
            await write(client, "fault_stop", True, ua.VariantType.Boolean)
            print("停機故障已觸發（拴鎖），解除請用 fault-reset")
        elif cmd == "fault-reset":
            await write(client, "fault_stop", False, ua.VariantType.Boolean)
            await pulse(client, "fault_reset")
            print("停機故障已復歸；motor_enable 仍為 1 時會自動回到運轉")
        elif cmd == "product":
            if len(sys.argv) < 3 or not sys.argv[2].isdigit():
                print("用法：python tools/plcsim_ctl.py product 品號（正整數）")
                return
            await write(client, "product_id", int(sys.argv[2]), ua.VariantType.Int16)
            print(f"品號 → {sys.argv[2]}")
        elif cmd == "counter-reset":
            await pulse(client, "counter_reset")
            print("良品／NG 計數已歸零")
        elif cmd != "status":
            print(__doc__)
            return
        if cmd != "status":
            await asyncio.sleep(0.3)   # 等 OB30 掃過一次，讀到的狀態才是新的
        await status(client)


if __name__ == "__main__":
    asyncio.run(main())
