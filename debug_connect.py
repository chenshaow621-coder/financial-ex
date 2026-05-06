import os
from dotenv import load_dotenv
from openai import OpenAI

# 1. 显式加载 .env (确保路径没问题)
load_dotenv("qwen.env", override=True)

# 2. 打印当前读到的配置 (打码显示，防止泄露)
api_key = os.getenv("DASHSCOPE_API_KEY")
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

print("=" * 30)
print("🔍 配置检查")
if api_key:
    print(f"✅ API KEY: {api_key[:6]}******{api_key[-4:]} (读取成功)")
else:
    print("❌ API KEY: 未找到！请检查 .env 文件名为 .env 且在项目根目录")

print(f"✅ BASE URL: {base_url}")
print("=" * 30)

# 3. 强制关闭代理 (排除网络干扰)
os.environ["http_proxy"] = ""
os.environ["https_proxy"] = ""
print("🛡️  已强制关闭系统代理，准备直连阿里云...")

# 4. 发起一个最简单的请求
client = OpenAI(api_key=api_key, base_url=base_url)

print("🚀 正在发送测试请求 (你好)...")
try:
    completion = client.chat.completions.create(
        model="qwen-plus",  # 确保用你 main.py 里的模型
        messages=[{'role': 'user', 'content': '你好，能听到吗？'}],
        timeout=30
    )
    print("\n✅✅✅ 连接成功！配置完全正确！")
    print("🤖 模型回复:", completion.choices[0].message.content)

except Exception as e:
    print("\n❌❌❌ 连接失败")
    print(f"错误详情: {e}")
    if "401" in str(e):
        print("👉 诊断: 确实是 Key 错了，去阿里云控制台重新复制一下。")
    elif "timed out" in str(e):
        print("👉 诊断: Key 是对的，但网络太慢或模型繁忙。")
    elif "Connection" in str(e):
        print("👉 诊断: 网址错了，或者断网了。")