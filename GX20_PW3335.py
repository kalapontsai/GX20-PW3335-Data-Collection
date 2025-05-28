# SAMPO RD2 LAB Data Collection
#-------------------------------------------------------------------------------
#GX20 info: Yokogawa GX20 Paperless Recorder
#       document : IM04L51B01-17EN 
#PW3335 info : GW Instek PW3335 Programmable DC Power Meter
#       document : PW_Communicator_zh / 2018 年1月出版 (改定1.60版)
#-------------------------------------------------------------------------------
#Rev 1_0 2025/5/14 重新編寫
#         1. 讀取GX20的溫度數據
#         2. 讀取PW3335的電壓/電流/功率數據
#         3. 繪製溫度與功率的圖表
#         4. 儲存數據到CSV檔案
#         5. 支援多工位數據收集,計算等功能
#         6. 可設定Debug模式下,離線以模擬數據操作
#-------------------------------------------------------------------------------
import socket
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox  # 修正：添加 messagebox 的導入
import csv
from datetime import datetime, timedelta  # 修正：添加 timedelta 的導入
import pandas as pd  # 修正：添加 pandas 的導入
import numpy as np
import threading
import os,sys
import matplotlib
import matplotlib.pyplot as plt
from matplotlib import rcParams
from matplotlib.figure import Figure
from matplotlib.animation import FuncAnimation
from matplotlib.font_manager import FontProperties
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.backends._backend_tk import NavigationToolbar2Tk
import tkinter.font as tkfont
import matplotlib.dates as mdates
import tempfile

# Set matplotlib default font to Microsoft JhengHei for CJK support
plt.rcParams['font.family'] = 'Microsoft JhengHei'
matplotlib.rcParams['axes.unicode_minus'] = False

Debug_mode = False  # 設定為 True 以啟用除錯模式

# 確保 LOG 檔案儲存到執行檔所在目錄或臨時目錄
if getattr(sys, 'frozen', False):  # 如果是 pyinstaller 打包的執行檔
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_PATH = os.path.join(APP_DIR, "Gx20_Pw3335.log")

# 如果無法寫入執行檔目錄，則使用臨時目錄
if not os.access(APP_DIR, os.W_OK):
    LOG_PATH = os.path.join(tempfile.gettempdir(), "Gx20_Pw3335.log")

def log_to_file(message): 
    """將訊息寫入 LOG 檔案"""
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(message + "\n")
    except Exception as e:
        print(f"無法寫入 LOG 檔案: {e}")

def log_error(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"[ERROR] {timestamp} - {message}"
    print(msg)
    log_to_file(msg)

def log_info(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"[INFO] {timestamp} - {message}"
    print(msg)
    log_to_file(msg)

class GX20:
    def __init__(self, host="192.168.1.1", port=34434):
        self.gsRemoteHost = host
        self.gnRemotePort = port
        # 新增：儲存各工位的頻道對應
        #channel_number = {station_name: {}}
        self.channel_number = {
            "工位1": ["0001","0002","0003","0004","0005","0006","0007","0008","0009","0010","0101","0102","0103","0104","0105","0106","0107","0108","0109","0110"],
            "工位2": ["0201","0202","0203","0204","0205","0206","0207","0208","0209","0210","0301","0302","0303","0304","0305","0306","0307","0308","0309","0310"],
            "工位3": ["0401","0402","0403","0404","0405","0406","0407","0408","0409","0410","1001","1002","1003","1004","1005","1006","1007","1008","1009","1010"],
            "工位4": ["0701","0702","0703","0704","0705","0706","0707","0708","0709","0710","0801","0802","0803","0804","0805","0806","0807","0808","0809","0810"],
            "工位5": ["0501","0502","0503","0504","0505","0506","0507","0508","0509","0510","0601","0602","0603","0604","0605","0606","0607","0608","0609","0610"],
            "工位6": ["1101","1102","1103","1104","1105","1106","1107","1108","1109","1110","1201","1202","1203","1204","1205","1206","1207","1208","1209","1210"]
        }
        self.channels_temp = {
            "工位1": [0.0] * 20,
            "工位2": [0.0] * 20,
            "工位3": [0.0] * 20,
            "工位4": [0.0] * 20,
            "工位5": [0.0] * 20,
            "工位6": [0.0] * 20
        }

    def parse_scientific_notation(self, value_str):
        """解析科學記號格式的數值，非數字或大於999時回傳 None"""
        try:
            if 'E' in value_str:
                base, exp = value_str.split('E')
                value = float(base) * (10 ** int(exp))
                    # 檢查值是否大於 999
                return None if value > 999 else value
            
        except (ValueError, TypeError):
            return None

    def parse_channel_data(self, line):
        """解析頻道數據
        格式: 31字元
        - 第1字元: 資料狀態 (N/B)
        - 第3-6字元: 頻道號碼
        - 第11-18字元: 單位
        - 第19字元: 正負號
        - 第20-31字元: 科學符號數值
        """
        if len(line) != 31:
            return None
            
        data_type = line[0]  # 資料狀態
        channel = line[2:6]  # 頻道號碼
        unit = line[10:18].strip()  # 單位
        sign = line[18]  # 正負號
        value_str = sign + line[19:31]  # 科學符號數值
        
        return {
            "type": data_type,
            "channel": channel,
            "unit": unit,
            "value_str": value_str
        }

    def GX20GetData(self):
        try:
            # 建立 TCP 連線
            with socket.create_connection((self.gsRemoteHost, self.gnRemotePort), timeout=3) as s:
                # 送出指令
                s.sendall(b"FData,0,0001,1210\r\n")
                # 暫停0.5秒
                time.sleep(0.5)
                # 接收資料
                data = s.recv(10240).decode("ascii", errors="ignore")
                #print("Raw data:", repr(data))
                
                # 將資料放入channel_temp
                for line in data.splitlines():
                    parsed_data = self.parse_channel_data(line)
                    if parsed_data:
                        channel = parsed_data["channel"]
                        value_str = parsed_data["value_str"]
                        value = self.parse_scientific_notation(value_str)
                        if value is not None:
                            # 將值存入 channel_temp
                            for station_name, channels in self.channel_number.items():
                                if channel in channels:
                                    index = channels.index(channel)
                                    self.channels_temp[station_name][index] = round(value, 1)
                                    break
                        else:
                            # 如果值無效，則將對應的 channel_temp 設為 None
                            for station_name, channels in self.channel_number.items():
                                if channel in channels:
                                    index = channels.index(channel)
                                    self.channels_temp[station_name][index] = 99.9
                                    break
                #print(f"GX20 channels_temp: {self.channels_temp['工位1']}")
        except Exception as e:
            print(f"GX20 connection error: {e}")
            log_error(f"GX20 connection error: {e}")
            self.valid_data = {}
            return None

        return self.channels_temp

    def decode_temperature(self, channels: list[str]) -> list[float]:
        """
        根據 self.valid_data 取出指定 channels 的溫度值，沒有資料則回傳 None。
        """
        return [
            self.valid_data.get(ch, {}).get("value", None)
            for ch in channels
        ]

    def parse_channels_number(self, station_name, checkbox_index):
        #從 channel_number 找出 station_name 對應的號碼字串
        return self.channel_number[station_name][checkbox_index]
    
class PW3335:
    def __init__(self, ip_address, port=3300):
        self.ip_address = ip_address
        self.port = port
        self.sock = None

    def connect(self):
        """Establish a TCP connection to the power meter."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.ip_address, self.port))

    def disconnect(self):
        """Close the TCP connection."""
        if self.sock:
            self.sock.close()
            self.sock = None

    def parse_measurement(self, value_str):
        """Parse a measurement string and return its numeric value."""
        return float(value_str.split()[1])

    def query_data(self):
        """Query voltage, current, power, and accumulated power."""
        if not self.sock:
            raise ConnectionError("Socket is not connected to the power meter.")
        self.sock.sendall(b':MEAS? U,I,P,WH\n')
        response = self.sock.recv(1024).decode('ascii').strip()
        try:
            # Parse the response format: "U +110.14E+0;I +0.0000E+0;P +000.00E+0;WP +00.0000E+0"
            data = response.split(';')
            if len(data) == 4:
                parsed_data = [float(item.split(' ')[-1].replace('E+0', '')) for item in data]
                return parsed_data
            else:
                raise ValueError(f"Unexpected response format: {response}")
        except Exception as e:
            print(f"Error parsing response: {response}, Exception: {e}")
            raise ValueError(f"Failed to parse response: {response}")

class EnergyCalculator:
    def __init__(self):
        pass
    def current_ef_thresholds(self,energy_allowance,fridge_type):
        if fridge_type == 5:
            #IF(fridge_type=5,ROUND(N4*1.72,1),ROUND(N4*1.6,1))
            threshold_lv1 = round(energy_allowance * 1.72,1)
            threshold_lv2 = round(energy_allowance * 1.54,1)
            threshold_lv3 = round(energy_allowance * 1.36,1)
            threshold_lv4 = round(energy_allowance * 1.18,1)
        else:
            #IF(fridge_type=5,ROUND(N4*1.72,1),ROUND(N4*1.6,1))
            threshold_lv1 = round(energy_allowance * 1.6,1)
            threshold_lv2 = round(energy_allowance * 1.45,1)
            threshold_lv3 = round(energy_allowance * 1.3,1)
            threshold_lv4 = round(energy_allowance * 1.15,1)
        return[ threshold_lv1, threshold_lv2, threshold_lv3, threshold_lv4 ]

    def future_ef_thresholds(self,energy_allowance,fridge_type):
        if fridge_type == 5:
            #IF(fridge_type=5,ROUND(N4*1.72,1),ROUND(N4*1.6,1))
            threshold_lv1 = round(energy_allowance * 1.294,1)
            threshold_lv2 = round(energy_allowance * 1.221,1)
            threshold_lv3 = round(energy_allowance * 1.147,1)
            threshold_lv4 = round(energy_allowance * 1.074,1)
        else:
            #IF(fridge_type=5,ROUND(N4*1.72,1),ROUND(N4*1.6,1))
            threshold_lv1 = round(energy_allowance * 1.308,1)
            threshold_lv2 = round(energy_allowance * 1.231,1)
            threshold_lv3 = round(energy_allowance * 1.154,1)
            threshold_lv4 = round(energy_allowance * 1.077,1)
        return[ threshold_lv1, threshold_lv2, threshold_lv3, threshold_lv4 ]


    def calculate(self, VF, VR, daily_consumption, freezer_temp, fridge_temp, fan_type):
        """
        計算冰箱能耗相關指標
        
        參數:
            VR: 冷藏室容積(L)
            VF: 冷凍室容積(L)
            daily_consumption: 日耗電量(kWh/日)
            fridge_temp: 冷藏室溫度(°C), 預設3.0
            freezer_temp: 冷凍室溫度(°C), 預設-18.0
        
        返回:
            包含所有計算結果的字典
        """
        results = {}
        
        # 1. 計算K值 (溫度係數)
        #print(f"冷凍室溫度: {freezer_temp}, 冷藏室溫度: {fridge_temp}")
        K = self.calculate_K_value(freezer_temp, fridge_temp)
        #print(f"K值: {K}")
        # 2. 計算等效內容積
        equivalent_volume = self.calculate_equivalent_volume(VR, VF, K)
        
        # 3. 確定冰箱型式
        fridge_type = self.determine_fridge_type(equivalent_volume, VR, VF, fan_type)
        #print(f"冰箱型式: {fridge_type}")
        # 4. 計算容許耗用能源基準 (每月)
        energy_allowance = self.calculate_energy_allowance(equivalent_volume, fridge_type)
        
        # 5. 計算2027容許耗用能源基準
        future_energy_allowance = self.calculate_future_energy_allowance(equivalent_volume, fridge_type)
        
        # 6. 計算耗電量基準 (每月)
        benchmark_consumption = self.calculate_benchmark_consumption(equivalent_volume, energy_allowance)
        
        # 7. 計算2027耗電量基準
        future_benchmark_consumption = self.calculate_future_benchmark_consumption(equivalent_volume, future_energy_allowance)
        
        # 8. 計算實測月耗電量
        monthly_consumption = round(daily_consumption * 30,1)
        
        # 9. 計算EF值 (能效因子)
        if monthly_consumption == 0:
            ef_value = 0.0
        else:
            ef_value = round(equivalent_volume / monthly_consumption,1)
        
        # 9.1 計算現有效率基準百分比和等級
        current_ef_thresholds = self.current_ef_thresholds(energy_allowance, fridge_type)

        # 10. 計算現有效率基準百分比和等級
        current_percent, current_grade = self.calculate_current_efficiency(ef_value, current_ef_thresholds)
        
        # 10.1 計算2027新效率基準百分比和等級
        future_ef_thresholds = self.future_ef_thresholds(future_energy_allowance, fridge_type)

        # 11. 計算2027新效率基準百分比和等級
        future_percent, future_grade = self.calculate_future_efficiency(ef_value, future_ef_thresholds)
        
        # 整理所有結果
        results.update({
            '冷凍室溫度': freezer_temp,
            '冷藏室溫度': fridge_temp,
            'K值': K,
            'VF(L)': VF,
            'VR(L)': VR,
            '等效內容積(L)': equivalent_volume,
            '冰箱型式': fridge_type,
            '\n----能效相關計算結果----': '',
            'EF值': ef_value,
            '實測月耗電量(kWh/月)': monthly_consumption,
            '2018年容許耗用能源基準(L/kWh/月)': energy_allowance,
            '2018年耗電量基準(kWh/月)': benchmark_consumption,
            '2018年效率等級': current_grade,
            '2018年一級效率百分比(%)': current_percent,
            '\n----2027年新能效公式----': '',
            '2027容許耗用能源基準(L/kWh/月)': future_energy_allowance,
            '2027年耗電量基準(kWh/月)': future_benchmark_consumption,
            '2027年效率等級': future_grade,
            '2027年一級效率百分比(%)': future_percent
        })
        
        return results
    
    def calculate_K_value(self, freezer_temp, fridge_temp):
        """計算K值 (溫度係數)"""
        # 根據公式 K = (30 - 冷凍庫溫度) / (30 - 冷藏庫溫度)
        #print(f"冷凍庫溫度: {freezer_temp}, 冷藏庫溫度: {fridge_temp}")        
        return round((30 - freezer_temp) / (30 - fridge_temp), 2)
    
    def calculate_equivalent_volume(self, VR, VF, K):
        """計算等效內容積"""
        return round(VR + (K * VF), 1)
    
    def determine_fridge_type(self, equivalent_volume, VR, VF, fan_type):
        """確定冰箱型式"""
        if VF == 0:  # 只有冷藏室
            return 5
        elif equivalent_volume < 400 and fan_type == 1:
            return 1  # 假設是風冷式(實際應根據具體設計)
        elif equivalent_volume >= 400 and fan_type == 1:
            return 2
        elif equivalent_volume < 400 and fan_type == 0:
            return 3
        else:
            return 4  # 假設是風冷式(實際應根據具體設計)
    
    def calculate_energy_allowance(self, equivalent_volume, fridge_type):
        """計算容許耗用能源基準"""
        # 根據公式，ROUND(IFS(fridge_type=1,equivalent_volume/(0.037*equivalent_volume+24.3),fridge_type=2,equivalent_volume/(0.031*M4+21),fridge_type=3,equivalent_volume/(0.033*equivalent_volume+19.7),fridge_type=4,equivalent_volume/(0.029*equivalent_volume+17),fridge_type=5,equivalent_volume/(0.033*equivalent_volume+15.8)),1)
        if fridge_type == 1:
            return round( equivalent_volume / (0.037 * equivalent_volume + 24.3), 1)
        elif fridge_type == 2:
            return round( equivalent_volume / (0.031 * equivalent_volume + 21), 1)
        elif fridge_type == 3:
            return round( equivalent_volume / (0.033 * equivalent_volume + 19.7), 1)
        elif fridge_type == 4:
            return round( equivalent_volume / (0.029 * equivalent_volume + 17), 1)
        else:
            return round( equivalent_volume / (0.033 * equivalent_volume + 15.8), 1)
    
    def calculate_future_energy_allowance(self, equivalent_volume, fridge_type):
        """計算2027年容許耗用能源基準"""
        # 公式:=ROUND(IFS(F4=1,1.3*M4/(0.037*M4+24.3),F4=2,1.3*M4/(0.031*M4+21),F4=3,1.3*M4/(0.033*M4+19.7),F4=4,1.3*M4/(0.029*M4+17),F4=5,1.36*M4/(0.033*M4+15.8)),1)
        # F4 = fridge_type, M4 = equivalent_volume
        if fridge_type == 1:
            return round( 1.3 * equivalent_volume / (0.037 * equivalent_volume + 24.3), 1)
        elif fridge_type == 2:
            return round( 1.3 * equivalent_volume / (0.031 * equivalent_volume + 21), 1)
        elif fridge_type == 3:
            return round(1.3 * equivalent_volume / (0.033 * equivalent_volume + 19.7), 1)
        elif fridge_type == 4:
            return round(1.3 * equivalent_volume / (0.029 * equivalent_volume + 17), 1)
        else:
            return round(1.36 * equivalent_volume / (0.033 * equivalent_volume + 15.8), 1)

    def calculate_benchmark_consumption(self, equivalent_volume, energy_allowance):
        """計算耗電量基準"""
        # 根據公式:ROUND(equivalent_volume / energy_allowance, 1)
        return round(equivalent_volume / energy_allowance, 1)
    
    def calculate_future_benchmark_consumption(self, equivalent_volume, future_energy_allowance):
        """計算2027耗電量基準"""
        return round(equivalent_volume / future_energy_allowance, 1)
    
    def calculate_current_efficiency(self, ef_value, thresholds):
        # 確定等級
        if ef_value >= thresholds[0]:
            grade = "1級"
            final_percent = round(ef_value / thresholds[0] * 100, 1)
        elif ef_value >= thresholds[0] * 0.95:
            grade = "1*級"
            final_percent = round(ef_value / thresholds[0] * 100, 1)
        elif ef_value >= thresholds[1]:
            grade = "2級"
            final_percent = round(ef_value / thresholds[0] * 100, 1)
        elif ef_value >= thresholds[2]:
            grade = "3級"
            final_percent = round(ef_value / thresholds[0] * 100, 1)
        elif ef_value >= thresholds[3]:
            grade = "4級"
            final_percent = round(ef_value / thresholds[0] * 100, 1)
        else :
            grade = "5級"
            final_percent = round(ef_value / thresholds[0] * 100, 1)
        
        return final_percent, grade
    
    def calculate_future_efficiency(self, ef_value, thresholds):
        # 確定等級
        if ef_value >= thresholds[0]:
            grade = "1級"
            final_percent = round(ef_value / thresholds[0] * 100, 1)
        elif ef_value >= thresholds[0] * 0.95:
            grade = "1*級"
            final_percent = round(ef_value / thresholds[0] * 100, 1)
        elif ef_value >= thresholds[1]:
            grade = "2級"
            final_percent = round(ef_value / thresholds[0] * 100, 1)
        elif ef_value >= thresholds[2]:
            grade = "3級"
            final_percent = round(ef_value / thresholds[0] * 100, 1)
        elif ef_value >= thresholds[3]:
            grade = "4級"
            final_percent = round(ef_value / thresholds[0] * 100, 1)
        else :
            grade = "5級"
            final_percent = round(ef_value / thresholds[0] * 100, 1)
        
        return final_percent, grade

class DraggableLine:
    def __init__(self, ax, xdata, ydata, initial_pos, color='red', linestyle='--', linewidth=1, 
                 date_var=None, time_var=None, on_drag_callback=None):
        self.ax = ax
        self.xdata = xdata
        self.ydata = ydata
        self.line = ax.axvline(x=initial_pos, color=color, linestyle=linestyle, linewidth=linewidth)
        self.press = None
        self.date_var = date_var
        self.time_var = time_var
        self.on_drag_callback = on_drag_callback
        self.cid_press = self.line.figure.canvas.mpl_connect('button_press_event', self.on_press)
        self.cid_release = self.line.figure.canvas.mpl_connect('button_release_event', self.on_release)
        self.cid_motion = self.line.figure.canvas.mpl_connect('motion_notify_event', self.on_motion)
        #print("date_var:", type(date_var), "time_var:", type(time_var))

    def on_press(self, event):
        if event.inaxes != self.ax:
            return
        contains, attrd = self.line.contains(event)
        if not contains:
            return
        self.press = True
        
    def on_motion(self, event):
        if not self.press or event.inaxes != self.ax:
            return
        x_pos = event.xdata
        #print("拖曳到", x_pos)
        self.line.set_xdata([x_pos, x_pos])
        self.update_text_boxes(x_pos)
        # 新增：呼叫 callback
        if self.on_drag_callback:
            self.on_drag_callback(x_pos)
        self.line.figure.canvas.draw()
        
    def on_release(self, event):
        self.press = False
        self.line.figure.canvas.draw()
        
    def get_position(self):
        return self.line.get_xdata()[0]
        
    def update_text_boxes(self, x_pos):
        if self.date_var is not None and self.time_var is not None:
            dt = mdates.num2date(x_pos)
            self.date_var.set(dt.strftime('%Y-%m-%d'))
            self.time_var.set(dt.strftime('%H:%M:%S'))

class App:
    def __init__(self, root, ws, hs):
        # --- 主題顏色 ---
        them_colors = {
            "Ocean Deep": [
                "#EAEFEF",  # 0: 白
                "#EAEFEF",  # 1: 淺藍
                "#B8CFCE",  # 2:
                "#7F8CAA",  # 3: 
                "#333446",  # 4: 
                "#333446",  # 5: 深藍
                "#333446"   # 6: 極深色
            ],
            "Serene Greens": [
                "#f4faee",  # 0: 白
                "#5a7939",  # 1: 淺綠
                "#4c6d3b",  # 2:
                "#395a2b",  # 3: 
                "#2c4521",  # 4: 
                "#293c16",  # 5: 深綠
                "#1d2e17"   # 6: 極深色
            ]
        }
# 主背景
        root.configure(bg=them_colors["Ocean Deep"][5])
        # Notebook 標籤樣式
        style = ttk.Style()
        style.configure('TNotebook.Tab', font=('Microsoft JhengHei', 13, 'bold'), padding=[16, 5],
                        background=them_colors["Ocean Deep"][4], foreground=them_colors["Ocean Deep"][2], relief='flat')
        style.map('TNotebook.Tab',
        background=[('selected', them_colors["Ocean Deep"][5]), ('active', them_colors["Ocean Deep"][4]), ('!selected', them_colors["Ocean Deep"][4])],
        foreground=[('selected', them_colors["Ocean Deep"][4]), ('active', them_colors["Ocean Deep"][3]), ('!selected', them_colors["Ocean Deep"][2])]
        )
        # Notebook 本體底色
        style.configure('TNotebook', background=them_colors["Ocean Deep"][4], borderwidth=0)
        # Frame 樣式
        style.configure('TFrame', background=them_colors["Ocean Deep"][4])
        # LabelFrame 樣式
        style.configure('TLabelframe', background=them_colors["Ocean Deep"][4], font=('Microsoft JhengHei', 11, 'bold'))
        style.configure('TLabelframe.Label', background=them_colors["Ocean Deep"][4], foreground=them_colors["Ocean Deep"][1], font=('Microsoft JhengHei', 11, 'bold'))
        # Button 樣式
        style.configure('TButton', font=('Microsoft JhengHei', 11), padding=6, background=them_colors["Ocean Deep"][0], foreground=them_colors["Ocean Deep"][4])
        style.map('TButton', background=[('active', them_colors["Ocean Deep"][4])], foreground=[('active', them_colors["Ocean Deep"][4])])
        # Entry 樣式（文字框底色加點藍灰色，提升可視性）
        style.configure('TEntry', font=('Microsoft JhengHei', 11),
                        fieldbackground='#263445', background='#263445', foreground=them_colors["Ocean Deep"][1],
                        bordercolor=them_colors["Ocean Deep"][4], lightcolor=them_colors["Ocean Deep"][0], darkcolor=them_colors["Ocean Deep"][0])
        # Checkbutton 樣式
        style.configure('TCheckbutton', background=them_colors["Ocean Deep"][4], foreground=them_colors["Ocean Deep"][1], font=('Microsoft JhengHei', 11))
        # Combobox 樣式
        style.configure('TCombobox', font=('Microsoft JhengHei', 11), fieldbackground=them_colors["Ocean Deep"][0], background=them_colors["Ocean Deep"][4], foreground=them_colors["Ocean Deep"][1])
        # Label 樣式
        style.configure('TLabel', background=them_colors["Ocean Deep"][4], foreground=them_colors["Ocean Deep"][0], font=('Microsoft JhengHei', 11))
        # tk.Entry 也套用同色
        root.option_add('*Entry.Background', them_colors["Ocean Deep"][4])
        root.option_add('*Entry.Foreground', them_colors["Ocean Deep"][4])
        # Matplotlib Figure 底色
        self.figure_facecolor = them_colors["Ocean Deep"][1]
        self.figure_temp_color = them_colors["Ocean Deep"][0]
        self.figure_power_color = them_colors["Ocean Deep"][0]

        self.font_prop = FontProperties(family="Microsoft JhengHei", size=10)
        self.root = root
        self.ws = ws
        self.hs = hs
        self.pause_plot = {}  # 用於控制圖表更新的暫停/恢復
        self.gx20_instance = GX20()
        self.pw3335_instances = {}
        self.EnergyCalculator = EnergyCalculator()
        self.plot_channel_labels = {} #即時顯示溫度的標籤
        self.collecting = {}
        self.plot_data = {}
        self.x_start = {}
        self.x_end = {}
        self.collection_threads = {}
        self.stop_events = {}  # 每個工位一個 stop event
 
        # 初始化 Notebook（頁面容器）
        self.notebook = ttk.Notebook(root)
        self.notebook.place(x=5, y=5, width=self.ws-5, height=self.hs-5)
        # 創建 6 個頁面（工位 1 到工位 6）
        self.frames = {}
        for i in range(1, 7):
            frame = ttk.Frame(self.notebook)
            self.notebook.add(frame, text=f"   工位{i}   ", padding=5)
            self.frames[f"工位{i}"] = frame
            self.pause_plot[f"工位{i}"] = False  # 初始化每個工位的暫停狀態
            self.x_start[f"工位{i}"] = datetime.now() - timedelta(minutes=30)  # 初始化每個工位的 x 軸起始時間
            self.x_end[f"工位{i}"] = datetime.now()  # 初始化每個工位的 x 軸結束時間
            # 在每個頁面中添加控件
            self.setup_station_page(frame, f"工位{i}")
        # 綁定窗口關閉事件
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        # 啟動 GX20 連線與資料更新執緒
        threading.Thread(target=self.instant_data_updater, daemon=True).start()

        # 初始化 PW3335 實例
        if not Debug_mode:
            for i in range(1, 7):
                pw_ip = f"192.168.1.{i + 1}"
                try:
                    pw = PW3335(pw_ip)
                    pw.connect()
                    self.pw3335_instances[pw_ip] = pw
                except Exception as e:
                    print(f"PW3335 {pw_ip} 連線失敗: {e}")
                    log_error(f"App.init:PW3335 {pw_ip} 連線失敗: {e}")
    
    def instant_data_updater(self):
        """持續連線GX20,儲存到self.station_data, 並即時更新各工位PLOT頁面溫度顯示"""
        while True:
            try:
                if not Debug_mode:
                    self.gx20_instance.GX20GetData()
                    # 取得溫度數據
                    self.gx20_data_dict = self.gx20_instance.channels_temp

                else:
                    simulation_value = int(datetime.now().strftime("%S")) / 100
                    self.simulation_wh = [0.0] * 7
                    # 實際收集GX20數據, 只模擬電力記錄
                    #self.gx20_instance.GX20GetData()
                    #self.gx20_data_dict = self.gx20_instance.channels_temp
                    # -------------------
                    # 產生6個工位的模擬數據
                    self.gx20_data_dict = {
                        "工位1": [round(simulation_value + i * 0.5, 1) for i in range(20)],
                        "工位2": [round(simulation_value + i * 0.5, 1) for i in range(20)],
                        "工位3": [round(simulation_value + i * 0.5, 1) for i in range(20)],
                        "工位4": [round(simulation_value + i * 0.5, 1) for i in range(20)],
                        "工位5": [round(simulation_value + i * 0.5, 1) for i in range(20)],
                        "工位6": [round(simulation_value + i * 0.5, 1) for i in range(20)]
                    }

                # 依照每個工位的頻道設定，更新溫度顯示
                for i in range(1, 7):
                    station_name = f"工位{i}"
                    if not self.pause_plot[station_name]:
                        """更新 PLOT 頁面的頻道讀值顯示"""
                        if station_name not in self.plot_channel_labels:
                            return
                        temp_list = self.gx20_data_dict[station_name]
                        
                        # 更新每個工位的instant_temp_label
                        for j, channel in enumerate(self.gx20_instance.channel_number[station_name]):
                            if channel in self.plot_channel_labels[station_name]:
                                label = self.plot_channel_labels[station_name][channel]
                                if label :
                                    if temp_list[j] != 99.9:
                                        label.config(text=f"{temp_list[j]}")
                                    else:
                                        label.config(text=f"--")
                    #print(f"即時溫度{station_name}: {self.gx20_data_dict[station_name]}")

            except Exception as e:
                self.show_error_dialog(f"GX20連線錯誤:", str(e))
            time.sleep(5)  # 每5秒更新一次數據

    def setup_station_page(self, frame, station_name):
        """設置每個工位頁面的控件"""
        station_name = station_name.replace(" ", "")  # 去除空格，統一名稱格式

        # 創建子頁面的 Notebook
        station_notebook = ttk.Notebook(frame)
        station_notebook.grid(row=0, column=0, sticky="nsew")
        
        # 創建四個子頁面
        parameter_frame = ttk.Frame(station_notebook)
        plot_frame = ttk.Frame(station_notebook)
        snapshot_frame = ttk.Frame(station_notebook)
        
        # 將子頁面加入 Notebook
        station_notebook.add(parameter_frame, text="  設定  ")
        station_notebook.add(plot_frame, text="  圖表  ")
        station_notebook.add(snapshot_frame, text="  計算  ")
        
        # 設置 frame 的網格權重，使其可以填滿整個空間
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        
        # 在各個子頁面中設置控件
        self.setup_parameter_page(parameter_frame, station_name)
        self.setup_plot_page(plot_frame, station_name)
        self.setup_snapshot_page(snapshot_frame, station_name)

    def setup_parameter_page(self, frame, station_name):
        """設置 參數 頁面的控件"""
        # 檔案框架
        file_frame = ttk.LabelFrame(frame, text="檔案設定")
        file_frame.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        if file_frame:
            # File path selection
            ttk.Label(file_frame, text="儲存路徑:").grid(row=0, column=0, padx=5, pady=5)
            file_path_var = tk.StringVar(value="D:/測試紀錄")  # 設定預設路徑
            file_path_entry = ttk.Entry(file_frame, textvariable=file_path_var, width=30, foreground="black")
            file_path_entry.grid(row=0, column=1, padx=5, pady=5)
            browse_button = ttk.Button(file_frame, text="Browse", command=lambda: self.browse_file(file_path_var))
            browse_button.grid(row=0, column=2, padx=5, pady=5)

            ttk.Label(file_frame, text="檔名:").grid(row=1, column=0, padx=5, pady=5)
            file_name_var = tk.StringVar(value="*.csv") 
            file_name_entry = ttk.Entry(file_frame, textvariable=file_name_var, width=30, state="readonly", foreground="black")
            file_name_entry.grid(row=1, column=1, padx=5, pady=5)

            # Frequency selection
            ttk.Label(file_frame, text="記錄頻率(sec):").grid(row=2, column=0, padx=5, pady=5)
            frequency_var = tk.IntVar(value=10)
            frequency_menu = ttk.Combobox(file_frame, textvariable=frequency_var, state="readonly", foreground="black")
            frequency_menu['values'] = [10, 60, 180, 300]
            frequency_menu.grid(row=2, column=1, padx=5, pady=5)

            # Start, Stop buttons
            start_button = ttk.Button(file_frame, text="Start", command=lambda: self.start_collect(station_name), state="normal")
            start_button.grid(row=1, column=2, padx=5, pady=5)
            stop_button = ttk.Button(file_frame, text="Stop", command=lambda: self.stop_collect(station_name), state="disabled")
            stop_button.grid(row=2, column=2, padx=5, pady=5)

        # 分割線
        ttk.Separator(frame, orient="horizontal").grid(row=1, column=0, sticky="ew", pady=10)
        
        # 機種框架
        prod_frame = ttk.LabelFrame(frame, text="機種資料")
        prod_frame.grid(row=2, column=0, padx=5, pady=5, sticky="nsew")
        # 設置行列權重
        #frame.grid_rowconfigure(1, weight=1)
        if prod_frame:
            # 新增一個frame,設定冰箱規格
            ttk.Label(prod_frame, text="機種:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
            model_entry_var = tk.StringVar(value="NA")
            model_entry = ttk.Entry(prod_frame, width=30, textvariable=model_entry_var, foreground="black")
            model_entry.grid(row=0, column=1, padx=5, pady=5)
            ttk.Label(prod_frame, text="冷凍庫容量(L):").grid(row=1, column=0, padx=5, pady=5, sticky="w")
            vf_entry_var = tk.StringVar(value="150")
            vf_entry = ttk.Entry(prod_frame, width=10, textvariable=vf_entry_var, foreground="black")
            vf_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")
            ttk.Label(prod_frame, text="冷藏庫容量:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
            vr_entry_var = tk.StringVar(value="350")
            vr_entry = ttk.Entry(prod_frame, width=10, textvariable=vr_entry_var, foreground="black")
            vr_entry.grid(row=2, column=1, padx=5, pady=5, sticky="w")
            # Fan type checkbox
            fan_type_var = tk.IntVar(value=1)  # 0: unchecked, 1: checked
            fan_type_checkbox = ttk.Checkbutton(prod_frame, text="風扇式", variable=fan_type_var)
            fan_type_checkbox.grid(row=3, column=0, columnspan=2, padx=5, pady=5, sticky="w")


        # 頻道框架
        channel_check = []
        ch_aliases = []
        channel_frame = ttk.LabelFrame(frame, text="頻道設定")
        channel_frame.grid(row=0, column=1, padx=20, pady=5, sticky="nw")
        if channel_frame:
            # GX20 channel switch
            for i in range(20):
                # 計算行(row)與列(column)位置
                if i < 10:
                    row = i
                    col = 0
                else:
                    row = i - 10
                    col = 3  # 第二列從第4欄開始（0,1,2,3...）
                # 勾選框
                channel_check_var = tk.IntVar(value=0)
                #channel_check_var.set(0)  # 預設為未勾選
                channel_checkbox = ttk.Checkbutton(channel_frame, variable=channel_check_var)
                channel_checkbox.grid(row=row, column=col, padx=2)
                channel_check.append(channel_check_var)

                ch_label = ttk.Label(channel_frame, text=i+1, width=3, anchor="center")
                ch_label.grid(row=row, column=col + 1, padx=5, pady=5)

                # 別名輸入框
                alias_entry = ttk.Entry(channel_frame, width=8, foreground="black")
                alias_entry.grid(row=row, column=col + 2, padx=2, sticky='ew')
                ch_aliases.append(alias_entry)




        # Save references
        setattr(self, f"{station_name}_file_path_var", file_path_var)
        setattr(self, f"{station_name}_file_path_entry", file_path_entry)
        setattr(self, f"{station_name}_file_name_var", file_name_var)
        setattr(self, f"{station_name}_file_name_entry", file_name_entry)
        setattr(self, f"{station_name}_Browse_button", browse_button)
        setattr(self, f"{station_name}_frequency_var", frequency_var)
        setattr(self, f"{station_name}_frequency_menu", frequency_menu)
        setattr(self, f"{station_name}_start_button", start_button)
        setattr(self, f"{station_name}_stop_button", stop_button)
        setattr(self, f"{station_name}_model_entry", model_entry)
        setattr(self, f"{station_name}_model_entry_var", model_entry_var)
        setattr(self, f"{station_name}_vf_entry", vf_entry)
        setattr(self, f"{station_name}_vf_entry_var", vf_entry_var)
        setattr(self, f"{station_name}_vr_entry", vr_entry)
        setattr(self, f"{station_name}_vr_entry_var", vr_entry_var)
        setattr(self, f"{station_name}_fan_type_var", fan_type_var)
        setattr(self, f"{station_name}_fan_type_checkbox", fan_type_checkbox)
        setattr(self, f"{station_name}_channel_check", channel_check)
        setattr(self, f"{station_name}_ch_aliases", ch_aliases)

    def get_enabled_channel(self, station_name):
        """將頻道設定的勾選狀態與別名輸出"""
        # 獲取頻道設定的勾選狀態與別名
        channel_check = getattr(self, f"{station_name}_channel_check")
        ch_aliases = getattr(self, f"{station_name}_ch_aliases")
        enabled_channels = []
        for i in range(20):
            if channel_check[i].get() == 1:
                #print(f"{station_name} - Channel {i+1} alias : {ch_aliases[i].get()}")
                #print(f"頻道號碼: {self.gx20_instance.parse_channels_number(station_name, i)}")
                enabled_channels.append([i, ch_aliases[i].get(),self.gx20_instance.parse_channels_number(station_name, i)])  
        #print(f"{station_name} - Enabled Channels: {enabled_channels}")
        #工位1 - Enabled Channels: [[4, '', '0005'], [12, 'fn', '0103'], [14, 'fgnfgnvb', '0105']]
        return enabled_channels

    def browse_file(self, file_path_var):
        file_path = filedialog.askdirectory()
        file_path_var.set(file_path)
        self.file_path = file_path  # 將選擇的路徑保存到 self.file_path

    def start_collect(self,station_name):
        try:
            # 清除舊數據
            self.plot_data[station_name] = []
            # 檢查檔案路徑
            file_path_var = getattr(self, f"{station_name}_file_path_var", None)
            if not file_path_var or not file_path_var.get():
                self.show_error_dialog("路徑錯誤", "請先選擇儲存路徑")
                return
            self.file_path = file_path_var.get()

            # **檢查至少有一個頻道被勾選**
            if len(self.get_enabled_channel(station_name)) < 1:
                self.show_error_dialog("頻道選擇錯誤", "請至少勾選一個頻道！")
                return
            
            # 啟用 stop 按鈕，禁用 start 按鈕
            start_button = getattr(self, f"{station_name}_start_button", None)
            stop_button = getattr(self, f"{station_name}_stop_button", None)
            pause_button = getattr(self, f"{station_name}_pause_button", None)
            if start_button:
                start_button.config(state="disabled")
            if stop_button:
                stop_button.config(state="normal")
            if pause_button:
                pause_button.config(state="normal")
            # 禁止改變file_path_entry
            file_path_entry = getattr(self, f"{station_name}_file_path_entry", None)
            if file_path_entry:
                file_path_entry.config(state="readonly")
            # 禁用 brwose_button
            browse_button = getattr(self, f"{station_name}_Browse_button", None)
            if browse_button:
                browse_button.config(state="disabled")
            # 禁用 frequency_menu
            frequency_menu = getattr(self, f"{station_name}_frequency_menu", None)
            if frequency_menu:
                frequency_menu.config(state="disabled")
            
            # 確保圖表初始化
            ax_temp = getattr(self, f"{station_name}_ax_temp", None)
            ax_power = getattr(self, f"{station_name}_ax_power", None)
            if ax_temp and ax_power:
                ax_temp.clear()
                ax_power.clear()

            # 設置收集狀態
            self.collecting[station_name] = True
            self.stop_events[station_name] = threading.Event()

            # 頁籤加上 []
            for idx in range(self.notebook.index("end")):
                tab_text = self.notebook.tab(idx, "text").replace(" ", "")
                if tab_text == station_name:
                    self.notebook.tab(idx, text=f"[{tab_text}]")
                    break
            
            # 取得工位對應的頻道和 IP
            station_index = int(station_name[-1]) - 1
            pw_ip = f"192.168.1.{station_index + 2}"

            # 啟動數據收集執行緒
            collection_thread = threading.Thread(
                target=self.collect_data,
                args=(station_name, pw_ip), # ← 這裡加逗號，確保是 tuple
                daemon=True
            )
            self.collection_threads[station_name] = collection_thread
            collection_thread.start()
            log_info(f"{station_name} 開始收集數據")
            #print(f"{station_name} 開始收集數據")

        except Exception as e:
            print(f"Error in start_collect: {e}")
            log_error(f"Error in start_collect: {e}")
            self.stop_collect(station_name)

    def stop_collect(self,station_name):
        """停止指定工位的數據收集"""
        try:
            self.collecting[station_name] = False
            if station_name in self.stop_events:
                self.stop_events[station_name].set()
            # 頁籤移除 []
            for idx in range(self.notebook.index("end")):
                tab_text = self.notebook.tab(idx, "text")
                if tab_text.replace("[", "").replace("]", "").replace(" ", "") == station_name:
                    self.notebook.tab(idx, text=station_name)
                    break
            
            # 啟用 start 按鈕，禁用 stop 按鈕和 pause 按鈕
            start_button = getattr(self, f"{station_name}_start_button", None)
            stop_button = getattr(self, f"{station_name}_stop_button", None)
            pause_button = getattr(self, f"{station_name}_pause_button", None)
            if start_button:
                start_button.config(state="normal")
            if stop_button:
                stop_button.config(state="disabled")
            if pause_button:
                pause_button.config(state="disabled")
                pause_button.config(text="暫停")  # 重設暫停按鈕文字
            # 開放file_path_entry
            file_path_entry = getattr(self, f"{station_name}_file_path_entry", None)
            if file_path_entry:
                file_path_entry.config(state="normal")
            # 開放 browse_button
            browse_button = getattr(self, f"{station_name}_Browse_button", None)
            if browse_button:
                browse_button.config(state="normal")
            # 開放 frequency_menu
            frequency_menu = getattr(self, f"{station_name}_frequency_menu", None)
            if frequency_menu:
                frequency_menu.config(state="enabled")
            
            # 重設暫停狀態
            self.pause_plot[station_name] = False
            log_info(f"{station_name} 停止收集數據")

            # 將collection_thread結束
            self.collecting[station_name] = False
            thread = self.collection_threads.get(station_name)
            if thread and thread.is_alive():
                thread.join(timeout=2)
                if thread.is_alive():
                    print(f"{station_name} 的數據收集執行緒無法正常結束")
        except Exception as e:
            print(f"Error in stop_collect: {e}")
            log_error(f"Error in stop_collect: {e}")

    def collect_data(self,station_name, pw_ip):
        freq = getattr(self, f"{station_name}_frequency_var", None)
        file_name_entry = getattr(self, f"{station_name}_file_name_entry", None)
        file_path_var = getattr(self, f"{station_name}_file_path_var", None)
        frequency_var = freq.get() if freq else 10
        try:
            if not Debug_mode:
                # 檢查 PW3335 連線
                pw = self.pw3335_instances.get(pw_ip)
                if not pw:
                    try:
                        pw = PW3335(pw_ip)
                        pw.connect()
                        self.pw3335_instances[pw_ip] = pw
                    except Exception as e:
                        self.show_error_dialog("設備錯誤", f"{station_name} 的 PW3335 未連線")
                        return

            
            if file_path_var:
                file_path = file_path_var.get()
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                file_name = f"{file_path}/{timestamp}_{station_name}.csv"
                if file_name_entry is not None:
                    file_name_entry.config(state="normal")
                    file_name_entry.delete(0, tk.END)
                    file_name_entry.insert(0, os.path.basename(file_name))
                    file_name_entry.config(state="readonly")
                if not os.path.exists(file_path):
                    os.makedirs(file_path)
                file_exists = os.path.exists(file_name)
                with open(file_name, mode="a", newline="", buffering=1, encoding="utf-8") as file:
                    writer = csv.writer(file)
                    # 寫入標題行：僅在檔案不存在時
                    if not file_exists:
                        header = ["Date", "Time"]
                        ch_aliases = getattr(self, f"{station_name}_ch_aliases", None)
                        if ch_aliases:
                            for i in range(20):
                                if ch_aliases[i].get():
                                    header.append(ch_aliases[i].get())
                                else:
                                    header.append(f"Ch{i+1}")
                        header.extend(["U(V)", "I(A)", "P(W)", "WP(Wh)"])
                        writer.writerow(header)

                    while self.collecting[station_name]:
                        if Debug_mode:
                            frequency_var = 1
                        else:
                            frequency_var = int(frequency_var)
                        active_ch_list = self.get_enabled_channel(station_name)
                        now = datetime.now()
                        # 將 99.9 轉為 None
                        temp_data = [
                            None if v == 99.9 else v
                            for v in self.gx20_data_dict[station_name]
                        ]

                        if not Debug_mode:
                            power_data = [None] * 4
                            try:
                                power_data = pw.query_data()[:4]
                                #print(f"{station_name}即時電力: {power_data}")
                            except Exception as e:
                                log_error(f"collect_data.pw.query_data()發生錯誤: {e} at {pw_ip}")
                        else:
                            # 模擬電力數據
                            power_data = [110.0,1,50,1.1]


                        self.plot_data[station_name].append([now, temp_data, power_data])
                        #print(f"{station_name}最新數據: {self.plot_data[station_name][-1]}")
                        self.update_plot(None, station_name, active_ch_list)
                        
 

                        date_str = now.strftime("%Y-%m-%d")
                        time_str = now.strftime("%H:%M:%S")
                        # 寫入csv的內容由self.gx20_data_dict[station_name]改為temp_data, 頻道內數據為99.9的位置,改為空值

                        writer.writerow([date_str, time_str] + temp_data + power_data)
                        #print(f"collect_data: plot_data{station_name}: {self.plot_data[station_name][-1]}")
                        
                        stop_event = self.stop_events.get(station_name)
                        if stop_event:
                            if stop_event.wait(timeout=frequency_var):
                                break
                        else:
                            time.sleep(frequency_var)
        except Exception as e:
            print(f"Error in collect_data: {e}")
            log_error(f"Error in collect_data: {e}")
            self.stop_collect(station_name)

    def setup_plot_page(self, frame, station_name):
        """設置 PLOT 頁面的控件"""
        xbar_frame = ttk.LabelFrame(frame, text=station_name)
        xbar_frame.grid(row=0, column=0, columnspan=2, padx=20, pady=5, sticky="nw")
        # X 軸範圍選擇
        x_axis_range_var = tk.StringVar(value="30min")
        x_axis_range_menu = ttk.Combobox(xbar_frame, textvariable=x_axis_range_var, state="readonly", width=6, foreground="black")
        x_axis_range_menu['values'] = ["30min", "3hrs", "12hrs", "24hrs", "ALL"]
        x_axis_range_menu.grid(row=0, column=0, padx=1, pady=5)
        
        # Pause/Resume button
        pause_button = ttk.Button(xbar_frame, text="暫停", command=lambda: self.toggle_pause_plot(station_name), 
                                state="disabled", width=6)
        pause_button.grid(row=0, column=1, padx=5, pady=5)
        
        # 頻道框架
        channel_frame = ttk.LabelFrame(frame, text="溫度")
        channel_frame.grid(row=1, column=0, columnspan=2, padx=20, pady=5, sticky="nw")
        channel_labels = {}  # 儲存當前工位的頻道標籤
        temp_alias_label = []  # 儲存頻道別名標籤
        for i in range(20):
            # 計算行(row)與列(column)位置
            if i < 10:
                row = i
                col = 0
            else:
                row = i - 10
                col = 3  # 第二列從第4欄開始（0,1,2,3...）
            instant_temp_label = ttk.Label(channel_frame, text=i+1, width=6, relief="solid", anchor="center")
            instant_temp_label.grid(row=row, column=col, padx=5, pady=5)
            channel_alias_label = ttk.Label(channel_frame, text=i+1, width=3, anchor="center")
            channel_alias_label.grid(row=row, column=col+1, padx=5, pady=5)
            temp_alias_label.append(channel_alias_label)
            # 用 channel number 當 key
            channel_num = self.gx20_instance.channel_number[station_name][i]
            channel_labels[channel_num] = instant_temp_label
        # 儲存該工位的頻道標籤
        self.plot_channel_labels[station_name] = channel_labels
    
        setattr(self, f"{station_name}_start_date", tk.StringVar())
        setattr(self, f"{station_name}_start_time", tk.StringVar())
        setattr(self, f"{station_name}_end_date", tk.StringVar())
        setattr(self, f"{station_name}_end_time", tk.StringVar())
        start_date_entry = ttk.Entry(frame, textvariable=getattr(self, f"{station_name}_start_date"), width=10, foreground="blue")
        start_date_entry.grid(row=2, column=0, padx=5, pady=5)

        start_time_entry = ttk.Entry(frame, textvariable=getattr(self, f"{station_name}_start_time"), width=10, foreground="blue")
        start_time_entry.grid(row=2, column=1, padx=5, pady=5)

        end_date_entry = ttk.Entry(frame, textvariable=getattr(self, f"{station_name}_end_date"), width=10, foreground="red")
        end_date_entry.grid(row=3, column=0, padx=5, pady=5)

        end_time_entry = ttk.Entry(frame, textvariable=getattr(self, f"{station_name}_end_time"), width=10, foreground="red")
        end_time_entry.grid(row=3, column=1, padx=5, pady=5)

        # calculate button
        calculate_button = ttk.Button(frame, text="平均", command=lambda: self.calculate_average(station_name), width=6)
        calculate_button.grid(row=4, column=0, columnspan=2, padx=5, pady=5)
        figure = Figure(figsize=(16, 8), dpi=80, facecolor=self.figure_facecolor)
        gs = figure.add_gridspec(2, 1, height_ratios=[7, 3])  # 7:3 高度比例
        canvas = FigureCanvasTkAgg(figure, master=frame)
        canvas_widget = canvas.get_tk_widget()
        canvas_widget.grid(row=0, rowspan= 5, column=2, padx=5, pady=5)
        ax_temp = figure.add_subplot(gs[0, 0], facecolor=self.figure_temp_color)
        ax_power = figure.add_subplot(gs[1, 0], sharex=ax_temp, facecolor=self.figure_power_color)
        # 減少左右空白
        figure.subplots_adjust(left=0.035, right=0.98, top=0.95, bottom=0.05, hspace=0.1)

        ax_temp.set_ylabel("Temperature (°C)")
        ax_temp.get_xaxis().set_visible(False)
        ax_temp.grid(True)
        ax_power.set_xlabel("Time")
        ax_power.set_ylabel("Power (W)")
        ax_power.grid(True)

        # 設置 X 軸範圍

        # 新增：創建一個 Frame 來容納工具欄，並使用標準工具欄
        toolbar_frame = ttk.Frame(frame)
        toolbar_frame.grid(row=5, column=2, padx=5, pady=5, sticky='wn')
        toolbar = NavigationToolbar2Tk(canvas, toolbar_frame)  # 使用標準工具欄
        #toolbar.grid(row=0, column=0)  # 在 toolbar_frame 中使用 grid
        toolbar.update()

        # Memo text box
        memo_text = tk.Text(frame, height=3, width=100, wrap="word", foreground="black")
        memo_text.grid(row=5, column=2, padx=5, pady=5,sticky="e")
        memo_text.insert(tk.END, "備註:\n")
        
        # Save references
        setattr(self, f"{station_name}_channel_labels", channel_labels)  # 儲存頻道標籤
        setattr(self, f"{station_name}_channel_alias_label", temp_alias_label)  # 儲存頻道別名標籤
        setattr(self, f"{station_name}_figure", figure)
        setattr(self, f"{station_name}_canvas", canvas)
        setattr(self, f"{station_name}_ax_temp", ax_temp)
        setattr(self, f"{station_name}_ax_power", ax_power)
        setattr(self, f"{station_name}_x_axis_range_var", x_axis_range_var)
        setattr(self, f"{station_name}_pause_button", pause_button)
        setattr(self, f"{station_name}_x_axis_range_var", x_axis_range_var)
        setattr(self, f"{station_name}_toolbar", toolbar)
        setattr(self, f"{station_name}_toolbar_frame", toolbar_frame)
        setattr(self, f"{station_name}_start_date_entry", start_date_entry)
        setattr(self, f"{station_name}_start_time_entry", start_time_entry)
        setattr(self, f"{station_name}_end_date_entry", end_date_entry)
        setattr(self, f"{station_name}_end_time_entry", end_time_entry)
        setattr(self, f"{station_name}_calculate_button", calculate_button)
        setattr(self, f"{station_name}_memo_text", memo_text)

        # 啟動 FuncAnimation
        anim = FuncAnimation(
                            figure,
                            self.update_plot,
                            fargs=(station_name,),
                            interval=5000,  # 每 5000 毫秒更新一次
                            cache_frame_data=False,  # 禁用幀緩存
                            blit=False  # 禁用 blit 以避免可能的繪圖問題
                            )

        # 保存動畫引用，避免被垃圾回收
        setattr(self, f"{station_name}_animation", anim)

    def update_plot(self, frame, station_name, active_ch_list=None):
        """更新圖表"""
        artists = []
        if not self.collecting.get(station_name, False):
            return artists
        plot_data = self.plot_data.get(station_name, [])
        if len(plot_data) == 0:
            return artists
        figure = getattr(self, f"{station_name}_figure", None)
        ax_temp = getattr(self, f"{station_name}_ax_temp", None)
        ax_power = getattr(self, f"{station_name}_ax_power", None)
        x_axis_range_var = getattr(self, f"{station_name}_x_axis_range_var", None)

        # 取得 active_ch_list
        if active_ch_list is None:
            active_ch_list = self.get_enabled_channel(station_name)
        # --- 新增：同步更新 plot 頁面的 channel_alias_label ---
        channel_alias_label = getattr(self, f"{station_name}_channel_alias_label", None)
        ch_aliases = getattr(self, f"{station_name}_ch_aliases", None)
        # 取得參數頁的 ch_label
        channel_check = getattr(self, f"{station_name}_channel_check", None)
        if channel_alias_label and ch_aliases and channel_check:
            for i in range(20):
                # 1. 先複製參數頁 ch_label 的標籤名稱
                label_text = f"{i+1}"
                # 2. 若 alias_entry 有值, 以 alias_entry 的內容取代
                if ch_aliases[i].get():
                    label_text = ch_aliases[i].get()
                channel_alias_label[i].config(text=label_text)


        # 更新圖表
        if figure and ax_temp and ax_power and not self.pause_plot[station_name]:
            # 清除舊數據
            ax_temp.clear()
            ax_power.clear()
            # 設置 X 軸範圍
            x_axis_range = x_axis_range_var.get() if x_axis_range_var is not None else "30min"
            #print(f"{station_name} - X 軸範圍: {x_axis_range}")
            if x_axis_range == "30min":
                time_delta = pd.Timedelta(minutes=30)
            elif x_axis_range == "3hrs":
                time_delta = pd.Timedelta(hours=3)
            elif x_axis_range == "12hrs":
                time_delta = pd.Timedelta(hours=12)
            elif x_axis_range == "24hrs":
                time_delta = pd.Timedelta(hours=24)
            elif x_axis_range == "ALL":
                time_delta = pd.Timedelta(plot_data[-1][0] - plot_data[0][0])
            else:
                time_delta = pd.Timedelta(minutes=30)

            # 設置 X 軸範圍
            self.x_start[station_name] = plot_data[-1][0] - time_delta
            self.x_end[station_name] = plot_data[-1][0]
            ax_temp.set_xlim(self.x_start[station_name], self.x_end[station_name])
            ax_power.set_xlim(self.x_start[station_name], self.x_end[station_name])

            # 設定 X 軸顯示日期時間格式
            #ax_temp.xaxis.set_major_formatter(mdates.DateFormatter('%d-%H:%M'))
            ax_power.xaxis.set_major_formatter(mdates.DateFormatter('%d-%H:%M'))

            # 設置 Y 軸格線
            ax_temp.yaxis.grid(True)
            ax_power.yaxis.grid(True)

            # 只顯示 active_ch_list 設定的頻道
            for ch_info in active_ch_list:
                i, alias, channel_num = ch_info
                temp_values = [data[1][i] for data in plot_data]
                label = alias if alias else f"Ch{channel_num}"
                line, = ax_temp.plot([data[0] for data in plot_data], temp_values, label=label)
                artists.append(line)
            # 只顯示啟用的頻道圖例, 若沒設定alias則顯示頻道index
            if active_ch_list:
                legend = ax_temp.legend(
                    [f"{alias}" if alias else f"{index+1}" for index, alias, _ in active_ch_list],
                    loc="upper left",
                    prop=self.font_prop)
                artists.append(legend)
            # 更新電力圖表
            # 修正：power_data 是 list，應用索引而不是字典 key
            power_values = [data[2][2] if isinstance(data[2], list) and len(data[2]) > 2 else None for data in plot_data]
            power_line, = ax_power.plot([data[0] for data in plot_data], power_values, label="Power")
            artists.append(power_line)
        return artists

    def _on_showtemp_drag(self, station_name, x_pos):
        """將 showtemp_draggable 的x 軸數值轉為 datetime，並顯示對應溫度"""
        dt = mdates.num2date(x_pos)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        self.show_temp_at_datetime(station_name, dt)


    def show_temp_at_datetime(self, station_name, dt):
        """根據 datetime 找出最接近的溫度資料，顯示在 channel_labels"""
        plot_data = self.plot_data.get(station_name, [])
        start_date_entry = getattr(self, f"{station_name}_start_date_entry", None)
        start_time_entry = getattr(self, f"{station_name}_start_time_entry", None)
        if not plot_data:
            return
        # 找到最接近 dt 的資料
        closest = min(plot_data, key=lambda x: abs(x[0] - dt))
        temps = closest[1]
        channel_labels = self.plot_channel_labels.get(station_name, {})
        for i, (ch_num) in enumerate(self.gx20_instance.channel_number[station_name]):
            label = channel_labels.get(ch_num)
            if label:
                label.config(text=f"{temps[i]:.1f}" if temps[i] is not None else "--")
        # 更新開始時間與結束時間
        if start_date_entry and start_time_entry:
            start_date_entry.delete(0, tk.END)
            start_date_entry.insert(0, dt.strftime('%Y-%m-%d'))
            start_time_entry.delete(0, tk.END)
            start_time_entry.insert(0, dt.strftime('%H:%M:%S'))

    def toggle_pause_plot(self, station_name):
        # 檢查 plot_data 是否有 10 筆以上，否則停止程序
        if len(self.plot_data.get(station_name, [])) < 2:
            self.show_error_dialog("資料不足", "資料筆數不足，無法暫停/分析。")
            return
        # 切換暫停/繼續圖表更新，暫停時於X軸起訖加axvline，繼續時隱藏，並可拖曳vline
        pause_button = getattr(self, f"{station_name}_pause_button", None)
        ax_temp = getattr(self, f"{station_name}_ax_temp", None)
        ax_power = getattr(self, f"{station_name}_ax_power", None)

        # 用於儲存axvline物件
        if not hasattr(self, "_pause_axvlines"):
            self._pause_axvlines = {}
        if not hasattr(self, "_pause_draggables"):
            self._pause_draggables = {}

        if pause_button:
            if self.pause_plot[station_name] == True:
                # 恢復繪圖，移除axvline與DraggableLine
                self.pause_plot[station_name] = False
                pause_button.config(text="暫停")
                # 移除axvline
                lines = self._pause_axvlines.get(station_name, [])
                for line in lines:
                    try:
                        line.remove()
                    except Exception:
                        pass
                self._pause_axvlines[station_name] = []
                # 移除 DraggableLine
                draggables = self._pause_draggables.get(station_name, [])
                for d in draggables:
                    # 解除事件綁定
                    try:
                        d.line.figure.canvas.mpl_disconnect(d.cid_press)
                        d.line.figure.canvas.mpl_disconnect(d.cid_release)
                        d.line.figure.canvas.mpl_disconnect(d.cid_motion)
                        d.line.remove()
                    except Exception:
                        pass
                self._pause_draggables[station_name] = []
                # 重新繪圖
                canvas = getattr(self, f"{station_name}_canvas", None)
                if canvas:
                    canvas.draw_idle()
            else:
                # 暫停繪圖，顯示axvline並可拖曳
                self.pause_plot[station_name] = True
                pause_button.config(text="繼續")
                # 填入目前 X 軸的資料到 start_date, start_time, end_date, end_time
                try:
                    start_date_entry = getattr(self, f"{station_name}_start_date_entry", None)
                    start_time_entry = getattr(self, f"{station_name}_start_time_entry", None)
                    end_date_entry = getattr(self, f"{station_name}_end_date_entry", None)
                    end_time_entry = getattr(self, f"{station_name}_end_time_entry", None)
                    time_offset = pd.Timedelta(self.x_end[station_name] - self.x_start[station_name])*0.25
                    
                    if start_date_entry and start_time_entry and end_date_entry and end_time_entry:
                        start_date_entry.delete(0, tk.END)
                        start_date_entry.insert(0, (self.x_start[station_name] + time_offset).strftime('%Y-%m-%d'))
                        start_time_entry.delete(0, tk.END)
                        start_time_entry.insert(0, (self.x_start[station_name]+ time_offset).strftime('%H:%M:%S'))
                        end_date_entry.delete(0, tk.END)
                        end_date_entry.insert(0, (self.x_end[station_name]- time_offset).strftime('%Y-%m-%d'))
                        end_time_entry.delete(0, tk.END)
                        end_time_entry.insert(0, (self.x_end[station_name]- time_offset).strftime('%H:%M:%S'))
                except AttributeError as e:
                    print(f"讀不到 start/end date/time 欄位: {e}")

                # 畫出x_start, x_end的axvline，並用DraggableLine包裝
                draggables = []
                lines = []
                if ax_temp and ax_power:
                    vline_start_pos = self.x_start[station_name] + time_offset
                    vline_end_pos = self.x_end[station_name] - time_offset
                    vline_show_pos = self.x_end[station_name] - pd.Timedelta(minutes=1)
                    # 建立 DraggableLine 物件
                    start_draggable = DraggableLine(
                        ax_temp, None, None, vline_start_pos,
                        color='blue', linestyle='--', linewidth=2,
                        date_var=getattr(self, f"{station_name}_start_date"),
                        time_var=getattr(self, f"{station_name}_start_time")
                    )
                    end_draggable = DraggableLine(
                        ax_temp, None, None, vline_end_pos,
                        color='red', linestyle='--', linewidth=2,
                        date_var=getattr(self, f"{station_name}_end_date"),
                        time_var=getattr(self, f"{station_name}_end_time")
                    )
                    showtemp_draggable = DraggableLine(
                        ax_temp, None, None, vline_show_pos,
                        color='green', linestyle='--', linewidth=2,
                        date_var=None,
                        time_var=None,
                        on_drag_callback=lambda x_pos: self._on_showtemp_drag(station_name, x_pos)
                    )
                    
                    draggables.extend([start_draggable, end_draggable, showtemp_draggable])
                    lines.extend([start_draggable.line, end_draggable.line, showtemp_draggable.line])
                    self._pause_axvlines[station_name] = lines
                    self._pause_draggables[station_name] = draggables
                    canvas = getattr(self, f"{station_name}_canvas", None)
                    if canvas:
                        canvas.draw_idle()
    
   
    def calculate_average(self, station_name):
        """計算平均值"""
        start_date_entry = getattr(self, f"{station_name}_start_date_entry", None)
        start_time_entry = getattr(self, f"{station_name}_start_time_entry", None)
        end_date_entry = getattr(self, f"{station_name}_end_date_entry", None)
        end_time_entry = getattr(self, f"{station_name}_end_time_entry", None)

        # 檢查日期和時間格式
        try:
            start_date = start_date_entry.get() if start_date_entry else ""
            start_time = start_time_entry.get() if start_time_entry else ""
            end_date = end_date_entry.get() if end_date_entry else ""
            end_time = end_time_entry.get() if end_time_entry else ""
            start_datetime = pd.to_datetime(f"{start_date} {start_time}")
            end_datetime = pd.to_datetime(f"{end_date} {end_time}")
            if start_datetime >= end_datetime:
                raise ValueError("結束時間必須晚於開始時間")
        except ValueError as e:
            self.show_error_dialog("錯誤", f"計算平均-無效的日期或時間格式: {e}")
            return

        try:
            # 計算平均值
            plot_data = self.plot_data[station_name]
            filtered_data = [data for data in plot_data if start_datetime <= data[0] <= end_datetime]
            if len(filtered_data) == 0:
                self.show_error_dialog("錯誤", "計算平均-在指定範圍內沒有數據")
                return

            # 排除 None 的數據再計算平均
            temp_arrays = []
            for i in range(len(filtered_data[0][1])):
                # 取出第 i 個 channel 的所有數據，排除 None
                ch_values = [data[1][i] for data in filtered_data if data[1][i] is not None]
                if ch_values:
                    temp_arrays.append(ch_values)
                else:
                    temp_arrays.append([])  # 若全為 None，則為空列表
            avg_temp = []
            for ch_values in temp_arrays:
                if ch_values:
                    avg_temp.append(np.mean(ch_values))
                else:
                    avg_temp.append(float('nan'))

            # 顯示平均溫度到 plot_channel_labels
            channel_labels = getattr(self, f"{station_name}_channel_labels", None)
            if channel_labels:
                for i, label in enumerate(channel_labels.values()):
                    if i < len(avg_temp):
                        if not np.isnan(avg_temp[i]):
                            label.config(text=f"({avg_temp[i]:.1f})")
                        else:
                            label.config(text="(nan)")
            #print(avg_text)
        except Exception as e:
            self.show_error_dialog("錯誤", f"計算平均值時發生錯誤: {e}")

    def setup_snapshot_page(self, frame, station_name):
        """設置 REPORT 頁面的控件"""
        # 能耗計算用欄位
        model_frame = ttk.LabelFrame(frame, text="溫度設定")  # 使用 Frame 包含文字框
        model_frame.grid(row=0, column=0, padx=5, pady=10, sticky="w")
        ttk.Label(model_frame, text="F:").grid(row=2, column=1, padx=5, pady=5, sticky="w")
        temp_f_entry_var = tk.StringVar(value="-18.0")
        temp_f_entry = ttk.Entry(model_frame, width=5, textvariable=temp_f_entry_var, foreground="black")
        temp_f_entry.grid(row=2, column=2, padx=5, pady=5, sticky="w")
        ttk.Label(model_frame, text="R:").grid(row=2, column=3, padx=5, pady=5, sticky="w")
        temp_r_entry_var = tk.StringVar(value="3.0")
        temp_r_entry = ttk.Entry(model_frame, width=5, textvariable=temp_r_entry_var, foreground="black")
        temp_r_entry.grid(row=2, column=4, padx=5, pady=5, sticky="w")
        
        ttk.Button(frame, text="計算平均值", command=lambda: self.snapshot_report(station_name)).grid(row=0, column=1, pady=10)
        ttk.Button(frame, text="儲存結果", command=lambda: self.save_results(station_name)).grid(row=0, column=2, pady=10)


        report_text = tk.Text(frame, height=30, width=100, wrap="word", foreground="black")
        report_text.grid(row=1, column=0, columnspan=3, padx=5, pady=5)
        report_text.insert(tk.END, "NA\n")
        
        setattr(self, f"{station_name}_report_text", report_text)
        setattr(self, f"{station_name}_temp_f_entry_var", temp_f_entry_var)
        setattr(self, f"{station_name}_temp_r_entry_var", temp_r_entry_var)
        setattr(self, f"{station_name}_temp_f_entry", temp_f_entry)
        setattr(self, f"{station_name}_temp_r_entry", temp_r_entry)

    def snapshot_report(self, station_name):
        """生成報告"""
        start_date = getattr(self, f"{station_name}_start_date_entry", None)
        start_time = getattr(self, f"{station_name}_start_time_entry", None)
        end_date = getattr(self, f"{station_name}_end_date_entry", None)
        end_time = getattr(self, f"{station_name}_end_time_entry", None)
        # 檢查日期和時間格式
        try:
            start_date = start_date.get() if start_date else ""
            start_time = start_time.get() if start_time else ""
            end_date = end_date.get() if end_date else ""
            end_time = end_time.get() if end_time else ""
            start_datetime = pd.to_datetime(f"{start_date} {start_time}")
            end_datetime = pd.to_datetime(f"{end_date} {end_time}")
            if start_datetime >= end_datetime:
                raise ValueError("結束時間必須晚於開始時間")
        except ValueError as e:
            self.show_error_dialog("錯誤", f"計算:無效的日期或時間格式: {e}")
            return
        #print(f"start_date: {start_date}, start_time: {start_time}")
        #print(f"end_date: {end_date}, end_time: {end_time}")
        try:
            # 展開 plot_data
            # example: plot_data[station_name] = [[datetime, [20個溫度], [電壓、電流、功率、累積功率]]]
            # plot_data-工位1: [datetime.datetime(2025, 5, 21, 9, 38, 4, 915350), [-11.6, -13.2, 29.9, -17.9, -14.3, -13.1, -10.2, 30.0, 29.9, 29.9, 29.7, 29.8, 29.9, 29.8, 29.8, 29.9, 29.9, 29.9, 30.0, 29.9], [110.02, 0.7605, 44.0, 27.513]]
            records = []
            for row in self.plot_data[station_name]:
                dt = row[0]
                temps = row[1]  # 20個溫度
                power = row[2]  # list[電壓、電流、功率、累積功率]
                record = {
                    "datetime": dt,
                }
                # 加入溫度
                for i in range(20):
                    record[f"Ch{i+1}"] = temps[i] if temps and i < len(temps) else None
                # 加入電力
                record["功率"] = power[2]
                record["累積功率"] = power[3]
                records.append(record)
            #print(f"records: {records}")
            # 轉換為 DataFrame
            df = pd.DataFrame(records)
            # 轉換 datetime 欄位為 datetime 格式
            df["datetime"] = pd.to_datetime(df["datetime"])
            # 設定 datetime 為索引
            df.set_index("datetime", inplace=True)
            # 設定開始與結束時間
            df = df.loc[start_datetime:end_datetime]
            # 計算 start 和 end 之間的分鐘數
            time_diff = round((end_datetime - start_datetime).total_seconds() / 60, 1)
            #print(f"時間差: {time_diff} 分鐘")
            # 取temps計算平均值avg_temp
            from typing import Optional
            avg_temp: list[Optional[float]] = [None] * 20
            for i in range(20):
                temp_column = f"Ch{i+1}"
                if temp_column in df.columns:
                    avg = round(df[temp_column].mean(), 1) if not df[temp_column].isnull().all() else None
                    avg_temp[i] = avg
            #print(f"平均溫度: {avg_temp}")

            avg_power = round(df["功率"].mean(), 1)
            #print(f"平均溫度: {avg_temp}")
            #print(f"平均功率: {avg_power}")
             # 計算電力啟停周期,大於 1W才算啟動
            power_column = '功率'
            if power_column in df.columns:
                # 確保正確建立 power_on 欄位
                df.loc[:, 'power_on'] = df[power_column] >= 3
                # 計算啟停周期次數
                power_cycles = int(df['power_on'].astype(int).diff().fillna(0).abs().sum() // 2)
                # 計算大於等於3W和小於3W的週期數，排除頭尾兩個周期
                mask = df['power_on']
                groups = (mask != mask.shift()).cumsum()
                segments = pd.DataFrame({
                    '狀態': mask,
                    '區段編號': groups,
                    '時間': df.index
                }).groupby(['區段編號', '狀態']).agg({'時間': ['min', 'max']}).reset_index()

                # 排除頭尾兩個周期
                if len(segments) > 2:
                    segments = segments.iloc[1:-1]

                # 計算每個區段的持續時間
                segments['持續時間'] = (segments[('時間', 'max')] - segments[('時間', 'min')]).dt.total_seconds()

                # 分別計算大於等於3W和小於3W的週期數與平均時間
                above_segments = segments[segments['狀態']]
                below_segments = segments[~segments['狀態']]

                above_count = len(above_segments)
                below_count = len(below_segments)

                above_avg_time = (above_segments['持續時間'].mean() / 60) if above_count > 0 else 0
                below_avg_time = (below_segments['持續時間'].mean() / 60) if below_count > 0 else 0

                # 計算百分比
                if above_avg_time + below_avg_time > 0:
                    above_percentage = (above_avg_time / (above_avg_time + below_avg_time)) * 100
                else:
                    above_percentage = 0
                #print(f"啟動次數: {power_cycles}, 大於等於3W的週期數: {above_count}, 小於3W的週期數: {below_count}")
                #print(f"大於等於3W的平均時間: {above_avg_time:.2f} 分鐘, 小於3W的平均時間: {below_avg_time:.2f} 分鐘")
                        # 計算 WP(Wh) 欄位的差值
            wp_column = '累積功率'
            if wp_column in df.columns:
                wp_difference = df[wp_column].iloc[-1] - df[wp_column].iloc[0]
                
                # 使用線性法推算 24 小時的差值
                total_seconds = (df.index[-1] - df.index[0]).total_seconds()
                if (total_seconds > 0):
                    wp_24h_difference = round((wp_difference / total_seconds) * (24 * 3600),1)
                else:
                    wp_24h_difference = 0
            #print(f"WP(Wh) 差值: {wp_difference}, 24 小時的差值: {wp_24h_difference}")

            # 計算能耗
            vf_entry = getattr(self, f"{station_name}_vf_entry", None)
            vr_entry = getattr(self, f"{station_name}_vr_entry", None)
            fan_type_var = getattr(self, f"{station_name}_fan_type_var", None)
            vf = float(vf_entry.get()) if vf_entry and vf_entry.get().isdigit() else 0
            vr = float(vr_entry.get()) if vr_entry and vr_entry.get().isdigit() else 0
            fan_type_var = getattr(self, f"{station_name}_fan_type_var", None)
            fan_type = fan_type_var.get() if fan_type_var is not None else 0  # 取得風扇類型的狀態
            #取得snapshot頁面上的溫度設定
            temp_f_entry = getattr(self, f"{station_name}_temp_f_entry", None)
            temp_r_entry = getattr(self, f"{station_name}_temp_r_entry", None)
            def is_float(val):
                try:
                    float(val)
                    return True
                except (ValueError, TypeError):
                    return False
            temp_f = float(temp_f_entry.get()) if temp_f_entry and is_float(temp_f_entry.get()) else 0
            temp_r = float(temp_r_entry.get()) if temp_r_entry and is_float(temp_r_entry.get()) else 0
            #print(f"vf: {vf}, vr: {vr}, fan_type: {fan_type}")
            #print(f"temp_f: {temp_f}, temp_r: {temp_r}")
            ef = EnergyCalculator()
            if isinstance(wp_24h_difference, (int, float)) and vf > 0 and vr > 0:
                daily_consumption = round(wp_24h_difference / 1000, 3)  # 將 Wh 轉換為 kWh
                #print(f"每日耗電量: {daily_consumption} kWh")
                # 計算
                results = ef.calculate(vf, vr, daily_consumption, temp_f, temp_r, fan_type)
                #print(f"能耗計算結果: {results}")
            else:
                results = None
                print("無耗電量數據,無法計算能耗")

            # 顯示結果
            report_text = getattr(self, f"{station_name}_report_text", None)
            if report_text is not None:
                report_text.delete(1.0, tk.END)  # 清空文字框
                report_text.insert(tk.END, f"統計範圍：{start_datetime} ~ {end_datetime}\n")
                report_text.insert(tk.END, f"筆數: {len(df)}\n")
                report_text.insert(tk.END, f"時間: {time_diff} 分鐘\n")
                report_text.insert(tk.END, f"平均溫度:\n")
                for i in range(20):
                    if avg_temp[i] is not None:
                        report_text.insert(tk.END, f"Ch{i+1}: {avg_temp[i]:.1f}\n")
                    else:
                        report_text.insert(tk.END, f"Ch{i+1}: --\n")
                report_text.insert(tk.END, f"平均功率: {avg_power} W\n")
                report_text.insert(tk.END, f"\nON / Off 周期次數：{power_cycles}\n")
                report_text.insert(tk.END, f"On 的平均時間: {above_avg_time:.1f} 分\n" if above_count > 0 else "P(W) >= 3 的平均時間: 無資料\n")
                report_text.insert(tk.END, f"Off 的平均時間: {below_avg_time:.1f} 分\n" if below_count > 0 else "P(W) < 3 的平均時間: 無資料\n")
                report_text.insert(tk.END, f"On / Off 百分比: {above_percentage:.2f}%\n")
                report_text.insert(tk.END, f"\n電力消耗：{wp_difference:.2f} w / {time_diff} 分\n")
                report_text.insert(tk.END, f"24 小時電力消耗：{wp_24h_difference:.1f} w\n")
                report_text.insert(tk.END, f"\n能耗計算：\n")
                if results:
                    for key, value in results.items():
                        report_text.insert(tk.END, f"{key}: {value}\n")
                else:
                    report_text.insert(tk.END, "無法計算能耗，請檢查數據\n")
        except Exception as e:
            self.show_error_dialog("錯誤", f"生成報告時發生錯誤: {e}")
            return

    def save_results(self, station_name):
        """儲存報告"""
        report_text = getattr(self, f"{station_name}_report_text", None)
        if report_text:
            file_path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text files", "*.txt")])
            if file_path:
                with open(file_path, "w") as file:
                    file.write(report_text.get(1.0, tk.END))
                messagebox.showinfo("儲存成功", f"報告已儲存到 {file_path}")
            else:
                messagebox.showwarning("儲存失敗", "未選擇檔案路徑")

    def on_closing(self):
        """關閉視窗時的處理"""
        # 檢查是否有工位正在啟動
        active_stations = [station for station, is_collecting in self.collecting.items() if is_collecting]
        if active_stations:
            messagebox.showwarning(
                "警告", 
                f"以下工位正在收集數據，請先停止數據收集再退出程序：\n{', '.join(active_stations)}"
            )
            log_info(f"以下工位正在收集數據，請先停止數據收集再退出程序：\n{', '.join(active_stations)}")
        else:
            self.root.destroy()
            log_info("程式已關閉")

    def show_error_dialog(self, title: str, message: str):
        """顯示錯誤對話框"""
        messagebox.showerror(title, message)
        print(f"{title}: {message}")
        log_error(f"{title}: {message}")
    
if __name__ == "__main__":
    log_info("程式啟動")
    # 支援 pyinstaller 打包時找資源
    def resource_path(relative_path):
        base_path = getattr(sys, '_MEIPASS', os.path.abspath("."))
        return os.path.join(base_path, relative_path)
    
    now = datetime.now()
    AppTitle = "SAMPO RD2 Lab Data Collection"
    specific_date = datetime(2025, 12, 31)
    if now > specific_date:
        messagebox.showinfo("Info", AppTitle)
        sys.exit()

    root = tk.Tk()

    # 全局字體加大 2 號，並指定為微軟正黑體
    default_font = tkfont.nametofont("TkDefaultFont")
    default_font.configure(family="Microsoft JhengHei", size=default_font.cget("size") + 2)
    text_font = tkfont.nametofont("TkTextFont")
    text_font.configure(family="Microsoft JhengHei", size=text_font.cget("size") + 2)
    fixed_font = tkfont.nametofont("TkFixedFont")
    fixed_font.configure(family="Microsoft JhengHei", size=fixed_font.cget("size") + 2)

    # 啟動時最大化
    root.state('zoomed')

    # 指定視窗大小並置中（這段可保留，最大化時會被覆蓋）
    #w, h = 1920, 1080
    #root.geometry(f'{w}x{h}')
    root.update_idletasks()
    ws = root.winfo_screenwidth()
    hs = root.winfo_screenheight()
    
    ModelID = AppTitle + now.strftime("%Y%m%d_%H%M%S")
    
    root.title(AppTitle)
    try:
        root.iconbitmap(resource_path('favicon.ico'))
    except:
        print("Icon not found, using default icon.")
        pass

    app = App(root, ws, hs)
    root.mainloop()