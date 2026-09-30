import main

called = {"placeholder": False, "text": None}
orig = main._write_no_data_trend

def wrapped(path, text):
    called["placeholder"] = True
    called["text"] = text
    return orig(path, text)

main._write_no_data_trend = wrapped

path = main.generate_trend_chart(metric="refusal_accuracy")
print("返回路径:", path)
print("是否走了'暂无数据'占位分支:", called["placeholder"])
print("占位文案(若有):", called["text"])

if not called["placeholder"]:
    import os
    print("趋势图文件存在:", os.path.exists(path), "| 大小(字节):", os.path.getsize(path) if os.path.exists(path) else 0)