import threading
import time
from ast import If
from json import load
from typing import final

from colorama import Fore, init
from env_config import load_backend_env
from file_service import FileService
from kimi_extract_service import KimiExtractService
from llm_service import DeepSeekService
from sympy import ask
from tenancy import LEGACY_TENANT
from vector_service import VectorRAGService
from voice_service import SparkTTSService

load_backend_env()


init(autoreset=True)  

def main():
    print(Fore.CYAN + "=== 智悦校园 · RAG 智能体 (DeepSeek + Kimi + zhipu) ===")
    
    # 1. 初始化双模型
    try:
        deepseek_brain = DeepSeekService(use_mock=False)  # 思考者
        kimi_eye = KimiExtractService(use_mock=True)    # 阅读者(<10k)
        vector_db = VectorRAGService(persist_directory = './chroma_db_data')#存储者(10k)
        tts_mouth = SparkTTSService() # 说话者

    except Exception as e:
        print(Fore.RED + f"系统初始化失败:{e}")
        print(Fore.RED + "请检查 .env 文件是否配置了 zhi_pu_API_KEY")
        return
    
    #定义路由
    router_key = 1000 #演示用1000 实际用10000 -- 50000

    current_doc_content = ""  # 存储当前加载的文档内容，供 Kimi 提取知识使用
    is_using_vector_db = False  # 标记是否使用向量数据库进行检索

    print(Fore.YELLOW + "系统正在就绪中...请稍候")
    print(Fore.WHITE + f"定义分配策略: 文档 < {router_key} -> Kimi (小文档) | 文档 >= {router_key} -> 向量数据库 (大文档)")
    welcome_text = "同学，你好！我是你的校园助手。请问有什么可以帮你？"
    print(Fore.GREEN + f"欢迎语: {welcome_text}")
    threading.Thread(target=tts_mouth.speak, args=(welcome_text,)).start() #播放欢迎语音
    print("-" * 40)

    while True:
        user_input = input(Fore.YELLOW + "\n 你: ").strip()

        tts_mouth.stop() #每次用户输入后，先关闭上一次的语音播放，避免语音叠加
        
        if user_input.lower() in ['exit', 'quit']:
            goodbye_text = "同学，再见！祝你学习愉快！"
            print(Fore.GREEN + goodbye_text)
            tts_mouth.speak(goodbye_text) #播放告别语音
            break

        #/read 命令：加载文档并建立知识库
        if user_input.startswith("/read"):
            file_path = user_input.replace("/read", "").strip()
            print(Fore.BLUE + f" 正在加载文件: {file_path} ...")

            # 1.读取文件内容
            content = FileService.read_file(file_path)
            if content.startswith("Error"):
                print(Fore.RED + content)
                continue

            doc_len = len(content)
            current_doc_content = content  # 更新当前文档内容
            print(Fore.GREEN + f" 文件读取成功 文档长度: {doc_len} 字")
            

            # 2.决定是否存入向量数据库
            if doc_len >= router_key:
                print(Fore.BLUE + f" 文档长度 {doc_len} 字 >= {router_key} 字，正在存入向量数据库...")
                print(Fore.BLUE +"->使用模型 zhipuAIembeding-3 进行向量化...")
                print(Fore.BLUE + "->存储位置 ：本地 Chroma DB...")

                try:
                    vector_db.index_document(content, file_path, LEGACY_TENANT)
                    is_using_vector_db = True
                    print(Fore.GREEN + " 向量构建完成 文档成功存入向量数据库！后续提问将走向量检索流程。")
                except Exception as e:
                    print(Fore.RED + f" 向量构建失败: {e}")
                    is_using_vector_db = False
            else:
                is_using_vector_db = False
                print(Fore.YELLOW + f" 文档长度 {doc_len} 字 < {router_key} 字，不存入向量数据库。后续提问将走 Kimi 小文档流程。")

            continue
        
        # /ask 命令：提问
        if user_input.startswith("/ask"):
            question = user_input.replace("/ask", "").strip()
            
            if not current_doc_content and not is_using_vector_db:
                print(Fore.RED + "请先使用 /read 命令加载文档！")
                continue
            
            start_time = time.time()
            context = ""

            #--- 分支一：走向量检索 (ZhipuAI + Chroma) ---
            if is_using_vector_db:
                print(Fore.MAGENTA + "[方案 B] 正在使用智谱向量模型检索 ChromaDB...")
                # 1. 向量检索(vector_service.py 中的 search)
                context = vector_db.search(question, LEGACY_TENANT, top_k=3)
                print(Fore.MAGENTA + "检索到的相关上下文片段，准备喂给deepseek")

            #--- 分支二：走 Kimi 小文档流程 ---
            else:
                print(Fore.MAGENTA + "[方案 A] 正在使用 Kimi 小文档模型提取知识...")
                # 1. Kimi 提取知识(kimi_extract_service.py 中的 extract_knowledge)
                context = kimi_eye.extract_knowledge(current_doc_content, question)
                print(Fore.MAGENTA + "Kimi 提取的上下文，准备为给deepseek")

            # --- 最终生成：DeepSeek ---

            if context:
                print(Fore.BLUE + "[DeepSeek] 正在思考...")
                final_answer = deepseek_brain.chat_with_context(context, question)
                print("-" * 40)
                print(Fore.GREEN + f"[DeepSeek] 回答: {final_answer}")
                # speak_thread = threading.Thread(target=tts_mouth.speak, args=(final_answer,))
                # speak_thread.start() #播放回答语音
                print(Fore.BLUE + f"本次问答耗时: {time.time() - start_time:.2f} 秒")
            else:
                print(Fore.RED + "未能获取到有效的上下文，无法生成回答。")
                continue
        
        #/clear 命令：清空向量数据库
        if user_input.startswith("/clear"):
            vector_db.clear_tenant(LEGACY_TENANT)
            current_doc_content = ""
            is_using_vector_db = False
            print(Fore.GREEN + "系统已重置，向量数据库已清空，当前文档内容已清除。")
            continue

        print(Fore.YELLOW + "无效命令，请使用 /read <文件路径> 加载文档，/ask <问题> 提问，/clear 重置系统，或输入 exit 退出。")

if __name__ == "__main__":
    main()

            





















