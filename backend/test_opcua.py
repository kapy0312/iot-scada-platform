import asyncio
from asyncua import Client

async def main():
    url = "opc.tcp://192.168.0.10:4840"

    async with Client(url=url) as client:
        print("✅ 連線成功，開始讀值\n")

        # Namespace 3 = PLC_1 的自定義空間
        ns = 3

        # 直接用路徑讀取變數
        motor_speed = await client.nodes.root.get_child(
            [f"0:Objects", f"{ns}:PLC_1", f"{ns}:DataBlocksGlobal", f"{ns}:DB1", f"{ns}:motor_speed"]
        )
        temperature = await client.nodes.root.get_child(
            [f"0:Objects", f"{ns}:PLC_1", f"{ns}:DataBlocksGlobal", f"{ns}:DB1", f"{ns}:temperature"]
        )
        pressure = await client.nodes.root.get_child(
            [f"0:Objects", f"{ns}:PLC_1", f"{ns}:DataBlocksGlobal", f"{ns}:DB1", f"{ns}:pressure"]
        )

        # 連續讀 5 次，確認數值有在變動
        for i in range(5):
            spd = await motor_speed.read_value()
            tmp = await temperature.read_value()
            prs = await pressure.read_value()
            print(f"[{i+1}] motor_speed={spd:.2f}, temperature={tmp:.3f}, pressure={prs:.3f}")
            await asyncio.sleep(1)

asyncio.run(main())