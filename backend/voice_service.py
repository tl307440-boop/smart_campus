import _thread as thread
import base64
import datetime
import hashlib
import hmac
import json
import os
import ssl
import time
from datetime import datetime
from time import mktime
from urllib.parse import urlencode
from wsgiref.handlers import format_date_time

import pygame
import websocket
from env_config import load_backend_env

load_backend_env()

class SparkTTSService:
    """
    封装讯飞语音合成的客户端类
    实现文本转语音
    """

    def __init__(self):
        self.APPID = os.getenv("xufei_APPID")
        self.APIKey = os.getenv("xufei_APIKey")
        self.APISecret = os.getenv("xufei_APISecret")
        self.ws_url = "wss://tts-api.xfyun.cn/v2/tts"
        self.vcn = "x4_yezi"  #聆小瑶-情感
        self.output_file = "讯飞小露.mp3"  # 输出文件路径
        
# // AI辅助生成：科大讯飞星火 (TTS), 2026-04
# 辅助编写基于 HMAC-SHA256 的鉴权算法及 WebSocket 连接管理
    def create_url(self):
        """
        生成鉴权 URL (包含签名)
        """
        url = 'wss://tts-api.xfyun.cn/v2/tts'
        # 1.生成 RFC1123 格式的时间戳
        now = datetime.now()
        date = format_date_time(mktime(now.timetuple()))

        # 2.构造签名字符串
        signature_origin = f"host: ws-api.xfyun.cn\ndate: {date}\nGET /v2/tts HTTP/1.1"

        # 3.使用 HMAC-SHA256 算法计算签名
            #使用hmac-sha256算法结合apiSecret对signature_origin签名，获得签名后的摘要signature_sha。
        signature_sha = hmac.new(
            self.APISecret.encode('utf-8'),#APISecret作为密钥，signature_origin作为消息，使用SHA256算法进行HMAC计算，得到的结果是一个二进制摘要。
            signature_origin.encode('utf-8'),#signature_origin字符串被编码为UTF-8字节串，以便进行HMAC计算。
            digestmod=hashlib.sha256   #指定使用SHA256算法进行HMAC计算。     
            ).digest() #digest()方法返回计算得到的HMAC摘要，结果是一个二进制字节串。
        
        signature_sha = base64.b64encode(signature_sha).decode('utf-8')#将二进制摘要进行Base64编码，得到一个可读的字符串格式的签名。解码成UTF-8字符串以便在URL中使用。

        # 4.构造鉴权参数
        authorization_origin = f"api_key=\"{self.APIKey}\", algorithm=\"hmac-sha256\", headers=\"host date request-line\", signature=\"{signature_sha}\""
        authorization = base64.b64encode(authorization_origin.encode('utf-8')).decode('utf-8')#将鉴权参数进行Base64编码，得到最终的鉴权字符串。

        # 5.构造完整的鉴权 URL
        params = {
            "authorization": authorization,
            "date": date,
            "host": "ws-api.xfyun.cn"
        }

        url = url + '?' + urlencode(params)
        return url
    
    def on_message(self, ws, message):
        """
        on_message 的 Docstring   对服务器返回的message进行处理，提取音频数据并保存到文件中。
        :param ws: ws (WebSocket Object): 它是当前的连接通道对象（就像电话听筒）。
        :param message: 服务器发来的消息，包含音频数据和状态信息为json格式字符串。
        """
        try:
            message = json.loads(message)
            code = message.get("code")
            sid = message.get("sid")
            """
            code 字段是服务器返回的状态码，0 表示成功，非0表示有错误发生。
            sid 是会话 ID，主要用于报错时去讯飞官方查日志。
            """
            if code != 0:
                errMsg = message.get('message', 'Unknown error')
                print("sid:%s call error:%s code is:%s" % (sid, errMsg, code))
                return
            
            data = message.get("data", {})
            audio = data.get("audio", "")
            status = data.get("status", 0)

            if audio:
                audio_data = base64.b64decode(audio)
                with open(self.output_file, 'ab') as f:
                    f.write(audio_data)

            if status == 2:  # 合成完成
                print(f"Audio saved to {self.output_file}")
                ws.close()
        except Exception as e:
            print(f"Error processing message: {e}")

    def on_error(self, ws, error):
        print(f"WebSocket error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        print("WebSocket closed")

    def on_open(self, ws):
        """
        on_open 的 Docstring
        在 WebSocket 连接建立后立即发送合成请求，开始文本转语音的过程。
        :param ws: 
        """
        def run(*args):
            # 构造合成请求的参数
            request = {
                "common": {
                    "app_id": self.APPID
                },
                "business": {
                    "aue": "lame",  # 输出音频格式为mp3
                    "sfl": 1,
                    "vcn": self.vcn,  # 使用聆小瑶-情感的声音
                    "tte": "UTF8"
                },
                "data": {
                    "text": base64.b64encode(self.text_to_speak.encode('utf-8')).decode('utf-8'),
                    "status": 2
                }
            }
            ws.send(json.dumps(request))
        
        thread.start_new_thread(run, ()) #在新的线程中运行 run 函数，避免阻塞主线程，使得 WebSocket 可以同时处理发送和接收消息。主线程你继续专心听电话（保持连接），我分身出去一个人（新线程）负责把这句话念给服务器听。
    
    def speak(self,text):
        """
        speak 的 Docstring
        
        :param self: 说明
        :param text: 要合成语音的文本内容

        """
        if not text:
            print("Text is empty. Please provide text to synthesize.")
            return
        
        #1.清理旧文件
        if os.path.exists(self.output_file):
            os.remove(self.output_file)

        self.text_to_speak = text

        #2.创建 WebSocket 连接
        print(f"[讯飞星火] 正在生成: {text[:15]}...")
        websocket.enableTrace(False)#启用或禁用 WebSocket 的调试跟踪功能。设置为 False 表示禁用调试输出，True 则会输出详细的调试信息到控制台，帮助开发者了解 WebSocket 连接的状态和数据传输过程。
        ws_url = self.create_url()
        ws = websocket.WebSocketApp(ws_url,
                                    on_message=self.on_message,
                                    on_error=self.on_error,
                                    on_close=self.on_close)
        ws.on_open = self.on_open
        ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE}) #强行告诉程序：“跳过证书检查，我信任这个地址，直接连吧

        #3.播放音频
        self._play_audio()

    def _play_audio(self):
        """
        _play_audio 的 Docstring
        
        播放合成完成的音频文件。
        """
        try:
            pygame.mixer.init(
                frequency=16000,  # 设置采样率
                size=-16,         # 设置采样位数，-16表示16位有符号整数
                channels=2  ,      # 设置声道数，2表示立体声
                buffer=2048       # 设置缓冲区大小，单位为字节
            )
            
            pygame.mixer.music.load(self.output_file)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                time.sleep(0.1)  # 等待音频播放完成
                pygame.time.Clock().tick(10)  # 控制播放速度
            pygame.mixer.music.unload()
        except Exception:
            #如果没有安装pygame,尝试使用系统默认播放器
            if os.name == 'nt': # Windows
                os.system(f'start {self.output_file}')
            else: # Mac/Linux
                os.system(f'afplay {self.output_file}')
        except Exception as e:
            print(f"播放失败: {e}")

    def stop(self):
        """
        stop 的 Docstring
        
        停止当前的语音播放。
        """
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()





        
        


