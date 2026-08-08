import time
from colorama import Fore, Style, init
from regex import F
from sympy import content
from llm_service import DeepSeekService
from kimi_extract_service import KimiExtractService
from file_service import FileService

# 初始化颜色输出
init(autoreset = True)

def main():
    print(Fore.CYAN + "智悦校园·心灵伴侣 v1.0")
    print(Fore.CYAN + "启动中~")

    #测试用 使用mock = True(设为False需配置API KEY)
    AI_agent_brain = DeepSeekService(use_mock=True)
    AI_agent_eye = KimiExtractService(use_mock=True)

    print(Fore.RED + "系统就绪！(输入 'exit' 退出)")
    print("-"*40)

    while True:
        #1.获取学生输入
        user_input = input(Fore.LIGHTBLUE_EX + "学生 ：")

        if user_input.lower() in ['exit','quit']:
            print("Bye Bye")
            break
        if not user_input.strip():
            continue

        #2.阅读文档（kimi）
        if user_input.startswith("read"):
            file_path = user_input.replace("read","").strip()
            print(Fore.BLACK + f"正在加载文件：{file_path}")
            content = FileService.read_file(file_path)


        #2.调用deepseekservice
        start_time = time.time()
        result = AI_agent_brain.analyze_emotion_and_reply(user_input)
        duration = time.time()-start_time

        #3.输出内容
        if "error" in result:
            print(Fore.RED + f"系统错误: {result['error']}")
        else :
            print("\n"+ Fore.LIGHTWHITE_EX + "[deepseek 分析]")
            print(f"   • 核心情绪: {result.get('core_emotion')}")
            print(f"   • 压力源头: {result.get('stress_source')}")
            print(f"   • 风险等级: {result.get('risk_level')}/10")

            print("\n" +Fore.BLUE +"[智能助手回复]")
            print(f"   {result.get('reply_text')}")
        print(Fore.WHITE + f"\n(耗时：{duration:.2f}s)")
        print("-"*40)
if __name__ == "__main__":
    main()

