import socket
import struct
import threading
import random
import time

# 模擬的 D 暫存器初始值
registers = {
    100: 1480,   # D100 motor_speed（RPM，整數）
    101: 700,    # D101 temperature × 10（700 = 70.0°C）
    102: 50,     # D102 pressure × 10（50 = 5.0 bar）
}

def simulate_fluctuation():
    """背景執行緒，讓資料自然波動"""
    while True:
        registers[100] = int(1480 + random.gauss(0, 10))   # 轉速
        registers[101] = int(700  + random.gauss(0, 5))    # 溫度
        registers[102] = int(50   + random.gauss(0, 2))    # 壓力
        time.sleep(1)

def parse_slmp_request(data: bytes):
    try:
        command = struct.unpack_from('<H', data, 11)[0]
        
        if command == 0x0401:
            # 正確 offset（從封包 hex 分析）
            head       = struct.unpack_from('<I', data, 15)[0] & 0xFFFFFF  # offset 15
            read_count = struct.unpack_from('<H', data, 19)[0]              # offset 19
            return head, read_count
    except Exception:
        pass
    return None, None

def build_slmp_response(head_device: int, read_count: int) -> bytes:
    values = []
    for i in range(read_count):
        addr = head_device + i
        values.append(registers.get(addr, 0))

    data_body = struct.pack(f'<{read_count}H', *values)

    end_code = 0x0000
    length = 2 + len(data_body)

    # SLMP 3E Binary 回應格式：
    # subheader(2) + reserved(1) + serial(2) + reserved(2) + length(2) + end_code(2) + data
    header = struct.pack('<HBH2sHH',
        0xD000,          # subheader
        0x00,            # reserved (1 byte)
        0x0001,          # serial number
        b'\x00\x00',     # reserved (2 bytes)
        length,          # data length
        end_code         # end code
    )

    return header + data_body

def handle_client(conn, addr):
    print(f"[MOCK] 連線來自：{addr}")
    try:
        while True:
            data = conn.recv(1024)
            if not data:
                break
            print(f"[MOCK] 收到封包 ({len(data)} bytes): {data.hex()}")
            
            head, count = parse_slmp_request(data)
            
            if head is not None and count is not None:
                response = build_slmp_response(head, count)
                conn.send(response)
                print(f"[MOCK] 讀取 D{head}~D{head+count-1} → {[registers.get(head+i,0) for i in range(count)]}")
            else:
                # 收到無法解析的封包，回傳空回應避免斷線
                conn.send(b'\xd0\x00\x00\x00\x01\x00\x00\x00\x02\x00\x00\x00')
                
    except Exception as e:
        print(f"[MOCK] 連線結束：{e}")
    finally:
        conn.close()
        print(f"[MOCK] 斷線：{addr}")

def start_mock_server(host="127.0.0.1", port=5011):
    # 背景執行緒讓資料波動
    t = threading.Thread(target=simulate_fluctuation, daemon=True)
    t.start()
    
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(5)
    print(f"[MOCK] FX5U Mock Server 啟動：{host}:{port}")
    print(f"[MOCK] 模擬暫存器：D100=motor_speed, D101=temp×10, D102=pressure×10")
    print(f"[MOCK] Ctrl+C 停止\n")
    
    try:
        while True:
            conn, addr = server.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\n[MOCK] 停止")
    finally:
        server.close()

if __name__ == "__main__":
    start_mock_server()