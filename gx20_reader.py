# -*- coding: utf-8 -*-
"""
gx20_reader.py
==============
移植自 kalapontsai/GX20-PW3335-Data-Collection/GX20_PW3335.py 的 GX20 通訊部分。

通訊協定（與桌面版完全一致）:
  - TCP 連線到 host:port（預設 192.168.1.1:34434）
  - 送出指令: b"FData,0,0001,1210\\r\\n"
  - 接收回應為多行 31-char 固定格式:
        [0]    : 資料狀態 (N=Normal / B=...)
        [2:6]  : 4-char 頻道號碼
        [10:18]: 單位
        [18]   : 正負號
        [19:31]: 科學符號數值（含小數點、E、指數）
  - 解析後值 > 999 視為無效（None）

網頁版與桌面版差異:
  - 移除所有 Tkinter/CSV/matplotlib 相依
  - 新增 get_all_temperatures() 一次回傳 6 工位扁平 dict
  - 新增非同步可呼叫介面（網頁 poller 用）
"""

import socket
import time
import logging
from typing import Dict, List, Optional

log = logging.getLogger("gx20_reader")


# === 6 工位的頻道對應表（直接搬自桌面版 GX20_PW3335.py） ===
CHANNEL_NUMBER: Dict[str, List[str]] = {
    "工位1": ["0001", "0002", "0003", "0004", "0005", "0006", "0007", "0008", "0009", "0010",
              "0101", "0102", "0103", "0104", "0105", "0106", "0107", "0108", "0109", "0110"],
    "工位2": ["0201", "0202", "0203", "0204", "0205", "0206", "0207", "0208", "0209", "0210",
              "0301", "0302", "0303", "0304", "0305", "0306", "0307", "0308", "0309", "0310"],
    "工位3": ["0401", "0402", "0403", "0404", "0405", "0406", "0407", "0408", "0409", "0410",
              "1001", "1002", "1003", "1004", "1005", "1006", "1007", "1008", "1009", "1010"],
    "工位4": ["0701", "0702", "0703", "0704", "0705", "0706", "0707", "0708", "0709", "0710",
              "0801", "0802", "0803", "0804", "0805", "0806", "0807", "0808", "0809", "0810"],
    "工位5": ["0501", "0502", "0503", "0504", "0505", "0506", "0507", "0508", "0509", "0510",
              "0601", "0602", "0603", "0604", "0605", "0606", "0607", "0608", "0609", "0610"],
    "工位6": ["1101", "1102", "1103", "1104", "1105", "1106", "1107", "1108", "1109", "1110",
              "1201", "1202", "1203", "1204", "1205", "1206", "1207", "1208", "1209", "1210"],
}
VOLTAGE_UNITS = {"V", "DCV", "VDC"}
DEFAULT_VOLTAGE_CHANNELS = {"0610"}
STATIONS = list(CHANNEL_NUMBER.keys())            # ["工位1", ..., "工位6"]
POINTS_PER_STATION = 20

# 預設顏色（每接點一色，使用 matplotlib tab20 + tab20b 組合）
DEFAULT_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
    "#bcbd22", "#17becf", "#393b79", "#637939", "#8c6d31", "#843c39", "#7b4173", "#3182bd",
    "#31a354", "#756bb1", "#636363", "#e6550d",
]


class GX20:
    """單一 GX20 記錄器的通訊客戶端。"""

    def __init__(self, host: str = "192.168.1.1", port: int = 34434,
                 timeout: float = 3.0, voltage_channels=None):
        self.gsRemoteHost = host
        self.gnRemotePort = port
        self.timeout = timeout
        self.channel_number = CHANNEL_NUMBER
        self.voltage_channels = set(
            DEFAULT_VOLTAGE_CHANNELS if voltage_channels is None else voltage_channels
        )
        # 當前快取（最近一次成功讀取的值）
        self.channels_temp: Dict[str, List[Optional[float]]] = {
            s: [None] * POINTS_PER_STATION for s in STATIONS
        }

    # ---------- 協定解析（與桌面版一致） ----------

    @staticmethod
    def parse_scientific_notation(value_str: str) -> Optional[float]:
        """解析 GX20 數值；非數字或超過合理範圍回傳 None。"""
        try:
            value = float(value_str.strip())
            return None if abs(value) > 999 else value
        except (ValueError, TypeError):
            return None

    @staticmethod
    def parse_channel_data(line: str) -> Optional[dict]:
        """解析 GX20 固定欄位資料，兼容電壓行較長的格式。"""
        if len(line) < 19 or len(line) < 6 or not line[2:6].strip():
            return None
        return {
            "type":    line[0],
            "channel": line[2:6],
            "unit":    line[10:18].strip(),
            "value_str": line[18:].strip(),
        }

    @classmethod
    def parse_channel_value(cls, parsed: dict, voltage_channels=None) -> Optional[float]:
        """依設定的訊號源解析數值；電壓頻道只接受電壓單位。"""
        unit = parsed["unit"].upper().replace(" ", "")
        channels = DEFAULT_VOLTAGE_CHANNELS if voltage_channels is None else set(voltage_channels)
        if parsed["channel"] in channels and unit not in VOLTAGE_UNITS:
            return None
        return cls.parse_scientific_notation(parsed["value_str"])

    # ---------- 主通訊 ----------

    def GX20GetData(self) -> Optional[Dict[str, List[Optional[float]]]]:
        """
        建立 TCP 連線、發指令、解析回應，回填 self.channels_temp。
        失敗時 channels_temp 維持上一次的值（不重置），並回傳 None。
        """
        try:
            with socket.create_connection(
                (self.gsRemoteHost, self.gnRemotePort), timeout=self.timeout
            ) as s:
                s.sendall(b"FData,0,0001,1210\r\n")
                time.sleep(0.5)
                data = s.recv(10240).decode("ascii", errors="ignore")

                for line in data.splitlines():
                    parsed = self.parse_channel_data(line)
                    if not parsed:
                        continue
                    channel = parsed["channel"]
                    value = self.parse_channel_value(parsed, self.voltage_channels)
                    # 找該 channel 屬於哪個工位的哪個 index
                    for station_name, ch_list in self.channel_number.items():
                        if channel in ch_list:
                            idx = ch_list.index(channel)
                            self.channels_temp[station_name][idx] = (
                                round(value, 1) if value is not None else 999.9
                            )
                            break
            return self.channels_temp

        except Exception as e:
            log.error("GX20 連線錯誤: %s", e)
            return None

    # ---------- 工具 ----------

    def parse_channels_number(self, station_name: str, checkbox_index: int) -> str:
        """由工位名稱 + 接點 index 取得 4 碼頻道號。"""
        return self.channel_number[station_name][checkbox_index]

    def get_all_temperatures(self) -> Optional[Dict[str, List[Optional[float]]]]:
        """
        對外主要介面：
          讀一次 GX20 → 回傳 {"工位1":[t1..t20], ..., "工位6":[t1..t20]}
          內部 999.9 統一轉為 None，方便前端 / 計算處理。
          連線失敗時回傳 None（poller 以此判斷連線狀態）。
        """
        if self.GX20GetData() is None:
            return None
        out: Dict[str, List[Optional[float]]] = {}
        for station, vals in self.channels_temp.items():
            out[station] = [None if (v is None or v == 999.9) else float(v) for v in vals]
        return out


# === 模組自我測試 ===
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    gx = GX20()
    data = gx.get_all_temperatures()
    for s, v in data.items():
        print(f"{s}: {v}")
