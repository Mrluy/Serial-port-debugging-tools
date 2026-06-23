# COM/TCP/UDP 调试工具

一个面向 Windows 的本地调试工具，支持 COM 串口、TCP 客户端、TCP 服务端、UDP 客户端、UDP 服务端收发测试。项目提供免安装单文件 EXE，目标电脑无需安装 Python 或额外运行环境。

## 适用场景

- 串口设备调试、协议帧收发、波特率和校验位验证
- TCP/UDP 设备、网关、服务器、本地端口监听测试
- 文本数据与 16 进制数据互转发送
- 通信日志查看、保存和实时落盘

## 主要功能

- 枚举本机 COM 串口，支持手动输入串口号
- 支持波特率、数据位、校验位、停止位、流控、DTR、RTS 设置
- 支持 TCP 客户端、TCP 服务端、UDP 客户端、UDP 服务端
- 每种网络类型下可以创建多个连接配置
- 连接列表中蓝色圆点表示未连接，绿色圆点表示已连接
- 同一时间只允许打开一个连接，打开新连接时会先关闭当前连接
- TCP 服务端支持多个客户端接入，发送时广播给所有已连接客户端
- UDP 服务端优先回复最近通信的客户端
- 发送区和接收区默认都是文本模式，可按需切换 16 进制
- 支持手动发送、自动循环发送、文件发送
- 支持发送时自动计算并追加 CRC16-Modbus 校验值
- 支持暂停显示、时间戳、保存接收区、实时保存到文件
- 显示发送/接收字节计数和每秒收发速度
- 使用 `assets/app.png` 作为窗口和 EXE 图标

## 直接使用 EXE

打包后的程序位于：

```text
dist\COM串口调试工具.exe
```

双击即可运行。该 EXE 是单文件程序，目标电脑不需要安装 Python、pyserial 或其它运行环境。

如果 Windows 资源管理器仍显示旧图标，通常是系统图标缓存未刷新；EXE 内部图标资源已经随打包更新。

## 从源码运行

开发环境需要安装 Python 3.10 或更高版本。

```powershell
python -m pip install -r requirements.txt
python main.py
```

也可以在安装依赖后双击：

```text
start.bat
```

## 打包 EXE

推荐直接双击：

```text
build_exe.bat
```

脚本会安装打包依赖、从 `assets\app.png` 生成临时 ICO、删除旧 EXE，然后重新生成单文件程序：

```text
dist\COM串口调试工具.exe
```

也可以手动执行：

```powershell
python -m pip install -r requirements.txt pyinstaller pillow
python tools\make_icon.py
python -m PyInstaller --noconfirm --clean --onefile --windowed --name "COM串口调试工具" --icon "assets\app.generated.ico" --add-data "assets\app.png;assets" --hidden-import serial.tools.list_ports_windows main.py
```

`assets\app.generated.ico` 是打包时生成的临时文件，不需要提交。

## 连接列表

左侧连接列表按类型分组：

```text
客户端模式
  COM串口
  TCP客户端
  UDP客户端

服务器模式
  TCP服务端
  UDP服务端
```

选中 `COM串口` 时，下方按钮显示为 `打开串口`，不需要创建连接。选择串口参数后直接打开即可。

选中 TCP/UDP 类型时，下方会显示 `创建连接` 和 `打开连接`。先填写网络参数并创建连接，连接配置会显示在对应类型下面；只有选中某个已创建的连接并点击 `打开连接` 后，程序才会真正连接或监听。

## 网络参数规则

- `TCP客户端`：只填写目标 IP、目标端口，本地端口随机分配
- `UDP客户端`：只填写目标 IP、目标端口，本地端口随机分配
- `TCP服务端`：只填写本地端口
- `UDP服务端`：只填写本地端口

连接名称只显示端点信息，例如：

```text
10.10.1.57:10123
本机(192.168.10.1):665
```

## 16 进制发送

发送区默认按文本发送。勾选发送区 `16进制` 后，可以使用以下格式：

```text
4E 57 00 13
4E,57,00,13
0x4E 0x57 0x00 0x13
```

接收区默认按文本显示。勾选接收区 `16进制` 后，接收到的数据会按字节显示。

## 自动 CRC

发送区勾选 `自动CRC` 后，点击 `发送` 时会自动计算 CRC16-Modbus，并把 CRC 低字节在前追加到实际发送的数据末尾。

如果指令末尾已经带有正确的 CRC16-Modbus，程序会直接发送原数据，不会重复追加。

示例：

```text
发送区输入：01 03 00 00 00 0A
实际发送：01 03 00 00 00 0A C5 CD
```

自动 CRC 同时适用于手动发送、自动发送和文件发送。

## 开发检查

提交前可以运行：

```powershell
python -m py_compile main.py tools\make_icon.py
python -m unittest discover -s tests
```

## 项目结构

```text
.
├─ main.py                  # 主程序
├─ requirements.txt         # 运行依赖
├─ start.bat                # 源码启动脚本
├─ build_exe.bat            # EXE 打包脚本
├─ assets\
│  └─ app.png               # 应用图标源文件
├─ tools\
│  └─ make_icon.py          # PNG 转 ICO 工具
├─ tests\
│  └─ test_payload.py       # 基础单元测试
└─ dist\
   └─ COM串口调试工具.exe   # 打包后的单文件程序
```

## 常见问题

### 程序提示缺少 pyserial

源码运行前需要安装依赖：

```powershell
python -m pip install -r requirements.txt
```

直接运行 `dist\COM串口调试工具.exe` 不需要安装该依赖。

### 找不到 COM 串口

确认设备驱动已经安装，并在 Windows 设备管理器中能看到对应的 COM 端口。也可以在串口下拉框中手动输入端口号，例如 `COM3`。

### TCP 服务端无法监听端口

检查端口是否已被其它程序占用，或是否被安全软件、防火墙策略拦截。服务端模式只需要填写本地端口。

### UDP 服务端发送失败

UDP 服务端会优先回复最近一次发来数据的客户端。请先让客户端向服务端发送一帧数据，再从服务端发送回复。
