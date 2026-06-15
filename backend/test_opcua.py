import asyncio
from asyncua import Client
from asyncua.ua import DataValue, Variant, VariantType

async def main():
    url = "opc.tcp://192.168.0.10:4840"

    async with Client(url=url) as client:
        print("✅ 連線成功\n")
        ns = 3

        # 取得所有節點
        motor_speed    = await client.nodes.root.get_child([f"0:Objects", f"{ns}:PLC_1", f"{ns}:DataBlocksGlobal", f"{ns}:DB1", f"{ns}:motor_speed"])
        temperature    = await client.nodes.root.get_child([f"0:Objects", f"{ns}:PLC_1", f"{ns}:DataBlocksGlobal", f"{ns}:DB1", f"{ns}:temperature"])
        pressure       = await client.nodes.root.get_child([f"0:Objects", f"{ns}:PLC_1", f"{ns}:DataBlocksGlobal", f"{ns}:DB1", f"{ns}:pressure"])
        motor_enable   = await client.nodes.root.get_child([f"0:Objects", f"{ns}:PLC_1", f"{ns}:DataBlocksGlobal", f"{ns}:DB1", f"{ns}:motor_enable"])
        speed_setpoint = await client.nodes.root.get_child([f"0:Objects", f"{ns}:PLC_1", f"{ns}:DataBlocksGlobal", f"{ns}:DB1", f"{ns}:speed_setpoint"])

        # 讀取現有值
        print("--- 讀取現有值 ---")
        for i in range(3):
            spd = await motor_speed.read_value()
            tmp = await temperature.read_value()
            prs = await pressure.read_value()
            ena = await motor_enable.read_value()
            spt = await speed_setpoint.read_value()
            print(f"[{i+1}] speed={spd:.2f} | temp={tmp:.3f} | pressure={prs:.3f} | enable={ena} | setpoint={spt:.1f}")
            await asyncio.sleep(1)

        # 測試寫入 motor_enable（Bool）
        print("\n--- 測試寫入 motor_enable = True ---")
        await motor_enable.write_value(
            DataValue(Variant(True, VariantType.Boolean))
        )
        val = await motor_enable.read_value()
        print(f"寫入後 motor_enable = {val}")

        await asyncio.sleep(1)

        # 測試寫入 speed_setpoint（Real）
        print("\n--- 測試寫入 speed_setpoint = 1500.0 ---")
        await speed_setpoint.write_value(
            DataValue(Variant(1500.0, VariantType.Float))
        )
        val = await speed_setpoint.read_value()
        print(f"寫入後 speed_setpoint = {val:.1f}")

        await asyncio.sleep(1)

        # 復原
        print("\n--- 復原 ---")
        await motor_enable.write_value(DataValue(Variant(False, VariantType.Boolean)))
        await speed_setpoint.write_value(DataValue(Variant(0.0, VariantType.Float)))
        print("motor_enable = False, speed_setpoint = 0.0 復原完成")

asyncio.run(main())