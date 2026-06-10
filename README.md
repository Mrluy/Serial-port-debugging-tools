# COM/TCP/UDP调试工具

一个参考 TCP&UDP 测试工具布局写的 COM/TCP/UDP 调试工具，适合在 Windows 上做串口收发、网络收发、协议帧调试和日志保存。

## 功能

- 枚举本机 COM 串口
- 配置波特率、数据位、校验位、停止位、流控、DTR/RTS
- TCP 客户端、TCP 服务端、UDP 客户端、UDP 服务端测试
- 每种连接类型下可创建多个连接配置
- 连接列表蓝色圆点表示未连接，绿色圆点表示已连接
- 同一时间只允许打开一个连接
- TCP 服务端支持多客户端接入，发送时会广播给所有已连接客户端
- UDP 服务端会优先回复最近一次通信的客户端，也可按目标 IP/端口发送
- 文本发送和 16 进制发送
- 自动循环发送，支持发送间隔
- 文件发送
- 发送区默认为空，不预置示例字段
- 文本接收和 16 进制接收
- 暂停显示、时间戳、接收区保存、实时保存到文件
- 发送/接收字节计数和每秒速度显示

## 运行

```powershell
python -m pip install -r requirements.txt
python main.py
```

也可以在安装依赖后双击 `start.bat` 启动。

如果程序能打开但提示缺少 `pyserial`，说明还没有安装依赖，执行上面的第一条命令即可。

## 打包成 exe

可选步骤，双击 `build_exe.bat`，或手动执行：

```powershell
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name "COM串口调试工具" --hidden-import serial.tools.list_ports_windows main.py
```

打包结果是 `dist\COM串口调试工具.exe`，目标电脑无需安装 Python 或 pyserial。

## 网络模式

在左侧 `连接列表` 中选择连接类型：

- `TCP客户端`：填写目标 IP 和目标端口后连接服务端
- `TCP服务端`：填写本地 IP 和本地端口后监听客户端
- `UDP客户端`：填写目标 IP 和目标端口；本地端口为 `0` 时自动分配
- `UDP服务端`：填写本地 IP 和本地端口后监听 UDP 数据

选中某个类型后填写参数，在该类型上右键可创建连接。连接会以蓝色圆点显示在对应类型下面。选中某个连接后点击 `打开连接` 或右键选择打开，才会真正连接；如果当前已有其它连接打开，程序会先关闭旧连接，再打开新连接。

## 16 进制格式

发送区支持以下格式：

```text
4E 57 00 13
4E,57,00,13
0x4E 0x57 0x00 0x13
```

开启 `16进制` 发送时，程序会按字节发送；关闭时会按选择的编码发送文本。
