# Part1工作心得

Part要求完成一个最基本的ReAct-Loop。


## 文件功能划分

- `integrations.py`用于存外部能力集成。这里仅是chunk返回的LLM API。
- `madsLoop.py`命令行接口，跑起整个Agent运行时
- `prompts.py`字符串形式的提示词模板
- `runtime.py`运行时时刻变化的harness层
- `tools.py`首先实现了Tool基类，然后实现了各种工具


## 小巧思

Done工具的实现方式：Done工具初始化时，传入一个消息盒子。这个盒子同时被agent runtime和done工具持有。
agent想结束工作时，直接使用看起来无状态的done工具，向msgbox写入消息。
runtime每一个turn都会清查msgbox。

这就实现了工具和运行时的内聚，并且这个消息盒子的功能后续可以扩展。